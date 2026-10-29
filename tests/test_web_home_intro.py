import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from web_home_page import WebHomePage


class DeferredPage:
    def __init__(self):
        self.calls = []

    def runJavaScript(self, script, callback=None):
        self.calls.append((script, callback))


class WebHomeIntroTests(unittest.TestCase):
    def test_loaded_page_is_revealed_after_javascript_preparation(self):
        events = []

        class View:
            def isVisible(self):
                return "show" in events

            def show(self):
                events.append("show")

            def page(self):
                return web_page

        class Page:
            def runJavaScript(self, script, callback=None):
                events.append(script)
                if callback is not None:
                    callback(None)

        web_page = Page()
        page = SimpleNamespace(
            _loaded=False, _intro_pending=True, view=View(),
            isVisible=lambda: True, theme_changed=lambda: events.append("theme"),
            _sync_motion_preference=lambda: events.append("motion"))
        with patch("web_home_page.animations_enabled", return_value=True), \
                patch("web_home_page.QTimer.singleShot",
                      side_effect=lambda _delay, callback: callback()):
            WebHomePage._on_loaded(page, True)

        self.assertLess(events.index("theme"), events.index("show"))
        self.assertLess(events.index("motion"), events.index("show"))
        self.assertLess(events.index(
            "window.prepareHomeEntrance && window.prepareHomeEntrance();"),
            events.index("show"))
        self.assertEqual(events.count("show"), 1)
        self.assertIn(
            "window.playHomeEntrance && window.playHomeEntrance();", events)

    def test_page_waits_for_webview_preparation_before_switch_callback(self):
        web_page = DeferredPage()
        ready = []
        page = SimpleNamespace(_loaded=True, view=SimpleNamespace(page=lambda: web_page))

        with patch("web_home_page.animations_enabled", return_value=True):
            pending = WebHomePage.prepare_intro(page, lambda: ready.append(True))

        self.assertTrue(pending)
        self.assertEqual(
            web_page.calls[0][0],
            "window.prepareHomeEntrance && window.prepareHomeEntrance();")
        self.assertEqual(ready, [])
        web_page.calls[0][1](None)
        self.assertEqual(ready, [True])

    def test_initial_document_hides_stage_until_entrance_is_scheduled(self):
        html = (ROOT / "app" / "web" / "index.html").read_text(encoding="utf-8")
        motion = (ROOT / "app" / "web" / "motion250.js").read_text(encoding="utf-8")

        self.assertIn('class="home-pending"', html)
        self.assertIn("html.home-pending #stage { visibility: hidden; }", html)
        self.assertIn("window.prepareHomeEntrance", motion)
        self.assertIn("document.documentElement.classList.remove('home-pending')", motion)
        page_source = (ROOT / "app" / "web_home_page.py").read_text(encoding="utf-8")
        self.assertLess(page_source.index("self.view.hide()"),
                        page_source.index("self.view.load("))


if __name__ == "__main__":
    unittest.main()
