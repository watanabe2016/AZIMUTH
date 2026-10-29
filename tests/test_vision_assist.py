import io
import json
import math
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import vision_assist as VA
import diagram_vision as DV


class VisionAssistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_response_parser_accepts_double_escaped_local_json(self):
        expected = {
            "complete": True,
            "condition_items": [{
                "normalized": "航线高度80米",
                "visual_evidence": "航线高度80米",
                "confidence": 1.0,
            }],
        }
        double_escaped = json.dumps(
            json.dumps(expected, ensure_ascii=False), ensure_ascii=False)[1:-1]
        self.assertEqual(VA._response_json(double_escaped), expected)
        escaped_lines = r'{\n  \"complete\": true,\n  \"condition_items\": []}'
        self.assertEqual(VA._response_json(escaped_lines), {
            "complete": True, "condition_items": []})

    def test_response_parser_repairs_unescaped_quotes_inside_condition_text(self):
        raw = '{"condition_items":[{"normalized":"到达“避让点"后继续飞行"}]}'
        self.assertEqual(VA._response_json(raw), {
            "condition_items": [{"normalized": "到达“避让点\"后继续飞行"}]})

    def test_completion_parser_accepts_structured_and_chunked_provider_content(self):
        expected = {"complete": True, "solution": {"summary": "解析成功"}}
        direct, finish, size = VA._completion_response_json({
            "choices": [{"finish_reason": "stop", "message": {"content": expected}}]})
        self.assertEqual(direct, expected)
        self.assertEqual(finish, "stop")
        self.assertGreater(size, 0)

        chunked, finish, _ = VA._completion_response_json({
            "choices": [{"finish_reason": "stop", "message": {"content": [
                {"type": "text", "text": "```json\\n"},
                {"type": "output_text", "text": json.dumps(expected, ensure_ascii=False)},
                {"type": "text", "text": "\\n```"},
            ]}}]})
        self.assertEqual(chunked, expected)
        self.assertEqual(finish, "stop")

    def test_api_settings_read_saved_values(self):
        values = {
            "vision_api_base_url": VA.DEFAULT_API_BASE_URL,
            "vision_api_model": VA.DEFAULT_API_MODEL,
        }
        with patch.dict(os.environ, {}, clear=True), patch.object(
                VA.cfg, "get", side_effect=lambda key, default=None:
                values.get(key, default)), patch.object(
                VA.cfg, "get_vision_api_key", return_value="saved-key"):
            self.assertEqual(VA._api_settings(), (
                True, VA.DEFAULT_API_BASE_URL, "saved-key", VA.DEFAULT_API_MODEL))

    def test_environment_configuration_remains_supported(self):
        env = {
            "AZIMUTH_VISION_API_BASE_URL": "https://vision.example/v1",
            "AZIMUTH_VISION_API_KEY": "local-token",
            "AZIMUTH_VISION_MODEL": "custom-vision-model",
        }
        with patch.dict(os.environ, env, clear=True):
            configured = VA._api_settings()
        self.assertEqual(configured, (
            True, env["AZIMUTH_VISION_API_BASE_URL"], env["AZIMUTH_VISION_API_KEY"],
            env["AZIMUTH_VISION_MODEL"]))

    def test_diagram_sanitizer_keeps_only_real_parallel_edges(self):
        diagram = VA._sanitize_diagram_model({
            "shape": "四边形", "nodes": ["1", "2", "3", "4"],
            "geometry_edges": [["1", "2"], ["2", "3"],
                               ["3", "4"], ["4", "1"]],
            "parallel_edge_pairs": [
                [["1", "2"], ["3", "4"]],
                [["1", "3"], ["2", "4"]],
            ],
            "point_positions": {
                "1": {"x": 0.8, "y": 0.8}, "2": {"x": 0.2, "y": 0.8},
                "3": {"x": 0.2, "y": 0.2}, "4": {"x": 0.8, "y": 0.2},
            },
            "confidence": 0.98,
        })
        self.assertEqual(diagram["shape"], "梯形")
        self.assertEqual(diagram["parallel_edge_pairs"], [
            [["1", "2"], ["3", "4"]]])
        self.assertEqual(diagram["point_positions"]["1"], {"x": 800.0, "y": 800.0})

    def test_api_health_check_sends_small_text_request(self):
        response = {"choices": [{"message": {"content": "OK"}}]}
        with patch.object(VA, "_api_settings", return_value=(
                True, VA.DEFAULT_API_BASE_URL, "test-key", VA.DEFAULT_API_MODEL)),              patch.object(VA.urllib.request, "urlopen", return_value=io.BytesIO(
                 json.dumps(response).encode("utf-8"))) as opened:
            status = VA.check_api_connection(timeout=0.1)
        self.assertTrue(status["connected"])
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url,
                         "https://api.deepseek.com/chat/completions")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], VA.DEFAULT_API_MODEL)
        self.assertNotIn("image_url", json.dumps(payload))

    def test_openai_health_check_omits_unsupported_sampling_parameter(self):
        response = {"choices": [{"message": {"content": "OK"}}]}
        with patch.object(VA, "_api_settings", return_value=(
                True, "https://api.openai.com/v1", "openai-key", "gpt-6-astra")), \
             patch.object(VA, "_api_provider", return_value="openai"), \
             patch.object(VA.urllib.request, "urlopen", return_value=io.BytesIO(
                 json.dumps(response).encode("utf-8"))) as opened:
            status = VA.check_api_connection(timeout=0.1)
        self.assertTrue(status["connected"])
        payload = json.loads(opened.call_args.args[0].data.decode("utf-8"))
        self.assertNotIn("temperature", payload)
        self.assertEqual(payload["max_completion_tokens"], 8)

    def test_condition_organization_reports_real_stages(self):
        content = {
            "complete": True,
            "condition_items": [{
                "normalized": "起飞点位于1点西南方向200米处",
                "visual_evidence": "起飞点位于1点的SW方向200m处",
                "confidence": 0.98,
            }],
            "uncertain_items": [],
        }
        response = {"choices": [{"message": {
            "content": json.dumps(content, ensure_ascii=False)}}]}
        progress = []
        with patch.object(VA, "_api_settings", return_value=(
                True, VA.DEFAULT_API_BASE_URL, "test-key", VA.DEFAULT_API_MODEL)), \
             patch.object(VA, "_query_deepseek_balance", return_value=None), \
             patch.object(VA, "_image_data", return_value="data:image/jpeg;base64,AA=="), \
             patch.object(VA.urllib.request, "urlopen",
                          return_value=io.BytesIO(json.dumps(response).encode("utf-8"))):
            result = VA.organize_conditions(
                "unused.png", "OCR 原文", {}, progress_callback=progress.append)
        self.assertEqual(result["status"], "ready")
        self.assertGreaterEqual(len(progress), 4)
        self.assertIn("正在准备题卡图像…", progress[0])
        self.assertTrue(any("检索题库中已核验" in value for value in progress))
        self.assertTrue(any("正在通过视觉模型 API 识别" in value for value in progress))

    def test_cloud_api_uses_openai_compatible_image_json_request(self):
        content = {
            "complete": True,
            "condition_items": [{
                "normalized": "航线高度80米",
                "visual_evidence": "航线高度为80m",
                "confidence": 0.98,
            }],
            "uncertain_items": [],
            "diagram_model": {
                "shape": "三角形", "nodes": ["1", "2", "3"],
                "flight_order": ["1", "2", "3"],
                "flight_closed": True, "confidence": 0.98,
            },
        }
        response = {"choices": [{"message": {
            "content": json.dumps(content, ensure_ascii=False)},
            "finish_reason": "stop"}]}
        local_cv = {
            "source": "local_cv", "status": "ready", "shape": "三角形",
            "nodes": ["1", "2", "3"],
            "geometry_edges": [["1", "2"], ["2", "3"], ["3", "1"]],
            "point_positions": {
                "1": {"x": 500, "y": 100},
                "2": {"x": 100, "y": 800},
                "3": {"x": 900, "y": 800},
            },
            "topology_valid": True, "validated_cycle": True,
            "confidence": 0.98,
        }
        with patch.object(VA, "_api_settings", return_value=(
                True, VA.DEFAULT_API_BASE_URL, "test-key", VA.DEFAULT_API_MODEL)),              patch.object(VA, "_image_data", return_value="data:image/jpeg;base64,AA=="),              patch.object(VA.urllib.request, "urlopen", return_value=io.BytesIO(
                 json.dumps(response).encode("utf-8"))) as opened:
            result = VA.organize_conditions(
                "unused.jpg", "航线高度为80m", diagram_model=local_cv)

        request = opened.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url,
                         "https://api.deepseek.com/chat/completions")
        self.assertEqual(payload["model"], VA.DEFAULT_API_MODEL)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["messages"][1]["content"][1]["image_url"]["url"],
                         "data:image/jpeg;base64,AA==")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["diagram_model"]["geometry_edges"],
                         local_cv["geometry_edges"])

    def test_unconfigured_api_keeps_local_shape_without_answer_claim(self):
        diagram = {
            "source": "local_cv", "status": "ready", "shape": "蝴蝶形",
            "nodes": ["1", "2", "3", "4"],
            "geometry_edges": [["1", "2"], ["2", "3"],
                               ["3", "4"], ["4", "1"]],
            "point_positions": {
                "1": {"x": 180, "y": 180}, "2": {"x": 820, "y": 180},
                "3": {"x": 180, "y": 820}, "4": {"x": 820, "y": 820},
            },
            "topology_valid": True, "confidence": .94,
        }
        with patch.object(VA, "_api_settings", return_value=(
                False, VA.DEFAULT_API_BASE_URL, "", VA.DEFAULT_API_MODEL)):
            result = VA.organize_conditions(
                "unused.png", "OCR 条件", diagram_model=diagram)
        self.assertEqual(result["status"], "review")
        self.assertFalse(result["solver_usable"])
        self.assertEqual(result["diagram_model"]["shape"], "蝴蝶形")
        self.assertFalse(result["condition_items"])
        self.assertTrue(any("不会据此生成" in text
                            for text in result["uncertain_items"]))

    def test_one_uncertain_condition_does_not_block_candidate_input(self):
        content = {
            "complete": False,
            "condition_items": [
                {"normalized": "起飞点位于1点西南方向200米",
                 "visual_evidence": "起飞点位于1点的SW方向200m处",
                 "confidence": 0.96},
                {"normalized": "图形航点1到2距离约92米",
                 "visual_evidence": "1-2=2-3=4-5=5-1=92m",
                 "confidence": 0.61},
            ],
            "uncertain_items": ["风向文字不清，可能是西南风"],
        }
        response = {"choices": [{"message": {
            "content": json.dumps(content, ensure_ascii=False)}}]}
        with patch.object(VA, "_api_settings", return_value=(
                True, VA.DEFAULT_API_BASE_URL, "test-key", VA.DEFAULT_API_MODEL)), \
             patch.object(VA, "_image_data", return_value="data:image/jpeg;base64,AA=="), \
             patch.object(VA.urllib.request, "urlopen",
                          return_value=io.BytesIO(json.dumps(response).encode("utf-8"))):
            result = VA.organize_conditions("unused.png", "OCR原文", {})

        self.assertEqual(result["status"], "review")
        self.assertTrue(result["solver_usable"])
        self.assertEqual(result["answer_mode"], "candidate")
        self.assertIn("起飞点位于1点西南方向200米", result["normalized_conditions"])
        self.assertIn("图形航点1到2距离约92米", result["normalized_conditions"])
        self.assertEqual(len(result["uncertain_items"]), 1)

    def test_diagram_topology_and_vertex_positions_are_retained(self):
        content = {
            "complete": True,
            "condition_items": [{
                "normalized": "起飞点位于1点西南方向200米",
                "visual_evidence": "起飞点位于1点的SW方向200m处",
                "confidence": 0.95,
            }],
            "uncertain_items": [],
            "diagram_model": {
                "shape": "凹五边形", "nodes": ["1", "2", "3", "4", "5"],
                "geometry_edges": [["1", "2"], ["2", "3"], ["3", "4"],
                                   ["4", "5"], ["5", "1"]],
                "flight_order": ["1", "2", "3", "4", "5"],
                "flight_closed": True,
                "point_positions": {
                    "1": {"x": 200, "y": 800}, "2": {"x": 200, "y": 200},
                    "3": {"x": 800, "y": 200}, "4": {"x": 500, "y": 500},
                    "5": {"x": 800, "y": 800},
                },
                "confidence": 0.94,
            },
        }
        response = {"choices": [{"message": {
            "content": json.dumps(content, ensure_ascii=False)}}]}
        with patch.object(VA, "_api_settings", return_value=(
                True, VA.DEFAULT_API_BASE_URL, "test-key", VA.DEFAULT_API_MODEL)), \
             patch.object(VA, "_image_data", return_value="data:image/jpeg;base64,AA=="), \
             patch.object(VA.urllib.request, "urlopen",
                          return_value=io.BytesIO(json.dumps(response).encode("utf-8"))):
            result = VA.organize_conditions("unused.png", "OCR原文", {})

        self.assertTrue(result["solver_usable"])
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["diagram_model"]["shape"], "五边形")
        self.assertEqual(len(result["diagram_model"]["geometry_edges"]), 5)
        self.assertEqual(result["diagram_model"]["point_positions"]["4"],
                         {"x": 500.0, "y": 500.0})
        self.assertTrue(result["diagram_complete"])
        self.assertTrue(result["diagram_model"]["topology_valid"])

    def test_validated_local_cv_geometry_overrides_visual_layout_guess(self):
        content = {
            "complete": True,
            "condition_items": [{
                "normalized": "12=100米",
                "visual_evidence": "12=100m",
                "confidence": 0.98,
            }],
            "uncertain_items": [],
            "diagram_model": {
                "shape": "四边形", "nodes": ["1", "2", "3", "4"],
                "geometry_edges": [["1", "2"], ["2", "3"],
                                   ["3", "4"], ["4", "1"]],
                "flight_order": ["1", "2", "3", "4"],
                "flight_closed": True,
                "point_positions": {
                    "1": {"x": 400, "y": 100}, "2": {"x": 800, "y": 100},
                    "3": {"x": 800, "y": 800}, "4": {"x": 400, "y": 800},
                },
                "confidence": 0.94,
            },
        }
        response = {"choices": [{"message": {
            "content": json.dumps(content, ensure_ascii=False)}}]}
        local_cv = {
            "source": "local_cv", "status": "ready", "shape": "梯形",
            "nodes": ["1", "2", "3", "4"],
            "geometry_edges": [["1", "2"], ["1", "4"],
                               ["2", "3"], ["3", "4"]],
            "point_positions": {
                "1": {"x": 330, "y": 380}, "2": {"x": 150, "y": 375},
                "3": {"x": 150, "y": 170}, "4": {"x": 420, "y": 170},
            },
            "validated_cycle": True, "confidence": 0.98,
            "parallel_edge_pairs": [[["1", "2"], ["3", "4"]]],
        }
        with patch.object(VA, "_api_settings", return_value=(
                True, VA.DEFAULT_API_BASE_URL, "test-key", VA.DEFAULT_API_MODEL)), \
             patch.object(VA, "_image_data", return_value="data:image/jpeg;base64,AA=="), \
             patch.object(VA.urllib.request, "urlopen",
                          return_value=io.BytesIO(json.dumps(response).encode("utf-8"))):
            result = VA.organize_conditions("unused.png", "OCR原文", local_cv)

        diagram = result["diagram_model"]
        self.assertEqual(diagram["shape"], "梯形")
        self.assertEqual(diagram["geometry_source"], "local_cv")
        self.assertTrue(diagram["geometry_validated"])
        self.assertEqual(diagram["geometry_edges"], local_cv["geometry_edges"])
        self.assertEqual(diagram["point_positions"], local_cv["point_positions"])
        self.assertEqual(diagram["parallel_edge_pairs"], local_cv["parallel_edge_pairs"])

    def test_incomplete_edges_and_missing_flight_order_are_not_filled_in(self):
        diagram = VA._sanitize_diagram_model({
            "shape": "五边形", "nodes": ["1", "2", "3", "4", "5"],
            "geometry_edges": [["1", "2"], ["2", "3"]],
            "flight_order": [], "flight_closed": None,
        })
        self.assertEqual(diagram["geometry_edges"], [["1", "2"], ["2", "3"]])
        self.assertEqual(diagram["flight_order"], [])
        self.assertEqual(diagram["flight_edges"], [])
        self.assertFalse(diagram["topology_valid"])
        self.assertTrue(diagram["topology_errors"])

    def test_closed_geometry_is_validated_independently_from_missing_route_metadata(self):
        cases = (("三角形", "三角形", 3), ("等边三角形", "等边三角形", 3),
                 ("直角三角形", "直角三角形", 3),
                 ("四边形", "四边形", 4), ("凹四边形", "凹四边形", 4),
                 ("梯形", "梯形", 4), ("直角梯形", "直角梯形", 4),
                 ("等腰梯形", "等腰梯形", 4),
                 ("平行四边形", "平行四边形", 4), ("矩形", "矩形", 4),
                 ("正方形", "正方形", 4), ("菱形", "菱形", 4),
                 ("蝴蝶形", "蝴蝶形", 4), ("五边形", "五边形", 5),
                 ("凹五边形", "五边形", 5), ("六边形", "六边形", 6),
                 ("凹六边形", "六边形", 6),
                 ("七边形", "七边形", 7), ("八边形", "八边形", 8),
                 ("九边形", "九边形", 9), ("十边形", "十边形", 10),
                 ("十一边形", "十一边形", 11), ("十二边形", "十二边形", 12),
                 ("十三边形", "十三边形", 13),
                 ("十四边形", "十四边形", 14),
                 ("十五边形", "十五边形", 15),
                 ("十六边形", "十六边形", 16),
                 ("圆形", "圆形", 4), ("扫描航线", "扫描航线", 6))
        for shape, normalized_shape, count in cases:
            with self.subTest(shape=shape):
                nodes = [str(index) for index in range(1, count + 1)]
                edges = [[nodes[index], nodes[(index + 1) % count]]
                         for index in range(count)]
                model = VA._sanitize_diagram_model({
                    "shape": shape, "nodes": nodes,
                    "geometry_edges": edges,
                    "flight_order": [], "flight_closed": None,
                    "point_positions": {
                        node: {"x": 500 + index, "y": 500}
                        for index, node in enumerate(nodes)},
                    "confidence": .95,
                })
                self.assertEqual(model["shape"], normalized_shape)
                self.assertEqual(model["nodes"], nodes)
                self.assertTrue(model["topology_valid"])
                self.assertFalse(model["topology_errors"])
                self.assertTrue(any("航点顺序" in item
                                    for item in model["warnings"]))

    def test_circle_without_straight_edges_counts_as_complete_diagram(self):
        diagram = VA._sanitize_diagram_model({
            "shape": "圆形", "nodes": [], "geometry_edges": [],
            "flight_order": [], "flight_closed": None,
        })
        self.assertEqual(diagram["geometry_edges"], [])
        self.assertTrue(diagram["topology_valid"])
        self.assertTrue(VA._diagram_is_complete(diagram))

    def test_polygon_with_missing_edges_does_not_count_as_complete_diagram(self):
        diagram = VA._sanitize_diagram_model({
            "shape": "梯形", "nodes": ["1", "2", "3", "4"],
            "geometry_edges": [["1", "2"], ["2", "3"]],
        })
        self.assertFalse(VA._diagram_is_complete(diagram))

    def test_local_geometry_reader_accepts_multi_digit_polygon_labels(self):
        words = []
        for index in range(16):
            angle = math.tau * index / 16
            x = 500 + 200 * math.cos(angle)
            y = 500 + 200 * math.sin(angle)
            words.append({"text": str(index + 1), "x": x - 5, "y": y - 5,
                          "width": 10, "height": 10, "confidence": .99})
        words.append({"text": "1、航线高度", "x": 20, "y": 850,
                      "width": 100, "height": 20, "confidence": .99})
        labels = DV._point_labels(words, 1000)
        self.assertEqual([item[0] for item in labels],
                         [str(index) for index in range(1, 17)])

    def test_large_scanned_image_is_encoded_with_live_qbytearray(self):
        from PySide6.QtGui import QImage
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "large-scan.png"
            image = QImage(1786, 797, QImage.Format.Format_RGB32)
            image.fill(0xFFE8E8E8)
            self.assertTrue(image.save(str(path), "PNG"))
            value = VA._image_data(str(path))
        self.assertTrue(value.startswith("data:image/jpeg;base64,"))
        self.assertGreater(len(value), 1000)

    def test_direct_api_solver_returns_solution_and_takeoff_avoidance_route(self):
        content = {
            "complete": True,
            "condition_items": [{
                "normalized": "起飞点到避让点再到1号点",
                "visual_evidence": "起飞点/返航点要求",
                "confidence": .96,
            }],
            "uncertain_items": [],
            "diagram_model": {
                "shape": "三角形", "nodes": ["1", "2", "3"],
                "geometry_edges": [["1", "2"], ["2", "3"], ["3", "1"]],
                "point_positions": {
                    "1": {"x": 500, "y": 800},
                    "2": {"x": 200, "y": 200},
                    "3": {"x": 800, "y": 200},
                },
            },
            "route_map": {
                "flight_edges": [["H", "A"], ["A", "1"], ["1", "2"]],
                "access_edges": [["H", "A"], ["A", "1"]],
                "north_up": True,
                "points": {"H": {"x": 0, "y": 0}, "A": {"x": -30, "y": -30},
                           "1": {"x": 0, "y": 0}, "2": {"x": 100, "y": 0}},
            },
            "solution": {
                "summary": "按题目条件完成参考解答。",
                "markdown": "## 参考答案\n依据题卡图片计算各航段。",
                "reasoning_steps": ["先确定图形点位，再按给定方向计算航段。"],
                "flight_legs": [
                    {"from": "H", "to": "A", "bearing_deg": 225,
                     "distance_m": 50, "note": "起飞点至避让点"},
                    {"from": "A", "to": "1", "bearing_deg": 45,
                     "distance_m": 35, "note": "避让点至1号点"},
                    {"from": "1", "to": "2", "bearing_deg": 90,
                     "distance_m": 100},
                ],
                "coordinates": [{"point": "1", "latitude": 30.1,
                                 "longitude": 104.1, "altitude_m": 75}],
                "flight_parameters": [{"name": "航线高度", "value": "75m"}],
                "assumptions": [],
            },
        }
        response = {"choices": [{"message": {
            "content": json.dumps(content, ensure_ascii=False)}}]}
        with patch.object(VA, "_api_settings", return_value=(
                True, VA.DEFAULT_API_BASE_URL, "test-key", VA.DEFAULT_API_MODEL)), \
             patch.object(VA, "_image_data", return_value="data:image/jpeg;base64,AA=="), \
             patch.object(VA.urllib.request, "urlopen", return_value=io.BytesIO(
                 json.dumps(response).encode("utf-8"))) as opened:
            result = VA.solve_question("unused.jpg")

        payload = json.loads(opened.call_args_list[0].args[0].data.decode("utf-8"))
        prompt = payload["messages"][0]["content"]
        user_content = payload["messages"][1]["content"]
        self.assertIn("只根据用户消息中的题卡原图", prompt)
        self.assertIn("solution.markdown", prompt)
        self.assertEqual(len(user_content), 1)
        self.assertEqual(user_content[0]["type"], "image_url")
        self.assertNotIn("LOCAL_OCR_SENTINEL", json.dumps(payload))
        self.assertNotIn("LOCAL_DIAGRAM_SENTINEL", json.dumps(payload))
        self.assertNotIn("LOCAL_FIELD_SENTINEL", json.dumps(payload))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["answer_mode"], "api_direct_reference")
        answer = result["direct_answer"]
        self.assertEqual(answer["route_map"]["flight_edges"][:2], [["H", "A"], ["A", "1"]])
        self.assertTrue(answer["route_map"]["north_up"])
        self.assertIn("依据题卡图片", answer["answer_markdown"])
        self.assertEqual([leg["to"] for leg in answer["flight_legs"][:2]], ["A", "1"])
        self.assertEqual(VA.direct_answer_reference(answer)["candidate_access_edges"],
                         [["H", "A"], ["A", "1"]])

    def test_direct_answer_map_preserves_model_sketch_even_when_fields_conflict(self):
        parsed = {
            "solution": {
                "summary": "航段参考答案。",
                "markdown": "完整推导。",
                "flight_legs": [
                    {"from": "H", "to": "1", "bearing_deg": 225,
                     "distance_m": 50},
                    {"from": "1", "to": "2", "bearing_deg": 332,
                     "distance_m": 300},
                ],
            },
            # 故意让模型点位与航段冲突；软件应原样显示，不替模型更改答案。
            "route_map": {
                "points": {
                    "H": {"x": 100, "y": 100},
                    "1": {"x": 900, "y": 100},
                    "2": {"x": 900, "y": 900},
                },
                "flight_edges": [["H", "1"], ["1", "2"]],
            },
        }
        answer = VA._normalize_direct_answer(parsed, "deepseek-flash", None)
        points = answer["route_map"]["points"]
        self.assertEqual(points, parsed["route_map"]["points"])
        self.assertEqual(answer["raw_response_json"], parsed)
        self.assertEqual(answer["response_mode"], "api_passthrough_v1")

    def test_openai_vision_api_uses_its_chat_completions_format(self):
        content = {"complete": True, "solution": {"summary": "OpenAI 直接作答。"}}
        response = {"choices": [{"message": {
            "content": json.dumps(content, ensure_ascii=False)}}]}
        with patch.object(VA, "_api_settings", return_value=(
                True, "https://api.openai.com/v1", "openai-key", "gpt-6-astra")), \
             patch.object(VA, "_api_provider", return_value="openai"), \
             patch.object(VA, "_image_data", return_value="data:image/jpeg;base64,AA=="), \
             patch.object(VA.urllib.request, "urlopen", return_value=io.BytesIO(
                 json.dumps(response).encode("utf-8"))) as opened:
            result = VA.solve_question("unused.jpg", "OCR文本")
        request = opened.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url,
                         "https://api.openai.com/v1/chat/completions")
        self.assertEqual(payload["model"], "gpt-6-astra")
        self.assertEqual(payload["max_completion_tokens"], 8192)
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["stream_options"], {"include_usage": True})
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("temperature", payload)
        self.assertIn("直接作答", result["direct_answer"]["summary"])

    def test_deepseek_empty_json_response_retries_once_without_json_mode(self):
        answer = {"complete": True, "solution": {"summary": "重试后完成作答。"}}
        responses = [
            {"choices": [{"finish_reason": "stop", "message": {
                "content": "", "reasoning_content": "已完成推演"}}]},
            {"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(answer, ensure_ascii=False)}}]},
        ]
        with patch.object(VA, "_api_settings", return_value=(
                True, VA.DEFAULT_API_BASE_URL, "deepseek-test-key", "deepseek-flash")), \
             patch.object(VA, "_api_provider", return_value="deepseek"), \
             patch.object(VA, "_query_deepseek_balance", return_value=None), \
             patch.object(VA, "_image_data", return_value="data:image/jpeg;base64,AA=="), \
             patch.object(VA.urllib.request, "urlopen", side_effect=[
                 io.BytesIO(json.dumps(response).encode("utf-8"))
                 for response in responses]) as opened:
            result = VA.solve_question("unused.jpg", "图形和航线条件")

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["direct_answer"]["summary"], "重试后完成作答。")
        self.assertEqual(opened.call_count, 2)
        first = json.loads(opened.call_args_list[0].args[0].data.decode("utf-8"))
        retry = json.loads(opened.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertEqual(first["max_tokens"], 65536)
        self.assertNotIn("response_format", first)
        self.assertEqual(first["thinking"], {"type": "enabled"})
        self.assertNotIn("response_format", retry)
        self.assertIn("重新核对题图", retry["messages"][0]["content"])

    def test_deepseek_exhausted_reasoning_does_not_restart_paid_request(self):
        answer = {"complete": True, "solution": {"summary": "扩容后完成作答。"}}
        responses = [
            {"choices": [{"finish_reason": "length", "message": {"content": ""}}]},
            {"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(answer, ensure_ascii=False)}}]},
        ]
        with patch.object(VA, "_api_settings", return_value=(
                True, VA.DEFAULT_API_BASE_URL, "deepseek-test-key", "deepseek-flash")), \
             patch.object(VA, "_api_provider", return_value="deepseek"), \
             patch.object(VA, "_query_deepseek_balance", return_value=None), \
             patch.object(VA, "_image_data", return_value="data:image/jpeg;base64,AA=="), \
             patch.object(VA.urllib.request, "urlopen", side_effect=[
                 io.BytesIO(json.dumps(response).encode("utf-8"))
                 for response in responses]) as opened:
            result = VA.solve_question("unused.jpg", "图形和航线条件")

        self.assertEqual(result["status"], "invalid_response")
        self.assertEqual(opened.call_count, 1)
        self.assertIn("推理额度", result["reason"])

    def test_deepseek_repeated_empty_json_response_shows_retry_guidance(self):
        empty = {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]}
        with patch.object(VA, "_api_settings", return_value=(
                True, VA.DEFAULT_API_BASE_URL, "deepseek-test-key", "deepseek-flash")), \
             patch.object(VA, "_api_provider", return_value="deepseek"), \
             patch.object(VA, "_query_deepseek_balance", return_value=None), \
             patch.object(VA, "_image_data", return_value="data:image/jpeg;base64,AA=="), \
             patch.object(VA.urllib.request, "urlopen", side_effect=[
                 io.BytesIO(json.dumps(empty).encode("utf-8")),
                 io.BytesIO(json.dumps(empty).encode("utf-8"))]):
            result = VA.solve_question("unused.jpg", "圖形和航線條件")

        self.assertEqual(result["status"], "invalid_response")
        self.assertIn("自动重试一次后仍为空", result["reason"])

    def test_openai_compatible_api_retries_without_unsupported_json_mode(self):
        response = {"choices": [{"message": {"content": "{}"}}]}
        error = VA.urllib.error.HTTPError(
            "https://vision.example/v1/chat/completions", 400, "Bad Request", {},
            io.BytesIO(b'{"error":{"message":"response_format is not supported"}}'))
        payload = {
            "model": "vision-model",
            "messages": [{"role": "user", "content": "return JSON"}],
            "response_format": {"type": "json_object"},
        }
        with patch.object(VA.urllib.request, "urlopen", side_effect=[
                error, io.BytesIO(json.dumps(response).encode("utf-8"))]) as opened:
            result = VA._chat_request(
                "https://vision.example/v1", "api-key", payload, timeout=1)
        self.assertEqual(result, response)
        first = json.loads(opened.call_args_list[0].args[0].data.decode("utf-8"))
        second = json.loads(opened.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertIn("response_format", first)
        self.assertNotIn("response_format", second)
        error.close()

    def test_chat_sse_stream_emits_answer_text_and_usage(self):
        events = [
            {"choices": [{"delta": {"content": '{"solution":{"markdown":"**航线**"}'},
                          "finish_reason": None}]},
            {"choices": [{"delta": {"content": ',"summary":"完成"}'},
                          "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 5,
                                      "total_tokens": 14}},
        ]
        raw = "".join("data: %s\n\n" % json.dumps(item, ensure_ascii=False)
                      for item in events) + "data: [DONE]\n\n"

        class StreamingResponse(io.BytesIO):
            headers = {"Content-Type": "text/event-stream"}

        received = []
        progress = []
        with patch.object(VA.urllib.request, "urlopen",
                          return_value=StreamingResponse(raw.encode("utf-8"))):
            body = VA._chat_request(
                "https://vision.example/v1", "api-key",
                {"model": "vision-model", "stream": True}, timeout=1,
                progress_callback=progress.append,
                stream_callback=received.append)

        self.assertTrue(received)
        self.assertIn("航线", VA.partial_answer_text(received[-1]))
        self.assertEqual(progress[-1], len(body["choices"][0]["message"]["content"]))
        self.assertEqual(body["usage"]["total_tokens"], 14)

    def test_partial_answer_parser_never_exposes_incomplete_json(self):
        raw = '{"solution":{"markdown":' + json.dumps(
            "**答案**\n第二段", ensure_ascii=False)[:-1]
        self.assertEqual(VA.partial_answer_text(raw), "**答案**\n第二段")
        self.assertEqual(VA.partial_answer_text('{"complete":true,"condition_items":['), "")
        self.assertEqual(VA.partial_answer_text("# 直接 Markdown"), "# 直接 Markdown")

    def test_cancelled_request_does_not_open_a_network_connection(self):
        control = VA.RequestControl()
        control.cancel()
        with patch.object(VA.urllib.request, "urlopen") as opened:
            with self.assertRaises(VA.RequestCancelled):
                VA._chat_request(
                    "https://vision.example/v1", "api-key",
                    {"model": "vision-model", "stream": True}, timeout=1,
                    request_control=control)
        opened.assert_not_called()

    def test_cancel_closes_an_active_stream_and_unblocks_the_worker(self):
        entered = threading.Event()
        closed = threading.Event()

        class WaitingResponse:
            headers = {"Content-Type": "text/event-stream"}

            def __iter__(self):
                entered.set()
                closed.wait(2)
                if closed.is_set():
                    raise OSError("connection closed")
                return iter(())

            def close(self):
                closed.set()

        control = VA.RequestControl()
        errors = []

        def request():
            try:
                VA._chat_request(
                    "https://vision.example/v1", "api-key",
                    {"model": "vision-model", "stream": True}, timeout=1,
                    request_control=control)
            except Exception as exc:
                errors.append(exc)

        with patch.object(VA.urllib.request, "urlopen", return_value=WaitingResponse()):
            worker = threading.Thread(target=request)
            worker.start()
            self.assertTrue(entered.wait(1))
            control.cancel()
            worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], VA.RequestCancelled)


class VisionBillingAndConditionAuditTests(unittest.TestCase):
    def test_numbered_conditions_are_preserved_for_coverage_checks(self):
        text = "1、航线高度60米\n2、三条边长120米\n10、起飞点坐标。"
        self.assertEqual(VA._numbered_condition_lines(text), [
            {"id": "1", "text": "航线高度60米"},
            {"id": "2", "text": "三条边长120米"},
            {"id": "10", "text": "起飞点坐标。"},
        ])
        self.assertEqual(VA._condition_item_id({
            "normalized": "2、三条边长120米"}), "2")

    def test_direct_answer_does_not_gate_on_local_ocr_coverage(self):
        text = "\n".join("%d、条件 %d。" % (index, index)
                          for index in range(1, 11))
        content = {
            "complete": True,
            "condition_items": [
                {"condition_id": str(index), "normalized": "%d、条件 %d" % (index, index),
                 "visual_evidence": "%d、条件 %d。" % (index, index), "confidence": 0.99}
                for index in range(1, 5)],
            "solution": {"summary": "仅检查部分条件的航线草案。"},
        }
        response = {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(content, ensure_ascii=False)}}]}
        config_values = {"vision_api_spend_by_currency": {},
                         "vision_api_call_count": 0,
                         "vision_api_budget_amount": 0,
                         "vision_api_currency": "USD"}
        with patch.object(VA, "_api_settings", return_value=(
                True, "https://vision.example/v1", "test-key", "vision-model")), \
             patch.object(VA, "_api_provider", return_value="custom"), \
             patch.object(VA, "_image_data", return_value="data:image/jpeg;base64,AA=="), \
             patch.object(VA.urllib.request, "urlopen", return_value=io.BytesIO(
                 json.dumps(response).encode("utf-8"))) as opened, \
             patch.object(VA.cfg, "get", side_effect=lambda key, default=None:
                          config_values.get(key, default)), \
             patch.object(VA.cfg, "set_"), patch.object(VA.cfg, "save"):
            result = VA.solve_question("unused.jpg", text)

        payload = json.loads(opened.call_args.args[0].data.decode("utf-8"))
        user_content = payload["messages"][1]["content"]
        self.assertEqual(len(user_content), 1)
        self.assertEqual(user_content[0]["type"], "image_url")
        self.assertNotIn("条件 10", json.dumps(payload, ensure_ascii=False))
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["complete"])
        self.assertEqual(result["condition_coverage"]["source_count"], 0)
        self.assertEqual(result["condition_coverage"]["returned_count"], 4)
        self.assertEqual(result["condition_coverage"]["missing_ids"], [])

    def test_deepseek_balance_is_read_from_provider_endpoint(self):
        with patch.object(VA, "_api_settings", return_value=(
                True, "https://api.deepseek.com", "test-key", "deepseek-flash")), \
             patch.object(VA, "_api_provider", return_value="deepseek"), \
             patch.object(VA.cfg, "get", return_value={}), \
             patch.object(VA, "_query_deepseek_balance", return_value=[
                 {"currency": "CNY", "amount": 12.66}]) as query:
            snapshot = VA.account_billing_snapshot()
        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["balances"][0]["amount"], 12.66)
        query.assert_called_once_with("https://api.deepseek.com", "test-key", request_control=None)

    def test_openai_month_cost_buckets_are_summed(self):
        response = io.BytesIO(json.dumps({"data": [
            {"results": [{"amount": {"value": 0.12, "currency": "usd"}}]},
            {"results": [{"amount": {"value": 0.08, "currency": "usd"}}]},
        ]}).encode("utf-8"))
        with patch.object(VA.urllib.request, "urlopen", return_value=response) as opened:
            totals = VA._query_openai_month_cost("test-admin-key")
        self.assertEqual(totals, [{"currency": "USD", "amount": 0.2}])
        self.assertIn("organization/costs", opened.call_args.args[0].full_url)


if __name__ == "__main__":
    unittest.main()
