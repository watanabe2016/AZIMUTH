"""核对演示航线终点、打断清理与静态降级，避免影响原题卡。"""
import copy
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from PySide6.QtCore import Qt, QAbstractAnimation
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
from PySide6.QtTest import QTest
from PySide6.QtGui import QFont
APP = QApplication.instance() or QApplication([])
import config as cfg
import theme as T
T.load_app_fonts()
T.load_anna_font()
APP.setFont(QFont(T.pick_font()))
from flight_motion import FlightRouteCanvas, CompletionStamp, CompanionGesture
from knowledge_page import KnowledgePage


class FlightMotionTests(unittest.TestCase):
    def setUp(self):
        self.previous = cfg._path, cfg._cache, T.app_theme, T.mode
        self.temp = tempfile.TemporaryDirectory()
        cfg._path = str(Path(self.temp.name) / "config.json")
        cfg._cache = copy.deepcopy(cfg.DEFAULTS)
        self.motion_patch = patch("flight_motion.animations_enabled", return_value=True)
        self.motion_patch.start()
        self.widgets = []
        T.set_app_theme("default")
        T.apply("light")

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
        APP.processEvents()
        self.motion_patch.stop()
        cfg._path, cfg._cache, theme, mode = self.previous
        T.set_app_theme(theme)
        T.apply(mode)
        self.temp.cleanup()

    def test_routes_reveal_and_land_on_correct_endpoint(self):
        canvas = FlightRouteCanvas()
        self.widgets.append(canvas)
        canvas.resize(480, 380)
        canvas.show()
        for index in range(8):
            canvas.set_shape(index)
            canvas.replay()
            start = canvas.grab().toImage()
            for fraction in (.25, .5, .75, 1):
                canvas.motion.setCurrentTime(int(canvas.motion.duration() * fraction))
                segment, position, heading = canvas._position()
                self.assertTrue(math.isfinite(heading))
                self.assertTrue(canvas.rect().contains(position.toPoint()))
                self.assertGreaterEqual(segment, 0)
            self.assertNotEqual(start, canvas.grab().toImage())
            endpoint = canvas._points()[-1 if index == 6 else 0]
            self.assertAlmostEqual(position.x(), endpoint.x(), places=4)
            self.assertAlmostEqual(position.y(), endpoint.y(), places=4)

    def test_hide_cancels_all_clocks_and_restores_zone_boundary(self):
        canvas = FlightRouteCanvas()
        self.widgets.append(canvas)
        canvas.show()
        canvas.replay()
        canvas.set_zone(True)
        canvas.zone_motion.setCurrentTime(220)
        canvas.hide()
        for animation in (canvas.motion, canvas.compass, canvas.zone_motion):
            self.assertEqual(animation.state(), QAbstractAnimation.Stopped)
        self.assertEqual(canvas.progress, 1)
        self.assertEqual(canvas.zone_progress, 1)
        canvas.show()
        with patch("flight_motion.animations_enabled", return_value=False):
            canvas.replay()
            canvas.set_zone(False)
            self.assertEqual(canvas.progress, 1)
            self.assertEqual(canvas.zone_progress, 0)
            self.assertEqual(canvas.motion.state(), QAbstractAnimation.Stopped)

    def test_simplified_demo_preserves_original_teaching_asset(self):
        folder = ROOT / ".build-cache" / "flight-motion-preview"
        folder.mkdir(parents=True, exist_ok=True)
        for theme in ("default", "chiyo", "anna", "industrial"):
            T.set_app_theme(theme)
            T.apply("dark" if theme == "industrial" else "light")
            APP.setStyleSheet(T.stylesheet())
            page = KnowledgePage()
            self.widgets.append(page)
            page.resize(1400, 950)
            page.show()
            page.teaching_hub.select_section('shapes')
            APP.processEvents()
            key = page.hero_figure._source_pixmap.cacheKey()
            self.assertIs(page.figure_stack.currentWidget(), page.route_demo)
            page.demo_zone.setChecked(True)
            page.route_demo.motion.setCurrentTime(page.route_demo.motion.duration() // 2)
            page.route_demo.zone_motion.setCurrentTime(650)
            page.route_demo.compass.setCurrentTime(420)
            APP.processEvents()
            self.assertEqual(page.hero_figure._source_pixmap.cacheKey(), key)
            self.assertIs(page.figure_stack.currentWidget(), page.route_demo)
            self.assertIn("航段", page.demo_caption.text())
            page.grab().save(str(folder / (theme + ".png")))
            self.assertTrue(page.demo_toggle.isHidden())
            page.teaching_hub.select_section('courses')
            self.assertEqual(page.route_demo.motion.state(), QAbstractAnimation.Stopped)
            page.close()
        APP.setStyleSheet("")

    def test_stamp_and_companion_are_finite_and_do_not_intercept_clicks(self):
        window = QWidget()
        self.widgets.append(window)
        window.resize(400, 280)
        button = QPushButton("标记已做", window)
        button.setGeometry(200, 200, 140, 40)
        window.show()
        stamp = CompletionStamp(button)
        stamp.play()
        self.assertTrue(stamp.testAttribute(Qt.WA_TransparentForMouseEvents))
        stamp.motion.setCurrentTime(600)
        self.assertFalse(stamp.grab().isNull())
        stamp.motion.setCurrentTime(1600)
        self.assertTrue(stamp.isHidden())
        stamp.play()
        button.hide()
        self.assertTrue(stamp.isHidden())
        self.assertEqual(stamp.motion.state(), QAbstractAnimation.Stopped)
        T.set_app_theme("default")
        gesture = CompanionGesture(window)
        gesture.play()
        self.assertFalse(gesture.delay.isActive())
        T.set_app_theme("anna")
        gesture.play()
        self.assertTrue(gesture.delay.isActive())
        gesture.stop()
        self.assertFalse(gesture.delay.isActive())
        self.assertEqual(gesture.progress, 0)

    def test_theme_characters_move_without_resizing_the_dialog(self):
        from main import AnnaTimeUpCard, ChiyoTimeUpCard
        for theme, card_type, size in (("anna", AnnaTimeUpCard, (460, 292)),
                                       ("chiyo", ChiyoTimeUpCard, (620, 410))):
            T.set_app_theme(theme)
            card = card_type()
            self.widgets.append(card)
            card.resize(*size)
            card.show()
            card.gesture.delay.stop()
            original = card.grab().toImage()
            card.gesture.motion.start()
            card.gesture.motion.setCurrentTime(625)
            self.assertNotEqual(original, card.grab().toImage())
            self.assertEqual((card.width(), card.height()), size)
            card.hide()
            self.assertEqual(card.gesture.motion.state(), QAbstractAnimation.Stopped)


if __name__ == "__main__":
    unittest.main()
