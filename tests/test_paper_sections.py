# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from paper_sections import paper_sections


class PaperSectionTests(unittest.TestCase):
    def test_numbered_location_wind_and_avoidance_stay_in_conditions(self):
        result = paper_sections({
            "conditions_text": (
                "一、起飞点经纬度：N 38°08′19″，E 115°21′01″\n"
                "二、因空域限制，先向 NE 飞行 380 米，再向 NW 飞行 520 米。\n"
                "三、当地风向：SW，航点 3→2 航向为逆风"),
        })
        self.assertEqual(result["conditions_text"].count("\n"), 2)
        self.assertIn("起飞点经纬度", result["conditions_text"])
        self.assertIn("风向", result["conditions_text"])
        self.assertEqual(result["requirements_text"], "")

    def test_geometry_and_suggestions_are_separated(self):
        result = paper_sections({
            "conditions_text": (
                "（一）规划闭合六边形航线，边长 300 米。\n"
                "（二）a 建议为 300 米，水平速度建议为 4m/s。\n"
                "起飞点坐标：N 38°08′19″，E 115°21′01″"),
        })
        self.assertIn("闭合六边形", result["requirements_text"])
        self.assertIn("建议为", result["suggested_text"])
        self.assertIn("起飞点坐标", result["conditions_text"])


if __name__ == "__main__":
    unittest.main()
