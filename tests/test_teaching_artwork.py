"""检查教学标注保留模型数据，缺失时不臆造角度。"""
import copy
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from api_teaching_artwork import teaching_svg, artwork_palette
import vision_assist as VA


def example():
    return {"points": {"1": {"x": 0, "y": 0}, "2": {"x": -60, "y": 60},
                       "3": {"x": -80, "y": -80}, "H": {"x": 30, "y": 80}},
            "north_up": True, "geometry_edges": [["1", "2"], ["2", "3"], ["3", "1"]],
            "flight_edges": [["1", "2"], ["2", "3"], ["3", "1"]],
            "access_edges": [["H", "2"]],
            "flight_legs": [{"from": "H", "to": "2", "distance_m": 85},
                            {"from": "1", "to": "3", "bearing_deg": 225}],
            "annotations": [{"type": "angle", "from": "2", "vertex": "1", "to": "3", "value_deg": 90},
                            {"type": "angle", "from": "1", "vertex": "2", "to": "H", "value_deg": 60}]}


class TeachingArtworkTests(unittest.TestCase):
    def test_theme_palette_and_readability_in_both_appearances(self):
        import theme as T
        from PySide6.QtGui import QColor
        previous = T.app_theme, T.mode
        palettes = []
        def brightness(value):
            channels = [n/255 for n in QColor(value).getRgb()[:3]]
            return sum((c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4)*w
                       for c, w in zip(channels, (.2126, .7152, .0722)))
        try:
            for theme in ("default", "chiyo", "anna", "industrial"):
                T.set_app_theme(theme)
                for mode in ("light", "dark"):
                    T.apply(mode)
                    palette = artwork_palette()
                    palettes.append(palette)
                    self.assertEqual(palette["canvas"], QColor(T.P["shell"]).name())
                    for key in ("primary", "access", "ink"):
                        fg, bg = brightness(palette[key]), brightness(palette["canvas"])
                        self.assertGreaterEqual((max(fg, bg)+.05)/(min(fg, bg)+.05), 4.5)
                    svg = teaching_svg(example())
                    self.assertIn(f'stroke="{palette["primary"]}"', svg)
                    self.assertIn(f'fill="{palette["canvas"]}"', svg)
            self.assertGreater(len({p["primary"] for p in palettes}), 3)
        finally:
            T.set_app_theme(previous[0])
            T.apply(previous[1])

    def test_open_chart_recolors_without_changing_original(self):
        from PySide6.QtWidgets import QApplication
        self.__class__.app = QApplication.instance() or QApplication([])
        from ref_panel import RouteMap
        import theme as T
        previous = T.app_theme, T.mode
        widget = RouteMap()
        data = example()
        data.update(api_direct=True, route_svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" fill="white"/></svg>')
        try:
            T.set_app_theme("default")
            T.apply("light")
            widget.set_solution(data)
            widget.resize(900, 720)
            first = widget.grab().toImage()
            T.set_app_theme("chiyo")
            T.apply("dark")
            second = widget.grab().toImage()
            self.assertNotEqual(first, second)
            self.assertEqual(widget._artwork_palette, artwork_palette())
            self.assertEqual(widget._display_result["points"], data["points"])
            self.assertEqual(widget._display_result["route_svg"], data["route_svg"])
            widget.toggle_artwork_mode()
            self.assertTrue(widget._show_model_original)
        finally:
            widget.deleteLater()
            T.set_app_theme(previous[0])
            T.apply(previous[1])

    def test_reference_details_without_mutating_model(self):
        result = example()
        before = copy.deepcopy(result)
        svg = teaching_svg(result)
        root = ET.fromstring(svg)
        text = " ".join(root.itertext())
        for value in ("起飞点 H", "85 m", "60°", "1→3  SW 225°", "N"):
            self.assertIn(value, text)
        self.assertIn(f'fill="{artwork_palette()["fill"]}"', svg)
        self.assertIn('stroke-dasharray="9 7"', svg)
        self.assertIn(f'stroke="{artwork_palette()["ink"]}" stroke-width="2.5"', svg)
        self.assertEqual(result, before)

    def test_missing_angles_not_inferred_and_other_shape_supported(self):
        result = example()
        result["annotations"] = []
        result["flight_legs"] = []
        self.assertNotIn("°", teaching_svg(result))
        result["points"]["4"] = {"x": 80, "y": 40}
        result["geometry_edges"] = [["1", "2"], ["2", "3"], ["3", "4"], ["4", "1"]]
        self.assertIn(f'fill="{artwork_palette()["fill"]}"', teaching_svg(result))

    def test_angle_fields_survive_normalization(self):
        raw = {"solution": {"markdown": "原文", "flight_legs": example()["flight_legs"]},
               "route_map": example()}
        answer = VA._normalize_direct_answer(raw, "test", None)
        reference = VA.direct_answer_reference(answer)
        self.assertEqual(reference["annotations"], raw["route_map"]["annotations"])
