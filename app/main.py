# -*- coding: utf-8 -*-
"""
CAAC Ground Station —— 主程序
视觉：苹果液态玻璃（预模糊背景 + 半透明玻璃面板 + 高光描边）

功能：
  1. 计时考试：1/2 分钟阅题倒计时 → 作答倒计时 → 到时置顶提醒 + 确定解锁
  2. 用户导入题卡：检索、筛选、练习与题卡工作台
  3. 桌面时钟：悬浮显示当前倒计时，手写体、可缩放、可开关
  4. 设置维护：深浅色主题、理论缩放、日志导出、免责声明、卸载
"""
import os
import sys

# 子进程只加载 Qt 视频播放组件，主程序繁重的页面模块不会拖慢动画帧。
if len(sys.argv) >= 4 and sys.argv[1] == "--splash-helper":
    from splash_process import run_splash_helper
    raise SystemExit(run_splash_helper(sys.argv[2], sys.argv[3]))

import subprocess
import json
import math
import random
import datetime
import time
import threading

from PySide6.QtCore import (Qt, QTimer, QSize, QRect, QRectF, QPoint, QPointF,
                            QPropertyAnimation, QEasingCurve, QUrl, Signal,
                            QParallelAnimationGroup, QEvent, Property, QTime)
from PySide6.QtGui import (QIcon, QPixmap, QFont, QColor, QPainter, QPen, QBrush,
                           QLinearGradient, QRadialGradient, QFontDatabase,
                           QDesktopServices, QCursor, QPainterPath, QKeySequence,
                            QShortcut, QFontMetricsF, QImage)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame, QStackedWidget, QListWidget, QListWidgetItem,
    QTextBrowser, QSplitter, QLineEdit, QMessageBox, QDialog, QScrollArea,
    QSizePolicy, QGraphicsEffect, QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QGraphicsBlurEffect,
    QSpacerItem, QFileDialog, QProgressBar, QCheckBox, QLayout, QSlider,
    QRadioButton, QButtonGroup, QTimeEdit, QDialogButtonBox)

import theme as T
from flight_motion import CompanionGesture
from motion_polish import PhaseRelay, accent
from anna_art import AnnaHeaderArt
import config as cfg
from industrial_details import (IndustrialPageFooter, IndustrialPageMeta,
                                paint_matrix_text)
from logger import get_logger, export_logs, app_root, APP_NAME, APP_VER
from ui_kit import (Icons, glass, glass_style, refresh_glass, btn_style,
                    IconButton, Combo, add_shadow, attach_shadow, replay_shadow,
                    fade_in, slide_fade, icon_label, repaint_icon,
                    animations_enabled, ToggleSwitch)
from motion_runtime import motion_page

log = get_logger()


def A(*p):
    return T.asset_path(*p)


APP_TITLE = "AZIMUTH"
DISCLAIMER = ("免责声明：本软件本身完全免费，不收取任何费用。使用 API 解题时产生的费用由 API 服务商收取，"
              "与本软件作者无关。请自行确认 API 服务商的计费规则并承担相应费用。\n\n"
              "软件生成内容仅供参考，请自行核验答案。\nby Gomigosan")


# ================================================================ 背景层
class _WallpaperImage(QWidget):
    """只处理壁纸本身的透明度和模糊，不影响上层文字与控件。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.pixmap = None
        self.opacity = 38
        self.effect = QGraphicsBlurEffect(self)
        self.effect.setBlurHints(QGraphicsBlurEffect.QualityHint)
        self.setGraphicsEffect(self.effect)

    def set_image(self, path, opacity, blur):
        self.pixmap = QPixmap(path) if path and os.path.isfile(path) else None
        if self.pixmap is not None and self.pixmap.isNull():
            self.pixmap = None
        self.opacity = max(0, min(100, int(opacity)))
        self.effect.setBlurRadius(max(0, min(24, int(blur))))
        self.update()

    def paintEvent(self, event):
        if self.pixmap is None:
            return
        stage = self.parentWidget().parentWidget()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setClipPath(stage._clip_path())
        painter.setOpacity(self.opacity / 100.0)
        source = self.pixmap
        scale = max(self.width() / max(1, source.width()),
                    self.height() / max(1, source.height()))
        crop_width = self.width() / scale
        crop_height = self.height() / scale
        source_rect = QRectF((source.width() - crop_width) / 2,
                             (source.height() - crop_height) / 2,
                             crop_width, crop_height)
        painter.drawPixmap(QRectF(self.rect()), source, source_rect)
        painter.end()


class _WallpaperDecor(QWidget):
    """壁纸上方的千代点阵装饰。"""

    def __init__(self, stage, parent):
        super().__init__(parent)
        self.stage = stage
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):
        if T.app_theme != "chiyo":
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setClipPath(self.stage._clip_path())
        base = QColor("#E43E55" if T.mode == "light" else "#E5DED5")
        alpha = 30 if T.mode == "light" else 22
        clusters = ((self.width() - 142, 30, 124, 70),
                    (self.width() - 142, self.height() - 118, 124, 70))
        for left, top, width, height in clusters:
            columns, rows = 14, 8
            for row in range(rows):
                y_fade = min(1.0, (row + 1) / 3, (rows - row) / 3)
                for col in range(columns):
                    dot = QColor(base)
                    dot.setAlpha(round(alpha * ((col + 1) / columns) * y_fade))
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(dot)
                    painter.drawEllipse(QPointF(
                        left + col * (width / columns),
                        top + row * (height / rows)), 1.35, 1.35)
        painter.end()


class _WallpaperLayer(QWidget):
    """位于主舞台背景与实际页面内容之间的壁纸层。"""

    def __init__(self, stage):
        super().__init__(stage)
        self.stage = stage
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.image = _WallpaperImage(self)
        self.decor = _WallpaperDecor(stage, self)
        self.image.lower()
        self.decor.raise_()
        self.hide()

    def resizeEvent(self, event):
        self.image.setGeometry(self.rect())
        self.decor.setGeometry(self.rect())
        super().resizeEvent(event)

    def set_wallpaper(self, path, opacity, blur):
        self.image.set_image(path, opacity, blur)
        self.setVisible(bool(self.image.pixmap))
        self.decor.update()


class StageWidget(QWidget):
    """承托所有内容的主舞台，自己绘制背景。
    图层顺序天然正确（widget 背景先画，子控件后画），无层级竞争。

    背景构成：渐变底 → BG1 极淡铺底 → 左侧柔光 → 右下淡色晕。
    注意：角色只出现一次（BG1 原图里右侧已有主体，这里不再叠加另一份立绘），
    避免出现"两个角色"的重复感。

    圆角：整个绘制区域先用圆角路径裁剪，保证左下 / 右下也是圆角
    （只给外壳设 border-radius 是不够的 —— 背景由本控件自绘，会盖成直角）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.bg = None
        # 四个角：左上/右上被标题栏覆盖，主要靠这里保证四角一致
        self.corner = 22
        self._mosaic_pos = QPointF(-10000, -10000)
        self._mosaic_layer = None
        self.bottom_left_square = False   # 侧栏要不要盖住左下圆角
        self._load()

        self.wallpaper_layer = _WallpaperLayer(self)
        self.wallpaper_layer.lower()

    def set_custom_background(self, path="", opacity=38, blur=0,
                              theme_key=None):
        selected_theme = theme_key or T.app_theme
        selected_path = path if path and os.path.isfile(path) else ""
        if not selected_path and isinstance(selected_theme, str) \
                and selected_theme.startswith("skin:"):
            try:
                from skin_manager import background_path
                selected_path = background_path(
                    selected_theme.split(":", 1)[1], T.mode)
            except (ImportError, OSError, ValueError):
                selected_path = ""
        self.wallpaper_layer.set_wallpaper(selected_path, opacity, blur)
        self.wallpaper_layer.setGeometry(self.rect())
        self.wallpaper_layer.lower()
        self.update()

    def set_corner(self, r):
        self.corner = r
        self.update()
        if self._mosaic_layer is not None:
            self._mosaic_layer.update()

    def _clip_path(self):
        """背景裁剪路径：只圆【下方】两角。

        stage 的上边缘位于窗口内部（紧邻标题栏），那里本来就不该有圆角。
        之前四角全圆，等于在标题栏下方挖掉两个圆角缺口 —— 用户看到的就是
        「遮罩上方不应该圆角的地方圆角了」。

        多个子路径叠加必须显式用 WindingFill：默认的 OddEvenFill 下，
        圆角矩形与补角方块的重叠区环绕数为偶数，会被判成「外部」，
        反而在角上挖出楔形空洞。
        """
        w, h = self.width(), self.height()
        r = max(0.0, float(self.corner))
        path = QPainterPath()
        path.setFillRule(Qt.WindingFill)
        if r <= 0.5:
            path.addRect(QRectF(0, 0, w, h))
            return path
        path.addRoundedRect(QRectF(0, 0, w, h), r, r)
        path.addRect(QRectF(0, 0, r, r))            # 左上补成直角
        path.addRect(QRectF(w - r, 0, r, r))        # 右上补成直角
        return path

    def _load(self):
        p = A("bg.png")
        self.bg = QPixmap(p) if os.path.exists(p) else None

    def theme_changed(self, pref=None):
        # 背景图与主题无关，沿用初始化时已解码的图像，避免每次换肤同步读盘。
        self.update()
        self.wallpaper_layer.decor.update()
        self.set_custom_background(
            cfg.get("wallpaper_path", ""),
            cfg.get("wallpaper_opacity", 38),
            cfg.get("wallpaper_blur", 0), T.app_theme)
        if self._mosaic_layer is not None:
            self._mosaic_layer.update()

    def set_mosaic_cursor(self, point):
        point = QPointF(point)
        delta = point - self._mosaic_pos
        if abs(delta.x()) + abs(delta.y()) < 5:
            return
        old = self._mosaic_pos
        self._mosaic_pos = point
        radius = 178
        for position in (old, point):
            if position.x() > -1000:
                dirty = QRect(int(position.x() - radius), int(position.y() - radius),
                              radius * 2, radius * 2)
                if self._mosaic_layer is not None:
                    self._mosaic_layer.update(dirty)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        P = T.P

        # 0) 圆角裁剪：背景只在下两角收圆，上两角保持直角
        p.setClipPath(self._clip_path())

        # 练习区域使用稳定底色，题卡和文字保持清晰。
        p.fillRect(self.rect(), QColor(P["shell"]))
        if (T.app_theme == "chiyo" and
                self.wallpaper_layer.image.pixmap is None):
            # 网点只落在舞台边角，让底色有漫画纸面的细节，但不压正文。
            base = QColor("#E43E55" if T.mode == "light" else "#E5DED5")
            base_alpha = 30 if T.mode == "light" else 22
            clusters = (
                (self.width() - 142, 30, 124, 70),
                (self.width() - 142, self.height() - 118, 124, 70),
            )
            p.setPen(Qt.NoPen)
            for left, top, width, height in clusters:
                columns, rows = 14, 8
                for row in range(rows):
                    y_fade = min(1.0, (row + 1) / 3, (rows - row) / 3)
                    for col in range(columns):
                        x_fade = (col + 1) / columns
                        dot = QColor(base)
                        dot.setAlpha(round(base_alpha * x_fade * y_fade))
                        p.setBrush(dot)
                        p.drawEllipse(QPointF(
                            left + col * (width / columns),
                            top + row * (height / rows)), 1.35, 1.35)
        p.end()



    def resizeEvent(self, event):
        self.wallpaper_layer.setGeometry(self.rect())
        super().resizeEvent(event)


class MosaicOverlay(QWidget):
    """鼠标跟随的轻量马赛克装饰层，不拦截控件操作。"""

    def __init__(self, stage, parent=None):
        super().__init__(parent or stage)
        self.stage = stage
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        stage._mosaic_layer = self
        self.setGeometry(stage.rect())
        self.show()

    def paintEvent(self, ev):
        center = self.stage._mosaic_pos
        radius = 178
        if not (-radius < center.x() < self.width() + radius
                and -radius < center.y() < self.height() + radius):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setClipPath(self.stage._clip_path())
        halo = QRadialGradient(center, radius)
        tint = QColor("#D96A8A" if T.mode == "light" else "#758FFF")
        tint.setAlpha(15 if T.mode == "light" else 21)
        edge = QColor(tint)
        edge.setAlpha(0)
        halo.setColorAt(0.0, tint)
        halo.setColorAt(1.0, edge)
        p.fillRect(QRectF(center.x() - radius, center.y() - radius,
                          radius * 2, radius * 2), halo)
        step = 15
        left = int((center.x() - radius) // step * step)
        top = int((center.y() - radius) // step * step)
        p.setPen(Qt.PenStyle.NoPen)
        for y in range(top, int(center.y() + radius), step):
            for x in range(left, int(center.x() + radius), step):
                dx, dy = x + 2 - center.x(), y + 2 - center.y()
                distance = (dx * dx + dy * dy) ** .5
                if distance >= radius:
                    continue
                alpha = int((1 - distance / radius) ** 1.7 * 32)
                dot = QColor("#B74E72" if T.mode == "light" else "#91A7FF")
                dot.setAlpha(alpha)
                p.setBrush(dot)
                p.drawRoundedRect(QRectF(x, y, 4, 4), 1, 1)
        p.end()


# ================================================================ 启动画面
class SplashScreen(QWidget):
    """以圆角窗口播放品牌启动动画，媒体不可用时显示静态海报。"""

    playback_finished = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.SplashScreen | Qt.FramelessWindowHint |
                            Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.pm = QPixmap(A("splash-poster.png"))
        if self.pm.isNull():
            self.pm = QPixmap(A("splash.png"))
        if self.pm.isNull():
            self.setFixedSize(760, 340)
        else:
            dpr = self.pm.devicePixelRatio() or 1.0
            w = min(int(self.pm.width() / dpr), 1180)
            sc = self.pm.scaledToWidth(
                w, Qt.SmoothTransformation) if w < self.pm.width() else self.pm
            self.pm = sc
            self.setFixedSize(self.pm.width(), self.pm.height())

        self._corner_radius = 24
        self._video_frame = QImage()
        self._video_player = None
        self._video_sink = None
        self._video_started = False
        self._use_static_fallback = False
        self._is_complete = False
        self._end_of_media = None
        self._invalid_media = None
        self._first_frame_timer = QTimer(self)
        self._first_frame_timer.setSingleShot(True)
        self._first_frame_timer.setInterval(2200)
        self._first_frame_timer.timeout.connect(self._show_static_fallback)
        self._playback_watchdog = QTimer(self)
        self._playback_watchdog.setSingleShot(True)
        self._playback_watchdog.setInterval(10000)
        self._playback_watchdog.timeout.connect(self._finish_or_fallback)

        self._center()
        self.setWindowOpacity(0.0)
        self._fade = QPropertyAnimation(self, b"windowOpacity")
        self._fade.setDuration(420)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)

    def _center(self):
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - self.width() // 2,
                  scr.center().y() - self.height() // 2)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        bounds = QRectF(self.rect())
        rounded = QPainterPath()
        rounded.addRoundedRect(bounds, self._corner_radius,
                               self._corner_radius)
        p.setClipPath(rounded)
        if not self._video_frame.isNull() and not self._use_static_fallback:
            p.drawImage(bounds, self._video_frame)
        elif not self.pm.isNull():
            p.drawPixmap(self.rect(), self.pm)
        p.end()

    def showEvent(self, ev):
        super().showEvent(ev)
        if animations_enabled():
            self._fade.start()
        else:
            self.setWindowOpacity(1.0)
        QTimer.singleShot(0, self._start_video)

    @property
    def is_complete(self):
        return self._is_complete

    def _start_video(self):
        if self._is_complete or self._video_player is not None:
            return
        video_path = A("splash.mp4")
        if not os.path.isfile(video_path):
            self._show_static_fallback()
            return
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

            self._end_of_media = QMediaPlayer.MediaStatus.EndOfMedia
            self._invalid_media = QMediaPlayer.MediaStatus.InvalidMedia
            self._video_sink = QVideoSink(self)
            self._video_sink.videoFrameChanged.connect(self._on_video_frame)
            self._video_player = QMediaPlayer(self)
            self._video_player.setVideoSink(self._video_sink)
            self._video_player.mediaStatusChanged.connect(
                self._on_media_status_changed)
            self._video_player.errorOccurred.connect(self._on_media_error)
            self._video_player.setSource(QUrl.fromLocalFile(video_path))
            self._video_player.play()
            self._first_frame_timer.start()
            self._playback_watchdog.start()
        except Exception:
            self._show_static_fallback()

    def _on_video_frame(self, frame):
        if frame is None or not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        self._video_frame = image
        self._video_started = True
        self._first_frame_timer.stop()
        self.update()

    def _on_media_status_changed(self, status):
        if status == self._end_of_media:
            self._mark_playback_finished()
        elif status == self._invalid_media:
            if self._video_started:
                self._mark_playback_finished()
            else:
                self._show_static_fallback()

    def _on_media_error(self, *args):
        if self._video_started:
            self._mark_playback_finished()
        else:
            self._show_static_fallback()

    def _show_static_fallback(self):
        if self._is_complete or self._video_started:
            return
        self._use_static_fallback = True
        if self._video_player is not None:
            self._video_player.stop()
        self.update()
        self._mark_playback_finished()

    def _finish_or_fallback(self):
        if self._video_started:
            self._mark_playback_finished()
        else:
            self._show_static_fallback()

    def _mark_playback_finished(self):
        if self._is_complete:
            return
        self._is_complete = True
        self._first_frame_timer.stop()
        self._playback_watchdog.stop()
        self.playback_finished.emit()

    def mousePressEvent(self, ev):
        """点一下可以提前跳过"""
        if not self._video_started:
            self._use_static_fallback = True
            self.update()
        if self._video_player is not None:
            self._video_player.stop()
        self._mark_playback_finished()

    def closeEvent(self, ev):
        self._first_frame_timer.stop()
        self._playback_watchdog.stop()
        if self._video_player is not None:
            self._video_player.stop()
        super().closeEvent(ev)


# ================================================================ 圆环计时
@motion_page
class RingTimer(QWidget):
    """共享数字滚轮倒计时；保留计时接口供三种主题与真实时钟驱动"""

    def __init__(self, total=360, title="作答倒计时"):
        super().__init__()
        T.load_app_fonts()
        self.total = total
        self.remain = total
        self.title = title
        self.status = "准备开始"
        self.subtitle = ""
        self.idle = True
        self.setMinimumSize(240, 280)
        self.setAccessibleName("训练倒计时")
        # 平滑显示值：真值突变时做缓动过渡
        self._shown = float(total)
        self._anim = QPropertyAnimation(self, b"shown", self)
        self._anim.setDuration(520)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._stage_feedback = 1.0
        self._stage_motion = QPropertyAnimation(self, b"stageFeedback", self)
        self._stage_motion.setDuration(650)
        self._stage_motion.setEasingCurve(QEasingCurve.OutCubic)
        self._digit_before = int(total)
        self._digit_t = 1.
        self._digit_motion = QPropertyAnimation(self, b'digitProgress', self)
        self._digit_motion.setDuration(680)
        self._digit_motion.setEasingCurve(QEasingCurve.InOutCubic)

    def _get_digit_progress(self):
        return self._digit_t

    def _set_digit_progress(self, value):
        self._digit_t = float(value)
        self.update()

    digitProgress = Property(float, _get_digit_progress, _set_digit_progress)

    def _countdown_font(self, pixels):
        font = QFont('Alibaba PuHuiTi 2.0')
        font.setPixelSize(round(pixels))
        font.setWeight(QFont.Bold)
        font.setItalic(False)
        font.setStyle(QFont.StyleNormal)
        font.setStyleName('85 Bold')
        return font

    def _roller_geometry(self):
        """字号同时受宽高约束，缩小窗口时仍给单位和说明留出空间。"""
        width, height = self.width(), self.height()
        digits = '%02d:%02d' % (self.remain // 60, self.remain % 60)
        size = max(28, min(220, width / (len(digits) * .66 + .4), height * .46))
        font = self._countdown_font(size)
        metrics = QFontMetricsF(font)
        cell = max(metrics.horizontalAdvance(str(i)) for i in range(10)) + size * .025
        colon = size * .40
        span = cell * (len(digits) - 1) + colon
        center = height * .46
        return font, cell, colon, QRectF((width - span) / 2, center - size * .72,
                                        span, size * 1.44)

    def _roller_rows(self, old, new, pitch):
        """真实数字相邻秒滚动；重置和加时直接交接，避免虚构中间秒数。"""
        if old == new or self._digit_t >= 1:
            return [(new, 0., 1.)]
        direction = 1 if self.remain < self._digit_before else -1
        shift = direction * self._digit_t * pitch
        rows = [(old, -shift), (new, direction * pitch - shift)]
        return [(digit, offset, max(0., 1. - abs(offset) / (pitch * 1.10)) ** 1.4)
                for digit, offset in rows]

    def _paint_time_digits(self, painter, box):
        """每列完整滚过一个字高，中心清晰、两端渐隐，不移动布局。"""
        current = '%02d:%02d' % (self.remain // 60, self.remain % 60)
        previous = '%02d:%02d' % (self._digit_before // 60, self._digit_before % 60)
        if len(previous) != len(current):
            previous = current
        metrics = QFontMetricsF(painter.font())
        size = painter.font().pixelSize()
        width = max(metrics.horizontalAdvance(str(i)) for i in range(10)) + size * .025
        colon = size * .40
        pitch = metrics.height() * .86
        total = (len(current) - 1) * width + colon
        x = box.center().x() - total / 2
        # 仅运动中分配小型透明图层，使用透明度蒙版形成真正的上下渐隐。
        moving = self._digit_t < 1 and previous != current
        target = painter
        if moving:
            dpr = self.devicePixelRatioF()
            layer = QImage(round(box.width() * dpr) + 2, round(box.height() * dpr) + 2,
                           QImage.Format_ARGB32_Premultiplied)
            layer.setDevicePixelRatio(dpr)
            layer.fill(Qt.transparent)
            target = QPainter(layer)
            target.setRenderHint(QPainter.TextAntialiasing)
            target.setFont(painter.font())
            target.setPen(painter.pen())
            target.translate(-box.x(), -box.y())
        target.save()
        target.setClipRect(box)
        for old, new in zip(previous, current):
            cell_width = colon if new == ':' else width
            cell = QRectF(x, box.center().y() - pitch / 2, cell_width, pitch)
            for digit, offset, opacity in self._roller_rows(old, new, pitch):
                target.setOpacity(opacity)
                target.drawText(cell.translated(0, offset), Qt.AlignCenter, digit)
            x += cell_width
        target.restore()
        if moving:
            target.setOpacity(1.)
            target.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            fade = QLinearGradient(0, box.top(), 0, box.bottom())
            fade.setColorAt(0., QColor(255, 255, 255, 0))
            fade.setColorAt(.25, QColor(255, 255, 255, 255))
            fade.setColorAt(.75, QColor(255, 255, 255, 255))
            fade.setColorAt(1., QColor(255, 255, 255, 0))
            target.fillRect(box, fade)
            target.end()
            painter.drawImage(box.topLeft(), layer)

    def hideEvent(self, event):
        self._digit_motion.stop(); self._digit_t = 1.
        self._stage_motion.stop(); self._stage_feedback = 1.
        self._anim.stop(); self._shown = float(self.remain)
        super().hideEvent(event)

    # 供动画驱动的属性
    # 必须用 Qt 的 Property，不能用 Python 内建 property：
    # QPropertyAnimation 走 Qt 元对象系统，内建 property 它根本看不见，
    # 只会静默报 "trying to animate a non-existing property shown" ——
    # 圆环的缓动就一直是失效的（数字硬跳）。
    def _get_shown(self):
        return float(self._shown)

    def _set_shown(self, v):
        self._shown = float(v)
        self.update()
        if hasattr(self, 'runway'):
            self.runway.update()

    shown = Property(float, _get_shown, _set_shown)

    def _get_stage_feedback(self):
        return self._stage_feedback

    def _set_stage_feedback(self, value):
        self._stage_feedback = float(value)
        self.update()

    stageFeedback = Property(float, _get_stage_feedback, _set_stage_feedback)

    def _paint_stage_feedback(self):
        if self._stage_feedback >= 1:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        progress = self._stage_feedback
        color = QColor(T.P["accent_fg"])
        color.setAlpha(round(90 * (1 - progress)))
        painter.setPen(QPen(color, 2))
        painter.setBrush(Qt.NoBrush)
        radius = min(self.width(), self.height()) * (.40 + .055 * progress)
        center = QRectF(self.rect()).center()
        rect = QRectF(center.x() - radius, center.y() - radius,
                      radius * 2, radius * 2)
        if T.app_theme == "industrial":
            painter.drawRoundedRect(rect, 6, 6)
        else:
            painter.drawEllipse(rect)
        painter.end()

    def set_total(self, s, animate=True, roll=False):
        previous = self.remain
        self._digit_motion.stop()
        self.total = max(0, int(s))
        self.remain = self.total
        rolling = (roll and previous != self.remain and self.isVisible()
                   and animations_enabled())
        self._digit_before = previous if rolling else self.remain
        self._digit_t = 0. if rolling else 1.
        if rolling:
            # 时长选择用缓入缓出的曲线，让数字滚动更从容地起步和落位。
            self._digit_motion.setDuration(720)
            self._digit_motion.setEasingCurve(QEasingCurve.OutCubic)
            self._digit_motion.setStartValue(0.)
            self._digit_motion.setEndValue(1.)
            self._digit_motion.start()
        self.setAccessibleDescription('%02d 分 %02d 秒' % (self.remain // 60, self.remain % 60))
        if animate and animations_enabled() and self.isVisible():
            self._anim.stop()
            self._anim.setDuration(520)
            self._anim.setEasingCurve(QEasingCurve.OutCubic)
            self._anim.setStartValue(self._shown)
            self._anim.setEndValue(float(s))
            self._anim.start()
        else:
            self._shown = float(s)
            self.update()

    def set_remain(self, s):
        before = self.remain
        self.remain = max(0, int(s))
        self.setAccessibleDescription('%02d 分 %02d 秒' % (self.remain // 60, self.remain % 60))
        if before != self.remain:
            self._digit_motion.stop()
            self._digit_before = before
            if self.isVisible() and animations_enabled():
                self._digit_motion.setDuration(680)
                self._digit_motion.setEasingCurve(QEasingCurve.InOutCubic)
                self._digit_motion.setStartValue(0.)
                self._digit_motion.setEndValue(1.)
                self._digit_motion.start()
            else:
                self._digit_t = 1.
        if not animations_enabled() or not self.isVisible():
            self._shown = float(self.remain)
            self.update()
            if hasattr(self, 'runway'):
                self.runway.update()
            return
        self._anim.stop()
        self._anim.setDuration(880)
        self._anim.setEasingCurve(QEasingCurve.Linear)
        self._anim.setStartValue(self._shown)
        self._anim.setEndValue(float(self.remain))
        self._anim.start()

    def set_idle(self, idle):
        self.idle = bool(idle)
        self.update()
        if hasattr(self, 'runway'):
            self.runway.update()

    def set_copy(self, status, title, subtitle):
        changed = (self.status, self.title) != (status, title)
        self.status = status
        self.title = title
        self.subtitle = subtitle
        if changed:
            self._stage_motion.stop()
            if self.isVisible() and animations_enabled():
                self._stage_motion.setStartValue(0.0)
                self._stage_motion.setEndValue(1.0)
                self._stage_motion.start()
            else:
                self._stage_feedback = 1.0
        self.update()

    def paintEvent(self, ev):
        if T.app_theme == 'industrial':
            self._paint_frequency_dial()
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        font, cell, colon, box = self._roller_geometry()
        size = font.pixelSize()
        width = self.width()
        ink = QColor(T.P['ink'])
        secondary = QColor(T.P['ink2'])
        accent = QColor(T.C_RED if self.remain == 0 and not self.idle
                        else T.C_AMBER if 0 < self.remain <= 10 and not self.idle
                        else T.P['accent_fg'])
        def text(value, y, height, pixels, color, bold=False):
            face = self._countdown_font(pixels)
            p.setFont(face)
            p.setPen(color)
            p.drawText(QRectF(10, y, width - 20, height), Qt.AlignCenter, value)
        # 状态文字只做轻量交接，不再绘制与数字重叠的圆环反馈。
        lift = (1 - self._stage_feedback) * 6
        p.setOpacity(.60 + .40 * self._stage_feedback)
        text(self.status, max(2, box.top() - size * .31 + lift), size * .23,
             max(15, min(28, size * .18)), secondary)
        p.setOpacity(1.)
        p.setFont(font)
        p.setPen(accent if not self.idle and self.remain <= 10 else ink)
        self._paint_time_digits(p, box)
        # 单位由冒号表达，腾出的空间用于扩大数字与保持说明间距。
        title_y = box.bottom() + size * .10
        text(self.title, title_y, size * .19, max(14, min(24, size * .16)), ink, True)
        text(self.subtitle, title_y + size * .22, size * .14,
             max(11, min(18, size * .12)), secondary)
        p.end()

    def _paint_frequency_dial(self):
        """工业极简皮肤用矩阵数字与调频刻度显示倒计时。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        width, height = self.width(), self.height()
        dark = T.mode == "dark"
        ink = QColor("#F3F5F5" if dark else "#15191C")
        muted = QColor("#A0AAAE" if dark else "#70787D")
        tick = QColor("#738085" if dark else "#92999D")
        accent = QColor("#FF453E" if dark else "#F1342D")

        p.setPen(muted)
        meta = QFont("Consolas", max(8, int(min(width, height) * .022)))
        meta.setLetterSpacing(QFont.AbsoluteSpacing, 2.0)
        p.setFont(meta)
        p.drawText(QRectF(4, 3, width * .42, 22),
                   Qt.AlignLeft | Qt.AlignVCenter, "TRAINING TIMER")
        p.drawText(QRectF(width * .55, 3, width * .42 - 4, 22),
                   Qt.AlignRight | Qt.AlignVCenter, "FREQUENCY TUNING")

        digits = "%02d:%02d" % (self.remain // 60, self.remain % 60)
        digit_area = QRectF(width * .04, max(34.0, height * .13), width * .73,
                            height * .58)
        paint_matrix_text(p, digits, digit_area, ink, max_pitch=18.0,
                          width_ratio=.98, height_ratio=.94, dot_ratio=.84)
        p.setPen(muted)
        small = QFont("Consolas", max(8, int(min(width, height) * .019)))
        small.setLetterSpacing(QFont.AbsoluteSpacing, .7)
        p.setFont(small)
        meta_x = width * .81
        meta_w = width * .18
        meta_y = digit_area.y() + digit_area.height() * .30
        p.drawText(QRectF(meta_x, meta_y, meta_w, 65),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   "CAAC\nGROUND STATION\nSIMULATION")
        p.setPen(tick)
        p.drawLine(QPointF(meta_x, meta_y + 77),
                   QPointF(meta_x + 30, meta_y + 77))
        p.setPen(muted)
        p.drawText(QRectF(meta_x, meta_y + 84, meta_w, 65),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   "FOCUS\nPRACTICE\nIMPROVE")

        ruler_y = height * .77
        left, right = 18.0, width - 18.0
        p.setPen(QPen(tick, 1))
        for index in range(101):
            tick_x = left + (right - left) * index / 100.0
            length = 39 if index % 25 == 0 else 25 if index % 5 == 0 else 12
            p.drawLine(QPointF(tick_x, ruler_y),
                       QPointF(tick_x, ruler_y + length))
        center_x = (left + right) / 2.0
        p.setPen(QPen(accent, 2))
        p.drawLine(QPointF(center_x, ruler_y - 7),
                   QPointF(center_x, ruler_y + 51))
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawEllipse(QPointF(center_x, ruler_y - 9), 3.5, 3.5)
        p.setFont(QFont("Consolas", max(8, int(min(width, height) * .022))))
        minute = max(1, self.total // 60)
        marks = (max(0, minute - 1), max(0, minute - .5), minute,
                 minute + .5, minute + 1)
        for idx, mark in enumerate(marks):
            label = "%02d:%02d" % (int(mark), int(round((mark % 1) * 60)))
            lx = left + (right - left) * idx / 4.0
            label_x = max(0.0, min(width - 76.0, lx - 38.0))
            p.setPen(accent if idx == 2 else muted)
            p.drawText(QRectF(label_x, ruler_y - 34, 76, 25),
                       Qt.AlignCenter, label)
        p.setPen(muted)
        p.setFont(small)
        p.drawText(QRectF(left, ruler_y + 58, width * .35, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, "TIME  /  MIN")
        p.drawText(QRectF(width * .65, ruler_y + 58, right - width * .65, 18),
                   Qt.AlignRight | Qt.AlignVCenter, "STAY FOCUSED  ////")
        p.end()

    def _paint_chiyo_dial(self):
        """千代主题使用细刻度仪表盘；红弧表示当前阶段剩余时间。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        side = max(180, int(min(rect.width(), rect.height()) * 0.88))
        cx, cy = rect.center().x(), rect.center().y()
        radius = side / 2 - 12
        dark = T.mode == "dark"
        line = QColor("#E7E1D9" if dark else "#22242A")
        dim = QColor("#A7A29D" if dark else "#92949A")
        surface = QColor("#202229" if dark else "#FFFEFA")
        p.setPen(Qt.NoPen)
        p.setBrush(surface)
        p.drawEllipse(QPointF(cx, cy), radius - 3, radius - 3)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(line, 1.4))
        p.drawEllipse(QPointF(cx, cy), radius + 8, radius + 8)
        p.setPen(QPen(dim, 0.9))
        p.drawEllipse(QPointF(cx, cy), radius + 3, radius + 3)

        # 六十个轻刻度，整五分钟刻度更长更醒目。
        for index in range(60):
            angle = math.radians(index * 6 - 90)
            major = index % 5 == 0
            inner = radius - (14 if major else 8)
            outer = radius - 1
            p.setPen(QPen(line if major else dim, 2.1 if major else 1.0,
                          Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx + inner * math.cos(angle), cy + inner * math.sin(angle)),
                       QPointF(cx + outer * math.cos(angle), cy + outer * math.sin(angle)))

        ratio = (self._shown / self.total) if self.total and not self.idle else 0.0
        ratio = max(0.0, min(1.0, ratio))
        if ratio > 0:
            accent = QColor(T.C_RED if self.remain <= 0 else
                            ("#FF536B" if dark else "#E83F56"))
            p.setPen(QPen(accent, 4.0, Qt.SolidLine, Qt.RoundCap))
            arc = QRectF(cx - radius + 6, cy - radius + 6,
                         (radius - 6) * 2, (radius - 6) * 2)
            # 从十二点顺时针绘制，弧长随剩余时间递减。
            p.drawArc(arc, 90 * 16, int(-360 * 16 * ratio))
            a = math.radians(-90 - 360 * ratio)
            p.setPen(Qt.NoPen)
            p.setBrush(accent)
            p.drawEllipse(QPointF(cx + (radius - 6) * math.cos(a),
                                  cy + (radius - 6) * math.sin(a)), 5.5, 5.5)

        status_font = QFont(T.pick_font(), max(12, side // 24))
        p.setFont(status_font)
        p.setPen(QColor(T.P["ink2"]))
        p.drawText(QRectF(0, cy - side * .30, self.width(), 34),
                   Qt.AlignCenter, self.status)
        time_font = QFont(T.pick_font(), max(34, side // 6))
        time_font.setBold(True)
        p.setFont(time_font)
        p.setPen(QColor(T.P["ink"]))
        self._paint_time_digits(p, QRectF(0, cy - side * .19, self.width(), side * .28))
        title_font = QFont(T.pick_font(), max(14, side // 22))
        title_font.setBold(True)
        p.setFont(title_font)
        p.setPen(QColor(T.P["ink"]))
        p.drawText(QRectF(0, cy + side * .11, self.width(), 34),
                   Qt.AlignCenter, self.title)
        sub_font = QFont(T.pick_font(), max(11, side // 30))
        p.setFont(sub_font)
        p.setPen(QColor(T.P["ink2"]))
        p.drawText(QRectF(24, cy + side * .24, self.width() - 48, 42),
                   Qt.AlignCenter | Qt.TextWordWrap, self.subtitle)
        p.end()


class ChiyoTimeUpCard(QWidget):
    """独立绘制的漫画到时稿纸；文字与确认按钮始终由真实控件承载。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._chiyo = QPixmap(A("chiyo_pack", "settings_chiyo.png"))
        self.gesture = CompanionGesture(self)

    def showEvent(self, event):
        super().showEvent(event)
        self.gesture.play()

    def hideEvent(self, event):
        self.gesture.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.scale(self.width() / 620.0, self.height() / 410.0)
        dark = T.mode == "dark"
        paper = QColor("#22242D" if dark else "#FFF9F1")
        edge = QColor("#F2EDE8" if dark else "#1E1D21")
        ink = QColor("#F4EEE9" if dark else "#29272D")
        outline = QPainterPath()
        outline.addRoundedRect(QRectF(6, 7, 608, 396), 21, 21)
        p.fillPath(outline, paper)
        p.setPen(QPen(edge, 3.1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(outline)
        p.setPen(QPen(QColor("#74717A" if dark else "#777277"), 1.1))
        p.drawRoundedRect(QRectF(16, 17, 588, 376), 17, 17)
        for origin_x, origin_y, sx, sy in ((33, 35, 1, 1), (585, 371, -1, -1)):
            for row in range(10):
                for col in range(12):
                    dot = QColor("#F05A70")
                    dot.setAlpha(max(0, 80 - row * 7 - col * 5))
                    p.setPen(Qt.NoPen)
                    p.setBrush(dot)
                    size = max(1.2, 4.2 - .22 * (row + col))
                    p.drawEllipse(QRectF(origin_x + sx * col * 7,
                                          origin_y + sy * row * 7,
                                          size, size))
        # 左上角的夹稿便签留在安全边缘，不盖住到时文案。
        p.save()
        p.translate(56, 33)
        p.rotate(-12)
        p.setPen(QPen(edge, 1.8))
        p.setBrush(QColor("#F5F0E8" if dark else "#FFFFFF"))
        p.drawRect(QRectF(0, 0, 72, 88))
        p.setPen(QPen(QColor("#B7B4B3"), 1.1))
        for y in (33, 44, 55, 66):
            p.drawLine(12, y, 58, y)
        p.setPen(QPen(edge, 2.0))
        p.setBrush(QColor("#35363D"))
        p.drawRoundedRect(QRectF(23, -13, 26, 28), 3, 3)
        p.setPen(QPen(QColor("#FFFDF9"), 1.2))
        p.drawLine(31, -9, 31, 11)
        p.drawLine(41, -9, 41, 11)
        p.restore()
        # 手绘秒表与红色校对短线。
        red = QColor("#EB4058")
        p.setPen(QPen(red, 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(282, 40, 56, 56))
        p.drawLine(310, 40, 310, 30)
        p.drawLine(300, 27, 320, 27)
        p.drawLine(336, 47, 344, 39)
        p.drawLine(310, 51, 310, 68)
        p.drawLine(310, 68, 322, 75)
        for x1, y1, x2, y2 in ((264, 57, 257, 50), (350, 58, 358, 50),
                                (269, 85, 260, 88), (348, 85, 358, 88)):
            p.drawLine(x1, y1, x2, y2)
        face = QRectF(416, 180, 200, 122)
        if not self._chiyo.isNull():
            p.save()
            self.gesture.apply(p, face)
            p.drawPixmap(face, self._chiyo,
                         QRectF(285, 225, 835, 510))
            p.restore()
        title = "作 答 时 间 到"
        font = QFont(T.pick_font(), 37)
        font.setBold(True)
        p.setFont(font)
        title_rect = QRectF(125, 112, 370, 64)
        p.setPen(QColor("#851D32" if dark else "#B53648"))
        p.drawText(title_rect.translated(2, 4), Qt.AlignCenter, title)
        p.setPen(red)
        p.drawText(title_rect, Qt.AlignCenter, title)
        p.setPen(QPen(QColor("#EE8393"), 3.5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(160, 180, 460, 180)
        p.setPen(QPen(ink, 1.3))
        p.drawLine(34, 319, 584, 319)
        p.end()


class _CenteredScaleOpacityEffect(QGraphicsOpacityEffect):
    """围绕弹窗中心缩放整张画面，不改变控件布局和窗口几何。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scale = 1.0

    def get_scale(self):
        return self._scale

    def set_scale(self, value):
        self._scale = max(0.88, min(1.0, float(value)))
        self.update()

    scale = Property(float, get_scale, set_scale)

    def draw(self, painter):
        pixmap = self.sourcePixmap(Qt.LogicalCoordinates)
        if pixmap.isNull():
            return
        center = self.sourceBoundingRect(Qt.LogicalCoordinates).center()
        painter.save()
        painter.setOpacity(self.opacity())
        painter.translate(center)
        painter.scale(self._scale, self._scale)
        painter.translate(-center)
        painter.drawPixmap(QPoint(0, 0), pixmap)
        painter.restore()


class AnnaTimeUpCard(QWidget):
    """八奈见主题的到时提示底板，正文仍由真实控件承载。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._anna = QPixmap(T.asset_path(
            "anna_theme", "assets", "characters", "anna-timeup-chibi.png"))
        self.gesture = CompanionGesture(self)

    def showEvent(self, event):
        super().showEvent(event)
        self.gesture.play()

    def hideEvent(self, event):
        self.gesture.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        w, h = self.width(), self.height()
        dark = T.mode == "dark"
        path = QPainterPath()
        path.addRoundedRect(QRectF(1, 1, w - 2, h - 2), 17, 17)
        p.fillPath(path, QColor("#192B42" if dark else "#F7FAFE"))
        p.setPen(QPen(QColor("#314968" if dark else "#CFDDF0"), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        p.save()
        p.setClipPath(path)
        corner = QPainterPath()
        corner.moveTo(w * .66, 0)
        corner.lineTo(w, 0)
        corner.lineTo(w, h)
        corner.lineTo(w * .86, h)
        corner.closeSubpath()
        p.fillPath(corner, QColor("#223B5A" if dark else "#EAF2FC"))

        p.setPen(QPen(QColor("#F0BE3D"), 2.0, Qt.SolidLine,
                      Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(QPointF(w - 43, 20), QPointF(w - 59, 59))
        p.drawLine(QPointF(w - 23, 30), QPointF(w - 34, 58))

        if not self._anna.isNull():
            area = QRectF(w * .605, h * .355, w * .39, h * .625)
            art = self._anna.scaled(area.size().toSize(), Qt.KeepAspectRatio,
                                    Qt.SmoothTransformation)
            p.save()
            self.gesture.apply(p, area)
            p.drawPixmap(round(area.right() - art.width()),
                         round(area.bottom() - art.height()), art)
            p.restore()
        p.restore()
        p.end()


@motion_page
class TimeUpDialog(QFrame):
    """作答时间到 —— 置顶提醒窗，一个「确定」按钮即可解除锁定。
    不带 emoji，不需要输入任何口令。"""

    confirmed = Signal()

    def __init__(self, parent=None, reason="作答时间已到", scale=1.0):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setWindowTitle("时间到")
        self._scale = scale
        self._drag = None
        self._motion = None
        self._entered_once = False
        self._closing = False
        self._allow_close = False
        self._pending_confirmation = False
        self._motion_effect = _CenteredScaleOpacityEffect()
        self._motion_effect.set_scale(0.90)
        self._motion_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._motion_effect)
        s = scale
        chiyo = T.app_theme == "chiyo"
        anna = T.app_theme == "anna"
        self.setFixedSize(int((484 if anna else 648 if chiyo else 480) * s),
                          int((316 if anna else 438 if chiyo else 320) * s))

        outer = QVBoxLayout(self)
        # 小范围柔影被完整收在透明边距内，避免边界裁切成矩形暗边。
        margin = int((12 if anna else 18) * s)
        outer.setContentsMargins(margin, margin, margin, margin)
        card = (AnnaTimeUpCard() if anna else
                ChiyoTimeUpCard() if chiyo else
                glass("GlassStrong", radius=int(24 * s)))
        add_shadow(card, blur=14, dy=3, alpha=42)
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        if anna:
            lay.setContentsMargins(int(24 * s), int(20 * s),
                                   int(24 * s), int(16 * s))
            lay.setSpacing(int(7 * s))
            title_row = QHBoxLayout()
            title_row.setSpacing(int(7 * s))
            title = QLabel("作答")
            ink = "#F2F6FC" if T.mode == "dark" else "#183052"
            ink2 = "#C0CCDA" if T.mode == "dark" else "#405873"
            ink3 = "#91A2B8" if T.mode == "dark" else "#74869C"
            title.setStyleSheet(
                f"font-size:{int(25*s)}px;font-weight:850;"
                f"color:{ink};background:transparent;")
            time_up = QLabel("时间到")
            time_up.setStyleSheet(
                f"font-size:{int(25*s)}px;font-weight:850;"
                "color:#E94B73;background:transparent;")
            title_row.addWidget(title)
            title_row.addWidget(time_up)
            title_row.addStretch(1)
            lay.addLayout(title_row)

            marks = QHBoxLayout()
            marks.setSpacing(int(5 * s))
            for color, width in (("#3477E5", 30), ("#F0BE3D", 12),
                                 ("#E94B73", 9)):
                mark = QFrame()
                mark.setFixedSize(int(width * s), max(3, int(4 * s)))
                mark.setStyleSheet(
                    f"background:{color};border:0;border-radius:2px;")
                marks.addWidget(mark)
            marks.addStretch(1)
            lay.addLayout(marks)

            d = QLabel(reason + "，已停止计时。\n请停止作答，点击下方按钮解除锁定。")
            d.setFixedWidth(int(240 * s))
            d.setWordWrap(True)
            d.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            d.setStyleSheet(
                f"font-size:{int(13*s)}px;color:{ink2};"
                "background:transparent;")
            lay.addWidget(d, 0, Qt.AlignLeft)

            self.ts = QLabel(datetime.datetime.now().strftime("锁定时刻 %H:%M:%S"))
            self.ts.setAlignment(Qt.AlignLeft)
            self.ts.setStyleSheet(
                f"font-size:{int(10*s)}px;color:{ink3};"
                "background:transparent;")
            lay.addWidget(self.ts, 0, Qt.AlignLeft)
            lay.addStretch(1)

            self.btn = IconButton("确定", "check", "primary", scale=s,
                                  font_size=14, radius=11)
            self.btn.setFixedWidth(int(244 * s))
            self.btn.setMinimumHeight(int(40 * s))
            self.btn.setStyleSheet(
                "QPushButton{color:#FFFFFF;font-size:14px;font-weight:750;"
                    "background:#2F70DF;border:none;border-radius:11px;}"
                "QPushButton:hover{background:#245FC7;}"
                "QPushButton:pressed{background:#174CA8;}")
            lay.addWidget(self.btn, 0, Qt.AlignLeft)
        else:
            lay.setContentsMargins(int(34 * s), int((205 if chiyo else 30) * s),
                                   int(34 * s), int(26 * s))
            lay.setSpacing(int(12 * s))
            if not chiyo:
                ic = icon_label("timer", T.C_RED, size=int(46 * s))
                lay.addWidget(ic, 0, Qt.AlignHCenter)

            t = QLabel("作 答 时 间 到")
            t.setAlignment(Qt.AlignCenter)
            t.setStyleSheet(
                f"font-size:{int(30*s)}px; font-weight:900; color:{T.C_RED};"
                f"letter-spacing:{int(5*s)}px; background:transparent;")
            if not chiyo:
                lay.addWidget(t)

            d = QLabel(reason + "，已停止计时。\n请停止作答，点击下方按钮解除锁定。")
            d.setAlignment(Qt.AlignCenter)
            d.setWordWrap(True)
            if chiyo:
                d.setMaximumWidth(int(400 * s))
                d.setAlignment(Qt.AlignCenter)
            d.setStyleSheet(
                f"font-size:{int((16 if chiyo else 14)*s)}px; color:{T.P['ink2']};"
                f"line-height:180%; background:transparent;")
            lay.addWidget(d, 0, Qt.AlignHCenter)

            self.ts = QLabel(datetime.datetime.now().strftime("锁定时刻 %H:%M:%S"))
            self.ts.setAlignment(Qt.AlignCenter)
            if chiyo:
                self.ts.setMaximumWidth(int(400 * s))
                self.ts.setAlignment(Qt.AlignCenter)
            self.ts.setStyleSheet(
                f"font-size:{int(11*s)}px; color:{T.P['ink3']};"
                f"background:transparent;")
            lay.addWidget(self.ts, 0, Qt.AlignHCenter)
            lay.addStretch()

            self.btn = IconButton("确　定", "check", "primary", scale=s,
                                  font_size=16, radius=14)
            self.btn.setMinimumHeight(int((66 if chiyo else 46) * s))
            if chiyo:
                self.btn.setStyleSheet(
                    "QPushButton{color:white;font-size:22px;font-weight:850;"
                    "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                    "stop:0 #FF6279,stop:1 #E73854);border:3px solid #9F2035;"
                    "border-radius:15px;}"
                    "QPushButton:pressed{background:#D9304B;border-color:#75192B;}")
            lay.addWidget(self.btn)
        self.btn.clicked.connect(self._ok)

        self.mousePressEvent = self._press
        self.mouseMoveEvent = self._move
        self.mouseReleaseEvent = self._release
        # 回车 / 空格 也算确定
        QShortcut(QKeySequence(Qt.Key_Return), self, self._ok)
        QShortcut(QKeySequence(Qt.Key_Enter), self, self._ok)
        QShortcut(QKeySequence(Qt.Key_Space), self, self._ok)
        self._center()

    def _center(self):
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - self.width() // 2,
                  scr.center().y() - self.height() // 2)

    def _stop_motion(self):
        if self._motion is not None:
            self._motion.stop()
            self._motion.deleteLater()
            self._motion = None

    def _animate_visibility(self, entering):
        if not animations_enabled():
            self._stop_motion()
            if entering:
                self._closing = False
                self._motion_effect.set_scale(1.0)
                self._motion_effect.setOpacity(1.0)
                self._entered_once = True
            else:
                self._finish_close()
            return

        self._stop_motion()
        group = QParallelAnimationGroup(self)
        scale = QPropertyAnimation(self._motion_effect, b"scale", group)
        opacity = QPropertyAnimation(self._motion_effect, b"opacity", group)

        if entering:
            self._closing = False
            scale.setStartValue(self._motion_effect.get_scale())
            scale.setEndValue(1.0)
            opacity.setStartValue(self._motion_effect.opacity())
            opacity.setEndValue(1.0)
            duration = 560
            easing = QEasingCurve.InOutCubic
        else:
            self._closing = True
            scale.setStartValue(self._motion_effect.get_scale())
            scale.setEndValue(0.90)
            opacity.setStartValue(self._motion_effect.opacity())
            opacity.setEndValue(0.0)
            duration = 420
            easing = QEasingCurve.InOutCubic

        scale.setEasingCurve(easing)
        opacity.setDuration(duration)
        scale.setDuration(duration)
        opacity.setEasingCurve(easing)
        group.addAnimation(scale)
        group.addAnimation(opacity)
        group.finished.connect(lambda: self._motion_finished(entering))
        group.finished.connect(group.deleteLater)
        self._motion = group
        group.start()

    def _motion_finished(self, entering):
        self._motion = None
        if entering:
            self._entered_once = True
            self._motion_effect.set_scale(1.0)
            self._motion_effect.setOpacity(1.0)
            return
        self._finish_close()

    def _finish_close(self):
        self._allow_close = True
        if self._pending_confirmation:
            self._pending_confirmation = False
            self.confirmed.emit()
        self.close()
        self._allow_close = False

    def closeEvent(self, ev):
        if self._allow_close:
            super().closeEvent(ev)
            return
        ev.ignore()
        if not self._closing:
            self._animate_visibility(False)

    def _press(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _move(self, ev):
        if self._drag and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag)

    def _release(self, ev):
        self._drag = None

    def _ok(self):
        if self._closing:
            return
        if T.app_theme == "chiyo":
            from sound import manager as sound_manager
            sound_manager().stop_stage()
        self._pending_confirmation = True
        self._animate_visibility(False)

    def showEvent(self, ev):
        super().showEvent(ev)
        self._closing = False
        self.raise_()
        self.activateWindow()
        self.btn.setFocus()
        self._animate_visibility(True)


# ================================================================ 锁定遮罩
class LockOverlay(QWidget):
    """作答超时遮罩：全屏挡住内容，提示到主窗口的置顶提醒处理。
    仅作为视觉遮挡，解除由 TimeUpDialog 的「确定」触发。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._blink = False
        self._bt = QTimer(self)
        self._bt.timeout.connect(self._do_blink)

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(14)

        self.ic = icon_label("timer", T.C_RED, size=60)
        lay.addWidget(self.ic, 0, Qt.AlignHCenter)

        self.title = QLabel("作 答 时 间 到")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet(
            f"font-size:44px; font-weight:900; color:{T.C_RED};"
            f"letter-spacing:8px; background:transparent;")
        lay.addWidget(self.title)

        self.d = QLabel("已超过作答时限，禁止继续作答。\n"
                        "请在弹窗中点击「确定」解除锁定。")
        self.d.setAlignment(Qt.AlignCenter)
        self.d.setStyleSheet(
            f"font-size:15px; color:{T.P['ink2']};"
            f"line-height:190%; background:transparent;")
        lay.addWidget(self.d)

        self.ts = QLabel("")
        self.ts.setAlignment(Qt.AlignCenter)
        self.ts.setStyleSheet(
            f"font-size:12px; color:{T.P['ink3']}; background:transparent;")
        lay.addWidget(self.ts)
        self.hide()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dark = (T.mode == "dark")
        g = QLinearGradient(0, 0, self.width(), self.height())
        if dark:
            g.setColorAt(0.0, QColor(14, 20, 30, 252))
            g.setColorAt(1.0, QColor(18, 26, 38, 252))
        else:
            g.setColorAt(0.0, QColor(247, 250, 254, 252))
            g.setColorAt(1.0, QColor(238, 244, 253, 252))
        p.fillRect(self.rect(), QBrush(g))
        rg = QRadialGradient(self.width() / 2, self.height() * 0.28,
                             max(self.width(), self.height()) * 0.55)
        rg.setColorAt(0.0, QColor(229, 72, 77, 44))
        rg.setColorAt(1.0, QColor(229, 72, 77, 0))
        p.fillRect(self.rect(), QBrush(rg))
        p.end()

    def start(self, reason="作答时间已到"):
        self.ts.setText("锁定时刻 "
                        + datetime.datetime.now().strftime("%H:%M:%S")
                        + "　·　" + reason)
        self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()
        chiyo = T.app_theme == "chiyo"
        for widget in (self.ic, self.title, self.d, self.ts):
            widget.setVisible(not chiyo)
        if not chiyo:
            self._bt.start(620)
        else:
            self._bt.stop()

    def stop(self):
        self._bt.stop()
        self.hide()

    def _do_blink(self):
        self._blink = not self._blink
        c = T.C_RED if self._blink else "#B08587"
        self.title.setStyleSheet(
            f"font-size:44px; font-weight:900; color:{c};"
            f"letter-spacing:8px; background:transparent;")

    def theme_changed(self, pref=None):
        chiyo = T.app_theme == "chiyo"
        if self.isVisible():
            for widget in (self.ic, self.title, self.d, self.ts):
                widget.setVisible(not chiyo)
            if chiyo:
                self._bt.stop()
        self.d.setStyleSheet(
            f"font-size:15px; color:{T.P['ink2']};"
            f"line-height:190%; background:transparent;")
        self.ts.setStyleSheet(
            f"font-size:12px; color:{T.P['ink3']}; background:transparent;")
        repaint_icon(self.ic, T.C_RED)
        self.update()

    def resizeEvent(self, ev):
        if self.parent():
            self.setGeometry(self.parent().rect())
        super().resizeEvent(ev)


# ================================================================ 计时页
@motion_page
class TimerArrangementPanel(QWidget):
    """用绘制层提供柔和卡片投影，避免图形效果覆盖滚动内容。"""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        card = QRectF(self.rect()).adjusted(11, 11, -11, -12)
        if card.width() > 0 and card.height() > 0:
            glow = QColor(T.P['accent_fg'] if T.mode == 'dark' else '#122642')
            painter.setPen(Qt.NoPen)
            painter.save()
            painter.setClipRect(QRectF(card.left() - 20, card.bottom() - 1,
                                       card.width() + 40,
                                       max(1, self.height() - card.bottom())))
            for spread, alpha in ((20, 1), (15, 2), (10, 3), (6, 4), (2, 5)):
                layer = QColor(glow)
                layer.setAlpha(alpha if T.mode == 'light' else max(1, alpha - 1))
                painter.setBrush(layer)
                shadow = card.adjusted(-spread, -spread, spread, spread)
                painter.drawRoundedRect(shadow.translated(0, 3),
                                        14 + spread, 14 + spread)
            painter.restore()
            panel = QColor(T.P['panel_bg'])
            if not panel.isValid():
                panel = QColor('#26374C' if T.mode == 'dark' else '#FFFFFF')
            painter.setBrush(panel)
            painter.drawRoundedRect(card, 14, 14)
            edge = QColor(T.P['accent_fg'])
            edge.setAlpha(13 if T.mode == 'light' else 11)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(edge, 1))
            painter.drawRoundedRect(card.adjusted(.5, .5, -.5, -.5), 14, 14)
            if T.mode == 'dark':
                painter.save()
                glow.setAlpha(8)
                wash = QLinearGradient(0, card.bottom() - card.height() * .20,
                                       0, card.bottom())
                wash.setColorAt(0, QColor(glow.red(), glow.green(), glow.blue(), 0))
                wash.setColorAt(1, glow)
                painter.setClipRect(QRectF(card.left() + 1,
                                           card.bottom() - card.height() * .18,
                                           card.width() - 2,
                                           card.height() * .18 - 1))
                painter.setBrush(wash)
                painter.drawRoundedRect(card, 14, 14)
                painter.restore()
        painter.end()


class TimerPage(QWidget):
    state_changed = Signal()
    IDLE, READ, DOING, LOCK = 0, 1, 2, 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = self.IDLE
        self.read_total = int(cfg.get("read_sec", 60))
        self.read_total = max(0, min(3600, self.read_total))
        self.do_total = int(cfg.get("do_sec", 360) or 360)
        self.do_total = max(10, min(7200, self.do_total))
        self._clock_custom_total = None
        self.remain = 0
        self.start_at = None
        self._session_started_mono = None
        self._elapsed_paused_at = None
        self._elapsed_paused_total = 0.0
        self._phase_deadline = None
        self._paused_at = None
        self._last_elapsed = -1
        self._widgets = []

        self.tick = QTimer(self)
        # 用短间隔采样单调时钟，显示值只在秒数变化时更新，避免系统繁忙时累计漂移。
        self.tick.setInterval(200)
        self.tick.timeout.connect(self._on_tick)
        self._build()

    # --------------------------------------------------- UI
    def _build(self):
        """按训练流程组织计时器与设置控件，沿用原计时状态和操作入口。"""
        root = QVBoxLayout(self)
        self.root_layout = root
        root.setContentsMargins(34, 22, 34, 22)
        root.setSpacing(17)

        self.anna_header_art = AnnaHeaderArt(
            "限时模拟", "先选题，再开始作答；时长可按练习要求调整。",
            self, page_key="timer")
        self.anna_header_art.setVisible(T.app_theme == "anna")
        root.addWidget(self.anna_header_art)

        self.heading_widget = QWidget(self)
        head = QHBoxLayout(self.heading_widget)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("限时模拟")
        title.setObjectName("H1")
        subtitle = QLabel("先选题，再开始作答；时长可按练习要求调整。")
        subtitle.setObjectName("Dim")
        self.page_title = title
        self.page_subtitle = subtitle
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        head.addLayout(title_box)
        head.addStretch()
        from chiyo_art import ChiyoMarginNote
        self.chiyo_header_note = ChiyoMarginNote(
            "好的练习\n会让未来的我\n更喜欢现在努力的自己！",
            width=244, height=94)
        self.chiyo_header_note.setVisible(T.app_theme == "chiyo")
        head.addWidget(self.chiyo_header_note, 0, Qt.AlignVCenter)
        self.elapsed = QLabel("已用时 00:00")
        self.elapsed.setObjectName("TimerElapsed")
        self.elapsed.setStyleSheet(
            f"font-size:13px; color:{T.P['ink2']}; background:transparent;")
        self.industrial_header = IndustrialPageMeta(
            "SIMULATION EXAM", "ALWAYS HIGHER", self)
        self.industrial_header.setVisible(T.app_theme == "industrial")
        head.addWidget(self.industrial_header, 0, Qt.AlignVCenter)
        head.addWidget(self.elapsed, 0, Qt.AlignVCenter)
        root.addWidget(self.heading_widget)

        body = QHBoxLayout()
        self.body_layout = body
        body.setSpacing(24)
        left = QWidget(self)
        self.left_panel = left
        left_layout = QVBoxLayout(left)
        self.left_layout = left_layout
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(12)

        steps_host = QWidget(left)
        self.timer_steps_host = steps_host
        steps = QHBoxLayout(steps_host)
        steps.setContentsMargins(0, 0, 0, 8)
        steps.setSpacing(8)
        self.chips = {}
        for key, name in (("read", "阅题"), ("do", "作答"), ("lock", "完成")):
            step = QLabel("○  " + name)
            step.setProperty("stepName", name)
            step.setAlignment(Qt.AlignCenter)
            step.setMinimumHeight(38)
            self.chips[key] = step
            steps.addWidget(step, 1)
        left_layout.addWidget(steps_host)
        self.phase_relay = PhaseRelay(steps_host, list(self.chips.values()))

        self.ring = RingTimer(self.read_total or self.do_total,
                              "阅题时间" if self.read_total else "作答时间")
        self.ring.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.ring.setMinimumSize(220, 220)
        left_layout.addWidget(self.ring, 1)
        from timer_motion import CountdownRunway
        self.countdown_runway = CountdownRunway(self.ring, left)
        self.ring.runway = self.countdown_runway
        left_layout.addSpacing(8)
        left_layout.addWidget(self.countdown_runway)
        left_layout.addSpacing(4)

        self.hint = QLabel("")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        self.hint.setMinimumHeight(28)
        self.hint.setObjectName("TimerHint")
        self.hint.setStyleSheet(
            f"font-size:14px; color:{T.P['ink2']}; background:transparent;")
        left_layout.addWidget(self.hint)

        self.running_actions = QWidget(left)
        self.running_actions.setObjectName("TimerRunningActions")
        self.running_action_row = QHBoxLayout(self.running_actions)
        self.running_action_row.setContentsMargins(0, 0, 0, 0)
        self.running_action_row.setSpacing(8)
        left_layout.addWidget(self.running_actions)
        self.running_actions.hide()

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.b_start = IconButton("开始阅题", None, "primary",
                                  font_size=15, radius=10, shadowed=True)
        self.b_start.setFixedHeight(48)
        self.b_start.setMinimumWidth(0)
        self.b_start.clicked.connect(self.start_session)
        self.b_pause = IconButton("暂停", None, "default", radius=10,
                                  font_size=14, shadowed=True)
        self.b_pause.setFixedHeight(48)
        self.b_pause.setMinimumWidth(0)
        self.b_pause.setEnabled(False)
        self.b_pause.clicked.connect(self.toggle_pause)
        self.b_reset = IconButton("重置", "reset", "default", radius=10,
                                  font_size=14, shadowed=True)
        self.b_reset.setFixedHeight(48)
        self.b_reset.setMinimumWidth(0)
        self.b_reset.clicked.connect(self.reset)
        actions.addWidget(self.b_start, 2)
        actions.addWidget(self.b_pause, 1)
        actions.addWidget(self.b_reset, 1)
        left_layout.addLayout(actions)

        right = TimerArrangementPanel(self)
        right.setObjectName("TimerArrangement")
        self.right_panel = right
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(32, 32, 32, 30)
        right_layout.setSpacing(12)
        right_layout.setAlignment(Qt.AlignTop)
        arrangement = QLabel("训练安排")
        self.arrangement_title = arrangement
        arrangement.setObjectName("H2")
        arrange_desc = QLabel("根据你的练习节奏，设置本次模拟的时间安排。")
        arrange_desc.setObjectName("Dim")
        arrange_desc.setWordWrap(True)
        right_layout.addWidget(arrangement)
        right_layout.addWidget(arrange_desc)
        self.arrangement_description = arrange_desc
        self.training_summary = QLabel()
        self.training_summary.setWordWrap(True)
        self.training_summary.setObjectName("TimerSummary")
        right_layout.addWidget(self.training_summary)
        self.b_arrangement = IconButton("查看训练安排", "gear", "default", font_size=12)
        self.b_arrangement.clicked.connect(self._toggle_running_arrangement)
        right_layout.addWidget(self.b_arrangement)

        self.duration_editor = QWidget()
        duration_layout = QVBoxLayout(self.duration_editor)
        duration_layout.setContentsMargins(0, 0, 0, 0)
        duration_layout.setSpacing(10)

        def section(title_text):
            label = QLabel(title_text)
            label.setObjectName("TimerSection")
            return label

        duration_layout.addWidget(section("阅题时长"))
        read_row = QHBoxLayout()
        read_row.setSpacing(8)
        self.read_btns = {}
        from ui_kit import AnimatedChoiceButton
        for sec, text in ((0, "跳过"), (60, "1 分钟"), (120, "2 分钟")):
            button = AnimatedChoiceButton(text)
            button.setObjectName("TimerDurationChoice")
            button.setCheckable(True)
            button.setMinimumHeight(50)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, value=sec: self.set_read_len(value))
            self.read_btns[sec] = button
            read_row.addWidget(button, 1)
        if self.read_total in self.read_btns:
            self.read_btns[self.read_total].setChecked(True)
        duration_layout.addLayout(read_row)

        duration_layout.addWidget(section("作答时长"))
        do_row = QHBoxLayout()
        do_row.setSpacing(8)
        self.do_btns = {}
        for sec, text in ((180, "3 分钟"), (300, "5 分钟"), (360, "6 分钟")):
            button = AnimatedChoiceButton(text)
            button.setObjectName("TimerDurationChoice")
            button.setCheckable(True)
            button.setMinimumHeight(50)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, value=sec: self.set_do_len(value))
            self.do_btns[sec] = button
            do_row.addWidget(button, 1)
        if self.do_total in self.do_btns:
            self.do_btns[self.do_total].setChecked(True)
        duration_layout.addLayout(do_row)
        right_layout.addWidget(self.duration_editor)
        self._sync_chips()

        self.chk_sound = ToggleSwitch()
        self.chk_sound.setAccessibleName("到点提示音")
        self.chk_sound.setChecked(bool(cfg.get("sound", True)))
        self.chk_sound.toggled.connect(lambda value: cfg.set_("sound", bool(value)))
        sound_row = QHBoxLayout()
        sound_copy = QVBoxLayout()
        sound_title = QLabel("到点提示音")
        sound_title.setObjectName("H3")
        sound_desc = QLabel("阅题或作答时间结束时播放提示音")
        sound_desc.setObjectName("Dim")
        sound_desc.setWordWrap(True)
        sound_copy.addWidget(sound_title)
        sound_copy.addWidget(sound_desc)
        sound_row.addLayout(sound_copy, 1)
        sound_row.addWidget(self.chk_sound)

        self.chk_auto = ToggleSwitch()
        self.chk_auto.setAccessibleName("阅题结束自动进入作答")
        self.chk_auto.setChecked(bool(cfg.get("auto_next", True)))
        self.chk_auto.toggled.connect(lambda value: cfg.set_("auto_next", bool(value)))
        auto_row = QHBoxLayout()
        auto_copy = QVBoxLayout()
        auto_title = QLabel("自动进入作答")
        auto_title.setObjectName("H3")
        auto_desc = QLabel("阅题时间结束后自动开始作答")
        auto_desc.setObjectName("Dim")
        auto_desc.setWordWrap(True)
        auto_copy.addWidget(auto_title)
        auto_copy.addWidget(auto_desc)
        auto_row.addLayout(auto_copy, 1)
        auto_row.addWidget(self.chk_auto)

        self.preference_options = QWidget()
        preferences = QVBoxLayout(self.preference_options)
        preferences.setContentsMargins(0, 0, 0, 0)
        preferences.setSpacing(14)
        def add_rule():
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            line.setFixedHeight(1)
            line.setStyleSheet(f"background:{T.P['glass_soft_edge']}; border:0;")
            preferences.addWidget(line)
        add_rule()
        preferences.addLayout(sound_row)
        add_rule()
        preferences.addLayout(auto_row)
        right_layout.addWidget(self.preference_options)
        from chiyo_art import ChiyoComicCell
        self.chiyo_art_slot = ChiyoComicCell(right, "ChiyoArtFrame")
        self.chiyo_art_slot.setObjectName("ChiyoArtFrame")
        # 小窗口先缩减装饰槽，避免插画边框被状态栏裁掉。
        self.chiyo_art_slot.setMinimumHeight(170)
        self.chiyo_art_slot.setMaximumHeight(290)
        self.chiyo_art_slot.setStyleSheet(
            "QFrame#ChiyoArtFrame{background:transparent;border:0px;}")
        art_note = QLabel("专注分镜　·　秒表为静态插画")
        self.art_note = art_note
        art_note.setObjectName("Dim")
        art_note.setWordWrap(True)
        art_note.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        art_note.setStyleSheet(
            f"font-size:11px;color:{T.P['ink3']};background:transparent;"
            f"border-bottom:1px solid {T.P['glass_soft_edge']};padding:2px 4px;")
        self.rec = QLabel("尚未开始")
        self.rec.setObjectName("Dim")
        self.rec.setWordWrap(True)
        self.rec.setMinimumHeight(24)
        art_meta = QHBoxLayout()
        art_meta.setSpacing(10)
        art_meta.addWidget(self.rec, 1)
        art_meta.addWidget(art_note)
        right_layout.addLayout(art_meta)
        right_layout.addWidget(self.chiyo_art_slot, 1)
        self._widgets = [left, right]
        body.addWidget(left, 14)
        # 保留控件字号与触达面积，高度不足时只滚动右侧任务清单。
        self.arrangement_scroll = QScrollArea(self)
        self.arrangement_scroll.setWidgetResizable(True)
        self.arrangement_scroll.setFrameShape(QFrame.NoFrame)
        self.arrangement_scroll.setMinimumWidth(0)
        self.arrangement_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.arrangement_scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:0;}"
            "QScrollArea > QWidget > QWidget{background:transparent;}")
        arrangement_host = QWidget()
        arrangement_box = QVBoxLayout(arrangement_host)
        arrangement_box.setContentsMargins(0, 0, 0, 0)
        arrangement_box.addWidget(right, 0, Qt.AlignTop)
        arrangement_box.addStretch(1)
        self.arrangement_scroll.setWidget(arrangement_host)
        # 滚动区会自动开启内容底色填充，透明转场必须显式关闭。
        arrangement_host.setAutoFillBackground(False)
        self.arrangement_scroll.viewport().setAutoFillBackground(False)
        self.arrangement_scroll.setMaximumWidth(470)
        self.arrangement_dock = QWidget(self)
        self.arrangement_dock.setObjectName("TimerArrangementDock")
        self.arrangement_dock.setMinimumWidth(0)
        self.arrangement_dock.setMaximumWidth(510)
        dock_layout = QHBoxLayout(self.arrangement_dock)
        dock_layout.setContentsMargins(0, 0, 0, 0)
        dock_layout.setSpacing(20)
        # 两栏通过留白分区，避免再用竖线切断计时区域。
        dock_layout.addWidget(self.arrangement_scroll, 1)
        self._arrangement_dock_motion = None
        self._arrangement_dock_open = True
        body.addWidget(self.arrangement_dock, 8)
        root.addLayout(body, 1)
        self.industrial_footer = IndustrialPageFooter(self)
        self.industrial_footer.setVisible(T.app_theme == "industrial")
        root.addWidget(self.industrial_footer)
        from chiyo_art import ChiyoCornerDots
        self.chiyo_corner_dots = ChiyoCornerDots(
            self, corners=("top-right", "bottom-left"))
        self._set_chips(read="off", do="off", lock="off")
        self._sync_ring_copy()
        self.hint.setText(self._idle_hint())

        from motion_features import install_timer_controls
        install_timer_controls(self, right_layout)
        for button in (self.b_skip, self.b_extend):
            self.timer_action_grid.removeWidget(button)
            self.running_action_row.addWidget(button, 1)
        self.b_arrangement.hide()
        self._run_layout_state = None
        self._arrangement_open = True
        self.state_changed.connect(self._sync_run_layout)
        self._sync_run_layout(animate=False)

    def _toggle_running_arrangement(self):
        from motion_polish import fold
        self._arrangement_open = not self._arrangement_open
        fold(self.duration_editor, self._arrangement_open)
        self.b_arrangement.setText("收起训练安排" if self._arrangement_open else "查看训练安排")

    def _animate_arrangement_dock(self, opened, animate=True):
        """真实布局立即交接，旧卡快照向右淡出，不影响子按钮绘制。"""
        opened = bool(opened)
        old = getattr(self, '_arrangement_exit', None)
        if old is not None:
            old.dispose()
        self._arrangement_transition_id = getattr(self, '_arrangement_transition_id', 0) + 1
        transition_id = self._arrangement_transition_id
        frame = getattr(self, '_arrangement_exit_frame', None)
        self._arrangement_exit_frame = None
        self._arrangement_dock_open = opened
        self.arrangement_dock.setMaximumWidth(510)
        self.arrangement_dock.setVisible(opened)
        if not opened and animate and frame is not None:
            from workbench_ui import DockExit
            self._arrangement_exit = DockExit(self, *frame)
        elif opened and animate and animations_enabled() and self.isVisible():
            # 等待布局和准备状态文案更新，再捕获回归快照。
            def return_card():
                if transition_id != self._arrangement_transition_id or not self.arrangement_dock.isVisible():
                    return
                from PySide6.QtCore import QPoint
                from workbench_ui import DockExit
                if self.layout() is not None:
                    self.layout().activate()
                self.right_panel.layout().activate()
                self.timer_action_grid.activate()
                from workbench_ui import transparent_snapshot
                self._arrangement_exit = DockExit(self, transparent_snapshot(self.arrangement_dock),
                    self.arrangement_dock.mapTo(self, QPoint(0, 0)), self.arrangement_dock)
            QTimer.singleShot(0, return_card)

    def _sync_run_layout(self, animate=True):
        """准备、运行、完成共用一个时钟；布局交接不改变真实计时状态。"""
        if not hasattr(self, "b_custom"):
            return
        state = "ready" if self.phase == self.IDLE else "complete" if self.phase == self.LOCK else "running"
        changed = state != self._run_layout_state
        visual_phase = (self.phase, self.tick.isActive())
        phase_changed = getattr(self, '_visual_phase', visual_phase) != visual_phase
        self._visual_phase = visual_phase
        if (changed or phase_changed) and animate and self.isVisible() and animations_enabled():
            self._animate_timer_information()
        def apply():
            self._run_layout_state = state
            self.duration_editor.setMaximumHeight(16777215)
            self.duration_editor.setVisible(state == "ready")
            self.preference_options.setVisible(state == "ready")
            self.b_arrangement.hide()
            self.arrangement_description.setVisible(state == "ready")
            self.arrangement_title.setText("训练安排" if state == "ready" else "训练回顾" if state == "complete" else "本次训练")
            self.chiyo_art_slot.setVisible(state == "ready" and T.app_theme == "chiyo")
            self.art_note.setVisible(state == "ready" and T.app_theme == "chiyo")
            self.b_custom.setVisible(state == "ready")
            from motion_polish import fold
            fold(self.custom_duration,False,animate=False)
            if state == "ready":
                fold(self.history_panel,False,animate=False)
            self.b_extend.setVisible(state == "running")
            self.b_skip.setVisible(self.phase == self.READ)
            self.running_actions.setVisible(state == "running")
        if changed:
            if state != "ready" and self.arrangement_dock.isVisible() and animate and animations_enabled():
                previous = getattr(self, '_arrangement_exit', None)
                if previous is not None:
                    previous.dispose()
                from PySide6.QtCore import QPoint
                from workbench_ui import transparent_snapshot
                self._arrangement_exit_frame = (transparent_snapshot(self.arrangement_dock),
                    self.arrangement_dock.mapTo(self, QPoint(0, 0)))
            apply()
            self._animate_arrangement_dock(state == "ready", animate=animate)
        self.b_extend.setEnabled(state == "running")
        self.b_skip.setVisible(self.phase == self.READ)
        self.running_actions.setVisible(
            state == "running" and (not self.b_extend.isHidden() or not self.b_skip.isHidden()))
        action_state=(state,self.phase==self.READ)
        if getattr(self,'_action_layout_state',None)!=action_state:
            self._action_layout_state=action_state
            for button in (self.b_custom,self.b_history):self.timer_action_grid.removeWidget(button)
            visible=[b for b in (self.b_custom,self.b_history) if not b.isHidden()]
            for index,button in enumerate(visible):self.timer_action_grid.addWidget(button,index//2,index%2,1,2 if len(visible)%2 and index==len(visible)-1 else 1)
        if self.phase == self.READ:
            self._run_read_span = self.ring.total
        self.training_summary.setText("阅题 %02d:%02d  →  作答 %02d:%02d" %
            (self.read_total//60, self.read_total%60, self.do_total//60, self.do_total%60))
        style=f"color:{T.P['ink2']};font-size:14px;font-weight:600;background:transparent;padding:8px 0;"
        if self.training_summary.styleSheet()!=style:self.training_summary.setStyleSheet(style)
        if state == "complete":
            records = cfg.get("timer_sessions", [])
            if records:
                record = records[-1]
                self.training_summary.setText(self.training_summary.text() + "\n\n本轮实际用时 %02d:%02d" % (record['elapsed']//60, record['elapsed']%60))
            self.history_label.setVisible(True)
            if changed:
                from motion_features import update_history
                from motion_polish import fold
                update_history(self)
                fold(self.history_panel, True, animate=False)
        self.countdown_runway.update()

    def toggle_custom_duration(self):
        from motion_features import toggle_custom_duration
        toggle_custom_duration(self)

    def apply_custom_duration(self):
        from motion_features import apply_custom_duration
        apply_custom_duration(self)

    def extend_time(self):
        from motion_features import extend_time
        extend_time(self)

    def skip_stage(self):
        from motion_features import skip_stage
        skip_stage(self)

    def toggle_history(self):
        from motion_features import toggle_history
        toggle_history(self)

    def _save_session_result(self):
        from motion_features import save_session
        save_session(self)

    def _last_seconds_feedback(self):
        self.countdown_runway.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "chiyo_header_note"):
            self.chiyo_header_note.setVisible(
                T.app_theme == "chiyo" and self.width() >= 980)
        if T.app_theme == "anna" and hasattr(self, "anna_header_art"):
            # 优先保留操作区高度，横幅装饰在小窗口中收缩。
            self.anna_header_art._sync_reference_height()
        if hasattr(self, "left_layout"):
            self._update_compact_layout()

    def _update_compact_layout(self):
        """小窗口优先保留操作按钮，压缩留白与表盘的最低占用。"""
        compact = self.height() < 620
        self.left_layout.setSpacing(8 if compact else 12)
        self.ring.setMinimumSize(180 if compact else 220, 180 if compact else 220)
        for chip in self.chips.values():
            chip.setMinimumHeight(30 if compact else 38)
        self.body_layout.setSpacing(16 if compact else 24)
        if T.app_theme == "anna":
            self.anna_header_art._sync_reference_height()

    def _sec_title(self, txt):
        l = QLabel(txt)
        l.setStyleSheet(
            f"font-size:15px; font-weight:700; color:{T.P['accent_fg']};"
            f"letter-spacing:.5px; background:transparent;")
        return l

    def play_intro(self):
        """阶段、时钟、提示和操作逐组入场，不使用父级透明度特效。"""
        self._animate_timer_information()
        if self.arrangement_dock.isVisible():
            self._animate_arrangement_dock(True)

    def prepare_intro(self):
        self.cancel_intro()

    def cancel_intro(self):
        from ui_kit import cancel_card_in
        cancel_card_in(self._widgets)
        self._timer_visual_generation = getattr(self, '_timer_visual_generation', 0) + 1
        motion = getattr(self, '_timer_visual_motion', None)
        if motion is not None:
            motion.dispose()
        dock = getattr(self, '_arrangement_exit', None)
        if dock is not None:
            dock.dispose()

    def _timer_motion_widgets(self):
        return (self.timer_steps_host, self.ring, self.countdown_runway,
                self.hint, self.running_actions, self.b_start, self.b_pause, self.b_reset)

    def timer_exit_frames(self, host):
        from PySide6.QtCore import QPoint
        from workbench_ui import transparent_snapshot
        return [(transparent_snapshot(widget), widget.mapTo(host, QPoint(0, 0)))
                for widget in (*self._timer_motion_widgets(), self.arrangement_dock)
                if widget.isVisible()]

    def _capture_timer_information(self):
        if self.isVisible() and animations_enabled():
            self.cancel_intro()
            from PySide6.QtCore import QPoint
            from workbench_ui import transparent_snapshot
            self._timer_before_frames = [(transparent_snapshot(widget), widget.mapTo(self, QPoint(0, 0)))
                for widget in self._timer_motion_widgets() if widget.isVisible()]

    def _animate_timer_information(self):
        if not self.isVisible() or not animations_enabled():
            return
        old = getattr(self, '_timer_visual_motion', None)
        if old is not None:
            old.dispose()
        self._timer_visual_generation = getattr(self, '_timer_visual_generation', 0) + 1
        generation = self._timer_visual_generation
        before = getattr(self, '_timer_before_frames', None)
        self._timer_before_frames = None
        def enter():
            if generation != self._timer_visual_generation or not self.isVisible() or not animations_enabled():
                return
            self.layout().activate()
            self.left_layout.activate()
            from workbench_ui import TimerVisualTransition
            self._timer_visual_motion = TimerVisualTransition(self, self._timer_motion_widgets(), previous=before)
        QTimer.singleShot(0, enter)

    def theme_changed(self, pref=None):
        """主题切换：重绘内联样式"""
        industrial = T.app_theme == "industrial"
        anna = T.app_theme == "anna"
        self.industrial_header.setVisible(industrial)
        self.industrial_header.update()
        self.industrial_footer.setVisible(industrial)
        self.industrial_footer.update()
        self.chiyo_header_note.setVisible(T.app_theme == "chiyo" and self.width() >= 980)
        self.chiyo_header_note.update()
        self.anna_header_art.setVisible(anna)
        self.anna_header_art.refresh_art()
        self.heading_widget.setVisible(not anna)
        self.page_title.setVisible(not anna)
        self.page_subtitle.setVisible(not anna)
        self.chiyo_corner_dots.setVisible(T.app_theme == "chiyo")
        self.chiyo_corner_dots.update()
        self.chiyo_art_slot.setVisible(T.app_theme == "chiyo")
        self.art_note.setVisible(T.app_theme == "chiyo")
        for chip in self.chips.values():
            chip.setVisible(not industrial)
        self.root_layout.setContentsMargins(
            (0 if anna else 32 if industrial else 34),
            (0 if anna else 22),
            (0 if anna else 32 if industrial else 34),
            (18 if anna else 20 if industrial else 22))
        self.root_layout.setSpacing(8 if anna else 17)
        self.body_layout.setContentsMargins(
            22 if anna else 0, 0, 22 if anna else 0, 0)
        self.body_layout.setSpacing(24)
        self.body_layout.setStretch(0, 16 if industrial else 14)
        self.body_layout.setStretch(2, 8)
        self.rec.setVisible(not industrial)
        self.art_note.setText("AZIMUTH　·　CAAC　·　TRAINING"
                              if industrial else "专注分镜　·　秒表为静态插画")
        self.chiyo_art_slot.setStyleSheet(
            "QFrame#ChiyoArtFrame{background:transparent;border:0px;}")
        self.elapsed.setStyleSheet(
            f"font-size:13px; color:{T.P['ink2']};"
            f"background:{'transparent' if industrial else T.P['panel_soft']};"
            f"border-radius:{'0' if industrial else '12px'};"
            f"padding:7px 4px 7px 15px;"
            f"border-left:1px solid {T.P['glass_soft_edge']};")
        self.hint.setStyleSheet(
            f"font-size:13px; color:{T.P['ink2']};"
            f"background:{'transparent' if industrial else T.P['panel_soft']};"
            f"border-radius:{'0' if industrial else '12px'};"
            f"padding:9px 14px;")
        self.rec.setStyleSheet(
            f"font-size:13px; color:{T.P['ink2']};"
            f"line-height:195%; background:transparent;")
        for w in self._widgets:
            refresh_glass(w)
            replay_shadow(w)
        self.right_panel.setStyleSheet(
            "QWidget#TimerArrangement{background:transparent;"
            "border:0;border-radius:14px;}")
        self.right_panel.update()
        for b in (self.b_start, self.b_pause, self.b_reset):
            b.setFixedHeight(58 if industrial else 52)
            b.refresh()
            # 主题级 min-height 会重新放开固定高度，局部规则锁住真实触达面积。
            content_height = 56 if industrial else 50
            b.setStyleSheet(b.styleSheet() +
                f"QPushButton{{min-height:{content_height}px;max-height:{content_height}px;"
                "padding:0 12px;margin:0;}")
        self._sync_chips()
        self._paint_chips()
        for l in self.findChildren(QLabel):
            if l.objectName() == "H1":
                l.setStyleSheet(
                    f"font-size:30px; font-weight:800; color:{T.P['ink']};"
                    f"letter-spacing:0px; background:transparent;"
                    f"border-left:{'6px' if industrial else '0px'} solid {T.P['accent_fg']};"
                    f"padding-left:{'12px' if industrial else '0px'};")
            elif l.objectName() == "Dim":
                l.setStyleSheet(
                    f"font-size:13px; color:{T.P['ink2']};"
                    f"background:transparent;")
            elif l.objectName() == "TimerSection":
                l.setStyleSheet(
                    f"font-size:20px; font-weight:750; color:{T.P['ink']};"
                    "background:transparent;")
            elif l.objectName() == "H3":
                l.setStyleSheet(
                    f"font-size:16px; font-weight:700; color:{T.P['ink']};"
                    "background:transparent;")
            elif l.objectName() == "TimerElapsed":
                l.setStyleSheet(
                    f"font-size:13px; color:{T.P['ink2']}; background:transparent;")
            elif l.objectName() == "TimerHint":
                l.setStyleSheet(
                    f"font-size:14px; color:{T.P['ink2']}; background:transparent;")
        for l in self.findChildren(QLabel):
            if l.text() in ("阅题时长", "作答时长", "本次记录"):
                l.setStyleSheet(
                    f"font-size:15px; font-weight:700;"
                    f"color:{T.P['accent_fg']};"
                    f"letter-spacing:.5px; background:transparent;")
        self.update()

        self._update_compact_layout()

        from motion_features import refresh_feature_controls
        refresh_feature_controls(self)
        self._run_layout_state = None
        self._sync_run_layout(animate=False)

    def _sync_chips(self):
        for buttons in (list(self.read_btns.values()), list(self.do_btns.values())):
            for button in buttons:
                checked = button.isChecked()
                accent = QColor(T.P['accent_fg'])
                accent.setAlpha(20)
                hover_accent = QColor(T.P['accent_fg'])
                hover_accent.setAlpha(40)
                focus_accent = QColor(T.P['accent_fg'])
                focus_accent.setAlpha(54)
                edge = f"rgba({accent.red()},{accent.green()},{accent.blue()},{accent.alpha()})"
                hover_edge = f"rgba({hover_accent.red()},{hover_accent.green()},{hover_accent.blue()},{hover_accent.alpha()})"
                focus_edge = f"rgba({focus_accent.red()},{focus_accent.green()},{focus_accent.blue()},{focus_accent.alpha()})"
                if T.app_theme == "industrial":
                    fill = T.P["accent_fg"] if checked else T.P["panel_bg"]
                    color = "#FFFFFF" if checked else T.P["ink"]
                    outline = "rgba(255,255,255,20)" if checked else edge
                    button.setStyleSheet(
                        "QPushButton{min-height:50px;padding:0 10px;"
                        f"color:{color};background:{fill};border:1px solid {outline};"
                        "border-radius:4px;font-size:15px;font-weight:600;}"
                        f"QPushButton:hover{{border-color:{hover_edge};"
                        f"background:{T.P['panel_hover'] if not checked else T.P['accent_fg']};}}"
                        f"QPushButton:focus{{border-color:{focus_edge};"
                        f"background:{fill if checked else T.P['accent_soft']};"
                        f"color:{'#FFFFFF' if checked else T.P['ink']};}}")
                    continue
                selector = "QPushButton#TimerDurationChoice"
                fill = (f"qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                        f"stop:0 {T.C_BLUE_HI},stop:1 {T.C_BLUE})"
                        if checked else
                        f"qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                        f"stop:0 {T.P['glass']},stop:1 {T.P['glass_lo']})")
                button.setStyleSheet(
                    f"{selector}{{min-height:48px;"
                    f"background:{fill};"
                    f"color:{'#FFFFFF' if checked else T.P['ink']};"
                    f"border:1px solid {'rgba(255,255,255,20)' if checked else edge};"
                    "border-radius:12px; font-size:15px; font-weight:"
                    f"{'700' if checked else '500'}; padding:0 8px; }}"
                    f"{selector}:hover{{border-radius:12px;border:1px solid {hover_edge};"
                    "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                    f"stop:0 {T.C_BLUE_HI if checked else T.P['panel_hover']},"
                    f"stop:1 {T.C_BLUE if checked else T.P['glass_lo']});}}"
                    f"{selector}:pressed{{border-radius:12px;border:1px solid {hover_edge};background:" + T.C_BLUE_DEEP + ";}"
                    f"{selector}:disabled{{border-radius:12px;background:" + T.P["panel_soft"] + ";"
                    "color:" + T.P["ink3"] + f";border:1px solid {edge};}}"
                    f"{selector}:focus{{border-radius:12px;border:1px solid {focus_edge};background:" +
                    (fill if checked else T.P['accent_soft']) + ";color:" +
                    ("#FFFFFF" if checked else T.P['ink']) + ";}")
        self._paint_chips()

    def _paint_chips(self):
        states = getattr(self, "_chip_state", {})
        for k, c in self.chips.items():
            st = states.get(k, "off")
            if st == "on":
                marker, color, weight = "●", T.C_BLUE, "700"
            elif st == "done":
                marker, color, weight = "✓", T.P["ok_fg"], "600"
            else:
                marker, color, weight = "○", T.P["ink3"], "500"
            c.setText(f"{marker}  {c.property('stepName')}")
            c.setStyleSheet(
                f"background:transparent; color:{color}; font-size:14px;"
                f"font-weight:{weight}; padding:0 4px;")

    def _set_chips(self, **kw):
        previous = getattr(self, "_chip_state", {})
        self._chip_state = kw
        self._paint_chips()
        active = next((i for i, key in enumerate(("read", "do", "lock"))
                       if kw.get(key) == "on"), None)
        self.phase_relay.set_stage(active)
        if kw.get("do") == "on" and previous.get("do") != "on":
            from chiyo_art import ChiyoArtwork
            for artwork in self.chiyo_art_slot.findChildren(ChiyoArtwork):
                artwork.gesture.play()

    # --------------------------------------------------- 逻辑
    def _idle_hint(self):
        if self._clock_custom_total is not None:
            total = self._clock_custom_total
            return "自定义倒计时 %02d:%02d:%02d，点击开始即可计时" % (
                total // 3600, (total % 3600) // 60, total % 60)
        if self.read_total <= 0:
            return "已选跳过阅题，点击「开始作答」即可开始计时"
        return ("阅题 %d 分钟后" % (self.read_total // 60)
                + "开始作答 · 本次共 %d 分钟" % ((self.read_total + self.do_total) // 60))

    def _sync_ring_copy(self, animate_duration=False):
        if self.phase == self.IDLE:
            if self._clock_custom_total is not None:
                total = self._clock_custom_total
                title = "自定义倒计时"
                subtitle = "点击开始或桌面时钟按钮计时"
            else:
                title = "阅题时间" if self.read_total > 0 else "作答时间"
                total = self.read_total if self.read_total > 0 else self.do_total
                subtitle = ("随后作答 %d 分钟 · 共 %d 分钟" %
                            (self.do_total // 60, (self.read_total + self.do_total) // 60)
                            if self.read_total > 0 else "本次作答 %d 分钟" %
                            (self.do_total // 60))
            status = "准备开始"
            self.ring.set_idle(True)
            if hasattr(self, "b_start"):
                self.b_start.setText(
                    "开始倒计时" if self._clock_custom_total is not None else
                    ("开始阅题" if self.read_total > 0 else "开始作答"))
                self.b_start.setEnabled(True)
            if self.ring.total != total:
                self.ring.set_total(total, animate=animate_duration, roll=animate_duration)
            self.ring.set_remain(total)
        elif self.phase == self.READ:
            title = "阅题时间"
            status = "已暂停" if not self.tick.isActive() else "正在阅题"
            subtitle = "阅题结束后自动进入作答" if self.chk_auto.isChecked() else "阅题结束后等待你开始作答"
            self.ring.set_idle(False)
        elif self.phase == self.DOING:
            if self._clock_custom_total is not None:
                title = "自定义倒计时"
                status = "已暂停" if not self.tick.isActive() else "正在倒计时"
                subtitle = "自定义时长倒计时"
            else:
                title = "作答时间"
                status = "已暂停" if not self.tick.isActive() else "正在作答"
                subtitle = "请规划并完成航线"
            self.ring.set_idle(False)
        else:
            if self._clock_custom_total is not None:
                title, status, subtitle = (
                    "自定义倒计时", "倒计时完成", "重置后可再次开始")
            else:
                title, status, subtitle = (
                    "模拟完成", "本次训练已结束", "可重置后开始下一次模拟")
            self.ring.set_idle(True)
        if self.phase == self.LOCK and hasattr(self, "b_start"):
            self.b_start.setText("重新开始")
        self.ring.set_copy(status, title, subtitle)
        self.countdown_runway.update()
        self.state_changed.emit()

    def set_read_len(self, sec):
        self._clock_custom_total = None
        self.read_total = sec
        for s, b in self.read_btns.items():
            b.setChecked(s == sec)
        self._sync_chips()
        cfg.set_("read_sec", sec)
        accent(self.read_btns.get(sec, self.hint))
        if self.phase == self.IDLE:
            self.hint.setText(self._idle_hint())
            self._sync_ring_copy(animate_duration=True)
        log.info("阅题时长=%ds", sec)

    def set_do_len(self, sec):
        self._clock_custom_total = None
        self.do_total = sec
        for s, b in self.do_btns.items():
            b.setChecked(s == sec)
        self._sync_chips()
        cfg.set_("do_sec", sec)
        accent(self.do_btns.get(sec, self.hint))
        if self.phase == self.IDLE:
            self.hint.setText(self._idle_hint())
            self._sync_ring_copy(animate_duration=True)
        log.info("作答时长=%ds", sec)

    def start_session(self):
        self._capture_timer_information()
        if self.phase == self.READ:
            self._to_doing(manual=True)
            return
        if self.phase == self.LOCK:
            self.reset()
        if self.phase != self.IDLE:
            return
        for button in (*self.read_btns.values(), *self.do_btns.values()):
            button.setEnabled(False)
        self.start_at = datetime.datetime.now()
        self._session_started_mono = time.monotonic()
        self._elapsed_paused_at = None
        self._elapsed_paused_total = 0.0
        self._last_elapsed = -1
        self.b_start.setEnabled(False)
        self.b_pause.setEnabled(True)
        self.b_pause.setText("暂停")
        self.rec.setText("开始于 " + self.start_at.strftime("%H:%M:%S"))
        if self._clock_custom_total is not None:
            self._enter_doing(from_read=False, duration=self._clock_custom_total)
            log.info("桌面自定义倒计时开始 %ds", self._clock_custom_total)
            return
        if self.read_total <= 0:
            # 选了「跳过阅题」：不跑阅题阶段，直接进入作答计时
            self._enter_doing(from_read=False)
            log.info("直接进入作答（跳过阅题） 作答=%ds", self.do_total)
            return
        self.phase = self.READ
        self.remain = self.read_total
        self._phase_deadline = time.monotonic() + self.read_total
        self._paused_at = None
        self.tick.start()
        self._set_chips(read="on", do="off", lock="off")
        self.ring.set_idle(False)
        self.ring.set_copy("正在阅题", "阅题时间",
                           "阅题结束后自动进入作答" if self.chk_auto.isChecked()
                           else "阅题结束后等待你开始作答")
        self.ring.set_total(self.read_total)
        self.ring.set_remain(self.read_total)
        self.hint.setText("阅题中：可查看题卡，不可作答")
        self._sync_ring_copy()
        self.b_start.setText("开始作答")
        self.b_start.setEnabled(True)
        self._push_clock()
        log.info("考试开始 阅题=%ds 作答=%ds", self.read_total, self.do_total)

    def toggle_from_clock(self):
        """让桌面时钟操作当前这份计时状态。"""
        if self.phase in (self.IDLE, self.LOCK):
            self.start_session()
        else:
            self.toggle_pause()

    def custom_from_clock(self):
        """设置并开始一段仅供本次使用的自定义倒计时。"""
        dialog = QDialog(self.window())
        dialog.setWindowTitle("自定义倒计时")
        dialog.setMinimumWidth(320)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        title = QLabel("设置倒计时时长")
        title.setStyleSheet(
            f"font-size:16px; font-weight:700; color:{T.P['ink']};")
        layout.addWidget(title)
        time_edit = QTimeEdit(dialog)
        time_edit.setDisplayFormat("HH:mm:ss")
        time_edit.setMinimumTime(QTime(0, 0, 1))
        time_edit.setMaximumTime(QTime(23, 59, 59))
        time_edit.setTime(QTime(0, 5, 0))
        time_edit.setMinimumHeight(42)
        layout.addWidget(time_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel |
            QDialogButtonBox.StandardButton.Ok, parent=dialog)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始计时")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        selected = time_edit.time()
        total = selected.hour() * 3600 + selected.minute() * 60 + selected.second()
        if total <= 0:
            return False
        self.reset()
        self._clock_custom_total = total
        self._sync_ring_copy()
        self.start_session()
        return True

    def select_stage_from_clock(self, stage):
        """从桌面时钟切换阶段，不创建另一套计时器。"""
        if stage == "read":
            if self.read_total <= 0:
                return False
            phase, duration = self.READ, self.read_total
        elif stage == "answer":
            phase, duration = self.DOING, self.do_total
        else:
            return False

        self._clock_custom_total = None
        if self.phase == self.LOCK:
            self.reset()
        now = time.monotonic()
        new_session = self.phase == self.IDLE
        was_running = self.tick.isActive()
        if new_session:
            self.start_at = datetime.datetime.now()
            self._session_started_mono = now
            self._elapsed_paused_at = None
            self._elapsed_paused_total = 0.0
            self._last_elapsed = -1
            self.rec.setText("开始于 " + self.start_at.strftime("%H:%M:%S"))
            for button in (*self.read_btns.values(), *self.do_btns.values()):
                button.setEnabled(False)

        paused = not new_session and not was_running
        self.tick.stop()
        self.phase = phase
        self.remain = duration
        self._phase_deadline = now + duration
        self._paused_at = now if paused else None
        if paused and self._elapsed_paused_at is None:
            self._elapsed_paused_at = now
        if not paused:
            self.tick.start()

        if phase == self.READ:
            self._set_chips(read="on", do="off", lock="off")
            self.ring.set_copy(
                "正在阅题", "阅题时间",
                "阅题结束后自动进入作答" if self.chk_auto.isChecked()
                else "阅题结束后等待你开始作答")
            self.hint.setText("阅题中：可查看题卡，不可作答")
            self.b_start.setText("开始作答")
            self.b_start.setEnabled(True)
        else:
            self._set_chips(read="done", do="on", lock="off")
            self.ring.set_copy("正在作答", "作答时间", "请规划并完成航线")
            self.hint.setText("作答中：请规划并上传航线")
            self.b_start.setText("作答中")
            self.b_start.setEnabled(False)
        self.ring.set_idle(False)
        self.ring.set_total(duration)
        self.ring.set_remain(duration)
        self.b_pause.setEnabled(True)
        self.b_pause.setText("继续" if paused else "暂停")
        self._sync_ring_copy()
        self._push_clock(running=not paused)
        log.info("桌面时钟切换阶段=%s paused=%s", stage, paused)
        return True

    def toggle_pause(self):
        self._capture_timer_information()
        # 阅题倒计时已经走完时，这个按钮的语义就是「开始作答」。
        # 旧代码在这里额外要求 chk_auto 已勾选 —— 于是用户一旦取消勾选
        # 「阅题结束后自动进入作答」，条件不成立就掉到下面的暂停/继续分支：
        # tick 重新跑起来，但 phase 仍是 READ、remain 仍是 0，下一个 tick
        # 又判定「阅题已结束」，永远进不了作答阶段（表现为点继续没反应，
        # 只能重置，而重置又退回阅题倒计时）。
        if self.phase == self.READ and self.remain <= 0:
            self._to_doing(manual=True)
            return
        if self.tick.isActive():
            self.tick.stop()
            self._paused_at = time.monotonic()
            self._elapsed_paused_at = self._paused_at
            self.b_pause.setText("继续")
            self.hint.setText("已暂停")
            self._sync_ring_copy()
            self._push_clock(running=False)
            log.info("暂停")
        else:
            now = time.monotonic()
            if self._paused_at is not None and self._phase_deadline is not None:
                self._phase_deadline += now - self._paused_at
            if self._elapsed_paused_at is not None:
                self._elapsed_paused_total += now - self._elapsed_paused_at
                self._elapsed_paused_at = None
            self._paused_at = None
            self.tick.start()
            self.b_pause.setText("暂停")
            self.hint.setText("阅题中：可查看题卡，不可作答"
                              if self.phase == self.READ
                              else "作答中：请规划并上传航线")
            self._sync_ring_copy()
            self._push_clock()
            log.info("继续")

    def reset(self):
        self._capture_timer_information()
        for button in (*self.read_btns.values(), *self.do_btns.values()):
            button.setEnabled(True)
        self.tick.stop()
        self.phase = self.IDLE
        idle_total = (self._clock_custom_total if self._clock_custom_total is not None
                      else (self.read_total if self.read_total > 0 else self.do_total))
        self.remain = idle_total
        self.start_at = None
        self._session_started_mono = None
        self._elapsed_paused_at = None
        self._elapsed_paused_total = 0.0
        self._phase_deadline = None
        self._paused_at = None
        self._last_elapsed = -1
        self.b_start.setEnabled(True)
        self.b_pause.setEnabled(False)
        self.b_pause.setText("暂停")
        self.ring.set_idle(False)
        self.ring.set_copy("正在作答", "作答时间", "请规划并完成航线")
        self.ring.set_total(idle_total)
        self.ring.set_remain(idle_total)
        self.hint.setText(self._idle_hint())
        self.ring.set_idle(True)
        self._sync_ring_copy()
        self.rec.setText("尚未开始")
        self.elapsed.setText("已用时 00:00")
        self._set_chips(read="off", do="off", lock="off")
        self.lock_stop()
        w = self.window()
        if hasattr(w, "clock") and w.clock:
            w.clock.running = False
            w.clock.update()
        self.state_changed.emit()
        self._push_clock(running=False)
        log.info("重置")

    def _on_tick(self):
        if not self.tick.isActive() and self._paused_at is not None:
            return
        now = time.monotonic()
        previous_remain = self.remain
        if self._phase_deadline is not None and self.phase in (self.READ, self.DOING):
            self.remain = max(0, int(self._phase_deadline - now + 0.999999))
        remain_changed = self.remain != previous_remain
        if remain_changed and previous_remain > 10 >= self.remain > 0:
            # 临近结束仅强调一次，避免每秒闪烁干扰作答。
            self._last_seconds_feedback()
        if self._session_started_mono is not None:
            elapsed_end = (self._elapsed_paused_at if self._elapsed_paused_at is not None
                           else now)
            el = max(0, int(elapsed_end - self._session_started_mono
                            - self._elapsed_paused_total))
            if el != self._last_elapsed:
                self._last_elapsed = el
                self.elapsed.setText("已用时 %02d:%02d" % (el // 60, el % 60))

        if self.phase == self.READ:
            if remain_changed:
                self.ring.set_remain(self.remain)
                self._push_clock()
            if self.remain <= 0:
                self._to_doing()
        elif self.phase == self.DOING:
            if remain_changed:
                self.ring.set_remain(self.remain)
                self._push_clock()
            if self.remain <= 0:
                self._to_lock()

    def _push_clock(self, running=True):
        """把当前倒计时同步给桌面时钟"""
        w = self.window()
        ck = getattr(w, "clock", None)
        if ck:
            phase = ("read" if self.phase == self.READ else
                     "answer" if self.phase == self.DOING else
                     "done" if self.phase == self.LOCK else "idle")
            ck.set_time(self.ring.total, self.remain, self.ring.title, running,
                        phase, self.read_total)
        self.state_changed.emit()

    def _to_doing(self, manual=False):
        """进入作答阶段。

        manual=True：用户点了「继续」或「跳过阅题」，无条件进入作答。
        manual=False：由阅题倒计时自然走完触发；若用户关掉了
        「阅题结束后自动进入作答」，就停在「阅题已结束」等待手动继续
        （此时 phase 保持 READ、remain 保持 0，靠 toggle_pause 里
        remain<=0 的分支收口）。
        """
        if not manual and not self.chk_auto.isChecked():
            self.tick.stop()
            self._paused_at = time.monotonic()
            self.hint.setText("阅题已结束，点「继续」开始作答")
            self.b_pause.setText("继续")
            self._push_clock(running=False)
            log.info("阅题结束，等待手动进入作答")
            return
        self._enter_doing(from_read=(self.phase == self.READ), natural=not manual)

    def _enter_doing(self, from_read=True, natural=False, duration=None):
        """真正切换到作答阶段的动作。from_read=True 表示前面跑过阅题，
        会在记录里补一行「阅题结束」；跳过阅题直接进入时为 False。"""
        if from_read:
            self._capture_timer_information()
        duration = int(self.do_total if duration is None else duration)
        custom = self._clock_custom_total is not None
        now = time.monotonic()
        if self._elapsed_paused_at is not None:
            self._elapsed_paused_total += now - self._elapsed_paused_at
            self._elapsed_paused_at = None
        previous_deadline = self._phase_deadline
        self.phase = self.DOING
        self.remain = duration
        self._phase_deadline = ((previous_deadline if natural and previous_deadline is not None else now)
                                + duration)
        self._paused_at = None
        self._set_chips(read="done", do="on", lock="off")
        self.ring.set_idle(False)
        if custom:
            self.ring.set_copy("正在倒计时", "自定义倒计时", "自定义时长倒计时")
            self.hint.setText("自定义倒计时进行中")
        else:
            self.ring.set_copy("正在作答", "作答时间", "请规划并完成航线")
            self.hint.setText("作答中：请规划并上传航线")
        self.ring.set_total(duration)
        self.ring.set_remain(duration)
        self.b_start.setText("倒计时中" if custom else "作答中")
        self.b_start.setEnabled(False)
        self.b_pause.setEnabled(True)
        self.b_pause.setText("暂停")
        # 手动进入 / 跳过阅题时 tick 可能已被停掉，这里必须重新启动，
        # 否则作答倒计时根本不会走。
        if not self.tick.isActive():
            self.tick.start()
        if from_read:
            self.rec.setText((self.rec.text() or "")
                             + "\n阅题结束 "
                             + datetime.datetime.now().strftime("%H:%M:%S"))
        self._push_clock()
        log.info("%s，%ds", "自定义倒计时开始" if custom else "进入作答",
                 duration)

    def _to_lock(self):
        self._capture_timer_information()
        if self._clock_custom_total is None:
            self._save_session_result()
        self.phase = self.LOCK
        self.tick.stop()
        self._phase_deadline = None
        self._paused_at = None
        self._set_chips(read="done", do="done", lock="on")
        self._sync_ring_copy()
        reason = ("自定义倒计时已到" if self._clock_custom_total is not None
                  else "%d 分钟作答时间已到" % (self.do_total // 60))
        self.hint.setText("自定义倒计时结束" if self._clock_custom_total is not None
                          else "作答时间已到，已锁定")
        self.b_pause.setEnabled(False)
        self.b_start.setEnabled(True)
        self.b_start.setText("重新开始")
        self.rec.setText((self.rec.text() or "")
                         + "\n作答结束 "
                         + datetime.datetime.now().strftime("%H:%M:%S"))
        self._push_clock(running=False)
        if self.chk_sound.isChecked():
            try:
                from sound import manager as sound_manager
                sound_manager().play("end")
            except Exception:
                pass
        w = self.window()
        if hasattr(w, "on_time_up"):
            w.on_time_up(reason)

    def lock_stop(self):
        w = self.window()
        if hasattr(w, "on_time_up_dismiss"):
            w.on_time_up_dismiss()


# ================================================================ 入口
def _launch_splash_process():
    """用同一套运行时启动独立动画进程，开发和发布包共用此路径。"""
    video_path = os.path.abspath(A("splash.mp4"))
    poster_path = os.path.abspath(A("splash-poster.png"))
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--splash-helper", video_path, poster_path]
    else:
        command = [sys.executable, os.path.abspath(__file__),
                   "--splash-helper", video_path, poster_path]
    options = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(command, **options)


def main():
    from logger import get_logger
    lg = get_logger()
    lg.info('=' * 64)
    lg.info('%s 启动', APP_TITLE)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    if '--recovery-qa' in sys.argv:
        from recovery_qa import run
        index = sys.argv.index('--recovery-qa')
        sys.exit(run(app, sys.argv[index + 1]) if index + 1 < len(sys.argv) else 2)
    if '--developer-qa' in sys.argv:
        from developer_qa import run
        index = sys.argv.index('--developer-qa')
        sys.exit(run(app, sys.argv[index + 1]) if index + 1 < len(sys.argv) else 2)
    if '--layout-qa' in sys.argv:
        from layout_refresh_qa import run
        index=sys.argv.index('--layout-qa')
        sys.exit(run(app,sys.argv[index+1]) if index+1<len(sys.argv) else 2)
    if '--teaching-qa' in sys.argv:
        from teaching_qa import run
        index = sys.argv.index('--teaching-qa')
        if index + 1 >= len(sys.argv):
            sys.exit(2)
        sys.exit(run(app, sys.argv[index + 1]))
    app.setApplicationName(APP_TITLE)
    startup_started = time.perf_counter()
    try:
        splash_process = _launch_splash_process()
    except Exception:
        splash_process = None
        lg.exception("启动动画进程无法启动，将直接打开主界面")

    # 先注册随包分发的字体（得意黑），再建样式表 —— 否则 QSS 里的字体名对不上，
    # 桌面时钟会悄悄退回系统手写体
    T.load_app_fonts()
    T.apply(T.resolve_theme(cfg.get('theme', 'system')))
    startup_state = {
        "imports_ready": False,
        "import_error": None,
        "main_ready": False,
        "main_shown": False,
        "splash_done": splash_process is None,
    }
    window_state = {"instance": None}

    def close_splash_process():
        if splash_process is None or splash_process.poll() is not None:
            return
        try:
            splash_process.terminate()
            splash_process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            splash_process.kill()
        except Exception:
            lg.exception("停止启动动画进程失败")

    def finish():
        if (not startup_state["splash_done"] or not startup_state["main_ready"]
                or startup_state["main_shown"]):
            return
        win = window_state["instance"]
        if win is None:
            return
        startup_state["main_shown"] = True
        win.show()
        win.raise_()
        win.activateWindow()
        # show 与入场起点设置在同一事件回调内，首帧不会先露出终点状态。
        win.first_intro()
        lg.info('界面就绪')
        if '--smoke-test' in sys.argv or '--smoke-test-installed' in sys.argv:
            # 自检由明确的退出码结束，不能先被最后一个窗口关闭触发成功退出。
            app.setQuitOnLastWindowClosed(False)
            # 打包验收使用相同启动路径，检查空题卡启动、中文 OCR 和 API 图片编码。
            from ocr import available_languages, recognize_image
            import tempfile
            from PySide6.QtGui import QImage, QPainter
            from vision_assist import _image_data
            languages, language_warning = available_languages()
            smoke_text, smoke_warning = '', ''
            image_ok = False
            image_warning = ''
            try:
                with tempfile.TemporaryDirectory(prefix='azimuth_vlm_smoke_') as folder:
                    probe = QImage(2200, 1200, QImage.Format.Format_RGB32)
                    probe.fill(QColor('#ffffff'))
                    painter = QPainter(probe)
                    painter.setPen(QColor('#111111'))
                    painter.setFont(QFont('Microsoft YaHei UI', 76))
                    painter.drawText(100, 360, '避让点：300米')
                    painter.drawText(100, 620, '正六边形航线')
                    painter.end()
                    probe_path = os.path.join(folder, 'synthetic-card.png')
                    if probe.save(probe_path, 'PNG'):
                        smoke_text, smoke_warning = recognize_image(probe_path)
                        image_ok = _image_data(probe_path).startswith(
                            'data:image/jpeg;base64,')
                    if not image_ok:
                        image_warning = '题卡图片没有编码为 JPEG 数据'
            except Exception as exc:
                image_warning = str(exc)[:240]
            ocr_ok = ('chi_sim' in languages and
                      any(word in smoke_text for word in ('避让点', '六边形')))
            bank_ok = ('--smoke-test-installed' in sys.argv or not win.p_bank.all)
            ok = (win.stack.currentIndex() == 4 and bank_ok
                  and ocr_ok and image_ok)
            lg.info('打包启动检查：%s', '通过' if ok else '失败')
            lg.info('打包 OCR 检查：%s%s', '通过' if ocr_ok else '失败',
                    '' if ocr_ok else '；%s %s' % (language_warning, smoke_warning))
            lg.info('打包视觉图片检查：%s%s', '通过' if image_ok else '失败',
                    '' if image_ok else '；%s' % image_warning)
            QTimer.singleShot(300, win.close)
            QTimer.singleShot(400, lambda: app.exit(0 if ok else 2))

    def poll_splash_process():
        if startup_state["splash_done"]:
            return
        exit_code = splash_process.poll()
        if exit_code is None:
            return
        startup_state["splash_done"] = True
        splash_poll.stop()
        if exit_code != 0:
            lg.warning("启动动画进程提前退出，退出码：%s", exit_code)
        finish()

    def preload_ui_modules():
        try:
            from main_window import MainWindow
            from main import StageWidget, LockOverlay, MosaicOverlay, TimerPage
            from bank_page import BankPage
            from knowledge_page import KnowledgePage
            from settings_page import SettingsPage
            from web_home_page import WebHomePage
            from chiyo_art import ChiyoArtwork, ChiyoDecoration
            startup_state["main_window_class"] = MainWindow
        except Exception as exc:
            startup_state["import_error"] = exc
        finally:
            startup_state["imports_ready"] = True

    def create_main_window_when_ready():
        if not startup_state["imports_ready"]:
            QTimer.singleShot(12, create_main_window_when_ready)
            return
        if startup_state["import_error"] is not None:
            lg.error("启动模块预加载失败：%s", startup_state["import_error"])
            close_splash_process()
            app.exit(1)
            return
        try:
            main_window_class = startup_state["main_window_class"]
            win = main_window_class()
            win.prepare_startup()
        except Exception:
            lg.exception("主界面预加载失败")
            close_splash_process()
            app.exit(1)
            return
        window_state["instance"] = win
        startup_state["main_ready"] = True
        startup_elapsed = (time.perf_counter() - startup_started) * 1000
        lg.info("启动预加载完成，用时 %.0f ms", startup_elapsed)
        finish()

    splash_poll = QTimer(app)
    splash_poll.setInterval(24)
    splash_poll.timeout.connect(poll_splash_process)
    if splash_process is not None:
        splash_poll.start()
    app.aboutToQuit.connect(close_splash_process)
    threading.Thread(target=preload_ui_modules,
                     name="azimuth-startup-import", daemon=True).start()
    QTimer.singleShot(0, create_main_window_when_ready)
    QTimer.singleShot(0, poll_splash_process)
    QTimer.singleShot(0, finish)
    sys.exit(app.exec())


if __name__ == '__main__':
    # 让主窗口复用启动入口模块，避免打包运行时重复导入整份主程序。
    sys.modules.setdefault("main", sys.modules[__name__])
    main()
