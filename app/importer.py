# -*- coding: utf-8 -*-
"""
离线导入 —— 把本地图片 / PDF 变成题库里的题。

设计取舍：
  · 图片走同步调用（几张图，瞬间的事），PDF 必须上后台线程 —— 几百页
    渲染加解码，放主线程会把界面卡成幻灯片。
  · 解析逻辑是从 build/parse_questions.py 移植过来的单页版本，那份专治
    那本固定的 444 页 PDF，这边要能啃任意格式，所以对每个环节都留了退路：
    认不出来源标记照导，整页没文字层（扫描件）也照导，只是题面留白。
    宁可让用户在软件里补，也别一声不吭把整页扔掉。
  · 一律转成 900px 宽 JPG，和内置题卡同规格，加载端不用区分。
"""
import os
import re
import shutil
import tempfile
import copy
import hashlib

from PySide6.QtCore import Qt, QThread, Signal, QSize, QTimer
from PySide6.QtGui import QImage, QColor, QPainter, QPixmap, QImageReader
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QWidget, QFormLayout,
                               QLineEdit, QPlainTextEdit, QSplitter, QDialogButtonBox,
                               QMessageBox, QPushButton)

import bankdata as BD
import theme as T
import ocr as OCR
import reference_engine as RE
import diagram_vision as DV
from logger import get_logger
from import_dialog_ui import ImportDialogShell, ImportProgressDialog

log = get_logger()

FIG_WIDTH = 900          # 输出宽度，与内置题卡保持一致
JPG_QUALITY = 74         # 与之搭配的画质，白底整页这个数已经很干净
MAX_SRC_WIDTH = 1600     # 源图先压到这个宽，再定稿到 900，避免二次缩放糊掉
PDF_ZOOM = 2.0           # PDF 渲染倍率；2 倍足够支撑 900px 输出
PDF_PREVIEW_ZOOM = 0.5   # 选页阶段只生成低清预览，避免提前做高清识别

NOISE = re.compile(r"^(无人机教员|大琳，|\s*)$")
TAIL_RE = re.compile(
    r"[（(]\s*(\d{1,2})[.\-](\d{1,2})\s+([^）)]{2,10}?)\s*[）)]\s*真题")
DOT_NUMS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


# ================================================================ 文本 → 结构化
def _clean_lines(body):
    out = []
    for ln in body.split("\n"):
        s = ln.strip()
        if not s or NOISE.match(s):
            continue
        out.append(s)
    return out


def _is_graph_line(s):
    """题卡顶部的图形标记行：圈数字，或 1 2 3 / 1 4 2 这类短数字串"""
    if not s:
        return False
    if any(c in DOT_NUMS for c in s) and all(c in DOT_NUMS + " " for c in s):
        return True
    if re.fullmatch(r"[\d\s]{1,16}", s):
        return True
    return False


def _unwrap(lines):
    """把 PDF 的硬换行还原成语义段落：碰到编号 / 小标题就起新段，否则并入上一段。"""
    new_para = re.compile(
        r"^[（(]?\s*[一二三四五六七八九十\d]+\s*[）)、.]"
        r"|^附加条件|^航线要求|^附\s*加\s*条\s*件")
    merged = []
    for ln in lines:
        if not merged:
            merged.append(ln)
            continue
        if new_para.match(ln):
            merged.append(ln)
        else:
            merged[-1] = merged[-1] + ln
    return merged


def split_tag(tag):
    """'9.20 广东03' → ('9.20', '广东03')；没写场次就整体当地区"""
    t = (tag or "").strip()
    if not t:
        return "本地", "本地导入"
    parts = t.split(None, 1)
    if len(parts) == 1:
        return "本地", parts[0]
    return parts[0], parts[1]


def parse_page_text(body, pageno=None, fallback_source=None):
    """一页正文 → 题目字段。认不出来源也不返回 None 式的放弃，
    改用 fallback_source 顶上，让这一页至少能进题库。"""
    lines = _clean_lines(body)
    if not lines:
        return None

    src_match, src_idx = None, -1
    for idx, ln in enumerate(lines):
        m = TAIL_RE.search(ln)
        if m:
            src_match, src_idx = m, idx
            break

    if src_match:
        body_lines = lines[:src_idx] + lines[src_idx + 1:]
        date = "%s.%s" % (src_match.group(1), src_match.group(2))
        region = src_match.group(3).strip()
        source = "%s %s" % (date, region)
    else:
        body_lines = lines
        date = region = source = ""
        if fallback_source:
            source = fallback_source
            date, region = split_tag(fallback_source)

    graph, content = [], []
    for ln in body_lines:
        # 图形标记全在正文之前；正文里偶尔混一个短数字行也算图形
        if _is_graph_line(ln) and (len(content) == 0 or (
                len(ln) <= 16 and not re.search(r"[，。：、米秒]", ln))):
            graph.append(ln)
        else:
            content.append(ln)

    if not content:
        return None

    paras = _unwrap(content)
    cond, req, sug = [], [], []
    state = "cond"
    for p in paras:
        if p.startswith("附加条件") or p.startswith("附 加 条 件"):
            state = "cond"
            rest = re.sub(r"^附\s*加\s*条\s*件\s*[:：]?", "", p).strip()
            if rest:
                cond.append(rest)
            continue
        if p.startswith("航线要求"):
            state = "req"
            rest = re.sub(r"^航\s*线\s*要\s*求\s*[:：]?", "", p).strip()
            if rest:
                req.append(rest)
            continue
        if re.match(r"^[（(]\s*二\s*[）)]", p):
            sug.append(p)
            continue
        if re.match(r"^[（(]\s*一\s*[）)]", p):
            state = "req"
            req.append(p)
            continue
        (cond if state == "cond" else req).append(p)

    # 没有显式的（二），就从 req 里按措辞再捞一次建议值
    if not sug:
        keep = []
        for p in req:
            if re.search(r"值建议为|建议为|值\s*为", p) and len(keep) > 0:
                sug.append(p)
            else:
                keep.append(p)
        req = keep

    q = {
        "id": None,
        "page": pageno,
        "date": date or "本地",
        "region": region or "本地导入",
        "source": source or "本地导入",
        "graph_hint": " ".join(graph).strip()[:60],
        "conditions_text": "\n".join(cond).strip(),
        "requirements_text": "\n".join(req).strip(),
        "suggested_text": "\n".join(sug).strip(),
    }
    if not q["suggested_text"]:
        for p in cond:
            if re.search(r"值建议为|建议为", p):
                q["suggested_text"] = p
                q["conditions_text"] = q["conditions_text"].replace(p, "").strip()
                break
    return q


def new_question(qid, source_tag, hint=""):
    """图片导入用：只有图没有文，题面留一句说明方便事后认人。"""
    date, region = split_tag(source_tag)
    return {
        "id": int(qid),
        "page": None,
        "date": date,
        "region": region,
        "source": source_tag or "本地导入",
        "graph_hint": "",
        "conditions_text": hint,
        "requirements_text": "",
        "suggested_text": "",
        "imported": True,
    }


# ================================================================ 图片落盘
def _flatten(img):
    """带透明通道的图转 RGB，否则直接存 JPG 透明区会变黑块"""
    if not img.hasAlphaChannel():
        return img
    flat = QImage(img.size(), QImage.Format_RGB888)
    flat.fill(QColor("#ffffff"))
    pt = QPainter(flat)
    pt.drawImage(0, 0, img)
    pt.end()
    return flat


def save_figure(img, dest):
    """统一转存：等比缩到 FIG_WIDTH、去 alpha、写 JPG"""
    if img.isNull():
        return False
    if img.width() > MAX_SRC_WIDTH:
        img = img.scaledToWidth(MAX_SRC_WIDTH, Qt.SmoothTransformation)
    if img.width() > FIG_WIDTH:
        img = img.scaledToWidth(FIG_WIDTH, Qt.SmoothTransformation)
    img = _flatten(img)
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
    except Exception:
        pass
    return bool(img.save(dest, "JPG", JPG_QUALITY))


def _file_sha256(path):
    """以分块方式计算来源文件摘要，避免把大 PDF 一次读入内存。"""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def import_images(paths, source_tag, start_id, progress=None, cancelled=None):
    """检查图片并暂存待审核项目，不向题库目录写入。"""
    added, failed = [], []
    total = len(paths)
    existing_hashes = BD.imported_source_hashes()
    seen_hashes = set()
    for index, p in enumerate(paths):
        if cancelled and cancelled():
            break
        name = os.path.basename(p)
        if progress:
            progress(index + 1, total, name)
        source_hash = _file_sha256(p)
        if source_hash and (source_hash in existing_hashes or source_hash in seen_hashes):
            failed.append(f"{name}：相同文件已导入，已跳过")
            continue
        if source_hash:
            seen_hashes.add(source_hash)
        try:
            reader = QImageReader(p)
            reader.setAutoTransform(True)
            img = reader.read()
        except Exception as e:
            log.warning("图片读取异常 %s: %r", p, e)
            failed.append("%s：图像解码失败（%s）" % (name, str(e)[:160]))
            continue
        if img.isNull():
            failed.append("%s：图片损坏或格式不受支持" % name)
            continue
        try:
            text, ocr_warning, ocr_words = OCR.recognize_image(
                p, with_details=True)
        except Exception as exc:
            text, ocr_words = "", []
            ocr_warning = "本地 OCR 失败：%s" % str(exc)[:160]
        parsed = parse_page_text(text, fallback_source=source_tag) if text else None
        shape_hint = " ".join(str(value or "") for value in (
            (parsed or {}).get("graph_hint"), text))
        try:
            diagram_model = DV.analyze(p, ocr_words, shape_hint=shape_hint)
        except Exception as exc:
            diagram_model = {}
            ocr_warning = ((ocr_warning + "；") if ocr_warning else "") + \
                "本地图形识别失败：%s" % str(exc)[:160]
        q = new_question(0, source_tag,
                         "（本地图片导入 · %s）" % name)
        q["id"] = None
        q["conditions_text"] = ""
        q["_staged_figure"] = os.path.abspath(p)
        q["_staged_temp"] = False
        q["_import_warnings"] = [
            "图片没有可用的本地 OCR 引擎；请在确认前补充题干、附加条件和航线要求。"]
        if parsed is not None:
            q.update(parsed)
        else:
            q["conditions_text"] = text or ""
        q["id"] = None
        q["page"] = None
        q["source"] = q.get("source") or source_tag or "本地导入"
        q["imported"] = True
        q["_import_ocr_text"] = text
        q["_import_warnings"] = []
        q["_reference_source"] = {
            "kind": "image", "file_name": name,
            "sha256": source_hash,
            "file_size": os.path.getsize(p) if os.path.exists(p) else 0,
            "source_text": text,
            "ocr_words": ocr_words[:1500],
            "diagram_model": diagram_model,
            "image_size": [img.width(), img.height()],
            "recognized_fields": {key: q.get(key, "") for key in (
                "graph_hint", "conditions_text", "requirements_text",
                "suggested_text")},
        }
        if text and parsed is None:
            ocr_warning = (ocr_warning + "；" if ocr_warning else "") + \
                "识别文字未能自动分段，已放入附加条件供校对。"
        if ocr_warning:
            q["_import_warnings"].append(ocr_warning)
        if img.width() < 900 or img.height() < 900:
            q["_import_warnings"].append(
                f"图片分辨率为 {img.width()}×{img.height()}，文字可能偏小；可重新选择高清图片。")
        added.append(q)
        log.info("图片已暂存审核 %s", name)
    return added, failed


class ImageImportThread(QThread):
    """后台识别图片题卡，避免 OCR 占用窗口线程。"""
    step = Signal(int, int, str)
    done = Signal(list, list)
    error = Signal(str)

    def __init__(self, paths, source_tag, start_id, parent=None):
        super().__init__(parent)
        self.paths = list(paths)
        self.tag = source_tag
        self._id0 = int(start_id)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            added, failed = import_images(
                self.paths, self.tag, self._id0,
                progress=lambda cur, total, name: self.step.emit(cur, total, name),
                cancelled=lambda: self._stop)
            self.done.emit(added, failed)
        except Exception as exc:
            log.exception("图片题卡 OCR 导入失败")
            self.error.emit("图片读取或 OCR 失败：\n%r" % exc)


class ImportReviewDialog(ImportDialogShell):
    """导入确认前逐项预览、勾选并校对题目字段。"""

    _TEXT_FIELDS = (
        ("title", "题目标题", False),
        ("source", "来源", False),
        ("date", "场次", False),
        ("region", "地区", False),
        ("graph_hint", "点位 / 图形", False),
        ("conditions_text", "附加条件", True),
        ("requirements_text", "航线要求", True),
        ("suggested_text", "建议参数", True),
    )

    def __init__(self, questions, warnings=None, parent=None):
        super().__init__("预览并校对导入题卡", parent,
                         size=(1080, 720), minimum=(820, 600))
        self._original = [copy.deepcopy(q) for q in questions]
        self._initial_fields = [
            {key: str(q.get(key) or "") for key in (
                "source", "date", "region", "graph_hint", "conditions_text",
                "requirements_text", "suggested_text")}
            for q in self._original]
        self._current = -1
        self._editors = {}
        self._loading = False
        self._reference_timer = QTimer(self)
        self._reference_timer.setSingleShot(True)
        self._reference_timer.setInterval(350)
        self._reference_timer.timeout.connect(self._update_reference_preview)
        root = self.body_layout
        root.addWidget(QLabel(
            "勾选要导入的题卡。来源优先从题面识别；未识别到时可在右侧逐题修改，或统一设置。"))
        if warnings:
            warn = QLabel("\n".join(warnings[:8]))
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color:{T.P['warn_fg']};")
            root.addWidget(warn)

        source_tools = QHBoxLayout()
        source_tools.setSpacing(8)
        source_tools.addWidget(QLabel("批量来源"))
        self.batch_source = QLineEdit()
        self.batch_source.setPlaceholderText("可选，例如：9.22 山东06")
        self.batch_source.setClearButtonEnabled(True)
        self.batch_source.setToolTip(
            "填写后点击右侧按钮，将来源、场次和地区应用到所有勾选题卡。")
        source_tools.addWidget(self.batch_source, 1)
        self.apply_batch_source_button = QPushButton("应用到勾选题卡")
        self.apply_batch_source_button.setEnabled(False)
        self.apply_batch_source_button.setToolTip(
            "只修改已勾选题卡；题面内容和图片不会变化。")
        self.apply_batch_source_button.clicked.connect(self._apply_batch_source)
        self.batch_source.textChanged.connect(
            lambda text: self.apply_batch_source_button.setEnabled(
                bool(str(text or "").strip())))
        source_tools.addWidget(self.apply_batch_source_button)
        root.addLayout(source_tools)

        split = QSplitter(Qt.Horizontal)
        self.items = QListWidget()
        self.items.setSpacing(4)
        self.items.setMinimumWidth(190)
        self.items.currentRowChanged.connect(self._select)
        for i, q in enumerate(self._original):
            item = QListWidgetItem(self._question_list_label(q, i))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, i)
            item.setToolTip(self._question_list_label(q, i, full=True))
            self.items.addItem(item)
        split.addWidget(self.items)
        panel = QWidget()
        panel_l = QVBoxLayout(panel)
        self.preview = QLabel("选择页面预览")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(240)
        self.preview.setStyleSheet(
            f"background:{T.P['panel_soft']};border:1px solid {T.P['glass_edge']};"
            "border-radius:10px;")
        panel_l.addWidget(self.preview, 2)
        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(f"color:{T.P['warn_fg']};")
        panel_l.addWidget(self.warning)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(7)
        for key, caption, multiline in self._TEXT_FIELDS:
            editor = QPlainTextEdit() if multiline else QLineEdit()
            if multiline:
                editor.setMaximumHeight(82)
                editor.setMinimumHeight(58)
            else:
                editor.setMinimumHeight(36)
            self._editors[key] = editor
            signal = (editor.textChanged if isinstance(editor, QPlainTextEdit)
                      else editor.textChanged)
            signal.connect(self._schedule_reference_preview)
            form.addRow(caption, editor)
        panel_l.addWidget(form_widget, 3)
        self.answer_status = QLabel("正在检查题面条件…")
        self.answer_status.setWordWrap(True)
        self.answer_status.setStyleSheet(
            f"font-size:12px;color:{T.P['ink3']};padding:5px 2px;")
        panel_l.addWidget(self.answer_status)
        split.addWidget(panel)
        split.setSizes([220, 820])
        root.addWidget(split, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确认导入所选页")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        for editor in self._editors.values():
            editor.setToolTip("可在确认导入前修改识别结果；原题卡图片会一并保存。")
        if self._original:
            self.items.setCurrentRow(0)

    @staticmethod
    def _question_list_label(question, index, full=False):
        source_meta = (question.get("_reference_source") or
                       question.get("reference_source") or {})
        file_name = os.path.basename(str(source_meta.get("file_name") or ""))
        source = str(question.get("source") or "本地导入")
        page = question.get("page")
        if full:
            location = f"第 {page} 页" if page else "图片"
            return " · ".join(part for part in (
                source, location, file_name) if part)

        compact_file = os.path.splitext(file_name)[0]
        if len(compact_file) > 18:
            compact_file = compact_file[:17] + "…"
        if page:
            label = f"{source} · 第 {page} 页"
            if compact_file:
                label += f" · {compact_file}"
        else:
            label = f"{source} · {compact_file}" if compact_file else (
                f"题卡 {index + 1} · {source}")
        return label

    def _apply_batch_source(self):
        tag = self.batch_source.text().strip()
        if not tag:
            return
        self._store_current()
        match = re.match(r"^(\d{1,2}[.\-]\d{1,2})\s+(.+)$", tag)
        if match:
            date, region = match.group(1), match.group(2).strip()
        else:
            date, region = "本地", tag

        updated = 0
        for row in range(self.items.count()):
            if self.items.item(row).checkState() != Qt.Checked:
                continue
            question = self._original[row]
            question["source"] = tag
            question["date"] = date
            question["region"] = region
            self.items.item(row).setText(
                self._question_list_label(question, row))
            self.items.item(row).setToolTip(
                self._question_list_label(question, row, full=True))
            updated += 1

        if 0 <= self._current < len(self._original):
            question = self._original[self._current]
            self._loading = True
            for key in ("source", "date", "region"):
                self._editors[key].setText(str(question.get(key) or ""))
            self._loading = False

        if updated:
            self.answer_status.setText(f"已将来源应用到 {updated} 张勾选题卡。")
        else:
            self.answer_status.setText("请先勾选要应用来源的题卡。")
        self.answer_status.setStyleSheet(
            f"font-size:12px;color:{T.P['ink2']};padding:5px 2px;")

    def _store_current(self):
        if not 0 <= self._current < len(self._original):
            return
        q = self._original[self._current]
        for key, editor in self._editors.items():
            value = editor.toPlainText() if isinstance(editor, QPlainTextEdit) else editor.text()
            q[key] = value.strip()

    def _select(self, row):
        self._store_current()
        self._current = row
        if not 0 <= row < len(self._original):
            return
        q = self._original[row]
        self._loading = True
        for key, editor in self._editors.items():
            value = str(q.get(key) or "")
            if isinstance(editor, QPlainTextEdit):
                editor.setPlainText(value)
            else:
                editor.setText(value)
        self._loading = False
        path = q.get("_staged_figure", "")
        pixmap = QPixmap(path) if path and os.path.exists(path) else QPixmap()
        self.preview.setPixmap(pixmap.scaled(
            QSize(640, 420), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            if not pixmap.isNull() else QPixmap())
        if pixmap.isNull():
            self.preview.setText("页面预览无法读取，请重新选择文件。")
        else:
            self.preview.setText("")
        self.warning.setText("；".join(q.get("_import_warnings", [])))
        self._reference_timer.stop()
        self._update_reference_preview()

    def _schedule_reference_preview(self, *_args):
        if not self._loading and 0 <= self._current < len(self._original):
            self.answer_status.setText("题面已修改，正在重新检查条件…")
            self._reference_timer.start()

    def _update_reference_preview(self):
        if not 0 <= self._current < len(self._original):
            self.answer_status.setText("")
            return
        q = copy.deepcopy(self._original[self._current])
        for key in ("source", "date", "region", "graph_hint",
                    "conditions_text", "requirements_text", "suggested_text"):
            editor = self._editors.get(key)
            if editor is None:
                continue
            value = (editor.toPlainText() if isinstance(editor, QPlainTextEdit)
                     else editor.text())
            q[key] = value.strip()
        if q.get("imported"):
            source = q.get("reference_source") or q.get("_reference_source") or {}
            direct_answer = source.get("direct_answer") if isinstance(source, dict) else None
            if direct_answer:
                text = "已有视觉 API 参考解答；导入后可在题卡工作台查看或重新作答。"
                color = T.P["warn_fg"]
            else:
                text = "导入后点击题卡工作台中的“解题”，由你选择的视觉模型直接解题。"
                color = T.P["ink2"]
            self.answer_status.setText(text)
            self.answer_status.setStyleSheet(
                f"font-size:12px;color:{color};padding:5px 2px;")
            return
        try:
            result = RE.solve_reference(q)
        except Exception as exc:
            self.answer_status.setText("参考答案检查失败：%s" % exc)
            self.answer_status.setStyleSheet(
                f"font-size:12px;color:{T.P['warn_fg']};padding:5px 2px;")
            return
        if result.get("status") == "READY":
            shape = result.get("spec", {}).get("shape") or "图形"
            count = len(result.get("points") or {})
            self.answer_status.setText(
                "✓ 条件已识别，可生成%s参考答案（%d个坐标点）。导入后可在题库中查看图形和计算过程。"
                % (shape, count))
            color = T.C_GREEN
        else:
            spec = result.get("spec") or {}
            shape = spec.get("shape") or "图形未识别"
            self.answer_status.setText(
                "需校对（%s）：%s" %
                (shape, result.get("reason") or "还有条件未能确认。"))
            color = T.P["warn_fg"]
        self.answer_status.setStyleSheet(
            f"font-size:12px;color:{color};padding:5px 2px;")

    def accept(self):
        self._store_current()
        selected = self.selected_questions()
        if not selected:
            QMessageBox.information(self, "尚未选择页面", "请至少勾选一个需要导入的页面。")
            return
        super().accept()

    def selected_questions(self):
        self._store_current()
        result = []
        for row in range(self.items.count()):
            if self.items.item(row).checkState() != Qt.Checked:
                continue
            q = copy.deepcopy(self._original[row])
            corrections = {}
            for key, before in self._initial_fields[row].items():
                after = str(q.get(key) or "")
                if after != before:
                    corrections[key] = {"before": before, "after": after}
            if corrections:
                q["_reference_corrections"] = corrections
            result.append(q)
        return result


class PdfPageSelectionDialog(ImportDialogShell):
    """先用低清页面预览选择范围，完整识别只在确认后进行。"""

    def __init__(self, preview, file_name="", parent=None):
        pages = list((preview or {}).get("pages") or [])
        super().__init__("选择要导入的 PDF 页面", parent,
                         size=(960, 650), minimum=(760, 500))
        self._pages = pages
        self._current = -1

        root = self.body_layout
        intro = QLabel(
            "%s 共 %d 页。先查看低清预览并勾选需要的页面；确认后才会对所选页面做高清渲染和识别。"
            % (file_name or "PDF 文件", len(pages)))
        intro.setWordWrap(True)
        root.addWidget(intro)

        toolbar = QHBoxLayout()
        self.select_all_button = QPushButton("全选")
        self.clear_selection_button = QPushButton("全不选")
        self.selected_count = QLabel("")
        self.select_all_button.clicked.connect(self._select_all)
        self.clear_selection_button.clicked.connect(self._clear_selection)
        toolbar.addWidget(self.select_all_button)
        toolbar.addWidget(self.clear_selection_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.selected_count)
        root.addLayout(toolbar)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.items = QListWidget()
        self.items.setMinimumWidth(180)
        self.items.currentRowChanged.connect(self._show_page)
        self.items.itemChanged.connect(self._update_selected_count)
        for page in pages:
            number = int(page.get("page") or len(self._pages))
            item = QListWidgetItem("第 %d 页" % number)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable |
                          Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(Qt.CheckState.Checked)
            self.items.addItem(item)
        split.addWidget(self.items)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(8, 0, 0, 0)
        self.preview_image = QLabel("选择左侧页面查看低清预览")
        self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image.setMinimumSize(420, 360)
        self.preview_image.setStyleSheet(
            "background:%s;border:1px solid %s;border-radius:10px;"
            % (T.P["panel_soft"], T.P["glass_edge"]))
        preview_layout.addWidget(self.preview_image, 1)
        self.preview_hint = QLabel("")
        self.preview_hint.setWordWrap(True)
        self.preview_hint.setStyleSheet(
            "font-size:12px;color:%s;background:transparent;" % T.P["ink3"])
        preview_layout.addWidget(self.preview_hint)
        split.addWidget(preview_panel)
        split.setSizes([220, 700])
        root.addWidget(split, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("继续识别所选页")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消导入")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        if pages:
            self.items.setCurrentRow(0)
        self._update_selected_count()

    def _select_all(self):
        for row in range(self.items.count()):
            self.items.item(row).setCheckState(Qt.CheckState.Checked)

    def _clear_selection(self):
        for row in range(self.items.count()):
            self.items.item(row).setCheckState(Qt.CheckState.Unchecked)

    def _update_selected_count(self, *_args):
        count = sum(self.items.item(row).checkState() == Qt.CheckState.Checked
                    for row in range(self.items.count()))
        self.selected_count.setText("已选 %d / %d 页" % (count, self.items.count()))

    def _show_page(self, row):
        self._current = int(row)
        if not 0 <= row < len(self._pages):
            self.preview_image.setPixmap(QPixmap())
            self.preview_image.setText("没有可预览的页面")
            self.preview_hint.setText("")
            return
        page = self._pages[row]
        path = str(page.get("preview_path") or "")
        pixmap = QPixmap(path) if path and os.path.isfile(path) else QPixmap()
        if pixmap.isNull():
            self.preview_image.setPixmap(QPixmap())
            self.preview_image.setText("此页低清预览生成失败；仍可勾选，确认后会重新尝试高清渲染。")
        else:
            self.preview_image.setText("")
            self.preview_image.setPixmap(pixmap.scaled(
                self.preview_image.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        self.preview_hint.setText(
            "第 %d 页 · 低清预览，仅用于选页；确认后才进行完整识别。"
            % int(page.get("page") or row + 1))

    def accept(self):
        if not self.selected_pages():
            QMessageBox.information(self, "尚未选择页面", "请至少勾选一个需要导入的页面。")
            return
        super().accept()

    def selected_pages(self):
        return [int(self._pages[row].get("page") or row + 1)
                for row in range(min(self.items.count(), len(self._pages)))
                if self.items.item(row).checkState() == Qt.CheckState.Checked]


def cleanup_pdf_preview(preview):
    """清理 PDF 选页用的临时缩略图目录。"""
    directory = str((preview or {}).get("preview_dir") or "")
    if not directory or not os.path.basename(directory).startswith(
            "azimuth_pdf_preview_"):
        return
    try:
        shutil.rmtree(directory, ignore_errors=True)
    except OSError:
        pass


def cleanup_staged(questions):
    for q in questions or []:
        path = q.get("_staged_figure")
        if path and q.get("_staged_temp"):
            try:
                os.remove(path)
            except OSError:
                pass


# ================================================================ PDF 后台解析
class PdfPreviewThread(QThread):
    """只生成低清选页缩略图，不运行 OCR 或图形识别。"""

    step = Signal(int, int, str)
    done = Signal(object, list)
    error = Signal(str)

    def __init__(self, pdf, parent=None):
        super().__init__(parent)
        self.pdf = pdf
        self._stop = False
        self.known_hashes = set(BD.imported_source_hashes())

    def stop(self):
        self._stop = True

    def run(self):
        try:
            import pymupdf
        except Exception as e:
            try:
                import fitz as pymupdf
            except Exception:
                self.error.emit(
                    "没能载入 PDF 解析库 PyMuPDF：\n%r\n\n"
                    "请安装包含 PyMuPDF 的完整版本。" % e)
                return

        doc = None
        preview_dir = ""
        try:
            source_hash = _file_sha256(self.pdf)
            if source_hash and source_hash in self.known_hashes:
                self.done.emit({"pages": [], "sha256": source_hash}, [
                    "%s：相同文件已导入，已跳过" % os.path.basename(self.pdf)])
                return
            doc = pymupdf.open(self.pdf)
            total = doc.page_count
            if total <= 0:
                self.error.emit("这个 PDF 一页都没有。")
                return
            preview_dir = tempfile.mkdtemp(prefix="azimuth_pdf_preview_")
            pages = []
            warns = []
            for index in range(total):
                if self._stop:
                    shutil.rmtree(preview_dir, ignore_errors=True)
                    self.done.emit({"pages": [], "sha256": source_hash}, warns)
                    return
                pageno = index + 1
                self.step.emit(pageno, total, "第 %d 页" % pageno)
                preview_path = os.path.join(
                    preview_dir, "page_%05d.png" % pageno)
                try:
                    pix = doc[index].get_pixmap(
                        matrix=pymupdf.Matrix(PDF_PREVIEW_ZOOM,
                                              PDF_PREVIEW_ZOOM), alpha=False)
                    if pix.width > 0 and pix.height > 0:
                        pix.save(preview_path)
                    else:
                        preview_path = ""
                except Exception as exc:
                    preview_path = ""
                    warns.append("第 %d 页低清预览生成失败：%s" %
                                 (pageno, str(exc)[:120]))
                    log.warning("第 %d 页低清预览生成失败 %r", pageno, exc)
                pages.append({"page": pageno, "preview_path": preview_path})
            self.done.emit({"pages": pages, "preview_dir": preview_dir,
                            "sha256": source_hash, "page_count": total}, warns)
        except Exception as exc:
            if preview_dir:
                shutil.rmtree(preview_dir, ignore_errors=True)
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
            log.error("PDF 低清预览失败 %r", exc)
            self.error.emit("PDF 打不开或已损坏：\n%r" % exc)
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass


class PdfImportThread(QThread):
    """后台啃 PDF：逐页提文字 + 渲染题卡图。

    刻意不用 PIL —— Qt 自带的 QImage 就能解码 pymupdf 吐出的 PNG 字节，
    少拖一个依赖，打包体积少好几个 MB。
    """
    step = Signal(int, int, str)      # 当前页 / 总页数 / 状态一句话
    done = Signal(list, list)         # (题目列表, 提醒列表)
    error = Signal(str)

    def __init__(self, pdf, source_tag, start_id, parent=None,
                 page_numbers=None, source_hash=None):
        super().__init__(parent)
        self.pdf = pdf
        self.tag = source_tag
        # 名字别叫 self.start —— 那会把 QThread.start() 这个方法整个盖掉，
        # 然后 th.start() 报 "TypeError: 'int' object is not callable"（实测踩到）。
        self._id0 = int(start_id)
        self._stop = False
        self._staged_questions = []
        self.known_hashes = set(BD.imported_source_hashes())
        self.page_numbers = (None if page_numbers is None else
                             sorted({int(page) for page in page_numbers
                                     if int(page) > 0}))
        self.source_hash = str(source_hash or "")

    def stop(self):
        self._stop = True

    def run(self):
        try:
            import pymupdf
        except Exception as e:
            try:
                import fitz as pymupdf
            except Exception:
                self.error.emit(
                    "没能载入 PDF 解析库 PyMuPDF：\n%r\n\n"
                    "请安装包含 PyMuPDF 的完整版本。" % e)
                return

        doc = None
        try:
            source_hash = self.source_hash or _file_sha256(self.pdf)
            if source_hash and source_hash in self.known_hashes:
                self.done.emit([], [
                    "%s：相同文件已导入，已跳过" % os.path.basename(self.pdf)])
                return
            doc = pymupdf.open(self.pdf)
            total = doc.page_count
            if total <= 0:
                self.error.emit("这个 PDF 一页都没有。")
                return

            selected_indexes = (list(range(total)) if self.page_numbers is None else
                                [page - 1 for page in self.page_numbers
                                 if page <= total])
            if not selected_indexes:
                self.error.emit("未选择任何有效的 PDF 页面。")
                return

            added, warns = [], []
            for position, i in enumerate(selected_indexes, start=1):
                if self._stop:
                    break
                pageno = i + 1
                self.step.emit(position, len(selected_indexes), "第 %d 页" % pageno)

                q = None
                text = ""
                try:
                    text = doc[i].get_text() or ""
                    q = parse_page_text(text, pageno=pageno,
                                        fallback_source=self.tag)
                except Exception as e:
                    warns.append("第 %d 页文字提取失败（已跳过图）" % pageno)
                    log.warning("第 %d 页提取失败 %r", pageno, e)

                # 扫描件 / 纯图形页：没有文字层也照导，图顶着，题面留空给
                # 用户自己补 —— 直接扔页的话这类 PDF 会整本导不进来。
                if q is None:
                    q = new_question(0, self.tag,
                                     "（第 %d 页 · 无文字层，请自行补充题面）"
                                     % pageno)
                    warns.append("第 %d 页没识别出文字（扫描件？），已只存题卡图"
                                 % pageno)

                try:
                    pix = doc[i].get_pixmap(
                        matrix=pymupdf.Matrix(PDF_ZOOM, PDF_ZOOM), alpha=False)
                except Exception as e:
                    pix = None
                    warns.append("第 %d 页渲染失败" % pageno)
                    log.warning("第 %d 页渲染失败 %r", pageno, e)

                if pix is None or pix.width <= 0 or pix.height <= 0:
                    continue
                fd, staged = tempfile.mkstemp(prefix="azimuth_page_", suffix=".png")
                os.close(fd)
                try:
                    # 直接保存 PyMuPDF 的像素图，避免 PNG 字节经 QImage 解码后
                    # 再次压缩；数百页 PDF 导入时可少做一遍高 CPU 图像处理。
                    pix.save(staged)
                    saved = os.path.isfile(staged) and os.path.getsize(staged) > 0
                except Exception as e:
                    log.warning("第 %d 页题卡图保存失败 %r", pageno, e)
                    saved = False
                if not saved:
                    try:
                        os.remove(staged)
                    except OSError:
                        pass
                    warns.append("第 %d 页题卡图暂存失败" % pageno)
                    continue
                q["id"] = None
                q["page"] = pageno
                q["imported"] = True
                q["_staged_figure"] = staged
                q["_staged_temp"] = True
                q["_import_warnings"] = []
                ocr_words = []
                embedded = " ".join(str(q.get(k) or "") for k in
                                     ("conditions_text", "requirements_text", "suggested_text"))
                if len("".join(embedded.split())) >= 20:
                    ocr_words = OCR.recognize_figure_words(staged)
                if ("无文字层" in embedded or
                        len("".join(embedded.split())) < 20):
                    ocr_text, ocr_warning, ocr_words = OCR.recognize_image(
                        staged, with_details=True)
                    q["_import_ocr_text"] = ocr_text
                    if ocr_text:
                        recognized = parse_page_text(
                            ocr_text, pageno=pageno, fallback_source=self.tag)
                        if recognized is not None:
                            for key in ("date", "region", "source", "graph_hint",
                                        "conditions_text", "requirements_text", "suggested_text"):
                                if recognized.get(key):
                                    q[key] = recognized[key]
                        else:
                            q["conditions_text"] = ocr_text
                            ocr_warning = (ocr_warning + "；" if ocr_warning else "") + \
                                "识别文字未能自动分段，已放入附加条件供校对。"
                    if ocr_warning:
                        q["_import_warnings"].append(ocr_warning)
                    if ocr_text:
                        text = ocr_text
                if self._stop:
                    try:
                        os.remove(staged)
                    except OSError:
                        pass
                    break
                shape_hint = " ".join(str(value or "") for value in (
                    q.get("graph_hint"), q.get("conditions_text"),
                    q.get("requirements_text"), q.get("suggested_text"), text))
                diagram_model = DV.analyze(
                    staged, ocr_words, shape_hint=shape_hint)
                if self._stop:
                    try:
                        os.remove(staged)
                    except OSError:
                        pass
                    break
                if ("无文字层" in str(q.get("conditions_text", "")) or
                        not (q.get("conditions_text") or q.get("requirements_text"))):
                    q["_import_warnings"].append(
                        "未提取到题干文字，请人工补充；可检查简体中文 OCR 模型是否可用。")
                if pix.width < 1200 or pix.height < 1200:
                    q["_import_warnings"].append(
                        f"渲染页分辨率偏低（{pix.width}×{pix.height}）。")
                q["_reference_source"] = {
                    "kind": "pdf", "file_name": os.path.basename(self.pdf),
                    "sha256": source_hash,
                    "file_size": os.path.getsize(self.pdf),
                    "page": pageno,
                    "source_text": text,
                    "ocr_words": ocr_words[:1500],
                    "diagram_model": diagram_model,
                    "image_size": [pix.width, pix.height],
                    "recognized_fields": {key: q.get(key, "") for key in (
                        "graph_hint", "conditions_text", "requirements_text",
                        "suggested_text")},
                }
                added.append(q)
                self._staged_questions.append(q)
            if total > 1:
                warns.append(f"共 {total} 页；请在预览中勾选需要导入的页面。")

            if doc is not None:
                doc.close()
            self.done.emit(added, warns)
        except Exception as e:
            cleanup_staged(self._staged_questions)
            self._staged_questions.clear()
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
            log.error("PDF 导入失败 %r", e)
            self.error.emit("PDF 打不开或已损坏：\n%r" % e)


def has_pdf_engine():
    """打包版有没有带 PDF 引擎 —— 没有就早点告诉用户，别让他白等半天"""
    try:
        import pymupdf  # noqa: F401
        return True
    except Exception:
        try:
            import fitz  # noqa: F401
            return True
        except Exception:
            return False
