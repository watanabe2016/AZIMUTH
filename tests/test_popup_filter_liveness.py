"""验证筛选缓存动效、下拉选择和快速反向时的清理与可操作性。"""
import copy,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from PySide6.QtCore import Qt,QPoint,QEvent
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QGraphicsOpacityEffect
from PySide6.QtTest import QTest
import config as cfg
import bankdata as BD
from ui_kit import Combo,_PopupMotionImage
from bank_page import BankPage,_QuestionDiagram


class PopupAndFilterLivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.saved=cfg._path,cfg._cache,BD._root
        cfg._path=str(Path(self.tmp.name)/"config.json")
        cfg._cache=copy.deepcopy(cfg.DEFAULTS)
        BD._root=str(ROOT/"data/bank")
        self.widgets=[]

    def tearDown(self):
        for w in self.widgets:
            if isinstance(w,BankPage):
                w._finish_filter_motion()
                w.cancel_intro()
            w.close()
            w.deleteLater()
        self.app.sendPostedEvents(None,QEvent.DeferredDelete)
        self.app.processEvents()
        cfg._path,cfg._cache,BD._root=self.saved
        self.tmp.cleanup()

    def bank(self):
        page=BankPage();page.resize(1300,850);page.show()
        QTest.qWait(100)
        self.widgets.append(page)
        return page

    def test_filter_can_reverse_from_current_visual_state_and_settle(self):
        page=self.bank()
        page.b_filters.setChecked(True);QTest.qWait(120)
        self.assertIsNotNone(page._filter_layer)
        current=page._filter_layer.current_y
        page.b_filters.setChecked(False)
        self.assertAlmostEqual(page._filter_layer.start_y,current,delta=1)
        QTest.qWait(600)
        self.assertIsNone(page._filter_layer)
        self.assertTrue(page.filter_panel.isHidden())
        self.assertTrue(page.motion_surface.isVisible())

    def test_filter_button_remains_clickable_through_snapshot(self):
        page=self.bank()
        page.b_filters.setChecked(True);QTest.qWait(100)
        layer=page._filter_layer
        p=page.b_filters.mapTo(page,page.b_filters.rect().center())
        QTest.mouseClick(layer,Qt.LeftButton,pos=p)
        self.assertFalse(page.b_filters.isChecked())
        QTest.qWait(600)
        self.assertIsNone(page._filter_layer)

    def test_hide_or_resize_during_filter_releases_cache(self):
        page=self.bank()
        for action in (page.hide,lambda:page.resize(1200,800)):
            page.show();page.b_filters.setChecked(False);QTest.qWait(600)
            page.b_filters.setChecked(True);QTest.qWait(80)
            action();QTest.qWait(50)
            self.assertIsNone(page._filter_layer)
            self.assertIsNone(page._filter_anim)

    def test_combo_rapid_reverse_keeps_selection_and_releases_visual_layers(self):
        host=QWidget();host.resize(600,600)
        layout=QVBoxLayout(host);combo=Combo();layout.addWidget(combo);layout.addStretch()
        for i in range(12):combo.addItem("选项%d"%i,i)
        self.widgets.append(host);host.show();QTest.qWait(50)
        for _ in range(10):
            combo.open_popup();QTest.qWait(30)
            self.assertIsNone(combo._mask.graphicsEffect())
            self.assertIsNotNone(combo._popup_motion._image)
            combo.close_popup();QTest.qWait(30)
            combo.open_popup();QTest.qWait(460)
            combo._on_picked(3);QTest.qWait(460)
            self.assertEqual(combo.currentData(),3)
            self.assertIsNone(combo._popup)
        self.app.sendPostedEvents(None,QEvent.DeferredDelete)
        self.assertFalse(host.findChildren(_PopupMotionImage))

    def test_hidden_paper_layout_does_not_recrop_images(self):
        page=self.bank()
        with patch.object(_QuestionDiagram,"_crop_original_diagram",wraps=_QuestionDiagram._crop_original_diagram) as crop:
            for _ in range(5):page._render_paper_card()
            crop.assert_not_called()
            page.set_card_layout("paper",save=False);QTest.qWait(120)
            self.assertFalse(page.paper_diagram._diagram_crop.isNull())
            self.assertGreaterEqual(crop.call_count,1)
            before=crop.call_count
            page.paper_diagram.set_question(page.cur,page.paper_diagram.fallback)
            self.assertEqual(crop.call_count,before)
            page.set_card_layout("standard",save=False)
if __name__=="__main__":unittest.main()
