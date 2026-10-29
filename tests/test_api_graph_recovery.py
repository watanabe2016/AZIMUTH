"""答案文本与航图字段兼容，以及旧缓存无图时的无网络恢复。"""
import copy
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import vision_assist as VA
from PySide6.QtWidgets import QApplication


class ApiGraphRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cached_numeric_legs_do_not_fabricate_model_graph(self):
        answer = {"summary": "已计算", "flight_legs": [
            {"from": "H", "to": "1", "bearing_deg": 90, "distance_m": 40},
            {"from": "1", "to": "2", "bearing_deg": 0, "distance_m": 30}],
            "route_map": {"points": {}}}
        before = copy.deepcopy(answer)
        reference = VA.direct_answer_reference(answer)
        self.assertIsNone(reference)
        self.assertEqual(answer, before)

    def test_nested_route_map_is_not_discarded(self):
        parsed = {"solution": {"summary": "已计算", "route_map": {
            "points": {"1": {"x": 20, "y": 30}, "2": {"x": 60, "y": 40}},
            "geometry_edges": [["1", "2"]], "flight_edges": [["1", "2"]]}}}
        answer = VA._normalize_direct_answer(parsed, "test", None)
        self.assertEqual(len(answer["route_map"]["points"]), 2)
        self.assertEqual(answer["route_map"]["flight_edges"], [["1", "2"]])

    def test_geographic_coordinates_are_not_locally_projected_into_model_graph(self):
        answer = {"coordinates": [{"point": "1", "latitude": 30, "longitude": 104},
                                  {"point": "2", "latitude": 30.001, "longitude": 104.001}],
                  "route_map": {"flight_edges": [["1", "2"]]}}
        result = VA.direct_answer_reference(answer)
        self.assertIsNone(result)

    def test_text_only_answer_does_not_fabricate_geometry(self):
        self.assertIsNone(VA.direct_answer_reference({"answer_markdown": "答案只有文字"}))

    def test_mixed_numeric_and_named_nodes_render_without_sort_error(self):
        from ref_panel import RouteMap
        widget = RouteMap()
        try:
            widget.set_solution({"status": "NEEDS_REVIEW", "candidate": True,
                "candidate_points": {"1": {"x": 0, "y": 0}, "P": {"x": 20, "y": 20}},
                "candidate_flight_edges": [["1", "P"]]})
            self.assertEqual(len(widget._pts), 2)
        finally:
            widget.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
