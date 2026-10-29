import sys
import math
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import reference_engine as RE


class ReferenceAssistSnapshotTests(unittest.TestCase):
    def _normalized_inputs(self, question):
        seen = []
        with patch.object(RE, "_norm_text",
                          side_effect=lambda value: seen.append(str(value)) or str(value)):
            RE.extract_question_spec(question)
        return seen

    def test_explicit_vision_result_is_used_for_unchanged_import(self):
        current = {
            "graph_hint": "凹五边形",
            "conditions_text": "OCR 误读文本",
            "requirements_text": "航线要求",
            "suggested_text": "建议参数",
        }
        question = {
            "id": 441, "imported": True, **current,
            "reference_source": {
                "recognized_fields": {**current, "conditions_text": "旧 OCR 文本"},
                "reviewed_fields": current,
                "condition_assist": {
                    "solver_usable": True,
                    "normalized_conditions": "视觉模型识别的坐标和航向条件",
                    "source_fields": current,
                },
            },
        }
        seen = self._normalized_inputs(question)
        self.assertIn("视觉模型识别的坐标和航向条件", seen)
        spec = RE.extract_question_spec(question)
        self.assertTrue(spec["condition_assist_used"])
        self.assertEqual(spec["condition_assist_status"], "review")

    def test_edit_after_vision_scan_disables_stale_assist(self):
        snapshot = {
            "graph_hint": "凹五边形",
            "conditions_text": "扫描时文本",
            "requirements_text": "航线要求",
            "suggested_text": "建议参数",
        }
        question = {
            "id": 441, "imported": True, **snapshot,
            "conditions_text": "用户后来修正的文本",
            "reference_source": {
                "condition_assist": {
                    "solver_usable": True,
                    "normalized_conditions": "旧视觉结果",
                    "source_fields": snapshot,
                },
            },
        }
        seen = self._normalized_inputs(question)
        self.assertNotIn("旧视觉结果", seen)
        self.assertTrue(any("用户后来修正的文本" in value for value in seen))

    def test_legacy_snapshot_is_reused_only_when_ocr_and_reviewed_fields_match(self):
        current = {
            "graph_hint": "",
            "conditions_text": "原始 OCR 条件",
            "requirements_text": "",
            "suggested_text": "",
        }
        assist = {
            "normalized_conditions": "视觉识别条件",
            "condition_items": [{"normalized": "边长为 180 米", "confidence": 1.0}],
            "source_fields": {key: value for key, value in current.items()
                              if key != "conditions_text"},
        }
        question = {
            "id": 987654320, "imported": True, **current,
            "reference_source": {
                "recognized_fields": current,
                "reviewed_fields": current,
                "condition_assist": assist,
            },
        }
        self.assertIs(RE._current_condition_assist(question), assist)
        changed = dict(question, conditions_text="用户后来改过的条件")
        self.assertIsNone(RE._current_condition_assist(changed))

    def test_unlabeled_coordinate_pair_is_not_guessed_when_both_values_are_latitudes(self):
        question = {
            "id": 987654319, "imported": True,
            "conditions_text": "起飞点经纬度: 35.3, 40.7",
        }
        coordinates = RE._coords(question)
        self.assertNotIn("H", coordinates)
        self.assertIn("_review", coordinates)

    def test_incomplete_uncertain_assist_does_not_draw_a_partial_route(self):
        current = {
            "graph_hint": "等边三角形",
            "conditions_text": "OCR文字",
            "requirements_text": "航线要求",
            "suggested_text": "",
        }
        question = {
            "id": 987654321, "imported": True, **current,
            "reference_source": {
                "condition_assist": {
                    "status": "review",
                    "solver_usable": True,
                    "complete": False,
                    "normalized_conditions": "识别出的航点和航向条件",
                    "condition_items": [{"normalized": "条件A", "confidence": 0.96}],
                    "uncertain_items": ["风向需要核对"],
                    "source_fields": current,
                },
            },
        }
        solved = {
            "status": "READY",
            "points": {
                "H": {"x": 0.0, "y": 0.0, "lat": 23.0, "lon": 113.0},
                "1": {"x": 100.0, "y": 50.0, "lat": 23.00045, "lon": 113.00098},
            },
            "geometry_edges": [["1", "2"]],
            "flight_edges": [["1", "2"]],
            "access_edges": [["H", "1"]],
        }
        with patch("qmeta.verified_reference_of", return_value=None), \
             patch.object(RE, "solve_generated_reference", return_value=solved):
            result = RE.solve_reference(question)

        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertFalse(result["candidate"])
        self.assertFalse(result["candidate_constraints_passed"])
        self.assertEqual(result["candidate_points"], {})
        self.assertIn("暂不绘制", result["candidate_note"])

    def test_visual_diagram_builds_graph_and_layout_for_external_import(self):
        current = {
            "graph_hint": "图形题",
            "conditions_text": "OCR文字",
            "requirements_text": "航线要求",
            "suggested_text": "",
        }
        diagram = {
            "source": "vision_model", "shape": "五边形",
            "nodes": ["1", "2", "3", "4", "5"],
            "geometry_edges": [["1", "2"], ["2", "3"], ["3", "4"],
                               ["4", "5"], ["5", "1"]],
            "flight_order": ["1", "2", "3", "4", "5"],
            "flight_edges": [["1", "2"], ["2", "3"], ["3", "4"],
                             ["4", "5"], ["5", "1"]],
            "flight_closed": True,
            "point_positions": {
                "1": {"x": 200, "y": 800}, "2": {"x": 200, "y": 200},
                "3": {"x": 800, "y": 200}, "4": {"x": 500, "y": 500},
                "5": {"x": 800, "y": 800},
            },
        }
        question = {
            "id": 987654322, "imported": True, **current,
            "reference_source": {
                "condition_assist": {
                    "status": "review", "solver_usable": True,
                    "normalized_conditions": "起飞点位于1点西南方向200米",
                    "diagram_model": diagram, "source_fields": current,
                },
            },
        }
        spec = RE.extract_question_spec(question)
        self.assertEqual(spec["shape"], "五边形")
        self.assertEqual(spec["graph_source"], "vision_model")
        self.assertEqual(spec["nodes"], ["1", "2", "3", "4", "5"])
        self.assertEqual(spec["geometry_edges"], diagram["geometry_edges"])
        self.assertEqual(spec["flight_edges"], diagram["flight_edges"])
        self.assertEqual(spec["layout_points"][3], [500.0, 500.0])

    def test_regular_hexagon_side_length_and_explicit_closed_cycle_are_solved(self):
        current = {
            "graph_hint": "",
            "conditions_text": (
                "如图正六边形，航线高度为80m。\n"
                "该正六边形航线边长为180米。\n"
                "起飛點要求：∠H23=60°，∠H65=60°。\n"
                "航向要求：2点位于1点正东方向。\n"
                "航线闭合不循环，结束后返回起飞点。\n"
                "起飞点经纬度：111.18443°,35.36752°。\n"
                "H-1航向保留，实线或虚线均可。"
            ),
            "requirements_text": "",
            "suggested_text": "",
        }
        edges = [["1", "2"], ["2", "3"], ["3", "4"],
                 ["4", "5"], ["5", "6"], ["6", "1"]]
        diagram = {
            "source": "vision_model", "shape": "六边形", "confidence": 1.0,
            "nodes": ["1", "2", "3", "4", "5", "6"],
            "geometry_edges": edges,
            "flight_order": [], "flight_edges": [], "flight_closed": False,
            "topology_errors": ["飞行顺序未完整识别"],
            "point_positions": {
                "1": {"x": 500, "y": 700}, "2": {"x": 300, "y": 600},
                "3": {"x": 200, "y": 400}, "4": {"x": 500, "y": 300},
                "5": {"x": 700, "y": 400}, "6": {"x": 700, "y": 600},
            },
        }
        assist = {
            "status": "review", "solver_usable": True, "complete": True,
            "normalized_conditions": current["conditions_text"],
            "condition_items": [{"normalized": "条件", "confidence": 1.0}],
            "uncertain_items": ["飞行顺序未完整识别"],
            "diagram_model": diagram, "source_fields": current,
        }
        question = {"id": 987654318, "imported": True, **current,
                    "reference_source": {"condition_assist": assist}}

        spec = RE.extract_question_spec(question)
        self.assertEqual(len(spec["distance_constraints"]), 6)
        self.assertTrue(all(abs(item["value"] - 180) < 1e-6
                            for item in spec["distance_constraints"]))
        self.assertEqual(spec["bearing_constraints"][0]["value"], 90.0)
        self.assertEqual(len(spec["vertex_ray_angles"]), 2)
        self.assertTrue(spec["route_inferred_from_closed_cycle"])
        self.assertEqual(spec["flight_edges"], [
            ["1", "2"], ["2", "3"], ["3", "4"],
            ["4", "5"], ["5", "6"], ["6", "1"]])
        self.assertEqual(spec["coordinates"]["H"]["kind"], "order-inferred")

        without_explicit_closure = dict(question)
        without_explicit_closure["conditions_text"] = current["conditions_text"].replace(
            "航线闭合不循环，", "航线不循环，")
        changed_current = dict(current,
                               conditions_text=without_explicit_closure["conditions_text"])
        without_explicit_closure["reference_source"] = {
            "condition_assist": dict(assist,
                                     normalized_conditions=changed_current["conditions_text"],
                                     source_fields=changed_current)}
        open_spec = RE.extract_question_spec(without_explicit_closure)
        self.assertFalse(open_spec["route_inferred_from_closed_cycle"])
        self.assertEqual(open_spec["flight_edges"], [])

        low_confidence = dict(question)
        low_confidence["reference_source"] = {
            "condition_assist": dict(assist,
                                     diagram_model=dict(diagram, confidence=0.71))}
        uncertain_spec = RE.extract_question_spec(low_confidence)
        self.assertFalse(uncertain_spec["route_inferred_from_closed_cycle"])
        self.assertEqual(uncertain_spec["flight_edges"], [])

        result = RE._apply_assist_review_label(
            question, RE.solve_generated_reference(question))
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertTrue(result.get("candidate"), result.get("candidate_validation_errors"))
        self.assertTrue(result.get("candidate_constraints_passed"))
        self.assertEqual(result["candidate_access_edges"], [["H", "1"]])
        self.assertEqual(len(result["candidate_flight_edges"]), 6)
        points = result["candidate_points"]
        self.assertAlmostEqual(points["H"]["lat"], 35.36752, places=7)
        self.assertAlmostEqual(points["H"]["lon"], 111.18443, places=7)
        self.assertAlmostEqual(
            RE._angle_of((points["H"]["x"], points["H"]["y"]),
                         (points["2"]["x"], points["2"]["y"]),
                         (points["3"]["x"], points["3"]["y"])),
            60.0, delta=0.1)
        self.assertAlmostEqual(
            RE._angle_of((points["H"]["x"], points["H"]["y"]),
                         (points["6"]["x"], points["6"]["y"]),
                         (points["5"]["x"], points["5"]["y"])),
            60.0, delta=0.1)
        self.assertAlmostEqual(
            (math.degrees(math.atan2(points["2"]["x"] - points["1"]["x"],
                                     points["2"]["y"] - points["1"]["y"])) % 360),
            90.0, delta=0.1)
        for first, second in zip(
                ["1", "2", "3", "4", "5", "6"],
                ["2", "3", "4", "5", "6", "1"]):
            distance = math.hypot(points[second]["x"] - points[first]["x"],
                                  points[second]["y"] - points[first]["y"])
            self.assertAlmostEqual(distance, 180.0, delta=0.1)

    def test_visual_pixel_layout_does_not_invent_missing_answer_points(self):
        current = {
            "graph_hint": "五边形", "conditions_text": "条件文字",
            "requirements_text": "航线要求", "suggested_text": "",
        }
        diagram = {
            "source": "vision_model", "shape": "五边形",
            "nodes": ["1", "2", "3", "4", "5"],
            "geometry_edges": [["1", "2"], ["2", "3"], ["3", "4"],
                               ["4", "5"], ["5", "1"]],
            "flight_order": ["1", "2", "3", "4", "5"],
            "flight_edges": [["1", "2"], ["2", "3"], ["3", "4"],
                             ["4", "5"], ["5", "1"]],
            "flight_closed": True,
            "point_positions": {
                "1": {"x": 200, "y": 800}, "2": {"x": 200, "y": 200},
                "3": {"x": 800, "y": 200}, "4": {"x": 500, "y": 500},
                "5": {"x": 800, "y": 800},
            },
        }
        question = {"id": 987654323, "imported": True, **current,
                    "reference_source": {"condition_assist": {
                        "status": "review", "complete": False,
                        "solver_usable": True, "diagram_model": diagram,
                        "uncertain_items": ["飞行顺序需对照原图"],
                        "source_fields": current,
                    }}}
        partial = {"status": "NEEDS_REVIEW", "spec": {
            "nodes": ["1", "2", "3", "4", "5"],
            "geometry_edges": diagram["geometry_edges"],
            "flight_edges": diagram["flight_edges"],
            "distance_constraints": [], "segment_constraints": [],
        }, "points": {"H": {"x": -20.0, "y": 0.0},
                     "1": {"x": 0.0, "y": 0.0, "lat": 23.0, "lon": 113.0}},
            "candidate": False}

        result = RE._apply_assist_review_label(question, partial)
        self.assertFalse(result["candidate"])
        self.assertFalse(result["visual_layout_fallback"])
        self.assertEqual(result["candidate_points"], {})
        self.assertTrue(result["candidate_validation_errors"])
        self.assertNotIn("H", result["candidate_access_edges"])

    def test_candidate_is_drawn_only_after_all_geometry_constraints_pass(self):
        current = {
            "graph_hint": "三角形", "conditions_text": "起飞点到避让点，再到1号点",
            "requirements_text": "闭合三角形航线", "suggested_text": "",
        }
        diagram = {
            "source": "vision_model", "shape": "三角形",
            "nodes": ["1", "2", "3"],
            "geometry_edges": [["1", "2"], ["2", "3"], ["3", "1"]],
            "flight_order": ["1", "2", "3"],
            "flight_edges": [["1", "2"], ["2", "3"], ["3", "1"]],
            "flight_closed": True,
        }
        question = {"id": 987654324, "imported": True, **current,
                    "reference_source": {"condition_assist": {
                        "status": "review", "solver_usable": True,
                        "diagram_model": diagram, "source_fields": current,
                    }}}
        spec = {
            "shape": "三角形", "nodes": ["1", "2", "3"],
            "graph_source": "vision_model", "closed": True,
            "geometry_edges": diagram["geometry_edges"],
            "flight_edges": diagram["flight_edges"],
            "distance_constraints": [
                {"a": "1", "b": "2", "value": 100},
                {"a": "2", "b": "3", "value": 100},
                {"a": "3", "b": "1", "value": 100},
            ],
            "segment_constraints": [
                {"a": "A", "b": "H", "bearing": 0, "distance": 50},
                {"a": "1", "b": "A", "bearing": 0, "distance": 50},
            ],
            "coordinates": {}, "equal_distance_constraints": [],
            "midpoint_perpendicular_constraints": [], "minimum_spacing": None,
            "bearing_constraints": [], "direction_relations": [],
            "vertex_ray_angles": [], "angle_constraints": [],
            "no_figure_crossing": False, "collinear_constraints": [],
            "source_text": "闭合三角形航线",
        }
        points = {
            "H": {"x": 0, "y": -100}, "A": {"x": 0, "y": -50},
            "1": {"x": 0, "y": 0}, "2": {"x": 100, "y": 0},
            "3": {"x": 50, "y": 86.6025403784},
        }
        result = {"status": "NEEDS_REVIEW", "spec": spec,
                  "candidate_points": points, "candidate": True,
                  "candidate_geometry_edges": diagram["geometry_edges"],
                  "candidate_flight_edges": diagram["flight_edges"]}

        checked = RE._apply_assist_review_label(question, result)
        self.assertTrue(checked["candidate"])
        self.assertTrue(checked["candidate_constraints_passed"])
        self.assertEqual(checked["candidate_access_edges"], [["H", "A"], ["A", "1"]])

        bad = dict(result)
        bad["spec"] = dict(spec, angle_constraints=[{"vertex": "1", "value": 90}])
        checked_bad = RE._apply_assist_review_label(question, bad)
        self.assertFalse(checked_bad["candidate"])
        self.assertFalse(checked_bad["candidate_constraints_passed"])
        self.assertEqual(checked_bad["candidate_points"], {})


if __name__ == "__main__":
    unittest.main()
