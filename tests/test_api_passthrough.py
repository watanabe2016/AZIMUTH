"""验证模型原文与原图不会被本地推导、正确性筛选或备用排版替换。"""
import copy
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import vision_assist as VA
from api_artwork import svg_for_display
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineCore import QWebEngineSettings

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400">'
       '<rect width="600" height="400" fill="white"/>'
       '<path d="M100 300 L100 80 L480 170 Z" fill="#e0f0f5" stroke="#136780"/>'
       '<text x="110" y="310">1→2 302°</text></svg>')


class ApiPassthroughTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        import ref_panel
        cls.RP = ref_panel

    def test_original_points_and_values_are_kept_even_if_mutually_inconsistent(self):
        original = {"complete": False, "solution": {
            "markdown": "  ## 模型原文\n1→2 = 302°\n\n",
            "flight_legs": [{"from": "1", "to": "2", "bearing_deg": 721,
                             "distance_m": -12}],
            "coordinates": [{"id": "1", "latitude": "25°12′45″"}]},
            "route_map": {"points": {"1": {"x": 0.5, "y": 0},
                                      "2": {"x": 120, "y": 100}},
                          "flight_edges": [["1", "2"]], "north_up": False,
                          "y_axis": "down"}}
        before = copy.deepcopy(original)
        with patch.object(VA, "_complete_route_map", side_effect=AssertionError("不得重算")):
            answer = VA._normalize_direct_answer(original, "test", None)
            reference = VA.direct_answer_reference(answer)
        self.assertEqual(original, before)
        self.assertEqual(answer["raw_response_json"], before)
        self.assertEqual(answer["answer_markdown"], original["solution"]["markdown"])
        self.assertEqual(answer["flight_legs"], original["solution"]["flight_legs"])
        self.assertEqual(answer["route_map"]["points"], original["route_map"]["points"])
        widget = self.RP.RouteMap()
        try:
            reference["_show_model_original"] = True
            widget.set_solution(reference)
            self.assertEqual(widget._raw, [(0.5, 0.0), (120.0, 100.0)])
            self.assertEqual(widget._flight_measurements[("1", "2")]["bearing_deg"], 721)
            self.assertFalse(widget._candidate)
            with patch.object(self.RP.math, "atan2") as recompute_bearing:
                widget.resize(600, 300)
                widget.grab()
                recompute_bearing.assert_not_called()
        finally:
            widget.deleteLater()

    def test_model_svg_wins_over_conflicting_structured_points_and_is_cleared(self):
        answer = VA._normalize_direct_answer({"solution": {"markdown": "原文"},
                 "route_map": {"svg": SVG, "points": {"1": {"x": 0, "y": 0},
                                                        "2": {"x": 0, "y": 5}}}}, "test", None)
        self.assertEqual(answer["route_svg"], SVG)
        widget = self.RP.RouteMap()
        try:
            widget.set_solution(VA.direct_answer_reference(answer))
            self.assertTrue(widget._svg_renderer.isValid())
            widget.toggle_artwork_mode()
            self.assertEqual(widget._svg_renderer.viewBoxF().width(), 600)
            self.assertIsNone(widget._pts)
            widget.resize(800, 300)
            self.assertFalse(widget.grab().isNull())
            widget.set_notice("新题尚无图形")
            self.assertIsNone(widget._svg_renderer)
        finally:
            widget.deleteLater()

    def test_full_markdown_is_shown_in_fallback_not_summary_reconstruction(self):
        panel = self.RP.RefAnswerPanel()
        panel._renderer_loading = True
        try:
            panel._show_api_markdown({"answer_markdown": "## 原文第九节\n模型完整表格结束。"},
                                     fallback_html="<p>本地重组摘要</p>")
            self.assertIn("原文第九节", panel.view.toPlainText())
            self.assertNotIn("本地重组摘要", panel.view.toPlainText())
        finally:
            panel.deleteLater()

    def test_answer_renderer_can_load_bundled_local_libraries(self):
        panel = self.RP.RefAnswerPanel()
        try:
            self.assertTrue(panel.web_answer.settings().testAttribute(
                QWebEngineSettings.LocalContentCanAccessFileUrls))
        finally:
            panel.deleteLater()

    def test_failed_web_renderer_restores_model_markdown_in_text_view(self):
        panel = self.RP.RefAnswerPanel()
        panel._renderer_loading = True
        try:
            panel._show_api_markdown({
                "answer_markdown": "## 保留模型正文\n这是完整的原始答案。"})
            generation = panel._answer_render_generation
            panel.answer_stack.setCurrentWidget(panel.web_answer)

            panel._activate_api_renderer(generation, False)

            self.assertIs(panel.answer_stack.currentWidget(), panel.view)
            self.assertIn("保留模型正文", panel.view.toPlainText())
            self.assertIn("已切换为文本显示", panel.answer_info.text())
        finally:
            panel.deleteLater()

    def test_whitespace_only_answer_uses_visible_explanation_fallback(self):
        panel = self.RP.RefAnswerPanel()
        panel._renderer_loading = True
        try:
            panel._show_api_markdown(
                {"answer_markdown": "  \n  "},
                fallback_html="<p>模型正文暂缺，航图仅供参考。</p>")

            self.assertIs(panel.answer_stack.currentWidget(), panel.view)
            self.assertIn("模型正文暂缺", panel.view.toPlainText())
            self.assertIsNone(panel._pending_markdown)
        finally:
            panel.deleteLater()

    def test_plain_model_answer_is_kept_without_second_paid_request(self):
        text = "## DeepSeek 原文\n模型未输出结构化图形，但这份原文仍须完整显示。\n"
        body = {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}
        with patch.object(VA, "_api_settings", return_value=(True, VA.DEFAULT_API_BASE_URL,
                                                          "test-key", "deepseek-flash")), \
             patch.object(VA, "_api_provider", return_value="deepseek"), \
             patch.object(VA, "_image_data", return_value="data:image/png;base64,AA=="), \
             patch.object(VA, "_chat_request", return_value=body) as request, \
             patch.object(VA.cfg, "set_"), patch.object(VA.cfg, "save"), \
             patch.object(VA, "_sanitize_diagram_model", side_effect=AssertionError("不得改图")), \
             patch.object(VA, "_diagram_is_complete", side_effect=AssertionError("不得拦截")):
            result = VA.solve_question("unused.png")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result["direct_answer"]["raw_response_text"], text)
        self.assertEqual(result["direct_answer"]["answer_markdown"], text)
        self.assertIsNone(VA.direct_answer_reference(result["direct_answer"]))

    def test_graphic_safety_does_not_change_internal_gradient_or_path(self):
        source = ('<svg xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="g"/>'
                  '</defs><path d="M1 2 L3 4" fill="url(\'#g\')"/>'
                  '<script>alert(1)</script><image href="file:///secret.png"/></svg>')
        shown = svg_for_display(source).decode("utf-8")
        self.assertIn("M1 2 L3 4", shown)
        self.assertIn("url('#g')", shown)
        self.assertNotIn("script", shown)
        self.assertNotIn("file:", shown)


if __name__ == "__main__":
    unittest.main()
