"""航线教学演示与练习完成反馈，所有动画都有静态终态。"""
import math

from PySide6.QtCore import Qt, QPoint, QPointF, QRectF, Signal, QVariantAnimation, QEasingCurve, QObject, QTimer, QEvent
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

import theme as T
from ui_kit import animations_enabled
from motion_runtime import motion_page


ROUTES = (
    ((.5, .86-.84*math.sqrt(3)/2), (.92, .86), (.08, .86)),
    ((.14, .10), (.86, .86), (.14, .86)),
    ((.14, .12), (.14, .88), (.68, .88), (.94, .12)),
    ((.14, .88), (.14, .12), (.94, .12), (.50, .50), (.94, .88)),
    ((.16, .88), (.84, .12), (.84, .88), (.16, .12)),
    tuple((.5 + .44 * math.cos(math.radians(-90 + i * 60)),
           .5 + .44 * math.sin(math.radians(-90 + i * 60))) for i in range(6)),
    ((.10, .18), (.90, .18), (.90, .50), (.10, .50), (.10, .84), (.90, .84)),
    tuple((.5 + .39 * math.cos(math.radians(-90 + i * 90)),
           .5 + .39 * math.sin(math.radians(-90 + i * 90))) for i in range(4)),
)


def _font(size, bold=False):
    font = QFont(T.pick_font())
    font.setPixelSize(size)
    font.setBold(bold)
    return font


@motion_page
class FlightRouteCanvas(QWidget):
    """独立的教学演示，不修改原始题卡；按真实边长比例播放航段。"""
    segment_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 290)
        self.setAccessibleName("航线连接顺序与航向演示")
        self.index = 0
        self.progress = 1.0
        self.zone_progress = 0.0
        self._zone_target = 0.0
        self.heading = 0.0
        self.speed = 1.0
        self.paused = False
        self.measurements = False
        self.angles = False
        self.selected_segment = -1
        self._segment = -1
        self.motion = QVariantAnimation(self)
        self.motion.setStartValue(0.0)
        self.motion.setEndValue(1.0)
        self.motion.setEasingCurve(QEasingCurve.Linear)
        self.motion.valueChanged.connect(self._advance)
        self.motion.finished.connect(self._finished)
        self.compass = QVariantAnimation(self)
        self.compass.setDuration(420)
        self.compass.setEasingCurve(QEasingCurve.InOutCubic)
        self.compass.valueChanged.connect(self._turn)
        self.zone_motion = QVariantAnimation(self)
        self.zone_motion.setDuration(650)
        self.zone_motion.setEasingCurve(QEasingCurve.InOutCubic)
        self.zone_motion.valueChanged.connect(self._expand_zone)
        self.arrival = 1.
        self.arrival_index = 0
        self.arrival_motion = QVariantAnimation(self)
        self.arrival_motion.setDuration(680)
        self.arrival_motion.setEasingCurve(QEasingCurve.OutCubic)
        self.arrival_motion.valueChanged.connect(self._arrival)

    def _arrival(self, value):
        self.arrival = float(value)
        self.update()

    def _arrive_at(self, index):
        self.arrival_index = index % len(ROUTES[self.index])
        self.arrival_motion.stop()
        self.arrival = 1.
        if self.isVisible() and animations_enabled():
            self.arrival_motion.setStartValue(0.)
            self.arrival_motion.setEndValue(1.)
            self.arrival_motion.start()

    def set_speed(self, speed):
        running = self.motion.state() == QVariantAnimation.Running
        self.motion.stop()
        self.speed = max(.25, min(2.0, float(speed)))
        if running:
            self._continue()

    def _continue(self):
        self.motion.setStartValue(self.progress)
        self.motion.setEndValue(1.0)
        self.motion.setDuration(max(1, round(1100 * len(ROUTES[self.index]) * (1 - self.progress) / self.speed)))
        self.motion.start()
        self.paused = False

    def toggle_pause(self):
        if self.motion.state() == QVariantAnimation.Running:
            self.motion.pause()
            self.compass.pause()
            self.paused = True
        elif self.motion.state() == QVariantAnimation.Paused:
            self.motion.resume()
            if self.compass.state() == QVariantAnimation.Paused:
                self.compass.resume()
            self.paused = False
        elif self.progress < 1:
            self._continue()
        else:
            self.replay()

    def step_forward(self):
        self.motion.stop()
        self.compass.stop()
        if self.index == 7:
            stops = [.25, .5, .75, 1.0]
        else:
            points = self._points()
            sequence = points if self.index == 6 else points + [points[0]]
            lengths = [math.hypot(b.x() - a.x(), b.y() - a.y())
                       for a, b in zip(sequence, sequence[1:])]
            total = max(.001, sum(lengths))
            distance = 0.0
            stops = []
            for length in lengths:
                distance += length
                stops.append(distance / total)
        self._advance(next((value for value in stops if value > self.progress + .0001), 1.0))
        self.paused = True

    def toggle_measurements(self, enabled):
        self.measurements = bool(enabled)
        self.update()

    def toggle_angles(self, enabled):
        self.angles = bool(enabled)
        self.update()

    def select_segment(self, index):
        self.selected_segment = int(index) % len(ROUTES[self.index])
        self.update()

    def mousePressEvent(self, event):
        point = event.position()
        points = self._points()
        closest = min(range(len(points)), key=lambda i: math.hypot(points[i].x() - point.x(), points[i].y() - point.y()))
        if math.hypot(points[closest].x() - point.x(), points[closest].y() - point.y()) < 28:
            self.select_segment(closest)
        super().mousePressEvent(event)

    def set_shape(self, index):
        self.stop()
        self.index = index % len(ROUTES)
        self._segment = -1
        self.progress = 1.0
        self.zone_progress = self._zone_target
        self.update()

    def replay(self):
        self.stop()
        self.paused = False
        self.motion.setStartValue(0.0)
        self._segment = -1
        if not animations_enabled():
            self._advance(1.0)
            self._finished()
            return
        self.progress = 0.0
        if not self.isVisible():
            self.update()
            return
        self.motion.setDuration(round((5200 if self.index == 7 else 1100 * len(ROUTES[self.index])) / self.speed))
        self.motion.start()

    def stop(self):
        self.motion.stop()
        self.compass.stop()
        self.zone_motion.stop()
        self.arrival_motion.stop()
        self.arrival = 1.

    def hideEvent(self, event):
        self.stop()
        self.progress = 1.0
        self.zone_progress = self._zone_target
        super().hideEvent(event)

    def set_zone(self, enabled):
        self.zone_motion.stop()
        target = 1.0 if enabled else 0.0
        self._zone_target = target
        if not animations_enabled() or not self.isVisible():
            self._expand_zone(target)
            return
        self.zone_motion.setStartValue(self.zone_progress)
        self.zone_motion.setEndValue(target)
        self.zone_motion.start()

    def _expand_zone(self, value):
        self.zone_progress = float(value)
        self.update()

    def _turn(self, value):
        self.heading = float(value)
        self.update()

    def _geometry(self):
        # 统一等比例坐标系，罗盘和提示文字放在图形外侧。
        size = max(80.0, min(self.width() - 58.0, self.height() - 104.0))
        return QRectF((self.width() - size) / 2, 38, size, size)

    def _points(self):
        box = self._geometry()
        return [QPointF(box.x() + x * box.width(), box.y() + y * box.height())
                for x, y in ROUTES[self.index]]

    def _position(self):
        points = self._points()
        if self.index == 7:
            box = self._geometry()
            angle = -math.pi / 2 + self.progress * math.tau
            radius = box.width() * .39
            pos = box.center() + QPointF(math.cos(angle) * radius, math.sin(angle) * radius)
            return min(3, int(self.progress * 4)), pos, self.progress * 360 + 90
        sequence = points if self.index == 6 else points + [points[0]]
        lengths = [math.hypot(b.x() - a.x(), b.y() - a.y())
                   for a, b in zip(sequence, sequence[1:])]
        distance = self.progress * sum(lengths)
        for i, (a, b, length) in enumerate(zip(sequence, sequence[1:], lengths)):
            if distance <= length or i == len(lengths) - 1:
                fraction = min(1.0, distance / max(1.0, length))
                position = a + (b - a) * fraction
                heading = math.degrees(math.atan2(b.x() - a.x(), -(b.y() - a.y()))) % 360
                return i, position, heading
            distance -= length

    def _advance(self, value):
        self.progress = float(value)
        segment, _point, heading = self._position()
        if segment != self._segment:
            self._segment = segment
            self._arrive_at(segment)
            self.compass.stop()
            difference = (heading - self.heading + 180) % 360 - 180
            if animations_enabled() and self.index != 7:
                self.compass.setStartValue(self.heading)
                self.compass.setEndValue(self.heading + difference)
                self.compass.start()
            else:
                self.heading = heading
            destination = (segment + 1) % len(ROUTES[self.index]) + 1
            self.segment_changed.emit(f"航段 {segment + 1} → {destination}　·　演示航向 {heading:.0f}°")
        elif self.index == 7:
            self.heading = heading
        self.update()

    def _finished(self):
        self._arrive_at(len(ROUTES[self.index]) - 1 if self.index == 6 else 0)
        self.segment_changed.emit("连接顺序演示完成　·　实际任务以题目要求为准")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        points = self._points()
        box = self._geometry()
        segment, position, _heading = self._position()
        p.setFont(_font(11))
        p.setPen(QColor(T.P["ink3"]))
        p.drawText(QRectF(12, 6, self.width() - 24, 24), Qt.AlignLeft | Qt.AlignVCenter,
                   "连接顺序演示 · 非飞行仿真")
        # 示意限制区独立标注，不将示例区域当作当前题目的限制条件。
        if self.zone_progress > 0:
            zone = QRectF(box.x() + box.width() * .65, box.y() + box.height() * .06,
                          box.width() * .31, box.height() * .24)
            rect = QRectF(zone.center().x() - zone.width() * self.zone_progress / 2,
                          zone.center().y() - zone.height() * self.zone_progress / 2,
                          zone.width() * self.zone_progress, zone.height() * self.zone_progress)
            p.save()
            p.setOpacity(self.zone_progress)
            amber = QColor(T.C_AMBER)
            fill = QColor(amber)
            fill.setAlpha(40 if rect.contains(position) else 18)
            p.setBrush(fill)
            p.setPen(QPen(amber, 1.5, Qt.DashLine))
            p.drawRoundedRect(rect, 8, 8)
            p.setPen(amber)
            p.drawText(QRectF(zone.x() - 20, zone.y() - 22, zone.width() + 40, 20),
                       Qt.AlignCenter, "演示空域")
            p.restore()
        path = QPainterPath()
        if self.index == 7:
            radius = box.width() * .39
            circle = QRectF(box.center().x() - radius, box.center().y() - radius, radius * 2, radius * 2)
            path.addEllipse(circle)
        else:
            path.moveTo(points[0])
            for point in points[1:]:
                path.lineTo(point)
            if self.index != 6:
                path.closeSubpath()
        p.setBrush(Qt.NoBrush)
        dim = QColor(T.P["ink3"])
        dim.setAlpha(65)
        p.setPen(QPen(dim, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        active = QPainterPath()
        if self.index == 7:
            active.arcMoveTo(circle, 90)
            active.arcTo(circle, 90, -360 * self.progress)
        else:
            active.moveTo(points[0])
            for point in points[1:segment + 1]:
                active.lineTo(point)
            active.lineTo(position)
        p.setPen(QPen(QColor(T.C_BLUE), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(active)
        p.setFont(_font(12, True))
        if self.selected_segment >= 0:
            start = points[self.selected_segment]
            end = points[(self.selected_segment + 1) % len(points)]
            p.setPen(QPen(QColor(T.C_AMBER), 5, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(start, end)
        if self.measurements or self.angles:
            p.setFont(_font(10))
            first = math.hypot(points[1].x() - points[0].x(), points[1].y() - points[0].y())
            for i, start in enumerate(points):
                end = points[(i + 1) % len(points)]
                if self.measurements and not (self.index == 6 and i == len(points)-1):
                    mid = (start + end) / 2
                    relative = math.hypot(end.x() - start.x(), end.y() - start.y()) / max(1, first)
                    p.setPen(QColor(T.P["ink2"]))
                    p.drawText(QRectF(mid.x() - 40, mid.y() - 25, 80, 18), Qt.AlignCenter, f"{relative:.2f} × a")
                if self.angles and self.index not in (4, 7) and not (self.index == 6 and i in (0, len(points)-1)):
                    a, b = points[i - 1] - start, end - start
                    angle = math.degrees(math.acos(max(-1, min(1, (a.x() * b.x() + a.y() * b.y()) /
                                             max(.001, math.hypot(a.x(), a.y()) * math.hypot(b.x(), b.y()))))))
                    if self.index == 3 and i == 3:
                        angle = 360-angle
                    p.setPen(QColor(T.C_BLUE))
                    p.drawText(QRectF(start.x() - 30, start.y() + 15, 60, 18), Qt.AlignCenter, f"{angle:.0f}°")
            p.setFont(_font(12, True))
        for i, point in enumerate(points):
            if i == self.arrival_index and self.arrival < 1:
                pulse = QColor(T.C_BLUE); pulse.setAlpha(round(110 * (1 - self.arrival)))
                p.setPen(QPen(pulse, 1.8)); p.setBrush(Qt.NoBrush)
                p.drawEllipse(point, 13 + 16 * self.arrival, 13 + 16 * self.arrival)
            reached = self.progress >= 1 or i <= segment
            p.setBrush(QColor(T.C_BLUE if reached else T.P["panel_bg"]))
            p.setPen(QPen(QColor(T.C_BLUE if reached else T.P["ink3"]), 1.4))
            p.drawEllipse(point, 10, 10)
            p.setPen(QColor("#FFFFFF" if reached else T.P["ink2"]))
            p.drawText(QRectF(point.x() - 10, point.y() - 10, 20, 20), Qt.AlignCenter, str(i + 1))
        if self.progress < 1:
            p.save()
            p.translate(position)
            p.rotate(self.heading)
            p.setPen(QPen(QColor(T.P["panel_bg"]), 1.4))
            p.setBrush(QColor(T.C_BLUE))
            p.drawPolygon(QPolygonF([QPointF(0, -10), QPointF(7, 8), QPointF(0, 4), QPointF(-7, 8)]))
            p.restore()
        center = QPointF(self.width() - 43, self.height() - 40)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(T.P["glass_soft_edge"]), 1.2))
        p.drawEllipse(center, 25, 25)
        p.setFont(_font(9, True))
        p.setPen(QColor(T.P["ink3"]))
        p.drawText(QRectF(center.x() - 10, center.y() - 24, 20, 13), Qt.AlignCenter, "N")
        p.save()
        p.translate(center)
        p.rotate(self.heading)
        p.setPen(QPen(QColor(T.C_BLUE), 2.3, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(0, 15), QPointF(0, -14))
        p.drawLine(QPointF(0, -14), QPointF(-4, -7))
        p.drawLine(QPointF(0, -14), QPointF(4, -7))
        p.restore()
        p.setPen(QColor(T.P["ink2"]))
        p.setFont(_font(11))
        p.drawText(QRectF(12, self.height() - 48, self.width() - 100, 32),
                   Qt.AlignLeft | Qt.AlignVCenter, f"罗盘寻向　{self.heading % 360:.0f}°")
        p.end()


class CompletionStamp(QWidget):
    """操作完成时在按钮附近轻落一次记录章，不遮挡鼠标操作。"""
    def __init__(self, anchor):
        super().__init__(anchor.window())
        self.anchor = anchor
        self.progress = 0.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(166, 60)
        self.motion = QVariantAnimation(self)
        self.motion.setDuration(1600)
        self.motion.setStartValue(0.0)
        self.motion.setEndValue(1.0)
        self.motion.valueChanged.connect(self._advance)
        self.motion.finished.connect(self.hide)
        anchor.installEventFilter(self)
        anchor.destroyed.connect(self.deleteLater)
        self.hide()

    def eventFilter(self, watched, event):
        if watched is getattr(self, "anchor", None) and event.type() == QEvent.Hide:
            self.hide()
        return super().eventFilter(watched, event)

    def play(self):
        if not animations_enabled() or not self.anchor.isVisible():
            return
        self.motion.stop()
        self.progress = 0.0
        position = self.anchor.mapTo(self.parentWidget(), QPoint(0, 0))
        self.move(max(4, min(position.x(), self.parentWidget().width() - self.width() - 4)),
                  max(4, position.y() - self.height() - 6))
        self.show()
        self.raise_()
        self.motion.start()

    def hideEvent(self, event):
        self.motion.stop()
        super().hideEvent(event)

    def _advance(self, value):
        if not animations_enabled():
            self.hide()
            return
        self.progress = float(value)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self.progress
        landing = min(1.0, t / .27)
        eased = 1 - (1 - landing) ** 3
        opacity = min(1.0, t / .12) * min(1.0, (1 - t) / .22)
        p.setOpacity(opacity)
        p.translate(self.width() / 2, self.height() / 2 - 5 * (1 - eased))
        p.rotate(-2.5 * (1 - eased))
        p.scale(1.07 - .07 * eased, 1.07 - .07 * eased)
        rect = QRectF(-76, -22, 152, 44)
        p.setBrush(QColor(T.P["panel_bg"]))
        p.setPen(QPen(QColor(T.C_BLUE), 1.5))
        p.drawRoundedRect(rect, 12, 12)
        p.setPen(QPen(QColor(T.C_BLUE), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        mark = QPainterPath(QPointF(-61, 0))
        mark.lineTo(-56, 5)
        mark.lineTo(-46, -6)
        p.drawPath(mark)
        p.setFont(_font(13, True))
        p.setPen(QColor(T.P["ink"]))
        p.drawText(QRectF(-38, -15, 110, 30), Qt.AlignCenter, "练习已记录")
        p.end()


def show_completion_stamp(anchor):
    stamp = getattr(anchor, "_completion_stamp", None)
    if stamp is None:
        stamp = CompletionStamp(anchor)
        anchor._completion_stamp = stamp
    stamp.play()


class CompanionGesture(QObject):
    """角色插画轻点头一次，保持原比例且不改变控件布局。"""
    def __init__(self, widget):
        super().__init__(widget)
        self.widget = widget
        self.progress = 0.0
        self.motion = QVariantAnimation(self)
        self.motion.setDuration(1250)
        self.motion.setStartValue(0.0)
        self.motion.setEndValue(1.0)
        self.motion.valueChanged.connect(self._advance)
        self.delay = QTimer(self)
        self.delay.setSingleShot(True)
        self.delay.timeout.connect(self._begin)

    def play(self):
        self.stop()
        if animations_enabled() and T.app_theme in ("anna", "chiyo"):
            self.delay.start(380)

    def _begin(self):
        if self.widget.isVisible() and animations_enabled():
            self.motion.start()

    def stop(self):
        self.delay.stop()
        self.motion.stop()
        self.progress = 0.0
        self.widget.update()

    def _advance(self, value):
        if not animations_enabled() or T.app_theme not in ("anna", "chiyo"):
            self.stop()
            return
        self.progress = float(value)
        self.widget.update()

    def apply(self, painter, area):
        nod = math.sin(self.progress * math.pi) ** 2
        painter.translate(area.center().x(), area.bottom())
        painter.translate(0, -3.5 * nod)
        painter.rotate(.7 * nod)
        painter.translate(-area.center().x(), -area.bottom())
