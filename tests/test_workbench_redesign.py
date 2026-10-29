"""工作台真实状态切换与图上拾取回归；使用独立配置。"""
import os,sys,copy,tempfile,unittest,math
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt,QPointF,QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
import config as cfg,theme as T

class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=(cfg._path,cfg._cache)
        cfg._path=str(Path(self.temp.name)/'config.json');cfg._cache=copy.deepcopy(cfg.DEFAULTS)
        cfg._cache['animations_on']=False;self.widgets=[]
    def tearDown(self):
        for w in self.widgets:w.close()
        self.app.processEvents();cfg._path,cfg._cache=self.old;self.temp.cleanup()
    def studio(self,number):
        from teaching_studio import TeachingStudio
        w=TeachingStudio();self.widgets.append(w);w.resize(1100,850);w.show();w.select_scene(number)
        w.player.pause();w.player.seek(1 if number in (47,48) else 7 if number==49 else len(w.player.lesson.steps)-1,1)
        self.app.processEvents();w.canvas.grab();return w
    def drag(self,canvas,key,target):
        start=canvas._handles[key]
        QTest.mousePress(canvas,Qt.LeftButton,pos=start.toPoint())
        event=QMouseEvent(QEvent.MouseMove,QPointF(target),QPointF(canvas.mapToGlobal(target.toPoint())),Qt.NoButton,Qt.LeftButton,Qt.NoModifier)
        QApplication.sendEvent(canvas,event)
        QTest.mouseRelease(canvas,Qt.LeftButton,pos=target.toPoint());self.app.processEvents()
    def test_heading_handle_changes_direction_and_background_pans(self):
        w=self.studio(47);c=w.canvas;r=min(c.width()-150,c.height()-145)*.43-20
        self.drag(c,'bearing',QPointF(c.width()/2,c.height()/2-r))
        self.assertAlmostEqual(w.parameters['heading'],0,delta=.2)
        QTest.mousePress(c,Qt.LeftButton,pos=c.rect().topLeft()+__import__('PySide6.QtCore',fromlist=['QPoint']).QPoint(35,45))
        self.assertEqual(c.drag,'pan');QTest.mouseRelease(c,Qt.LeftButton)
    def test_wind_handle_snaps_relative_to_plane(self):
        w=self.studio(48);c=w.canvas
        self.drag(c,'wind',QPointF(c.width()/2+80,c.height()/2))
        self.assertEqual(w.parameters['wind_mode'],'逆风')
        self.assertEqual(w.parameters['heading'],90)
    def test_angle_handle_synchronizes_formula_field_and_ray(self):
        w=self.studio(49);c=w.canvas;origin=c.project(__import__('teaching_math').Point(0,0))
        self.drag(c,'angle',origin+QPointF(0,65))
        self.assertAlmostEqual(w.parameters['angle'],90,delta=.1)
        self.assertEqual(w.editors['angle'].value(),w.parameters['angle'])
        ray=next(o for o in w.player.current.objects if o.id=='branch0')
        self.assertAlmostEqual(__import__('teaching_math').azimuthBetweenPoints(*ray.points),180,delta=.1)
    def test_avoidance_drag_preserves_projection_and_model_distance(self):
        w=self.studio(41);c=w.canvas;bounds=c.bounds;projection=c.projection()
        point=__import__('teaching_math').destinationPoint(__import__('teaching_math').Point(0,0),45,700)
        self.drag(c,'avoidance',c.project(point))
        self.assertEqual(c.bounds,bounds);self.assertEqual(c.projection(),projection)
        self.assertAlmostEqual(w.parameters['avoid_distance'],700,delta=1)
        self.assertAlmostEqual(w.parameters['avoid_heading'],45,delta=.1)
        self.assertIn('1200',w.player.current.result)
    def test_four_topics_preserve_complete_courses(self):
        w=self.studio(47)
        for key,number in [('wind',48),('avoidance',41),('angle',49),('courses',1)]:
            w.select_topic(key);self.assertEqual(w.number,number)
        w.select_chapter('settings');self.assertEqual(w.scene_selector.count(),5)
        self.assertEqual(w.course_lesson_selector.count(),5)
        self.assertEqual(len(w.course_grid.cards),6)
        self.assertTrue(w.course_nav.isVisible())
        self.assertTrue(w.scroll.isVisible());self.assertTrue(w.footer.isVisible())
        self.assertTrue(w.group_buttons['courses']._checked_style)
        self.assertFalse(w.group_buttons['tools']._checked_style)
        w.course_lesson_selector.setCurrentIndex(w.course_lesson_selector.findData(46));self.assertEqual(w.number,46)
        self.assertEqual(w.course_grid.current_key,'settings')
        grid=w.course_grid
        grid.resize(1100,240);grid._reflow();self.assertEqual(grid._columns,3)
        grid.resize(800,240);grid._reflow();self.assertEqual(grid._columns,3)
        grid.resize(650,240);grid._reflow();self.assertEqual(grid._columns,2)
        grid.resize(400,240);grid._reflow();self.assertEqual(grid._columns,1)
        grid.set_current('takeoff',animate=False);w.show_course_overview()
        QTest.mouseClick(grid.cards['takeoff'],Qt.LeftButton)
        self.assertEqual(w.chapter,'takeoff')
        self.assertEqual(w.number,w.course_lesson_selector.itemData(0))
        w.select_topic('angle')
        self.assertTrue(w.group_buttons['tools']._checked_style)
        self.assertFalse(w.group_buttons['courses']._checked_style)
    def test_course_cards_select_with_mouse_and_keyboard(self):
        w=self.studio(1);w.select_group('courses')
        card=w.course_grid.cards['headings']
        QTest.mouseClick(card,Qt.LeftButton);self.app.processEvents()
        self.assertEqual(w.chapter,'headings');self.assertEqual(w.course_grid.current_key,'headings')
        w.show_course_overview()
        card=w.course_grid.cards['lengths'];card.setFocus();self.app.processEvents()
        self.assertTrue(card.hasFocus());QTest.keyClick(card,Qt.Key_Return)
        self.app.processEvents();self.assertEqual(w.chapter,'lengths')

    def test_course_resume_card_tracks_last_course_lesson(self):
        w=self.studio(1);w.select_group('courses');w.select_chapter('settings')
        w.course_lesson_selector.setCurrentIndex(w.course_lesson_selector.findData(46))
        w.show_course_overview()
        self.assertEqual(w.course_resume._number,46)
        QTest.mouseClick(w.course_resume,Qt.LeftButton);self.app.processEvents()
        self.assertEqual(w.number,46);self.assertEqual(w.chapter,'settings')
        self.assertTrue(w.course_nav.isVisible())

    def test_course_cards_use_slow_eased_interruptible_selection_transition(self):
        from PySide6.QtCore import QAbstractAnimation
        cfg._cache['animations_on']=True
        w=self.studio(1);w.select_group('courses')
        grid=w.course_grid;grid.set_current('basics',animate=False);grid.set_current('headings')
        card=grid.cards['headings']
        self.assertEqual(card.selection_animation.duration(),420)
        self.assertEqual(card.selection_animation.state(),QAbstractAnimation.Running)
        QTest.qWait(470);self.assertEqual(grid.current_key,'headings')
        self.assertAlmostEqual(card._selection_progress,1.0,places=2)
    def test_focus_mode_selector_is_removed_and_paper_conditions_stay_visible(self):
        from bank_page import BankPage
        w=BankPage();self.widgets.append(w);w.resize(1200,850);w.show();self.app.processEvents()
        self.assertFalse(hasattr(w,'reading_modes'))
        self.assertFalse(hasattr(w,'set_reading_mode'))
        self.assertTrue(w.body_column.isVisible())
        w.set_card_layout('paper',save=False)
        self.app.processEvents()
        self.assertFalse(w.paper_conditions_frame.isHidden())
    def test_running_layout_does_not_change_deadline_and_reset_restores_controls(self):
        from main import TimerPage
        w=TimerPage();self.widgets.append(w);w.show();w.set_read_len(20);w.set_do_len(60);w.start_session()
        deadline=w._phase_deadline;w._sync_run_layout(False)
        self.assertEqual(w._phase_deadline,deadline);self.assertTrue(w.duration_editor.isHidden())
        self.assertTrue(w.arrangement_dock.isHidden())
        self.assertTrue(w.running_actions.isVisible())
        w.skip_stage();self.assertEqual(w.phase,w.DOING);self.assertTrue(w.b_skip.isHidden())
        w.reset();self.assertFalse(w.duration_editor.isHidden());self.assertFalse(w.preference_options.isHidden())
        self.assertFalse(w.arrangement_dock.isHidden())
    def test_timer_dock_clears_and_returns_without_delay_on_reset(self):
        from main import TimerPage
        cfg._cache['animations_on']=True
        w=TimerPage();self.widgets.append(w);w.resize(1200,850);w.show();self.app.processEvents()
        ready_width=w.ring.width();w.set_read_len(20);w.set_do_len(60);w.start_session()
        self.app.processEvents()
        self.assertTrue(w.arrangement_dock.isHidden())
        self.assertGreater(w.ring.width(),ready_width)
        w.reset();self.app.processEvents()
        self.assertFalse(w.arrangement_dock.isHidden())
        self.assertLess(w.ring.width(),w.left_panel.width())
    def test_stage_track_uses_real_durations(self):
        from main import TimerPage
        w=TimerPage();self.widgets.append(w);w.show();w.set_read_len(20);w.set_do_len(60);w.start_session()
        w.ring._shown=10
        read,answer,progress=w.countdown_runway.segments()
        self.assertEqual((read,answer),(20,60));self.assertAlmostEqual(progress,.125)
        w.reset()

    def test_timer_exit_snapshot_fades_and_reset_cancels_it(self):
        from main import TimerPage
        cfg._cache['animations_on'] = True
        w = TimerPage(); self.widgets.append(w)
        w.resize(1200, 850); w.show(); self.app.processEvents()
        w.set_read_len(0); w.set_do_len(60); w.start_session()
        self.app.processEvents()
        overlay = w._arrangement_exit
        self.assertTrue(overlay.isVisible())
        self.assertTrue(w.arrangement_dock.isHidden())
        QTest.qWait(100)
        self.assertGreater(overlay.progress, 0)
        self.assertLess(overlay.progress, 1)
        w.reset(); self.app.processEvents()
        returning = w._arrangement_exit
        self.assertIsNotNone(returning)
        self.assertIs(returning.returning_dock, w.arrangement_dock)
        QTest.qWait(100)
        self.assertGreater(returning.progress, 0)
        self.assertLess(returning.progress, 1)
        self.assertTrue(w.b_custom.isVisible())
        self.assertIsNone(w.arrangement_dock.graphicsEffect())
        # 回归过程中再次开始，旧快照和绘制遮罩必须清理。
        w.start_session(); QTest.qWait(330)
        self.assertIsNone(w._arrangement_exit)
        w.reset()
        QTest.qWait(330)
        self.assertIsNone(w._arrangement_exit)
        self.assertTrue(w.arrangement_dock.mask().isEmpty())

    def test_settings_fast_switch_has_no_old_content_overlay(self):
        from settings_page import SettingsPage
        cfg._cache['animations_on'] = True
        w = SettingsPage(); self.widgets.append(w)
        w.resize(1200, 850); w.show(); self.app.processEvents()
        for key in ('timer', 'bank', 'timer', 'bank', 'appearance'):
            w._select_settings_group(key)
            self.app.processEvents()
            section = w._settings_section
            self.assertEqual(section, key)
            titles = w._settings_groups[section]
            known = set().union(*w._settings_groups.values())
            for card in w._cards:
                expected = card._title_lab.text() in titles or (section == 'maintenance' and card._title_lab.text() not in known)
                self.assertEqual(not card.isHidden(), expected)
            self.assertIsNone(getattr(w.scroll.viewport(), '_workbench_reveal', None))

    def test_timer_snapshots_keep_background_transparent_in_both_modes(self):
        from main import TimerPage
        from workbench_ui import transparent_snapshot
        for mode in ('light', 'dark'):
            T.apply(mode)
            w = TimerPage(); self.widgets.append(w)
            w.resize(1200, 850); w.show(); self.app.processEvents()
            image = transparent_snapshot(w.arrangement_dock).toImage()
            self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
            self.assertEqual(image.pixelColor(image.width() // 2, image.height() - 1).alpha(), 0)
            self.assertGreater(image.pixelColor(image.width() // 2, 60).alpha(), 0)

    def test_timer_information_motion_covers_all_groups_without_changing_deadline(self):
        from main import TimerPage
        cfg._cache['animations_on'] = True
        w = TimerPage(); self.widgets.append(w)
        w.resize(1200, 850); w.show(); self.app.processEvents()
        w.set_read_len(0); w.set_do_len(60); w.start_session(); self.app.processEvents()
        deadline = w._phase_deadline
        motion = w._timer_visual_motion
        for widget in (w.timer_steps_host, w.ring, w.countdown_runway, w.hint, w.b_start, w.b_pause, w.b_reset):
            self.assertIn(widget, motion.widgets)
        QTest.qWait(100)
        self.assertEqual(w._phase_deadline, deadline)
        self.assertGreater(motion.progress, 0)
        w.reset(); self.app.processEvents(); QTest.qWait(370)
        self.assertIsNone(w._timer_visual_motion)
        for widget in w._timer_motion_widgets():
            self.assertTrue(widget.mask().isEmpty())
        w.play_intro(); self.app.processEvents()
        self.assertIsNotNone(w._timer_visual_motion)
        w.hide(); self.app.processEvents()
        self.assertIsNone(w._timer_visual_motion)
    def test_settings_navigation_reflows_and_preview_has_real_controls(self):
        from settings_page import SettingsPage
        from ui_kit import IconButton,ToggleSwitch
        w=SettingsPage();self.widgets.append(w);w.resize(740,700);w.show();self.app.processEvents()
        self.assertFalse(w.settings_navigation.vertical)
        self.assertIsInstance(w.preference_preview.action,IconButton);self.assertIsInstance(w.preference_preview.switch,ToggleSwitch)
        self.assertEqual(w.scroll.horizontalScrollBar().maximum(),0)
        w._select_settings_group('api');self.assertTrue(w.preference_preview.isHidden())
        w.resize(1200,850);self.app.processEvents();self.assertTrue(w.settings_navigation.vertical)
    def test_custom_duration_themed_fields_align_and_apply_seconds(self):
        from main import TimerPage
        w=TimerPage();self.widgets.append(w);w.resize(1200,850);w.show()
        w.toggle_custom_duration();self.app.processEvents()
        for appearance in ('light','dark'):
            T.apply(appearance)
            from motion_features import refresh_feature_controls
            refresh_feature_controls(w)
            self.assertIn('border-radius:10px',w.custom_read.styleSheet())
            self.assertEqual(w.custom_read.height(),44)
            self.assertEqual(w.custom_do.height(),44)
            self.assertEqual(w.custom_read.y(),w.custom_do.y())
        w.custom_read.setValue(25);w.custom_do.setValue(75);w.apply_custom_duration()
        self.assertEqual((w.read_total,w.do_total),(25,75))
    def test_same_selection_callback_does_not_cancel_clicked_animation(self):
        from workbench_ui import SegmentRail
        from PySide6.QtCore import QAbstractAnimation
        cfg._cache['animations_on']=True
        w=SegmentRail((('a','航向',None),('b','风向',None)))
        self.widgets.append(w);w.resize(600,50);w.show();self.app.processEvents();w.set_current('a',animate=False)
        w.changed.connect(lambda key:w.set_current(key,animate=False))
        QTest.mouseClick(w.buttons['b'],Qt.LeftButton);QTest.qWait(60)
        self.assertEqual(w.animation.state(),QAbstractAnimation.Running)
        self.assertNotEqual(w.plate.geometry(),w.buttons['b'].geometry())
        QTest.qWait(550);self.assertEqual(w.plate.geometry(),w.buttons['b'].geometry())
    def test_course_explanation_stays_compact_and_top_aligned(self):
        w=self.studio(1)
        self.assertLess(w.panel.height(),w.panel_scroll.height())
        self.assertLess(w.panel.y(),10)
    def test_selected_plate_reverses_from_current_geometry_and_hidden_animation_stops(self):
        from workbench_ui import SegmentRail
        from PySide6.QtCore import QAbstractAnimation
        cfg._cache['animations_on']=True
        w=SegmentRail((('a','航向',None),('b','风向',None),('c','夹角',None)))
        self.widgets.append(w);w.resize(600,50);w.show();self.app.processEvents();w.set_current('a',animate=False)
        w.set_current('c');QTest.qWait(100);before=w.plate.geometry();w.set_current('b')
        self.assertEqual(w.plate.geometry(),before);w.hide();self.assertEqual(w.animation.state(),QAbstractAnimation.Stopped)

if __name__=='__main__':unittest.main()
