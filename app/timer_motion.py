"""按真实阶段时长组织的时间带，不以循环装饰模拟训练进度。"""
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QLinearGradient
from PySide6.QtWidgets import QWidget
import theme as T
from teaching_scene_canvas import painted_color


class CountdownRunway(QWidget):
    def __init__(self, ring, parent=None):
        super().__init__(parent)
        self.ring = ring
        self.page = parent.parentWidget() if parent else None
        self.setFixedHeight(62)
        self.setAccessibleName('阅题与作答时间带，分段长度按实际时长分配')

    def segments(self):
        page = self.page
        read = page.read_total
        answer = page.do_total
        if page.phase == page.READ:
            read = self.ring.total
        elif page.phase == page.DOING:
            read = getattr(page, '_run_read_span', read) if read else 0
            answer = self.ring.total
        total = max(1, read + answer)
        phase_progress = 1 - max(0, min(1, self.ring._shown / max(1, self.ring.total)))
        elapsed = (phase_progress * read if page.phase == page.READ else
            read + phase_progress * answer if page.phase == page.DOING else
            total if page.phase == page.LOCK else 0)
        return read, answer, elapsed / total

    def paintEvent(self, event):
        if self.page is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        read, answer, progress = self.segments()
        x, y, width = 10.0, 39.0, max(1.0, self.width()-20.0)
        split = x + width*read/max(1, read+answer)
        accent = QColor(T.C_BLUE)
        muted = QColor(T.P['ink3'])
        edge = painted_color(T.P['input_border'])
        p.setFont(self.ring._countdown_font(11))
        p.setPen(muted)
        p.drawText(QRectF(x, 0, width*.5, 23), Qt.AlignLeft|Qt.AlignVCenter,
            '跳过阅题' if read == 0 else '阅题 %02d:%02d' % (read//60, read%60))
        p.drawText(QRectF(x+width*.5, 0, width*.5, 23), Qt.AlignRight|Qt.AlignVCenter,
            '作答 %02d:%02d' % (answer//60, answer%60))
        gap = 5 if read else 0
        p.setPen(QPen(edge, 5, Qt.SolidLine, Qt.RoundCap))
        if read:
            p.drawLine(QPointF(x, y), QPointF(max(x, split-gap), y))
        p.drawLine(QPointF(split+gap, y), QPointF(x+width, y))
        head = x + width*progress
        p.setPen(QPen(accent, 5, Qt.SolidLine, Qt.RoundCap))
        if progress > 0:
            if read:
                p.drawLine(QPointF(x, y), QPointF(min(head, max(x, split-gap)), y))
            if head > split+gap:
                p.drawLine(QPointF(split+gap, y), QPointF(head, y))
        if self.page.phase != self.page.IDLE:
            p.setPen(QPen(painted_color(T.P['panel_bg']), 3))
            p.setBrush(accent)
            p.drawEllipse(QPointF(head, y), 6, 6)
        if read:
            p.setPen(QPen(muted, 1))
            p.drawLine(QPointF(split, y-7), QPointF(split, y+7))
        p.end()
