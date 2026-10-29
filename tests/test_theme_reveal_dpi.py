"""验证高 DPI 截图不会导致 Windows 主题切换动画被跳过。"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from PySide6.QtCore import QPointF, QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QWidget, QPushButton
import main_window as MW


class ThemeRevealDpiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def snapshot(self, size, dpr, color):
        image = QPixmap(round(size.width() * dpr), round(size.height() * dpr))
        image.setDevicePixelRatio(dpr)
        image.fill(QColor(color))
        return image

    def test_physical_snapshots_are_compared_in_logical_pixels(self):
        size = QSize(480, 300)
        for dpr in (1.0, 1.25, 1.5, 2.0):
            with self.subTest(dpr=dpr):
                pixmap = self.snapshot(size, dpr, "white")
                self.assertTrue(MW.snapshot_matches_widget(pixmap, size))
                self.assertFalse(MW.snapshot_matches_widget(pixmap, QSize(500, 300)))

    def test_prepare_and_start_reveal_accept_high_dpi(self):
        shell = QWidget()
        shell.resize(480, 300)
        source = QPushButton(shell)
        source.setGeometry(30, 30, 100, 30)
        shell.show()
        self.app.processEvents()
        class WindowStub:
            def __init__(self):
                self.shell = shell
                self._theme_reveal = None
            def isVisible(self):
                return True
            def _clear_theme_reveal(self):
                MW.MainWindow._clear_theme_reveal(self)
            def _theme_reveal_finished(self, layer):
                MW.MainWindow._theme_reveal_finished(self, layer)

        window = WindowStub()
        try:
            for dpr in (1.25, 1.5, 2.0):
                with self.subTest(dpr=dpr), patch.object(MW, "motion_enabled", return_value=True):
                    old = self.snapshot(shell.size(), dpr, "white")
                    new = self.snapshot(shell.size(), dpr, "blue")
                    with patch.object(shell, "grab", return_value=old):
                        self.assertTrue(MW.MainWindow._prepare_theme_reveal(window, source))
                    layer = window._theme_reveal
                    with patch.object(shell, "grab", return_value=new):
                        MW.MainWindow._start_theme_reveal(window)
                    self.assertEqual(layer._motion.state(), MW.QVariantAnimation.Running)
                    layer._set_progress(70)
                    frame = layer.grab().toImage()
                    self.assertEqual(frame.pixelColor(50, 45), QColor("blue"))
                    self.assertEqual(frame.pixelColor(470, 290), QColor("white"))
                    window._clear_theme_reveal()
        finally:
            window._clear_theme_reveal()
            shell.close()
            shell.deleteLater()
            self.app.processEvents()


    def test_superseded_refresh_callback_does_not_start_latest_layer(self):
        sentinel = object()
        window = type("WindowStub", (), {"_theme_refresh_revision": 2,
                                         "_theme_reveal": sentinel})()
        MW.MainWindow._start_theme_reveal(window, revision=1)
        self.assertIs(window._theme_reveal, sentinel)


if __name__ == "__main__":
    unittest.main()
