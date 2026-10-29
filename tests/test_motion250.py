"""检验动效手感、快速反向、资源退出以及新增操作的真实状态。"""
import copy, math, os, sys, tempfile, unittest
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout
from PySide6.QtCore import Qt, QAbstractAnimation
from PySide6.QtTest import QTest
APP=QApplication.instance() or QApplication([])
import config as cfg
from motion_runtime import response, clock, follow
from motion_polish import fold
from motion_catalog import ALL
from main import TimerPage
from flight_motion import FlightRouteCanvas
from lesson_motion import LessonConceptCanvas
from knowledge_page import KnowledgePage
from figure_board import FigureDialog

class Motion250Tests(unittest.TestCase):
    def setUp(self):
        self.old=(cfg._path,cfg._cache)
        self.directory=tempfile.TemporaryDirectory()
        cfg._path=str(Path(self.directory.name)/'config.json')
        cfg._cache=copy.deepcopy(cfg.DEFAULTS)
        cfg.set_('animations',True)
        cfg.set_('sound_master',False)
        self.widgets=[]

    def tearDown(self):
        for widget in self.widgets: widget.close()
        QTest.qWait(120)
        cfg._path,cfg._cache=self.old
        self.directory.cleanup()

    def show(self,widget):
        self.widgets.append(widget);widget.resize(1100,900);widget.show();QTest.qWait(40)
        return widget

    def test_catalog_unique_and_five_pages(self):
        self.assertEqual(len({item['id'] for item in ALL}),250)
        for page in ('home','bank','timer','settings','teaching'):
            self.assertEqual(sum(item['page']==page for item in ALL),50)

    def test_follow_is_independent_of_frame_rate(self):
        values=[]
        for hz in (30,60,120,144):
            value=0
            for _ in range(hz):value=follow(value,1,8,1/hz)
            values.append(value)
        self.assertLess(max(values)-min(values),1e-12)

    def test_shared_layers_do_not_erase_other_tracks_and_stop_hidden(self):
        target=self.show(QLabel('交互反馈'))
        response(target,'wave','test-wave',duration=450)
        response(target,'outline','test-outline',duration=650)
        self.assertEqual(len(target._motion_response.tracks),2)
        self.assertTrue(target._motion_response.testAttribute(Qt.WA_TransparentForMouseEvents))
        first=target.grab().toImage();QTest.qWait(230)
        self.assertNotEqual(first,target.grab().toImage())
        target.hide();QTest.qWait(25)
        self.assertNotIn(id(target._motion_response),clock().jobs)

    def test_fold_reverses_from_current_height_and_restores_constraints(self):
        host=QWidget();layout=QVBoxLayout(host);panel=QLabel('内容\n'*12)
        layout.addWidget(panel);self.show(host)
        fold(panel,False);QTest.qWait(200)
        before=panel.maximumHeight();fold(panel,True)
        self.assertEqual(panel.maximumHeight(),before)
        QTest.qWait(570)
        self.assertTrue(panel.isVisible());self.assertEqual(panel.maximumHeight(),16777215)
        fold(panel,False);QTest.qWait(570);self.assertTrue(panel.isHidden())

    def test_route_step_lands_on_real_vertex_with_unequal_edges(self):
        canvas=self.show(FlightRouteCanvas());canvas.set_shape(4);canvas._advance(0)
        for _ in range(len(canvas._points())):
            canvas.step_forward();_,position,_=canvas._position()
            self.assertLess(min(math.hypot(position.x()-p.x(),position.y()-p.y()) for p in canvas._points()),.01)

    def test_wind_strength_changes_render_and_cleans_up(self):
        canvas=self.show(LessonConceptCanvas());canvas.set_mode('wind');canvas.motion.stop();canvas._advance(.5)
        before=canvas.grab().toImage();canvas.set_strength(1.8);QTest.qWait(570)
        self.assertAlmostEqual(canvas.strength,1.8);self.assertNotEqual(before,canvas.grab().toImage())
        canvas.hide();self.assertEqual(canvas.strength_motion.state(),QAbstractAnimation.State.Stopped)

    def test_custom_timer_and_extension_use_real_deadline(self):
        page=self.show(TimerPage());page.custom_read.setValue(15);page.custom_do.setValue(45)
        page.apply_custom_duration();self.assertEqual((page.read_total,page.do_total),(15,45))
        page.start_session();deadline=page._phase_deadline;page.extend_time()
        self.assertEqual(page._phase_deadline,deadline+60)
        page.skip_stage();self.assertEqual(page.phase,page.DOING)
        page.reset()

    def test_teaching_controls_have_live_entry_and_progress(self):
        page=self.show(KnowledgePage());page.step_demo()
        self.assertIs(page.figure_stack.currentWidget(),page.route_demo)
        self.assertTrue(page.route_demo.paused)
        page.complete_lesson();self.assertIn(page._shape_index,cfg.get('lesson_completed_shapes'))
        page.select_shape(1,animate=False);self.assertEqual(page.b_complete.text(),'已掌握')
        page.open_lesson_card();QTest.qWait(570)
        dialog=page._lesson_card;self.widgets.append(dialog)
        self.assertEqual((dialog.preview.image.width(),dialog.preview.image.height()),(840,1188))
        start=dialog.preview.scale;dialog.zoom_by(1.15);QTest.qWait(200)
        self.assertGreater(dialog.preview.scale,start)
        dialog.hide();self.assertEqual(dialog.motion.state(),QAbstractAnimation.State.Stopped)

    def test_answer_panel_width_reversal_keeps_original_window_size(self):
        dialog=FigureDialog();self.widgets.append(dialog)
        dialog.show();QTest.qWait(50)
        if dialog._ans_on:
            dialog.toggle_answer();QTest.qWait(620)
        dialog.resize(600,560);QTest.qWait(50)
        original=dialog.width()
        dialog.toggle_answer();QTest.qWait(230)
        middle=dialog.width();self.assertGreater(middle,original)
        dialog.toggle_answer();self.assertEqual(dialog._answer_size_motion.startValue().width(),middle)
        QTest.qWait(620)
        self.assertEqual(dialog.width(),original,dict(hidden=dialog.apanel.isHidden(),
            minimum=dialog.minimumSize(),hint=dialog.minimumSizeHint(),layout=dialog.layout().minimumSize(),
            before=dialog._w_before,on=dialog._ans_on,panel_min=dialog.apanel.minimumWidth(),panel_max=dialog.apanel.maximumWidth()))
        self.assertTrue(dialog.apanel.isHidden())
