import os
import sys
import unittest
from pathlib import Path
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtTest import QTest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


class SettingsApiPricingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])
        from settings_page import SettingsPage

        cls.SettingsPage = SettingsPage

    def test_prices_trim_trailing_zeroes_without_losing_precision(self):
        page = self.SettingsPage()
        try:
            self.assertEqual(
                page.vision_api_input_price.textFromValue(0.3), "0.3")
            self.assertEqual(
                page.vision_api_cached_price.textFromValue(0.000006),
                "0.000006")
            self.assertEqual(page.vision_api_budget.suffix(), " USD")
        finally:
            page.close()

    def test_cached_pricing_fold_does_not_relayout_on_each_frame(self):
        from motion_polish import fold

        class ResizeProbe(QWidget):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.resize_count = 0

            def resizeEvent(self, event):
                self.resize_count += 1
                super().resizeEvent(event)

        host = QWidget()
        layout = QVBoxLayout(host)
        panel = QWidget(host)
        panel_layout = QVBoxLayout(panel)
        for index in range(8):
            from PySide6.QtWidgets import QLabel
            panel_layout.addWidget(QLabel("价格设置 %s" % index))
        panel.hide()
        after = ResizeProbe(host)
        after.setMinimumHeight(40)
        layout.addWidget(panel)
        layout.addWidget(after, 1)
        host.resize(340, 420)
        host.show()
        self.app.processEvents()
        try:
            with patch("motion_polish.animations_enabled", return_value=True):
                fold(panel, True, snapshot=True)
                resize_count = after.resize_count
                QTest.qWait(120)
                self.assertEqual(after.resize_count, resize_count)
                controller = panel._polish_fold
                self.assertIsNotNone(controller.snapshot_layer)
                QTest.qWait(450)
                self.assertTrue(panel.isVisible())
                self.assertIsNone(controller.snapshot_layer)

                fold(panel, False, snapshot=True)
                resize_count = after.resize_count
                QTest.qWait(120)
                self.assertEqual(after.resize_count, resize_count)
                self.assertIsNotNone(controller.snapshot_layer)
                QTest.qWait(450)
                self.assertTrue(panel.isHidden())
                self.assertIsNone(controller.snapshot_layer)
        finally:
            host.close()
            host.deleteLater()
            self.app.processEvents()

    def test_currency_change_updates_budget_unit_and_saves_settings(self):
        with patch("settings_page.cfg.set_") as save_value, \
                patch("settings_page.cfg.save"):
            page = self.SettingsPage()
            try:
                index = page.vision_api_currency.findData("CNY")
                page.vision_api_currency.setCurrentIndex(index, emit=True)
                self.app.processEvents()
                self.assertEqual(page.vision_api_budget.suffix(), " CNY")
                self.assertTrue(any(
                    call.args[:2] == ("vision_api_currency", "CNY")
                    for call in save_value.call_args_list))
            finally:
                page.close()



    def test_pricing_card_snapshot_avoids_frame_by_frame_layout(self):
        import copy, tempfile
        import config as cfg
        old = cfg._path, cfg._cache
        with tempfile.TemporaryDirectory() as folder:
            cfg._path = str(Path(folder)/'config.json')
            cfg._cache = copy.deepcopy(cfg.DEFAULTS)
            cfg._cache['animations_on'] = True
            page = self.SettingsPage()
            page.resize(1380, 850)
            page.show()
            page._select_settings_group('api', save=False)
            QTest.qWait(500)
            panel = page.api_pricing_panel
            frame = panel.parentWidget()
            try:
                page._toggle_api_pricing(True)
                self.assertTrue(page.b_pricing_toggle._checked_style)
                controller = page.api_pricing_fold
                self.assertIsNotNone(controller.layer)
                self.assertTrue(panel.isHidden())
                QTest.qWait(40)
                collapsed = frame.height()
                controller.motion.setCurrentTime(120)
                self.app.processEvents()
                middle_extent = controller.layer.extent
                self.assertGreater(middle_extent, collapsed)
                self.assertEqual(frame.height(), collapsed)
                self.assertEqual(page.api_pricing_content.objectName(), "ApiPricingPanel")
                self.assertIn("border-radius:12px", page.api_pricing_content.styleSheet())
                QTest.qWait(180)
                self.assertGreater(frame.height(), collapsed)
                expanded = frame.height()
                self.assertTrue(panel.isVisible())
                self.assertTrue(page.api_pricing_content.layout().isEnabled())
                self.assertIsNone(controller.layer)

                page._toggle_api_pricing(False)
                self.assertTrue(panel.isHidden())
                self.assertLess(frame.height(), expanded)
                close_height = frame.height()
                controller.motion.setCurrentTime(100)
                self.app.processEvents()
                self.assertEqual(frame.height(), close_height)
                self.assertIsNotNone(controller.layer)
                QTest.qWait(180)
                self.assertLess(frame.height(), expanded)
                self.assertTrue(panel.isHidden())
                self.assertIsNone(controller.layer)

                page._toggle_api_pricing(True)
                QTest.qWait(30)
                controller.motion.setCurrentTime(70)
                opening_extent = controller.layer.extent
                page._toggle_api_pricing(False)
                controller.motion.setCurrentTime(70)
                self.assertLess(controller.layer.extent, opening_extent)
                QTest.qWait(260)
                self.assertTrue(panel.isHidden())
                self.assertIsNone(controller.layer)
                self.assertTrue(page.api_pricing_content.layout().isEnabled())
                self.assertIsNone(getattr(panel, "_motion_response", None))
            finally:
                page.close()
                page.deleteLater()
                self.app.processEvents()
                cfg._path, cfg._cache = old

if __name__ == "__main__":
    unittest.main()
