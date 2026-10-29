"""验证按钮交互语义、动画打断与主题颜色兼容。"""
import os,sys,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLineEdit
from PySide6.QtCore import Qt,QAbstractAnimation
from PySide6.QtTest import QTest
from ui_kit import IconButton
from interactive_button import color
from motion_polish import fold
APP=QApplication.instance() or QApplication([])


class InteractiveHoverTests(unittest.TestCase):
    def setUp(self):
        self.host=QWidget();self.layout=QVBoxLayout(self.host)
        self.button=IconButton('开始练习','pen');self.button.setFixedSize(160,44)
        self.layout.addWidget(self.button);self.host.show();APP.processEvents()
        self.enabled=patch('ui_kit.animations_enabled',return_value=True);self.enabled.start()

    def tearDown(self):
        self.host.close();APP.processEvents();self.enabled.stop()

    def test_pointer_and_keyboard_still_trigger_one_real_click(self):
        calls=[];self.button.clicked.connect(lambda:calls.append(1))
        QTest.mouseClick(self.button,Qt.LeftButton)
        self.button.setFocus();QTest.keyClick(self.button,Qt.Key_Space)
        self.assertEqual(len(calls),2)
        self.button.setEnabled(False);QTest.mouseClick(self.button,Qt.LeftButton)
        self.assertEqual(len(calls),2)

    def test_reversing_hover_does_not_jump_or_change_geometry(self):
        motion=self.button._interactive_hover;rect=self.button.geometry()
        motion.animate(1);motion.motion.setCurrentTime(140);current=motion.progress
        motion.animate(0);self.assertAlmostEqual(motion.progress,current)
        self.assertAlmostEqual(motion.motion.startValue(),current)
        for i in range(8):
            motion.motion.setCurrentTime(i*30);self.button.grab()
            self.assertEqual(self.button.geometry(),rect)
        self.button.hide();self.assertEqual(motion.motion.state(),QAbstractAnimation.Stopped)
        self.assertEqual(motion.progress,0)

    def test_reduced_motion_keeps_button_interactive_and_stops_clock(self):
        motion=self.button._interactive_hover;motion.animate(1)
        with patch('ui_kit.animations_enabled',return_value=False):
            motion.animate(0)
            self.assertEqual(motion.motion.state(),QAbstractAnimation.Stopped)
            self.assertEqual(motion.progress,0)
            self.assertFalse(self.button.grab().isNull())

    def test_css_color_forms_produce_valid_expected_alpha(self):
        for raw,alpha in (('rgba(15,22,34,0.5)',128),('rgba(15,22,34,105)',105),('#142840',255)):
            result=color(raw);self.assertTrue(result.isValid());self.assertEqual(result.alpha(),alpha)

    def test_fold_reversal_restores_input_and_releases_effect(self):
        panel=QWidget();form=QVBoxLayout(panel);edit=QLineEdit('保留输入');form.addWidget(edit)
        self.layout.addWidget(panel);APP.processEvents();panel.hide()
        with patch('motion_polish.animations_enabled',return_value=True):
            fold(panel,True);controller=panel._polish_fold
            controller.motion.setCurrentTime(160);height=panel.maximumHeight();progress=controller.motion.currentValue()
            fold(panel,False);self.assertAlmostEqual(controller.motion.startValue(),progress)
            self.assertLessEqual(abs(progress-height),.5)
            controller.motion.setCurrentTime(controller.motion.duration())
            fold(panel,True);controller.motion.setCurrentTime(controller.motion.duration())
        self.assertEqual(edit.text(),'保留输入');self.assertIsNone(panel.graphicsEffect())
        self.assertTrue(panel.isVisible());self.assertEqual(panel.maximumHeight(),16777215)
