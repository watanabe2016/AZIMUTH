# -*- coding: utf-8 -*-
"""参考答案浮层：显示用户标注或配置的视觉 API 解答。"""
from __future__ import annotations

import math
import re as _re
import copy
import os
import json
import hashlib
import time
from html import escape as _html_escape

from PySide6.QtCore import (Qt, Signal, QRect, QRectF, QPointF, QThread, QTimer, QUrl,
                            QPropertyAnimation, QParallelAnimationGroup,
                            QEasingCurve)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QFont, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                               QTextBrowser, QWidget, QPushButton, QSizePolicy,
                               QLineEdit, QProgressBar, QStackedWidget, QDialog,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QApplication, QScrollArea)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtSvg import QSvgRenderer
from api_artwork import svg_for_display
from api_teaching_artwork import teaching_svg, artwork_palette

import theme as T
import config as cfg
import answers as AN
import reference_engine as RE
from workflow_feedback import recovery_advice
import bankdata as BD
import diagram_vision as DV
import vision_assist as VA
import ocr as OCR
import qmeta as QM
import qtag as QT
import geo as GEO
import geometry
import question_versions as QV
from knowledge_page import SHAPES as REFERENCE_SHAPES
from window_resize import create_corner_resize_handles, sync_corner_resize_handles
from logger import get_logger
from ui_kit import (IconButton, glass, attach_shadow, Icons, btn_style,
                    animations_enabled)
from motion_runtime import motion_page

log = get_logger()

# 字体名从 theme 里问（跟 trend_dialog 同一来源），别硬编码
FONT = T.pick_font()
_IMPORTED_DIAGRAM_RECOGNIZER_VERSION = "local-cv-topology-2"
_IMPORTED_DIAGRAM_REFRESH_ATTEMPTS = set()

_ANSWER_QUESTION_FIELDS = (
    "graph_hint", "conditions_text", "requirements_text", "suggested_text")


def _question_answer_signature(question):
    """用题卡 ID、题面字段和图片内容给 API 答案建立稳定归属。"""
    if not isinstance(question, dict):
        return ""
    qid = str(question.get("id", "")).strip()
    if not qid:
        return ""
    image_digest = ""
    try:
        image_path = BD.fig_path(int(qid))
        digest = hashlib.sha256()
        with open(image_path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        image_digest = digest.hexdigest()
    except (OSError, TypeError, ValueError, OverflowError):
        pass
    payload = {
        "question_id": qid,
        "fields": {key: str(question.get(key) or "")
                   for key in _ANSWER_QUESTION_FIELDS},
        "image_sha256": image_digest,
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _persist_imported_diagram(question, source_key, source):
    """把本地校正后的题图拓扑保存回用户题库，避免下次打开重复识别。"""
    qid = str((question or {}).get("id", "")).strip()
    if not qid:
        return False
    try:
        items = BD.load()
        changed = False
        for item in items:
            if str(item.get("id", "")).strip() != qid:
                continue
            item[source_key] = copy.deepcopy(source)
            changed = True
            break
        if changed:
            return bool(BD.save(items))
    except Exception:
        log.exception("参考答案 #%s：本地题图拓扑保存失败", qid)
    return False


def _refresh_cached_imported_geometry(question):
    if not isinstance(question, dict) or not question.get("imported"):
        return False
    source_key = ("reference_source" if isinstance(
        question.get("reference_source"), dict) else "_reference_source")
    source = question.get(source_key)
    if not isinstance(source, dict):
        return False
    assist = source.get("condition_assist")
    if not isinstance(assist, dict):
        assist = {}
    diagram = (source.get("diagram_model") or
               assist.get("diagram_model") or {})
    if (diagram.get("recognizer_version") ==
            _IMPORTED_DIAGRAM_RECOGNIZER_VERSION):
        return False
    qid = str(question.get("id", "")).strip()
    if not qid:
        return False
    if qid in _IMPORTED_DIAGRAM_REFRESH_ATTEMPTS:
        return False
    _IMPORTED_DIAGRAM_REFRESH_ATTEMPTS.add(qid)
    ocr_words = source.get("ocr_words") or []
    image_path = BD.fig_path(question.get("id"))
    if not ocr_words or not os.path.isfile(image_path):
        return False

    shape_hint = " ".join(str(question.get(key) or "") for key in (
        "graph_hint", "conditions_text", "requirements_text", "suggested_text"))
    vlm_diagram = (assist.get("diagram_model") or
                   (diagram if diagram.get("source") == "vision_model" else {}))
    refreshed = None
    if (vlm_diagram.get("source") == "vision_model" and
            vlm_diagram.get("geometry_source") != "local_cv_hough"):
        # 保持既有视觉模型校正通路，避免退化旧版本已能修复的四边形。
        refined_vlm = VA.refine_diagram_geometry(
            image_path, ocr_words, source.get("image_size"), vlm_diagram)
        if isinstance(refined_vlm, dict) and refined_vlm.get(
                "geometry_source") == "local_cv_hough":
            refreshed = refined_vlm
    if refreshed is None:
        refreshed = DV.analyze(image_path, ocr_words, shape_hint)
    graph = RE._validated_local_graph(refreshed)
    refined_vlm_valid = (
        refreshed.get("source") == "vision_model" and
        refreshed.get("geometry_source") == "local_cv_hough" and
        bool(refreshed.get("geometry_validated")))
    if graph is None and not refined_vlm_valid:
        return False

    if refreshed.get("source") == "local_cv":
        # OCR 点号给出标签映射，本地线段给出真实边；再用题卡边缘拟合
        # 校准各顶点位置，减少扫描线交点和文字中心的偏差。
        if refreshed.get("shape") != "扫描航线":
            refined_polygon = DV.refine_partial_polygon(
                image_path, ocr_words, source.get("image_size"), refreshed)
            if (isinstance(refined_polygon, dict) and
                    refined_polygon.get("shape") == refreshed.get("shape") and
                    RE._validated_local_graph(refined_polygon) is not None):
                refreshed = refined_polygon
    refreshed["recognizer_version"] = _IMPORTED_DIAGRAM_RECOGNIZER_VERSION

    source = copy.deepcopy(source)
    assist = source.get("condition_assist") or {}
    old_diagram = assist.get("diagram_model") or {}
    stale_warnings = set(old_diagram.get("warnings") or [])
    uncertain = [str(item) for item in assist.get("uncertain_items") or []
                 if str(item) not in stale_warnings]
    if assist:
        assist["diagram_model"] = refreshed
        assist["uncertain_items"] = list(dict.fromkeys(
            uncertain + [str(item) for item in refreshed.get("warnings", [])]))
        assist["source_fields"] = {
            key: str(question.get(key) or "") for key in (
                "graph_hint", "conditions_text", "requirements_text", "suggested_text")}
        source["condition_assist"] = assist
    source["diagram_model"] = refreshed
    question[source_key] = source
    persisted = _persist_imported_diagram(question, source_key, source)
    log.info("参考答案 #%s：本地识图拓扑已校正%s",
             question.get("id"), "并保存" if persisted else "")
    return True


# ================================================================ 航线图

class RouteMap(QWidget):
    """标准答案航线俯视图（v1.9.9 五三：「参考答案要有图形化」）。

    数据就是 ``answers.derive()`` 的 points —— 图上画的**就是判题比对用的
    那组坐标**，图和文字永远同源，不会出现"图一个答案、字另一个答案"。

    画法零依赖：经纬度等距投影成像素（北为上），QPainter 现画。
    视觉参照 LinkGCS2 的航线显示（绿线 + 编号圆点 + 边长/方位标注），
    用户在地面站里对着画时两边长得像，核对最快。
    """

    def __init__(self, parent=None, scale=1.0):
        super().__init__(parent)
        self._scale = scale
        self._pts = None        # [(name, {lat,lon,why}), ...] 保序
        self._raw = None        # [(x米, y米), ...] 投影后的米制坐标
        self._geometry_edges = []
        self._flight_edges = []
        self._access_edges = []
        self._flight_measurements = {}
        self._note = None       # 图底部说明（形状/边长/核对口径）
        self._full = False      # v1.9.11：是否画出了完整多边形
        self._candidate = False  # 参考草案使用虚线显示，不冒充核验答案
        self._north_up = True   # 坐标是否采用北向为正的航段坐标
        self._model = None
        self._notice = None
        self._api_direct = False
        self._svg_renderer = None
        self._zoom_button = IconButton("放大查看", None, "bare", font_size=10,
                                       radius=7, scale=scale)
        self._zoom_button.setParent(self)
        self._zoom_button.setFixedHeight(28)
        self._zoom_button.clicked.connect(self.open_artwork)
        self._zoom_button.hide()
        self._artwork_mode = IconButton("模型原图", None, "bare", font_size=10,
                                        radius=7, scale=scale)
        self._artwork_mode.setParent(self)
        self._artwork_mode.setFixedHeight(28)
        self._artwork_mode.clicked.connect(self.toggle_artwork_mode)
        self._artwork_mode.hide()
        self.setMinimumHeight(int(240 * scale))
        self.hide()

    # -------------------------------------------------- 数据
    def _clear_api_artwork(self):
        renderer = self._svg_renderer
        self._svg_renderer = None
        self._api_direct = False
        self._zoom_button.hide()
        self._artwork_mode.hide()
        self.setMinimumHeight(int(240 * self._scale))
        if renderer is not None:
            renderer.deleteLater()

    def set_derived(self, d, note=None):
        """喂入 derive 结果；点不足 2 个画不了航线，整个隐藏。

        ``note``：图底部的核对口径说明（形状/边长）—— 图形其余顶点的
        方位标注在**题卡图片**上、文字题面里没有，软件推不出就不瞎画，
        但要把"判题到底核对什么"跟用户说清楚。
        """
        self._clear_api_artwork()
        self._note = note
        self._candidate = False
        self._north_up = True
        self._model = None
        self._notice = None
        self._flight_measurements = {}
        pts = dict((d or {}).get("points") or {})
        # ★ v1.9.11 布局查表：points_full 是合成出的完整多边形顶点
        #   （图形1..n 号点）—— 并进 points，链式连线 + 首尾闭合自然成型
        for k, v in ((d or {}).get("points_full") or {}).items():
            pts[k] = v
        self._full = bool((d or {}).get("points_full"))
        if len(pts) < 2:
            self._pts = self._raw = None
            self._geometry_edges = self._flight_edges = self._access_edges = []
            self.hide()
            self.update()
            return
        items = list(pts.items())
        # 连线：按推导顺序连成链（起飞 → 避让 → 图形1 → …）；
        # 名称里带编号的"图形点"满 3 个时首尾再连一条（图形闭合）。
        edges = [(items[i][0], items[i + 1][0])
                 for i in range(len(items) - 1)]
        gidx = [i for i, (nm, _p) in enumerate(items)
                if _re.search(r"\d", nm or "")]
        if len(gidx) >= 3:
            edges.append((items[gidx[-1]][0], items[gidx[0]][0]))

        lats = [p["lat"] for _, p in items]
        lons = [p["lon"] for _, p in items]
        lat0 = sum(lats) / len(lats)
        kx = 111320.0 * math.cos(math.radians(lat0))   # 经度→米（纬度修正）
        ky = 110540.0                                   # 纬度→米
        lon0, lat_min = min(lons), min(lats)
        raw = [((p["lon"] - lon0) * kx, (lat_min - p["lat"]) * ky)
               for _, p in items]     # 北为上 → 纬度越大 y 越小

        self._pts, self._raw = items, raw
        self._geometry_edges, self._flight_edges = [], edges
        self._access_edges = []
        self.setVisible(True)
        self.update()

    def set_solution(self, result):
        """展示求解器输出的实际坐标和明确连线；待复核时不画猜测路线。"""
        self._clear_api_artwork()
        self._display_result = copy.deepcopy(result)
        self._model = None
        self._candidate = False
        self._north_up = False
        self._note = None
        self._notice = None
        self._api_direct = bool((result or {}).get("api_direct"))
        self._svg_renderer = None
        if not result:
            self._pts = self._raw = None
            self._geometry_edges = self._flight_edges = self._access_edges = []
            self._flight_measurements = {}
            self.hide()
            self.update()
            return
        is_candidate = (result.get("status") != "READY" and
                        bool(result.get("candidate")))
        if result.get("status") != "READY" and not is_candidate and not self._api_direct:
            self._pts = self._raw = None
            self._geometry_edges = self._flight_edges = self._access_edges = []
            self._flight_measurements = {}
            self._notice = result.get("reason") or "题面条件尚未完成校对。"
            self.setVisible(True)
            self.update()
            return
        self._candidate = is_candidate
        self._north_up = (bool(result.get("candidate_north_up")) if is_candidate else
                          bool(result.get("north_up")) if self._api_direct else True)
        self._note = result.get("candidate_note") if is_candidate else result.get("note")
        if self._api_direct:
            self._geometry_edges = [tuple(edge) for edge in result.get("geometry_edges") or []]
            self._flight_edges = [tuple(edge) for edge in result.get("flight_edges") or []]
            self._access_edges = [tuple(edge) for edge in result.get("access_edges") or []]
            styled = teaching_svg(result)
            self._artwork_palette = artwork_palette()
            self._show_model_original = bool(result.get("_show_model_original")) or not styled
            svg_data = svg_for_display(result.get("route_svg") if self._show_model_original else styled)
            if svg_data is None:
                svg_data = svg_for_display(result.get("route_svg"))
                self._show_model_original = True
            if svg_data:
                renderer = QSvgRenderer(svg_data, self)
                if renderer.isValid():
                    self._svg_renderer = renderer
                    self.setMinimumHeight(int(380 * self._scale))
                    self._zoom_button.setVisible(getattr(self, "_allow_artwork_zoom", True))
                    self._artwork_mode.setText("教学航图" if self._show_model_original else "模型原图")
                    self._artwork_mode.setVisible(bool(styled and result.get("route_svg")))
                    self.setToolTip("双击放大航图")
                    self._pts = self._raw = None
                    self.setVisible(True)
                    self.update()
                    return
                renderer.deleteLater()
        points = ((result.get("candidate_points") or result.get("points"))
                  if is_candidate else result.get("points")) or {}
        order = {"H": 0, "A": 1, "T": 2}
        items = sorted(points.items(), key=lambda item: (
            order.get(item[0], 3),
            0 if item[0].isdigit() else 1,
            int(item[0]) if item[0].isdigit() else item[0]))
        if len(items) < 2:
            self._pts = self._raw = None
            self._geometry_edges = self._flight_edges = self._access_edges = []
            self._flight_measurements = {}
            self._notice = "答案点位不足，无法绘制航线图。"
            self.setVisible(True)
            self.update()
            return
        self._pts = items
        # 求解坐标采用东、北为正；绘图区的纵轴向下，故将北坐标取反。
        y_sign = 1 if self._api_direct and result.get("y_axis") == "down" else -1
        self._raw = [(float(point["x"]), y_sign * float(point["y"]))
                     for _name, point in items]
        names = {name for name, _point in items}
        self._geometry_edges = [tuple(map(str, edge[:2])) for edge in
                                (result.get("candidate_geometry_edges") or
                                 result.get("geometry_edges", []))
                                if len(edge) >= 2 and
                                str(edge[0]) in names and str(edge[1]) in names]
        self._flight_edges = [tuple(map(str, edge[:2])) for edge in
                              (result.get("candidate_flight_edges") or
                               result.get("flight_edges", []))
                              if len(edge) >= 2 and
                              str(edge[0]) in names and str(edge[1]) in names]
        self._access_edges = [tuple(map(str, edge[:2])) for edge in
                              (result.get("candidate_access_edges") or
                               result.get("access_edges", []))
                              if len(edge) >= 2 and
                              str(edge[0]) in names and str(edge[1]) in names]
        legs = (result.get("candidate_flight_legs") if is_candidate else
                result.get("flight_legs")) or []
        self._flight_measurements = self._measurements_from_legs(legs, self._api_direct)
        self._full = not self._candidate
        self.setVisible(True)
        self.update()

    def mouseDoubleClickEvent(self, event):
        if (self._api_direct and getattr(self, "_allow_artwork_zoom", True)
                and getattr(self, "_display_result", None)):
            self.open_artwork()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._zoom_button.setGeometry(max(8, self.width() - 104), 8, 94, 28)
        self._artwork_mode.setGeometry(max(110, self.width() - 204), 8, 94, 28)

    def toggle_artwork_mode(self):
        result = copy.deepcopy(self._display_result)
        result["_show_model_original"] = not self._show_model_original
        self.set_solution(result)

    def open_artwork(self):
        if not self._api_direct or not getattr(self, "_display_result", None):
            return
        dialog = QDialog(self.window())
        dialog.setWindowTitle("航图 · 放大查看")
        dialog.setStyleSheet(f"QDialog{{background:{T.P['dialog_bg']};}}")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        toolbar = QHBoxLayout()
        title = QLabel("模型原图" if self._show_model_original else "教学航图")
        title.setStyleSheet(f"color:{T.P['ink']};font-size:16px;font-weight:600;")
        toolbar.addWidget(title, 1)
        scroll = QScrollArea(dialog)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea, QScrollArea > QWidget > QWidget"
                            f"{{background:{T.P['dialog_bg']};}}")
        artwork = RouteMap(scale=self._scale)
        display = copy.deepcopy(self._display_result)
        display["_show_model_original"] = self._show_model_original
        artwork.set_solution(display)
        artwork._allow_artwork_zoom = False
        artwork._zoom_button.hide()
        artwork._artwork_mode.hide()
        scroll.setWidget(artwork)
        zoom = [1.0]

        def resize_artwork(factor):
            zoom[0] = max(1.0, min(4.0, factor))
            artwork.setMinimumSize(int(scroll.viewport().width() * zoom[0]),
                                   int(scroll.viewport().height() * zoom[0]))

        for label, action in (("缩小", lambda: resize_artwork(zoom[0] / 1.25)),
                              ("放大", lambda: resize_artwork(zoom[0] * 1.25)),
                              ("适合窗口", lambda: resize_artwork(1.0)),
                              ("关闭", dialog.accept)):
            button = IconButton(label, None, "bare", font_size=10, radius=7,
                                scale=self._scale)
            button.clicked.connect(action)
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        layout.addWidget(scroll, 1)
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 1000, 800)
        dialog.resize(min(1100, int(available.width() * 0.9)),
                      min(850, int(available.height() * 0.9)))
        dialog.exec()
        dialog.deleteLater()

    @staticmethod
    def _measurements_from_legs(legs, preserve_values=False):
        measurements = {}
        for leg in legs if isinstance(legs, list) else []:
            if not isinstance(leg, dict):
                continue
            start, end = str(leg.get("from") or ""), str(leg.get("to") or "")
            if not start or not end:
                continue
            measured = {}
            try:
                distance = float(leg.get("distance_m"))
                if math.isfinite(distance) and (preserve_values or distance >= 0):
                    measured["distance_m"] = distance
            except (TypeError, ValueError, OverflowError):
                pass
            try:
                bearing = float(leg.get("bearing_deg"))
                if math.isfinite(bearing):
                    measured["bearing_deg"] = bearing if preserve_values else bearing % 360.0
            except (TypeError, ValueError, OverflowError):
                pass
            if measured:
                measurements[(start, end)] = measured
        return measurements

    @staticmethod
    def _flight_leg_text(measurement, approximate=False):
        if not isinstance(measurement, dict):
            return ""

        def number(value):
            if math.isclose(value, round(value), rel_tol=0.0, abs_tol=1e-6):
                return "%.0f" % value
            return ("%.2f" % value).rstrip("0").rstrip(".")

        parts = []
        distance = measurement.get("distance_m")
        if isinstance(distance, (int, float)):
            parts.append(("≈" if approximate else "") + number(float(distance)) + "m")
        bearing = measurement.get("bearing_deg")
        if isinstance(bearing, (int, float)):
            parts.append(("≈" if approximate else "") + number(float(bearing)) + "°")
        return " · ".join(parts)

    def set_notice(self, text):
        self._clear_api_artwork()
        self._model = None
        self._candidate = False
        self._north_up = False
        self._pts = self._raw = None
        self._geometry_edges = self._flight_edges = self._access_edges = []
        self._note = None
        self._notice = text
        self.setVisible(bool(text))
        self.update()

    def set_model(self, shape):
        """展示图形参考模型；模型只说明题型，不暗示具体航点坐标。"""
        self._clear_api_artwork()
        self._model = shape
        self._candidate = False
        self._north_up = False
        self._pts = self._raw = None
        self._geometry_edges = self._flight_edges = self._access_edges = []
        self._notice = None
        self.setVisible(bool(shape))
        self.update()

    # -------------------------------------------------- 画
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        W, H = self.width(), self.height()
        P = T.P

        # 底卡
        # ★ 面板系颜色（panel_soft/glass_edge）是 CSS rgba 字符串，
        #   QColor 解析不了会变 invalid → 画出来是纯黑（浅色主题下露馅）。
        #   自绘图只用 #RRGGBB 实体色（ink 系 / C_* 系），透明度自己 setAlpha。
        base = QColor(P["ink3"])
        base.setAlpha(26)
        edge = QColor(P["ink3"])
        edge.setAlpha(70)
        p.setPen(QPen(edge, 1))
        p.setBrush(base)
        p.drawRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), 10, 10)

        renderer = getattr(self, "_svg_renderer", None)
        if renderer is not None:
            if not self._show_model_original and self._artwork_palette != artwork_palette():
                # 主题切换只重绘展示用SVG，不更改缓存中的模型原图和数值。
                data = svg_for_display(teaching_svg(self._display_result))
                if data:
                    renderer.load(data)
                self._artwork_palette = artwork_palette()
            # 原图使用完整纸白画布；只做等比显示，不改模型的路径和文字。
            top = 44 if getattr(self, "_allow_artwork_zoom", True) else 12
            canvas = QRectF(12, top, max(1, W - 24), max(1, H - top - 12))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("white" if self._show_model_original else self._artwork_palette["canvas"]))
            p.drawRoundedRect(canvas, 8, 8)
            target = canvas.adjusted(8, 8, -8, -8)
            box = renderer.viewBoxF()
            if box.width() > 0 and box.height() > 0:
                factor = min(target.width() / box.width(), target.height() / box.height())
                size = QPointF(box.width() * factor, box.height() * factor)
                target = QRectF(target.center().x() - size.x() / 2,
                                target.center().y() - size.y() / 2, size.x(), size.y())
            renderer.render(p, target)
            p.setFont(QFont(FONT, max(9, int(10 * self._scale)), QFont.DemiBold))
            p.setPen(QColor(P["ink2"]))
            if getattr(self, "_allow_artwork_zoom", True):
                p.drawText(QRectF(14, 8, W - 128, 28), Qt.AlignLeft | Qt.AlignVCenter,
                           "模型原图" if self._show_model_original else "教学航图")
            p.end()
            return

        if self._model:
            model = next((draw for name, _desc, draw in REFERENCE_SHAPES
                          if name == self._model), None)
            if model:
                sample = model()
                target = QRectF(20, 27, W - 40, H - 62)
                p.drawPixmap(target, sample, QRectF(sample.rect()))
            p.setPen(QColor(P["ink2"]))
            p.setFont(QFont(FONT, max(9, int(10 * self._scale)), QFont.Bold))
            p.drawText(QRectF(16, 5, W - 32, 24),
                       Qt.AlignCenter, "%s · 图形参考模型" % self._model)
            p.setPen(QColor(P["ink3"]))
            p.setFont(QFont(FONT, max(8, int(8 * self._scale))))
            p.drawText(QRectF(10, H - 30, W - 20, 22),
                       Qt.AlignCenter, "示意图不代表本题的方位、比例或标准坐标")
            p.end()
            return

        if self._notice:
            p.setPen(QColor(P["ink2"]))
            p.setFont(QFont(FONT, max(9, int(11 * self._scale)), QFont.Bold))
            p.drawText(QRectF(18, 18, W - 36, 28),
                       Qt.AlignLeft | Qt.AlignVCenter, "参考航线暂未生成")
            p.setPen(QColor(P["ink3"]))
            p.setFont(QFont(FONT, max(8, int(9 * self._scale))))
            p.drawText(QRectF(18, 52, W - 36, H - 68),
                       Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                       self._notice)
            p.end()
            return

        if not self._pts or not self._raw:
            p.setPen(QColor(P["ink3"]))
            p.setFont(QFont(FONT, max(8, int(9 * self._scale))))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "这题推不出完整坐标链，画不了航线图")
            p.end()
            return

        xmin = min(x for x, _ in self._raw)
        xmax = max(x for x, _ in self._raw)
        ymin = min(y for _, y in self._raw)
        ymax = max(y for _, y in self._raw)
        pad = int(34 * self._scale)
        top = int(20 * self._scale)          # 顶部留标题行
        bot = pad + (14 if self._note else 0)   # 底部说明行再多留一点
        sw = max(xmax - xmin, 1.0)
        sh = max(ymax - ymin, 1.0)
        s = min((W - 2 * pad) / sw, (H - top - bot) / sh)
        ox = (W - sw * s) / 2.0
        oy = top + (H - top - bot - sh * s) / 2.0

        def m(i):
            x, y = self._raw[i]
            return QPointF(ox + (x - xmin) * s, oy + (y - ymin) * s)

        # 标题 + 指北
        p.setPen(QColor(P["ink3"]))
        p.setFont(QFont(FONT, max(7, int(8 * self._scale))))
        title = (("视觉识别参考草案（按航段方位绘制 · 北为上）" if self._north_up
                  else "视觉识别参考草案（图形示意 · 方位未校准）") if self._candidate
                 else "标准答案航线（红色进场 · 绿色图形 · 北为上）" if self._access_edges
                 else "标准答案航线（完整图形 · 俯视 · 北为上）" if self._full
                 else "标准答案关键点（俯视 · 北为上）")
        p.drawText(QRectF(10, 4, W - 46, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   "模型航图 · 原坐标直显" if self._api_direct else title)
        p.drawText(QRectF(W - 40, 4, 32, 16), Qt.AlignRight | Qt.AlignVCenter,
                   "N↑" if self._north_up else "示意")

        # ★ v1.9.10：核对口径说明 —— 五三反馈"只做了一半"。
        #   完整多边形顶点的方位标注在**题卡图片**上（软件不读图），
        #   文字题面推不出，就不瞎画；但图上必须写清"判题核对的是
        #   这些关键点 + 边长 + 参数"，并指路「自动扫描」逐项核对。
        if self._note:
            p.setFont(QFont(FONT, max(6, int(7 * self._scale))))
            p.drawText(QRectF(10, H - 18, W - 20, 16),
                       Qt.AlignLeft | Qt.AlignVCenter, self._note)

        index_by_name = {name: i for i, (name, _point) in enumerate(self._pts)}
        # 图形结构只作底图，实际飞行顺序使用明确的航线边逐段覆盖绘制。
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(P["ink3"]), 1.4, Qt.DashLine))
        for a, b in self._geometry_edges:
            if a in index_by_name and b in index_by_name:
                p.drawLine(m(index_by_name[a]), m(index_by_name[b]))
        p.setPen(QPen(QColor(T.C_AMBER if self._candidate else T.C_GREEN), 2.4,
                      Qt.DashLine if self._candidate else Qt.SolidLine))
        for a, b in self._flight_edges:
            if a in index_by_name and b in index_by_name:
                p.drawLine(m(index_by_name[a]), m(index_by_name[b]))
        p.setPen(QPen(QColor(T.C_AMBER if self._candidate else T.C_RED), 2.8,
                      Qt.DashLine if self._candidate else Qt.SolidLine))
        for a, b in self._access_edges:
            if a in index_by_name and b in index_by_name:
                p.drawLine(m(index_by_name[a]), m(index_by_name[b]))

        # 边标注：距离 + 方位（放在边中点法向偏外一点）
        fs = max(6, int(7 * self._scale))
        p.setFont(QFont(FONT, fs))
        labeled_edges = list(dict.fromkeys(self._flight_edges + self._access_edges))
        for name_a, name_b in labeled_edges:
            if name_a not in index_by_name or name_b not in index_by_name:
                continue
            i, j = index_by_name[name_a], index_by_name[name_b]
            a, b = m(i), m(j)
            if self._api_direct:
                txt = self._flight_leg_text(
                    self._flight_measurements.get((name_a, name_b)), approximate=False)
                if not txt:
                    continue
            elif self._candidate:
                measurement = self._flight_measurements.get((name_a, name_b))
                if measurement is None:
                    reverse = self._flight_measurements.get((name_b, name_a))
                    if reverse:
                        measurement = dict(reverse)
                        if "bearing_deg" in measurement:
                            measurement["bearing_deg"] = (
                                measurement["bearing_deg"] + 180.0) % 360.0
                txt = self._flight_leg_text(measurement, approximate=True)
                if not txt:
                    continue
            else:
                pa, pb = self._pts[i][1], self._pts[j][1]
                dx = float(pb["x"]) - float(pa["x"])
                dy = float(pb["y"]) - float(pa["y"])
                d = math.hypot(dx, dy)
                brg = math.degrees(math.atan2(dx, dy)) % 360.0
                txt = "%.0fm · %.0f°" % (d, brg)
            cx, cy = (a.x() + b.x()) / 2.0, (a.y() + b.y()) / 2.0
            tw = p.fontMetrics().horizontalAdvance(txt)
            # 沿边的法线往外偏 10px；dx=0（竖边）时往右偏
            dx, dy = b.x() - a.x(), b.y() - a.y()
            ln = math.hypot(dx, dy) or 1.0
            nx, ny = (-dy / ln, dx / ln)
            if ny > 0 or (ny == 0 and nx < 0):   # 统一朝"外"侧
                nx, ny = -nx, -ny
            bx = cx + nx * 12 - tw / 2.0
            by = cy + ny * 12 - 7
            # 小底板（描边字在浅色底上看不清，垫一块不透明底更稳）
            # ★ 白色系实体色 + 按主题定 alpha —— 深色主题微提亮、浅色主题白纸片
            p.setPen(Qt.NoPen)
            bg = QColor(255, 255, 255)
            bg.setAlpha(36 if T.mode == "dark" else 225)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(bx - 3, by, tw + 6, 14), 4, 4)
            p.setPen(QColor(P["ink2"]))
            p.drawText(QRectF(bx - 3, by, tw + 6, 14),
                       Qt.AlignCenter, txt)

        # 点 + 编号
        for i, (name, pt) in enumerate(self._pts):
            pos = m(i)
            if name == "H" or "起" in name:
                col, r, lab = T.C_AMBER, 9.0, "起"
            elif name == "A" or "避" in name:
                col, r, lab = T.C_RED, 8.0, "避"
            elif name == "T" or "中转" in name:
                col, r, lab = T.C_AMBER, 8.0, "转"
            else:
                col, r, lab = T.C_BLUE, 8.0, ""
                mm = _re.search(r"(\d+)", name or "")
                if mm:
                    lab = mm.group(1)
            p.setPen(QPen(QColor("#ffffff"), 1.6))
            p.setBrush(QColor(col))
            p.drawEllipse(pos, r, r)
            p.setPen(QColor("#ffffff"))
            p.setFont(QFont(FONT, max(6, int(7 * self._scale)), QFont.Bold))
            p.drawText(QRectF(pos.x() - r, pos.y() - r, 2 * r, 2 * r),
                       Qt.AlignCenter, lab)
        p.end()


# ================================================================ 文本渲染

def _conf_note(conf):
    return {
        "full": ("关键点链条完整，这组坐标就是标准答案", T.C_GREEN),
        "partial": ("部分关键点题面没给全，只能推出这些", T.C_AMBER),
        "none": ("题面缺少可起算的绝对坐标或方位链条，坐标推不出来",
                 T.P["ink3"]),
    }.get(conf, ("", T.P["ink3"]))


def _fmt_num(v):
    """参数值统一成 '300 米' / '6 m/s' / '停止转弯' 这种好读的样子"""
    if v is None or v == "":
        return "—"
    if isinstance(v, (int, float)):
        s = ("%g" % v)
        return s
    return str(v)


def _conv_note(raw, shown):
    """题面原写法是否**被换算过** —— 只比较数字部分，忽略空格与单位写法差异。

    例：raw="d值为18km/h"、shown="5 m/s" → True（要标出"题面写 18km/h"）
        raw="a值为225米"、shown="225 米" → False（没换算，不用啰嗦）
    """
    import re as _re
    nums = _re.findall(r"[\d.]+", raw or "")
    if not nums:
        return False
    a = nums[-1]
    try:
        b = _fmt_num(float(a))
    except Exception:
        return False
    return b != shown.split(" ")[0].strip()


def _render_reference(q, result):
    spec = (result or {}).get("spec") or {}
    ready = result.get("status") == "READY"
    status = "计算完成，已逐项复核" if ready else "需要校对，暂不显示猜测航线"
    source = ("作答核验参考答案" if result.get("source") == "verified_attempt"
              else "自动参考答案")
    color = T.C_GREEN if ready else T.C_AMBER
    parts = [
        f"<div style='font-size:11px;color:{color};font-weight:700;"
        f"margin-bottom:8px;'>● {_esc(source)} · {_esc(status)}</div>"]
    if ready:
        parts.append(
            f"<div style='font-size:12px;color:{T.P['ink']};line-height:1.7;'>"
            f"题型：<b>{_esc(spec.get('shape') or '未分类')}</b>　"
            f"坐标：{_esc(result.get('coordinate_system') or '局部坐标')}"
            f"</div>")
        points = result.get("points") or {}
        if points:
            parts.append(_sec("标准航点坐标"))
            for name, point in points.items():
                if point.get("lat") is not None and point.get("lon") is not None:
                    coord = "N %.6f　E %.6f" % (point["lat"], point["lon"])
                else:
                    coord = "东 %.2f m　北 %.2f m" % (point["x"], point["y"])
                parts.append(
                    f"<div style='font-size:12px;line-height:1.7;'>"
                    f"<b>{_esc(name)}</b>　<span style='color:{T.C_BLUE};"
                    f"font-family:Consolas,monospace;'>{_esc(coord)}</span></div>")
        for title, key in (("计算过程", "steps"), ("条件复核", "checks")):
            values = result.get(key) or []
            if values:
                parts.append(_sec(title))
                parts.extend(
                    f"<div style='font-size:12px;color:{T.P['ink']};"
                    f"line-height:1.7;'>• {_esc(value)}</div>"
                    for value in values)
        params = spec.get("parameters") or {}
        shown = [(key, value) for key, value in params.items()
                 if not str(key).startswith("__") and value is not None]
        if shown:
            parts.append(_sec("题面飞行参数"))
            parts.append("<div style='font-size:12px;line-height:1.7;'>" +
                         "　".join("%s=%s" % (_esc(key), _esc(_fmt_num(value)))
                                   for key, value in shown) + "</div>")
    else:
        parts.append(
            f"<div style='font-size:12px;color:{T.P['ink']};line-height:1.7;'>"
            f"{_esc(result.get('reason') or '有条件未能确认。')}</div>")
        category, advice = recovery_advice(result.get("reason"), result.get("details"))
        parts.append(_sec(category))
        parts.append(f"<div style='font-size:12px;color:{T.P['ink2']};line-height:1.7;'>{_esc(advice)}</div>")
        if result.get("details"):
            parts.append("<div style='font-size:11px;color:%s;line-height:1.6;'>%s</div>" %
                         (T.P["ink3"], "<br/>".join(
                             "• " + _esc(item) for item in result["details"])))
        parts.append(_sec("已识别条件（可在导入校对中修正）"))
        shape = spec.get("shape") or "未识别图形"
        nodes = spec.get("nodes") or []
        parts.append(
            f"<div style='font-size:12px;line-height:1.6;'>"
            f"图形：{_esc(shape)}　航点：{_esc('、'.join(nodes) or '未识别')}"
            f"</div>")
        constraints = []
        for item in spec.get("distance_constraints", []):
            constraints.append("%s-%s = %s m" %
                               (item["a"], item["b"], _fmt_num(item["value"])))
        for item in spec.get("angle_constraints", []):
            constraints.append("∠%s = %s°" %
                               (item["vertex"], _fmt_num(item["value"])))
        for item in spec.get("bearing_constraints", []):
            constraints.append("%s→%s = %s°" %
                               (item["a"], item["b"], _fmt_num(item["value"])))
        for item in spec.get("segment_constraints", []):
            constraints.append("%s→%s：方位%s°，距离%s m" %
                               (item["b"], item["a"], _fmt_num(item["bearing"]),
                                _fmt_num(item["distance"])))
        if constraints:
            parts.append(
                f"<div style='font-size:11px;color:{T.P['ink2']};line-height:1.7;'>"
                + "<br/>".join(_esc(item) for item in constraints[:12]) + "</div>")
        parts.append(
            f"<div style='font-size:11px;color:{T.P['ink3']};margin-top:8px;'>"
            "修改导入题干或点位关系后会重新计算；确认条件正确时才展示参考航线。</div>")
    return "".join(parts), {"source": "auto", "status": result.get("status"),
                            "n_points": len(result.get("points") or {})}


def render(q, derived=None, reference=None):
    """渲染成一段**纯文本**（供 QTextBrowser，富文本用 HTML 标签拼）。

    返回 (html, meta)：
      meta = {"conf":..., "source": "manual"|"auto"|"none", "n_points":..., ...}
    """
    qid = q.get("id")
    manual = QM.answer_of(qid)

    if manual:
        html = (
            f"<div style='font-size:12px;color:{T.C_GREEN};font-weight:700;"
            f"margin-bottom:6px;'>✎ 你自己标注的答案</div>"
            f"<div style='font-size:13px;color:{T.P['ink']};line-height:1.7;'>"
            f"{_esc(manual).replace(chr(10), '<br/>')}</div>")
        return html, {"conf": "manual", "source": "manual", "n_points": 0}

    if reference is not None:
        return _render_reference(q, reference)

    d = derived if derived is not None else AN.derive(q)
    conf = d.get("conf", "none")
    note, col = _conf_note(conf)

    parts = []
    parts.append(
        f"<div style='font-size:11px;color:{col};font-weight:700;"
        f"margin-bottom:8px;'>● 自动生成 · {note}</div>")

    pts = d.get("points") or {}
    if pts:
        parts.append(_sec("关键点坐标（WGS84）"))
        for name, p in pts.items():
            why = p.get("why") or ""
            parts.append(
                f"<div style='margin:0 0 7px 0;'>"
                f"<span style='font-weight:700;color:{T.P['ink']};'>"
                f"{_esc(name)}</span><br/>"
                f"<span style='color:{T.C_BLUE};font-family:Consolas,monospace;"
                f"font-size:12px;'>N {p['lat']:.6f}&nbsp;&nbsp;"
                f"E {p['lon']:.6f}</span>"
                + (f"<br/><span style='font-size:11px;color:{T.P['ink3']};'>"
                   f"↳ {_esc(why)}</span>" if why else "")
                + "</div>")
    else:
        parts.append(
            f"<div style='font-size:12px;color:{T.P['ink3']};'>"
            f"题面没给出可起算的绝对坐标 / 方位链条，坐标无法推导。</div>")

    params = d.get("params") or []
    if params:
        parts.append(_sec("飞行参数（来自题面「建议值」）"))
        parts.append("<div style='margin:0 0 4px 0;'>")
        for it in params:
            key = it.get("key") or "?"
            mean = it.get("meaning") or ""
            val = it.get("value")
            unit = it.get("unit") or ""
            shown = "%s%s" % (_fmt_num(val), (" " + unit) if unit else "")
            # ★ 题面原写法与归一值不一致时（"18km/h" → "5 m/s"），把原写法标出来 ——
            #   否则用户看到 5 m/s 会以为软件改了他的题面数据。
            raw = (it.get("raw") or "").strip()
            note = ""
            if raw and unit and _conv_note(raw, shown):
                note = "（题面写 %s）" % raw
            parts.append(
                f"<div style='font-size:12px;color:{T.P['ink']};"
                f"line-height:1.9;'>"
                f"<span style='display:inline-block;min-width:56px;"
                f"color:{T.P['ink2']};font-weight:700;'>{_esc(key)}</span>"
                f"<span style='font-weight:700;'>{_esc(shown)}</span>"
                + (f"<span style='color:{T.P['ink3']};'>　{_esc(note)}</span>"
                   if note else "")
                + (f"<span style='color:{T.P['ink3']};'>　{_esc(mean)}</span>"
                   if mean else "")
                + "</div>")
        parts.append("</div>")

    extras = d.get("extras") or {}
    if extras:
        parts.append(_sec("其他要求"))
        for k, v in extras.items():
            parts.append(
                f"<div style='font-size:12px;color:{T.P['ink']};"
                f"line-height:1.9;'>{_esc(k)}：<span style='font-weight:700;'>"
                f"{_esc(_fmt_num(v))}</span></div>")

    uns = d.get("unresolved") or []
    if uns:
        parts.append(
            f"<div style='font-size:11px;color:{T.P['ink3']};margin-top:8px;"
            f"border-top:1px solid {T.P['glass_soft_edge']};padding-top:6px;'>"
            f"题面这 {len(uns)} 条方位约束没能串进链条（不是你的问题，"
            f"是题干写法软件没认出来）：<br/>"
            + "<br/>".join(_esc(u) for u in uns[:4]) + "</div>")

    # ★ v1.9.9：给「标注」一句看得懂的引导 —— 五三反馈"标注功能意义不明"。
    #   这功能是给"自动答案推错了/不全"的题兜底用的，不说清楚确实猜不到。
    parts.append(
        f"<div style='font-size:11px;color:{T.P['ink3']};margin-top:8px;'>"
        f"✎ 答案跟你的理解有出入？点右上「<b>标注</b>」写一份你自己的，"
        f"保存后优先显示；清空则恢复自动生成。</div>")

    return "".join(parts), {
        "conf": conf, "source": "auto", "n_points": len(pts),
        "n_params": len(params),
    }


def _sec(text):
    return (f"<div style='font-size:11px;font-weight:700;color:{T.P['ink3']};"
            f"margin:12px 0 5px 0;'>{_esc(text)}</div>")


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


# ================================================================ 浮层面板

class ConditionVisionWorker(QThread):
    """在后台只把原始题卡图片交给视觉模型解题。"""

    progress = Signal(int, str)
    streamed = Signal(int, str)
    completed = Signal(int, object, object, str)

    def __init__(self, qid, image_path):
        super().__init__()
        self.qid = int(qid)
        self.image_path = str(image_path)
        self.source_fields = {}
        self.request_control = VA.RequestControl()

    def cancel(self):
        self.request_control.cancel()

    def run(self):
        try:
            if self.request_control.cancelled:
                self.completed.emit(
                    self.qid,
                    {"provider": "api", "status": "cancelled",
                     "reason": "用户已取消解题请求"}, self.source_fields, "")
                return
            result = VA.solve_question(
                self.image_path,
                progress_callback=lambda text: self.progress.emit(self.qid, text),
                stream_callback=lambda text: self.streamed.emit(self.qid, text)
                if text else None,
                request_control=self.request_control)
            self.completed.emit(self.qid, result, self.source_fields, "")
        except Exception as exc:
            if self.request_control.cancelled:
                self.completed.emit(
                    self.qid,
                    {"provider": "api", "status": "cancelled",
                     "reason": "用户已取消解题请求"}, self.source_fields, "")
                return
            self.completed.emit(self.qid, None, self.source_fields,
                                str(exc)[:400])


class BillingSnapshotWorker(QThread):
    completed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.request_control = VA.RequestControl()

    def cancel(self):
        self.request_control.cancel()

    def run(self):
        try:
            self.completed.emit(VA.account_billing_snapshot(
                request_control=self.request_control))
        except Exception:
            self.completed.emit({"status": "error",
                                 "message": "服务商费用或余额暂时无法查询。"})


class QuestionVersionDialog(QDialog):
    """浏览并恢复题卡快照。"""

    def __init__(self, qid, versions, scale=1.0, parent=None):
        super().__init__(parent)
        self.qid = int(qid)
        self.versions = list(versions or [])
        self.scale = max(0.75, float(scale or 1.0))
        self.selected_version = None
        self.setWindowTitle("题目版本 · #%s" % self.qid)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.resize(int(680 * self.scale), int(520 * self.scale))
        self.setMinimumSize(int(560 * self.scale), int(420 * self.scale))
        self.setStyleSheet(
            f"QDialog{{background:{T.P['panel_bg']};color:{T.P['ink']};}}"
            f"QLabel{{color:{T.P['ink']};background:transparent;}}"
            f"QListWidget,QTextBrowser{{background:{T.P['panel_soft']};"
            f"color:{T.P['ink']};border:1px solid {T.P['glass_soft_edge']};"
            f"border-radius:10px;padding:5px;}}"
            f"QListWidget::item{{padding:8px;border-radius:7px;}}"
            f"QListWidget::item:selected{{background:{T.C_BLUE};color:#ffffff;}}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(int(16 * self.scale), int(14 * self.scale),
                                  int(16 * self.scale), int(14 * self.scale))
        layout.setSpacing(int(9 * self.scale))
        title = QLabel("题目版本")
        title.setStyleSheet("font-size:%dpx;font-weight:800;" %
                            max(14, int(17 * self.scale)))
        layout.addWidget(title)
        subtitle = QLabel("按时间查看题面、题图与参考答案；恢复旧版会另存一条新版本。")
        subtitle.setStyleSheet(f"font-size:11px;color:{T.P['ink3']};")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        content = QHBoxLayout()
        content.setSpacing(int(10 * self.scale))
        self.list = QListWidget()
        self.list.setMinimumWidth(int(190 * self.scale))
        self.list.setWordWrap(True)
        self.list.setSpacing(3)
        for row in self.versions:
            timestamp = str(row.get("created_at") or "").replace("T", " ")[:16]
            item = QListWidgetItem("v%d  ·  %s\n%s" % (
                int(row.get("version", 0)), timestamp,
                str(row.get("reason") or "题卡更新")))
            item.setData(Qt.UserRole, int(row.get("version", 0)))
            self.list.addItem(item)
        content.addWidget(self.list, 2)

        detail_column = QVBoxLayout()
        detail_column.setSpacing(int(7 * self.scale))
        self.preview = QLabel("此版本未保存题图")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedHeight(int(126 * self.scale))
        self.preview.setStyleSheet(
            f"background:{T.P['panel_bg']};border:1px solid {T.P['glass_soft_edge']};"
            f"border-radius:9px;color:{T.P['ink3']};font-size:11px;")
        detail_column.addWidget(self.preview)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        self.detail.setStyleSheet(
            f"QTextBrowser{{font-size:12px;line-height:1.55;"
            f"color:{T.P['ink']};padding:9px;}}")
        detail_column.addWidget(self.detail, 1)
        content.addLayout(detail_column, 5)
        layout.addLayout(content, 1)

        footer = QLabel("恢复只覆盖题卡内容和参考答案，不会清除练习记录、笔记或做题进度。")
        footer.setWordWrap(True)
        footer.setStyleSheet(f"font-size:10px;color:{T.P['ink3']};")
        layout.addWidget(footer)
        actions = QHBoxLayout()
        actions.addStretch()
        self.restore_button = QPushButton("恢复此版本")
        self.restore_button.setCursor(Qt.PointingHandCursor)
        self.restore_button.setMinimumHeight(int(34 * self.scale))
        self.restore_button.setStyleSheet(btn_style(
            "primary", radius=9, scale=self.scale, font_size=12))
        self.restore_button.clicked.connect(self._restore_selected)
        actions.addWidget(self.restore_button)
        close_button = QPushButton("关闭")
        close_button.setCursor(Qt.PointingHandCursor)
        close_button.setMinimumHeight(int(34 * self.scale))
        close_button.setStyleSheet(btn_style(
            "default", radius=9, scale=self.scale, font_size=12))
        close_button.clicked.connect(self.reject)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        self.list.currentRowChanged.connect(self._show_selected)
        self.restore_button.setEnabled(bool(self.versions))
        if self.versions:
            self.list.setCurrentRow(0)
        else:
            self.detail.setHtml("<p>暂无历史版本。</p>")

    @staticmethod
    def _api_answer(question):
        source = (question.get("reference_source") or
                  question.get("_reference_source") or {})
        if not isinstance(source, dict):
            return ""
        assist = source.get("condition_assist") or {}
        answer = ((assist.get("direct_answer") if isinstance(assist, dict) else None)
                  or source.get("direct_answer") or {})
        if isinstance(answer, dict):
            return str(answer.get("answer_markdown") or
                       answer.get("answer_text") or answer.get("content") or "")
        return str(answer or "")

    def _show_selected(self, index):
        if not 0 <= index < len(self.versions):
            return
        row = self.versions[index]
        question = row.get("question") or {}
        image_path = QV.image_path_of(self.qid, row)
        pixmap = QPixmap(image_path) if image_path else QPixmap()
        if not pixmap.isNull():
            pixmap = pixmap.scaled(
                max(100, self.preview.width() - int(16 * self.scale)),
                max(80, self.preview.height() - int(12 * self.scale)),
                Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.preview.setPixmap(pixmap)
            self.preview.setText("")
        else:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("此版本未保存题图")

        labels = (
            ("来源", question.get("source")),
            ("场次", question.get("session") or question.get("exam")),
            ("地区", question.get("region")),
            ("图形", question.get("graph_hint")),
            ("题目条件", question.get("conditions_text")),
            ("航线要求", question.get("requirements_text")),
            ("建议参数", question.get("suggested_text")),
        )
        chunks = [
            "<div style='font-size:11px;color:%s;'>v%d · %s<br/>%s</div>" % (
                T.P["ink3"], int(row.get("version", 0)),
                _html_escape(str(row.get("created_at") or "").replace("T", " ")[:19]),
                _html_escape(str(row.get("reason") or "题卡更新"))),
            "<h3>题卡内容</h3>",
        ]
        for label, value in labels:
            value = str(value or "").strip()
            if value:
                chunks.append("<p><b>%s：</b>%s</p>" % (
                    _html_escape(label), _html_escape(value).replace("\n", "<br/>")))
        manual = str(row.get("manual_answer") or "").strip()
        answer = manual or self._api_answer(question)
        answer_title = "手动参考答案" if manual else "API 参考答案"
        chunks.append("<h3>%s</h3>" % answer_title)
        chunks.append("<pre style='white-space:pre-wrap;word-break:break-word;'>%s</pre>" %
                      _html_escape(answer or "此版本尚未保存参考答案。"))
        self.detail.setHtml("".join(chunks))

    def _restore_selected(self):
        item = self.list.currentItem()
        if item is None:
            return
        version = int(item.data(Qt.UserRole))
        answer = QMessageBox.question(
            self, "恢复题目版本",
            "恢复 v%d 会替换当前题面、题图与参考答案，并自动备份当前状态。\n\n是否继续？" % version,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        success, message = QV.restore_version(self.qid, version)
        if not success:
            QMessageBox.warning(self, "恢复失败", message)
            return
        self.selected_version = version
        self.accept()


@motion_page
class RefAnswerPanel(QFrame):
    """参考答案浮层。挂在题卡工作台里，点按钮才弹出来。

    ★ 为什么用浮层而不是侧栏：常驻侧栏等于把答案摊在屏幕上，
      违背"避免还没做就已经知道答案了"。
    """

    closed = Signal()
    DISCLAIMER = ("本软件本身完全免费，不收取任何费用。使用 API 解题时产生的费用由 API 服务商收取，"
                  "与本软件作者无关。请自行确认 API 服务商的计费规则并承担相应费用。")

    W = 500

    def __init__(self, parent=None, scale=1.0, on_save=None, on_assist=None,
                 on_settings=None, on_restore=None):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setWindowTitle("参考答案")
        self._scale = scale
        self._q = None
        self._drag = None
        self._editing = False
        self._on_save = on_save
        self._on_assist = on_assist
        self._on_settings = on_settings
        self._on_restore = on_restore
        self._vision_thread = None
        self._vision_started_at = 0.0
        self._vision_last_status = ""
        self._vision_health_timer = QTimer(self)
        self._vision_health_timer.setInterval(1000)
        self._vision_health_timer.timeout.connect(self._update_vision_health)
        self._billing_worker = None
        self._billing_snapshot = None
        self._billing_updated_at = 0.0
        self._billing_config_stamp = ""
        self._last_answer_billing_at = 0.0
        self._answer_markdown = ""
        self._answer_expanded = True
        self._pending_markdown = None
        self._answer_render_generation = 0
        self._api_renderer_generation = None
        self._renderer_loaded = False
        self._renderer_loading = False
        self._panel_animation = None
        self._panel_target_geometry = None
        self._panel_is_hiding = False
        self._user_resized = False
        self._adjusting_content_size = False
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumSize(int(440 * scale), int(340 * scale))
        self.resize(int(self.W * scale), int(620 * scale))
        self._resize_handles = create_corner_resize_handles(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(0)
        self.card = glass("GlassStrong", radius=16)
        attach_shadow(self.card, blur=44, dy=10, alpha=110)
        outer.addWidget(self.card)

        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        # ---- 头（可拖拽）----
        self.head = QWidget()
        self.head.setFixedHeight(int(48 * scale))
        self.head.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(14, 0, 8, 0)
        hl.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(Icons.key(T.C_BLUE, 17))
        ic.setStyleSheet("background:transparent;")
        ic.setAttribute(Qt.WA_TransparentForMouseEvents)
        hl.addWidget(ic)
        self.cap = QLabel("参考答案")
        self.cap.setStyleSheet(
            f"font-size:13px;font-weight:700;color:{T.P['ink']};"
            f"background:transparent;")
        self.cap.setAttribute(Qt.WA_TransparentForMouseEvents)
        hl.addWidget(self.cap)
        hl.addStretch()
        self.b_vision = IconButton("解题", None, "primary", font_size=11,
                                   radius=8, scale=scale)
        self.b_vision.setFixedHeight(int(28 * scale))
        self.b_vision.setMinimumWidth(int(54 * scale))
        self._style_vision_button()
        self.b_vision.setToolTip("把题卡图片交给设置的视觉模型，由模型直接审题、计算并生成参考答案")
        self.b_vision.clicked.connect(lambda _checked=False: self.start_condition_vision(refresh=True))
        hl.addWidget(self.b_vision)
        self.b_versions = IconButton("版本", None, "default", font_size=11,
                                     radius=9, scale=scale)
        self.b_versions.setFixedHeight(int(28 * scale))
        self.b_versions.setMinimumWidth(int(52 * scale))
        self._style_header_action(self.b_versions, font_size=11)
        self.b_versions.setToolTip("查看、对比并恢复这道题的历史版本")
        self.b_versions.clicked.connect(self.open_versions)
        hl.addWidget(self.b_versions)
        self.billing_badge = QLabel("费用与余额读取中", self.card)
        self.billing_badge.setFixedHeight(int(20 * scale))
        self.billing_badge.setContentsMargins(12, 0, 12, 0)
        self.billing_badge.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.billing_badge.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.billing_badge.setToolTip("API 费用和余额状态")
        self.billing_badge.hide()
        self.b_edit = IconButton("人工核验", None, "default", font_size=12,
                                 radius=8, scale=scale)
        self.b_edit.setFixedHeight(int(28 * scale))
        self.b_edit.setMinimumWidth(int(72 * scale))
        self._style_header_action(self.b_edit, font_size=12)
        self.b_edit.setToolTip("核对作答与参考答案一致后，点击保存人工核验状态。")
        self.b_edit.clicked.connect(self._on_answer_action)
        hl.addWidget(self.b_edit)
        # ★ v1.9.9：关闭按钮从一个小小的「×」改成明确的文字按钮 ——
        #   五三反馈"参考答案窗口没有关闭按钮"，其实是 × 太不显眼（等于没有）。
        self.b_close = QPushButton("关闭")
        self.b_close.setFixedHeight(int(26 * scale))
        self.b_close.setMinimumWidth(int(56 * scale))
        self.b_close.setCursor(Qt.PointingHandCursor)
        self.b_close.setToolTip("关闭（切题时也会自动跟着换内容）")
        self.b_close.clicked.connect(self.hide)
        hl.addWidget(self.b_close)
        self._style_close()
        self.head.mousePressEvent = self._p_press
        self.head.mouseMoveEvent = self._p_move
        self.head.mouseReleaseEvent = self._p_release
        cl.addWidget(self.head)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{T.P['glass_soft_edge']};")
        cl.addWidget(sep)
        cl.addWidget(self.billing_badge)

        self.vision_status = QLabel("")
        self.vision_status.setWordWrap(True)
        self.vision_status.setStyleSheet(
            f"font-size:11px;color:{T.P['ink3']};background:transparent;")
        self.vision_progress = QProgressBar()
        self.vision_progress.setTextVisible(False)
        self.vision_progress.setFixedHeight(max(4, int(5 * scale)))
        self.vision_progress.setRange(0, 100)
        self.vision_progress.setValue(0)
        self.vision_progress.setStyleSheet(
            f"QProgressBar{{border:0;background:{T.P['glass_soft_edge']};"
            f"border-radius:{max(2, int(3 * scale))}px;}}"
            f"QProgressBar::chunk{{background:{T.C_BLUE};border-radius:"
            f"{max(2, int(3 * scale))}px;}}")
        self.vision_wrap = QWidget()
        self.vision_wrap.setStyleSheet("background:transparent;")
        vision_layout = QVBoxLayout(self.vision_wrap)
        vision_layout.setContentsMargins(12, 5, 12, 5)
        vision_layout.setSpacing(4)
        vision_layout.addWidget(self.vision_status)
        vision_layout.addWidget(self.vision_progress)
        self.vision_wrap.hide()
        cl.addWidget(self.vision_wrap)

        # ---- 正文：航线图（v1.9.9 新增）+ 文字答案 ----
        # 图固定高度常驻上方（不随文字滚 —— "这题答案长什么样"应当一眼可见），
        # 推不出坐标链的题图会自动隐藏，只剩文字。
        self.map = RouteMap(scale=scale)
        outer_map = QVBoxLayout()
        outer_map.setContentsMargins(10, 8, 10, 0)
        outer_map.addWidget(self.map)
        cl.addLayout(outer_map)

        self.answer_tools = QWidget()
        self.answer_tools.setStyleSheet("background:transparent;")
        tools_layout = QHBoxLayout(self.answer_tools)
        tools_layout.setContentsMargins(12, 4, 12, 0)
        tools_layout.setSpacing(6)
        self.answer_info = QLabel("")
        self.answer_info.setWordWrap(True)
        self.answer_info.setStyleSheet(
            f"font-size:10px;color:{T.P['ink3']};background:transparent;")
        tools_layout.addWidget(self.answer_info, 1)
        self.b_copy = IconButton("复制", None, "bare", font_size=10,
                                 radius=7, scale=scale)
        self.b_copy.setFixedHeight(int(25 * scale))
        self.b_copy.clicked.connect(self.copy_answer)
        tools_layout.addWidget(self.b_copy)
        self.b_fold = IconButton("收起", None, "bare", font_size=10,
                                 radius=7, scale=scale)
        self.b_fold.setFixedHeight(int(25 * scale))
        self.b_fold.clicked.connect(self.toggle_answer_expanded)
        tools_layout.addWidget(self.b_fold)
        self.answer_tools.hide()
        cl.addWidget(self.answer_tools)

        self.answer_stack = QStackedWidget()
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setStyleSheet(
            f"QTextBrowser{{background:transparent;border:none;"
            f"color:{T.P['ink']};padding:10px 14px;}}")
        self.answer_stack.addWidget(self.view)
        self.web_answer = QWebEngineView()
        self.web_answer.setMinimumHeight(int(190 * scale))
        self.web_answer.setStyleSheet("background:transparent;border:none;")
        self.web_answer.page().setBackgroundColor(Qt.transparent)
        web_settings = self.web_answer.settings()
        # 正文页面需要加载随包提供的 Markdown、KaTeX 和净化器脚本；外来正文仍由页面 CSP 与 DOMPurify 限制。
        web_settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        web_settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        self.web_answer.loadFinished.connect(self._answer_renderer_ready)
        self.answer_stack.addWidget(self.web_answer)
        cl.addWidget(self.answer_stack, 1)

        # ---- 编辑区（默认藏）----
        self.edit_wrap = QWidget()
        self.edit_wrap.setStyleSheet("background:transparent;")
        el = QVBoxLayout(self.edit_wrap)
        el.setContentsMargins(14, 4, 14, 2)
        el.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(
            "自动答案不对？在这里写你自己的，回车保存；留空恢复自动生成")
        self.edit.setMinimumHeight(int(34 * scale))
        self.edit.returnPressed.connect(self.save_answer)
        el.addWidget(self.edit)
        erow = QHBoxLayout()
        erow.setSpacing(6)
        erow.addStretch()
        self.b_cancel = IconButton("取消", None, "bare", font_size=12,
                                   radius=8, scale=scale)
        self.b_cancel.setFixedHeight(int(28 * scale))
        self.b_cancel.clicked.connect(lambda: self.toggle_edit(False))
        self.b_save = IconButton("保存", "check", "primary", font_size=12,
                                 radius=8, scale=scale)
        self.b_save.setFixedHeight(int(28 * scale))
        self.b_save.clicked.connect(self.save_answer)
        erow.addWidget(self.b_cancel)
        erow.addWidget(self.b_save)
        el.addLayout(erow)
        cl.addWidget(self.edit_wrap)
        self.edit_wrap.hide()

        self._style_close()

    def _condition_assist_html(self, assist, reference=None):
        if not isinstance(assist, dict) or not assist:
            return ""
        parts = [
            f"<div style='font-size:12px;font-weight:700;color:{T.C_BLUE};"
            "margin-top:12px;'>视觉识别条件（请对照原图核对）</div>"]
        diagram = assist.get("diagram_model") or {}
        if isinstance(diagram, dict) and diagram.get("shape"):
            point_count = len(diagram.get("nodes") or [])
            source_note = ("本地图形识别" if diagram.get("source") == "local_cv"
                           else "视觉模型识别")
            parts.append(
                f"<div style='color:{T.P['ink3']};margin-top:5px;'>"
                f"{source_note}：{_esc(diagram.get('shape'))}，"
                f"识别到 {point_count} 个航点。此项只确认图形结构，不代表题目条件已识别完整。</div>")
        experience_count = int(assist.get("experience_count") or 0)
        if experience_count:
            parts.append(
                f"<div style='color:{T.P['ink3']};margin-top:4px;'>"
                f"本次参考了 {experience_count} 条相似历史题型经验；仅借鉴审题方式，不复用其中答案。</div>")
        if isinstance(reference, dict) and reference.get("candidate"):
            parts.append(
                f"<div style='color:{T.P['warn_fg']};margin-top:5px;'>"
                "已绘制候选路线；已提取的几何和航段条件均通过校验，但视觉识别仍待人工确认，坐标仅供参考。</div>")
        elif isinstance(reference, dict) and reference.get("status") != "READY":
            parts.append(
                f"<div style='color:{T.P['warn_fg']};margin-top:5px;'>"
                "候选路线未通过完整条件校验，因此暂不绘制；已识别内容仍可供核对。</div>")
            validation_errors = reference.get("candidate_validation_errors") or []
            if validation_errors:
                parts.append(
                    f"<div style='color:{T.P['warn_fg']};margin-top:4px;'>"
                    "待解决：<br/>" + "<br/>".join(
                        _esc(item) for item in validation_errors[:8]) + "</div>")
        items = assist.get("condition_items") or []
        if items:
            parts.append("<ol style='margin-top:4px;'>")
            for item in items[:40]:
                if not isinstance(item, dict):
                    continue
                normalized = _esc(item.get("normalized", ""))
                evidence = _esc(item.get("visual_evidence", ""))
                try:
                    confidence = max(0.0, min(1.0, float(item.get("confidence", 0))))
                except (TypeError, ValueError):
                    confidence = 0.0
                confidence_color = (T.P["warn_fg"] if confidence < 0.72
                                    else T.P["ink3"])
                parts.append(
                    f"<li style='margin-bottom:5px;'>{normalized}"
                    f"<br/><span style='font-size:11px;color:{T.P['ink3']};'>"
                    f"图中文字：{evidence}</span> "
                    f"<span style='font-size:10px;color:{confidence_color};'>"
                    f"识别可信度约 {round(confidence * 100)}%</span></li>")
            parts.append("</ol>")
            if len(items) > 40:
                parts.append(f"<div style='color:{T.P['ink3']};'>其余 {len(items) - 40} 条已保存。</div>")
        uncertain = assist.get("uncertain_items") or []
        if uncertain:
            parts.append(
                f"<div style='color:{T.P['warn_fg']};margin-top:6px;'>"
                "以下内容模型无法确认；参考路线不能视为已核验：<br/>"
                + "<br/>".join(_esc(item) for item in uncertain[:20])
                + "</div>")
        if assist.get("status") == "review" and reference and reference.get("candidate"):
            parts.append(
                f"<div style='color:{T.P['warn_fg']};margin-top:6px;'>"
                "识别中存在待确认内容；请逐条对照题卡原文和路线。</div>")
        return "".join(parts)

    def _direct_answer_html(self, answer, assist=None):
        """显示视觉 API 直接给出的推理、航段与参数，不调用本地求解器。"""
        parts = [
            f"<div style='font-size:11px;color:{T.P['warn_fg']};font-weight:700;"
            f"margin-bottom:8px;'>● 视觉 API 直接作答 · 仅供参考，尚未人工核验</div>"]
        model_name = _esc(answer.get("model") or "所选模型")
        provider_name = _esc(answer.get("provider") or "视觉 API")
        parts.append(
            f"<div style='font-size:11px;color:{T.P['ink3']};'>"
            f"解答模型：{provider_name} / {model_name}</div>")
        summary = str(answer.get("summary") or "").strip()
        if summary:
            parts.append(_sec("解题结论"))
            parts.append(
                f"<div style='font-size:13px;color:{T.P['ink']};line-height:1.8;'>"
                f"{_esc(summary).replace(chr(10), '<br/>')}</div>")
        steps = answer.get("steps") or []
        if steps:
            parts.append(_sec("模型推理步骤"))
            parts.append("<ol style='margin-top:3px;'>")
            for step in steps[:24]:
                parts.append(
                    f"<li style='margin-bottom:5px;line-height:1.65;'>"
                    f"{_esc(step).replace(chr(10), '<br/>')}</li>")
            parts.append("</ol>")
        legs = answer.get("flight_legs") or []
        if legs:
            parts.append(_sec("航段参考"))
            for leg in legs[:40]:
                first, second = _esc(leg.get("from", "?")), _esc(leg.get("to", "?"))
                bearing = leg.get("bearing_deg")
                bearing_text = (f"航向 {bearing:.1f}°" if isinstance(bearing, (int, float))
                                else _esc(leg.get("heading", "")))
                distance = leg.get("distance_m")
                distance_text = (f"{distance:g} 米" if isinstance(distance, (int, float))
                                 else _esc(leg.get("distance_text", "")))
                details = "　".join(value for value in (bearing_text, distance_text) if value)
                note = _esc(leg.get("note", ""))
                parts.append(
                    f"<div style='font-size:12px;line-height:1.8;'>"
                    f"<b>{first} → {second}</b>　{details}"
                    + (f"<br/><span style='color:{T.P['ink3']};'>{note}</span>" if note else "")
                    + "</div>")
        coordinates = answer.get("coordinates") or []
        if coordinates:
            parts.append(_sec("坐标与高度参考"))
            for item in coordinates[:32]:
                fields = []
                if item.get("latitude") is not None:
                    fields.append("N " + _esc(item["latitude"]))
                if item.get("longitude") is not None:
                    fields.append("E " + _esc(item["longitude"]))
                if item.get("altitude_m") is not None:
                    fields.append("高度 " + _esc(item["altitude_m"]) + " 米")
                note = _esc(item.get("note", ""))
                parts.append(
                    f"<div style='font-size:12px;line-height:1.75;'>"
                    f"<b>{_esc(item.get('point', '航点'))}</b>　"
                    + "　".join(fields)
                    + (f"<br/><span style='color:{T.P['ink3']};'>{note}</span>" if note else "")
                    + "</div>")
        parameters = answer.get("parameters") or []
        if parameters:
            parts.append(_sec("飞行参数参考"))
            for item in parameters[:40]:
                name = _esc(item.get("name", ""))
                value = _esc(item.get("value", ""))
                parts.append(
                    f"<div style='font-size:12px;line-height:1.75;'>"
                    f"{(name + '：') if name else ''}<b>{value}</b></div>")
        assumptions = answer.get("assumptions") or []
        if assumptions:
            parts.append(_sec("模型假设与待核对项"))
            parts.extend(
                f"<div style='font-size:11px;color:{T.P['warn_fg']};line-height:1.7;'>"
                f"• {_esc(item)}</div>" for item in assumptions[:24])
        if not any((summary, steps, legs, coordinates, parameters, assumptions)):
            parts.append(_sec("文字答案暂缺"))
            parts.append(
                f"<div style='font-size:12px;color:{T.P['warn_fg']};line-height:1.7;'>"
                "模型返回了航图数据，但没有返回可阅读的文字解答。"
                "请重新解题或更换模型；当前航图仅供参考。</div>")
        conditions = (assist or {}).get("condition_items") or []
        if conditions:
            parts.append(_sec("模型识别的题面条件"))
            parts.append("<ol style='margin-top:3px;'>")
            for item in conditions[:40]:
                text = _esc(item.get("normalized", ""))
                evidence = _esc(item.get("visual_evidence", ""))
                parts.append(
                    f"<li style='margin-bottom:4px;'>{text}"
                    + (f"<br/><span style='font-size:11px;color:{T.P['ink3']};'>"
                       f"原图依据：{evidence}</span>" if evidence else "")
                    + "</li>")
            parts.append("</ol>")
        uncertain = (assist or {}).get("uncertain_items") or []
        if uncertain:
            parts.append(_sec("请对照原图复核"))
            parts.extend(
                f"<div style='font-size:11px;color:{T.P['warn_fg']};line-height:1.7;'>"
                f"• {_esc(item)}</div>" for item in uncertain[:24])
        billing = self._billing_text(answer)
        if billing:
            parts.append(_sec("本次调用信息"))
            parts.append(
                f"<div style='font-size:11px;color:{T.P['ink3']};line-height:1.7;'>"
                f"{_esc(billing)}<br/>{_esc(self.DISCLAIMER)}</div>")
        return "".join(parts)

    @staticmethod
    def _billing_text(answer):
        pieces = []
        elapsed = answer.get("elapsed_seconds")
        if isinstance(elapsed, (int, float)):
            pieces.append("耗时 %.1f 秒" % max(0.0, elapsed))
        cost = answer.get("estimated_cost") or {}
        if isinstance(cost, dict) and isinstance(cost.get("amount"), (int, float)):
            pieces.append("本次花费估算 %s %.6f" % (
                cost.get("currency", "USD"), max(0.0, cost["amount"])))
        else:
            pieces.append("本次费用暂无法估算（请核对单价配置）")
        usage = answer.get("usage") or {}
        if isinstance(usage, dict) and usage.get("total_tokens"):
            pieces.append("用量 %s tokens" % int(usage["total_tokens"]))
        balances = answer.get("account_balance") or []
        if balances:
            pieces.append("API 账户余额 " + " / ".join(
                "%s %s" % (row.get("currency", ""), row.get("amount", ""))
                for row in balances))
        elif answer.get("local_remaining_estimate") is not None:
            currency = (cost or {}).get("currency", "USD")
            pieces.append("本地预算剩余估算 %s %.4f（非服务商余额）" % (
                currency, answer["local_remaining_estimate"]))
        elif answer.get("provider") == "DeepSeek":
            pieces.append("暂未能查询 DeepSeek 账户余额")
        else:
            pieces.append("服务商未提供通用余额查询；可在设置填写本地预算估算剩余额")
        if answer.get("local_estimated_spend") is not None and cost:
            pieces.append("本机累计估算 %s %.6f" % (
                cost.get("currency", "USD"), answer["local_estimated_spend"]))
        return "　·　".join(pieces)

    def _billing_meta_html(self, answer):
        text = self._billing_text(answer)
        model = "%s / %s" % (answer.get("provider", "视觉 API"),
                              answer.get("model", ""))
        return ("<div class='result-meta'><b>%s</b><br>%s<br>%s</div>" %
                (_esc(model), _esc(text), _esc(self.DISCLAIMER)))

    def _show_api_markdown(self, answer, fallback_html=None):
        self._answer_markdown = str(answer.get("answer_markdown") or "")
        self._answer_render_generation += 1
        self._api_renderer_generation = self._answer_render_generation
        answer_html = str(answer.get("answer_html") or "")
        has_markdown = bool(self._answer_markdown.strip())
        has_html = bool(answer_html.strip())
        if has_html:
            self.view.setPlainText(answer_html)
        elif has_markdown:
            self.view.setMarkdown(self._answer_markdown)
        else:
            self.view.setHtml(fallback_html or (
                "<p>模型返回了航图，但没有返回可阅读的文字答案。"
                "请重新解题或更换模型；当前航图仅供参考。</p>"))
        info = self._billing_text(answer)
        if answer.get("human_verified"):
            info = (info + " · " if info else "") + "已人工核验"
        self.answer_info.setText(info)
        self.answer_tools.show()
        self.answer_stack.setCurrentWidget(self.view)
        if not has_markdown and not has_html:
            self.answer_stack.setVisible(self._answer_expanded)
            self._pending_markdown = None
            return
        from motion_polish import fold
        fold(self.answer_stack, self._answer_expanded)
        self.b_fold.setText("收起" if self._answer_expanded else "展开")
        self._pending_markdown = (
            self._answer_markdown if has_markdown else "", T.mode == "dark",
            self._billing_meta_html(answer), answer_html if has_html else "")
        if not self._renderer_loaded:
            if not self._renderer_loading:
                self._renderer_loading = True
                self.web_answer.setUrl(QUrl.fromLocalFile(
                    T.asset_path("web", "answer", "index.html")))
        else:
            self._render_pending_markdown()

    def _answer_renderer_ready(self, ok):
        self._renderer_loading = False
        if not ok:
            self._renderer_loaded = False
            self._use_text_answer_fallback(
                self._api_renderer_generation,
                "答案排版组件加载失败，已切换为文本显示。")
            return
        self._renderer_loaded = True
        self._render_pending_markdown()

    def _render_pending_markdown(self):
        if (not self._renderer_loaded or not self._pending_markdown or
                not self._answer_expanded):
            return
        markdown, dark, metadata, html = self._pending_markdown
        self._pending_markdown = None
        script = "window.renderApiAnswer(%s,%s,%s,%s);" % (
            json.dumps(markdown, ensure_ascii=False),
            "true" if dark else "false",
            json.dumps(metadata, ensure_ascii=False),
            json.dumps(html, ensure_ascii=False))
        generation = self._answer_render_generation

        def verify_rendered(_result=None):
            if generation != self._answer_render_generation:
                return
            self.web_answer.page().runJavaScript(
                "(() => { const node = document.getElementById('answer'); "
                "return !!(node && (node.innerText.trim() || "
                "node.querySelector('img,svg,table,pre'))); })()",
                lambda ready: self._activate_api_renderer(generation, ready))

        # 先让 WebEngine 成为当前页并完成一次布局，再更新 DOM；隐藏状态下
        # 先排版会让部分 Windows 图形后端保留旧的合成位置，拖动窗口才重绘。
        self.answer_stack.setVisible(True)
        self.answer_stack.setCurrentWidget(self.web_answer)
        self.web_answer.updateGeometry()
        self.answer_stack.updateGeometry()
        layout = self.answer_stack.layout()
        if layout is not None:
            layout.activate()

        def render_after_layout():
            if generation != self._answer_render_generation:
                return
            self.web_answer.page().runJavaScript(script, verify_rendered)

        QTimer.singleShot(0, render_after_layout)

    def _activate_api_renderer(self, generation, ready):
        if generation != self._answer_render_generation:
            return
        if not ready:
            self._use_text_answer_fallback(
                generation, "答案排版未能完成，已切换为文本显示。")
            return
        if self._answer_expanded:
            self.answer_stack.setCurrentWidget(self.web_answer)

    def _use_text_answer_fallback(self, generation, message):
        """正文网页组件失败时恢复已准备好的 Qt 文本答案。"""
        if (generation is None or generation != self._answer_render_generation or
                generation != self._api_renderer_generation):
            return
        if not self.view.toPlainText().strip():
            text = self._answer_markdown.strip()
            self.view.setPlainText(text or (
                "模型正文暂不可用。请重新解题或更换模型；当前航图仅供参考。"))
        self.answer_stack.setCurrentWidget(self.view)
        self.answer_stack.setVisible(self._answer_expanded)
        info = str(self.answer_info.text() or "").strip()
        if message and message not in info:
            self.answer_info.setText((info + " · " if info else "") + message)
        self.answer_tools.show()

    def copy_answer(self):
        text = self._answer_markdown or self.view.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.answer_info.setText("答案已复制到剪贴板")

    def toggle_answer_expanded(self):
        self._answer_expanded = not self._answer_expanded
        self.answer_stack.setVisible(self._answer_expanded)
        self.b_fold.setText("收起" if self._answer_expanded else "展开")
        if self._answer_expanded and self._pending_markdown:
            self._render_pending_markdown()

    def _set_vision_status(self, text, kind="normal"):
        colors = {
            "normal": T.P["ink3"],
            "ok": T.P["ok_fg"],
            "warn": T.P["warn_fg"],
            "error": T.C_RED,
        }
        self.vision_status.setText(str(text or ""))
        self.vision_status.setStyleSheet(
            f"font-size:11px;color:{colors.get(kind, colors['normal'])};"
            "background:transparent;")

    def start_condition_vision(self, refresh=False):
        if self._vision_thread is not None and self._vision_thread.isRunning():
            self.cancel_condition_vision()
            return
        q = self._q
        if not q or not q.get("imported"):
            self._set_vision_status("视觉识别 API 只用于导入题卡。", "warn")
            self.vision_wrap.show()
            return
        if self._vision_thread is not None:
            return
        cached = self._direct_answer_for_question(q)
        if cached and not refresh:
            self.vision_wrap.show()
            self.vision_progress.setRange(0, 100)
            self.vision_progress.setValue(100)
            self._set_vision_status(
                "已读取与本题绑定的 API 答案，本次没有再次调用或计费。", "ok")
            return
        configured, _url, _key, _model = VA._api_settings()
        if not configured:
            self.vision_wrap.show()
            self._set_vision_status("请先在设置中选择服务商并填写 API 地址、模型和 API Key。", "warn")
            box = QMessageBox(self)
            box.setWindowTitle("尚未配置 API")
            box.setText("请先在设置界面输入 API Key 和模型信息。")
            go = box.addButton("前往设置", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is go and callable(self._on_settings):
                self._on_settings()
            return
        if not cfg.get("vision_api_disclaimer_ack", False):
            box = QMessageBox(self)
            box.setWindowTitle("API 使用与费用说明")
            box.setText(self.DISCLAIMER)
            box.setInformativeText("继续前请确认你已了解服务商的计费规则。")
            agree = box.addButton("同意并继续", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("暂不使用", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not agree:
                return
            cfg.set_("vision_api_disclaimer_ack", True)
        qid = q.get("id")
        try:
            qid = int(qid)
        except (TypeError, ValueError):
            self.vision_wrap.show()
            self._set_vision_status("这道题尚未保存到题库，无法读取题卡图片。", "error")
            return
        image_path = BD.fig_path(qid)
        if not os.path.isfile(image_path):
            self.vision_wrap.show()
            self._set_vision_status("找不到已导入的题卡图片，请重新导入题目。", "error")
            return

        self.vision_wrap.show()
        self.vision_progress.setRange(0, 0)
        self._set_vision_status("正在准备原始题卡图片，随后由视觉模型独立解题…")
        self._answer_markdown = ""
        self._pending_markdown = None
        self.view.clear()
        self.answer_info.clear()
        self.answer_tools.hide()
        self.answer_stack.setCurrentWidget(self.view)
        self.b_vision.setEnabled(True)
        self.b_vision.setText("取消")
        worker = ConditionVisionWorker(qid, image_path)
        self._vision_thread = worker
        self._vision_started_at = time.monotonic()
        self._vision_last_status = "正在连接服务商…"
        self._vision_health_timer.start()
        worker.progress.connect(self._on_vision_progress)
        worker.streamed.connect(self._on_vision_streamed)
        worker.completed.connect(self._on_vision_completed)
        worker.finished.connect(self._on_vision_thread_finished)
        worker.start()

    def _vision_update_cancelled(self):
        worker = self.sender()
        return bool(worker is not None and
                    (worker is not self._vision_thread or worker.request_control.cancelled))

    def _on_vision_progress(self, qid, text):
        if self._vision_update_cancelled():
            return
        if self._q is None or int(self._q.get("id", -1)) != int(qid):
            return
        self.vision_wrap.show()
        self.vision_progress.setRange(0, 0)
        self._vision_last_status = str(text)
        self._set_vision_status(text)

    def _update_vision_health(self):
        worker = self._vision_thread
        if worker is None or not worker.isRunning():
            self._vision_health_timer.stop()
            return
        if not self._q or str(self._q.get("id")) != str(worker.qid):
            return
        control = worker.request_control
        if control.cancelled:
            self._set_vision_status("已请求取消，正在后台回收连接…", "warn")
            return
        elapsed = int(time.monotonic() - self._vision_started_at)
        idle = int(time.monotonic() - control.last_activity)
        if control.failure_reason:
            self._set_vision_status(control.failure_reason, "error")
        elif control.network_started and idle >= 15:
            self._set_vision_status(
                f"已等待 {elapsed} 秒，最近 {idle} 秒未收到服务商数据；可能网络不稳定或模型仍在处理，可随时取消。", "warn")
        else:
            self._set_vision_status(f"{self._vision_last_status}（已用 {elapsed} 秒）")

    def _on_vision_streamed(self, qid, markdown):
        if self._vision_update_cancelled():
            return
        if (self._q is None or int(self._q.get("id", -1)) != int(qid) or
                not str(markdown or "").strip()):
            return
        self._answer_render_generation += 1
        self._answer_markdown = str(markdown)
        self._pending_markdown = (
            self._answer_markdown, T.mode == "dark",
            "<div class='result-meta'>正在接收并排版模型解答…</div>", "")
        self.answer_tools.show()
        self.answer_info.setText("正在接收解答片段；完整答案返回后会统一排版。")
        self.b_fold.setText("收起" if self._answer_expanded else "展开")
        self.answer_stack.setVisible(self._answer_expanded)
        self.view.setMarkdown(self._answer_markdown)
        self.answer_stack.setCurrentWidget(self.view)
        # 流式阶段不反复启动网页排版，避免和主线程的布局、航图绘制争抢时间。

    def _persist_condition_assist(self, qid, assist, ocr_words=None):
        questions = BD.load()
        found = None
        for question in questions:
            try:
                matches = int(question.get("id", -1)) == int(qid)
            except (TypeError, ValueError):
                matches = False
            if matches:
                found = question
                break
        if found is None:
            return False
        source = dict(found.get("reference_source") or {})
        assist = copy.deepcopy(assist)
        if isinstance(assist.get("direct_answer"), dict):
            direct_answer = assist["direct_answer"]
            direct_answer["question_id"] = int(qid)
            direct_answer["question_signature"] = _question_answer_signature(found)
            direct_answer["human_verified"] = False
            direct_answer.pop("human_verified_at", None)
            assist["direct_answer"] = direct_answer
        source["condition_assist"] = assist
        if isinstance(assist.get("direct_answer"), dict):
            source["direct_answer"] = copy.deepcopy(assist["direct_answer"])
        if ocr_words:
            source["ocr_words"] = list(ocr_words)
        found["reference_source"] = source
        return bool(BD.save(questions))

    @staticmethod
    def _direct_answer_for_question(question):
        """只返回仍绑定到当前题卡内容的 API 答案。"""
        source = (question.get("reference_source") or
                  question.get("_reference_source") or {})
        answer = source.get("direct_answer")
        if not isinstance(answer, dict):
            return None
        saved_qid = answer.get("question_id")
        if saved_qid is not None and str(saved_qid) != str(question.get("id")):
            return None
        saved_signature = answer.get("question_signature")
        if (saved_signature and
                saved_signature != _question_answer_signature(question)):
            return None
        saved_fields = answer.get("source_fields")
        if isinstance(saved_fields, dict) and saved_fields:
            current_fields = {key: str(question.get(key) or "")
                              for key in _ANSWER_QUESTION_FIELDS}
            if saved_fields != current_fields:
                return None
        return answer

    def _persist_human_verification(self, qid, verified):
        """把用户对 API 答案的核验状态保存在对应题卡记录中。"""
        questions = BD.load()
        for question in questions:
            try:
                if int(question.get("id", -1)) != int(qid):
                    continue
            except (TypeError, ValueError, OverflowError):
                continue
            source = dict(question.get("reference_source") or
                          question.get("_reference_source") or {})
            answer = self._direct_answer_for_question(question)
            if not isinstance(answer, dict):
                return False, None
            answer = copy.deepcopy(answer)
            answer["human_verified"] = bool(verified)
            if verified:
                answer["human_verified_at"] = time.time()
            else:
                answer.pop("human_verified_at", None)
            source["direct_answer"] = copy.deepcopy(answer)
            assist = source.get("condition_assist")
            if isinstance(assist, dict):
                assist = copy.deepcopy(assist)
                assist["direct_answer"] = copy.deepcopy(answer)
                source["condition_assist"] = assist
            question["reference_source"] = source
            if BD.save(questions):
                return True, question
            return False, None
        return False, None

    def _on_answer_action(self):
        """API 答案走人工核验；其他题型保留手动录入答案入口。"""
        question = self._q
        if not isinstance(question, dict):
            return
        qid = question.get("id")
        api_answer = (self._direct_answer_for_question(question)
                      if question.get("imported") and
                      not QM.answer_of(qid) else None)
        if api_answer:
            verified = not bool(api_answer.get("human_verified"))
            saved, updated_question = self._persist_human_verification(
                qid, verified)
            if not saved or updated_question is None:
                self.vision_wrap.show()
                self._set_vision_status("人工核验状态保存失败，请检查题库目录是否可写。",
                                        "error")
                return
            self.set_question(updated_question, keep_pos=True)
            self.vision_wrap.show()
            self._set_vision_status(
                "已确认作答与参考答案一致，核验状态已绑定并保存到本题。"
                if verified else "已撤销本题的人工核验状态。", "ok")
            return
        self.toggle_edit()

    def _on_vision_completed(self, qid, result, source_fields, error):
        if self._vision_update_cancelled():
            result = {"status": "cancelled"}
        if isinstance(result, dict) and result.get("status") == "cancelled":
            if self._q is not None and int(self._q.get("id", -1)) == int(qid):
                self.vision_wrap.show()
                self._pending_markdown = None
                self._answer_render_generation += 1
                self._set_vision_status("已取消解题请求；没有保存未完成的答案。", "warn")
                self.vision_progress.setRange(0, 100)
                self.vision_progress.setValue(0)
            return
        if error or not isinstance(result, dict):
            result = {"status": "unavailable",
                      "reason": error or "视觉 API 未返回可读取的答案。"}
        status = result.get("status")
        answer = result.get("direct_answer")
        if isinstance(answer, dict) and status in ("ready", "review"):
            assist = copy.deepcopy(result)
            assist.pop("_refreshed_ocr_words", None)
            assist.pop("_ocr_warning", None)
            assist.pop("source_fields", None)
            assist["trigger"] = "reference_answer_button"
            assist["direct_answer"].pop("source_fields", None)
            try:
                persisted = bool(self._persist_condition_assist(qid, assist))
            except Exception:
                log.exception("参考答案 #%s：答案持久化失败", qid)
                persisted = False
            try:
                is_current = (self._q is not None and
                              int(self._q.get("id", -1)) == int(qid))
            except (TypeError, ValueError):
                is_current = False
            if is_current:
                # 先在当前题卡上展示 API 返回值；本地写入失败不能吞掉已生成的答案。
                source = dict(self._q.get("reference_source") or {})
                source["condition_assist"] = assist
                source["direct_answer"] = copy.deepcopy(assist["direct_answer"])
                self._q["reference_source"] = source
                self.set_question(self._q, keep_pos=True)
                self._set_billing_from_answer(answer)
                self._refresh_billing_badge(force=True, keep_current=True)
                leg_count = len(answer.get("flight_legs") or [])
                uncertain_count = len(assist.get("uncertain_items") or [])
                message = (f"{answer.get('model') or '视觉 API'} 已直接生成参考解答，"
                           f"包含 {leg_count} 个航段；答案尚未人工核验。")
                if VA.direct_answer_reference(answer) is None:
                    message += "本次未返回可绘图的数值航段或坐标，目前仅有文字答案。"
                if uncertain_count:
                    message += f"另有 {uncertain_count} 项请对照原题确认。"
                coverage = assist.get("condition_coverage") or {}
                source_count = int(coverage.get("source_count", 0) or 0)
                returned_count = int(coverage.get("returned_count", 0) or 0)
                if source_count and returned_count < source_count:
                    message += (f"题面有 {source_count} 条编号条件，模型只逐条核对了 "
                                f"{returned_count} 条；航线目前只能作为草案。")
                if not persisted:
                    message = ("模型已返回参考答案，但本地保存失败；答案仍显示在当前窗口。"
                               "检查题库目录可写后，可重新解题保存。")
                self._set_vision_status(message, "warn")
                self.vision_progress.setRange(0, 100)
                self.vision_progress.setValue(100)
                self.vision_wrap.show()
                if persisted and callable(self._on_assist):
                    try:
                        self._on_assist(int(qid), status, "API_DIRECT_ANSWER")
                    except Exception:
                        pass
                return
        if status == "save_failed":
            message, kind = "模型已返回参考答案，但本地保存失败。", "warn"
        else:
            message = str(result.get("reason") or "模型没有返回可展示的直接解答。")
            kind = "error"
        if self._q is not None and int(self._q.get("id", -1)) == int(qid):
            self.vision_wrap.show()
            self._set_vision_status(message, kind)
            self.vision_progress.setRange(0, 100)
            self.vision_progress.setValue(0)
            partial = str(self._answer_markdown or "").strip()
            if partial:
                self.view.setMarkdown(
                    "**模型输出中断：以下是已接收片段，尚未校验，也未保存。**\n\n" +
                    partial)
                self.answer_info.setText(
                    "仅显示已接收的部分内容；请重试后再使用完整航线和坐标。")
                self.answer_tools.show()
                self.answer_stack.setCurrentWidget(self.view)
                self.answer_stack.setVisible(self._answer_expanded)
                self.b_fold.setText("收起" if self._answer_expanded else "展开")

    def _on_vision_thread_finished(self):
        self._vision_health_timer.stop()
        worker = self.sender()
        if worker is self._vision_thread:
            self._vision_thread = None
        question = self._q if isinstance(self._q, dict) else None
        imported = bool(question and question.get("imported"))
        answer = (self._direct_answer_for_question(question)
                  if imported else None)
        if answer:
            self.b_vision.setText("已缓存")
            self.b_vision.setEnabled(False)
            self.b_vision.setToolTip("本题 API 答案已与题卡绑定并保存，打开时直接读取，不会重复调用。")
        else:
            self.b_vision.setText("重试解题")
            self.b_vision.setEnabled(imported)
            self.b_vision.setToolTip("恢复网络后重新发起解题；重试可能产生服务商费用。")
        if worker is not None:
            worker.deleteLater()

    def _set_billing_badge(self, text, tooltip="", color=None):
        label = self.billing_badge
        label.setStyleSheet(
            "font-size:9px;font-weight:600;color:%s;background:transparent;" %
            (color or T.P["ink3"]))
        label.setText(str(text or "费用状态未知"))
        label.setToolTip(str(tooltip or text or "费用状态未知"))

    @staticmethod
    def _compact_money(amount, currency, approximate=False, decimals=3):
        try:
            value = float(amount)
        except (TypeError, ValueError, OverflowError):
            return str(amount or "")
        if not math.isfinite(value):
            return ""
        code = str(currency or "").upper()
        symbols = {"USD": "$", "CNY": "¥", "RMB": "¥", "EUR": "€",
                   "GBP": "£", "JPY": "¥", "KRW": "₩"}
        if decimals == 2:
            number = "%.2f" % value
        else:
            number = "%.3g" % value
        prefix = "≈" if approximate else ""
        if code in symbols:
            return prefix + symbols[code] + number
        return prefix + number + (" " + code if code else "")

    @staticmethod
    def _billing_snapshot_text(snapshot):
        if not isinstance(snapshot, dict):
            return "费用/余额查询中…", "正在向当前 API 服务商查询费用信息。"
        status = snapshot.get("status")
        if status == "unconfigured":
            return "API 未配置", snapshot.get("message") or "请先在设置中配置视觉 API。"
        balances = snapshot.get("balances") or []
        if balances:
            rows = ["%s %s" % (row.get("currency", ""), row.get("amount", ""))
                    for row in balances if isinstance(row, dict)]
            first = next((row for row in balances if isinstance(row, dict)), {})
            compact = RefAnswerPanel._compact_money(
                first.get("amount"), first.get("currency"), decimals=2)
            detail = "服务商账户余额：" + "；".join(rows)
            return "余额" + compact, detail
        month_spend = snapshot.get("month_spend") or []
        if month_spend:
            rows = ["本月 %s %s" % (row.get("currency", ""), row.get("amount", ""))
                    for row in month_spend if isinstance(row, dict)]
            return "本月费用已查", ("；".join(rows) + "；" +
                                  str(snapshot.get("message", "")))
        totals = snapshot.get("local_estimated_spend") or {}
        if totals:
            currency, amount = next(iter(sorted(totals.items())))
            return "累计" + RefAnswerPanel._compact_money(
                amount, currency, approximate=True), (
                (snapshot.get("message") or "") +
                " 本机根据已记录的 token 和单价估算，非服务商账单。")
        return "余额暂不可查", snapshot.get("message") or "服务商未开放通用余额查询。"

    def _set_billing_from_answer(self, answer):
        answer = answer if isinstance(answer, dict) else {}
        cost = answer.get("estimated_cost") or {}
        balances = answer.get("account_balance") or []
        pieces = []
        if isinstance(cost, dict) and isinstance(cost.get("amount"), (int, float)):
            pieces.append("本次" + self._compact_money(
                max(0.0, cost["amount"]), cost.get("currency", "USD"),
                approximate=True))
        else:
            pieces.append("本次费用待估")
        first_balance = next((row for row in balances if isinstance(row, dict)), None)
        if first_balance:
            pieces.append("余" + self._compact_money(
                first_balance.get("amount"), first_balance.get("currency"),
                decimals=2))
        elif answer.get("local_remaining_estimate") is not None:
            currency = (cost or {}).get("currency", "USD")
            pieces.append("余估" + self._compact_money(
                answer["local_remaining_estimate"], currency, decimals=2))
        else:
            pieces.append("余额不可查")
        self._last_answer_billing_at = time.monotonic()
        tooltip = self._billing_text(answer)
        _, snapshot_message = self._billing_snapshot_text(self._billing_snapshot)
        if snapshot_message:
            tooltip = (tooltip + "\n" if tooltip else "") + snapshot_message
        color = T.P["ok_fg"] if first_balance else T.P["ink3"]
        self._set_billing_badge(" · ".join(pieces), tooltip, color)

    def _refresh_billing_badge(self, force=False, keep_current=False):
        configured, base_url, api_key, model = VA._api_settings()
        if not configured:
            self._set_billing_badge("API 未配置", "请先到设置填写视觉 API 地址、模型和 Key。",
                                     T.P["warn_fg"])
            return
        stamp = hashlib.sha256((base_url + "\0" + model + "\0" + api_key).encode(
            "utf-8", errors="replace")).hexdigest()[:16]
        fresh = (stamp == self._billing_config_stamp and
                 time.monotonic() - self._billing_updated_at < 60)
        if not force and (fresh or time.monotonic() - self._last_answer_billing_at < 60):
            return
        if self._billing_worker is not None and self._billing_worker.isRunning():
            return
        self._billing_config_stamp = stamp
        if not keep_current:
            self._set_billing_badge(
                "费用/余额查询中…", "正在向当前 API 服务商查询费用信息。")
        worker = BillingSnapshotWorker(self)
        self._billing_worker = worker
        worker.completed.connect(self._on_billing_snapshot)
        worker.finished.connect(self._on_billing_thread_finished)
        worker.start()

    def _on_billing_snapshot(self, snapshot):
        self._billing_snapshot = snapshot if isinstance(snapshot, dict) else {}
        self._billing_updated_at = time.monotonic()
        if isinstance(self._q, dict) and self._q.get("imported"):
            answer = self._direct_answer_for_question(self._q)
            if answer and (answer.get("estimated_cost") or
                           answer.get("account_balance")):
                if self._billing_snapshot.get("balances"):
                    answer = dict(answer)
                    answer["account_balance"] = self._billing_snapshot["balances"]
                self._set_billing_from_answer(answer)
                return
        text, tooltip = self._billing_snapshot_text(self._billing_snapshot)
        if self._billing_snapshot.get("balances"):
            text += " · 等待解题"
        elif self._billing_snapshot.get("month_spend"):
            text += " · 余额不可查"
        color = (T.P["warn_fg"] if self._billing_snapshot.get("status") in
                 ("error", "unavailable", "unsupported") else T.P["ink3"])
        self._set_billing_badge(text, tooltip, color)

    def _on_billing_thread_finished(self):
        worker = self.sender()
        if worker is self._billing_worker:
            self._billing_worker = None
        if worker is not None:
            worker.deleteLater()

    def cancel_condition_vision(self):
        worker = self._vision_thread
        if worker is None or not worker.isRunning():
            return
        worker.cancel()
        self.b_vision.setEnabled(False)
        self._set_vision_status("正在关闭 API 请求…", "warn")

    def shutdown_workers(self):
        workers = (self._vision_thread, self._billing_worker)
        for worker in workers:
            if worker is None or not worker.isRunning():
                continue
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                cancel()
            worker.requestInterruption()
        # 由宿主窗口异步轮询收尾，不在界面线程等待网络连接锁。
        return not any(worker is not None and worker.isRunning() for worker in workers)

    # -------------------------------------------------- 主题
    def _style_header_action(self, button, font_size=11, kind="default"):
        """把参考答案标题栏操作统一为清晰的圆角矩形。"""
        radius = max(5, int(9 * self._scale))
        style = btn_style(kind, radius=9, font_size=font_size,
                          scale=self._scale)
        button.setStyleSheet(
            style + f"\nQPushButton{{border-radius:{radius}px;}}")

    def _style_close(self):
        # ★ v1.9.9 换成文字按钮后走 btn_style（跟「标注」一个体系）
        self._style_header_action(self.b_close, font_size=12)

    def _style_vision_button(self):
        """固定主操作的圆角矩形轮廓，避免主题把它压成直角按钮。"""
        self._style_header_action(self.b_vision, font_size=11, kind="primary")

    def refresh_theme(self):
        # ★ v1.9.9 顺手修：玻璃卡片本体也要跟着换主题 —— 之前只刷了文字控件，
        #   切主题后浮层底色停在旧主题（错题集/趋势窗是销毁重建没这问题，
        #   参考答案浮层是常驻对象，必须自己刷）。
        from ui_kit import refresh_glass
        refresh_glass(self.card)
        self.map.update()
        self.map._zoom_button.refresh()
        self.map._artwork_mode.refresh()
        self._style_close()
        self.b_vision.refresh()
        self._style_vision_button()
        self.b_versions.refresh()
        self._style_header_action(self.b_versions, font_size=11)
        self.b_edit.refresh()
        self._style_header_action(self.b_edit, font_size=12)
        self.b_vision.setVisible(bool(self._q and self._q.get("imported")))
        self.b_versions.setVisible(bool(self._q and self._q.get("imported")))
        self.billing_badge.setStyleSheet(
            f"font-size:9px;font-weight:600;color:{T.P['ink3']};background:transparent;")
        self.billing_badge.setVisible(bool(self._q and self._q.get("imported")))
        self.vision_progress.setStyleSheet(
            f"QProgressBar{{border:0;background:{T.P['glass_soft_edge']};"
            f"border-radius:{max(2, int(3 * self._scale))}px;}}"
            f"QProgressBar::chunk{{background:{T.C_BLUE};border-radius:"
            f"{max(2, int(3 * self._scale))}px;}}")
        self.cap.setStyleSheet(
            f"font-size:13px;font-weight:700;color:{T.P['ink']};"
            f"background:transparent;")
        self.view.setStyleSheet(
            f"QTextBrowser{{background:transparent;border:none;"
            f"color:{T.P['ink']};padding:10px 14px;}}")
        if self._q is not None:
            self.set_question(self._q, keep_pos=True)

    # -------------------------------------------------- 数据
    def set_question(self, q, keep_pos=False):
        """换一道题 —— 重新渲染。编辑态会被重置（避免把 A 题的编辑框带进 B 题）。"""
        self._answer_render_generation += 1
        self._api_renderer_generation = None
        self._pending_markdown = None
        old_qid = self._q.get("id") if isinstance(self._q, dict) else None
        new_qid = q.get("id") if isinstance(q, dict) else None
        self._q = q
        self._editing = False
        self.edit_wrap.hide()
        self._answer_markdown = ""
        self._pending_markdown = None
        self.answer_info.clear()
        self.answer_tools.hide()
        self.answer_stack.setCurrentWidget(self.view)
        self.answer_stack.show()
        if old_qid != new_qid:
            worker_qid = getattr(self._vision_thread, "qid", None)
            if worker_qid == new_qid:
                self.vision_wrap.show()
                self.vision_progress.setRange(0, 0)
                self._set_vision_status(f"第 {new_qid} 题的视觉 API 作答仍在后台进行…")
            else:
                self.vision_wrap.hide()
        if q is None:
            self.b_vision.hide()
            self.b_versions.hide()
            self.billing_badge.hide()
            self.vision_wrap.hide()
            self.map.set_solution(None)
            self.view.setHtml(
                f"<div style='color:{T.P['ink3']};font-size:12px;'>"
                f"没有选中题目。</div>")
            return
        qid = q.get("id")
        self.b_vision.setVisible(bool(q.get("imported")))
        manual = QM.answer_of(qid)
        imported = bool(q.get("imported"))
        self.b_versions.setVisible(imported)
        assist = (q.get("reference_source") or q.get("_reference_source") or {}).get(
            "condition_assist")
        api_answer = self._direct_answer_for_question(q) if imported else None
        worker = self._vision_thread
        worker_running = bool(worker is not None and worker.isRunning())
        worker_qid = getattr(worker, "qid", None) if worker_running else None
        if worker_running and str(worker_qid) == str(qid):
            self.b_vision.setText("取消")
            self.b_vision.setEnabled(True)
            self.b_vision.setToolTip("取消本题正在进行的 API 解题请求。")
        elif worker_running:
            self.b_vision.setText("解题中")
            self.b_vision.setEnabled(False)
            self.b_vision.setToolTip("另一道题的 API 解题仍在后台进行。")
        elif api_answer:
            self.b_vision.setText("重新解题")
            self.b_vision.setEnabled(imported)
            self.b_vision.setToolTip(
                "重新把原题交给模型生成原文和原图；按服务商计费，旧答案保留在版本中。")
        else:
            self.b_vision.setText("解题")
            self.b_vision.setEnabled(imported)
            self.b_vision.setToolTip(
                "把题卡图片交给设置的视觉模型，由模型直接审题、计算并生成参考答案。")
        self.billing_badge.setVisible(imported)
        if imported:
            if api_answer and (api_answer.get("estimated_cost") or
                               api_answer.get("account_balance")):
                self._set_billing_from_answer(api_answer)
            else:
                self._refresh_billing_badge()
        else:
            self.billing_badge.hide()
        # 导入题卡直接展示 API 结果；打开浮层不再同步运行本地图形识别。
        reference = None
        if manual:
            self.map.set_notice("这道题使用你手动标注的答案。自动航线图不覆盖手动答案。")
            html, meta = render(q)
        elif imported:
            if api_answer:
                reference = VA.direct_answer_reference(api_answer)
                if reference:
                    try:
                        self.map.set_solution(reference)
                    except (KeyError, TypeError, ValueError, OverflowError):
                        log.exception("参考答案 #%s：API航线图数据格式无效", qid)
                        self.map.set_notice("API 已作答，但航图数据格式不完整；请查看下方文字答案。")
                else:
                    self.map.set_notice("API 已直接作答，但模型未提供足够点位，无法绘制航图。")
                html = self._direct_answer_html(api_answer, assist)
                if api_answer.get("answer_markdown") or api_answer.get("answer_html"):
                    self._show_api_markdown(api_answer, fallback_html=html)
                else:
                    info = self._billing_text(api_answer)
                    if api_answer.get("human_verified"):
                        info = (info + " · " if info else "") + "已人工核验"
                    self.answer_info.setText(info)
                    self.answer_tools.show()
                    self.b_fold.setText("收起" if self._answer_expanded else "展开")
                    self.answer_stack.setVisible(self._answer_expanded)
            else:
                self.map.set_notice(
                    "导入题卡尚无 API 参考答案。点击上方“解题”，由所选视觉模型直接审题并计算。")
                empty_result = {"conf": "none", "points": {}, "params": [],
                                "extras": {}, "unresolved": []}
                html, meta = render(q, derived=empty_result)
                if assist:
                    html += self._condition_assist_html(assist)
                html += (
                    f"<div style='font-size:12px;color:{T.P['ink3']};line-height:1.7;'>"
                    "导入题目的参考答案由你配置的视觉 API 直接生成；本地几何推导不会替它作答。</div>")
        else:
            reference = RE.solve_reference(q)
            # 兼容手动录入的题目；导入题卡统一由视觉 API 直接解题。
            try:
                preset = (reference if reference and reference.get("status") == "READY"
                          else None)
                adapted = RE.preset_reference_for_display(q, preset, reference)
                if adapted:
                    reference = adapted
                    log.info("参考答案 #%s：答案已适配浮层（conf=%s，点数=%d，航线边=%d）",
                             qid, preset.get("conf"), len(adapted.get("points") or {}),
                             len(adapted.get("flight_edges") or []))
            except Exception:
                log.exception("参考答案 #%s：航图数据适配失败，保留求解器结果", qid)
            try:
                self.map.set_solution(reference)
            except (KeyError, TypeError, ValueError, OverflowError):
                log.exception("参考答案 #%s：航线图数据格式无效", qid)
                self.map.set_solution(None)
            html, meta = render(q, reference=reference)
            html += self._condition_assist_html(assist, reference)
        answer_state = ("手动标注" if manual else
                        "视觉 API 直接作答" if imported and api_answer else
                        "等待视觉 API 作答" if imported else
                        (reference or {}).get("status", "未知"))
        log.info("参考答案 #%s：%s", qid, answer_state)
        if not (imported and api_answer and
                (api_answer.get("answer_markdown") or api_answer.get("answer_html"))):
            self.answer_stack.setCurrentWidget(self.view)
            self.view.setHtml(
                f"<div style='font-size:13px;'>{html}</div>")
            self.view.verticalScrollBar().setValue(0)
        self.cap.setText("参考答案 · #%s" % qid)
        if imported and api_answer and not manual:
            verified = bool(api_answer.get("human_verified"))
            self.b_edit.setText("已核验" if verified else "人工核验")
            self.b_edit.setToolTip(
                "此题已确认作答与参考答案一致；再次点击可撤销核验。" if verified else
                "核对作答与参考答案一致后，点击保存人工核验状态；该状态跟随本题保存。")
        elif manual:
            self.b_edit.setText("改答案")
            self.b_edit.setToolTip("编辑或清除你为本题录入的参考答案。")
        else:
            self.b_edit.setText("录入答案")
            self.b_edit.setToolTip("为本题手动录入参考答案。")
        if not keep_pos and not self._user_resized:
            self._fit_content_size()

    @staticmethod
    def _shape_note(q, d):
        """图底部的核对口径说明：形状 + 边长（都没有就不啰嗦）。

        五三反馈参考答案图"只做了一半"—— 完整多边形的逐边方位标注在
        **题卡图片**上，文字题面里没有，软件推不出就不瞎画（画错比不画
        更害人）。但要把"判题到底核对什么"说清楚：关键点 + 边长 + 参数。
        """
        import re as _re2
        try:
            tags = QT.tags(q)
        except Exception:
            tags = []
        shape = None
        for t in tags:
            if t.startswith("graph:"):
                shape = t[6:]
                break
        side = None
        params = (d.get("params") or []) if d else []
        # 边长在题面里通常写作"边长为a / 每条边边长为 a / 边长300米"——
        # 先抽"边长"后面的记号（变量名或数字），再回 params 查值。
        m = _re2.search(r"边长[^0-9a-zA-Z]{0,8}([0-9.]+|[a-zA-Z])",
                        (q.get("requirements_text") or ""))
        if m:
            tok = m.group(1)
            if _re2.match(r"[0-9.]", tok):
                side = tok
            else:
                for it in params:
                    if it.get("key") == tok and it.get("value") is not None:
                        side = "%g%s" % (it.get("value"), it.get("unit") or "")
                        break
        if side is None:
            # 后备：建议值句子里直接带"边长"的（如"边长建议为300米"）
            for it in params:
                if "边长" in (it.get("raw") or "") and it.get("value") is not None:
                    side = "%g%s" % (it.get("value"), it.get("unit") or "")
                    break
        # ★ v1.9.11：有布局查表时完整多边形已经画出来 —— 说明改口径
        if d is not None and d.get("points_full"):
            bits = []
            if shape:
                bits.append(shape)
            if side:
                bits.append("边长 %s" % side)
            rot = d.get("layout_rot") or 0
            bits.append("按题面方位约束校准朝向（旋转 %g°）" % rot
                        if abs(rot) > 0.5 else "朝向与图示一致")
            return "完整%s —— 「自动扫描」逐点逐边核对" % (" · ".join(bits))
        if shape and side:
            return ("%s · 边长 %s —— 其余顶点按题卡图形标注画，"
                    "「自动扫描」会逐边核对" % (shape, side))
        if shape:
            return "%s —— 其余顶点按题卡图形标注画，「自动扫描」逐项核对" % shape
        return None

    def open_versions(self):
        """打开当前导入题卡的快照历史。"""
        question = self._q if isinstance(self._q, dict) else None
        if not question or not question.get("imported"):
            return
        qid = question.get("id")
        try:
            QV.record_snapshot(question, "当前题卡版本")
            versions = QV.versions_of(qid)
        except Exception as exc:
            log.exception("读取题目版本失败 #%s", qid)
            QMessageBox.warning(self, "题目版本不可用",
                                "无法读取本题版本记录：%s" % exc)
            return
        dialog = QuestionVersionDialog(qid, versions, self._scale, self)
        if dialog.exec() != QDialog.Accepted:
            return
        updated = next((row for row in BD.load()
                        if str(row.get("id")) == str(qid)), None)
        if updated:
            self.set_question(updated, keep_pos=True)
            if self._on_restore:
                try:
                    self._on_restore(updated)
                except Exception:
                    log.exception("恢复题目版本后刷新题卡界面失败 #%s", qid)
            if self._on_save:
                try:
                    self._on_save(int(qid))
                except Exception:
                    pass

    def toggle_edit(self, on=None):
        on = (not self._editing) if on is None else bool(on)
        self._editing = on
        self.edit_wrap.setVisible(on)
        if on and self._q is not None:
            self.edit.setText(QM.answer_of(self._q.get("id")))
            self.edit.setFocus()
        if not self._user_resized:
            self._fit_content_size()

    def _fit_content_size(self):
        """按内容调整初始高度，同时保留默认或用户选定的宽度。"""
        width = self.width() or int(self.W * self._scale)
        hint = self.sizeHint()
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, max(self.minimumWidth(), available.width() - 24))
            height = min(max(self.minimumHeight(), hint.height()),
                         max(self.minimumHeight(), available.height() - 24))
        else:
            height = max(self.minimumHeight(), hint.height())
        self._adjusting_content_size = True
        try:
            self.resize(width, height)
        finally:
            self._adjusting_content_size = False
        if self.isVisible() and self._panel_animation is None:
            self._panel_target_geometry = QRect(self.geometry())

    def _sync_resize_handles(self, raise_handles=False):
        if not hasattr(self, "_resize_handles"):
            return
        sync_corner_resize_handles(self, self._resize_handles,
                                   raise_handles=raise_handles)

    def _on_corner_resize(self, geometry):
        self._user_resized = True
        self._panel_target_geometry = QRect(geometry)

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_resize_handles(raise_handles=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_resize_handles(raise_handles=True)
        if (self.isVisible() and not self._adjusting_content_size and
                self._panel_animation is None and not self._panel_is_hiding):
            self._user_resized = True
            self._panel_target_geometry = QRect(self.geometry())

    def save_answer(self):
        if self._q is None:
            return
        txt = self.edit.text()
        QM.set_answer(self._q.get("id"), txt)
        if self._on_save:
            try:
                self._on_save(self._q.get("id"))
            except Exception:
                pass
        self.toggle_edit(False)
        self.set_question(self._q, keep_pos=True)

    # -------------------------------------------------- 拖拽（只认头部）
    def _p_press(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _p_move(self, ev):
        if self._drag is not None and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag)
            self._panel_target_geometry = QRect(self.geometry())

    def _p_release(self, ev):
        self._drag = None

    def _stop_panel_animation(self):
        animation, self._panel_animation = self._panel_animation, None
        if animation is not None:
            animation.stop()
            animation.deleteLater()

    def _animate_panel(self, start_geometry, end_geometry,
                       start_opacity, end_opacity, closing=False):
        animation = QParallelAnimationGroup(self)
        move = QPropertyAnimation(self, b"geometry", animation)
        move.setDuration(210)
        move.setEasingCurve(QEasingCurve.Type.OutCubic)
        move.setStartValue(QRect(start_geometry))
        move.setEndValue(QRect(end_geometry))
        fade = QPropertyAnimation(self, b"windowOpacity", animation)
        fade.setDuration(210)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.setStartValue(float(start_opacity))
        fade.setEndValue(float(end_opacity))
        animation.addAnimation(move)
        animation.addAnimation(fade)
        self._panel_animation = animation
        animation.finished.connect(
            self._finish_panel_hide if closing else self._finish_panel_show)
        animation.start()

    def _finish_panel_show(self):
        animation, self._panel_animation = self._panel_animation, None
        if animation is not None:
            animation.deleteLater()
        if self._panel_target_geometry is not None:
            self.setGeometry(self._panel_target_geometry)
        self.setWindowOpacity(1.0)

    def _finish_panel_hide(self):
        if not self._panel_is_hiding:
            return
        self._panel_is_hiding = False
        animation, self._panel_animation = self._panel_animation, None
        if animation is not None:
            animation.deleteLater()
        QFrame.hide(self)
        if self._panel_target_geometry is not None:
            self.setGeometry(self._panel_target_geometry)
        self.setWindowOpacity(1.0)
        self.closed.emit()

    def show(self):
        was_visible = self.isVisible()
        was_hiding = self._panel_is_hiding
        target = QRect(self._panel_target_geometry if was_hiding and
                       self._panel_target_geometry is not None else self.geometry())
        self._stop_panel_animation()
        self._panel_is_hiding = False
        if not animations_enabled():
            self.setGeometry(target)
            self.setWindowOpacity(1.0)
            QFrame.show(self)
            self.raise_()
            return
        if was_visible and not was_hiding:
            QFrame.show(self)
            self.raise_()
            return
        self._panel_target_geometry = QRect(target)
        if not was_visible:
            self.setGeometry(target.translated(0, -8))
            self.setWindowOpacity(0.0)
        start_geometry = QRect(self.geometry())
        start_opacity = max(0.0, float(self.windowOpacity()))
        QFrame.show(self)
        self.raise_()
        self._animate_panel(start_geometry, target, start_opacity, 1.0)

    def hide(self):
        if not self.isVisible():
            QFrame.hide(self)
            return
        if self._panel_is_hiding:
            return
        if self._panel_animation is None or self._panel_target_geometry is None:
            self._panel_target_geometry = QRect(self.geometry())
        target_geometry = QRect(self._panel_target_geometry)
        start_geometry = QRect(self.geometry())
        start_opacity = max(0.0, float(self.windowOpacity()))
        self._stop_panel_animation()
        if not animations_enabled():
            self._finish_panel_hide_immediately()
            return
        self._panel_is_hiding = True
        end_geometry = target_geometry.translated(0, -8)
        self._animate_panel(start_geometry, end_geometry,
                            start_opacity, 0.0, closing=True)

    def _finish_panel_hide_immediately(self):
        self._panel_is_hiding = False
        QFrame.hide(self)
        if self._panel_target_geometry is not None:
            self.setGeometry(self._panel_target_geometry)
        self.setWindowOpacity(1.0)
        self.closed.emit()

    def closeEvent(self, ev):
        ev.ignore()
        self.hide()
