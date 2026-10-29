"""在独立进程中播放启动动画，避免主界面构建挤占视频帧。"""
import os
import sys

from PySide6.QtCore import (QElapsedTimer, QEasingCurve, QPropertyAnimation,
                            QTimer, Qt, QUrl, Signal, QRectF)
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QWidget


class SplashWindow(QWidget):
    """以圆角透明窗口播放动画，并在媒体故障时保留静态启动画面。"""

    playback_finished = Signal()

    def __init__(self, video_path, poster_path, minimum_duration=3000):
        super().__init__()
        self.video_path = os.path.abspath(video_path)
        self.poster_path = os.path.abspath(poster_path)
        self.minimum_duration = max(0, int(minimum_duration))
        self.setWindowFlags(Qt.SplashScreen | Qt.FramelessWindowHint |
                            Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.pm = QPixmap(self.poster_path)
        if self.pm.isNull():
            self.setFixedSize(760, 340)
        else:
            ratio = self.pm.height() / max(1, self.pm.width())
            width = min(self.pm.width(), 1180)
            height = max(1, round(width * ratio))
            if width != self.pm.width():
                self.pm = self.pm.scaled(
                    width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            self.setFixedSize(width, height)

        self._corner_radius = 24
        self._video_frame = QImage()
        self._video_player = None
        self._video_sink = None
        self._video_started = False
        self._use_static_fallback = False
        self._is_complete = False
        self._end_of_media = None
        self._invalid_media = None
        self._elapsed = QElapsedTimer()

        self._first_frame_timer = QTimer(self)
        self._first_frame_timer.setSingleShot(True)
        self._first_frame_timer.setInterval(2200)
        self._first_frame_timer.timeout.connect(self._show_static_fallback)
        self._playback_watchdog = QTimer(self)
        self._playback_watchdog.setSingleShot(True)
        self._playback_watchdog.setInterval(8000)
        self._playback_watchdog.timeout.connect(self._finish_or_fallback)

        screen = QApplication.primaryScreen()
        if screen is not None:
            bounds = screen.availableGeometry()
            self.move(bounds.center().x() - self.width() // 2,
                      bounds.center().y() - self.height() // 2)
        self.setWindowOpacity(0.0)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(420)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)

    @property
    def is_complete(self):
        return self._is_complete

    def showEvent(self, event):
        super().showEvent(event)
        self._elapsed.start()
        self._fade.start()
        QTimer.singleShot(0, self._start_video)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)
        bounds = QRectF(self.rect())
        rounded = QPainterPath()
        rounded.addRoundedRect(bounds, self._corner_radius,
                               self._corner_radius)
        painter.setClipPath(rounded)
        if not self._video_frame.isNull() and not self._use_static_fallback:
            painter.drawImage(bounds, self._video_frame)
        elif not self.pm.isNull():
            painter.drawPixmap(self.rect(), self.pm)

    def _start_video(self):
        if self._is_complete or self._video_player is not None:
            return
        if not os.path.isfile(self.video_path):
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
            self._video_player.setSource(QUrl.fromLocalFile(self.video_path))
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
            self._finish_after_minimum_duration()
        elif status == self._invalid_media:
            self._show_static_fallback()

    def _on_media_error(self, *args):
        self._show_static_fallback()

    def _show_static_fallback(self):
        if self._is_complete:
            return
        self._use_static_fallback = True
        if self._video_player is not None:
            self._video_player.stop()
        self.update()
        self._finish_after_minimum_duration()

    def _finish_or_fallback(self):
        if self._video_started:
            self._finish_after_minimum_duration()
        else:
            self._show_static_fallback()

    def _finish_after_minimum_duration(self):
        elapsed = self._elapsed.elapsed() if self._elapsed.isValid() else 0
        remaining = max(0, self.minimum_duration - elapsed)
        QTimer.singleShot(remaining, self._mark_complete)

    def _mark_complete(self):
        if self._is_complete:
            return
        self._is_complete = True
        self._first_frame_timer.stop()
        self._playback_watchdog.stop()
        if self._video_player is not None:
            self._video_player.stop()
        self.playback_finished.emit()
        self.close()

    def mousePressEvent(self, event):
        """允许点击启动画面跳过等待。"""
        self._mark_complete()

    def closeEvent(self, event):
        self._first_frame_timer.stop()
        self._playback_watchdog.stop()
        if self._video_player is not None:
            self._video_player.stop()
        if not self._is_complete:
            self._is_complete = True
            self.playback_finished.emit()
        super().closeEvent(event)


def run_splash_helper(video_path, poster_path):
    """独立事件循环只负责动画播放，退出码交给启动主进程检测。"""
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("AZIMUTH 启动画面")
    splash = SplashWindow(video_path, poster_path)
    splash.playback_finished.connect(app.quit)
    splash.show()
    return app.exec()
