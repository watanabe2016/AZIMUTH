"""题库页缓存转场的可见性与中断清理回归。"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtTest import QTest

import bank_page


class _MotionHarness(QWidget):
    """只提供题库页动效方法所需的最小控件树。"""

    _capture_page_motion_snapshot = bank_page.BankPage._capture_page_motion_snapshot
    _clear_page_motion_snapshot = bank_page.BankPage._clear_page_motion_snapshot
    _stop_page_motion = bank_page.BankPage._stop_page_motion
    _finish_page_motion = bank_page.BankPage._finish_page_motion
    _start_page_motion = bank_page.BankPage._start_page_motion
    prepare_intro = bank_page.BankPage.prepare_intro
    play_intro = bank_page.BankPage.play_intro
    cancel_intro = bank_page.BankPage.cancel_intro
    play_exit = bank_page.BankPage.play_exit
    _begin_prepared_intro = bank_page.BankPage._begin_prepared_intro

    def __init__(self):
        super().__init__()
        self.resize(360, 220)
        self.motion_surface = QWidget(self)
        self.motion_surface.setGeometry(self.rect())
        self.motion_surface.setStyleSheet("background:#d13b63;")
        self._page_motion_animation = None
        self._page_motion_effect = None
        self._page_motion_snapshot = None
        self._page_motion_kind = None
        self._page_motion_callback = None
        self._intro_prepared = False
        self._page_intro_timer = QTimer(self)
        self._page_intro_timer.setSingleShot(True)
        self._page_intro_timer.timeout.connect(self._begin_prepared_intro)


class BankPageMotionPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_snapshot_keeps_page_visible_and_exit_restores_live_surface(self):
        page = _MotionHarness()
        page.show()
        self.app.processEvents()
        try:
            with patch.object(bank_page, "animations_enabled", return_value=True):
                page.prepare_intro()
                self.assertIsNone(page._page_motion_snapshot)
                self.assertFalse(page.motion_surface.isHidden())

                page.play_intro()
                QTest.qWait(30)
                snapshot = page._page_motion_snapshot
                self.assertIsNotNone(snapshot)
                self.assertTrue(page.motion_surface.isHidden())
                self.assertLess(snapshot.opacity, 0.5)
                self.assertEqual(snapshot._pixmap.toImage().pixelColor(80, 80).red(), 209)
                self.assertEqual(page._page_motion_animation.duration(), 420)
                QTest.qWait(470)
                self.assertIsNone(page._page_motion_snapshot)
                self.assertFalse(page.motion_surface.isHidden())

                completed = []
                page.play_exit(lambda: completed.append(True))
                self.assertEqual(page._page_motion_animation.duration(), 190)
                self.assertTrue(page.motion_surface.isHidden())
                QTest.qWait(250)
                self.assertEqual(completed, [True])
                self.assertIsNone(page._page_motion_snapshot)
                self.assertFalse(page.motion_surface.isHidden())
        finally:
            page.cancel_intro()
            page.close()
            self.app.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()

    def test_hidden_page_defers_capture_until_final_geometry_is_visible(self):
        page = _MotionHarness()
        page.resize(640, 400)
        page.motion_surface.resize(page.size())
        try:
            with patch.object(bank_page, "animations_enabled", return_value=True):
                page.prepare_intro()
                self.assertIsNone(page._page_motion_snapshot)
                self.assertFalse(page.motion_surface.isHidden())
                page.show()
                self.app.processEvents()
                page.play_intro()
                QTest.qWait(30)
                snapshot = page._page_motion_snapshot
                self.assertIsNotNone(snapshot)
                ratio = max(1.0, float(snapshot._pixmap.devicePixelRatio()))
                self.assertEqual(
                    (round(snapshot._pixmap.width() / ratio),
                     round(snapshot._pixmap.height() / ratio)),
                    (page.width(), page.height()))
                self.assertEqual(page._page_motion_animation.duration(), 420)
        finally:
            page.cancel_intro()
            page.close()
            self.app.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
