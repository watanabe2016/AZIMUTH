from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from menu_interaction import MenuSelectionGate, should_dismiss_menu


class ImportMenuInteractionTests(unittest.TestCase):
    def setUp(self):
        self.menu_bounds = (100, 100, 299, 219)
        self.anchor_bounds = (300, 80, 379, 119)

    def test_click_inside_menu_is_not_treated_as_outside(self):
        self.assertFalse(should_dismiss_menu((150, 150), self.menu_bounds, self.anchor_bounds))

    def test_click_on_anchor_does_not_dismiss_menu(self):
        self.assertFalse(should_dismiss_menu((320, 100), self.menu_bounds, self.anchor_bounds))

    def test_click_outside_menu_and_anchor_dismisses(self):
        self.assertTrue(should_dismiss_menu((450, 300), self.menu_bounds, self.anchor_bounds))

    def test_menu_child_event_stays_inside_even_outside_painted_bounds(self):
        self.assertFalse(should_dismiss_menu(
            (450, 300), self.menu_bounds, self.anchor_bounds, event_inside_menu=True))

    def test_first_selection_is_claimed_once_per_open_state(self):
        gate = MenuSelectionGate()
        self.assertTrue(gate.claim())
        self.assertFalse(gate.claim())

        gate.reset()
        self.assertTrue(gate.claim())


if __name__ == "__main__":
    unittest.main()
