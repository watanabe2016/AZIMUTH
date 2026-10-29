"""验证动效打断、隐藏清理和静态降级，不依靠截图猜测状态。"""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from PySide6.QtCore import Qt, QAbstractAnimation, QEvent
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QListWidget
APP = QApplication.instance() or QApplication([])
from motion_polish import accent, fold, PhaseRelay, locate_item
from lesson_motion import LessonConceptCanvas


class MotionPolishTests(unittest.TestCase):
    def setUp(self):
        self.widgets = []
        self.enabled = patch("motion_polish.animations_enabled", return_value=True)
        self.enabled.start()

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
        APP.processEvents()
        self.enabled.stop()

    def host(self):
        host = QWidget()
        host.resize(480, 400)
        self.widgets.append(host)
        return host

    def test_feedback_does_not_capture_clicks_and_busy_stops_when_hidden(self):
        host = self.host()
        label = QLabel("真实状态", host)
        label.resize(300, 40)
        host.show()
        overlay = accent(label, "scan", busy=True)
        self.assertTrue(overlay.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.assertEqual(overlay.motion.loopCount(), -1)
        label.hide()
        self.assertEqual(overlay.motion.state(), QAbstractAnimation.Stopped)
        label.show()
        accent(label, "success")
        self.assertEqual(overlay.motion.loopCount(), 1)
        overlay.motion.setCurrentTime(overlay.motion.duration())
        self.assertTrue(overlay.isHidden())

    def test_phase_interrupt_continues_current_position_and_reduced_settles(self):
        host = self.host()
        layout = QHBoxLayout(host)
        labels = [QLabel(text) for text in ("阅题", "作答", "完成")]
        for label in labels:
            layout.addWidget(label)
        relay = PhaseRelay(host, labels)
        host.show()
        APP.processEvents()
        relay.set_stage(2)
        relay.motion.setCurrentTime(300)
        previous = relay.position
        relay.set_stage(1)
        self.assertAlmostEqual(relay.motion.startValue(), previous)
        host.hide()
        self.assertEqual(relay.motion.state(), QAbstractAnimation.Stopped)
        self.assertEqual(relay.position, 1)
        with patch("motion_polish.animations_enabled", return_value=False):
            relay.set_stage(2)
        self.assertEqual(relay.position, 2)

    def test_fold_reversal_restores_natural_height_and_final_visibility(self):
        host = self.host()
        layout = QVBoxLayout(host)
        panel = QWidget()
        content = QVBoxLayout(panel)
        content.addWidget(QLabel("费用明细\n示例说明\n设置项目"))
        layout.addWidget(panel)
        panel.hide()
        host.show()
        APP.processEvents()
        fold(panel, True)
        controller = panel._polish_fold
        controller.motion.setCurrentTime(220)
        fold(panel, False)
        controller.motion.setCurrentTime(520)
        self.assertTrue(panel.isHidden())
        self.assertEqual(panel.maximumHeight(), 16777215)
        with patch("motion_polish.animations_enabled", return_value=False):
            fold(panel, True)
        self.assertFalse(panel.isHidden())
        self.assertEqual(panel.maximumHeight(), 16777215)

    def test_directory_scroll_can_be_cancelled_by_user_input(self):
        host = self.host()
        layout = QVBoxLayout(host)
        listing = QListWidget()
        listing.addItems([str(i) for i in range(200)])
        layout.addWidget(listing)
        host.show()
        APP.processEvents()
        locate_item(listing, listing.item(170))
        controller = listing._polish_scroll
        controller.motion.setCurrentTime(180)
        self.assertGreater(listing.verticalScrollBar().value(), 0)
        controller.eventFilter(listing.viewport(), QEvent(QEvent.Wheel))
        self.assertEqual(controller.motion.state(), QAbstractAnimation.Stopped)

    def test_teaching_concepts_paint_all_themes_and_stop_on_hide(self):
        import theme as T
        T.load_app_fonts()
        T.load_anna_font()
        previous = T.app_theme, T.mode
        folder = ROOT / ".build-cache" / "concept-preview"
        folder.mkdir(parents=True, exist_ok=True)
        canvas = LessonConceptCanvas()
        self.widgets.append(canvas)
        canvas.resize(480, 380)
        canvas.show()
        try:
            for theme in ("default", "anna", "chiyo", "industrial"):
                T.set_app_theme(theme)
                T.apply("dark" if theme == "industrial" else "light")
                for mode in ("coordinate", "altitude", "wind", "avoidance"):
                    with patch("lesson_motion.animations_enabled", return_value=True):
                        canvas.set_mode(mode)
                        canvas.motion.setCurrentTime(2400)
                        self.assertFalse(canvas.grab().isNull())
                        canvas.motion.setCurrentTime(4200)
                        canvas.grab().save(str(folder / (theme + "-" + mode + ".png")))
                    self.assertEqual(canvas.progress, 1)
            canvas.hide()
            self.assertEqual(canvas.motion.state(), QAbstractAnimation.Stopped)
            with patch("lesson_motion.animations_enabled", return_value=False):
                canvas.replay()
            self.assertEqual(canvas.progress, 1)
        finally:
            T.set_app_theme(previous[0])
            T.apply(previous[1])

    def test_teaching_shapes_preserve_asset_and_cancel_previous(self):
        from knowledge_page import KnowledgePage
        page = KnowledgePage()
        self.widgets.append(page)
        page.resize(1400, 950)
        page.show()
        APP.processEvents()
        original = page.hero_figure._source_pixmap.cacheKey()
        page.teaching_hub.select_section('shapes')
        self.assertIs(page.figure_stack.currentWidget(), page.route_demo)
        for index in range(1, 5):
            page.select_shape(index,animate=False)
            APP.processEvents()
            self.assertIs(page.figure_stack.currentWidget(), page.route_demo)
            self.assertTrue(page.demo_zone.isHidden())
            chosen=page.hero_figure._source_pixmap.cacheKey()
            page._replay_demo()
            self.assertEqual(page.hero_figure._source_pixmap.cacheKey(),chosen)
        folder = ROOT / ".build-cache" / "concept-preview"
        page.grab().save(str(folder / "teaching-page.png"))
        page.teaching_hub.select_section('courses')
        self.assertEqual(page.route_demo.motion.state(), QAbstractAnimation.Stopped)

    def test_timer_visual_motion_does_not_delay_deadline_or_pause_state(self):
        from main import TimerPage
        page = TimerPage()
        self.widgets.append(page)
        page.resize(1350, 900)
        page.show()
        APP.processEvents()
        page.phase = page.DOING
        page.remain = 90
        page._phase_deadline = 200.0
        page._set_chips(read="done", do="on", lock="off")
        with patch("main.time.monotonic", return_value=120.0):
            page._on_tick()
        self.assertEqual(page.remain, 80)
        self.assertEqual(page.ring.remain, 80)
        self.assertEqual(page.phase_relay.target, 1)
        page.tick.start()
        with patch("main.time.monotonic", return_value=120.0):
            page.toggle_pause()
        self.assertFalse(page.tick.isActive())
        with patch("main.time.monotonic", return_value=130.0):
            page.toggle_pause()
        self.assertTrue(page.tick.isActive())
        self.assertEqual(page._phase_deadline, 210.0)
        page.tick.stop()


if __name__ == "__main__":
    unittest.main()
