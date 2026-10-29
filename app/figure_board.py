# -*- coding: utf-8 -*-
"""
题卡画板 —— 完整展示 + 自由缩放 + 绘制批注 + 橡皮擦 + 导出
用于在飞行计划题卡上直接画航线、标点、写备注。

交互：
  · 滚轮 / 捏合  → 缩放
  · 中键拖 / 空格+左键拖 / 右键拖 → 平移
  · 左键拖      → 按当前工具绘制（笔 / 橡皮）
  · Ctrl+Z      → 撤销；Ctrl+Shift+Z → 重做
  · 双击        → 复位视图
"""
import os
import datetime
import time

from PySide6.QtCore import (Qt, QEvent, QPoint, QPointF, QRectF, QSize, Signal,
                            QPropertyAnimation, QVariantAnimation, QEasingCurve, QTimer)
from PySide6.QtGui import (QColor, QPainter, QPen, QBrush, QPixmap, QImage,
                           QPainterPath, QFont, QTransform, QKeySequence)
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QVBoxLayout,
                               QHBoxLayout, QFileDialog, QMessageBox,
                               QSizePolicy, QColorDialog)

import theme as T
import progress as PG
import geo
import answers as AN
import linkgcs as LG
import reference_learning as RL
from logger import get_logger
from ui_kit import (Icons, add_shadow, glass, btn_style, IconButton, fade_in,
                    pm_to_icon, refresh_glass, show_window_animated,
                    close_window_animated, animations_enabled)
from answer_panel import AnswerPanel, JudgeDialog
from motion_runtime import motion_page
from window_resize import create_corner_resize_handles, sync_corner_resize_handles

log = get_logger()

# 调色板：黑 / 红 / 蓝 / 绿 / 橙 / 紫
PEN_COLORS = ["#111827", "#E5484D", "#1A56C4", "#2FA36B", "#E8A33D", "#9333EA"]

# 笔锋：落笔最粗，沿笔迹按弧长衰减，收到宽度的 (1-TAPER) 倍后保持。
PEN_TAPER = 0.65            # 收笔保留 35% 宽度
PEN_TAPER_SPAN = 26         # 衰减尺度 = 笔宽 × 该系数（单位：图片像素）
PEN_TAPER_SPAN_MIN = 300    # 衰减尺度下限（图片像素）——
# 细笔若严格按笔宽成比例，跨度只有百来个像素，画一条短横几乎看不出收锋


def _fit_screen(w, h, ratio=0.90):
    """把窗口尺寸压进屏幕可用区域（已扣掉任务栏）。

    无边框窗口不会自己适配屏幕。之前写死 1000x802，在 1366x768 或者开了
    系统缩放的机器上会整块超出屏幕 —— 底栏的「缩放百分比 / 关闭」直接被裁掉，
    用户报的"关闭缩放按钮不显示"就是这个。
    """
    try:
        from PySide6.QtWidgets import QApplication
        scr = QApplication.primaryScreen()
        if scr is not None:
            a = scr.availableGeometry()
            w = max(360, min(int(w), int(a.width() * ratio)))
            h = max(320, min(int(h), int(a.height() * ratio)))
    except Exception:
        pass
    return int(w), int(h)


class _Stroke:
    """一笔（一条连续轨迹）。

    除了点序列，还维护 cum[i] = 从落笔点到 pts[i] 的**累计弧长**。
    笔锋（落笔粗、收笔细）的宽度就是按这个弧长算的 —— 见 _paint_stroke。
    """

    __slots__ = ("pts", "cum", "pressures", "color", "width", "eraser",
                 "taper", "d0")

    def __init__(self, color, width, eraser=False):
        self.pts = []
        self.cum = [0.0]
        self.pressures = []
        self.color = color
        self.width = width
        self.eraser = eraser
        # 橡皮擦不需要笔锋：等宽擦除，手感更可控
        self.taper = 0.0 if eraser else PEN_TAPER
        self.d0 = 0.0        # 本段起点在整笔中的弧长（增量落墨时用）

    def add(self, x, y, pressure=0.5):
        """追加一个点，并同步累加弧长"""
        if self.pts:
            x0, y0 = self.pts[-1]
            x = x0 + (x - x0) * 0.74
            y = y0 + (y - y0) * 0.74
            if ((x - x0) ** 2 + (y - y0) ** 2) < 0.04:
                return
            self.cum.append(self.cum[-1]
                            + ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5)
            if self.pressures:
                pressure = self.pressures[-1] + (
                    pressure - self.pressures[-1]) * 0.62
        self.pts.append((x, y))
        self.pressures.append(max(0.0, min(1.0, float(pressure))))


@motion_page
class FigureBoard(QWidget):
    """题卡显示区：完整显示图形 + 缩放平移 + 批注。

    绘制栈：底图 → 批注笔迹（合成在 overlay QImage 上）。
    橡皮擦用 DestinationOut 合成模式擦除 overlay，保证擦掉的是笔迹而不是底图。
    """

    zoom_changed = Signal(float)

    MIN_SCALE, MAX_SCALE = 0.15, 8.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_TabletTracking, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(240, 200)

        self.src = None          # 原图 QPixmap
        self.scale = 1.0
        self.offset = QPointF(0, 0)   # 图片左上角在控件坐标中的位置
        # 「整页完整显示」模式：窗口尺寸一变就重新适配，保证题卡永远
        # 完整可见、不裁切。手动缩放过后这个标记会关掉，免得抢用户的视图。
        self.fit_mode = True
        self._resize_fit_timer = QTimer(self)
        self._resize_fit_timer.setSingleShot(True)
        self._resize_fit_timer.timeout.connect(self.fit)

        self.tool = "pan"        # pan / pen / eraser
        self.pen_color = PEN_COLORS[0]
        self.pen_width = 3
        self.eraser_width = 22

        self.strokes = []        # 已完成的笔画
        self.redo_stack = []
        self.cur = None          # 正在画的笔画
        self._space = False
        self._tablet_active = False
        self._tablet_suppress_until = 0.0
        self._drag = None        # ("pan", start_pos) / ("draw", None)
        self._overlay = None     # QImage，尺寸与当前显示的图一致

    # ---------------------------------------------------- 图片
    def set_image(self, pm):
        self.src = self._a4_page_image(pm)
        self.strokes = []
        self.redo_stack = []
        self._overlay = None
        self.fit()
        self.update()

    @staticmethod
    def _a4_page_image(pm):
        """把题卡原图完整放入 A4 纵向白纸，不拉伸、不裁掉原图内容。"""
        if pm is None or pm.isNull():
            return pm
        width, height = pm.width(), pm.height()
        page_width = width
        page_height = max(height, round(width * 297 / 210))
        if page_height == height and width / float(max(1, height)) < 210 / 297:
            page_width = max(width, round(height * 210 / 297))
        if (page_width, page_height) == (width, height):
            return pm

        page = QImage(page_width, page_height, QImage.Format_RGB32)
        page.fill(QColor("#FFFFFF"))
        painter = QPainter(page)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap((page_width - width) // 2,
                           (page_height - height) // 2, pm)
        painter.end()
        return QPixmap.fromImage(page)

    def fit(self):
        """缩放到完整可见（fit 模式，保留一点边距）"""
        if not self.src or self.src.isNull():
            return
        self.fit_mode = True
        aw = max(60, self.width() - 24)
        ah = max(60, self.height() - 24)
        iw, ih = self.src.width(), self.src.height()
        self.scale = min(aw / iw, ah / ih)
        self.offset = QPointF((self.width() - iw * self.scale) / 2,
                              (self.height() - ih * self.scale) / 2)
        self._ensure_overlay()
        self.zoom_changed.emit(self.scale)
        self.update()

    def actual_size(self):
        """1:1 原始像素"""
        if not self.src:
            return
        self.fit_mode = False
        self.scale = 1.0
        self.offset = QPointF((self.width() - self.src.width()) / 2,
                             (self.height() - self.src.height()) / 2)
        self._ensure_overlay()
        self.zoom_changed.emit(self.scale)
        self.update()

    def zoom_by(self, factor, center=None):
        if not self.src:
            return
        old = self.scale
        new = max(self.MIN_SCALE, min(self.MAX_SCALE, old * factor))
        if abs(new - old) < 1e-9:
            return
        # 用户一旦手动缩放，就退出「整页显示」模式，
        # 否则窗口稍一变化就会被强行拉回适配状态，等于缩放白做。
        self.fit_mode = False
        if center is None:
            center = QPointF(self.width() / 2, self.height() / 2)
        # 让 center 对应的图片坐标在缩放前后保持不动
        ix = (center.x() - self.offset.x()) / old
        iy = (center.y() - self.offset.y()) / old
        self.scale = new
        self.offset = QPointF(center.x() - ix * new, center.y() - iy * new)
        self.zoom_changed.emit(self.scale)
        self.update()

    def autofit(self):
        """外部（窗口首次显示 / 尺寸收敛后）调用：仍在整页模式下就重新适配。

        打开题卡时控件尺寸还没定下来，fit() 会按一个偏小的中间尺寸算，
        等布局收敛后题卡就只占一角、或者反过来溢出被裁 —— 用户看到的就是
        「文字显示不全」。所以显示之后再补一次。
        """
        if self.fit_mode:
            self.fit()

    # ---------------------------------------------------- 绘制栈
    def _ensure_overlay(self):
        """批注层图片，尺寸等于「原始图片像素」，这样缩放时笔迹跟着放大不模糊"""
        if not self.src or self.src.isNull():
            return
        w, h = self.src.width(), self.src.height()
        if (self._overlay is None or self._overlay.width() != w
                or self._overlay.height() != h):
            img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
            img.fill(Qt.transparent)
            if self._overlay is not None:
                p = QPainter(img)
                p.drawImage(0, 0, self._overlay)
                p.end()
            self._overlay = img

    def _rebuild_overlay(self):
        """按笔画列表重画批注层（撤销/重做/清空后调用）"""
        if not self.src or self.src.isNull():
            return
        w, h = self.src.width(), self.src.height()
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        for s in self.strokes:
            self._paint_stroke(p, s)
        p.end()
        self._overlay = img

    @classmethod
    def _paint_stroke(cls, p, s):
        """画一笔，带笔锋：落笔最粗，沿弧长收细到 (1-taper) 倍后保持。

        为什么不是「一根等宽的 QPen 折线」：用户要的是毛笔那种落笔粗、
        收笔细的笔触。做法是把中心线按每点的宽度扩成一条**闭合轮廓**
        （左右两条偏移线 + 两端圆头）再填充。

        宽度只依赖「该点距落笔点的弧长」（绝对量），不依赖「占整笔的比例」：
        笔迹是鼠标拖动过程中**增量落墨**的，一笔会分很多帧画上去。若按整笔
        比例算宽度，同一个点在拖动过程中会算出不同宽度，撤销/重做后的整笔
        重建结果也会跟当初画的不一致。用绝对弧长则增量与重建完全等价。
        """
        n = len(s.pts)
        if n < 1:
            return
        p.setRenderHint(QPainter.Antialiasing)
        if s.eraser:
            p.setCompositionMode(QPainter.CompositionMode_DestinationOut)
            col = QColor(0, 0, 0)
        else:
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            col = QColor(s.color)
        p.setPen(Qt.NoPen)
        p.setBrush(col)

        unit = max(0.6, float(s.width))
        span = max(PEN_TAPER_SPAN_MIN, unit * PEN_TAPER_SPAN)

        def w_at(d):
            """距落笔点弧长 d 处的笔宽（图片像素）"""
            t = min(1.0, d / span)
            return max(0.4, unit * (1.0 - s.taper * (t ** 0.85)))

        cum = s.cum if len(s.cum) == n else None
        pressures = s.pressures if len(s.pressures) == n else None

        def pressure_factor(index):
            if pressures is None or s.eraser:
                return 1.0
            return 0.58 + max(0.0, min(1.0, pressures[index])) * 0.84

        if n == 1:
            r = w_at(s.d0) * pressure_factor(0) / 2.0
            x, y = s.pts[0]
            p.drawEllipse(QPointF(x, y), r, r)
            return

        left, right = [], []
        for i in range(n):
            x, y = s.pts[i]
            # 走向向量（首尾用单侧差分）
            if i == 0:
                ax, ay = s.pts[1][0] - x, s.pts[1][1] - y
            elif i == n - 1:
                ax, ay = x - s.pts[-2][0], y - s.pts[-2][1]
            else:
                ax = s.pts[i + 1][0] - s.pts[i - 1][0]
                ay = s.pts[i + 1][1] - s.pts[i - 1][1]
            ln = (ax * ax + ay * ay) ** 0.5
            if ln < 1e-9:
                ax, ay, ln = 1.0, 0.0, 1.0
            nx, ny = -ay / ln, ax / ln           # 单位法线
            d = s.d0 + (cum[i] if cum else 0.0)
            half = w_at(d) * pressure_factor(i) / 2.0
            left.append((x + nx * half, y + ny * half))
            right.append((x - nx * half, y - ny * half))

        path = QPainterPath()
        path.setFillRule(Qt.WindingFill)
        path.moveTo(left[0][0], left[0][1])
        for q in left[1:]:
            path.lineTo(q[0], q[1])
        for q in reversed(right):
            path.lineTo(q[0], q[1])
        path.closeSubpath()
        p.drawPath(path)

        # 两端补圆头（轮廓的端面是平的，不补会有方角）
        total = cum[-1] if cum else 0.0
        for (px, py), d, index in ((s.pts[0], s.d0, 0),
                                    (s.pts[-1], s.d0 + total, n - 1)):
            r = w_at(d) * pressure_factor(index) / 2.0
            p.drawEllipse(QPointF(px, py), r, r)

    def _to_image(self, pos):
        """控件坐标 → 图片像素坐标"""
        return ((pos.x() - self.offset.x()) / self.scale,
                (pos.y() - self.offset.y()) / self.scale)

    def _begin_stroke(self, pos, pressure=0.5):
        if not self.src or self.tool not in ("pen", "eraser"):
            return
        self._ensure_overlay()
        self.cur = _Stroke(self.pen_color, self.cur_width(),
                           eraser=(self.tool == "eraser"))
        self.cur.add(*self._to_image(pos), pressure=pressure)
        self._drag = ("draw", None, None)
        self._paint_cur()

    def _append_stroke(self, pos, pressure=0.5):
        if self.cur is None:
            return
        before = len(self.cur.pts)
        self.cur.add(*self._to_image(pos), pressure=pressure)
        if len(self.cur.pts) != before:
            self._paint_cur()

    def _finish_stroke(self):
        if self.cur is not None and self.cur.pts:
            self.strokes.append(self.cur)
            self.redo_stack = []
        self.cur = None
        self._drag = None
        self.update()

    def cur_width(self):
        """当前笔宽，换算成「图片像素」。

        pen_width / eraser_width 的语义是**屏幕上看到的像素**，但笔迹是画在
        原图分辨率（900×1125）的 overlay 上的：显示时整层跟着 scale 一起缩放。
        所以落笔必须除以当前 scale —— 整页模式下 scale≈0.47，若把 4 直接当
        图片像素画，屏幕上只有约 1.9px，四档粗细几乎看不出差别（用户反馈
        "画出来没有粗细"）。除以 scale 后，选 8 就在屏幕上真的是 8px。
        """
        w = self.pen_width if self.tool == "pen" else self.eraser_width
        return max(0.6, float(w) / max(0.05, self.scale))

    # ---------------------------------------------------- 事件
    def wheelEvent(self, ev):
        if not self.src:
            return
        d = ev.angleDelta().y()
        f = 1.0015 ** d
        self.zoom_by(f, QPointF(ev.position()))
        ev.accept()

    def mousePressEvent(self, ev):
        self.setFocus()
        btn = ev.button()
        pos = ev.position()
        if time.monotonic() < self._tablet_suppress_until or self._tablet_active:
            ev.accept()
            return

        if btn == Qt.MiddleButton or (btn == Qt.LeftButton and self._space) \
                or (btn == Qt.RightButton and self.tool == "pan"):
            self._drag = ("pan", pos, QPointF(self.offset))
            self.setCursor(Qt.ClosedHandCursor)
            return

        if btn == Qt.LeftButton and self.tool in ("pen", "eraser"):
            self._begin_stroke(pos, 0.5)
            return
        self._drag = ("pan", pos, QPointF(self.offset))

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        if not self._drag:
            return
        kind = self._drag[0]
        if kind == "pan":
            d = pos - self._drag[1]
            self.offset = QPointF(self._drag[2].x() + d.x(),
                                  self._drag[2].y() + d.y())
            self.update()
        elif kind == "draw" and self.cur is not None:
            self._append_stroke(pos, 0.5)

    def mouseReleaseEvent(self, ev):
        if time.monotonic() < self._tablet_suppress_until:
            ev.accept()
            return
        if not self._drag:
            return
        kind = self._drag[0]
        if kind == "draw":
            self._finish_stroke()
        else:
            self._drag = None
        if self.tool in ("pen", "eraser"):
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.OpenHandCursor)

    def tabletEvent(self, ev):
        event_type = ev.type()
        if event_type == QEvent.Type.TabletPress:
            self._tablet_active = True
            self._begin_stroke(ev.position(), ev.pressure())
        elif event_type == QEvent.Type.TabletMove:
            if self._tablet_active:
                self._append_stroke(ev.position(), ev.pressure())
        elif event_type == QEvent.Type.TabletRelease:
            if self._tablet_active:
                self._finish_stroke()
            self._tablet_active = False
            self._tablet_suppress_until = time.monotonic() + 0.25
        ev.accept()

    def mouseDoubleClickEvent(self, ev):
        self.fit()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Space:
            self._space = True
            self.setCursor(Qt.OpenHandCursor)
        elif ev.matches(QKeySequence.Undo):
            self.undo()
        elif ev.matches(QKeySequence.Redo):
            self.redo()
        elif ev.key() == Qt.Key_Escape:
            self.set_tool("pan")
        else:
            super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):
        if ev.key() == Qt.Key_Space:
            self._space = False
        super().keyReleaseEvent(ev)

    def _paint_cur(self):
        """把当前笔画的最新一小段增量画到 overlay 上（避免每次全量重绘）。

        增量段的宽度必须继承「该段起点在整笔中的弧长」d0（见 _paint_stroke）：
        否则每一小段都会各自从头收细一遍，画出来是一串小蝌蚪。
        """
        if self._overlay is None or self.cur is None:
            return
        p = QPainter(self._overlay)
        n = len(self.cur.pts)
        if n >= 2:
            x1, y1 = self.cur.pts[n - 2]
            x2, y2 = self.cur.pts[n - 1]
            tmp = _Stroke(self.cur.color, self.cur.width, self.cur.eraser)
            tmp.pts = [(x1, y1), (x2, y2)]
            tmp.cum = [0.0, self.cur.cum[n - 1] - self.cur.cum[n - 2]]
            tmp.pressures = [self.cur.pressures[n - 2],
                             self.cur.pressures[n - 1]]
            tmp.d0 = self.cur.cum[n - 2]
            self._paint_stroke(p, tmp)
        elif n == 1:
            self._paint_stroke(p, self.cur)
        p.end()
        self.update()

    # ---------------------------------------------------- 编辑操作
    def undo(self):
        if not self.strokes:
            return
        self.redo_stack.append(self.strokes.pop())
        self._rebuild_overlay()
        self.update()

    def redo(self):
        if not self.redo_stack:
            return
        self.strokes.append(self.redo_stack.pop())
        self._rebuild_overlay()
        self.update()

    def clear_marks(self):
        if not self.strokes:
            return
        self.redo_stack = list(self.strokes)
        self.strokes = []
        self._rebuild_overlay()
        self.update()

    def can_undo(self):
        return bool(self.strokes)

    def set_tool(self, tool):
        self.tool = tool
        if tool == "pan":
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.setCursor(Qt.CrossCursor)

    # ---------------------------------------------------- 导出
    def compose(self):
        """把底图与批注合成成一张 QPixmap（导出用）"""
        if not self.src:
            return None
        out = QPixmap(self.src.size())
        out.fill(Qt.white)
        p = QPainter(out)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(0, 0, self.src)
        if self._overlay is not None:
            p.drawImage(0, 0, self._overlay)
        p.end()
        return out

    def save_png(self, path):
        pm = self.compose()
        if pm is None:
            return False
        return pm.save(path, "PNG")

    # ---------------------------------------------------- 绘制
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        # 底板：必须不透明。panel_soft 是半透明的，衬在 WA_TranslucentBackground
        # 的无边框窗口上会让人以为"画板背景是黑的"（抓图 / 部分显卡下尤其明显）。
        p.fillRect(self.rect(), QColor("#000000"))

        if not self.src or self.src.isNull():
            p.setPen(QColor(T.P["ink3"]))
            f = QFont(T.pick_font(), 12)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter, "本题无配图")
            p.end()
            return

        iw, ih = self.src.width(), self.src.height()
        target = QRectF(self.offset.x(), self.offset.y(),
                        iw * self.scale, ih * self.scale)

        # 白色纸底
        p.fillRect(target, QColor("#FFFFFF"))
        # 顺序非常重要：必须先铺底图，再把批注层画在上面。
        # 之前是反过来的（先 overlay 后底图），而题卡底图是张不透明 JPEG，
        # 会把笔迹整个盖住 —— 用户画了半天屏幕上什么都没有，
        # 反馈就是"题卡绘制功能无法使用"。诊断脚本实测：绘制前后像素差异 = 0。
        p.drawPixmap(target, self.src, QRectF(0, 0, iw, ih))
        if self._overlay is not None:
            p.drawImage(target, self._overlay)

        p.end()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        # 整页显示模式下，控件尺寸一变就重新适配：
        # 否则窗口拉大后题卡还是按旧尺寸缩的，四周留一大片空白；
        # 反过来窗口拉小时题卡会溢出，底部内容被裁掉。
        if self.fit_mode:
            # 拖动窗口时最多每帧适配一次，避免连续重算造成题卡闪烁。
            if not self._resize_fit_timer.isActive():
                self._resize_fit_timer.start(16)


@motion_page
class FigureDialog(QFrame):
    """题卡工作台（独立窗口）：
    完整题卡 + 绘制/橡皮擦 + 缩放 + 置顶 + 导出 + 上一题/下一题。"""

    # 标记状态变了要通知题库页刷新列表（qid, 现在是否已做）
    done_changed = Signal(int, bool)
    # 判题记账变了（收错题/移出错题）—— 判**错**时不标已做、done 不发，
    # 题库页统计卡的「错题」数就停在这之前的旧值（实测一直显示 0）。
    wrong_changed = Signal()
    # 窗口被关闭 / 隐藏 —— 题库页靠它掐掉还没到期的延迟记账
    closed = Signal()

    def __init__(self, parent=None, qid=None, source="", title="", scale=1.0):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setWindowTitle("题卡 · %s" % title)
        self._scale = scale
        self._drag = None
        self._topmost = True
        self._theme_mode = T.mode
        self._window_motion = None
        self._window_closing = False
        self._qid = qid
        self._cur_q = None       # 当前这道题的完整数据（判题要用题干）
        self._bank = []          # 当前用户导入的题卡（外部注入，见 qbank_cb）
        self.qbank_cb = None     # 由外部注入 ()->[题目] —— 拿题库全量
        self.go_question_cb = None   # 由外部注入 (qid)->None —— 跳到指定题
        self.ref_saved_cb = None     # 由外部注入 (qid)->None —— 标注答案后通知
        self._dlg = None         # 判题 / 提示弹窗，必须持引用否则被 GC 掉
        self.last_gcs = None     # 最近一次「自动扫描」的结果
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        # 尺寸必须压进屏幕可用区域，否则底栏（缩放百分比 / 关闭）会被裁到
        # 屏幕外 —— 用户报的"关闭、缩放按钮不显示"就是这个原因。
        # 作答栏默认展开（见下方 wrap 段），所以初始尺寸直接按「题卡 + 作答栏」
        # 算好，免得先以窄窗口画出来再被撑宽、视觉上闪一下。
        # _w_before 记的是「收起作答栏后应该回到的宽度」。
        # 注意别改回"showEvent 里首次加宽"：offscreen 平台下 show() 不投递
        # showEvent，逻辑会静默失效（验收实测过）。
        self._base_w, base_h = _fit_screen(1000, 802)
        self._w_before = self._base_w
        self.resize(*_fit_screen(self._base_w + int(AnswerPanel.W * scale),
                                 base_h))
        self.setMinimumSize(620, 460)

        self._resize_handles = create_corner_resize_handles(self)

        outer = QVBoxLayout(self)
        # 阴影必须留在无边框窗口预留的透明边距内，否则系统会截断阴影，
        # 在窗口四周形成突兀的矩形边缘。
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(0)

        self.card = glass("GlassStrong", radius=20)
        add_shadow(self.card, blur=14, dy=3, alpha=42)
        outer.addWidget(self.card)

        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        # ---- 标题栏（可拖拽） ----
        self.tbar = QWidget()
        self.tbar.setFixedHeight(int(46 * min(scale, 1.2)))
        self.tbar.setStyleSheet("background:transparent;")
        tl = QHBoxLayout(self.tbar)
        tl.setContentsMargins(16, 0, 10, 0)
        tl.setSpacing(6)

        cap = QLabel("题卡 · %s" % title)
        cap.setStyleSheet(
            f"font-size:{'13'}px; font-weight:700; color:{T.P['ink']};"
            f"background:transparent;")
        self.cap = cap
        tl.addWidget(cap)
        tl.addStretch()

        # ★ v1.9.12：标题栏的"作答栏开关"小 ✓ 删掉了 —— 五三：
        #   「下面这个按钮代替上面那个小按钮，不然下面的按钮存在没多大意义。」
        #   它跟底栏「判题」是同一个功能（都是开/收右侧作答栏），留两颗
        #   只会让底栏「判题」显得多余。现在**底栏「判题」是唯一开关**
        #   （点一下开、再点一下收），标题栏只留 置顶 / 关闭。
        self.b_top = self._tb("pin", "窗口置顶", self.toggle_top,
                              icon_size=16, toggle=True)
        self.b_close = self._tb("close", "关闭", self.close, icon_size=16)
        tl.addWidget(self.b_top)
        tl.addWidget(self.b_close)
        self.tbar.mousePressEvent = self._t_press
        self.tbar.mouseMoveEvent = self._t_move
        self.tbar.mouseReleaseEvent = self._t_release
        cl.addWidget(self.tbar)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{T.P['glass_soft_edge']};")
        cl.addWidget(sep)

        # ---- 工具条 ----
        # 为什么分两行：工具按钮 + 6 色板 + 4 档粗细 + 6 个操作按钮，
        # 单行总宽约 1080px，在 1000px 窗口里会把右侧操作按钮挤出可视区
        # （表现为一排空白方块）。拆成两行后每行都有富余，窗口再小也不挤。
        tools = QWidget()
        tools.setStyleSheet("background:transparent;")
        tools_v = QVBoxLayout(tools)
        tools_v.setContentsMargins(14, 10, 14, 10)
        tools_v.setSpacing(8)

        # 第一行：绘制工具 + 颜色 + 粗细
        row1 = QWidget()
        row1.setStyleSheet("background:transparent;")
        tll = QHBoxLayout(row1)
        tll.setContentsMargins(0, 0, 0, 0)
        tll.setSpacing(8)

        self.b_pan = self._tool("pan", "拖拽平移", "拖动查看", lambda: self.set_tool("pan"))
        self.b_pen = self._tool("pen", "画笔", "在题卡上绘制", lambda: self.set_tool("pen"))
        self.b_era = self._tool("eraser", "橡皮擦", "擦除笔迹", lambda: self.set_tool("eraser"))
        for b in (self.b_pan, self.b_pen, self.b_era):
            tll.addWidget(b)

        d = QFrame()
        d.setFixedWidth(1)
        d.setStyleSheet(f"background:{T.P['glass_soft_edge']};")
        tll.addWidget(d)

        # 颜色
        self.color_btns = []
        for c in PEN_COLORS:
            cb = QPushButton()
            cb.setFixedSize(int(24 * scale), int(24 * scale))
            cb.setCursor(Qt.PointingHandCursor)
            cb._color = c
            cb.clicked.connect(lambda _=False, col=c: self.set_color(col))
            tll.addWidget(cb)
            self.color_btns.append(cb)

        d2 = QFrame()
        d2.setFixedWidth(1)
        d2.setStyleSheet(f"background:{T.P['glass_soft_edge']};")
        tll.addWidget(d2)

        self.width_lb = QLabel("笔触")
        self.width_lb.setStyleSheet(
            f"font-size:12px; color:{T.P['ink2']}; background:transparent;")
        tll.addWidget(self.width_lb)
        # 粗细档位：数值 = 屏幕像素。按钮上直接画一条同粗的横线做预览，
        # 比只写数字直观（光看 4 和 8 看不出差多少）。
        for w in (2, 4, 8, 16):
            wb = QPushButton()
            wb.setFixedSize(int(40 * scale), int(26 * scale))
            wb.setCursor(Qt.PointingHandCursor)
            wb.setToolTip("笔触粗细：屏幕上约 %d px" % w)
            wb._w = w
            wb.clicked.connect(lambda _=False, x=w: self.set_pen_width(x))
            tll.addWidget(wb)
        self._width_btns = [tll.itemAt(i).widget() for i in
                            range(tll.count() - 4, tll.count())]
        tll.addStretch()
        tools_v.addWidget(row1)

        # 第二行：操作按钮（撤销/重做/清空/适应/1:1/导出），右对齐
        row2 = QWidget()
        row2.setStyleSheet("background:transparent;")
        tl2 = QHBoxLayout(row2)
        tl2.setContentsMargins(0, 0, 0, 0)
        tl2.setSpacing(8)

        self.b_undo = self._tb2("reset", "撤销 (Ctrl+Z)", self.do_undo)
        self.b_redo = self._tb2("chevron", "重做 (Ctrl+Shift+Z)", self.do_redo,
                                down=False)
        self.b_clear = self._tb2("trash", "清空批注", self.do_clear)
        self.b_fit = self._tb2("zoom", "整页完整显示（不裁切）· 双击题卡同样效果",
                               self.do_fit)
        self.b_1to1 = self._tb2("eye", "原始比例 1:1", self.do_1to1)
        self.b_save = self._tb2("save", "导出题卡（含批注）", self.do_save)

        # ★ v1.9.7 参考答案：带文字的主按钮（不是图标）——
        #   这是新功能，光给个钥匙图标用户认不出来。
        #   默认收起，点一下才弹浮层（五三要求："避免还没做了就已经知道答案了"）。
        self.b_ref = IconButton("参考答案", "key", "default",
                                scale=scale, font_size=12, radius=9)
        self.b_ref.setFixedHeight(int(30 * scale))
        self.b_ref.setToolTip(
            "看这道题的标准答案（坐标 / 边长 / 高度 / 速度 / 建议参数）\n"
            "默认收起，点开才显示 · 也可以自己标注一份")

        hint = QLabel("提示：滚轮缩放 · 中键或空格拖动平移 · Ctrl+Z 撤销")
        hint.setStyleSheet(
            f"font-size:11px; color:{T.P['ink3']}; background:transparent;")
        tl2.addWidget(hint)
        tl2.addStretch()
        tl2.addWidget(self.b_ref)
        for b in (self.b_undo, self.b_redo, self.b_clear, self.b_fit,
                  self.b_1to1, self.b_save):
            tl2.addWidget(b)
        tools_v.addWidget(row2)
        cl.addWidget(tools)

        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"background:{T.P['glass_soft_edge']};")
        cl.addWidget(sep2)

        # ---- 画板 + 作答侧栏 ----
        # 作答栏和题卡必须同屏：用户是一边看题卡一边算坐标的，
        # 做成独立窗口会来回切，做题节奏直接断。
        wrap = QWidget()
        wrap.setStyleSheet("background:transparent;")
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(14, 12, 14, 12)
        wl.setSpacing(10)
        self.board = FigureBoard()
        wl.addWidget(self.board, 1)

        self.apanel = AnswerPanel(scale=scale)
        # ★ v1.9.10：手填判题也走统一记账链路 —— 原来只连 _on_judged（只管判对
        #   标已做），**手填判错的题根本没进错题集**（自动扫描那条路才记）。
        #   同一个动作两条账，用户的错题集就漏了手填判错的题。
        self.apanel.judged.connect(self._on_manual_judged)
        # 「自动扫描」按钮的落点：读地面站 → 回填 → 判当前题，全在工作台这层做。
        self.apanel.scan_cb = self.auto_scan
        wl.addWidget(self.apanel, 0)
        # 默认展开。判题是这版的主功能，之前藏在标题栏一个 30px 的小图标里，
        # 五三直接问"判题按钮在哪" —— 所以打开工作台就把作答栏摆在右边。
        # 窗口宽度已在 __init__ 开头按展开态设好，_w_before 也在那里记过。
        self._ans_on = True

        cl.addWidget(wrap, 1)

        sep3 = QFrame()
        sep3.setFixedHeight(1)
        sep3.setStyleSheet(f"background:{T.P['glass_soft_edge']};")
        cl.addWidget(sep3)

        # ---- 底栏 ----
        foot = QWidget()
        foot.setStyleSheet("background:transparent;")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(14, 9, 14, 11)
        fl.setSpacing(9)
        self.zoom_lb = QLabel("100%")
        self.zoom_lb.setStyleSheet(
            f"font-size:12px; font-weight:700; color:{T.P['ink2']};"
            f"background:transparent;")
        fl.addWidget(self.zoom_lb)

        # ---- 做题进度：标记按钮 + 练习历史 ----
        self.b_done = IconButton("标记已做", "check", "default",
                                 scale=scale, font_size=13, radius=10)
        self.b_done.setMinimumHeight(int(34 * scale))
        self.b_done.setToolTip("在题目页停留满 1 分钟会自动记为已做；"
                               "这里也可手动标记或撤销")
        self.b_done.clicked.connect(self.toggle_done)
        fl.addWidget(self.b_done)

        self.hist_lb = QLabel("")
        self.hist_lb.setStyleSheet(
            f"font-size:11px; color:{T.P['ink3']}; background:transparent;")
        fl.addWidget(self.hist_lb)

        fl.addStretch()
        # 判题入口。
        #
        # v1.9.5 重新划清了两个按钮的职责 —— 五三的原话：
        # 「扫盘判题叫做收起判题，谁能知道这是扫盘判题？」
        #
        # 老版：底栏这一个按钮既是"扫盘判题"、又兼职"收起作答栏"，
        #       名字叫「判题 / 收起判题」，两件事都说不清。
        # 现在：底栏「判题」= **打开右侧作答栏**（手动判题界面）的入口；
        #       扫盘的动作搬进作答栏里的「自动扫描」（紧挨「提交判题」）。
        #       用户路径变成：点「判题」→ 点「自动扫描」→ 点「提交判题」。
        self.b_judge = IconButton("判题", "check", "primary",
                                  scale=scale, font_size=13)
        self.b_judge.setMinimumHeight(int(34 * scale))
        self.b_judge.setToolTip(
            "打开右侧作答栏（手动判题）：\n"
            "· 点「自动扫描」→ 读地面站里画好的航线，自动填进来\n"
            "· 也可以直接手填\n"
            "· 点「提交判题」看对错")
        self.b_judge.clicked.connect(self.judge_smart)
        fl.addWidget(self.b_judge)

        self.b_prev = IconButton("上一题", None, "default", scale=scale,
                                 font_size=13)
        self.b_prev.clicked.connect(lambda: self.step(-1))
        self.b_next = IconButton("下一题", None, "default", scale=scale,
                                 font_size=13)
        self.b_next.clicked.connect(lambda: self.step(1))
        fl.addWidget(self.b_prev)
        fl.addWidget(self.b_next)
        cl.addWidget(foot)

        self.board.zoom_changed.connect(
            lambda s: self.zoom_lb.setText("%d%%" % round(s * 100)))
        self.board.set_tool("pan")
        self.set_color(PEN_COLORS[0])
        self.set_pen_width(4)
        self._sync_top_btn()
        self._sync_done()
        self.b_ref.clicked.connect(self.toggle_ref)
        self._ref = None         # 参考答案浮层（懒建，必须持引用）
        self.step_cb = None      # 由外部注入 (delta)->(id, source) or None

    # ---------------------------------------------------- 小工具
    def _tb(self, kind, tip, slot, icon_size=16, toggle=False):
        b = QPushButton()
        b.setFixedSize(int(30 * self._scale), int(26 * self._scale))
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b._kind = kind
        b._toggle = toggle
        b.clicked.connect(slot)
        return b

    def _tb2(self, icon, tip, slot, down=True):
        b = QPushButton()
        b.setFixedSize(int(34 * self._scale), int(30 * self._scale))
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b._icon = icon
        b._icon_kw = dict(down=down) if icon == "chevron" else {}
        b.clicked.connect(slot)
        return b

    def _tool(self, icon, text, tip, slot):
        b = QPushButton(text)
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b.setMinimumHeight(int(32 * self._scale))
        b._icon = icon
        b.clicked.connect(slot)
        return b

    def _sync_top_btn(self):
        col = T.C_BLUE if self._topmost else T.P["ink2"]
        self.b_top.setIcon(self._qicon("pin", col, filled=self._topmost))
        self.b_top.setIconSize(QSize(int(17 * self._scale),
                                     int(17 * self._scale)))
        self.b_top.setStyleSheet(f"""
            QPushButton {{
                background: {'rgba(26,86,196,0.16)' if self._topmost else 'transparent'};
                border: none; border-radius: {int(7*self._scale)}px;
                padding: 0px;
            }}
            QPushButton:hover {{ background: rgba(26,86,196,0.22); }}
        """)
        col2 = T.P["ink2"]
        # 底栏「判题」按钮文案固定 —— 它的职责就是开/收作答栏（v1.9.12 起
        # 是**唯一**开关：标题栏的小 ✓ 已删，避免"两个按钮一个功能"）。
        self.b_judge.setText("判题")
        self.b_judge.refresh()
        ic = Icons.chevron(col2, size=int(13 * self._scale), down=False)
        # 关闭按钮：和标题栏三键同款矢量图标。
        # 之前这里是手搓的 16px 画布 + 13px iconSize，线宽缩完基本看不见。
        sz_close = max(14, int(16 * self._scale))
        self.b_close.setIcon(self._close_icon(col2, sz_close))
        self.b_close.setIconSize(QSize(sz_close, sz_close))
        self.b_close.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                border-radius: {int(7*self._scale)}px;
                padding: 0px;
            }}
            QPushButton:hover {{ background: rgba(229,72,77,0.84); }}
        """)
        # 普通工具按钮图标
        # 关键：必须显式 padding:0。否则 QPushButton 会把 32×28 的固定尺寸
        # 全部分配给边框与内边距，留给图标的空间被压成 0 —— 表现就是
        # 按钮有形无图（一排空白方块，已实测复现）。
        for b in (self.b_undo, self.b_redo, self.b_clear, self.b_fit,
                  self.b_1to1, self.b_save):
            b.setIcon(self._qicon(b._icon, T.P["ink2"],
                                  **getattr(b, "_icon_kw", {})))
            b.setIconSize(QSize(int(19 * self._scale), int(19 * self._scale)))
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {T.P['input_bg']};
                    border: 1px solid {T.P['input_border']};
                    border-radius: {int(9*self._scale)}px;
                    padding: 0px;
                }}
                QPushButton:hover {{
                    background: {T.P['panel_hover']};
                    border: 1px solid rgba(59,125,232,0.6);
                }}
            """)

    def _close_icon(self, color, size=None):
        """关闭图标：复用 ui_kit 的矢量图标。

        早期这里手搓画布（Icons._canvas 拿到 16px 画布直接画 24 网格的线），
        画布尺寸和坐标系对不上，再被 setIconSize(13,13) 一缩，线宽就没了 ——
        表现是"关闭按钮不显示"。
        """
        sz = max(14, int(size or 16 * self._scale))
        return _pm_to_icon(Icons.close(color, size=sz))

    def _qicon(self, name, color, size=None, **kw):
        sz = size or int(16 * self._scale)
        return _pm_to_icon(getattr(Icons, name)(color, size=sz, **kw))

    # ---------------------------------------------------- 生命周期
    def closeEvent(self, ev):
        if self.isVisible() and animations_enabled() and \
                close_window_animated(self, self._finish_animated_close):
            ev.ignore()
            return
        # 关窗 = 这次练习没看够时间，让题库页把还没到期的记账掐掉
        try:
            self.closed.emit()
        except RuntimeError:
            pass
        super().closeEvent(ev)

    def hideEvent(self, ev):
        motion = getattr(self, '_answer_size_motion', None)
        if motion is not None:
            motion.stop()
        super().hideEvent(ev)

    def _finish_animated_close(self):
        try:
            self.closed.emit()
        except RuntimeError:
            pass

    def theme_changed(self, pref=None):
        old = T.PALETTES.get(self._theme_mode, T.PALETTES["light"])
        for widget in self.findChildren(QWidget):
            style = widget.styleSheet()
            if old.get("glass_soft_edge") and old["glass_soft_edge"] != T.P["glass_soft_edge"]:
                style = style.replace(old["glass_soft_edge"], T.P["glass_soft_edge"])
            widget.setStyleSheet(style)
        for label in self.findChildren(QLabel):
            style = label.styleSheet()
            for key in ("ink", "ink2", "ink3", "ok_fg", "warn_fg",
                        "err_fg", "accent_fg"):
                before, after = old.get(key), T.P[key]
                if before and before != after:
                    style = style.replace(before, after)
            label.setStyleSheet(style)
        self._theme_mode = T.mode
        self.card.setStyleSheet(
            __import__("ui_kit").glass_style("GlassStrong", radius=20))
        refresh_glass(self.card)
        self.apanel.theme_changed(pref)
        self._sync_top_btn()
        self._refresh_tool_styles()
        self._refresh_colors()
        self.set_pen_width(self.pen_width)
        for button in (self.b_ref, self.b_done, self.b_judge,
                       self.b_prev, self.b_next):
            button.refresh()
        reference = getattr(self, "_ref", None)
        if reference is not None:
            reference.refresh_theme()
        self.board.update()
        self.update()

    def show_animated(self):
        show_window_animated(self)

    # ---------------------------------------------------- 状态
    def set_question(self, qid, source, pm, q=None):
        self._qid = qid
        if q is not None:
            self._cur_q = q
        self.cap.setText("题卡 · 第 %d 题　%s" % (qid, source))
        self.board.set_image(pm)
        self._refresh_tool_styles()
        self._refresh_colors()
        self._sync_done()
        if q is not None:
            self.apanel.set_question(q)
        # 参考答案浮层开着的时候跟着换题（否则会停留在上一题的结论上）
        ref = getattr(self, "_ref", None)
        if ref is not None and ref.isVisible():
            ref.set_question(self._cur_q, keep_pos=True)

    def _apply_answer_width(self, on):
        """展开 / 收起作答栏时同步窗口宽度。

        题卡画板和 380px 的作答栏挤在 1000px 里，画板会被压得没法看，
        所以展开时要加宽；加完再用 _fit_screen 压回屏幕可用区，
        免得在 1366×768 这类小屏上按钮跑到屏幕外。
        """
        if self.isMaximized() or self.isFullScreen():
            return
        if on:
            if self._w_before is None:
                self._w_before = self.width()
            w, h = _fit_screen(self._w_before + int(AnswerPanel.W * self._scale),
                               self.height())
        elif self._w_before:
            w, h = self._w_before, self.height()
        else:
            return
        motion = getattr(self, '_answer_size_motion', None)
        if motion is None:
            motion = QVariantAnimation(self)
            motion.setDuration(520)
            motion.setEasingCurve(QEasingCurve.InOutCubic)
            motion.valueChanged.connect(lambda size: self.resize(size))
            motion.finished.connect(self._finish_answer_size)
            self._answer_size_motion = motion
        motion.stop()
        if not animations_enabled() or not self.isVisible():
            self.resize(w, h)
            self._finish_answer_size()
            return
        motion.setStartValue(self.size())
        motion.setEndValue(QSize(w, h))
        motion.start()

    def _finish_answer_size(self):
        if not self._ans_on:
            original = self._w_before
            if original is None:
                return
            def restore():
                if not self._ans_on and self._w_before == original:
                    # 隐藏作答栏后先更新布局下限，再恢复宽度；避免旧下限卡住窗口。
                    self.layout().activate()
                    self.resize(original, self.height())
                    self._w_before = None
            QTimer.singleShot(0, self, restore)

    def toggle_answer(self):
        """展开 / 收起右侧作答栏。"""
        on = not getattr(self, "_ans_on", False)
        self._ans_on = on
        from motion_polish import fold
        fold(self.apanel, on, horizontal=True)
        self._sync_top_btn()
        self._apply_answer_width(on)

    def _on_judged(self, res):
        """答对就自动记为已做（由 _record_judge 内部调用）。"""
        if not res or res.get("partial_grading") or not res.get("ok") or self._qid is None:
            return
        if not PG.is_done(self._qid):
            PG.mark(self._qid)
        self._sync_done()
        self.done_changed.emit(int(self._qid), True)
        from flight_motion import show_completion_stamp
        show_completion_stamp(self.b_done)

    def _on_manual_judged(self, res):
        """手填「提交判题」的统一入口 —— 与自动扫描同一条记账链路。

        ★ v1.9.10 修：原来只连 _on_judged，判错直接被 return 掉，
          手填判错的题**不进错题集**；现在统一走 _record_judge
          （判对→标已做，判错→收错题集，两条路账目一致）。
        """
        self._maybe_learn_reference(res)
        self._record_judge(res, src="manual")

    def _maybe_learn_reference(self, res, task=None):
        """作答通过后再用题面全约束独立复核，避免部分判对污染参考答案。"""
        if not res or res.get("partial_grading") or not res.get("ok") or self._cur_q is None:
            return
        try:
            review = RL.learn_from_attempt(
                self._cur_q,
                user_points=res.get("_submission_points") or res.get("_gcs_user_points"),
                user_params=res.get("_submission_params") or res.get("_gcs_user_params"),
                user_extras=res.get("_submission_extras") or res.get("_gcs_user_extras"),
                task=task)
        except Exception as exc:
            log.warning("作答参考答案核验失败 #%s: %r", self._qid, exc)
            return
        res["reference_review"] = review.get("status")
        res["reference_reason"] = review.get("reason")
        if review.get("status") == "SAVED":
            res["reference_saved"] = True
            self._on_ref_saved(int(self._qid))
            if self._ref is not None and self._ref.isVisible():
                self._ref.set_question(self._cur_q, keep_pos=True)

    # ---------------------------------------------------- 判题
    def auto_scan(self):
        """「自动扫描」：读地面站里画好的航线，判**当前这道题**。

        与「判题」按钮的分工（v1.9.5 重新划定）：

        - **「判题」**（底栏）→ 打开/收起右侧作答栏，就是"手动判题界面"的入口，
          不再兼职"扫盘"。
        - **「自动扫描」**（作答栏底部，紧挨「提交判题」）→ 去读地面站数据，
          读到的值填进上面那些输入框，然后**顺便判一次**当前题。

        为什么挪进作答栏：用户点"判题"是想判题，不是想扫盘。扫盘是**作答栏内部
        的一个动作**（把数据"填进来"），放在填答案的地方才说得通。
        """
        res, head, fail, task = self._try_gcs_judge()

        if res is not None:
            self.last_gcs = res
            # ★ 先把读到的值摊进作答栏（可见可改），再弹判题窗。
            # 顺序不能反：判题窗是"结论"，作答栏是"依据"。结论盖在依据上，
            # 用户想知道"你读到了什么"就得先关窗，很容易就不看了 ——
            # 而这份依据恰恰是他唯一能核实"软件有没有读错"的东西。
            n_pt, n_pm, n_ex = self._fill_answer_from_gcs(res)
            self._maybe_learn_reference(res, task=task)
            self._show_judge(res, qhead=head)
            self._record_judge(res, src="linkgcs")
            if n_pt or n_pm or n_ex:
                self._toast("已把读到的 %d 个坐标点、%d 个参数、%d 个选项"
                            "填进作答栏，可以改完再点「提交判题」。"
                            % (n_pt, n_pm, n_ex), ms=4200)
            return

        # 读不到 —— 如实说一句，作答栏保持可填（不弹报错窗打断）
        self._toast(fail or "读不到地面站的任务，请手动填写答案。", ms=4200)

    def judge_smart(self):
        """底栏「判题」按钮：开/收右侧作答栏（手动判题界面）。

        v1.9.12 起是作答栏的**唯一开关**（标题栏的小 ✓ 已删）：
        没开就开；开着就收。名字保持「判题」不变 —— 用户认的是这两个字。
        扫盘在作答栏里（「自动扫描」按钮）。
        """
        # 为什么不再是"点一下直接扫盘"：早先这个按钮文案会在
        # 「判题 / 收起判题」之间切换，一套动作两个名字，用户根本认不出
        # 哪个是扫盘入口（五三原话：「扫盘判题叫做收起判题，谁能知道
        # 这是扫盘判题？」）。按钮名字和它干的事对不上，等于没有入口。
        if getattr(self, "_ans_on", False):
            self.toggle_answer()
        else:
            self.toggle_answer()
            self._toast("在右侧填答案，或点「自动扫描」读地面站里画好的航线。",
                        ms=3600)

    def _fill_answer_from_gcs(self, res):
        """把 GCS 读到的值回填进作答栏，并把作答栏展开。

        只做两件事，且都不该抛异常把判题流程带崩（回填失败最多是用户
        手填，判题结果本身已经拿到了）：

        1. 切到"实际判的那道题"的表单 —— 万一之前界面开着别的题，
           表单里的字段名跟这次判的题对不上，直接填会填到空处。
        2. 回填 + 展开。

        返回 ``(点数, 参数数, 选项数)``。
        """
        try:
            qid = res.get("_qid_judged")
            q = self._cur_q
            # 表单结构跟着题走：拿到的题号跟当前表单不一致时，先切表单，
            # 否则 _pt_edits 里没有对应的点名，回填会静默漏掉好几项。
            if q is not None and qid is not None and \
                    int(q.get("id", -1)) != int(qid):
                q = self._question_by_id(qid) or q
                self.apanel.set_question(q)
            n = self.apanel.fill_from(res)
        except Exception:
            return 0, 0, 0
        try:
            if not getattr(self, "_ans_on", False):
                self.toggle_answer()
        except Exception:
            pass
        return n

    def _question_by_id(self, qid):
        """按题号在题库全量里找题（找不到返回 None）。"""
        try:
            qid = int(qid)
            for q in (self._all_questions() or []):
                if int(q.get("id", -1)) == qid:
                    return q
        except Exception:
            pass
        return None

    def _try_gcs_judge(self):
        """静默尝试「读 LinkGCS2 判题」。

        返回 ``(结果, 来源说明, 失败原因, 附加信息)``：

        - 成功 → ``(res, head, "", None)``
        - 失败 → ``(None, "", 能照做的原因, None)``

        这个函数不弹窗，由调用方决定怎么展示（现在只有 :meth:`auto_scan` 用）。

        ★ **判的永远是当前打开的那道题** —— 读到的数据是从哪来的无所谓。
        五三的原话："我看着第 3 道题就只做第三道题，你比对我做出来的数据跟
        题库数据对不对得上就行。"
        """
        try:
            db = LG.find_db()
        except Exception as e:
            return None, "", "读地面站数据目录出错：%s" % e, None
        if db is None:
            return None, "", ("没找到地面站的任务数据库 —— "
                              "可以在「设置」里选地面站类型、扫盘或手动指定位置。"), None

        try:
            tasks = LG.load_tasks(db_path=db)   # ★ v1.9.9：整库列出（新→旧）
        except Exception as e:
            return None, "", "读地面站数据库出错：%s" % e, None
        if not tasks:
            return None, "", ("地面站里还没保存过带航点的任务 —— "
                              "先在那儿画好航线并点保存。"), None

        # 题目：**一律用当前打开的这道**。
        #
        # 走过一段弯路：早先这里是"认出的题 ≠ 当前题就拦停问用户"，起因是
        # 记账串题（判 A 记 B）。但拦停解决的是记账问题，不该拦到判题本身 ——
        # 五三的用法很直白：「我看着第 3 题，就只做第 3 题，你拿我做出来的数据
        # 跟第 3 题的标准答案比就完了」，至于那份数据是从哪来的（第 1 题的航线、
        # 手画的、随手编的）根本不重要。
        #
        # 所以改成：**照判当前题**。对不上就对不上，该判错判错、该进错题集进错题集。
        # "读到的数据其实属于另一道题"只作为**一行提示**告知，不作拦停。
        # 记账用 self._qid（当前题），语义自洽：他做的就是他看着的那道题。
        q = self._cur_q
        if q is None or not isinstance(q, dict) or q.get("id") is None:
            return None, "", "当前没有打开的题目。", None

        # ★ v1.9.9：**挑任务** —— 优先取"起飞点跟当前题吻合"的最新一条。
        #
        # 根因（五三实测）："在地面站里切换任务"**不会写数据库** —— 库里
        # rowid 最大的永远是最后点了保存的那条。他从第 3 题切回第 1 题、
        # 地面站里也把第 1 题的任务加载出来了，但只要没重新点保存，
        # 旧的"取最新一条"读到的仍是第 3 题的航线 → 回填全不对。
        # 现在整库列出、按起飞点反查：吻合就取吻合的最新一条；
        # 一条都不吻合才回落到最新一条（保持"照判当前题"的既有语义）。
        task = None
        for _t in tasks:
            if self._q_matches(q, _t):
                task = _t
                break
        matched = task is not None
        if task is None:
            task = tasks[0]
        if not task.get("points"):
            return None, "", ("地面站里还没保存过带航点的任务 —— "
                              "先在那儿画好航线并点保存。"), None

        try:
            ans = AN.derive_for_grading(q)
            res, meta = LG.judge_task(q, task, ans)
        except Exception as e:
            return None, "", "判题时出错：%s" % e, None

        nm = task.get("name") or "未命名"
        sta = task.get("station_name") or "地面站"
        # 只有 LinkGCS2 是实测过的 —— 其余几家字段靠通用规则猜，
        # 必须在来源条里说出来，否则用户看到"停留时间没读到"会以为是自己漏填。
        if not (LG.station(task.get("station") or "linkgcs2") or {}).get(
                "verified", True):
            sta += "（字段名未实测，个别项可能读不到）"
        head = "数据来自%s的任务「%s」（#%s %s）" % (
            sta, nm, task.get("rowid"), task.get("create_date") or "")
        # 来源条如实说明"这份任务是挑出来的"：
        if matched and len(tasks) > 1:
            head += "　·　已从 %d 条任务里挑出与本题起飞点吻合的一条" % len(tasks)
        elif not matched:
            head += ("　·　⚠ 库里 %d 条任务都没有跟本题起飞点吻合的，"
                     "用了最新一条（如果这是别题的航线，结果仅供参考）"
                     % len(tasks))
        res["_gcs_head"] = head
        res["_qid_judged"] = q.get("id")   # 判的是哪道题，记账按它来
        res["_open_qid"] = self._qid       # 当时界面开着哪道题
        res["_gcs_other_qid"] = None
        return res, head, "", task

    def _record_judge(self, res, src="manual"):
        """判完统一记账：对了标已做 + 记进通过；错了收进错题集。

        手填判题走的是 ``AnswerPanel.judged`` 信号 → ``_on_judged``，
        这里给自动扫描这条路径用，保证两条路记账一致。
        顺手用轻提示给一句结果反馈（判题窗已经写了详情，这里只补一句
        "账怎么记的" —— 用户关掉判题窗之后，还得知道这题进没进错题集）。

        ★ 记到 ``res["_qid_judged"]`` 头上。v1.9.5 后这条路径**永远判当前题**
        （``_try_gcs_judge`` 不再反查别道题），所以它恒等于 ``self._qid``；
        保留这个字段是为了"判的是哪道题"在数据里显式可查，不靠隐式约定。
        """
        qid = res.get("_qid_judged") if res else None
        if qid is None:
            qid = self._qid
        if res is None or qid is None:
            return
        qid = int(qid)
        if res.get("partial_grading"):
            self._toast("本次只核验了部分条件，未记录整题通过或错题。")
            return
        if not res.get("judgeable", True):
            # 无法自动判分的题不该往错题集里塞 —— 那不是他做错了
            self._toast("这道题没法自动判分，没有收进错题集。")
            return
        try:
            if res.get("ok"):
                PG.mark_judge(qid, True, src=src)
                self._on_judged(res)
                if res.get("reference_saved"):
                    self._toast("✅ 作答正确，完整航线已核验并收录为参考答案。",
                                ms=4200)
                else:
                    self._toast("✅ 做题正确，已记为已做。")
            else:
                PG.mark_judge(qid, False, src=src,
                              detail=self._wrong_digest(res))
                self._toast("已收进错题集，可在题库页筛「只看错题」复习。",
                            ms=3400)
            self.wrong_changed.emit()   # ★ 判对/判错都动了错题账，通知统计卡刷新
        except Exception:
            # 记错题失败不该挡判题结果展示
            pass

    @staticmethod
    def _wrong_digest(res):
        """把判错项压成一行摘要，存进错题集用。"""
        bad = []
        for it in (res.get("points") or []):
            if not it.get("ok") and not it.get("skipped"):
                bad.append("%s(偏差 %s)" % (it.get("name"),
                                           (it.get("diff") or "").replace(
                                               "偏差 ", "")))
        for it in (res.get("params") or []):
            if not it.get("ok") and not it.get("skipped"):
                bad.append("%s(你 %s / 应为 %s)" % (
                    it.get("meaning") or it.get("key"),
                    it.get("your"), it.get("want")))
        for it in (res.get("extras") or []):
            if not it.get("ok") and not it.get("skipped"):
                bad.append("%s(你 %s / 应为 %s)" % (
                    it.get("name"), it.get("your"), it.get("want")))
        return "；".join(bad[:6])

    def _toast(self, text, ms=2600):
        """在窗口内浮一条轻提示（不打断操作，也不需要点确定）。

        用它而不是弹窗：回落手填是**正常路径**，弹个"读取失败"的窗
        会让用户以为出了故障。
        """
        try:
            from ui_kit import Toast
        except Exception:
            return
        try:
            self._toast_win = Toast(self, text, scale=self._scale, ms=ms)
            self._toast_win.pop()
        except Exception:
            pass

    def _q_matches(self, q, task):
        """当前这道题的起飞点是否跟任务吻合（不吻合说明用户看的是别道题）。"""
        tk = task.get("takeoff")
        if not tk:
            return False
        try:
            _r, blat, blon = AN.extract_base(
                "%s %s" % (q.get("conditions_text") or "",
                           q.get("requirements_text") or ""))
        except Exception:
            return False
        if blat is None or blon is None:
            return False
        d = geo.distance_of(blat, blon, tk[0], tk[1])
        return d is not None and d <= LG.BASE_TOL_M

    def _all_questions(self):
        """拿当前用户导入的题卡。"""
        try:
            if self.qbank_cb is not None:
                return self.qbank_cb() or []
        except Exception:
            pass
        return self._bank or []

    def _gcs_alert(self, text):
        """读数据失败时的提示窗口。复用判题弹窗的错误模式。"""
        self._dlg = JudgeDialog(None, parent=self, scale=self._scale,
                                errors=[text])
        self._dlg.show_at_parent()

    def _show_judge(self, res, qhead=""):
        """弹判题结果窗（把引用挂住，否则会被 GC 掉闪一下就没）。

        标题里的题号用 ``_qid_judged``（**实际判的那道题**），不是当前开着的题 ——
        否则用户看到"第 2 题 · 做题正确"，会以为他第 2 题做对了。
        """
        qid = res.get("_qid_judged") if res else None
        if qid is None:
            qid = self._qid
        self._dlg = JudgeDialog(res, parent=self, scale=self._scale,
                                qid=qid, gcs_note=qhead)
        self._dlg.show_at_parent()

    # ---------------------------------------------------- 参考答案
    def toggle_ref(self):
        """弹出 / 收起参考答案浮层。

        ★ 为什么是浮层、还默认不弹：五三明确要求"要点击才能看到，
          放进题卡里，避免还没做了就已经知道答案了"。
        ★ 浮层必须持引用（``self._ref``），否则无父窗口的顶层 QWidget
          会被 GC 掉 —— 跟判题窗同一个坑。
        """
        from ref_panel import RefAnswerPanel
        if self._ref is None:
            self._ref = RefAnswerPanel(parent=None, scale=self._scale,
                                       on_save=self._on_ref_saved,
                                       on_restore=self._on_ref_restored,
                                       on_settings=getattr(self, "settings_cb", None))
        if self._ref.isVisible():
            self._ref.hide()
            return
        self._ref.set_question(self._cur_q)
        # 贴着工作台右侧展开（题卡右边），别挡住题卡本身
        try:
            g = self.frameGeometry()
            x = min(g.right() + 8,
                    self._ref.screen().availableGeometry().right()
                    - self._ref.width() - 12)
            y = max(g.top() + 60,
                    self._ref.screen().availableGeometry().top() + 12)
            self._ref.move(x, y)
        except Exception:
            pass
        self._ref.show()
        self._ref.raise_()

    def _on_ref_saved(self, qid):
        """用户标注了答案 —— 题库页那边的列表标记要跟着刷新。"""
        try:
            if self.ref_saved_cb:
                self.ref_saved_cb(int(qid))
        except Exception:
            pass

    def _on_ref_restored(self, question):
        """恢复题目版本后，同步练习窗口里的题面、图片和参考解答。"""
        if not isinstance(question, dict):
            return
        try:
            import bankdata as BD
            qid = int(question.get("id"))
            self._qid = qid
            self._cur_q = question
            source = str(question.get("source") or "")
            self.cap.setText("题卡 · #%d %s" % (qid, source))
            image = QPixmap(BD.fig_path(qid))
            if not image.isNull():
                self.board.set_image(image)
            self.apanel.set_question(question)
            self._refresh_colors()
            self._on_ref_saved(qid)
        except Exception:
            log.exception("同步恢复后的题卡版本失败")

    def toggle_done(self):
        """标记 / 取消标记「已做」"""
        if self._qid is None:
            return
        on = PG.toggle(self._qid)
        self._sync_done()
        self.done_changed.emit(int(self._qid), bool(on))
        if on:
            from flight_motion import show_completion_stamp
            show_completion_stamp(self.b_done)

    def _sync_done(self):
        """刷新底栏的标记按钮与练习历史文案"""
        done = PG.is_done(self._qid) if self._qid is not None else False
        rec = PG.info(self._qid) if self._qid is not None else {}
        self.b_done.setText("已做（点此取消）" if done else "标记已做")
        self.b_done._icon_name = "check"
        self.b_done._kind = "primary" if done else "default"
        self.b_done.set_checked_style(False)
        self.b_done.refresh()

        n = int(rec.get("count", 0) or 0)
        last = rec.get("last", "")
        if not n:
            self.hist_lb.setText("尚未练习过")
        elif last:
            # 只显示到分钟，年月太占地方
            short = last[5:] if len(last) >= 16 else last
            self.hist_lb.setText("已练习 %d 次　·　最近 %s" % (n, short))
        else:
            self.hist_lb.setText("已练习 %d 次" % n)

    def set_tool(self, tool):
        self.board.set_tool(tool)
        self._refresh_tool_styles()

    def _refresh_tool_styles(self):
        for b, key in ((self.b_pan, "pan"), (self.b_pen, "pen"),
                       (self.b_era, "eraser")):
            on = (self.board.tool == key)
            b.setChecked(on)
            b.setIcon(self._qicon(b._icon, "#ffffff" if on else T.P["ink"]))
            b.setIconSize(QSize(int(15 * self._scale), int(15 * self._scale)))
            b.setStyleSheet(btn_style("default", checked=on, radius=9,
                                      font_size=13, scale=self._scale))

    def set_color(self, c):
        """选择笔色。

        【必须同步到 board】这就是用户报的"只能绘制黑色，其他颜色不可用"：
        色块的高亮圈确实变了，但真正落笔用的 board.pen_color 从头到尾都是
        初始值 PEN_COLORS[0]（#111827，近黑）—— 因为这里以前只写了窗口
        自己的 self.pen_color，从没传给画板。
        """
        self.pen_color = c
        self.board.pen_color = c
        self._refresh_colors()

    def _refresh_colors(self):
        for cb in self.color_btns:
            sel = (cb._color == self.pen_color)
            ring = T.C_BLUE if sel else T.P["glass_soft_edge"]
            w = 3 if sel else 1
            cb.setStyleSheet(f"""
                QPushButton {{
                    background: {cb._color};
                    border: {w}px solid {ring};
                    border-radius: {int((6 if T.app_theme == 'default' else 12) * self._scale)}px;
                }}
            """)

    def _width_icon(self, w, color=None):
        """笔宽按钮的图标：按真实粗细画一条横线。

        16px 档在 26px 高的按钮里也放得下，所以预览按 1:1 画 ——
        用户看到的粗细就是落笔后的粗细。
        """
        col = color or T.P["ink"]
        sz = max(18, int(30 * self._scale))
        hh = max(16, int(20 * self._scale))
        pm = QPixmap(sz, hh)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        lw = max(1.5, min(float(w) * self._scale, hh - 4.0))
        p.setPen(QPen(QColor(col), lw, Qt.SolidLine, Qt.RoundCap))
        y = hh / 2.0
        p.drawLine(QPointF(sz * 0.16, y), QPointF(sz * 0.84, y))
        p.end()
        return _pm_to_icon(pm)

    def set_pen_width(self, w):
        """设置笔触粗细（与 set_color 同理：必须同步到 board 才生效）"""
        self.pen_width = w
        self.board.pen_width = w
        for b in self._width_btns:
            on = (int(getattr(b, "_w", 0)) == int(w))
            b.setIcon(self._width_icon(getattr(b, "_w", w),
                                       "#ffffff" if on else T.P["ink"]))
            b.setIconSize(QSize(int(30 * self._scale), int(20 * self._scale)))
            # padding 必须显式归零：否则 40×26 的固定尺寸会被边框与内边距
            # 吃光，留给图标的空间变成 0 —— 表现是"一排空白方块"。
            b.setStyleSheet(btn_style("default", checked=on, radius=8,
                                      font_size=12, scale=self._scale,
                                      extra="QPushButton { padding: 0px; }"))

    # ---------------------------------------------------- 动作
    def toggle_top(self):
        self._topmost = not self._topmost
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self._topmost)
        self.show()
        self._sync_top_btn()

    def do_undo(self):
        self.board.undo()

    def do_redo(self):
        self.board.redo()

    def do_clear(self):
        self.board.clear_marks()

    def do_fit(self):
        self.board.fit()

    def do_1to1(self):
        self.board.actual_size()

    def do_save(self):
        pm = self.board.compose()
        if pm is None:
            return
        cap = self.cap.text().replace("题卡 · ", "").replace("　", "_")
        dflt = os.path.join(
            os.path.expanduser("~"), "Desktop",
            "%s_%s.png" % (cap, datetime.datetime.now().strftime("%H%M%S")))
        path, _ = QFileDialog.getSaveFileName(
            self, "导出题卡（含批注）", dflt, "PNG 图片 (*.png)")
        if not path:
            return
        if self.board.save_png(path):
            QMessageBox.information(self, "导出成功", "题卡已保存到：\n" + path)
        else:
            QMessageBox.critical(self, "导出失败", "图片写入失败，请换个目录再试。")

    def step(self, delta):
        if callable(self.step_cb):
            self.step_cb(delta)

    # ---------------------------------------------------- 标题栏拖拽
    def _t_press(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _t_move(self, ev):
        if self._drag and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag)

    def _t_release(self, ev):
        self._drag = None

    def showEvent(self, ev):
        super().showEvent(ev)
        self._sync_resize_handles(raise_handles=True)
        # 打开题卡时控件尺寸还没定下来（set_question 早于 show），
        # fit() 会按一个偏小的中间尺寸算 —— 结果是题卡只占版面一角、
        # 四周一大片空白；窗口偏小时又会溢出被裁。所以显示之后补两次
        # 整页适配（0ms 抢在首帧、60ms 等布局彻底收敛）。
        QTimer.singleShot(0, self.board.autofit)
        QTimer.singleShot(60, self.board.autofit)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._sync_resize_handles(raise_handles=True)

    def _sync_resize_handles(self, raise_handles=False):
        if not hasattr(self, "_resize_handles"):
            return
        sync_corner_resize_handles(self, self._resize_handles,
                                   raise_handles=raise_handles)


def _pm_to_icon(pm):
    """QPixmap -> QIcon。直接转交 ui_kit.pm_to_icon。

    早期这里自己做 DPR 搬运（把 3 倍图缩回 1 倍来"修复空白按钮"），
    其实真正的病根在 Icons._canvas 的坐标变换 —— 那套搬运是白忙，
    还额外引入一层缩放误差。现在 ui_kit 保证返回图「逻辑尺寸 == 物理
    尺寸」，包成 QIcon 即可。
    """
    return pm_to_icon(pm)
