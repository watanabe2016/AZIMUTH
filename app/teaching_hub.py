"""以课程、工具和图形三个入口组织教学，复用既有数学与播放逻辑。"""
import math
from PySide6.QtCore import Qt, QPointF, QRectF, QVariantAnimation, QTimer, Signal, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QPainterPath, QLinearGradient
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QWidget, QPushButton, QLabel, QVBoxLayout,
    QHBoxLayout, QScrollArea, QFrame, QStackedWidget, QGraphicsOpacityEffect)
import theme as T
from ui_kit import animations_enabled, card_in, prepare_card_in, cancel_card_in
from workbench_ui import SegmentRail
from teaching_scene_catalog import CHAPTERS, SCENES


def font(size, bold=False):
    result = QFont(T.pick_font())
    result.setPixelSize(size)
    result.setWeight(QFont.Bold if bold else QFont.Normal)
    return result


class TeachingEntryCard(QPushButton):
    """整张卡均可点击；反馈只改变绘制，不挤压相邻布局。"""
    def __init__(self, key, title, index, summary, topics, motif, parent=None):
        super().__init__(parent)
        self.key, self.title, self.index = key, title, index
        self.summary, self.topics, self.motif = summary, topics, motif
        self._icon_renderers = {}
        for icon_name in ('book-open', 'settings', 'wrench', 'circle', 'triangle',
                          'square', 'question-mark', 'shapes', 'compass', 'wind',
                          'monitor', 'angle', 'sigma'):
            renderer = QSvgRenderer(T.asset_path('teaching_icons', icon_name + '.svg'))
            if renderer.isValid():
                self._icon_renderers[icon_name] = renderer
        self.hover = 0.0
        self._intro_hidden = False
        self.motion = QVariantAnimation(self)
        self.motion.setDuration(280)
        from PySide6.QtCore import QEasingCurve
        self.motion.setEasingCurve(QEasingCurve.OutCubic)
        self.motion.valueChanged.connect(self._hover_frame)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(title + '，' + summary + '，点击进入')
        self.setMinimumSize(172, 390)
        self.setStyleSheet('QPushButton{background:transparent;border:0;padding:0;}')

    def _hover_frame(self, value):
        self.hover = float(value)
        self.update()

    def _feedback(self, target):
        self.motion.stop()
        if not animations_enabled():
            self.hover = target
            self.update()
            return
        self.motion.setStartValue(self.hover)
        self.motion.setEndValue(target)
        self.motion.start()

    def enterEvent(self, event):
        self._feedback(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._feedback(0.0)
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self._feedback(1.0)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._feedback(0.0)
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self.motion.stop()
        self.hover = 0.0
        super().hideEvent(event)

    def paintEvent(self, event):
        if self._intro_hidden:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        colors = ('#ad415f', '#745093', '#b14b2e', '#287c77', '#385e97', '#8a663c')
        color = QColor(colors[(self.index - 1) % len(colors)])
        if T.app_theme == 'chiyo':
            color = QColor(('#b93c55', '#983c59', '#a55539', '#7b5668', '#945b49', '#82505d')[(self.index-1)%6])
        fill = color.lighter(round(100 + self.hover * 8))
        inset = 12.0 if self.isDown() else 10.0
        rect = QRectF(self.rect()).adjusted(inset, inset + 4 - self.hover * 4, -inset, -inset)
        gradient=QLinearGradient(rect.topLeft(),rect.bottomRight())
        gradient.setColorAt(0,fill.lighter(118));gradient.setColorAt(.45,fill)
        gradient.setColorAt(1,fill.darker(135))
        painter.setPen(Qt.NoPen)
        if T.mode == 'light':
            shadow = QColor(17, 34, 61)
            painter.save()
            painter.setClipRect(QRectF(rect.left() - 18, rect.top() - 8,
                                       rect.width() + 36, rect.height() + 28))
            for spread, alpha in ((22, 1), (17, 2), (12, 3), (7, 4), (3, 5)):
                layer = QColor(shadow)
                layer.setAlpha(round(alpha * (0.7 + self.hover * 0.22)))
                painter.setBrush(layer)
                painter.drawRoundedRect(
                    rect.adjusted(-spread, -spread, spread, spread).translated(0, 3),
                    14 + spread, 14 + spread)
            painter.restore()
        painter.setBrush(gradient)
        edge = QColor(255, 255, 255, 18 + round(self.hover * 8))
        painter.setPen(QPen(edge, 1))
        painter.drawRoundedRect(rect, 14, 14)
        if T.mode == 'dark':
            painter.save()
            glow = QColor(T.P['accent_fg'])
            glow.setAlpha(8 + round(self.hover * 3))
            wash = QLinearGradient(0, rect.bottom() - rect.height() * .24,
                                  0, rect.bottom())
            wash.setColorAt(0, QColor(glow.red(), glow.green(), glow.blue(), 0))
            wash.setColorAt(1, glow)
            painter.setClipRect(QRectF(rect.left(),
                                       rect.bottom() - rect.height() * .22,
                                       rect.width(), rect.height() * .22))
            painter.setBrush(wash)
            painter.drawRoundedRect(rect, 14, 14)
            painter.restore()
            painter.save()
            painter.setClipRect(QRectF(rect.left() - 18, rect.bottom() - 1,
                                       rect.width() + 36,
                                       max(1, self.height() - rect.bottom())))
            for spread, alpha in ((20, 2), (14, 3), (9, 4), (4, 5)):
                layer = QColor(T.P['accent_fg'])
                layer.setAlpha(round(alpha * (0.75 + self.hover * 0.2)))
                painter.setBrush(layer)
                painter.drawRoundedRect(
                    rect.adjusted(-spread, -spread, spread, spread).translated(0, 4),
                    14 + spread, 14 + spread)
            painter.restore()
        entry = self.key.startswith('entry:')
        scale = rect.width() / (586 if entry else 462)
        pad = 30 * scale
        left, width = rect.left() + pad, rect.width() - 2 * pad
        top, height = rect.top(), rect.height()
        painter.save()
        painter.setClipRect(rect)
        painter.setPen(QColor('#ffffff'))
        painter.setFont(font(max(12, round((20 if entry else 26) * scale)), True))
        painter.drawText(QRectF(left, top + height * .035, width, height * .06), Qt.AlignLeft,
            f'{self.index:02d}  /  ' + ('教学入口' if entry else '工具' if self.key.startswith('tool:') else '课程'))
        painter.setFont(font(max(22, round((66 if entry else 56) * scale)), True))
        painter.drawText(QRectF(left, top + height * .10, width, height * .12),
                         Qt.AlignLeft | Qt.TextWordWrap, self.title)
        painter.setFont(font(max(12, round((24 if entry else 26) * scale))))
        painter.drawText(QRectF(left, top + height * (.225 if entry else .205), width, height * .075),
                         Qt.AlignLeft | Qt.TextWordWrap, self.summary)
        size = rect.width() * .67 if entry else min(rect.width() * .72, height * .31)
        motif_rect = QRectF(rect.right() - size - (0 if entry else pad * .5),
                           top + height * (.43 if entry else .57), size, size)
        from teaching_artwork import draw_artwork
        draw_artwork(painter, self.motif, motif_rect, entry=entry,
                     angle_tool=self.key == 'tool:angle')
        painter.setPen(QColor('#fff5f0'))
        painter.setFont(font(max(12, round((24 if entry else 26) * scale))))
        for index, text in enumerate(self.topics[:3]):
            clipped = painter.fontMetrics().elidedText('· ' + text, Qt.ElideRight, round(width))
            painter.drawText(QRectF(left, top + height * (.41 if entry else .305) + index * 48 * scale,
                                   width, 40 * scale), Qt.AlignLeft, clipped)
        bottom = rect.bottom() - height * .065
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
        painter.drawLine(QPointF(left, bottom - height * .035), QPointF(left + width, bottom - height * .035))
        painter.setPen(QColor('#ffffff'))
        painter.setFont(font(max(13, round(26 * scale)), True))
        painter.drawText(QRectF(left, bottom, width - 22, height * .045), Qt.AlignLeft,
            '打开' + self.title if entry else '打开工具' if self.key.startswith('tool:') else '进入课程')
        x = rect.right() - pad + self.hover * 4
        y = bottom + 13 * scale
        painter.setPen(QPen(QColor('#ffffff'), max(1.8, 3 * scale), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawLine(QPointF(x - 20 * scale, y), QPointF(x, y))
        painter.drawLine(QPointF(x - 8 * scale, y - 8 * scale), QPointF(x, y))
        painter.drawLine(QPointF(x - 8 * scale, y + 8 * scale), QPointF(x, y))
        painter.restore()
        painter.end()

    def _draw_motif(self, painter, rect):
        painter.save()
        if self.key.startswith('entry:') and self._draw_entry_icons(painter, rect):
            painter.restore()
            return
        if self._draw_card_icons(painter, rect):
            painter.restore()
            return
        alpha=66 if self.key.startswith('entry:') else 48
        alpha+=round(self.hover*10)
        stroke=max(2.2,min(5.2,rect.width()*.018))
        painter.setPen(QPen(QColor(255,255,255,alpha),stroke,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        center = rect.center()
        if self.motif in ('book','tools','shapes'):
            left,right=rect.left()+rect.width()*.08,rect.right()-rect.width()*.05
            top,bottom=rect.top()+rect.height()*.08,rect.bottom()-rect.height()*.08
            if self.motif=='book':
                spine=center.x()
                left_page=QPainterPath(QPointF(spine,top+rect.height()*.10))
                left_page.cubicTo(QPointF(spine-rect.width()*.19,top),QPointF(left,top+rect.height()*.05),QPointF(left+rect.width()*.04,top+rect.height()*.20))
                left_page.lineTo(left+rect.width()*.08,bottom-rect.height()*.15)
                left_page.cubicTo(QPointF(left+rect.width()*.24,bottom-rect.height()*.22),QPointF(spine-rect.width()*.08,bottom-rect.height()*.15),QPointF(spine,bottom))
                right_page=QPainterPath(QPointF(spine,top+rect.height()*.10))
                right_page.cubicTo(QPointF(spine+rect.width()*.19,top),QPointF(right,top+rect.height()*.05),QPointF(right-rect.width()*.04,top+rect.height()*.20))
                right_page.lineTo(right-rect.width()*.08,bottom-rect.height()*.15)
                right_page.cubicTo(QPointF(right-rect.width()*.24,bottom-rect.height()*.22),QPointF(spine+rect.width()*.08,bottom-rect.height()*.15),QPointF(spine,bottom))
                painter.drawPath(left_page);painter.drawPath(right_page)
                painter.drawLine(QPointF(spine,top+rect.height()*.10),QPointF(spine,bottom))
            elif self.motif=='tools':
                radius=min(rect.width(),rect.height())*.31
                gear=QPainterPath()
                for i in range(48):
                    angle=-math.pi/2+i*math.pi/24
                    tooth=(i%4 in (0,1))
                    r=radius*(1.16 if tooth else .98)
                    point=center+QPointF(math.cos(angle)*r,math.sin(angle)*r)
                    if i==0:gear.moveTo(point)
                    else:gear.lineTo(point)
                gear.closeSubpath();painter.drawPath(gear)
                painter.drawEllipse(center,radius*.36,radius*.36)
                wrench=QPainterPath(center+QPointF(radius*.08,-radius*.05))
                wrench.lineTo(center+QPointF(radius*.62,-radius*.60))
                wrench.moveTo(center+QPointF(radius*.49,-radius*.77))
                wrench.lineTo(center+QPointF(radius*.86,-radius*.82))
                wrench.lineTo(center+QPointF(radius*.78,-radius*.45))
                painter.drawPath(wrench)
            else:
                radius=min(rect.width(),rect.height())*.27
                circle_center=QPointF(center.x()+radius*.52,center.y()-radius*.46)
                painter.drawEllipse(circle_center,radius,radius)
                triangle=QPainterPath(QPointF(left+rect.width()*.04,bottom))
                triangle.lineTo(center.x()-radius*.10,center.y()+radius*.35)
                triangle.lineTo(center.x()+radius*1.62,bottom)
                triangle.closeSubpath();painter.drawPath(triangle)
                square=QRectF(right-radius*1.75,bottom-radius*1.72,radius*1.5,radius*1.5)
                painter.drawRoundedRect(square,stroke*1.4,stroke*1.4)
        elif self.motif in ('compass', 'wind', 'angle'):
            r = min(rect.width(),rect.height())*.40
            painter.drawEllipse(center, r, r)
            for i in range(8):
                a = i * math.pi / 4
                painter.drawLine(center + QPointF(math.sin(a)*(r-5), math.cos(a)*(r-5)),
                                 center + QPointF(math.sin(a)*r, math.cos(a)*r))
            painter.drawLine(center, center+QPointF(r*.55, -r*.72-self.hover*4))
            if self.motif == 'wind':
                for dy in (-r*.38, 0, r*.38):
                    painter.drawLine(QPointF(rect.left(), center.y()+dy), QPointF(center.x()-r*.32, center.y()+dy))
            if self.motif == 'angle':
                painter.drawLine(center, center+QPointF(r*.72,r*.58))
                arc=QRectF(center.x()-r*.60,center.y()-r*.60,r*1.2,r*1.2)
                painter.drawArc(arc,-35*16,90*16)
        elif self.motif == 'settings':
            for i in range(3):
                y = rect.top()+rect.height()*(.28+i*.23)
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                painter.setBrush(QColor('#ffffff'))
                painter.drawEllipse(QPointF(rect.left()+rect.width()*(.3+.2*(i%2)), y), stroke*1.25, stroke*1.25)
        elif self.motif == 'system':
            top_box=QRectF(center.x()-rect.width()*.15,rect.top()+rect.height()*.08,rect.width()*.30,rect.height()*.23)
            left_box=QRectF(rect.left()+rect.width()*.04,rect.bottom()-rect.height()*.30,rect.width()*.34,rect.height()*.24)
            right_box=QRectF(rect.right()-rect.width()*.38,rect.bottom()-rect.height()*.30,rect.width()*.34,rect.height()*.24)
            painter.drawLine(QPointF(top_box.center().x(),top_box.bottom()),QPointF(left_box.center().x(),left_box.top()))
            painter.drawLine(QPointF(top_box.center().x(),top_box.bottom()),QPointF(right_box.center().x(),right_box.top()))
            for box in (top_box,left_box,right_box):painter.drawRoundedRect(box,stroke*1.8,stroke*1.8)
        else:
            points = [QPointF(rect.left()+rect.width()*.07,rect.bottom()-rect.height()*.10),
                      QPointF(center.x(),rect.top()+rect.height()*.08),
                      QPointF(rect.right()-rect.width()*.07,rect.bottom()-rect.height()*.10)]
            path=QPainterPath(points[0])
            for point in points[1:]:path.lineTo(point)
            if self.motif != 'avoidance':path.closeSubpath()
            painter.drawPath(path)
            for point in points:
                painter.setBrush(QColor('#ffffff'))
                painter.drawEllipse(point,stroke*1.25,stroke*1.25)
        painter.restore()

    def _draw_card_icons(self, painter, rect):
        """按卡片主题绘制统一的低对比 Tabler 线稿水印。"""
        def render(name, target):
            icon = self._icon_renderers.get(name)
            if icon is not None:
                icon.render(painter, target)

        painter.save()
        painter.setOpacity(.15 + self.hover * .018)
        size = min(rect.width() * .92, rect.height() * 1.12)
        target = QRectF(rect.right() - size, rect.center().y() - size * .52,
                        size, size)
        if self.motif in ('question', 'shapes', 'compass', 'angle', 'sigma', 'book'):
            names = {'question': 'question-mark', 'shapes': 'shapes',
                     'compass': 'compass', 'angle': 'angle',
                     'sigma': 'sigma', 'book': 'book-open'}
            render(names[self.motif], target)
        elif self.motif == 'wind':
            compass_size = min(rect.height() * .98, rect.width() * .62)
            compass = QRectF(rect.right() - compass_size,
                             rect.center().y() - compass_size * .50,
                             compass_size, compass_size)
            wind = QRectF(rect.left() - rect.width() * .02,
                          rect.center().y() - compass_size * .34,
                          compass_size * .72, compass_size * .72)
            render('wind', wind)
            render('compass', compass)
        elif self.motif == 'avoidance':
            render('monitor', target)
        else:
            painter.restore()
            return False
        painter.restore()
        return True

    def _draw_entry_icons(self, painter, rect):
        """用统一的 SVG 线性图标绘制大卡片的低对比水印。"""
        def render(name, target):
            icon = self._icon_renderers.get(name)
            if icon is not None:
                icon.render(painter, target)

        painter.save()
        painter.setOpacity(.15 + self.hover * .018)
        if self.motif == 'book':
            size = min(rect.width() * .98, rect.height() * 1.12)
            target = QRectF(rect.right() - size, rect.center().y() - size / 2,
                            size, size)
            render('book-open', target)
        elif self.motif == 'tools':
            size = min(rect.height() * .96, rect.width() * .77)
            gear = QRectF(rect.right() - size * .94,
                          rect.center().y() - size * .53, size, size)
            wrench_size = size * .78
            wrench = QRectF(gear.center().x() - wrench_size * .35,
                            gear.center().y() - wrench_size * .10,
                            wrench_size, wrench_size)
            render('settings', gear)
            render('wrench', wrench)
        elif self.motif == 'shapes':
            unit = min(rect.height() * .63, rect.width() * .43)
            circle = QRectF(rect.left() + rect.width() * .44,
                            rect.top() + rect.height() * .03, unit, unit)
            triangle = QRectF(rect.left() + rect.width() * .02,
                              rect.top() + rect.height() * .40,
                              unit * .94, unit * .94)
            square = QRectF(rect.right() - unit * .96,
                            rect.top() + rect.height() * .42,
                            unit * .90, unit * .90)
            render('circle', circle)
            render('triangle', triangle)
            render('square', square)
        else:
            painter.restore()
            return False
        painter.restore()
        return True


class CardEntrance(QWidget):
    """只移动卡片快照；真实按钮保持可点击，结束即释放快照。"""
    def __init__(self, row, frames):
        super().__init__(row)
        self.row = row
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(row.rect())
        self.frames = frames
        self.clock = 0.0
        self.motion = QVariantAnimation(self)
        self.motion.setDuration(720 + 45 * (len(row.cards) - 1))
        self.motion.setStartValue(0.0)
        self.motion.setEndValue(float(self.motion.duration()))
        self.motion.valueChanged.connect(self._frame)
        self.motion.finished.connect(self.finish)
        self.show()
        self.raise_()
        self.motion.start()

    def _frame(self, value):
        if not animations_enabled():
            self.finish()
            return
        self.clock = float(value)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        for index, (card, home, pixmap, _) in enumerate(self.frames):
            t = max(0.0, min(1.0, (self.clock - index * 45) / 720))
            eased = 1 - (1 - t) ** 5
            painter.setOpacity(min(1.0, t * 4))
            distance = max(96, self.width() - home.x() + 20)
            painter.drawPixmap(round(home.x() + distance * (1 - eased)),
                               home.y(), pixmap)
        painter.end()

    def finish(self):
        self.motion.stop()
        for card, _, _, updates_enabled in self.frames:
            card._intro_hidden = False
            card.update()
        self.frames.clear()
        if self.row.entrance is self:
            self.row.entrance = None
        self.hide()
        self.deleteLater()


class TeachingCardRow(QWidget):
    activated = Signal(str)

    def __init__(self, entries, parent=None, card_width=330):
        super().__init__(parent)
        self.entrance = None
        self._prepared_frames = None
        self._warmup_frames = None
        self._warmup_theme = None
        self._intro_cancelled = False
        self.card_width = card_width
        self.cards = []
        for index, entry in enumerate(entries, 1):
            card = TeachingEntryCard(*entry[:2], index, *entry[2:], self)
            card.clicked.connect(
                lambda checked=False, c=card: self._activate(c.key))
            self.cards.append(card)
        self.setMinimumWidth(
            len(entries) * (card_width or 220) + (len(entries) - 1) * 18)
        self.setMinimumHeight(408)

    def _activate(self, key):
        self.cancel_intro()
        self.activated.emit(key)

    def warm_up(self):
        """在启动遮罩期间缓存卡片图像，首进页面时不再同步抓图。"""
        self.invalidate_warmup()
        if not animations_enabled():
            return False
        parent = self.parentWidget()
        layout = parent.layout() if parent is not None else None
        if layout is not None:
            layout.activate()
        frames = []
        for card in self.cards:
            card.ensurePolished()
            pixmap = card.grab()
            if pixmap.isNull():
                return False
            frames.append((card, card.pos(), card.size(), pixmap))
        self._warmup_frames = frames
        self._warmup_theme = (T.app_theme, T.mode)
        return True

    def invalidate_warmup(self):
        """尺寸或主题变化后丢弃过期卡片缓存。"""
        self._warmup_frames = None
        self._warmup_theme = None

    def prepare_intro(self):
        """在页面切换前隐藏真实控件，避免先闪现终点。"""
        self.cancel_intro()
        self._intro_cancelled = False
        if not animations_enabled():
            self.invalidate_warmup()
            return False
        parent = self.parentWidget()
        layout = parent.layout() if parent is not None else None
        if layout is not None:
            layout.activate()
        cached = self._warmup_frames
        theme = self._warmup_theme
        self.invalidate_warmup()
        frames = []
        if (cached and theme == (T.app_theme, T.mode)
                and all(card.pos() == home and card.size() == size
                        for card, home, size, _ in cached)):
            frames = [(card, home, pixmap, card.updatesEnabled())
                      for card, home, _, pixmap in cached]
        else:
            for card in self.cards:
                card.ensurePolished()
                pixmap = card.grab()
                if pixmap.isNull():
                    return False
                frames.append((card, card.pos(), pixmap, card.updatesEnabled()))
        for card, _, _, _ in frames:
            card._intro_hidden = True
            card.update()
        self._prepared_frames = frames
        return True

    def play_intro(self):
        if self._intro_cancelled and self._prepared_frames is None:
            return
        if self.entrance is not None:
            self.entrance.finish()
        if not animations_enabled() or not self.isVisible():
            self.cancel_intro()
            return
        if self._prepared_frames is None:
            self.prepare_intro()
        frames = self._prepared_frames
        if not frames:
            return
        self._prepared_frames = None
        self.entrance = CardEntrance(self, frames)

    def cancel_intro(self):
        self._intro_cancelled = True
        if self.entrance is not None:
            self.entrance.finish()
        frames = self._prepared_frames
        self._prepared_frames = None
        if frames:
            for card, _, _, updates_enabled in frames:
                card._intro_hidden = False
                card.update()

    def resizeEvent(self, event):
        self.invalidate_warmup()
        was_prepared = self._prepared_frames is not None
        self.cancel_intro()
        super().resizeEvent(event)
        width = self.card_width or (
            self.width() - 18 * (len(self.cards) - 1)) / len(self.cards)
        for index, card in enumerate(self.cards):
            card.setGeometry(
                round(index * (width + 18)), 0, round(width),
                max(390, self.height() - 8))
        if was_prepared:
            self.prepare_intro()

    def hideEvent(self, event):
        self.cancel_intro()
        super().hideEvent(event)


class HorizontalCardScroll(QScrollArea):
    """滚轮累积目标，连续滚动不中断手感；减少动画时立即定位。"""
    def __init__(self):
        super().__init__()
        self.target=0
        self.motion=QVariantAnimation(self)
        self.motion.setDuration(420);self.motion.setEasingCurve(QEasingCurve.OutQuint)
        self.motion.valueChanged.connect(lambda value:self.horizontalScrollBar().setValue(round(value)))
        self.horizontalScrollBar().sliderPressed.connect(self.motion.stop)

    def wheelEvent(self,event):
        row = self.widget()
        if isinstance(row, TeachingCardRow):
            row.cancel_intro()
        bar=self.horizontalScrollBar()
        if not bar.maximum():super().wheelEvent(event);return
        delta=event.pixelDelta()
        amount=delta.x() or delta.y()
        if not amount:amount=(event.angleDelta().x() or event.angleDelta().y())*1.8
        if self.motion.state()!=QVariantAnimation.Running:self.target=bar.value()
        self.target=max(0,min(bar.maximum(),self.target-amount))
        current=float(bar.value())
        self.motion.stop()
        if animations_enabled():
            self.motion.blockSignals(True)
            self.motion.setStartValue(current);self.motion.setEndValue(float(self.target));self.motion.setCurrentTime(0)
            self.motion.blockSignals(False);self.motion.start()
        else:bar.setValue(round(self.target))
        event.accept()

    def hideEvent(self,event):
        self.motion.stop();super().hideEvent(event)


class TeachingHub(QWidget):
    chapterSelected=Signal(str)
    toolSelected=Signal(str)
    def __init__(self,knowledge,parent=None):
        super().__init__(parent)
        self.knowledge=knowledge
        self.setObjectName('TeachingHub')
        shell=QVBoxLayout(self);shell.setContentsMargins(0,0,0,0);shell.setSpacing(0)
        self.title=QLabel('先看懂，\n再掌握航线。');self.title.setWordWrap(True)
        self.subtitle=QLabel('六章课程循序学习，四个工具动手验证，八大图形看清连接顺序。');self.subtitle.setWordWrap(True)
        from anna_art import AnnaHeaderArt
        from chiyo_art import ChiyoMarginNote
        from ui_kit import IconButton
        self.banner=AnnaHeaderArt('地面站教学','先看懂，再掌握航线。',page_key='teaching')
        shell.addWidget(self.banner)
        content=QWidget();outer=QVBoxLayout(content);self.content_layout=outer
        outer.setContentsMargins(30,12,30,18);outer.setSpacing(12)
        shell.addWidget(content,1)
        head=QHBoxLayout();head.addWidget(self.title,1)
        self.chiyo_note=ChiyoMarginNote('先看懂，\n再掌握航线！',width=218,height=80)
        head.addWidget(self.chiyo_note);outer.addLayout(head);outer.addWidget(self.subtitle)
        self.section_navigation=QWidget();navigation=QHBoxLayout(self.section_navigation);navigation.setContentsMargins(0,0,0,0)
        self.section_back=IconButton('',icon_name='arrow_left');self.section_back.setFixedSize(44,44)
        self.section_back.setAccessibleName('返回教学菜单');self.section_back.setToolTip('返回教学菜单')
        self.section_back.clicked.connect(self.show_menu)
        self.section_title=QLabel();navigation.addWidget(self.section_back);navigation.addWidget(self.section_title,1)
        outer.addWidget(self.section_navigation)
        self.rail=SegmentRail((('courses','01  课程','book-open'),('tools','02  工具','sliders-horizontal'),('shapes','03  图形展示','triangle')))
        self.rail.changed.connect(self.select_section);self.rail.hide()
        self.stack=QStackedWidget();outer.addWidget(self.stack,1)
        motifs=('question','shapes','compass','angle','sigma','book')
        course_entries=[]
        for i,(key,title) in enumerate(CHAPTERS):
            scenes=[scene for scene in SCENES if scene.chapter==key]
            course_entries.append((key,title,f'{len(scenes)} 节 · 动态讲解',[s.title for s in scenes[:3]],motifs[i]))
        self.course_row,self.course_page=self._card_page('从基础到完整任务，按章节开始。',course_entries)
        self.course_row.activated.connect(self.chapterSelected)
        tools=(('tool:heading','航向','方向从这里开始',('拖动罗盘','顺逆时针加减角','正向与反向'),'compass'),
               ('tool:wind','风向','区分来向与吹向',('顺风与逆风','左右侧风','航向与风向关系'),'wind'),
               ('tool:avoidance','避让点','看清相对位置',('起飞点与过渡点','方向与距离','位移与实际航程'),'avoidance'),
               ('tool:angle','夹角','两条射线，一步步求解',('按角名识别顶点','已知方向与夹角','未知方向与反向'),'angle'))
        self.tool_row,self.tool_page=self._card_page('选择一个考点，拖动图形或调整数值验证。',tools)
        self.tool_row.activated.connect(lambda key:self.toolSelected.emit(key.split(':',1)[1]))
        self.graph_page=QWidget();graph_layout=QVBoxLayout(self.graph_page);graph_layout.setContentsMargins(0,0,0,0);graph_layout.setSpacing(10)
        graph_layout.addWidget(QLabel('选择图形即播放；再次点击当前图形即可重播。'))
        nav_scroll=QScrollArea();nav_scroll.setWidgetResizable(True);nav_scroll.setFrameShape(QFrame.NoFrame)
        nav_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        nav_scroll.setFixedHeight(86)
        knowledge.shape_nav.setMinimumWidth(1200)
        nav_scroll.setWidget(knowledge.shape_nav);graph_layout.addWidget(nav_scroll)
        graph_scroll=QScrollArea();graph_scroll.setWidgetResizable(True);graph_scroll.setFrameShape(QFrame.NoFrame)
        graph_scroll.setWidget(knowledge.feature);graph_layout.addWidget(graph_scroll,1)
        self.stack.addWidget(self.graph_page)
        menu_entries=(('entry:courses','课程','六个章节 · 50 节动态课程',('从系统构成开始','跟随图形逐步理解','进入章节选择课时'),'book'),
                      ('entry:tools','工具','四个考点 · 动手验证',('航向与风向','避让点的相对位置','方向与夹角推导'),'tools'),
                      ('entry:shapes','图形展示','八大图形 · 自动播放',('看清顶点与连接顺序','选择图形即可观看','再次点击即可重播'),'shapes'))
        self.menu_row,self.menu_page=self._card_page('选择一种学习方式，进入专属页面。',menu_entries,card_width=0)
        self.menu_row.activated.connect(lambda key:self.select_section(key.split(':')[1]))
        self.stack.setCurrentIndex(3)
        self.simplify_shapes();self.refresh_theme()

    def _card_page(self,caption,entries,card_width=330):
        page=QWidget();box=QVBoxLayout(page);box.setContentsMargins(0,0,0,0);box.setSpacing(12)
        text=QLabel(caption);text.setWordWrap(True);box.addWidget(text)
        scroll=HorizontalCardScroll();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        row=TeachingCardRow(entries,card_width=card_width);scroll.setWidget(row);box.addWidget(scroll,1)
        hint=QLabel('点击整张卡片进入 · 鼠标滚轮左右浏览' if card_width else '课程、工具和图形，各有一个独立空间。');box.addWidget(hint)
        self.stack.addWidget(page)
        return row,page

    def simplify_shapes(self):
        page=self.knowledge
        for name in ('demo_toggle','demo_replay','demo_controls','demo_zone','demo_mode',
                     'lesson_toggle','chiyo_art_slot','hero_focus','hero_steps'):
            widget=getattr(page,name,None)
            if widget is not None:widget.hide()
        page.hero_figure.hide();page.figure_stack.setCurrentWidget(page.route_demo)
        page.feature.setMinimumHeight(320)
        page.feature_detail.setMinimumWidth(220)
        page.feature_row.setStretch(0,7);page.feature_row.setStretch(1,3)
        page.hero_title.setStyleSheet(f'font-size:28px;font-weight:800;color:{T.P["ink"]};background:transparent;')
        page.hero_meta.setStyleSheet(f'font-size:14px;color:{T.P["ink2"]};background:transparent;')
        page.hero_desc.setStyleSheet(f'font-size:14px;color:{T.P["ink2"]};background:transparent;')
        page.shape_nav_layout.setContentsMargins(0,0,0,0)
        for button in page._shape_nav_buttons:button.setMinimumHeight(62)

    def select_section(self, key):
        self.cancel_intro()
        from workbench_ui import reveal_layout
        target = {'courses': 0, 'tools': 1, 'shapes': 2}[key]
        row = self.course_row if target == 0 else self.tool_row
        if target != 2:
            row.prepare_intro()
        transition = reveal_layout(
            self, lambda: self.stack.setCurrentIndex(target),
            background=T.P['shell'])
        self._update_header()
        self.rail.set_current(key)
        if key == 'shapes':
            self.simplify_shapes()
            self._after_layout_transition(
                transition, self.knowledge._replay_demo)
        else:
            self.knowledge.route_demo.stop()
            self._after_layout_transition(transition, row.play_intro)

    def _after_layout_transition(self, transition, callback):
        if transition is None:
            if self.isVisible():
                QTimer.singleShot(0, callback)
            return
        transition.animation.finished.connect(
            lambda: callback() if self.isVisible() else None)

    def current_row(self):
        return {0:self.course_row,1:self.tool_row,3:self.menu_row}.get(self.stack.currentIndex(),self.course_row)

    def prepare_menu_entry(self):
        '''隐藏页切换前同步到入口菜单，不在后台排队播放动画。'''
        self.cancel_intro()
        self.knowledge.route_demo.stop()
        self.stack.setCurrentIndex(3)
        self._update_header()

    def show_menu(self):
        self.cancel_intro()
        self.knowledge.route_demo.stop()
        self.menu_row.prepare_intro()
        from workbench_ui import reveal_layout
        transition = reveal_layout(
            self, lambda: self.stack.setCurrentIndex(3),
            background=T.P['shell'])
        self._update_header()
        self._after_layout_transition(transition, self.menu_row.play_intro)

    def warm_up_menu(self):
        """在启动阶段预绘入口大卡片，避免首进时阻塞动画首帧。"""
        return self.menu_row.warm_up()

    def prepare_intro(self):
        prepare_card_in((self.title,self.subtitle,self.rail))
        if self.stack.currentIndex() != 2:
            self.current_row().prepare_intro()

    def play_intro(self):
        card_in((self.title,self.subtitle,self.rail),step=55,ms=560)
        if self.stack.currentIndex()!=2:QTimer.singleShot(80,self.current_row().play_intro)
        else:QTimer.singleShot(80,self.knowledge._replay_demo)

    def cancel_intro(self):
        cancel_card_in((self.title,self.subtitle,self.rail))
        self.course_row.cancel_intro();self.tool_row.cancel_intro()
        if hasattr(self,'menu_row'):self.menu_row.cancel_intro()

    def refresh_theme(self):
        self.cancel_intro()
        self._update_header()
        self.subtitle.setStyleSheet(f'font-size:14px;color:{T.P["ink2"]};background:transparent;')
        self.setStyleSheet(f'QWidget#TeachingHub{{background:transparent;}} QWidget#TeachingHub QLabel{{color:{T.P["ink2"]};background:transparent;}} QScrollArea{{background:transparent;border:0;}} QScrollArea>QWidget>QWidget{{background:transparent;}}')
        self.rail.refresh_theme()
        self.section_back.refresh();self.banner.refresh_art()
        for row in (self.course_row,self.tool_row,self.menu_row):
            row.invalidate_warmup()
            for card in row.cards:card.update()
        self.simplify_shapes()

    def _update_header(self):
        if not hasattr(self,'menu_row'):return
        compact=self.width()<1050 or self.height()<760 or self.stack.currentIndex()==2
        section={0:'课程',1:'工具',2:'图形展示',3:'教学菜单'}[self.stack.currentIndex()]
        self.section_navigation.setVisible(self.stack.currentIndex()!=3)
        self.section_title.setText(section)
        self.section_title.setStyleSheet(f'font-size:24px;font-weight:800;color:{T.P["ink"]};')
        self.banner.setVisible(T.app_theme=='anna')
        self.chiyo_note.setVisible(T.app_theme=='chiyo')
        self.title.setVisible(T.app_theme!='anna' and (self.stack.currentIndex()==3 or T.app_theme=='chiyo'))
        self.subtitle.setVisible(self.stack.currentIndex()==3 and T.app_theme!='anna')
        self.title.setText('地面站教学' if T.app_theme=='chiyo' else '地面站教学 · 图形展示' if self.stack.currentIndex()==2 else '先看懂，再掌握航线。' if compact else '先看懂，\n再掌握航线。')
        self.title.setStyleSheet(f'font-size:{30 if compact else 38}px;font-weight:800;color:{T.P["ink"]};background:transparent;')
        self.content_layout.setContentsMargins(30,8 if self.banner.isVisible() else 18,30,12 if compact else 18)
        self.content_layout.setSpacing(12 if compact else 16)

    def resizeEvent(self,event):
        self.cancel_intro()
        self._update_header()
        super().resizeEvent(event)

    def hideEvent(self,event):
        self.cancel_intro();super().hideEvent(event)
