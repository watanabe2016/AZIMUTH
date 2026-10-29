import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import reference_experience as RX


class ReferenceExperienceTests(unittest.TestCase):
    def test_retrieves_relevant_examples_without_answer_coordinates(self):
        verified = [{
            "id": "verified-1", "trust": "user_verified", "shape": "五边形",
            "nodes": ["1", "2", "3", "4", "5"],
            "features": ["angle", "distance", "access_point"],
            "question_excerpt": "五边形；两个边长相等；起飞与避让要求",
            "condition_lines": ["五边形", "两边等长"],
            "diagram": {"shape": "五边形", "nodes": ["1", "2", "3", "4", "5"]},
            "audit_checks": ["由用户核验"], "audit_steps": ["先建立坐标锚点"],
            "points": {"H": {"lat": 1, "lon": 2}},
        }, {
            "id": "verified-2", "trust": "user_verified", "shape": "三角形",
            "nodes": ["1", "2", "3"], "features": ["distance"],
            "question_excerpt": "三角形和距离条件",
            "condition_lines": ["三角形"],
            "diagram": {"shape": "三角形", "nodes": ["1", "2", "3"]},
            "audit_checks": ["由用户核验"], "audit_steps": [],
        }]
        with patch.object(RX, "_runtime_verified_examples", return_value=verified):
            examples = RX.retrieve_examples(
                "五边形。起飞点经纬度，1-2=2-3=92米，∠1=∠2=90°，航向相反，避让点",
                {"shape": "五边形", "nodes": ["1", "2", "3", "4", "5"]},
                limit=3)
        self.assertEqual(len(examples), 2)
        self.assertTrue(all(item["diagram"]["shape"] == "五边形"
                            for item in examples if item["case_id"] == "verified-1"))
        self.assertTrue(all("points" not in item for item in examples))
        self.assertTrue(all(item["trust"] == "user_verified" for item in examples))
        self.assertTrue(any("angle" in item.get("features", [])
                            for item in examples))

    def test_no_static_or_unverified_examples_are_returned(self):
        with patch.object(RX, "_runtime_verified_examples", return_value=[]):
            self.assertEqual(RX.retrieve_examples("五边形、边长 90 米"), [])

    def test_domain_rules_encode_the_users_route_and_wind_conventions(self):
        rules = "\n".join(RX.domain_rules())
        self.assertIn("起飞点→避让点→1号点", rules)
        self.assertIn("正左侧风加 90°", rules)
        self.assertIn("正右侧风减 90°", rules)
        self.assertIn("两个坐标锚点", rules)


if __name__ == "__main__":
    unittest.main()
