"""读取千代素材包提供的透明角色插画，不从预览截图裁切。"""
from PySide6.QtCore import Qt, QEvent, QRectF, QPointF, QPoint, QRect
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QPainterPath, QFont
from PySide6.QtWidgets import (QLabel, QSizePolicy, QGraphicsOpacityEffect,
                               QFrame, QWidget)

import theme as T


ASSETS = {
    "home": "home_banner.png",
    "timer": "timer_chiyo.png",
    "knowledge": "knowledge_chiyo.png",
    "settings": "settings_chiyo.png",
    "sidebar": "sidebar_chiyo.png",
}
DECOR = {
    "home": "home_decor_{mode}.png",
    "bank": "bank_decor_{mode}.png",
    "timer": "timer_decor_{mode}.png",
    "knowledge": "knowledge_decor_{mode}.png",
    "settings": "settings_decor_{mode}.png",
}

# 每页角色插画的最大显示尺寸；保持素材原比例，避免切掉人物或题卡。
MAX_SIZES = {
    "home": (560, 360),
    "timer": (720, 430),
    "knowledge": (560, 370),
    "settings": (285, 205),
}

_CACHE = {}


class ChiyoComicCell(QFrame):
    """带粗细墨线和边角网点的漫画分镜槽。"""

    def __init__(self, parent=None, object_name="ChiyoComicCell"):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setFrameShape(QFrame.NoFrame)
        self.setContentsMargins(14, 13, 14, 13)
        self.setStyleSheet(f"QFrame#{object_name}{{background:transparent;border:0px;}}")
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, event):
        if T.app_theme != "chiyo":
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        dark = T.mode == "dark"
        edge = QColor("#E7E0D8" if dark else "#27262A")
        inner = QColor("#78727A" if dark else "#A19A94")
        paper = QColor("#252730" if dark else "#FFFDF8")
        red = QColor("#F04A60")

        shape = QPainterPath()
        shape.moveTo(rect.left() + 9, rect.top() + 1)
        shape.lineTo(rect.right() - 15, rect.top() + 1)
        shape.lineTo(rect.right() - 2, rect.top() + 11)
        shape.lineTo(rect.right() - 2, rect.bottom() - 10)
        shape.lineTo(rect.right() - 11, rect.bottom() - 2)
        shape.lineTo(rect.left() + 10, rect.bottom() - 2)
        shape.lineTo(rect.left() + 2, rect.bottom() - 11)
        shape.lineTo(rect.left() + 2, rect.top() + 10)
        shape.closeSubpath()
        painter.fillPath(shape, paper)
        painter.setPen(QPen(edge, 3.0, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        painter.drawPath(shape)

        inset = QPainterPath()
        inset.addRect(rect.adjusted(7, 7, -7, -7))
        painter.setPen(QPen(inner, 1.0))
        painter.drawPath(inset)

        # 网点只留在右上和左下边角，人物与说明区保持干净。
        painter.save()
        painter.setClipPath(shape)
        painter.setPen(Qt.NoPen)
        for row in range(5):
            for col in range(8):
                dot = QColor(red)
                dot.setAlpha(max(18, 76 - row * 11 - col * 7))
                painter.setBrush(dot)
                painter.drawEllipse(QRectF(rect.right() - 34 + col * 3.2,
                                            rect.top() + 12 + row * 3.2,
                                            1.5, 1.5))
                if row < 4 and col < 5:
                    dot2 = QColor(red)
                    dot2.setAlpha(max(14, 52 - row * 8 - col * 6))
                    painter.setBrush(dot2)
                    painter.drawEllipse(QRectF(rect.left() + 12 + col * 3.2,
                                                rect.bottom() - 25 + row * 3.2,
                                                1.4, 1.4))
        painter.setPen(QPen(red, 2.0, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(rect.left() + 13, rect.top() + 5,
                         rect.left() + 42, rect.top() + 5)
        painter.restore()
        painter.end()


class ChiyoMarginNote(QWidget):
    """短句便签：用在页头留白处，文字不进入正文阅读区。"""

    def __init__(self, text, parent=None, width=194, height=72, anchor=None):
        super().__init__(parent)
        self._text = str(text)
        self._anchor = anchor
        self.setFixedSize(width, height)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        if parent is not None and anchor is not None:
            parent.installEventFilter(self)
            self._place_note()

    def _place_note(self):
        parent = self.parentWidget()
        if parent is not None and self._anchor == "bottom-left":
            self.move(12, max(12, parent.height() - self.height() - 12))

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Resize:
            self._place_note()
        return super().eventFilter(obj, event)

    def setText(self, text):
        self._text = str(text)
        self.update()

    def paintEvent(self, event):
        if T.app_theme != "chiyo":
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        dark = T.mode == "dark"
        edge = QColor("#EEE8DF" if dark else "#26252A")
        paper = QColor("#2A2D35" if dark else "#FFFDF7")
        red = QColor("#F04459")
        painter.save()
        painter.translate(3, 3)
        painter.rotate(-3.5)
        rect = QRectF(2, 5, self.width() - 10, self.height() - 14)
        back = QPainterPath()
        back.moveTo(rect.left() + 4, rect.top() + 1)
        back.lineTo(rect.right() - 2, rect.top() + 1)
        back.lineTo(rect.right(), rect.bottom() - 5)
        back.lineTo(rect.right() - 7, rect.bottom())
        back.lineTo(rect.left() + 1, rect.bottom() - 2)
        back.closeSubpath()
        shadow = QPainterPath(back)
        shadow.translate(2, 3)
        painter.fillPath(shadow, QColor(0, 0, 0, 28 if not dark else 70))
        painter.fillPath(back, paper)
        painter.setPen(QPen(edge, 1.5, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        painter.drawPath(back)

        dots = QColor(red)
        dots.setAlpha(95 if not dark else 135)
        painter.setPen(Qt.NoPen)
        painter.setBrush(dots)
        for row in range(4):
            for col in range(6):
                painter.drawEllipse(QPointF(rect.right() - 27 + col * 3.2,
                                            rect.top() + 8 + row * 3.2), 1.0, 1.0)

        text_color = QColor("#F2EDE5" if dark else "#24232A")
        painter.setPen(text_color)
        painter.setFont(QFont("KaiTi", 12, QFont.DemiBold))
        text_rect = rect.adjusted(12, 3, -13, -12)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap,
                         self._text)
        painter.setPen(QPen(red, 2.2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(rect.left() + 11, rect.bottom() - 8,
                         rect.left() + min(92, rect.width() * .58), rect.bottom() - 10)
        painter.setPen(QPen(red, 1.6))
        painter.setBrush(Qt.NoBrush)
        flower = QPointF(rect.right() - 15, rect.bottom() - 13)
        for index in range(5):
            painter.save()
            painter.translate(flower)
            painter.rotate(index * 72)
            painter.drawEllipse(QRectF(-2.0, -7.0, 4.0, 5.5))
            painter.restore()
        painter.drawEllipse(QRectF(flower.x() - 1.7, flower.y() - 1.7, 3.4, 3.4))
        painter.restore()
        painter.end()


class ChiyoBinderClip(QWidget):
    """用户提供的红色夹子，夹在详情分镜的右上外缘。"""

    def __init__(self, anchor, parent=None):
        super().__init__(parent)
        self._anchor = anchor
        self.setFixedSize(52, 46)
        pixmap = QPixmap(T.asset_path("chiyo_pack", "binder_clip.png"))
        # 原图的远处杂点透明度为 1/255；按有效透明度边界取景，文件保持原样。
        self._pixmap = pixmap.copy(QRect(524, 305, 498, 418)) if not pixmap.isNull() else pixmap
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        anchor.installEventFilter(self)
        if parent is not None:
            parent.installEventFilter(self)
            self._place()

    def _place(self):
        parent = self.parentWidget()
        if parent is not None:
            point = self._anchor.mapTo(parent, QPoint(
                self._anchor.width() - self.width() + 8, -30))
            self.move(point)
            self.raise_()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.Move, QEvent.Show):
            self._place()
        return super().eventFilter(obj, event)

    def paintEvent(self, event):
        if T.app_theme != "chiyo":
            return
        painter = QPainter(self)
        image = self._pixmap.scaled(self.size(), Qt.KeepAspectRatio,
                                     Qt.SmoothTransformation)
        painter.drawPixmap((self.width() - image.width()) // 2,
                           (self.height() - image.height()) // 2, image)
        painter.end()



class ChiyoSpeechBubble(QWidget):
    """用于教学插画的短对白，避开图形绘制与说明正文。"""

    def __init__(self, text, parent=None, width=166, height=78):
        super().__init__(parent)
        self._text = str(text)
        self.setFixedSize(width, height)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        if parent is not None:
            parent.installEventFilter(self)
            self._place()

    def _place(self):
        self.move(12, 11)

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Resize:
            self._place()
        return super().eventFilter(obj, event)

    def paintEvent(self, event):
        if T.app_theme != "chiyo":
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        dark = T.mode == "dark"
        edge = QColor("#EEE7DE" if dark else "#27262A")
        paper = QColor("#262930" if dark else "#FFFDF8")
        red = QColor("#F04459")
        bubble = QPainterPath()
        bubble.moveTo(10, 8)
        bubble.quadTo(5, 4, 14, 3)
        bubble.lineTo(self.width() - 10, 4)
        bubble.quadTo(self.width() - 2, 6, self.width() - 4, 16)
        bubble.lineTo(self.width() - 8, self.height() - 18)
        bubble.quadTo(self.width() - 18, self.height() - 7,
                      self.width() - 30, self.height() - 11)
        bubble.lineTo(self.width() - 43, self.height() - 2)
        bubble.lineTo(self.width() - 46, self.height() - 14)
        bubble.lineTo(12, self.height() - 13)
        bubble.quadTo(3, self.height() - 14, 5, self.height() - 25)
        bubble.closeSubpath()
        painter.fillPath(bubble, QColor(0, 0, 0, 35 if not dark else 80))
        bubble.translate(-2, -2)
        painter.fillPath(bubble, paper)
        painter.setPen(QPen(edge, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(bubble)
        painter.setPen(QColor("#F1ECE4" if dark else "#24232A"))
        painter.setFont(QFont("KaiTi", 12, QFont.DemiBold))
        painter.drawText(QRectF(17, 11, self.width() - 38, self.height() - 28),
                         Qt.AlignCenter | Qt.TextWordWrap, self._text)
        painter.setPen(QPen(red, 2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(23, self.height() - 18, self.width() - 57,
                         self.height() - 20)
        painter.end()


class ChiyoCornerDots(QWidget):
    """透明网点层，只在指定边角出现，不覆盖正文和控件。"""

    def __init__(self, parent, corners=("top-right", "bottom-left")):
        super().__init__(parent)
        self.corners = tuple(corners)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setGeometry(parent.rect())
        parent.installEventFilter(self)
        self.raise_()

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(obj.rect())
        return super().eventFilter(obj, event)

    def paintEvent(self, event):
        if T.app_theme != "chiyo":
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        red = QColor("#F04459")
        for corner in self.corners:
            for row in range(8):
                for col in range(10):
                    if corner == "top-right":
                        x, y = self.width() - 7 - col * 4.0, 7 + row * 4.0
                    elif corner == "top-left":
                        x, y = 7 + col * 4.0, 7 + row * 4.0
                    elif corner == "bottom-right":
                        x, y = self.width() - 7 - col * 4.0, self.height() - 7 - row * 4.0
                    else:
                        x, y = 7 + col * 4.0, self.height() - 7 - row * 4.0
                    dot = QColor(red)
                    dot.setAlpha(max(8, 66 - row * 5 - col * 4))
                    painter.setBrush(dot)
                    painter.drawEllipse(QPointF(x, y), 1.25, 1.25)
        painter.end()


def _asset_pixmap(name):
    if name not in _CACHE:
        _CACHE[name] = QPixmap(T.asset_path("chiyo_pack", ASSETS[name]))
    return _CACHE[name]


def _decoration_pixmap(page):
    mode = "dark" if T.mode == "dark" else "light"
    key = f"decor:{page}:{mode}"
    if key not in _CACHE:
        _CACHE[key] = QPixmap(T.asset_path(
            "chiyo_pack", DECOR[page].format(mode=mode)))
    return _CACHE[key]


class ChiyoDecoration(QLabel):
    """旧版整页稿纸素材的兼容占位层；不再把整幅素材铺在正文下。"""

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAlignment(Qt.AlignCenter)
        self._pixmap = QPixmap()
        self.setGraphicsEffect(None)
        if parent is not None:
            parent.installEventFilter(self)
        self.refresh_art()

    def refresh_art(self):
        # 这些素材包含完整桌面与纸张背景，不是可透明铺满页面的角落纹理。
        # 整幅显示会把灰底和黑边带到正文、开关和图形选择条上。
        self.clear()
        self.hide()

    def eventFilter(self, obj, ev):
        if obj is self.parentWidget() and ev.type() == QEvent.Resize:
            self.refresh_art()
        return super().eventFilter(obj, ev)


class ChiyoArtwork(QLabel):
    """受限于专用插画槽的透明角色素材层，鼠标事件穿透到实际控件。"""

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page
        from flight_motion import CompanionGesture
        self.gesture = CompanionGesture(self)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAlignment(Qt.AlignRight | Qt.AlignBottom
                          if page == "knowledge" else Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._pixmap = _asset_pixmap(page)
        self.refresh_art()
        if parent is not None:
            parent.installEventFilter(self)
            self._place_art()

    def _place_art(self):
        if self._pixmap.isNull():
            return
        maximum = MAX_SIZES.get(self.page, (300, 200))
        parent = self.parentWidget()
        if parent is None:
            self.setPixmap(self._pixmap.scaled(*maximum, Qt.KeepAspectRatio,
                                               Qt.SmoothTransformation))
            return
        content_rect = parent.contentsRect()
        self.setGeometry(content_rect)
        target = content_rect.size().boundedTo(self._pixmap.size())
        art = self._pixmap.scaled(target, Qt.KeepAspectRatio,
                                  Qt.SmoothTransformation)
        # 插画填在槽位内部并保持原比例；不给透明画布施加整页绝对定位。
        self.setPixmap(art.scaled(*maximum, Qt.KeepAspectRatio,
                                  Qt.SmoothTransformation)
                       if art.width() > maximum[0] or art.height() > maximum[1]
                       else art)

    def hideEvent(self, event):
        self.gesture.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        if self.gesture.progress == 0.0 or self.pixmap().isNull():
            super().paintEvent(event)
            return
        art = self.pixmap()
        size = art.deviceIndependentSize()
        area = QRectF((self.width() - size.width()) / 2,
                      (self.height() - size.height()) / 2,
                      size.width(), size.height())
        if self.page == "knowledge":
            area.moveBottomRight(QPointF(self.width(), self.height()))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        self.gesture.apply(painter, area)
        painter.drawPixmap(area, art, QRectF(art.rect()))
        painter.end()

    def eventFilter(self, obj, ev):
        if obj is self.parentWidget() and ev.type() == QEvent.Resize:
            self._place_art()
        return super().eventFilter(obj, ev)

    def refresh_art(self):
        self._pixmap = _asset_pixmap(self.page)
        if self._pixmap.isNull():
            self.clear()
            self.hide()
            return
        self._place_art()
        self.setVisible(T.app_theme == "chiyo")


def sidebar_art_pixmap(width, height):
    """读取人物侧栏原始透明插画，按容器比例缩放，不做截图裁切。"""
    pixmap = _asset_pixmap("sidebar")
    if pixmap.isNull():
        return QPixmap()
    return pixmap.scaled(width, height, Qt.KeepAspectRatio,
                         Qt.SmoothTransformation)
