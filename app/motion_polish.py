"""有限、可打断的界面动效，不占用控件的阴影或透明度效果。"""
import math

from PySide6.QtCore import Qt, QEvent, QPoint, QPointF, QRect, QRectF, QSize, QVariantAnimation, QEasingCurve, QObject
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget, QSizePolicy, QGraphicsOpacityEffect
import theme as T
from ui_kit import animations_enabled


class AccentOverlay(QWidget):
    """在真实操作发生后绘制短反馈，加载扫描由调用者明确结束。"""

    def __init__(self, anchor):
        super().__init__(anchor)
        self.anchor = anchor
        self.progress = 1.0
        self.role = "trace"
        self.motion = QVariantAnimation(self)
        self.motion.setStartValue(0.0)
        self.motion.setEndValue(1.0)
        self.motion.setEasingCurve(QEasingCurve.InOutCubic)
        self.motion.valueChanged.connect(self._advance)
        self.motion.finished.connect(self.hide)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.hide()
        anchor.installEventFilter(self)

    def play(self, role="trace", busy=False):
        self.motion.stop()
        self.role = role
        if not animations_enabled() or not self.anchor.isVisible():
            self.hide()
            return
        self.progress = 0.0
        self.setGeometry(self.anchor.rect())
        self.show()
        self.raise_()
        self.motion.setDuration(1200 if busy or role == "wave" else 760)
        self.motion.setLoopCount(-1 if busy else 1)
        self.motion.start()

    def _advance(self, value):
        if not animations_enabled():
            self.stop()
            return
        self.progress = float(value)
        self.update()

    def stop(self):
        self.motion.stop()
        self.hide()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            self.setGeometry(watched.rect())
        elif event.type() == QEvent.Hide:
            self.stop()
        return False

    def hideEvent(self, event):
        motion = getattr(self, "motion", None)
        if motion is not None:
            motion.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        progress = self.progress
        color = QColor(T.P["ok_fg"] if self.role == "success" else
                       T.P["err_fg"] if self.role == "error" else T.C_BLUE)
        color.setAlpha(round(180 * min(1, progress * 5) * min(1, (1 - progress) * 4)))
        painter.setPen(QPen(color, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        y = self.height() - 3.0
        width = max(4.0, self.width() - 12.0)
        if self.role == "scan":
            center = 6 + progress * width
            painter.drawLine(QPointF(max(6, center - width * .22), y), QPointF(center, y))
        elif self.role == "wave":
            center = self.width() / 2
            for i in range(9):
                height = (3 + 10 * abs(math.sin(i * .7 + progress * math.tau))) * math.sin(progress * math.pi)
                painter.drawLine(QPointF(center + (i - 4) * 5, y), QPointF(center + (i - 4) * 5, y - height))
        elif self.role in ("success", "error"):
            painter.drawLine(QPointF(6, y), QPointF(6 + width * min(1, progress * 2), y))
        else:
            # 强调线只画在边缘，题卡内容和输入文字始终可见。
            painter.drawLine(QPointF(6, y), QPointF(6 + width * progress, y))
        painter.end()


def accent(anchor, role="trace", busy=False):
    overlay = getattr(anchor, "_polish_accent", None)
    if overlay is None:
        overlay = AccentOverlay(anchor)
        anchor._polish_accent = overlay
    overlay.play(role, busy)
    return overlay


class PhaseRelay(QWidget):
    """计时阶段指示线连续移动，快速切换从当前呈现位置接续。"""

    def __init__(self, host, labels):
        super().__init__(host)
        self.host = host
        self.labels = labels
        self.position = 0.0
        self.target = 0.0
        self.active = False
        self.motion = QVariantAnimation(self)
        self.motion.setDuration(650)
        self.motion.setEasingCurve(QEasingCurve.InOutCubic)
        self.motion.valueChanged.connect(self._advance)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)
        host.installEventFilter(self)
        self.setGeometry(host.rect())

    def set_stage(self, index):
        self.motion.stop()
        self.active = index is not None
        self.target = float(index or 0)
        if not self.active or not self.isVisible() or not animations_enabled():
            self._advance(self.target)
            return
        self.motion.setStartValue(self.position)
        self.motion.setEndValue(self.target)
        self.motion.start()

    def _advance(self, value):
        self.position = float(value)
        if not animations_enabled():
            self.motion.stop()
            self.position = self.target
        self.update()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            self.setGeometry(watched.rect())
        return False

    def hideEvent(self, event):
        self.motion.stop()
        self.position = self.target
        super().hideEvent(event)

    def paintEvent(self, event):
        if len(self.labels) < 2:
            return
        points = [label.mapTo(self.host, QPoint(label.width() // 2, label.height() - 2)).x()
                  for label in self.labels]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        y = self.height() - 3
        edge = QColor(T.P["input_border"])
        painter.setPen(QPen(edge, 1))
        painter.drawLine(QPointF(points[0], y), QPointF(points[-1], y))
        if self.active:
            low = min(len(points) - 2, max(0, int(self.position)))
            x = points[low] + (points[low + 1] - points[low]) * (self.position - low)
            painter.setPen(QPen(QColor(T.C_BLUE), 2, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF(points[0], y), QPointF(x, y))
            painter.setBrush(QColor(T.C_BLUE))
            painter.drawEllipse(QPointF(x, y), 3, 3)
        painter.end()


class _FoldSnapshot(QWidget):
    """缓存展开面板，只裁切绘制快照，不逐帧重排真实控件。"""

    def __init__(self, parent, pixmap, geometry, extent=0):
        super().__init__(parent)
        self.pixmap = pixmap
        self.extent = max(0, int(extent))
        self.setGeometry(geometry)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.NoFocus)

    def set_extent(self, value):
        extent = max(0, min(self.height(), int(value)))
        if extent == self.extent:
            return
        self.extent = extent
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setClipRect(0, 0, self.width(), self.extent)
        painter.drawPixmap(0, 0, self.pixmap)
        painter.end()


class FoldController(QObject):
    """小型内容区域展开和收起，恢复自然高度，不留下布局约束。"""

    def __init__(self, panel, horizontal=False, snapshot=False, freeze_layout=False,
                 fade=True, duration=520, easing=QEasingCurve.InOutCubic):
        super().__init__(panel)
        self.panel = panel
        self.horizontal = horizontal
        self.freeze_layout = freeze_layout
        self.fade = bool(fade)
        self.frozen_layout = None
        self.snapshot_motion = bool(snapshot and not horizontal)
        self.natural_minimum = panel.minimumWidth() if horizontal else panel.minimumHeight()
        self.natural_maximum = panel.maximumWidth() if horizontal else panel.maximumHeight()
        self.opened = not panel.isHidden()
        self.content_fade = None
        self.snapshot_layer = None
        self.snapshot_host = None
        self._visibility_mutating = False
        self.reveal_extent = 1
        self.motion = QVariantAnimation(self)
        self.motion.setDuration(max(1, int(duration)))
        self.motion.setEasingCurve(easing)
        self.motion.valueChanged.connect(self._advance)
        self.motion.finished.connect(self.settle)
        panel.installEventFilter(self)

    def _layout(self):
        parent = self.panel.parentWidget()
        return parent.layout() if parent is not None else None

    def _snapshot_parent(self):
        ancestor = self.panel.parentWidget()
        while ancestor is not None:
            viewport = getattr(ancestor, "viewport", None)
            if callable(viewport):
                candidate = viewport()
                if candidate is not None and candidate.isAncestorOf(self.panel):
                    return candidate
            ancestor = ancestor.parentWidget()
        return self.panel.parentWidget()

    def _snapshot_geometry(self, host):
        if host is self.panel.parentWidget():
            return self.panel.geometry()
        return QRect(self.panel.mapTo(host, QPoint(0, 0)), self.panel.size())

    def _hide_panel_visual(self):
        effect = self.panel.graphicsEffect()
        if effect is None:
            effect = QGraphicsOpacityEffect(self.panel)
            self.panel.setGraphicsEffect(effect)
        if not isinstance(effect, QGraphicsOpacityEffect):
            self.snapshot_motion = False
            return False
        self.content_fade = effect
        effect.setOpacity(0.0)
        return True

    def _create_snapshot(self, extent):
        host = self._snapshot_parent()
        if host is None:
            return False
        pixmap = self.panel.grab()
        if pixmap.isNull():
            return False
        layer = _FoldSnapshot(
            host, pixmap, self._snapshot_geometry(host), extent)
        layer.installEventFilter(self)
        host.installEventFilter(self)
        self.snapshot_host = host
        self.snapshot_layer = layer
        return True

    def _set_open_snapshot(self, opened, current):
        panel = self.panel
        layout = self._layout()
        start = float(current) if current is not None else None

        if opened:
            self._visibility_mutating = True
            panel.show()
            if layout is not None:
                layout.activate()
            self._visibility_mutating = False
            if self.snapshot_layer is None:
                if not self._create_snapshot(0):
                    self.snapshot_motion = False
                    self.set_open(opened)
                    return
                start = 0.0 if start is None else start
            else:
                if not self._hide_panel_visual():
                    self.set_open(opened)
                    return
                self.snapshot_layer.setGeometry(
                    self._snapshot_geometry(self.snapshot_host))
                if start is None:
                    start = float(self.snapshot_layer.extent)
            if not self._hide_panel_visual():
                self.set_open(opened)
                return
            self.snapshot_layer.show()
            self.snapshot_layer.raise_()
            target = self.snapshot_layer.height()
        else:
            if self.snapshot_layer is None:
                if panel.isHidden():
                    self.settle()
                    return
                if not self._create_snapshot(panel.height()):
                    self.snapshot_motion = False
                    self.set_open(opened)
                    return
                start = float(self.snapshot_layer.extent) if start is None else start
            elif start is None:
                start = float(self.snapshot_layer.extent)
            self.snapshot_layer.raise_()
            self._visibility_mutating = True
            panel.hide()
            if layout is not None:
                layout.activate()
            self._visibility_mutating = False
            self.snapshot_layer.show()
            self.snapshot_layer.raise_()
            target = 0.0

        self.motion.setStartValue(max(0.0, float(start or 0.0)))
        self.motion.setEndValue(float(max(0.0, target)))
        self.motion.start()

    def set_open(self, opened):
        current = self.motion.currentValue() if self.motion.state() == QVariantAnimation.Running else None
        self.motion.stop()
        self.opened = bool(opened)
        parent = self.panel.parentWidget()
        if not animations_enabled() or (parent is not None and not parent.isVisible()):
            self.settle()
            return
        if self.snapshot_motion:
            effect = self.panel.graphicsEffect()
            if effect is None or isinstance(effect, QGraphicsOpacityEffect):
                self._set_open_snapshot(self.opened, current)
                return
            self.snapshot_motion = False
        start = (float(current) if current is not None else
                 0 if self.panel.isHidden() else (self.panel.width() if self.horizontal else self.panel.height()))
        setter = self.panel.setMaximumWidth if self.horizontal else self.panel.setMaximumHeight
        minimum = self.panel.setMinimumWidth if self.horizontal else self.panel.setMinimumHeight
        minimum(0)
        setter(16777215)
        hint = self.panel.sizeHint()
        target = max(self.natural_minimum, hint.width() if self.horizontal else hint.height()) if opened else 0
        self.reveal_extent=max(1,target if opened else start)
        if self.freeze_layout:
            # 只让外层框参与高度变化，内部表单保持完整尺寸并由父控件裁切。
            self.panel.show()
            parent_layout = self._layout()
            if parent_layout is not None:
                parent_layout.activate()
            self.frozen_layout = self.panel.layout()
            if self.frozen_layout is not None:
                self.frozen_layout.setEnabled(True)
                self.frozen_layout.activate()
                self.frozen_layout.setEnabled(False)
        if self.fade and not self.freeze_layout and self.panel.graphicsEffect() is None:
            self.content_fade=QGraphicsOpacityEffect(self.panel)
            self.panel.setGraphicsEffect(self.content_fade)
        setter(start)
        self.panel.show()
        self.motion.setStartValue(float(start))
        self.motion.setEndValue(float(max(0, target)))
        self.motion.start()

    def _advance(self, value):
        if not animations_enabled():
            self.settle()
            return
        if self.snapshot_motion and self.snapshot_layer is not None:
            self.snapshot_layer.set_extent(round(float(value)))
            return
        setter = self.panel.setMaximumWidth if self.horizontal else self.panel.setMaximumHeight
        setter(max(0, round(float(value))))
        if self.content_fade is not None:
            ratio=max(0,min(1,float(value)/self.reveal_extent))
            self.content_fade.setOpacity(.2+.8*ratio)

    def settle(self):
        self.motion.stop()
        if self.frozen_layout is not None:
            self.frozen_layout.setEnabled(True)
            self.frozen_layout = None
        if self.snapshot_layer is not None:
            layer = self.snapshot_layer
            host = self.snapshot_host
            self.snapshot_layer = None
            self.snapshot_host = None
            if host is not None:
                host.removeEventFilter(self)
            layer.removeEventFilter(self)
            layer.hide()
            layer.deleteLater()
        if self.content_fade is not None:
            if self.panel.graphicsEffect() is self.content_fade:
                self.panel.setGraphicsEffect(None)
            self.content_fade=None
        setter = self.panel.setMaximumWidth if self.horizontal else self.panel.setMaximumHeight
        setter(self.natural_maximum)
        minimum = self.panel.setMinimumWidth if self.horizontal else self.panel.setMinimumHeight
        minimum(self.natural_minimum)
        self.panel.setVisible(self.opened)

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Hide and
                self.motion.state() == QVariantAnimation.Running and
                not self._visibility_mutating):
            if watched is self.panel or watched is self.snapshot_host:
                self.motion.stop()
                self.settle()
        elif (event.type() == QEvent.Wheel and
              watched is self.snapshot_layer and
              self.motion.state() == QVariantAnimation.Running):
            self.motion.stop()
            self.settle()
        return False


class FoldClip(QWidget):
    """让内容保持自然尺寸，由外层高度裁切并带动卡片布局同步展开。"""

    def __init__(self, content=None, parent=None):
        super().__init__(parent)
        self._content = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        if content is not None:
            self.set_content(content)

    def set_content(self, content):
        if content is self._content:
            return
        if self._content is not None:
            self._content.hide()
            self._content.setParent(None)
        self._content = content
        if content is None:
            self.updateGeometry()
            return
        content.setParent(self)
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        content.show()
        self._sync_content_geometry()
        self.updateGeometry()

    def _natural_content_size(self, width=None):
        content = self._content
        if content is None:
            return QSize(0, 0)
        hint = content.sizeHint()
        min_hint = content.minimumSizeHint()
        target_width = max(0, int(width if width is not None else hint.width()))
        height = max(hint.height(), min_hint.height())
        if target_width > 0 and content.hasHeightForWidth():
            height = max(height, content.heightForWidth(target_width))
        return QSize(max(hint.width(), min_hint.width()), max(0, height))

    def sizeHint(self):
        return self._natural_content_size(self.width())

    def minimumSizeHint(self):
        content = self._content
        if content is None:
            return QSize(0, 0)
        return QSize(max(0, content.minimumSizeHint().width()), 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_content_geometry()

    def _sync_content_geometry(self):
        content = self._content
        if content is None:
            return
        size = self._natural_content_size(self.width())
        height = max(1, size.height())
        content.setMinimumHeight(height)
        content.setMaximumHeight(height)
        content.setGeometry(0, 0, max(0, self.width()), height)


class _CardFoldSnapshot(QWidget):
    """绘制卡片缓存图的裁切部分，不让父布局跟随动画逐帧重排。"""

    def __init__(self, parent, pixmap, geometry, extent):
        super().__init__(parent)
        self.pixmap = pixmap
        self.extent = max(0, int(extent))
        self.setGeometry(geometry)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)

    def set_extent(self, extent):
        extent = max(0, min(self.height(), int(extent)))
        if extent == self.extent:
            return
        self.extent = extent
        self.update()

    def paintEvent(self, event):
        if self.extent <= 0 or self.pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setClipRect(QRect(0, 0, self.width(), self.extent))
        painter.drawPixmap(self.rect(), self.pixmap, self.pixmap.rect())
        painter.end()


class CardFoldController(QObject):
    """用整卡快照完成折叠过渡，只在开合起止各重排一次。"""

    def __init__(self, panel, card, host, scroll_area=None, duration=220,
                 easing=QEasingCurve.OutCubic):
        super().__init__(panel)
        self.panel = panel
        self.card = card
        self.host = host
        self.scroll_area = scroll_area
        self.opened = not panel.isHidden()
        self.duration = max(1, int(duration))
        self.easing = easing
        self.layer = None
        self.pixmap = QPixmap()
        self.collapsed_height = 0
        self.expanded_height = 0
        self.motion = QVariantAnimation(self)
        self.motion.setDuration(self.duration)
        self.motion.setEasingCurve(self.easing)
        self.motion.valueChanged.connect(self._advance)
        self.motion.finished.connect(self._finish)
        self.host.installEventFilter(self)
        if scroll_area is not None:
            for bar in (scroll_area.verticalScrollBar(),
                        scroll_area.horizontalScrollBar()):
                bar.valueChanged.connect(self._sync_layer_geometry)

    def _activate_layouts(self):
        widget = self.card
        while widget is not None:
            layout = widget.layout()
            if layout is not None:
                layout.invalidate()
                layout.activate()
            widget = widget.parentWidget()

    def _capture_open_card(self):
        """短暂计算展开尺寸并抓取整卡快照，不处理界面事件。"""
        bars = (() if self.scroll_area is None else
                (self.scroll_area.verticalScrollBar(),
                 self.scroll_area.horizontalScrollBar()))
        scroll_values = tuple(bar.value() for bar in bars)
        self.panel.show()
        self._activate_layouts()
        expanded_height = self.card.height()
        pixmap = self.card.grab()
        self.panel.hide()
        self._activate_layouts()
        for bar, value in zip(bars, scroll_values):
            bar.setValue(value)
        self.host.update()
        return pixmap, expanded_height

    def _sync_layer_geometry(self, *_args):
        if self.layer is None:
            return
        point = self.card.mapTo(self.host, QPoint(0, 0))
        self.layer.setGeometry(
            point.x(), point.y(), max(1, self.card.width()),
            max(1, self.expanded_height))

    def _begin_snapshot(self, opened):
        if opened:
            self.collapsed_height = self.card.height()
            pixmap, self.expanded_height = self._capture_open_card()
            self.collapsed_height = self.card.height()
            start = self.collapsed_height
        else:
            pixmap = self.card.grab()
            self.expanded_height = self.card.height()
            self.panel.hide()
            self._activate_layouts()
            self.collapsed_height = self.card.height()
            start = self.expanded_height

        if pixmap.isNull() or self.expanded_height <= self.collapsed_height:
            self.panel.setVisible(opened)
            self._activate_layouts()
            self.opened = bool(opened)
            return False

        self.pixmap = pixmap
        point = self.card.mapTo(self.host, QPoint(0, 0))
        geometry = QRect(point.x(), point.y(), max(1, self.card.width()),
                         max(1, self.expanded_height))
        self.layer = _CardFoldSnapshot(
            self.host, pixmap, geometry, start)
        self.layer.show()
        self.layer.raise_()
        return True

    def set_open(self, opened):
        opened = bool(opened)
        if self.motion.state() == QVariantAnimation.Running:
            if opened == self.opened:
                return
            current = self.motion.currentValue()
            self.motion.stop()
            self.opened = opened
            target = self.expanded_height if opened else self.collapsed_height
            self.motion.setStartValue(float(current or 0))
            self.motion.setEndValue(float(target))
            self.motion.start()
            return

        if opened == self.opened:
            return
        self.opened = opened
        if (not animations_enabled() or not self.card.isVisible() or
                not self.host.isVisible()):
            self.panel.setVisible(opened)
            self._activate_layouts()
            self._remove_layer()
            return

        if not self._begin_snapshot(opened):
            self._remove_layer()
            return
        start = self.collapsed_height if opened else self.expanded_height
        target = self.expanded_height if opened else self.collapsed_height
        self.motion.setDuration(self.duration)
        self.motion.setEasingCurve(self.easing)
        self.motion.setStartValue(float(start))
        self.motion.setEndValue(float(target))
        self.motion.start()

    def _advance(self, value):
        if self.layer is not None:
            self.layer.set_extent(round(float(value)))

    def _remove_layer(self):
        layer = self.layer
        self.layer = None
        self.pixmap = QPixmap()
        if layer is not None:
            layer.hide()
            layer.deleteLater()

    def _finish(self):
        self.motion.stop()
        self.panel.setVisible(self.opened)
        self._activate_layouts()
        if self.opened and self.layer is not None:
            self.expanded_height = self.card.height()
            self._sync_layer_geometry()
            self.layer.set_extent(self.expanded_height)
        self._remove_layer()
        self.host.update()

    def eventFilter(self, watched, event):
        if watched is self.host and self.motion.state() == QVariantAnimation.Running:
            if event.type() in (QEvent.Resize, QEvent.Hide, QEvent.Wheel):
                self._finish()
        return False


def fold(panel, opened, horizontal=False, animate=True, snapshot=False, freeze_layout=False,
         fade=True, duration=520, easing=QEasingCurve.InOutCubic):
    controller = getattr(panel, "_polish_fold", None)
    if controller is None:
        controller = FoldController(
            panel, horizontal=horizontal, snapshot=snapshot, freeze_layout=freeze_layout,
            fade=fade, duration=duration, easing=easing)
        panel._polish_fold = controller
    elif snapshot:
        controller.snapshot_motion = not controller.horizontal
    controller.fade = bool(fade)
    controller.motion.setDuration(max(1, int(duration)))
    controller.motion.setEasingCurve(easing)
    if animate:controller.set_open(opened)
    else:
        controller.opened=bool(opened);controller.settle()


class ListScrollMotion(QObject):
    """目录定位平滑滚动；真实滚轮和拖动优先，立即打断补间。"""

    def __init__(self, listing):
        super().__init__(listing)
        self.listing = listing
        self.motion = QVariantAnimation(self)
        self.motion.setDuration(500)
        self.motion.setEasingCurve(QEasingCurve.OutCubic)
        self.motion.valueChanged.connect(self._advance)
        listing.viewport().installEventFilter(self)
        listing.verticalScrollBar().sliderPressed.connect(self.motion.stop)
        listing.installEventFilter(self)

    def locate(self, item):
        bar = self.listing.verticalScrollBar()
        self.motion.stop()
        rectangle = self.listing.visualItemRect(item)
        target = max(bar.minimum(), min(bar.maximum(), bar.value() + rectangle.center().y() - self.listing.viewport().height() // 2))
        if not self.listing.isVisible() or not animations_enabled():
            bar.setValue(target)
            return
        self.motion.setStartValue(bar.value())
        self.motion.setEndValue(target)
        self.motion.start()

    def _advance(self, value):
        if not animations_enabled():
            value = self.motion.endValue()
            self.motion.stop()
        self.listing.verticalScrollBar().setValue(round(float(value)))

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Wheel, QEvent.MouseButtonPress, QEvent.Hide):
            self.motion.stop()
        return False


def locate_item(listing, item):
    controller = getattr(listing, "_polish_scroll", None)
    if controller is None:
        controller = ListScrollMotion(listing)
        listing._polish_scroll = controller
    controller.locate(item)
