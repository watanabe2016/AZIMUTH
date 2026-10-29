"""学习工作台：蓝色学习工具，安静精确，四页分别采用阅读桌、运行专注、画布和分类编辑器。
颜色继承 theme.py 的语义令牌；布局切换保持原生控件与业务状态。
"""
from pathlib import Path
from PySide6.QtCore import Qt, Signal, QSize, QRect, QRectF, QEvent, QVariantAnimation, QEasingCurve, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap, QIcon
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QWidget, QFrame, QHBoxLayout, QVBoxLayout, QPushButton,
    QLabel, QLineEdit, QGraphicsBlurEffect, QGraphicsDropShadowEffect)
import theme as T
import config as cfg
from ui_kit import IconButton, ToggleSwitch, animations_enabled


def themed_icon(name, color):
    """只对小尺寸矢量进行主题着色。"""
    path = Path(T.asset_path('icons', 'workbench', name + '.svg'))
    if not path.is_file():
        return QIcon()
    renderer = QSvgRenderer(path.read_bytes().replace(b'currentColor', color.encode()))
    image = QPixmap(20, 20)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    return QIcon(image)


class SegmentRail(QWidget):
    """分类共用一个移动底板，业务切换即时完成，底板从当前进度接力。"""
    changed = Signal(str)

    def __init__(self, items, parent=None, vertical=False):
        super().__init__(parent)
        self.setObjectName('WorkbenchRail')
        self.items = items
        self.current = None
        self.vertical = vertical
        self.buttons = {}
        self.plate = QFrame(self)
        self.plate.setAttribute(Qt.WA_TransparentForMouseEvents)
        plate_shadow=QGraphicsDropShadowEffect(self.plate)
        plate_shadow.setBlurRadius(18);plate_shadow.setOffset(0,4)
        plate_shadow.setColor(QColor(19,55,100,42))
        self.plate.setGraphicsEffect(plate_shadow)
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(520)
        self.animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.animation.valueChanged.connect(self._frame)
        self.box = QVBoxLayout(self) if vertical else QHBoxLayout(self)
        self.box.setContentsMargins(4, 4, 4, 4)
        self.box.setSpacing(4)
        for key, text, icon in items:
            b = QPushButton(text, self)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(40)
            b.setMinimumWidth(0)
            b.setAccessibleName(text)
            b.setIconSize(QSize(18, 18))
            b.clicked.connect(lambda checked=False, k=key: self.set_current(k, emit=True))
            self.buttons[key] = b
            self.box.addWidget(b, 0 if vertical else 1)
        if vertical:
            self.box.addStretch(1)
        self._has_stretch=vertical
        self.plate.lower()
        self.refresh_theme()

    def set_current(self, key, emit=False, animate=True):
        if key not in self.buttons:
            return
        changed = self.current != key
        self.current = key
        for value, button in self.buttons.items():
            button.setChecked(value == key)
        # 页面同步同一选项时继续现有动画，避免点击信号回调将底板瞬间归位。
        if changed or self.animation.state() != QVariantAnimation.Running:
            self._move(animate and changed)
        if emit and changed:
            self.changed.emit(key)

    def _move(self, animate):
        if self.current is None:
            return
        target = self.buttons[self.current].geometry()
        if not target.isValid():
            return
        self.animation.stop()
        self.start_rect, self.end_rect = self.plate.geometry(), target
        if animate and animations_enabled() and self.isVisible() and self.start_rect.isValid():
            self.animation.setStartValue(0.0)
            self.animation.setEndValue(1.0)
            self.animation.start()
        else:
            self.plate.setGeometry(target)
        self.plate.lower()

    def _frame(self, value):
        if not animations_enabled():
            self.animation.stop();self.plate.setGeometry(self.end_rect);return
        a, b, t = self.start_rect, self.end_rect, float(value)
        self.plate.setGeometry(QRect(round(a.x()+(b.x()-a.x())*t),
            round(a.y()+(b.y()-a.y())*t), round(a.width()+(b.width()-a.width())*t),
            round(a.height()+(b.height()-a.height())*t)))

    def set_vertical(self, vertical):
        if vertical == self.vertical:
            return
        self.vertical = vertical
        self.box.setDirection(QVBoxLayout.TopToBottom if vertical else QHBoxLayout.LeftToRight)
        for index in range(len(self.buttons)):self.box.setStretch(index,0 if vertical else 1)
        if self._has_stretch:self.box.setStretch(len(self.buttons),1 if vertical else 0)
        self._move(False)

    def refresh_theme(self):
        self.setStyleSheet(f'QWidget#WorkbenchRail{{background:{T.P["panel_soft"]};border:0;border-radius:13px;}}')
        self.plate.setStyleSheet(f'QFrame{{background:{T.P["accent_soft"]};border:0;border-radius:10px;}}')
        for key, text, icon in self.items:
            b = self.buttons[key]
            b.setStyleSheet(f'QPushButton{{background:transparent;border:0;border-radius:10px;padding:6px 8px;color:{T.P["ink2"]};font-size:13px;font-weight:600;}}'
                f'QPushButton:checked{{color:{T.P["accent_fg"]};}}'
                f'QPushButton:hover{{background:{T.P["panel_hover"]};}}'
                f'QPushButton:focus{{background:{T.P["panel_hover"]};font-weight:800;}}')
            if icon:
                b.setIcon(themed_icon(icon, T.P['accent_fg']))
        self._move(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, lambda: self._move(False))

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self._move(False))

    def hideEvent(self, event):
        self.animation.stop()
        super().hideEvent(event)


def transparent_snapshot(widget):
    """只捕获控件自身与子控件，跳过 Qt 默认窗口底色。"""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QPixmap, QRegion
    ratio = widget.devicePixelRatioF()
    pixmap = QPixmap(round(widget.width() * ratio), round(widget.height() * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    widget.render(pixmap, QPoint(), QRegion(), QWidget.DrawChildren)
    return pixmap


class TimerVisualTransition(QWidget):
    """计时信息逐组淡入淡出，仅移动透明快照，计时逻辑持续运行。"""
    def __init__(self, host, widgets, entering=True, frames=None, previous=None):
        super().__init__(host)
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QRegion
        self.entering = entering
        self.previous = previous or []
        self.widgets = [widget for widget in widgets if widget.isVisible()]
        self.frames = frames if frames is not None else [
            (transparent_snapshot(widget), widget.mapTo(host, QPoint(0, 0))) for widget in self.widgets]
        self._disposed = False
        self.progress = 0.0
        for widget in self.widgets:
            widget.setMask(QRegion(-2, -2, 1, 1))
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setGeometry(host.rect())
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(320 if entering else 180)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.valueChanged.connect(self._frame)
        self.animation.finished.connect(self.dispose)
        host.installEventFilter(self)
        self.show(); self.raise_(); self.animation.start()

    def _frame(self, value):
        if not animations_enabled():
            self.dispose(); return
        self.progress = float(value)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.previous and self.progress < .32:
            amount = self.progress / .32
            painter.setOpacity(1 - amount)
            for pixmap, origin in self.previous:
                painter.drawPixmap(origin.x(), origin.y() - round(8 * amount), pixmap)
        for index, (pixmap, origin) in enumerate(self.frames):
            delay = min(index, 4) * .035
            start = .32 if self.previous else 0
            t = max(0, min(1, (self.progress - start - delay) / (1 - start - delay)))
            eased = 1 - (1 - t) ** 3
            painter.setOpacity(eased if self.entering else 1 - eased)
            offset = 12 * (1 - eased) if self.entering else -10 * eased
            painter.drawPixmap(origin.x(), origin.y() + round(offset), pixmap)
        painter.end()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize:
            # 布局交接会改变页面尺寸，快照跟随画布，保持当前动画进度。
            self.setGeometry(obj.rect())
        elif event.type() == QEvent.Hide:
            self.dispose()
        return False

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self.animation.stop()
        for widget in self.widgets:
            widget.clearMask(); widget.update()
        host = self.parentWidget()
        if host is not None:
            host.removeEventFilter(self)
            if getattr(host, '_timer_visual_motion', None) is self:
                host._timer_visual_motion = None
        self.hide(); self.deleteLater()


class DockExit(QWidget):
    """训练卡的退场与回归共用快照动画，不逐帧重排真实布局。"""
    def __init__(self, host, pixmap, origin, returning_dock=None):
        super().__init__(host)
        self.pixmap, self.origin = pixmap, origin
        self.returning_dock = returning_dock
        if returning_dock is not None:
            from PySide6.QtGui import QRegion
            returning_dock.setMask(QRegion(-2, -2, 1, 1))
        self.progress = 0.0
        self._disposed = False
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setGeometry(host.rect())
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(280)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.valueChanged.connect(self._frame)
        self.animation.finished.connect(self.dispose)
        host.installEventFilter(self)
        self.show()
        self.raise_()
        self.animation.start()

    def _frame(self, value):
        if not animations_enabled():
            self.dispose()
            return
        self.progress = float(value)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        returning = self.returning_dock is not None
        painter.setOpacity(self.progress if returning else 1 - self.progress)
        offset = 1 - self.progress if returning else self.progress
        painter.drawPixmap(self.origin.x() + round(self.pixmap.width() * .45 * offset),
                           self.origin.y(), self.pixmap)
        painter.end()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize:
            # 收起侧栏可能触发页面布局更新，不能把正常退场提前中止。
            self.setGeometry(obj.rect())
        elif event.type() == QEvent.Hide:
            self.dispose()
        return False

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self.animation.stop()
        if self.returning_dock is not None:
            self.returning_dock.clearMask()
            self.returning_dock.update()
        host = self.parentWidget()
        if host is not None:
            host.removeEventFilter(self)
            if getattr(host, '_arrangement_exit', None) is self:
                host._arrangement_exit = None
        self.hide()
        self.deleteLater()


class LayoutReveal(QWidget):
    """旧布局仅绘制静态快照，新控件即刻可操作；不逐帧重排业务控件。"""
    def __init__(self, host, pixmap):
        super().__init__(host)
        self._disposed=False
        host._workbench_reveal=self
        self.pixmap = pixmap
        self.progress = 0.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(host.rect())
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(520)
        self.animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.valueChanged.connect(self._frame)
        self.animation.finished.connect(self.dispose)
        host.installEventFilter(self)
        self.show()
        self.raise_()
        self.animation.start()

    def _frame(self, value):
        if not animations_enabled():self.dispose();return
        self.progress = float(value)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setOpacity(1-self.progress)
        p.drawPixmap(round(-8*self.progress), 0, self.pixmap)
        p.end()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.Hide):
            self.dispose()
        return False

    def dispose(self):
        if self._disposed:return
        self._disposed=True
        self.animation.stop()
        host = self.parentWidget()
        if host is not None:
            host.removeEventFilter(self)
            if getattr(host, '_workbench_reveal', None) is self:
                host._workbench_reveal = None
        self.hide()
        self.deleteLater()


def reveal_layout(host, mutation, background=None):
    old = getattr(host, '_workbench_reveal', None)
    if old is not None:
        old.dispose()
    snapshot = None
    # 刚 show 的窗口可能尚未完成首次绘制，此时截图会触发 Qt 特效的重入绘制。
    # 窗口真正可见后继续使用原有转场；未显示的旧布局无需制作退场快照。
    handle = host.window().windowHandle()
    capture = host.isVisible() and animations_enabled() and bool(handle and handle.isExposed())
    if capture and background is not None:
        # 透明教学页的默认窗口底色不是实际主题底色，显式绘制可避免深色转场闪白。
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QPixmap,QRegion
        snapshot=QPixmap(host.size());snapshot.fill(QColor(background))
        host.render(snapshot,QPoint(),QRegion(),QWidget.DrawChildren)
    elif capture:
        snapshot = host.grab()
    mutation()
    if host.layout() is not None:
        host.layout().activate()
    if snapshot is not None and not snapshot.isNull():
        return LayoutReveal(host, snapshot)
    return None


class PreferencePreview(QFrame):
    """使用真实按钮、输入框和开关的局部外观预览，不绘制假窗口。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('PreferencePreview')
        self.setMinimumHeight(190)
        self.backdrop = QLabel(self)
        self.backdrop.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.blur = QGraphicsBlurEffect(self.backdrop)
        self.backdrop.setGraphicsEffect(self.blur)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 16, 18, 16)
        self.caption = QLabel('当前外观 · 实际控件')
        self.title = QLabel('把每一次练习，写成新的进步。')
        self.title.setWordWrap(True)
        self.field = QLineEdit('航向、风向、避让点、夹角')
        self.field.setReadOnly(True)
        self.field.setFocusPolicy(Qt.NoFocus)
        self.action = IconButton('开始练习', 'pen', 'primary', font_size=13)
        self.action.setFocusPolicy(Qt.NoFocus)
        self.action.setToolTip('外观预览中的示例按钮')
        self.action.setMinimumHeight(40)
        self.switch = ToggleSwitch()
        self.switch.setChecked(True)
        self.switch.setFocusPolicy(Qt.NoFocus)
        row = QHBoxLayout()
        row.addWidget(self.action)
        row.addStretch(1)
        row.addWidget(QLabel('提示音'))
        row.addWidget(self.switch)
        box.addWidget(self.caption)
        box.addWidget(self.title)
        box.addWidget(self.field)
        box.addLayout(row)
        self._background_key = None
        self.refresh_theme()

    def refresh_theme(self):
        self.setStyleSheet(f'QFrame#PreferencePreview{{background:{T.P["panel_bg"]};border:1px solid {T.P["input_border"]};border-radius:14px;}}'
            f'QFrame#PreferencePreview QLabel{{color:{T.P["ink2"]};background:transparent;border:0;}}')
        self.caption.setStyleSheet(f'color:{T.P["ink3"]};font-size:12px;background:transparent;')
        self.title.setStyleSheet(f'color:{T.P["ink"]};font-size:19px;font-weight:700;background:transparent;')
        self.field.setStyleSheet(f'background:{T.P["input_bg"]};color:{T.P["ink2"]};border:1px solid {T.P["input_border"]};border-radius:9px;padding:8px;')
        self.action.refresh()
        self.switch.update()
        self.update_background()

    def update_background(self):
        path = cfg.get('wallpaper_path', '')
        key = (path, self.size().width(), self.size().height(), cfg.get('wallpaper_opacity', 38), cfg.get('wallpaper_blur', 0))
        if key == self._background_key:
            return
        self._background_key = key
        self.backdrop.setGeometry(self.rect().adjusted(8, 8, -8, -8))
        source = QPixmap(path) if path else QPixmap()
        self.backdrop.setVisible(not source.isNull())
        if not source.isNull():
            image = QPixmap(self.backdrop.size())
            image.fill(Qt.transparent)
            p = QPainter(image)
            p.setOpacity(float(key[3])/100)
            scaled = source.scaled(image.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap((image.width()-scaled.width())//2, (image.height()-scaled.height())//2, scaled)
            p.end()
            self.backdrop.setPixmap(image)
            self.blur.setBlurRadius(float(key[4]))
        self.backdrop.lower()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_background()
