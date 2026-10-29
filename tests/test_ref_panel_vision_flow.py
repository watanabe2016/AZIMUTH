import copy
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication


class ReferencePanelVisionFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        import ref_panel as RP
        import vision_assist as VA
        import bankdata as BD
        cls.RP = RP
        cls.VA = VA
        cls.BD = BD

    def test_direct_answer_html_includes_model_reasoning_and_steps(self):
        answer = {
            "provider": "测试服务商",
            "model": "vision-test",
            "summary": "先确定起点，再沿题图航线闭合。",
            "steps": ["第 1 步：从起点飞往 1 号点。", "第 2 步：依次连接各航点。"],
            "answer_markdown": "## 参考解答\n按题图顺序完成航线。",
        }
        panel = type("AnswerHtmlHarness", (), {
            "_billing_text": lambda _self, _answer: "",
            "_direct_answer_html": self.RP.RefAnswerPanel._direct_answer_html,
        })()
        fallback = panel._direct_answer_html(answer)

        self.assertIn("解题结论", fallback)
        self.assertIn("先确定起点", fallback)
        self.assertIn("第 1 步：从起点飞往 1 号点。", fallback)

    def test_api_answer_cache_is_bound_to_question_text_and_image(self):
        question = {
            "id": 987654420, "graph_hint": "六边形",
            "conditions_text": "边长300米", "requirements_text": "闭合航线",
            "suggested_text": "", "reference_source": {},
        }
        with tempfile.TemporaryDirectory() as folder:
            image_path = Path(folder) / "card.png"
            image_path.write_bytes(b"first card image")
            with patch.object(self.BD, "fig_path", return_value=str(image_path)):
                signature = self.RP._question_answer_signature(question)
                answer = {"question_id": question["id"],
                          "question_signature": signature}
                question["reference_source"]["direct_answer"] = answer
                self.assertEqual(
                    self.RP.RefAnswerPanel._direct_answer_for_question(question),
                    answer)

                image_path.write_bytes(b"replacement card image")
                self.assertIsNone(
                    self.RP.RefAnswerPanel._direct_answer_for_question(question))
                image_path.write_bytes(b"first card image")
                question["conditions_text"] = "边长400米"
                self.assertIsNone(
                    self.RP.RefAnswerPanel._direct_answer_for_question(question))

    def test_unverified_reference_candidate_is_drawn(self):
        route_map = self.RP.RouteMap(scale=1.0)
        route_map.set_solution({
            "status": "NEEDS_REVIEW",
            "candidate": True,
            "candidate_points": {
                "H": {"x": 0, "y": 0, "lat": 23, "lon": 113},
                "A": {"x": 40, "y": -20, "lat": 22.9998, "lon": 113.0004},
                "1": {"x": 100, "y": 0, "lat": 23, "lon": 113.001},
            },
            "candidate_geometry_edges": [["1", "1"]],
            "candidate_flight_edges": [["H", "A"]],
            "candidate_access_edges": [["H", "A"], ["A", "1"]],
            "candidate_note": "参考草案，仅作路线参考。",
        })

        self.assertTrue(route_map.isVisible())
        self.assertTrue(route_map._candidate)
        self.assertEqual(route_map._flight_edges, [("H", "A")])
        self.assertEqual(route_map._access_edges, [("H", "A"), ("A", "1")])

    def test_candidate_route_labels_use_api_measurements_not_layout_coordinates(self):
        route_map = self.RP.RouteMap(scale=1.0)
        route_map.set_solution({
            "status": "NEEDS_REVIEW",
            "candidate": True,
            "candidate_points": {
                "H": {"x": 0, "y": 0},
                "1": {"x": 1000, "y": 0},
            },
            "candidate_flight_edges": [["H", "1"]],
            "candidate_flight_legs": [{
                "from": "H", "to": "1", "distance_m": 78.05,
                "bearing_deg": 306.3,
            }],
        })

        measurement = route_map._flight_measurements[("H", "1")]
        self.assertEqual(route_map._flight_leg_text(measurement, approximate=True),
                         "≈78.05m · ≈306.3°")
        self.assertEqual(route_map._flight_leg_text(None, approximate=True), "")

    def test_billing_badge_formats_call_cost_and_provider_balance_compactly(self):
        self.assertEqual(self.RP.RefAnswerPanel._compact_money(
            0.043913, "USD", approximate=True), "≈$0.0439")
        text, detail = self.RP.RefAnswerPanel._billing_snapshot_text({
            "balances": [{"currency": "CNY", "amount": 12.66}],
        })
        self.assertEqual(text, "余额¥12.66")
        self.assertIn("12.66", detail)

    def test_reference_answer_window_resizes_from_corners_and_remembers_size(self):
        panel = self.RP.RefAnswerPanel(scale=1.0)
        deltas_and_expected = (
            (QPoint(-30, -20), (70, 80, 530, 420)),
            (QPoint(30, -20), (100, 80, 530, 420)),
            (QPoint(-30, 20), (70, 100, 530, 420)),
            (QPoint(30, 20), (100, 100, 530, 420)),
        )
        try:
            with patch.object(self.RP, "animations_enabled", return_value=False):
                panel.show()
                self.app.processEvents()
                self.assertEqual(len(panel._resize_handles), 4)
                self.assertTrue(all(handle.width() == 24 and
                                    handle.height() == 24
                                    for handle in panel._resize_handles))
                for handle, (delta, expected) in zip(
                        panel._resize_handles, deltas_and_expected):
                    start = QRect(100, 100, 500, 400)
                    panel.setGeometry(start)
                    handle._drag_geometry = QRect(start)
                    handle._resize_from_delta(delta)
                    geometry = panel.geometry()
                    self.assertEqual(
                        (geometry.x(), geometry.y(), geometry.width(),
                         geometry.height()), expected)

                saved_geometry = QRect(panel.geometry())
                self.assertTrue(panel._user_resized)
                panel.hide()
                self.app.processEvents()
                panel.show()
                self.app.processEvents()
                self.assertEqual(panel.geometry(), saved_geometry)
        finally:
            panel.hide()
            panel.deleteLater()
            self.app.processEvents()

    def test_opening_reference_answer_refreshes_stale_cached_rectangle(self):
        fields = {
            "graph_hint": "",
            "conditions_text": (
                "1、该任务航线为如图，航线高度为80m\n"
                "2、该航线12=100m，34=140m，14=80m\n"
                "3、起飞点条件：∠H12=60° ∠H23=30°\n"
                "4、水平速度：5m/s，垂直速度：3m/s\n"
                "5、航向为：4点在3点的南偏西35°方向。\n"
                "6、转弯模式：停止转弯，停留5秒。\n"
                "7、循环条件：闭合不循环，完成任务后在H点悬停\n"
                "8、起飞点经纬度：E:104.4615°，N:30.8697°\n"
                "9、起飞点高度为航线高度一半。"),
            "requirements_text": "", "suggested_text": "",
        }
        words = [
            {"text": "湖南01地面站真题（9.11）", "x": 295, "y": 100,
             "width": 392, "height": 56, "confidence": .99},
            {"text": "3", "x": 328, "y": 192, "width": 28,
             "height": 37, "confidence": .99},
            {"text": "2", "x": 329, "y": 449, "width": 34,
             "height": 37, "confidence": .99},
            {"text": "1、该任务航线为如图，航线高度为80m", "x": 199,
             "y": 548, "width": 542, "height": 43, "confidence": .99},
        ]
        stale_diagram = {
            "source": "vision_model", "shape": "四边形",
            "nodes": ["1", "2", "3", "4"],
            "geometry_edges": [["1", "2"], ["2", "3"],
                               ["3", "4"], ["4", "1"]],
            "flight_order": [], "flight_edges": [], "flight_closed": None,
            "point_positions": {
                "1": {"x": 500, "y": 300},
                "2": {"x": 200, "y": 300},
                "3": {"x": 200, "y": 200},
                "4": {"x": 500, "y": 200},
            },
            "warnings": [
                "视觉图形结构尚未经过人工核对，路线仅作参考",
                "题面航点顺序未完整识别；不会按编号顺序补造路线",
                "航线是否闭合未从题面确认",
            ],
            "topology_errors": ["飞行顺序未完整识别", "航线闭合条件未确认"],
            "topology_valid": False,
        }
        question = {
            "id": 987654402, "imported": True, **fields,
            "reference_source": {
                "image_size": [1080, 1283], "ocr_words": words,
                "condition_assist": {
                    "status": "review", "solver_usable": True, "complete": True,
                    "normalized_conditions": fields["conditions_text"],
                    "condition_items": [
                        {"normalized": line, "visual_evidence": line,
                         "confidence": 1.0}
                        for line in fields["conditions_text"].splitlines()],
                    "uncertain_items": stale_diagram["warnings"],
                    "diagram_model": copy.deepcopy(stale_diagram),
                    "source_fields": fields,
                },
            },
        }
        fixture = ROOT / "tests" / "fixtures" / "trapezoid_card.jpg"
        with patch.object(self.BD, "fig_path", return_value=str(fixture)):
            self.assertTrue(self.RP._refresh_cached_imported_geometry(question))

        diagram = question["reference_source"]["condition_assist"]["diagram_model"]
        self.assertEqual(diagram["geometry_source"], "local_cv_hough")
        self.assertEqual(diagram["shape"], "梯形")
        result = self.RP.RE.solve_reference(question)
        self.assertTrue(result["candidate"], result.get("candidate_validation_errors"))
        self.assertEqual(result["candidate_flight_edges"], [
            ["1", "2"], ["2", "3"], ["3", "4"], ["4", "1"]])

    def test_imported_local_recognition_is_used_and_persisted(self):
        qid = 987654403
        question = {
            "id": qid, "imported": True, "graph_hint": "梯形",
            "conditions_text": "梯形，起飞点条件略",
            "requirements_text": "", "suggested_text": "",
            "reference_source": {
                "image_size": [900, 1114],
                "ocr_words": [{"text": "1", "x": 70, "y": 520,
                               "width": 20, "height": 20, "confidence": .99}],
                "diagram_model": {
                    "source": "local_cv", "status": "review",
                    "shape": "", "nodes": ["1", "2", "3", "4"],
                    "geometry_edges": [["1", "2"], ["3", "4"]],
                    "route_order": [], "topology_valid": False,
                },
            },
        }
        nodes = ["1", "2", "3", "4"]
        diagram = {
            "source": "local_cv", "status": "ready", "shape": "梯形",
            "nodes": nodes,
            "geometry_edges": [["1", "2"], ["2", "3"],
                               ["3", "4"], ["4", "1"]],
            "route_order": nodes, "topology_valid": True,
            "validated_cycle": True, "confidence": .94,
            "point_positions": {
                "1": {"x": 300, "y": 600}, "2": {"x": 100, "y": 600},
                "3": {"x": 100, "y": 200}, "4": {"x": 400, "y": 200},
            },
        }
        fixture = ROOT / "tests" / "fixtures" / "trapezoid_card.jpg"
        persisted = [copy.deepcopy(question)]

        def save_items(items):
            persisted[:] = copy.deepcopy(items)
            return True

        with (
            patch.object(self.BD, "fig_path", return_value=str(fixture)),
            patch.object(self.BD, "load",
                         side_effect=lambda: copy.deepcopy(persisted)),
            patch.object(self.BD, "save", side_effect=save_items),
            patch.object(self.RP.DV, "analyze",
                         return_value=copy.deepcopy(diagram)) as analyze,
            patch.object(self.RP.DV, "refine_partial_polygon",
                         return_value=None),
        ):
            self.assertTrue(self.RP._refresh_cached_imported_geometry(question))
            self.assertFalse(self.RP._refresh_cached_imported_geometry(question))

        saved_diagram = persisted[0]["reference_source"]["diagram_model"]
        self.assertEqual(saved_diagram["recognizer_version"],
                         self.RP._IMPORTED_DIAGRAM_RECOGNIZER_VERSION)
        self.assertEqual(saved_diagram["shape"], "梯形")
        self.assertIsNotNone(self.RP.RE._validated_local_graph(
            self.RP.RE._local_cv_diagram(question)))
        analyze.assert_called_once()

    def test_imported_local_cv_card_without_vlm_keeps_review_drawing(self):
        nodes = ["1", "2", "3"]
        question = {
            "id": 987654405, "imported": True,
            "reference_source": {
                "diagram_model": {
                    "source": "local_cv", "status": "ready", "shape": "三角形",
                    "nodes": nodes,
                    "geometry_edges": [["1", "2"], ["2", "3"], ["3", "1"]],
                    "route_order": nodes, "topology_valid": True,
                    "point_positions": {
                        "1": {"x": 100, "y": 500},
                        "2": {"x": 500, "y": 500},
                        "3": {"x": 300, "y": 100},
                    },
                },
            },
        }
        spec = {
            "shape": "三角形", "nodes": nodes,
            "geometry_edges": [["1", "2"], ["2", "3"], ["3", "1"]],
            "flight_edges": [["1", "2"], ["2", "3"], ["3", "1"]],
            "distance_constraints": [
                {"a": "1", "b": "2", "value": 100.0, "source": "1-2为100米"}],
            "segment_constraints": [], "bearing_constraints": [],
            "direction_relations": [], "angle_constraints": [],
            "vertex_ray_angles": [], "equal_distance_constraints": [],
            "parallel_constraints": [], "coordinates": {},
            "source_text": "1-2为100米",
        }
        review = {"status": "NEEDS_REVIEW", "reason": "题面约束不足",
                  "spec": spec}

        result = self.RP.RE._apply_assist_review_label(question, review)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertTrue(result["candidate"])
        self.assertFalse(result["candidate_constraints_passed"])
        self.assertEqual(result["candidate_flight_edges"], spec["flight_edges"])

    def test_click_runs_in_background_without_local_ocr_and_saves_answer(self):
        question = {
            "id": 987654321,
            "imported": True,
            "graph_hint": "五边形",
            "conditions_text": "OCR 文本",
            "requirements_text": "",
            "suggested_text": "",
            "reference_source": {
                "source_text": "题卡 OCR 原文",
                "diagram_model": {"shape": "五边形"},
            },
        }
        result = {
            "status": "ready",
            "normalized_conditions": "题面条件已整理",
            "condition_items": [{
                "normalized": "起飞点位于1点西南方向200米处",
                "visual_evidence": "题面原文：西南方向200米",
                "confidence": 0.99,
            }],
            "uncertain_items": [],
            "direct_answer": {
                "provider": "测试服务商", "model": "test-vision",
                "summary": "按题面方向和距离完成参考解答。",
                "steps": ["起飞点先到避让点，再进入1号点。"],
                "flight_legs": [
                    {"from": "H", "to": "A", "bearing_deg": 225,
                     "distance_m": 50, "note": "起飞点到避让点"},
                    {"from": "A", "to": "1", "bearing_deg": 45,
                     "distance_m": 35, "note": "避让点到1号点"},
                ],
                "coordinates": [], "parameters": [], "assumptions": [],
                "route_map": {
                    "points": {
                        "H": {"x": 150, "y": 850},
                        "A": {"x": 260, "y": 740},
                        "1": {"x": 360, "y": 630},
                        "2": {"x": 620, "y": 300},
                    },
                    "geometry_edges": [["1", "2"]],
                    "flight_edges": [["H", "A"], ["A", "1"]],
                    "access_edges": [["H", "A"], ["A", "1"]],
                },
            },
        }
        vision_calls = []
        with tempfile.TemporaryDirectory() as folder:
            image_path = os.path.join(folder, "card.png")
            Path(image_path).write_bytes(b"test image")
            persisted = [copy.deepcopy(question)]

            def save_items(items):
                persisted[:] = copy.deepcopy(items)
                return True

            def fake_vision(_path, *args, **kwargs):
                vision_calls.append((args, kwargs))
                kwargs["progress_callback"]("视觉 API 正在直接解题…")
                return copy.deepcopy(result)

            panel = self.RP.RefAnswerPanel(scale=1.0)
            panel.set_question(question)
            panel.show()
            with patch.object(self.VA, "_api_settings", return_value=(
                    True, self.VA.DEFAULT_API_BASE_URL, "test-key", self.VA.DEFAULT_API_MODEL)), \
                 patch.object(self.RP.cfg, "get", return_value=True), \
                 patch.object(self.VA, "solve_question", side_effect=fake_vision), \
                 patch.object(self.RP.RE, "solve_reference",
                              side_effect=AssertionError("导入题卡不得调用本地求解器")), \
                 patch.object(self.RP.OCR, "recognize_image",
                              side_effect=AssertionError("解题请求不得调用本地 OCR")) as rescan, \
                 patch.object(self.BD, "fig_path", return_value=image_path), \
                 patch.object(self.BD, "load", side_effect=lambda: copy.deepcopy(persisted)), \
                 patch.object(self.BD, "save", side_effect=save_items):
                panel.start_condition_vision()
                worker = panel._vision_thread
                self.assertIsNotNone(worker)
                self.assertTrue(worker.wait(5000))
                self.app.processEvents()
                self.app.processEvents()

            self.assertIsNone(panel._vision_thread)
            self.assertEqual(panel.vision_progress.value(), 100)
            rescan.assert_not_called()
            self.assertEqual(vision_calls[0][0], ())
            self.assertEqual(set(vision_calls[0][1]), {
                "progress_callback", "stream_callback", "request_control"})
            self.assertIn("视觉 API 直接作答", panel.view.toPlainText())
            self.assertIn("起飞点先到避让点，再进入1号点", panel.view.toPlainText())
            self.assertEqual(panel.map._access_edges, [("H", "A"), ("A", "1")])
            self.assertEqual(
                persisted[0]["reference_source"]["condition_assist"]["trigger"],
                "reference_answer_button")
            self.assertEqual(
                persisted[0]["reference_source"]["direct_answer"]["provider"],
                "测试服务商")
            cached_answer = persisted[0]["reference_source"]["direct_answer"]
            self.assertEqual(cached_answer["question_id"], question["id"])
            self.assertTrue(cached_answer["question_signature"])
            self.assertEqual(panel.b_edit.text(), "人工核验")

            # 重新打开同一题应直接读取绑定的答案，不再次请求 API。
            with (
                patch.object(self.BD, "fig_path", return_value=image_path),
                patch.object(self.BD, "load",
                             side_effect=lambda: copy.deepcopy(persisted)),
                patch.object(self.BD, "save", side_effect=save_items),
            ):
                reopened = copy.deepcopy(persisted[0])
                self.assertEqual(
                    cached_answer["question_signature"],
                    self.RP._question_answer_signature(reopened))
                panel.set_question(reopened)
                self.assertEqual(panel.b_vision.text(), "重新解题")
                self.assertEqual(panel.b_edit.text(), "人工核验")
                panel.start_condition_vision()
                self.assertEqual(len(vision_calls), 1)
                self.assertIn("没有再次调用", panel.vision_status.text())

                # 用户确认答案后，核验标记也保存在该题的记录中。
                panel._on_answer_action()
                self.assertTrue(persisted[0]["reference_source"][
                    "direct_answer"]["human_verified"])
                self.assertEqual(panel.b_edit.text(), "已核验")
                panel.set_question(copy.deepcopy(persisted[0]))
                self.assertEqual(panel.b_edit.text(), "已核验")
            panel.hide()
            panel.deleteLater()
            self.app.processEvents()

    def test_answer_remains_visible_when_saving_to_question_bank_fails(self):
        question = {"id": 987654321, "imported": True,
                    "reference_source": {}}
        result = {
            "status": "ready",
            "condition_items": [],
            "uncertain_items": [],
            "direct_answer": {
                "provider": "测试服务商", "model": "test-vision",
                "summary": "模型已经返回可读的参考答案。",
                "steps": ["按图片逐项计算航段。"],
                "flight_legs": [], "coordinates": [], "parameters": [],
                "assumptions": [], "estimated_cost": 0.01,
                "route_map": {"points": {}, "geometry_edges": [],
                              "flight_edges": [], "access_edges": []},
            },
        }
        panel = self.RP.RefAnswerPanel(scale=1.0)
        panel._refresh_billing_badge = lambda *args, **kwargs: None
        panel.set_question(question)
        with patch.object(self.BD, "load", return_value=[copy.deepcopy(question)]), \
             patch.object(self.BD, "save", return_value=False), \
             patch.object(self.VA, "_api_provider", return_value="deepseek"):
            panel._on_vision_completed(question["id"], result, {}, "")
            self.app.processEvents()

        self.assertIn("模型已经返回可读的参考答案", panel.view.toPlainText())
        self.assertIn("本地保存失败", panel.vision_status.text())
        self.assertIn("direct_answer", panel._q["reference_source"])
        panel.hide()
        panel.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
