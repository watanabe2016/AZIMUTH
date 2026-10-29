import sys
import tempfile
import unittest
from pathlib import Path

os_env = __import__("os").environ
os_env.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import diagram_vision as DV
import vision_assist as VA


class DiagramShapeRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt_app = QApplication.instance() or QApplication([])

    def _image_and_words(self, shape):
        from PySide6.QtCore import QPointF, QRectF, Qt
        from PySide6.QtGui import QImage, QPainter, QPen

        points_by_shape = {
            "等边三角形": [(360, 145), (160, 510), (560, 510)],
            "直角三角形": [(170, 170), (170, 520), (550, 520)],
            "梯形": [(190, 190), (530, 190), (600, 520), (120, 520)],
            "五边形": [(360, 130), (570, 290), (500, 530),
                       (220, 530), (150, 290)],
            "蝴蝶形": [(190, 190), (530, 190), (190, 520), (530, 520)],
            "六边形": [(360, 120), (550, 240), (550, 450),
                       (360, 570), (170, 450), (170, 240)],
            "扫描航线": [(160, 190), (560, 190), (560, 300),
                        (160, 300), (160, 410), (560, 410)],
            "圆形": [(360, 180), (540, 360), (360, 540), (180, 360)],
        }
        points = points_by_shape[shape]
        image = QImage(720, 720, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(Qt.GlobalColor.black, 4))
        if shape == "圆形":
            painter.drawEllipse(QRectF(180, 180, 360, 360))
        elif shape == "扫描航线":
            for first, second in zip(points, points[1:]):
                painter.drawLine(QPointF(*first), QPointF(*second))
        else:
            order = points + ([points[0]] if shape == "蝴蝶形" else [])
            for first, second in zip(order, order[1:]):
                painter.drawLine(QPointF(*first), QPointF(*second))
            if shape != "蝴蝶形":
                painter.drawLine(QPointF(*points[-1]), QPointF(*points[0]))
        painter.end()

        words = []
        for index, (x, y) in enumerate(points, 1):
            words.append({"text": str(index), "x": x - 10, "y": y - 10,
                          "width": 20, "height": 20, "confidence": .99})
        words.append({"text": "1、航线条件", "x": 25, "y": 650,
                      "width": 180, "height": 22, "confidence": .99})
        return image, words

    def test_local_image_recognizer_covers_all_eight_shapes(self):
        with tempfile.TemporaryDirectory() as folder:
            for shape in ("等边三角形", "直角三角形", "梯形", "五边形",
                          "蝴蝶形", "六边形", "扫描航线", "圆形"):
                with self.subTest(shape=shape):
                    image, words = self._image_and_words(shape)
                    path = Path(folder) / (shape + ".png")
                    self.assertTrue(image.save(str(path)))
                    result = DV.analyze(path, words)
                    self.assertEqual(result.get("status"), "ready", result)
                    self.assertEqual(result.get("shape"), shape, result)
                    self.assertEqual(len(result.get("nodes", [])),
                                     len(self._image_and_words(shape)[1]) - 1)
                    self.assertGreaterEqual(len(result.get("geometry_edges", [])), 3)

    def test_local_image_recognizer_reports_the_right_angle_vertex(self):
        with tempfile.TemporaryDirectory() as folder:
            image, words = self._image_and_words("直角三角形")
            path = Path(folder) / "right_triangle.png"
            self.assertTrue(image.save(str(path)))
            result = DV.analyze(path, words)

        self.assertEqual(result.get("status"), "ready", result)
        self.assertEqual(result.get("shape"), "直角三角形", result)
        self.assertEqual(result.get("right_angle_vertex"), "2", result)

    def test_local_labels_survive_a_missing_middle_number(self):
        words = [
            {"text": "1", "x": 100, "y": 100, "width": 20,
             "height": 20, "confidence": .99},
            {"text": "3", "x": 500, "y": 100, "width": 20,
             "height": 20, "confidence": .99},
            {"text": "4", "x": 300, "y": 400, "width": 20,
             "height": 20, "confidence": .99},
            {"text": "1、航线条件", "x": 20, "y": 600, "width": 180,
             "height": 20, "confidence": .99},
        ]
        labels = DV._point_labels(words, 700)
        self.assertEqual([item[0] for item in labels], ["1", "3", "4"])

    def test_scan_route_is_recovered_when_small_point_labels_are_missed(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QImage, QPainter, QPen

        image = QImage(900, 1146, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        points = [(322, 504), (92, 504), (92, 549), (322, 549),
                  (322, 594), (92, 594), (92, 638), (322, 638),
                  (322, 682), (92, 682), (92, 728), (322, 728)]
        painter = QPainter(image)
        painter.setPen(QPen(Qt.GlobalColor.black, 3))
        for first, second in zip(points, points[1:]):
            painter.drawLine(QPointF(*first), QPointF(*second))
        painter.end()
        words = [
            {"text": "1. 扫描航线高度", "x": 419, "y": 529,
             "width": 170, "height": 22, "confidence": .99},
            {"text": "☆起飞点（返航点）", "x": 224, "y": 797,
             "width": 102, "height": 28, "confidence": .95},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "scan.png"
            self.assertTrue(image.save(str(path)))
            result = DV.analyze(
                path, words,
                shape_hint="扫描航线；1点位于H点东偏北方向，1点位于11点的SE方向")

        self.assertEqual(result.get("status"), "ready", result)
        self.assertEqual(result.get("shape"), "扫描航线", result)
        self.assertEqual(len(result.get("nodes", [])), 12, result)
        self.assertEqual(len(result.get("geometry_edges", [])), 11, result)
        point_one = result["point_positions"]["1"]
        point_eleven = result["point_positions"]["11"]
        self.assertGreater(point_one["x"], point_eleven["x"], result)
        self.assertGreater(point_one["y"], point_eleven["y"], result)

    def test_trapezoid_route_is_recovered_without_ocr_vertex_numbers(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QImage, QPainter, QPen

        image = QImage(900, 1114, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        corners = [(88, 535), (315, 535), (239, 667), (88, 667)]
        painter = QPainter(image)
        painter.setPen(QPen(Qt.GlobalColor.black, 3))
        for first, second in zip(corners, corners[1:] + corners[:1]):
            painter.drawLine(QPointF(*first), QPointF(*second))
        painter.end()
        words = [
            {"text": "1. 该图形航线高度为", "x": 336, "y": 489,
             "width": 164, "height": 22, "confidence": .99},
            {"text": "☆起飞点（返航点）", "x": 177, "y": 713,
             "width": 95, "height": 21, "confidence": .95},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "trapezoid.png"
            self.assertTrue(image.save(str(path)))
            result = DV.analyze(
                path, words,
                shape_hint="梯形；1到2（40m），3到4（100m）；H点在1点NE方向；∠4=45°")

        self.assertEqual(result.get("status"), "ready", result)
        self.assertEqual(result.get("shape"), "梯形", result)
        self.assertEqual(result.get("nodes"), ["1", "2", "3", "4"], result)
        self.assertEqual(len(result.get("geometry_edges", [])), 4, result)
        point_one = result["point_positions"]["1"]
        point_four = result["point_positions"]["4"]
        self.assertGreater(point_one["x"], .22, result)
        self.assertGreater(point_one["y"], .55, result)
        self.assertGreater(point_four["x"], point_one["x"], result)
        self.assertLess(point_four["y"], point_one["y"], result)

    def test_trapezoid_numbering_uses_explicit_point_heading(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QImage, QPainter, QPen

        image = QImage(900, 1114, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        corners = [(88, 535), (315, 535), (239, 667), (88, 667)]
        painter = QPainter(image)
        painter.setPen(QPen(Qt.GlobalColor.black, 3))
        for first, second in zip(corners, corners[1:] + corners[:1]):
            painter.drawLine(QPointF(*first), QPointF(*second))
        painter.end()

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "heading_trapezoid.png"
            self.assertTrue(image.save(str(path)))
            result = DV.analyze(
                path, [],
                shape_hint="梯形；1到2（40m），3到4（100m）；"
                           "1点在4点的为航向W；∠4=45°")

        self.assertEqual(result.get("status"), "ready", result)
        self.assertEqual(result.get("shape"), "梯形", result)
        point_one = result["point_positions"]["1"]
        point_four = result["point_positions"]["4"]
        self.assertLess(point_one["x"], point_four["x"], result)
        self.assertLess(abs(point_one["y"] - point_four["y"]), 3.0, result)
        self.assertEqual(DV._route_bearing_hints("1点在4点的为航向W"),
                         [("4", "1", 270.0)])

    def test_model_diagram_sanitizer_accepts_all_eight_shape_types(self):
        cases = [
            ("等边三角形", ["1", "2", "3"], [["1", "2"], ["2", "3"], ["3", "1"]]),
            ("直角三角形", ["1", "2", "3"], [["1", "2"], ["2", "3"], ["3", "1"]]),
            ("梯形", ["1", "2", "3", "4"], [["1", "2"], ["2", "3"], ["3", "4"], ["4", "1"]]),
            ("五边形", ["1", "2", "3", "4", "5"], [["1", "2"], ["2", "3"], ["3", "4"], ["4", "5"], ["5", "1"]]),
            ("蝴蝶形", ["1", "2", "3", "4"], [["1", "2"], ["2", "3"], ["3", "4"], ["4", "1"]]),
            ("六边形", ["1", "2", "3", "4", "5", "6"], [["1", "2"], ["2", "3"], ["3", "4"], ["4", "5"], ["5", "6"], ["6", "1"]]),
            ("扫描航线", ["1", "2", "3", "4", "5"], [["1", "2"], ["2", "3"], ["3", "4"], ["4", "5"]]),
            ("圆形", ["1", "2", "3", "4"], [["1", "2"], ["2", "3"], ["3", "4"], ["4", "1"]]),
        ]
        for shape, nodes, edges in cases:
            with self.subTest(shape=shape):
                result = VA._sanitize_diagram_model({
                    "shape": shape, "nodes": nodes, "geometry_edges": edges,
                    "flight_order": nodes,
                    "flight_closed": shape not in ("扫描航线",),
                    "point_positions": {
                        node: {"x": 100 + index * 100, "y": 200 + index * 40}
                        for index, node in enumerate(nodes)},
                })
                self.assertIsNotNone(result)
                self.assertEqual(result["shape"], shape)
                self.assertEqual(result["nodes"], nodes)
                self.assertEqual(result["geometry_edges"], edges)
                self.assertTrue(result["topology_valid"], result["topology_errors"])

    def test_incomplete_model_shape_uses_verified_local_graph_as_fallback(self):
        nodes = ["1", "2", "3", "4", "5"]
        edges = [["1", "2"], ["2", "3"], ["3", "4"], ["4", "5"]]
        fallback = {
            "source": "local_cv", "status": "ready", "shape": "扫描航线",
            "nodes": nodes, "geometry_edges": edges,
            "point_positions": {
                node: {"x": index * 120.0, "y": index * 35.0}
                for index, node in enumerate(nodes)},
        }
        result = VA._sanitize_diagram_model(
            {"shape": "扫描航线", "geometry_edges": [["1", "2"]]},
            fallback_diagram=fallback)
        self.assertEqual(result["shape"], "扫描航线")
        self.assertEqual(result["nodes"], nodes)
        self.assertEqual(result["geometry_edges"], edges)
        self.assertTrue(result["topology_valid"], result["topology_errors"])
        missing_result = VA._sanitize_diagram_model(None, fallback_diagram=fallback)
        self.assertEqual(missing_result["shape"], "扫描航线")
        self.assertEqual(missing_result["geometry_edges"], edges)


if __name__ == "__main__":
    unittest.main()
