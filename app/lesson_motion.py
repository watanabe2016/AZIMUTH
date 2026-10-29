"""可重播的教学示意：以有限动画解释概念，不推导题目答案。"""
import math
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath, QFont
from PySide6.QtWidgets import QWidget
import theme as T
from ui_kit import animations_enabled
from motion_runtime import motion_page


@motion_page
class LessonConceptCanvas(QWidget):
    caption_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 290)
        self.mode = "coordinate"
        self.progress = 1.0
        self.speed = 1.0
        self.paused = False
        self.strength = 1.0
        self.strength_motion = QVariantAnimation(self)
        self.strength_motion.setDuration(520)
        self.strength_motion.setEasingCurve(QEasingCurve.InOutCubic)
        self.strength_motion.valueChanged.connect(self._set_strength)
        self.motion = QVariantAnimation(self)
        self.motion.setDuration(4200)
        self.motion.setStartValue(0.0)
        self.motion.setEndValue(1.0)
        self.motion.setEasingCurve(QEasingCurve.InOutSine)
        self.motion.valueChanged.connect(self._advance)

    def set_mode(self, mode):
        self.mode = mode
        self.replay()

    def replay(self):
        self.motion.stop()
        self.paused = False
        self.motion.setStartValue(0.0)
        self.motion.setDuration(round(4200 / self.speed))
        self.caption_changed.emit({
            "coordinate": "教学示例：先确定东、北方向，再定位航点；坐标为示意值。",
            "altitude": "教学示例：上升、保持高度、下降；折线表示高度变化，不代表真实航迹。",
            "wind": "教学示例：风向表示风的来向；侧风时通过航向修正保持预定航迹。",
            "avoidance": "教学示例：通过过渡点绕开限制区域，再接入任务航线。",
        }[self.mode])
        if animations_enabled() and self.isVisible():
            self.progress = 0.0
            self.motion.start()
        else:
            self.progress = 1.0
            self.update()

    def set_speed(self, speed):
        running = self.motion.state() == QVariantAnimation.Running
        self.motion.stop()
        self.speed = max(.25, min(2.0, float(speed)))
        if running:
            self._continue()

    def _continue(self):
        self.motion.setStartValue(self.progress)
        self.motion.setEndValue(1.0)
        self.motion.setDuration(max(1, round(4200 * (1 - self.progress) / self.speed)))
        self.motion.start()
        self.paused = False

    def toggle_pause(self):
        if self.motion.state() == QVariantAnimation.Running:
            self.motion.pause()
            self.paused = True
        elif self.motion.state() == QVariantAnimation.Paused:
            self.motion.resume()
            self.paused = False
        elif self.progress < 1:
            self._continue()
        else:
            self.replay()

    def step_forward(self):
        self.motion.stop()
        self._advance(min(1, (math.floor(self.progress * 4 + .0001) + 1) / 4))
        self.paused = True

    def set_strength(self, value):
        self.strength_motion.stop()
        target = max(.1, min(2, float(value)))
        if not animations_enabled() or not self.isVisible():
            self._set_strength(target)
            return
        self.strength_motion.setStartValue(self.strength)
        self.strength_motion.setEndValue(target)
        self.strength_motion.start()

    def _set_strength(self, value):
        self.strength = float(value)
        self.update()

    def _advance(self, value):
        self.progress = float(value)
        if not animations_enabled():
            self.motion.stop()
            self.progress = 1.0
        self.update()

    def hideEvent(self, event):
        self.motion.stop()
        self.strength_motion.stop()
        self.progress = 1.0
        super().hideEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        scale = min(self.width() / 440, self.height() / 340)
        p.translate((self.width() - 440 * scale) / 2, (self.height() - 340 * scale) / 2)
        p.scale(scale, scale)
        p.setFont(QFont(T.pick_font(), 11))
        ink, blue = QColor(T.C_INK), QColor(T.C_BLUE)
        faint = QColor(T.P["input_border"])
        p.setPen(ink)
        p.drawText(QRectF(20, 12, 400, 30), Qt.AlignCenter, "教学示例 · 非题目条件")
        t = self.progress

        def line(a, b, color=blue, width=2):
            p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(*a), QPointF(*b))

        def dot(x, y, text):
            p.setPen(Qt.NoPen)
            p.setBrush(blue)
            p.drawEllipse(QPointF(x, y), 5, 5)
            p.setPen(ink)
            p.drawText(QRectF(x + 9, y - 22, 95, 26), text)

        if self.mode == "coordinate":
            for x in range(70, 381, 40):
                line((x, 65), (x, 285), faint, 1)
            for y in range(85, 286, 40):
                line((70, y), (380, y), faint, 1)
            line((70, 285), (380, 285))
            line((70, 285), (70, 65))
            p.setPen(ink)
            p.drawText(383, 288, "E")
            p.drawText(65, 60, "N")
            u = min(1, t * 2)
            v = max(0, min(1, (t - .5) * 2))
            line((70, 285), (70 + 220 * u, 285))
            line((290, 285), (290, 285 - 140 * v))
            dot(70 + 220 * u, 285 - 140 * v, "航点" if t > .8 else "定位中")
        elif self.mode == "altitude":
            line((55, 285), (390, 285), faint)
            line((55, 285), (55, 75), faint)
            points = [(65, 275), (155, 125), (295, 125), (375, 275)]
            p.setPen(ink)
            p.drawText(60, 65, "高度")
            p.drawText(320, 310, "飞行阶段")
            for i, (a, b) in enumerate(zip(points, points[1:])):
                u = max(0, min(1, t * 3 - i))
                line(a, (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u))
            i = min(2, int(t * 3))
            u = min(1, t * 3 - i)
            a, b = points[i:i + 2]
            dot(a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, ("上升", "平飞", "下降")[i])
        elif self.mode == "wind":
            line((220, 285), (220, 80), faint, 2)
            p.setPen(ink)
            p.drawText(230, 95, "预定航迹")
            wind = QColor(T.P["err_fg"])
            for y in (150, 190, 230):
                x = 70 + 22 * math.sin(t * math.pi)
                length = 75 * self.strength
                line((x, y), (x + length, y), wind)
                line((x + length - 12, y - 6), (x + length, y), wind)
                line((x + length - 12, y + 6), (x + length, y), wind)
            p.setPen(ink)
            p.drawText(65, 120, "风来自西侧")
            y = 275 - 175 * t
            p.save()
            p.translate(220, y)
            p.rotate(-18 * self.strength * min(1, t * 2))
            path = QPainterPath()
            path.moveTo(0, -13)
            path.lineTo(-9, 10)
            path.lineTo(0, 5)
            path.lineTo(9, 10)
            path.closeSubpath()
            p.fillPath(path, blue)
            p.restore()
            p.setPen(ink)
            p.drawText(270, 245, "向来风侧修正")
        else:
            zone = QColor(T.P["err_fg"])
            zone.setAlpha(28)
            p.setBrush(zone)
            p.setPen(QPen(QColor(T.P["err_fg"]), 1, Qt.DashLine))
            p.drawRoundedRect(QRectF(145, 125, 130, 120), 14, 14)
            p.setPen(ink)
            p.drawText(QRectF(145, 160, 130, 50), Qt.AlignCenter, "限制区域")
            points = [(65, 275), (105, 85), (335, 85), (375, 275)]
            for i, (a, b) in enumerate(zip(points, points[1:])):
                u = max(0, min(1, t * 3 - i))
                line(a, (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u))
            for i, (x, y) in enumerate(points):
                if t * 3 >= i or i == 0:
                    dot(x, y, ("起点", "过渡点1", "过渡点2", "接入点")[i])
        p.end()
