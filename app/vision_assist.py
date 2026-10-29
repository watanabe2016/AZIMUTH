# -*- coding: utf-8 -*-
"""视觉模型 API 适配层；导入题卡的参考答案由云端模型直接解答。"""
import base64
import copy
import io
import json
import math
import os
import re
import threading
import socket
import queue
import http.client
import time
from datetime import datetime, timezone
import urllib.parse
import urllib.error
import urllib.request
from contextlib import contextmanager

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImageReader

import config as cfg


DEFAULT_API_BASE_URL = "https://api.deepseek.com"
DEFAULT_API_MODEL = "deepseek-flash"
API_PROVIDER_PRESETS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-6-astra",
    },
    "custom": {
        "label": "其他 OpenAI 兼容服务",
        "base_url": "",
        "model": "",
    },
}


class RequestCancelled(Exception):
    """用户主动取消了正在进行的视觉 API 请求。"""


class NetworkInterrupted(OSError):
    """网络连接中断或长时间没有收到服务商数据。"""


def _response_socket(response):
    """仅沿标准 HTTP 响应的传输链寻找套接字，不读取响应正文。"""
    current = response
    for _ in range(6):
        if isinstance(current, socket.socket):
            return current
        current = next((getattr(current, name, None)
                        for name in ("fp", "raw", "_sock")
                        if getattr(current, name, None) is not None), None)
        if current is None:
            break
    return None


def _abort_response(response):
    """先中断底层读取，再关闭缓冲流；绝不占用界面线程等待读取锁。"""
    transport = _response_socket(response)
    if transport is not None:
        try:
            transport.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    try:
        response.close()
    except Exception:
        pass


def _network_disconnected():
    """读取 Windows 的联网状态，不向服务商额外发送请求。"""
    try:
        import ctypes
        flags = ctypes.c_ulong()
        return not bool(ctypes.windll.wininet.InternetGetConnectedState(
            ctypes.byref(flags), 0))
    except Exception:
        return False


class RequestControl:
    """取消立即返回，连接回收在后台执行；记录最近的传输活动。"""

    IDLE_TIMEOUT = 45.0

    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._response = None
        self._aborting = False
        self.failure_reason = ""
        self.last_activity = time.monotonic()
        self.network_started = False

    @property
    def cancelled(self):
        return self._event.is_set()

    def check(self):
        if self.cancelled:
            raise RequestCancelled("用户已取消解题请求")
        if self.failure_reason:
            raise NetworkInterrupted(self.failure_reason)

    def touch(self):
        self.last_activity = time.monotonic()

    def attach(self, response):
        with self._lock:
            self._response = response
        if self.cancelled or self.failure_reason:
            self._interrupt()
            self.check()
        self.touch()

    def detach(self, response):
        with self._lock:
            if self._response is response:
                self._response = None
                self._aborting = False

    def _interrupt(self):
        with self._lock:
            response = self._response
            if response is None or self._aborting:
                return
            self._aborting = True
        threading.Thread(target=_abort_response, args=(response,),
                         name="API连接回收", daemon=True).start()

    def cancel(self):
        self._event.set()
        self._interrupt()

    def fail(self, reason):
        self.failure_reason = str(reason)
        self._interrupt()


def _interruptible_io(callback, control):
    """等待网络 I/O 时持续检查取消；阻塞读写只由无 Qt 对象的后台线程持有。"""
    completed = queue.Queue(maxsize=1)
    def read():
        try:
            completed.put((True, callback()))
        except Exception as exc:
            completed.put((False, exc))
    threading.Thread(target=read, name="API网络读取", daemon=True).start()
    while True:
        control.check()
        try:
            ok, result = completed.get(timeout=.1)
        except queue.Empty:
            continue
        control.check()
        if not ok:
            raise result
        return result


class _ControlledResponse:
    """把阻塞连接和流式读取与 Qt 工作线程生命周期分开。"""

    def __init__(self, response, control):
        self.response, self.control = response, control
        self.headers = getattr(response, "headers", {})

    def read(self, *args):
        result = _interruptible_io(lambda: self.response.read(*args), self.control)
        self.control.touch()
        return result

    def __iter__(self):
        received = queue.Queue(maxsize=16)
        stopped = threading.Event()
        def offer(item):
            while not stopped.is_set():
                try:
                    received.put(item, timeout=.1)
                    return
                except queue.Full:
                    pass
        def read_lines():
            try:
                for line in self.response:
                    if stopped.is_set():
                        return
                    self.control.touch()
                    offer(("line", line))
                offer(("done", None))
            except Exception as exc:
                offer(("error", exc))
        threading.Thread(target=read_lines, name="API流式读取", daemon=True).start()
        try:
            while True:
                self.control.check()
                try:
                    kind, value = received.get(timeout=.1)
                except queue.Empty:
                    continue
                self.control.check()
                if kind == "error":
                    raise value
                if kind == "done":
                    return
                yield value
        finally:
            stopped.set()


@contextmanager
def _open_response(request, timeout, request_control=None):
    control = request_control or RequestControl()
    control.check()
    control.network_started = True
    control.touch()
    stopped = threading.Event()
    # 连接建立与读取等待分开限时；流式保活和推理数据都会刷新活动时间。
    def monitor():
        while not stopped.wait(0.5):
            if control.cancelled:
                control._interrupt()
                return
            if _network_disconnected():
                control.fail("网络已断开，解题已停止。请恢复网络后点击“重试解题”。")
                return
            if time.monotonic() - control.last_activity >= control.IDLE_TIMEOUT:
                control.fail("已超过45秒未收到服务商数据，可能网络中断或服务暂时无响应。请检查网络后重试。")
                return
    threading.Thread(target=monitor, name="API网络状态", daemon=True).start()
    response = None
    try:
        def connect():
            raw = urllib.request.urlopen(request, timeout=min(float(timeout), 20.0))
            control.attach(raw)
            return raw
        response = _interruptible_io(connect, control)
        transport = _response_socket(response)
        if transport is not None:
            transport.settimeout(min(float(timeout), control.IDLE_TIMEOUT))
        yield _ControlledResponse(response, control)
        control.check()
    except Exception:
        control.check()
        raise
    finally:
        stopped.set()
        if response is not None:
            control.detach(response)
            if control.cancelled or control.failure_reason:
                threading.Thread(target=_abort_response, args=(response,),
                                 name="API连接收尾", daemon=True).start()
            else:
                response.close()


def _api_settings():
    """读取兼容 OpenAI Chat Completions 的云端视觉模型设置。"""
    base_url = (os.environ.get("AZIMUTH_VISION_API_BASE_URL", "").strip() or
                str(cfg.get("vision_api_base_url", DEFAULT_API_BASE_URL)).strip())
    api_key = (os.environ.get("AZIMUTH_VISION_API_KEY", "").strip() or
               cfg.get_vision_api_key())
    model = (os.environ.get("AZIMUTH_VISION_MODEL", "").strip() or
             str(cfg.get("vision_api_model", DEFAULT_API_MODEL)).strip())
    parsed = urllib.parse.urlparse(base_url)
    secure_remote = parsed.scheme == "https" and bool(parsed.hostname)
    configured = bool(secure_remote and api_key and model)
    return configured, base_url.rstrip("/"), api_key, model


def _api_provider():
    provider = (os.environ.get("AZIMUTH_VISION_API_PROVIDER", "").strip() or
                str(cfg.get("vision_api_provider", "deepseek")).strip().lower())
    return provider if provider in API_PROVIDER_PRESETS else "custom"


def _token_limit_field():
    """OpenAI 新模型使用 max_completion_tokens，兼容接口使用 max_tokens。"""
    return "max_completion_tokens" if _api_provider() == "openai" else "max_tokens"


def _sampling_parameters():
    """仅向确认支持 temperature 的 DeepSeek 发送采样参数。"""
    return {"temperature": 0} if _api_provider() == "deepseek" else {}


def _chat_request(base_url, api_key, payload, timeout, progress_callback=None,
                  stream_callback=None, request_control=None, reasoning_callback=None):
    """发送兼容 Chat Completions 的请求，并收拢普通 JSON 与 SSE 流式响应。"""
    request_control = request_control or RequestControl()
    endpoint = _endpoint(base_url)

    def send(request_payload):
        if request_control is not None:
            request_control.check()
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + api_key,
                     "Content-Type": "application/json"},
            method="POST")
        with _open_response(request, timeout, request_control) as response:
            headers = getattr(response, "headers", {}) or {}
            content_type = str(headers.get("Content-Type") or "").lower()
            if "text/event-stream" not in content_type:
                raw = response.read().decode("utf-8", errors="replace")
                try:
                    body = json.loads(raw)
                except (TypeError, ValueError):
                    body = {"choices": [{"message": {"content": raw}}]}
                content = _response_content_text(
                    (((body.get("choices") or [{}])[0].get("message") or {}).get(
                        "content")) if isinstance(body, dict) else "")
                if content and callable(stream_callback):
                    try:
                        stream_callback(content)
                    except Exception:
                        pass
                if content and callable(progress_callback):
                    try:
                        progress_callback(len(content))
                    except Exception:
                        pass
                return body

            fragments = []
            usage = None
            finish_reason = ""
            received = 0
            reasoning_received = 0
            last_reasoning_report = 0.0
            last_report = time.monotonic()
            last_stream_report = last_report
            completed_stream = False
            for raw_line in response:
                if request_control is not None:
                    request_control.touch()
                    request_control.check()
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                event = line[5:].strip()
                if event == "[DONE]":
                    completed_stream = True
                    break
                try:
                    chunk = json.loads(event)
                except (TypeError, ValueError):
                    continue
                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                choice = choices[0] if choices and isinstance(choices[0], dict) else {}
                delta = choice.get("delta") or {}
                reasoning = _response_content_text(delta.get("reasoning_content")) if isinstance(delta, dict) else ""
                if reasoning:
                    # 仅报告正在推理和耗时，不展示或存储模型的内部思维文本。
                    reasoning_received += len(reasoning)
                    now = time.monotonic()
                    if callable(reasoning_callback) and now - last_reasoning_report >= 1.0:
                        try:
                            reasoning_callback(reasoning_received)
                        except Exception:
                            pass
                        last_reasoning_report = now
                piece = delta.get("content") if isinstance(delta, dict) else None
                piece = _response_content_text(piece)
                if isinstance(piece, str) and piece:
                    fragments.append(piece)
                    received += len(piece)
                    now = time.monotonic()
                    if callable(progress_callback) and (
                            received % 240 < len(piece) or now - last_report >= 0.55):
                        try:
                            progress_callback(received)
                        except Exception:
                            pass
                        last_report = now
                    if callable(stream_callback) and (
                            received % 180 < len(piece) or
                            now - last_stream_report >= 0.55):
                        try:
                            stream_callback("".join(fragments))
                        except Exception:
                            pass
                        last_stream_report = now
                reason = choice.get("finish_reason")
                if reason:
                    finish_reason = str(reason)
            if not completed_stream and not finish_reason:
                raise NetworkInterrupted("接收模型解答时连接中断，未保存未完成的答案。请检查网络后重试。")
            content = "".join(fragments)
            if content and callable(stream_callback):
                try:
                    stream_callback(content)
                except Exception:
                    pass
            if content and callable(progress_callback):
                try:
                    progress_callback(len(content))
                except Exception:
                    pass
            return {
                "choices": [{"message": {"content": content},
                             "finish_reason": finish_reason}],
                "usage": usage,
            }

    request_payload = dict(payload)
    for _attempt in range(4):
        try:
            return send(request_payload)
        except RequestCancelled:
            raise
        except urllib.error.HTTPError as exc:
            if request_control is not None and request_control.cancelled:
                raise RequestCancelled("用户已取消解题请求") from exc
            error_body = exc.read()
            error_text = error_body.decode("utf-8", errors="replace").lower()
            if exc.code in (400, 422):
                if "stream" in request_payload and (
                        "stream" in error_text or "event" in error_text):
                    request_payload.pop("stream", None)
                    request_payload.pop("stream_options", None)
                    continue
                if "stream_options" in request_payload and (
                        "stream_options" in error_text or "include_usage" in error_text):
                    request_payload.pop("stream_options", None)
                    continue
                if "response_format" in request_payload and (
                        "response_format" in error_text or "json_object" in error_text):
                    request_payload.pop("response_format", None)
                    continue
            raise urllib.error.HTTPError(
                exc.url, exc.code, exc.reason, exc.headers, io.BytesIO(error_body)) from exc
        except Exception as exc:
            if request_control is not None and request_control.cancelled:
                raise RequestCancelled("用户已取消解题请求") from exc
            raise
    raise RuntimeError("API 多次返回不兼容的请求格式")


def check_api_connection(timeout=25.0):
    """用短文本请求检查 API 地址、密钥和模型；不上传题卡图片。"""
    configured, base_url, api_key, model = _api_settings()
    if not configured:
        return {"connected": False, "model": model,
                "error": "请填写 HTTPS API 地址、模型名称和 API Key"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复 OK"}],
        **_sampling_parameters(),
        _token_limit_field(): 8,
    }
    request = urllib.request.Request(
        _endpoint(base_url),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key,
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", errors="replace"))
        if not (body.get("choices") or [{}])[0].get("message"):
            raise ValueError("接口未返回模型答复")
        return {"connected": True, "model": model}
    except urllib.error.HTTPError as exc:
        return {"connected": False, "model": model,
                "error": "API 返回 HTTP %s" % exc.code}
    except (OSError, http.client.HTTPException, ValueError, KeyError, IndexError, TypeError,
            urllib.error.URLError) as exc:
        return {"connected": False, "model": model,
                "error": str(exc)[:240]}


def _endpoint(base_url):
    url = str(base_url or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def _image_data(path):
    """优先缩小图片再编码，避免把原始超大扫描件直接发送给模型。"""
    try:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if not image.isNull():
            scale = min(1.0, 2400.0 / max(image.width(), image.height()))
            if scale < 1.0:
                image = image.scaled(
                    round(image.width() * scale), round(image.height() * scale),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
            # QBuffer 引用的 QByteArray 必须保持 Python 生命周期；直接传临时对象时，
            # 较大的外部扫描图可能在 JPEG 编码阶段访问已释放的缓冲区并导致进程崩溃。
            image_data = QByteArray()
            buffer = QBuffer(image_data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            # 题卡的小字、角度符号和红色标注很容易被低质量 JPEG 压糊。
            if image.save(buffer, "JPG", 94):
                return "data:image/jpeg;base64," + base64.b64encode(
                    bytes(buffer.data())).decode("ascii")
    except Exception:
        pass
    with open(path, "rb") as source:
        data = source.read(12 * 1024 * 1024 + 1)
    if len(data) > 12 * 1024 * 1024:
        return ""
    suffix = os.path.splitext(path)[1].lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))


def _repair_unescaped_json_quotes(text):
    """转义 JSON 字符串内部意外出现的未转义双引号。"""
    output = []
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            output.append(char)
            escaped = False
            continue
        if char == "\\" and in_string:
            output.append(char)
            escaped = True
            continue
        if char == '"':
            if not in_string:
                in_string = True
                output.append(char)
                continue
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            next_char = text[lookahead] if lookahead < len(text) else ""
            if next_char in ("", ":", ",", "}", "]"):
                in_string = False
                output.append(char)
            else:
                output.append('\\"')
            continue
        output.append(char)
    return "".join(output)


def _response_json(text):
    if isinstance(text, dict):
        return text
    if isinstance(text, list):
        text = _response_content_text(text)
    value = str(text or "").strip()
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I)
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = value[start:end + 1]
    candidates = [candidate]
    repaired = _repair_unescaped_json_quotes(candidate)
    if repaired != candidate:
        candidates.append(repaired)
    # 部分本地 OpenAI 兼容接口会把模型 JSON 再转义一层，返回形如
    # {\\n  \\"complete\\": true} 的文本。先按普通 JSON 解析；失败后再
    # 仅解开这一层转义，避免把正确内容误判成“模型没有结果”。
    escaped = candidate.replace("\r", "\\r").replace("\n", "\\n")
    try:
        unwrapped = json.loads('"' + escaped + '"')
        if isinstance(unwrapped, str):
            candidates.append(unwrapped)
            unwrapped_repaired = _repair_unescaped_json_quotes(unwrapped)
            if unwrapped_repaired != unwrapped:
                candidates.append(unwrapped_repaired)
    except (TypeError, ValueError):
        pass
    for item in candidates:
        try:
            result = json.loads(item)
        except (TypeError, ValueError):
            continue
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (TypeError, ValueError):
                continue
        if isinstance(result, dict):
            return result
    return None


def _response_content_text(content):
    """兼容不同 OpenAI 兼容接口返回的文本分块。"""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("text", "output_text", "content", "value"):
            value = content.get(key)
            if isinstance(value, str):
                return value
        return ""
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("text", item.get("output_text"))
                if isinstance(value, str):
                    parts.append(value)
        return "".join(parts)
    return ""


def _completion_response_json(body):
    """提取 Chat Completions 与兼容服务的结构化答案及结束原因。"""
    body = body if isinstance(body, dict) else {}
    choices = body.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") or choice.get("delta") or {}
    message = message if isinstance(message, dict) else {}
    content = message.get("content")

    if isinstance(content, dict) and any(
            key in content for key in ("solution", "answer", "complete",
                                       "condition_items", "diagram_model")):
        parsed = content
        raw_text = json.dumps(content, ensure_ascii=False)
    else:
        raw_text = _response_content_text(content)
        parsed = _response_json(raw_text)

    if not isinstance(parsed, dict):
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function") or {}
            arguments = function.get("arguments") if isinstance(function, dict) else None
            parsed = _response_json(arguments)
            if isinstance(parsed, dict):
                raw_text = raw_text or _response_content_text(arguments)
                break
    if not isinstance(parsed, dict) and isinstance(message.get("function_call"), dict):
        parsed = _response_json(message["function_call"].get("arguments"))

    if not isinstance(parsed, dict) and not choices:
        raw_text = _response_content_text(body.get("output_text"))
        parsed = _response_json(raw_text)

    finish_reason = str(choice.get("finish_reason") or "").strip().lower()
    return parsed, finish_reason, len(raw_text or "")


def _plain_answer_from_response(body, model):
    """兼容服务直接回 Markdown 的情况，避免可读答案被误判为空白。"""
    choices = body.get("choices") if isinstance(body, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else {}
    message = choice.get("message") if isinstance(choice, dict) else {}
    content = message.get("content") if isinstance(message, dict) else None
    text = _response_content_text(content)
    if not text and isinstance(body, dict):
        text = _response_content_text(body.get("output_text"))
    candidate = text.strip("\ufeff\n\r ")
    if not candidate:
        return None
    return {
        "complete": str(choice.get("finish_reason") or "") != "length",
        "condition_items": [],
        "uncertain_items": [],
        "solution": {
            "summary": "%s 返回的文本参考解答" % (_safe_text(model, 120) or "模型"),
            "markdown": text,
        },
    }


def partial_answer_text(response_text):
    """从尚未结束的 JSON 流中提取可读答案字段，不把半截 JSON 展示给用户。"""
    text = str(response_text or "").lstrip("\ufeff\r\n \t")
    if not text:
        return ""
    if not text.startswith("{"):
        return text

    pattern = re.compile(
        r'"(?:answer_markdown|solution_markdown|markdown|formatted_answer|summary)"\s*:\s*"')
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    escaped = False
    end = len(text)
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            end = index
            break
    raw = text[start:end]
    for trim in range(0, min(5, len(raw)) + 1):
        candidate = raw[:len(raw) - trim] if trim else raw
        try:
            return json.loads('"' + candidate + '"')
        except (TypeError, ValueError):
            continue
    return ""


_DIAGRAM_SHAPES = {
    "三角形": "三角形", "等边三角形": "等边三角形",
    "正三角形": "等边三角形",
    "直角三角形": "直角三角形", "四边形": "四边形",
    "凹四边形": "凹四边形", "梯形": "梯形", "直角梯形": "直角梯形",
    "等腰梯形": "等腰梯形", "平行四边形": "平行四边形",
    "矩形": "矩形", "长方形": "矩形", "正方形": "正方形",
    "菱形": "菱形",
    "五边形": "五边形", "凹五边形": "五边形",
    "六边形": "六边形", "凹六边形": "六边形",
    "正六边形": "六边形", "蝴蝶形": "蝴蝶形", "蝶形": "蝴蝶形",
    "扫描航线": "扫描航线", "扫描式航线": "扫描航线",
    "往复式航线": "扫描航线", "圆形": "圆形", "圆周": "圆形",
    "圆弧航线": "圆形",
}
_DIAGRAM_SHAPE_COUNTS = {
    "三角形": 3, "等边三角形": 3, "直角三角形": 3,
    "四边形": 4, "凹四边形": 4, "梯形": 4, "直角梯形": 4,
    "等腰梯形": 4, "平行四边形": 4, "矩形": 4, "正方形": 4,
    "菱形": 4, "蝴蝶形": 4,
    "五边形": 5, "六边形": 6, "圆形": 0,
}
_POLYGON_NAMES = {
    3: "三角形", 4: "四边形", 5: "五边形", 6: "六边形",
    7: "七边形", 8: "八边形", 9: "九边形", 10: "十边形",
    11: "十一边形", 12: "十二边形", 13: "十三边形",
    14: "十四边形", 15: "十五边形", 16: "十六边形",
}
for _side_count, _polygon_name in _POLYGON_NAMES.items():
    _DIAGRAM_SHAPES.setdefault(_polygon_name, _polygon_name)
    _DIAGRAM_SHAPE_COUNTS.setdefault(_polygon_name, _side_count)


def _diagram_node(value):
    if isinstance(value, dict):
        value = value.get("id", value.get("label", ""))
    text = str(value or "").strip()
    circled = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
    if text in circled:
        return str(circled.index(text) + 1)
    match = re.fullmatch(r"(?:图形)?\s*([1-9]\d?)\s*(?:号)?(?:点)?", text)
    return match.group(1) if match else ""


def _diagram_edges(values, nodes):
    result, seen = [], set()
    if not isinstance(values, list):
        return result
    known = set(nodes)
    for value in values[:40]:
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            continue
        a, b = _diagram_node(value[0]), _diagram_node(value[1])
        key = frozenset((a, b))
        if a not in known or b not in known or a == b or key in seen:
            continue
        seen.add(key)
        result.append([a, b])
    return result


def _sanitize_diagram_model(value, fallback_diagram=None):
    """把视觉模型的图形判断约束成有限、可显示的点线草案。"""
    fallback = fallback_diagram if isinstance(fallback_diagram, dict) else {}
    if not isinstance(value, dict):
        if (fallback.get("status") != "ready" or
                len(fallback.get("nodes") or []) < 3 or
                not fallback.get("shape")):
            return None
        value = {
            "shape": fallback.get("shape"),
            "nodes": fallback.get("nodes", []),
            "geometry_edges": fallback.get("geometry_edges", []),
            "point_positions": fallback.get("point_positions", {}),
            "confidence": fallback.get("confidence", 0.0),
        }
    raw_shape = str(value.get("shape") or "").strip()
    shape = _DIAGRAM_SHAPES.get(raw_shape)
    raw_nodes = value.get("nodes") or []
    if isinstance(raw_nodes, str):
        raw_nodes = re.findall(r"[1-9]\d?", raw_nodes)
    nodes = list(dict.fromkeys(
        node for node in (_diagram_node(item) for item in raw_nodes[:20])
        if node and int(node) <= 16)) if isinstance(raw_nodes, (list, tuple)) else []
    raw_positions = value.get("point_positions") or {}
    evidence_nodes = []
    for raw_edge in value.get("geometry_edges", []) or []:
        if isinstance(raw_edge, (list, tuple)):
            evidence_nodes.extend(_diagram_node(item) for item in raw_edge[:2])
    raw_order = value.get("flight_order") or []
    if isinstance(raw_order, str):
        raw_order = re.findall(r"[1-9]\d?", raw_order)
    if isinstance(raw_order, (list, tuple)):
        evidence_nodes.extend(_diagram_node(item) for item in raw_order[:20])
    for raw_edge in value.get("flight_edges", []) or []:
        if isinstance(raw_edge, (list, tuple)):
            evidence_nodes.extend(_diagram_node(item) for item in raw_edge[:2])
    if isinstance(raw_positions, dict):
        evidence_nodes.extend(_diagram_node(item) for item in raw_positions)
    nodes = list(dict.fromkeys(nodes + [
        node for node in evidence_nodes if node and int(node) <= 16]))
    fallback_nodes = [str(node) for node in fallback.get("nodes", [])
                      if str(node).isdigit() and 1 <= int(node) <= 16]
    if (fallback.get("status") == "ready" and fallback_nodes and
            (not nodes or set(nodes).issubset(set(fallback_nodes)))):
        nodes = fallback_nodes
    if (not shape and fallback.get("status") == "ready" and
            fallback.get("shape")):
        shape = _DIAGRAM_SHAPES.get(str(fallback.get("shape")), "")
    expected = _DIAGRAM_SHAPE_COUNTS.get(shape)
    inferred_nodes = False
    if not nodes and expected:
        nodes = [str(index) for index in range(1, expected + 1)]
        inferred_nodes = True
    if not shape and len(nodes) >= 3:
        shape = _POLYGON_NAMES.get(len(nodes))
    if shape == "蝴蝶形" and not nodes:
        nodes = ["1", "2", "3", "4"]
    if shape == "圆形":
        nodes = nodes or ["1", "2", "3", "4"]
    if not shape and len(nodes) < 3:
        return None

    geometry_edges = _diagram_edges(value.get("geometry_edges"), nodes)
    fallback_edges = _diagram_edges(fallback.get("geometry_edges"), nodes)
    expected_edges = max(0, len(nodes) - (1 if shape == "扫描航线" else 0))
    if (fallback.get("status") == "ready" and set(fallback_nodes) == set(nodes)
            and len(fallback_edges) == expected_edges
            and len(geometry_edges) < expected_edges):
        geometry_edges = fallback_edges
    warnings = []
    topology_errors = []
    warnings.append("视觉图形结构尚未经过人工核对，路线仅作参考")
    if inferred_nodes:
        warnings.append("顶点编号未完整识别，点数按图形名称估算")
        topology_errors.append("顶点编号未完整识别")

    # 图形闭环和飞行顺序分开核验。多边形边不完整时拒绝猜边；
    # 航序缺失或闭合要求暂未判断只作为路线待核验提示，不否定图形识别。
    if shape not in ("圆形", "扫描航线") and len(nodes) >= 3:
        degree = {node: 0 for node in nodes}
        adjacency = {node: set() for node in nodes}
        for first, second in geometry_edges:
            degree[first] += 1
            degree[second] += 1
            adjacency[first].add(second)
            adjacency[second].add(first)
        geometry_complete = (
            len(geometry_edges) == len(nodes) and
            all(value == 2 for value in degree.values()))
        if geometry_complete:
            reached, pending = set(), [nodes[0]]
            while pending:
                current = pending.pop()
                if current in reached:
                    continue
                reached.add(current)
                pending.extend(adjacency[current] - reached)
            geometry_complete = len(reached) == len(nodes)
        if not geometry_complete:
            warnings.append("图形边未识别成完整闭合轮廓；不会按点号猜边")
            topology_errors.append("图形边不完整或连接关系不构成单环")
    elif shape == "扫描航线":
        connected_nodes = {node for edge in geometry_edges for node in edge}
        if len(geometry_edges) < max(1, len(nodes) - 1) or not set(nodes).issubset(connected_nodes):
            warnings.append("扫描航线的全部连接线未识别完整")
            topology_errors.append("扫描航线连线不完整")

    raw_order = value.get("flight_order") or []
    if isinstance(raw_order, str):
        raw_order = re.findall(r"[1-9]\d?", raw_order)
    order = list(dict.fromkeys(
        node for node in (_diagram_node(item) for item in raw_order[:20])
        if node in nodes)) if isinstance(raw_order, (list, tuple)) else []
    order_complete = len(order) == len(nodes) and set(order) == set(nodes)
    if not order_complete:
        warnings.append("题面航点顺序未完整识别；不会按编号顺序补造路线")
    raw_closed = value.get("flight_closed")
    flight_closed = raw_closed if isinstance(raw_closed, bool) else None
    if not isinstance(raw_closed, bool):
        warnings.append("航线是否闭合未从题面确认")
    flight_edges = [[order[index], order[index + 1]]
                    for index in range(len(order) - 1)]
    if flight_closed is True and len(order) >= 3:
        flight_edges.append([order[-1], order[0]])

    positions = {}
    if isinstance(raw_positions, dict):
        for raw_name, raw_pair in list(raw_positions.items())[:20]:
            name = _diagram_node(raw_name)
            if name not in nodes:
                continue
            try:
                if isinstance(raw_pair, dict):
                    x, y = float(raw_pair.get("x")), float(raw_pair.get("y"))
                else:
                    x, y = float(raw_pair[0]), float(raw_pair[1])
                if max(abs(x), abs(y)) <= 1.0:
                    x, y = x * 1000.0, y * 1000.0
                if (0.0 <= x <= 1000.0 and 0.0 <= y <= 1000.0 and
                        math.isfinite(x) and math.isfinite(y)):
                    positions[name] = {"x": x, "y": y}
            except (TypeError, ValueError, IndexError, OverflowError):
                continue
    fallback_positions = fallback.get("point_positions") or {}
    if fallback.get("status") == "ready" and set(fallback_nodes) == set(nodes):
        for name in nodes:
            if name not in positions and name in fallback_positions:
                positions[name] = dict(fallback_positions[name])

    geometry_keys = {frozenset(edge) for edge in geometry_edges}
    parallel_edge_pairs = []
    raw_parallel_pairs = value.get("parallel_edge_pairs") or []
    if isinstance(raw_parallel_pairs, (list, tuple)):
        for raw_pair in raw_parallel_pairs[:8]:
            if not isinstance(raw_pair, (list, tuple)) or len(raw_pair) != 2:
                continue
            parsed_pair = []
            for raw_edge in raw_pair:
                if not isinstance(raw_edge, (list, tuple)) or len(raw_edge) != 2:
                    parsed_pair = []
                    break
                edge = [_diagram_node(item) for item in raw_edge]
                if (not all(edge) or edge[0] == edge[1] or
                        frozenset(edge) not in geometry_keys):
                    parsed_pair = []
                    break
                parsed_pair.append(edge)
            if (len(parsed_pair) == 2 and
                    frozenset(parsed_pair[0]) != frozenset(parsed_pair[1]) and
                    not set(parsed_pair[0]).intersection(parsed_pair[1])):
                parallel_edge_pairs.append(parsed_pair)

    if shape == "四边形" and len(nodes) == 4:
        if len(parallel_edge_pairs) == 1:
            shape = "梯形"
        elif len(parallel_edge_pairs) == 2:
            shape = "平行四边形"

    try:
        confidence = float(value.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "source": "vision_model", "shape": shape,
        "nodes": nodes, "geometry_edges": geometry_edges,
        "flight_order": order, "flight_edges": flight_edges,
        "flight_closed": flight_closed,
        "point_positions": positions,
        "parallel_edge_pairs": parallel_edge_pairs,
        "confidence": max(0.0, min(1.0, confidence)),
        "warnings": warnings,
        "topology_errors": topology_errors,
        "topology_valid": not topology_errors,
    }


def _diagram_is_complete(diagram):
    if not isinstance(diagram, dict):
        return False
    shape = str(diagram.get("shape") or "")
    nodes = diagram.get("nodes") or []
    edges = diagram.get("geometry_edges") or []
    if not shape or len(nodes) < 3 or not diagram.get("topology_valid"):
        return False
    # 圆形轮廓没有直边列表，点数由题面条件单独描述。
    if shape == "圆形":
        return True
    return len(edges) >= 3


def _diagram_review_fallback(diagram_model, reason):
    """API 不可用时保留本地确认的图形，不冒充完整答案。"""
    if (not isinstance(diagram_model, dict) or
            diagram_model.get("status") != "ready" or
            not diagram_model.get("shape") or
            len(diagram_model.get("nodes") or []) < 3):
        return None
    diagram = copy.deepcopy(diagram_model)
    diagram["source"] = "local_cv"
    return {
        "provider": "local_cv", "model": "本地几何识别",
        "status": "review", "solver_usable": False,
        "answer_mode": "candidate", "complete": False,
        "normalized_conditions": "", "condition_items": [],
        "uncertain_items": [
            str(reason or "视觉模型没有返回可用结果")[:300],
            "本地已识别图形和航点，但题目条件未完成识别；不会据此生成或核验参考答案。",
        ],
        "diagram_model": diagram,
        "diagram_complete": _diagram_is_complete(diagram),
        "experience_count": 0, "experience_cases": [],
    }


def refine_diagram_geometry(image_path, ocr_words, source_image_size,
                            diagram_model):
    """用题卡线段校正已缓存的视觉草图，不调用视觉模型。"""
    if not isinstance(diagram_model, dict) or not ocr_words:
        return diagram_model
    if (diagram_model.get("source") == "local_cv" and
            diagram_model.get("status") == "ready"):
        return diagram_model
    if (diagram_model.get("geometry_source") == "local_cv_hough" and
            diagram_model.get("geometry_validated")):
        return diagram_model
    try:
        import diagram_vision as DV
        if diagram_model.get("shape") in ("圆形", "扫描航线"):
            recovered = DV.analyze(
                image_path, ocr_words, shape_hint=diagram_model.get("shape"))
            if (recovered.get("status") != "ready" or
                    set(map(str, recovered.get("nodes", []))) !=
                    set(map(str, diagram_model.get("nodes", [])))):
                recovered = None
        else:
            recovered = DV.refine_partial_polygon(
                image_path, ocr_words, source_image_size, diagram_model)
    except Exception:
        recovered = None
    if not recovered:
        return diagram_model

    visual_diagram = copy.deepcopy(diagram_model)
    prior_warnings = list(visual_diagram.get("warnings", []))
    prior_errors = list(visual_diagram.get("topology_errors", []))
    visual_diagram.update(recovered)
    visual_diagram["source"] = "vision_model"
    visual_diagram["geometry_source"] = "local_cv_hough"
    visual_diagram["geometry_validated"] = True
    visual_diagram["geometry_confidence"] = recovered.get(
        "geometry_confidence", 0.0)
    visual_diagram["warnings"] = [
        warning for warning in prior_warnings
        if warning != "视觉图形结构尚未经过人工核对，路线仅作参考"]
    visual_diagram["warnings"].extend(recovered.get("warnings", []))
    visual_diagram["warnings"] = list(dict.fromkeys(visual_diagram["warnings"]))
    visual_diagram["topology_errors"] = [
        error for error in prior_errors
        if error not in ("图形边不完整或连接关系不构成单环",
                         "顶点编号未完整识别")]
    visual_diagram["topology_valid"] = not visual_diagram["topology_errors"]
    return visual_diagram


def organize_conditions(image_path, ocr_text, diagram_model=None,
                        progress_callback=None):
    """兼容旧识别结果的整理入口；当前题卡作答按钮改用 solve_question。"""
    def report(message):
        if callable(progress_callback):
            try:
                progress_callback(str(message))
            except Exception:
                pass

    diagram_context = diagram_model if isinstance(diagram_model, dict) else {}
    ocr_words = diagram_context.get("_ocr_words") or []
    source_image_size = diagram_context.get("_source_image_size")
    diagram_model = {key: value for key, value in diagram_context.items()
                     if not str(key).startswith("_")}
    local_cv_complete = (
        diagram_model.get("source") == "local_cv" and
        diagram_model.get("status") == "ready" and
        bool(diagram_model.get("topology_valid")))
    point_position_rule = (
        "detected_diagram 已有本地 CV 验证的完整边和点位，diagram_model 不要返回 "
        "point_positions，避免重复输出；"
        if local_cv_complete else
        "point_positions 按整张原图宽高归一化到0至1000；若不能确认顶点位置就留空，不要猜；")
    point_position_schema = (
        "\"point_positions\":{\"1\":{\"x\":500,\"y\":200}},"
        if not local_cv_complete else "")
    diagram_output_rule = (
        "本地CV已经从原图确认了完整顶点和图形边，直接沿用 detected_diagram；"
        "不要重复输出 geometry_edges 或 point_positions。只核对图形名称、点号，"
        "并根据题面明确文字填写 flight_order 与 flight_closed。"
        if local_cv_complete else
        "必须从图片识别图形边和点位；无法确认时留空并说明。")
    diagram_edge_schema = (
        "\"flight_order\":[],\"flight_closed\":null,"
        if local_cv_complete else
        "\"geometry_edges\":[[\"1\",\"2\"],[\"2\",\"3\"]],"
        "\"flight_order\":[],\"flight_closed\":null,")
    enabled, base_url, api_key, model = _api_settings()
    if not enabled:
        return _diagram_review_fallback(
            diagram_model, "请先在设置中填写 HTTPS 视觉模型 API 地址、模型和 API Key") or {
                "provider": "api", "model": model,
                "status": "unconfigured", "solver_usable": False,
                "reason": "视觉模型 API 尚未配置"}
    try:
        report("正在准备题卡图像…")
        image = _image_data(image_path)
    except OSError as exc:
        fallback = _diagram_review_fallback(diagram_model, str(exc))
        return fallback or {"provider": "api", "model": model,
                "status": "unavailable", "solver_usable": False,
                "reason": str(exc)[:240]}
    if not image:
        fallback = _diagram_review_fallback(
            diagram_model, "题卡图片超过视觉模型上传限制")
        return fallback or {"provider": "api",
                            "status": "image_too_large", "solver_usable": False}

    try:
        import reference_experience as RX
        report("正在检索题库中已核验的相似解题经验…")
        experience_rules = RX.domain_rules()
        experience_examples = RX.retrieve_examples(
            ocr_text, diagram_model=diagram_model, limit=2)
    except Exception:
        experience_rules, experience_examples = [], []

    instructions = (
        "你是 CAAC 无人机地面站题卡的视觉审题助手。请同时完成两件事：逐条抄录题目条件，"
        "并识别题图的几何点位与连线拓扑。不要替换或猜测题面数字。尤其要忠实保留‘=’、"
        "‘≈’、‘-’和角度关系的区别；例如‘1-2=2-3=4-5=5-1=92m’必须作为等长关系保留，"
        "不能改写成‘1-2-2-3-4-5-5-1’。不要求解经纬度。看不清处放入 uncertain_items。"
        "按固定流程审题：先确认图形名称、所有标号顶点和图形实际边；再逐条提取坐标、边长、"
        "等长、角度、方位、风向和航段关系；最后抄录题面明确给出的飞行顺序。图形边与飞行"
        "航线是不同字段，不能互相替代。不要根据常见题型补造坐标、距离、风向、进场点或航线。"
        "下面提供的历史案例只用于学习题面条件的拆分方式和图形连线表达；严禁照抄案例数字、"
        "坐标、图形编号或航向。题面明确有起飞点到避让点再到1号点时，必须分别保留两段。"
        "图形结构只按图片可见内容填写：识别三角形、四边形（可细分矩形、正方形、梯形、"
        "平行四边形、凹四边形）、五边形、六边形、蝴蝶形、扫描航线、圆形；其他 3 至 16 点"
        "闭合多边形写成对应的 n 边形；nodes 是题图上标出的顶点编号；"
        "四边形必须仔细比较两组对边的方向，若确实平行，在 parallel_edge_pairs 中写出边对；"
        "只凭‘像梯形’的名称不能补平行边。不得把斜边拉直、把凹形改成凸形，不能将草图规整成"
        "标准矩形；图中有倾斜就按倾斜返回。"
        "geometry_edges 是图形实际画出的边，不要把交叉航线误当成边；flight_order 是题面明确"
        "规定的飞行点号顺序，只有边界连线而没有方向时返回空数组；flight_closed 只有明确要求闭合才填 true，"
        "明确不闭合填 false，否则填 null。" + diagram_output_rule + " " +
        point_position_rule + "只输出 JSON："
        "{\"complete\":true/false,\"condition_items\":[{\"normalized\":\"单条条件\","
        "\"visual_evidence\":\"对应原文\",\"confidence\":0到1}],"
        "\"uncertain_items\":[\"无法确认的内容\"],\"diagram_model\":{"
        "\"shape\":\"图形名称\",\"nodes\":[\"1\",\"2\",\"3\"],"
        + diagram_edge_schema +
        "\"parallel_edge_pairs\":[[[\"1\",\"2\"],[\"3\",\"4\"]]],"
        + point_position_schema +
        "\"confidence\":0到1}}。每条条件要有图片原文依据；无法确认的图形细节也要记入 uncertain_items。"
    )
    user_context = {
        # 图片已携带主要视觉信息，OCR 原文保留前 7000 字符，避免长题卡挤掉图形信息。
        "ocr_text": str(ocr_text or "")[:7000],
        "detected_diagram": diagram_model or {},
        "domain_rules": experience_rules,
        "retrieved_experience_examples": experience_examples,
        "task": "识别题面文字、图形顶点、图形边和题面明确的航点顺序，不做数值求解",
    }
    payload = {
        "model": model,
        **_sampling_parameters(),
        _token_limit_field(): 4096,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": [
                {"type": "text", "text": json.dumps(
                    user_context, ensure_ascii=False)},
                {"type": "image_url", "image_url": {"url": image, "detail": "high"}},
            ]},
        ],
    }
    finish_reason = ""
    try:
        report("正在通过视觉模型 API 识别题卡图文…")
        body = _chat_request(base_url, api_key, payload, timeout=120)
        report("识别完成，正在整理并校验条件…")
        finish_reason = str((body.get("choices") or [{}])[0].get(
            "finish_reason") or "")
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(str(item.get("text", "")) for item in content
                              if isinstance(item, dict))
        parsed = _response_json(content)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            detail = ""
        if api_key:
            detail = detail.replace(api_key, "[已隐藏]")
        reason = "视觉模型 API 返回 HTTP %s%s" % (
            exc.code, ("：" + detail[:360]) if detail else "")
        fallback = _diagram_review_fallback(diagram_model, reason)
        return fallback or {"provider": "api", "model": model,
                "status": "unavailable", "solver_usable": False,
                "reason": reason}
    except (OSError, ValueError, KeyError, IndexError, TypeError,
            urllib.error.URLError) as exc:
        fallback = _diagram_review_fallback(diagram_model, str(exc))
        return fallback or {"provider": "api", "model": model,
                "status": "unavailable", "solver_usable": False,
                "reason": str(exc)[:240]}
    if not parsed:
        reason = ("视觉模型输出达到长度上限，JSON 被截断"
                  if finish_reason == "length" else
                  "视觉模型返回内容不是可用的 JSON")
        fallback = _diagram_review_fallback(
            diagram_model, reason)
        return fallback or {"provider": "api", "model": model,
                "status": "invalid_response", "solver_usable": False,
                "reason": reason}

    items = []
    raw_items = parsed.get("condition_items", [])
    if not isinstance(raw_items, list):
        raw_items = []
    for item in raw_items[:80]:
        if not isinstance(item, dict):
            continue
        normalized = str(item.get("normalized", "")).strip()
        evidence = str(item.get("visual_evidence", "")).strip()
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        if normalized and evidence and len(normalized) <= 500 and len(evidence) <= 500:
            items.append({"normalized": normalized, "visual_evidence": evidence,
                          "confidence": max(0.0, min(1.0, confidence))})
    raw_uncertain = parsed.get("uncertain_items", [])
    if not isinstance(raw_uncertain, list):
        raw_uncertain = []
    uncertain = [str(value)[:300] for value in raw_uncertain[:40]
                 if str(value).strip()]
    visual_diagram = _sanitize_diagram_model(
        parsed.get("diagram_model"), fallback_diagram=diagram_model)
    # 带圈编号与图形连线由本地 CV 从题卡像素中直接检测，比 VLM
    # 生成的示意坐标更适合作为形状先验。只在 CV 给出完整闭环时覆盖；
    # 航线顺序仍须来自题面/VLM，不能把图形边自动当成飞行航线。
    if (visual_diagram and isinstance(diagram_model, dict) and
            diagram_model.get("source") == "local_cv" and
            diagram_model.get("status") == "ready"):
        cv_nodes = [str(node) for node in diagram_model.get("nodes", [])]
        cv_edges = [[str(edge[0]), str(edge[1])]
                    for edge in diagram_model.get("geometry_edges", [])
                    if isinstance(edge, (list, tuple)) and len(edge) >= 2]
        cv_positions = diagram_model.get("point_positions") or {}
        same_nodes = set(cv_nodes) == set(visual_diagram.get("nodes", []))
        expected_cv_edges = (len(cv_nodes) - 1
                             if diagram_model.get("shape") == "扫描航线"
                             else len(cv_nodes))
        if (same_nodes and len(cv_edges) == expected_cv_edges and
                all(node in cv_positions for node in cv_nodes)):
            visual_diagram["geometry_edges"] = cv_edges
            visual_diagram["point_positions"] = {
                node: cv_positions[node] for node in cv_nodes}
            visual_diagram["geometry_source"] = "local_cv"
            visual_diagram["geometry_validated"] = bool(
                diagram_model.get("topology_valid") or
                diagram_model.get("validated_cycle"))
            visual_diagram["geometry_confidence"] = float(
                diagram_model.get("confidence", 0.0) or 0.0)
            visual_diagram["parallel_edge_pairs"] = [
                pair for pair in diagram_model.get("parallel_edge_pairs", [])
                if isinstance(pair, (list, tuple)) and len(pair) == 2]
            if diagram_model.get("shape") in (
                    "等边三角形", "直角三角形", "梯形", "五边形",
                    "蝴蝶形", "六边形", "扫描航线", "圆形",
                    "平行四边形"):
                visual_diagram["shape"] = diagram_model["shape"]
            if diagram_model.get("geometry_kind"):
                visual_diagram["geometry_kind"] = diagram_model["geometry_kind"]
            if visual_diagram["geometry_validated"]:
                visual_diagram["topology_errors"] = [
                    error for error in visual_diagram.get("topology_errors", [])
                    if error not in ("图形边不完整或连接关系不构成单环",
                                     "扫描航线连线不完整")]
                visual_diagram["topology_valid"] = not visual_diagram["topology_errors"]
                visual_diagram["warnings"] = [
                    warning for warning in visual_diagram.get("warnings", [])
                    if warning not in ("图形边未识别成完整闭合轮廓；不会按点号猜边",
                                       "扫描航线的全部连接线未识别完整")]
    if visual_diagram and ocr_words:
        visual_diagram = refine_diagram_geometry(
            image_path, ocr_words, source_image_size, visual_diagram)
    if visual_diagram:
        for value in visual_diagram.get("warnings", []):
            warning = str(value).strip()
            if not warning:
                continue
            # 这是本地 CV 的处理说明，不代表题面识别存在疑点。
            if warning == "本地线段检测已恢复题卡图形轮廓":
                continue
            if (visual_diagram.get("geometry_source") in
                    ("local_cv", "local_cv_hough") and
                    visual_diagram.get("geometry_validated") and
                    warning == "视觉图形结构尚未经过人工核对，路线仅作参考"):
                continue
            uncertain.append(warning[:300])
    normalized = "\n".join(item["normalized"] for item in items)
    fully_recognized = (
        bool(parsed.get("complete")) and not uncertain and bool(items) and
        all(item["confidence"] >= 0.72 for item in items))
    # 识别完整度决定能否标记为“已核对”，不决定是否尝试生成参考草案。
    # 有原图依据的条件先交给求解器；低置信或不确定项由界面单独提示。
    solver_usable = bool(items or visual_diagram)
    diagram_complete = _diagram_is_complete(visual_diagram)
    return {
        "provider": "api", "model": model,
        "status": "ready" if fully_recognized else "review",
        "solver_usable": solver_usable,
        "answer_mode": "verified_eligible" if fully_recognized else "candidate",
        "complete": bool(parsed.get("complete")),
        "normalized_conditions": normalized,
        "condition_items": items, "uncertain_items": uncertain,
        "diagram_model": visual_diagram,
        "diagram_complete": diagram_complete,
        "experience_count": len(experience_examples),
        "experience_cases": [str(item.get("case_id", ""))
                             for item in experience_examples],
    }


def _point_name(value):
    """把模型给出的点名归一成航图使用的 H、A 和数字点名。"""
    if isinstance(value, dict):
        value = value.get("id", value.get("label", value.get("name", "")))
    text = re.sub(r"\s+", "", str(value or "").strip()).upper()
    if text in ("H", "起飞点", "起点", "返航点", "起飞/返航点", "返航/起飞点"):
        return "H"
    if text in ("A", "避让点", "绕行点"):
        return "A"
    if text in ("T", "过渡点", "接入点"):
        return "T"
    match = re.fullmatch(r"(?:图形)?(\d{1,2})(?:号)?(?:点)?", text)
    if match and 1 <= int(match.group(1)) <= 16:
        return str(int(match.group(1)))
    return ""


def _safe_text(value, limit=1200):
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not math.isfinite(float(value)):
        return ""
    return str(value).strip()[:limit]


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value or ""))
    if not match:
        return None
    try:
        result = float(match.group(0))
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _normalize_edges(raw_edges, known_points=None, limit=64):
    edges, seen = [], set()
    if not isinstance(raw_edges, (list, tuple)):
        return edges
    known = set(known_points or [])
    for edge in raw_edges[:limit]:
        if not isinstance(edge, (list, tuple)) or len(edge) < 2:
            continue
        first, second = _point_name(edge[0]), _point_name(edge[1])
        if (not first or not second or first == second or
                (known and (first not in known or second not in known))):
            continue
        key = (first, second)
        if key not in seen:
            seen.add(key)
            edges.append([first, second])
    return edges


def _normalize_flight_legs(raw_legs):
    legs = []
    if not isinstance(raw_legs, list):
        return legs
    for item in raw_legs[:64]:
        if not isinstance(item, dict):
            continue
        first = _point_name(item.get("from", item.get("start", item.get("起点"))))
        second = _point_name(item.get("to", item.get("end", item.get("终点"))))
        if not first or not second or first == second:
            continue
        leg = {"from": first, "to": second}
        bearing = _number(item.get("bearing_deg", item.get("bearing", item.get("航向"))))
        distance = _number(item.get("distance_m", item.get("distance", item.get("距离"))))
        if bearing is not None:
            leg["bearing_deg"] = bearing % 360.0
        if distance is not None and 0 <= distance <= 100000:
            leg["distance_m"] = distance
        elif item.get("distance_m") is not None or item.get("distance") is not None:
            leg["distance_text"] = _safe_text(
                item.get("distance_text", item.get("distance_m", item.get("distance"))), 80)
        for key, aliases in (
                ("heading", ("heading", "航向说明")),
                ("wind", ("wind", "风向处理")),
                ("note", ("note", "说明", "reason"))):
            value = next((item.get(name) for name in aliases if item.get(name) is not None), None)
            if value is not None:
                leg[key] = _safe_text(value, 240)
        legs.append(leg)
    return legs


def _normalize_answer_coordinates(raw_items):
    results = []
    if not isinstance(raw_items, list):
        return results
    for item in raw_items[:32]:
        if not isinstance(item, dict):
            continue
        point = _point_name(item.get("point", item.get("name", item.get("点名"))))
        entry = {"point": point or _safe_text(item.get("point", item.get("name")), 40)}
        for key, aliases in (
                ("latitude", ("latitude", "lat", "纬度")),
                ("longitude", ("longitude", "lon", "lng", "经度")),
                ("altitude_m", ("altitude_m", "altitude", "height_m", "高度"))):
            value = next((item.get(alias) for alias in aliases if item.get(alias) is not None), None)
            if value is not None:
                entry[key] = _safe_text(value, 80) if isinstance(value, str) else _number(value)
        note = item.get("note", item.get("说明"))
        if note:
            entry["note"] = _safe_text(note, 200)
        if len(entry) > 1:
            results.append(entry)
    return results


def _normalize_map_points(raw_points, diagram):
    points = {}
    if isinstance(diagram, dict):
        for label, pair in (diagram.get("point_positions") or {}).items():
            name = _point_name(label)
            if not name:
                continue
            x, y = _number((pair or {}).get("x") if isinstance(pair, dict) else None), \
                _number((pair or {}).get("y") if isinstance(pair, dict) else None)
            if x is None and isinstance(pair, (list, tuple)) and len(pair) >= 2:
                x, y = _number(pair[0]), _number(pair[1])
            if x is not None and y is not None and abs(x) <= 10000 and abs(y) <= 10000:
                if max(abs(x), abs(y)) <= 1.0:
                    x, y = x * 1000.0, y * 1000.0
                points[name] = {"x": x, "y": y}
    if isinstance(raw_points, dict):
        for label, pair in list(raw_points.items())[:40]:
            name = _point_name(label)
            if not name:
                continue
            if isinstance(pair, dict):
                x, y = _number(pair.get("x")), _number(pair.get("y"))
            elif isinstance(pair, (list, tuple)) and len(pair) >= 2:
                x, y = _number(pair[0]), _number(pair[1])
            else:
                continue
            if x is None or y is None or abs(x) > 10000 or abs(y) > 10000:
                continue
            if max(abs(x), abs(y)) <= 1.0:
                x, y = x * 1000.0, y * 1000.0
            points[name] = {"x": x, "y": y}
    return points


def _complete_route_map(points, flight_legs):
    """按航段方位和距离生成东、北为正的局部坐标。"""
    legs = [leg for leg in flight_legs
            if leg.get("bearing_deg") is not None and leg.get("distance_m") is not None]
    if legs:
        # 模型给出的屏幕布局坐标不一定与航向角一致，不能拿它冒充北向坐标。
        points = {}
    route_distance = sum(leg["distance_m"] for leg in legs)
    scale = min(1.0, 650.0 / route_distance) if route_distance > 0 else 0.5
    for _ in range(len(legs) + 1):
        changed = False
        for leg in legs:
            first, second = leg["from"], leg["to"]
            if first in points and second in points:
                continue
            angle = math.radians(leg["bearing_deg"])
            dx = math.sin(angle) * leg["distance_m"] * scale
            # 航向角以正北为 0°；这里保存北向为正，绘图时再翻转屏幕纵轴。
            dy = math.cos(angle) * leg["distance_m"] * scale
            if first in points:
                points[second] = {"x": points[first]["x"] + dx,
                                  "y": points[first]["y"] + dy}
                changed = True
            elif second in points:
                points[first] = {"x": points[second]["x"] - dx,
                                  "y": points[second]["y"] - dy}
                changed = True
        if not points and legs:
            first = legs[0]["from"]
            points[first] = {"x": 500.0, "y": 500.0}
            changed = True
        if not changed:
            break
    if points:
        xs = [point["x"] for point in points.values()]
        ys = [point["y"] for point in points.values()]
        width, height = max(xs) - min(xs), max(ys) - min(ys)
        factor = min(820.0 / max(width, 1.0), 820.0 / max(height, 1.0))
        cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
        for point in points.values():
            point["x"] = round(500.0 + (point["x"] - cx) * factor, 2)
            point["y"] = round(500.0 + (point["y"] - cy) * factor, 2)
    return points


def _api_map_points(raw_points):
    """读取模型点位供绘制使用，只检查可绘制格式，不缩放或纠正点位。"""
    points = {}
    if not isinstance(raw_points, dict):
        return points
    for label, pair in raw_points.items():
        name = str(label)
        if isinstance(pair, dict):
            x, y = _number(pair.get("x")), _number(pair.get("y"))
        elif isinstance(pair, (list, tuple)) and len(pair) >= 2:
            x, y = _number(pair[0]), _number(pair[1])
        else:
            continue
        if not name or x is None or y is None:
            continue
        points[name] = {"x": x, "y": y}
    return points


def _api_map_edges(raw_edges, known_points):
    """保持模型点名与连线顺序，仅跳过绘图控件无法读取的字段。"""
    if not isinstance(raw_edges, (list, tuple)):
        return []
    known = set(known_points)
    return [[str(edge[0]), str(edge[1])] for edge in raw_edges
            if isinstance(edge, (list, tuple)) and len(edge) >= 2
            and (not known or (str(edge[0]) in known and str(edge[1]) in known))]


def _normalize_direct_answer(parsed, model, diagram):
    raw_answer = parsed.get("solution", parsed.get("answer"))
    top_markdown = parsed.get("answer_markdown", parsed.get("solution_markdown",
                    parsed.get("markdown", "")))
    if isinstance(raw_answer, str):
        raw_answer = {"summary": raw_answer, "markdown": raw_answer}
    if not isinstance(raw_answer, dict):
        if isinstance(parsed.get("content"), str):
            raw_answer = {"markdown": parsed["content"]}
        else:
            raw_answer = {"markdown": json.dumps(parsed, ensure_ascii=False, indent=2)}
    answer_markdown = str(
        top_markdown or raw_answer.get("markdown", raw_answer.get(
            "formatted_answer", raw_answer.get("content", ""))) or "")
    summary = str(raw_answer.get("summary", raw_answer.get("overview", "")) or "")
    steps = raw_answer.get("reasoning_steps", raw_answer.get("steps", []))
    if isinstance(steps, str):
        steps = [steps]
    if not isinstance(steps, list):
        steps = []
    steps = [_safe_text(value.get("text", value.get("content", ""))
                        if isinstance(value, dict) else value, 1200)
             for value in steps[:32]]
    steps = [value for value in steps if value]
    raw_legs = raw_answer.get("flight_legs", raw_answer.get("route", raw_answer.get("segments", [])))
    legs = [copy.deepcopy(item) for item in raw_legs if isinstance(item, dict)] \
        if isinstance(raw_legs, list) else []
    raw_coordinates = raw_answer.get("coordinates", raw_answer.get("point_coordinates", []))
    coordinates = [copy.deepcopy(item) for item in raw_coordinates if isinstance(item, dict)] \
        if isinstance(raw_coordinates, list) else []
    for item in coordinates:
        if "point" not in item:
            item["point"] = str(item.get("name", item.get("id", item.get("label", ""))))
    parameters = raw_answer.get("flight_parameters", raw_answer.get("parameters", []))
    if not isinstance(parameters, list):
        parameters = [{"name": key, "value": value}
                      for key, value in parameters.items()] if isinstance(parameters, dict) else []
    clean_parameters = []
    for item in parameters[:40]:
        if isinstance(item, dict):
            name = _safe_text(item.get("name", item.get("key", item.get("参数"))), 80)
            value = _safe_text(item.get("value", item.get("值", "")), 240)
            if name and value:
                clean_parameters.append({"name": name, "value": value})
        elif _safe_text(item, 240):
            clean_parameters.append({"name": "", "value": _safe_text(item, 240)})
    assumptions = raw_answer.get("assumptions", raw_answer.get("notes", []))
    if isinstance(assumptions, str):
        assumptions = [assumptions]
    assumptions = [_safe_text(value, 500) for value in assumptions[:24]] \
        if isinstance(assumptions, list) else []
    assumptions = [value for value in assumptions if value]

    raw_route_map = parsed.get("route_map") or raw_answer.get("route_map") or {}
    if not isinstance(raw_route_map, dict):
        raw_route_map = {}
    # 原文、原图与模型点位均保留；软件不再按航向、经纬度或本地题面重算图形。
    points = _api_map_points(raw_route_map.get("points"))
    route_svg = raw_route_map.get("svg", parsed.get("route_svg", raw_answer.get("route_svg", "")))
    route_svg = route_svg if isinstance(route_svg, str) else ""
    answer_html = raw_answer.get("html", parsed.get("answer_html", ""))
    answer_html = answer_html if isinstance(answer_html, str) else ""
    known = set(points)
    raw_geometry = raw_route_map.get("geometry_edges")
    geometry_edges = _api_map_edges(
        raw_geometry if raw_geometry is not None else [],
        known)
    raw_flight_edges = raw_route_map.get("flight_edges")
    flight_edges = _api_map_edges(raw_flight_edges, known)
    if not flight_edges:
        flight_edges = _api_map_edges(
            [[leg.get("from"), leg.get("to")] for leg in legs
             if leg.get("from") is not None and leg.get("to") is not None], known)
    access_edges = _api_map_edges(raw_route_map.get("access_edges"), known)
    if not summary and not steps and not legs and not coordinates and not clean_parameters and not answer_markdown and not answer_html and not route_svg:
        return None
    return {
        "provider": API_PROVIDER_PRESETS.get(_api_provider(), {}).get("label", "视觉 API"),
        "model": _safe_text(model, 120),
        "summary": summary,
        "answer_markdown": answer_markdown,
        "answer_html": answer_html,
        "route_svg": route_svg,
        "response_mode": "api_passthrough_v1",
        "raw_response_json": copy.deepcopy(parsed),
        "steps": steps,
        "flight_legs": legs,
        "coordinates": coordinates,
        "parameters": clean_parameters,
        "assumptions": assumptions,
        "route_map": {
            "annotations": copy.deepcopy(raw_route_map.get("annotations") or []),
            "points": points,
            "north_up": bool(raw_route_map.get("north_up")),
            "y_axis": "down" if raw_route_map.get("y_axis") == "down" else "up",
            "geometry_edges": geometry_edges,
            "flight_edges": flight_edges,
            "access_edges": access_edges,
        },
    }


def _api_usage(body):
    """从兼容接口响应中提取实际返回的 token 用量。"""
    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return None
    prompt = _number(usage.get("prompt_tokens", usage.get("input_tokens")))
    completion = _number(usage.get("completion_tokens", usage.get("output_tokens")))
    if prompt is None and completion is None:
        return None
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    cached = _number(details.get("cached_tokens")) if isinstance(details, dict) else None
    return {
        "input_tokens": int(prompt or 0),
        "cached_input_tokens": int(cached or 0),
        "output_tokens": int(completion or 0),
        "total_tokens": int(_number(usage.get("total_tokens")) or
                             (prompt or 0) + (completion or 0)),
    }


def _estimate_api_cost(usage, model):
    """按用户设置的每百万 token 单价估算；不把估算冒充服务商账单。"""
    if not usage:
        return None
    price_model = str(cfg.get("vision_api_price_model", "")).strip()
    if not price_model or price_model.casefold() != str(model or "").strip().casefold():
        return None
    try:
        input_price = float(cfg.get("vision_api_input_price_per_million", 0) or 0)
        cached_price = float(cfg.get("vision_api_cached_input_price_per_million", 0) or 0)
        output_price = float(cfg.get("vision_api_output_price_per_million", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    inputs = max(0, int(usage.get("input_tokens", 0)))
    cached = min(inputs, max(0, int(usage.get("cached_input_tokens", 0))))
    outputs = max(0, int(usage.get("output_tokens", 0)))
    amount = ((inputs - cached) * input_price + cached * cached_price +
              outputs * output_price) / 1_000_000.0
    return {"amount": amount, "currency": str(cfg.get("vision_api_currency", "USD")),
            "kind": "estimated", "price_model": price_model}


def _query_deepseek_balance(base_url, api_key, timeout=12,
                            request_control=None):
    """按 DeepSeek 官方余额端点查询账户余额；其他兼容服务不假定有此接口。"""
    base = str(base_url or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    request = urllib.request.Request(
        base + "/user/balance",
        headers={"Authorization": "Bearer " + api_key,
                 "Accept": "application/json"}, method="GET")
    with _open_response(request, timeout, request_control) as response:
        data = json.loads(response.read().decode("utf-8", errors="replace"))
    rows = data.get("balance_infos") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            total = float(row.get("total_balance"))
        except (TypeError, ValueError, OverflowError):
            continue
        result.append({"currency": str(row.get("currency") or ""),
                       "amount": total})
    return result or None


def _query_openai_month_cost(api_key, timeout=12, now=None,
                             request_control=None):
    """查询 OpenAI 官方组织费用接口的本月费用，需具备组织费用权限。"""
    now = now or datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    query = urllib.parse.urlencode({
        "start_time": int(month_start.timestamp()),
        "end_time": int(now.timestamp()),
        "bucket_width": "1d",
        "limit": 180,
    })
    request = urllib.request.Request(
        "https://api.openai.com/v1/organization/costs?" + query,
        headers={"Authorization": "Bearer " + api_key,
                 "Accept": "application/json"}, method="GET")
    with _open_response(request, timeout, request_control) as response:
        data = json.loads(response.read().decode("utf-8", errors="replace"))
    totals = {}
    for bucket in data.get("data", []) if isinstance(data, dict) else []:
        if not isinstance(bucket, dict):
            continue
        for row in bucket.get("results", []) or []:
            if not isinstance(row, dict):
                continue
            amount = row.get("amount") or {}
            if not isinstance(amount, dict):
                continue
            try:
                value = float(amount.get("value"))
            except (TypeError, ValueError, OverflowError):
                continue
            currency = str(amount.get("currency") or "USD").upper()
            totals[currency] = totals.get(currency, 0.0) + value
    return [{"currency": currency, "amount": round(amount, 6)}
            for currency, amount in sorted(totals.items())]


def account_billing_snapshot(request_control=None):
    """读取可由当前 API Key 查询的余额或费用，不把估算冒充服务商余额。"""
    configured, base_url, api_key, model = _api_settings()
    provider = _api_provider()
    result = {"provider": provider, "model": model,
              "balances": None, "month_spend": None,
              "local_estimated_spend": None, "status": "unsupported"}
    spend_map = cfg.get("vision_api_spend_by_currency", {})
    if isinstance(spend_map, dict):
        result["local_estimated_spend"] = {
            str(currency): max(0.0, float(amount or 0))
            for currency, amount in spend_map.items()
            if isinstance(amount, (int, float))}
    if not configured:
        result.update(status="unconfigured", message="请先在设置中配置视觉 API。")
        return result
    try:
        if request_control is not None:
            request_control.check()
        if provider == "deepseek":
            result["balances"] = _query_deepseek_balance(
                base_url, api_key, request_control=request_control)
            result.update(status="ok" if result["balances"] else "unavailable",
                          message=("已从服务商读取账户余额。" if result["balances"]
                                   else "DeepSeek 暂未返回账户余额。"))
            return result
        if provider == "openai" and urllib.parse.urlparse(base_url).hostname == "api.openai.com":
            result["month_spend"] = _query_openai_month_cost(
                api_key, request_control=request_control)
            result.update(status="ok", message=(
                "已查询 OpenAI 本月组织费用；OpenAI 未提供通用 Key 余额查询。"))
            return result
        result["message"] = "此服务商没有可用的通用余额查询接口；本机累计费用为估算值。"
        return result
    except urllib.error.HTTPError as exc:
        message = ("OpenAI 本月组织费用查询需要具备相应权限的 API Key。"
                   if provider == "openai" and exc.code in (401, 403)
                   else "%s 费用或余额查询失败（HTTP %s）。" % (
                       API_PROVIDER_PRESETS.get(provider, {}).get("label", "API"), exc.code))
        result.update(status="error", message=message)
    except Exception:
        result.update(status="error", message="服务商费用或余额暂时无法查询。")
    return result


def _coordinate_route_points(coordinates):
    """仅将模型明确给出的十进制经纬度投影成北向坐标，不补造点位。"""
    anchors = {}
    for item in coordinates if isinstance(coordinates, list) else []:
        if not isinstance(item, dict):
            continue
        name = _point_name(item.get("point"))
        try:
            latitude = float(item.get("latitude"))
            longitude = float(item.get("longitude"))
        except (TypeError, ValueError, OverflowError):
            continue
        if (name and math.isfinite(latitude) and math.isfinite(longitude)
                and -90 <= latitude <= 90 and -180 <= longitude <= 180):
            anchors[name] = (latitude, longitude)
    if len(anchors) < 2:
        return {}
    latitude0, longitude0 = next(iter(anchors.values()))
    east_scale = 111320.0 * math.cos(math.radians(latitude0))
    return {name: {"x": (longitude - longitude0) * east_scale,
                   "y": (latitude - latitude0) * 110540.0}
            for name, (latitude, longitude) in anchors.items()}


def direct_answer_reference(answer):
    """直接传递模型原图或点位，不求解、校正方向或重建坐标。"""
    if not isinstance(answer, dict):
        return None
    route_map = answer.get("route_map") or {}
    points = _api_map_points(route_map.get("points"))
    north_up = bool(route_map.get("north_up"))
    svg = answer.get("route_svg") or route_map.get("svg") or ""
    if len(points) < 2 and not svg:
        return None
    known = set(points)
    flight_edges = _api_map_edges(route_map.get("flight_edges"), known)
    geometry_edges = _api_map_edges(route_map.get("geometry_edges"), known)
    access_edges = _api_map_edges(route_map.get("access_edges"), known)
    note = ("模型原图 / 原坐标直显" if answer.get("response_mode") == "api_passthrough_v1"
            else "旧版缓存图形 · 重新解题可获取模型原图")
    return {
        "status": "API_DIRECT",
        "api_direct": True,
        "candidate": False,
        "points": points,
        "geometry_edges": geometry_edges,
        "flight_edges": flight_edges,
        "access_edges": access_edges,
        "north_up": north_up,
        "y_axis": route_map.get("y_axis", "up"),
        "flight_legs": copy.deepcopy(answer.get("flight_legs") or []),
        "route_svg": svg,
        "annotations": copy.deepcopy(route_map.get("annotations") or []),
        "note": note,
        "candidate_points": points,
        "candidate_geometry_edges": geometry_edges,
        "candidate_flight_edges": flight_edges,
        "candidate_access_edges": access_edges,
        "candidate_north_up": north_up,
        "candidate_flight_legs": copy.deepcopy(answer.get("flight_legs") or []),
        "candidate_note": note,
    }


def _numbered_condition_lines(text):
    lines = []
    for line in str(text or "").splitlines():
        match = re.match(r"\s*(\d{1,2})\s*[,、.．)]\s*(\S.*)", line)
        if match:
            lines.append({"id": match.group(1), "text": match.group(2).strip()})
    return lines


def _condition_item_id(item):
    explicit = str(item.get("condition_id") or "").strip()
    if explicit.isdigit():
        return explicit
    for field in ("normalized", "visual_evidence"):
        match = re.match(r"\s*(\d{1,2})\s*[,、.．)]", str(item.get(field) or ""))
        if match:
            return match.group(1)
    return ""


def solve_question(image_path, ocr_text="", diagram_model=None, source_fields=None,
                   progress_callback=None, stream_callback=None,
                   request_control=None):
    """只把原始题卡图片交给云端视觉模型；旧文本参数仅为兼容而忽略。"""
    started_at = time.monotonic()
    def report(message):
        if callable(progress_callback):
            try:
                progress_callback(str(message))
            except Exception:
                pass

    configured, base_url, api_key, model = _api_settings()
    if request_control is not None and request_control.cancelled:
        return {"provider": "api", "model": model, "status": "cancelled",
                "reason": "用户已取消解题请求",
                "elapsed_seconds": round(time.monotonic() - started_at, 2)}
    if not configured:
        return {"provider": "api", "model": model, "status": "unconfigured",
                "reason": "请先在设置中填写 HTTPS API 地址、模型名称和 API Key"}
    try:
        report("正在准备原始题卡图片…")
        image = _image_data(image_path)
    except OSError as exc:
        return {"provider": "api", "model": model, "status": "unavailable",
                "reason": _safe_text(exc, 240)}
    if not image:
        return {"provider": "api", "model": model, "status": "image_too_large",
                "reason": "题卡图片超过 API 上传限制"}

    # 不读取调用方传入的 OCR、图形检测结果、人工校对文本或历史样题。
    # 用户内容只包含题卡原图，避免本地误识别干扰模型独立判断。
    diagram_model = None
    examples = []
    instructions = (
        "你是 CAAC 无人机地面站题卡的解题教师。只根据用户消息中的题卡原图审题和计算，"
        "不要假设 OCR、人工转录或历史样题。区分图形边与实际飞行顺序；航向以正北0度、顺时针增加，"
        "每条方位表示 from 到 to。先给出可复核的数值，再做简洁校核。"
        "图中未标北向箭头时，纸面上下左右只用于辨认图形拓扑，不能当作地理方向；"
        "绝对方位以文字条件为准，不能因为题图左右摆放而颠倒起终点或取反向。"
        "三点角∠ABC的顶点是中间B，必须用B→A与B→C复核；"
        "题目写外侧夹角时仍须满足给出的角度，不得自行改成其补角。"
        "距离区间不是固定边长：选取区间内值时列明依据。"
        "闭合任务必须在flight_legs和flight_edges中包含最后回到起点的航段；"
        "回程边若未画在原题图中，不要因此加到geometry_edges。"
        "solution.markdown 写出完整作答，使用清晰的标题、公式和表格，不截断推导、坐标或参数。"
        "markdown 固定包含关键几何/航向推导、逐段航线表、航点坐标与高度、飞行参数与时间、闭合及约束校核；"
        "写出关键公式、单位和必要假设。水平飞行与爬升若同时进行，不得重复累计时间。"
        "markdown、flight_legs、coordinates 和 flight_parameters 的数值必须一致；闭合航线用东、北分量复核误差。"
        "条件冲突或图像不清时，给出有依据的最佳参考解并标注不确定项；不得编造无法推出的数值。"
        "先输出 solution，再输出条件核对和航图数据。只返回一个有效 JSON 对象，不要使用代码围栏。"
        "软件直接展示你的原文、原图和点位，不会重新解题、校核、旋转或补造点线。"
        "软件另有统一教学航图排版，必须提供标注字段，不要只把重要数据藏在SVG或正文里。"
        "route_map.annotations列出所有题目给定或最终解明确推得的夹角，"
        "每项为type=angle、from、vertex、to、value_deg，顶点为vertex；90度也必须列出以显示直角符号。"
        "包含起飞点与进场线的夹角；from/to为两条射线端点，严格采用最终解数值。"
        "进场距离放进对应flight_legs.distance_m，所有航段给bearing_deg和distance_m。"
        "无法确定的夹角不填，不要编造。原SVG对标教学插图：白底、蓝绿色航线及浅蓝面填充、"
        "白心深蓝点圈和外置点号、航线中段箭头、左上N、橙色起飞点H及橙色虚线进场、"
        "橙色距离与夹角弧线、深蓝直角符号、航段外侧标from→to与方位缩写及度数，避免文字覆盖线条。"
        "必须由你提供 route_map.points 的所有点位和 route_map.svg 完整航线示意图；"
        "points 的 x 为东向米数、y 为北向米数，north_up=true，y_axis=up；"
        "SVG 使用 viewBox、白色背景、标准图形/路径/文字元素，含点号、起飞点、航线箭头和北向箭头，"
        "按你的最终航向和比例画图，避免文字重叠，不含脚本、外链或 foreignObject。"
        "SVG、points、航段表和正文的点号、方向、距离保持一致。结构："
        "{\"complete\":true,\"solution\":{\"summary\":\"简短结论\","
        "\"markdown\":\"精炼且包含所需计算的参考解\",\"reasoning_steps\":[\"步骤\"],"
        "\"flight_legs\":[{\"from\":\"H\",\"to\":\"1\","
        "\"bearing_deg\":225,\"distance_m\":50}],\"coordinates\":[],"
        "\"flight_parameters\":[],\"assumptions\":[]},"
        "\"condition_items\":[{\"condition_id\":\"1\","
        "\"normalized\":\"读出的条件\",\"visual_evidence\":\"简短原图依据\","
        "\"confidence\":0.98}],\"uncertain_items\":[],"
        "\"diagram_model\":{\"shape\":\"图形名称\",\"nodes\":[\"1\",\"2\",\"3\"],"
        "\"geometry_edges\":[[\"1\",\"2\"]],\"flight_order\":[\"1\",\"2\",\"3\"],"
        "\"flight_closed\":null},"
        "\"route_map\":{\"points\":{\"点名\":{\"x\":0,\"y\":0}},\"north_up\":true,\"y_axis\":\"up\","
        "\"svg\":\"完整SVG原图\",\"annotations\":[{\"type\":\"angle\",\"from\":\"1\",\"vertex\":\"2\",\"to\":\"H\",\"value_deg\":60}],\"geometry_edges\":[[\"1\",\"2\"]],"
        "\"flight_edges\":[[\"H\",\"1\"],[\"1\",\"2\"]],"
        "\"access_edges\":[[\"H\",\"1\"]]}}。"
        "geometry_edges 只表示题图实际画出的边，flight_edges 只表示实际飞行路线，不能混淆。"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": image, "detail": "high"}},
            ]},
        ],
        **_sampling_parameters(),
        "response_format": {"type": "json_object"},
        "stream": True,
        _token_limit_field(): 65536 if _api_provider() == "deepseek" else 8192,
    }
    if _api_provider() == "deepseek":
        # 直接采用已实测的普通 JSON 输出路径，给推理和最终答案预留完整额度，
        # 避免先耗完小额度后又从原图重算一遍。
        payload.pop("response_format", None)
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = "high"
    if _api_provider() in ("deepseek", "openai"):
        payload["stream_options"] = {"include_usage": True}
    finish_reason = ""
    response_chars = 0
    retry_attempted = False
    response_usages = []
    completed_calls = 0
    try:
        report("视觉 API 正在读取原图并独立推演航线…")
        body = _chat_request(
            base_url, api_key, payload, timeout=240,
            progress_callback=lambda count: report(
                "模型正在流式输出，已接收 %d 字符；完成后会统一排版…" % count),
            stream_callback=lambda text: (
                stream_callback(partial_answer_text(text))
                if callable(stream_callback) else None),
            reasoning_callback=lambda count: report(
                "模型正在推演航线，已运行 %d 秒…" % (time.monotonic() - started_at)),
            request_control=request_control)
        completed_calls += 1
        usage = _api_usage(body)
        if usage:
            response_usages.append(usage)
        parsed, finish_reason, response_chars = _completion_response_json(body)
        if not isinstance(parsed, dict) and response_chars:
            parsed = _plain_answer_from_response(body, model)
        reasoning_exhausted = (_api_provider() == "deepseek" and
                              finish_reason == "length" and not response_chars)
        if (not isinstance(parsed, dict) and not response_chars and
                not reasoning_exhausted and
                finish_reason not in ("content_filter", "aborted",
                                      "insufficient_system_resource")):
            if request_control is not None:
                request_control.check()
            retry_attempted = True
            report("模型没有返回完整答案，正在自动调整格式并重试一次…")
            retry_payload = dict(payload)
            retry_messages = [dict(message) for message in payload["messages"]]
            retry_messages[0]["content"] += (
                "\n\n上一答没有返回完整内容，请重新核对题图。这次先输出 solution，再输出其他字段；"
                "完整输出 solution.markdown、route_map.points 和 route_map.svg；"
                "原文与原图中点号、方向、距离保持一致。必须实际解题，"
                "只输出一个完整合法的 JSON 对象，不要返回代码围栏或 JSON 以外的说明；"
                "题面没有依据的字段留空，不得编造。")
            retry_payload["messages"] = retry_messages
            retry_limit = int(payload.get(_token_limit_field()) or 8192)
            if _api_provider() in ("deepseek", "openai"):
                retry_limit = max(retry_limit, 32768 if finish_reason == "length" else 16384)
            retry_payload[_token_limit_field()] = retry_limit
            if _api_provider() == "deepseek":
                # DeepSeek 文档说明 JSON 模式可能偶发返回空 content；第二次保留
                # 明确 JSON 指令，改用普通文本输出，避免同一模式重复空答。
                retry_payload.pop("response_format", None)
            body = _chat_request(
                base_url, api_key, retry_payload, timeout=240,
                progress_callback=lambda count: report(
                    "模型正在流式输出，已接收 %d 字符；完成后会统一排版…" % count),
                stream_callback=lambda text: (
                    stream_callback(partial_answer_text(text))
                    if callable(stream_callback) else None),
                reasoning_callback=lambda count: report(
                    "模型正在重新整理答案，已运行 %d 秒…" % (time.monotonic() - started_at)),
                request_control=request_control)
            completed_calls += 1
            usage = _api_usage(body)
            if usage:
                response_usages.append(usage)
            parsed, finish_reason, response_chars = _completion_response_json(body)
        if request_control is not None:
            request_control.check()
    except RequestCancelled as exc:
        return {"provider": "api", "model": model, "status": "cancelled",
                "reason": str(exc),
                "elapsed_seconds": round(time.monotonic() - started_at, 2)}
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            detail = ""
        if api_key:
            detail = detail.replace(api_key, "[已隐藏]")
        return {"provider": "api", "model": model, "status": "unavailable",
                "reason": "模型 API 返回 HTTP %s%s" % (
                    exc.code, ("：" + detail[:360]) if detail else ""),
                "elapsed_seconds": round(time.monotonic() - started_at, 2)}
    except (OSError, ValueError, KeyError, IndexError, TypeError,
            urllib.error.URLError) as exc:
        return {"provider": "api", "model": model, "status": "unavailable",
                "reason": (str(exc) if isinstance(exc, NetworkInterrupted) else
                           "连接超时，请检查网络或稍后重试。" if isinstance(exc, (TimeoutError, socket.timeout)) else
                           "网络连接失败或接收中断，请检查网络后重试。" if isinstance(exc, (OSError, urllib.error.URLError, http.client.HTTPException)) else
                           _safe_text(exc, 240)),
                "elapsed_seconds": round(time.monotonic() - started_at, 2)}
    model_elapsed = round(time.monotonic() - started_at, 2)
    usage = None
    if response_usages:
        usage = {key: sum(int(row.get(key, 0)) for row in response_usages)
                 for key in ("input_tokens", "cached_input_tokens", "output_tokens",
                             "total_tokens")}
    cost = _estimate_api_cost(usage, model)
    call_count = int(cfg.get("vision_api_call_count", 0) or 0)
    if completed_calls:
        cfg.set_("vision_api_call_count", call_count + completed_calls,
                 save_now=False)
    if cost:
        spend_by_currency = cfg.get("vision_api_spend_by_currency", {})
        if not isinstance(spend_by_currency, dict):
            spend_by_currency = {}
        currency = str(cost.get("currency") or "USD")
        local_spend = float(spend_by_currency.get(currency, 0) or 0) + cost["amount"]
        spend_by_currency[currency] = local_spend
        cfg.set_("vision_api_spend_by_currency", spend_by_currency, save_now=False)
        cfg.set_("vision_api_spend_total", local_spend, save_now=False)
    if usage or completed_calls:
        cfg.save()
    balance = None
    # 余额由窗口的独立工作线程查询，答案和航图无需等待额外网络请求。
    spend_map = cfg.get("vision_api_spend_by_currency", {})
    if not isinstance(spend_map, dict):
        spend_map = {}
    currency = str(cost.get("currency") if cost else
                   cfg.get("vision_api_currency", "USD"))
    local_spend = spend_map.get(currency)
    try:
        local_budget = float(cfg.get("vision_api_budget_amount", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        local_budget = 0.0
    local_remaining = (max(0.0, local_budget - float(local_spend or 0))
                       if local_budget > 0 and
                       currency == str(cfg.get("vision_api_currency", "USD")) else None)
    billing = {"usage": usage, "estimated_cost": cost,
               "elapsed_seconds": model_elapsed,
               "local_estimated_spend": local_spend if cost else None,
               "local_remaining_estimate": local_remaining,
               "local_budget_amount": local_budget if local_remaining is not None else None,
               "local_call_count": int(cfg.get("vision_api_call_count", call_count + completed_calls)),
               "account_balance": balance}
    if not isinstance(parsed, dict):
        plain_answer = _plain_answer_from_response(body, model)
        if plain_answer:
            parsed = plain_answer
    if not isinstance(parsed, dict):
        if finish_reason == "length":
            reason = ("模型已用完本次推理额度，尚未生成完整答案；可稍后重试或更换模型。"
                      if not retry_attempted else
                      "模型输出达到长度上限，重试后仍未得到完整答案。")
        elif not response_chars:
            if finish_reason == "content_filter":
                reason = "服务商过滤了本次回答，没有生成参考答案。"
            elif retry_attempted and _api_provider() == "deepseek":
                reason = "DeepSeek API 返回空内容，自动重试一次后仍为空；请稍后重试。"
            elif retry_attempted:
                reason = "视觉 API 连续返回空内容，自动重试一次后仍未得到答案。"
            else:
                reason = "模型未生成最终内容（结束原因：%s）。" % (
                    finish_reason or "未知")
        elif retry_attempted:
            reason = "视觉 API 连续两次未返回可解析的 JSON 解答，请重试或更换模型。"
        else:
            reason = "视觉 API 未返回可解析的 JSON 解答，请重试。"
        return {"provider": "api", "model": model, "status": "invalid_response",
                "reason": reason, **billing}

    report("模型已完成推理，正在整理航图和答案内容…")
    diagram = copy.deepcopy(parsed.get("diagram_model")) \
        if isinstance(parsed.get("diagram_model"), dict) else None
    items = []
    for item in (parsed.get("condition_items") or [])[:80]:
        if not isinstance(item, dict):
            continue
        normalized = _safe_text(item.get("normalized"), 500)
        evidence = _safe_text(item.get("visual_evidence"), 500)
        confidence = _number(item.get("confidence")) or 0.0
        if normalized:
            items.append({"normalized": normalized,
                          "condition_id": _condition_item_id(item),
                          "visual_evidence": evidence,
                          "confidence": max(0.0, min(1.0, confidence))})
    uncertain = parsed.get("uncertain_items") or []
    if isinstance(uncertain, str):
        uncertain = [uncertain]
    uncertain = [_safe_text(value, 400) for value in uncertain[:40]] \
        if isinstance(uncertain, list) else []
    uncertain = [value for value in uncertain if value]
    # 覆盖情况只能基于模型自己从原图识别的条件，不读取本地 OCR。
    source_conditions = []
    source_ids = {item["id"] for item in source_conditions}
    item_ids = {item["condition_id"] for item in items if item.get("condition_id")}
    missing_ids = sorted(source_ids - item_ids, key=lambda value: int(value))
    count_shortfall = max(0, len(source_conditions) - len(items))
    coverage_ok = not source_conditions or (
        not missing_ids if item_ids else count_shortfall == 0)
    if source_conditions and not coverage_ok:
        if item_ids:
            detail = "未逐条核对条件 %s" % "、".join(missing_ids)
        else:
            detail = "模型只返回 %d/%d 条逐项核对内容，且没有提供条件编号" % (
                len(items), len(source_conditions))
        uncertain.append("题面条件覆盖检查：%s；航线仅作未核验草案。" % detail)
    model_complete = bool(parsed.get("complete")) and coverage_ok
    answer = _normalize_direct_answer(parsed, model, diagram)
    if not answer:
        return {"provider": "api", "model": model, "status": "no_answer",
                "complete": model_complete,
                "condition_items": items, "uncertain_items": uncertain,
                "condition_coverage": {"source_count": len(source_conditions),
                                       "returned_count": len(items),
                                       "missing_ids": missing_ids},
                "diagram_model": diagram,
                "reason": "模型识别了题目，但没有返回可展示的解题步骤或航段答案",
                **billing}
    answer.update(billing)
    choices = body.get("choices") or []
    message = choices[0].get("message") or {} if choices else {}
    answer["raw_response_text"] = _response_content_text(message.get("content"))
    answer["finish_reason"] = finish_reason
    report("正在保存模型直接生成的参考解答…")
    return {
        "provider": "api", "model": model,
        "status": "review" if uncertain or not model_complete else "ready",
        "complete": model_complete,
        "condition_items": items,
        "condition_coverage": {"source_count": len(source_conditions),
                               "returned_count": len(items),
                               "missing_ids": missing_ids},
        "normalized_conditions": "\n".join(item["normalized"] for item in items),
        "uncertain_items": uncertain,
        "diagram_model": diagram,
        "diagram_complete": bool(parsed.get("complete")),
        "direct_answer": answer,
        "answer_mode": "api_direct_reference",
        "experience_count": len(examples),
        "experience_cases": [str(item.get("case_id", "")) for item in examples],
        **billing,
    }
