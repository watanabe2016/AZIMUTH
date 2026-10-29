# -*- coding: utf-8 -*-
"""把完整且可独立核验的作答收录为本地参考答案。"""
from __future__ import annotations

import math
import re

import answers as AN
import geo as GEO
import qmeta as QM
import reference_engine as RE


def _role(name):
    text = str(name or "").strip()
    if text in ("H", AN.TAKEOFF):
        return "H"
    if text in ("A", AN.AVOID):
        return "A"
    if text in ("T", AN.TRANSIT):
        return "T"
    match = re.fullmatch(r"(?:题卡)?(?:图形)?\s*(\d+)\s*(?:号)?点?", text)
    return str(int(match.group(1))) if match else text


def _valid_latlon(value):
    try:
        lat, lon = float(value[0]), float(value[1])
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (TypeError, ValueError, IndexError, KeyError):
        pass
    return None


def _required_nodes(spec):
    names = set(spec.get("nodes") or [])
    names.update(name for name in spec.get("coordinates", {})
                 if not str(name).startswith("_"))
    for key in ("distance_constraints", "bearing_constraints", "segment_constraints"):
        for item in spec.get(key, []):
            names.update((str(item["a"]), str(item["b"])))
    for item in spec.get("direction_relations", []):
        names.update(item["first"] + item["second"])
    for item in spec.get("angle_constraints", []):
        names.add(str(item["vertex"]))
    return names


def _ordered_task_points(spec, task):
    """只接受航点数和进场段都明确的任务，避免将避让点错当图形1点。"""
    nodes = [str(name) for name in spec.get("nodes") or []]
    if nodes != [str(i) for i in range(1, len(nodes) + 1)]:
        return None
    raw = list(task.get("points") or [])
    if not raw:
        return None
    access = _required_nodes(spec) & {"A", "T"}
    if len(access) > 1:
        return None
    order = sorted(access) + nodes
    if len(raw) == len(order) + 1:
        last = _valid_latlon((raw[-1].get("lat"), raw[-1].get("lon")))
        first = _valid_latlon((raw[0].get("lat"), raw[0].get("lon")))
        home = _valid_latlon(task.get("takeoff"))
        if last and any(point and GEO.distance_of(*last, *point) <= 2.0
                        for point in (first, home)):
            raw.pop()
    if len(raw) != len(order):
        return None
    points = {}
    home = _valid_latlon(task.get("takeoff"))
    if home:
        points["H"] = home
    for name, item in zip(order, raw):
        value = _valid_latlon((item.get("lat"), item.get("lon")))
        if value is None:
            return None
        points[name] = value
    return points


def _candidate_points(spec, user_points, task):
    if task is not None:
        ordered = _ordered_task_points(spec, task)
        if ordered is not None:
            return ordered
        return {}
    output = {}
    for name, value in (user_points or {}).items():
        checked = _valid_latlon(value)
        if checked is not None:
            output[_role(name)] = checked
    return output


def _local_points(spec, points):
    _, anchors, origin = RE._coordinate_frame(spec)
    if origin is None:
        return None
    lat0, lon0, _ = origin
    result = {}
    for name, (lat, lon) in points.items():
        if RE.Geodesic is not None:
            leg = RE.Geodesic.WGS84.Inverse(lat0, lon0, lat, lon)
            result[name] = RE._bearing_xy(leg["azi1"], leg["s12"])
        else:
            bearing = GEO.bearing_of(lat0, lon0, lat, lon)
            distance = GEO.distance_of(lat0, lon0, lat, lon)
            if bearing is None or distance is None:
                return None
            result[name] = RE._bearing_xy(bearing, distance)
    return result


def _turns(points, nodes):
    signs = []
    for i, name in enumerate(nodes):
        before = points[nodes[i - 1]]
        here = points[name]
        after = points[nodes[(i + 1) % len(nodes)]]
        cross = ((here[0] - before[0]) * (after[1] - here[1])
                 - (here[1] - before[1]) * (after[0] - here[0]))
        signs.append(1 if cross > 1e-6 else -1 if cross < -1e-6 else 0)
    return signs


def _layout_matches(spec, points):
    """只比较凹点编号，不拿扫描图的手绘像素当米制精确坐标。"""
    nodes = spec.get("nodes") or []
    layout = spec.get("layout_points") or []
    if spec.get("shape") != "五边形" or len(nodes) != 5 or len(layout) != 5:
        return True
    reference = {name: tuple(layout[i]) for i, name in enumerate(nodes)}
    actual_signs, ref_signs = _turns(points, nodes), _turns(reference, nodes)

    def reflex(signs):
        nonzero = [value for value in signs if value]
        if len(nonzero) < 4:
            return None
        majority = 1 if sum(nonzero) > 0 else -1
        return {i for i, value in enumerate(signs) if value and value != majority}

    wanted = reflex(ref_signs)
    return wanted is None or wanted == reflex(actual_signs)


def _measurements(spec, points):
    """约束残差供秩检测；只有每个自由坐标都受题面约束才可自动收录。"""
    values = []
    _, known, _ = RE._coordinate_frame(spec)
    for name in spec.get("coordinates", {}):
        if name in points and name in known:
            values.extend((points[name][0] - known[name][0],
                           points[name][1] - known[name][1]))

    def length(a, b):
        return math.hypot(points[b][0] - points[a][0],
                          points[b][1] - points[a][1])

    def bearing(a, b):
        return RE._xy_bearing(points[b][0] - points[a][0],
                              points[b][1] - points[a][1])

    for item in spec.get("distance_constraints", []):
        values.append(length(item["a"], item["b"]) - float(item["value"]))
    for item in spec.get("bearing_constraints", []):
        values.append(RE._wrap_angle(bearing(item["a"], item["b"]) -
                                     float(item["value"])))
    for item in spec.get("segment_constraints", []):
        values.append(length(item["a"], item["b"]) - float(item["distance"]))
        values.append(RE._wrap_angle(bearing(item["b"], item["a"]) -
                                     float(item["bearing"])))
    for item in spec.get("direction_relations", []):
        a, b = item["first"], item["second"]
        offset = float(item.get("rotation", 0)) + (180 if item.get("opposite") else 0)
        values.append(RE._wrap_angle(bearing(*b) - bearing(*a) - offset))
    neighbors = {}
    for a, b in spec.get("geometry_edges", []):
        neighbors.setdefault(a, []).append(b)
        neighbors.setdefault(b, []).append(a)
    for item in spec.get("angle_constraints", []):
        vertex = item["vertex"]
        adjacent = list(dict.fromkeys(neighbors.get(vertex, [])))
        if len(adjacent) >= 2:
            values.append(RE._angle_of(points[adjacent[0]], points[vertex],
                                       points[adjacent[1]]) - float(item["value"]))
    if spec.get("shape") == "六边形" and "正六边形" in spec.get("source_text", ""):
        nodes = spec["nodes"]
        if len(nodes) == 6:
            for index, vertex in enumerate(nodes):
                values.append(RE._angle_of(points[nodes[index - 1]],
                                           points[vertex],
                                           points[nodes[(index + 1) % 6]]) - 120.0)
    return values


def _rank(rows, columns):
    """用行归一化的消元判断局部是否仍有自由航点，避免只判到部分条件。"""
    matrix = []
    for row in rows:
        size = max((abs(value) for value in row), default=0.0)
        if size > 1e-8:
            matrix.append([value / size for value in row])
    rank = 0
    for column in range(columns):
        pivot = max(range(rank, len(matrix)),
                    key=lambda i: abs(matrix[i][column]), default=None)
        if pivot is None or abs(matrix[pivot][column]) < 1e-5:
            continue
        matrix[rank], matrix[pivot] = matrix[pivot], matrix[rank]
        factor = matrix[rank][column]
        for j in range(column, columns):
            matrix[rank][j] /= factor
        for i in range(rank + 1, len(matrix)):
            multiple = matrix[i][column]
            for j in range(column, columns):
                matrix[i][j] -= multiple * matrix[rank][j]
        rank += 1
        if rank == columns:
            break
    return rank


def _fully_determined(spec, points):
    names = sorted(_required_nodes(spec))
    columns = len(names) * 2
    base = _measurements(spec, points)
    if len(base) < columns:
        return False
    rows = [[] for _ in base]
    step = 0.05
    for name in names:
        x, y = points[name]
        for shifted in ((x + step, y), (x, y + step)):
            moved = dict(points)
            moved[name] = shifted
            values = _measurements(spec, moved)
            for row, before, after in zip(rows, base, values):
                row.append((after - before) / step)
    return _rank(rows, columns) == columns


def _parameters_match(q, supplied, extras):
    expected = AN.derive(q)
    for item in expected.get("params") or []:
        key, want = item.get("key"), item.get("value")
        if want is None:
            continue
        try:
            got = float((supplied or {})[key])
        except (KeyError, TypeError, ValueError):
            return False
        unit = item.get("unit")
        tolerance = max(0.5, abs(float(want)) * 0.02) if unit == "m/s" else max(2.0, abs(float(want)) * 0.02)
        if not math.isfinite(got) or abs(got - float(want)) > tolerance:
            return False
    for key, want in (expected.get("extras") or {}).items():
        if key == "停留时间原文":
            continue
        if str((extras or {}).get(key, "")).strip() != str(want).strip():
            return False
    return True


def verify_attempt(q, user_points=None, user_params=None, user_extras=None,
                   task=None):
    """独立核验题面所有已提取条件；返回可否收录及可读原因。"""
    if not q or q.get("id") is None:
        return {"status": "REJECTED", "reason": "没有选中题目"}
    if QM.answer_of(q["id"]):
        return {"status": "REJECTED", "reason": "已有手写参考答案"}
    spec = RE.extract_question_spec(q)
    if spec.get("review"):
        return {"status": "REJECTED", "reason": "题面坐标需先校对"}
    if not spec.get("nodes") or len(spec["nodes"]) < 3:
        return {"status": "REJECTED", "reason": "题卡航点或图形尚未识别完整"}
    points = _candidate_points(spec, user_points, task)
    required = _required_nodes(spec)
    missing = required - set(points)
    if missing:
        return {"status": "REJECTED", "reason": "缺少航点：" + "、".join(sorted(missing))}
    if not _parameters_match(q, user_params, user_extras):
        return {"status": "REJECTED", "reason": "飞行参数或其他要求未全部核对"}
    local = _local_points(spec, points)
    if local is None:
        return {"status": "REJECTED", "reason": "缺少可确认的绝对坐标基准"}
    errors = RE._validate_solution(
        spec, local, spec.get("geometry_edges", []),
        spec.get("flight_edges", []), distance_tolerance=2.0,
        angle_tolerance=1.0)
    if errors:
        return {"status": "REJECTED", "reason": errors[0]}
    if not _layout_matches(spec, local):
        return {"status": "REJECTED", "reason": "图形凹点编号与题卡不一致"}
    generated = RE.solve_generated_reference(q)
    if generated.get("status") == "READY":
        for name in required:
            expected = (generated.get("points") or {}).get(name)
            if not expected or expected.get("lat") is None:
                return {"status": "REJECTED", "reason": "自动答案仍缺少航点" + name}
            offset = GEO.distance_of(points[name][0], points[name][1],
                                     expected["lat"], expected["lon"])
            if offset is None or offset > 2.0:
                return {"status": "REJECTED", "reason": "航点%s与已算答案不符" % name}
        return {"status": "MATCHED", "reason": "作答与完整参考答案一致"}
    if spec.get("shape") in ("圆形", "扫描航线"):
        return {"status": "REJECTED", "reason": "该题型的全部图形条件尚不能独立核验"}
    if not _fully_determined(spec, local):
        return {"status": "REJECTED", "reason": "已识别条件仍不足以唯一核验全部航点"}
    output = {name: {"x": float(local[name][0]), "y": float(local[name][1]),
                     "lat": float(value[0]), "lon": float(value[1])}
              for name, value in points.items()}
    result = {
        "status": "READY", "source": "verified_attempt",
        "fingerprint": RE.question_fingerprint(q),
        "points": output,
        "geometry_edges": spec.get("geometry_edges", []),
        "flight_edges": spec.get("flight_edges", []),
        "access_edges": RE._access_edges(spec, output),
        "steps": ["从完整作答识别各编号航点及进场航段",
                  "逐项核对题面坐标、距离、方位、夹角与飞行参数"],
        "checks": ["全部已识别条件满足；各航点没有未约束的自由度",
                   "题面或求解规则变化时本答案自动失效"],
        "parameters": spec.get("parameters", {}),
        "loop_count": spec.get("loop_count", 0),
        "coordinate_system": "WGS84",
    }
    return {"status": "NEW", "reason": "完整作答已独立核验",
            "solution": result}


def learn_from_attempt(q, user_points=None, user_params=None,
                       user_extras=None, task=None):
    """只有独立核验通过的新答案才落盘；返回收录状态。"""
    review = verify_attempt(q, user_points, user_params, user_extras, task)
    if review["status"] != "NEW":
        return review
    if QM.set_verified_reference(q["id"], review["solution"]):
        return {"status": "SAVED", "reason": "完整作答已核验并成为参考答案"}
    return {"status": "REJECTED", "reason": "参考答案保存失败"}
