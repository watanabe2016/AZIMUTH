"""本地 Tesseract OCR 探测与识别。"""
import csv
import io
import math
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from functools import lru_cache


def _missing_condition_numbers(text):
    """查找连续编号条件中完全漏识别的编号。"""
    numbers = sorted({int(match.group(1)) for match in re.finditer(
        r"(?m)^\s*(\d{1,2})\s*[,、.．]", str(text or ""))})
    if len(numbers) < 2:
        return set()
    return {str(number) for number in range(numbers[0], numbers[-1] + 1)
            if number not in numbers}
from pathlib import Path

_rapid_ocr = None


def _merge_recovered_condition_rows(words, recovered_words):
    """仅用增强识别补全已有编号但正文为空的题目条件。"""
    existing = list(words or [])
    numbered_text = _rapid_question_text(existing)
    missing = _missing_condition_numbers(numbered_text)
    missing.update(match.group(1) for match in re.finditer(
        r"(?m)^\s*(\d{1,2})\s*[,、.．]\s*$", numbered_text))
    if not missing:
        return existing

    for candidate in recovered_words or []:
        text = str(candidate.get("text") or "").strip()
        match = re.match(r"^(\d{1,2})\s*[,、.．]\s*\S", text)
        if not match or match.group(1) not in missing:
            continue
        label = match.group(1)
        candidate_y = float(candidate.get("y", 0)) + float(
            candidate.get("height", 0)) / 2.0
        kept = []
        for word in existing:
            old_text = str(word.get("text") or "").strip()
            old_label = re.fullmatch(r"(\d{1,2})\s*[,、.．]", old_text)
            old_y = float(word.get("y", 0)) + float(
                word.get("height", 0)) / 2.0
            if (old_label and old_label.group(1) == label and
                    abs(old_y - candidate_y) <= max(
                        24.0, float(word.get("height", 0)) * 1.5,
                        float(candidate.get("height", 0)) * 1.5)):
                continue
            kept.append(word)
        existing = kept
        existing.append(candidate)
        missing.remove(label)
        if not missing:
            break
    return existing


def _rapid_words(path):
    """以放大后的整页识别航点和图上距离方位，坐标换回原图。"""
    global _rapid_ocr
    try:
        import cv2
        from rapidocr import RapidOCR
        import numpy as np
        # OpenCV 的 Windows 文件路径接口不能稳定读取中文路径。
        original = cv2.imdecode(np.fromfile(os.path.abspath(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        image = original
        if image is None:
            return []
        height, width = image.shape[:2]
        # 一页只推理一次：旧流程对原图和放大图各跑一次 OCR，整本 PDF
        # 会把模型推理量近乎翻倍。将输入限制在 1800px 左右，同时保留
        # 低清小图的适度放大，坐标再按比例还原到原图。
        scale = max(0.35, min(1.6, 1800.0 / max(width, height)))
        if abs(scale - 1.0) > 0.03:
            interpolation = (cv2.INTER_AREA if scale < 1.0
                             else cv2.INTER_CUBIC)
            image = cv2.resize(image, None, fx=scale, fy=scale,
                               interpolation=interpolation)
        if _rapid_ocr is None:
            _rapid_ocr = RapidOCR()
        words = []
        result = _rapid_ocr(image)
        if result is None:
            return []
        ratio = scale if abs(scale - 1.0) > 0.03 else 1.0
        for token, score, box in zip(
                result.txts if result.txts is not None else [],
                result.scores if result.scores is not None else [],
                result.boxes if result.boxes is not None else []):
            sides = [(box[(index + 1) % 4][0] - box[index][0],
                      box[(index + 1) % 4][1] - box[index][1])
                     for index in range(4)]
            axis = max(sides, key=lambda side: math.hypot(*side))
            x0 = min(point[0] for point in box) / ratio
            y0 = min(point[1] for point in box) / ratio
            x1 = max(point[0] for point in box) / ratio
            y1 = max(point[1] for point in box) / ratio
            words.append({"text": str(token), "confidence": float(score),
                          "x": round(x0), "y": round(y0),
                          "width": round(x1 - x0), "height": round(y1 - y0),
                          "angle": math.degrees(math.atan2(axis[1], axis[0])) % 180})
        # 部分题卡的红色条件字与浅色水印叠在一起，原图 OCR 会只读到“3、”而漏掉正文。
        # 仅在存在空编号行时额外做一次全页 Otsu 二值识别，并只补回对应编号，避免重复整页文本。
        text = _rapid_question_text(words)
        missing_numbers = _missing_condition_numbers(text)
        missing_numbers.update(match.group(1) for match in re.finditer(
            r"(?m)^\s*(\d{1,2})\s*[,、.．]\s*$", text))
        if missing_numbers:
            gray = cv2.cvtColor(original, cv2.COLOR_BGR2GRAY)
            binary = cv2.threshold(
                gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
            if scale > 1.05:
                binary = cv2.resize(binary, None, fx=scale, fy=scale,
                                    interpolation=cv2.INTER_CUBIC)
            result = _rapid_ocr(binary)
            recovered = []
            if result is not None:
                for token, score, box in zip(
                        result.txts if result.txts is not None else [],
                        result.scores if result.scores is not None else [],
                        result.boxes if result.boxes is not None else []):
                    text = str(token).strip()
                    marker = re.match(r"^(\d{1,2})\s*[,、.．]\s*\S", text)
                    if not marker or marker.group(1) not in missing_numbers:
                        continue
                    sides = [(box[(index + 1) % 4][0] - box[index][0],
                              box[(index + 1) % 4][1] - box[index][1])
                             for index in range(4)]
                    axis = max(sides, key=lambda side: math.hypot(*side))
                    recovered.append({
                        "text": text, "confidence": float(score),
                        "x": round(min(point[0] for point in box) / scale),
                        "y": round(min(point[1] for point in box) / scale),
                        "width": round((max(point[0] for point in box) -
                                        min(point[0] for point in box)) / scale),
                        "height": round((max(point[1] for point in box) -
                                         min(point[1] for point in box)) / scale),
                        "angle": math.degrees(math.atan2(axis[1], axis[0])) % 180,
                    })
            words = _merge_recovered_condition_rows(words, recovered)
        return words
    except Exception:
        return []


def _rapid_question_text(words):
    """从水平题干识别框重组正文，跳过斜向水印和图中点号。"""
    import re
    horizontal = [word for word in words if
                  min(abs(float(word.get("angle", 0))),
                      abs(float(word.get("angle", 0)) - 180)) < 12 and
                  str(word.get("text", "")).strip()]
    if not horizontal:
        return ""
    # 用户有时导入包含软件界面的整张截图。题卡外的控件文字会与题卡条件
    # 落在同一行；若检测到远离图形航点的独立文字栏，只读取题卡栏。
    try:
        from diagram_vision import _point_labels
        image_height = max(float(word.get("y", 0)) + float(word.get("height", 0))
                           for word in words)
        labels = _point_labels(words, image_height)
        if len(labels) >= 3:
            xs = [item[1] for item in labels]
            spread = max(xs) - min(xs)
            centre = (max(xs) + min(xs)) / 2
            if spread >= 50:
                margin = max(28.0, spread * 0.32)
                left, right = min(xs) - margin, max(xs) + margin
                far_words = [word for word in horizontal
                             if abs(float(word.get("x", 0)) +
                                    float(word.get("width", 0)) / 2 - centre) >
                             spread * 1.4]
                isolated = [word for word in horizontal
                            if left <= float(word.get("x", 0)) +
                            float(word.get("width", 0)) / 2 <= right]
                if (len(isolated) >= 8 and
                        len(far_words) >= max(8, len(horizontal) * 0.14) and
                        len(far_words) > len(isolated) * 1.5):
                    horizontal = isolated
    except Exception:
        pass
    horizontal.sort(key=lambda word: (word["y"] + word["height"] / 2, word["x"]))
    rows = []
    for word in horizontal:
        cy = word["y"] + word["height"] / 2
        match = next((row for row in rows if
                      abs(cy - row[0]) <= max(9, min(word["height"], row[2]) * 0.35)), None)
        if match is None:
            rows.append([cy, [word], word["height"]])
        else:
            match[1].append(word)
    lines = []
    for _, members, _ in rows:
        members.sort(key=lambda word: word["x"])
        kept = []
        for word in members:
            # 放大与原尺寸两轮识别会产生重复框，只保留较可信的一份。
            duplicate = next((old for old in kept if
                              abs(old["x"] - word["x"]) < max(12, word["height"] * 0.6) and
                              abs(old["width"] - word["width"]) < max(20, word["width"] * 0.2)), None)
            if duplicate is None:
                kept.append(word)
            elif word["confidence"] > duplicate["confidence"]:
                kept[kept.index(duplicate)] = word
        line = "".join(str(word["text"]) for word in kept)
        for size in range(len(line) // 2, 12, -1):
            if line[:size] == line[size:2 * size] and len(line) == 2 * size:
                line = line[:size]
                break
        lines.append(line)
    first = next((index for index, line in enumerate(lines)
                  if re.match(r"\s*1[,、.．]\s*", line)), None)
    if first is None:
        return ""
    body = lines[first:]
    numbered = {int(match.group(1)) for line in body
                if (match := re.match(r"\s*(\d{1,2})[,、.．]", line))}
    if len(numbered) < 3:
        return ""
    paragraphs = []
    for line in body:
        replan = re.search(r"重规划\s*[：:]", line)
        main_line = line[:replan.start()] if replan else line
        replan_line = line[replan.start():] if replan else ""
        if re.match(r"\s*\d{1,2}[,、.．]", main_line):
            paragraphs.append(main_line)
        elif paragraphs:
            paragraphs[-1] += main_line
        if replan_line:
            # 题卡底部常用红字添加“重规划”条件，OCR 会把它接在经纬度行末尾。
            # 拆成独立条件，避免经度解析把红字并入坐标值。
            paragraphs.append(replan_line)
    paragraphs = [re.sub(r"(∠[HAT]\d{2}\s*=\s*\d{1,3})$", r"\1°", line)
                  for line in paragraphs]
    paragraphs = [re.sub(r"^(\d{1,2})[,、.．](.{8,}?)\1[,、.．]\2$", r"\1、\2", line)
                  for line in paragraphs]
    return "\n".join(paragraphs)


def recognize_figure_words(path):
    """只识别图形标注，供文字层完整的 PDF 页面补充航点数据。"""
    words = _rapid_words(path)
    if words:
        return words
    # 发布版不一定带 OpenCV/RapidOCR；用已有的 Tesseract TSV 继续提供
    # 点位数字框，让本地图形识别无需额外视觉依赖。
    _, _, words = recognize_image(path, with_details=True)
    return words


def _candidates():
    override = os.environ.get("TESSERACT_CMD")
    if override:
        yield Path(override)
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
    roots.append(Path(__file__).resolve().parent.parent)
    for root in roots:
        yield root / "tools" / "ocr" / "Tesseract-OCR" / "tesseract.exe"
        yield root / "tools" / "ocr" / "tesseract.exe"
    for raw in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if raw:
            yield Path(raw) / "Tesseract-OCR" / "tesseract.exe"
    found = shutil.which("tesseract")
    if found:
        yield Path(found)


def engine_path():
    return next((p for p in _candidates() if p.is_file()), None)


def tessdata_path(engine=None):
    engine = Path(engine) if engine else engine_path()
    if not engine:
        return None
    guesses = [engine.parent / "tessdata", engine.parent.parent / "tessdata"]
    env = os.environ.get("TESSDATA_PREFIX")
    if env:
        guesses.insert(0, Path(env))
    return next((p for p in guesses if p.is_dir()), None)


def _tesseract_path_args(engine, data):
    """优先用相对模型目录，避开旧版 Tesseract 的 Unicode 绝对路径转换错误。"""
    engine = Path(engine)
    data = Path(data) if data else None
    cwd = engine.parent
    if data:
        try:
            data_arg = str(data.resolve().relative_to(cwd.resolve()))
        except (OSError, ValueError):
            data_arg = str(data)
    else:
        data_arg = None
    return cwd, data_arg


@lru_cache(maxsize=1)
def available_languages():
    exe = engine_path()
    if not exe:
        return (), "未找到 Tesseract OCR 引擎"
    data = tessdata_path(exe)
    cwd, data_arg = _tesseract_path_args(exe, data)
    cmd = [str(exe), "--list-langs"]
    if data_arg:
        cmd += ["--tessdata-dir", data_arg]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=15,
                                cwd=str(cwd),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:
        return (), "Tesseract 无法启动：%s" % exc
    languages = set()
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        token = line.strip()
        if token and token not in ("List of available languages in \"%s\":" % data,
                                   "List of available languages (2):"):
            if token.replace("_", "").isalnum() and token.lower() not in ("tesseract",):
                languages.add(token)
    if "chi_sim" in languages:
        return tuple(sorted(languages)), ""
    return tuple(sorted(languages)), "缺少简体中文模型 chi_sim，中文识别不可用"


def recognize_image(path, timeout=90, with_details=False):
    """返回(识别文字, 提醒)。引擎/语言不可用时不抛出到导入界面。"""
    def response(text, warning, words=None):
        if with_details:
            return text, warning, words or []
        return text, warning

    figure_words = _rapid_words(path)
    rapid_text = _rapid_question_text(figure_words)
    if rapid_text:
        return response(rapid_text, "", figure_words)

    exe = engine_path()
    if not exe:
        return response("", "未找到 Tesseract OCR；已保留题图，请在审核页手动填写题目文字。")
    languages, warning = available_languages()
    if "chi_sim" not in languages:
        return response("", (warning or "缺少简体中文模型 chi_sim") + "；题图已保留，可手动校对。")
    language = "chi_sim+eng" if "eng" in languages else "chi_sim"
    data = tessdata_path(exe)
    cwd, data_arg = _tesseract_path_args(exe, data)
    cmd = [str(exe), os.path.abspath(path), "stdout", "-l", language,
           "--oem", "1", "--psm", "6"]
    if data_arg:
        cmd += ["--tessdata-dir", data_arg]
    cmd.append("tsv")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout,
                                cwd=str(cwd),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return response("", "本页 OCR 超时；题图已保留，请手动补充文字。")
    except Exception as exc:
        return response("", "OCR 启动失败：%s；题图已保留，请手动补充文字。" % exc)
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        return response("", "OCR 识别失败%s；题图已保留，请手动补充文字。" %
                        (("：" + detail[-1]) if detail else ""))
    # TSV 同时给出文字框置信度；分组回行后交给题卡解析器。
    lines, confidences, words = {}, [], []
    try:
        for row in csv.DictReader(io.StringIO(result.stdout or ""), delimiter="\t"):
            word = (row.get("text") or "").strip()
            try:
                confidence = float(row.get("conf", -1))
            except (TypeError, ValueError):
                confidence = -1
            if word and confidence >= 0:
                key = tuple(row.get(k, "") for k in
                            ("page_num", "block_num", "par_num", "line_num"))
                lines.setdefault(key, []).append(word)
                confidences.append(confidence)
                try:
                    words.append({
                        "text": word, "confidence": confidence,
                        "x": int(row.get("left", 0)),
                        "y": int(row.get("top", 0)),
                        "width": int(row.get("width", 0)),
                        "height": int(row.get("height", 0)),
                    })
                except (TypeError, ValueError):
                    pass
    except Exception:
        lines, confidences, words = {}, [], []
    def is_cjk(char):
        return "CJK" in unicodedata.name(char, "") or "IDEOGRAPH" in unicodedata.name(char, "")

    def join_words(words):
        result = ""
        for word in words:
            if not result:
                result = word
                continue
            previous = result.rstrip()
            # Tesseract 将中文短词切分成多个 TSV 单词；相邻汉字应直接拼接，
            # 否则“附加条件”“避让点”等关键字段会被结构化解析器漏掉。
            if previous and word and is_cjk(previous[-1]) and is_cjk(word[0]):
                result += word
            else:
                result += " " + word
        return result

    text = "\n".join(join_words(words) for words in lines.values()).strip()
    if figure_words:
        words = figure_words
    if len("".join(text.split())) < 12:
        return response(text, "识别到的文字较少；可能是低清、倾斜或文字区域复杂，请在预览中校对。", words)
    if confidences:
        confidence = sum(confidences) / len(confidences)
        low_ratio = sum(value < 55 for value in confidences) / len(confidences)
        if confidence < 60 or low_ratio > 0.2:
            return response(text, "OCR 置信度偏低（平均 %.0f%%，低置信词 %.0f%%）；请重点校对数字、坐标和航点。" % (
                confidence, low_ratio * 100), words)
    return response(text, "", words)
