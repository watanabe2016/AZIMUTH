"""四考点工具、50场景的数学画面一致性与真实 Qt 交互。"""
import os,sys,math,copy,tempfile,unittest
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from teaching_tool_math import wind_from,parse_angle,solve_angle
from teaching_scene_catalog import SCENES,CHAPTERS,build_scene,defaults
from teaching_math import azimuthBetweenPoints,quadrantBearingToAzimuth,reverseBearing,convertSpeed


class StudioMathTests(unittest.TestCase):
    def test_required_heading_and_wind_cases(self):
        for base,toward,value,result in [('北','东',30,30),('东','北',30,60),('西','南',15,255)]:
            self.assertEqual(quadrantBearingToAzimuth(base,toward,value),result)
        self.assertEqual(reverseBearing(255),75)
        for mode,value in [('顺风',270),('逆风',90),('正左侧风',0),('正右侧风',180)]:self.assertEqual(wind_from(90,mode),value)
        self.assertAlmostEqual(convertSpeed(7.2),2);self.assertAlmostEqual(convertSpeed(10.8),3)
        for heading in (-730,-1,0,359,1080):
            for mode in ('顺风','逆风','正左侧风','正右侧风'):self.assertTrue(0<=wind_from(heading,mode)<360)

    def test_angle_vertex_known_reverse_and_two_solutions(self):
        self.assertEqual(parse_angle('∠H12')['vertex'],'1')
        for name,vertex in [('12H','2'),('21H','1'),('32H','2')]:self.assertEqual(parse_angle(name)['vertex'],vertex)
        parsed,solutions=solve_angle('H12','1→2',90,45)
        self.assertEqual([(s['target'],s['reverse']) for s in solutions],[(135,315),(45,225)])
        self.assertEqual(solve_angle('H12','2→1',270,45,'顺时针')[1][0]['target'],135)
        self.assertEqual(solve_angle('H12','1→2',90,45,'逆时针',True)[1][0]['result'],225)
        for name in ('H11','12','H123','∠'):
            with self.assertRaises(ValueError):parse_angle(name)
        with self.assertRaises(ValueError):solve_angle('H12','2→H',90,45)

    def test_fifty_real_scenes_and_ray_geometry(self):
        self.assertEqual(len(SCENES),50);self.assertEqual(len({s.kind for s in SCENES}),50)
        self.assertEqual(len(CHAPTERS),6)
        for number in range(1,51):
            with self.subTest(number=number):
                lesson=build_scene(number);self.assertTrue(lesson.steps)
                self.assertTrue(all(s.objects for s in lesson.steps))
                self.assertEqual(len({s.id for s in lesson.steps}),len(lesson.steps))
                for step in lesson.steps:
                    for obj in step.objects:
                        for point in obj.points:self.assertTrue(math.isfinite(point.x) and math.isfinite(point.y))
                        if obj.kind=='arc':
                            self.assertGreater(abs(obj.value),0);self.assertLessEqual(abs(obj.value),180)
        for side,expected in [('顺时针',135),('逆时针',45)]:
            lesson=build_scene(49,{'turn_side':side});stage=lesson.steps[7]
            ray=next(o for o in stage.objects if o.id=='branch0')
            self.assertAlmostEqual(azimuthBetweenPoints(*ray.points),expected)
            angle=next(o for o in stage.objects if o.id=='arc0')
            self.assertEqual(azimuthBetweenPoints(*angle.points),90)
            self.assertEqual(angle.value,45 if side=='顺时针' else -45)
        two=build_scene(49,{'turn_side':'未指定（两解）'});self.assertTrue(two.warnings)
        self.assertEqual(len(build_scene(49).steps),10)
        reversed_known=build_scene(49,{'known_index':3,'heading':270}).steps[7]
        known=next(o for o in reversed_known.objects if o.id=='known')
        self.assertEqual(known.label,'1→2')
        self.assertAlmostEqual(azimuthBetweenPoints(*known.points),90)


class StudioInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        import config as cfg,theme as T
        self.cfg=cfg;self.theme=T;self.old=(cfg._path,cfg._cache,T.app_theme,T.mode)
        self.temp=tempfile.TemporaryDirectory();cfg._path=str(Path(self.temp.name)/'config.json');cfg._cache=copy.deepcopy(cfg.DEFAULTS)
        from teaching_studio import TeachingStudio
        self.page=TeachingStudio();self.page.resize(1250,880);self.page.show();self.app.processEvents();self.page.player.pause()
    def tearDown(self):
        self.page.close();self.app.processEvents();self.cfg._path,self.cfg._cache,theme,mode=self.old
        self.theme.set_app_theme(theme);self.theme.apply(mode);self.temp.cleanup()

    def test_eight_wind_choices_and_embedded_player_menu(self):
        from teaching_tool_math import WIND_OFFSETS,wind_from
        from ui_kit import Combo
        self.page.select_scene(48);self.app.processEvents()
        self.assertEqual(tuple(self.page.wind_buttons),tuple(WIND_OFFSETS))
        self.page.wind_choice_toggle.click();self.assertTrue(self.page.wind_choice_panel.isVisible())
        for name,offset in WIND_OFFSETS.items():
            with self.subTest(name=name):
                self.page.wind_buttons[name].click()
                self.assertEqual(self.page.parameters['wind_mode'],name)
                self.assertEqual(wind_from(90,name),(90+offset)%360)
                self.assertFalse(self.page.wind_choice_panel.isVisible())
                self.page.wind_choice_toggle.click()
        self.assertIsInstance(self.page.speed,Combo)
        self.page.speed.open_popup(animate=False);self.app.processEvents()
        popup=self.page.speed._popup
        self.assertIsNotNone(popup)
        self.assertTrue(self.page.rect().contains(popup.geometry()))
        self.page.speed.close_popup()

    def test_all_scenes_render_motion_and_pause(self):
        from PySide6.QtTest import QTest
        for number in range(1,51):
            with self.subTest(number=number):
                self.page.select_scene(number);self.page.player.pause()
                moving=False
                for index,step in enumerate(self.page.player.lesson.steps):
                    self.page.player.seek(index,.2);self.app.processEvents();first=self.page.canvas.grab().toImage()
                    self.page.player.seek(index,.8);self.app.processEvents();last=self.page.canvas.grab().toImage()
                    if first!=last:moving=True
                self.assertTrue(moving,'场景必须有真实变化的教学帧')
                self.page.player.replay();self.page.player.pause();position=self.page.player.snapshot();QTest.qWait(25)
                self.assertEqual(position,self.page.player.snapshot());self.assertFalse(self.page.player.timer.isActive())

    def test_concept_diagram_nodes_do_not_overlap_and_flow_links_are_sequential(self):
        for number in (1,5):
            with self.subTest(number=number):
                self.page.select_scene(number);self.page.player.pause()
                lesson=self.page.player.lesson;self.page.player.seek(len(lesson.steps)-1,1)
                self.app.processEvents();self.page.canvas.grab()
                nodes={o.id:o for o in self.page.player.current.objects if o.kind=='node'}
                self.assertEqual(len(nodes),3 if number==1 else 6)
                centers={key:self.page.canvas.project(node.points[0]) for key,node in nodes.items()}
                card_width,card_height=self.page.canvas.node_card_size()
                for left_index,left in enumerate(centers):
                    for right in list(centers)[left_index+1:]:
                        delta=centers[left]-centers[right]
                        self.assertTrue(abs(delta.x())>=card_width or abs(delta.y())>=card_height,
                            f'场景 {number} 的节点 {left} 与 {right} 发生重叠')
                if number==5:
                    links={o.id:o for o in self.page.player.current.objects if o.kind=='line' and o.id.startswith('flow')}
                    self.assertEqual(len(links),5)
                    for index in range(1,6):
                        link=links[f'flow{index}']
                        self.assertEqual(link.points,(nodes[str(index-1)].points[0],nodes[str(index)].points[0]))

    def test_parameters_update_visuals_and_preserve_invalid_snapshot(self):
        self.page.select_scene(49);self.page.parameters.update(heading=90,angle=45,turn_side='顺时针',known_index=1)
        self.page.recalculate(self.page.parameters,True)
        self.assertEqual(self.page.player.current.result,'135°')
        self.page.editors['turn_side'].setCurrentIndex(1);self.page.player.seek(7);self.assertEqual(self.page.player.current.result,'45°')
        self.page.player.seek(7,1);self.page.canvas.grab()
        self.assertEqual(next(o['sweep'] for o in self.page.canvas.last_objects if o['kind']=='arc'),-45)
        self.page.player.seek(7,.5);self.page.canvas.grab()
        ray=next(o for o in self.page.canvas.last_objects if o.get('id')=='branch0')
        self.assertAlmostEqual(azimuthBetweenPoints(*ray['points']),67.5)
        self.assertEqual(next(o['sweep'] for o in self.page.canvas.last_objects if o['kind']=='arc'),-22.5)
        old=self.page.player.lesson;self.page.recalculate(dict(self.page.parameters,angle=180));self.assertIs(old,self.page.player.lesson)
        self.assertTrue(self.page.error.text())
        self.page.select_scene(47);self.page.drag_bearing(300)
        self.assertEqual(self.page.player.current.result,'300°');self.page.canvas.grab();self.assertEqual(self.page.canvas.drawn_values['compass'],300)
        self.page.select_scene(48)
        for mode in ('顺风','逆风','正左侧风','正右侧风'):
            self.page.set_wind(mode);self.page.player.seek(1,1);self.page.canvas.grab()
            self.assertEqual(self.page.canvas.drawn_values['wind'],wind_from(90,mode))
        self.page.select_scene(27);self.page.player.seek(1,.5);self.page.canvas.grab()
        ray=next(o for o in self.page.canvas.last_objects if o.get('id')=='map')
        self.assertAlmostEqual(azimuthBetweenPoints(*ray['points']),self.page.canvas.drawn_values['compass'])

    def test_theme_sizes_hidden_clock_and_old_records(self):
        self.cfg.set_('lesson_completed_shapes',[1,4]);self.cfg.set_('teaching_v2',{'schema_version':2,'completed':['bearing']})
        for theme in ('default','chiyo','anna'):
            self.theme.set_app_theme(theme)
            for appearance in ('light','dark'):
                self.theme.apply(appearance);self.page.theme_changed()
                for width,height in ((780,620),(1060,760),(1400,900)):
                    self.page.resize(width,height);self.app.processEvents();self.assertLess(self.page.footer.geometry().bottom(),self.page.height())
                    self.assertFalse(self.page.grab().isNull())
        self.page.player.play();self.page.hide();self.app.processEvents();self.assertFalse(self.page.player.timer.isActive())
        self.assertEqual(self.cfg.get('lesson_completed_shapes'),[1,4]);self.assertEqual(self.cfg.get('teaching_v2')['completed'],['bearing'])

    def test_folded_course_to_tool_and_reduced_motion(self):
        from PySide6.QtTest import QTest
        from unittest.mock import patch
        self.page.select_scene(36);self.page.toggle_conditions();QTest.qWait(800)
        self.page.toggle_conditions();QTest.qWait(800)
        self.page.select_scene(49);self.app.processEvents()
        self.assertTrue(self.page.conditions.isVisible())
        self.assertGreater(self.page.conditions.height(),200)
        with patch('teaching_player.animations_enabled',return_value=False):
            self.page.player.replay();self.app.processEvents()
            self.assertEqual(self.page.player.session.progress,1)
            self.assertFalse(self.page.player.timer.isActive())
            self.page.next();self.assertEqual(self.page.player.session.step,1)

    def test_drag_cross_zero_interruption_and_speed_restore(self):
        from PySide6.QtCore import Qt,QPoint
        from PySide6.QtTest import QTest
        self.page.select_scene(47);self.page.player.pause()
        center=QPoint(self.page.canvas.width()//2,self.page.canvas.height()//2)
        QTest.mouseClick(self.page.canvas,Qt.LeftButton,pos=center+QPoint(100,0))
        self.assertEqual(self.page.parameters['heading'],90)
        self.page.drag_bearing(359);self.page.canvas.grab();self.page.set_heading(1)
        self.page.player.seek(1,.5);self.page.canvas.grab()
        self.assertAlmostEqual(self.page.canvas.drawn_values['compass'],0)
        self.page.set_heading(90);self.page.player.seek(1,.5);self.page.canvas.grab()
        previous=self.page.canvas.drawn_values['compass']
        self.page.set_heading(180);self.page.player.seek(1,0);self.page.canvas.grab()
        self.assertAlmostEqual(self.page.canvas.drawn_values['compass'],previous)
        self.page.player.set_speed(1.5);self.page.select_scene(48);self.page.select_scene(47)
        self.assertEqual(self.page.speed.currentData(),1.5)


if __name__=='__main__':unittest.main()
