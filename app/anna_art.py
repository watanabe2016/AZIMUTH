# -*- coding: utf-8 -*-
"""八奈见 V2 主题页面横幅。"""

from PySide6.QtCore import Qt, QPointF, QRect, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget, QSizePolicy

import theme as T


class AnnaHeaderArt(QWidget):
    """用 V2 主题原图、人物素材和可更新的页面文字绘制横幅。"""

    _PAGES = {
        "question": ("02", "真题题库"),
        "timer": ("03", "限时模拟"),
        "teaching": ("04", "地面站教学"),
        "settings": ("05", "设置"),
    }
    _REFERENCE_HEIGHTS = {
        "light": {"question": 210, "timer": 203,
                  "teaching": 220, "settings": 195},
        "dark": {"question": 203, "timer": 200,
                 "teaching": 203, "settings": 186},
    }

    def __init__(self, title, subtitle, parent=None, page_key=None):
        super().__init__(parent)
        self.page_key = page_key or "question"
        self.chapter, self.page_title = self._PAGES[self.page_key]
        self.subtitle = str(subtitle or "").rstrip("。")
        self._stats_line = ""
        self._sky = {}
        self._references = {}
        self._character = QPixmap(T.asset_path(
            "anna_theme", "assets", "characters", "anna-yanami-dynamic.png"))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(210)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.refresh_art()

    def sizeHint(self):
        return QSize(960, 210)

    def set_stats_line(self, text):
        text = str(text or "")
        if text != self._stats_line:
            self._stats_line = text
            self.update()

    def refresh_art(self):
        if T.app_theme != "anna":
            self.update()
            return
        mode = "dark" if T.mode == "dark" else "light"
        if mode not in self._references:
            self._references[mode] = QPixmap(T.asset_path(
                "anna_theme", "assets", "reference", mode,
                f"{self.page_key}.png"))
        if mode not in self._sky:
            self._sky[mode] = QPixmap(T.asset_path(
                "anna_theme", "assets", "backgrounds", f"sky-{mode}.png"))
        self._sync_reference_height()
        self.update()

    def _sync_reference_height(self):
        mode = "dark" if T.mode == "dark" else "light"
        art_height = self._REFERENCE_HEIGHTS[mode][self.page_key]
        desired = max(110, round(max(1, self.width()) * art_height / 1358))
        if self.page_key == "timer" and self.parentWidget() is not None:
            desired = min(desired, max(90, self.parentWidget().height() // 4))
        if not self._references.get(mode, QPixmap()).isNull():
            if desired != self.height():
                self.setFixedHeight(desired)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if T.app_theme == "anna":
            self._sync_reference_height()

    def _paint_reference(self, painter, mode):
        reference = self._references.get(mode)
        if reference is None or reference.isNull():
            return False
        art_height = self._REFERENCE_HEIGHTS[mode][self.page_key]
        painter.drawPixmap(QRectF(self.rect()), reference,
                           QRectF(228, 50, 1358, art_height))
        if self.page_key == "question":
            # 题量与地区数由真实题库提供，覆盖渲染图中的示例数字。
            painter.save()
            painter.scale(self.width() / 1358.0, self.height() / art_height)
            light = mode == "light"
            left = 224 if light else 180
            # 深色横幅的参考图从原图第50像素开始裁切，统计文字在裁切后的
            # 实际位置比浅色版更靠上，覆盖区按屏幕坐标提前，避免旧示例透出。
            top = 132 if light else 108
            painter.fillRect(QRectF(left, top, 344, 31),
                             QColor("#FFFFFF" if light else "#F8FBFF"))
            painter.setFont(self._font(13, QFont.DemiBold))
            painter.setPen(QColor("#566D96" if light else "#243A62"))
            painter.drawText(QRectF(left + 2, top + 1, 340, 27),
                             Qt.AlignLeft | Qt.AlignVCenter,
                             self._stats_line or self.subtitle)
            painter.restore()
        return True

    @staticmethod
    def _draw_cover(painter, pixmap, target, alignment=Qt.AlignCenter):
        if pixmap.isNull() or target.width() <= 0 or target.height() <= 0:
            return
        scaled = pixmap.scaled(target.size().toSize(),
                               Qt.KeepAspectRatioByExpanding,
                               Qt.SmoothTransformation)
        extra_x = max(0, scaled.width() - round(target.width()))
        extra_y = max(0, scaled.height() - round(target.height()))
        source_x = extra_x if alignment & Qt.AlignRight else extra_x // 2
        source_y = extra_y if alignment & Qt.AlignBottom else extra_y // 2
        source_rect = QRect(source_x, source_y,
                            round(target.width()), round(target.height()))
        painter.drawPixmap(target.toRect(), scaled, source_rect)

    def _font(self, size, weight=QFont.DemiBold):
        font = QFont(T.pick_font(), max(9, round(size)))
        font.setWeight(weight)
        return font

    def _draw_editorial_motifs(self, painter, scale):
        """在横幅留白处绘制八奈见主题的几何点缀。"""
        width, height = self.width(), self.height()
        dark = T.mode == "dark"
        blue = QColor("#67AFFF" if dark else "#1687F8")
        yellow = QColor("#FFE45B" if dark else "#FFE83F")
        pink = QColor("#FF6C9A" if dark else "#F93F86")

        def dots(x, y, columns, rows, color):
            painter.save()
            painter.setPen(Qt.NoPen)
            step = 7 * scale
            diameter = max(1.3, 2.0 * scale)
            for row in range(rows):
                for column in range(columns):
                    if (row + column) % 5 == 4:
                        continue
                    dot = QColor(color)
                    dot.setAlpha(max(36, 178 - row * 25 - column * 13))
                    painter.setBrush(dot)
                    painter.drawEllipse(QRectF(x + column * step,
                                               y + row * step,
                                               diameter, diameter))
            painter.restore()

        def triangle(x, y, size, color, rotation=0, outline=False):
            painter.save()
            painter.translate(x, y)
            painter.rotate(rotation)
            path = QPainterPath()
            path.moveTo(0, -size * 0.58)
            path.lineTo(size * 0.54, size * 0.42)
            path.lineTo(-size * 0.54, size * 0.42)
            path.closeSubpath()
            if outline:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(color, max(1.5, 2.1 * scale),
                                    Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            else:
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
            painter.drawPath(path)
            painter.restore()

        # 点阵留在标题与人物之间的空隙，避免压住文字和人物面部。
        dots(width * 0.405, height * 0.035, 7, 4, blue)
        dots(width * 0.835, height * 0.765, 6, 4, blue)
        triangle(width * 0.455, height * 0.19, 22 * scale,
                 yellow, rotation=12, outline=True)
        triangle(width * 0.965, height * 0.77, 18 * scale,
                 yellow, rotation=18)
        triangle(width * 0.755, height * 0.87, 13 * scale,
                 pink, rotation=-8)
        triangle(width * 0.49, height * 0.89, 17 * scale,
                 blue, rotation=21)

        painter.save()
        painter.setPen(QPen(QColor(blue.red(), blue.green(), blue.blue(), 210),
                            max(1.5, 2.2 * scale), Qt.SolidLine,
                            Qt.RoundCap, Qt.RoundJoin))
        painter.drawLine(QPointF(width * 0.715, height * 0.18),
                         QPointF(width * 0.735, height * 0.09))
        painter.drawLine(QPointF(width * 0.735, height * 0.18),
                         QPointF(width * 0.755, height * 0.09))
        painter.restore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        mode = "dark" if T.mode == "dark" else "light"
        if self._paint_reference(painter, mode):
            painter.end()
            return
        painter.fillRect(self.rect(), QColor(
            "#101827" if T.mode == "dark" else "#FFFCF5"))

        scale = min(1.0, self.width() / 1358.0)
        sky = self._sky.get("dark" if T.mode == "dark" else "light")
        if not sky:
            self.refresh_art()
            sky = self._sky.get("dark" if T.mode == "dark" else "light")
        if sky is not None and not sky.isNull():
            sky_rect = QRectF(self.width() * 0.30, 0,
                              self.width() * 0.70, self.height())
            clip = QPainterPath()
            clip.moveTo(self.width() * 0.43, 0)
            clip.lineTo(self.width(), 0)
            clip.lineTo(self.width(), self.height())
            clip.lineTo(self.width() * 0.30, self.height())
            clip.closeSubpath()
            painter.save()
            painter.setClipPath(clip)
            self._draw_cover(painter, sky, sky_rect, Qt.AlignRight | Qt.AlignBottom)
            painter.restore()

        if not self._character.isNull():
            art_height = max(260, round(self.height() * 2.12))
            art_width = round(art_height * self._character.width()
                              / max(1, self._character.height()))
            x = round(self.width() * 0.49)
            y = round(self.height() * 0.04)
            painter.drawPixmap(QRectF(x, y, art_width, art_height),
                               self._character,
                               QRectF(self._character.rect()))

        self._draw_editorial_motifs(painter, scale)

        light = T.mode != "dark"
        ink = QColor("#102B5D" if light else "#F3F5FB")
        muted = QColor("#536B91" if light else "#B4C0D5")
        blue = QColor("#1769E8" if light else "#5FA1FF")
        coral = QColor("#F13F78" if light else "#FF789C")

        chapter_font_size = {"question": 105, "timer": 82,
                             "teaching": 98, "settings": 68}[self.page_key]
        title_font_size = {"question": 65, "timer": 61,
                           "teaching": 57, "settings": 53}[self.page_key]
        number_x = 34 * scale
        number_y = 7 * scale
        number_w = {"question": 150, "timer": 130,
                    "teaching": 150, "settings": 112}[self.page_key] * scale
        number_rect = QRectF(number_x, number_y, number_w, 142 * scale)
        painter.setFont(self._font(chapter_font_size * scale, QFont.Black))
        painter.setPen(blue)
        painter.drawText(number_rect, Qt.AlignLeft | Qt.AlignVCenter, self.chapter)

        if self.page_key == "question":
            title_x = 202 * scale
            title_y = 25 * scale
            title_h = 90 * scale
            subtitle_y = 121 * scale
            accent_x = 200 * scale
            accent_y = 163 * scale
        elif self.page_key == "timer":
            title_x = 197 * scale
            title_y = 21 * scale
            title_h = 86 * scale
            subtitle_y = 120 * scale
            accent_x = 38 * scale
            accent_y = 164 * scale
        elif self.page_key == "teaching":
            title_x = 214 * scale
            title_y = 24 * scale
            title_h = 87 * scale
            subtitle_y = 120 * scale
            accent_x = 38 * scale
            accent_y = 163 * scale
        else:
            title_x = 158 * scale
            title_y = 23 * scale
            title_h = 75 * scale
            subtitle_y = 111 * scale
            accent_x = 38 * scale
            accent_y = 108 * scale

        title_rect = QRectF(title_x, title_y,
                            max(260 * scale, self.width() * 0.48), title_h)
        painter.setFont(self._font(title_font_size * scale, QFont.Black))
        painter.setPen(ink)
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         self.page_title)

        if self.page_key == "timer":
            painter.setPen(Qt.NoPen)
            painter.setBrush(blue)
            painter.drawRoundedRect(QRectF(171 * scale, 30 * scale,
                                           3 * scale, 106 * scale),
                                    1.5 * scale, 1.5 * scale)

        painter.setPen(Qt.NoPen)
        painter.setBrush(coral)
        painter.drawRoundedRect(QRectF(accent_x, accent_y,
                                       34 * scale, 6 * scale),
                                3 * scale, 3 * scale)
        if self.page_key in {"teaching", "settings"}:
            painter.save()
            painter.translate((accent_x + 52 * scale), accent_y - 6 * scale)
            painter.rotate(-48)
            painter.setBrush(QColor("#56A8FF"))
            painter.drawRoundedRect(QRectF(0, 0, 9 * scale,
                                           52 * scale), 1 * scale, 1 * scale)
            painter.restore()

        if self.page_key == "question":
            label = self._stats_line or self.subtitle
            subtitle_font = self._font(16 * scale, QFont.DemiBold)
            subtitle_rect = QRectF(title_x, subtitle_y,
                                   self.width() * 0.46, 32 * scale)
        else:
            label = self.subtitle
            subtitle_font = self._font(18 * scale, QFont.Medium)
            subtitle_rect = QRectF(title_x, subtitle_y,
                                   self.width() * 0.45, 42 * scale)
        painter.setFont(subtitle_font)
        painter.setPen(muted)
        painter.drawText(subtitle_rect, Qt.AlignLeft | Qt.AlignVCenter, label)
        painter.end()
