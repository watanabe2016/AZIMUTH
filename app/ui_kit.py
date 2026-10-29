# -*- coding: utf-8 -*-
"""
UI 组件库
  · Icons       —— 纯矢量图标（不用 emoji，避免字形缺失与风格不统一）
  · glass / btn_style —— 玻璃面板与按钮（主题感知）
  · Combo       —— 自绘下拉选择（规避原生 popup 在黑底窗口下渲染成黑框的 bug）
  · Anims       —— 各种入场/交互动画
"""
import math

from PySide6.QtCore import (Qt, QSize, QPoint, QPointF, QRect, QRectF, QEvent,
                            QPropertyAnimation, QEasingCurve, QTimer,
                            QParallelAnimationGroup, QVariantAnimation, Signal,
                            QObject)
from PySide6.QtGui import (QColor, QPainter, QPen, QBrush, QPixmap,
                           QLinearGradient, QPainterPath, QIcon, QRegion)
from PySide6.QtWidgets import (QApplication, QWidget, QFrame, QLabel, QPushButton, QVBoxLayout,
                               QHBoxLayout, QGraphicsDropShadowEffect,
                               QGraphicsOpacityEffect, QListWidget,
                               QListWidgetItem, QCheckBox)

import theme as T
import config as cfg


class ToggleSwitch(QCheckBox):
    """键盘可操作的轻量开关，滑块状态由复选框自身管理。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(58, 32)
        self.setFocusPolicy(Qt.StrongFocus)
        self._progress = 1.0 if self.isChecked() else 0.0
        self._pressed = False
        self._motion = QVariantAnimation(self)
        self._motion.setDuration(320)
        self._motion.setEasingCurve(QEasingCurve.OutCubic)
        self._motion.valueChanged.connect(self._set_progress)
        self.toggled.connect(self._animate_state)

    def _set_progress(self, value):
        self._progress = float(value)
        self.update()

    def _animate_state(self, checked):
        target = 1.0 if checked else 0.0
        if not animations_enabled() or not self.isEnabled():
            self._motion.stop()
            self._set_progress(target)
            return
        self._motion.stop()
        self._motion.setStartValue(self._progress)
        self._motion.setEndValue(target)
        self._motion.start()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        checked = self.isChecked()
        disabled = not self.isEnabled()
        off = QColor(T.P["input_border"])
        on = QColor(T.C_BLUE)
        t = self._progress
        track = QColor(round(off.red() + (on.red() - off.red()) * t),
                       round(off.green() + (on.green() - off.green()) * t),
                       round(off.blue() + (on.blue() - off.blue()) * t))
        if disabled:
            track.setAlpha(105)
        painter.setPen(Qt.PenStyle.NoPen)
        sheen = QColor(track).lighter(108 if self.underMouse() and not disabled else 100)
        gradient = QLinearGradient(0, 1, 0, 31)
        gradient.setColorAt(0, sheen)
        gradient.setColorAt(1, track)
        painter.setBrush(gradient)
        painter.drawRoundedRect(QRectF(1, 1, 56, 30), 15, 15)
        knob_x = 3 + 28 * t
        knob_size = 22 if self._pressed else 24
        knob_y = 4 + (24 - knob_size) / 2
        painter.setBrush(QColor(255, 255, 255, 242 if not disabled else 190))
        painter.drawEllipse(QRectF(knob_x + (24 - knob_size) / 2,
                                   knob_y, knob_size, knob_size))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(T.C_BLUE), 1.5))
            painter.drawRoundedRect(QRectF(0.5, 0.5, 57, 31), 16, 16)
        painter.end()


def animations_enabled():
    """同时尊重软件和 Windows 的界面动画偏好。"""
    if not cfg.get("animations_on", True):
        return False
    try:
        import ctypes
        enabled = ctypes.c_int()
        ok = ctypes.windll.user32.SystemParametersInfoW(
            0x1042, 0, ctypes.byref(enabled), 0)
        return bool(enabled.value) if ok else True
    except Exception:
        return True


def _window_scaled_rect(rect, factor):
    """围绕浮窗中心计算缩放后的窗口范围。"""
    width = max(1, round(rect.width() * factor))
    height = max(1, round(rect.height() * factor))
    return QRect(rect.x() + (rect.width() - width) // 2,
                 rect.y() + (rect.height() - height) // 2,
                 width, height)


def _stop_window_motion(widget):
    motion = getattr(widget, "_window_motion", None)
    if motion is not None:
        try:
            motion.stop()
            motion.deleteLater()
        except RuntimeError:
            pass
    widget._window_motion = None


class _WindowMotionOverlay(QWidget):
    """绘制浮窗快照，让进出场不再逐帧重排真实窗口。"""

    def __init__(self, pixmap, rect, source=None):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setGeometry(rect)
        self._pixmap = pixmap
        self._progress = 1.0
        self._source = source
        if source is not None:
            source.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self._source and event.type() in (
                QEvent.Type.Move, QEvent.Type.Resize):
            origin = watched.mapToGlobal(QPoint(0, 0))
            self.setGeometry(QRect(origin, watched.size()))
            if event.type() == QEvent.Type.Resize:
                self._pixmap = watched.grab()
        return False

    def set_progress(self, value):
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    def paintEvent(self, event):
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setOpacity(self._progress)
        scale = 0.965 + 0.035 * self._progress
        width, height = self.width() * scale, self.height() * scale
        target = QRectF((self.width() - width) / 2,
                        (self.height() - height) / 2, width, height)
        painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
        painter.end()


class _WindowPaintReady(QObject):
    """在真实首帧绘制后缓存，避开隐藏控件的冷绘制开销。"""

    def __init__(self, widget, duration, revision):
        super().__init__(widget)
        self._widget = widget
        self._duration = duration
        self._revision = revision
        self._queued = False
        widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (watched is self._widget and event.type() == QEvent.Paint and
                not self._queued):
            self._queued = True
            QTimer.singleShot(0, self._capture_after_paint)
        return False

    def _capture_after_paint(self):
        widget = self._widget
        widget.removeEventFilter(self)
        if (getattr(widget, "_window_motion_prepare", None) is not self or
                getattr(widget, "_window_motion_revision", None) !=
                self._revision):
            self.deleteLater()
            return
        widget._window_motion_prepare = None
        pixmap, rect = _window_snapshot(widget)
        if pixmap.isNull():
            widget.setWindowOpacity(1.0)
            self.deleteLater()
            return
        widget._window_motion_overlay = _WindowMotionOverlay(
            pixmap, rect, widget)
        widget._window_motion_progress = 0.0
        _animate_window_snapshot(widget, True, duration=self._duration)
        self.deleteLater()


def _window_snapshot(widget):
    widget.ensurePolished()
    layout = widget.layout()
    if layout is not None:
        layout.activate()
    origin = widget.mapToGlobal(QPoint(0, 0))
    return widget.grab(), QRect(origin, widget.size())


def _animate_window_snapshot(widget, opening, finished=None, duration=420):
    """沿当前快照进度转向新目标；重复操作不会跳回动画起点。"""
    _stop_window_motion(widget)
    overlay = getattr(widget, "_window_motion_overlay", None)
    if overlay is None:
        pixmap, rect = _window_snapshot(widget)
        if pixmap.isNull():
            return False
        overlay = _WindowMotionOverlay(pixmap, rect, widget)
        widget._window_motion_overlay = overlay
        widget._window_motion_progress = 0.0 if opening else 1.0

    start = float(getattr(widget, "_window_motion_progress", 1.0))
    end = 1.0 if opening else 0.0
    overlay.set_progress(start)
    overlay.show()
    overlay.raise_()
    animation = QVariantAnimation(widget)
    animation.setDuration(max(1, round(duration * abs(end - start))))
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.setEasingCurve(QEasingCurve.InOutCubic)

    def update(value, w=widget, layer=overlay):
        if getattr(w, "_window_motion_overlay", None) is layer:
            w._window_motion_progress = float(value)
            layer.set_progress(value)

    def finish(w=widget, layer=overlay, current=animation, entering=opening,
               callback=finished):
        if getattr(w, "_window_motion", None) is not current:
            return
        w._window_motion = None
        current.deleteLater()
        layer.hide()
        layer.deleteLater()
        w._window_motion_overlay = None
        w._window_motion_progress = None
        w.setWindowOpacity(1.0)
        if entering:
            w._window_closing = False
            w.show()
            w.raise_()
        else:
            w._window_closing = False
            w._window_finishing_close = True
            w.hide()
            w._window_finishing_close = False
            w.setEnabled(True)
            if hasattr(w, "_window_restore_geometry"):
                del w._window_restore_geometry
            if callback:
                callback()

    animation.valueChanged.connect(update)
    animation.finished.connect(finish)
    widget._window_motion = animation
    animation.start()
    return True


def show_window_animated(widget, duration=420):
    was_visible = widget.isVisible()
    closing = bool(getattr(widget, "_window_closing", False))
    target = QRect(getattr(widget, "_window_restore_geometry",
                           widget.geometry()) if closing else widget.geometry())
    has_overlay = getattr(widget, "_window_motion_overlay", None) is not None
    if was_visible and not closing and not has_overlay:
        widget.show()
        widget.raise_()
        return
    widget._window_closing = False
    widget.setEnabled(True)
    if not animations_enabled():
        widget._window_motion_revision = getattr(
            widget, "_window_motion_revision", 0) + 1
        pending = getattr(widget, "_window_motion_prepare", None)
        if pending is not None:
            widget.removeEventFilter(pending)
            pending.deleteLater()
            widget._window_motion_prepare = None
        _stop_window_motion(widget)
        overlay = getattr(widget, "_window_motion_overlay", None)
        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()
            widget._window_motion_overlay = None
        widget.setGeometry(target)
        widget.setWindowOpacity(1.0)
        widget.show()
        widget.raise_()
        return
    widget._window_restore_geometry = QRect(target)
    if not was_visible:
        widget.setGeometry(target)
        widget.setWindowOpacity(0.0)
        widget._window_motion_revision = getattr(
            widget, "_window_motion_revision", 0) + 1
        revision = widget._window_motion_revision
        pending = getattr(widget, "_window_motion_prepare", None)
        if pending is not None:
            widget.removeEventFilter(pending)
            pending.deleteLater()
        widget._window_motion_prepare = _WindowPaintReady(
            widget, duration, revision)
    widget.show()
    widget.raise_()
    if was_visible:
        _animate_window_snapshot(widget, True, duration=duration)


def close_window_animated(widget, finished, duration=420):
    if getattr(widget, "_window_closing", False):
        return True
    pending = getattr(widget, "_window_motion_prepare", None)
    if pending is not None:
        widget._window_motion_revision += 1
        widget.removeEventFilter(pending)
        pending.deleteLater()
        widget._window_motion_prepare = None
        widget.setWindowOpacity(1.0)
        widget._window_finishing_close = True
        widget.hide()
        widget._window_finishing_close = False
        if finished:
            finished()
        return False
    if not animations_enabled() or not widget.isVisible():
        _stop_window_motion(widget)
        overlay = getattr(widget, "_window_motion_overlay", None)
        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()
            widget._window_motion_overlay = None
        widget._window_closing = False
        widget.setWindowOpacity(1.0)
        widget._window_finishing_close = True
        widget.hide()
        widget._window_finishing_close = False
        if finished:
            finished()
        return False
    widget._window_closing = True
    if getattr(widget, "_window_motion_overlay", None) is None:
        pixmap, rect = _window_snapshot(widget)
        if pixmap.isNull():
            widget._window_closing = False
            widget.hide()
            if finished:
                finished()
            return False
        widget._window_motion_overlay = _WindowMotionOverlay(
            pixmap, rect, widget)
        widget._window_motion_progress = 1.0
        widget._window_restore_geometry = QRect(widget.geometry())
        widget.setWindowOpacity(0.0)
    widget.setEnabled(False)
    return _animate_window_snapshot(widget, False, finished=finished,
                                    duration=duration)


def _window_motion_finished(widget, motion, geometry, opacity):
    if getattr(widget, "_window_motion", None) is not motion:
        return
    widget._window_motion = None
    widget._window_closing = False
    if hasattr(widget, "_window_restore_geometry"):
        del widget._window_restore_geometry
    widget.setGeometry(geometry)
    widget.setWindowOpacity(opacity)


# ================================================================ 阴影
def add_shadow(w, blur=30, dy=8, alpha=None, color=None):
    """柔和投影 —— 玻璃浮起感的关键。
    注意：同一个控件只能有一个 QGraphicsEffect，反复调用会替换掉旧的。"""
    r, g, b, a = T.P["shadow"]
    e = QGraphicsDropShadowEffect(w)
    e.setBlurRadius(blur)
    e.setOffset(0, dy)
    if color is not None:
        e.setColor(color)
    else:
        e.setColor(QColor(r, g, b, a if alpha is None else alpha))
    w.setGraphicsEffect(e)
    return e


def attach_shadow(w, blur=30, dy=8, alpha=None):
    """记录阴影参数，主题切换后可重放"""
    # 内容面板通过描边区分层级，浮层仍可单独调用 add_shadow。
    w._shadow_kw = None
    w.setGraphicsEffect(None)
    return None


def replay_shadow(w):
    kw = getattr(w, "_shadow_kw", None)
    if kw:
        add_shadow(w, **kw)


def place_near(win, anchor, prefer="below", gap=8, margin=10):
    """把浮动窗口 ``win`` 摆在 ``anchor`` 控件附近，并保证不越出屏幕。

    为什么需要它：原来错题集 / 成绩趋势两个浮层是**相对主窗口居中**摆的
    （``g.center().x() - d.width()//2, max(g.top()+70, ...)``）——
    实际效果是浮层飘在屏幕中间偏上，跟"我刚点了哪个按钮"完全对不上，
    用户看不出这个窗是从哪冒出来的。

    摆法：
      · ``below``  锚点下方；**左右会自动选边**：
        - 浮层左边对齐按钮左边 → 放得下就用这个（下拉菜单手感）；
        - 放不下（会顶出屏幕右边）→ 改成**浮层右边对齐按钮右边**，
          往左展开。★ 这一步是必须的：本项目的「更多」按钮在最右侧，
          左对齐摆一个 660 宽的窗必然越界，然后被"夹回屏幕内"的兜底逻辑
          推到屏幕最左边 —— 实测弹到了 x=129，离按钮十万八千里。
      · ``above``  正上方。
      · ``right``  右侧对齐顶部。

    ★ 尺寸要在调用**之前**定好（``adjustSize()`` 或 ``resize()``）——
      本函数只负责算位置，不会替你重新布局。

    ★ 最后一定会夹进 ``availableGeometry()``：没有这道保险，
      屏幕边缘的按钮会让浮层有一半跑到屏幕外点不到。
    """
    try:
        bl = anchor.mapToGlobal(anchor.rect().bottomLeft())
        br = anchor.mapToGlobal(anchor.rect().bottomRight())
        tl = anchor.mapToGlobal(anchor.rect().topLeft())
    except Exception:
        bl = br = tl = None

    # 拿"逻辑屏幕"可用区域 —— 任务栏、多屏都能正确排除
    try:
        scr = (anchor.screen() if anchor is not None else None) or win.screen()
    except Exception:
        scr = None
    if scr is not None:
        geo = scr.availableGeometry()
    else:                                    # 兜底：拿不到屏幕就用主屏
        from PySide6.QtGui import QGuiApplication
        geo = QGuiApplication.primaryScreen().availableGeometry()

    if bl is None:                           # 没锚点就退回居中
        x = geo.center().x() - win.width() // 2
        y = geo.center().y() - win.height() // 2
    elif prefer == "right":
        x, y = br.x() + gap, tl.y()
    else:
        # 左对齐优先；右边放不下就翻成右对齐（往左展开）
        x = bl.x()
        if x + win.width() > geo.right() - margin:
            right_aligned = br.x() - win.width() + 1
            # 右对齐后左边还得放得下，否则保持左对齐交给下面的夹取逻辑
            if right_aligned >= geo.left() + margin:
                x = right_aligned
        y = bl.y() + gap
        # 下方放不下就翻到上方
        if y + win.height() > geo.bottom() - margin:
            if tl.y() - gap - win.height() >= geo.top() + margin:
                y = tl.y() - gap - win.height()

    # 夹进屏幕内（各留 margin）
    x = max(geo.left() + margin, min(x, geo.right() - win.width() - margin))
    y = max(geo.top() + margin, min(y, geo.bottom() - win.height() - margin))
    win.move(int(x), int(y))
    return win


# ================================================================ 动画
def fade_in(w, ms=260, start=0.0, end=1.0):
    """淡入（会占用 graphicsEffect，勿与阴影同时用）"""
    eff = QGraphicsOpacityEffect(w)
    eff.setOpacity(start)
    w.setGraphicsEffect(eff)
    a = QPropertyAnimation(eff, b"opacity", w)
    a.setDuration(ms)
    a.setStartValue(start)
    a.setEndValue(end)
    a.setEasingCurve(QEasingCurve.OutCubic)
    a.start(QPropertyAnimation.DeleteWhenStopped)
    w._fade_anim = a
    return a


def slide_fade(w, ms=280, dx=24):
    """横向滑入 + 淡入（页面切换用）。
    用独立的时间线驱动 pos 与自绘偏移，不用 opacity effect，
    以免覆盖阴影效果。"""
    a = QPropertyAnimation(w, b"pos", w)
    p0 = w.pos()
    a.setDuration(ms)
    a.setStartValue(QPoint(p0.x() + dx, p0.y()))
    a.setEndValue(p0)
    a.setEasingCurve(QEasingCurve.OutCubic)
    a.start(QPropertyAnimation.DeleteWhenStopped)
    w._slide_anim = a
    return a


def bump(a, lo=0.0, hi=1.0, ms=260, curve=QEasingCurve.OutCubic):
    """通用属性补间，返回并启动"""
    a.setDuration(ms)
    a.setStartValue(lo)
    a.setEndValue(hi)
    a.setEasingCurve(curve)
    a.start(QPropertyAnimation.DeleteWhenStopped)
    return a


class _CardGeometryTracker(QObject):
    """记录布局给卡片的静止位置，不把动画中的位置误当成新目标。"""

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.Move and
                not getattr(watched, "_card_motion_mutating", False) and
                getattr(watched, "_card_entry_anim", None) is None):
            watched._card_entry_home = watched.pos()
        return False


def _stop_card_motion(w, settle=True):
    """撤销一张卡片的等待或动画，并交还稳定的静止状态。"""
    w._card_entry_revision = getattr(w, "_card_entry_revision", 0) + 1
    timer = getattr(w, "_card_entry_timer", None)
    w._card_entry_timer = None
    if timer is not None:
        timer.stop()
        timer.deleteLater()
    anim = getattr(w, "_card_entry_anim", None)
    w._card_entry_anim = None
    if anim is not None:
        # 该动画以 DeleteWhenStopped 启动；stop() 后由 Qt 负责回收，
        # 再调用 deleteLater() 会给同一对象重复安排销毁。
        anim.stop()
    effect = getattr(w, "_card_entry_effect", None)
    w._card_entry_effect = None
    if effect is not None:
        try:
            if w.graphicsEffect() is effect:
                effect.setOpacity(1.0)
                # Qt 接管 effect 的所有权；卸载时会同步删除它。
                w.setGraphicsEffect(None)
        except RuntimeError:
            # 父控件销毁或替换 effect 时，旧 Python 包装器可能已失效。
            pass
    if settle:
        home = getattr(w, "_card_entry_home", None)
        if home is not None:
            w._card_motion_mutating = True
            w.move(home)
            w._card_motion_mutating = False


def cancel_card_in(widgets):
    """页面离开或尺寸改变时，停止入场并还原到完整可见的布局位置。"""
    for w in widgets:
        active = any(getattr(w, name, None) is not None for name in
                     ("_card_entry_timer", "_card_entry_anim",
                      "_card_entry_effect"))
        if not active:
            continue
        _stop_card_motion(w, settle=True)
        parent = w.parentWidget()
        layout = parent.layout() if parent is not None else None
        if layout is not None:
            layout.activate()


def prepare_card_in(widgets):
    """在目标页首帧绘制前准备起点，避免先闪现终点再跳回入场起点。"""
    if not animations_enabled():
        cancel_card_in(widgets)
        return
    tracker_type = _CardGeometryTracker
    for w in widgets:
        _stop_card_motion(w, settle=True)
        parent = w.parentWidget()
        layout = parent.layout() if parent is not None else None
        if layout is not None:
            layout.activate()
        child_layout = w.layout()
        if child_layout is not None:
            child_layout.activate()
        tracker = getattr(w, "_card_geometry_tracker", None)
        if tracker is None:
            tracker = tracker_type(w)
            w._card_geometry_tracker = tracker
            w.installEventFilter(tracker)
        home = w.pos()
        w._card_entry_home = home
        w._card_entry_revision += 1
        if w.graphicsEffect() is None:
            effect = QGraphicsOpacityEffect(w)
            effect.setOpacity(0.0)
            w.setGraphicsEffect(effect)
            w._card_entry_effect = effect
        w._card_motion_mutating = True
        w.move(home.x(), home.y() + 10)
        w._card_motion_mutating = False


def card_in(widgets, base_delay=0, step=70, ms=460, prepared=False):
    """按顺序播放卡片入场；目标位置与初始视觉状态需在页面显示前准备。"""
    widgets = list(widgets)
    if not prepared:
        prepare_card_in(widgets)
    allowed = animations_enabled()
    for index, w in enumerate(widgets):
        revision = getattr(w, "_card_entry_revision", 0)
        effect = getattr(w, "_card_entry_effect", None)
        if not allowed:
            if effect is not None:
                effect.setOpacity(1.0)
            continue

        timer = QTimer(w)
        timer.setSingleShot(True)

        def run(w=w, timer=timer, revision=revision, effect=effect):
            if w._card_entry_timer is timer:
                w._card_entry_timer = None
            timer.deleteLater()
            if revision != w._card_entry_revision:
                return
            if not w.isVisible():
                _stop_card_motion(w, settle=True)
                return
            group = QParallelAnimationGroup(w)
            home = w._card_entry_home
            if effect is not None:
                alpha = QPropertyAnimation(effect, b"opacity", group)
                alpha.setDuration(ms)
                alpha.setStartValue(effect.opacity())
                alpha.setEndValue(1.0)
                alpha.setEasingCurve(QEasingCurve.OutCubic)
                group.addAnimation(alpha)
            move = QPropertyAnimation(w, b"pos", group)
            move.setDuration(ms)
            move.setStartValue(w.pos())
            move.setEndValue(home)
            move.setEasingCurve(QEasingCurve.OutCubic)
            group.addAnimation(move)

            def finish():
                if revision != getattr(w, "_card_entry_revision", -1):
                    return
                if w._card_entry_anim is group:
                    w._card_entry_anim = None
                    w._card_motion_mutating = True
                    w.move(home)
                    w._card_motion_mutating = False
                    if effect is not None:
                        try:
                            if w.graphicsEffect() is effect:
                                effect.setOpacity(1.0)
                                # setGraphicsEffect(None) 会销毁 Qt 持有的 effect。
                                w.setGraphicsEffect(None)
                        except RuntimeError:
                            pass
                        w._card_entry_effect = None

            group.finished.connect(finish)
            w._card_entry_anim = group
            group.start(QPropertyAnimation.DeleteWhenStopped)

        timer.timeout.connect(run)
        w._card_entry_timer = timer
        timer.start(max(0, base_delay + index * step))


# ================================================================ 矢量图标
class Icons:
    """纯代码绘制的矢量图标。统一 24x24 设计网格，描边风格。
    不依赖字体、不依赖图片，任何 DPI 都清晰，也不用 emoji。"""

    @staticmethod
    def arrow_left(color, size=20):
        pm,p=Icons._canvas(size);p.setPen(Icons._pen(p,color,1.9));p.setBrush(Qt.NoBrush)
        p.drawLine(5,12,19,12);p.drawLine(5,12,11,6);p.drawLine(5,12,11,18)
        p.end();return Icons._out(pm,size)

    @staticmethod
    def play(color, size=20):
        pm,p=Icons._canvas(size);p.setPen(Qt.NoPen);p.setBrush(QColor(color))
        path=QPainterPath();path.moveTo(8,5);path.lineTo(19,12);path.lineTo(8,19);path.closeSubpath();p.drawPath(path)
        p.end();return Icons._out(pm,size)

    @staticmethod
    def pause(color, size=20):
        pm,p=Icons._canvas(size);p.setPen(Qt.NoPen);p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(6,5,4,14),1,1);p.drawRoundedRect(QRectF(14,5,4,14),1,1)
        p.end();return Icons._out(pm,size)

    @staticmethod
    def skip_next(color, size=20):
        pm,p=Icons._canvas(size);p.setPen(Icons._pen(p,color,1.9));p.setBrush(Qt.NoBrush)
        path=QPainterPath();path.moveTo(6,5);path.lineTo(16,12);path.lineTo(6,19);path.closeSubpath();p.drawPath(path);p.drawLine(19,5,19,19)
        p.end();return Icons._out(pm,size)

    @staticmethod
    def skip_previous(color, size=20):
        pm,p=Icons._canvas(size);p.setPen(Icons._pen(p,color,1.9));p.setBrush(Qt.NoBrush)
        path=QPainterPath();path.moveTo(18,5);path.lineTo(8,12);path.lineTo(18,19);path.closeSubpath();p.drawPath(path);p.drawLine(5,5,5,19)
        p.end();return Icons._out(pm,size)

    @staticmethod
    def _canvas(size, ss=4):
        """创建绘图画布：物理 size*ss 像素，DPR 固定 1.0。

        这里踩过三个坑，注释留证别再踩：
        1) 画布不要设 devicePixelRatio。硬编码 DPR（早期是 3.0）与屏幕实际
           DPR 不一致时，QIcon 在 setIconSize() 阶段找不到匹配尺寸，直接
           渲染空白 —— 按钮"有形无图"。实测 DPR=1.0 时一切正常。
        2) 不要同时用 p.scale() 和 p.setWindow() / p.setViewport()。三者的
           都会改写坐标映射，叠加后笔迹被放大并裁切，整块画布被涂满，
           图标糊成一个色块（实测 48x48 像素被 100% 填充）。
        3) 只用一次 p.scale(k, k) 把 24x24 设计网格映射到画布，收尾由
           _out() 缩到目标尺寸。DPR=1.0 + 4 倍超采样，任何 DPI 下都清晰。
        """
        px = max(2, int(round(size * ss)))
        pm = QPixmap(px, px)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # 24x24 设计网格 -> 画布像素
        p.scale(px / 24.0, px / 24.0)
        return pm, p

    @staticmethod
    def _out(pm, size):
        """收尾：超采样画布 -> 目标尺寸，DPR 保持 1.0。

        返回图的「逻辑尺寸 == 物理尺寸」都是 size*size，
        QIcon / QLabel 拿来即用，不再需要任何 DPR 换算。
        """
        n = max(1, int(round(size)))
        if pm.width() == n and pm.height() == n:
            return pm
        return pm.scaled(n, n, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    @staticmethod
    def _pen(p, color, w):
        return QPen(QColor(color), w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

    @staticmethod
    def home(color, size=20, w=2.0):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w))
        p.setBrush(Qt.NoBrush)
        roof = QPainterPath()
        roof.moveTo(2.5, 11.4)
        roof.lineTo(12, 3.4)
        roof.lineTo(21.5, 11.4)
        p.drawPath(roof)
        p.drawLine(QPointF(5.2, 9.6), QPointF(5.2, 20.2))
        p.drawLine(QPointF(18.8, 9.6), QPointF(18.8, 20.2))
        p.drawLine(QPointF(5.2, 20.2), QPointF(9.4, 20.2))
        p.drawLine(QPointF(14.6, 20.2), QPointF(18.8, 20.2))
        p.drawRect(QRectF(9.4, 13.5, 5.2, 6.7))
        p.end()
        return Icons._out(pm, size)

    @staticmethod
    def monitor(color, size=20, w=1.9):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(2.4, 4.2, 19.2, 13.1), 1.7, 1.7)
        p.drawLine(QPointF(12, 17.3), QPointF(12, 21.2))
        p.drawLine(QPointF(8.1, 21.2), QPointF(15.9, 21.2))
        p.end()
        return Icons._out(pm, size)

    # ---- 计时器（秒表） ----
    @staticmethod
    def timer(color, size=20, w=1.9):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(12, 13.6), 7.4, 7.4)
        p.drawLine(QPointF(12, 3.0), QPointF(12, 5.2))
        p.drawLine(QPointF(9.6, 3.2), QPointF(14.4, 3.2))
        p.drawLine(QPointF(17.9, 7.3), QPointF(19.4, 5.8))
        p.drawLine(QPointF(12, 13.6), QPointF(12, 9.4))
        p.drawLine(QPointF(12, 13.6), QPointF(15.2, 15.2))
        p.end()
        return Icons._out(pm, size)

    # ---- 灯泡（地面站教学） ----
    @staticmethod
    def bulb(color, size=20, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        # 灯泡轮廓：圆 + 收口
        p.drawEllipse(QPointF(12.0, 9.8), 6.3, 6.3)
        p.drawLine(QPointF(9.0, 14.6), QPointF(9.0, 15.8))
        p.drawLine(QPointF(15.0, 14.6), QPointF(15.0, 15.8))
        p.drawLine(QPointF(9.0, 15.8), QPointF(15.0, 15.8))
        # 底座两道
        p.drawLine(QPointF(9.8, 18.2), QPointF(14.2, 18.2))
        p.drawLine(QPointF(10.6, 20.4), QPointF(13.4, 20.4))
        # 灯丝
        p.drawLine(QPointF(12.0, 7.6), QPointF(12.0, 11.0))
        p.end()
        return Icons._out(pm, size)

    # ---- 书本（题库） ----
    @staticmethod
    def book(color, size=20, w=1.9):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        # 三本叠放
        p.drawLine(QPointF(4.6, 7.2), QPointF(19.4, 7.2))
        p.drawLine(QPointF(4.6, 7.2), QPointF(6.2, 3.8))
        p.drawLine(QPointF(6.2, 3.8), QPointF(21.0, 3.8))
        p.drawLine(QPointF(19.4, 7.2), QPointF(21.0, 3.8))
        p.drawLine(QPointF(3.0, 13.2), QPointF(20.2, 13.2))
        p.drawLine(QPointF(3.0, 13.2), QPointF(4.6, 9.8))
        p.drawLine(QPointF(4.6, 9.8), QPointF(21.8, 9.8))
        p.drawLine(QPointF(20.2, 13.2), QPointF(21.8, 9.8))
        p.drawLine(QPointF(4.6, 19.2), QPointF(19.4, 19.2))
        p.drawLine(QPointF(4.6, 19.2), QPointF(6.2, 15.8))
        p.drawLine(QPointF(6.2, 15.8), QPointF(21.0, 15.8))
        p.drawLine(QPointF(19.4, 19.2), QPointF(21.0, 15.8))
        p.end()
        return Icons._out(pm, size)

    # ---- 齿轮（设置） ----
    @staticmethod
    def gear(color, size=20, w=1.9):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        c = QPointF(12, 12)
        path = QPainterPath()
        for i in range(8):
            a = math.radians(i * 45)
            r1, r2 = 9.4, 6.9
            pts = [(r2, -0.21), (r1, -0.11), (r1, 0.11), (r2, 0.21)]
            for j, (rr, off) in enumerate(pts):
                x = c.x() + math.cos(a + off) * rr
                y = c.y() + math.sin(a + off) * rr
                if i == 0 and j == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
        path.closeSubpath()
        p.drawPath(path)
        p.drawEllipse(c, 3.0, 3.0)
        p.end()
        return Icons._out(pm, size)

    # ---- 骰子（随机抽题） ----
    @staticmethod
    def dice(color, size=20, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(3.8, 3.8, 16.4, 16.4), 4.2, 4.2)
        p.setBrush(QBrush(QColor(color))); p.setPen(Qt.NoPen)
        for x, y in ((8.4, 8.4), (15.6, 8.4), (12.0, 12.0),
                     (8.4, 15.6), (15.6, 15.6)):
            p.drawEllipse(QPointF(x, y), 1.3, 1.3)
        p.end()
        return Icons._out(pm, size)

    # ---- 画笔 ----
    @staticmethod
    def pen(color, size=20, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(3.8, 20.2)
        path.lineTo(6.4, 13.4)
        path.lineTo(14.2, 5.6)
        path.lineTo(18.4, 9.8)
        path.lineTo(10.6, 17.6)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(12.6, 7.2), QPointF(16.8, 11.4))
        p.end()
        return Icons._out(pm, size)

    # ---- 橡皮擦 ----
    @staticmethod
    def eraser(color, size=20, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(4.4, 14.6)
        path.lineTo(12.6, 4.0)
        path.lineTo(19.6, 9.6)
        path.lineTo(11.4, 20.2)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(8.4, 17.4), QPointF(15.4, 17.4))
        p.end()
        return Icons._out(pm, size)

    # ---- 保存 / 导出 ----
    @staticmethod
    def save(color, size=20, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(3.6, 3.6, 16.8, 16.8), 3.4, 3.4)
        p.drawLine(QPointF(8.6, 3.6), QPointF(8.6, 9.6))
        p.drawLine(QPointF(15.4, 3.6), QPointF(15.4, 9.6))
        p.drawLine(QPointF(8.6, 9.6), QPointF(15.4, 9.6))
        p.drawLine(QPointF(12.0, 13.4), QPointF(12.0, 17.8))
        p.drawLine(QPointF(10.0, 15.6), QPointF(12.0, 17.8))
        p.drawLine(QPointF(14.0, 15.6), QPointF(12.0, 17.8))
        p.end()
        return Icons._out(pm, size)

    # ---- 图钉（置顶） ----
    @staticmethod
    def pin(color, size=20, w=1.8, filled=False):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w))
        p.setBrush(QBrush(QColor(color)) if filled else Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(9.2, 3.8)
        path.lineTo(14.8, 3.8)
        path.lineTo(13.8, 9.2)
        path.lineTo(17.2, 12.6)
        path.lineTo(17.2, 14.2)
        path.lineTo(6.8, 14.2)
        path.lineTo(6.8, 12.6)
        path.lineTo(10.2, 9.2)
        path.closeSubpath()
        p.drawPath(path)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(12.0, 14.2), QPointF(12.0, 20.6))
        p.end()
        return Icons._out(pm, size)

    # ---- 缩放（放大镜加号） ----
    @staticmethod
    def zoom(color, size=20, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(10.6, 10.6), 6.4, 6.4)
        p.drawLine(QPointF(15.3, 15.3), QPointF(20.4, 20.4))
        p.drawLine(QPointF(10.6, 7.9), QPointF(10.6, 13.3))
        p.drawLine(QPointF(7.9, 10.6), QPointF(13.3, 10.6))
        p.end()
        return Icons._out(pm, size)

    # ---- 重置 / 复原 ----
    @staticmethod
    def reset(color, size=20, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(4.2, 4.2, 15.6, 15.6), 60 * 16, 270 * 16)
        p.drawLine(QPointF(16.4, 3.0), QPointF(20.2, 6.4))
        p.drawLine(QPointF(20.2, 6.4), QPointF(16.2, 9.0))
        p.end()
        return Icons._out(pm, size)

    # ---- 太阳 ----
    @staticmethod
    def sun(color, size=20, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(12, 12), 4.4, 4.4)
        for i in range(8):
            a = math.radians(i * 45)
            p.drawLine(QPointF(12 + math.cos(a) * 7.0, 12 + math.sin(a) * 7.0),
                       QPointF(12 + math.cos(a) * 9.4, 12 + math.sin(a) * 9.4))
        p.end()
        return Icons._out(pm, size)

    # ---- 月亮 ----
    @staticmethod
    def moon(color, size=20, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(15.4, 3.4)
        path.arcTo(QRectF(3.8, 3.8, 16.4, 16.4), 66, 248)
        path.arcTo(QRectF(9.6, 3.0, 15.2, 15.2), 200, -110)
        p.drawPath(path)
        p.end()
        return Icons._out(pm, size)

    # ---- 桌面时钟 ----
    @staticmethod
    def clockwin(color, size=20, w=1.9):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(2.8, 4.4, 18.4, 15.0), 3.0, 3.0)
        p.drawLine(QPointF(2.8, 9.2), QPointF(21.2, 9.2))
        p.drawEllipse(QPointF(12, 14.6), 3.0, 3.0)
        p.drawLine(QPointF(12, 14.6), QPointF(12, 12.8))
        p.drawLine(QPointF(12, 14.6), QPointF(13.8, 15.4))
        p.end()
        return Icons._out(pm, size)

    # ---- 眼睛 / 显示 ----
    @staticmethod
    def eye(color, size=20, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(2.8, 12.0)
        path.quadTo(12.0, 4.6, 21.2, 12.0)
        path.quadTo(12.0, 19.4, 2.8, 12.0)
        p.drawPath(path)
        p.drawEllipse(QPointF(12, 12), 3.0, 3.0)
        p.end()
        return Icons._out(pm, size)

    # ---- 文档 / 声明 ----
    @staticmethod
    def doc(color, size=20, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(5.6, 2.8)
        path.lineTo(14.0, 2.8)
        path.lineTo(18.6, 7.4)
        path.lineTo(18.6, 21.2)
        path.lineTo(5.6, 21.2)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(14.0, 2.8), QPointF(14.0, 7.4))
        p.drawLine(QPointF(14.0, 7.4), QPointF(18.6, 7.4))
        p.drawLine(QPointF(8.4, 12.0), QPointF(15.8, 12.0))
        p.drawLine(QPointF(8.4, 15.6), QPointF(15.8, 15.6))
        p.drawLine(QPointF(8.4, 19.2), QPointF(12.6, 19.2))
        p.end()
        return Icons._out(pm, size)

    # ---- 警示三角 ----
    @staticmethod
    def warn(color, size=20, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(12.0, 3.4)
        path.lineTo(21.4, 19.8)
        path.lineTo(2.6, 19.8)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(12.0, 9.6), QPointF(12.0, 14.2))
        p.setBrush(QBrush(QColor(color))); p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(12.0, 16.9), 0.95, 0.95)
        p.end()
        return Icons._out(pm, size)

    # ---- 垃圾桶 ----
    @staticmethod
    def trash(color, size=20, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(4.4, 6.4), QPointF(19.6, 6.4))
        p.drawLine(QPointF(9.4, 6.4), QPointF(9.4, 3.8))
        p.drawLine(QPointF(14.6, 6.4), QPointF(14.6, 3.8))
        p.drawLine(QPointF(9.4, 3.8), QPointF(14.6, 3.8))
        path = QPainterPath()
        path.moveTo(6.2, 6.4)
        path.lineTo(7.4, 20.4)
        path.lineTo(16.6, 20.4)
        path.lineTo(17.8, 6.4)
        p.drawPath(path)
        p.drawLine(QPointF(10.6, 10.2), QPointF(10.6, 16.8))
        p.drawLine(QPointF(13.4, 10.2), QPointF(13.4, 16.8))
        p.end()
        return Icons._out(pm, size)

    # ---- 文件夹 ----
    @staticmethod
    def folder(color, size=20, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(3.0, 19.6)
        path.lineTo(3.0, 5.4)
        path.lineTo(9.8, 5.4)
        path.lineTo(11.8, 8.4)
        path.lineTo(21.0, 8.4)
        path.lineTo(21.0, 19.6)
        path.closeSubpath()
        p.drawPath(path)
        p.end()
        return Icons._out(pm, size)

    # ---- 放大镜（扫盘识别） ----
    @staticmethod
    def search(color, size=20, w=1.9):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(10.6, 10.6), 6.4, 6.4)
        p.drawLine(QPointF(15.3, 15.3), QPointF(20.4, 20.4))
        p.end()
        return Icons._out(pm, size)

    # ---- 更多（三个点）----
    @staticmethod
    def more(color, size=20, w=1.8):
        """横排三点 —— 「更多」菜单按钮用。"""
        pm, p = Icons._canvas(size)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        for x in (6.0, 12.0, 18.0):
            p.drawEllipse(QPointF(x, 12.0), 1.9, 1.9)
        p.end()
        return Icons._out(pm, size)

    # ---- 标签（Tag）----
    @staticmethod
    def tagicon(color, size=20, w=1.8):
        """吊牌形状 —— 「考点标签」用。"""
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        pa = QPainterPath()
        pa.moveTo(3.6, 11.0)
        pa.lineTo(11.0, 3.6)
        pa.lineTo(20.4, 3.6)
        pa.lineTo(20.4, 13.0)
        pa.lineTo(13.0, 20.4)
        pa.closeSubpath()
        p.drawPath(pa)
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(16.4, 7.6), 1.5, 1.5)
        p.end()
        return Icons._out(pm, size)

    # ---- 钥匙（参考答案）----
    @staticmethod
    def key(color, size=20, w=1.8):
        """钥匙 —— 「参考答案」用（答案=解题的钥匙）。"""
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(8.0, 8.0), 4.6, 4.6)
        p.drawLine(QPointF(11.4, 11.4), QPointF(20.2, 20.2))
        p.drawLine(QPointF(16.6, 16.6), QPointF(14.6, 18.6))
        p.drawLine(QPointF(18.8, 18.8), QPointF(16.8, 20.8))
        p.end()
        return Icons._out(pm, size)

    # ---- 箭头 ----
    @staticmethod
    def chevron(color, size=16, w=1.9, down=True):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        if down:
            p.drawLine(QPointF(6.4, 9.4), QPointF(12.0, 15.0))
            p.drawLine(QPointF(17.6, 9.4), QPointF(12.0, 15.0))
        else:
            p.drawLine(QPointF(6.4, 14.6), QPointF(12.0, 9.0))
            p.drawLine(QPointF(17.6, 14.6), QPointF(12.0, 9.0))
        p.end()
        return Icons._out(pm, size)

    # ---- 对勾 ----
    @staticmethod
    def check(color, size=16, w=2.2):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(5.0, 12.4), QPointF(9.8, 17.2))
        p.drawLine(QPointF(9.8, 17.2), QPointF(19.0, 6.8))
        p.end()
        return Icons._out(pm, size)

    # ---- 导入（向下箭头落进托盘）----
    # 用在「读软件判题」上：语义就是"从外部软件把数据读进来"
    @staticmethod
    def import_(color, size=18, w=1.9):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        # 向下箭头
        p.drawLine(QPointF(12.0, 3.8), QPointF(12.0, 14.2))
        p.drawLine(QPointF(12.0, 14.2), QPointF(7.6, 9.8))
        p.drawLine(QPointF(12.0, 14.2), QPointF(16.4, 9.8))
        # 托盘（上半开口的 U 形）
        path = QPainterPath()
        path.moveTo(4.6, 13.4)
        path.lineTo(4.6, 19.6)
        path.lineTo(19.4, 19.6)
        path.lineTo(19.4, 13.4)
        p.drawPath(path)
        p.end()
        return Icons._out(pm, size)

    # ---- 抓手（拖拽平移） ----
    @staticmethod
    def pan(color, size=20, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        # 四向箭头
        p.drawLine(QPointF(12, 4.6), QPointF(12, 19.4))
        p.drawLine(QPointF(4.6, 12), QPointF(19.4, 12))
        # 上
        p.drawLine(QPointF(12, 4.6), QPointF(9.6, 7.0))
        p.drawLine(QPointF(12, 4.6), QPointF(14.4, 7.0))
        # 下
        p.drawLine(QPointF(12, 19.4), QPointF(9.6, 17.0))
        p.drawLine(QPointF(12, 19.4), QPointF(14.4, 17.0))
        # 左
        p.drawLine(QPointF(4.6, 12), QPointF(7.0, 9.6))
        p.drawLine(QPointF(4.6, 12), QPointF(7.0, 14.4))
        # 右
        p.drawLine(QPointF(19.4, 12), QPointF(17.0, 9.6))
        p.drawLine(QPointF(19.4, 12), QPointF(17.0, 14.4))
        p.end()
        return Icons._out(pm, size)

    # ---- 收起（面板折叠） ----
    @staticmethod
    def collapse(color, size=18, w=1.9):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(5.0, 10.0), QPointF(19.0, 10.0))
        p.drawLine(QPointF(5.0, 14.0), QPointF(19.0, 14.0))
        p.end()
        return Icons._out(pm, size)


    # ---- 窗口控制三键 ----
    @staticmethod
    def minimize(color, size=16, w=1.7):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(5.0, 12.0), QPointF(19.0, 12.0))
        p.end()
        return Icons._out(pm, size)

    @staticmethod
    def maximize(color, size=16, w=1.7):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(5.0, 5.0, 14.0, 14.0), 2.2, 2.2)
        p.end()
        return Icons._out(pm, size)

    @staticmethod
    def close(color, size=16, w=1.7):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(6.6, 6.6), QPointF(17.4, 17.4))
        p.drawLine(QPointF(17.4, 6.6), QPointF(6.6, 17.4))
        p.end()
        return Icons._out(pm, size)

    # ---- 相机（截图） ----
    @staticmethod
    def camera(color, size=18, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(3.4, 7.2, 17.2, 12.4), 2.6, 2.6)
        p.drawLine(QPointF(8.6, 7.2), QPointF(9.8, 4.8))
        p.drawLine(QPointF(9.8, 4.8), QPointF(14.2, 4.8))
        p.drawLine(QPointF(14.2, 4.8), QPointF(15.4, 7.2))
        p.drawEllipse(QPointF(12.0, 13.4), 3.5, 3.5)
        p.end()
        return Icons._out(pm, size)

    # ---- 便签（备忘录） ----
    @staticmethod
    def note(color, size=18, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(4.6, 3.6, 14.8, 16.8), 2.4, 2.4)
        p.drawLine(QPointF(8.2, 8.6), QPointF(15.8, 8.6))
        p.drawLine(QPointF(8.2, 12.2), QPointF(15.8, 12.2))
        p.drawLine(QPointF(8.2, 15.8), QPointF(12.6, 15.8))
        p.end()
        return Icons._out(pm, size)

    # ---- 图片（插入题卡） ----
    @staticmethod
    def image(color, size=18, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(3.6, 4.8, 16.8, 14.4), 2.4, 2.4)
        p.drawEllipse(QPointF(9.0, 9.6), 1.5, 1.5)
        p.drawLine(QPointF(5.6, 17.8), QPointF(11.2, 12.0))
        p.drawLine(QPointF(11.2, 12.0), QPointF(15.0, 15.8))
        p.drawLine(QPointF(15.0, 15.8), QPointF(17.0, 13.8))
        p.drawLine(QPointF(17.0, 13.8), QPointF(18.6, 15.4))
        p.end()
        return Icons._out(pm, size)

    # ---- 柱状图（成绩趋势） ----
    @staticmethod
    def chart(color, size=18, w=1.8):
        pm, p = Icons._canvas(size)
        p.setPen(Icons._pen(p, color, w)); p.setBrush(Qt.NoBrush)
        # 坐标轴
        p.drawLine(QPointF(3.8, 4.2), QPointF(3.8, 20.0))
        p.drawLine(QPointF(3.8, 20.0), QPointF(20.2, 20.0))
        # 三根高低不一的柱子
        p.drawLine(QPointF(8.2, 20.0), QPointF(8.2, 14.4))
        p.drawLine(QPointF(12.0, 20.0), QPointF(12.0, 8.6))
        p.drawLine(QPointF(15.8, 20.0), QPointF(15.8, 11.6))
        p.end()
        return Icons._out(pm, size)


def icon_label(name, color, size=20, **kw):
    """把矢量图标做成 QLabel（可随主题重绘）"""
    lab = QLabel()
    lab.setFixedSize(size + 4, size + 4)
    lab.setAlignment(Qt.AlignCenter)
    lab.setStyleSheet("background:transparent;")
    lab.setPixmap(getattr(Icons, name)(color, size=size, **kw))
    lab._icon_name = name
    lab._icon_size = size
    lab._icon_kw = kw
    return lab


def repaint_icon(lab, color):
    name = getattr(lab, "_icon_name", None)
    if not name:
        return
    size = getattr(lab, "_icon_size", 20)
    kw = getattr(lab, "_icon_kw", {})
    lab.setPixmap(getattr(Icons, name)(color, size=size, **kw))


# ================================================================ 玻璃控件
BASE_RADIUS = 15
BUTTON_RADIUS = 15
BUTTON_PILL_RADIUS = 26
BUTTON_PRESS_MS = 190
BUTTON_EASE = QEasingCurve.OutCubic

_PRESET = {
    "Glass":       ("glass",      "glass_edge",      "glass_soft_edge", 22),
    "GlassStrong": ("glass_hi",   "glass_edge",      "glass_soft_edge", 22),
    "GlassSoft":   ("glass_lo",   "glass_soft_edge", "glass_soft_edge", 18),
    "GlassDeep":   ("__deep__",   "__deep_edge__",   "__deep_edge__",   22),
}
_DEEP = "rgba(14,58,140,0.94)"
_DEEP_EDGE = "rgba(255,255,255,0.34)"


def glass_style(kind="Glass", radius=None, fill=None, border=None, extra=""):
    P = T.P
    key_fill, key_edge, _, r = _PRESET.get(kind, _PRESET["Glass"])
    bg = _DEEP if key_fill == "__deep__" else P[key_fill]
    bd = _DEEP_EDGE if key_edge == "__deep_edge__" else P[key_edge]
    # 千代是独立品牌主题：玻璃容器改用漫画墨线，避免浅描边像雾面塑料。
    if border is None and T.app_theme == "chiyo":
        bd = P["glass_edge"]
    if fill is not None:
        bg = fill
    if border is not None:
        bd = border
    if radius is not None:
        r = radius
    r = min(r, 6 if T.app_theme == "chiyo" else 14)
    line = 0
    return (f"QFrame#{kind} {{ background: {bg}; border: {line}px solid {bd};"
            f" border-radius: {r}px; }} {extra}")


def glass(kind="Glass", parent=None, radius=None, fill=None, border=None,
          extra=""):
    """创建玻璃面板（样式内联，规避父层 transparent 的覆盖问题）"""
    f = QFrame(parent)
    f.setObjectName(kind)
    f._glass_kind = kind
    f._glass_kw = dict(radius=radius, fill=fill, border=border, extra=extra)
    f.setStyleSheet(glass_style(kind, **f._glass_kw))
    return f


def refresh_glass(f):
    """主题切换时刷新玻璃面板配色"""
    kw = getattr(f, "_glass_kw", None)
    if kw is None:
        return
    f.setStyleSheet(glass_style(getattr(f, "_glass_kind", "Glass"), **kw))


def btn_style(kind="default", checked=False, radius=BASE_RADIUS, font_size=14,
              scale=1.0, extra=""):
    """生成按钮内联样式表。checked=True 用主色（用于可选中按钮）"""
    P = T.P
    radius = min(radius, 6) if T.app_theme == "chiyo" else radius
    C_INK, C_INK_2, C_INK_3 = P["ink"], P["ink2"], P["ink3"]
    presets = {
        "default": dict(
            bg=f"qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {P['glass']},"
                f"stop:1 {P['glass_lo']})",
            fg=C_INK, bd=P["input_border"],
            hbg=f"qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {P['panel_hover']},"
                f"stop:1 {P['glass_lo']})",
            hbd=P["accent_edge"]),
        "primary": dict(
            bg=f"qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {T.C_BLUE_HI},"
                f"stop:1 {T.C_BLUE})",
            fg="#ffffff", bd="rgba(255,255,255,0.44)",
            hbg=f"qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {T.C_BLUE_HI},"
                f"stop:1 {T.C_BLUE})",
            hbd="rgba(255,255,255,0.66)"),
        "danger":  dict(bg=T.C_RED, fg="#ffffff",
                        bd="rgba(255,255,255,0.42)",
                        hbg="#FF6B70", hbd="rgba(255,255,255,0.55)"),
        "ghost":   dict(bg=P["panel_soft"], fg=C_INK_2,
                        bd=P["glass_soft_edge"],
                        hbg=P["panel_hover"],
                        hbd=P["accent_edge"]),
        "bare":    dict(bg="transparent", fg=C_INK_2, bd="transparent",
                        hbg=P["panel_hover"], hbd=P["input_border"]),
    }
    c = presets.get(kind, presets["default"])
    line = 2 if T.app_theme == "chiyo" else 1
    if T.app_theme == "chiyo":
        c = dict(c)
        c["bd"] = P["glass_edge"]
        c["hbd"] = P["glass_edge"]
    if checked:
        bg, fg, bd, fw = T.C_BLUE, "#ffffff", P["glass_edge"], "700"
    else:
        bg, fg, bd, fw = c["bg"], c["fg"], c["bd"], "600"
    # 控件只使用中圆角与行动胶囊两档，避免同一组按钮的形状各自漂移。
    radius_token = (12 if T.app_theme == "default" else
                    BUTTON_PILL_RADIUS if radius >= 24 else BUTTON_RADIUS)
    r = max(4, int(radius_token * scale))
    fs = max(10, int(font_size * scale))
    # 纵向内边距：QSS 的 padding 会直接从「控件高度」里抠掉，剩下的才是文字
    # 能用的内容区。这里踩过坑：原本写 10px（上下共 20px），而题卡工作台的
    # 工具按钮 setMinimumHeight(32) —— 内容区只剩 32-20-2=10px，而 13px 中文
    # 的行高是 16px，于是文字下半截被硬裁掉（用户红框标注的"文字显示不全"）。
    # 改成 5px 后：内容区 = 32-10-2 = 20px > 16px，且 QPushButton 的
    # sizeHint 降到 28px < 32px，布局再也不会把它压到装不下文字。
    # 高按钮（≥40px）本来就靠显式最小高度撑着，视觉完全不变。
    pad_v = max(3, int(5 * scale))
    pad_h = max(8, int(20 * scale))
    return f"""
        QPushButton {{
            background: {bg};
            color: {fg};
            border: none;
            border-radius: {r}px;
            padding: {pad_v}px {pad_h}px;
            font-size: {fs}px;
            font-weight: {fw};
        }}
        QPushButton:hover {{
            background: {T.C_BLUE_HI if checked else c['hbg']};
            border: none;
            color: {fg};
        }}
        QPushButton:focus {{ border: none; background: {T.C_BLUE_HI if checked or kind == 'primary' else P['panel_hover']}; }}
        QPushButton:pressed {{
            background: {f"qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {T.C_BLUE},stop:1 {T.C_BLUE_DEEP})" if checked or kind == 'primary' else P['panel_hover']};
            border: none;
        }}
        QPushButton:disabled {{
            background: {P['panel_soft']};
            color: {C_INK_3};
            border: none;
        }}
        {extra}
    """


def pm_to_icon(pm, size=None):
    """QPixmap -> QIcon。

    Icons.xxx() 现在返回的就是「逻辑尺寸 == 物理尺寸、DPR = 1.0」的普通位图，
    所以这里不需要任何 DPR 归一化 —— 早期版本在这里做 DPR 搬运，反而让
    QIcon 在 setIconSize() 时渲染成空白。可选 size 参数用于显式指定尺寸。
    """
    if size:
        n = max(1, int(round(size)))
        if pm.width() != n or pm.height() != n:
            pm = pm.scaled(n, n, Qt.KeepAspectRatio,
                           Qt.SmoothTransformation)
    return QIcon(pm)

class AnimatedChoiceButton(QPushButton):
    """选项换色时交接当前画面，快速连续选择也从当前帧接续。"""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._state_snapshot = None
        self._state_alpha = 0.0
        self._state_motion = QVariantAnimation(self)
        self._state_motion.setDuration(380)
        self._state_motion.setEasingCurve(QEasingCurve.OutCubic)
        self._state_motion.valueChanged.connect(self._set_state_alpha)
        self._state_motion.finished.connect(self._clear_state_snapshot)

    def _set_state_alpha(self, value):
        self._state_alpha = float(value)
        self.update()

    def _clear_state_snapshot(self):
        self._state_snapshot = None
        self._state_alpha = 0.0
        self.update()

    def setStyleSheet(self, style):
        if style == self.styleSheet():
            return
        motion = getattr(self, "_state_motion", None)
        snapshot = (self.grab() if motion is not None and self.isVisible()
                    and animations_enabled() else None)
        if motion is not None:
            motion.stop()
        super().setStyleSheet(style)
        if snapshot is not None:
            self._state_snapshot = snapshot
            self._state_alpha = 1.0
            motion.setStartValue(1.0)
            motion.setEndValue(0.0)
            motion.start()
        elif motion is not None:
            self._clear_state_snapshot()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._state_snapshot is not None:
            painter = QPainter(self)
            painter.setOpacity(self._state_alpha)
            painter.drawPixmap(QPoint(0, 0), self._state_snapshot)
            painter.end()

    def hideEvent(self, event):
        self._state_motion.stop()
        self._clear_state_snapshot()
        super().hideEvent(event)

    def resizeEvent(self, event):
        self._state_motion.stop()
        self._clear_state_snapshot()
        super().resizeEvent(event)


class IconButton(QPushButton):
    """带矢量图标的按钮。图标颜色随主题/选中态自动重绘。"""

    def __init__(self, text="", icon_name=None, kind="default", parent=None,
                 icon_size=18, radius=BASE_RADIUS, font_size=14, scale=1.0,
                 on_color=None, gap="  ", prominent=False, arrow=False,
                 shadowed=True):
        super().__init__(text, parent)
        self._icon_name = icon_name
        from interactive_button import attach_interactive
        attach_interactive(self)
        self._kind = kind
        self._icon_size = icon_size
        self._radius = radius
        self._font_size = font_size
        self._scale = scale
        self._on_color = on_color
        self._gap = gap
        self._prominent = bool(prominent)
        self._shadowed = bool(shadowed)
        self._arrow = bool(arrow)
        self._checked_style = False
        self._icon_motion = QPropertyAnimation(self, b"iconSize", self)
        self._shadow_effect = None
        self._shadow_motion = None
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(max(28, int(42 * scale)))
        self.refresh()
        if self._prominent or self._shadowed:
            effect = QGraphicsDropShadowEffect(self)
            color = QColor(T.P['accent_fg'])
            color.setAlpha(70 if kind == "primary" else 30)
            effect.setColor(color)
            effect.setBlurRadius(17 if kind == "primary" else 10)
            effect.setOffset(0, 5 if kind == "primary" else 3)
            self.setGraphicsEffect(effect)
            self._shadow_effect = effect
            self.pressed.connect(lambda: self._animate_shadow(pressed=True))
            self.released.connect(lambda: self._animate_shadow(
                hover=self.underMouse()))

    def set_checked_style(self, on):
        self._checked_style = bool(on)
        self.refresh()

    def refresh(self):
        P = T.P
        has_icon = bool(self._icon_name)
        # 带图标时右侧留出内边距，避免图标与文字贴在一起
        pad_left = int(12 * self._scale) if has_icon else int(20 * self._scale)
        pad_right = int((48 if self._arrow else 14) * self._scale)
        extra = (f"QPushButton {{ padding-left: {pad_left}px;"
                 f" padding-right: {pad_right}px; }}")
        if self._prominent or self.property('roundedToolbar'):
            extra += (
                "QPushButton { background: transparent; border-color: transparent; }"
                "QPushButton:hover { background: transparent; border-color: transparent; }"
                "QPushButton:pressed { background: transparent; border-color: transparent; }"
                "QPushButton:disabled { background: transparent; border-color: transparent; }"
                "QPushButton:focus { background: transparent; border-color: transparent; }")
        self.setStyleSheet(btn_style(self._kind, checked=self._checked_style,
                                     radius=self._radius,
                                     font_size=self._font_size,
                                     scale=self._scale, extra=extra))
        if has_icon:
            if self._on_color:
                col = self._on_color
            elif self._checked_style or self._kind in ("primary", "danger"):
                col = "#ffffff"
            else:
                col = P["ink"]
            isz = max(12, int(self._icon_size * self._scale))
            self.setIcon(pm_to_icon(getattr(Icons, self._icon_name)
                                    (col, size=isz)))
            self.setIconSize(QSize(isz, isz))
            # 图标与文字之间插一个间隔，避免贴在一起
            txt = self.text().lstrip()
            if txt and not txt.startswith(self._gap):
                self.setText(self._gap + txt)

    def _animate_icon(self, factor, duration=220, curve=QEasingCurve.OutCubic):
        if not self._icon_name:
            return
        base = max(12, int(self._icon_size * self._scale))
        target_size = max(10, round(base * factor))
        if not animations_enabled():
            self.setIconSize(QSize(target_size, target_size))
            return
        self._icon_motion.stop()
        self._icon_motion.setDuration(duration)
        self._icon_motion.setStartValue(self.iconSize())
        self._icon_motion.setEndValue(QSize(target_size, target_size))
        self._icon_motion.setEasingCurve(curve)
        self._icon_motion.start()

    def enterEvent(self, event):
        if self.isEnabled():
            self._animate_icon(1.06, 220, QEasingCurve.OutCubic)
            self._animate_shadow(hover=True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_icon(1.0, 300, QEasingCurve.OutCubic)
        self._animate_shadow()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._icon_motion.stop()
            base = max(12, int(self._icon_size * self._scale))
            self.setIconSize(QSize(round(base * 0.9), round(base * 0.9)))
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._animate_icon(1.06 if self.underMouse() else 1.0,
                           260, QEasingCurve.OutCubic)

    def _animate_shadow(self, pressed=False, hover=False):
        effect = self._shadow_effect
        if effect is None:
            return
        if not self.isEnabled():
            blur, offset, alpha = 8.0, QPointF(0, 2), 22
        elif pressed:
            blur, offset, alpha = 6.0, QPointF(0, 1.5), 38
        elif hover:
            if self._shadowed and not self._prominent:
                blur, offset, alpha = 13.0, QPointF(0, 4), 49
            else:
                blur, offset, alpha = 19.0, QPointF(0, 5), 76
        else:
            if self._shadowed and not self._prominent:
                blur, offset, alpha = 10.0, QPointF(0, 3), 32
            else:
                blur, offset, alpha = 17.0, QPointF(0, 5), 70
        color = QColor(T.C_BLUE)
        color.setAlpha(alpha)
        effect.setColor(color)
        if not animations_enabled():
            effect.setBlurRadius(blur)
            effect.setOffset(offset)
            return
        previous_motion = self._shadow_motion
        if previous_motion is not None:
            try:
                previous_motion.stop()
            except RuntimeError:
                pass
            self._shadow_motion = None
            try:
                previous_motion.deleteLater()
            except RuntimeError:
                pass
        group = QParallelAnimationGroup(self)
        blur_anim = QPropertyAnimation(effect, b"blurRadius", group)
        blur_anim.setDuration(BUTTON_PRESS_MS)
        blur_anim.setStartValue(effect.blurRadius())
        blur_anim.setEndValue(blur)
        blur_anim.setEasingCurve(BUTTON_EASE)
        offset_anim = QPropertyAnimation(effect, b"offset", group)
        offset_anim.setDuration(BUTTON_PRESS_MS)
        offset_anim.setStartValue(effect.offset())
        offset_anim.setEndValue(offset)
        offset_anim.setEasingCurve(BUTTON_EASE)
        group.finished.connect(lambda motion=group: self._forget_shadow_motion(motion))
        group.start(QParallelAnimationGroup.DeleteWhenStopped)
        self._shadow_motion = group

    def _forget_shadow_motion(self, motion):
        if self._shadow_motion is motion:
            self._shadow_motion = None

    def paintEvent(self, event):
        if self._interactive_hover.paint():return
        if self._prominent or self.property('roundedToolbar'):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            radius = min((11 if self.property('roundedToolbar') else 12 if T.app_theme == "default" else BUTTON_PILL_RADIUS) * self._scale,
                         rect.height() / 2, rect.width() / 2)
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            if not self.isEnabled():
                top = bottom = T.P["panel_soft"]
                edge = T.P["input_border"]
            elif self.isDown():
                top, bottom = T.C_BLUE, T.C_BLUE_DEEP
                edge = "rgba(255,255,255,0.30)"
            elif self.property('roundedToolbar') and self._kind not in ('primary', 'danger'):
                top, bottom = T.P['panel_hover'] if self.underMouse() else T.P['input_bg'], T.P['panel_soft']
                edge = T.P['accent_fg'] if self.hasFocus() else T.P['glass_edge']
            else:
                top, bottom = T.C_BLUE_HI, T.C_BLUE
                edge = "rgba(255,255,255,0.46)"
            gradient = QLinearGradient(0, rect.top(), 0, rect.bottom())
            gradient.setColorAt(0, QColor(top))
            gradient.setColorAt(1, QColor(bottom))
            painter.setPen(QPen(QColor(edge), 1))
            painter.setBrush(gradient)
            painter.drawPath(path)
            painter.end()

        if not (self._prominent or self.property('roundedToolbar')) and self._interactive_hover.paint():
            return
        super().paintEvent(event)
        if self._arrow:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            diameter = max(22, min(30, self.height() - 12))
            badge = QRectF(self.width() - diameter - 8,
                           (self.height() - diameter) / 2,
                           diameter, diameter)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFFFFF" if self.isEnabled()
                                    else T.P["input_bg"]))
            painter.drawEllipse(badge)
            cx = badge.center().x()
            cy = badge.center().y()
            arrow_color = T.C_BLUE if self.isEnabled() else T.P["ink3"]
            painter.setPen(QPen(QColor(arrow_color), 2,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(QPointF(cx - 5, cy), QPointF(cx + 5, cy))
            painter.drawLine(QPointF(cx + 1, cy - 4), QPointF(cx + 5, cy))
            painter.drawLine(QPointF(cx + 1, cy + 4), QPointF(cx + 5, cy))
            painter.end()
        if self._prominent and self.isEnabled() and self.hasFocus():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
            radius = min((12 if T.app_theme == "default" else BUTTON_PILL_RADIUS) * self._scale,
                         rect.height() / 2, rect.width() / 2)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(T.P["accent_fg"]), 2))
            painter.drawRoundedRect(rect, radius, radius)
            painter.end()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange and self._shadow_effect:
            self._animate_shadow(hover=self.isEnabled() and self.underMouse())


# ================================================================ 自绘下拉框
class _ComboMask(QWidget):
    """下拉展开时的压暗遮罩 —— 宿主窗口内的普通子控件。

    点它任意位置即收起下拉。为什么不搞"点击外部自动关闭"的监听：
    子控件收不到窗口外的事件，装全窗口 eventFilter 又容易和 self.window()
    的层级打架。铺一层遮罩最直白，且必定收得到 —— 普通子控件的事件走常规
    窗口路径，不存在分层窗口命中区域为空的问题。

    圆角：遮罩铺满宿主窗口，而窗口外壳是圆角矩形。子控件**不会**被父控件
    paintEvent 里的 clipPath 裁掉（clipPath 只作用于父控件自己的绘制），
    所以直接 fillRect 就会在四个角露出直角色块 —— 深色模式下压暗层边缘
    四角发直、和圆角外壳对不上，很突兀。这里显式按同样的圆角半径裁剪。
    """

    def __init__(self, host, on_click):
        super().__init__(host)
        self._on_click = on_click
        self._opacity = 0.0
        self._background = host.grab()
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    def set_motion_opacity(self, value):
        self._opacity = max(0.0, min(1.0, float(value)))
        self.update()

    def _corner(self):
        """圆角半径取自宿主外壳（StageWidget.corner），拿不到就退回 22"""
        host = self.parentWidget()
        try:
            r = float(getattr(host, "corner", 22) or 22)
        except (TypeError, ValueError):
            r = 22.0
        return max(0.0, r)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(self.rect(), self._background)
        r = self._corner()
        if r > 0.5:
            p.setClipPath(self._rounded_clip(r))
        # 极轻的压暗：让弹层"浮"起来，又不至于挡住下面的内容
        p.fillRect(self.rect(), QColor(12, 20, 36, round(26 * self._opacity)))
        p.end()

    def _rounded_clip(self, r):
        """按「是否正好落在窗口角上」逐个角决定要不要圆。

        只对真正压在窗口外形边缘的那几个角做圆角：窗口底的左下 / 右下被
        外壳裁成圆角，遮罩必须跟着圆，否则半透明的深色会糊进圆角外的透明
        区域，四角看起来就是直角（用户红箭头标注的就是这个）。
        而遮罩贴着标题栏的那条边属于窗口**内部**拐角，跟圆反而会留下一小块
        没压暗的缺口 —— 用一块正方形把它补平。
        """
        w = self.window()
        off = self.mapTo(w, QPoint(0, 0))
        W, H = w.width(), w.height()
        at_left = off.x() <= 0
        at_top = off.y() <= 0
        at_right = off.x() + self.width() >= W
        at_bottom = off.y() + self.height() >= H

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), r, r)
        # 关键：改用「非零环绕」填充规则。
        # Qt 的 QPainterPath 默认是 OddEvenFill —— 下面补平的方块和圆角矩形
        # 一旦重叠，重叠区域的环绕数是 2（偶数）就会被判成「外面」，
        # 结果是在角上挖出一个楔形空洞。WindingFill 才是真正的并集。
        path.setFillRule(Qt.WindingFill)
        bw, bh = float(self.width()), float(self.height())
        if not (at_left and at_top):
            path.addRect(QRectF(0, 0, r, r))
        if not (at_right and at_top):
            path.addRect(QRectF(bw - r, 0, r, r))
        if not (at_left and at_bottom):
            path.addRect(QRectF(0, bh - r, r, r))
        if not (at_right and at_bottom):
            path.addRect(QRectF(bw - r, bh - r, r, r))
        return path

    def mousePressEvent(self, ev):
        if callable(self._on_click):
            self._on_click()
        ev.accept()

    def wheelEvent(self, ev):
        ev.accept()


class _PopupMotionImage(QWidget):
    """菜单动效只合成一张含阴影的缓存图，不逐帧重绘列表和阴影。"""

    def __init__(self, popup):
        super().__init__(popup.parentWidget())
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.pixmap = popup.grab()
        self.ratio = 0.0
        self.clip = QRect()
        self.setGeometry(popup.geometry())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setClipRect(self.clip)
        painter.setOpacity(self.ratio)
        painter.drawPixmap(self.rect(), self.pixmap)


class AnchoredPopupMotion(QObject):
    """菜单保持完整布局，用缓存图移动揭开，并从当前进度反向接续。"""

    finished = Signal(bool)

    def __init__(self, popup, anchor, destination, from_bottom=False, shade=None):
        super().__init__(popup)
        self.popup = popup
        self.shade = shade
        self.anchor = QRect(anchor)
        self.destination = QRect(destination)
        self.from_bottom = from_bottom
        self.progress = 0.0
        self.opening = True
        self._image = None
        self._animation = QVariantAnimation(self)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._render)
        self._animation.finished.connect(self._finish)
        popup.setGeometry(destination)

    def _prepare(self):
        if self._image is not None:
            return
        popup = self.popup
        popup.clearMask()
        popup.setGraphicsEffect(None)
        popup.setGeometry(self.destination)
        popup.ensurePolished()
        if popup.layout() is not None:
            popup.layout().activate()
        self._image = _PopupMotionImage(popup)
        popup.setUpdatesEnabled(False)
        self._image.show()
        self._image.raise_()

    def _render(self, value):
        self.progress = float(value)
        popup = self.popup
        ratio = self.progress
        closed_y = (self.anchor.bottom() - popup.height() + 1
                    if self.from_bottom else self.anchor.top())
        pos = QPoint(round(self.anchor.x() + (self.destination.x() - self.anchor.x()) * ratio),
                     round(closed_y + (self.destination.y() - closed_y) * ratio))
        width = round(min(self.anchor.width(), popup.width()) * (1 - ratio)
                      + popup.width() * ratio)
        height = round(min(self.anchor.height(), popup.height()) * (1 - ratio)
                       + popup.height() * ratio)
        clip = QRect(0, popup.height() - height if self.from_bottom else 0,
                     max(1, width), max(1, height))
        popup.move(pos)
        popup.setMask(QRegion(clip))
        if self._image is not None:
            self._image.move(pos)
            self._image.ratio = ratio
            self._image.clip = clip
            self._image.update()
        if self.shade is not None:
            self.shade.set_motion_opacity(ratio * .72)

    def set_open(self, opening, animate=True):
        self._animation.stop()
        self.opening = bool(opening)
        target = 1.0 if opening else 0.0
        if not animate or not animations_enabled():
            self._render(target)
            self._finish()
            return
        self._prepare()
        self._render(self.progress)
        self._animation.setDuration(max(70, round(420 * abs(target - self.progress))))
        self._animation.setStartValue(self.progress)
        self._animation.setEndValue(target)
        self._animation.start()

    def _release_image(self):
        if self._image is not None:
            self._image.hide()
            self._image.deleteLater()
            self._image = None
        self.popup.setUpdatesEnabled(True)

    def _finish(self):
        self._release_image()
        if self.opening:
            self.popup.setGeometry(self.destination)
            self.popup.clearMask()
            self.popup.update()
        self.finished.emit(self.opening)

    def stop(self):
        self._animation.stop()
        self._release_image()


class _ComboPopup(QFrame):
    """下拉弹层：宿主窗口内的覆盖层，而不是独立窗口。

    为什么不继续用 Qt.Popup 独立窗口：
      无边框 + WA_TranslucentBackground 的弹出窗口在 Windows 上走 layered
      window 通道。一旦 UpdateLayeredWindowIndirect 失败（本机实测出现过），
      该窗口的命中区域就是空的 —— 列表看得见、却怎么也点不中，正是"能弹出
      但不能选择"的根因。
      改成宿主窗口内的普通子控件后，事件走常规路径，问题从根上消失；
      顺带还能正常使用半透明底、圆角和阴影。
    """

    picked = Signal(int)
    dismissed = Signal()

    def __init__(self, host, items, cur, scale=1.0):
        super().__init__(host)
        self.setObjectName("ComboPopup")
        self._scale = scale

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        panel = glass("GlassStrong", radius=int(14 * scale))
        add_shadow(panel, blur=38, dy=9, alpha=110)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(5, 5, 5, 5)
        pl.setSpacing(0)
        outer.addWidget(panel)

        self.lw = QListWidget()
        self.lw.setFrameShape(QFrame.NoFrame)
        self.lw.setUniformItemSizes(True)
        self.lw.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.lw.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        row_h = max(24, int(31 * scale))
        for txt in items:
            it = QListWidgetItem(txt)
            it.setSizeHint(QSize(10, row_h))
            self.lw.addItem(it)
        self.lw.setCurrentRow(max(0, cur))
        self.lw.itemClicked.connect(self._pick)
        pl.addWidget(self.lw)

        self.lw.setStyleSheet(f"""
            QListWidget {{
                background: transparent;
                border: none;
                border-radius: {int(10 * scale)}px;
                padding: 0px;
                font-size: {max(11, int(13 * scale))}px;
                color: {T.P['ink']};
                outline: none;
            }}
            QListWidget::item {{
                padding: {max(3, int(6 * scale))}px {max(8, int(12 * scale))}px;
                border-radius: {int(8 * scale)}px;
                margin: 1px 0;
                color: {T.P['ink2']};
            }}
            QListWidget::item:hover {{
                background: {T.P['panel_hover']};
                color: {T.P['ink']};
            }}
            QListWidget::item:selected {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {T.C_BLUE_HI}, stop:1 {T.C_BLUE});
                color: #ffffff;
                font-weight: 600;
            }}
        """)

    def _pick(self, item):
        self.picked.emit(self.lw.row(item))

    def keyPressEvent(self, ev):
        k = ev.key()
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self.picked.emit(self.lw.currentRow())
        elif k == Qt.Key_Escape:
            self.dismissed.emit()
        elif k == Qt.Key_Down:
            self.lw.setCurrentRow(min(self.lw.currentRow() + 1,
                                      self.lw.count() - 1))
        elif k == Qt.Key_Up:
            self.lw.setCurrentRow(max(self.lw.currentRow() - 1, 0))
        else:
            super().keyPressEvent(ev)


class Combo(QFrame):
    """自绘下拉选择器。
    为什么不用 QComboBox：在无边框 + 置顶窗口下，QComboBox 的原生弹出视图
    会丢失父级绘制上下文，样式表里的半透明圆角背景被渲染成纯黑矩形（已实测复现）。"""

    changed = Signal(int)

    def __init__(self, parent=None, scale=1.0, width=None, height=None):
        super().__init__(parent)
        self.setObjectName("Combo")
        self._scale = scale
        self.setFocusPolicy(Qt.StrongFocus)
        self._items = []          # [(text, data)]
        self._cur = 0
        self._popup = None
        self._mask = None
        self._open = False
        self._closing = False
        self._popup_anim = None
        self._popup_motion = None
        self._popup_end_rect = QRect()
        self._keyboard_popup = False
        self._outside_filter_installed = False
        self._popup_clip_height = 0
        self._popup_grow_from_bottom = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(height or max(30, int(42 * scale)))
        if width:
            self.setFixedWidth(width)
        else:
            self.setMinimumWidth(int(130 * scale))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(int(14 * scale), 0, int(11 * scale), 0)
        lay.setSpacing(int(6 * scale))
        self.lb = QLabel("")
        self.lb.setStyleSheet("background:transparent;")
        lay.addWidget(self.lb, 1)
        self.arrow = QLabel()
        sz = int(16 * scale) + 2
        self.arrow.setFixedSize(sz, sz)
        self.arrow.setAlignment(Qt.AlignCenter)
        self.arrow.setStyleSheet("background:transparent;")
        lay.addWidget(self.arrow)
        self.refresh()
        add_shadow(self, blur=14, dy=3, alpha=24)

    # ---------------- 数据 API
    def addItem(self, text, data=None):
        self._items.append((text, data))
        self.refresh()

    def clear(self):
        self._items = []
        self._cur = 0
        self.refresh()

    def count(self):
        return len(self._items)

    def itemText(self, i):
        """第 i 项的显示文本（``QComboBox`` 标准接口）。

        ★ v1.9.8 补：验收脚本和"遍历下拉看看装了啥"的调试代码都会用它，
          缺了就只能去摸 ``_items`` 私有属性 —— 私有属性改了名，调用方悄悄坏掉。
        """
        if 0 <= i < len(self._items):
            return self._items[i][0]
        return ""

    def itemData(self, i):
        """第 i 项挂的数据（``QComboBox`` 标准接口）。"""
        if 0 <= i < len(self._items):
            return self._items[i][1]
        return None

    def items(self):
        """全部项的快照 ``[(text, data), ...]`` —— 只读，改它不影响控件。"""
        return list(self._items)

    def currentIndex(self):
        return self._cur

    def currentData(self):
        if 0 <= self._cur < len(self._items):
            return self._items[self._cur][1]
        return None

    def currentText(self):
        if 0 <= self._cur < len(self._items):
            return self._items[self._cur][0]
        return ""

    def setCurrentIndex(self, i, emit=False):
        if 0 <= i < len(self._items):
            self._cur = i
            self.refresh()
            if emit:
                self.changed.emit(i)

    def findData(self, data):
        for i, (_, d) in enumerate(self._items):
            if d == data:
                return i
        return -1

    def findText(self, text):
        """按显示文本找下标（``QComboBox`` 的标准接口，本控件缺了它）。

        补上它是因为调用方常常只拿到一个**字符串**（比如从地面站读到的
        "协调转弯"），而这个字符串是显示文本还是 data 得看当初怎么加的项。
        两个查找都提供，调用方不必猜。
        """
        for i, (t, _d) in enumerate(self._items):
            if str(t) == str(text):
                return i
        return -1

    # ---------------- 绘制
    def refresh(self):
        P = T.P
        sc = self._scale
        self.setStyleSheet(f"""
            QFrame#Combo {{
                background: {P['input_bg']};
                border: none;
                border-radius: {int((6 if T.app_theme == "chiyo" else 13) * sc)}px;
            }}
            QFrame#Combo:focus {{border:none;background:{P['accent_soft']};}}
            QFrame#Combo:hover {{
                border: none;
                background: {P['input_bg_focus']};
            }}
            QLabel {{border:0; border-radius:0; background:transparent;}}
        """)
        self.lb.setText(self.currentText())
        self.lb.setStyleSheet(
            f"font-size:{max(11, int(13 * sc))}px; font-weight:600;"
            f"color:{P['ink']}; background:transparent;")
        self.arrow.setPixmap(
            Icons.chevron(P["ink3"], size=int(14 * sc), down=not self._open))

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Down):
            self.open_popup(animate=False)
            ev.accept()
        elif ev.key() == Qt.Key_Escape:
            self.close_popup()
            ev.accept()
        else:
            super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.toggle()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            # QWidget 默认把双击继续按普通按下处理，会把刚展开的列表关掉。
            # 双击锚点保持展开；明确的下一次单击仍可按常规方式收起。
            self.open_popup()
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)

    def hideEvent(self, event):
        if self._popup is not None:
            self._dispose_popup()
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        """弹层外点击先收起旧菜单，再把这次点击留给目标控件。"""
        if (event.type() == QEvent.Type.MouseButtonPress and
                self._popup is not None):
            popup = self._popup
            global_pos = event.globalPosition().toPoint()
            popup_rect = QRect(popup.mapToGlobal(QPoint(0, 0)), popup.size())
            anchor_rect = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
            in_popup = (popup_rect.contains(global_pos) or
                        watched is popup or
                        (isinstance(watched, QWidget) and
                         popup.isAncestorOf(watched)))
            in_anchor = (anchor_rect.contains(global_pos) or
                         watched is self or
                         (isinstance(watched, QWidget) and
                          self.isAncestorOf(watched)))
            if not in_popup and not in_anchor:
                self.close_popup()
        return False

    def _install_outside_filter(self):
        app = QApplication.instance()
        if app is not None and not self._outside_filter_installed:
            app.installEventFilter(self)
            self._outside_filter_installed = True

    def _remove_outside_filter(self):
        app = QApplication.instance()
        if app is not None and self._outside_filter_installed:
            app.removeEventFilter(self)
        self._outside_filter_installed = False

    def toggle(self):
        if self._open:
            self.close_popup()
        else:
            self.open_popup()

    def open_popup(self, animate=True):
        """在宿主窗口内展开列表。

        定位策略：默认贴在下沿；下方空间不够就翻到上方；再不够就贴着窗口
        底边。水平方向同样防溢出 —— 弹层是子控件，超出父控件会被裁掉。
        """
        if not self._items:
            return
        if self._popup is not None:
            if self._closing:
                self._open = True
                self._closing = False
                self.refresh()
                host = self._popup.parentWidget()
                anchor = QRect(self.mapTo(host, QPoint(0, 0)), self.size())
                self._animate_popup(self._popup, self._mask, anchor,
                                    self._popup_end_rect, opening=True, animate=animate)
            return
        if self._open:
            return
        host = getattr(self.window(), "stage", None) or self.window()
        self._open = True
        self._keyboard_popup = not animate
        self._closing = False
        self.refresh()

        w = max(self.width(), int(160 * self._scale))
        rows = min(len(self._items), 11)
        h = min(int(rows * 33 * self._scale) + 32, 430)

        # 遮罩：点任意位置收起
        self._mask = _ComboMask(host, self.close_popup)
        # 遮罩仅负责视觉压暗；应用级过滤器负责收起并让点击穿透到新目标。
        self._mask.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._mask.setGeometry(host.rect())
        self._mask.set_motion_opacity(0.0 if animations_enabled() else 0.72)
        self._mask.show()
        self._mask.raise_()

        pop = _ComboPopup(host, [t for t, _ in self._items], self._cur,
                          scale=self._scale)
        pop.setFixedSize(w, h)
        pos = self.mapTo(host, QPoint(0, self.height() + 4))
        self._popup_grow_from_bottom = False
        if pos.y() + h > host.height() - 6:
            up = self.mapTo(host, QPoint(0, 0)).y() - h - 4
            pos.setY(up if up >= 6 else max(6, host.height() - h - 6))
            self._popup_grow_from_bottom = True
        if pos.x() + w > host.width() - 6:
            pos.setX(max(6, host.width() - w - 6))
        end_rect = QRect(pos, QSize(w, h))
        self._popup_end_rect = QRect(end_rect)
        anchor = QRect(self.mapTo(host, QPoint(0, 0)), self.size())
        start_rect = QRect(anchor.x(), anchor.y(), anchor.width(),
                           max(30, min(anchor.height(), int(46 * self._scale))))
        pop.setGeometry(end_rect)
        self._popup_clip_height = (max(30, start_rect.height())
                                   if animations_enabled() else h)
        clip_y = (h - self._popup_clip_height
                  if self._popup_grow_from_bottom else 0)
        pop.setMask(QRegion(0, clip_y, w, self._popup_clip_height))
        pop.picked.connect(self._on_picked)
        pop.dismissed.connect(self.close_popup)
        self._popup = pop
        self._install_outside_filter()
        pop.show()
        pop.raise_()
        pop.setFocus()
        self._animate_popup(pop, self._mask, anchor, end_rect, opening=True, animate=animate)

    def _animate_popup(self, popup, mask, start_rect, end_rect, opening, animate=True):
        if self._popup_motion is None:
            self._popup_motion = AnchoredPopupMotion(
                popup, start_rect, end_rect, self._popup_grow_from_bottom, mask)
            self._popup_motion.finished.connect(self._popup_transition_finished)
        self._popup_motion.set_open(opening, animate=animate)

    def _popup_transition_finished(self, opening):
        if opening:
            return
        popup, mask = self._popup, self._mask
        focus = QApplication.focusWidget()
        restore = popup is not None and (focus is popup or
                  (focus is not None and popup.isAncestorOf(focus)))
        self._popup = None
        self._mask = None
        self._popup_motion = None
        self._closing = False
        self._remove_outside_filter()
        for widget in (popup, mask):
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        if restore:
            self.setFocus(Qt.PopupFocusReason)


    def _dispose_popup(self):
        self._remove_outside_filter()
        focus = QApplication.focusWidget()
        restore = self._popup is not None and (focus is self._popup or
                  (focus is not None and self._popup.isAncestorOf(focus)))
        if self._popup_motion is not None:
            self._popup_motion.stop()
            self._popup_motion = None
        group = self._popup_anim
        self._popup_anim = None
        if group is not None:
            try:
                group.stop()
            except RuntimeError:
                pass
        for widget in (self._popup, self._mask):
            if widget is not None:
                try:
                    widget.hide()
                    widget.deleteLater()
                except RuntimeError:
                    pass
        self._popup = None
        self._mask = None
        self._open = False
        self._closing = False
        if restore and self.isVisible():
            self.setFocus(Qt.PopupFocusReason)

    def _popup_gone(self, *_):
        self._open = False
        self._popup = None
        self._remove_outside_filter()
        self.refresh()

    def close_popup(self):
        if self._closing:
            return
        self._open = False
        self.refresh()
        popup, mask = self._popup, self._mask
        if popup is None:
            self._dispose_popup()
            return
        if not animations_enabled():
            self._dispose_popup()
            return
        self._closing = True
        host = popup.parentWidget()
        anchor = QRect(self.mapTo(host, QPoint(0, 0)), self.size())
        self._animate_popup(popup, mask, anchor, self._popup_end_rect, opening=False,
                            animate=not self._keyboard_popup)

    def _on_picked(self, i):
        if self._closing:
            return
        self._cur = i
        self.close_popup()
        self.changed.emit(i)


# ================================================================ 轻提示
class Toast(QFrame):
    """贴在父窗口底部中央的一条轻提示，到点自动消失。

    为什么不用 QMessageBox：它要用户点一下「确定」才能继续。
    「读不到 LinkGCS2 的数据，已帮你打开作答栏」这种事属于**正常路径**，
    弹个模态窗会让用户以为软件坏了；而且弹窗一挡，用户还得先关掉才能答题。
    这里浮一条、2~3 秒自己走，不抢焦点、不打断操作。

    两个实现上的坑：
    1) **不能用 QGraphicsDropShadowEffect**：它是控件级的，挂上去之后
       同一控件的 QSS ``border-radius`` 就不再影响窗口形状，圆角会方掉。
       所以这里放弃阴影，改用半透明深色底 + 描边来获得"浮起来"的观感。
    2) **必须持引用**：没有父窗口的 QWidget 会随 Python 侧引用一起析构，
       表现是"闪一下就没了"（判题弹窗上踩过同一个坑）。调用方要存住它。
    """

    def __init__(self, parent=None, text="", scale=1.0, ms=2800):
        super().__init__(parent)
        self._scale = float(scale or 1.0)
        self._ms = int(ms)
        self._timer = None
        self._anim = None

        # 挂名字只是为了便于样式表选中；颜色直接内联，避开"全局 QSS 覆盖内联"的坑
        self.setObjectName("Toast")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        P = T.P
        self.setStyleSheet(
            "QFrame#Toast{"
            "background:%s;"
            "border:1px solid %s;"
            "border-radius:%dpx;}"
            % (P["tip_bg"], P.get("glass_edge", "rgba(255,255,255,0.14)"),
               int(11 * self._scale)))

        lay = QHBoxLayout(self)
        m = int(14 * self._scale)
        lay.setContentsMargins(m, int(9 * self._scale),
                               m, int(9 * self._scale))
        lay.setSpacing(0)
        self.lab = QLabel(text)
        self.lab.setWordWrap(False)
        self.lab.setStyleSheet(
            "font-size:%dpx; font-weight:600; color:%s; background:transparent;"
            % (max(10, int(12.5 * self._scale)), P["tip_fg"]))
        lay.addWidget(self.lab)
        self.adjustSize()

    # ---------------------------------------------------------- 对外
    def set_text(self, text):
        self.lab.setText(text)
        self.adjustSize()

    def pop(self):
        """摆到父窗口底部居中，淡入，``ms`` 毫秒后自动淡出销毁。"""
        par = self.parentWidget()
        if par is None:
            self.deleteLater()
            return
        # 父窗口可能是还没显示出来的（构造期就调），先把布局跑完再算位置
        lay = par.layout()
        if lay is not None:
            lay.activate()
        self._place(par)
        self.show()
        self.raise_()
        fade_in(self, ms=200)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._bye)
        self._timer.start(self._ms)

    def _place(self, par):
        """摆位：横向居中、纵向贴底（留一点边距）。"""
        pad = int(34 * self._scale)
        w, h = self.width(), self.height()
        x = max(6, (par.width() - w) // 2)
        y = max(6, par.height() - h - pad)
        self.move(x, y)

    def _bye(self):
        """淡出。动画结束才真的销毁 —— 提前 deleteLater 会拍到半透明残影。"""
        eff = QGraphicsOpacityEffect(self)
        eff.setOpacity(1.0)
        self.setGraphicsEffect(eff)
        a = QPropertyAnimation(eff, b"opacity", self)
        a.setDuration(240)
        a.setStartValue(1.0)
        a.setEndValue(0.0)
        a.setEasingCurve(QEasingCurve.InCubic)
        a.finished.connect(self.deleteLater)
        a.start()
        self._anim = a

    def resizeEvent(self, ev):
        # 父窗口尺寸变了（比如作答栏展开把工作台撑宽）要跟着重新居中
        super().resizeEvent(ev)


# ================================================================ 三按钮确认框

class ConfirmDialog(QFrame):
    """一个能带「主操作 / 次操作 / 取消」三条出路的确认框。

    为什么不用 QMessageBox：它的按钮只能是标准角色（Yes/No/Cancel），
    而这类场景的按钮文案是**具体的动宾短语** —— 「切到第 1 题」/「仍按第 2 题判」/
    「取消」。用户得一眼看懂点了会发生什么，而不是去猜 Yes 是哪一个。

    无父窗口，独立顶层，居中贴在父窗口上；同样必须由调用方持引用。
    """

    def __init__(self, parent=None, title="", text="", ok_text="确定",
                 alt_text="", cancel_text="取消", on_ok=None, on_alt=None,
                 scale=1.0):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self._scale = float(scale or 1.0)
        self._on_ok = on_ok
        self._on_alt = on_alt

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(0)
        card = glass("GlassStrong", radius=18)
        attach_shadow(card, blur=54, dy=12, alpha=125)
        outer.addWidget(card)

        cl = QVBoxLayout(card)
        m = int(26 * self._scale)
        cl.setContentsMargins(m, int(22 * self._scale), m, int(18 * self._scale))
        cl.setSpacing(int(12 * self._scale))

        t = QLabel(title)
        t.setWordWrap(True)
        t.setStyleSheet(
            "font-size:%dpx; font-weight:800; color:%s; background:transparent;"
            % (round(17 * self._scale), T.P["ink"]))
        cl.addWidget(t)

        body = QLabel(text)
        body.setWordWrap(True)
        body.setStyleSheet(
            "font-size:%dpx; color:%s; background:transparent; line-height:175%%;"
            % (max(11, round(12.8 * self._scale)), T.P["ink2"]))
        # 文案里用 **粗体** 标关键信息 —— 只是把它去掉，别把星号显示出来
        body.setText(str(text).replace("**", ""))
        cl.addWidget(body)

        row = QHBoxLayout()
        row.setContentsMargins(0, int(4 * self._scale), 0, 0)
        row.setSpacing(int(8 * self._scale))
        row.addStretch()
        b_cancel = QPushButton(cancel_text)
        b_cancel.setCursor(Qt.PointingHandCursor)
        b_cancel.setMinimumHeight(int(34 * self._scale))
        b_cancel.setStyleSheet(btn_style("default", scale=self._scale, font_size=13))
        b_cancel.clicked.connect(self.close)
        row.addWidget(b_cancel)

        if alt_text:
            self.b_alt = QPushButton(alt_text)
            self.b_alt.setCursor(Qt.PointingHandCursor)
            self.b_alt.setMinimumHeight(int(34 * self._scale))
            self.b_alt.setStyleSheet(
                btn_style("default", scale=self._scale, font_size=13))
            self.b_alt.clicked.connect(self._alt)
            row.addWidget(self.b_alt)

        self.b_ok = QPushButton(ok_text)
        self.b_ok.setCursor(Qt.PointingHandCursor)
        self.b_ok.setMinimumHeight(int(34 * self._scale))
        self.b_ok.setStyleSheet(btn_style("primary", scale=self._scale, font_size=13))
        self.b_ok.clicked.connect(self._ok)
        row.addWidget(self.b_ok)
        cl.addLayout(row)

        self.setMinimumWidth(int(430 * self._scale))
        self.resize(int(500 * self._scale), self.sizeHint().height())

    def _ok(self):
        cb, self._on_ok = self._on_ok, None
        self.close()
        if cb:
            try:
                cb()
            except Exception:
                pass

    def _alt(self):
        cb, self._on_alt = self._on_alt, None
        self.close()
        if cb:
            try:
                cb()
            except Exception:
                pass

    def pop(self):
        """显示并居中贴在父窗口上。"""
        self.show()
        par = self.parent()
        if par is not None and par.isVisible():
            g = par.frameGeometry()
            self.move(g.center().x() - self.width() // 2,
                      g.center().y() - self.height() // 2)
        else:
            scr = self.screen().availableGeometry()
            self.move(scr.center().x() - self.width() // 2,
                      scr.center().y() - self.height() // 2)
        self.raise_()
        self.activateWindow()
