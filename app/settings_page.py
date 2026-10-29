# -*- coding: utf-8 -*-
"""设置页 —— 主题 / 缩放 / 桌面时钟 / 日志 / 免责声明 / 卸载"""

import os
import datetime
import pathlib
import re
import shutil
import subprocess
import zipfile

from PySide6.QtCore import (Qt, Signal, QPoint, QPointF, QUrl, QPropertyAnimation,
                            QEasingCurve, QTimer, QRect, QObject, QEvent, QThread,
                            QParallelAnimationGroup, QSize)
from PySide6.QtGui import (QColor, QPainter, QPen, QPainterPath, QPixmap,
                           QDesktopServices)
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
                                QApplication, QPushButton, QMessageBox, QFileDialog,
                                QScrollArea, QFrame, QCheckBox, QSlider,
                                 QSizePolicy, QLineEdit, QListWidget,
                                 QListWidgetItem, QButtonGroup, QDoubleSpinBox,
                                 QAbstractSpinBox, QGraphicsOpacityEffect)

import theme as T
from motion_polish import accent, fold, FoldClip, CardFoldController
from industrial_details import IndustrialPageFooter, IndustrialPageMeta
from anna_art import AnnaHeaderArt
from workbench_ui import SegmentRail, PreferencePreview, reveal_layout


import config as cfg
import vision_assist as VA
import progress as PG
import linkgcs as LG
from logger import get_logger, export_logs, app_root, APP_NAME, APP_VER
from ui_kit import (Icons, glass, glass_style, refresh_glass, btn_style,
                    IconButton, icon_label, repaint_icon, attach_shadow,
                    replay_shadow, Combo, card_in, animations_enabled,
                    prepare_card_in, cancel_card_in, ToggleSwitch)
from motion_runtime import motion_page

log = get_logger()


class VisionApiCheckWorker(QThread):
    """在后台验证视觉 API，避免网络等待阻塞设置页。"""

    completed = Signal(object)

    def run(self):
        self.completed.emit(VA.check_api_connection())


class CompactPriceSpinBox(QDoubleSpinBox):
    """显示紧凑单价，同时保留精细的小数编辑精度。"""

    def textFromValue(self, value):
        text = f"{value:.{self.decimals()}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text


def _qq_protocol_executable():
    """读取 QQ 协议处理器；旧版本路径失效时改用同安装目录的新版本。"""
    try:
        import winreg
    except ImportError:
        return None

    command = ""
    for protocol in (r"mqqapi\shell\open\command",
                     r"tencent\shell\open\command"):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, protocol) as key:
                command, _kind = winreg.QueryValueEx(key, None)
                if command:
                    break
        except OSError:
            continue
    if not command:
        return None

    match = re.match(r'\s*"([^"]+\.exe)"', command, re.IGNORECASE)
    if not match:
        match = re.match(r"\s*(.+?\.exe)(?:\s|$)", command, re.IGNORECASE)
    if not match:
        return None

    hinted = pathlib.Path(match.group(1))
    if hinted.is_file():
        return hinted

    versions_dir = next((parent for parent in hinted.parents
                         if parent.name.lower() == "versions"), None)
    if versions_dir and versions_dir.is_dir():
        candidates = list(versions_dir.glob("*/resources/app/Timwp.exe"))
        if candidates:
            def version_key(path):
                version = path.parents[2].name
                return tuple(int(part) for part in re.findall(r"\d+", version))
            return max(candidates, key=version_key)
        stable = versions_dir.parent / "QQ.exe"
        if stable.is_file():
            return stable
    return None


def _qq_client_executable():
    """从协议记录定位当前 QQ 安装，并优先返回稳定启动器。"""
    handler = _qq_protocol_executable()
    if handler is None:
        return None
    versions_dir = next((parent for parent in handler.parents
                         if parent.name.lower() == "versions"), None)
    if versions_dir is not None:
        launcher = versions_dir.parent / "QQ.exe"
        if launcher.is_file():
            return launcher
    return handler

APP_TITLE = "AZIMUTH"
DISCLAIMER = (
    "免责声明：本软件本身完全免费，不收取任何费用。使用 API 解题时产生的费用由 API 服务商收取，"
    "与本软件作者无关。请自行确认 API 服务商的计费规则并承担相应费用。"
)


class ChiyoSettingsHeader(QWidget):
    """设置页页头的文具漫画分镜，只显示素材包中的页眉场景。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(250)
        self.setFixedHeight(104)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._sheets = {
            mode: QPixmap(T.asset_path(
                "chiyo_pack", f"settings_decor_{mode}.png"))
            for mode in ("light", "dark")
        }

    def paintEvent(self, event):
        if T.app_theme != "chiyo":
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        dark = T.mode == "dark"
        edge = QColor("#E8E1D9" if dark else "#29272B")
        paper = QColor("#292B33" if dark else "#F7F4ED")
        panel = QPainterPath()
        panel.moveTo(rect.left() + 9, rect.top())
        panel.lineTo(rect.right() - 13, rect.top())
        panel.lineTo(rect.right(), rect.top() + 10)
        panel.lineTo(rect.right() - 2, rect.bottom() - 8)
        panel.lineTo(rect.left() + 3, rect.bottom() - 2)
        panel.lineTo(rect.left(), rect.top() + 9)
        panel.closeSubpath()
        painter.fillPath(panel, paper)

        source = self._sheets["dark" if dark else "light"]
        if not source.isNull():
            band = source.copy(0, 0, source.width(),
                               max(1, round(source.height() * .43)))
            target = rect.adjusted(8, 5, -8, -5)
            scaled = band.scaled(int(target.width()), int(target.height()),
                                 Qt.KeepAspectRatioByExpanding,
                                 Qt.SmoothTransformation)
            crop_x = max(0, (scaled.width() - int(target.width())) // 2)
            source_rect = QRectF(crop_x, 0, target.width(), target.height())
            painter.save()
            painter.setClipPath(panel)
            painter.drawPixmap(target, scaled, source_rect)
            painter.restore()

        painter.setPen(QPen(edge, 2.4))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(panel)
        painter.setPen(QPen(QColor("#EF435B"), 2.0, Qt.SolidLine,
                            Qt.RoundCap))
        painter.drawLine(rect.left() + 15, rect.bottom() - 6,
                         rect.left() + 82, rect.bottom() - 8)
        painter.end()


class SettingsCardDots(QWidget):
    """设置章节卡片外缘的轻量网点装饰。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setGeometry(parent.rect())
        parent.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(watched.rect())
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        if T.app_theme not in ("chiyo", "anna"):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        anna = T.app_theme == "anna"
        color = QColor("#3188F3" if anna else "#F04B60")
        for row in range(5):
            for col in range(8):
                dot = QColor(color)
                dot.setAlpha(max(13 if anna else 16,
                                 (46 if anna else 74) - row * 9 - col * 5))
                painter.setPen(Qt.NoPen)
                painter.setBrush(dot)
                right_offset = 76 if anna else 29
                painter.drawEllipse(QRectF(self.width() - right_offset + col * 3.2,
                                            7 + row * 3.2, 1.6, 1.6))
        painter.end()


class SectionNumberTag(QLabel):
    """带切角的章节编号签。"""

    def paintEvent(self, event):
        if T.app_theme == "anna":
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            tab = QPainterPath()
            tab.moveTo(rect.left(), rect.top())
            tab.lineTo(rect.right() - 8, rect.top())
            tab.lineTo(rect.right(), rect.bottom() - 8)
            tab.lineTo(rect.right() - 7, rect.bottom())
            tab.lineTo(rect.left(), rect.bottom())
            tab.closeSubpath()
            painter.fillPath(tab, QColor("#1769E8" if T.mode == "light"
                                         else "#2878F0"))
            painter.setPen(QPen(QColor(255, 255, 255, 110), 1.0))
            painter.drawLine(rect.left() + 2, rect.top() + 2,
                             rect.right() - 10, rect.top() + 2)
            painter.setPen(QColor("#FFFFFF"))
            painter.setFont(self.font())
            painter.drawText(rect, Qt.AlignCenter, self.text())
            painter.end()
            return
        if T.app_theme != "chiyo":
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        tab = QPainterPath()
        tab.moveTo(rect.left(), rect.top())
        tab.lineTo(rect.right() - 7, rect.top())
        tab.lineTo(rect.right(), rect.bottom() - 7)
        tab.lineTo(rect.right() - 6, rect.bottom())
        tab.lineTo(rect.left(), rect.bottom())
        tab.closeSubpath()
        painter.fillPath(tab, QColor("#E9435B"))
        painter.setPen(QPen(QColor(255, 255, 255, 95), 1.0))
        painter.drawLine(rect.left() + 2, rect.top() + 2,
                         rect.right() - 9, rect.top() + 2)
        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(self.font())
        painter.drawText(rect, Qt.AlignCenter, self.text())
        painter.end()


class SettingsChapterMark(QWidget):
    """每个设置章节的一枚小型校对图章。"""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.title = title
        self.setFixedSize(42, 32)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):
        if T.app_theme != "chiyo":
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        ink = QColor("#EAE4DD" if T.mode == "dark" else "#333036")
        red = QColor("#EF435B")
        grey = QColor("#A59EA0" if T.mode == "dark" else "#98918E")
        painter.setPen(QPen(ink, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)

        if "外观" in self.title:
            for i, color in enumerate((red, ink, grey)):
                painter.setPen(QPen(ink, 1.0))
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF(2 + i * 12, 7 + (i % 2) * 3,
                                                17, 16), 1.5, 1.5)
        elif "应用主题" in self.title:
            painter.setPen(QPen(red, 1.6))
            painter.setBrush(Qt.NoBrush)
            center = QRectF(17, 11, 8, 8)
            for x, y in ((21, 5), (27, 11), (21, 17), (15, 11)):
                painter.drawEllipse(QRectF(x - 3, y - 3, 6, 6))
            painter.setBrush(red)
            painter.drawEllipse(center)
        elif "题卡" in self.title:
            for offset, color in ((0, grey), (4, red), (8, ink)):
                painter.setPen(QPen(color, 1.3))
                painter.drawRect(QRectF(4 + offset, 4 + offset, 27, 21))
            painter.setPen(QPen(red, 1.5))
            painter.drawLine(12, 28, 31, 28)
        elif "音效" in self.title:
            painter.setPen(QPen(ink, 1.7))
            speaker = QPainterPath()
            speaker.moveTo(5, 12)
            speaker.lineTo(11, 12)
            speaker.lineTo(19, 7)
            speaker.lineTo(19, 25)
            speaker.lineTo(11, 20)
            speaker.lineTo(5, 20)
            speaker.closeSubpath()
            painter.drawPath(speaker)
            painter.setPen(QPen(red, 2.0, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(QRectF(17, 9, 15, 15), -55 * 16, 110 * 16)
            painter.drawArc(QRectF(20, 5, 19, 23), -55 * 16, 110 * 16)
        elif "倒计时" in self.title:
            painter.setPen(QPen(ink, 1.6))
            painter.drawEllipse(QRectF(7, 3, 27, 27))
            painter.setPen(QPen(red, 2.0, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(20, 16, 20, 8)
            painter.drawLine(20, 16, 27, 19)
            painter.setPen(QPen(ink, 1.2))
            painter.drawLine(16, 2, 24, 2)
        elif "数据" in self.title or "日志" in self.title:
            painter.setPen(QPen(ink, 1.4))
            for offset in (0, 4, 8):
                sheet = QPainterPath()
                sheet.moveTo(5 + offset, 7 + offset)
                sheet.lineTo(29, 7 + offset)
                sheet.lineTo(35, 13 + offset)
                sheet.lineTo(35, 27)
                sheet.lineTo(5 + offset, 27)
                sheet.closeSubpath()
                painter.drawPath(sheet)
            painter.setPen(QPen(red, 2.0))
            painter.drawLine(11, 18, 26, 18)
        elif "阴影" in self.title or "窗口" in self.title:
            painter.setPen(QPen(grey, 1.3))
            painter.drawRect(QRectF(6, 5, 25, 20))
            painter.setPen(QPen(ink, 1.7))
            painter.drawRect(QRectF(3, 3, 25, 20))
            painter.setPen(QPen(red, 2.0))
            painter.drawLine(7, 27, 34, 27)
        else:
            painter.setPen(QPen(ink, 1.5))
            painter.drawRect(QRectF(7, 4, 25, 24))
            painter.setPen(QPen(red, 2.0))
            painter.drawLine(12, 11, 27, 11)
            painter.drawLine(12, 17, 24, 17)
        painter.end()


class _SwitchLabelFilter(QObject):
    """让设置标题的整块点击区稳定切换对应开关。"""

    def __init__(self, switch, parent=None):
        super().__init__(parent)
        self.switch = switch

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.MouseButtonPress and
                event.button() == Qt.LeftButton and self.switch.isEnabled()):
            self.switch.click()
            return True
        return False


class ThemePreviewButton(QPushButton):
    """绘制真实可点击的主题结构预览，不启动或复制应用页面。"""

    def __init__(self, key, label, parent=None):
        super().__init__(parent)
        self.theme_key = key
        self.setAccessibleName(label)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(138, 116)
        self.setStyleSheet(
            "background:transparent;border:0;padding:0;min-height:116px;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        accent = QColor("#E9435B" if T.app_theme == "chiyo" else T.C_BLUE)
        outer = QRectF(5, 4, self.width() - 10, self.height() - 28)
        preview = outer.adjusted(7, 6, -7, -18)
        painter.setPen(QPen(QColor(accent if self.isChecked()
                                    else T.P["glass_soft_edge"]),
                            2 if self.isChecked() else 1))
        painter.setBrush(QColor(T.P["glass"]))
        painter.drawRoundedRect(outer, 11, 11)

        def draw_sample(rect, dark):
            bg = QColor("#101A29" if dark else "#F7FAFE")
            nav = QColor("#1B2B42" if dark else "#E6EDF6")
            panel = QColor("#26354A" if dark else "#FFFFFF")
            line = QColor("#3A4A61" if dark else "#D4DFEC")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(rect, 7, 7)
            nav_rect = QRectF(rect.left(), rect.top(), rect.width() * .25,
                              rect.height())
            painter.setBrush(nav)
            painter.drawRoundedRect(nav_rect, 7, 7)
            painter.drawRect(QRectF(nav_rect.right() - 7, nav_rect.top(), 7,
                                    nav_rect.height()))
            painter.setBrush(QColor(T.C_BLUE))
            painter.drawRoundedRect(QRectF(nav_rect.left() + 6,
                nav_rect.top() + 7, nav_rect.width() - 12, 5), 2, 2)
            for index in range(3):
                painter.setBrush(line)
                painter.drawRoundedRect(QRectF(nav_rect.left() + 6,
                    nav_rect.top() + 19 + index * 9,
                    nav_rect.width() - 14, 3), 1, 1)
            content = QRectF(nav_rect.right() + 6, rect.top() + 7,
                             rect.width() - nav_rect.width() - 13,
                             rect.height() - 14)
            painter.setBrush(panel)
            painter.drawRoundedRect(content, 5, 5)
            painter.setBrush(line)
            painter.drawRoundedRect(QRectF(content.left() + 7, content.top() + 7,
                content.width() * .48, 4), 2, 2)
            painter.drawRoundedRect(QRectF(content.left() + 7, content.top() + 16,
                content.width() * .74, 3), 1, 1)

        if self.theme_key == "system":
            left = QRectF(preview.left(), preview.top(), preview.width() / 2,
                          preview.height())
            right = QRectF(left.right(), preview.top(), preview.width() / 2,
                           preview.height())
            draw_sample(left, False)
            draw_sample(right, True)
        else:
            draw_sample(preview, self.theme_key == "dark")

        if self.isChecked():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(QRectF(outer.right() - 21, outer.top() - 2, 19, 19))
            painter.setPen(QPen(QColor("#FFFFFF"), 2))
            painter.drawLine(outer.right() - 16, outer.top() + 7,
                             outer.right() - 12, outer.top() + 11)
            painter.drawLine(outer.right() - 12, outer.top() + 11,
                             outer.right() - 6, outer.top() + 4)
        painter.setPen(QColor(accent if self.isChecked() else T.P["ink2"]))
        painter.drawText(QRectF(0, self.height() - 23, self.width(), 20),
                         Qt.AlignCenter, {"light": "浅色", "dark": "深色",
                                          "system": "跟随系统"}[self.theme_key])
        painter.end()


class AppThemePreviewButton(QPushButton):
    """应用主题小样：跟随实际浅深外观，选中状态独立于外观偏好。"""

    def __init__(self, key, label, parent=None):
        super().__init__(parent)
        self.theme_key = key
        self.preview_label = label
        self.setAccessibleName(label)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(180, 144)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            "background:transparent;border:0;padding:0;min-height:144px;")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dark = T.mode == "dark"
        chiyo = self.theme_key == "chiyo"
        industrial = self.theme_key == "industrial"
        anna = self.theme_key == "anna"
        custom = self.theme_key.startswith("skin:")
        colors = {}
        if anna:
            colors = ({"accent": "#315BC4", "ink": "#F3F5FB",
                       "canvas": "#101827", "surface": "#1B2941",
                       "border": "#3C4E6C"} if dark else
                      {"accent": "#315BC4", "ink": "#202D48",
                       "canvas": "#FFFCF5", "surface": "#FFFFFF",
                       "border": "#D6DDEC"})
        if industrial:
            colors = ({"accent": "#FF625C", "ink": "#F3F5F5",
                       "canvas": "#111416", "surface": "#1B2023",
                       "border": "#394145"} if dark else
                      {"accent": "#F1342D", "ink": "#15191C",
                       "canvas": "#EEF0F1", "surface": "#FFFFFF",
                       "border": "#CFD4D7"})
        if custom:
            try:
                from skin_manager import palette as skin_palette
                colors = skin_palette(self.theme_key.split(":", 1)[1], T.mode)
            except (ImportError, OSError, ValueError):
                colors = {}
        accent = QColor(colors.get("accent", T.P["accent_fg"]))
        ink = QColor(colors.get("ink", "#EEE8E0" if dark else "#29272C"))
        paper = QColor(colors.get("canvas", "#252730" if dark else "#FFFDF8"))
        surface = QColor(colors.get("surface", "#28364A" if dark else "#FFFFFF"))
        muted = QColor(colors.get("border", "#625D66" if dark else "#D4D9E1"))
        outer = QRectF(5, 4 + int(self.isDown()), self.width() - 10, self.height() - 24)
        p.setPen(QPen(accent if self.isChecked() or self.hasFocus()
                      else QColor(T.P["glass_soft_edge"]),
                      2 if self.isChecked() or self.hasFocus() else 1))
        p.setBrush(paper)
        p.drawRoundedRect(outer, 4 if industrial else 8,
                          4 if industrial else 8)
        screen = outer.adjusted(10, 10, -10, -40)
        p.setPen(QPen(ink if chiyo else muted, 1))
        p.setBrush(paper if custom or industrial or anna else
                   QColor("#1A1C22" if dark else "#F3F6FA"))
        p.drawRoundedRect(screen, 2 if industrial else 4,
                          2 if industrial else 4)
        nav = QRectF(screen.left() + 7, screen.top() + 7,
                     screen.width() * .23, screen.height() - 14)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(accent if industrial or anna else
                          "#EF425C" if chiyo else "#4385EF"))
        p.drawRoundedRect(QRectF(nav.left(), nav.top(), nav.width(), 5), 2, 2)
        p.setBrush(muted)
        for index in range(3):
            p.drawRoundedRect(QRectF(nav.left(), nav.top() + 15 + index * 9,
                                    nav.width() * .82, 3), 1, 1)
        page = QRectF(nav.right() + 10, screen.top() + 7,
                      screen.right() - nav.right() - 17, screen.height() - 14)
        if chiyo:
            p.setPen(QPen(muted, 1))
            p.setBrush(paper)
            p.drawRect(page.translated(-3, 3))
        p.setPen(QPen(ink if chiyo else muted, 1))
        p.setBrush(paper if chiyo else surface)
        p.drawRoundedRect(page, 2 if chiyo else 4, 2 if chiyo else 4)
        p.setPen(Qt.NoPen)
        p.setBrush(muted)
        for y, width in ((8, .48), (19, .76), (39, .68), (46, .40)):
            p.drawRoundedRect(QRectF(page.left() + 7, page.top() + y,
                                    max(7, page.width() * width - 10), 3), 1, 1)
        if chiyo:
            p.setBrush(QColor("#EF425C"))
            p.drawRect(QRectF(page.left() + 7, page.top() + 30,
                             page.width() * .60, 1.5))
            for row in range(7):
                for col in range(8):
                    dot = QColor("#EF425C")
                    dot.setAlpha(max(12, 160 - row * 16 - col * 16))
                    p.setBrush(dot)
                    p.drawEllipse(QRectF(page.right() - 4 - col * 3,
                                         page.top() + 4 + row * 3, 1.3, 1.3))
        elif industrial:
            p.setPen(QPen(accent, 2))
            p.drawLine(page.left(), page.top() + 2,
                       page.left(), page.bottom() - 2)
            p.setPen(QPen(muted, 1))
            for index in range(27):
                tick_x = page.left() + 7 + index * max(
                    1, (page.width() - 15) / 26)
                tick_height = 7 if index % 5 == 0 else 3
                p.drawLine(QPointF(tick_x, page.bottom() - 5),
                           QPointF(tick_x, page.bottom() - 5 - tick_height))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent)
            p.drawEllipse(QPointF(page.left() + page.width() * .46,
                                  page.bottom() - 13), 2, 2)
        p.setPen(accent if self.isChecked() else QColor(T.P["ink"]))
        font = p.font()
        font.setPixelSize(13)
        font.setBold(self.isChecked())
        p.setFont(font)
        p.drawText(QRectF(outer.left() + 3, outer.bottom() - 32,
                         outer.width() - 6, 23),
                   Qt.AlignCenter | Qt.TextWordWrap, self.preview_label)
        if self.isChecked():
            badge = QRectF(outer.right() - 74, outer.bottom() - 7, 76, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(accent)
            p.drawRoundedRect(badge, 10, 10)
            font.setPixelSize(11)
            p.setFont(font)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(badge, Qt.AlignCenter, "当前主题")
        p.end()


@motion_page
class SettingsPage(QWidget):
    """设置与维护。通过信号把主题/缩放变更交给主窗口执行。"""

    theme_request = Signal(str, object)      # 主题 pref 和触发按钮
    app_theme_request = Signal(str, object)
    wallpaper_request = Signal(str, int, int)
    card_layout_request = Signal(str)
    clock_skin_request = Signal(str)
    clock_toggle = Signal(bool)
    clock_scale_request = Signal(float)
    shadow_request = Signal(bool, float)   # 窗口阴影：开关 + 强度(0.0~1.5)
    sound_settings_changed = Signal(bool, bool, bool, int)
    sound_preview = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards = []
        self._theme_btns = {}
        self._app_theme_btns = {}
        self._custom_theme_layout = None
        self._wallpaper_save_timer = QTimer(self)
        self._wallpaper_save_timer.setSingleShot(True)
        self._wallpaper_save_timer.timeout.connect(cfg.save)
        self._sound_lists = {}
        self._sound_actions = []
        self._sound_preview_btns = {}
        self._sound_mode_btns = {}
        self._vision_api_worker = None
        self._handles_theme_stylesheet = True
        self._build()
        for slider_name, label_name in (("wallpaper_opacity_slider", "wallpaper_opacity_label"),
                                       ("wallpaper_blur_slider", "wallpaper_blur_label"),
                                       ("sound_slider", "sound_level"), ("cslider", "cscale_lb")):
            slider, label = getattr(self, slider_name, None), getattr(self, label_name, None)
            if slider is not None and label is not None:
                slider.sliderReleased.connect(lambda anchor=label: accent(anchor))

    # --------------------------------------------------- 构造
    def _build(self):
        scroll = QScrollArea(self)
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}"
                             "QScrollArea>QWidget>QWidget{background:transparent;}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        scroll.setWidget(inner)

        page_layout = QVBoxLayout(inner)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        self.anna_header_art = AnnaHeaderArt(
            "设置", "调整外观，让训练更顺手。", self, page_key="settings")
        self.anna_header_art.setVisible(T.app_theme == "anna")
        page_layout.addWidget(self.anna_header_art)

        content = QWidget(inner)
        content.setStyleSheet("background:transparent;")
        root = QVBoxLayout(content)
        root.setContentsMargins(30, 22, 30, 22)
        root.setSpacing(13)
        root.setAlignment(Qt.AlignTop)
        self._settings_root_layout = root
        self._settings_root_side_margin = 30
        page_layout.addWidget(content, 1)

        self.heading_widget = QWidget(self)
        self.heading_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        head = QHBoxLayout(self.heading_widget)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(18)
        head_copy = QVBoxLayout()
        head_copy.setSpacing(2)
        t = QLabel("设置")
        t.setObjectName("H1")
        self.page_title = t
        s = QLabel("调整外观，让训练更顺手。")
        s.setObjectName("Dim")
        self.page_subtitle = s
        head_copy.addWidget(t)
        head_copy.addWidget(s)
        head_copy.addStretch(1)
        head.addLayout(head_copy, 1)
        self.industrial_header = IndustrialPageMeta(
            "SETTINGS", "GOOD OPERATORS", self)
        self.industrial_header.setVisible(T.app_theme == "industrial")
        head.addWidget(self.industrial_header, 0, Qt.AlignVCenter)
        self.chiyo_header_scene = ChiyoSettingsHeader()
        from chiyo_art import ChiyoMarginNote
        self.chiyo_header_note = ChiyoMarginNote(
            "梦野咲子老师的\n工作台", self.chiyo_header_scene,
            width=192, height=82)
        self.chiyo_header_note.move(12, 11)
        self.chiyo_header_scene.setVisible(T.app_theme == "chiyo")
        head.addWidget(self.chiyo_header_scene, 2)
        self.heading_widget.setVisible(T.app_theme != "anna")
        root.addWidget(self.heading_widget)

        # ---------- 外观主题 ----------
        c1, l1 = self._card("外观",
                            "选择合适的界面风格与显示效果。")
        self.theme_host = QWidget()
        self.theme_host.setStyleSheet("background:transparent;")
        row = QHBoxLayout(self.theme_host)
        row.setContentsMargins(5, 5, 5, 11)
        row.setSpacing(18)
        theme_lead = QVBoxLayout()
        theme_lead.setSpacing(4)
        theme_label = QLabel("显示外观")
        self.theme_label = theme_label
        theme_label.setStyleSheet(f"font-size:15px;font-weight:750;color:{T.P['ink']};background:transparent;")
        theme_desc = QLabel("浅色、深色或跟随系统；与应用主题分别保存。")
        theme_desc.setWordWrap(True)
        self.theme_desc = theme_desc
        theme_desc.setStyleSheet(f"font-size:13px;color:{T.P['ink2']};background:transparent;")
        theme_lead.addWidget(theme_label)
        theme_lead.addWidget(theme_desc)
        theme_lead.addStretch()
        row.addLayout(theme_lead, 1)
        theme_choices = QHBoxLayout()
        theme_choices.setSpacing(14)
        for key, name in (("light", "浅色"), ("dark", "深色"),
                          ("system", "跟随系统")):
            b = ThemePreviewButton(key, name)
            b.clicked.connect(lambda _=False, k=key, btn=b: self.theme_request.emit(k, btn))
            self._theme_btns[key] = b
            theme_choices.addWidget(b, 1)
        row.addLayout(theme_choices, 3)
        self.theme_indicator = QFrame(self.theme_host)
        self.theme_indicator.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        indicator_bg = ("rgba(49,91,196,0.10)" if T.mode == "light"
                        else "rgba(95,161,255,0.14)") if T.app_theme == "anna" \
            else T.P["accent_soft"]
        indicator_edge = ("#315BC4" if T.mode == "light" else "#5FA1FF") \
            if T.app_theme == "anna" else T.P["accent_edge"]
        self.theme_indicator.setStyleSheet(
            f"QFrame {{ background:{indicator_bg};"
            f"border:1px solid {indicator_edge}; border-radius:14px; }}")
        self.theme_indicator.lower()
        self.theme_host.installEventFilter(self)
        self._theme_motion = None
        self._theme_pref = cfg.get("theme", "system")
        l1.addWidget(self.theme_host)
        root.addWidget(c1)

        # ---------- 应用主题 ----------
        c_theme, l_theme = self._card(
            "应用主题", "主题会随浅色或深色外观自动适配；应用主题与外观模式独立保存。")
        app_theme_row = QGridLayout()
        self.app_theme_grid = app_theme_row
        app_theme_row.setSpacing(12)
        app_theme_label = QLabel("主题选择")
        self.app_theme_label = app_theme_label
        app_theme_label.setStyleSheet(
            f"font-size:14px;font-weight:700;color:{T.P['ink']};background:transparent;")
        app_theme_row.addWidget(app_theme_label, 0, 0)
        from skin_manager import builtin_theme_available
        for key, name in (("default", "默认主题"),
                          ("chiyo", "千代·月刊编辑部"),
                          ("industrial", "工业极简·调频训练台")):
            button = AppThemePreviewButton(key, name)
            button.clicked.connect(lambda _=False, value=key, source=button:
                                   self._on_app_theme(value, source))
            self._app_theme_btns[key] = button
            if key == "chiyo":
                button.setVisible(builtin_theme_available("chiyo"))
            app_theme_row.addWidget(button, 0, len(self._app_theme_btns))
        button = AppThemePreviewButton("anna", "败犬女主·八奈见")
        button.clicked.connect(
            lambda _=False, value="anna", source=button:
            self._on_app_theme(value, source))
        self._app_theme_btns["anna"] = button
        button.setVisible(builtin_theme_available("anna"))
        app_theme_row.addWidget(button, 0, len(self._app_theme_btns))
        self.set_app_theme_state(cfg.get("app_theme", "default"))
        l_theme.addLayout(app_theme_row)

        self.custom_theme_scroll = QScrollArea()
        self.custom_theme_scroll.setWidgetResizable(True)
        self.custom_theme_scroll.setFixedHeight(158)
        self.custom_theme_scroll.setFrameShape(QFrame.NoFrame)
        self.custom_theme_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.custom_theme_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.custom_theme_scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollArea>QWidget>QWidget{background:transparent;}")
        self.custom_theme_host = QWidget()
        self._custom_theme_layout = QHBoxLayout(self.custom_theme_host)
        self._custom_theme_layout.setContentsMargins(4, 3, 8, 8)
        self._custom_theme_layout.setSpacing(10)
        self.custom_theme_scroll.setWidget(self.custom_theme_host)
        self._reload_custom_themes()
        l_theme.addWidget(self.custom_theme_scroll)

        skin_actions = QHBoxLayout()
        skin_actions.addStretch(1)
        self.b_skin_import = IconButton("导入皮肤包", "folder", "default",
                                        font_size=12)
        self.b_skin_template = IconButton("导出设计模板", "save", "default",
                                          font_size=12)
        self.b_skin_import.clicked.connect(self._import_skin)
        self.b_skin_template.clicked.connect(self._export_skin_template)
        skin_actions.addWidget(self.b_skin_import)
        skin_actions.addWidget(self.b_skin_template)
        l_theme.addLayout(skin_actions)
        root.addWidget(c_theme)

        # ---------- 自定义背景 ----------
        c_wallpaper, l_wallpaper = self._card(
            "自定义背景", "上传自己的背景图，并分别调节图片显示比例与高斯模糊。")
        wallpaper_top = QHBoxLayout()
        self.wallpaper_path_label = QLabel()
        self.wallpaper_path_label.setMinimumWidth(120)
        self.wallpaper_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.b_wallpaper_pick = IconButton("选择图片", "folder", "default",
                                           font_size=12)
        self.b_wallpaper_clear = IconButton("移除图片", "trash", "default",
                                            font_size=12)
        self.b_wallpaper_pick.clicked.connect(self._choose_wallpaper)
        self.b_wallpaper_clear.clicked.connect(self._clear_wallpaper)
        wallpaper_top.addWidget(self.wallpaper_path_label, 1)
        wallpaper_top.addWidget(self.b_wallpaper_pick)
        wallpaper_top.addWidget(self.b_wallpaper_clear)
        l_wallpaper.addLayout(wallpaper_top)

        wallpaper_hint = QLabel(
            "建议使用 1920 × 1080 或更大的图片，单张不超过 18 MB。"
            "透明度越高，图片越明显；浅色模式叠白底，深色模式叠黑底。")
        wallpaper_hint.setWordWrap(True)
        wallpaper_hint.setStyleSheet(
            f"font-size:12px;color:{T.P['ink2']};background:transparent;")
        self.wallpaper_hint = wallpaper_hint
        l_wallpaper.addWidget(wallpaper_hint)

        self.wallpaper_opacity_label = QLabel()
        self.wallpaper_opacity_slider = QSlider(Qt.Horizontal)
        self.wallpaper_opacity_slider.setRange(0, 100)
        self.wallpaper_opacity_slider.setValue(int(cfg.get("wallpaper_opacity", 38)))
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("图片透明度"))
        opacity_row.addWidget(self.wallpaper_opacity_slider, 1)
        opacity_row.addWidget(self.wallpaper_opacity_label)
        l_wallpaper.addLayout(opacity_row)

        self.wallpaper_blur_label = QLabel()
        self.wallpaper_blur_slider = QSlider(Qt.Horizontal)
        self.wallpaper_blur_slider.setRange(0, 24)
        self.wallpaper_blur_slider.setValue(int(cfg.get("wallpaper_blur", 0)))
        blur_row = QHBoxLayout()
        blur_row.addWidget(QLabel("高斯模糊"))
        blur_row.addWidget(self.wallpaper_blur_slider, 1)
        blur_row.addWidget(self.wallpaper_blur_label)
        l_wallpaper.addLayout(blur_row)
        self.wallpaper_opacity_slider.valueChanged.connect(
            self._on_wallpaper_settings)
        self.wallpaper_blur_slider.valueChanged.connect(
            self._on_wallpaper_settings)
        self._on_wallpaper_settings(save=False)
        self._sync_wallpaper_label()
        root.addWidget(c_wallpaper)

        # ---------- 题卡版式 ----------
        c_card_layout, l_card_layout = self._card(
            "题卡排版", "原始题卡直接显示 PDF 或图片本身，不重排题目；广东02样式才重新排版。")
        self.cmb_card_layout = Combo(width=254, height=40)
        self.cmb_card_layout.setAccessibleName("题卡排版")
        self.cmb_card_layout.addItem("原始题卡（只显示原图）", "standard")
        self.cmb_card_layout.addItem("广东02题卡样式", "paper")
        self.cmb_card_layout.setCurrentIndex(max(0, self.cmb_card_layout.findData(
            cfg.get("card_layout", "standard"))))
        self.cmb_card_layout.changed.connect(self._on_card_layout)
        card_row = QHBoxLayout()
        self.card_layout_label = QLabel("题卡练习中的显示方式")
        card_row.addWidget(self.card_layout_label)
        card_row.addStretch(1)
        card_row.addWidget(self.cmb_card_layout)
        l_card_layout.addLayout(card_row)
        root.addWidget(c_card_layout)

        # ---------- 音效 ----------
        c11, l11 = self._card("音效", "按钮音效较轻；计时音效只在阶段或结束节点播放。")
        self.chk_sound_master = ToggleSwitch()
        self.chk_sound_master.setAccessibleName("启用音效")
        self.chk_sound_master.setChecked(bool(cfg.get("sound_master", True)))
        self.chk_sound_master.toggled.connect(self._on_sound_changed)
        self._bind_switch_label(c11._title_lab, self.chk_sound_master)
        c11._header_layout.insertWidget(c11._header_layout.count() - 1,
                                        self.chk_sound_master)
        sound_row = QHBoxLayout()
        self.sound_ui_label = QLabel("按钮交互音效")
        sound_row.addWidget(self.sound_ui_label)
        self.chk_sound_ui = QCheckBox("轻触反馈")
        self.chk_sound_ui.setChecked(bool(cfg.get("sound_ui", False)))
        self.chk_sound_ui.toggled.connect(self._on_sound_changed)
        sound_row.addWidget(self.chk_sound_ui)
        self.chk_sound_stage = QCheckBox("计时阶段提示")
        self.chk_sound_stage.setChecked(bool(cfg.get("sound_stage", True)))
        self.chk_sound_stage.toggled.connect(self._on_sound_changed)
        sound_row.addWidget(self.chk_sound_stage)
        sound_row.addSpacing(18)
        self.sound_volume_label = QLabel("音量")
        sound_row.addWidget(self.sound_volume_label)
        self.sound_slider = QSlider(Qt.Horizontal)
        self.sound_slider.setRange(0, 100)
        self.sound_slider.setValue(int(cfg.get("sound_volume", 35)))
        self.sound_slider.valueChanged.connect(self._on_sound_volume_preview)
        self._sound_save_timer = QTimer(self)
        self._sound_save_timer.setSingleShot(True)
        self._sound_save_timer.setInterval(240)
        self._sound_save_timer.timeout.connect(self._on_sound_changed)
        self.sound_level = QLabel(f"{self.sound_slider.value()}%")
        self.b_sound_preview = IconButton("试听", None, "default")
        self.b_sound_preview.clicked.connect(self.sound_preview.emit)
        sound_row.addWidget(self.sound_slider, 1)
        sound_row.addWidget(self.sound_level)
        sound_row.addWidget(self.b_sound_preview)
        l11.addLayout(sound_row)
        library_row = QHBoxLayout()
        library_row.setSpacing(12)
        for kind, title in (("tap", "按键音效库"), ("stage", "计时阶段音乐库")):
            pane = QWidget()
            pane.setStyleSheet("background:transparent;")
            pane_layout = QVBoxLayout(pane)
            pane_layout.setContentsMargins(0, 8, 0, 0)
            pane_layout.setSpacing(6)
            heading = QLabel(title)
            heading.setStyleSheet(
                f"font-size:13px;font-weight:700;color:{T.P['ink']};background:transparent;")
            pane_layout.addWidget(heading)
            modes = QHBoxLayout()
            modes.setSpacing(4)
            mode_group = QButtonGroup(self)
            mode_group.setExclusive(True)
            self._sound_mode_btns[kind] = []
            current_mode = str(cfg.get(f"sound_{kind}_mode", "random"))
            for mode, label in (("random", "随机播放"),
                                ("sequence", "顺序播放"),
                                ("single", "单曲播放")):
                button = QPushButton(label)
                button.setCheckable(True)
                button.setMinimumHeight(27)
                button.setChecked(mode == current_mode)
                button.toggled.connect(
                    lambda checked, k=kind, m=mode:
                    self._on_sound_mode(k, m, checked))
                mode_group.addButton(button)
                self._sound_mode_btns[kind].append(button)
                modes.addWidget(button, 1)
            pane_layout.addLayout(modes)
            listing = QListWidget()
            listing.setAccessibleName(title)
            listing.setMinimumHeight(104)
            listing.setMaximumHeight(118)
            self._sound_lists[kind] = listing
            listing.currentRowChanged.connect(
                lambda _row, k=kind: self._on_sound_selection(k))
            pane_layout.addWidget(listing)
            actions = QHBoxLayout()
            actions.setSpacing(6)
            for caption, icon, callback in (
                    ("导入", "folder", lambda _=False, k=kind: self._import_sound(k)),
                    ("试听", None, lambda _=False, k=kind: self._preview_sound(k)),
                    ("删除", "trash", lambda _=False, k=kind: self._remove_sound(k)),
                    ("恢复内置", "reset", lambda _=False, k=kind: self._restore_sound(k))):
                button = IconButton(caption, icon, "default", font_size=11)
                button.setMinimumHeight(32)
                button.clicked.connect(callback)
                if caption == "试听":
                    button.setProperty("sound_silent", True)
                    self._sound_preview_btns[kind] = button
                actions.addWidget(button)
                self._sound_actions.append(button)
            pane_layout.addLayout(actions)
            library_row.addWidget(pane, 1)
        l11.addLayout(library_row)
        self._sound_state_timer = QTimer(self)
        self._sound_state_timer.setInterval(180)
        self._sound_state_timer.timeout.connect(self._sync_sound_preview_labels)
        self._refresh_sound_lists()
        self._style_sound_modes()
        for widget in (self.chk_sound_ui, self.chk_sound_stage,
                       self.sound_slider, self.b_sound_preview):
            widget.setEnabled(self.chk_sound_master.isChecked())
        root.addWidget(c11)

        # ---------- 窗口阴影 ----------
        c2b, l2b = self._card(
            "窗口阴影",
            "窗口四周的投影。关掉后窗口四角完全透明、与桌面融为一体；\n"
            "强度控制投影浓淡，拉到 0% 等同于关闭。")
        shrow = QHBoxLayout()
        shrow.setSpacing(11)
        self.chk_shadow = ToggleSwitch()
        self.chk_shadow.setAccessibleName("启用窗口阴影")
        self.chk_shadow.setChecked(bool(cfg.get("shadow_on", True)))
        self.chk_shadow.toggled.connect(self._on_shadow_switch)
        self._bind_switch_label(c2b._title_lab, self.chk_shadow)
        c2b._header_layout.insertWidget(c2b._header_layout.count() - 1,
                                        self.chk_shadow)

        self.shadow_lb = QLabel("投影强度")
        self.shadow_lb.setStyleSheet(f"font-size:13px; color:{T.P['ink2']};"
                                     f"background:transparent;")
        shrow.addWidget(self.shadow_lb)

        self.sslider = QSlider(Qt.Horizontal)
        self.sslider.setMinimum(0)
        self.sslider.setMaximum(150)
        self.sslider.setSingleStep(5)
        self.sslider.setPageStep(10)
        self.sslider.setMinimumWidth(150)
        self.sslider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.sslider.setValue(
            int(round(float(cfg.get("shadow_strength", 1.0)) * 100)))
        self.sslider.valueChanged.connect(self._on_shadow_strength)
        shrow.addWidget(self.sslider)

        self.slevel_lb = QLabel("%d%%" % self.sslider.value())
        self.slevel_lb.setFixedWidth(52)
        self.slevel_lb.setStyleSheet(
            f"font-size:13px; font-weight:700; color:{T.P['ink2']};"
            f"background:transparent;")
        shrow.addWidget(self.slevel_lb)

        self.b_shadow_reset = IconButton("复位 100%", "reset", "default",
                                         font_size=12)
        self.b_shadow_reset.setMinimumHeight(34)
        self.b_shadow_reset.clicked.connect(lambda: self.sslider.setValue(100))
        shrow.addWidget(self.b_shadow_reset)
        shrow.addStretch()
        l2b.addLayout(shrow)
        self._sync_shadow_enabled()
        self._sync_clock_enabled()
        root.addWidget(c2b)

        c_motion, _ = self._card(
            "界面动效", "控制页面切换、卡片入场与按钮反馈；关闭时界面立即显示最终状态。")
        self.chk_motion = ToggleSwitch()
        self.chk_motion.setAccessibleName("启用界面动效")
        self.chk_motion.setChecked(bool(cfg.get("animations_on", True)))
        self.chk_motion.toggled.connect(self._on_motion_switch)
        self._bind_switch_label(c_motion._title_lab, self.chk_motion)
        c_motion._header_layout.insertWidget(
            c_motion._header_layout.count() - 1, self.chk_motion)
        root.addWidget(c_motion)

        # ---------- 桌面时钟 ----------
        c3, l3 = self._card("桌面倒计时时钟",
                            "桌面时钟与限时模拟同步；可直接开始、暂停、重置或切换阅题/作答。"
                            "拖动空白处移动，Ctrl+滚轮缩放。导入主题包后，会增加对应的人物选择。")
        crow = QHBoxLayout()
        crow.setSpacing(11)
        self.chk_clock = ToggleSwitch()
        self.chk_clock.setAccessibleName("启用桌面时钟")
        self.chk_clock.setChecked(bool(cfg.get("clock_on", False)))
        self.chk_clock.toggled.connect(self._on_clock_switch)
        self._bind_switch_label(c3._title_lab, self.chk_clock)
        c3._header_layout.insertWidget(c3._header_layout.count() - 1,
                                       self.chk_clock)
        cl = QLabel("时钟大小")
        self.clock_size_label = cl
        cl.setStyleSheet(f"font-size:13px; color:{T.P['ink2']};"
                         f"background:transparent;")
        crow.addWidget(cl)
        self.cslider = QSlider(Qt.Horizontal)
        self.cslider.setMinimum(85)
        self.cslider.setMaximum(220)
        self.cslider.setValue(int(round(float(cfg.get("clock_scale", 1.0)) * 100)))
        self.cslider.setMinimumWidth(150)
        self.cslider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.cslider.valueChanged.connect(self._on_clock_scale)
        crow.addWidget(self.cslider)
        self.cscale_lb = QLabel("%d%%" % self.cslider.value())
        self.cscale_lb.setFixedWidth(52)
        self.cscale_lb.setStyleSheet(
            f"font-size:13px; font-weight:700; color:{T.P['ink2']};"
            f"background:transparent;")
        crow.addWidget(self.cscale_lb)
        crow.addStretch()
        l3.addLayout(crow)
        skin_row = QHBoxLayout()
        skin_label = QLabel("时钟皮肤")
        self.clock_skin_label = skin_label
        skin_label.setStyleSheet(
            f"font-size:13px;color:{T.P['ink2']};background:transparent;")
        skin_row.addWidget(skin_label)
        skin_row.addStretch(1)
        self.cmb_clock_skin = Combo(self)
        self.cmb_clock_skin.setAccessibleName("桌面时钟人物")
        self.cmb_clock_skin.setToolTip("导入主题包后解锁对应人物；人物选择与应用主题独立保存")
        skin_row.addWidget(self.cmb_clock_skin, 1)
        self.cmb_clock_skin.changed.connect(
            lambda: self.clock_skin_request.emit(self.cmb_clock_skin.currentData()))
        self.cmb_clock_skin.changed.connect(lambda: self._refresh_clock_preview())
        l3.addLayout(skin_row)
        clock_info_row = QHBoxLayout()
        self.clock_preview = QLabel()
        self.clock_preview.setFixedSize(128, 154)
        self.clock_preview.setAlignment(Qt.AlignCenter)
        self.clock_preview.setAccessibleName("已选桌面倒计时人物预览")
        self.clock_unlock_hint = QLabel()
        self.clock_unlock_hint.setWordWrap(True)
        self.clock_unlock_hint.setObjectName("Dim")
        clock_info_row.addWidget(self.clock_preview)
        clock_info_row.addWidget(self.clock_unlock_hint, 1)
        clock_import = IconButton("导入主题包", "folder", "default", font_size=12)
        clock_import.clicked.connect(self._import_skin)
        clock_info_row.addWidget(clock_import)
        l3.addLayout(clock_info_row)
        self._reload_clock_skins()
        self._sync_clock_enabled()
        root.addWidget(c3)
        self._style_setting_sliders()

        # ---------- 数据与日志 ----------
        c4, l4 = self._card("数据与日志",
                            "训练中若遇到闪退、计时异常等问题，可导出日志发给维护者排查。\n"
                            "日志只记录程序运行状态，不含个人隐私信息。")
        b4 = QHBoxLayout()
        b4.setSpacing(9)
        btn_log = IconButton("导出日志", "save", "primary")
        btn_log.setMinimumHeight(42)
        btn_log.clicked.connect(self.do_export)
        btn_open = IconButton("打开日志目录", "folder", "default")
        btn_open.setMinimumHeight(42)
        btn_open.clicked.connect(self.open_logs)
        b4.addStretch()
        b4.addWidget(btn_open)
        b4.addWidget(btn_log)
        l4.addLayout(b4)
        root.addWidget(c4)

        # ---------- 导入题卡信息 ----------
        c5, l5 = self._card("题库题卡信息", "加载中…")
        self.bank_lb = l5.itemAt(1).widget()
        root.addWidget(c5)

        # ---------- LinkGCS2 数据位置 ----------
        c9, l9 = self._gcs_card()
        root.addWidget(c9)

        # ---------- 视觉识别 API ----------
        c_vlm, l_vlm = self._card(
            "视觉识别 API",
            "点击题卡“解题”后，由所选视觉模型审题、计算并生成参考答案；本地 OCR 和图形识别只提供辅助信息。")
        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("服务商"))
        self.vision_api_provider = Combo(self)
        for provider_id, preset in VA.API_PROVIDER_PRESETS.items():
            self.vision_api_provider.addItem(preset["label"], provider_id)
        selected_provider = str(cfg.get("vision_api_provider", "deepseek"))
        self.vision_api_provider.setCurrentIndex(
            max(0, self.vision_api_provider.findData(selected_provider)))
        self.vision_api_provider.changed.connect(self._on_vision_provider_changed)
        provider_row.addWidget(self.vision_api_provider, 1)
        l_vlm.addLayout(provider_row)

        api_url_row = QHBoxLayout()
        api_url_row.addWidget(QLabel("API 地址"))
        self.vision_api_url_edit = QLineEdit(
            str(cfg.get("vision_api_base_url", VA.DEFAULT_API_BASE_URL)))
        self.vision_api_url_edit.setPlaceholderText(VA.DEFAULT_API_BASE_URL)
        self.vision_api_url_edit.setToolTip(
            "支持 OpenAI、DeepSeek 和图片输入兼容 Chat Completions 的服务；若服务不支持 JSON 模式，会自动兼容重试。")
        self.vision_api_url_edit.editingFinished.connect(self._save_vision_api)
        api_url_row.addWidget(self.vision_api_url_edit, 1)
        l_vlm.addLayout(api_url_row)

        api_model_row = QHBoxLayout()
        api_model_row.addWidget(QLabel("模型名称"))
        self.vision_api_model_edit = QLineEdit(
            str(cfg.get("vision_api_model", VA.DEFAULT_API_MODEL)))
        self.vision_api_model_edit.setPlaceholderText(VA.DEFAULT_API_MODEL)
        self.vision_api_model_edit.editingFinished.connect(self._save_vision_api)
        api_model_row.addWidget(self.vision_api_model_edit, 1)
        l_vlm.addLayout(api_model_row)

        api_key_row = QHBoxLayout()
        api_key_row.addWidget(QLabel("API Key"))
        self.vision_api_key_edit = QLineEdit(cfg.get_vision_api_key())
        self.vision_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.vision_api_key_edit.setPlaceholderText("粘贴你的 API Key")
        self.vision_api_key_edit.setToolTip(
            "密钥保存在本机设置中；Windows 使用当前用户 DPAPI 加密。")
        self.vision_api_key_edit.editingFinished.connect(self._save_vision_api)
        api_key_row.addWidget(self.vision_api_key_edit, 1)
        l_vlm.addLayout(api_key_row)

        api_status_row = QHBoxLayout()
        self.vision_api_status = QLabel("请填写 API Key 后测试连接。")
        self.vision_api_status.setWordWrap(True)
        self.vision_api_status.setStyleSheet(
            f"font-size:12px;color:{T.P['ink2']};background:transparent;")
        api_status_row.addWidget(self.vision_api_status, 1)
        self.b_vision_api_check = IconButton(
            "测试 API", "search", "default", font_size=12)
        self.b_vision_api_check.clicked.connect(self._check_vision_api)
        api_status_row.addWidget(self.b_vision_api_check)
        l_vlm.addLayout(api_status_row)

        api_help = QLabel(
            "题卡图片只在点击“解题”时上传。显示的 token 费用为估算值，按下方单价计算；服务商账单为准。\n" + DISCLAIMER)
        api_help.setWordWrap(True)
        api_help.setStyleSheet(
            f"font-size:11px;color:{T.P['ink3']};background:transparent;")
        l_vlm.addWidget(api_help)

        self.b_pricing_toggle = IconButton("展开费用估算设置", None, "default", font_size=12)
        self.b_pricing_toggle.setCheckable(True)
        l_vlm.addWidget(self.b_pricing_toggle, 0, Qt.AlignLeft)
        self.api_pricing_panel = FoldClip()
        self.api_pricing_content = QFrame()
        self.api_pricing_content.setObjectName("ApiPricingPanel")
        self.api_pricing_content.setStyleSheet(
            f"QFrame#ApiPricingPanel {{ background:{T.P['panel_soft']};"
            f" border:1px solid {T.P['glass_soft_edge']};"
            " border-radius:12px; }")
        pricing_layout = QVBoxLayout(self.api_pricing_content)
        pricing_layout.setContentsMargins(16, 14, 16, 14)
        pricing_layout.setSpacing(12)
        self.api_pricing_panel.hide()
        self.b_pricing_toggle.toggled.connect(self._toggle_api_pricing)

        pricing_heading = QHBoxLayout()
        pricing_heading.setSpacing(8)
        pricing_title = QLabel("费用估算")
        pricing_title.setStyleSheet(
            f"font-size:14px;font-weight:750;color:{T.P['ink']};"
            "background:transparent;")
        pricing_heading.addWidget(pricing_title)
        pricing_basis = QLabel("每百万 token")
        pricing_basis.setStyleSheet(
            f"font-size:12px;color:{T.P['ink3']};background:transparent;")
        pricing_heading.addWidget(pricing_basis)
        pricing_heading.addStretch(1)
        currency_label = QLabel("计价币种")
        currency_label.setStyleSheet(
            f"font-size:12px;color:{T.P['ink2']};background:transparent;")
        pricing_heading.addWidget(currency_label)
        self.vision_api_currency = Combo(self)
        for currency in ("USD", "CNY", "EUR", "GBP"):
            self.vision_api_currency.addItem(currency, currency)
        self.vision_api_currency.setCurrentIndex(max(0, self.vision_api_currency.findData(
            cfg.get("vision_api_currency", "USD"))))
        self.vision_api_currency.setFixedSize(104, 38)
        self.vision_api_currency.setAccessibleName("API 费用计价币种")
        pricing_heading.addWidget(self.vision_api_currency)
        pricing_layout.addLayout(pricing_heading)

        price_model_row = QHBoxLayout()
        price_model_label = QLabel("计价模型")
        price_model_label.setMinimumWidth(74)
        price_model_label.setStyleSheet(
            f"font-size:12px;color:{T.P['ink2']};background:transparent;")
        price_model_row.addWidget(price_model_label)
        self.vision_api_price_model = QLineEdit(
            str(cfg.get("vision_api_price_model", "deepseek-flash")))
        self.vision_api_price_model.setPlaceholderText("请填写服务商的模型名称")
        self.vision_api_price_model.setMinimumHeight(38)
        self.vision_api_price_model.setAccessibleName("费用估算适用的模型名称")
        self.vision_api_price_model.editingFinished.connect(self._save_vision_api)
        price_model_row.addWidget(self.vision_api_price_model, 1)
        pricing_layout.addLayout(price_model_row)

        price_row = QHBoxLayout()
        price_row.setSpacing(14)
        self.vision_api_input_price = CompactPriceSpinBox()
        self.vision_api_cached_price = CompactPriceSpinBox()
        self.vision_api_output_price = CompactPriceSpinBox()
        for title, control, key in (
                ("输入 token", self.vision_api_input_price,
                 "vision_api_input_price_per_million"),
                ("缓存输入 token", self.vision_api_cached_price,
                 "vision_api_cached_input_price_per_million"),
                ("输出 token", self.vision_api_output_price,
                 "vision_api_output_price_per_million")):
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(6)
            label = QLabel(title)
            label.setStyleSheet(
                f"font-size:12px;color:{T.P['ink2']};background:transparent;")
            column.addWidget(label)
            control.setDecimals(6)
            control.setRange(0, 1000000)
            control.setSingleStep(0.01)
            control.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            control.setKeyboardTracking(False)
            control.setAlignment(Qt.AlignRight)
            control.setMinimumHeight(38)
            control.setAccessibleName(f"{title}每百万单价")
            control.setToolTip(f"{title}的每百万 token 价格，币种由上方选择")
            control.setValue(float(cfg.get(key, 0) or 0))
            control.editingFinished.connect(self._save_vision_api)
            column.addWidget(control)
            price_row.addLayout(column, 1)
        pricing_layout.addLayout(price_row)

        price_divider = QFrame()
        price_divider.setFrameShape(QFrame.Shape.HLine)
        price_divider.setStyleSheet(
            f"color:{T.P['input_border']};background:transparent;")
        pricing_layout.addWidget(price_divider)

        budget_row = QHBoxLayout()
        budget_row.setSpacing(12)
        budget_copy = QVBoxLayout()
        budget_copy.setContentsMargins(0, 0, 0, 0)
        budget_copy.setSpacing(3)
        budget_title = QLabel("本地预算")
        budget_title.setStyleSheet(
            f"font-size:13px;font-weight:700;color:{T.P['ink']};"
            "background:transparent;")
        budget_copy.addWidget(budget_title)
        budget_hint = QLabel("用于估算剩余额；填 0 则不显示")
        budget_hint.setWordWrap(True)
        budget_hint.setStyleSheet(
            f"font-size:11px;color:{T.P['ink3']};background:transparent;")
        budget_copy.addWidget(budget_hint)
        budget_row.addLayout(budget_copy, 1)
        self.vision_api_budget = QDoubleSpinBox()
        self.vision_api_budget.setDecimals(4)
        self.vision_api_budget.setRange(0, 100000000)
        self.vision_api_budget.setSingleStep(1)
        self.vision_api_budget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.vision_api_budget.setKeyboardTracking(False)
        self.vision_api_budget.setAlignment(Qt.AlignRight)
        self.vision_api_budget.setFixedWidth(200)
        self.vision_api_budget.setMinimumHeight(38)
        self.vision_api_budget.setAccessibleName("可选的本地预算金额")
        self.vision_api_budget.setValue(
            float(cfg.get("vision_api_budget_amount", 0) or 0))
        self.vision_api_budget.setToolTip(
            "仅在服务商不提供余额查询时使用；按所选币种减去本机估算支出，不能代替服务商账单。填 0 表示不估算剩余额。")
        self.vision_api_budget.editingFinished.connect(self._save_vision_api)
        budget_row.addWidget(self.vision_api_budget)
        pricing_layout.addLayout(budget_row)
        price_hint = QLabel(
            "默认价格按 DeepSeek deepseek-flash 设置；请按所选服务商的当前价格校对。"
            "费用为本机估算，最终以服务商账单为准。")
        price_hint.setWordWrap(True)
        price_hint.setStyleSheet(
            f"font-size:11px;color:{T.P['ink3']};background:transparent;")
        pricing_layout.addWidget(price_hint)
        self.api_pricing_panel.set_content(self.api_pricing_content)
        l_vlm.addWidget(self.api_pricing_panel)
        self.vision_api_currency.changed.connect(
            self._on_vision_api_currency_changed)
        root.addWidget(c_vlm)
        self.api_pricing_fold = CardFoldController(
            self.api_pricing_panel, c_vlm, self.scroll.viewport(),
            scroll_area=self.scroll, duration=220,
            easing=QEasingCurve.OutCubic)

        # ---------- 赞赏码 ----------
        self.reward_support_card, l_support = self._card(
            "赞赏支持", "软件免费使用。如果它对你的练习有帮助，欢迎扫码赞赏；完全自愿。")
        reward_row = QHBoxLayout()
        reward_row.setSpacing(18)
        reward_copy = QVBoxLayout()
        reward_copy.setSpacing(8)
        self.reward_title = QLabel("感谢支持后续维护")
        self.reward_title.setStyleSheet(
            f"font-size:15px;font-weight:750;color:{T.P['ink']};background:transparent;")
        reward_copy.addWidget(self.reward_title)
        self.reward_detail = QLabel(
            "使用微信扫描右侧赞赏码。每一份支持都会用于继续完善 AZIMUTH。")
        self.reward_detail.setWordWrap(True)
        self.reward_detail.setStyleSheet(
            f"font-size:13px;color:{T.P['ink2']};background:transparent;")
        reward_copy.addWidget(self.reward_detail)
        reward_copy.addStretch(1)
        reward_row.addLayout(reward_copy, 1)
        self.reward_code = QLabel()
        self.reward_code.setAlignment(Qt.AlignCenter)
        self.reward_code.setFixedSize(320, 320)
        self.reward_code.setAccessibleName("MEGUMI-AKI 的微信赞赏码")
        self.reward_code.setToolTip("使用微信扫描赞赏码")
        self._refresh_reward_code()
        reward_row.addWidget(self.reward_code, 0, Qt.AlignRight | Qt.AlignVCenter)
        l_support.addLayout(reward_row)
        root.addWidget(self.reward_support_card)

        # ---------- 做题记录 ----------
        c8, l8 = self._card(
            "做题记录",
            "题库页停留达到设置的练习时间会自动标为已做；也可以用“标记已做”按钮手动记录。\n"
            "题目目录显示练习状态；Ctrl+D 标记或取消已做，Alt+← / Alt+→ 切换题目。",
            color=T.C_GREEN, icon="check")
        self.pr_lb = QLabel("")
        self.pr_lb.setWordWrap(True)
        self.pr_lb.setStyleSheet(
            f"font-size:13px; color:{T.P['ink']}; background:transparent;")
        l8.addWidget(self.pr_lb)

        pr_row = QHBoxLayout()
        pr_row.setSpacing(9)
        self.b_pr_refresh = IconButton("刷新", "reset", "default", font_size=13)
        self.b_pr_refresh.setMinimumHeight(40)
        self.b_pr_refresh.clicked.connect(self.refresh_progress)
        self.b_pr_clear = IconButton("清空做题记录", "trash", "default",
                                     font_size=13)
        self.b_pr_clear.setMinimumHeight(40)
        self.b_pr_clear.clicked.connect(self.do_clear_progress)
        pr_row.addWidget(self.b_pr_refresh)
        pr_row.addWidget(self.b_pr_clear)
        pr_row.addStretch()
        l8.addLayout(pr_row)
        root.addWidget(c8)

        # ---------- 免责声明 ----------
        c6, l6 = self._card("免责声明", DISCLAIMER, color=T.P["warn_fg"],
                            icon="warn")
        note = QLabel("继续使用本软件即表示你已阅读并同意上述声明。")
        note.setStyleSheet(
            f"font-size:12px; color:{T.P['ink3']}; background:transparent;")
        l6.addWidget(note)
        root.addWidget(c6)

        # ---------- 卸载 ----------
        c7, l7 = self._card(
            "卸载",
            "将移除桌面快捷方式与开始菜单项，并删除本工具的配置、日志、缓存。\n"
            "程序目录本身不会被删除，需要时请手动清理。",
            color=T.C_RED, icon="trash")
        btn_un = IconButton("卸载本工具", "trash", "danger")
        btn_un.setFixedWidth(180)
        btn_un.setMinimumHeight(42)
        btn_un.clicked.connect(self.do_uninstall)
        l7.addWidget(btn_un)
        root.addWidget(c7)

        # ---------- QQ 交流群 ----------
        c_qq, l_qq = self._card(
            "QQ 交流群", "点击后打开 QQ 并复制群号，在 QQ 内搜索加入。")
        self.b_qq_group = IconButton(
            "打开 QQ 并复制群号　1109038020", "note", "primary", font_size=13)
        self.b_qq_group.setMinimumHeight(42)
        self.b_qq_group.setToolTip("打开 QQ；群号 1109038020 已复制，可在 QQ 内搜索加入")
        self.b_qq_group.clicked.connect(self._join_qq_group)
        l_qq.addWidget(self.b_qq_group, 0, Qt.AlignLeft)
        root.addWidget(c_qq)

        from developer_ui import DeveloperDocs, PluginTools
        c_developer, l_developer = self._card(
            "开发协议", "复制协议给其他 Agent，并补充需求，即可制作符合规范的皮肤和离线工具插件。")
        self.developer_docs = DeveloperDocs(c_developer)
        l_developer.addWidget(self.developer_docs)
        root.addWidget(c_developer)
        c_plugins, l_plugins = self._card(
            "插件管理", "导入 .azplugin 后在独立窗口运行。v1 支持离线网页工具与交互教学，使用软件主题配色。")
        self.plugin_tools = PluginTools(c_plugins)
        l_plugins.addWidget(self.plugin_tools)
        root.addWidget(c_plugins)

        about = QLabel(f"{APP_TITLE}　v{APP_VER}　·　运行目录：{app_root()}")
        about.setWordWrap(True)
        about.setStyleSheet(
            f"font-size:11px; color:{T.P['ink3']}; background:transparent;")
        root.addWidget(about)
        self._build_settings_groups(root, about)
        self.industrial_footer = IndustrialPageFooter(self)
        self.industrial_footer.setVisible(T.app_theme == "industrial")
        root.addWidget(self.industrial_footer)
        root.addStretch()

        # _card 工厂已按实际页面顺序登记卡片，保留全量列表可让新卡片
        # 和新增设置项一起响应外观切换及入场状态。
        self._apply_btn_states(cfg.get("theme", "system"))
        self._style_gcs_inputs()
        self._style_vision_api_controls()
        self.refresh_progress()
        self.refresh_gcs_status()

    def _build_settings_groups(self, root, about):
        """以任务分类显示设置，避免低频参数占据整条长页面。"""
        sections = (
            ("appearance", "外观主题", {"外观", "应用主题", "自定义背景", "窗口阴影", "界面动效"}),
            ("timer", "计时声音", {"音效", "桌面倒计时时钟"}),
            ("bank", "题库判分", {"题卡排版", "题库题卡信息", "地面站数据位置", "做题记录"}),
            ("api", "视觉 API", {"视觉识别 API"}),
            ("developer", "开发与扩展", {"开发协议", "插件管理"}),
            ("maintenance", "维护与关于", {"数据与日志", "赞赏支持", "免责声明", "卸载", "QQ 交流群"}),
        )
        self._settings_tabs = {}
        self._settings_groups = {key: set(titles) for key, _, titles in sections}
        self._settings_about = about
        self.settings_navigation = SegmentRail(tuple((key, text, 'sliders-horizontal' if key == 'appearance' else None)
            for key, text, _ in sections), vertical=True)
        self.settings_navigation.changed.connect(self._select_settings_group)
        self._settings_tabs = self.settings_navigation.buttons
        self.preference_preview = PreferencePreview()
        root.insertWidget(1, self.preference_preview)
        # 页眉与目录不随表单滚动，编辑区域独立保存滚动位置。
        outer = self.layout()
        outer.removeWidget(self.scroll)
        page_layout = self.scroll.widget().layout()
        page_layout.removeWidget(self.anna_header_art)
        root.removeWidget(self.heading_widget)
        outer.addWidget(self.anna_header_art)
        outer.addWidget(self.heading_widget)
        self.editor_workspace = QWidget()
        self.editor_layout = QHBoxLayout(self.editor_workspace)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.editor_layout.setSpacing(18)
        self.editor_layout.addWidget(self.settings_navigation)
        self.editor_layout.addWidget(self.scroll, 1)
        outer.addWidget(self.editor_workspace, 1)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(14)
        root.setContentsMargins(0, 0, 0, 0)
        self._settings_root_side_margin = 0
        self._section_scrolls = {}
        self._settings_editor_narrow = None
        self._reflow_preferences()
        self._select_settings_group(cfg.get("settings_section", "appearance"), save=False)
        self._style_settings_actions()

    def _reflow_preferences(self):
        """小窗口用横向分类，大窗口用独立目录，编辑控件保持阅读宽度。"""
        if not hasattr(self, 'editor_workspace'):
            return
        narrow = self.width() < 940
        compact_themes = self.width() < 1220
        # 发布包可能只携带默认主题和工业主题；布局只为实际可见的内置主题
        # 分配列，避免缺失主题仍占着原来的空位。
        theme_buttons = [
            button for key, button in self._app_theme_btns.items()
            if not key.startswith("skin:") and not button.isHidden()
        ]
        theme_state = tuple(
            (key, not button.isHidden()) for key, button in self._app_theme_btns.items()
            if not key.startswith("skin:")
        )
        state = (narrow, compact_themes, theme_state)
        if state == self._settings_editor_narrow:
            return
        self._settings_editor_narrow = state
        self.editor_layout.setDirection(QVBoxLayout.TopToBottom if narrow else QHBoxLayout.LeftToRight)
        self.settings_navigation.set_vertical(not narrow)
        self.app_theme_grid.removeWidget(self.app_theme_label)
        self.app_theme_grid.addWidget(
            self.app_theme_label, 0, 0, 1,
            max(2, len(theme_buttons)) if compact_themes else 1)
        for index,button in enumerate(theme_buttons):
            self.app_theme_grid.removeWidget(button)
            self.app_theme_grid.addWidget(
                button,
                1 + index // 2 if compact_themes else 0,
                index % 2 if compact_themes else index + 1)
        for index in range(6):
            self.app_theme_grid.setColumnStretch(index, 0)
        if compact_themes:
            for index in range(2):
                self.app_theme_grid.setColumnStretch(index, 1)
        else:
            for index in range(len(theme_buttons)):
                self.app_theme_grid.setColumnStretch(index + 1, 1)
        self.settings_navigation.setMaximumWidth(16777215 if narrow else 168)
        self.settings_navigation.setMaximumHeight(52 if narrow else 16777215)
        self.settings_navigation.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed if narrow else QSizePolicy.Expanding)

    def _style_settings_actions(self):
        """默认主题使用独立的操作尺寸，每次换肤重新计算，避免沿用其他主题度量。"""
        default = T.app_theme == "default"
        self.settings_navigation.refresh_theme()
        self.preference_preview.refresh_theme()
        for button in self._settings_tabs.values():
            button.setFixedHeight(44)
        self.b_pricing_toggle._font_size = 14 if default else 12
        self.b_pricing_toggle.setMinimumHeight(44 if default else 42)
        self.b_pricing_toggle.setMinimumWidth(200 if default else 0)
        self.b_pricing_toggle.refresh()
        # 遗漏刷新的日志、卸载等按钮也要按当前配色恢复。
        for button in self.findChildren(IconButton):
            button.refresh()
        height = 44 if default else 42
        self.b_pricing_toggle.setStyleSheet(self.b_pricing_toggle.styleSheet() +
                                           f"QPushButton{{min-height:{height - 12}px;}}")

    def _select_settings_group(self, key, save=True):
        key = key if key in self._settings_groups else "appearance"
        previous = getattr(self, '_settings_section', None)
        if hasattr(self, '_section_scrolls') and previous is not None:
            self._section_scrolls[previous] = self.scroll.verticalScrollBar().value()
        old=getattr(self.scroll.viewport(),'_workbench_reveal',None)
        if old is not None:old.dispose()
        # 设置内容具有较多透明控件，直接切换可避免旧文字叠在新内容上。
        snapshot = None
        if save:
            cancel_card_in(self._cards)
        self._settings_section = key
        titles = self._settings_groups[key]
        # 未归类的扩展卡片放进维护页，确保更新后仍能找到。
        known = set().union(*self._settings_groups.values())
        for card in self._cards:
            title = card._title_lab.text()
            card.setVisible(title in titles or (key == "maintenance" and title not in known))
        self.settings_navigation.set_current(key, animate=save)
        self.preference_preview.setVisible(key == "appearance")
        self._settings_about.setVisible(key == "maintenance")
        QTimer.singleShot(0, lambda section=key: self.scroll.verticalScrollBar().setValue(self._section_scrolls.get(section, 0)))
        if save:
            cfg.set_("settings_section", key)
            if snapshot is not None:
                from workbench_ui import LayoutReveal
                viewport = self.scroll.viewport()
                old = getattr(viewport, '_workbench_reveal', None)
                if old is not None:
                    old.dispose()
                viewport._workbench_reveal = LayoutReveal(viewport, snapshot)

    def _toggle_api_pricing(self, opened):
        self.api_pricing_fold.set_open(opened)
        self.b_pricing_toggle.set_checked_style(opened)
        self.b_pricing_toggle.setText("收起费用估算设置" if opened else "展开费用估算设置")

    # --------------------------------------------------- LinkGCS2 数据位置
    def _style_gcs_inputs(self):
        """输入框是内联样式，主题切换时要重刷（不能靠全局 QSS）"""
        if not hasattr(self, "gcs_edit"):
            return
        self.gcs_edit.setStyleSheet(
            f"QLineEdit{{background:{T.P['input_bg']};"
            f" border:1px solid {T.P['input_border']}; border-radius:9px;"
            f" padding:6px 11px; color:{T.P['ink']}; font-size:13px;"
            f" selection-background-color:{T.C_BLUE};}}"
            f"QLineEdit:focus{{border:1px solid {T.C_BLUE};"
            f" background:{T.P['input_bg_focus']};}}")
        # Combo 是自绘控件，颜色在 refresh() 里从调色板现取 —— 换主题要叫它重画
        if hasattr(self, "gcs_station"):
            try:
                self.gcs_station.refresh()
            except Exception:
                pass
        self._refresh_station_tip()
        self._gcs_preview()

    def _style_vision_api_controls(self):
        if not hasattr(self, "vision_api_input_price"):
            return
        if hasattr(self, "api_pricing_content"):
            self.api_pricing_content.setStyleSheet(
                f"QFrame#ApiPricingPanel {{ background:{T.P['panel_soft']};"
                f" border:1px solid {T.P['glass_soft_edge']};"
                " border-radius:12px; }")
        field_style = (
            f"QDoubleSpinBox{{background:{T.P['input_bg']};"
            f"border:1px solid {T.P['input_border']};border-radius:8px;"
            f"padding:6px 11px;color:{T.P['ink']};font-size:13px;"
            f"selection-background-color:{T.C_BLUE};}}"
            f"QDoubleSpinBox:focus{{border:1px solid {T.C_BLUE};"
            f"background:{T.P['input_bg_focus']};}}")
        for control in (self.vision_api_input_price,
                        self.vision_api_cached_price,
                        self.vision_api_output_price,
                        self.vision_api_budget):
            control.setStyleSheet(field_style)
        self.vision_api_price_model.setStyleSheet(
            f"QLineEdit{{background:{T.P['input_bg']};"
            f"border:1px solid {T.P['input_border']};border-radius:8px;"
            f"padding:6px 11px;color:{T.P['ink']};font-size:13px;"
            f"selection-background-color:{T.C_BLUE};}}"
            f"QLineEdit:focus{{border:1px solid {T.C_BLUE};"
            f"background:{T.P['input_bg_focus']};}}")
        currency = str(self.vision_api_currency.currentData() or "USD")
        self.vision_api_budget.setSuffix(f" {currency}")
        self.vision_api_currency.refresh()

    def _on_vision_api_currency_changed(self, _index=None):
        self._style_vision_api_controls()
        self._save_vision_api()

    def _gcs_card(self):
        """地面站数据位置：选类型 + 扫盘自动识别 + 手动指定。

        为什么值得单开一张卡：判定答案的来源要么是用户手抄坐标，要么是
        从地面站的库里直接读。后者零成本、厘米级精度，但前提是
        「让软件知道他用的是哪家地面站、把任务存哪了」。

        ★ **多地面站支持**：LinkGCS2 / 华科尔 / 汉鲲 / 凌动 都能读。
        其中只有 LinkGCS2 的字段名是实测出来的，另外三家走通用启发式
        （找 lat/lon 一类字段），界面上要**如实说明**这一点 ——
        让用户知道"读到了但可能不准"比假装都能读要诚实得多。
        """
        c, l = self._card(
            "地面站数据位置",
            "「题卡工作台」里点「判题」时会直接读地面站里画好的航线来判对错，"
            "不用再把坐标抄一遍。\n"
            "留空由软件自动查找（先看地面站自己的配置，再试几个常规位置）；"
            "自动找不到时，可以在这里选类型、扫盘或手动指定。",
            color=T.C_BLUE, icon="folder")

        # ---- 地面站类型 ----
        srow = QHBoxLayout()
        srow.setSpacing(9)
        slb = QLabel("地面站类型")
        slb.setStyleSheet(
            f"font-size:13px; color:{T.P['ink2']}; background:transparent;")
        slb.setFixedWidth(88)
        srow.addWidget(slb)
        self.gcs_station = Combo(scale=getattr(self, "_scale", 1.0))
        self.gcs_station.addItem("自动识别（推荐）", "auto")
        for k, nm in LG.station_names():
            self.gcs_station.addItem(nm, k)
        # 把当前存的选上
        _cur = str(cfg.get("gcs_station", "") or "")
        _idx = self.gcs_station.findData(_cur)
        if _idx >= 0:
            self.gcs_station.setCurrentIndex(_idx)
        self.gcs_station.setFixedWidth(230)
        self.gcs_station.changed.connect(self._on_gcs_station)
        srow.addWidget(self.gcs_station)
        srow.addStretch()
        l.addLayout(srow)

        self.gcs_sta_tip = QLabel("")
        self.gcs_sta_tip.setWordWrap(True)
        self.gcs_sta_tip.setStyleSheet(
            f"font-size:11.5px; color:{T.P['ink3']}; background:transparent;"
            f"line-height:165%;")
        l.addWidget(self.gcs_sta_tip)
        self._refresh_station_tip()

        # ---- 当前状态 ----
        self.gcs_lb = QLabel("")
        self.gcs_lb.setWordWrap(True)
        self.gcs_lb.setStyleSheet(
            f"font-size:13px; color:{T.P['ink']}; background:transparent;"
            f"line-height:175%;")
        l.addWidget(self.gcs_lb)

        # ---- 路径输入 ----
        prow = QHBoxLayout()
        prow.setSpacing(9)
        self.gcs_edit = QLineEdit()
        self.gcs_edit.setPlaceholderText(
            "留空 = 自动查找。也可以填数据目录，或直接填 .db 文件")
        self.gcs_edit.setMinimumHeight(40)
        self.gcs_edit.setText(str(cfg.get("linkgcs_path", "") or ""))
        self.gcs_edit.textChanged.connect(self._gcs_preview)
        prow.addWidget(self.gcs_edit, 1)
        self.b_gcs_pick = IconButton("选择…", "folder", "default", font_size=13)
        self.b_gcs_pick.setMinimumHeight(40)
        self.b_gcs_pick.clicked.connect(self._gcs_pick)
        prow.addWidget(self.b_gcs_pick)
        l.addLayout(prow)

        # 输入时的即时反馈（绿=识别成功 / 红=有问题 / 灰=留空自动找）
        self.gcs_tip = QLabel("")
        self.gcs_tip.setWordWrap(True)
        self.gcs_tip.setStyleSheet(
            f"font-size:11.5px; color:{T.P['ink3']}; background:transparent;"
            f"line-height:165%;")
        l.addWidget(self.gcs_tip)

        # ---- 操作按钮 ----
        brow = QHBoxLayout()
        brow.setSpacing(9)
        self.b_gcs_scan = IconButton("扫盘识别", "search", "primary",
                                     font_size=13)
        self.b_gcs_scan.setMinimumHeight(40)
        self.b_gcs_scan.setToolTip(
            "在常见位置（各盘根目录、文档、地面站配置里记的路径）"
            "查找它的任务数据库")
        self.b_gcs_scan.clicked.connect(self._gcs_scan)
        brow.addWidget(self.b_gcs_scan)

        self.b_gcs_save = IconButton("保存路径", "check", "default", font_size=13)
        self.b_gcs_save.setMinimumHeight(40)
        self.b_gcs_save.clicked.connect(self._gcs_save)
        brow.addWidget(self.b_gcs_save)

        self.b_gcs_auto = IconButton("恢复自动查找", "reset", "default",
                                     font_size=13)
        self.b_gcs_auto.setMinimumHeight(40)
        self.b_gcs_auto.setToolTip("清空这里填的路径，交回给软件自己找")
        self.b_gcs_auto.clicked.connect(self._gcs_auto)
        brow.addWidget(self.b_gcs_auto)
        brow.addStretch()
        l.addLayout(brow)
        return c, l

    def _refresh_station_tip(self):
        """把"这家是不是实测过"如实讲出来。"""
        key = (self.gcs_station.currentData()
               if hasattr(self, "gcs_station") else "auto")
        if not key or key == "auto":
            self.gcs_sta_tip.setStyleSheet(
                f"font-size:11.5px; color:{T.P['ink3']};"
                f"background:transparent; line-height:165%;")
            self.gcs_sta_tip.setText(
                "自动按 LinkGCS2 → 华科尔 → 汉鲲 → 凌动 的顺序找，"
                "认得出哪家就用哪家。知道你用哪家的话，直接选它能省时间。")
            return
        prof = LG.station(key)
        if prof.get("verified"):
            txt = ("这家是实测过的，字段名对得上，坐标/高度/速度/循环次数"
                   "都能准确读出来。")
            col = T.P["ok_fg"]
        else:
            txt = ("⚠️ 这家**还没有真实样本实测过**：能定位它的数据文件、"
                   "也能读里面的航点坐标，但字段名是靠通用规则猜的 —— "
                   "高度/速度这类参数**可能读不到**。判题时若发现某项空白，"
                   "直接手动填上就行，不会算你错。\n"
                   "如果你愿意把它的数据文件发我看看，我可以把它做成"
                   "跟 LinkGCS2 一样的精确支持。")
            col = T.P["warn_fg"]
        self.gcs_sta_tip.setStyleSheet(
            f"font-size:11.5px; color:{col}; background:transparent;"
            f"line-height:165%;")
        self.gcs_sta_tip.setText(txt.replace("**", ""))

    def _on_gcs_station(self, idx=None):
        cfg.set_("gcs_station",
                 self.gcs_station.currentData() or "auto")
        self._refresh_station_tip()
        self.refresh_gcs_status()
        self._gcs_preview()

    def _gcs_preview(self):
        """输入框内容变了 —— 就地给一句能不能用的判断。"""
        raw = self.gcs_edit.text().strip()
        if not raw:
            self.gcs_tip.setStyleSheet(
                f"font-size:11.5px; color:{T.P['ink3']};"
                f"background:transparent; line-height:165%;")
            self.gcs_tip.setText("留空：交给软件自动查找。")
            return
        key = self.gcs_station.currentData() if hasattr(
            self, "gcs_station") else None
        db, msg = LG.probe(raw, key=None if key in (None, "auto") else key)
        col = T.P["ok_fg"] if db else T.P["err_fg"]
        self.gcs_tip.setStyleSheet(
            f"font-size:11.5px; color:{col}; background:transparent;"
            f"line-height:165%;")
        self.gcs_tip.setText(msg + ("\n记得点一下「保存路径」把位置存下来。"
                                    if db else ""))

    def _gcs_pick(self):
        """选目录，或者直接选那个 .db 文件。"""
        start = self.gcs_edit.text().strip() or str(pathlib.Path.home())
        r = QMessageBox(self)
        r.setWindowTitle("选择地面站数据位置")
        r.setText("你想指定到什么？\n\n"
                  "· 数据目录：地面站的数据文件夹（里面通常是 db 子目录）\n"
                  "· 数据库文件：直接指到它的 .db / .sqlite")
        b_dir = r.addButton("选目录", QMessageBox.ButtonRole.AcceptRole)
        b_file = r.addButton("选数据库文件", QMessageBox.ButtonRole.ActionRole)
        r.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        r.exec()
        if r.clickedButton() is b_dir:
            p = QFileDialog.getExistingDirectory(self, "选择地面站数据目录",
                                                 start)
        elif r.clickedButton() is b_file:
            p, _ = QFileDialog.getOpenFileName(
                self, "选择地面站的数据库", start,
                "数据库 (*.db *.sqlite *.sqlite3);;所有文件 (*)")
        else:
            return
        if p:
            self.gcs_edit.setText(p)
            self._gcs_preview()

    def _gcs_scan(self):
        """扫盘。可能耗时，用进度对话框挡住并给「第几个目录」的反馈。"""
        from PySide6.QtWidgets import QProgressDialog
        from PySide6.QtCore import QCoreApplication
        # 用户选定了某一家就只扫那家 —— 别拿着 LinkGCS2 的目录去翻汉鲲，
        # 又慢又容易"找到个不相干的库"。
        _k = self.gcs_station.currentData() if hasattr(
            self, "gcs_station") else "auto"
        keys = None if _k in (None, "auto") else [_k]
        _who = "地面站" if keys is None else LG.station(_k)["name"]
        prog = QProgressDialog("正在查找%s的任务数据库…" % _who, "取消",
                               0, 0, self)
        prog.setWindowTitle("扫盘识别")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumWidth(420)
        prog.setMinimumDuration(0)
        prog.show()

        state = {"n": 0, "stop": False}

        def on_step(n, folder):
            state["n"] = n
            if n % 12:
                return
            prog.setLabelText("已翻过 %d 个目录…\n%s" % (n, folder))
            QCoreApplication.processEvents()
            if prog.wasCanceled():
                state["stop"] = True

        try:
            db, tried = LG.scan(on_step=on_step, keys=keys)
        finally:
            prog.close()
        if state["stop"]:
            self._gcs_tip("扫盘已取消。", T.P["ink3"])
            return
        if db:
            self.gcs_edit.setText(str(db))
            self._gcs_preview()
            self._gcs_tip("扫盘成功：翻过 %d 个目录，找到\n%s\n"
                          "点「保存路径」把它记下来。" % (tried, db),
                          T.P["ok_fg"])
        else:
            self._gcs_tip(
                "翻过 %d 个目录，没找到%s的任务数据库。\n"
                "接下来可以：① 点「选择…」直接指到那个 .db 文件（最可靠）；"
                "② 在软件里先随便保存一条航线再回来扫；"
                "③ 如果本机压根没装地面站，那就在工作台里手动填答案判题。"
                % (tried, _who), T.P["err_fg"])

    def _gcs_tip(self, text, color):
        self.gcs_tip.setStyleSheet(
            f"font-size:11.5px; color:{color}; background:transparent;"
            f"line-height:165%;")
        self.gcs_tip.setText(text)

    def _gcs_key(self):
        """设置页当前选中的地面站 key（自动 → None）。"""
        if not hasattr(self, "gcs_station"):
            return None
        k = self.gcs_station.currentData()
        return None if k in (None, "auto") else k

    def _gcs_save(self):
        raw = self.gcs_edit.text().strip()
        cfg.set_("linkgcs_path", raw)
        if not raw:
            self.refresh_gcs_status()
            self._gcs_tip("已改为自动查找。", T.P["ink3"])
            self.set_status("已改为自动查找地面站数据")
            return
        db, msg = LG.probe(raw, key=self._gcs_key())
        self.refresh_gcs_status()
        if db:
            self._gcs_tip("已保存。实际会读取：\n%s" % db, T.P["ok_fg"])
            self.set_status("地面站数据位置已保存")
        else:
            self._gcs_tip("已保存，但%s\n判题时读不到会退回手动填写。" % msg,
                          T.P["warn_fg"])
            self.set_status("地面站数据位置已保存（但当前无效）")

    def _gcs_auto(self):
        self.gcs_edit.setText("")
        cfg.set_("linkgcs_path", "")
        self.refresh_gcs_status()
        self._gcs_tip("已恢复自动查找。", T.P["ink3"])
        self.set_status("已恢复自动查找地面站数据")

    def refresh_gcs_status(self):
        """把「现在到底读到哪份数据、有几条任务」摆出来。

        只说"已找到"是不够的 —— 五三装了不止一份地面站（或者数据放过
        好几个位置），得让他确认读到的是**他正在用的那一份**。所以这里
        把路径和任务条数一起显示。

        「填了手动路径但读不到」要单独说清楚：这时软件**不会**退回自动
        查找（避免判的是另一套航线数据），状态必须如实显示为不可用。
        """
        if not hasattr(self, "gcs_lb"):
            return
        manual = str(cfg.get("linkgcs_path", "") or "").strip()
        key = self._gcs_key()
        if manual:
            db, msg = LG.probe(manual, key=key)
            if db is None:
                self.gcs_lb.setText(
                    "当前状态：<b>手动指定的路径不可用</b> —— 判题会退回手动填写。\n"
                    "%s\n%s" % (manual, msg))
                return
            self._gcs_found(db, "手动指定", key)
            return
        try:
            db = LG.find_db(key=key)
        except Exception as e:
            self.gcs_lb.setText("读取地面站数据位置出错：%s" % e)
            return
        if db is None:
            who = LG.station(key)["name"] if key else "地面站"
            self.gcs_lb.setText(
                "当前状态：<b>没找到</b>%s的任务数据库。\n"
                "「判题」会退回手动填写模式。装了地面站的话，"
                "可以用下面的「扫盘识别」或「选择…」指一下位置。" % who)
            return
        self._gcs_found(db, "自动查找", key)

    def _gcs_found(self, db, src, key=None):
        """已知可用时的状态文案：路径 + 任务条数。"""
        n = -1
        try:
            n = len(LG.list_tasks(db, limit=500, key=key))
        except Exception:
            pass
        line = "当前状态：<b>已找到</b>（%s）\n%s" % (src, db)
        if n >= 0:
            line += "\n库里共 %d 条任务。" % n
        else:
            line += "\n（库能打开，但读任务列表时出错，判题可能不正常。）"
        self.gcs_lb.setText(line)

    def _save_vision_api(self):
        provider = (self.vision_api_provider.currentData()
                    if hasattr(self, "vision_api_provider") else "deepseek")
        provider = str(provider or "custom")
        preset = VA.API_PROVIDER_PRESETS.get(provider, {})
        base_url = self.vision_api_url_edit.text().strip() or preset.get("base_url", "")
        model = self.vision_api_model_edit.text().strip() or preset.get("model", "")
        self.vision_api_url_edit.setText(base_url)
        self.vision_api_model_edit.setText(model)
        cfg.set_("vision_api_provider", provider, save_now=False)
        cfg.set_("vision_api_base_url", base_url, save_now=False)
        cfg.set_("vision_api_model", model, save_now=False)
        cfg.set_("vision_api_price_model",
                 self.vision_api_price_model.text().strip() or model,
                 save_now=False)
        cfg.set_("vision_api_currency",
                 str(self.vision_api_currency.currentData() or "USD"),
                 save_now=False)
        cfg.set_("vision_api_input_price_per_million",
                 self.vision_api_input_price.value(), save_now=False)
        cfg.set_("vision_api_cached_input_price_per_million",
                 self.vision_api_cached_price.value(), save_now=False)
        cfg.set_("vision_api_output_price_per_million",
                 self.vision_api_output_price.value(), save_now=False)
        cfg.set_("vision_api_budget_amount",
                 self.vision_api_budget.value(), save_now=False)
        try:
            cfg.set_vision_api_key(self.vision_api_key_edit.text().strip())
        except Exception:
            self.vision_api_status.setText("API Key 无法加密保存，请检查 Windows 用户配置。")
            return False
        return True

    def _on_vision_provider_changed(self, _index):
        provider = str(self.vision_api_provider.currentData() or "custom")
        preset = VA.API_PROVIDER_PRESETS.get(provider, {})
        if provider != "custom":
            self.vision_api_url_edit.setText(preset.get("base_url", ""))
            self.vision_api_model_edit.setText(preset.get("model", ""))
        self._save_vision_api()

    def _check_vision_api(self):
        if self._vision_api_worker is not None:
            return
        if not self._save_vision_api():
            return
        self.b_vision_api_check.setEnabled(False)
        self.vision_api_status.setText("正在连接模型 API……")
        accent(self.vision_api_status, "scan", busy=True)
        worker = VisionApiCheckWorker(self)
        self._vision_api_worker = worker
        worker.completed.connect(self._on_vision_api_checked)
        worker.finished.connect(self._on_vision_api_check_finished)
        worker.start()

    def _on_vision_api_checked(self, result):
        if result.get("connected"):
            self.vision_api_status.setText("连接成功：" + str(result.get("model", "")))
        else:
            self.vision_api_status.setText("连接失败：" + str(result.get("error", "未知错误")))
        accent(self.vision_api_status, "success" if result.get("connected") else "error")

    def _on_vision_api_check_finished(self):
        self.b_vision_api_check.setEnabled(True)
        self._vision_api_worker = None

    def set_status(self, txt):
        """转给主窗口状态栏（同 BankPage 的坑：别自己调自己）。"""
        w = self.window()
        fn = getattr(w, "set_status", None)
        if callable(fn) and w is not self:
            fn(txt)
        if self.isVisible():
            self.page_subtitle.setText(str(txt))
            accent(self.page_subtitle, "success")

    # --------------------------------------------------- 做题记录
    def refresh_progress(self):
        """刷新「做题记录」卡片：已做数量 + 最近练过的几题"""
        n = PG.done_count()
        if not n:
            self.pr_lb.setText("还没有练习记录。打开任意题卡工作台就会自动记一次。")
        else:
            txt = "已做 %d 题。" % n
            rows = PG.recent(5)
            if rows:
                items = "　".join("第 %d 题（%d 次）" % (q, r["count"])
                                 for q, r in rows)
                txt += "\n最近练习：" + items
            self.pr_lb.setText(txt)
        self.b_pr_clear.setEnabled(n > 0)
        self.b_pr_clear.setCursor(Qt.PointingHandCursor if n > 0
                                  else Qt.ArrowCursor)

    def do_clear_progress(self):
        if PG.done_count() == 0:
            return
        r = QMessageBox.question(
            self, "清空做题记录",
            "将清除所有题目的「已做」标记与练习次数。\n此操作不可撤销，确定继续吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        PG.clear()
        self.refresh_progress()
        log.info("做题记录已清空")
        w = self.window()
        if hasattr(w, "set_status"):
            w.set_status("做题记录已清空")
        # 题库页可能正显示「只看已做」这类筛选，切回去时由 on_show 兜底刷新

    def on_show(self):
        self.refresh_progress()

    def _intro_cards(self):
        viewport = self.scroll.viewport()
        visible = [card for card in self._cards
                   if not card.isHidden() and
                   QRect(card.mapTo(viewport, QPoint(0, 0)), card.size())
                   .intersects(viewport.rect())]
        return visible[:5]

    def prepare_intro(self):
        self._intro_prepared_cards = self._intro_cards()
        prepare_card_in(self._intro_prepared_cards)

    def play_intro(self):
        """每次回到设置页时，让当前视口中的卡片依次浮入。"""
        cards = getattr(self, "_intro_prepared_cards", self._intro_cards())
        card_in(cards, step=70, ms=460, prepared=True)
        self._intro_prepared_cards = None

    def cancel_intro(self):
        cancel_card_in(self._intro_cards())
        self._intro_prepared_cards = None

    # --------------------------------------------------- 卡片工厂
    def _card(self, title, desc, color=None, icon=None):
        c = glass()
        c.setProperty("chiyoSection", True)
        c._comic_dots = SettingsCardDots(c)
        self._cards.append(c)
        l = QVBoxLayout(c)
        l.setContentsMargins(22, 18, 22, 18)
        l.setSpacing(9)
        attach_shadow(c, blur=32, dy=9, alpha=46)

        hrow = QHBoxLayout()
        hrow.setSpacing(8)
        section = SectionNumberTag("%02d" % len(self._cards))
        section.setAlignment(Qt.AlignCenter)
        section.setFixedSize(42, 29)
        section.setStyleSheet(
            "background:transparent;color:#fff;font-size:13px;font-weight:850;"
            "border:0;padding:0;")
        section.hide()
        c._section_lab = section
        hrow.addWidget(section)
        if icon:
            ic = QLabel()
            ic.setFixedSize(22, 22)
            ic.setPixmap(getattr(Icons, icon)(
                color or T.P["accent_fg"], size=18))
            hrow.addWidget(ic)
            c._icon_lab = ic
            c._icon_name = icon
        h = QLabel(title)
        heading_color = color or (
            T.P["ink"] if T.app_theme == "industrial" else
            T.C_BLUE if T.app_theme == "anna" else T.P["accent_fg"])
        h.setStyleSheet(
            f"font-size:16px; font-weight:800;"
            f"color:{heading_color}; background:transparent;")
        hrow.addWidget(h)
        hrow.addStretch()
        c._chapter_mark = SettingsChapterMark(title, c)
        c._chapter_mark.setVisible(T.app_theme == "chiyo")
        hrow.addWidget(c._chapter_mark)
        c._header_layout = hrow
        l.addLayout(hrow)

        d = QLabel(desc)
        d.setWordWrap(True)
        d.setStyleSheet(
            f"font-size:13px; color:{T.P['ink2']};"
            f"line-height:185%; background:transparent;")
        l.addWidget(d)
        c._title_lab = h
        c._desc_lab = d
        c._title_color = color
        return c, l

    # --------------------------------------------------- 主题
    def set_theme_state(self, pref):
        self._apply_btn_states(pref)

    def _apply_btn_states(self, pref):
        self._theme_pref = pref
        for k, b in self._theme_btns.items():
            b.setChecked(k == pref)
            b.update()
        QTimer.singleShot(0, lambda value=pref: self._move_theme_indicator(value))

    def _move_theme_indicator(self, pref, animate=True):
        """主题选项用同一块柔和底板标出当前偏好。"""
        button = self._theme_btns.get(pref)
        if button is None or not button.isVisible():
            return
        rect = button.geometry().adjusted(-3, -3, 3, 3)
        rect = rect.intersected(self.theme_host.rect().adjusted(1, 1, -1, -1))
        if rect.width() <= 0 or rect.height() <= 0:
            return
        if self._theme_motion is not None:
            try:
                self._theme_motion.stop()
            except RuntimeError:
                pass
            self._theme_motion = None
        if (not animate or not animations_enabled() or
                not self.theme_host.isVisible() or
                self.theme_indicator.geometry().isNull()):
            self.theme_indicator.setGeometry(rect)
            return
        motion = QPropertyAnimation(self.theme_indicator, b"geometry", self)
        motion.setDuration(380)
        motion.setStartValue(self.theme_indicator.geometry())
        motion.setEndValue(rect)
        motion.setEasingCurve(QEasingCurve.InOutCubic)
        motion.start(QPropertyAnimation.DeleteWhenStopped)
        self._theme_motion = motion

    def theme_changed(self, pref=None):
        self.developer_docs.refresh_theme()
        industrial = T.app_theme == "industrial"
        anna = T.app_theme == "anna"
        self.industrial_header.setVisible(industrial)
        self.industrial_header.update()
        self.industrial_footer.setVisible(industrial)
        self.industrial_footer.update()
        self.chiyo_header_scene.setVisible(T.app_theme == "chiyo")
        self.chiyo_header_scene.update()
        self.chiyo_header_note.update()
        self.anna_header_art.setVisible(anna)
        self.anna_header_art.refresh_art()
        self.heading_widget.setVisible(not anna)
        self._settings_root_side_margin = 0
        self._settings_root_layout.setContentsMargins(
            0, 0, 0, 0)
        for c in self._cards:
            c.layout().setContentsMargins(
                22, 10 if anna else 18, 22, 8 if anna else 18)
            c.layout().setSpacing(4 if anna else 9)
            # 卡片样式在下方立即切换为千代专属规则或清空后继承主窗口主题；
            # 这里再次解析并写入旧的玻璃样式表是冗余的，且会拖慢主题切换。
            replay_shadow(c)
            c._comic_dots.update()
            c._chapter_mark.setVisible(T.app_theme == "chiyo")
            c._chapter_mark.update()
            c._section_lab.setVisible(T.app_theme in ("chiyo", "anna"))
            c._section_lab.update()
            if c.styleSheet():
                c.setStyleSheet("")
            c._section_lab.setStyleSheet(
                "background:transparent;color:#fff;font-size:13px;font-weight:850;"
                "border:0;padding:0;")
            title_color = c._title_color or (
                T.P["ink"] if industrial else
                T.P["ink"] if anna else T.P["accent_fg"])
            c._title_lab.setStyleSheet(
                f"font-size:{25 if anna else 16}px; font-weight:800;"
                f"color:{title_color};background:transparent;")
            c._desc_lab.setStyleSheet(
                f"font-size:{15 if anna else 13}px; color:{T.P['ink2']};"
                f"line-height:185%; background:transparent;")
            if getattr(c, "_icon_name", None):
                repaint_icon(c._icon_lab,
                             c._title_color or T.P["accent_fg"])
        for l in self.findChildren(QLabel):
            if l.objectName() == "H1":
                l.setStyleSheet(
                    f"font-size:30px; font-weight:800; color:{T.P['ink']};"
                    f"letter-spacing:1px; background:transparent;")
            elif l.objectName() == "Dim":
                l.setStyleSheet(
                    f"font-size:13px; color:{T.P['ink2']};"
                    f"background:transparent;")
        self.cscale_lb.setStyleSheet(
            f"font-size:13px; font-weight:700; color:{T.P['ink2']};"
            f"background:transparent;")
        self.slevel_lb.setStyleSheet(
            f"font-size:13px; font-weight:700; color:{T.P['ink2']};"
            f"background:transparent;")
        self.shadow_lb.setStyleSheet(
            f"font-size:13px; color:{T.P['ink2']}; background:transparent;")
        self._style_setting_sliders()
        self.b_shadow_reset.refresh()
        for b in self._theme_btns.values():
            b.update()
        for b in self._app_theme_btns.values():
            b.update()
        for button in self._sound_actions:
            button.refresh()
        self._style_sound_modes()
        for listing in self._sound_lists.values():
            listing.setStyleSheet(
                f"QListWidget{{color:{T.P['ink']};background:{T.P['input_bg']};"
                f"border:1px solid {T.P['input_border']};border-radius:6px;}}"
                f"QListWidget::item:selected{{color:{T.P['ink']};"
                f"background:{T.P['accent_soft']};}}")
        for combo in (self.cmb_card_layout, self.vision_api_provider):
            combo._dispose_popup()
            combo.refresh()
        for label in (self.card_layout_label, self.sound_ui_label,
                      self.sound_volume_label):
            label.setStyleSheet(
                f"font-size:13px;color:{T.P['ink2']};background:transparent;")
        # 明确设置禁用文案颜色，系统调色板不再把它压成近乎不可见的文字。
        for checkbox in (self.chk_sound_ui, self.chk_sound_stage):
            checkbox.setStyleSheet(
                f"QCheckBox{{color:{T.P['ink']};font-size:13px;background:transparent;}}"
                f"QCheckBox:disabled{{color:{T.P['ink3']};}}")
        self.theme_label.setStyleSheet(
            f"font-size:15px;font-weight:750;color:{T.P['ink']};background:transparent;")
        self.theme_desc.setStyleSheet(
            f"font-size:13px;color:{T.P['ink2']};background:transparent;")
        indicator_bg = ("rgba(49,91,196,0.10)" if T.mode == "light"
                        else "rgba(95,161,255,0.14)") if anna \
            else T.P["accent_soft"]
        indicator_edge = ("#315BC4" if T.mode == "light" else "#5FA1FF") \
            if anna else T.P["accent_edge"]
        self.theme_indicator.setStyleSheet(
            f"QFrame {{ background:{indicator_bg};"
            f"border:1px solid {indicator_edge}; border-radius:14px; }}")
        self.app_theme_label.setStyleSheet(
            f"font-size:14px;font-weight:700;color:{T.P['ink']};background:transparent;")
        for label in (self.wallpaper_path_label, self.wallpaper_hint,
                      self.wallpaper_opacity_label, self.wallpaper_blur_label):
            label.setStyleSheet(
                f"font-size:12px;color:{T.P['ink2']};background:transparent;")
        for button in (self.b_skin_import, self.b_skin_template,
                       self.b_wallpaper_pick, self.b_wallpaper_clear):
            button.refresh()
        self.cmb_clock_skin.refresh()
        self._style_settings_actions()
        self.b_qq_group.refresh()
        self._refresh_reward_code()
        self.reward_title.setStyleSheet(
            f"font-size:15px;font-weight:750;color:{T.P['ink']};background:transparent;")
        self.reward_detail.setStyleSheet(
            f"font-size:13px;color:{T.P['ink2']};background:transparent;")
        # 做题记录卡片里的标签与按钮也要跟着换色
        self.pr_lb.setStyleSheet(
            f"font-size:13px; color:{T.P['ink']}; background:transparent;")
        self.b_pr_refresh.refresh()
        self.b_pr_clear.refresh()
        for label in (getattr(self, "clock_size_label", None),
                      getattr(self, "clock_skin_label", None),
                      getattr(self, "sound_level", None)):
            if label is not None:
                label.setStyleSheet(
                    f"font-size:13px; color:{T.P['ink2']};"
                    f"background:transparent;")
        # LinkGCS2 卡片：状态文案 + 那三个按钮的图标颜色
        if hasattr(self, "gcs_lb"):
            self.gcs_lb.setStyleSheet(
                f"font-size:13px; color:{T.P['ink']}; background:transparent;"
                f"line-height:175%;")
            for b in (self.b_gcs_pick, self.b_gcs_scan, self.b_gcs_save,
                      self.b_gcs_auto):
                b.refresh()
            self._style_gcs_inputs()
            self._style_vision_api_controls()
        if pref is not None:
            self._apply_btn_states(pref)
        self.update()

    # --------------------------------------------------- 事件
    def _on_app_theme(self, theme, source=None):
        self.set_app_theme_state(theme)
        self.app_theme_request.emit(theme, source)

    def _join_qq_group(self):
        """打开 QQ 并复制群号，避免桌面版不支持的旧加群协议弹窗。"""
        group_number = "1109038020"
        QApplication.clipboard().setText(group_number)
        executable = _qq_client_executable()
        opened = False
        if executable is not None:
            try:
                subprocess.Popen(
                    [str(executable)],
                    cwd=str(executable.parent), close_fds=True)
                opened = True
            except OSError as exc:
                log.warning("直接唤起 QQ 失败：%s", exc)

        status = "已尝试打开 QQ" if opened else "未能自动打开 QQ，请手动启动"
        QMessageBox.information(
            self, "QQ 加群", f"{status}，群号 {group_number} 已复制。\n"
            "请在 QQ 搜索栏粘贴群号并申请加入。")

    def set_app_theme_state(self, theme):
        for key, button in self._app_theme_btns.items():
            button.setChecked(key == theme)
            button.update()
        if hasattr(self, "cmb_clock_skin"):
            self._reload_clock_skins()
        self._refresh_reward_code()

    def _reload_clock_skins(self):
        from skin_manager import clock_skins, valid_clock_skin
        combo = self.cmb_clock_skin
        combo.blockSignals(True)
        combo.clear()
        for key, name in clock_skins():
            combo.addItem(name, key)
        skin = valid_clock_skin(cfg.get("clock_skin", "default"))
        combo.setCurrentIndex(max(0, combo.findData(skin)))
        combo.blockSignals(False)
        self._refresh_clock_preview()

    def _refresh_clock_preview(self):
        if not hasattr(self, "clock_preview"):
            return
        from skin_manager import clock_path, clock_skins
        key = self.cmb_clock_skin.currentData() or "default"
        theme_id = key.split(":", 1)[1] if key.startswith("skin:") else key
        path = clock_path(theme_id)
        if not path:
            filename = {"anna": "clock_anna.png", "chiyo": "clock_chiyo.png"}.get(key, "clock.png")
            path = T.asset_path(filename)
        pixmap = QPixmap(path)
        self.clock_preview.setPixmap(pixmap.scaled(128, 154, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        available = "、".join(name for _, name in clock_skins())
        self.clock_unlock_hint.setText(
            f"已解锁人物：{available}\n\n导入主题包会增加对应人物；可以独立选择时钟人物和应用主题。")

    def sync_clock_skin(self, skin):
        self.cmb_clock_skin.blockSignals(True)
        self.cmb_clock_skin.setCurrentIndex(max(0, self.cmb_clock_skin.findData(skin)))
        self.cmb_clock_skin.blockSignals(False)
        self._refresh_clock_preview()

    def _refresh_reward_code(self):
        """根据当前浅色或深色模式显示对应的赞赏码海报。"""
        if not hasattr(self, "reward_code"):
            return
        filename = ("megumi-aki-reward-light.png" if T.mode == "light"
                    else "megumi-aki-reward.png")
        pixmap = QPixmap(T.asset_path("support", filename))
        if pixmap.isNull():
            self.reward_code.clear()
            self.reward_code.setText("赞赏码暂时无法读取")
            return
        self.reward_code.setText("")
        self.reward_code.setPixmap(pixmap.scaled(
            self.reward_code.size(), Qt.KeepAspectRatio,
            Qt.SmoothTransformation))

    def _reload_custom_themes(self):
        from skin_manager import list_themes, builtin_theme_available
        for key in tuple(self._app_theme_btns):
            if key.startswith("skin:"):
                button = self._app_theme_btns.pop(key)
                self._custom_theme_layout.removeWidget(button)
                button.deleteLater()
        themes = list_themes()
        for manifest in themes:
            if manifest.get("builtin_theme") in ("anna", "chiyo"):
                self._app_theme_btns[manifest["builtin_theme"]].setVisible(True)
                continue
            key = "skin:" + manifest["id"]
            button = AppThemePreviewButton(key, manifest["name"])
            button.setFixedSize(190, 142)
            button.clicked.connect(
                lambda _=False, value=key, source=button:
                self._on_app_theme(value, source))
            self._app_theme_btns[key] = button
            self._custom_theme_layout.addWidget(button)
        self.custom_theme_scroll.setVisible(
            any(not manifest.get("builtin_theme") for manifest in themes))
        for theme_id in ("anna", "chiyo"):
            self._app_theme_btns[theme_id].setVisible(
                builtin_theme_available(theme_id))
        self._settings_editor_narrow = None
        self._reflow_preferences()
        self.set_app_theme_state(cfg.get("app_theme", "default"))

    def _import_skin(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 AZIMUTH 皮肤包", "", "AZIMUTH 皮肤包 (*.aztheme)")
        if not path:
            return
        try:
            from skin_manager import import_theme
            manifest = import_theme(path)
            if not cfg.get("wallpaper_path", ""):
                self.wallpaper_opacity_slider.setValue(
                    int(manifest.get("background_opacity", 55)))
                self.wallpaper_blur_slider.setValue(
                    int(manifest.get("background_blur", 0)))
            self._reload_custom_themes()
            key = (manifest["builtin_theme"] if manifest.get("builtin_theme")
                   else "skin:" + manifest["id"])
            self._on_app_theme(key, self._app_theme_btns.get(key))
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def _export_skin_template(self):
        default = os.path.join(os.path.expanduser("~"), "Desktop",
                               "AZIMUTH_主题设计模板.aztheme")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 AZIMUTH 皮肤设计模板", default,
            "AZIMUTH 皮肤包 (*.aztheme)")
        if not path:
            return
        if not path.lower().endswith(".aztheme"):
            path += ".aztheme"
        try:
            from skin_manager import export_template
            export_template(path)
            QMessageBox.information(
                self, "模板已导出",
                "模板和设计规范已保存。解压后替换颜色、添加背景图，再压缩为 .aztheme 即可分享。")
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _choose_wallpaper(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.webp)")
        if not path:
            return
        extension = os.path.splitext(path)[1].lower()
        if extension not in (".png", ".jpg", ".jpeg", ".webp"):
            QMessageBox.warning(self, "图片格式不支持", "请选择 PNG、JPG 或 WEBP 图片。")
            return
        if os.path.getsize(path) > 18 * 1024 * 1024:
            QMessageBox.warning(self, "图片过大", "背景图片不能超过 18 MB。")
            return
        preview = QPixmap(path)
        if preview.isNull():
            QMessageBox.warning(self, "图片无法读取", "请选择一张有效的 PNG、JPG 或 WEBP 图片。")
            return
        folder = os.path.dirname(cfg.config_path())
        os.makedirs(folder, exist_ok=True)
        destination = os.path.join(folder, "azimuth_custom_background" + extension)
        try:
            if os.path.normcase(os.path.abspath(path)) != os.path.normcase(
                    os.path.abspath(destination)):
                shutil.copyfile(path, destination)
            cfg.set_("wallpaper_path", destination)
            self._sync_wallpaper_label()
            self._emit_wallpaper()
            if preview.width() < 1920 or preview.height() < 1080:
                QMessageBox.information(
                    self, "背景图已设置",
                    f"当前图片为 {preview.width()} × {preview.height()}。"
                    "为避免窗口放大后变模糊，建议使用 1920 × 1080 或更大的图片。")
        except OSError as exc:
            QMessageBox.warning(self, "设置失败", f"图片无法复制到用户数据目录：{exc}")

    def _clear_wallpaper(self):
        cfg.set_("wallpaper_path", "")
        self._sync_wallpaper_label()
        self._emit_wallpaper()

    def _sync_wallpaper_label(self):
        path = cfg.get("wallpaper_path", "")
        self.wallpaper_path_label.setText(
            os.path.basename(path) if path and os.path.isfile(path)
            else "尚未选择背景图")
        self.wallpaper_path_label.setToolTip(path or "")

    def _on_wallpaper_settings(self, _value=None, save=True):
        opacity = self.wallpaper_opacity_slider.value()
        blur = self.wallpaper_blur_slider.value()
        self.wallpaper_opacity_label.setText(f"{opacity}%")
        self.wallpaper_blur_label.setText(f"{blur}")
        if save:
            cfg.set_("wallpaper_opacity", opacity, save_now=False)
            cfg.set_("wallpaper_blur", blur, save_now=False)
            self._wallpaper_save_timer.start(180)
            self._emit_wallpaper()

    def _emit_wallpaper(self):
        if hasattr(self, 'preference_preview'):
            self.preference_preview.update_background()
        self.wallpaper_request.emit(
            cfg.get("wallpaper_path", ""),
            self.wallpaper_opacity_slider.value(),
            self.wallpaper_blur_slider.value())

    def _on_card_layout(self, _index):
        layout = self.cmb_card_layout.currentData() or "standard"
        cfg.set_("card_layout", layout)
        self.card_layout_request.emit(layout)

    def _on_sound_changed(self, _value=None):
        master = bool(self.chk_sound_master.isChecked())
        ui_sound = bool(self.chk_sound_ui.isChecked())
        volume = int(self.sound_slider.value())
        self.sound_level.setText(f"{volume}%")
        for widget in (self.chk_sound_ui, self.chk_sound_stage, self.sound_slider,
                       self.b_sound_preview, *self._sound_actions):
            widget.setEnabled(master)
        for buttons in self._sound_mode_btns.values():
            for button in buttons:
                button.setEnabled(master)
        for listing in self._sound_lists.values():
            listing.setEnabled(master)
        cfg.set_("sound_master", master)
        cfg.set_("sound_ui", ui_sound)
        cfg.set_("sound_stage", bool(self.chk_sound_stage.isChecked()))
        cfg.set_("sound_volume", volume)
        self.sound_settings_changed.emit(
            master, ui_sound, self.chk_sound_stage.isChecked(), volume)

    def _style_sound_modes(self):
        for buttons in self._sound_mode_btns.values():
            for button in buttons:
                button.setStyleSheet(
                    f"QPushButton{{color:{T.P['ink2']};background:{T.P['input_bg']};"
                    f"border:0;border-radius:{12 if T.app_theme == 'default' else 6}px;"
                    f"padding:3px 6px;font-size:{13 if T.app_theme == 'default' else 11}px;font-weight:650;}}"
                    f"QPushButton:checked{{color:#fff;background:{T.C_RED};"
                    f"border:0;}}"
                    f"QPushButton:hover{{background:{T.P['accent_soft']};}}")
                button.setMinimumHeight(38 if T.app_theme == "default" else 27)

    def _refresh_sound_lists(self):
        from sound import manager
        for kind, listing in self._sound_lists.items():
            key = "sound_tap_single" if kind == "tap" else "sound_stage_single"
            selected = cfg.get(key)
            if not selected and listing.currentItem():
                selected = listing.currentItem().data(Qt.UserRole)
            listing.blockSignals(True)
            listing.clear()
            for identifier, name, path in manager().items(kind):
                item = QListWidgetItem(name)
                item.setData(Qt.UserRole, identifier)
                item.setToolTip(str(path))
                listing.addItem(item)
                if identifier == selected:
                    listing.setCurrentItem(item)
            if listing.count() and listing.currentRow() < 0:
                listing.setCurrentRow(0)
            listing.blockSignals(False)
            if listing.currentItem() is not None:
                cfg.set_(key, listing.currentItem().data(Qt.UserRole))

    def _import_sound(self, kind):
        from sound import manager
        folder = pathlib.Path("D:/个人项目/20260808 地面站教学/AZIMUTH · 方位角/音效")
        if kind == "stage":
            folder /= "闹钟"
        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入音效" if kind == "tap" else "导入计时音乐",
            str(folder if folder.is_dir() else pathlib.Path.home()),
            "音频文件 (*.mp3 *.wav *.flac *.ogg *.m4a)")
        if paths:
            count = manager().import_files(kind, paths)
            self._refresh_sound_lists()
            self.set_status("已导入 %d 个音频文件" % count)

    def _preview_sound(self, kind):
        from sound import manager
        item = self._sound_lists[kind].currentItem()
        if item is None:
            self.set_status("先选择一条音频")
            return
        manager().preview(kind, item.data(Qt.UserRole))
        accent(self.sound_level, "wave")
        self._sound_state_timer.start()
        self._sync_sound_preview_labels()

    def _on_sound_selection(self, kind):
        listing = self._sound_lists.get(kind)
        item = listing.currentItem() if listing is not None else None
        if item is None:
            return
        key = "sound_tap_single" if kind == "tap" else "sound_stage_single"
        cfg.set_(key, item.data(Qt.UserRole))

    def _on_sound_mode(self, kind, mode, checked):
        if checked:
            cfg.set_(f"sound_{kind}_mode", mode)

    def _sync_sound_preview_labels(self):
        from sound import manager
        sound = manager()
        from PySide6.QtMultimedia import QMediaPlayer
        tap_playing = bool(sound._tap_preview_effect and
                           sound._tap_preview_effect.isPlaying())
        stage_playing = sound._stage_player.playbackState() == QMediaPlayer.PlayingState
        self._sound_preview_btns["tap"].setText(
            "暂停试听" if tap_playing else "试听")
        self._sound_preview_btns["stage"].setText(
            "暂停试听" if stage_playing and sound._stage_preview_identifier
            else "试听")
        if not tap_playing and not stage_playing:
            self._sound_state_timer.stop()

    def _remove_sound(self, kind):
        from sound import manager
        item = self._sound_lists[kind].currentItem()
        if item is None:
            return
        identifier = item.data(Qt.UserRole)
        if identifier.startswith("custom:"):
            answer = QMessageBox.question(
                self, "删除已导入音效", "删除软件内的「%s」副本？源文件不会受到影响。" % item.text(),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        if manager().remove(identifier):
            for key in ("sound_tap_single", "sound_stage_single"):
                if cfg.get(key) == identifier:
                    cfg.set_(key, None)
            self._refresh_sound_lists()
            self.set_status("音效已从列表移除")

    def _restore_sound(self, kind):
        from sound import manager
        manager().restore_builtin(kind)
        self._refresh_sound_lists()

    def _on_sound_volume_preview(self, volume):
        self.sound_level.setText(f"{int(volume)}%")
        # 拖动时只更新界面，停手后再写配置文件，避免每帧同步落盘。
        self._sound_save_timer.start()

    def _on_clock_scale(self, v):
        self.cscale_lb.setText("%d%%" % v)
        self.clock_scale_request.emit(v / 100.0)

    def _style_setting_sliders(self):
        style = (
            f"QSlider::groove:horizontal{{height:4px;background:{T.P['input_border']};"
            "border-radius:2px;}"
            f"QSlider::sub-page:horizontal{{background:{T.C_BLUE};border-radius:2px;}}"
            f"QSlider::handle:horizontal{{width:16px;height:16px;margin:-6px 0;"
            f"border-radius:8px;background:{T.C_BLUE};border:2px solid {T.P['glass']};}}"
            f"QSlider::handle:horizontal:disabled{{background:{T.P['ink3']};}}")
        for slider in (getattr(self, "sslider", None),
                       getattr(self, "cslider", None),
                       getattr(self, "sound_slider", None)):
            if slider is not None:
                slider.setStyleSheet(style)

    def _on_clock_switch(self, on):
        self._sync_clock_enabled()
        cfg.set_("clock_on", bool(on))
        self.clock_toggle.emit(bool(on))

    @staticmethod
    def _bind_switch_label(label, switch):
        """让卡片标题也成为开关点击入口，键盘仍由开关本身处理。"""
        label.setCursor(Qt.PointingHandCursor)
        label.setToolTip("点击切换")
        toggle_filter = _SwitchLabelFilter(switch, label)
        label._switch_toggle_filter = toggle_filter
        label.installEventFilter(toggle_filter)

    def _sync_clock_enabled(self):
        if not hasattr(self, "cslider"):
            return
        on = self.chk_clock.isChecked()
        for widget in (self.cslider, self.cscale_lb):
            widget.setEnabled(on)

    # --------------------------------------------------- 窗口阴影
    def _sync_shadow_enabled(self):
        """关掉阴影后，强度滑块就没有意义了，一并置灰"""
        on = self.chk_shadow.isChecked()
        for w in (self.sslider, self.slevel_lb, self.shadow_lb,
                  self.b_shadow_reset):
            w.setEnabled(on)

    def _on_motion_switch(self, enabled):
        cfg.set_("animations_on", enabled)
        if not enabled:
            self.cancel_intro()

    def _on_shadow_switch(self, on):
        self._sync_shadow_enabled()
        cfg.set_("shadow_on", bool(on))
        self.shadow_request.emit(bool(on), self.sslider.value() / 100.0)

    def _on_shadow_strength(self, v):
        self.slevel_lb.setText("%d%%" % v)
        self.shadow_request.emit(self.chk_shadow.isChecked(), v / 100.0)

    def sync_shadow_ui(self, on, strength):
        """页面重建 / 外部改动后回写控件状态（不触发信号，避免来回弹）"""
        self.chk_shadow.blockSignals(True)
        self.chk_shadow.setChecked(bool(on))
        self.chk_shadow.blockSignals(False)
        self.sslider.blockSignals(True)
        self.sslider.setValue(int(round(float(strength) * 100)))
        self.sslider.blockSignals(False)
        self.slevel_lb.setText("%d%%" % self.sslider.value())
        self._sync_shadow_enabled()

    def sync_clock_ui(self, on):
        self.chk_clock.blockSignals(True)
        self.chk_clock.setChecked(bool(on))
        self.chk_clock.blockSignals(False)
        self._sync_clock_enabled()

    # --------------------------------------------------- 数据
    def set_bank_info(self, text):
        self.bank_lb.setText(text)

    def do_export(self):
        dflt = os.path.join(
            os.path.expanduser("~"), "Desktop",
            "GroundStation日志_%s.zip"
            % datetime.datetime.now().strftime("%Y%m%d_%H%M"))
        path, _ = QFileDialog.getSaveFileName(
            self, "导出日志", dflt, "Zip 压缩包 (*.zip)")
        if not path:
            return
        try:
            export_logs(path)
            QMessageBox.information(self, "导出成功", "日志已导出到：\n" + path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
        except Exception as e:
            log.error("导出日志失败 %r", e)
            QMessageBox.critical(self, "导出失败", str(e))

    def open_logs(self):
        from logger import log_dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(log_dir()))

    def do_uninstall(self):
        r = QMessageBox.warning(
            self, "确认卸载",
            "即将执行：\n\n· 删除桌面 / 开始菜单快捷方式\n· 删除配置、日志、缓存\n\n"
            "此操作不可撤销，确定继续吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        log.warning("用户确认卸载")
        from uninstall import run_uninstall
        run_uninstall(self)

    def eventFilter(self, watched, event):
        if watched is getattr(self, 'theme_host', None) and event.type() in (QEvent.Resize, QEvent.Show):
            QTimer.singleShot(0, lambda: self._move_theme_indicator(self._theme_pref, False))
        return super().eventFilter(watched, event)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._reflow_preferences()
        side_margin = 0
        if side_margin != self._settings_root_side_margin:
            self._settings_root_side_margin = side_margin
            self._settings_root_layout.setContentsMargins(
                side_margin, 0, side_margin, 0)
        if hasattr(self, "theme_host"):
            QTimer.singleShot(
                0, lambda pref=self._theme_pref:
                self._move_theme_indicator(pref, False))
