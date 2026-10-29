"""验证借位滚动、真实计时值、主题绘制和隐藏清理。"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QAbstractAnimation
from PySide6.QtGui import QFontInfo
APP = QApplication.instance() or QApplication([])
import theme as T
from main import RingTimer


class CountdownRollerTests(unittest.TestCase):
    def setUp(self):
        self.theme = T.app_theme, T.mode
        self.timer = RingTimer(60)
        self.timer.resize(700, 500)
        self.timer.show()
        APP.processEvents()
        self.enabled = patch('main.animations_enabled', return_value=True)
        self.enabled.start()

    def tearDown(self):
        self.timer.close()
        self.enabled.stop()
        T.set_app_theme(self.theme[0])
        T.apply(self.theme[1])

    def test_minute_borrow_moves_full_columns_and_lands_on_real_value(self):
        timer = self.timer
        font = timer._roller_geometry()[0]
        self.assertIn('Alibaba PuHuiTi', QFontInfo(font).family())
        self.assertTrue(QFontInfo(font).bold())
        timer.set_remain(59)
        timer._digit_motion.setCurrentTime(340)
        self.assertEqual(timer.remain, 59)
        self.assertIn('59', timer.accessibleDescription())
        rows = timer._roller_rows('0', '9', 100)
        self.assertLess(rows[0][1], -40)
        self.assertGreater(rows[1][1], 40)
        self.assertEqual(timer._roller_rows('0', '0', 100), [('0', 0., 1.)])
        middle = timer.grab().toImage()
        timer._digit_motion.setCurrentTime(680)
        self.assertEqual(timer._roller_rows('0', '9', 100), [('9', 0., 1.)])
        self.assertNotEqual(middle, timer.grab().toImage())

    def test_reset_hide_and_reduced_motion_cancel_stale_digits(self):
        timer = self.timer
        timer.set_remain(59)
        timer._digit_motion.setCurrentTime(200)
        timer.set_total(180, animate=False)
        self.assertEqual(timer.remain, 180)
        self.assertEqual(timer._digit_before, 180)
        self.assertEqual(timer._digit_t, 1.)
        self.assertEqual(timer._digit_motion.state(), QAbstractAnimation.Stopped)
        with patch('main.animations_enabled', return_value=False):
            timer.set_remain(0)
        self.assertEqual(timer._digit_t, 1.)
        timer.set_total(60, animate=False)
        timer.set_remain(59)
        timer.hide()
        self.assertEqual(timer._digit_motion.state(), QAbstractAnimation.Stopped)
        self.assertEqual(timer._anim.state(), QAbstractAnimation.Stopped)

    def test_three_themes_two_appearances_fit_large_and_small_windows(self):
        folder = ROOT / '.build-cache' / 'roller-preview'
        folder.mkdir(parents=True, exist_ok=True)
        T.load_app_fonts()
        for theme in ('default', 'chiyo', 'anna'):
            T.set_app_theme(theme)
            for appearance in ('light', 'dark'):
                T.apply(appearance)
                for width, height in ((700, 500), (300, 280)):
                    self.timer.resize(width, height)
                    self.timer.set_total(60, animate=False)
                    self.timer.set_remain(59)
                    self.timer._digit_motion.setCurrentTime(340)
                    font, cell, colon, box = self.timer._roller_geometry()
                    self.assertGreaterEqual(box.left(), 0)
                    self.assertLessEqual(box.right(), width)
                    self.assertLess(box.bottom() + font.pixelSize() * .57, height)
                    picture = self.timer.grab()
                    self.assertFalse(picture.isNull())
                    picture.save(str(folder / f'{theme}-{appearance}-{width}.png'))


if __name__ == '__main__':
    unittest.main()
