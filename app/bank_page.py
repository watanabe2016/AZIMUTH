# -*- coding: utf-8 -*-
"""导入题卡页 —— 文件题卡检索与工作台（绘制/橡皮擦/导出/置顶/缩放）"""

import os
import json
import random
import re
from motion_polish import accent, locate_item
from collections import OrderedDict

from PySide6.QtGui import (QPixmap, QColor, QFont, QPainter, QPainterPath, QPen,
                           QDesktopServices, QRegion, QImage, QImageReader,
                           QShortcut, QKeySequence)
from PySide6.QtCore import (Qt, QSize, QTimer, QPoint, QPointF, QRect, QRectF, Signal, QUrl,
                             QPropertyAnimation, QEasingCurve,
                             QParallelAnimationGroup, QVariantAnimation,
                              QObject, QRunnable, QThreadPool, QEvent, Slot, Property)
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
                               QPushButton, QLineEdit, QTextBrowser, QSplitter,
                               QListWidget, QListWidgetItem, QSizePolicy,
                               QMessageBox, QScrollArea, QFrame,
                                QFileDialog, QMenu, QComboBox,
                               QGraphicsOpacityEffect, QGraphicsDropShadowEffect,
                               QApplication, QTextEdit)

import theme as T
from industrial_details import (IndustrialMatrixStamp, IndustrialPageFooter,
                                IndustrialPageMeta)
from anna_art import AnnaHeaderArt
from chiyo_art import ChiyoMarginNote, ChiyoBinderClip, ChiyoCornerDots
import config as cfg
import progress as PG
import notes as NT
import bankdata as BD
import importer as IM
import qtag
import qmeta as QM
import geometry
from paper_sections import paper_sections
from workflow_feedback import import_report
from wrong_dialog import WrongSetDialog
from menu_interaction import MenuSelectionGate, should_dismiss_menu
from logger import get_logger
from ui_kit import (glass, glass_style, refresh_glass, btn_style, IconButton,
                    Combo, attach_shadow, replay_shadow, place_near,
                    animations_enabled,
                    show_window_animated, AnchoredPopupMotion)
from motion_runtime import motion_page

log = get_logger()


class ImportDropTarget(QFrame):
    """可点击选择或拖入题卡文件的导入入口。"""

    files_dropped = Signal(list)
    files_requested = Signal()
    SUPPORTED = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ImportDropTarget")
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(46)
        self.setMaximumHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击选择多个 PDF 或图片，也可以把文件拖到这里。")
        self.label = QLabel("拖入 PDF 或题卡图片，或点击选择文件（支持多选）", self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.addWidget(self.label)
        self.refresh_theme()

    @classmethod
    def _paths(cls, event):
        return [url.toLocalFile() for url in event.mimeData().urls()
                if url.isLocalFile() and
                os.path.splitext(url.toLocalFile())[1].lower() in cls.SUPPORTED]

    def dragEnterEvent(self, event):
        if self._paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self._paths(event)
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.files_dropped.emit(paths)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.files_requested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                           Qt.Key.Key_Space):
            self.files_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def refresh_theme(self):
        self.setStyleSheet(
            f"QFrame#ImportDropTarget{{background:{T.P['panel_soft']};"
            f"border:1px dashed {T.P['glass_soft_edge']};border-radius:9px;}}"
            f"QFrame#ImportDropTarget:hover,QFrame#ImportDropTarget:focus{{"
            f"background:{T.P.get('panel', T.P['panel_soft'])};"
            f"border:1px solid {T.C_BLUE};}}")
        self.label.setStyleSheet(
            f"font-size:11px;color:{T.P['ink3']};background:transparent;border:0;")


class _FigureSignals(QObject):
    """把工作线程解码结果安全送回界面线程。"""
    ready = Signal(int, str, object)


class _FigureDecodeTask(QRunnable):
    """异步读取并缩放单张题卡；过期任务可在开始前跳过。"""
    def __init__(self, revision, path, target, cancelled, fit_mode="page"):
        super().__init__()
        self.revision = revision
        self.path = path
        self.target = target
        self.cancelled = cancelled
        self.fit_mode = fit_mode
        self.error = ""
        self.signals = _FigureSignals()

    def run(self):
        image = QImage()
        try:
            if not self.cancelled():
                reader = QImageReader(self.path)
                reader.setAutoTransform(True)
                image = reader.read()
                if image.isNull():
                    self.error = reader.errorString() or "题卡图片格式损坏或无法读取。"
                if not image.isNull() and not self.cancelled():
                    width, height = image.width(), image.height()
                    aw, ah = self.target
                    scale = aw / float(width)
                    if self.fit_mode == "page":
                        scale = min(scale, ah / float(height))
                    size = QSize(max(1, round(width * scale)),
                                 max(1, round(height * scale)))
                    if size != image.size():
                        image = image.scaled(size, Qt.KeepAspectRatio,
                                             Qt.SmoothTransformation)
                    if self.cancelled():
                        image = QImage()
        except Exception as exc:
            self.error = str(exc) or "图片解码失败。"
            image = QImage()
        finally:
            # 即使任务作废也通知 GUI 清理持有的包装对象和缓存引用。
            self.signals.ready.emit(self.revision, self.path, image)


class _WatermarkedPaper(QWidget):
    """可滚动的广东02纸质题卡底板，水印始终保持低对比度。"""
    def paintEvent(self, event):
        painter = QPainter(self)
        # 广东02是纸面题卡；阅读环境变暗时，纸张本身仍保持白色。
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QColor(105, 129, 163, 25))
        painter.save()
        painter.rotate(-34)
        painter.setFont(QFont(T.pick_font(), 19, QFont.DemiBold))
        step_x, step_y = 300, 230
        start_x = -self.height() - 180
        for y in range(start_x, self.height() + self.width(), step_y):
            for x in range(-self.width(), self.width() * 2, step_x):
                painter.drawText(x + (step_x // 2 if (y // step_y) % 2 else 0),
                                 y, "广东地面站")
        painter.restore()
        painter.end()


class _QuestionDiagram(QWidget):
    """从原题卡提取原始图形区域，不另行绘制示意图。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.question = {}
        self.fallback = QPixmap()
        self._diagram_crop = QPixmap()
        self._source_key = None
        self._display_key = None
        self._display_image = QPixmap()
        self._model_id = None
        self._model = QPixmap()
        self.setMinimumHeight(250)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_question(self, question, fallback=None):
        self.question = question or {}
        image = fallback or QPixmap()
        key = (self.question.get("id"), image.cacheKey())
        if key != self._source_key:
            self._source_key = key
            self.fallback = image
            self._diagram_crop = self._crop_original_diagram(image)
            self._display_key = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        area = self.rect().adjusted(10, 10, -10, -10)
        source = self._diagram_crop
        if not source.isNull():
            key = (source.cacheKey(), area.width(), area.height())
            if key != self._display_key:
                self._display_image = source.scaled(area.size(), Qt.KeepAspectRatio,
                                                    Qt.SmoothTransformation)
                self._display_key = key
            image = self._display_image
            painter.drawPixmap(area.center().x() - image.width() // 2,
                               area.center().y() - image.height() // 2, image)
        else:
            painter.setPen(QColor("#53657D"))
            painter.drawText(area, Qt.AlignCenter, "题卡图片中未找到可用图形")
        painter.end()

    @staticmethod
    def _crop_original_diagram(image):
        if image.isNull():
            return QPixmap()
        width, height = image.width(), image.height()
        try:
            import numpy as np
            from diagram_crop import diagram_bounds
            from PySide6.QtGui import QImage
            gray_image = image.toImage().convertToFormat(QImage.Format_Grayscale8)
            stride = gray_image.bytesPerLine()
            pixels = np.frombuffer(gray_image.bits().tobytes(), dtype=np.uint8)
            gray = pixels.reshape((height, stride))[:, :width].copy()
            bounds = diagram_bounds(gray)
            if bounds:
                left, top, crop_width, crop_height = bounds
                return image.copy(left, top, crop_width, crop_height)
        except Exception:
            pass
        # 拼图或深色地图无法可靠分割时保留整张原题，不能用固定比例裁残。
        return image.copy()


class QuestionFrequencyRuler(QWidget):
    """题卡页顶部的调频刻度，标出当前题号与总量。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.position = 1
        self.total = 1
        self.setFixedHeight(48)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_position(self, position, total):
        self.total = max(1, int(total or 1))
        self.position = max(1, min(self.total, int(position or 1)))
        self.update()

    def paintEvent(self, event):
        if T.app_theme != "industrial":
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        accent = QColor(T.P["accent_fg"])
        muted = QColor(T.P["ink3"])
        line = QColor(T.P["glass_soft_edge"])
        left, right = 86.0, max(87.0, self.width() - 70.0)
        baseline = 26.0
        painter.setPen(muted)
        painter.setFont(QFont(T.pick_font(), 8, QFont.DemiBold))
        painter.drawText(QRectF(0, 5, 72, 34), Qt.AlignLeft | Qt.AlignVCenter,
                         "QUESTION\nPOSITION")
        painter.setPen(QPen(line, 1))
        tick_count = 240
        for index in range(tick_count + 1):
            x = left + (right - left) * index / float(tick_count)
            tall = index % 24 == 0
            medium = index % 8 == 0
            length = 27 if tall else 18 if medium else 9
            painter.drawLine(QPointF(x, baseline - length / 2),
                             QPointF(x, baseline + length / 2))
            if tall and index <= 192:
                value = 1 if index == 0 else round(index / 24.0 * 5)
                painter.setPen(muted)
                painter.setFont(QFont("Consolas", 8))
                painter.drawText(QRectF(x - 19, 32, 38, 14), Qt.AlignCenter,
                                 "%03d" % value)
                painter.setPen(QPen(line, 1))
        active = left + (right - left) * (self.position - 1) / max(
            1, self.total - 1)
        painter.setPen(QPen(accent, 2))
        painter.drawLine(QPointF(active, 6), QPointF(active, 41))
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(QPointF(active, 5), 4, 4)
        painter.setPen(accent)
        painter.setFont(QFont("Consolas", 9, QFont.Bold))
        painter.drawText(QRectF(active - 24, 32, 48, 14), Qt.AlignCenter,
                         "%03d" % self.position)
        painter.setPen(QPen(line, 1))
        painter.drawLine(QPointF(right + 20, 5), QPointF(right + 20, 43))
        painter.setPen(muted)
        painter.setFont(QFont("Consolas", 8))
        painter.drawText(QRectF(right + 29, 4, 39, 32), Qt.AlignCenter,
                         "TOTAL\n%d" % self.total)
        painter.end()


def A(*p):
    return T.asset_path(*p)


def _paint_popup_card(w):
    """弹层卡片底（ImportMenu / TagMenu 共用）。

    ★ 为什么不能用 P["input_bg"] / glass 系：它们是 CSS rgba() **字符串**，
    给 QSS 用没问题，但 paintEvent 里 QColor("rgba(...)") 解析失败 → invalid
    → 画出来整块纯黑（深色主题下黑块不显眼，浅色主题下整个菜单全黑 ——
    v1.9.11 五三浅色模式实测翻车）。自绘弹层只用实体色 dialog_bg
    （浅 #F4F8FE / 深 #131C2A，两套主题都是 #RRGGBB），圆角自己画。
    """
    P = T.P
    pt = QPainter(w)
    pt.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = w.rect().adjusted(2, 2, -2, -2)
    pa = QPainterPath()
    radius = 6 if T.app_theme == "chiyo" else 14
    pa.addRoundedRect(r, radius, radius)
    pt.setPen(Qt.PenStyle.NoPen)
    pt.setBrush(QColor(P["dialog_bg"]))
    pt.drawPath(pa)
    pt.setPen(QPen(QColor(T.P["glass_edge"]), 1.5) if T.app_theme == "chiyo"
              else QPen(QColor(255, 255, 255, 42) if T.mode == "dark"
                        else QColor("#C8D7F0"), 1))
    pt.setBrush(Qt.BrushStyle.NoBrush)
    pt.drawRoundedRect(r, radius, radius)
    pt.end()


class ImportMenu(QFrame):
    """宿主窗口内的二级菜单，沿触发按钮展开与收回。"""

    picked = Signal(str)

    def __init__(self, items, parent=None):
        host = getattr(parent.window(), "stage", None) or parent.window() if parent else None
        super().__init__(host)
        self._owner = parent
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        self._buttons = []
        for key, label, icon in items:
            button = IconButton(label, icon, "bare", font_size=13, radius=9, gap="   ")
            button.setMinimumHeight(34)
            button.clicked.connect(lambda _=False, value=key: self._pick(value))
            lay.addWidget(button)
            self._buttons.append(button)
        self._anchor_rect = QRect()
        self._anchor_widget = None
        self._motion = None
        self._closing = False
        self._selection_gate = MenuSelectionGate()
        self._outside_filter_installed = False
        self.last_pick_rect = QRect()
        self.hide()
        if parent is not None:
            parent.installEventFilter(self)

    def open_from(self, anchor, animate=True):
        self._selection_gate.reset()
        if self.isVisible() and self._motion is not None:
            self._closing = False
            self._motion.set_open(True, animate)
            return
        host = self.parentWidget()
        if host is None:
            return
        self._anchor_widget = None if isinstance(anchor, QRect) else anchor
        self._anchor_rect = (QRect(anchor) if isinstance(anchor, QRect) else
                             QRect(anchor.mapToGlobal(QPoint(0, 0)), anchor.size()))
        local = QRect(host.mapFromGlobal(self._anchor_rect.topLeft()),
                      self._anchor_rect.size())
        for button in self._buttons:
            button.refresh()
        self.adjustSize()
        size = self.sizeHint().boundedTo(host.size())
        self.setFixedSize(size)
        x = max(6, min(local.x(), host.width() - size.width() - 6))
        below = local.bottom() + 6
        from_bottom = below + size.height() > host.height() - 6
        y = max(6, local.y() - size.height() - 6) if from_bottom else below
        destination = QRect(QPoint(x, y), size)
        self._closing = False
        self._motion = AnchoredPopupMotion(self, local, destination, from_bottom)
        self._motion.finished.connect(self._motion_finished)
        self.show()
        self.raise_()
        self._install_filter()
        self._motion.set_open(True, animate)
        self._buttons[0].setFocus(Qt.PopupFocusReason)

    def toggle_from(self, anchor):
        if self.isVisible() and not self._closing:
            self.dismiss()
        else:
            self.open_from(anchor)

    def dismiss(self, animate=True):
        if self._closing or not self.isVisible():
            return
        self._closing = True
        self._motion.set_open(False, animate) if self._motion else self.hide()

    def _motion_finished(self, opening):
        if opening:
            return
        focus = QApplication.focusWidget()
        restore = focus is self or (focus is not None and self.isAncestorOf(focus))
        self.hide()
        self._closing = False
        anchor = self._anchor_widget or getattr(self._owner, "b_more", None)
        if restore and anchor is not None:
            anchor.setFocus(Qt.PopupFocusReason)
        if self._motion is not None:
            self._motion.deleteLater()
            self._motion = None

    def _install_filter(self):
        app = QApplication.instance()
        if app is not None and not self._outside_filter_installed:
            app.installEventFilter(self)
            self._outside_filter_installed = True

    def hideEvent(self, event):
        app = QApplication.instance()
        if app is not None and self._outside_filter_installed:
            app.removeEventFilter(self)
        self._outside_filter_installed = False
        if self._motion is not None:
            self._motion.stop()
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if watched is self._owner and event.type() == QEvent.Hide:
            self.dismiss(animate=False)
        if self.isVisible() and event.type() == QEvent.MouseButtonPress:
            point = event.globalPosition().toPoint()
            # 全局过滤器拿到的 watched 在部分 Qt/Windows 窗口组合下可能是宿主窗口，
            # 即使鼠标实际落在菜单按钮上也不一定是菜单子控件；优先用屏幕坐标命中菜单框。
            menu_rect = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
            event_inside_menu = (watched is self or
                                 (isinstance(watched, QWidget) and self.isAncestorOf(watched)))
            if should_dismiss_menu(
                    (point.x(), point.y()), menu_rect.getCoords(),
                    self._anchor_rect.getCoords(), event_inside_menu):
                self.dismiss()
        return False

    def closeEvent(self, event):
        event.ignore()
        self.dismiss()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            self.dismiss(animate=False)
            event.accept()
        elif key in (Qt.Key_Up, Qt.Key_Down):
            current = QApplication.focusWidget()
            index = self._buttons.index(current) if current in self._buttons else 0
            self._buttons[(index + (1 if key == Qt.Key_Down else -1)) %
                          len(self._buttons)].setFocus()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _pick(self, key):
        # ★ 用户报「错题集 / 成绩趋势 / 导入题目」点不开：原实现先 dismiss() 再 emit()，
        #   一旦收起动画/隐藏过程抛异常，业务信号永远发不出去（异常还在 Qt 槽里，
        #   外面的 try 接不到）⇒ 菜单能开、点了却没反应。这里做三件事：
        #   ① 先把业务信号发出去（选择立即生效，不依赖动画）；
        #   ② 收起动作单独 try 包住并记日志；
        #   ③ 记一条“点了什么”，便于日后排查。
        # 点击按下时外部过滤器可能已经启动收起动画；按钮的 release/click 仍须完成动作。
        # 单独用一次性标记拦重复信号，不用 _closing 吞掉这次有效选择。
        if not self._selection_gate.claim():
            return
        button = self.sender()
        if button is not None:
            self.last_pick_rect = QRect(button.mapToGlobal(QPoint(0, 0)), button.size())
        try:
            import logging as _lg
            _lg.getLogger("azimuth").info("菜单选择：%s", key)
        except Exception:
            pass
        self.picked.emit(key)
        try:
            self.dismiss()
        except Exception:
            import traceback
            try:
                import logging as _lg
                _lg.getLogger("azimuth").error("菜单收起失败：%s", traceback.format_exc())
            except Exception:
                pass
            try:
                self.hide()
            except Exception:
                pass

    def paintEvent(self, event):
        _paint_popup_card(self)



class TagMenu(QFrame):
    """右键改标签菜单（v1.9.11）—— 自绘 Qt.Popup，跟 ImportMenu 同一个坑同一种躲法。

    行为：每行一个可勾选的标签，**点一下立即生效且不关菜单**（连续改几题
    不用反复重开）。勾选逻辑分两层：
      · 勾上 —— 手动层加一条（qmeta.manual_tags）；若它先前被手动排除过，
        先把排除撤了（否则并集之后还是会被压住）。
      · 取消 —— 若这标签是**自动引擎**认出来的，光删手动层没用，必须写进
        排除层（qmeta.manual_tags_off）把它压住；纯手勾的直接删。
    关闭时发 changed(qid)，题库页收到后刷下拉/列表/筛选。
    """

    changed = Signal(int)

    def __init__(self, q, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self._q = q
        self._qid = int(q["id"])
        self._auto = set(qtag.classify(q))       # 自动认出的（判断取消语义用）
        self._on = set(QM.tags_of(self._qid))    # 手动勾选层
        self._off = set(QM.offs_of(self._qid))   # 手动排除层

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(4)

        t = QLabel("#%d · 标签（点一下即生效）" % self._qid)
        t.setStyleSheet(f"font-size:12px; font-weight:800; color:{T.P['ink2']};"
                        f"background:transparent; padding:2px 6px 6px;")
        t.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        root.addWidget(t)

        # 22 个标签一行排下去太长，塞进滚动区（最大高由调用方按屏幕夹）
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        for title, pairs in (("考点", qtag.EXAM_TAGS),
                             ("形状", qtag.SHAPE_TAGS),
                             ("结构", qtag.OTHER_TAGS)):
            cap = QLabel(title)
            cap.setStyleSheet(
                f"font-size:10px; color:{T.P['ink3']}; background:transparent;"
                f"padding:5px 6px 1px;")
            cap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            lay.addWidget(cap)
            for code, label in pairs:
                lay.addWidget(self._mk_row(code, label))
        lay.addStretch()

        sc = QScrollArea()
        sc.setWidget(inner)
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setStyleSheet("QScrollArea{background:transparent;}"
                         "QWidget{background:transparent;}")
        sc.setMaximumHeight(430)
        root.addWidget(sc)

        hint = QLabel("勾选 = 归入此分类；取消 = 移出（自动认出的会记为排除）")
        hint.setStyleSheet(f"font-size:10px; color:{T.P['ink3']};"
                           f"background:transparent; padding:4px 6px 0;")
        hint.setWordWrap(True)
        hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        root.addWidget(hint)
        self.setFixedWidth(230)
        self.adjustSize()
        self._sync_checks()

    def _mk_row(self, code, label):
        b = QPushButton(label)
        b.setCheckable(True)
        b.setProperty("tag_code", code)      # text 可能重名，勾选态按 code 对
        b.setCursor(Qt.PointingHandCursor)
        b.setMinimumHeight(27)
        b.setStyleSheet(
            f"QPushButton{{text-align:left; padding:2px 10px; border:none;"
            f"border-radius:7px; font-size:12px; color:{T.P['ink']};"
            f"background:transparent;}}"
            f"QPushButton:hover{{background:{T.P['input_bg']};}}"
            f"QPushButton:checked{{color:{T.C_BLUE};"
            f"background:{T.P['input_bg']}; font-weight:700;}}")
        b.clicked.connect(lambda _=False, c=code, ck=b: self._toggle(c, ck))
        return b

    def _rows(self):
        for b in self.findChildren(QPushButton):
            yield b

    def _sync_checks(self):
        """按三层状态刷所有行的勾选态。"""
        eff = set(qtag.tags(self._q, list(self._on), list(self._off)))
        for b in self._rows():
            b.blockSignals(True)
            b.setChecked(b.property("tag_code") in eff)
            b.blockSignals(False)

    def _toggle(self, code, btn):
        # ★ click() 发信号前**已经翻转**了 checked —— 所以 want 直接读
        #   当前值，不能再取反（取反会让真机首次点击执行"取消"分支）。
        want = btn.isChecked()
        if want:
            self._off.discard(code)              # 排除优先撤掉
            if code not in self._auto:
                self._on.add(code)               # 自动认出的不必记手动层
        else:
            self._on.discard(code)
            if code in self._auto:
                self._off.add(code)              # 自动认出的要靠排除层压住
        QM.set_tags(self._qid, sorted(self._on))
        QM.set_offs(self._qid, sorted(self._off))
        self._sync_checks()
        self.changed.emit(self._qid)

    def paintEvent(self, ev):
        _paint_popup_card(self)


class _BankPageMotionSnapshot(QWidget):
    """题库页转场缓存：动画期间只重绘一张页面快照。"""

    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self._pixmap = pixmap
        self._opacity = 1.0
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet("background:transparent;")

    def _get_opacity(self):
        return self._opacity

    def _set_opacity(self, value):
        value = max(0.0, min(1.0, float(value)))
        if abs(self._opacity - value) < 0.001:
            return
        self._opacity = value
        self.update()

    opacity = Property(float, _get_opacity, _set_opacity)

    def paintEvent(self, event):
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setOpacity(self._opacity)
        painter.drawPixmap(self.rect(), self._pixmap)


class _FilterMotionSnapshot(QWidget):
    """筛选折叠只绘制首尾缓存，题卡位移期间不反复重排和缩放原图。"""

    def __init__(self, before, after, start_y, end_y, opening, parent):
        super().__init__(parent)
        self.before, self.after = before, after
        self.start_y, self.end_y = float(start_y), float(end_y)
        self.opening, self.progress = bool(opening), 0.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WA_NoSystemBackground)

    def mousePressEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        # 动效期间仍能再次点击筛选按钮，沿当前可见状态反向接续。
        button = self.parentWidget().b_filters
        local = button.mapFromGlobal(event.globalPosition().toPoint())
        if button.rect().contains(local):
            button.click()
        event.accept()

    @property
    def current_y(self):
        return self.start_y + (self.end_y - self.start_y) * self.progress

    def advance(self, value):
        self.progress = float(value)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        y = self.current_y
        base = min(self.start_y, self.end_y)
        width, height = self.width(), self.height()

        def strip(pixmap, source_y, target_y, extent, alpha=1.0):
            if extent <= 0 or alpha <= 0:
                return
            ratio = pixmap.devicePixelRatio()
            painter.setOpacity(alpha)
            painter.drawPixmap(QRectF(0, target_y, width, extent), pixmap,
                               QRectF(0, source_y * ratio, width * ratio, extent * ratio))

        strip(self.after, 0, 0, base)
        strip(self.after if self.opening else self.before, base, base,
              max(0, y - base), self.progress if self.opening else 1 - self.progress)
        # 内容区只裁切较大状态的缓存，底部操作栏固定；避免首尾交叉淡化产生重影。
        footer = min(90, max(0, height - max(self.start_y, self.end_y)))
        body = self.before if self.start_y <= self.end_y else self.after
        source_y = min(self.start_y, self.end_y)
        painter.save()
        painter.setClipRect(QRectF(0, y, width, max(0, height - y - footer)))
        strip(body, source_y, y, height - source_y - footer)
        painter.restore()
        strip(self.after, height - footer, height - footer, footer)
        painter.end()


@motion_page
class BankPage(QWidget):
    """用户导入题卡：筛选检索 + 题卡预览 + 题卡工作台"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.all, self.view, self.cur = [], [], None
        self._detail_revision = 0
        self._detail_timer = QTimer(self)
        self._detail_timer.setSingleShot(True)
        self._detail_timer.timeout.connect(self._finish_pending_detail)
        self._pending_detail = None
        self._figure_revision = 0
        self._figure_jobs = {}
        self._figure_cache = OrderedDict()
        self._directory_anim = None
        self._directory_animating = False
        self._directory_open = False
        self._directory_target_width = 230
        self._page_motion_animation = None
        self._page_motion_effect = None
        self._page_motion_snapshot = None
        self._page_motion_kind = None
        self._page_motion_callback = None
        self._intro_prepared = False
        self._page_intro_timer = QTimer(self)
        self._page_intro_timer.setSingleShot(True)
        self._page_intro_timer.timeout.connect(self._begin_prepared_intro)
        self._return_refresh_timer = None
        self._last_list_state_signature = None
        self._directory_effects = []
        self._preview_zoom = 1.0
        self._preview_fit = "width"
        self.fig_win = None
        self.note_win = None
        self._cards = []
        self._stat_cards = []
        self._pend = None        # 延迟记账：("qid", QTimer)，未到期则由它管
        self._imp_thread = None
        self._imp_prog = None
        self._import_batch = None
        self._import_phase = "idle"
        self._last_import_paths = []
        # 启动加载时 refresh() 会选中第 1 题，那不是用户真的在看 ——
        # 用 _ready 挡住：加载完成前选中题目不自动起表，防止一开软件
        # 就开始给第 1 题偷偷计时。
        self._ready = False
        self._build()
        self._install_shortcuts()
        self.load()

    # --------------------------------------------------- 构建
    def _build(self):
        self.motion_surface = QWidget(self)
        self.motion_surface.setObjectName("BankPageMotionSurface")
        self.motion_surface.setStyleSheet("background:transparent;")
        self.motion_surface.setGeometry(self.rect())
        page = QVBoxLayout(self.motion_surface)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)
        self.anna_header_art = AnnaHeaderArt(
            "题库练习", "每一次练习，都是进步", self.motion_surface,
            page_key="question")
        self.anna_header_art.setVisible(T.app_theme == "anna")
        page.addWidget(self.anna_header_art)
        content = QWidget(self.motion_surface)
        content.setStyleSheet("background:transparent;")
        page.addWidget(content, 1)
        root = QVBoxLayout(content)
        self.root_layout = root
        root.setContentsMargins(30, 22, 30, 22)
        root.setSpacing(13)

        self.heading_widget = QWidget(self)
        head = QHBoxLayout(self.heading_widget)
        hb = QVBoxLayout()
        hb.setSpacing(2)
        t = QLabel("题库题卡")
        t.setObjectName("H1")
        s = QLabel("导入自己的 PDF 或图片题卡，在这里整理和练习")
        s.setObjectName("Dim")
        self.page_title = t
        self.sub_lb = s
        hb.addWidget(t)
        hb.addWidget(s)
        head.addLayout(hb)
        head.addStretch()
        self.industrial_header = IndustrialPageMeta(
            "EXAMINATION", "KNOWLEDGE", self)
        self.industrial_header.setVisible(T.app_theme == "industrial")
        head.addWidget(self.industrial_header, 0, Qt.AlignVCenter)
        self.chiyo_header_note = ChiyoMarginNote(
            "多练多思考，\n每一次都是进步！", width=202, height=72)
        self.chiyo_header_note.setVisible(T.app_theme == "chiyo")
        head.addWidget(self.chiyo_header_note, 0, Qt.AlignVCenter)
        self.heading_widget.setVisible(T.app_theme != "anna")
        root.addWidget(self.heading_widget)

        self.import_drop_target = ImportDropTarget(self)
        self.import_drop_target.files_dropped.connect(self._begin_import_batch)
        self.import_drop_target.files_requested.connect(self.import_files)
        root.addWidget(self.import_drop_target)

        # 统计：导入题卡总量 / 已做 / 未做 / 错题 / 当前筛选
        # 「覆盖地区」「考次」移到副标题里，把版面让给做题进度 ——
        # 用户要的是「一眼看出还有多少没做」。
        self.stats_widget = QWidget()
        stat = QHBoxLayout(self.stats_widget)
        stat.setSpacing(11)
        self.stats = {}
        for key, label, col in (("total", "题卡总量", "accent_fg"),
                                ("done", "已练", "ok_fg"),
                                ("todo", "未练", "warn_fg"),
                                ("wrong", "错题", "err_fg"),
                                ("shown", "当前筛选", None)):
            c = glass("GlassSoft")
            cl = QHBoxLayout(c)
            cl.setContentsMargins(12, 8, 12, 8)
            cl.setSpacing(1)
            v = QLabel("0")
            v.setAlignment(Qt.AlignCenter)
            cc = T.P[col] if col else T.P["ink2"]
            v.setStyleSheet(f"font-size:18px; font-weight:700; color:{cc};"
                            f"background:transparent;")
            l = QLabel(label)
            l.setAlignment(Qt.AlignCenter)
            l.setStyleSheet(f"font-size:12px; color:{T.P['ink3']};"
                            f"background:transparent;")
            cl.addWidget(v)
            cl.addWidget(l)
            attach_shadow(c, blur=22, dy=6, alpha=34)
            self.stats[key] = v
            self._stat_cards.append((c, v, col, l))
            stat.addWidget(c, 1)
        self.stats_widget.hide()

        # 筛选栏
        fb = glass()
        fl = QVBoxLayout(fb)
        fl.setContentsMargins(0, 2, 0, 2)
        fl.setSpacing(6)
        attach_shadow(fb, blur=26, dy=7, alpha=40)
        self.fb = fb

        self.q = QLineEdit()
        self.q.setPlaceholderText("搜索关键词：避让点 / 扫描航线 / NW / 五边形 …")
        self.q.textChanged.connect(self.apply_filter)
        self.q.setMinimumHeight(42)

        # 自绘下拉：规避原生 popup 在无边框窗口下渲染成黑底的 bug
        self.cb_r = Combo()
        self.cb_d = Combo()
        self.cb_r.changed.connect(lambda _: self.apply_filter())
        self.cb_d.changed.connect(lambda _: self.apply_filter())

        # 做题进度筛选：把「已做 / 未做」变成可筛的条件，
        # 不然 100 多道题里很难找出还没练过的。
        self.cb_p = Combo()
        self.cb_p.clear()
        self.cb_p.addItem("全部题目", None)
        self.cb_p.addItem("只看未做", "todo")
        self.cb_p.addItem("只看已做", "done")
        # 错题集：判题判错的题自动收进来，这里能专门捞出来复习
        self.cb_p.addItem("只看错题", "wrong")
        # 无标签：导入的图片/扫描件题面没文字，认不出考点，专门捞出来手工补
        self.cb_p.addItem("只看无标签", "naked")
        self.cb_p.refresh()
        self.cb_p.changed.connect(lambda _: self.apply_filter())

        # 考点筛选由 qtag 根据每张导入题卡的字段即时计算。
        # 只有 ≥3 道题的考点才进下拉 —— 1~2 道的列进去纯属噪音。
        # 与「做题进度」是**叠加**关系（"风向题 + 只看未做"就筛出来了）。
        self.cb_t = Combo()
        self._build_tag_combo()
        self.cb_t.changed.connect(lambda _: self.apply_filter())

        # ★ 形状筛选（v1.9.8）：用户 2026-09-21「形状跟题型分开」——
        #   原来「考点」下拉里混着六边形/五边形这些**画的东西**，跟"考什么"
        #   混在一格里，筛"风向题"时还得从一堆形状里挑。拆成两个下拉。
        self.cb_s = Combo()
        self._build_shape_combo()
        self.cb_s.changed.connect(lambda _: self.apply_filter())

        self.b_rand = IconButton("随机抽题", "dice", "primary")
        self.b_rand.clicked.connect(self.random_pick)

        # ★ v1.9.7 精简：原来「错题集 / 成绩趋势 / 导入」三个按钮平铺在筛选栏，
        # 加上 4 个下拉一共 8 个控件横着挤，五三说"有点多有点繁杂"。
        # 三个低频入口收进「更多」菜单，主色只留「随机抽题」。
        # v1.9.8 又拆出一个「形状」下拉 —— 但它是**替换**关系：
        #   原来挤在「考点」里的形状项挪走了，下拉总数不变（仍 5 个）。
        self.b_more = IconButton("更多", "more", "default",
                                 font_size=13, radius=10, gap="  ")
        self.b_more.setMinimumHeight(42)
        self.b_more.setToolTip("错题集 / 成绩趋势 / 管理已导入题卡")
        self.b_more.clicked.connect(self.show_more_menu)


        # ★ v1.9.8：筛选栏从 6 个控件变 7 个（多一个「形状」下拉）后，
        #   搜索框被等比压到只剩"搜…"两个字。给几个控件设最小宽度兜底 ——
        #   权重只管"富余空间怎么分配"，挡不住被压得太狠。
        #   ★ 实测（1180 宽窗口）：下拉全卡在 124 的最小值，而搜索框拿了 216 ——
        #     说明权重没调对。搜索框 4 → 3、下拉 2 → 3，把富余空间多分给下拉。
        self.q.setMinimumWidth(150)
        for cb in (self.cb_p, self.cb_t, self.cb_s, self.cb_r, self.cb_d):
            cb.setMinimumWidth(122)

        self.q.setPlaceholderText("搜索题号或关键词，例如：12、避让点、五边形")
        self.q.setClearButtonEnabled(True)
        self.q.setAccessibleName("搜索题号或关键词")
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.b_directory = IconButton("题目目录", None, "default", font_size=13)
        self.b_directory.setMinimumHeight(42)
        self.b_directory.setCheckable(True)
        self.b_directory.setToolTip("显示或收起题目目录")
        self.b_directory.toggled.connect(self._toggle_directory)
        search_row.addWidget(self.b_directory)
        search_row.addWidget(self.q, 1)
        self.b_import = IconButton("导入题卡", "folder", "primary", font_size=13)
        self.b_import.setMinimumHeight(42)
        self.b_import.setAccessibleName("导入题库题卡")
        self.b_import.setToolTip("选择 PDF 或图片题卡，支持多选（Ctrl+O）")
        self.b_import.clicked.connect(self.import_files)
        search_row.addWidget(self.b_import)
        fl.addLayout(search_row)
        self.filter_summary = QLabel("全部题目")
        self.filter_summary.setObjectName("Dim2")
        self.filter_summary.setWordWrap(True)
        self.filter_summary.setAccessibleName("当前检索和筛选条件")
        self.b_clear_visible = IconButton("清除", "reset", "bare", font_size=12)
        self.b_clear_visible.clicked.connect(self.clear_filters)
        self.b_clear_visible.hide()
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addWidget(self.filter_summary, 1)
        action_row.addWidget(self.b_clear_visible)
        action_row.addWidget(self.b_rand)
        self.b_filters = IconButton("筛选", "tagicon", "default", font_size=13)
        self.b_filters.setCheckable(True)
        self.b_filters.setMinimumHeight(42)
        action_row.addWidget(self.b_filters)
        action_row.addWidget(self.b_more)
        fl.addLayout(action_row)
        self.filter_panel = QWidget()
        filter_grid = QGridLayout(self.filter_panel)
        filter_grid.setContentsMargins(0, 6, 0, 0)
        filter_grid.setSpacing(8)
        for index, (caption, combo) in enumerate((("练习状态", self.cb_p),
                ("考点", self.cb_t), ("图形", self.cb_s),
                ("地区", self.cb_r), ("场次", self.cb_d))):
            field = QVBoxLayout()
            field.setSpacing(4)
            label = QLabel(caption)
            label.setObjectName("Dim2")
            field.addWidget(label)
            combo.setAccessibleName(caption)
            combo.setMinimumWidth(120)
            field.addWidget(combo)
            filter_grid.addLayout(field, index // 3, index % 3)
        self.b_clear = IconButton("清除筛选", "reset", "bare", font_size=13)
        self.b_clear.clicked.connect(self.clear_filters)
        filter_grid.addWidget(self.b_clear, 1, 2, Qt.AlignBottom)
        self.filter_panel.setMaximumHeight(0)
        self.filter_panel.hide()
        self._filter_open = False
        self._filter_anim = None
        self._filter_clip_height = 0
        self._filter_progress = 0.0
        self._filter_panel_full_height = 0
        self._filter_card_base_height = 0
        self._filter_card_full_height = 0
        self.b_filters.toggled.connect(self._animate_filter_panel)
        fl.addWidget(self.filter_panel)
        root.addWidget(fb)
        self.frequency_ruler = QuestionFrequencyRuler(self)
        self.frequency_ruler.setVisible(T.app_theme == "industrial")
        root.addWidget(self.frequency_ruler)

        # 给跨过分镜上缘的夹子留出实体空间，避免进入“更多”按钮的点击区域。
        self._clip_gap = QWidget()
        self._clip_gap.setFixedHeight(12)
        self._clip_gap.setVisible(T.app_theme == "chiyo")
        root.addWidget(self._clip_gap)

        # 主体
        split = QSplitter(Qt.Horizontal)
        self.splitter = split
        self.listw = QListWidget()
        self.listw.setMinimumWidth(0)
        self.listw.setMinimumSize(0, 0)
        self.listw.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.listw.setMaximumWidth(0)
        self.listw.currentRowChanged.connect(self.on_select)
        # v1.9.11 右键改标签：自动分类认错时用户直接在列表里纠正
        self.listw.setContextMenuPolicy(Qt.CustomContextMenu)
        self.listw.customContextMenuRequested.connect(self._tag_menu)
        attach_shadow(self.listw, blur=26, dy=8, alpha=44)
        split.addWidget(self.listw)
        self.listw.hide()

        detail = glass()
        self.detail = detail
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(18, 14, 18, 14)
        dl.setSpacing(10)
        self.detail_shadow = QGraphicsDropShadowEffect(detail)
        detail.setGraphicsEffect(self.detail_shadow)

        dh = QHBoxLayout()
        self.industrial_question_label = QLabel("题目 #")
        self.industrial_question_label.setStyleSheet(
            f"font-size:19px;font-weight:700;color:{T.P['ink']};"
            "background:transparent;")
        self.industrial_question_label.hide()
        dh.addWidget(self.industrial_question_label)
        self.d_stamp = IndustrialMatrixStamp("01")
        self.d_stamp.setAlignment(Qt.AlignCenter)
        self.d_stamp.setFixedSize(42, 30)
        self.d_stamp.setStyleSheet(
            f"background:{T.C_BLUE};color:#fff;font-size:14px;font-weight:900;"
            "border:0;border-radius:3px 3px 3px 0px;")
        self.d_stamp.hide()
        dh.addWidget(self.d_stamp)
        self.d_title = QLabel("请从左侧选择题目")
        self.d_title.setObjectName("H2")
        self.d_sub = QLabel("")
        self.d_sub.setStyleSheet(f"font-size:12px; font-weight:700;"
                                 f"color:{T.P['accent_fg']}; background:transparent;")
        dh.addWidget(self.d_title)
        dh.addStretch()
        dh.addWidget(self.d_sub)
        self.b_note = IconButton("备忘录", "note", "default",
                                 font_size=12, radius=10)
        self.b_note.setMinimumHeight(32)
        self.b_note.setToolTip("给本题写文字笔记、存截图")
        self.b_note.clicked.connect(self.open_note)
        dh.addWidget(self.b_note)
        self.b_ind_prev = IconButton("上一题", None, "default", font_size=11)
        self.b_ind_next = IconButton("下一题", None, "primary", font_size=11)
        for button in (self.b_ind_prev, self.b_ind_next):
            button.setFixedSize(70, 40)
            button.setVisible(False)
        self.b_ind_prev.clicked.connect(lambda: self.step(-1))
        self.b_ind_next.clicked.connect(lambda: self.step(1))
        # 工业版把翻题与题卡工具并入同一条工具栏，按钮共用尺寸和基线。
        dl.addLayout(dh)

        ct = QHBoxLayout()
        self.detail_content = ct
        ct.setSpacing(14)
        self.body = QTextBrowser()
        self.body.setOpenLinks(False)
        self.body.anchorClicked.connect(self._open_body_action)
        self.body.setMinimumWidth(170)
        self.body_column = QWidget()
        self.body_column.setStyleSheet("background:transparent;")
        body_column_layout = QVBoxLayout(self.body_column)
        body_column_layout.setContentsMargins(0, 0, 0, 0)
        body_column_layout.setSpacing(5)
        self.annotation_title = QLabel("条件与批注")
        self.annotation_title.setObjectName("H3")
        body_column_layout.addWidget(self.annotation_title)
        self.detail_micro_header = QWidget()
        self.detail_micro_header.setFixedHeight(20)
        micro_layout = QHBoxLayout(self.detail_micro_header)
        micro_layout.setContentsMargins(2, 0, 2, 0)
        micro_layout.addWidget(QLabel("QUESTION DETAILS"))
        micro_layout.addStretch(1)
        micro_layout.addWidget(QLabel("DATA REFERENCE"))
        body_column_layout.addWidget(self.detail_micro_header)
        self.detail_micro_rule = self._paper_separator()
        body_column_layout.addWidget(self.detail_micro_rule)
        body_column_layout.addWidget(self.body, 1)
        # 文字区 : 题卡区 = 3 : 6 —— 题卡是这页的主角，让它在版面上占大头

        fw = QVBoxLayout()
        fw.setSpacing(6)
        fcap = QHBoxLayout()
        self.fcap_lb = QLabel("题卡图形")
        self.fcap_lb.setStyleSheet(
            f"font-size:12px; font-weight:700; color:{T.P['ink3']};"
            f"background:transparent;")
        fcap.addWidget(self.fcap_lb)
        self.paper_micro_label = QLabel("PAPER DIAGRAM")
        self.paper_micro_label.setVisible(T.app_theme == "industrial")
        self.paper_micro_label.setStyleSheet(
            f"font:8px Consolas;color:{T.P['ink3']};letter-spacing:1px;"
            "background:transparent;")
        fcap.addWidget(self.paper_micro_label)
        fcap.addStretch(1)
        self.b_open = IconButton("打开练习", "pen", "primary",
                                 font_size=12, radius=10)
        self.b_open.setFixedSize(104, 40)
        self.b_open.setToolTip("打开题卡工作台：绘制批注 / 橡皮擦 / 缩放 / 导出 / 置顶")
        self.b_open.clicked.connect(self.open_figure_win)
        fw.addLayout(fcap)

        # 题卡预览：完整显示图形，绝不裁切。
        # 为什么用 QScrollArea：整页配图是「上半图形 + 下半文字」的长条图，
        # 之前直接把 QPixmap 塞进固定高度的 QLabel，按控件高度等比缩放后
        # 底部文字会被压没 —— 改成「按宽度撑开、高度按原图比例自动延长」，
        # 装不下就出滚动条，保证整页内容 100% 可见。
        self.fig_scroll = QScrollArea()
        self.fig_scroll.setWidgetResizable(False)
        self.fig_scroll.setAlignment(Qt.AlignCenter)
        self.fig_scroll.setFrameShape(QFrame.NoFrame)
        self.fig_scroll.setMinimumWidth(180)
        self.fig_scroll.setCursor(Qt.PointingHandCursor)
        self.fig_scroll.setToolTip("点击打开题卡工作台")
        self.fig_scroll.viewport().setCursor(Qt.PointingHandCursor)

        self.fig = QLabel("导入题卡后显示题图")
        self.fig.setAlignment(Qt.AlignCenter)
        self.fig.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.fig.setMinimumWidth(160)
        self.fig.setCursor(Qt.PointingHandCursor)
        self.fig.setToolTip("点击打开题卡工作台")
        self.fig.mousePressEvent = lambda e: self.open_figure_win()
        # 原始题卡和广东02版式分属两个独立滚动页面，避免隐藏版式里的文字
        # 仍占据原图上方的布局空间。
        self.original_sheet = QWidget()
        self.original_sheet.setObjectName("OriginalQuestionImage")
        original_layout = QVBoxLayout(self.original_sheet)
        original_layout.setContentsMargins(0, 0, 0, 0)
        original_layout.setSpacing(0)
        original_layout.addWidget(self.fig, 0, Qt.AlignHCenter | Qt.AlignTop)
        self._active_card_widget = self.original_sheet
        self.paper_sheet = _WatermarkedPaper()
        self.paper_sheet.setObjectName("ImportedPaperSheet")
        self.paper_layout = QVBoxLayout(self.paper_sheet)
        self.paper_layout.setContentsMargins(30, 25, 30, 28)
        self.paper_layout.setSpacing(12)
        self.paper_title = QLabel("（未填写 广东02）真题")
        self.paper_title.setAlignment(Qt.AlignCenter)
        self.paper_title.setStyleSheet("font-size:24px;font-weight:750;color:#17253B;background:transparent;")
        self.paper_candidate = QLabel("考生姓名：________________　　　　身份证号码：________________")
        self.paper_meta = QLabel("机构名称：________________________________________________")
        for lab in (self.paper_candidate, self.paper_meta):
            lab.setStyleSheet("font-size:13px;color:#35445A;background:transparent;")
        self.paper_candidate.setContentsMargins(12, 4, 12, 2)
        self.paper_meta.setContentsMargins(12, 0, 12, 5)
        self.paper_content_labels = {}
        self.paper_layout.addWidget(self.paper_title)
        self.paper_layout.addWidget(self.paper_candidate)
        self.paper_layout.addWidget(self.paper_meta)
        self.paper_rule = self._paper_separator()
        self.paper_layout.addWidget(self.paper_rule)
        for key, title in (("conditions_text", "附加条件"),
                           ("requirements_text", "航线要求"),
                           ("suggested_text", "建议参数")):
            heading = QLabel(title)
            heading.setStyleSheet("font-size:16px;font-weight:750;color:#17253B;background:transparent;")
            body = QLabel("")
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            body.setStyleSheet("font-size:14px;line-height:160%;color:#17253B;background:transparent;")
            section = QWidget()
            section.setStyleSheet("background:transparent;")
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(7)
            section_layout.setAlignment(Qt.AlignTop)
            section_layout.addWidget(heading)
            section_layout.addWidget(body)
            self.paper_content_labels[key] = (section, body)
        self.paper_diagram = _QuestionDiagram()
        self.paper_diagram.setMinimumWidth(270)
        self.paper_conditions_frame = QFrame()
        self.paper_conditions_frame.setStyleSheet(
            "QFrame{background:rgba(255,255,255,105);border:1.5px solid #182638;}"
            "QLabel{border:0;background:transparent;}")
        condition_layout = QVBoxLayout(self.paper_conditions_frame)
        condition_layout.setContentsMargins(16, 13, 16, 13)
        condition_layout.addWidget(self.paper_content_labels["conditions_text"][0])
        self.paper_main_row = QWidget()
        main_row_layout = QHBoxLayout(self.paper_main_row)
        main_row_layout.setContentsMargins(0, 0, 0, 0)
        main_row_layout.setSpacing(18)
        main_row_layout.addWidget(self.paper_diagram, 6)
        main_row_layout.addWidget(self.paper_conditions_frame, 4)
        self.paper_layout.addWidget(self.paper_main_row, 1)
        self.paper_layout.addWidget(self.paper_content_labels["requirements_text"][0])
        self.paper_layout.addWidget(self.paper_content_labels["suggested_text"][0])
        self.paper_layout.addStretch(1)
        # A4 逻辑画布与预览分离，缩放整页而不是挤压其中的控件。
        self.paper_sheet.setFixedSize(840, 1188)
        self.paper_preview = QLabel()
        self.paper_preview.setAlignment(Qt.AlignCenter)
        self.paper_preview.mousePressEvent = lambda e: self.open_figure_win()
        self.fig_scroll.setWidget(self.original_sheet)
        self._style_fig()
        self.chiyo_card_dots = ChiyoCornerDots(
            self.detail, corners=("top-right", "bottom-left"))
        self.chiyo_binder_clip = ChiyoBinderClip(
            self.detail, self.motion_surface)
        self.chiyo_binder_clip.raise_()
        self.set_card_layout(cfg.get("card_layout", "standard"), save=False)
        fw.addWidget(self.fig_scroll, 1)
        self.figure_column = QWidget()
        self.figure_column.setStyleSheet("background:transparent;")
        self.figure_column.setLayout(fw)
        ct.addWidget(self.figure_column, 7)
        self.b_fit = IconButton("整页适配", None, "default", font_size=11)
        self.b_fit.setFixedSize(88, 40)
        self.b_fit.clicked.connect(self._fit_preview)
        self.b_fit_width = IconButton("适合宽度", None, "default", font_size=11)
        self.b_fit_width.setFixedSize(88, 40)
        self.b_fit_width.clicked.connect(lambda: self._fit_preview("width"))
        self.preview_scale_label = QLabel("100%")
        self.preview_scale_label.setObjectName("Dim2")
        self.preview_scale_label.setMinimumWidth(38)
        self.b_retry = IconButton("重试", "reset", "default", font_size=11)
        self.b_retry.clicked.connect(self._retry_figure)
        self.b_retry.hide()
        self.b_zoom_out = QPushButton("−")
        self.b_zoom_out.setFixedSize(34, 40)
        self.b_zoom_out.setCursor(Qt.PointingHandCursor)
        self.b_zoom_out.setAccessibleName("缩小题卡")
        self.b_zoom_out.clicked.connect(lambda: self._change_preview_zoom(-0.15))
        self.b_zoom_in = QPushButton("+")
        self.b_zoom_in.setFixedSize(34, 40)
        self.b_zoom_in.setCursor(Qt.PointingHandCursor)
        self.b_zoom_in.setAccessibleName("放大题卡")
        self.b_zoom_in.clicked.connect(lambda: self._change_preview_zoom(0.15))
        self._style_zoom_buttons()
        self.b_full = IconButton("全屏", None, "default", font_size=11)
        self.b_full.setFixedSize(62, 40)
        self.b_full.clicked.connect(self._open_fullscreen_workbench)
        fcontrols = QHBoxLayout()
        fcontrols.setSpacing(8)
        fcontrols.addWidget(self.b_ind_prev)
        fcontrols.addWidget(self.b_ind_next)
        self.preview_fit_choice = QComboBox()
        self.preview_fit_choice.addItem("整页适配", "page")
        self.preview_fit_choice.addItem("适合宽度", "width")
        self.preview_fit_choice.setCurrentIndex(self.preview_fit_choice.findData(self._preview_fit))
        self.preview_fit_choice.setFixedSize(108, 40)
        self.preview_fit_choice.setAccessibleName("题卡适配方式")
        self.preview_fit_choice.activated.connect(lambda: self._fit_preview(self.preview_fit_choice.currentData()))
        self.b_fit.hide()
        self.b_fit_width.hide()
        fcontrols.addWidget(self.preview_fit_choice)
        fcontrols.addWidget(self.b_zoom_out)
        fcontrols.addWidget(self.b_zoom_in)
        fcontrols.addWidget(self.preview_scale_label)
        fcontrols.addStretch(1)
        fcontrols.addWidget(self.b_full)
        fcap.addWidget(self.b_retry)
        self._figure_header_actions = fcap
        self._figure_toolbar = fcontrols
        self._figure_toolbar_compact = False
        fw.insertLayout(1, fcontrols)
        self._style_figure_toolbar_buttons()
        ct.addWidget(self.body_column, 3)
        dl.addLayout(ct, 1)

        self.detail_nav = QWidget()
        nav = QHBoxLayout(self.detail_nav)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(9)
        self.b_prev = IconButton("上一题", None, "default")
        self.b_prev.setMinimumHeight(40)
        self.b_prev.clicked.connect(lambda: self.step(-1))
        self.b_next = IconButton("下一题", None, "primary")
        self.b_next.setMinimumHeight(40)
        self.b_next.clicked.connect(lambda: self.step(1))
        self.result_count = QLabel()
        self.result_count.setObjectName("Dim2")
        nav.addWidget(self.result_count)
        nav.addStretch()
        nav.addWidget(self.b_open)
        self.b_empty_action = IconButton("导入题卡", "folder", "primary", font_size=12)
        self.b_empty_action.clicked.connect(self._empty_action)
        self.b_empty_action.hide()
        self.b_done = IconButton("标记已做", "check", "default", font_size=12)
        self.b_done.setMinimumHeight(40)
        self.b_done.setToolTip("手动标记或取消已做（Ctrl+D）")
        self.b_done.clicked.connect(self._toggle_current_done)
        nav.addWidget(self.b_empty_action)
        nav.addWidget(self.b_done)
        nav.addWidget(self.b_prev)
        nav.addWidget(self.b_next)
        dl.addWidget(self.detail_nav)

        split.addWidget(detail)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 5)
        split.setCollapsible(0, True)
        split.setSizes([210, 950])
        root.addWidget(split, 1)
        self.industrial_footer = IndustrialPageFooter(self)
        self.industrial_footer.setVisible(T.app_theme == "industrial")
        root.addWidget(self.industrial_footer)

        self._cards = [fb, detail]
        self._style_reading_desk()
        self._hook_fig_resize()

    def _style_reading_desk(self):
        """管理工具与阅读主体分层，原图保留完整纸面。"""
        self.fb.setStyleSheet("QFrame{background:transparent;border:0;}")
        effect = self.fb.graphicsEffect()
        if effect is not None:
            effect.setEnabled(False)
        self.annotation_title.setStyleSheet(f"color:{T.P['ink2']};font-size:13px;font-weight:700;background:transparent;padding:2px 0 6px;")
        self.preview_fit_choice.setStyleSheet(f"QComboBox{{background:{T.P['input_bg']};color:{T.P['ink']};border:1px solid {T.P['input_border']};border-radius:10px;padding:4px 8px;}}")

    def _install_shortcuts(self):
        """快捷键只在题库页内生效，输入框保留自己的编辑行为。"""
        self._shortcuts = []
        for key, handler in (("Ctrl+O", self.import_files),
                             ("Ctrl+F", self._focus_search),
                             ("Alt+Left", lambda: self.step(-1)),
                             ("Alt+Right", lambda: self.step(1)),
                             ("Ctrl+Return", self.open_figure_win),
                             ("Ctrl+D", self._shortcut_done)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)
        self.b_prev.setToolTip("上一题（Alt+←）")
        self.b_next.setToolTip("下一题（Alt+→）")
        self.q.setToolTip("搜索题号或关键词（Ctrl+F）")
        self.b_open.setToolTip("打开题卡工作台（Ctrl+Enter）")
        QWidget.setTabOrder(self.q, self.b_import)
        QWidget.setTabOrder(self.b_import, self.b_rand)
        QWidget.setTabOrder(self.b_rand, self.b_filters)
        QWidget.setTabOrder(self.b_filters, self.b_more)

    def _focus_search(self):
        self.q.setFocus(Qt.ShortcutFocusReason)
        self.q.selectAll()

    def _toggle_current_done(self):
        if not self.cur or not self.isVisible():
            return
        qid = self.cur["id"]
        self.cancel_record()
        PG.toggle(qid)
        if self.fig_win is not None and self.fig_win._qid == int(qid):
            self.fig_win._sync_done()
        self.set_status("本题已标记已做。" if PG.is_done(qid) else "已取消本题已做标记。")
        self.apply_filter()
        if self.cur and any(q["id"] == qid for q in self.view):
            self.goto_question(qid)
        if PG.is_done(qid):
            from flight_motion import show_completion_stamp
            show_completion_stamp(self.b_done)

    def _shortcut_done(self):
        if not isinstance(QApplication.focusWidget(), QLineEdit):
            self._toggle_current_done()

    def _empty_action(self):
        if self.all:
            self.clear_filters()
        else:
            self.import_files()

    def _open_body_action(self, url):
        if url.toString() == "azimuth:import":
            self.import_files()
        elif url.toString() == "azimuth:clear":
            self.clear_filters()
        elif url.toString() == "azimuth:retry":
            self._retry_figure()
        elif url.scheme() in ("https", "http"):
            QDesktopServices.openUrl(url)

    def _set_import_phase(self, phase):
        self._import_phase = phase
        busy = phase in ("processing", "selecting", "review")
        self.b_import.setEnabled(not busy)
        self.import_drop_target.setEnabled(not busy)
        self.b_import.setText("正在导入" if busy else "导入题卡")
        if not self.view:
            self.show_empty()

    def _retry_figure(self):
        if not self.cur:
            return
        self._figure_cache.clear()
        self._failed_figure_key = None
        self._render_fig()

    def _style_fig(self):
        # 预览区：外层滚动容器与内层图片标签共用同一套玻璃底
        self.fig_scroll.setStyleSheet(
            f"QScrollArea{{background:{T.P['panel_soft']};"
            f"border:1px solid {T.P['glass_soft_edge']};"
            f"border-radius:{8 if T.app_theme == 'industrial' else 16}px;}}"
            f"QScrollArea > QWidget > QWidget{{background:transparent;}}"
            f"QScrollBar:vertical{{width:8px;background:transparent;margin:4px 2px;}}"
            f"QScrollBar::handle:vertical{{background:{T.P['scroll']};"
            f"border-radius:4px;min-height:30px;}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{"
            f"height:0px;}}"
            f"QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{{"
            f"background:transparent;}}")
        self.fig.setStyleSheet(
            f"background:transparent; border:none;"
            f"color:{T.P['ink3']}; font-size:12px; padding:0;")

    @staticmethod
    def _paper_separator():
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet("background:#CFD7E2;border:0;")
        return line

    def set_card_layout(self, layout="standard", save=True):
        layout = "paper" if layout in ("paper", "guangdong02") else "standard"
        self._card_layout = layout
        if save:
            cfg.set_("card_layout", layout)
        paper = layout == "paper"
        target = self.paper_preview if paper else self.original_sheet
        self.fig_scroll.setWidgetResizable(False)
        if target is not self._active_card_widget:
            self.fig_scroll.takeWidget()
            self.fig_scroll.setWidget(target)
            self._active_card_widget = target
        self.paper_sheet.setStyleSheet(
            "QWidget#ImportedPaperSheet{border:1px solid #CFD7E2;border-radius:3px;}"
            if paper else "QWidget#ImportedPaperSheet{background:transparent;border:0;}")
        for widget in (self.paper_title, self.paper_candidate, self.paper_meta):
            widget.setVisible(paper)
        self.paper_main_row.setVisible(paper)
        self.paper_diagram.setVisible(paper)
        self.paper_conditions_frame.setVisible(paper)
        for key in ("requirements_text", "suggested_text"):
            self.paper_content_labels[key][0].setVisible(paper)
        self.fig.setVisible(not paper)
        self._render_paper_card()
        if paper:
            self._resize_paper_sheet()
        self.paper_sheet.updateGeometry()
        self.fig_scroll.viewport().update()
        if self.cur:
            self._render_fig()

    def _render_paper_card(self):
        # 标准题卡布局不预先解码、裁剪隐藏的纸面题卡。
        if getattr(self, "_card_layout", "standard") != "paper":
            return
        q = self.cur or {}
        date = str(q.get("date") or "未填写").strip()
        region = str(q.get("region") or "未填写").strip()
        title = f"（{date}　{region}）真题"
        self.paper_title.setText(title)
        self.paper_meta.setText("机构名称：________________________________________________")
        sections = paper_sections(q)
        for key, (section, body) in self.paper_content_labels.items():
            text = sections[key]
            body.setText(text or "（题目未提供此部分内容）")
            if key == "conditions_text":
                section.setVisible(True)
            else:
                section.setVisible(bool(text))
        source_path = self._fig_path(q.get("id")) if q.get("id") is not None else ""
        original = QPixmap(source_path) if source_path and os.path.isfile(source_path) else QPixmap()
        self.paper_diagram.set_question(q, original)

    def _resize_paper_sheet(self):
        if getattr(self, "_card_layout", "standard") != "paper":
            return
        viewport = self.fig_scroll.viewport()
        available_width = viewport.width() - 20
        available_height = viewport.height() - 20
        if available_width < 1 or available_height < 1 or not self.cur:
            return
        scale = available_width / 840
        if self._preview_fit == "page":
            scale = min(scale, available_height / 1188)
        scale *= self._preview_zoom
        page_width = max(1, round(840 * scale))
        page_height = max(1, round(1188 * scale))
        self.paper_sheet.ensurePolished()
        self.paper_layout.activate()
        pixmap = QPixmap(self.paper_sheet.size())
        pixmap.fill(Qt.white)
        self.paper_sheet.render(pixmap)
        self.paper_preview.setPixmap(pixmap.scaled(
            page_width, page_height, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.paper_preview.setFixedSize(page_width, page_height)

    def _style_zoom_buttons(self):
        style = (f"QPushButton{{background:{T.P['input_bg']};color:{T.P['ink']};"
                 "border:0;"
                 f"border-radius:{12 if T.app_theme == 'chiyo' else 8}px;"
                 "font-size:15px;font-weight:700;padding:0;}"
                 f"QPushButton:hover{{background:{T.P['accent_soft']};color:{T.C_BLUE};}}"
                 f"QPushButton:pressed{{background:{T.P['glass_lo']};}}")
        for button in (self.b_zoom_out, self.b_zoom_in):
            button.setStyleSheet(style)
            button.setFont(QFont(T.pick_font(), 13, QFont.Bold))

    def _style_figure_toolbar_buttons(self):
        for button in (self.b_open, self.b_full, self.b_fit,
                       self.b_fit_width, self.b_retry):
            button.setProperty('roundedToolbar', T.app_theme == 'chiyo')
            button.refresh()

    def _reflow_figure_toolbar(self):
        """主操作固定在题图标题行，缩放工具始终保持一行且不来回跳位。"""
        if not hasattr(self, "_figure_toolbar"):return
        compact=self.fig_scroll.width()<440
        self.b_fit.setFixedWidth(76 if compact else 88)
        self.b_fit_width.setFixedWidth(76 if compact else 88)
        self._figure_toolbar_compact=compact

    def _toggle_directory(self, opened):
        opened = bool(opened)
        old_anim = self._directory_anim
        current_width = self.listw.width() if self.listw.isVisible() else 0
        if old_anim is not None:
            old_anim.stop()
            old_anim.deleteLater()
            self._directory_anim = None

        # 锁住当前宽度作为新目标的起点，动画中反向时不先跳回端点。
        if opened:
            self.listw.show()
            self.listw.setMaximumWidth(16777215)
        self._directory_animating = True
        available = max(0, self.splitter.width())
        target_width = min(230, max(150, available - 430)) if opened else 0
        self._directory_target_width = target_width
        self.b_directory.set_checked_style(opened)

        if not self._directory_effects:
            for widget in (self.listw, self.detail):
                effect = widget.graphicsEffect()
                if effect is not None and effect.isEnabled():
                    effect.setEnabled(False)
                    self._directory_effects.append(effect)
        self.splitter.setSizes(
            [current_width, max(1, self.splitter.width() - current_width)])

        if not animations_enabled() or current_width == target_width:
            self._finish_directory_transition(opened, target_width)
            return

        motion = QVariantAnimation(self)
        motion.setStartValue(current_width)
        motion.setEndValue(target_width)
        motion.setDuration(560)
        motion.setEasingCurve(QEasingCurve.InOutCubic)
        motion.valueChanged.connect(
            lambda value, a=motion: self._set_directory_width(a, value))
        motion.finished.connect(
            lambda a=motion, state=opened, width=target_width:
            self._complete_directory_animation(a, state, width))
        self._directory_anim = motion
        motion.start(QVariantAnimation.DeleteWhenStopped)

    def _set_directory_width(self, animation, width):
        if animation is not self._directory_anim:
            return
        width = max(0, int(round(width)))
        available = max(1, self.splitter.width())
        width = min(width, max(0, available - 1))
        self.splitter.setSizes([width, max(1, available - width)])

    def _complete_directory_animation(self, animation, opened, target_width):
        if animation is not self._directory_anim:
            return
        self._directory_anim = None
        self._finish_directory_transition(opened, target_width)

    def _finish_directory_transition(self, opened, target_width):
        self._directory_animating = False
        if opened:
            self.listw.setMaximumWidth(16777215)
            self.splitter.setSizes(
                [target_width, max(1, self.splitter.width() - target_width)])
        else:
            self.listw.hide()
            self.listw.setMaximumWidth(16777215)
            self.splitter.setSizes([0, max(1, self.splitter.width())])
        self._directory_open = bool(opened)
        for effect in self._directory_effects:
            try:
                effect.setEnabled(True)
            except RuntimeError:
                pass
        self._directory_effects.clear()
        # 目录动画期间只在布局稳定后适配一次题图，避免逐帧重复缩放/解码。
        self._render_fig()

    def _fit_preview(self, mode="page"):
        self._preview_fit = mode if mode in ("page", "width") else "page"
        self._preview_zoom = 1.0
        self.preview_scale_label.setText("100%")
        self._render_fig()

    def _change_preview_zoom(self, delta):
        self._preview_zoom = min(3.0, max(0.25, self._preview_zoom + delta))
        self.preview_scale_label.setText(f"{round(self._preview_zoom * 100)}%")
        self._render_fig()

    def _open_fullscreen_workbench(self):
        self.open_figure_win()
        if self.fig_win is not None:
            self.fig_win.showMaximized()

    # --------------------------------------------------- 数据
    def load(self):
        try:
            self.all = BD.load()
            for question in self.all:
                question["imported"] = True
            QM.retain_imported_question_ids(
                question.get("id") for question in self.all)
            self.all.sort(key=lambda q: int(str(q.get("id", 0))))
            log.info("用户导入题卡加载 %d 题", len(self.all))
        except Exception as e:
            log.error("用户导入题卡加载失败 %r", e)
            self.all = []

        regions = sorted({str(q.get("region") or "") for q in self.all
                          if str(q.get("region") or "").strip()})
        dates = sorted({str(q.get("date") or "") for q in self.all
                        if str(q.get("date") or "").strip()},
                       key=lambda d: [int(x) if x.isdigit() else 0
                                      for x in d.split(".")])

        # 地区 / 考次的信息挪到副标题，统计卡让给做题进度
        self.sub_lb.setText(
            "共 %d 张题库题卡 · 覆盖 %d 个地区 · %d 个考次"
            % (len(self.all), len(regions), len(dates)))
        # 题库已有内容时保留显式导入按钮，把重复拖放横条的空间让给题面。
        self.import_drop_target.setVisible(T.app_theme!="anna" and not self.all)

        self.cb_r.clear()
        self.cb_r.addItem("全部地区", None)
        for r in regions:
            n = sum(1 for q in self.all if str(q.get("region") or "") == r)
            self.cb_r.addItem("%s（%d）" % (r, n), r)

        self.cb_d.clear()
        self.cb_d.addItem("全部场次", None)
        for d in dates:
            n = sum(1 for q in self.all if str(q.get("date") or "") == d)
            self.cb_d.addItem("%s（%d）" % (d, n), d)

        self.cb_r.refresh()
        self.cb_d.refresh()
        # 考点 / 形状两个下拉都要跟着题库重算（导入或删题后题数会变）
        if hasattr(self, "cb_t"):
            self._build_tag_combo()
        if hasattr(self, "cb_s"):
            self._build_shape_combo()

        self.apply_filter()
        self._ready = True

    def refresh_stats(self):
        """刷新统计卡：总量 / 已做 / 未做 / 错题 / 当前筛选"""
        # 只按题库里真实存在的 id 计数，换题库后旧记录不会把数字顶高
        ids = [q["id"] for q in self.all]
        done = sum(1 for i in ids if PG.is_done(i))
        wrong = sum(1 for i in ids if PG.is_wrong(i))
        self.stats["total"].setText(str(len(ids)))
        self.stats["done"].setText(str(done))
        self.stats["todo"].setText(str(max(0, len(ids) - done)))
        self.stats["wrong"].setText(str(wrong))
        self.stats["shown"].setText(str(len(self.view)))

    def _build_tag_combo(self):
        """构建考点下拉。选项从 qtag 现算（自动标签 + 手动标签），题目变了重算一次。

        ★ v1.9.8：只取 ``group="exam"`` 的十类考点 ——
          形状（六边形/五边形…）和结构类（双夹角/避让点）挪去了「形状」下拉，
          用户 2026-09-21：「形状跟题型分开」。

        ★ v1.9.8 又挪走一个：「无标签」原来挂在这个下拉最后一项
          （code ``__naked__``）。它根本不是考点，是**归类状态** ——
          「只看无标签」在「做题进度」下拉里已经有一档了，同一个筛法
          出现两次、还挂在"考什么"下面，扫一眼会以为"无标签"是一种考点。
          直接从考点下拉里摘掉。
        """
        self.cb_t.clear()
        self.cb_t.addItem("全部考点", None)
        rows = qtag.table(self.all, manual_map=QM.all_tags(), group="exam",
                          off_map=QM.all_offs())
        for code, label, n in rows:
            self.cb_t.addItem("%s（%d）" % (label, n), code)
        self.cb_t.refresh()
        if hasattr(self, "cb_s"):
            self.cb_s.refresh()

    def _build_shape_combo(self):
        """构建形状下拉（v1.9.8）。

        装的是"这道题画的是什么/什么结构"—— 图形的形状（六边形/五边形/三角…）
        加上结构类标签（双夹角 / 避让点 / 教员重规划）。

        为什么不给结构类单开第三个下拉：筛题栏控件越少越好（v1.9.7 刚精简过），
        而"形状"和"结构"回答的是同一类问题 —— **这题长什么样**，
        跟「考点」回答的"考什么"是两回事，两档就够。
        """
        self.cb_s.clear()
        self.cb_s.addItem("全部形状", None)
        rows = qtag.table(self.all, manual_map=QM.all_tags(), group="shape",
                          off_map=QM.all_offs())
        for code, label, n in rows:
            self.cb_s.addItem("%s（%d）" % (label, n), code)
        self.cb_s.refresh()

    def apply_filter(self, *_):
        kw = self.q.text().strip().casefold()
        r = self.cb_r.currentData()
        d = self.cb_d.currentData()
        p = self.cb_p.currentData()
        t = self.cb_t.currentData() if hasattr(self, "cb_t") else None
        # ★ v1.9.8：形状是独立一档，与考点**叠加**（"六边形的风向题"= 两个都选）。
        s = self.cb_s.currentData() if hasattr(self, "cb_s") else None
        # ★ 兼容 v1.9.7 及更早留下的旧项：那版把「无标签」挂在考点下拉里
        #   （code ``__naked__``），而这里的判定只认进度档 ``p == "naked"`` ——
        #   选中它时 ``t`` 变成 ``__naked__``，往下走 `if t and t not in tmap`
        #   必然不成立（没有任何题带这个标签），**结果是一道题都不显示**。
        #   现在那项已从下拉里摘掉；万一有存档把它选了回来，这里直接当"不过滤"。
        if t == "__naked__":
            t, p = None, ("naked" if not p else p)
        # 标签一次性算好，避免筛选循环中重复解析题面。
        mmap = QM.all_tags()
        # 按需计算：只有筛了标签或"无标签"时才逐题跑正则
        need_tags = bool(t) or bool(s) or p == "naked"
        tmap = {}
        if need_tags:
            tmap = {id(q): qtag.tags(q, mmap.get(str(q.get("id"))))
                    for q in self.all}
        res = []
        for q in self.all:
            if r and q["region"] != r:
                continue
            if d and q["date"] != d:
                continue
            if p == "todo" and PG.is_done(q["id"]):
                continue
            if p == "done" and not PG.is_done(q["id"]):
                continue
            if p == "wrong" and not PG.is_wrong(q["id"]):
                continue
            # 「无标签」= 没筛任何标签（自动+手动都是空）
            if p == "naked" and tmap.get(id(q), ()):
                continue
            if t and t not in tmap.get(id(q), ()):
                continue
            if s and s not in tmap.get(id(q), ()):
                continue
            haystack = (q["conditions_text"] + q["requirements_text"]
                         + q["suggested_text"] + q["source"]).casefold()
            id_match = kw.lstrip("#") == str(q["id"])
            if kw and not id_match and kw not in haystack:
                continue
            res.append(q)
        self.view = res
        active = sum(value is not None for value in (r, d, p, t, s))
        self.b_filters.setText(f"筛选 · {active}" if active else "筛选")
        self.b_filters.setToolTip("展开或收起筛选条件；收起后条件仍然生效")
        labels = []
        if kw:
            labels.append("搜索：" + self.q.text().strip())
        for combo in (self.cb_p, self.cb_t, self.cb_s, self.cb_r, self.cb_d):
            if combo.currentData() is not None:
                labels.append(combo.currentText())
        description = " · ".join(labels) if labels else "全部题目"
        self.filter_summary.setText(f"{description}　（{len(res)} / {len(self.all)} 题）")
        self.filter_summary.setToolTip(description)
        self.b_clear_visible.setVisible(bool(labels))
        self.result_count.setText(f"找到 {len(res)} 题")
        for button in (self.b_open, self.b_note, self.b_prev, self.b_next, self.b_rand,
                       self.b_full, self.b_done, self.b_fit, self.b_fit_width,
                       self.b_zoom_in, self.b_zoom_out):
            button.setEnabled(bool(res))
        self.refresh_stats()
        self.refresh()
        if kw and self.q.hasFocus():
            accent(self.result_count)

    def clear_filters(self):
        """一次性清除全部条件，避免出现无结果却不知道哪里筛住了。"""
        self.q.blockSignals(True)
        self.q.clear()
        self.q.blockSignals(False)
        for combo in (self.cb_p, self.cb_t, self.cb_s, self.cb_r, self.cb_d):
            combo.setCurrentIndex(0)
        self.apply_filter()

    def show_wrong_set(self, anchor=None):
        """错题集窗口：错过哪几题、各错几次、错在哪。

        v1.9.6 从 QMessageBox 换成自绘窗口（原来是 18 条拼成一大段纯文本，
        不换行不对齐不能滚动，五三说"太丑了"。

        ★ v1.9.8：``anchor`` 传触发它的控件（「更多」按钮），浮层就**贴着它弹**
          —— 原来相对主窗口居中摆，飘在屏幕中间偏上，看不出是从哪冒出来的。
        """
        by_id = {int(str(q["id"]).strip()): q
                 for q in self.all if str(q.get("id", "")).strip().isdigit()}

        def _src(qid):
            q = by_id.get(qid)
            return (q or {}).get("source") or ""

        d = getattr(self, "_wrong_dlg", None)
        if d is None:
            # ★ v1.9.9：不传 parent —— 浮层现在是 Qt.Window 顶层窗口，
            #   引用由 self._wrong_dlg 持有（防 GC）。挂 parent 会让它变子控件，
            #   place_near 的全局坐标就被当父内坐标解释，弹到右下角去。
            d = WrongSetDialog(PG.wrong_recent(400), source_of=_src,
                               total_all=len(self.all))
            d.jump_to.connect(self._wrong_jump)
            d.attach_clear(self._clear_wrong_set)
        else:
            d.set_rows(PG.wrong_recent(400))
        # ★ 先 adjustSize 再定位 —— place_near 只算位置，不改尺寸；
        #   窗口没显示过时 width() 是最小值，直接定位会摆歪。
        if not getattr(d, "_window_closing", False):
            d.adjustSize()
            place_near(d, anchor or getattr(self, "b_more", None),
                       prefer="below", gap=8)
        d.theme_changed()
        self._wrong_dlg = d
        d.show_animated()
        d.raise_()

    def _wrong_jump(self, qid):
        """错题窗里点了某道题（qid=-1 表示只要筛出来，不跳题）。"""
        if qid == -1:
            self._pick_progress("wrong")
            return
        # 跳题前把筛选复位 —— 目标题很可能被「只看错题/未做」之类筛掉了
        self.cb_t.setCurrentIndex(0)
        self.cb_p.setCurrentIndex(0)
        self.q.clear()
        self.apply_filter()
        try:
            self.goto_question(int(qid))
        except Exception:
            pass
        d = getattr(self, "_wrong_dlg", None)
        if d is not None:
            d.close()

    def _clear_wrong_set(self):
        r = QMessageBox.question(
            self, "清空错题集",
            "将清空错题集（已做标记与练习次数不受影响）。\n"
            "此操作不可撤销，确定继续吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        PG.wrong_clear()
        self.refresh_stats()
        self.refresh(keep_row=True)
        d = getattr(self, "_wrong_dlg", None)
        if d is not None:
            d.set_rows([])
        self.set_status("错题集已清空")

    def show_trend(self, anchor=None):
        """成绩趋势窗口。

        为什么不做成 QDialog：这个主窗口是无边框半透明的，原生 QDialog 在它
        上面会出现黑底 / 阴影断层的毛病（跟自绘 Combo 当初躲的是同一个坑）。
        所以仍然用 QFrame + 手摆位置，并把引用存在 self 上 —— 无父窗口的
        QWidget 会被 Python GC 悄悄回收（判题窗当初就闪一下没了）。

        ★ v1.9.8：``anchor`` 传触发控件，浮层贴着它弹（原来居中摆，飘在屏幕中间）。
        """
        d = getattr(self, "_trend_dlg", None)
        if d is not None:
            d.reload()
            if not getattr(d, "_window_closing", False):
                d.adjustSize()
                place_near(d, anchor or getattr(self, "b_more", None),
                           prefer="below", gap=8)
            d.theme_changed()
            d.raise_()
            d.show_animated()
            return
        from trend_dialog import TrendDialog
        d = TrendDialog(None)   # ★ v1.9.9 顶层窗口，不挂 parent（引用在 self._trend_dlg）
        d.attach_clear(self._clear_trend)
        if not getattr(d, "_window_closing", False):
            d.adjustSize()
            place_near(d, anchor or getattr(self, "b_more", None),
                       prefer="below", gap=8)
        d.theme_changed()
        self._trend_dlg = d
        d.show_animated()
        d.raise_()

    def _clear_trend(self):
        r = QMessageBox.question(
            self, "清空成绩趋势",
            "将清空全部成绩趋势记录（已做标记、练习次数、错题集都不受影响）。\n"
            "此操作不可撤销，确定继续吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        PG.trend_clear()
        d = getattr(self, "_trend_dlg", None)
        if d is not None:
            d.reload()
        self.set_status("成绩趋势已清空")

    def _pick_progress(self, key):
        """把「做题进度」下拉切到某个值，并触发筛选。"""
        i = self.cb_p.findData(key)
        if i < 0:
            return
        self.cb_p.setCurrentIndex(i)
        self.apply_filter()

    # --------------------------------------------------- 右键改标签（v1.9.11）
    def _tag_menu(self, pos):
        """列表右键 → 标签菜单。自动分类认错的题在这里直接纠正。"""
        it = self.listw.itemAt(pos)
        if it is None:
            return
        row = self.listw.row(it)
        if not (0 <= row < len(self.view)):
            return
        q = self.view[row]
        m = TagMenu(q, self)
        m.changed.connect(self._on_tags_changed)
        # 贴着光标弹；底部放不下就整体上提，别让菜单戳出屏幕
        g = self.listw.mapToGlobal(pos)
        m.adjustSize()
        scr = self.screen().availableGeometry()
        if g.y() + m.height() > scr.bottom() - 8:
            g.setY(max(scr.top() + 8, scr.bottom() - m.height() - 8))
        if g.x() + m.width() > scr.right() - 8:
            g.setX(max(scr.left() + 8, scr.right() - m.width() - 8))
        m.move(g)
        m.show()

    def _on_tags_changed(self, qid):
        """标签改完：下拉的题数、当前筛选结果、列表条目（tooltip）都要跟上。"""
        self._build_tag_combo()
        self._build_shape_combo()
        # 改的可能正是当前筛的那个标签 —— 重跑筛选；列表保持选中行不跳
        self.apply_filter()
        self.refresh_stats()
        self.set_status("第 %d 题标签已更新" % int(qid))
        log.info("手动改标签 #%d", int(qid))

    def _progress_list_snapshot(self):
        """一次读取列表要用的进度副本，避免逐题复制整张进度表。"""
        return {
            "done": set(PG._done_ids()),
            "history": PG._history(),
            "wrong": PG._wrong(),
        }

    @staticmethod
    def _list_state_signature(progress, notes_revision, questions=()):
        question_rows = tuple(
            (int(row["id"]), str(row.get("source", "") or ""))
            for row in questions)
        history = tuple(sorted(
            (str(qid), int(info.get("count", 0) or 0),
             str(info.get("last", "") or ""))
            for qid, info in progress["history"].items()
            if isinstance(info, dict)))
        wrong = tuple(sorted(
            (str(qid), int(info.get("n", 0) or 0),
             str(info.get("last", "") or ""),
             str(info.get("detail", "") or ""))
            for qid, info in progress["wrong"].items()
            if isinstance(info, dict)))
        return (question_rows, tuple(sorted(progress["done"])), history, wrong,
                notes_revision)

    def _item(self, q, progress=None, note_ids=None):
        """列表条目：已做打勾、未做留空心圈，并配不同颜色。"""
        qid = int(q["id"])
        if progress is None:
            done = PG.is_done(qid)
            wrong = PG.is_wrong(qid)
            history = PG.info(qid)
            wrong_info = PG.wrong_info(qid)
        else:
            done = qid in progress["done"]
            wrong_info = progress["wrong"].get(str(qid), {})
            wrong = str(qid) in progress["wrong"]
            history = progress["history"].get(str(qid), {})
        has_note = (NT.has(qid) if note_ids is None else qid in note_ids)
        mark = "✓" if done else "○"
        note_tag = "  ✎" if has_note else ""
        wrong_tag = "  ✗错题" if wrong else ""
        it = QListWidgetItem("  %s  #%03d    %s%s%s"
                             % (mark, q["id"], q["source"], wrong_tag, note_tag))
        if done:
            # 已做的整行压成绿色，扫一眼就能分出做过 / 没做过
            it.setForeground(QColor(T.C_GREEN))
            f = QFont()
            f.setBold(True)
            it.setFont(f)
            it.setToolTip("已做　·　%s" % (history.get("last") or "—"))
        else:
            it.setForeground(QColor(T.P["ink2"]))
            it.setToolTip("未做")
        if wrong:
            # 错题用红色盖过"已做"的绿色：这是更该被注意到的状态
            it.setForeground(QColor(T.C_RED))
            tip = "错题　·　错过 %d 次　·　%s" % (
                int(wrong_info.get("n", 0) or 0),
                wrong_info.get("last") or "—")
            det = (wrong_info.get("detail") or "").strip()
            if det:
                tip += "\n" + det
            it.setToolTip(tip)
        if has_note:
            tip = it.toolTip()
            it.setToolTip((tip + "\n有备忘录") if tip else "有备忘录")
        return it

    def refresh(self, keep_row=False):
        row = self.listw.currentRow() if keep_row else 0
        progress = self._progress_list_snapshot()
        notes_revision = NT.revision()
        note_ids = NT.has_any(q["id"] for q in self.view)
        self._last_list_state_signature = self._list_state_signature(
            progress, notes_revision, self.view)
        signals_blocked = self.listw.blockSignals(True)
        self.listw.setUpdatesEnabled(False)
        try:
            self.listw.clear()
            for q in self.view:
                self.listw.addItem(self._item(q, progress, note_ids))
        finally:
            self.listw.setUpdatesEnabled(True)
            self.listw.blockSignals(signals_blocked)
        if self.view:
            self.listw.setCurrentRow(min(max(row, 0), len(self.view) - 1))
        else:
            self.show_empty()
        self._refresh_anna_banner_stats()

    def touch(self, qid):
        """记一次练习。

        刻意刷新列表但保留当前选中行 —— 否则用户打开工作台后，
        左侧列表会跳回第 1 题，看起来像"点一下就乱跳"。
        """
        PG.mark(qid)
        self.refresh_stats()
        self.refresh(keep_row=True)
        from flight_motion import show_completion_stamp
        show_completion_stamp(self.b_done)

    # --------------------------------------------------- 延迟记账
    def arm_record(self, qid):
        """打开题卡先不下笔，看满 N 秒才真的记一次。

        以前是「一打开就记账」：随手点开瞄一眼、切题时路过一下，
        统统算成练过，进度看着挺漂亮，实际一题没过脑子。改成延时后，
        账本上留下的才是真盯着看够时间的。
        时长可配（record_delay_sec），填 0 就退回原来的立即记账。
        """
        self.cancel_record()
        try:
            sec = int(cfg.get("record_delay_sec", 60) or 0)
        except Exception:
            sec = 60
        qid = int(qid)
        if sec <= 0:
            self.touch(qid)
            return
        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(lambda: self.commit_record(qid))
        t.start(sec * 1000)
        self._pend = (qid, t)
        self.set_status("第 %d 题 · 看满 %d 秒自动记为已做" % (qid, sec))

    def cancel_record(self):
        """放弃这一次记账。切题 / 关工作台时用 —— 已经到点的不会被撤。"""
        if not self._pend:
            return
        _qid, t = self._pend
        self._pend = None
        try:
            t.stop()
            t.deleteLater()
        except RuntimeError:
            pass                      # 定时器已被 Qt 回收，无所谓

    def commit_record(self, qid):
        if not self._pend or int(self._pend[0]) != int(qid):
            return                    # 期间切过题，这次轮不到你
        self._pend = None
        self.touch(qid)
        self.set_status("第 %d 题 · 停留满 %d 秒，已自动记为已做"
                        % (int(qid), int(cfg.get("record_delay_sec", 60) or 60)))
        log.info("延迟记账到点 #%d", int(qid))
        # 自动记账后同步工作台底栏：以前只有列表会变，工作台里的
        # 「标记已做」按钮纹丝不动，用户以为没记上、又手动点了一下 ——
        # 结果 toggle 反而把刚记上的撤销了。状态必须两边一致。
        if self.fig_win is not None and self.fig_win._qid == int(qid):
            try:
                self.fig_win._sync_done()
            except RuntimeError:
                pass                  # 工作台恰好被回收，无所谓

    def show_empty(self):
        self._detail_revision += 1
        self._detail_timer.stop()
        self._pending_detail = None
        self._figure_revision += 1
        for task in self._figure_jobs.values():
            task.cancelled_flag = True
        self.cur = None
        busy = self._import_phase in ("processing", "selecting", "review")
        if busy:
            titles = {"processing": "正在导入题卡", "selecting": "请选择需要导入的页面",
                      "review": "等待确认导入内容"}
            title = titles[self._import_phase]
            message = "题卡正在处理，确认导入后会显示在题库中。当前题库数据尚未改变。"
            action = ""
        elif not self.all:
            title = "题库中还没有题卡"
            message = "点击“导入题卡”选择 PDF 或图片，也可以拖入文件。支持一次选择多个文件。"
            action = "<a href='azimuth:import'>导入题卡</a>"
        else:
            title = "没有匹配的题目"
            message = "当前搜索或筛选没有找到题目。清除条件后可以恢复全部题目。"
            action = "<a href='azimuth:clear'>清除搜索和筛选</a>"
        self.d_title.setText(title)
        self.d_sub.setText("")
        self.body.setHtml(
            "<div style='color:%s;font-size:14px;'>"
            "%s</div><p>%s</p>" % (T.P["ink2"], message, action))
        self.b_empty_action.setText("清除筛选" if self.all else "导入题卡")
        self.b_empty_action.setVisible(not busy)
        self.b_retry.hide()
        self.paper_preview.clear()
        self.paper_preview.setFixedSize(1, 1)
        self.fig.setPixmap(QPixmap())
        self.fig.setText("导入确认后显示题图" if busy else "暂无题卡预览")
        self.fig.setMinimumSize(0, 0)
        self.fig.setMaximumSize(16777215, 16777215)
        self.original_sheet.setMinimumSize(0, 0)
        self.original_sheet.setMaximumSize(16777215, 16777215)
        self.fig.setMinimumHeight(0)
        self.fig.setMaximumHeight(16777215)

    def _fig_path(self, qid):
        """返回题卡图的用户数据路径。"""
        try:
            return BD.fig_path(qid)
        except Exception:
            return ""

    def _highlight_query(self):
        keyword = self.q.text().strip()
        selections = []
        if keyword:
            document = self.body.document()
            cursor = document.find(keyword)
            while not cursor.isNull() and len(selections) < 100:
                selection = QTextEdit.ExtraSelection()
                selection.cursor = cursor
                color = QColor(T.C_BLUE)
                color.setAlpha(42)
                selection.format.setBackground(color)
                selections.append(selection)
                cursor = document.find(keyword, cursor)
        self.body.setExtraSelections(selections)

    def on_select(self, row):
        if row < 0 or row >= len(self.view):
            return
        q = self.view[row]
        self.cur = q
        self.b_empty_action.hide()
        self.b_retry.hide()
        self.b_done.setText("取消已做" if PG.is_done(q["id"]) else "标记已做")
        self.frequency_ruler.set_position(row + 1, len(self.view))
        self._render_paper_card()
        self.d_title.setText(q.get("title") or "题目 #%03d" % int(q["id"]))
        self.d_stamp.setVisible(T.app_theme in ("chiyo", "industrial"))
        number = self.view.index(q) + 1 if q in self.view else int(q["id"])
        self.d_stamp.setText(("%03d" if T.app_theme == "industrial" else "%02d")
                             % number)
        self.d_sub.setText(q["source"])
        html = self._question_detail_html(q)
        # 先绘制题目正文，再延后计算参考答案与解码题卡图，避免点击回调阻塞首帧。
        self.body.setHtml(html)
        self._highlight_query()
        self._detail_revision += 1
        revision = self._detail_revision
        qid = str(q["id"])
        self._pending_detail = (qid, html, revision)
        # 让正文先完成当前事件循环中的首帧，再补参考答案和异步配图；
        # 页面离开时可明确取消，不留下匿名 singleShot 在后台工作。
        self._detail_timer.start(0)

        # 选中题目即起表：未做过的题在详情页停留满 N 秒，自动记「已做」。
        # 不用打开题卡工作台，也不用点任何按钮 —— 这是这一版把记账锚点
        # 从「打开工作台」前移到「选中题目」的原因：以前只盯着工作台计时，
        # 用户在详情页把题看明白了一分钟，账上却什么都没有。
        if self._ready:
            if self.isVisible():
                cfg.set_("last_question_id", q["id"])
            if PG.is_done(q["id"]):
                # 已做的不再重复计时（避免练习次数虚涨），
                # 但上一题没到期的计时必须掐掉，不然它会替旧题记账。
                self.cancel_record()
            else:
                self.arm_record(q["id"])

    def _finish_pending_detail(self):
        pending = self._pending_detail
        self._pending_detail = None
        if pending is not None and self.isVisible():
            self._finish_question_detail(*pending)

    def _finish_question_detail(self, qid, base_html, revision):
        """题面首帧显示后，再补上答案与题卡图；忽略过期的快速连点任务。"""
        if revision != self._detail_revision or not self.cur:
            return
        if str(self.cur.get("id")) != str(qid):
            return
        reference = self._ref_block(self.cur)
        if reference:
            self.body.setHtml(base_html + reference)
            self._highlight_query()
        self._render_fig(revision)

    def _ref_block(self, q):
        """详情页的参考答案 —— **默认收起**的一段可展开内容。

        ★ 为什么默认收起：五三明确"避免还没做了就已经知道答案了"。
          详情页是打开就能看到的地方，摊开等于直接泄题。
        ★ 手动标注的优先显示（标了就在标题上打星，让用户知道这条是他自己的）。
        """
        import ref_panel as RP
        if q.get("imported"):
            source = q.get("reference_source") or q.get("_reference_source") or {}
            answer = source.get("direct_answer") if isinstance(source, dict) else None
            import qmeta as QM
            manual = QM.answer_of(q.get("id"))
            if manual:
                title = "你自己标注的参考答案"
                summary = RP._esc(manual[:260])
                col = T.C_GREEN
            elif answer:
                title = "视觉 API 已生成参考答案"
                summary = RP._esc((answer.get("summary") or "模型已完成直接作答。")[:260])
                col = T.P["warn_fg"]
            else:
                title = "等待视觉 API 直接作答"
                summary = "在题卡工作台中点击“API作答”，由所选服务商模型审题并生成答案。"
                col = T.P["ink3"]
            return (
                "<div style='margin-top:4px;padding-top:10px;border-top:1px dashed %s;'>"
                "<div style='color:%s;font-size:12px;font-weight:700;'>%s</div>"
                "<div style='color:%s;font-size:11px;margin-top:4px;'>%s</div></div>"
                % (T.P["glass_soft_edge"], col, title, T.P["ink3"], summary))
        import reference_engine as RE
        try:
            _body, meta = RP.render(q, reference=RE.solve_reference(q))
        except Exception as e:
            log.warning("参考答案渲染失败 #%s: %r", q.get("id"), e)
            return ""
        own = meta.get("source") == "manual"
        ready = meta.get("status") == "READY"
        title = ("★ 参考答案（你自己标注的）" if own else
                 "参考答案已核验" if ready else "参考答案需校对")
        col = T.C_GREEN if own or ready else T.P["warn_fg"]
        # QTextBrowser 没法做"点击展开"的真交互 —— 用 <details> 不可靠，
        # 所以这里给的是**收起态提示 + 一行页内锚点**，真正的展开交互
        # 交给「题卡工作台」里的浮层（那是主入口）。
        return ("<div style='margin-top:4px;padding-top:10px;border-top:1px dashed "
                "%s;'>"
                "<div style='color:%s;font-size:12px;font-weight:700;'>%s</div>"
                "<div style='color:%s;font-size:11px;margin-top:4px;'>"
                "在「题卡工作台」里点「参考答案」查看完整内容"
                "（含关键点坐标 / 飞行参数 / 其它要求）</div></div>"
                % (T.P["glass_soft_edge"], col, title, T.P["ink3"]))

    @Slot()
    def _render_fig(self, revision=None):
        if getattr(self, "_card_layout", "standard") == "paper":
            if self.cur:
                path = self._fig_path(self.cur["id"])
                self.b_retry.setVisible(not os.path.isfile(path) or QPixmap(path).isNull())
                if self.b_retry.isVisible():
                    self.set_status("原题卡图片无法读取，仿真题卡图形缺失；请重试或重新导入。")
                else:
                    self._render_paper_card()
            self._resize_paper_sheet()
            return
        """预览区：完整展示配图，绝不裁切。

        策略：以「可用宽度」为基准等比缩放（宽度是瓶颈时优先保证横向不溢出），
        再按缩放后的宽高比反推标签高度 —— 纵向装不下就交给外层滚动条，
        而不是像以前那样按控件高度硬压，把底部文字压没。"""
        if not self.cur:
            return
        fp = self._fig_path(self.cur["id"])
        if not os.path.exists(fp):
            self.fig.setPixmap(QPixmap())
            self._figure_error("题卡图片不存在，可能被移动或删除。请重试或重新导入原文件。")
            return
        vp = self.fig_scroll.viewport()
        aw = max(1, round((vp.width() - 20) * self._preview_zoom))
        ah = max(1, round((vp.height() - 20) * self._preview_zoom))
        if vp.width() <= 20 or vp.height() <= 20:
            return
        try:
            key = (fp, aw, ah, os.path.getmtime(fp), self._preview_fit)
        except OSError:
            self._figure_error("题卡图片暂时无法读取。请检查文件是否还在原位置。")
            return
        cached = self._figure_cache.get(key)
        if cached is not None:
            self._figure_cache.move_to_end(key)
            self._show_figure(cached)
            return
        if any(task.key == key and not getattr(task, "cancelled_flag", False)
               for task in self._figure_jobs.values()):
            return
        if getattr(self, "_failed_figure_key", None) == key:
            return
        self.b_retry.hide()
        # 解码时保留原尺寸，避免滚动条反复变化触发新的解码任务。
        if getattr(self, "_displayed_figure_path", None) != fp:
            self.fig.clear()
            self.fig.setText("正在载入题卡…")
            self.fig.setFixedSize(max(160, vp.width() - 20), 110)
            self.original_sheet.setFixedSize(self.fig.size())
            self._displayed_figure_path = fp
        self._figure_revision += 1
        token = self._figure_revision
        # 运行中的旧任务只做一次轻量令牌检查；新选择不会排队等待旧图解码。
        for old_token, task in tuple(self._figure_jobs.items()):
            if old_token != token:
                task.cancelled_flag = True
        task = _FigureDecodeTask(
            token, fp, (aw, ah), lambda job=None, t=token: (
                getattr(self._figure_jobs.get(t), "cancelled_flag", False)
                or t != self._figure_revision), fit_mode=self._preview_fit)
        task.key = key
        task.signals.ready.connect(self._figure_ready)
        self._figure_jobs[token] = task
        QThreadPool.globalInstance().start(task)

    @Slot(int, str, object)
    def _figure_ready(self, token, path, image):
        task = self._figure_jobs.pop(token, None)
        if task is None or token != self._figure_revision or not self.isVisible():
            return
        if not self.cur or self._fig_path(self.cur["id"]) != path:
            return
        if image is None or image.isNull():
            self._failed_figure_key = task.key
            self._figure_error(getattr(task, "error", "") or "图片损坏或格式无法识别，请重新导入。")
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._figure_error("题卡图片转换失败，请重试。")
            return
        self._figure_cache[task.key] = pixmap
        self._figure_cache.move_to_end(task.key)
        while len(self._figure_cache) > 24:
            self._figure_cache.popitem(last=False)
        self._show_figure(pixmap)

    def _show_figure(self, pixmap):
        if pixmap.isNull():
            return
        self.fig.setText("")
        self._failed_figure_key = None
        self._displayed_figure_path = self._fig_path(self.cur["id"])
        self.b_retry.hide()
        self.fig.setPixmap(pixmap)
        if (getattr(self, "_card_layout", "standard") == "paper" and
                getattr(self, "paper_diagram", None) is not None):
            source_path = self._fig_path(self.cur["id"])
            original = QPixmap(source_path) if os.path.isfile(source_path) else pixmap
            self.paper_diagram.set_question(self.cur, original)
        self.fig.setFixedSize(pixmap.size())
        self.original_sheet.setFixedSize(pixmap.size())

    def _figure_error(self, reason):
        """读取失败要能恢复，不把错误结果留在无限加载状态。"""
        self.fig.clear()
        self.fig.setText("题卡读取失败\n" + str(reason))
        self.fig.setWordWrap(True)
        self.fig.setFixedSize(max(160, self.fig_scroll.viewport().width() - 20), 110)
        self.original_sheet.setFixedSize(self.fig.size())
        self.b_retry.show()
        self.set_status("题卡读取失败，可点击预览区上方的“重试”。")

    def step(self, delta):
        r = self.listw.currentRow() + delta
        if 0 <= r < len(self.view):
            self.listw.setCurrentRow(r)
            self.listw.scrollToItem(self.listw.item(r))

    def goto_question(self, qid):
        """跳到指定题号的详情页（供工作台"切到第 X 题"用）。

        两个坑：
        1) 当前筛选（比如「只看未做」「只看错题」）可能**把目标题筛掉了** ——
           这时在 self.view 里找不到它，得先把筛选复位回「全部」，否则
           setCurrentRow 找不到行、静默什么都不做。
        2) 目标题不在 self.view 里时**别乱选一行** —— 宁可什么都不做，
           也好过把用户带到一道无关的题上（那正是本次要修的毛病）。
        """
        try:
            qid = int(qid)
        except (TypeError, ValueError):
            return False
        row = None
        for i, q in enumerate(self.view):
            if int(q.get("id", -1)) == qid:
                row = i
                break
        if row is None:
            # 被筛选挡掉了 —— 复位到「全部」再找一次
            self._pick_progress("all")
            for i, q in enumerate(self.view):
                if int(q.get("id", -1)) == qid:
                    row = i
                    break
        if row is None:
            return False
        self.listw.setCurrentRow(row)
        it = self.listw.item(row)
        if it is not None:
            self.listw.scrollToItem(it)
        return True

    # --------------------------------------------------- 题卡工作台
    def _workbench_pixmap(self):
        """按当前题库显示的排版生成工作台画面；原始题卡继续直接用源图。"""
        if not self.cur:
            return QPixmap()
        if getattr(self, "_card_layout", "standard") != "paper":
            path = self._fig_path(self.cur["id"])
            return QPixmap(path) if os.path.exists(path) else QPixmap()

        self._render_paper_card()
        sheet = self.paper_sheet
        sheet.ensurePolished()
        size = sheet.size()
        if size.width() < 2 or size.height() < 2:
            return QPixmap()
        # 以两倍分辨率绘制，保留页面中文字的清晰度。
        scale = 2
        pixmap = QPixmap(size.width() * scale, size.height() * scale)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.scale(scale, scale)
        sheet.render(painter, QPoint(0, 0))
        painter.end()
        return pixmap

    def open_figure_win(self):
        if not self.cur:
            log.warning("打开题卡工作台失败：当前没有选中题目")
            return
        qid = self.cur.get("id")
        try:
            pm = self._workbench_pixmap()
        except Exception as exc:
            log.exception("打开题卡工作台 #%s：生成题卡图像失败", qid)
            QMessageBox.warning(self, "题卡暂时无法打开",
                                "生成练习画面时遇到问题，请重试。\n\n%s" % str(exc)[:240])
            return
        if pm.isNull():
            path = self._fig_path(qid) if getattr(self, "_card_layout", "standard") != "paper" else ""
            log.error("打开题卡工作台 #%s：题图为空，路径=%r，存在=%s，布局=%s",
                      qid, path, bool(path and os.path.isfile(path)),
                      getattr(self, "_card_layout", "standard"))
            QMessageBox.information(self, "题卡图片无法读取",
                                    "没有生成可用的练习画面，请检查题卡图片或重新导入该页。")
            return
        if self.fig_win is None:
            from figure_board import FigureDialog
            self.fig_win = FigureDialog(
                scale=float(cfg.get("ui_scale", 1.0) or 1.0))
            self.fig_win.step_cb = self._fig_step
            self.fig_win.done_changed.connect(self._on_done_changed)
            # ★ v1.9.10：判题记账变了（尤其判**错**时 done 不发），统计卡的
            #   「错题」数要跟着刷 —— 否则错题集里 3 道、主页显示 0。
            self.fig_win.wrong_changed.connect(self.refresh_stats)
            # 题库全量给工作台，供「读软件判题」反查题目用
            # （不能给 self.view —— 那是筛选后的，题干可能不在里面）
            self.fig_win.qbank_cb = lambda: list(self.all)
            # 判题发现「你画的跟你开的不是同一题」时，允许工作台把详情页
            # 切到正确那道题 —— 否则用户得自己翻列表找，一错又容易判错。
            self.fig_win.go_question_cb = self.goto_question
            # 在工作台里标注了参考答案 → 详情页也跟着重渲染（标注优先显示）
            self.fig_win.ref_saved_cb = self._on_ref_saved
            main_window = self.window()
            self.fig_win.settings_cb = lambda host=main_window: host.switch(3) \
                if callable(getattr(host, "switch", None)) else None
        # 计时在 on_select（选中题目）时就已经起表了，这里不再重新起 ——
        # 重新起等于把用户在详情页已经看掉的时间清零；打开工作台只是
        # 换个地方继续看同一道题，计时必须连续。
        # 同理，关闭工作台也不再掐表：关掉只是回到详情页，题还在眼前。
        try:
            self.fig_win.set_question(qid, self.cur["source"], pm, self.cur)
            self.fig_win.theme_changed()
            show_window_animated(self.fig_win)
            self.fig_win.show()
            self.fig_win.raise_()
            self.fig_win.activateWindow()
            log.info("打开题卡工作台 #%d（图片 %dx%d）", qid,
                     pm.width(), pm.height())
        except Exception as exc:
            log.exception("打开题卡工作台 #%s 失败", qid)
            QMessageBox.warning(
                self, "题卡暂时无法打开",
                "练习窗口启动失败，请重试。错误已写入运行日志。\n\n%s" %
                str(exc)[:240])

    def open_note(self):
        """打开当前题的备忘录弹窗（文字笔记 + 截图）"""
        if not self.cur:
            return
        title = "第 %d 题 · 备忘录" % self.cur["id"]
        if self.note_win is None:
            from note_dialog import NoteDialog
            self.note_win = NoteDialog(
                self.cur["id"], title,
                scale=float(cfg.get("ui_scale", 1.0) or 1.0))
            self.note_win.note_changed.connect(self._on_note_changed)
        else:
            self.note_win.set_question(self.cur["id"], title)
        self.note_win.show()
        self.note_win.raise_()
        self.note_win.activateWindow()
        log.info("打开备忘录 #%d", self.cur["id"])

    def _on_note_changed(self):
        """备忘录增删后刷新列表里的 ✎ 标记"""
        self.refresh(keep_row=True)

    def _on_done_changed(self, qid, done):
        """工作台里点了「标记已做 / 取消」——同步列表与统计"""
        self.refresh_stats()
        self.refresh(keep_row=True)
        self.set_status("第 %d 题已标记为%s" % (qid, "已做" if done else "未做"))

    def _on_ref_saved(self, qid):
        """工作台里标注了参考答案 —— 详情页重渲染（标注优先于自动生成）"""
        if self.cur and int(self.cur.get("id", -1)) == int(qid):
            self.on_select(self.listw.currentRow())
        self.set_status("第 %d 题参考答案已标注" % qid)

    # --------------------------------------------------- 页面显隐
    def hideEvent(self, ev):
        if self._filter_anim is not None:
            self._finish_filter_motion()
        # 切去别的页（计时考试 / 设置）时，题虽然还「选中」着，人已经不在看
        # 了 —— 计时继续跑会在用户回来前偷偷把题记成已做，必须掐掉。
        if self._return_refresh_timer is not None:
            self._return_refresh_timer.stop()
        self.cancel_record()
        self._detail_timer.stop()
        self._figure_revision += 1
        for task in self._figure_jobs.values():
            task.cancelled_flag = True
        super().hideEvent(ev)

    def showEvent(self, ev):
        super().showEvent(ev)
        if self.cur:
            QTimer.singleShot(0, self._render_fig)
        # 回到题库页：重新给当前选中的题起表（未做过的才开始计时）
        if self._ready and self.cur and not PG.is_done(self.cur["id"]):
            self.arm_record(self.cur["id"])

    @staticmethod
    def _question_detail_html(q):
        """按当前调色板构建详情正文，换外观时可安全重绘缓存 HTML。"""
        def block(title, text, color):
            if not text:
                return ""
            safe = (text.replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace("\n", "<br>"))
            return ("<div style='margin-bottom:14px;'>"
                    "<div style='color:%s;font-size:13px;font-weight:800;"
                    "letter-spacing:1px;margin-bottom:6px;'>%s</div>"
                    "<div style='color:%s;font-size:14px;line-height:180%%;'>"
                    "%s</div></div>"
                    % (color, title, T.P["ink"], safe))

        html = ""
        source = "\n".join(str(q.get(key, "")) for key in ("conditions_text", "requirements_text", "suggested_text"))
        tags = [name for name, words in (("航向", ("航向", "方向", "偏东", "偏西")),
            ("风向", ("风向", "顺风", "逆风", "侧风")),
            ("避让与过渡", ("避让", "过渡点", "空域")),
            ("夹角", ("夹角", "∠", "内角", "外角"))) if any(word in source for word in words)]
        if tags:
            html += ("<div style='color:%s;font-size:12px;margin-bottom:16px;'>题面提及 · %s</div>"
                     % (T.P["accent_fg"], " / ".join(tags)))
        if q.get("graph_hint"):
            html += ("<div style='color:%s;font-size:12px;margin-bottom:12px;'>"
                     "题卡点位：%s</div>" % (T.P["ink3"], q["graph_hint"]))
        html += block("附加条件", q["conditions_text"], T.P["warn_fg"])
        html += block("航线要求", q["requirements_text"], T.C_BLUE)
        html += block("建议参数", q["suggested_text"], T.P["ok_fg"])
        return html

    def set_status(self, txt):
        """把状态文本转给主窗口的状态栏。

        坑：不能直接写 `self.window().set_status(...)` —— BankPage 自己就有一个
        set_status，当它作为顶层窗口存在时 window() 返回的就是它自己，
        于是自己调自己，直接 RecursionError（自检里实测踩到）。
        """
        w = self.window()
        fn = getattr(w, "set_status", None)
        if callable(fn) and w is not self:
            fn(txt)

    def _fig_step(self, delta):
        r = self.listw.currentRow() + delta
        if not (0 <= r < len(self.view)):
            return
        old_scroll = self.listw.verticalScrollBar().value()
        self.listw.setCurrentRow(r)
        self.listw.verticalScrollBar().setValue(old_scroll)
        locate_item(self.listw, self.listw.item(r))
        q = self.view[r]
        fp = self._fig_path(q["id"])
        pm = QPixmap(fp) if os.path.exists(fp) else None
        if pm is not None and not pm.isNull() and self.fig_win:
            # 切题时 on_select（setCurrentRow 触发）已经把新题的表起好了，
            # 这里不再重复 arm —— 重复 arm 只是白掐一次表，没意义。
            self.fig_win.set_question(q["id"], q["source"], pm, q)

    def random_pick(self):
        if not self.view:
            return
        q = random.choice(self.view)
        try:
            r = self.view.index(q)
        except ValueError:
            return
        old_scroll = self.listw.verticalScrollBar().value()
        self.listw.setCurrentRow(r)
        self.listw.verticalScrollBar().setValue(old_scroll)
        locate_item(self.listw, self.listw.item(r))
        accent(self.d_title)
        self.set_status("已随机抽到 第 %d 题　%s" % (q["id"], q["source"]))
        log.info("随机抽题 #%d %s", q["id"], q["source"])

    # --------------------------------------------------- 更多菜单
    def _rounded_filter_region(self, height):
        height = max(1, min(self.fb.height(), int(height)))
        radius = min(14.0, height / 2.0)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.fb.width(), height),
                            radius, radius)
        return QRegion(path.toFillPolygon().toPolygon())

    def _finish_filter_motion(self):
        motion, self._filter_anim = self._filter_anim, None
        if motion is not None:
            motion.stop()
            motion.deleteLater()
        layer = getattr(self, "_filter_layer", None)
        self._filter_layer = None
        self.motion_surface.show()
        if layer is not None:
            layer.hide()
            layer.deleteLater()

    def _animate_filter_panel(self, opening):
        """只提交一次真实布局，再用缓存表现筛选展开及下方题卡位移。"""
        surface = self.motion_surface
        previous = getattr(self, "_filter_layer", None)
        before = previous.grab() if previous is not None else surface.grab()
        start_y = (previous.current_y if previous is not None else
                   self.splitter.mapTo(surface, QPoint(0, 0)).y())
        self._finish_filter_motion()
        self.filter_panel.setMaximumHeight(16777215 if opening else 0)
        self.filter_panel.setVisible(bool(opening))
        self._filter_open = bool(opening)
        self.fb.layout().activate()
        self.root_layout.activate()
        surface.layout().activate()
        if not animations_enabled() or not self.isVisible():
            return
        after = surface.grab()
        end_y = self.splitter.mapTo(surface, QPoint(0, 0)).y()
        if before.isNull() or after.isNull() or start_y == end_y:
            return
        layer = _FilterMotionSnapshot(before, after, start_y, end_y, opening, self)
        layer.setGeometry(surface.geometry())
        self._filter_layer = layer
        layer.show()
        layer.raise_()
        surface.hide()
        motion = QVariantAnimation(self)
        motion.setDuration(520)
        motion.setStartValue(0.0)
        motion.setEndValue(1.0)
        motion.setEasingCurve(QEasingCurve.InOutCubic)
        motion.valueChanged.connect(layer.advance)
        self._filter_anim = motion
        motion.finished.connect(self._finish_filter_motion)
        motion.start()

    def show_more_menu(self):
        """「更多」下拉：原来平铺的三个按钮 + 导入的二级入口。

        v1.9.7 精简版面用 —— 筛选栏从 8 个控件降到 6 个。
        """
        submenu = getattr(self, "_import_menu", None)
        if submenu is not None and submenu.isVisible():
            submenu.toggle_from(self.b_more)
            return
        m = getattr(self, "_more_menu", None)
        if m is None:
            m = ImportMenu([("wrong", "错题集", "warn"),
                            ("trend", "成绩趋势", "chart"),
                            ("manage", "管理已导入题卡", "folder")], self)
            self._more_menu = m
            m.picked.connect(self._on_more_pick)
        m.toggle_from(self.b_more)

    def _on_more_pick(self, key):
        # ★ 诊断埋点（用户报“错题集/成绩趋势/导入题目点不开”）：先记下回调有没有被触发，
        #   再包一层异常记录 —— 这样日志能区分“点击没分发到”与“函数里抛了异常”。
        try:
            import logging as _lg
            _lg.getLogger("azimuth").info("更多菜单点击：%s", key)
        except Exception:
            pass
        if key == "manage":
            self.manage_imported()
            return
        if key == "import":
            # 导入再分一层（图片 / PDF / 管理）—— 贴着「更多」按钮展开
            # 收回时回到仍可见的按钮，避免飞向已经消失的菜单行。
            self.import_files()
        else:
            # ★ 传 b_more 当锚点 —— 浮层贴着「更多」按钮弹，用户一眼看出
            #   它是从这个按钮出来的（原来相对主窗口居中，飘在屏幕中间）。
            handler = {"wrong": self.show_wrong_set,
                       "trend": self.show_trend}.get(key)
            if handler is None:
                try:
                    import logging as _lg
                    _lg.getLogger("azimuth").warning("更多菜单未知项：%s", key)
                except Exception:
                    pass
                return
            try:
                handler(self.b_more)
                try:
                    import logging as _lg
                    _lg.getLogger("azimuth").info("更多菜单打开成功：%s", key)
                except Exception:
                    pass
            except Exception as error:
                import traceback
                try:
                    import logging as _lg
                    _lg.getLogger("azimuth").error(
                        "更多菜单打开失败：%s -> %s", key, traceback.format_exc())
                except Exception:
                    pass

    # --------------------------------------------------- 离线导入
    def _show_import_menu(self, anchor=None):
        """打开题卡文件导入或已导入文件管理。"""
        m = getattr(self, "_import_menu", None)
        if m is None:
            m = ImportMenu([("files", "选择题卡文件…", "image"),
                            ("manage", "管理已导入…", "trash")], self)
            self._import_menu = m
            m.picked.connect(self._on_import_pick)
        m.open_from(anchor or self.b_more)


    def _on_import_pick(self, key):
        {"files": self.import_files,
         "manage": self.manage_imported}.get(key, lambda: None)()

    def _max_id(self):
        ids = [int(str(q["id"]).strip()) for q in self.all
               if str(q.get("id", "")).strip().isdigit()]
        return max(ids) if ids else 0

    def _append_imported(self, new_items):
        """写回用户题库并整份重载 —— 下拉框、统计卡、列表一起刷新"""
        if not BD.save(BD.load() + list(new_items)):
            raise OSError("用户题库写入失败")
        self.load()
        log.info("导入完成，题库合计 %d 题", len(self.all))

    def _review_and_commit_import(self, staged, warnings=None):
        self._import_commit_outcome = "canceled"
        dialog = IM.ImportReviewDialog(staged, warnings or [], self)
        committed_files = []
        try:
            if dialog.exec() != dialog.DialogCode.Accepted:
                return 0
            selected = dialog.selected_questions()
            next_id = BD.next_id(self._max_id())
            additions = []
            import_records = {}
            for q in selected:
                source = q.get("_staged_figure", "")
                if not source or not os.path.exists(source):
                    raise OSError("有页面的暂存题图已经失效，请重新选择文件。")
                qid = next_id
                next_id += 1
                dest = BD.fig_path(qid)
                image = QImage(source)
                if not IM.save_figure(image, dest):
                    raise OSError(f"题目 #{qid} 的图片保存失败。")
                committed_files.append(dest)
                q["id"] = qid
                source_meta = q.pop("_reference_source", {})
                corrections = q.pop("_reference_corrections", {})
                source_id = str(source_meta.get("sha256") or "")
                if source_id:
                    q["source_id"] = source_id
                    record = import_records.setdefault(source_id, {
                        "source_id": source_id,
                        "sha256": source_id,
                        "file_name": source_meta.get("file_name") or
                                     os.path.basename(source),
                        "file_type": "PDF" if source_meta.get("kind") == "pdf"
                                     else "图片",
                        "size_bytes": int(source_meta.get("file_size") or 0),
                        "question_ids": [],
                        "status": "成功",
                        "failure_reason": "",
                    })
                    record["question_ids"].append(qid)
                if source_meta or corrections:
                    source_meta["reviewed_fields"] = {
                        key: q.get(key, "") for key in (
                            "graph_hint", "conditions_text", "requirements_text",
                            "suggested_text")}
                    if corrections:
                        source_meta["corrections"] = corrections
                    q["reference_source"] = source_meta
                for key in list(q):
                    if key.startswith("_"):
                        q.pop(key, None)
                q["imported"] = True
                additions.append(q)
            if not BD.save(BD.load() + additions):
                raise OSError("题目记录未写入，已回滚刚生成的题图。")
            if import_records and not BD.register_imported_sources(
                    list(import_records.values())):
                log.warning("题目已导入，但来源文件清单更新失败")
            self.load()
            log.info("确认导入 %d 题，题库合计 %d 题", len(additions), len(self.all))
            self._import_commit_outcome = "committed"
            return len(additions)
        except Exception as exc:
            self._import_commit_outcome = "failed"
            for path in committed_files:
                try:
                    os.remove(path)
                except OSError:
                    pass
            QMessageBox.warning(self, "导入未完成", str(exc))
            return 0
        finally:
            IM.cleanup_staged(staged)

    def import_images(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择题目图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;所有文件 (*.*)")
        if not paths:
            return
        self._begin_import_batch(paths)

    def import_files(self):
        """用一个多选窗口导入 PDF、图片或混合文件。"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择题卡文件", "",
            "题卡文件 (*.pdf *.png *.jpg *.jpeg *.bmp *.webp *.gif);;"
            "PDF 文件 (*.pdf);;图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;"
            "所有文件 (*.*)")
        if paths:
            self._begin_import_batch(paths)

    def _on_image_done(self, added, failed):
        batch = self._import_batch
        if batch is None:
            IM.cleanup_staged(added)
            return
        batch["staged"].extend(added)
        batch["warnings"].extend(failed)
        if self._imp_prog and self._imp_prog.wasCanceled():
            self._finish_batch_worker()
            self._abort_import_batch()
            return
        for question in added:
            source = question.get("_reference_source") or {}
            if source.get("sha256"):
                batch["known_hashes"].add(str(source["sha256"]).lower())
        batch["file_progress"] = len(batch["images"]) * 100
        self._finish_batch_worker()
        self._start_next_pdf()

    def _on_image_error(self, msg):
        batch = self._import_batch
        if batch is not None:
            batch["warnings"].append("图片批次处理失败：" + str(msg))
            self._finish_batch_worker()
            self._start_next_pdf()

    def import_pdf(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择 PDF 题卡", "", "PDF 文件 (*.pdf);;所有文件 (*.*)")
        if not paths:
            return
        self._begin_import_batch(paths)

    def _begin_import_batch(self, paths):
        """把多选或拖入的图片、PDF 放进同一批次后台处理。"""
        paths = [os.path.abspath(str(path)) for path in paths or []
                 if os.path.isfile(str(path))]
        supported = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}
        paths = [path for path in paths
                 if os.path.splitext(path)[1].lower() in supported]
        if not paths:
            return
        if self._import_batch is not None:
            QMessageBox.information(self, "正在导入", "当前批次完成后再开始下一批导入。")
            return
        if not BD.writable():
            QMessageBox.warning(self, "无处可写", "当前目录不可写，导入的题卡无法保存。")
            return
        pdfs = [path for path in paths if path.lower().endswith(".pdf")]
        if pdfs and not IM.has_pdf_engine():
            QMessageBox.warning(
                self, "缺少 PDF 引擎",
                "当前版本没有 PDF 解析组件。请改用包含 PyMuPDF 的完整版本。")
            return
        # 题面上的日期和地区会优先自动识别；没有识别到时，在导入校对页
        # 再按单题或勾选批量补充，避免每次导入前都被来源弹窗打断。
        tag = "本地导入"
        images = [path for path in paths if path not in pdfs]
        self._last_import_paths = list(paths)
        total = len(images) + len(pdfs)
        progress = IM.ImportProgressDialog(total * 100, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        self._import_batch = {
            "images": images, "pdfs": pdfs, "pdf_index": 0,
            "staged": [], "warnings": [], "tag": tag,
            "file_progress": 0, "total_files": total,
            "known_hashes": set(BD.imported_source_hashes()),
        }
        self._imp_prog = progress
        self._set_import_phase("processing")
        self.set_status("正在导入题卡；识别完成后会进入校对，确认后才写入题库。")
        progress.canceled.connect(self._stop_import_worker)
        if images:
            thread = IM.ImageImportThread(images, tag, self._max_id() + 1, self)
            thread.step.connect(self._on_batch_image_step)
            thread.done.connect(self._on_image_done)
            thread.error.connect(self._on_image_error)
            self._imp_thread = thread
            thread.start()
            progress.setLabelText("正在异步识别 %d 张图片…" % len(images))
        else:
            self._start_next_pdf()
        progress.show()

    def _on_batch_image_step(self, current, total, name):
        batch = self._import_batch
        if batch is None or self._imp_prog is None:
            return
        value = min(len(batch["images"]), int(current)) * 100
        self._imp_prog.setValue(value)
        self._imp_prog.setLabelText("正在识别图片（%d / %d）：%s" % (
            int(current), int(total), name))

    def _stop_import_worker(self):
        worker = self._imp_thread
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                pass

    def _start_next_pdf(self):
        batch = self._import_batch
        if batch is None:
            return
        if self._imp_prog and self._imp_prog.wasCanceled():
            self._abort_import_batch()
            return
        index = batch["pdf_index"]
        if index >= len(batch["pdfs"]):
            self._finish_import_batch()
            return
        path = batch["pdfs"][index]
        self._set_import_phase("processing")
        thread = IM.PdfPreviewThread(path, self)
        thread.known_hashes.update(batch["known_hashes"])
        thread.step.connect(self._on_batch_pdf_preview_step)
        thread.done.connect(self._on_pdf_preview_done)
        thread.error.connect(self._on_pdf_error)
        self._imp_thread = thread
        if self._imp_prog:
            self._imp_prog.setLabelText("正在生成低清页面预览（%d / %d）：%s" % (
                index + 1, len(batch["pdfs"]), os.path.basename(path)))
        thread.start()

    def _on_batch_pdf_preview_step(self, current, total, status):
        batch = self._import_batch
        if batch is None or self._imp_prog is None:
            return
        base = len(batch["images"]) * 100 + batch["pdf_index"] * 100
        part = int(25 * int(current) / max(1, int(total)))
        self._imp_prog.setValue(min(batch["total_files"] * 100, base + part))
        self._imp_prog.setLabelText("PDF %d / %d　生成低清预览：%s（%d / %d 页）" % (
            batch["pdf_index"] + 1, max(1, len(batch["pdfs"])), status,
            int(current), int(total)))

    def _on_pdf_preview_done(self, preview, warns):
        batch = self._import_batch
        if batch is None:
            IM.cleanup_pdf_preview(preview)
            return
        if self._imp_prog and self._imp_prog.wasCanceled():
            IM.cleanup_pdf_preview(preview)
            self._finish_batch_worker()
            self._abort_import_batch()
            return

        batch["warnings"].extend(warns)
        pages = list((preview or {}).get("pages") or [])
        self._finish_batch_worker()
        if not pages:
            IM.cleanup_pdf_preview(preview)
            batch["pdf_index"] += 1
            self._start_next_pdf()
            return

        if self._imp_prog:
            self._imp_prog.hide()
        path = batch["pdfs"][batch["pdf_index"]]
        self._set_import_phase("selecting")
        dialog = IM.PdfPageSelectionDialog(
            preview, os.path.basename(path), self)
        accepted = dialog.exec() == dialog.DialogCode.Accepted
        selected_pages = dialog.selected_pages() if accepted else []
        source_hash = str((preview or {}).get("sha256") or "")
        IM.cleanup_pdf_preview(preview)

        if self._import_batch is not batch:
            return
        if not selected_pages:
            self._abort_import_batch()
            return

        base = len(batch["images"]) * 100 + batch["pdf_index"] * 100
        if self._imp_prog:
            self._imp_prog.setValue(base + 25)
            self._imp_prog.setLabelText(
                "已选择 %d 页，正在高清渲染、识别文字和图形…" %
                len(selected_pages))
            self._imp_prog.show()

        self._set_import_phase("processing")

        thread = IM.PdfImportThread(
            path, batch["tag"], self._max_id() + 1, self,
            page_numbers=selected_pages, source_hash=source_hash)
        thread.known_hashes.update(batch["known_hashes"])
        thread.step.connect(self._on_batch_pdf_step)
        thread.done.connect(self._on_pdf_done)
        thread.error.connect(self._on_pdf_error)
        self._imp_thread = thread
        thread.start()

    def _on_batch_pdf_step(self, current, total, status):
        batch = self._import_batch
        if batch is None or self._imp_prog is None:
            return
        base = len(batch["images"]) * 100 + batch["pdf_index"] * 100
        part = 25 + int(75 * int(current) / max(1, int(total)))
        self._imp_prog.setValue(min(batch["total_files"] * 100, base + part))
        self._imp_prog.setLabelText("PDF %d / %d　完整识别：%s（%d / %d 页）" % (
            batch["pdf_index"] + 1, max(1, len(batch["pdfs"])), status,
            int(current), int(total)))

    def _abort_import_batch(self):
        batch = self._import_batch
        self._import_batch = None
        self._close_prog()
        if batch:
            IM.cleanup_staged(batch.get("staged"))
        self.set_status("已取消导入；暂存内容已清理。")
        self._set_import_phase("idle")

    def _finish_import_batch(self):
        batch = self._import_batch
        self._close_prog()
        if not batch:
            return
        staged = batch["staged"]
        warnings = batch["warnings"]
        count = 0
        outcome = "empty"
        try:
            if staged:
                self._set_import_phase("review")
                count = self._review_and_commit_import(staged, warnings)
                outcome = self._import_commit_outcome
            else:
                IM.cleanup_staged(staged)
        finally:
            self._import_batch = None
            self._set_import_phase("idle")
        groups = import_report(warnings)
        state = {"committed": "导入完成", "canceled": "已取消确认，题库未增加题卡",
                 "failed": "写入未完成，请查看失败提示", "empty": "没有可提交的题卡"}[outcome]
        summary = (f"{state}\n\n成功导入：{count} 张题卡\n"
                   f"重复跳过：{len(groups['skipped'])} 个文件\n"
                   f"处理失败：{len(groups['failed'])} 项\n"
                   f"识别提醒：{len(groups['notes'])} 项")
        report = QMessageBox(self)
        report.setWindowTitle("题卡导入结果")
        report.setTextFormat(Qt.PlainText)
        report.setText(summary)
        report.setIcon(QMessageBox.Warning if groups["failed"] or outcome == "failed"
                       else QMessageBox.Information)
        details = []
        for key, title in (("failed", "处理失败"), ("skipped", "重复跳过"),
                           ("notes", "识别提醒")):
            if groups[key]:
                details.append(title + "\n" + "\n".join(groups[key]))
        if details:
            report.setDetailedText("\n\n".join(details))
            report.setInformativeText("展开详细信息可查看全部文件和页面原因；重复文件不会重复加入题库。")
        report.setStandardButtons(QMessageBox.Ok)
        self.set_status(f"导入结果：新增 {count} 题，重复 {len(groups['skipped'])} 个文件，失败 {len(groups['failed'])} 项。")
        report.exec()

    def _finish_batch_worker(self):
        worker, self._imp_thread = self._imp_thread, None
        if worker is not None:
            worker.deleteLater()

    def _close_prog(self):
        try:
            if self._imp_prog is not None:
                self._imp_prog.close()
        except Exception:
            pass
        self._imp_prog = None
        self._imp_thread = None

    def _on_pdf_done(self, added, warns):
        batch = self._import_batch
        if batch is None:
            IM.cleanup_staged(added)
            return
        if self._imp_prog and self._imp_prog.wasCanceled():
            batch["staged"].extend(added)
            self._abort_import_batch()
            return
        batch["staged"].extend(added)
        filename = os.path.basename(batch["pdfs"][batch["pdf_index"]])
        batch["warnings"].extend(
            item if filename in str(item) else f"{filename}：{item}" for item in warns)
        for question in added:
            source = question.get("_reference_source") or {}
            if source.get("sha256"):
                batch["known_hashes"].add(str(source["sha256"]).lower())
        batch["pdf_index"] += 1
        batch["file_progress"] = (len(batch["images"]) + batch["pdf_index"]) * 100
        if self._imp_prog:
            self._imp_prog.setValue(batch["file_progress"])
        self._finish_batch_worker()
        self._start_next_pdf()

    def _on_pdf_error(self, msg):
        batch = self._import_batch
        if batch is None:
            return
        if self._imp_prog and self._imp_prog.wasCanceled():
            self._finish_batch_worker()
            self._abort_import_batch()
            return
        current = batch["pdfs"][batch["pdf_index"]]
        batch["warnings"].append("%s：处理失败：%s" % (os.path.basename(current), str(msg)))
        batch["pdf_index"] += 1
        self._finish_batch_worker()
        self._start_next_pdf()

    def manage_imported(self):
        """列出用户导入的来源文件，允许多选删除或清空。"""
        from import_manager import ImportManagerDialog
        ImportManagerDialog(self, on_deleted=self.load).exec()

    # --------------------------------------------------- 主题 / 动画
    def _capture_page_motion_snapshot(self, opacity=1.0):
        """动画开始前截取一次页面，避免每帧重绘整棵控件树。"""
        if self._page_motion_snapshot is not None:
            return self._page_motion_snapshot
        surface = self.motion_surface
        try:
            pixmap = surface.grab()
        except RuntimeError:
            return None
        if pixmap.isNull():
            return None
        ratio = max(1.0, float(pixmap.devicePixelRatio()))
        captured_size = (round(pixmap.width() / ratio),
                         round(pixmap.height() / ratio))
        if captured_size != (surface.width(), surface.height()):
            return None
        snapshot = _BankPageMotionSnapshot(pixmap, self)
        snapshot.opacity = opacity
        snapshot.setGeometry(surface.geometry())
        snapshot.show()
        snapshot.raise_()
        surface.hide()
        self._page_motion_snapshot = snapshot
        return snapshot

    def _clear_page_motion_snapshot(self):
        snapshot = self._page_motion_snapshot
        self._page_motion_snapshot = None
        if snapshot is not None:
            snapshot.hide()
            snapshot.deleteLater()
        self.motion_surface.show()

    def _stop_page_motion(self, settle=True, keep_effect=False,
                          keep_snapshot=False):
        self._page_intro_timer.stop()
        animation = self._page_motion_animation
        self._page_motion_animation = None
        self._page_motion_kind = None
        self._page_motion_callback = None
        self._intro_prepared = False
        if animation is not None:
            animation.stop()
            animation.deleteLater()

        effect = self._page_motion_effect
        if not keep_effect:
            self._page_motion_effect = None
            if effect is not None and effect is self.motion_surface.graphicsEffect():
                effect.setOpacity(1.0)
                self.motion_surface.setGraphicsEffect(None)
        if settle:
            self.motion_surface.setGeometry(self.rect())
            if not keep_snapshot:
                self._clear_page_motion_snapshot()

    def _finish_page_motion(self, animation):
        if self._page_motion_animation is not animation:
            return
        effect = self._page_motion_effect
        callback = (self._page_motion_callback
                    if self._page_motion_kind == "exit" else None)
        self._page_motion_animation = None
        self._page_motion_effect = None
        self._page_motion_kind = None
        self._page_motion_callback = None
        self._intro_prepared = False
        if effect is not None and effect is self.motion_surface.graphicsEffect():
            effect.setOpacity(1.0)
            self.motion_surface.setGraphicsEffect(None)
        self.motion_surface.setGeometry(self.rect())
        self._clear_page_motion_snapshot()
        animation.deleteLater()
        if callable(callback):
            callback()

    def _start_page_motion(self, kind, on_finished=None):
        if not animations_enabled() or not self.isVisible():
            self._stop_page_motion(settle=True)
            if callable(on_finished):
                on_finished()
            return

        surface = self.motion_surface
        effect = None
        snapshot = self._page_motion_snapshot
        if snapshot is None and self._page_motion_effect is None:
            snapshot = self._capture_page_motion_snapshot()
        if snapshot is not None:
            self._page_motion_effect = None
            if kind == "intro":
                snapshot.setGeometry(self.rect())
                snapshot.move(0, 16)
                snapshot.opacity = 0.0
                end_pos = QPoint(0, 0)
                duration = 420
                end_opacity = 1.0
            else:
                start_pos = snapshot.pos()
                end_pos = QPoint(start_pos.x(), start_pos.y() + 12)
                duration = 190
                end_opacity = 0.0
            target = snapshot
            start_pos = snapshot.pos()
            start_opacity = snapshot.opacity
        else:
            effect = self._page_motion_effect
            if effect is None or surface.graphicsEffect() is not effect:
                effect = QGraphicsOpacityEffect(surface)
                effect.setOpacity(0.0 if kind == "intro" else 1.0)
                surface.setGraphicsEffect(effect)
                self._page_motion_effect = effect
            if kind == "intro":
                surface.setGeometry(self.rect())
                surface.move(0, 16)
                effect.setOpacity(0.0)
                end_pos = QPoint(0, 0)
                duration = 420
                end_opacity = 1.0
            else:
                surface.resize(self.size())
                start_pos = surface.pos()
                end_pos = QPoint(start_pos.x(), start_pos.y() + 12)
                duration = 190
                end_opacity = 0.0
            target = surface
            start_pos = surface.pos()
            start_opacity = effect.opacity()

        animation = QParallelAnimationGroup(self)
        position = QPropertyAnimation(target, b"pos", animation)
        position.setDuration(duration)
        position.setEasingCurve(QEasingCurve.OutCubic)
        position.setStartValue(start_pos)
        position.setEndValue(end_pos)
        opacity = QPropertyAnimation(target, b"opacity", animation)
        opacity.setDuration(duration)
        opacity.setEasingCurve(QEasingCurve.OutCubic)
        opacity.setStartValue(start_opacity)
        opacity.setEndValue(end_opacity)
        animation.finished.connect(
            lambda motion=animation: self._finish_page_motion(motion))
        self._page_motion_animation = animation
        self._page_motion_effect = (None if snapshot is not None else effect)
        self._page_motion_kind = kind
        self._page_motion_callback = on_finished
        animation.start()

    def prepare_intro(self):
        self._stop_page_motion(settle=True)
        if not animations_enabled():
            return
        # prepare_intro 可能在 QStackedWidget 切换当前页之前调用；此时页面仍隐藏，
        # 抓取到的快照尺寸可能还是构造时的初始尺寸。只记录意图，等页面显示后再采集。
        self._intro_prepared = True

    def play_intro(self):
        if not self._intro_prepared:
            self.prepare_intro()
        if self._intro_prepared and self.isVisible():
            self._page_intro_timer.start(0)

    def _begin_prepared_intro(self):
        if not self._intro_prepared or not self.isVisible():
            return
        self.ensurePolished()
        surface = self.motion_surface
        surface.setGeometry(self.rect())
        surface.ensurePolished()
        surface_layout = surface.layout()
        if surface_layout is not None:
            surface_layout.activate()
        content_layout = getattr(self, "root_layout", None)
        if content_layout is not None:
            content_layout.activate()
        self._start_page_motion("intro")

    def cancel_intro(self):
        self._stop_page_motion(settle=True)

    def play_exit(self, on_finished=None):
        self._stop_page_motion(settle=False, keep_effect=True,
                               keep_snapshot=True)
        self._start_page_motion("exit", on_finished=on_finished)

    def cancel_exit(self):
        self._stop_page_motion(settle=True)

    def on_show(self):
        pending = self._pending_detail
        if pending is not None:
            self._pending_detail = None
            self._finish_question_detail(*pending)
        # 回到本页时同步一次做题进度（可能在设置页刚清空过记录）
        self.refresh_stats()
        self._refresh_anna_banner_stats()
        progress = self._progress_list_snapshot()
        signature = self._list_state_signature(
            progress, NT.revision(), self.view)
        if signature != self._last_list_state_signature:
            if self._return_refresh_timer is None:
                self._return_refresh_timer = QTimer(self)
                self._return_refresh_timer.setSingleShot(True)
                self._return_refresh_timer.timeout.connect(
                    self._refresh_after_page_entry)
            self._return_refresh_timer.start(
                330 if animations_enabled() else 0)

    def _refresh_after_page_entry(self):
        if self.isVisible():
            self.refresh(keep_row=True)

    def _refresh_anna_banner_stats(self):
        rows = getattr(self, "all", ()) or ()
        question_ids = {str(row.get("id")) for row in rows
                        if isinstance(row, dict) and row.get("id") is not None}
        regions = {str(row.get("region")).strip() for row in rows
                   if isinstance(row, dict) and row.get("region")}
        exams = {str(row.get("date")).strip() for row in rows
                 if isinstance(row, dict) and row.get("date")}
        self.anna_header_art.set_stats_line(
            f"共 {len(question_ids)} 题 · 覆盖 {len(regions)} 个地区 · {len(exams)} 个考次")

    def theme_changed(self, pref=None):
        chiyo = T.app_theme == "chiyo"
        industrial = T.app_theme == "industrial"
        anna = T.app_theme == "anna"
        self.root_layout.setContentsMargins(
            20 if anna else 30, 20 if anna else 22,
            20 if anna else 30, 18 if anna else 22)
        self.root_layout.setSpacing(11 if anna else 13)
        self.heading_widget.setVisible(not anna)
        self.anna_header_art.setVisible(anna)
        self.import_drop_target.setVisible(not anna and not self.all)
        self.import_drop_target.refresh_theme()
        self.industrial_header.setVisible(industrial)
        self.industrial_header.update()
        self.industrial_footer.setVisible(industrial)
        self.industrial_footer.update()
        self.detail_micro_header.setVisible(industrial)
        self.paper_micro_label.setVisible(industrial)
        self.b_ind_prev.setVisible(industrial)
        self.b_ind_next.setVisible(industrial)
        self.detail_nav.setVisible(not industrial)
        self.industrial_question_label.setVisible(
            industrial and self.cur is not None)
        self.industrial_question_label.setStyleSheet(
            f"font-size:19px;font-weight:700;color:{T.P['ink']};"
            "background:transparent;")
        self.d_title.setVisible(not industrial)
        self.d_title.setStyleSheet(
            f"font-size:{29 if anna else 22}px;font-weight:800;"
            f"color:{T.P['ink']};background:transparent;")
        for label in self.detail_micro_header.findChildren(QLabel):
            label.setStyleSheet(
                f"font:8px Consolas;color:{T.P['ink3']};"
                "letter-spacing:1.1px;background:transparent;")
        self.detail_micro_rule.setStyleSheet(
            f"background:{T.P['glass_soft_edge']};border:0;")
        self._clip_gap.setVisible(chiyo)
        for name in ("_more_menu", "_import_menu"):
            menu = getattr(self, name, None)
            if menu is not None:
                menu.dismiss(animate=False)
        self.chiyo_header_note.setVisible(chiyo)
        self.chiyo_header_note.update()
        self.anna_header_art.setVisible(T.app_theme == "anna")
        self.anna_header_art.refresh_art()
        self._refresh_anna_banner_stats()
        self.chiyo_card_dots.setVisible(chiyo)
        self.chiyo_card_dots.update()
        self.chiyo_card_dots.raise_()
        self.chiyo_binder_clip.setVisible(chiyo)
        self.chiyo_binder_clip.raise_()
        self.frequency_ruler.setVisible(industrial)
        self.frequency_ruler.set_position(
            self.listw.currentRow() + 1 if self.listw.currentRow() >= 0 else 1,
            len(self.view))
        for c, v, col, l in self._stat_cards:
            refresh_glass(c)
            replay_shadow(c)
            v.setStyleSheet(
                f"font-size:18px; font-weight:700;"
                f"color:{T.P[col] if col else T.P['ink2']}; background:transparent;")
            l.setStyleSheet(
                f"font-size:12px; color:{T.P['ink3']}; background:transparent;")
        for c in self._cards:
            refresh_glass(c)
            replay_shadow(c)
        depth_color=QColor(T.P['accent_fg']) if T.mode=='dark' else QColor(17,35,63)
        depth_color.setAlpha(24 if T.mode=='dark' else 18)
        self.detail_shadow.setColor(depth_color)
        self.detail_shadow.setBlurRadius(36 if T.mode=='dark' else 34)
        self.detail_shadow.setOffset(0,8)
        self.d_sub.setStyleSheet(
            f"font-size:{15 if anna else 12}px; font-weight:700;"
            f"color:{T.C_BLUE if anna else T.P['accent_fg']};"
            f"background:transparent;")
        if industrial:
            self.d_stamp.setFixedSize(176, 108)
            self.d_stamp.setStyleSheet(
                f"background:transparent;color:{T.P['accent_fg']};"
                "font-family:Consolas,'Courier New';font-size:10px;font-weight:500;"
                "border:0;border-radius:0;")
        else:
            self.d_stamp.setFixedSize(42, 30)
            self.d_stamp.setStyleSheet(
                f"background:{T.C_BLUE};color:#fff;font-size:14px;font-weight:900;"
                "border:0;border-radius:3px 3px 3px 0px;")
        self.d_stamp.setVisible(T.app_theme in ("chiyo", "industrial") and self.cur is not None)
        self.fcap_lb.setStyleSheet(
            f"font-size:{16 if anna else 12}px; font-weight:700; color:{T.P['ink3']};"
            f"background:transparent;")
        for l in self.findChildren(QLabel):
            if l.objectName() == "H1":
                l.setStyleSheet(
                    f"font-size:30px; font-weight:800; color:{T.P['ink']};"
                    f"letter-spacing:0px; background:transparent;"
                    f"border-left:{'6px' if industrial else '0px'} solid {T.P['accent_fg']};"
                    f"padding-left:{'12px' if industrial else '0px'};")
            elif l.objectName() == "Dim":
                l.setStyleSheet(
                    f"font-size:13px; color:{T.P['ink2']};"
                    f"background:transparent;")
        paper = getattr(self, "_card_layout", "standard") == "paper"
        self.paper_sheet.setStyleSheet(
            "QWidget#ImportedPaperSheet{border:1px solid #CFD7E2;border-radius:3px;}"
            if paper else "QWidget#ImportedPaperSheet{background:transparent;border:0;}")
        paper_ink = "#17253B"
        paper_muted = "#35445A"
        self.paper_title.setStyleSheet(
            f"font-size:24px;font-weight:750;color:{paper_ink};background:transparent;")
        for label in (self.paper_candidate, self.paper_meta):
            label.setStyleSheet(
                f"font-size:13px;color:{paper_muted};background:transparent;")
        for section, body in self.paper_content_labels.values():
            for heading in section.findChildren(QLabel):
                if heading is not body:
                    heading.setStyleSheet(
                        f"font-size:16px;font-weight:750;color:{paper_ink};"
                        "background:transparent;")
            body.setStyleSheet(
                f"font-size:14px;line-height:160%;color:{paper_muted};"
                "background:transparent;")
        self.paper_conditions_frame.setStyleSheet(
            "QFrame{background:rgba(255,255,255,105);border:1px solid #182638;}"
            "QLabel{border:0;background:transparent;}")
        self.paper_rule.setStyleSheet(
            "background:#CFD7E2;border:0;")
        self.paper_sheet.update()
        self.paper_diagram.update()
        self._style_fig()
        self._style_zoom_buttons()
        self._render_paper_card()
        for b in (self.b_directory, self.b_rand, self.b_open, self.b_prev,
                  self.b_next, self.b_note, self.b_more, self.b_filters,
                  self.b_clear, self.b_fit, self.b_full, self.b_import,
                  self.b_fit_width, self.b_retry, self.b_done,
                  self.b_empty_action, self.b_clear_visible,
                  self.b_ind_prev, self.b_ind_next):
            b.refresh()
        self._style_figure_toolbar_buttons()
        QTimer.singleShot(0, self._reflow_figure_toolbar)
        self.cb_r.refresh()
        self.cb_d.refresh()
        self.cb_p.refresh()
        self.cb_t.refresh()
        if hasattr(self, "cb_s"):
            self.cb_s.refresh()
        # 列表条目持有显式前景色，主题变化时原位更新，不重建全部条目。
        for row, question in enumerate(self.view):
            item = self.listw.item(row)
            if item is None:
                continue
            if PG.is_wrong(question["id"]):
                color = T.C_RED
            elif PG.is_done(question["id"]):
                color = T.C_GREEN
            else:
                color = T.P["ink2"]
            item.setForeground(QColor(color))
        if self.cur is not None:
            number = self.listw.currentRow() + 1 if self.listw.currentRow() >= 0 else 1
            self.d_stamp.setText(("%03d" if industrial else "%02d") % number)
            self._detail_timer.stop()
            self._detail_revision += 1
            self._pending_detail = None
            self.body.setHtml(
                self._question_detail_html(self.cur) + self._ref_block(self.cur))
        # 独立弹窗绘制时会读取当前主题色，只请求重绘，不关闭再重建。
        self.theme_overlays_changed(pref)
        self._style_reading_desk()
        self.update()

    def theme_overlays_changed(self, pref=None):
        """立即刷新题库页拥有的独立窗口，即使题库页此刻在后台。"""
        for window in (getattr(self, "_wrong_dlg", None),
                       getattr(self, "_trend_dlg", None),
                       getattr(self, "fig_win", None)):
            if window is None or not window.isVisible():
                continue
            refresh = getattr(window, "theme_changed", None)
            if callable(refresh):
                refresh(pref)

    def _hook_fig_resize(self):
        """拖动分隔条 / 改窗口大小时，视口尺寸变了要重算配图缩放"""
        vp = self.fig_scroll.viewport()
        vp.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if obj is self.fig_scroll.viewport() and ev.type() == ev.Type.Resize:
            if self._directory_animating:
                return False
            if getattr(self, "_card_layout", "standard") == "paper":
                if not getattr(self, "_paper_resize_timer", None):
                    self._paper_resize_timer = QTimer(self)
                    self._paper_resize_timer.setSingleShot(True)
                    self._paper_resize_timer.timeout.connect(
                        self._resize_paper_sheet)
                self._paper_resize_timer.start(50)
            # 防抖：布局收敛后再重算，避免连续触发
            if not getattr(self, "_fig_timer", None):
                self._fig_timer = QTimer(self)
                self._fig_timer.setSingleShot(True)
                self._fig_timer.timeout.connect(self._render_fig)
            self._fig_timer.start(60)
        return super().eventFilter(obj, ev)

    def resizeEvent(self, ev):
        if getattr(self, "_filter_anim", None) is not None:
            self._finish_filter_motion()
        super().resizeEvent(ev)
        surface = getattr(self, "motion_surface", None)
        if surface is not None:
            surface.resize(self.size())
            if self._page_motion_animation is None:
                surface.move(0, 0)
            snapshot = getattr(self, "_page_motion_snapshot", None)
            if snapshot is not None and snapshot.size() != self.size():
                # 快照不能靠拉伸适配新窗口尺寸，否则首次布局或缩放时会模糊、裁切。
                self._stop_page_motion(settle=True)
        QTimer.singleShot(0, self._reflow_figure_toolbar)
        self._render_fig()
        if getattr(self, "_card_layout", "standard") == "paper":
            QTimer.singleShot(0, self._resize_paper_sheet)
