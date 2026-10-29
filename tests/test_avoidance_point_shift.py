# -*- coding: utf-8 -*-
"""验证空域与避让题的自动判题航点顺延。"""
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import judge as JG
import linkgcs as LG
import qtag as QT


class AvoidancePointShiftTests(unittest.TestCase):
    def setUp(self):
        self.answer = {"points": {
            "避让点": {"lat": 0.0, "lon": 0.0},
            "图形1号点": {"lat": 0.0, "lon": 0.0001},
            "图形2号点": {"lat": 0.0, "lon": 0.0002},
        }}
        # 前两个坐标故意交错，确认关键词题按航点编号而非最近距离对齐。
        self.task = {"points": [
            {"lat": 0.0, "lon": 0.0001},
            {"lat": 0.0, "lon": 0.0},
            {"lat": 0.0, "lon": 0.0002},
        ]}

    def test_keywords_are_detected_across_question_fields(self):
        self.assertTrue(QT.requires_avoidance_point_shift({
            "requirements_text": "由于空域限制，需要绕开限制区"
        }))
        self.assertTrue(QT.requires_avoidance_point_shift({
            "suggested_text": "起飞后先到过渡点，再进入图形航线"
        }))
        self.assertFalse(QT.requires_avoidance_point_shift({
            "conditions_text": "按图形点规划普通航线"
        }))

    def test_avoidance_point_uses_first_waypoint_and_shifts_figure_points(self):
        question = {"requirements_text": "空域限制，设置避让点"}
        _, details = LG.align_points(self.answer, self.task, q=question)
        by_name = {item["name"]: item for item in details}
        self.assertEqual(
            [by_name[name]["src_idx"] for name in
             ("避让点", "图形1号点", "图形2号点")],
            [1, 2, 3],
        )

    def test_ordinary_question_keeps_nearest_point_matching(self):
        _, details = LG.align_points(
            self.answer, self.task,
            q={"conditions_text": "普通图形点航线"},
        )
        by_name = {item["name"]: item for item in details}
        self.assertEqual(
            [by_name[name]["src_idx"] for name in
             ("避让点", "图形1号点", "图形2号点")],
            [2, 1, 3],
        )

    def test_graph_points_shift_even_when_answer_has_no_named_avoidance_point(self):
        answer = {"points": {
            "图形1号点": {"lat": 0.0, "lon": 0.0001},
            "图形2号点": {"lat": 0.0, "lon": 0.0002},
        }}
        _, details = LG.align_points(
            answer, self.task, q={"conditions_text": "禁飞区域需绕行"}
        )
        by_name = {item["name"]: item for item in details}
        self.assertEqual(by_name["图形1号点"]["src_idx"], 2)
        self.assertEqual(by_name["图形2号点"]["src_idx"], 3)

    def test_judge_task_passes_question_into_alignment(self):
        question = {"requirements_text": "空域限制，设置避让点"}
        with mock.patch.object(JG, "judge", return_value={
            "points": [], "params": [], "extras": []
        }):
            _, meta = LG.judge_task(question, self.task, ans=self.answer)
        by_name = {item["name"]: item for item in meta["details"]}
        self.assertEqual(by_name["避让点"]["src_idx"], 1)
        self.assertEqual(by_name["图形1号点"]["src_idx"], 2)


if __name__ == "__main__":
    unittest.main()
