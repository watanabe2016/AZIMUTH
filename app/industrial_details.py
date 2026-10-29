"""工业极简主题共用的点阵字与图纸式页眉页脚。"""

from PySide6.QtCore import Qt, QPointF, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

import theme as T


def _digit_cells(char):
    """用 8×11 方点字模绘制紧凑、笔画清楚的矩阵数字。"""
    glyphs = {
        "0": ("00111100", "01111110", "11000011", "11000011",
              "11000011", "11000011", "11000011", "11000011",
              "11000011", "01111110", "00111100"),
        "1": ("00011000", "00111000", "01111000", "00011000",
              "00011000", "00011000", "00011000", "00011000",
              "00011000", "01111110", "01111110"),
        "2": ("00111100", "01100110", "11000011", "00000011",
              "00000110", "00001100", "00011000", "00110000",
              "01100000", "11000000", "11111111"),
        "3": ("11111100", "00000110", "00000110", "00001100",
              "00111100", "00001100", "00000110", "00000110",
              "11000011", "11000011", "01111110"),
        "4": ("00001100", "00011100", "00111100", "01101100",
              "11001100", "11001100", "11111111", "00001100",
              "00001100", "00001100", "00001100"),
        "5": ("11111111", "11000000", "11000000", "11111100",
              "00000110", "00000011", "00000011", "00000011",
              "11000011", "11000011", "01111110"),
        "6": ("00111110", "01100000", "11000000", "11000000",
              "11111100", "11000110", "11000011", "11000011",
              "11000011", "01100110", "00111100"),
        "7": ("11111111", "11000011", "00000011", "00000110",
              "00001100", "00011000", "00011000", "00011000",
              "00011000", "00011000", "00011000"),
        "8": ("00111100", "01100110", "11000011", "11000011",
              "01100110", "00111100", "01100110", "11000011",
              "11000011", "01100110", "00111100"),
        "9": ("00111100", "01100110", "11000011", "11000011",
              "11000111", "01111111", "00000011", "00000011",
              "00000110", "00001100", "01111000"),
    }
    if char == ":":
        cells = {(1, row) for row in (3, 4, 7, 8)}
        return 3, 11, cells

    rows = glyphs.get(char, glyphs["0"])
    cells = {(column, row) for row, line in enumerate(rows)
             for column, bit in enumerate(line) if bit == "1"}
    return 8, 11, cells


def paint_matrix_text(painter, text, rect, color, max_pitch=12.0,
                      width_ratio=0.8, height_ratio=0.43,
                      dot_ratio=0.82):
    """按 8×11 方点字模居中绘制紧密点阵数字。"""
    text = str(text)
    glyphs = [_digit_cells(char) for char in text]
    spacing = 1.2
    total_cells = sum(width for width, _height, _cells in glyphs)
    total_cells += spacing * max(0, len(glyphs) - 1)
    glyph_height = max(height for _width, height, _cells in glyphs)
    pitch = min(max_pitch,
                rect.width() * width_ratio / max(1, total_cells),
                rect.height() * height_ratio / glyph_height)
    # 方点彼此留一条细缝，密度接近参考图中的点阵显示。
    dot = min(max(0.45, pitch * dot_ratio), pitch * 0.9)
    total_width = total_cells * pitch - (pitch - dot)
    x = rect.x() + (rect.width() - total_width) / 2
    y = rect.y() + max(0.0, (rect.height() - glyph_height * pitch) / 2)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    for width, height, cells in glyphs:
        for col, row in cells:
            painter.drawRect(QRectF(x + col * pitch, y + row * pitch, dot, dot))
        x += (width + spacing) * pitch


class IndustrialPageMeta(QWidget):
    """按皮肤参考图绘制点阵页眉、品牌信息与窄分隔线。"""

    def __init__(self, section="EXAMINATION", focus="KNOWLEDGE", parent=None):
        super().__init__(parent)
        self.section = section
        self.focus = focus
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(360, 82)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(440, 84)

    def paintEvent(self, event):
        if T.app_theme != "industrial":
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        muted = QColor(T.P["ink3"])
        line = QColor(T.P["glass_soft_edge"])
        dot_color = QColor(muted)
        dot_color.setAlpha(115)
        painter.setPen(Qt.NoPen)
        painter.setBrush(dot_color)
        width = self.width()
        dots_width = min(196.0, width * 0.45)
        for row in range(4):
            for col in range(25):
                x = 2 + col * 8.0
                if x > dots_width:
                    break
                painter.drawEllipse(QRectF(x, 8 + row * 8, 1.6, 1.6))

        painter.setPen(QPen(line, 1))
        divider_x = dots_width + 13
        painter.drawLine(QRectF(divider_x, 3, 0, 31).topLeft(),
                         QRectF(divider_x, 3, 0, 31).bottomLeft())
        self._text(painter, "SIMULATION TRAINING", divider_x + 16, 10, muted)
        self._text(painter, "FOR A SAFER SKY", divider_x + 16, 25, muted)

        lower_y = 46
        col1_x = max(150.0, divider_x - 18)
        right_x = min(width - 102, col1_x + 158)
        left_width = max(72, right_x - col1_x - 16)
        self._text(painter, "AZIMUTH", col1_x, lower_y, muted, left_width)
        self._text(painter, "CAAC GROUND STATION", col1_x, lower_y + 12,
                   muted, left_width)
        self._text(painter, self.section, col1_x, lower_y + 24, muted,
                   left_width)
        if right_x > col1_x + 124:
            painter.setPen(QPen(line, 1))
            painter.drawLine(QRectF(right_x - 12, lower_y - 2, 0, 37).topLeft(),
                             QRectF(right_x - 12, lower_y - 2, 0, 37).bottomLeft())
        focus_lines = {
            "GOOD OPERATORS": ("GOOD OPERATORS", "KEEP THE WORLD", "CONNECTED"),
            "KNOWLEDGE": ("KNOWLEDGE", "PRACTICE", "PROFICIENCY"),
            "ALWAYS HIGHER": ("FOCUS", "PRACTICE", "IMPROVE"),
            "FOCUS": ("FOCUS", "PRACTICE", "IMPROVE"),
        }.get(self.focus, (self.focus, "PRACTICE", "PROFICIENCY"))
        for index, line_text in enumerate(focus_lines):
            self._text(painter, line_text, right_x, lower_y + index * 12,
                       muted, width - right_x)

    @staticmethod
    def _text(painter, text, x, y, color, width=180):
        font = QFont("Consolas", 7)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(QRectF(x, y, width, 12), Qt.AlignLeft | Qt.AlignVCenter,
                         text)


class IndustrialPageFooter(QWidget):
    """轻量页脚：点阵、训练口号与双侧图纸式分栏。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumHeight(58)
        self.setMaximumHeight(58)

    def sizeHint(self):
        return QSize(720, 58)

    def paintEvent(self, event):
        if T.app_theme != "industrial":
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        muted = QColor(T.P["ink3"])
        line = QColor(T.P["glass_soft_edge"])
        dot_color = QColor(muted)
        dot_color.setAlpha(100)
        w, h = self.width(), self.height()
        painter.setPen(QPen(line, 1))
        painter.drawLine(0, 1, w, 1)
        painter.setPen(Qt.NoPen)
        painter.setBrush(dot_color)
        for x0 in (8, max(8, w - 202)):
            for row in range(5):
                for col in range(15):
                    painter.drawEllipse(QRectF(x0 + col * 8, 14 + row * 8,
                                               1.5, 1.5))
        self._text(painter, "TUNE", 145, 15, muted)
        self._text(painter, "PRACTICE", 145, 27, muted)
        self._text(painter, "PROGRESS", 145, 39, muted)
        center = "AZIMUTH  ·  CAAC  ·  TRAINING  ·  ALWAYS HIGHER"
        font = QFont("Consolas", 8)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
        painter.setFont(font)
        painter.setPen(muted)
        painter.drawText(QRectF(max(280, (w - 360) / 2), 30,
                                max(240, w - 560), 11),
                         Qt.AlignCenter | Qt.AlignVCenter, center)
        right_x = max(12, w - 108)
        painter.setPen(QPen(line, 1))
        painter.drawLine(QPointF(right_x - 14, 13), QPointF(right_x - 14, 51))
        right_width = max(64, w - right_x - 4)
        self._text(painter, "GOOD OPERATORS", right_x, 15, muted, right_width)
        self._text(painter, "KEEP THE WORLD", right_x, 27, muted, right_width)
        self._text(painter, "CONNECTED", right_x, 39, muted, right_width)

    @staticmethod
    def _text(painter, text, x, y, color, width=460):
        font = QFont("Consolas", 7)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(QRectF(x, y, width, 11), Qt.AlignLeft | Qt.AlignVCenter,
                         text)


class IndustrialMatrixStamp(QLabel):
    """题库题号使用和计时页一致的高密度矩阵数字。"""

    def paintEvent(self, event):
        if T.app_theme != "industrial":
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = T.P["ink"]
        paint_matrix_text(painter, self.text(),
                          QRectF(0, 0, self.width(), self.height() - 25),
                          color, max_pitch=6.3, width_ratio=0.96,
                          height_ratio=0.9, dot_ratio=0.86)
        painter.setPen(QPen(QColor(T.P["glass_soft_edge"]), 1.0))
        painter.drawLine(3, self.height() - 22, self.width() - 3,
                         self.height() - 22)
        font = QFont("Consolas", 7)
        font.setLetterSpacing(QFont.AbsoluteSpacing, .8)
        painter.setFont(font)
        painter.setPen(QColor(T.P["ink3"]))
        painter.drawText(QRectF(4, self.height() - 19, self.width() - 8, 9),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         "CAAC GROUND STATION")
        painter.drawText(QRectF(4, self.height() - 10, self.width() - 8, 9),
                         Qt.AlignLeft | Qt.AlignVCenter, "EXAMINATION")
