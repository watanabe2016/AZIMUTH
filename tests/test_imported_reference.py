import math
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import answers
import diagram_vision
import judge
import ocr
import reference_engine as re
import numeric_reference as nr


QUESTION_TEXT = """1、该任务航线为如图五边形，航线高度为170m
2、该图形航线长度为：1235构成正方形，边长为90米，∠1=∠2=∠4=90°
3、起飞点/返航点要求：起飞点到3点方向为西偏南37°距离120米处。
4、水平速度为：5m/s
5、垂直速度为：2m/s
6、航向为(或位置要求)：∠H34=135°，∠H32=75°
7、转弯模式为：停止转弯，各点停留时间7秒
8、是否循环执行：任务航线闭合不循环，任务完成后原地悬停
9、起飞点(返航点)经纬度:118.3776°35.1884°
10、航线高度为起飞点高度的一半。"""


class ImportedReferenceTests(unittest.TestCase):
    def question(self):
        return {"id": 441, "imported": True, "source": "9.18 山东05",
                "graph_hint": "五边形", "conditions_text": QUESTION_TEXT,
                "requirements_text": "", "suggested_text": ""}

    def test_imported_right_trapezoid_solves_ray_angles_and_real_lengths(self):
        diagram = {
            "source": "local_cv", "status": "ready", "shape": "梯形",
            "nodes": ["1", "2", "3", "4"],
            "geometry_edges": [["1", "2"], ["2", "3"],
                               ["3", "4"], ["4", "1"]],
            "route_order": ["1", "2", "3", "4"],
            "validated_cycle": True, "topology_valid": True,
            "parallel_edge_pairs": [[["1", "2"], ["3", "4"]]],
            "point_positions": {
                "1": {"x": 300, "y": 360},
                "2": {"x": 550, "y": 360},
                "3": {"x": 650, "y": 150},
                "4": {"x": 300, "y": 150},
            },
        }
        question = {
            "id": 987654442, "imported": True, "graph_hint": "梯形",
            "conditions_text": (
                "该任务航线如图直角梯形，航线高度75米。\n"
                "该图形航线长度为1-2=82m，2-3=186m。\n"
                "起飞点到3点的东偏北41°，距离55米。\n"
                "航向要求：∠H34=73°，∠H32=103°。\n"
                "任务航线闭合不循环，起飞点经纬度：104.6415°，30.8679°。"),
            "requirements_text": "", "suggested_text": "",
            "reference_source": {"diagram_model": diagram},
        }
        result = re.solve_reference(question)
        self.assertEqual(result["status"], "READY", result.get("reason"))
        points = result["points"]
        xy = {name: (point["x"], point["y"])
              for name, point in points.items()}
        self.assertAlmostEqual(math.dist(xy["1"], xy["2"]), 82, places=6)
        self.assertAlmostEqual(math.dist(xy["2"], xy["3"]), 186, places=6)
        self.assertAlmostEqual(math.dist(xy["H"], xy["3"]), 55, places=6)
        self.assertAlmostEqual(re._angle_of(xy["H"], xy["3"], xy["4"]),
                               73, places=6)
        self.assertAlmostEqual(re._angle_of(xy["H"], xy["3"], xy["2"]),
                               103, places=6)
        self.assertAlmostEqual(re._angle_of(xy["2"], xy["1"], xy["4"]),
                               90, places=6)
        self.assertAlmostEqual(re._angle_of(xy["1"], xy["4"], xy["3"]),
                               90, places=6)
        self.assertEqual(result["access_edges"], [["H", "1"]])

    def test_imported_condition_text_supplies_parameter_values(self):
        scan = {
            "id": 987654440, "imported": True,
            "graph_hint": "扫描航线", "requirements_text": "",
            "suggested_text": "",
            "conditions_text": (
                "规划扫描航线，航线长度为a，航线间隔为b，"
                "航线相对地面高度为c，水平速度为d，垂直速度为e。"
                "a值为300米，b值为60米，c值为200米，"
                "d值为10m/s，e值为5m/s。"),
        }
        scan_spec = re.extract_question_spec(scan)
        self.assertEqual(scan_spec["parameters"].get("a"), 300)
        self.assertEqual(scan_spec["parameters"].get("b"), 60)
        self.assertEqual(scan_spec["scan_length"], 300)
        self.assertEqual(scan_spec["scan_spacing"], 60)

        hexagon = {
            "id": 987654441, "imported": True,
            "graph_hint": "六边形", "requirements_text": "",
            "suggested_text": "",
            "conditions_text": "规划一个闭合六边形，边长为a。a值为300米。",
        }
        hexagon_spec = re.extract_question_spec(hexagon)
        self.assertEqual(hexagon_spec["parameters"].get("a"), 300)

    def test_imported_square_and_edge_equalities_are_solver_constraints(self):
        text = "五边形，1235构成正方形，12边长150米，1-4=2-3，∠2H4=55°，∠H42=55°。"
        square_equalities = re._parse_equal_distances("1235构成正方形")
        edge_equalities = re._parse_equal_distances("1-4=2-3")
        self.assertEqual(len(square_equalities), 3)
        self.assertEqual(edge_equalities[0]["first"], ["1", "4"])
        self.assertEqual(edge_equalities[0]["second"], ["2", "3"])

        q = {"id": 987654442, "imported": True, "graph_hint": "五边形",
             "conditions_text": text, "requirements_text": "",
             "suggested_text": ""}
        spec = re.extract_question_spec(q)
        self.assertEqual(len([item for item in spec["vertex_ray_angles"]
                              if item["source"] == "1235构成正方形"]), 4)
        self.assertTrue(any(item["a"] == "2" and item["vertex"] == "H" and
                            item["b"] == "4" and item["value"] == 55
                            for item in spec["vertex_ray_angles"]))

    def test_angle_degrees_are_not_misread_as_takeoff_bearing(self):
        question = {
            "id": 987654449, "imported": True,
            "conditions_text": (
                "起飞点/返航点要求：起飞点到1点距离50m，"
                "∠H12=90°，∠H14=120°"),
            "requirements_text": "", "suggested_text": "",
        }
        spec = re.extract_question_spec(question)
        self.assertEqual(spec["segment_constraints"], [])
        self.assertTrue(any(item["a"] == "H" and item["vertex"] == "1" and
                            item["b"] == "2" and item["value"] == 90
                            for item in spec["vertex_ray_angles"]))

    def test_imported_local_cv_right_triangle_adds_detected_right_angle(self):
        diagram = {
            "source": "local_cv", "status": "ready", "shape": "直角三角形",
            "nodes": ["1", "2", "3"],
            "geometry_edges": [["1", "2"], ["2", "3"], ["3", "1"]],
            "route_order": ["1", "2", "3"], "validated_cycle": True,
            "topology_valid": True, "right_angle_vertex": "2",
            "point_positions": {
                "1": {"x": 100, "y": 100},
                "2": {"x": 100, "y": 500},
                "3": {"x": 500, "y": 500},
            },
        }
        question = {
            "id": 987654443, "imported": True,
            "graph_hint": "直角三角形", "conditions_text": "1-2边长45米",
            "requirements_text": "", "suggested_text": "",
            "reference_source": {"diagram_model": diagram},
        }

        spec = re.extract_question_spec(question)
        detected = [item for item in spec["vertex_ray_angles"]
                    if item.get("source") == "本地题图检测到直角顶点 2"]
        self.assertEqual(len(detected), 1)
        self.assertEqual((detected[0]["a"], detected[0]["vertex"],
                          detected[0]["b"], detected[0]["value"]),
                         ("1", "2", "3", 90.0))

    def test_incomplete_candidate_is_replaced_by_full_local_cv_drawing(self):
        nodes = ["1", "2", "3", "4"]
        diagram = {
            "source": "local_cv", "status": "ready", "shape": "梯形",
            "nodes": nodes,
            "geometry_edges": [["1", "2"], ["2", "3"],
                               ["3", "4"], ["4", "1"]],
            "route_order": nodes, "validated_cycle": True,
            "topology_valid": True,
            "point_positions": {
                "1": {"x": 120, "y": 500}, "2": {"x": 120, "y": 120},
                "3": {"x": 500, "y": 120}, "4": {"x": 420, "y": 500},
            },
        }
        question = {
            "id": 987654444, "imported": True, "graph_hint": "梯形",
            "conditions_text": (
                "1-2边长75米，3-4边长140米；"
                "起飞点经纬度 E120.0000° N30.0000°"),
            "requirements_text": "", "suggested_text": "",
            "reference_source": {"diagram_model": diagram},
        }
        spec = re.extract_question_spec(question)
        incomplete = {
            "status": "NEEDS_REVIEW", "candidate": True,
            "candidate_points": {"1": {"x": 0, "y": 0}},
            "candidate_geometry_edges": [["1", "2"]],
            "candidate_flight_edges": [], "spec": spec,
        }

        result = re._apply_assist_review_label(question, incomplete)
        self.assertTrue(result.get("reference_drawing_only"), result)
        self.assertEqual(set(result.get("candidate_points", {})),
                         set(nodes) | {"H"})
        self.assertEqual(len(result.get("candidate_geometry_edges", [])), 4)
        self.assertEqual(len(result.get("candidate_flight_edges", [])), 4)
        self.assertEqual(result.get("candidate_access_edges"), [["H", "1"]])

    def test_notched_square_solver_is_not_used_for_imported_answers(self):
        q = self.question()
        result = re.solve_reference(q)
        self.assertEqual(result["status"], "READY", result.get("reason"))
        pts = result["points"]
        self.assertEqual(set(pts), {"H", "1", "2", "3", "4", "5"})
        self.assertEqual(result["access_edges"], [["H", "1"]])
        self.assertAlmostEqual(math.dist((pts["H"]["x"], pts["H"]["y"]),
                                         (pts["3"]["x"], pts["3"]["y"])), 120, delta=.2)
        self.assertAlmostEqual(math.dist((pts["2"]["x"], pts["2"]["y"]),
                                         (pts["3"]["x"], pts["3"]["y"])), 90, delta=.2)
        answer = answers.derive(q)
        self.assertEqual(answer["conf"], "none")
        self.assertEqual(answer["points"], {})
        self.assertTrue(answer["api_answer_pending"])

    def test_imported_answer_module_uses_saved_api_coordinates_only(self):
        q = self.question()
        fields = {key: str(q.get(key) or "") for key in (
            "graph_hint", "conditions_text", "requirements_text", "suggested_text")}
        q["reference_source"] = {"direct_answer": {
            "source_fields": fields,
            "coordinates": [
                {"point": "H", "latitude": 30.1, "longitude": 104.1},
                {"point": "A", "latitude": 30.1001, "longitude": 104.1001},
                {"point": "1", "latitude": 30.1002, "longitude": 104.1002},
            ],
            "parameters": [{"name": "航线高度", "value": "75m"}],
        }}
        answer = answers.derive(q)
        self.assertEqual(answer["conf"], "full")
        self.assertEqual(set(answer["points"]), {"起飞点", "避让点", "图形1号点"})
        self.assertFalse(answer["api_answer_pending"])
        q["requirements_text"] += " 题面已修改"
        stale = answers.derive(q)
        self.assertEqual(stale["points"], {})
        self.assertTrue(stale["api_answer_pending"])

    def test_rapid_text_keeps_wrapped_distance_and_filters_watermark(self):
        rows = [
            ("1、该任务航线为如图五边形", 100),
            ("2、1235构成正方形，边长为90米", 140),
            ("∠1=∠2=∠4=90°", 180),
            ("3、起飞点到3点方向为西偏南37°距离", 220),
            ("120米处。", 260),
            ("4、水平速度为5m/s", 300),
        ]
        words = [{"text": text, "x": 20, "y": y, "width": 400,
                  "height": 30, "angle": 0, "confidence": .99}
                 for text, y in rows]
        words.append({"text": "斜向水印", "x": 20, "y": 245,
                      "width": 90, "height": 90, "angle": 135,
                      "confidence": .99})
        text = ocr._rapid_question_text(words)
        self.assertIn("距离120米处", text)
        self.assertIn("∠1=∠2=∠4=90°", text)
        self.assertNotIn("水印", text)

    def test_unverified_import_is_not_judged_from_partial_coordinates(self):
        q = self.question()
        q["conditions_text"] = "起飞点经纬度 N35.1884 E118.3776；图形其余条件不可辨。"
        answer = answers.derive(q)
        self.assertEqual(answer["conf"], "none")
        self.assertFalse(judge.judge(q, ans=answer)["judgeable"])

    def test_imported_heading_phrases_keep_node_direction_and_distance(self):
        heading = re._parse_position_bearings("1点在4点的为航向W")
        self.assertEqual(heading[0]["a"], "4")
        self.assertEqual(heading[0]["b"], "1")
        self.assertAlmostEqual(heading[0]["value"], 270.0)

        offset = re._parse_position_bearings("H点在1点NE偏N20°方向200米")
        self.assertEqual([(item["a"], item["b"], item["kind"])
                          for item in offset],
                         [("1", "H", "bearing"), ("1", "H", "distance")])
        self.assertAlmostEqual(offset[0]["value"], 25.0)
        self.assertAlmostEqual(offset[1]["value"], 200.0)

    def test_local_cv_trapezoid_keeps_a_drawing_draft_when_text_conflicts(self):
        text = (
            "1. 该图形航线高度为60M\n"
            "2. 该图形航线长度为1到2（40m）、3到4（100m）\n"
            "3. 起飞点/返航点要求H点在1点NE偏N20°方向200米\n"
            "4. 航向为1点在4点的为航向W，∠4=45°\n"
            "5. 2点经纬度：30.6578°110.2768°")
        fields = {"graph_hint": "梯形", "conditions_text": text,
                  "requirements_text": "", "suggested_text": ""}
        diagram = {
            "source": "local_cv", "status": "ready", "shape": "梯形",
            "nodes": ["1", "2", "3", "4"],
            "geometry_edges": [["1", "2"], ["2", "3"],
                               ["3", "4"], ["4", "1"]],
            "point_positions": {
                "1": {"x": 98.3, "y": 480.7},
                "2": {"x": 98.3, "y": 598.3},
                "3": {"x": 266.1, "y": 598.3},
                "4": {"x": 349.6, "y": 480.7}},
            "topology_valid": True,
            "parallel_edge_pairs": [[["2", "3"], ["4", "1"]]],
        }
        assist = {
            "status": "review", "solver_usable": True, "complete": True,
            "normalized_conditions": text, "uncertain_items": [],
            "condition_items": [
                {"normalized": line, "visual_evidence": line, "confidence": .99}
                for line in text.splitlines()],
            "diagram_model": diagram, "source_fields": fields,
        }
        question = {"id": 987654402, "imported": True, **fields,
                    "reference_source": {"diagram_model": diagram,
                                         "condition_assist": assist}}

        result = re.solve_reference(question)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertTrue(result.get("reference_drawing_only"))
        self.assertTrue(result.get("candidate"))
        self.assertFalse(result.get("candidate_constraints_passed"))
        self.assertEqual(set(result["candidate_points"]), {"H", "1", "2", "3", "4"})
        self.assertEqual(result["candidate_access_edges"], [["H", "1"]])
        self.assertIn("梯形条件冲突", "；".join(result["candidate_validation_errors"]))

    def test_local_cv_scan_route_builds_all_twelve_points_and_takeoff_path(self):
        nodes = [str(index) for index in range(1, 13)]
        text = (
            "1. 该扫描航线高度为100M\n"
            "2. 该扫描航线长度为1000M\n"
            "3. 该扫描航线间隔为200M\n"
            "4. 起飞点要求1位于H点东偏北100°100m方向\n"
            "5. 1点位于11点的航向为SE方向\n"
            "6. 2点经纬度：N50.7896°，E60.6541°")
        fields = {"graph_hint": "扫描航线", "conditions_text": text,
                  "requirements_text": "", "suggested_text": ""}
        positions = [(364.7, 636.6), (104.0, 636.2), (104.0, 594.7),
                     (353.3, 594.7), (353.3, 556.3), (103.8, 556.3),
                     (103.8, 517.9), (351.0, 517.9), (351.6, 479.1),
                     (103.5, 479.1), (104.2, 440.2), (364.4, 440.2)]
        edges = [[nodes[index], nodes[index + 1]]
                 for index in range(len(nodes) - 1)]
        diagram = {
            "source": "local_cv", "status": "ready", "shape": "扫描航线",
            "nodes": nodes, "geometry_edges": edges,
            "route_order": nodes, "flight_edges": edges,
            "point_positions": {
                name: {"x": x, "y": y}
                for name, (x, y) in zip(nodes, positions)},
            "topology_valid": True, "flight_closed": False,
        }
        assist = {
            "status": "ready", "solver_usable": True, "complete": True,
            "normalized_conditions": text, "uncertain_items": [],
            "condition_items": [
                {"normalized": line, "visual_evidence": line, "confidence": .99}
                for line in text.splitlines()],
            "diagram_model": diagram, "source_fields": fields,
        }
        question = {"id": 987654403, "imported": True, **fields,
                    "reference_source": {"diagram_model": diagram,
                                         "condition_assist": assist}}

        result = re.solve_reference(question)
        self.assertEqual(result["status"], "READY", result.get("reason"))
        self.assertEqual(set(result["points"]), set(nodes) | {"H"})
        self.assertEqual(len(result["flight_edges"]), 11)
        self.assertEqual(result["access_edges"], [["H", "1"]])
        self.assertAlmostEqual(result["spec"]["scan_length"], 1000.0)
        self.assertAlmostEqual(result["spec"]["scan_spacing"], 200.0)

    def test_new_triangle_answer_constraints_are_parsed_and_solved(self):
        """用用户新导入的直角三角形卡和地面站答案图校验完整航迹。"""
        nodes = ["1", "2", "3"]
        diagram = {
            "source": "local_cv", "status": "ready", "shape": "直角三角形",
            "nodes": nodes,
            "geometry_edges": [["1", "2"], ["2", "3"], ["3", "1"]],
            "route_order": nodes,
            "flight_edges": [["1", "2"], ["2", "3"], ["3", "1"]],
            "flight_closed": True, "validated_cycle": True,
            "topology_valid": True, "right_angle_vertex": "1",
            "point_positions": {
                "1": {"x": 100, "y": 500},
                "2": {"x": 500, "y": 500},
                "3": {"x": 100, "y": 100},
            },
        }
        text = (
            "1、该直角三角形航线高度为：75米\n"
            "2、该直角三角形航线边长为：1-2=45米\n"
            "3、起飞点/返航点要求：起飞点到1点80米，"
            "∠H12=40°，∠H13=130°（夹角朝向图形外侧）\n"
            "4、水平速度为：1m/s\n"
            "5、垂直速度为：3m/s\n"
            "6、航向为（或位置要求）：2点在3点的（西偏南15°）方向距离90米\n"
            "7、转弯模式为：停止转弯，各点停留时间5秒\n"
            "8、是否循环执行：任务航线闭合不循环，任务结束后原地悬停\n"
            "9、起飞点经纬度：104.4615°，30.8697°\n"
            "10、起飞点高度比航线高度低36米")
        question = {
            "id": 987654701, "imported": True,
            "graph_hint": "直角三角形", "conditions_text": text,
            "requirements_text": "", "suggested_text": "",
            "reference_source": {"diagram_model": diagram},
        }

        spec = re.extract_question_spec(question)
        self.assertTrue(any(item["a"] == "3" and item["b"] == "2" and
                            abs(item["value"] - 255.0) < 1e-6
                            for item in spec["bearing_constraints"]), spec)
        self.assertTrue(any({item["a"], item["b"]} == {"2", "3"} and
                            abs(item["value"] - 90.0) < 1e-6
                            for item in spec["distance_constraints"]), spec)
        self.assertTrue(any({item["a"], item["b"]} == {"H", "1"} and
                            abs(item["value"] - 80.0) < 1e-6
                            for item in spec["distance_constraints"]), spec)
        self.assertEqual(spec["loop_count"], 0, spec)

        result = re.solve_reference(question)
        self.assertEqual(result["status"], "READY", result.get("candidate_validation_errors"))
        self.assertEqual(result["loop_count"], 0)
        self.assertEqual(result["flight_edges"], [["1", "2"], ["2", "3"], ["3", "1"]])
        self.assertEqual(result["access_edges"], [["H", "1"]])
        points = result["points"]

        def distance(a, b):
            return math.dist((points[a]["x"], points[a]["y"]),
                             (points[b]["x"], points[b]["y"]))

        def bearing(a, b):
            return (math.degrees(math.atan2(points[b]["x"] - points[a]["x"],
                                            points[b]["y"] - points[a]["y"])) + 360) % 360

        self.assertAlmostEqual(distance("H", "1"), 80.0, delta=.2)
        self.assertAlmostEqual(distance("1", "2"), 45.0, delta=.2)
        self.assertAlmostEqual(distance("2", "3"), 90.0, delta=.2)
        self.assertAlmostEqual(distance("3", "1"), math.sqrt(90 ** 2 - 45 ** 2), delta=.2)
        self.assertAlmostEqual(bearing("H", "1"), 55.0, delta=.2)
        self.assertAlmostEqual(bearing("1", "2"), 195.0, delta=.2)
        self.assertAlmostEqual(bearing("2", "3"), 75.0, delta=.2)
        self.assertAlmostEqual(bearing("3", "1"), 285.0, delta=.2)

    def test_new_scan_answer_parses_spacing_heading_and_keeps_serpentine_open(self):
        """扫描题的闭合循环是重复任务，不应补造12→1的斜向航段。"""
        nodes = [str(index) for index in range(1, 13)]
        edges = [[nodes[index], nodes[index + 1]] for index in range(11)]
        positions = {
            "1": {"x": 800, "y": 1000}, "2": {"x": 100, "y": 1000},
            "3": {"x": 100, "y": 900}, "4": {"x": 800, "y": 900},
            "5": {"x": 800, "y": 800}, "6": {"x": 100, "y": 800},
            "7": {"x": 100, "y": 700}, "8": {"x": 800, "y": 700},
            "9": {"x": 800, "y": 600}, "10": {"x": 100, "y": 600},
            "11": {"x": 100, "y": 500}, "12": {"x": 800, "y": 500},
        }
        diagram = {
            "source": "local_cv", "status": "ready", "shape": "扫描航线",
            "nodes": nodes, "geometry_edges": edges, "route_order": nodes,
            "flight_edges": edges, "flight_closed": False,
            "topology_valid": True, "point_positions": positions,
        }
        text = (
            "1、该图形航线海拔高度为：50m\n"
            "2、该图形航线长度为100m，间隔30m\n"
            "3、起飞点/返航点要求：起飞点位于1点NE方向50m\n"
            "4、水平速度为：3m/s\n"
            "5、垂直速度为：2m/s\n"
            "6、航向为（或位置要求）：12点到2点航向310\n"
            "7、转弯模式为：停止转弯\n"
            "8、是否循环执行：闭合循环\n"
            "9、各点停留时间为：2s")
        question = {
            "id": 987654702, "imported": True,
            "graph_hint": "扫描航线", "conditions_text": text,
            "requirements_text": "", "suggested_text": "",
            "reference_source": {"diagram_model": diagram},
        }

        spec = re.extract_question_spec(question)
        self.assertEqual(spec["scan_length"], 100.0, spec)
        self.assertEqual(spec["scan_spacing"], 30.0, spec)
        self.assertTrue(any(item["a"] == "12" and item["b"] == "2" and
                            abs(item["value"] - 310.0) < 1e-6
                            for item in spec["bearing_constraints"]), spec)
        self.assertTrue(spec["closed"] is False, spec)
        self.assertEqual(spec["loop_count"], 1, spec)
        self.assertEqual(spec["flight_edges"], edges)

        result = re.solve_reference(question)
        self.assertEqual(result["status"], "READY", result.get("candidate_validation_errors"))
        self.assertEqual(result["loop_count"], 1)
        self.assertEqual(result["flight_edges"], edges)
        self.assertEqual(result["access_edges"], [["H", "1"]])
        points = result["points"]

        def distance(a, b):
            return math.dist((points[a]["x"], points[a]["y"]),
                             (points[b]["x"], points[b]["y"]))

        def bearing(a, b):
            return (math.degrees(math.atan2(points[b]["x"] - points[a]["x"],
                                            points[b]["y"] - points[a]["y"])) + 360) % 360

        self.assertAlmostEqual(bearing("12", "2"), 310.0, delta=.2)
        self.assertAlmostEqual(bearing("1", "H"), 45.0, delta=.2)
        for index in range(1, 13, 2):
            self.assertAlmostEqual(distance(str(index), str(index + 1)), 100.0, delta=.2)
        for index in range(2, 12, 2):
            self.assertAlmostEqual(distance(str(index), str(index + 1)), 30.0, delta=.2)
        self.assertAlmostEqual(distance("H", "1"), 50.0, delta=.2)

    def test_imported_visual_card_produces_only_a_fully_checked_candidate(self):
        text = (
            "1、该任务航线为如图，航线高度为80m\n"
            "2、该航线12=100m,34=140m,14=80m\n"
            "3、起飞点条件:∠H12=60°∠H23=30°\n"
            "4、水平速度:5m/s,垂直速度:3m/s\n"
            "5、航向为:4点在3点的南偏西35°方向。\n"
            "6、转弯模式:停止转弯,停留5秒。\n"
            "7、循环条件:闭合不循环,完成任务后在H点悬停\n"
            "8、起飞点经纬度:E:104.4615°,N:30.8697°\n"
            "9、起飞点高度为航线高度一半。")
        fields = {"graph_hint": "梯形", "conditions_text": text,
                  "requirements_text": "", "suggested_text": ""}
        items = [line for line in text.splitlines()]
        diagram = {
            "source": "vision_model", "shape": "梯形",
            "nodes": ["1", "2", "3", "4"],
            "geometry_edges": [["1", "2"], ["1", "4"], ["2", "3"], ["3", "4"]],
            "flight_order": ["1", "2", "3", "4"],
            "flight_edges": [["1", "2"], ["2", "3"], ["3", "4"], ["4", "1"]],
            "flight_closed": True,
            "point_positions": {
                "1": {"x": 330.3, "y": 379.9},
                "2": {"x": 152.2, "y": 374.0},
                "3": {"x": 148.5, "y": 170.3},
                "4": {"x": 417.9, "y": 169.3},
            },
            "confidence": 1.0, "warnings": ["视觉图形结构尚未经过人工核对，路线仅作参考"],
            "topology_errors": [], "topology_valid": True,
            "geometry_source": "local_cv", "geometry_validated": True,
            "geometry_confidence": .988,
            "parallel_edge_pairs": [[["1", "2"], ["3", "4"]]],
        }
        assist = {
            "status": "review", "solver_usable": True, "complete": True,
            "normalized_conditions": text,
            "condition_items": [
                {"normalized": item, "visual_evidence": item, "confidence": 1.0}
                for item in items],
            "uncertain_items": ["视觉图形结构尚未经过人工核对，路线仅作参考"],
            "diagram_model": diagram, "source_fields": fields,
        }
        question = {"id": 987654399, "imported": True, **fields,
                    "reference_source": {"source_text": text,
                                         "condition_assist": assist}}

        spec = re.extract_question_spec(question)
        self.assertEqual(spec["shape"], "梯形")
        self.assertEqual(len(spec["vertex_ray_angles"]), 2)
        self.assertEqual(spec["parallel_constraints"], [
            {"first": ["1", "2"], "second": ["3", "4"],
             "source": "题图线段检测到的平行边"}])

        result = re.solve_reference(question)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertTrue(result["candidate"])
        self.assertTrue(result["candidate_constraints_passed"], result)
        self.assertFalse(result.get("candidate_validation_errors"))
        self.assertEqual(set(result["candidate_points"]), {"H", "1", "2", "3", "4"})
        self.assertEqual(result["candidate_access_edges"], [["H", "1"]])
        self.assertEqual(result["candidate_flight_edges"], diagram["flight_edges"])
        self.assertIn("未被题面唯一锁定", result["candidate_note"])
        cycle = nr._diagram_cycle_order(spec["nodes"], spec["geometry_edges"])
        layout = {name: (float(diagram["point_positions"][name]["x"]),
                         -float(diagram["point_positions"][name]["y"]))
                  for name in spec["nodes"]}
        candidate = {name: (float(result["candidate_points"][name]["x"]),
                            float(result["candidate_points"][name]["y"]))
                     for name in spec["nodes"]}
        self.assertGreater(nr._polygon_signed_area(cycle, layout) *
                           nr._polygon_signed_area(cycle, candidate), 0,
                           "本地图形候选不能选成与题卡相反的镜像")
        self.assertLessEqual(result["candidate_layout_angle_error_degrees"], 45)

    def test_real_imported_trapezoid_card_refines_vlm_rectangle_before_solving(self):
        image = ROOT / "tests" / "fixtures" / "trapezoid_card.jpg"
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
        vlm_diagram = {
            "source": "vision_model", "shape": "四边形",
            "nodes": ["1", "2", "3", "4"],
            "geometry_edges": [["1", "2"], ["2", "3"],
                               ["3", "4"], ["4", "1"]],
            "point_positions": {
                "1": {"x": 500, "y": 300},
                "2": {"x": 200, "y": 300},
                "3": {"x": 200, "y": 200},
                "4": {"x": 500, "y": 200},
            },
            "flight_order": [], "flight_edges": [], "flight_closed": None,
            "confidence": 1.0, "warnings": ["视觉模型草图待校验"],
            "topology_errors": [], "topology_valid": False,
        }
        refined = diagram_vision.refine_partial_polygon(
            image, words, [1080, 1283], vlm_diagram)
        self.assertIsNotNone(refined)
        self.assertEqual(refined["geometry_source"], "local_cv_hough")
        self.assertEqual(refined["shape"], "梯形")

        text = (
            "1、该任务航线为如图，航线高度为80m\n"
            "2、该航线12=100m，34=140m，14=80m\n"
            "3、起飞点条件：∠H12=60° ∠H23=30°\n"
            "4、水平速度：5m/s，垂直速度：3m/s\n"
            "5、航向为：4点在3点的南偏西35°方向。\n"
            "6、转弯模式：停止转弯，停留5秒。\n"
            "7、循环条件：闭合不循环，完成任务后在H点悬停\n"
            "8、起飞点经纬度：E:104.4615°，N:30.8697°\n"
            "9、起飞点高度为航线高度一半。")
        fields = {"graph_hint": "", "conditions_text": text,
                  "requirements_text": "", "suggested_text": ""}
        refined["source"] = "vision_model"
        assist = {
            "status": "review", "solver_usable": True, "complete": True,
            "normalized_conditions": text,
            "condition_items": [
                {"normalized": line, "visual_evidence": line, "confidence": 1.0}
                for line in text.splitlines()],
            "uncertain_items": refined["warnings"],
            "diagram_model": refined, "source_fields": fields,
        }
        question = {"id": 987654401, "imported": True, **fields,
                    "reference_source": {"condition_assist": assist}}
        result = re.solve_reference(question)
        self.assertTrue(result["candidate"], result.get("candidate_validation_errors"))
        self.assertTrue(result["candidate_constraints_passed"], result)
        self.assertEqual(set(result["candidate_points"]), {"H", "1", "2", "3", "4"})
        points = result["candidate_points"]
        for first, second, distance in (("1", "2", 100), ("3", "4", 140),
                                       ("1", "4", 80)):
            actual = math.dist((points[first]["x"], points[first]["y"]),
                               (points[second]["x"], points[second]["y"]))
            self.assertAlmostEqual(actual, distance, delta=.2)
        dx = points["4"]["x"] - points["3"]["x"]
        dy = points["4"]["y"] - points["3"]["y"]
        bearing = (math.degrees(math.atan2(dx, dy)) + 360) % 360
        self.assertAlmostEqual(bearing, 215.0, delta=.2)
        self.assertEqual(result["candidate_access_edges"], [["H", "1"]])
        self.assertEqual(result["candidate_flight_edges"], [
            ["1", "2"], ["2", "3"], ["3", "4"], ["4", "1"]])

    def test_vlm_only_mismatched_quadrilateral_layout_does_not_produce_candidate(self):
        """导入图的角标 OCR 不全时，完整 VLM 四边形仍可进入独立求解与复核。"""
        text = (
            "1、该任务航线为如图，航线高度为80m\n"
            "2、该航线12=100m，34=140m，14=80m\n"
            "3、起飞点条件：∠H12=60° ∠H23=30°\n"
            "4、水平速度：5m/s，垂直速度：3m/s\n"
            "5、航向为：4点在3点的南偏西35°方向。\n"
            "6、转弯模式：停止转弯，停留5秒。\n"
            "7、循环条件：闭合不循环，完成任务后在H点悬停\n"
            "8、起飞点经纬度：E:104.4615°，N:30.8697°\n"
            "9、起飞点高度为航线高度一半。")
        fields = {"graph_hint": "", "conditions_text": text,
                  "requirements_text": "", "suggested_text": ""}
        diagram = {
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
            "confidence": 1.0,
            "warnings": ["视觉图形结构尚未经过人工核对，路线仅作参考",
                         "题面航点顺序未完整识别；不会按编号顺序补造路线",
                         "航线是否闭合未从题面确认"],
            "topology_errors": ["飞行顺序未完整识别", "航线闭合条件未确认"],
            "topology_valid": False,
        }
        assist = {
            "status": "review", "solver_usable": True, "complete": True,
            "normalized_conditions": text,
            "condition_items": [
                {"normalized": line, "visual_evidence": line, "confidence": 1.0}
                for line in text.splitlines()],
            "uncertain_items": diagram["warnings"],
            "diagram_model": diagram, "source_fields": fields,
        }
        question = {"id": 987654400, "imported": True, **fields,
                    "reference_source": {
                        "diagram_model": {"source": "local_cv",
                                          "status": "labels_incomplete"},
                        "condition_assist": assist}}

        spec = re.extract_question_spec(question)
        self.assertEqual(spec["shape"], "四边形")
        self.assertTrue(spec["route_inferred_from_closed_cycle"])
        self.assertEqual(spec["flight_edges"], [
            ["1", "2"], ["2", "3"], ["3", "4"], ["4", "1"]])

        result = re.solve_reference(question)
        self.assertEqual(result["status"], "NEEDS_REVIEW", result.get("reason"))
        self.assertFalse(result["candidate"])
        self.assertFalse(result["candidate_points"])
        self.assertIn("视觉识别", result["candidate_note"])

    def test_visual_layout_match_rejects_wrong_polygon_morphology(self):
        spec = {
            "shape": "四边形", "nodes": ["1", "2", "3", "4"],
            "layout_points": [(0, 0), (100, 0), (100, 100), (0, 100)],
        }
        convex = {"1": (0, 0), "2": (100, 0), "3": (100, 100), "4": (0, 100)}
        concave = {"1": (0, 0), "2": (100, 0), "3": (40, 40), "4": (0, 100)}
        crossed = {"1": (0, 0), "2": (100, 100), "3": (0, 100), "4": (100, 0)}
        self.assertTrue(nr._layout_matches(spec, convex))
        self.assertFalse(nr._layout_matches(spec, concave))
        self.assertFalse(nr._layout_matches(spec, crossed))

    def test_generic_polygon_topology_supports_three_through_sixteen_vertices(self):
        for count in range(3, 17):
            with self.subTest(vertex_count=count):
                nodes = [str(index) for index in range(1, count + 1)]
                cycle = [(nodes[index], nodes[(index + 1) % count])
                         for index in range(count)]
                self.assertTrue(nr._diagram_is_single_cycle(nodes, cycle))
                self.assertFalse(nr._diagram_is_single_cycle(nodes, cycle[:-1]))

        scan_nodes = [str(index) for index in range(1, 7)]
        scan_edges = [(scan_nodes[index], scan_nodes[index + 1])
                      for index in range(len(scan_nodes) - 1)]
        self.assertTrue(nr._diagram_edges_connected(scan_nodes, scan_edges))
        self.assertFalse(nr._diagram_is_single_cycle(scan_nodes, scan_edges))


if __name__ == "__main__":
    unittest.main()
