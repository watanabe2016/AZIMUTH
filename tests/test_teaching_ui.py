"""验证真实操作、条件错误、练习保密与跨主题状态。"""
import os,sys,copy,tempfile,unittest
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
APP=QApplication.instance() or QApplication([])
import config as cfg,theme as T
from teaching_controls import TeachingWorkspace


class TeachingUITests(unittest.TestCase):
    def setUp(self):
        self.old=cfg._path,cfg._cache,T.app_theme,T.mode
        self.temp=tempfile.TemporaryDirectory();cfg._path=str(Path(self.temp.name)/'config.json');cfg._cache=copy.deepcopy(cfg.DEFAULTS)
        self.page=TeachingWorkspace();self.page.resize(1200,860);self.page.show();APP.processEvents()
    def tearDown(self):
        self.page.close();APP.processEvents();cfg._path,cfg._cache,theme,mode=self.old;T.set_app_theme(theme);T.apply(mode);self.temp.cleanup()
    def test_invalid_conditions_keep_valid_snapshot(self):
        self.page.editors['angle'].setValue(95);old=self.page.player.lesson
        self.page.apply_conditions();self.assertIs(self.page.player.lesson,old)
        self.assertIn('保留',self.page.error.text())
        self.page.editors['angle'].setValue(20);self.page.apply_conditions()
        self.assertEqual(self.page.player.lesson.steps[1].result,'250°')
        self.assertFalse(self.page.error.text())
    def test_chapter_sessions_are_isolated_and_legacy_records_kept(self):
        cfg.set_('lesson_completed_shapes',[0,3])
        self.page.player.seek(1,.6);self.page.select_chapter('geometry');self.page.player.seek(2)
        self.page.select_chapter('bearing');self.assertEqual(self.page.player.session.step,1)
        self.assertAlmostEqual(self.page.player.session.progress,.6)
        self.assertEqual(cfg.get('lesson_completed_shapes'),[0,3])
    def test_quiz_and_exam_hide_solution_and_score_checked_values(self):
        self.page.mode.setCurrentIndex(1);APP.processEvents()
        self.assertFalse(self.page.main.isVisible());self.assertTrue(self.page.quiz.isVisible())
        for q,w in zip(self.page.player.lesson.questions,self.page.quiz_inputs):w.setText(str(q.answer))
        self.page.submit_answers();self.assertIn('2 / 2',self.page.quiz_result.text())
        self.page.mode.setCurrentIndex(2);self.page.start_exam()
        self.assertFalse(self.page.quiz_edit_button.isEnabled());self.assertFalse(self.page.chapter_buttons['wind'].isEnabled())
        self.page.exam_started-=181;self.page._exam_tick()
        self.assertFalse(self.page.exam_active);self.assertIn('时间到',self.page.quiz_result.text())
        self.assertTrue(all(w.isReadOnly() for w in self.page.quiz_inputs))
    def test_themes_resize_and_drag_consistency(self):
        for theme in ('default','chiyo','anna','industrial'):
            T.set_app_theme(theme)
            for mode in ('light','dark'):
                T.apply(mode);self.page.theme_changed();self.page.resize(760,650);APP.processEvents()
                self.assertTrue(self.page.footer.isVisible());self.assertFalse(self.page.canvas.grab().isNull())
                self.assertLessEqual(self.page.footer.geometry().bottom(),self.page.height())
        self.page.drag_bearing(300)
        self.assertEqual(self.page.player.current.result,'300°')
        self.assertAlmostEqual(self.page.parameters['angle'],30)
    def test_case_has_source_and_review_and_deterministic_step_objects(self):
        self.page.select_chapter('cases');self.assertTrue(self.page.source_button.isVisible())
        self.assertIn('待复核',self.page.warning.text())
        self.page.player.seek(3)
        ids=[obj.id for obj in self.page.player.current.objects]
        self.assertIn('H',ids);self.assertIn('R',ids);self.assertIn('P1',ids)

    def test_learning_card_exports_actual_steps(self):
        from unittest.mock import patch
        import pymupdf
        target=Path(self.temp.name)/'lesson.pdf'
        with patch('teaching_controls.QFileDialog.getSaveFileName',return_value=(str(target),'PDF')):
            self.page.export_card()
        self.assertTrue(target.is_file())
        document=pymupdf.open(target)
        text=''.join(p.get_text() for p in document)
        self.assertIn('255',text);self.assertGreater(len(document),0)


if __name__=='__main__':unittest.main()
