import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import main_window
import theme


class _SettingsStub:
    def __init__(self):
        self.selected_theme = None

    def set_app_theme_state(self, theme_key):
        self.selected_theme = theme_key


class _WindowStub:
    def __init__(self):
        self.p_set = _SettingsStub()
        self.applied_theme = None
        self.wrap_lay = self
        self.stage = self

    def isMaximized(self):
        return False

    def setContentsMargins(self, *_margins):
        pass

    def set_corner(self, _radius):
        pass

    def _prepare_theme_reveal(self, source):
        return None

    def _apply_theme(self, mode, pref=None):
        self.applied_theme = (mode, pref)


class AnnaThemeSwitchTests(unittest.TestCase):
    def test_main_window_accepts_and_applies_anna_theme(self):
        original_theme = theme.app_theme
        try:
            theme.app_theme = "default"
            window = _WindowStub()
            with patch.object(main_window.cfg, "set_"), \
                    patch.object(main_window.cfg, "get", return_value="system"):
                main_window.MainWindow.set_app_theme(window, "anna")

            self.assertEqual(theme.app_theme, "anna")
            self.assertEqual(window.p_set.selected_theme, "anna")
            self.assertIsNotNone(window.applied_theme)
        finally:
            theme.app_theme = original_theme


if __name__ == "__main__":
    unittest.main()
