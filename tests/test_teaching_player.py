"""统一时间轴的暂停、跳步、修订及减少动效语义。"""
import os,sys,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
APP=QApplication.instance() or QApplication([])
from teaching_player import TeachingPlayer
from teaching_courses import build_course


class TeachingPlayerTests(unittest.TestCase):
    def setUp(self):self.player=TeachingPlayer();self.player.load(build_course('bearing'))
    def tearDown(self):self.player.pause()
    def test_seek_and_snapshot_restore(self):
        self.player.seek(1,.4);self.player.set_speed(.5)
        saved=self.player.snapshot();revision=self.player.session.revision
        self.player.load(build_course('bearing',{'angle':20}),saved)
        self.assertEqual(self.player.session.step,1);self.assertEqual(self.player.session.progress,.4)
        self.assertGreater(self.player.session.revision,revision)
        self.assertIn('250',self.player.current.result)
        self.player.previous();self.assertEqual(self.player.session.step,0)
    def test_pause_and_hidden_stop_do_not_finish(self):
        with patch('teaching_player.animations_enabled',return_value=True):
            self.player.play();QTest.qWait(40);self.player.pause()
            state=self.player.snapshot();QTest.qWait(50);self.assertEqual(state,self.player.snapshot())
            self.player.play();self.player.set_visible(False)
            self.assertFalse(self.player.timer.isActive());self.assertFalse(self.player.session.playing)
            self.assertLess(self.player.session.progress,1)
    def test_linear_phase_and_reading_hold(self):
        self.player.advance(700);self.assertAlmostEqual(self.player.session.progress,.5)
        self.player.advance(700);self.assertEqual(self.player.session.step,0)
        self.player.advance(2200);self.assertEqual(self.player.session.step,1)
        self.assertEqual(self.player.session.progress,0)
    def test_reduced_motion_does_not_skip_lessons(self):
        with patch('teaching_player.animations_enabled',return_value=False):
            self.player.play();self.assertEqual(self.player.session.progress,1)
            self.assertEqual(self.player.session.step,0);self.assertFalse(self.player.timer.isActive())
            self.player.next();self.assertEqual(self.player.session.step,1)
    def test_speed_change_keeps_current_frame(self):
        self.player.seek(2,.5);self.player.set_speed(2)
        self.assertEqual(self.player.session.progress,.5)
        self.player.advance(100);self.assertAlmostEqual(self.player.session.progress,.5+200/1400)


if __name__=='__main__':unittest.main()
