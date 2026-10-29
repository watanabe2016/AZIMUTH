"""教学入口的真实路由、动画中断和简化图形交互。"""
import os,sys,copy,tempfile,unittest
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt,QAbstractAnimation
from PySide6.QtTest import QTest
import config as cfg

class TeachingHubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.old=cfg._path,cfg._cache
        self.folder=tempfile.TemporaryDirectory()
        cfg._path=str(Path(self.folder.name)/'config.json')
        cfg._cache=copy.deepcopy(cfg.DEFAULTS);cfg._cache['animations_on']=False
        from knowledge_page import KnowledgePage
        self.page=KnowledgePage();self.page.resize(1380,850);self.page.show();QTest.qWait(30)
        self.hub=self.page.teaching_hub
        self.hub.select_section('courses')

    def tearDown(self):
        self.page.close();self.page.deleteLater();self.app.processEvents()
        cfg._path,cfg._cache=self.old;self.folder.cleanup()

    def test_six_chapters_and_four_tools_route_to_real_content(self):
        from teaching_scene_catalog import CHAPTERS
        self.assertEqual(self.page.teaching_stack.currentIndex(),2)
        for card,(key,title) in zip(self.hub.course_row.cards,CHAPTERS):
            self.assertEqual(card.title,title)
            QTest.mouseClick(card,Qt.LeftButton);QTest.qWait(10)
            self.assertEqual(self.page.teaching_stack.currentIndex(),0)
            self.assertIn(title,self.page.teaching_breadcrumb.text())
            self.assertTrue(self.page.teaching_workspace.course_nav.isVisible())
            QTest.mouseClick(self.page.teaching_back,Qt.LeftButton)
        self.hub.select_section('tools')
        for card in self.hub.tool_row.cards:
            QTest.mouseClick(card,Qt.LeftButton);QTest.qWait(10)
            self.assertEqual(self.page.teaching_workspace._topic,card.key.split(':')[1])
            self.assertFalse(self.page.teaching_workspace.topic_rail.isVisible())
            QTest.mouseClick(self.page.teaching_back,Qt.LeftButton)

    def test_graph_switch_plays_and_hides_obsolete_controls(self):
        cfg._cache['animations_on']=True
        self.hub.select_section('shapes');QTest.qWait(30)
        for index,button in enumerate(self.page._shape_nav_buttons):
            QTest.mouseClick(button,Qt.LeftButton);QTest.qWait(600)
            self.assertEqual(self.page._shape_index,index)
            self.assertEqual(self.page.route_demo.motion.state(),QAbstractAnimation.Running)
            self.assertTrue(self.page.demo_toggle.isHidden())
            self.assertTrue(self.page.demo_controls.isHidden())
        QTest.mouseClick(self.page._shape_nav_buttons[-1],Qt.LeftButton)
        self.assertLess(self.page.route_demo.progress,.1)

    def test_course_cards_are_hidden_before_transition_can_show_them(self):
        from unittest.mock import patch

        cfg._cache['animations_on'] = True
        row = self.hub.course_row
        self.hub.stack.setCurrentIndex(3)

        def apply_change(host, mutation, background=None):
            mutation()
            return None

        with patch('workbench_ui.reveal_layout', side_effect=apply_change):
            self.hub.select_section('courses')
            self.assertTrue(row.isVisible())
            self.assertIsNotNone(row._prepared_frames)
            self.assertTrue(all(
                card._intro_hidden
                for card in row.cards))
            self.app.processEvents()
            QTest.qWait(40)
            self.assertIsNotNone(row.entrance)
            self.assertTrue(all(
                card._intro_hidden
                for card in row.cards))

    def test_warmed_menu_cards_reuse_startup_snapshots(self):
        cfg._cache['animations_on'] = True
        row = self.hub.menu_row
        self.hub.stack.setCurrentIndex(3)
        self.app.processEvents()
        self.assertTrue(row.warm_up())
        warmed = [frame[3] for frame in row._warmup_frames]
        self.assertEqual(len(warmed), len(row.cards))

        self.assertTrue(row.prepare_intro())
        self.assertEqual(len(row._prepared_frames), len(row.cards))
        for index, frame in enumerate(row._prepared_frames):
            self.assertIs(frame[2], warmed[index])

        row.cancel_intro()
        self.assertTrue(all(card.graphicsEffect() is None for card in row.cards))

    def test_card_entrance_is_interruptible_and_cleans_effects(self):
        cfg._cache['animations_on']=True
        row=self.hub.course_row;row.prepare_intro();row.play_intro()
        row.entrance.motion.setCurrentTime(120)
        self.assertIsNotNone(row.entrance)
        self.assertGreater(row.entrance.clock,0)
        self.assertTrue(all(c._intro_hidden for c in row.cards))
        self.assertTrue(all(c.updatesEnabled() for c in row.cards))
        self.assertTrue(all(c.isVisible() and c.isEnabled() for c in row.cards))
        self.hub.select_section('tools');QTest.qWait(60)
        self.assertIsNone(row.entrance)
        self.assertTrue(all(c.graphicsEffect() is None for c in row.cards))
        self.assertTrue(all(c.updatesEnabled() for c in row.cards))
        self.page.hide();self.app.processEvents()
        self.assertIsNone(self.hub.tool_row.entrance)

    def test_hidden_page_prepares_menu_without_queuing_intro(self):
        from unittest.mock import patch
        from knowledge_page import KnowledgePage
        from teaching_hub import TeachingHub
        cfg._cache['animations_on']=True
        with patch.object(TeachingHub, 'play_intro') as play_intro:
            page=KnowledgePage()
            self.app.processEvents()
            page.prepare_teaching_menu()
            self.app.processEvents()
            self.assertEqual(page.teaching_stack.currentIndex(),2)
            self.assertEqual(page.teaching_hub.stack.currentIndex(),3)
            play_intro.assert_not_called()
            page.close();page.deleteLater();self.app.processEvents()

    def test_reduced_motion_keeps_cards_immediately_available(self):
        self.hub.course_row.play_intro()
        self.assertIsNone(self.hub.course_row.entrance)
        self.assertTrue(all(c.graphicsEffect() is None for c in self.hub.course_row.cards))

    def test_transition_uses_theme_background_instead_of_flashing_white(self):
        from PySide6.QtGui import QColor
        import theme as T
        cfg._cache['animations_on']=True
        self.page._open_hub_chapter('basics')
        overlay=self.page._workbench_reveal
        self.assertEqual(overlay.pixmap.toImage().pixelColor(0,0).name(),QColor(T.P['shell']).name())
        QTest.qWait(600)
        self.assertIsNone(self.page._workbench_reveal)

    def test_narrow_window_browses_full_course_cards(self):
        self.page.resize(790,620);QTest.qWait(30)
        row=self.hub.course_row;scroll=row.parentWidget().parentWidget()
        self.assertGreater(scroll.horizontalScrollBar().maximum(),0)
        scroll.horizontalScrollBar().setValue(scroll.horizontalScrollBar().maximum())
        QTest.mouseClick(row.cards[-1],Qt.LeftButton)
        self.assertIn('题目条件设置',self.page.teaching_breadcrumb.text())

    def test_first_level_menu_and_detail_controls(self):
        self.hub.show_menu()
        self.assertEqual(self.hub.stack.currentIndex(),3)
        QTest.mouseClick(self.hub.menu_row.cards[0],Qt.LeftButton)
        self.assertEqual(self.hub.stack.currentIndex(),0)
        QTest.mouseClick(self.hub.course_row.cards[0],Qt.LeftButton)
        studio=self.page.teaching_workspace
        self.assertTrue(studio.lesson_picker.isAncestorOf(studio.course_lesson_selector))
        self.assertTrue(studio.lesson_picker.isAncestorOf(studio.step_list))
        self.assertTrue(studio.source_button.isHidden())
        for b in (self.page.teaching_back,studio.previous_button,studio.play_button,studio.next_button,studio.replay_button):
            self.assertFalse(b.text().strip())
            self.assertFalse(b.icon().isNull())
            self.assertTrue(b.accessibleName())
        QTest.mouseClick(self.page.teaching_back,Qt.LeftButton)
        QTest.mouseClick(self.hub.section_back,Qt.LeftButton)
        self.assertEqual(self.hub.stack.currentIndex(),3)

    def test_wheel_accumulates_and_clamps_horizontal_scroll(self):
        from PySide6.QtCore import QPoint,QPointF
        from PySide6.QtGui import QWheelEvent
        cfg._cache['animations_on']=True
        scroll=self.hub.course_row.parentWidget().parentWidget()
        bar=scroll.horizontalScrollBar()
        def wheel(delta):
            event=QWheelEvent(QPointF(30,30),QPointF(30,30),QPoint(),QPoint(0,delta),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
            scroll.wheelEvent(event)
        wheel(-120);QTest.qWait(60);first=scroll.target
        wheel(-120);self.assertGreater(scroll.target,first)
        QTest.qWait(450);self.assertEqual(bar.value(),round(scroll.target))
        cfg._cache['animations_on']=False
        wheel(-9999);self.assertEqual(bar.value(),bar.maximum())
        wheel(9999);self.assertEqual(bar.value(),0)

    def test_rapid_menu_return_and_resize_release_snapshots(self):
        cfg._cache['animations_on']=True
        for section in ('tools','courses','shapes','courses'):
            self.hub.select_section(section);QTest.qWait(20)
            self.hub.show_menu()
            self.page.resize(self.page.width()-10,self.page.height()-5)
            self.app.processEvents()
        self.hub.show_menu();QTest.qWait(600)
        self.assertIsNone(getattr(self.hub,'_workbench_reveal',None))
        self.page.hide();self.app.processEvents()
        self.assertIsNone(self.hub.menu_row.entrance)


    def test_wheel_during_intro_restores_visible_cards_after_resize(self):
        from PySide6.QtCore import QPoint, QPointF
        from PySide6.QtGui import QWheelEvent
        cfg._cache['animations_on'] = True
        row = self.hub.course_row
        scroll = row.parentWidget().parentWidget()
        for delay in (0, 60, 400, 900):
            self.page.setUpdatesEnabled(False)
            row.prepare_intro()
            self.page.setUpdatesEnabled(True)
            row.play_intro()
            QTest.qWait(delay)
            event = QWheelEvent(QPointF(30, 30), QPointF(30, 30), QPoint(),
                                QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                                Qt.NoScrollPhase, False)
            scroll.wheelEvent(event)
            self.page.resize(self.page.width()-1, self.page.height())
            QTest.qWait(500)
            self.assertIsNone(row.entrance)
            self.assertIsNone(row._prepared_frames)
            for card in row.cards:
                self.assertFalse(card._intro_hidden)
                self.assertTrue(card.updatesEnabled())
                self.assertTrue(card.isVisible())
                self.assertIsNone(card.graphicsEffect())
                self.assertGreater(card.grab().toImage().pixelColor(card.width()//2, card.height()//2).alpha(), 0)

if __name__=='__main__':unittest.main()
