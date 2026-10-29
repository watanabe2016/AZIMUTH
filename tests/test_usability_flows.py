"""覆盖题卡失败恢复、整页适配和导入状态等真实边界。"""
import copy
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtGui import QImage, QColor
from PySide6.QtCore import Qt, QThreadPool
_app = QApplication.instance() or QApplication([])
import config as cfg
import bankdata as BD
import theme as T
from bank_page import BankPage
from settings_page import SettingsPage
from workflow_feedback import import_report, recovery_advice


class UsabilityFlowTests(unittest.TestCase):
    def settle(self, milliseconds=700):
        # 等待线程解码时释放 Python 锁，同时持续处理界面事件。
        deadline = time.monotonic() + milliseconds / 1000
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = (cfg._path, cfg._cache, BD._root)
        cfg._path = str(Path(self.temp.name) / "config.json")
        cfg._cache = copy.deepcopy(cfg.DEFAULTS)
        cfg.set_("animations_on", False)
        cfg.set_("sound_master", False)
        BD._root = str(Path(self.temp.name) / "bank")
        Path(BD._root).mkdir()
        T.set_app_theme("default")
        T.apply("light")
        self.pages = []

    def tearDown(self):
        for page in self.pages:
            page.close()
        QThreadPool.globalInstance().waitForDone(3000)
        self.app.processEvents()
        cfg._path, cfg._cache, BD._root = self.previous
        self.temp.cleanup()

    def question(self):
        return {"id": 1, "region": "测试地区", "date": "10.09", "source": "测试题卡",
                "conditions_text": "航线高度为75米。", "requirements_text": "",
                "suggested_text": "", "graph_hint": "①②③", "imported": True}

    def bank(self, question=False):
        if question:
            BD.save([self.question()], record_version=False)
        page = BankPage()
        self.pages.append(page)
        page.resize(1000, 700)
        page.show()
        self.settle()
        return page

    def test_empty_import_and_filter_states_offer_correct_recovery(self):
        page = self.bank()
        self.assertEqual(page.d_title.text(), "题库中还没有题卡")
        page._set_import_phase("processing")
        self.assertEqual(page.d_title.text(), "正在导入题卡")
        self.assertFalse(page.b_import.isEnabled())
        page._set_import_phase("idle")
        self.assertTrue(page.b_import.isEnabled())
        page.all = [self.question()]
        page.q.setText("不存在的关键词")
        self.assertEqual(page.d_title.text(), "没有匹配的题目")
        self.assertTrue(page.b_clear_visible.isVisible())
        self.assertIn("不存在的关键词", page.filter_summary.text())
        page.clear_filters()
        self.assertEqual(len(page.view), 1)
        self.assertTrue(page.b_done.isEnabled())

    def test_question_change_skips_full_card_snapshot_transition(self):
        page = self.bank(question=True)
        first = self.question()
        second = dict(first, id=2, title="第二题")
        page.all = [first, second]
        page.view = [first, second]
        page.on_select(0)
        self.settle(40)
        with patch.object(page.detail, "grab", wraps=page.detail.grab) as capture:
            page.on_select(1)
            self.assertFalse(capture.called)
        self.assertIs(page.cur, second)
        self.assertFalse(hasattr(page, "_detail_transition"))

    def test_corrupt_image_shows_error_and_recovers_after_retry(self):
        path = Path(BD.fig_path(1))
        path.write_text("损坏的题卡", encoding="utf-8")
        page = self.bank(question=True)
        self.assertIn("读取失败", page.fig.text())
        self.assertTrue(page.b_retry.isVisible())
        image = QImage(400, 900, QImage.Format_RGB32)
        image.fill(QColor("white"))
        image.save(str(path))
        page._retry_figure()
        self.settle()
        self.assertFalse(page.fig.pixmap().isNull())
        self.assertFalse(page.b_retry.isVisible())

    def test_page_fit_contains_a4_and_width_fit_preserves_ratio(self):
        image = QImage(840, 1188, QImage.Format_RGB32)
        image.fill(Qt.white)
        image.save(BD.fig_path(1))
        page = self.bank(question=True)
        for layout in ("standard", "paper"):
            page.set_card_layout(layout, save=False)
            page._fit_preview()
            self.settle()
            viewport = page.fig_scroll.viewport()
            preview = page.paper_preview if layout == "paper" else page.fig
            self.assertLessEqual(preview.width(), viewport.width())
            self.assertLessEqual(preview.height(), viewport.height())
            self.assertAlmostEqual(preview.width() / preview.height(), 210 / 297, delta=0.01)
            self.assertEqual(page.fig_scroll.horizontalScrollBar().maximum(), 0)
            self.assertEqual(page.fig_scroll.verticalScrollBar().maximum(), 0)
            page._fit_preview("width")
            self.settle()
            self.assertGreater(preview.height(), page.fig_scroll.viewport().height())

    def test_settings_groups_hide_unrelated_controls_and_prices_start_collapsed(self):
        page = SettingsPage()
        self.pages.append(page)
        page.resize(1000, 700)
        page.show()
        self.app.processEvents()
        page._select_settings_group("api", save=False)
        self.app.processEvents()
        self.assertEqual([c._title_lab.text() for c in page._cards if not c.isHidden()], ["视觉识别 API"])
        self.assertTrue(page.api_pricing_panel.isHidden())
        page.b_pricing_toggle.click()
        self.assertFalse(page.api_pricing_panel.isHidden())
        page._select_settings_group("timer", save=False)
        self.assertEqual({c._title_lab.text() for c in page._cards if not c.isHidden()}, {"音效", "桌面倒计时时钟"})
        self.assertIn("默认人物", page.clock_unlock_hint.text())

    def test_chiyo_figure_actions_stay_aligned_in_stable_groups(self):
        T.set_app_theme("chiyo")
        T.apply("dark")
        page = self.bank()
        page.resize(1600, 900)
        self.settle(150)
        page._reflow_figure_toolbar()
        self.app.processEvents()
        self.assertEqual(page.preview_fit_choice.mapToGlobal(page.preview_fit_choice.rect().center()).y(),
                         page.b_full.mapToGlobal(page.b_full.rect().center()).y())
        self.assertEqual(page.b_open.mapToGlobal(page.b_open.rect().center()).y(),
                         page.b_done.mapToGlobal(page.b_done.rect().center()).y())
        self.assertTrue(page.b_fit.isHidden())
        page.resize(900, 700)
        self.settle(150)
        page._reflow_figure_toolbar()
        self.app.processEvents()
        self.assertGreater(page.b_open.mapToGlobal(page.b_open.rect().center()).y(),
                           page.b_full.mapToGlobal(page.b_full.rect().center()).y())

    def test_import_notes_do_not_count_as_failed_pages(self):
        report = import_report(["a.pdf：相同文件已导入，已跳过", "b.pdf：第2页渲染失败",
                                "c.pdf：没识别出文字，已只存题卡图", "b.pdf：第2页渲染失败"])
        self.assertEqual(len(report["skipped"]), 1)
        self.assertEqual(len(report["failed"]), 1)
        self.assertEqual(len(report["notes"]), 1)
        self.assertIn("坐标", recovery_advice("缺少基准坐标")[0])


if __name__ == "__main__":
    unittest.main()
