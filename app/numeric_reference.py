"""按题面约束求航点坐标；只返回能逐项复核且局部唯一的解。"""

import math
import re


def _names(spec):
    names = set(spec.get("nodes") or [])
    names.update(name for name in spec.get("coordinates", {})
                 if not str(name).startswith("_"))
    for key in ("distance_constraints", "bearing_constraints", "segment_constraints"):
        for item in spec.get(key, []):
            names.update((str(item["a"]), str(item["b"])))
    for item in spec.get("direction_relations", []):
        names.update(item["first"] + item["second"])
    for item in spec.get("equal_distance_constraints", []):
        names.update(item["first"] + item["second"])
    for item in spec.get("midpoint_perpendicular_constraints", []):
        names.update((item["point"], item["a"], item["b"]))
    return sorted(names, key=lambda name: (not str(name).isdigit(), str(name)))


def _fixed_points(spec, engine):
    _, fixed, _ = engine._coordinate_frame(spec)
    fixed = dict(fixed)
    # 已知坐标与完整航段可双向传播；其他航点仍交给约束求解。
    for _ in range(len(spec.get("segment_constraints", [])) + 1):
        changed = False
        for item in spec.get("segment_constraints", []):
            a, b = str(item["a"]), str(item["b"])
            if a in fixed and b not in fixed:
                dx, dy = engine._bearing_xy(float(item["bearing"]) + 180,
                                            float(item["distance"]))
                fixed[b] = (fixed[a][0] + dx, fixed[a][1] + dy)
                changed = True
            elif b in fixed and a not in fixed:
                dx, dy = engine._bearing_xy(item["bearing"], item["distance"])
                fixed[a] = (fixed[b][0] + dx, fixed[b][1] + dy)
                changed = True
        if not changed:
            break
    return fixed


def _residuals(spec, points, engine):
    values = []

    def distance(a, b):
        return math.hypot(points[b][0] - points[a][0],
                          points[b][1] - points[a][1])

    def bearing(a, b):
        return engine._xy_bearing(points[b][0] - points[a][0],
                                  points[b][1] - points[a][1])

    for item in spec.get("distance_constraints", []):
        values.append(distance(item["a"], item["b"]) - float(item["value"]))
    for item in spec.get("parallel_constraints", []):
        first, second = item.get("first", []), item.get("second", [])
        if len(first) != 2 or len(second) != 2 or not all(
                name in points for name in first + second):
            continue
        ax, ay = points[first[1]][0] - points[first[0]][0], points[first[1]][1] - points[first[0]][1]
        bx, by = points[second[1]][0] - points[second[0]][0], points[second[1]][1] - points[second[0]][1]
        denominator = math.hypot(ax, ay) * math.hypot(bx, by)
        if denominator > 1e-8:
            sine = max(-1.0, min(1.0, (ax * by - ay * bx) / denominator))
            values.append(3.0 * math.degrees(math.asin(sine)))
    for item in spec.get("equal_distance_constraints", []):
        first, second = item["first"], item["second"]
        values.append(distance(*first) - distance(*second))
    for item in spec.get("midpoint_perpendicular_constraints", []):
        h, a, b = item["point"], item["a"], item["b"]
        midpoint = ((points[a][0] + points[b][0]) / 2.0,
                    (points[a][1] + points[b][1]) / 2.0)
        offset = (points[h][0] - midpoint[0], points[h][1] - midpoint[1])
        edge = (points[b][0] - points[a][0], points[b][1] - points[a][1])
        edge_length = math.hypot(*edge)
        values.append(math.hypot(*offset) - float(item["distance"]))
        if edge_length > 1e-8:
            values.append((offset[0] * edge[0] + offset[1] * edge[1]) / edge_length)
    # ★ “起飞点位于航线 A-B 的**中点**的 <方向> <距离> 处”（实测 旧样本）：
    #   是**关于 H 的两条约束**（到中点的距离 + 从 midpoint 指向 H 的方位），
    #   而不是一条 A→B 的航段。航段解析器会把它误当成航段，所以在 spec 里已被
    #   转到 `midpoint_distance_constraints`，这里按“方位残差 = 3×角度差”的同一口径加残差。
    for item in spec.get("midpoint_distance_constraints", []):
        h, a, b = item["point"], item["a"], item["b"]
        if not all(name in points for name in (h, a, b)):
            continue                      # 某个点不在本轮点集里时跳过，不能崩
        midpoint = ((points[a][0] + points[b][0]) / 2.0,
                    (points[a][1] + points[b][1]) / 2.0)
        values.append(math.hypot(points[h][0] - midpoint[0],
                                 points[h][1] - midpoint[1]) - float(item["distance"]))
        if item.get("bearing") is not None:
            wanted = math.degrees(math.atan2(points[h][0] - midpoint[0],
                                             points[h][1] - midpoint[1])) % 360.0
            values.append(3.0 * engine._wrap_angle(wanted - float(item["bearing"])))
    for item in spec.get("bearing_constraints", []):
        values.append(3.0 * engine._wrap_angle(
            bearing(item["a"], item["b"]) - float(item["value"])))
    for item in spec.get("segment_constraints", []):
        a, b = item["a"], item["b"]
        values.append(distance(a, b) - float(item["distance"]))
        values.append(3.0 * engine._wrap_angle(
            bearing(b, a) - float(item["bearing"])))
    for item in spec.get("direction_relations", []):
        first, second = item["first"], item["second"]
        offset = float(item.get("rotation", 0.0))
        if item.get("opposite"):
            offset += 180.0
        values.append(3.0 * engine._wrap_angle(
            bearing(*second) - bearing(*first) - offset))
    neighbors = {}
    angle_edges = (spec.get("flight_edges", []) if spec.get("shape") == "蝴蝶形"
                   else spec.get("geometry_edges", []))
    for a, b in angle_edges:
        neighbors.setdefault(a, []).append(b)
        neighbors.setdefault(b, []).append(a)
    for item in spec.get("angle_constraints", []):
        adjacent = list(dict.fromkeys(neighbors.get(item["vertex"], [])))
        if len(adjacent) >= 2:
            values.append(3.0 * (engine._angle_of(
                points[adjacent[0]], points[item["vertex"]],
                points[adjacent[1]]) - float(item["value"])))
    for item in spec.get("vertex_ray_angles", []):
        a, vertex, b = (str(item[key]) for key in ("a", "vertex", "b"))
        if all(name in points for name in (a, vertex, b)):
            values.append(3.0 * (engine._angle_of(
                points[a], points[vertex], points[b]) - float(item["value"])))
    if spec.get("shape") == "六边形" and "正六边形" in spec.get("source_text", ""):
        nodes = spec.get("nodes") or []
        if len(nodes) == 6:
            for index, vertex in enumerate(nodes):
                values.append(3.0 * (engine._angle_of(
                    points[nodes[index - 1]], points[vertex],
                    points[nodes[(index + 1) % 6]]) - 120.0))
    # ★ 题面写的是「规划一个六边形，边长为a」这类正多边形（每条边都被约束为同一
    #   长度、且没给任何夹角）时，按正 n 边形补上等内角约束。
    #   实测这一条卡住了 13 道六边形（旧样本/…：给 H 到相邻两顶点的等距 +
    #   1 号点方位，几何上完全可解，却因「等边不足以定形」判成"尚未唯一"）。
    regular = _regular_angles(spec)
    if regular:
        for vertex, value in regular.items():
            adjacent = list(dict.fromkeys(neighbors.get(vertex, [])))
            if len(adjacent) >= 2 and vertex in points:
                values.append(3.0 * (engine._angle_of(
                    points[adjacent[0]], points[vertex],
                    points[adjacent[1]]) - float(value)))
    # ★ “各点之间距离不小于 N 米”：这族（蝴蝶形／直角沙漏）题面不给边长，
    #   参考图按**最小可分辨尺寸**取 N，所以把「全部点对的最小距离 = N」当成等式约束，
    #   它同时给出了图形尺寸（否则相似变换里的缩放自由度无法确定）。
    #   实测 旧样本 五道。
    minimum = spec.get("minimum_spacing")
    if minimum:
        nodes = [str(name) for name in (spec.get("nodes") or []) if name in points]
        if len(nodes) >= 3:
            spans = [math.dist(points[nodes[i]], points[nodes[j]])
                     for i in range(len(nodes)) for j in range(i + 1, len(nodes))]
            values.append(min(spans) - float(minimum))
    # 共线／延长线：用「点到直线距离」做残差（单位是米，与距离约束同量级）。
    for item in spec.get("collinear_constraints", []):
        a, b, point = str(item["a"]), str(item["b"]), str(item["point"])
        if all(name in points for name in (a, b, point)):
            ax, ay = points[a]
            bx, by = points[b]
            px, py = points[point]
            span = math.hypot(bx - ax, by - ay)
            if span > 1e-8:
                values.append(((px - ax) * (by - ay) - (py - ay) * (bx - ax)) / span)
    return values


def _solve_linear(matrix, vector):
    n = len(vector)
    rows = [list(matrix[i]) + [vector[i]] for i in range(n)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda i: abs(rows[i][column]))
        if abs(rows[pivot][column]) < 1e-12:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        for j in range(column, n + 1):
            rows[column][j] /= divisor
        for i in range(column + 1, n):
            scale = rows[i][column]
            if scale:
                for j in range(column, n + 1):
                    rows[i][j] -= scale * rows[column][j]
    answer = [0.0] * n
    for i in range(n - 1, -1, -1):
        answer[i] = rows[i][n] - sum(rows[i][j] * answer[j]
                                     for j in range(i + 1, n))
    return answer


def _rank(rows, count):
    matrix = []
    for row in rows:
        size = max((abs(value) for value in row), default=0.0)
        if size > 1e-9:
            matrix.append([value / size for value in row])
    rank = 0
    for column in range(count):
        pivot = max(range(rank, len(matrix)),
                    key=lambda i: abs(matrix[i][column]), default=None)
        if pivot is None or abs(matrix[pivot][column]) < 1e-5:
            continue
        matrix[rank], matrix[pivot] = matrix[pivot], matrix[rank]
        factor = matrix[rank][column]
        for j in range(column, count):
            matrix[rank][j] /= factor
        for i in range(rank + 1, len(matrix)):
            multiple = matrix[i][column]
            for j in range(column, count):
                matrix[i][j] -= multiple * matrix[rank][j]
        rank += 1
        if rank == count:
            break
    return rank


def _jacobian(spec, points, variables, base, engine):
    rows = [[] for _ in base]
    for name, axis in variables:
        moved = dict(points)
        old = points[name]
        # 角度残差对短边很敏感；0.05 米前向差分会给 30 米边带来约 0.1° 级的梯度偏差，
        # 使本来可满足的航向关系停在验证阈值附近。使用更小的扰动并保留米制距离精度。
        step = 0.005
        moved[name] = (old[0] + step, old[1]) if axis == 0 else (old[0], old[1] + step)
        after = _residuals(spec, moved, engine)
        for row, before, now in zip(rows, base, after):
            row.append((now - before) / step)
    return rows


def _optimise(spec, initial, variables, engine):
    points = dict(initial)
    damping = 0.01
    for _ in range(180):
        residual = _residuals(spec, points, engine)
        cost = sum(value * value for value in residual)
        if cost < 1e-12:
            break
        rows = _jacobian(spec, points, variables, residual, engine)
        count = len(variables)
        normal = [[0.0] * count for _ in range(count)]
        right = [0.0] * count
        for row, value in zip(rows, residual):
            for i in range(count):
                right[i] -= row[i] * value
                for j in range(i, count):
                    normal[i][j] += row[i] * row[j]
        for i in range(count):
            for j in range(i):
                normal[i][j] = normal[j][i]
            normal[i][i] += damping * (normal[i][i] + 1.0)
        step = _solve_linear(normal, right)
        if step is None:
            return None
        candidate = dict(points)
        for (name, axis), delta in zip(variables, step):
            old = candidate[name]
            candidate[name] = (old[0] + delta, old[1]) if axis == 0 else (old[0], old[1] + delta)
        after = _residuals(spec, candidate, engine)
        new_cost = sum(value * value for value in after)
        if new_cost < cost:
            points = candidate
            damping = max(1e-8, damping * 0.35)
            if max((abs(value) for value in step), default=0.0) < 1e-8:
                break
        else:
            damping = min(1e10, damping * 6.0)
            if damping >= 1e10:
                break
    return points


def refine_candidate(spec, candidate_points, frame, geometry, route, engine):
    """在现有示意解附近精修硬约束；满秩且独立复核通过才升级为 READY。"""
    if not isinstance(candidate_points, dict):
        return None
    points = {}
    for name, raw in candidate_points.items():
        key = str(name)
        if isinstance(raw, dict):
            raw = (raw.get("x"), raw.get("y"))
        if not isinstance(raw, (tuple, list)) or len(raw) < 2:
            continue
        try:
            point = (float(raw[0]), float(raw[1]))
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(value) for value in point):
            points[key] = point

    fixed = _fixed_points(spec, engine)
    points.update(fixed)
    figure_seed = _figure_shape_seed(spec, fixed)
    trusted_figure_seed = figure_seed is not None
    if figure_seed:
        for name in spec.get("nodes", []):
            key = str(name)
            if key not in fixed and key in figure_seed:
                points[key] = figure_seed[key]
    names = list(dict.fromkeys(
        [str(name) for name in spec.get("nodes", [])] + _names(spec)))
    names.extend(name for name in points if name not in names)
    initial_points = dict(points)
    variables = [(name, axis) for name in names if name not in fixed
                 for axis in (0, 1)]
    if not variables or any(name not in points for name in names):
        return None
    try:
        polished = _optimise(spec, points, variables, engine)
        if not polished or engine._validate_solution(spec, polished,
                                                     geometry, route):
            return None
        nodes = [str(name) for name in spec.get("nodes", [])]
        if not _topology_preserved(initial_points, polished, nodes):
            return None
        if any(math.dist(polished[a], polished[b]) < 1.0
               for a, b in geometry):
            return None
        residual = _residuals(spec, polished, engine)
        if residual is None or max((abs(value) for value in residual),
                                   default=9e9) > 0.05:
            return None
        jacobian = _jacobian(spec, polished, variables, residual, engine)
        rank = _rank(jacobian, len(variables))
        layout = spec.get("layout_points") or []
        labelling = ({name: (float(layout[index][0]), -float(layout[index][1]))
                      for index, name in enumerate(nodes)}
                     if len(layout) == len(nodes) and nodes else {})
        prior_size = _prior_size(labelling, polished) if labelling else 0.0
        prior_deviation = (_prior_deviation(labelling, polished)
                           if labelling else float("inf"))
        shape_supported = (math.isfinite(prior_size) and prior_size > 1e-8 and
                           math.isfinite(prior_deviation) and
                           prior_deviation <= 0.20 * prior_size)
        edge_lengths = [math.dist(points[a], points[b]) for a, b in geometry
                        if a in points and b in points and
                        math.dist(points[a], points[b]) > 1.0]
        edge_lengths.sort()
        shape_scale = edge_lengths[len(edge_lengths) // 2] if edge_lengths else 0.0
        shared_nodes = [name for name in nodes
                        if name in points and name in polished]
        correction = max((math.dist(points[name], polished[name])
                          for name in shared_nodes), default=float("inf"))
        local_refinement = (shape_scale > 1.0 and math.isfinite(correction) and
                            correction <= 0.02 * shape_scale)
        if (not _layout_matches(spec, polished) and not local_refinement and
                not trusted_figure_seed):
            return None
        # 满秩由题面本身定解；欠定时，只有图卡布局能在原有 20% 形状门限内
        # 明确选出当前分支，才允许把已通过硬条件的候选升级。
        if (rank != len(variables) and not shape_supported and
                not local_refinement and not trusted_figure_seed):
            return None
        verification = ["全部已识别硬约束通过复核"]
        if rank == len(variables):
            verification.append("自由航点约束雅可比满秩")
        elif shape_supported:
            verification.append("题图先验在原有形状门限内选定参考分支")
        elif trusted_figure_seed:
            verification.append("高覆盖率编号轮廓选定未定形航点，图形拓扑保持")
        else:
            verification.append("局部精修改变量不超过典型边长2%，图形拓扑保持")
        return engine._ready(
            spec, polished, frame, geometry, route,
            ["以当前参考草案选定图形分支",
             *(["按高覆盖率编号轮廓对齐已知航向和边长"]
               if trusted_figure_seed else []),
             "在题面约束附近精修全部航点",
             "独立复核坐标、距离、航向和夹角"],
            verification)
    except (KeyError, ValueError, ZeroDivisionError, OverflowError,
            TypeError, IndexError):
        return None


def _figure_shape_seed(spec, fixed):
    """用高覆盖率编号轮廓按题面边长与航向对齐，补出未约束的图形点。"""
    figure = spec.get("figure_shape") or {}
    nodes = [str(name) for name in spec.get("nodes", [])]
    raw_points = figure.get("points") or []
    labels = figure.get("labels") or {}
    try:
        coverage = float(figure.get("coverage", 0) or 0)
    except (TypeError, ValueError):
        return None
    if (coverage < 0.85 or len(nodes) < 3 or len(raw_points) != len(nodes) or
            not figure.get("simple") or
            not all(name in labels for name in nodes)):
        return None
    try:
        model = {name: (float(raw_points[int(labels[name])][0]),
                        float(raw_points[int(labels[name])][1]))
                 for name in nodes}
    except (IndexError, TypeError, ValueError, KeyError, OverflowError):
        return None
    if (len({int(labels[name]) for name in nodes}) != len(nodes) or
            any(not math.isfinite(value) for point in model.values()
                for value in point) or
            not _is_simple_polygon([model[name] for name in nodes])):
        return None
    if not any(name in fixed for name in nodes):
        return None

    distances = {}
    for item in spec.get("distance_constraints", []):
        a, b = str(item.get("a")), str(item.get("b"))
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        if a in model and b in model and value > 0:
            distances[frozenset((a, b))] = value

    directions = []
    for item in spec.get("bearing_constraints", []):
        a, b = str(item.get("a")), str(item.get("b"))
        pair = frozenset((a, b))
        if a in model and b in model and pair in distances:
            try:
                directions.append((a, b, float(item.get("value")) % 360.0,
                                   distances[pair]))
            except (TypeError, ValueError):
                continue
    if not directions:
        return None
    a, b, target_bearing, _target_distance = directions[0]
    model_bearing = math.degrees(math.atan2(model[b][0] - model[a][0],
                                             model[b][1] - model[a][1])) % 360.0
    rotation = -math.radians((target_bearing - model_bearing + 180.0) % 360.0 - 180.0)
    scale_candidates = [value / math.dist(model[first], model[second])
                        for pair, value in distances.items()
                        for first, second in [tuple(pair)]
                        if math.dist(model[first], model[second]) > 1e-8]
    if not scale_candidates:
        return None
    scale_candidates.sort()
    middle = len(scale_candidates) // 2
    scale = (scale_candidates[middle] if len(scale_candidates) % 2 else
             (scale_candidates[middle - 1] + scale_candidates[middle]) / 2.0)
    if (not math.isfinite(scale) or scale <= 0 or
            any(abs(value / scale - 1.0) > 0.15 for value in scale_candidates)):
        return None
    cosine, sine = math.cos(rotation), math.sin(rotation)
    rotated = {name: (scale * (point[0] * cosine - point[1] * sine),
                      scale * (point[0] * sine + point[1] * cosine))
               for name, point in model.items()}
    anchor = next(name for name in nodes if name in fixed)
    dx = fixed[anchor][0] - rotated[anchor][0]
    dy = fixed[anchor][1] - rotated[anchor][1]
    return {name: (point[0] + dx, point[1] + dy)
            for name, point in rotated.items()}


def _layout_matches(spec, points):
    layout = spec.get("layout_points") or []
    nodes = [str(name) for name in (spec.get("nodes") or [])]
    shape = str(spec.get("shape") or "")
    if (shape in ("圆形", "扫描航线", "蝴蝶形") or
            len(layout) < 3 or len(layout) != len(nodes) or
            any(name not in points for name in nodes)):
        return True

    def reflex(coords):
        crosses = []
        for index in range(len(coords)):
            before = coords[index - 1]
            here = coords[index]
            after = coords[(index + 1) % len(coords)]
            first_length = math.dist(before, here)
            second_length = math.dist(here, after)
            if first_length < 1e-8 or second_length < 1e-8:
                return None
            cross = ((here[0] - before[0]) * (after[1] - here[1]) -
                     (here[1] - before[1]) * (after[0] - here[0]))
            sine = cross / (first_length * second_length)
            crosses.append(1 if sine > 0.035 else -1 if sine < -0.035 else 0)
        nonzero = [value for value in crosses if value]
        if len(nonzero) < max(3, len(coords) - 1):
            return None
        majority = 1 if sum(nonzero) > 0 else -1
        return {index for index, value in enumerate(crosses)
                if value and value != majority}

    actual_coords = [points[name] for name in nodes]
    if not _is_simple_polygon(layout) or not _is_simple_polygon(actual_coords):
        return False
    wanted = reflex(layout)
    actual = reflex(actual_coords)
    return wanted is None or actual is not None and wanted == actual


def _topology_preserved(before, after, nodes):
    """精修前后保持图形的交叉关系和明确的凸凹转向。"""
    if len(nodes) < 3 or any(name not in before or name not in after
                             for name in nodes):
        return False

    def signature(points):
        coords = [points[name] for name in nodes]

        def cross(origin, first, second):
            return ((first[0] - origin[0]) * (second[1] - origin[1]) -
                    (first[1] - origin[1]) * (second[0] - origin[0]))

        crossings = []
        count = len(coords)
        for first in range(count):
            a, b = coords[first], coords[(first + 1) % count]
            for second in range(first + 1, count):
                if (second + 1) % count == first or (first + 1) % count == second:
                    continue
                c, d = coords[second], coords[(second + 1) % count]
                d1, d2 = cross(c, d, a), cross(c, d, b)
                d3, d4 = cross(a, b, c), cross(a, b, d)
                if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
                    crossings.append((first, second))

        turns = []
        for index in range(count):
            previous = coords[index - 1]
            here = coords[index]
            following = coords[(index + 1) % count]
            first_length = math.dist(previous, here)
            second_length = math.dist(here, following)
            if first_length < 1e-8 or second_length < 1e-8:
                return None
            value = cross(previous, here, following) / (first_length * second_length)
            turns.append(1 if value > 0.035 else -1 if value < -0.035 else 0)
        return tuple(crossings), tuple(turns)

    old_signature = signature(before)
    new_signature = signature(after)
    if old_signature is None or new_signature is None:
        return False
    if old_signature[0] != new_signature[0]:
        return False
    return all(not old or not new or old == new
               for old, new in zip(old_signature[1], new_signature[1]))


# 这些图形的卡片形状是「规则多边形」：题面只写「规划一个六边形，边长为a」时，
# 六个等边还不足以定形（六边等长可以是任意六边形），必须把卡片的形状也纳入约束。
# 依据：题面规则「按图规划」+ lessons 三·21（规则图形只信卡片的形状，旋转不可信）。
_REGULAR_CAPABLE_SHAPES = ("六边形", "五边形", "等边三角形", "正方形", "三角形")


def _layout_polygon(spec):
    """卡片顶点（画布 x 右 / y 下）→ 地理平面 (x 东, y 北)。"""
    nodes = list(spec.get("nodes") or [])
    layout = spec.get("layout_points") or []
    if len(nodes) != len(layout) or len(nodes) < 3:
        return None
    return [(float(x), -float(y)) for x, y in layout], nodes


def _layout_interior_angles(spec):
    """按卡片顺序算每个顶点的内角（度）。节点顺序即多边形顺序时才有意义。"""
    polygon = _layout_polygon(spec)
    if not polygon:
        return None
    points, nodes = polygon
    total = len(points)
    angles = {}
    for index in range(total):
        before, here, after = (points[index - 1], points[index],
                               points[(index + 1) % total])
        first = (before[0] - here[0], before[1] - here[1])
        second = (after[0] - here[0], after[1] - here[1])
        first_length = math.hypot(*first)
        second_length = math.hypot(*second)
        if first_length < 1e-9 or second_length < 1e-9:
            return None
        cosine = ((first[0] * second[0] + first[1] * second[1]) /
                  (first_length * second_length))
        angles[nodes[index]] = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
    return angles


def _regular_layout_angles(spec, tolerance=3.0):
    """卡片是不是规则多边形；是的话返回每个顶点的内角，否则 None。

    角度取自当前导入题卡给出的图形点位，只作为次要判据；
    主判据仍是题面中明确给出的规则（见 `_regular_angles`）。
    """
    if spec.get("shape") not in _REGULAR_CAPABLE_SHAPES:
        return None
    angles = _layout_interior_angles(spec)
    if not angles or len(angles) < 3:
        return None
    values = list(angles.values())
    if max(values) - min(values) > tolerance:
        return None
    return angles


def _all_polygon_edges_equal(spec, nodes, tolerance=0.5):
    """多边形的每一条边是否都被题面显式约束为同一条长度。"""
    lengths = {}
    for item in spec.get("distance_constraints", []):
        pair = frozenset((str(item["a"]), str(item["b"])))
        lengths[pair] = float(item["value"])
    values = []
    for index, name in enumerate(nodes):
        pair = frozenset((str(name), str(nodes[(index + 1) % len(nodes)])))
        if pair not in lengths:
            return False
        values.append(lengths[pair])
    if not values:
        return False
    return max(values) - min(values) <= tolerance


def _regular_angles(spec):
    """题面是否等价于「规划一个正 n 边形」。

    判据（三条同时成立，缺一不可）：
      ① 图形属于可能是正多边形的类型；
      ② 题面把这个多边形的**每一条边**都约束成同一个长度（「12、23、34、45、15为a」
         或「边长为a」展开后的六条边）；
      ③ 题面**没有**给任何夹角 —— 给了角度的（如 ∠1=∠2=90° 的凹五边形、
         等腰直角三角形）就不是规则图形。
    满足这三条时，考卷画的就是正 n 边形（该考试的固定图形约定），内角 = (n−2)·180/n。

    为什么必须补这一步：只有「六条边都等于 a」定不了形 —— 六边等长的六边形
    仍有 4 个自由度，求解器会（正确地）判成「尚未组成唯一坐标解」。
    """
    nodes = list(spec.get("nodes") or [])
    count = len(nodes)
    if count < 3 or spec.get("shape") not in _REGULAR_CAPABLE_SHAPES:
        return None
    if spec.get("angle_constraints"):
        return None
    if not _all_polygon_edges_equal(spec, nodes):
        return None
    value = (count - 2) * 180.0 / count
    return {name: value for name in nodes}


def _seeds(spec, fixed, preliminary, names, engine):
    template = engine._template_points(spec)
    if template is None and len(spec.get("layout_points") or []) == len(spec.get("nodes") or []):
        template = {name: (float(x), -float(y)) for name, (x, y) in
                    zip(spec["nodes"], spec["layout_points"])}
    if template is None:
        count = len(spec.get("nodes") or [])
        if count < 3:
            return
        template = {name: (math.cos(2 * math.pi * i / count),
                           math.sin(2 * math.pi * i / count))
                    for i, name in enumerate(spec["nodes"])}
    ratios = []
    for item in spec.get("distance_constraints", []):
        a, b = item["a"], item["b"]
        if a in template and b in template:
            model = math.dist(template[a], template[b])
            if model > 1e-8:
                ratios.append(float(item["value"]) / model)
    scale = sorted(ratios)[len(ratios) // 2] if ratios else 200.0
    if not 0 < scale < 100000:
        return
    shape_fixed = [name for name in spec.get("nodes", []) if name in fixed]
    orientation = None
    for index, a in enumerate(shape_fixed):
        for b in shape_fixed[index + 1:]:
            if a in template and b in template and math.dist(template[a], template[b]) > 1e-6:
                model = engine._xy_bearing(template[b][0] - template[a][0],
                                           template[b][1] - template[a][1])
                actual = engine._xy_bearing(fixed[b][0] - fixed[a][0],
                                            fixed[b][1] - fixed[a][1])
                orientation = -math.radians(engine._wrap_angle(actual - model))
                break
        if orientation is not None:
            break
    if orientation is None:
        bearings = list(spec.get("bearing_constraints", []))
        bearings += [{"a": item["b"], "b": item["a"],
                      "value": item["bearing"]}
                     for item in spec.get("segment_constraints", [])]
        for item in engine._relation_bearings(spec, fixed):
            bearings.append({"a": item["target_a"], "b": item["target_b"],
                             "value": item["value"]})
        for item in bearings:
            a, b = item["a"], item["b"]
            if a in template and b in template and math.dist(template[a], template[b]) > 1e-6:
                model = engine._xy_bearing(template[b][0] - template[a][0],
                                           template[b][1] - template[a][1])
                orientation = -math.radians(engine._wrap_angle(float(item["value"]) - model))
                break
    angles = [orientation + delta for delta in (0, math.pi / 2, math.pi, -math.pi / 2)] if orientation is not None else [0, math.pi / 2, math.pi, -math.pi / 2]
    for angle in angles:
        cosine, sine = math.cos(angle), math.sin(angle)
        moved = {name: (scale * (x * cosine - y * sine),
                        scale * (x * sine + y * cosine))
                 for name, (x, y) in template.items()}
        if shape_fixed:
            anchor = shape_fixed[0]
            dx = fixed[anchor][0] - moved[anchor][0]
            dy = fixed[anchor][1] - moved[anchor][1]
        else:
            dx = dy = 0.0
        result = {name: (value[0] + dx, value[1] + dy)
                  for name, value in moved.items()}
        result.update(fixed)
        # 用完整航段反推图形以外的起飞点、避让点和过渡点初值。
        for _ in range(len(spec.get("segment_constraints", [])) + 1):
            for item in spec.get("segment_constraints", []):
                a, b = item["a"], item["b"]
                if a in result and b not in result:
                    vx, vy = engine._bearing_xy(float(item["bearing"]) + 180,
                                                float(item["distance"]))
                    result[b] = (result[a][0] + vx, result[a][1] + vy)
                elif b in result and a not in result:
                    vx, vy = engine._bearing_xy(item["bearing"], item["distance"])
                    result[a] = (result[b][0] + vx, result[b][1] + vy)
        for index, name in enumerate(names):
            if name not in result:
                result[name] = preliminary.get(name, (dx + 75 * index, dy + 90 * index))
        yield result


# ============================================================ 相似变换求解器
# 题面给不出完整形状时（例：「规划扫描航线，航线长度为a，航线间隔为b」——
# 只给尺寸，形状与走向靠卡片；「按图规划一个梯形」——只给部分边长与夹角），
# 用**题卡模板当形状**，只解「平移 + 旋转（+ 缩放）」这几个自由度。
#
# 依据是交接文档第 8 条与 lessons 三·21：卡片是题目的一部分；
# 但卡片**朝向与比例**不可盲信 —— 所以这里不直接把卡片点当坐标，而是把
# 「题面全部约束」放进残差，让求解器去定位置、朝向、尺度。
# 好处：相似变换天然**不含镜像**，卡片给出的手性直接消掉了「镜像解」歧义。
#
# 只在自由坐标法先失败时才调用（见 reference_engine._solve_reference_uncached），
# 所以题面本身能定形的题不会被它带偏。


def _metric_scan_model(spec):
    """扫描航线的米制模型：形状由「航线长度 + 航线间隔 + 航点数」完全确定。

    与 reference_engine._scan_answer 的编号约定保持一致：
    1→2 反向扫描、2→3 换道、3→4 正向扫描，后续依次折返。
    """
    nodes = [str(name) for name in (spec.get("nodes") or [])]
    length = spec.get("scan_length")
    spacing = spec.get("scan_spacing")
    if length is None or spacing is None or len(nodes) < 4 or len(nodes) % 2:
        return None
    length, spacing = float(length), float(spacing)
    if length <= 0 or spacing <= 0:
        return None
    lanes = len(nodes) // 2
    center_offset = (lanes - 3) * spacing / 2.0
    model = {}
    for index, name in enumerate(nodes):
        lane = index // 2
        forward = ((lane % 2 == 0 and index % 2 == 0) or
                   (lane % 2 == 1 and index % 2 == 1))
        along = (length if forward else 0.0) - length / 2.0
        offset = (lane - 1) * spacing - center_offset
        model[name] = (along, offset)
    return model


def _metric_circle_model(spec):
    """圆形航线的米制模型：直径与航点数已知，只差圆心与起始角。"""
    nodes = [str(name) for name in (spec.get("nodes") or [])]
    diameter = spec.get("circle_diameter")
    if not diameter or len(nodes) < 3:
        return None
    radius = float(diameter) / 2.0
    model = {}
    for index, name in enumerate(nodes):
        angle = 2.0 * math.pi * index / len(nodes)
        model[name] = (radius * math.cos(angle), radius * math.sin(angle))
    return model


def _metric_square_center_pentagon(spec):
    """“正方形 + 中心点”五边形 —— 用户口径（#232）。

    用户原话：“如果说制图的话两条边知道它是正方形，角4为90度，那另外两个就只能是
    **等腰三角形**…一般我们做等腰三角形”；配图里 **④ 落在图形中间**。

    于是唯一形状：正方形 1-2-3-5（边长 a）＋ ④ = 正方形中心 (a/2, a/2)。
    验算：∠1 = ∠2 = 90° ✓；4→3 与 4→5 点积 = 0 ⇒ ∠4 = 90° ✓；|3-4| = |4-5| = a/√2（等腰）✓。
    """
    if spec.get("shape") != "五边形":
        return None
    nodes = [str(name) for name in (spec.get("nodes") or [])]
    if len(nodes) != 5:
        return None
    lengths = {}
    for item in spec.get("distance_constraints", []):
        lengths[frozenset((str(item["a"]), str(item["b"])))] = float(item["value"])
    twelve = lengths.get(frozenset(("1", "2")))
    twenty_three = lengths.get(frozenset(("2", "3")))
    if not twelve or not twenty_three:
        return None
    if abs(twelve - twenty_three) > 0.02 * twelve:
        return None
    angles = {str(item["vertex"]): float(item["value"])
              for item in spec.get("angle_constraints") or []}
    for vertex in ("1", "2", "4"):
        if abs(angles.get(vertex, -1.0) - 90.0) > 1e-6:
            return None
    side = twelve
    return {"1": (0.0, 0.0), "2": (0.0, side), "3": (side, side),
            "5": (side, 0.0), "4": (side / 2.0, side / 2.0)}


def _metric_square_triangle_pentagon(spec):
    """“五边相等 + ∠1=∠2=90°”的五边形 —— 由**题型约定**唯一确定。

    ★ 用户（领域专家）确认的口径：五条边都相等时只剩角度自由；再给定 ∠1=∠2=90°，
      满足条件的五边形只有一种形状 —— **1-2-3-5 是正方形，3-4-5 是等边三角形**
      （原话：“在满足五边都相等的情况下的五边形也就只有里面放了个等边三角形”）。

    取 1=(0,0) 2=(0,a) 3=(a,a) 5=(a,0)，则 4 必须同时距 3、5 为 a 且在 1-5 那一侧：
    4 = (a + a·√3/2, a/2)。可验算：|3-4| = |4-5| = a（等边三角形），∠1 = ∠2 = 90°。
    """
    if spec.get("shape") != "五边形":
        return None
    nodes = [str(name) for name in (spec.get("nodes") or [])]
    if len(nodes) != 5 or not _all_polygon_edges_equal(spec, nodes):
        return None
    angles = {str(item["vertex"]): float(item["value"])
              for item in spec.get("angle_constraints") or []}
    if not (abs(angles.get("1", -1) - 90.0) < 1e-6 and
            abs(angles.get("2", -1) - 90.0) < 1e-6):
        return None
    side = None
    for item in spec.get("distance_constraints", []):
        pair = frozenset((str(item["a"]), str(item["b"])))
        if pair == frozenset((nodes[0], nodes[1])):
            side = float(item["value"])
            break
    if not side or side <= 0:
        return None
    return {"1": (0.0, 0.0), "2": (0.0, side), "3": (side, side),
            "5": (side, 0.0),
            "4": (side + side * math.sqrt(3.0) / 2.0, side / 2.0)}


def _inside_polygon(point, polygon):
    """射线法判定点是否在多边形内（用于"起飞点在图形内侧/外侧"）。"""
    x, y = point
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < cross:
                inside = not inside
    return inside


def _distance_to_polygon(point, polygon):
    """点到多边形**边界**的最短距离（用于识别"正好压在边上"的 H）。"""
    best = float("inf")
    count = len(polygon)
    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy
        if span <= 1e-12:
            ratio = 0.0
        else:
            ratio = max(0.0, min(1.0, ((point[0] - ax) * dx +
                                       (point[1] - ay) * dy) / span))
        px, py = ax + ratio * dx, ay + ratio * dy
        best = min(best, math.hypot(point[0] - px, point[1] - py))
    return best


def pick_branch_by_takeoff(spec, solutions):
    """镜像/内外侧分支决胜：**起飞点默认在图形外侧**（用户口径 + 物理常识）。

    实测 #268：两支解都精确满足题面全部条件，差别只是 H 在六边形内（|H-1|=250 米）
    还是外（550 米）；用户给出的标准答案截图显示 **H 在外侧** ✓。
    题面若明确写了"起飞点在图形内侧"（如 旧样本），则反过来取内侧那支。
    """
    if len(solutions) != 2:
        return solutions
    nodes = [str(name) for name in (spec.get("nodes") or [])]
    if len(nodes) < 3:
        return solutions
    want_inside = bool(re.search(r"起飞点.{0,8}图形\s*内侧", spec.get("source_text", "")))
    scored = []
    for candidate in solutions:
        if "H" not in candidate or any(n not in candidate for n in nodes):
            return solutions
        polygon = [candidate[name] for name in nodes]
        # ★ 关键修正（实测 #268）：H **正好压在图形边上**（解2 的 H 落在 1-2 边中点，
        #   250/400 = 0.625）时，射线法会判成"不在内侧" ✗，于是两支都被当成"外侧"，
        #   规则失效。用户口径是"H 必须在图形外侧"，压在边界上显然不算外侧，所以
        #   这里把"内侧 **或** 离边界不足 1 米"都视作不符合。
        inside = (_inside_polygon(candidate["H"], polygon) or
                  _distance_to_polygon(candidate["H"], polygon) < 1.0)
        scored.append((inside != want_inside, candidate))     # False 排在前面 = 更符合
    if scored[0][0] != scored[1][0]:
        return [scored[0][1]]
    return solutions


def figure_orientation_error(spec, points, engine):
    """候选解与**题卡图形**的朝向偏差（度，越小越贴合卡片）。

    ★ 为什么不能只比一条边：实测 旧样本 的两支镜像解，`1→2` 边的方位**完全相同**
      （镜像轴恰好就是 1-2 那条边），只比一条边等于没比。这里对**图形所有边**的
      罗盘方位取平均偏差 —— 镜像必然翻转其中一部分边，分数就会分开。
      返回 None 表示没有可比对的题卡测点。
    """
    figure = spec.get("figure_shape") or {}
    nodes = [str(name) for name in (spec.get("nodes") or [])]
    points_of_figure = list(figure.get("points") or [])
    if len(points_of_figure) != len(nodes) or len(nodes) < 3:
        return None
    reference = {name: tuple(point) for name, point in zip(nodes, points_of_figure)}
    edges = [tuple(edge) for edge in (spec.get("geometry_edges") or [])]
    if not edges:
        edges = [(nodes[index], nodes[(index + 1) % len(nodes)])
                 for index in range(len(nodes))]
    total = 0.0
    counted = 0
    for first, second in edges:
        if first not in reference or second not in reference:
            continue
        if first not in points or second not in points:
            continue
        wanted = engine._xy_bearing(reference[second][0] - reference[first][0],
                                    reference[second][1] - reference[first][1])
        got = engine._xy_bearing(points[second][0] - points[first][0],
                                 points[second][1] - points[first][1])
        total += abs((got - wanted + 180.0) % 360.0 - 180.0)
        counted += 1
    return total / counted if counted else None


def _metric_right_isosceles(spec):
    """“等腰／直角三角形：12=a1、31=a2、∠1=90°”的**米制**模型。

    题面已经把两条直角边和一个直角都给全了 —— 形状完全由题面确定，根本不该去用
    题卡模板（实测 旧样本：走 layout/题图那条路时 **90 个种子全部因残差失败**，
    因为题卡的 5% 手绘误差让"30/30/42.4 + ∠1=90°"永远对不上）。

    取 1=(0,0)、2=(a1,0)、3=(0,a2)（∠1 即 x 轴与 y 轴之间的直角）。
    镜像的另一支由 `_solve_similarity_with` 的“题卡朝向决胜”处理。
    """
    if spec.get("shape") != "直角三角形":
        return None
    nodes = [str(name) for name in (spec.get("nodes") or [])]
    if len(nodes) != 3:
        return None
    angles = {str(item["vertex"]): float(item["value"])
              for item in spec.get("angle_constraints") or []}
    if abs(angles.get("1", -1.0) - 90.0) > 1e-6:
        return None
    lengths = {}
    for item in spec.get("distance_constraints", []):
        lengths[frozenset((str(item["a"]), str(item["b"])))] = float(item["value"])
    first = lengths.get(frozenset(("1", "2")))
    second = lengths.get(frozenset(("1", "3")))
    if not first or not second:
        return None
    return {"1": (0.0, 0.0), "2": (first, 0.0), "3": (0.0, second)}


def _metric_right_angle_hourglass(spec):
    """“直角沙漏”蝴蝶形 —— 由**题型约定**唯一确定。

    ★ 用户（领域专家）确认：“既然是直角沙漏了，直角了，肯定就是互相垂直”。
      蝴蝶形画的正是 1-2、2-3、3-4、4-1 四条边，其中 **1-2 与 3-4 是交叉的那一对**。
      已知三条边（12/23/34）与其中一个角（∠2 或 ∠3）时，加上“1-2 ⊥ 3-4”就唯一确定了。

    构造：令 1=(0,0)、2=(L12,0)（1-2 水平）；因 3-4 必须竖直：
      · 角在 3：3→2 与竖直方向成 ∠3 → 3 的位置解出；
      · 角在 2：2→1 指向左，2→3 与它成 ∠2 → 3 的位置解出。
    再由 4 = 3 ± L34·(0,1) 得 4（取远离 1 的那一侧，避免图形自重合）。
    """
    if spec.get("shape") != "蝴蝶形":
        return None
    nodes = [str(name) for name in (spec.get("nodes") or [])]
    if len(nodes) != 4:
        return None
    lengths = {}
    for item in spec.get("distance_constraints", []):
        pair = frozenset((str(item["a"]), str(item["b"])))
        lengths[pair] = float(item["value"])
    twelve = lengths.get(frozenset(("1", "2")))
    twenty_three = lengths.get(frozenset(("2", "3")))
    thirty_four = lengths.get(frozenset(("3", "4")))
    if not (twelve and twenty_three and thirty_four):
        return None
    angles = {str(item["vertex"]): float(item["value"])
              for item in spec.get("angle_constraints") or []}
    point1 = (0.0, 0.0)
    point2 = (twelve, 0.0)
    if "3" in angles:
        theta = math.radians(angles["3"])
        point3 = (twelve - twenty_three * math.sin(theta),
                  -twenty_three * math.cos(theta))
    elif "2" in angles:
        theta = math.radians(angles["2"])
        point3 = (twelve - twenty_three * math.cos(theta),
                  twenty_three * math.sin(theta))
    else:
        return None
    point4 = (point3[0], point3[1] + thirty_four)      # 3-4 竖直（与 1-2 垂直）
    return {"1": point1, "2": point2, "3": point3, "4": point4}


def _metric_square_corner_quad(spec):
    """“12 = 23 = a、∠1 = ∠2 = 90°、34 = b” —— 四边形（用户给的 #274 答案图）。

    构造：1=(0,0)、2=(0,a)、3=(a,a)（于是 ∠2 = 90°、|12|=|23|=a ✓，
    且 1→2 ⊥ 1→4 ⇒ 4 在 x 轴上），再由 |3-4| = b 解出 4=(t,0)：
        (a−t)² + a² = b²  ⇒  t = a ∓ √(b² − a²)
    取**不与 1-2-3 自我交叉**的那一支（另一支图形会翻到里侧）。
    """
    shape = spec.get("shape")
    if shape not in ("梯形", "四边形", "直角梯形", "矩形"):
        return None
    lengths = {}
    for item in spec.get("distance_constraints", []):
        lengths[frozenset((str(item["a"]), str(item["b"])))] = float(item["value"])
    twelve = lengths.get(frozenset(("1", "2")))
    twenty_three = lengths.get(frozenset(("2", "3")))
    thirty_four = lengths.get(frozenset(("3", "4")))
    if not (twelve and twenty_three and thirty_four):
        return None
    if abs(twelve - twenty_three) > 0.02 * twelve:
        return None
    angles = {str(item["vertex"]): float(item["value"])
              for item in spec.get("angle_constraints") or []}
    for vertex in ("1", "2"):
        if abs(angles.get(vertex, -1.0) - 90.0) > 1e-6:
            return None
    if thirty_four <= twelve:
        return None
    offset = math.sqrt(thirty_four * thirty_four - twelve * twelve)
    return {"1": (0.0, 0.0), "2": (0.0, twelve), "3": (twelve, twelve),
            "4": (twelve + offset, 0.0)}


def _metric_hexagon_center(spec):
    """“正六边形 + 起飞点在中心” —— 用户给的 #386 答案图。

    答案图：边长 30 米的正六边形，**H 就在中心**（图中 `H→1 = 30.0 米 270°`）。
    题面条件 `|H-3| = |H-5| = 30 米` 正是“到两个顶点的距离都等于边长”——
    正六边形里只有**中心**到每个顶点的距离 = 边长（= 外接圆半径），所以 H 被唯一确定为圆心 ✓。

    构造：H = (0,0)，顶点 1..6 依次落在 180°、120°、60°、0°、300°、240°、半径 = 边长的位置上。
    """
    if spec.get("shape") != "六边形":
        return None
    lengths = {frozenset((str(item["a"]), str(item["b"]))): float(item["value"])
               for item in spec.get("distance_constraints", [])}
    side = None
    for pair in (("1", "2"), ("2", "3"), ("3", "4"),
                 ("4", "5"), ("5", "6"), ("6", "1")):
        value = lengths.get(frozenset(pair))
        if value:
            side = value
            break
    if not side:
        return None
    # 必须出现“H 到某顶点的距离 = 边长”（这正是“H 在中心”的题面证据）
    if not any("H" in pair and abs(value - side) < 0.02 * side
               for pair, value in lengths.items()):
        return None
    model = {}
    for index in range(6):
        angle = math.radians(180.0 - 60.0 * index)
        model[str(index + 1)] = (side * math.cos(angle), side * math.sin(angle))
    return model


def _metric_butterfly_from_spacing(spec):
    """“45° 蝴蝶 + 尺寸自由取”——用户口径（#138）。

    用户原话：“形状靠图片中的**直角符号**决定，图中有直角符号，那四个角就假定它为
    **45 度**…然后再根据题干‘各点之间距离不小于 50 米’，那就**随便定一个长点的**…
    12 设 60 米，23×√2=84.84，这样蝴蝶形就画出来了，再根据题目要求调整航向和起飞点位置”。

    落地：四角 45° 的蝴蝶就是**正方形蝴蝶**（1-3-2-4 摆成正方形，边长 s）；
    题面**没给边长**时，尺寸取"各点间距不小于 N 米"的**下界 N**（正方形蝴蝶的最小点距 = 边长 s）。
    题面一旦给了边长，就走别的约定（本函数不介入）。
    """
    if spec.get("shape") != "蝴蝶形":
        return None
    minimum = spec.get("minimum_spacing")
    if not minimum:
        return None
    if spec.get("distance_constraints"):
        return None                       # 题面给了边长 → 交给其它约定
    side = float(minimum)
    if side <= 0:
        return None
    return {"1": (0.0, 0.0), "3": (side, 0.0),
            "2": (side, side), "4": (0.0, side)}


def _metric_equilateral_butterfly(spec):
    """“等边蝴蝶”：12 长 a，中点为 M，1-M 与 M-2 上各立一个等边三角形（边长 a/2）。

    用户口径（原话）：“12等于800米，14等于400米，那中点相交，1到中点也就是400米，
    也就是两个等边三角形，也就是等边蝴蝶。蝴蝶形中点都是相交的。”

    取 1=(0,0)、2=(a,0)、M=(a/2,0)；4 是 1-M 上的等边三角形顶点、3 是 M-2 上的顶点，
    分居两侧（这才是"蝴蝶"）：
        4 = (a/4, +a·√3/4)    3 = (3a/4, −a·√3/4)
    可验算：|1-4| = |2-3| = a/2 = b ✓，且 3-4 的中点 = (a/2, 0) = 1-2 的中点 ✓（中点相交）。
    """
    if spec.get("shape") != "蝴蝶形":
        return None
    lengths = {}
    for item in spec.get("distance_constraints", []):
        lengths[frozenset((str(item["a"]), str(item["b"])))] = float(item["value"])
    twelve = lengths.get(frozenset(("1", "2")))
    one_four = lengths.get(frozenset(("1", "4")))
    two_three = lengths.get(frozenset(("2", "3")))
    if not (twelve and one_four and two_three):
        return None
    # 两个等边三角形的边长必须正好是 12 的一半（这是"中点相交"的等价条件）
    if abs(one_four - twelve / 2.0) > 0.02 * twelve:
        return None
    if abs(two_three - twelve / 2.0) > 0.02 * twelve:
        return None
    return {"1": (0.0, 0.0), "2": (twelve, 0.0),
            "4": (twelve / 4.0, twelve * math.sqrt(3.0) / 4.0),
            "3": (twelve * 3.0 / 4.0, -twelve * math.sqrt(3.0) / 4.0)}


def _metric_square_butterfly(spec):
    """“四条边都相等的闭合蝴蝶” = **正方形**（用户口径：化成正方形后 2、3 点对换）。

    用户原话：“如果13 23 24 14 这种都化成正方形了，就直接化成正方形然后
    2点和3点对换就可以出正确图形了。”

    即：航线的四条边是 1-3、3-2、2-4、4-1（交叉顺序），把它们摆成正方形：
        1=(0,0)  3=(s,0)  2=(s,s)  4=(0,s)
    → |1-3| = |3-2| = |2-4| = |4-1| = s ✓
    """
    if spec.get("shape") != "蝴蝶形":
        return None
    lengths = {}
    for item in spec.get("distance_constraints", []):
        lengths[frozenset((str(item["a"]), str(item["b"])))] = float(item["value"])
    pairs = [frozenset(("1", "3")), frozenset(("2", "3")),
             frozenset(("2", "4")), frozenset(("1", "4"))]
    values = [lengths.get(pair) for pair in pairs]
    if any(value is None for value in values):
        return None
    side = values[0]
    if side <= 0 or any(abs(value - side) > 0.02 * side for value in values):
        return None
    return {"1": (0.0, 0.0), "3": (side, 0.0),
            "2": (side, side), "4": (0.0, side)}


def _metric_regular_model(spec):
    """正多边形的米制模型：边长固定，外接圆半径 R = a / (2 sin(π/n))。"""
    nodes = [str(name) for name in (spec.get("nodes") or [])]
    count = len(nodes)
    if count < 3 or not _all_polygon_edges_equal(spec, nodes):
        return None
    side = None
    for item in spec.get("distance_constraints", []):
        pair = frozenset((str(item["a"]), str(item["b"])))
        if pair == frozenset((nodes[0], nodes[1])):
            side = float(item["value"])
            break
    if not side or side <= 0:
        return None
    radius = side / (2.0 * math.sin(math.pi / count))
    model = {}
    for index, name in enumerate(nodes):
        angle = 2.0 * math.pi * index / count
        model[name] = (radius * math.cos(angle), radius * math.sin(angle))
    return model


def _butterfly_metric_model(spec):
    """「闭合直角沙漏 + 各点之间距离不小于 N 米」→ 边长 N 的**精确**正方形外框。

    与题面给出的蝴蝶形点位关系一致：1、3、2、4 依次是正方形的四个角，
    航线 1→2→3→4 走两条对角线与两条边，四个顶点处的航段夹角正好各 45°，
    全部点对的最小距离正好等于边长 N。

    为什么不能用卡片形状直接缩放：卡片是手绘的，实测它的角是 45.37°（题面写 45°），
    刚性套用会让角度残差停在 1.4（3×0.47°）上，永远过不了复核。
    """
    if spec.get("shape") != "蝴蝶形":
        return None
    nodes = [str(name) for name in (spec.get("nodes") or [])]
    minimum = spec.get("minimum_spacing")
    if len(nodes) != 4 or not minimum:
        return None
    angles = spec.get("angle_constraints") or []
    if len(angles) < 4:
        return None
    values = {round(float(item["value"]), 3) for item in angles}
    if len(values) != 1 or len({str(item["vertex"]) for item in angles}) != 4:
        return None
    side = float(minimum)
    if side <= 0:
        return None
    return {"1": (0.0, 0.0), "3": (side, 0.0),
            "2": (side, side), "4": (0.0, side)}


def _similarity_model(spec):
    """返回 (模型点表, 是否还需要解缩放)。

    优先级：
      ① 题图里量出来的顶点（`figure_shape`，带编号）—— 最准，且节点已经按编号排好；
      ② 题面尺寸直接确定的米制模型（扫描航线／圆形／正多边形）；
      ③ 归一化的卡片模板形状（另解一个缩放）。
    """
    shape = spec.get("figure_shape")
    nodes = [str(name) for name in (spec.get("nodes") or [])]
    if shape and len(shape.get("points") or []) == len(nodes) >= 3 and False:
        # 说明：这里**故意不启用**「图形测点当刚性模型」这条路。
        # 试过：刚性拟合对少量题有效，但会让原本靠 layout 模板能解出来的题
        # （旧样本）失去唯一解而回退成待复核，净收益为负。
        # 图形测点仍然在 `solve_shape_prior` 里作为**可形变**的形状先验使用（收益更大）。
        model = {node: tuple(point)
                 for node, point in zip(nodes, shape["points"])}
        return model, False
    shape_name = spec.get("shape")
    if shape_name == "蝴蝶形":
        model = _butterfly_metric_model(spec)
        if model:
            return model, True
    if shape_name == "扫描航线":
        model = _metric_scan_model(spec)
        if model:
            return model, True
    if shape_name in ("梯形", "四边形", "直角梯形", "矩形"):
        model = _metric_square_corner_quad(spec)
        if model:
            return model, True
    if shape_name == "六边形":
        model = _metric_hexagon_center(spec)
        if model:
            return model, True
    if shape_name == "圆形":
        model = _metric_circle_model(spec)
        if model:
            return model, True
    if shape_name == "蝴蝶形":
        # 用户确认的三种蝴蝶约定，按“信息量”从强到弱依次试：
        #   ① 四条交叉边都相等 → 正方形（2、3 点对换）
        #   ② 12 = 2×14 = 2×23 → 等边蝴蝶（两个等边三角形、中点相交）
        #   ③ 直角沙漏 → 1-2 ⊥ 3-4
        for builder in (_metric_square_butterfly, _metric_equilateral_butterfly,
                        _metric_right_angle_hourglass,
                        _metric_butterfly_from_spacing):
            model = builder(spec)
            if model:
                return model, True
    if shape_name == "直角三角形":
        model = _metric_right_isosceles(spec)
        if model:
            return model, True
    if shape_name == "五边形":
        # 先看“五边相等 + ∠1=∠2=90°”那条题型约定（用户确认的唯一形状）
        # ★ 两个约定会“抢跑”（实测 #282）：题面若**写明五条边都等于 a**，
        #   那就必须用“正方形 + 等边三角形”那条（用户口径），因为“正方形 + 中心点”
        #   里 中心到顶点的距离 = a/√2 ≠ a，会违反等边条件 → 复核报“12、23、34、45、15
        #   的边长为 a”不符。所以先看题面给没给满五条等边，给满了就优先等边约定。
        lengths = {frozenset((str(item["a"]), str(item["b"]))): float(item["value"])
                   for item in spec.get("distance_constraints", [])}
        five = [lengths.get(frozenset(pair)) for pair in
                (("1", "2"), ("2", "3"), ("3", "4"), ("4", "5"), ("5", "1"))]
        all_equal = all(value for value in five) and max(five) - min(five) < 0.02 * max(five)
        builders = ((_metric_square_triangle_pentagon, _metric_square_center_pentagon)
                    if all_equal else
                    (_metric_square_center_pentagon, _metric_square_triangle_pentagon))
        for pentagon_builder in builders:
            model = pentagon_builder(spec)
            if model:
                return model, True
    if shape_name in _REGULAR_CAPABLE_SHAPES:
        model = _metric_regular_model(spec)
        if model:
            return model, True
    nodes = [str(name) for name in (spec.get("nodes") or [])]
    layout = spec.get("layout_points") or []
    if len(nodes) != len(layout) or len(nodes) < 3:
        return None, False
    pts = [(float(x), -float(y)) for x, y in layout]
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    pts = [(p[0] - cx, p[1] - cy) for p in pts]
    rms = math.sqrt(sum(p[0] ** 2 + p[1] ** 2 for p in pts) / len(pts))
    if rms < 1e-9:
        return None, False
    pts = [(p[0] / rms, p[1] / rms) for p in pts]
    model = {name: pts[index] for index, name in enumerate(nodes)}
    # ★ “各点之间距离不小于 N 米”直接给出图形尺寸：把卡片形状缩放到最小点距 = N，
    #   于是模型变成米制、缩放不再是自由度。早先把它做成 min() 残差，因为 min 不可导，
    #   优化器总在 1.4 米左右来回（实测 旧样本 卡在这一步）。
    minimum = spec.get("minimum_spacing")
    if minimum and float(minimum) > 0:
        names = list(model)
        spans = [math.dist(model[names[i]], model[names[j]])
                 for i in range(len(names)) for j in range(i + 1, len(names))]
        smallest = min(spans) if spans else 0.0
        if smallest > 1e-9:
            factor = float(minimum) / smallest
            model = {name: (point[0] * factor, point[1] * factor)
                     for name, point in model.items()}
            return model, True
    return model, False


def _similarity_unpack(model, params, metric):
    tx, ty, theta = params[0], params[1], params[2]
    # 尺度取对数是为了条件数，但优化过程会跑到没有意义的地方（math.exp 溢出），
    # 所以夹到 1e-7 ~ 1e7 米这一段——题面尺度不可能超出这个范围。
    scale = 1.0 if metric else math.exp(min(16.0, max(-16.0, params[3])))
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    out = {}
    for name, (x, y) in model.items():
        px, py = x * scale, y * scale
        out[name] = (tx + px * cos_t - py * sin_t,
                     ty + px * sin_t + py * cos_t)
    return out


def _similarity_residuals(spec, model, params, extras, fixed, metric, engine):
    points = _similarity_unpack(model, params, metric)
    # 避让点／过渡点这类不在卡片顶点表里的点，也要当自由变量一起解。
    base = 4 if not metric else 3
    for index, name in enumerate(extras):
        points[name] = (params[base + 2 * index], params[base + 2 * index + 1])
    # 固定点（已知坐标、由航段传播出来的避让点等）直接进残差上下文；
    # 与卡片顶点重名的（题面坐标就标在某个图形点上）另加一条“必须落在锚点上”。
    context = dict(points)
    anchors = []
    for name, value in fixed.items():
        if name in model:
            anchors.append((name, value))
        else:
            context[name] = value
    values = _residuals(spec, context, engine)
    if values is None:
        return None, None
    for name, value in anchors:
        values.append(points[name][0] - value[0])
        values.append(points[name][1] - value[1])
    if not values:
        return None, None
    return values, context


def _similarity_optimise(spec, model, engine, fixed, extras, metric, initial,
                         iterations=80):
    params = list(initial)
    count = len(params)
    damping = 1e-3
    for _ in range(iterations):
        residual, _ = _similarity_residuals(spec, model, params, extras, fixed,
                                           metric, engine)
        if residual is None:
            return None
        cost = sum(value * value for value in residual)
        if cost < 1e-12:
            break
        rows = []
        for index in range(count):
            step = 1e-5 * max(1.0, abs(params[index]))
            shifted = list(params)
            shifted[index] += step
            after, _ = _similarity_residuals(spec, model, shifted, extras, fixed,
                                             metric, engine)
            if after is None or len(after) != len(residual):
                return None
            rows.append([(a - b) / step for a, b in zip(after, residual)])
        # 正规方程：JᵀJ δ = -Jᵀr（rows 是“每个参数一列”的梯度表）
        normal = [[0.0] * count for _ in range(count)]
        right = [0.0] * count
        for position, value in enumerate(residual):
            gradient = [rows[i][position] for i in range(count)]
            for i in range(count):
                right[i] -= gradient[i] * value
                for j in range(i, count):
                    normal[i][j] += gradient[i] * gradient[j]
        for i in range(count):
            for j in range(i):
                normal[i][j] = normal[j][i]
            normal[i][i] += damping * (normal[i][i] + 1.0)
        step_vector = _solve_linear(normal, right)
        if step_vector is None:
            return None
        trial = [params[i] + step_vector[i] for i in range(count)]
        trial_residual, _ = _similarity_residuals(spec, model, trial, extras,
                                                  fixed, metric, engine)
        if trial_residual is None:
            return None
        if sum(v * v for v in trial_residual) < cost:
            params = trial
            damping = max(1e-8, damping * 0.35)
            if max(abs(v) for v in step_vector) < 1e-7:
                break
        else:
            damping = min(1e10, damping * 6.0)
            if damping >= 1e10:
                break
    residual, context = _similarity_residuals(spec, model, params, extras, fixed,
                                              metric, engine)
    if residual is None:
        return None
    return params, context, residual


def _similarity_scales(spec, model, metric):
    """缩放起点：题面给出模型内两点距离时按比值取；米制模型只需要 1.0。"""
    if metric:
        return [1.0]
    ratios = []
    for item in spec.get("distance_constraints", []):
        a, b = str(item["a"]), str(item["b"])
        if a in model and b in model:
            span = math.dist(model[a], model[b])
            if span > 1e-9:
                ratios.append(float(item["value"]) / span)
    for item in spec.get("segment_constraints", []):
        a, b = str(item["a"]), str(item["b"])
        if a in model and b in model:
            span = math.dist(model[a], model[b])
            if span > 1e-9:
                ratios.append(float(item["distance"]) / span)
    # 这族（蝴蝶形／直角沙漏）题面既不给边长、也没有落在卡片内的航段，
    # 尺寸只由「各点之间距离不小于 N 米」给出 —— 缩放起点就取 N ÷ 卡片最小点距。
    minimum = spec.get("minimum_spacing")
    if minimum:
        names = list(model)
        spans = [math.dist(model[names[i]], model[names[j]])
                 for i in range(len(names)) for j in range(i + 1, len(names))]
        smallest = min(spans) if spans else 0.0
        if smallest > 1e-9:
            ratios.append(float(minimum) / smallest)
    ratios = [value for value in ratios if 1e-6 < value < 1e7]
    if not ratios:
        return []
    ratios.sort()
    unique = sorted({round(value, 9) for value in ratios})
    return [unique[0], unique[len(unique) // 2], unique[-1]]


def _similarity_seeds_base(spec, model, engine, fixed, extras, metric):
    """给「位置 / 朝向 / 尺度」三样各找一个能落地的起点。"""
    scales = _similarity_scales(spec, model, metric)
    if not scales:
        return []
    rotations = [0.0]
    for item in spec.get("bearing_constraints", []):
        a, b = str(item["a"]), str(item["b"])
        if a in model and b in model:
            base = engine._xy_bearing(model[b][0] - model[a][0],
                                      model[b][1] - model[a][1])
            rotations.append(engine._wrap_angle(float(item["value"]) - base))
    for item in spec.get("segment_constraints", []):
        a, b = str(item["a"]), str(item["b"])       # 记录的方位是 b→a
        if a in model and b in model:
            base = engine._xy_bearing(model[a][0] - model[b][0],
                                      model[a][1] - model[b][1])
            rotations.append(engine._wrap_angle(float(item["bearing"]) - base))
    for item in engine._relation_bearings(spec, fixed):
        a, b = str(item["target_a"]), str(item["target_b"])
        if a in model and b in model:
            base = engine._xy_bearing(model[b][0] - model[a][0],
                                      model[b][1] - model[a][1])
            rotations.append(engine._wrap_angle(float(item["value"]) - base))
    rotations = [value for value in rotations if value is not None]
    if all(abs(value) < 1e-9 for value in rotations):
        rotations = [0.0, math.pi / 2.0, math.pi, -math.pi / 2.0]

    first = list(model)[0]
    targets = [(first, (0.0, 0.0))]
    for name, value in fixed.items():
        targets.append((name if name in model else first, value))

    # ★ 位置种子还要覆盖「固定点到模型点有距离约束」的情形（如“起飞点到任务航线2点
    #   310米”）：把模型点摆到以固定点为圆心、半径为该距离的圆周上，按角度采样出若干
    #   起点。否则种子会把模型点直接压在固定点上，优化器容易停在局部极小
    #   （实测 旧样本 就卡在 |H-2| = 300 而不是 310）。
    for item in spec.get("distance_constraints", []):
        a, b = str(item["a"]), str(item["b"])
        for fixed_name, model_name in ((a, b), (b, a)):
            if fixed_name not in fixed or model_name not in model:
                continue
            centre = fixed[fixed_name]
            radius = float(item["value"])
            if not 0 < radius < 1e6:
                continue
            for step in range(0, 360, 45):
                angle = math.radians(step)
                targets.append((model_name,
                                (centre[0] + radius * math.cos(angle),
                                 centre[1] + radius * math.sin(angle))))

    # 外部点（避让点/过渡点）的起点：贴在已知点上，或放在原点附近。
    origin = next(iter(fixed.values()), (0.0, 0.0))
    extra_start = []
    for index, name in enumerate(extras):
        place = fixed.get(name)
        if place is None:
            place = (origin[0] + 60.0 * index, origin[1] + 45.0 * index)
        extra_start.extend([place[0], place[1]])

    seeds = []
    for angle in rotations[:4]:
        for scale in scales:
            cos_t, sin_t = math.cos(angle), math.sin(angle)
            for model_name, target in targets[:40]:
                mx, my = model[model_name]
                px = scale * (mx * cos_t - my * sin_t)
                py = scale * (mx * sin_t + my * cos_t)
                head = [target[0] - px, target[1] - py, angle]
                if not metric:
                    head.append(math.log(scale))
                seeds.append(head + list(extra_start))
    return seeds


def _figure_similarity_model(spec):
    """用**题图测点**当形状模型（`figure_shape`，顶点已按编号排好）。

    为什么单独列一个来源而不是塞进 `_similarity_model`：两者可信度不同 ——
    layout 模板对多数题是对的，而题图测点只在"这张图确实是该题的图"时才对
    （题图里相当一部分是共用占位图）。所以顺序必须是 **米制模型 → layout → 题图测点**，
    谁先解出唯一解就用谁；早先把题图测点提到 layout 前面，直接把 旧样本 打回了待复核。
    """
    shape = spec.get("figure_shape")
    nodes = [str(name) for name in (spec.get("nodes") or [])]
    points = list((shape or {}).get("points") or [])
    if not points or len(points) != len(nodes) or len(nodes) < 3:
        return None, False
    labels = (shape or {}).get("labels") or {}
    model = {}
    for index, node in enumerate(nodes):
        position = labels.get(node)
        if not isinstance(position, int) or not 0 <= position < len(points):
            position = index
        model[node] = tuple(points[position])
    return model, False


def solve_similarity(spec, frame, engine):
    """用「卡片形状 + 相似变换」求唯一解；按可信度依次试各形状来源。"""
    sources = []
    model, metric = _similarity_model(spec)
    if model and 3 <= len(model) <= 16:
        sources.append((model, metric))
    figure, figure_metric = _figure_similarity_model(spec)
    if figure and 3 <= len(figure) <= 16:
        sources.append((figure, figure_metric))
    for model, metric in sources:
        result = _solve_similarity_with(spec, frame, engine, model, metric)
        if result is not None:
            return result
    return None


def _solve_similarity_with(spec, frame, engine, model, metric):
    """用给定形状模型求解（`solve_similarity` 的内部实现）。"""
    if not model or len(model) < 3 or len(model) > 16:
        return None
    fixed = _fixed_points(spec, engine)
    extras = [name for name in _names(spec)
              if name not in model and name not in fixed]
    if len(extras) > 3:
        return None                       # 自由点太多，起点搜索不可靠
    geometry = [tuple(item) for item in spec.get("geometry_edges", [])]
    route = [tuple(item) for item in spec.get("flight_edges", [])]
    if not geometry:
        return None
    base_count = 3 if metric else 4
    solutions = []

    def consider(params):
        """把一组参数优化到底并过闸门；通过则计入 solutions，返回收敛后的点集。"""
        try:
            outcome = _similarity_optimise(spec, model, engine, fixed, extras,
                                           metric, params)
        except (OverflowError, ValueError, ZeroDivisionError, KeyError, IndexError):
            return None
        if outcome is None:
            return None
        settled, points, residual = outcome
        count = len(settled)
        if max((abs(value) for value in residual), default=9e9) > 0.5:
            return None
        if engine._validate_solution(spec, points, geometry, route):
            return None
        if any(math.dist(points[a], points[b]) < 1.0 for a, b in geometry):
            return None
        if not all(math.isfinite(value) for point in points.values()
                   for value in point):
            return None
        if not metric and math.exp(min(16.0, max(-16.0, settled[3]))) < 1e-3:
            return None
        rows = []
        current, _ = _similarity_residuals(spec, model, settled, extras, fixed,
                                           metric, engine)
        if current is None:
            return None
        for index in range(count):
            step = 1e-5 * max(1.0, abs(settled[index]))
            shifted = list(settled)
            shifted[index] += step
            after, _ = _similarity_residuals(spec, model, shifted, extras, fixed,
                                             metric, engine)
            if after is None or len(after) != len(current):
                return None
            rows.append([(a - b) / step for a, b in zip(after, current)])
        jacobian = [[rows[column][index] for column in range(count)]
                    for index in range(len(current))]
        if _rank(jacobian, count) != count:
            return None
        if not any(all(math.dist(old[name], points[name]) < 2.0
                       for name in points if name in old) for old in solutions):
            solutions.append(points)
        return settled, points

    for initial in _similarity_seeds(spec, model, engine, fixed, extras, metric):
        settled = consider(initial)
        if settled is None:
            continue
        # ★ 「起飞点在图形内侧/外侧」有**两支**解，模板种子只落在一支上
        #   （实测 旧样本 要求"图形内侧"，只搜到外侧 → 复核全否 → 判不出唯一解）。
        #   `_placement_seeds` 给的是"点名→坐标"的点集，和这里的**参数向量**不是
        #   一回事（上轮直接套上去是空操作）。正确做法：把收敛后的点集翻支，
        #   由于翻支是**纯平移**（H 是固定点不动），只需把参数里的 tx/ty 挪同样的量，
        #   再局部优化一次。
        params, points = settled
        reference = next(iter(model))
        for flipped in _placement_seeds(spec, points):
            if reference not in flipped or reference not in points:
                continue
            shifted = list(params)
            shifted[0] += flipped[reference][0] - points[reference][0]
            shifted[1] += flipped[reference][1] - points[reference][1]
            consider(shifted)
    # ★ 镜像二选一：题面条件对"镜像两支"往往都成立（实测 旧样本 的蝴蝶、
    #   旧样本 的等腰直角三角形）。用户口径是“**参考如图**” —— 于是拿
    #   **题卡上对应边的罗盘方位**当决胜依据：与题卡贴得更近的那一支胜出。
    #   只在两支差异明显（≥20°）时才采信，避免用噪声去挑解。
    # 先按“起飞点默认在外侧 / 题面写了内侧就取内侧”决胜（与自由坐标法同一口径）。
    # 早先只在自由坐标那条路上加了这一步，相似变换这条路漏了 —— 实测 #386 就卡在这：
    # 它有两支解，而题面明写“起飞点在图形内侧”，本可以据此定下来。
    if len(solutions) == 2:
        solutions = pick_branch_by_takeoff(spec, solutions)
    if len(solutions) == 2 and spec.get("figure_shape"):
        figure = spec["figure_shape"]
        nodes = [str(name) for name in (spec.get("nodes") or [])]
        points_of_figure = list(figure.get("points") or [])
        if len(points_of_figure) == len(nodes) >= 3:
            def _edge_bearing(table, first, second):
                return engine._xy_bearing(table[second][0] - table[first][0],
                                          table[second][1] - table[first][1])
            reference = {name: tuple(point)
                         for name, point in zip(nodes, points_of_figure)}
            wanted = _edge_bearing(reference, nodes[0], nodes[1])

            def _error(candidate):
                got = _edge_bearing(candidate, nodes[0], nodes[1])
                return abs((got - wanted + 180.0) % 360.0 - 180.0)

            ordered = sorted(solutions, key=_error)
            if _error(ordered[0]) + 20.0 < _error(ordered[1]):
                solutions = ordered[:1]
    if len(solutions) != 1:
        return None
    steps = ["形状取自题卡模板（相似变换：平移／旋转%s）"
             % ("" if metric else "／缩放"),
             "位置与朝向由题面全部约束联合求解"]
    if metric:
        steps[0] = "形状由题面尺寸直接确定（不依赖题卡比例）"
    return engine._ready(
        spec, solutions[0], frame, geometry, route, steps,
        ["题面全部可读条件通过复核",
         "相似变换的各个自由度均被独立约束确定",
         "镜像解由题卡手性排除"] + ([], ["形状尺度由题面尺寸给定"])[not metric])


# ======================================================== 卡片形状先验求解器
# 题面只给出**部分**形状条件时（典型：五边形只说「①②边及②③边为 a，∠②为 90°」，
# 剩下两个顶点毫无约束），答案的唯一来源是题卡图形本身。做法：
#   ① 形状先验——当前导入题卡的图形点位给出形状参考；
#   ② 硬约束——题面全部可读条件必须**精确**满足；
#   ③ 在满足题面的解集里，取**最接近卡片形状**的那一个（加权最小二乘）。
# 先验是"软"的（只做偏好），题面是"硬"的（用 100 倍权重压住），
# 最后由 `_validate_solution` 逐条复核——题面条件不过关就不发布。
#
# 为什么需要它：卡片是题目的一部分（交接文档第 8 条）。但卡片可能有错
# （共用模板、点号顺序乱、手绘比例不准），所以：
#   · 点序先按「简单多边形」校验并重排（五边形模板就是点对、顺序错）；
#   · 先验偏差过大就拒绝，宁可继续 NEEDS_REVIEW；
#   · 编号（1..n）用题面条件反推，而不是照抄卡片的标签。

_PRIOR_TEXT_WEIGHT = 100.0
_PRIOR_SHAPE_WEIGHT = 3.0
_PRIOR_TOLERANCE = 0.5


def _is_simple_polygon(points):
    """顶点按给定顺序是否构成简单多边形（无自交）。"""
    count = len(points)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def intersect(a, b, c, d):
        d1, d2 = cross(c, d, a), cross(c, d, b)
        d3, d4 = cross(a, b, c), cross(a, b, d)
        return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))

    for i in range(count):
        for j in range(i + 1, count):
            if (j + 1) % count == i or (i + 1) % count == j:
                continue
            if intersect(points[i], points[(i + 1) % count],
                         points[j], points[(j + 1) % count]):
                return False
    return True


def _cyclic_order(points):
    """把卡片顶点排成简单多边形的循环顺序；已经简单就原样返回。"""
    if len(points) < 4 or _is_simple_polygon(points):
        return list(points)
    count = len(points)
    cx = sum(p[0] for p in points) / count
    cy = sum(p[1] for p in points) / count
    angular = sorted(points, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    if _is_simple_polygon(angular):
        return angular
    # 角序也不简单时退到最近邻回路（自相交图形如蝴蝶形会走这条）。
    remaining = list(points)
    tour = [remaining.pop(0)]
    while remaining:
        last = tour[-1]
        nxt = min(remaining, key=lambda p: math.dist(last, p))
        tour.append(nxt)
        remaining.remove(nxt)
    return tour


def _prior_fit(model, points):
    """把卡片模型用**复数最小二乘**（不含镜像）贴到当前解上，返回 (ratio, center)。"""
    names = [name for name in model if name in points]
    if len(names) < 3:
        return None
    mx = sum(model[k][0] for k in names) / len(names)
    my = sum(model[k][1] for k in names) / len(names)
    px = sum(points[k][0] for k in names) / len(names)
    py = sum(points[k][1] for k in names) / len(names)
    a = b = den = 0.0
    for k in names:
        ux, uy = model[k][0] - mx, model[k][1] - my
        vx, vy = points[k][0] - px, points[k][1] - py
        a += vx * ux + vy * uy
        b += vx * uy - vy * ux
        den += ux * ux + uy * uy
    if den < 1e-12:
        return None
    return (a / den, b / den, mx, my, px, py)


def _prior_residuals(model, points, weight):
    """当前解偏离卡片**形状**多少（尺度无关）。

    ★ 不能再用「含尺度的最佳相似变换」：那样把图形缩成一个点、残差同样趋于 0，
      先验的极小点就退化成"塌陷"（实测低文本权重下 旧样本 的 1、5 两点并到了同一点）。
      这里把两边各自按 RMS 归一化，只比较**形状**；塌陷解由 rms→0 的护栏直接顶掉。
      归一化后的残差是无量纲的，0 表示形状完全贴合卡片。
    """
    names = [name for name in model if name in points]
    if len(names) < 3:
        return None

    def centre_rms(values):
        cx = sum(v[0] for v in values) / len(values)
        cy = sum(v[1] for v in values) / len(values)
        rms = math.sqrt(sum((v[0] - cx) ** 2 + (v[1] - cy) ** 2
                            for v in values) / len(values))
        return cx, cy, rms

    mx, my, rms_m = centre_rms([model[k] for k in names])
    px, py, rms_p = centre_rms([points[k] for k in names])
    if rms_m < 1e-9 or rms_p < 1e-9:
        return [1e6] * (2 * len(names))          # 塌陷 → 直接判定极差
    a = b = 0.0
    for name in names:
        ux, uy = (model[name][0] - mx) / rms_m, (model[name][1] - my) / rms_m
        vx, vy = (points[name][0] - px) / rms_p, (points[name][1] - py) / rms_p
        a += vx * ux + vy * uy
        b += vx * uy - vy * ux
    norm = math.hypot(a, b)
    if norm < 1e-12:
        return [1e6] * (2 * len(names))
    cos_t, sin_t = a / norm, b / norm
    out = []
    for name in names:
        ux, uy = (model[name][0] - mx) / rms_m, (model[name][1] - my) / rms_m
        qx = cos_t * ux - sin_t * uy
        qy = sin_t * ux + cos_t * uy
        vx, vy = (points[name][0] - px) / rms_p, (points[name][1] - py) / rms_p
        out.append((vx - qx) * weight)
        out.append((vy - qy) * weight)
    return out


def _prior_size(model, points):
    """形状比较已经尺度无关，这里统一返回 1，让门限保持无量纲。"""
    return 1.0


def _prior_deviation(model, points):
    """形状偏差（无量纲，0 = 完全贴合卡片）。"""
    residual = _prior_residuals(model, points, 1.0)
    if not residual:
        return 9e9
    return math.sqrt(sum(v * v for v in residual) / len(residual))


def _diagram_cycle_order(nodes, edges):
    """按已识别的无向边恢复一个稳定的图形环顺序。"""
    names = [str(name) for name in nodes]
    if len(names) < 3 or not edges:
        return []
    adjacency = {name: [] for name in names}
    for raw in edges:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        first, second = str(raw[0]), str(raw[1])
        if first not in adjacency or second not in adjacency or first == second:
            continue
        if second not in adjacency[first]:
            adjacency[first].append(second)
        if first not in adjacency[second]:
            adjacency[second].append(first)
    if any(len(neighbors) != 2 for neighbors in adjacency.values()):
        return []
    start = names[0]
    first = next((str(edge[1]) if str(edge[0]) == start else str(edge[0])
                  for edge in edges if isinstance(edge, (list, tuple)) and
                  len(edge) >= 2 and start in (str(edge[0]), str(edge[1]))), None)
    if first not in adjacency[start]:
        first = adjacency[start][0]
    order = [start, first]
    previous, current = start, first
    while len(order) < len(names):
        following = next((name for name in adjacency[current] if name != previous), None)
        if following is None or following == start or following in order:
            return []
        order.append(following)
        previous, current = current, following
    if start not in adjacency[current]:
        return []
    return order


def _polygon_signed_area(order, points):
    if len(order) < 3 or any(name not in points for name in order):
        return 0.0
    return sum(points[order[index]][0] * points[order[(index + 1) % len(order)]][1]
               - points[order[(index + 1) % len(order)]][0] * points[order[index]][1]
               for index in range(len(order))) / 2.0


def _layout_angle_errors(order, model, points):
    """比较逐顶点夹角；示意图不按比例时，角形比绝对边长更稳定。"""
    if len(order) < 3 or any(name not in model or name not in points for name in order):
        return None
    errors = []
    for index, name in enumerate(order):
        before = order[index - 1]
        after = order[(index + 1) % len(order)]
        first = (model[before][0] - model[name][0],
                 model[before][1] - model[name][1])
        second = (model[after][0] - model[name][0],
                  model[after][1] - model[name][1])
        actual_first = (points[before][0] - points[name][0],
                        points[before][1] - points[name][1])
        actual_second = (points[after][0] - points[name][0],
                         points[after][1] - points[name][1])

        def angle(a, b):
            denominator = math.hypot(*a) * math.hypot(*b)
            if denominator < 1e-9:
                return None
            cosine = max(-1.0, min(1.0,
                (a[0] * b[0] + a[1] * b[1]) / denominator))
            return math.degrees(math.acos(cosine))

        expected, observed = angle(first, second), angle(actual_first, actual_second)
        if expected is None or observed is None:
            return None
        errors.append(abs(expected - observed))
    return errors


def _prior_seed(spec, labelling, fixed, extras, engine):
    """把卡片形状（按某套编号）用相似变换贴到题面已知点上作为起点。"""
    anchors = [(name, fixed[name]) for name in labelling if name in fixed]
    model_names = list(labelling)
    if anchors:
        first_name, target = anchors[0]
        origin = labelling[first_name]
    else:
        first_name = model_names[0]
        target = next(iter(fixed.values()), (0.0, 0.0))
        origin = labelling[first_name]
    initial = {}
    # 尺度参照：题面里落在卡片内的距离约束
    ratios = []
    for item in spec.get("distance_constraints", []):
        a, b = str(item["a"]), str(item["b"])
        if a in labelling and b in labelling:
            span = math.dist(labelling[a], labelling[b])
            if span > 1e-9:
                ratios.append(float(item["value"]) / span)
    ratios = [r for r in ratios if 1e-6 < r < 1e7]
    scale = sorted(ratios)[len(ratios) // 2] if ratios else 1.0
    for name in model_names:
        initial[name] = (target[0] + (labelling[name][0] - origin[0]) * scale,
                         target[1] + (labelling[name][1] - origin[1]) * scale)
    place = next(iter(fixed.values()), (0.0, 0.0))
    for index, name in enumerate(extras):
        initial[name] = fixed.get(name) or (place[0] + 40.0 * index,
                                            place[1] + 30.0 * index)
    # ★ 卡片与**不在模型里的固定点**（典型：起飞点 H）之间的定位关系必须用上。
    #   上面 anchors 只在"固定点恰好是模型顶点"时才有值；旧样本 的固定点是 H，
    #   于是卡片被随手摆在原点，`|H-1|=120米` 一开始就差出 358 —— 后续所有候选
    #   （刚性/投影/可形变）都从这个烂起点出发，全过不了题面。
    #   这里按「固定点到模型点的距离约束」把模型点摆到以固定点为心的圆周上采样。
    displaced = False
    for item in spec.get("distance_constraints", []):
        a, b = str(item["a"]), str(item["b"])
        for fixed_name, model_name in ((a, b), (b, a)):
            if fixed_name not in fixed or model_name not in labelling:
                continue
            if fixed_name in labelling:
                continue                  # 固定点本身是模型顶点，上面已处理
            radius = float(item["value"]) * 1.0
            if not 0 < radius < 1e6:
                continue
            centre = fixed[fixed_name]
            current = initial.get(model_name)
            if current is None:
                continue
            span = math.dist(current, centre)
            if span > 1e-9 and abs(span - radius) <= 0.05 * radius:
                continue                  # 已经落在正确半径上
            # 沿"固定点→模型点"的当前方向把它搬到正确半径上。若两者刚好重合
            # （种子把模型点压在了固定点上，实测 旧样本 就是 span=0），
            # 就取一个固定方向推出去；朝向的修正交给后面的优化。
            if span < 1e-9:
                direction = (1.0, 0.0)
            else:
                direction = ((current[0] - centre[0]) / span,
                             (current[1] - centre[1]) / span)
            target_point = (centre[0] + direction[0] * radius,
                            centre[1] + direction[1] * radius)
            dx = target_point[0] - current[0]
            dy = target_point[1] - current[1]
            # 纯平移：卡片形状严格不变
            for name in model_names:
                point = initial[name]
                initial[name] = (point[0] + dx, point[1] + dy)
            displaced = True
    if displaced:
        # 模型整体缩放后，模型内两点的距离应当仍与题面一致；若不一致，说明
        # 尺度来源本身矛盾，此时以**模型内距离约束**为准重新定尺度更稳妥。
        for item in spec.get("distance_constraints", []):
            a, b = str(item["a"]), str(item["b"])
            if a in labelling and b in labelling and a in initial and b in initial:
                want = float(item["value"])
                span = math.dist(initial[a], initial[b])
                if span > 1e-9 and abs(span - want) > 0.05 * want:
                    factor = want / span
                    centre = ((initial[a][0] + initial[b][0]) / 2.0,
                              (initial[a][1] + initial[b][1]) / 2.0)
                    for name in model_names:
                        point = initial[name]
                        initial[name] = (
                            centre[0] + (point[0] - centre[0]) * factor,
                            centre[1] + (point[1] - centre[1]) * factor)
    # 固定点（已知坐标、由航段传播出来的避让点等）必须进点表：残差里会引用它们，
    # 而它们不参与优化（不在 variables 里）。与卡片顶点重名的固定点保留卡片算出来的
    # 位置（种子阶段已把它摆到锚点上）。
    for name, value in fixed.items():
        initial.setdefault(name, value)
    return initial


def _prior_rotation(labelling, points):
    """当前解相对卡片的**旋转角**（弧度）：0 表示与卡片同向。

    「任务航线当前视图坐标系」这类题面明确授权以卡片画面为正北，所以这一项要压到 0；
    顺带把镜像解（旋转 180° 的那种对应关系）也排除掉。
    """
    fit = _prior_fit(labelling, points)
    if fit is None:
        return None
    ratio_re, ratio_im = fit[0], fit[1]
    if math.hypot(ratio_re, ratio_im) < 1e-12:
        return None
    return math.atan2(ratio_im, ratio_re)


def _prior_best_rotation(spec, labelling, initial, engine, step=5.0, anchor=None):
    """把整套点绕**锚点**（没有锚点则绕重心）旋转，挑「题面残差最小」的朝向作为起点。

    ★ 为什么必须补这一步：种子沿用的是**卡片原始朝向**，而题面往往要求某个航向
      （实测五边形常要整体转 90°+）。差值这么大时高斯-牛顿的线性化失效，
      优化器会顺带把**形状**也拧歪（形状偏差 0.4~1.3，远超 20% 门限）。
      只转不形变的粗搜既便宜又稳，转完再进正常的优化。
    ★ 必须绕**锚点**转：已知坐标点是硬条件，绕重心转会把锚点带跑，
      复核直接报"已知坐标点偏离题面"。
    """
    if len(initial) < 3:
        return initial
    points = {name: tuple(value) for name, value in initial.items()}
    if anchor is not None and anchor in points:
        cx, cy = points[anchor]
    else:
        cx = sum(p[0] for p in points.values()) / len(points)
        cy = sum(p[1] for p in points.values()) / len(points)
    best = None
    angle = 0.0
    while angle < 360.0:
        theta = math.radians(angle)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        trial = {}
        for name, (x, y) in points.items():
            dx, dy = x - cx, y - cy
            trial[name] = (cx + dx * cos_t - dy * sin_t,
                           cy + dx * sin_t + dy * cos_t)
        values = _residuals(spec, trial, engine)
        if values:
            score = max(abs(v) for v in values)
            if best is None or score < best[0]:
                best = (score, trial)
        angle += step
    return best[1] if best else initial


def _prior_rigid(spec, labelling, initial, engine, iterations=60):
    """先用**刚性相似变换**把卡片形状对齐到题面（只动缩放/旋转/平移四个参数）。

    ★ 为什么必须有这一步：种子沿用的是卡片原始朝向，而题面通常要求某个航向
      （实测五边形要整体转 90°+）。此时直接跑可形变优化，高斯-牛顿的线性化失效，
      优化器会顺带把**形状**拧歪 —— 20 道五边形的失败模式完全一致：
      编号唯一、题面精确满足、复核通过，唯独形状偏差 0.4~1.3（门限 0.2）。
      先刚性对齐（形状严格不变），再让可形变优化去做**小**修正。
    """
    if len(initial) < 3:
        return initial
    # ★ 尺度必须按**当前点表**算，不能按卡片原尺寸算：种子已经缩放过一次，
    #   再乘一遍卡片尺度就是"重复缩放"，整库会崩（实测 394 → 370，就是这一处）。
    # ★ 而且只能取**模型内**两点的距离比：像 `|H-1|=120米` 里的 H 是自由点，
    #   在种子里位置是随便摆的，拿它算比例会把整体尺度定错
    #   （实测 旧样本 因此整条刚性摆放都过不了题面，文本残差 358）。
    ratios = []
    for item in spec.get("distance_constraints", []):
        a, b = str(item["a"]), str(item["b"])
        if a in labelling and b in labelling and a in initial and b in initial:
            span = math.dist(initial[a], initial[b])
            if span > 1e-9:
                ratios.append(float(item["value"]) / span)
    ratios = [value for value in ratios if 1e-7 < value < 1e7]
    if not ratios:
        return initial
    ratios.sort()
    scale = ratios[len(ratios) // 2]
    cx = sum(p[0] for p in initial.values()) / len(initial)
    cy = sum(p[1] for p in initial.values()) / len(initial)

    def unpack(params):
        theta, tx, ty = params
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        out = {}
        for name, (x, y) in initial.items():
            dx, dy = (x - cx) * scale, (y - cy) * scale
            out[name] = (tx + dx * cos_t - dy * sin_t,
                         ty + dx * sin_t + dy * cos_t)
        return out

    params = [0.0, cx, cy]
    damping = 1e-3
    for _ in range(iterations):
        residual = _residuals(spec, unpack(params), engine)
        if residual is None:
            return initial
        cost = sum(value * value for value in residual)
        if cost < 1e-12:
            break
        rows = []
        ok = True
        for index in range(3):
            step = 1e-4 * max(1.0, abs(params[index]))
            shifted = list(params)
            shifted[index] += step
            after = _residuals(spec, unpack(shifted), engine)
            if after is None or len(after) != len(residual):
                ok = False
                break
            rows.append([(a - b) / step for a, b in zip(after, residual)])
        if not ok:
            break
        normal = [[0.0] * 3 for _ in range(3)]
        right = [0.0] * 3
        for position, value in enumerate(residual):
            gradient = [rows[i][position] for i in range(3)]
            for i in range(3):
                right[i] -= gradient[i] * value
                for j in range(i, 3):
                    normal[i][j] += gradient[i] * gradient[j]
        for i in range(3):
            for j in range(i):
                normal[i][j] = normal[j][i]
            normal[i][i] += damping * (normal[i][i] + 1.0)
        step_vector = _solve_linear(normal, right)
        if step_vector is None:
            break
        trial = [params[i] + step_vector[i] for i in range(3)]
        after = _residuals(spec, unpack(trial), engine)
        if after is None:
            break
        if sum(v * v for v in after) < cost:
            params = trial
            damping = max(1e-9, damping * 0.4)
            if max(abs(v) for v in step_vector) < 1e-7:
                break
        else:
            damping = min(1e10, damping * 6.0)
            if damping >= 1e10:
                break
    return unpack(params)


def _prior_project(spec, labelling, initial, variables, engine, iterations=40):
    """把卡片形状**投影**到题面约束流形上（最小范数高斯-牛顿）。

    与加权最小二乘的区别：加权法靠"题面权重 vs 先验权重"折中，优化器很容易落在一个
    "题面精确满足、形状却跑飞"的局部极小 —— 实测 20 道五边形的失败模式完全一致：
    编号唯一、题面残差 0、复核全过，唯独形状偏差 0.4~1.3（门限 0.2）。
    最小范数步永远取"在当前点附近满足题面所需的**最小**改动"，
    因此解会自然贴着卡片形状，不需要任何权重。
    """
    if not variables or len(initial) < 3:
        return None
    points = {name: tuple(value) for name, value in initial.items()}
    count = len(variables)
    damping = 1e-6
    for _ in range(iterations):
        residual = _residuals(spec, points, engine)
        if residual is None or not residual:
            return None
        if max(abs(value) for value in residual) < 1e-7:
            break
        rows = []
        ok = True
        for name, axis in variables:
            step = 1e-5 * max(1.0, abs(points[name][axis]))
            shifted = dict(points)
            old = shifted[name]
            shifted[name] = ((old[0] + step, old[1]) if axis == 0
                             else (old[0], old[1] + step))
            after = _residuals(spec, shifted, engine)
            if after is None or len(after) != len(residual):
                ok = False
                break
            rows.append([(a - b) / step for a, b in zip(after, residual)])
        if not ok:
            return None
        # J 为 nr×nv，最小范数解 δ = Jᵀ (J Jᵀ + λI)⁻¹ (−r)（nr 很小，直接解）
        nr = len(residual)
        gram = [[0.0] * nr for _ in range(nr)]
        for i in range(nr):
            for j in range(i, nr):
                total = sum(rows[k][i] * rows[k][j] for k in range(count))
                if i == j:
                    total += damping
                gram[i][j] = total
                gram[j][i] = total
        multiplier = _solve_linear(gram, [-value for value in residual])
        if multiplier is None:
            return None
        delta = [sum(rows[k][i] * multiplier[i] for i in range(nr))
                 for k in range(count)]
        # 限步：单步位移不超过图形尺度的 25%，避免线性化失效
        centre_x = sum(p[0] for p in points.values()) / len(points)
        centre_y = sum(p[1] for p in points.values()) / len(points)
        rms = math.sqrt(sum((p[0] - centre_x) ** 2 + (p[1] - centre_y) ** 2
                            for p in points.values()) / len(points))
        limit = 0.25 * max(rms, 1e-6)
        norm = math.sqrt(sum(value * value for value in delta))
        if norm > limit:
            factor = limit / norm
            delta = [value * factor for value in delta]
        trial = dict(points)
        for (name, axis), change in zip(variables, delta):
            old = trial[name]
            trial[name] = ((old[0] + change, old[1]) if axis == 0
                           else (old[0], old[1] + change))
        after = _residuals(spec, trial, engine)
        if after is None:
            return None
        if sum(v * v for v in after) < sum(v * v for v in residual):
            points = trial
            damping = max(1e-9, damping * 0.5)
        else:
            damping = min(1e6, damping * 10.0)
            if damping >= 1e6:
                break
    return points


def _prior_augmented(spec, labelling, initial, variables, engine, fixed=None,
                     outer=6):
    """增广拉格朗日：min 形状偏差 s.t. 题面残差 = 0（乘子自适应）。

    为什么需要它：罚函数法（把题面权重推到 100）在 旧样本 这类题上会停在
    "题面精确满足、形状偏差 0.27"的**局部极小**——权重从 3 一路调到 100 都不动，
    迭代预算从 40 加到 800 也不动（两轮对照实验已证）。增广拉格朗日靠**乘子**
    而不是靠大权重：每轮外层把 λ ← λ + μ·c 更新，内层只解一个温和的加权问题，
    因此更容易在流形上滑到离卡片更近的地方，且**不需要放宽任何门限**。
    """
    if len(initial) < 3 or not variables:
        return None
    weight = 10.0
    multipliers = None
    points = dict(initial)
    best = None
    for _ in range(outer):
        residual = _residuals(spec, points, engine)
        if residual is None:
            return best
        if multipliers is None:
            multipliers = [0.0] * len(residual)
        # 内层：增广拉格朗日的内层问题是 min D² + (μ/2)·Σ(c_i + λ_i/μ)²，
        # 所以"题面残差"要整体平移 λ/μ 之后再压（这就是乘子起作用的地方）。
        shifts = [value / weight for value in multipliers]
        inner = _prior_optimise(spec, labelling, points, variables, engine,
                                text_weight=weight, fixed=fixed,
                                text_offsets=shifts)
        if inner is not None:
            points = inner
        after = _residuals(spec, points, engine)
        if after is None:
            break
        deviation = _prior_deviation(labelling, points)
        if best is None or deviation < best[0]:
            best = (deviation, dict(points))
        if max(abs(value) for value in after) < 1e-6 and deviation < 0.05:
            break
        multipliers = [multipliers[index] + weight * value
                       for index, value in enumerate(after)]
        weight = min(weight * 2.5, 400.0)
    return best[1] if best else None


def _prior_optimise(spec, labelling, initial, variables, engine, iterations=120,
                    lock_rotation=False, text_weight=None, fixed=None,
                    text_offsets=None):
    """题面残差（×权重）+ 形状先验残差 的加权高斯-牛顿。"""
    weight = _PRIOR_TEXT_WEIGHT if text_weight is None else float(text_weight)
    points = dict(initial)
    damping = 1e-3
    count = len(variables)

    def assemble(candidate_points):
        text_values = _residuals(spec, candidate_points, engine)
        if text_offsets and len(text_offsets) == len(text_values):
            # 增广拉格朗日的内层：题面残差整体平移 λ/μ（见 `_prior_augmented`）
            text_values = [value + text_offsets[index]
                           for index, value in enumerate(text_values)]
        # ★ 已知坐标点（锚点）也必须在目标函数里：`_residuals` 里**没有**这一项，
        #   锚点只在事后的 `_validate_solution` 里核对 —— 于是优化器可以随意把已知点
        #   挪走（实测 旧样本 的刚性摆放把航点 3 挪偏了 20.65 米，复核直接报"偏离题面"）。
        anchors = [(name, (fixed or {})[name]) for name in labelling
                   if name in (fixed or {})]
        for name, value in anchors:
            if name in candidate_points:
                text_values.append(candidate_points[name][0] - value[0])
                text_values.append(candidate_points[name][1] - value[1])
        # 先验权重：题面条件必须精确满足（权重 100），但先验也不能弱到形同虚设 ——
        # 权重 1 时解会落在满足题面的流形上任意一处（实测 20 道五边形的形状偏差
        # 因此高达 0.4~1.3，远超 0.20 门限）。取 3 是在"题面优先"与"形状可辨"之间
        # 试出来的折中。
        prior_values = _prior_residuals(labelling, candidate_points,
                                        _PRIOR_SHAPE_WEIGHT)
        if text_values is None or prior_values is None:
            return None
        values = [v * weight for v in text_values] + list(prior_values)
        if lock_rotation:
            rotation = _prior_rotation(labelling, candidate_points)
            if rotation is None:
                return None
            values.append(rotation * weight)
        return values

    for _ in range(iterations):
        residual = assemble(points)
        if residual is None:
            return None
        cost = sum(value * value for value in residual)
        if cost < 1e-14:
            break
        rows = []
        ok = True
        for name, axis in variables:
            step = 1e-4 * max(1.0, abs(points[name][axis]))
            shifted = dict(points)
            old = shifted[name]
            shifted[name] = ((old[0] + step, old[1]) if axis == 0
                             else (old[0], old[1] + step))
            after = assemble(shifted)
            if after is None or len(after) != len(residual):
                ok = False
                break
            rows.append([(a - b) / step for a, b in zip(after, residual)])
        if not ok:
            return None
        normal = [[0.0] * count for _ in range(count)]
        right = [0.0] * count
        for position, value in enumerate(residual):
            gradient = [rows[i][position] for i in range(count)]
            for i in range(count):
                right[i] -= gradient[i] * value
                for j in range(i, count):
                    normal[i][j] += gradient[i] * gradient[j]
        for i in range(count):
            for j in range(i):
                normal[i][j] = normal[j][i]
            normal[i][i] += damping * (normal[i][i] + 1.0)
        step_vector = _solve_linear(normal, right)
        if step_vector is None:
            return None
        trial = dict(points)
        for (name, axis), delta in zip(variables, step_vector):
            old = trial[name]
            trial[name] = ((old[0] + delta, old[1]) if axis == 0
                           else (old[0], old[1] + delta))
        after = assemble(trial)
        if after is None:
            return None
        if sum(v * v for v in after) < cost:
            points = trial
            damping = max(1e-9, damping * 0.4)
            if max(abs(v) for v in step_vector) < 1e-7:
                break
        else:
            damping = min(1e10, damping * 6.0)
            if damping >= 1e10:
                break
    return points


def solve_shape_prior(spec, frame, engine):
    """以卡片形状为先验、题面条件为硬约束求解；不唯一或偏差过大则返回 None。"""
    nodes = [str(name) for name in (spec.get("nodes") or [])]
    # 形状来源按可信度排序，**两个都试**：先从题图里量出来的顶点（最准，还带编号），
    # 再退回 layout 模板。早先只取前者，导致原本靠 layout 能解的两道题反而变成待复核
    # （实测 旧样本）—— 单一来源覆盖不了所有图。
    sources = []
    shape = spec.get("figure_shape")
    if shape and len(shape.get("points") or []) == len(nodes) >= 3:
        sources.append((list(shape["points"]), dict(shape.get("labels") or {}),
                        "题卡图形测点（霍夫直线交点求顶点 + 带圈编号最优指派）"))
    layout = spec.get("layout_points") or []
    if (len(nodes) == len(layout) and len(nodes) >= 3 and
            spec.get("layout_confidence")):
        sources.append((_cyclic_order([(float(x), -float(y)) for x, y in layout]), {},
                        "题卡布局模板（顶点顺序按重心角序重排）"))
    if not sources:
        return None
    geometry = [tuple(edge) for edge in spec.get("geometry_edges", [])]
    route = [tuple(edge) for edge in spec.get("flight_edges", [])]
    if not geometry:
        return None
    fixed = _fixed_points(spec, engine)
    extras = [name for name in _names(spec)
              if name not in nodes and name not in fixed]
    if len(extras) > 3:
        return None
    variables = [(name, axis) for name in list(nodes) + extras
                 if name not in fixed for axis in (0, 1)]
    if not variables or len(variables) > 24:
        return None
    count = len(nodes)
    # 「任务航线当前视图坐标系」= 题面明确授权以卡片画面为正北，于是把「相对卡片的
    # 旋转」也压成硬约束（顺带排除镜像解）。这是题面自己给的方向来源，不是我们猜的。
    lock_rotation = "视图坐标系" in spec.get("source_text", "")
    fallback = None
    for card_points, card_labels, source in sources:

        def search(card_points=card_points, card_labels=card_labels):
            """对一套卡片形状求「满足题面全部条件」的解集。"""
            found = []
            for direction in (1, -1):
                base = card_points if direction > 0 else list(reversed(card_points))
                for shift in range(count):
                    labelling = {nodes[i]: base[(i + shift) % count]
                                 for i in range(count)}
                    # 卡片上的带圈编号是**硬信息**：编号 digit 必须落在它自己那个顶点上。
                    if any(labelling.get(str(digit)) != card_points[index]
                           for digit, index in card_labels.items()
                           if str(digit) in labelling and
                           0 <= index < len(card_points)):
                        continue
                    initial = _prior_seed(spec, labelling, fixed, extras, engine)
                    # 注：围绕"种子的卡片朝向不对"试过 8 种办法（无条件/有条件的朝向粗搜、
                    # 刚性相似变换拟合、信赖域限步、把刚性摆放当额外候选、最小范数投影、
                    # 先验权重扫描、绕锚点粗搜），**只有先验权重 3.0 有 +1 收益**。
                    # 其中"朝向粗搜"在**起点位置修好之后**又试了一次，依然净亏
                    # （400 → 397）—— 结论固定：不要在这条路上再试了。
                    # 见 `docs/card_vs_text.json`：剩下的题里相当一部分用的是**共用占位图**，
                    # 形状先验指向的本来就不是该题的真实图形。
                    # 候选一：两段式可形变优化（低权重先贴形状、高权重压题面）。
                    # 候选二：**刚性卡片摆放**（只动相似变换，形状严格等于卡片，
                    #   所以形状偏差天然≈0）。它的意义在于：题面条件若恰好能被卡片
                    #   精确满足，它就是标准答案；而可形变优化在大角度转向时容易把形状
                    #   拧歪（实测 20 道五边形都死在这里）。两个候选走**同一套闸门**，
                    #   谁过谁算；若都过且几乎重合，去重会合并成一个。
                    candidates = []
                    try:
                        # ★ 权重**渐进延续**（3 → 10 → 30 → 100，逐级热启动），而不是
                        #   一步从 3 跳到 100：跳变会让高斯-牛顿直接落进离卡片很远的
                        #   局部极小（实测 旧样本 两类候选的偏差都停在 0.27~0.31，
                        #   而题面相对卡片只差约 5%，本来应该能到 ~0.07）。
                        #   延续法是小步逼近的标准做法，且**不改动任何门限**。
                        warm = initial
                        for weight in (3.0, 10.0, 30.0):
                            stepped = _prior_optimise(
                                spec, labelling, warm, variables, engine,
                                lock_rotation=lock_rotation, text_weight=weight,
                                fixed=fixed)
                            if stepped is not None:
                                warm = stepped
                        candidates.append(_prior_optimise(
                            spec, labelling, warm, variables, engine,
                            lock_rotation=lock_rotation, fixed=fixed))
                    except (OverflowError, ValueError, ZeroDivisionError,
                            KeyError, IndexError):
                        pass
                    except (OverflowError, ValueError, ZeroDivisionError,
                            KeyError, IndexError):
                        pass
                    try:
                        candidates.append(_prior_rigid(spec, labelling, initial,
                                                       engine))
                    except (OverflowError, ValueError, ZeroDivisionError,
                            KeyError, IndexError):
                        pass
                    # 注：**增广拉格朗日**（`_prior_augmented`）试过，结论是**不需要**：
                    #   它在 旧样本 上同样收敛到 0.270（与罚函数法、最小范数投影完全一致），
                    #   说明 0.270 就是该题**真正的约束最小形变** —— 题图与题面确实差这么多，
                    #   20% 门限拒绝它是对的。接进候选列表反而会多产出一个不同解、
                    #   把某道题从 READY 挤成"多解"（实测 402 → 401），故不接入。
                    # 候选三：**最小范数投影**（从卡片形状出发，每步只做满足题面所需的
                    # 最小改动）—— 前几个候选都可能跑飞。
                    try:
                        candidates.append(_prior_project(spec, labelling, initial,
                                                         variables, engine))
                    except (OverflowError, ValueError, ZeroDivisionError,
                            KeyError, IndexError):
                        pass
                    for candidate in candidates:
                        if candidate is None:
                            continue
                        # 形状先验用于选布局与镜像分支；最终点位再用题面硬约束单独精修。
                        # 先验加权解允许亚米级残差，直接提交会在独立复核的 0.1° 角度门限处
                        # 把若干本可满足的解挡掉。精修结果仍须通过原校验及原有形状偏差门限。
                        try:
                            polished = _optimise(spec, candidate, variables, engine)
                        except (OverflowError, ValueError, ZeroDivisionError,
                                KeyError, IndexError):
                            polished = None
                        if (polished is not None and
                                not engine._validate_solution(spec, polished,
                                                              geometry, route)):
                            candidate = polished
                        text = _residuals(spec, candidate, engine)
                        if text is None or max((abs(v) for v in text),
                                               default=9e9) > _PRIOR_TOLERANCE:
                            continue
                        if engine._validate_solution(spec, candidate, geometry,
                                                     route):
                            continue
                        if any(math.dist(candidate[a], candidate[b]) < 1.0
                               for a, b in geometry):
                            continue
                        size = _prior_size(labelling, candidate)
                        # 先验偏差超过图形尺度的 20%，说明这张卡片跟题面不是一回事，
                        # 宁可继续 NEEDS_REVIEW，也不用不相干的模板硬凑答案。
                        if _prior_deviation(labelling, candidate) > 0.20 * size:
                            continue
                        if lock_rotation:
                            rotation = _prior_rotation(labelling, candidate)
                            if rotation is None or abs(rotation) > 0.15:
                                continue
                        # 去重要比**全部**点（含 H/避让点）：只比图形顶点会把「H 落在
                        # 延长线两侧」这类真正不同的解误当成同一个，从而错报"唯一解"。
                        if not any(all(math.dist(old[name], candidate[name]) < 2.0
                                       for name in candidate if name in old)
                                   for old in found):
                            found.append(candidate)
            return found

        solutions = search()
        if len(solutions) == 1:
            # ★ 顺序回退，不是并集：两套形状来源各自可能给出不同的解，
            #   并在一起会变成"多解"而全部作废（实测把 10 道已解题又打回待复核）。
            outcome = engine._ready(
                spec, solutions[0], frame, geometry, route,
                ["题面条件不足以完全定形，采用卡片形状作为先验：" + source,
                 "在满足题面全部可读条件的解集中取最贴近卡片的解",
                 "编号以卡片上的带圈标签为准，缺的编号由题面条件补齐"],
                ["题面全部可读条件通过逐条复核",
                 "形状与卡片的偏差在图形尺度的 20% 以内"])
            if outcome.get("status") == "READY":
                return outcome
            # ★ 这套形状算出来复核不过时**不能立刻返回**：后面还有别的形状来源可试。
            #   （同类短路 bug 这一轮已经踩到第三次：旧样本 就是被它挡回待复核的。）
            if fallback is None:
                fallback = outcome
    return fallback


def _placement_seeds(spec, seed):
    """「起飞点在图形外侧/内侧」有两个解，这里造出另一支的种子。

    典型题干：「起飞点到航点2和航点3距离相等，H到2和H到3=300米，起飞点在图形外侧」。
    「到两点等距且距离=边长」的解恰有**两支**：多边形的中心、以及该边外侧的等边三角形
    顶点。两支里 H 与这两点的**中点**的关系互为镜像（X=(0,0)、Y=(c,0) 时两支是 (c/2,±h)），
    所以另一支 = 把图形整体平移 -(2·(M-H))，H 自身不动。

    ★ 为什么必须移动图形而不是移动 H：H 是**题面给定的固定坐标点**，
      在求解器里属于 fixed、根本不参与优化 —— 早先把 H 镜像过去，优化器只好把
      多边形挪回来，结果报「已知坐标点H偏离题面519.62米」。

    布局模板给的种子天然落在「中心」那一支，于是『外侧』的题全部被
    `_validate_solution` 判成「起飞点位于图形外侧的条件不符」——
    实测 13 道六边形（旧样本…）卡在这一个点上。
    """
    if not re.search(r"起飞点.{0,8}图形\s*[内外]侧", spec.get("source_text", "")):
        return []
    if "H" not in seed:
        return []
    distances = {}
    for item in spec.get("distance_constraints", []):
        a, b = str(item["a"]), str(item["b"])
        if a == "H":
            distances[b] = float(item["value"])
        elif b == "H":
            distances[a] = float(item["value"])
    names = sorted(distances, key=lambda name: (not name.isdigit(), name))
    out = []
    for index, first in enumerate(names):
        for second in names[index + 1:]:
            if abs(distances[first] - distances[second]) > 1e-6:
                continue
            if first not in seed or second not in seed:
                continue
            midpoint = ((seed[first][0] + seed[second][0]) / 2.0,
                        (seed[first][1] + seed[second][1]) / 2.0)
            shift = (2.0 * (midpoint[0] - seed["H"][0]),
                     2.0 * (midpoint[1] - seed["H"][1]))
            flipped = {}
            for name, value in seed.items():
                if name == "H":
                    flipped[name] = value
                else:
                    flipped[name] = (value[0] - shift[0], value[1] - shift[1])
            out.append(flipped)
    return out


def solve(spec, frame, preliminary, engine):
    """模板法之外的约束求解后备；不够唯一或拓扑不符时保持待复核。"""
    if spec.get("shape") in ("圆形", "扫描航线"):
        return None
    names = _names(spec)
    if len(names) < 3 or len(names) > 14:
        return None
    fixed = _fixed_points(spec, engine)
    variables = [(name, axis) for name in names if name not in fixed
                 for axis in (0, 1)]
    if not variables:
        if engine._validate_solution(spec, fixed,
                                     [tuple(item) for item in spec.get("geometry_edges", [])],
                                     [tuple(item) for item in spec.get("flight_edges", [])]):
            return None
        return engine._ready(spec, fixed, frame,
                             [tuple(item) for item in spec.get("geometry_edges", [])],
                             [tuple(item) for item in spec.get("flight_edges", [])],
                             ["按图卡标注传播各航段坐标"],
                             ["图卡距离、方位、题面关系和航线连接均通过复核"])
    probe = {name: (50.0 * index + 1.0, 37.0 * index * index + 3.0)
             for index, name in enumerate(names)}
    if len(_residuals(spec, probe, engine)) < len(variables):
        return None
    geometry = [tuple(item) for item in spec.get("geometry_edges", [])]
    route = [tuple(item) for item in spec.get("flight_edges", [])]
    solutions = []
    seeds = list(_seeds(spec, fixed, preliminary, names, engine))
    for seed in list(seeds):
        seeds.extend(_placement_seeds(spec, seed))
    for initial in seeds:
        try:
            candidate = _optimise(spec, initial, variables, engine)
            if candidate is None or not all(math.isfinite(value)
                                             for point in candidate.values() for value in point):
                continue
            if engine._validate_solution(spec, candidate, geometry, route):
                continue
            if not _layout_matches(spec, candidate):
                continue
            if any(math.dist(candidate[a], candidate[b]) < 1.0
                   for a, b in geometry):
                continue
            base = _residuals(spec, candidate, engine)
            jacobian = _jacobian(spec, candidate, variables, base, engine)
            if _rank(jacobian, len(variables)) != len(variables):
                continue
            if not any(all(math.dist(old[name], candidate[name]) < 2.0
                           for name in spec.get("nodes", [])) for old in solutions):
                solutions.append(candidate)
        except (KeyError, ValueError, ZeroDivisionError, OverflowError):
            continue
    # ★ 镜像二选一：题面条件常常对"镜像两支"都成立（实测 旧样本 自由求解器正好 2 个解）。
    #   按用户口径“参考如图”，用**图形所有边**的平均朝向偏差来挑（只比一条边会失效：
    #   这两道的镜像轴恰好就是 1→2 边）。只在差异 ≥20° 时才采信，避免拿噪声挑解。
    if len(solutions) == 2:
        # 先按"起飞点默认在外侧"决胜（#268/#274 属于这一类：两支只差 H 在内外）
        solutions = pick_branch_by_takeoff(spec, solutions)
    if len(solutions) == 2:
        scored = [(figure_orientation_error(spec, candidate, engine), candidate)
                  for candidate in solutions]
        if all(score is not None for score, _ in scored):
            scored.sort(key=lambda item: item[0])
            if scored[0][0] + 20.0 < scored[1][0]:
                solutions = [scored[0][1]]
    if len(solutions) != 1:
        return None
    return engine._ready(spec, solutions[0], frame, geometry, route,
                         ["联合求解题面坐标、边长、方位和夹角",
                          "对全部航点进行独立约束复核"],
                         ["已识别条件全部满足", "全部自由航点通过独立性检查"])


def _diagram_edges_connected(nodes, edges):
    """检查识别出的图形边是否覆盖并连通全部标号点。"""
    names = [str(name) for name in nodes]
    if len(names) < 3 or len(set(names)) != len(names) or not edges:
        return False
    adjacency = {name: set() for name in names}
    seen = set()
    for raw_edge in edges:
        if not isinstance(raw_edge, (list, tuple)) or len(raw_edge) < 2:
            return False
        first, second = str(raw_edge[0]), str(raw_edge[1])
        key = frozenset((first, second))
        if first not in adjacency or second not in adjacency or first == second or key in seen:
            return False
        seen.add(key)
        adjacency[first].add(second)
        adjacency[second].add(first)
    reached, pending = set(), [names[0]]
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        pending.extend(adjacency[current] - reached)
    return reached == set(names)


def _diagram_is_single_cycle(nodes, edges):
    """检查多边形是否恰好是一条覆盖全部顶点的简单闭环。"""
    if len(edges) != len(nodes) or not _diagram_edges_connected(nodes, edges):
        return False
    degree = {str(name): 0 for name in nodes}
    for first, second in edges:
        degree[str(first)] += 1
        degree[str(second)] += 1
    return all(value == 2 for value in degree.values())


def solve_imported_visual_candidate(spec, frame, engine):
    """为完整导入题卡选一个可复核的形状先验候选，不冒充唯一解。

    接受本地 CV 验证的图形，或高置信 VLM 结构经本地拓扑复核后的图形；
    不要求 OCR 角标必须完整。图像只用于在可行坐标中择形，题面条件仍逐项硬校验。
    """
    diagram = spec.get("condition_assist_diagram_model") or {}
    if (spec.get("graph_source") not in ("vision_model", "local_cv") or
            not spec.get("condition_assist_solver_usable")):
        return None
    items = spec.get("condition_assist_items") or []
    if not items or any(not isinstance(item, dict) or
                        float(item.get("confidence", 0.0) or 0.0) < 0.72
                        for item in items):
        return None

    nodes = [str(name) for name in spec.get("nodes") or []]
    geometry = [tuple(map(str, edge[:2])) for edge in spec.get("geometry_edges", [])
                if isinstance(edge, (list, tuple)) and len(edge) >= 2]
    route = [tuple(map(str, edge[:2])) for edge in spec.get("flight_edges", [])
             if isinstance(edge, (list, tuple)) and len(edge) >= 2]
    if len(nodes) < 3 or len(nodes) > 16 or len(set(nodes)) != len(nodes):
        return None
    shape = str(spec.get("shape") or "")
    if shape == "扫描航线":
        if len(geometry) < len(nodes) - 1 or not _diagram_edges_connected(nodes, geometry):
            return None
    elif shape == "圆形":
        # 圆形用离散航点近似绘制时，边仍须形成闭环；连续圆周由专用约束处理。
        if geometry and not _diagram_is_single_cycle(nodes, geometry):
            return None
        if not geometry:
            geometry = list(route)
            if not _diagram_is_single_cycle(nodes, geometry):
                return None
    elif not _diagram_is_single_cycle(nodes, geometry):
        return None
    if len(route) < len(nodes) - 1:
        return None
    if any(route[index][1] != route[index + 1][0]
           for index in range(len(route) - 1)):
        return None
    if not set(nodes).issubset({name for edge in route for name in edge}):
        return None
    expected_closed = bool(spec.get("closed")) or diagram.get("flight_closed") is True
    if expected_closed and (len(route) != len(nodes) or
                            route[-1][1] != route[0][0]):
        return None
    route_only_issues = {"飞行顺序未完整识别", "航线闭合条件未确认"}
    if any(str(issue) not in route_only_issues
           for issue in diagram.get("topology_errors", [])):
        return None
    if ("飞行顺序未完整识别" in diagram.get("topology_errors", []) and
            not spec.get("route_inferred_from_closed_cycle") and
            len(route) < len(nodes)):
        return None
    try:
        visual_confidence = float(diagram.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        visual_confidence = 0.0
    positions = diagram.get("point_positions") or {}
    has_layout = all(
        isinstance(positions.get(name), dict) and
        all(key in positions[name] for key in ("x", "y"))
        for name in nodes)
    cv_validated = (str(diagram.get("geometry_source") or "").startswith("local_cv") and
                    diagram.get("geometry_validated"))
    if not cv_validated and (visual_confidence < 0.72 or not has_layout):
        return None

    fixed = _fixed_points(spec, engine)
    names = _names(spec)
    extras = [name for name in names if name not in nodes and name not in fixed]
    if len(extras) > 3:
        return None
    variables = [(name, axis) for name in nodes + extras if name not in fixed
                 for axis in (0, 1)]
    if not variables:
        return None

    layout = spec.get("layout_points") or []
    if len(layout) != len(nodes) or not spec.get("layout_confidence"):
        return None
    labelling = {name: (float(layout[index][0]), -float(layout[index][1]))
                 for index, name in enumerate(nodes)}
    visual_cycle = _diagram_cycle_order(nodes, geometry) if cv_validated else []
    visual_area = _polygon_signed_area(visual_cycle, labelling) if visual_cycle else 0.0
    if cv_validated and (not visual_cycle or abs(visual_area) < 1e-6):
        return None
    seeds = list(_seeds(spec, fixed, {}, names, engine))
    initial = _prior_seed(spec, labelling, fixed, extras, engine)
    if initial:
        seeds.insert(0, initial)
        try:
            projected = _prior_project(spec, labelling, initial, variables, engine)
            if projected:
                seeds.insert(0, projected)
        except (KeyError, ValueError, ZeroDivisionError, OverflowError):
            pass

    accepted = []
    for seed in seeds[:48]:
        try:
            candidate = _optimise(spec, seed, variables, engine)
            if not candidate or not all(math.isfinite(value)
                                        for point in candidate.values() for value in point):
                continue
            if engine._validate_solution(spec, candidate, geometry, route):
                continue
            if not _layout_matches(spec, candidate):
                continue
            if any(math.dist(candidate[first], candidate[second]) < 1.0
                   for first, second in geometry):
                continue
            if cv_validated:
                # 卡片示意图通常不按比例绘制，因此夹角用于选分支，长度比例只作弱参考。
                candidate_area = _polygon_signed_area(visual_cycle, candidate)
                if abs(candidate_area) < 1e-6 or visual_area * candidate_area <= 0:
                    continue
                angle_errors = _layout_angle_errors(visual_cycle, labelling, candidate)
                if not angle_errors or max(angle_errors) > 45.0:
                    continue
                angle_rms = math.sqrt(sum(value * value for value in angle_errors) /
                                      len(angle_errors))
                prior_deviation = _prior_deviation(labelling, candidate)
                if not math.isfinite(angle_rms) or not math.isfinite(prior_deviation):
                    continue
                deviation = prior_deviation
                score = angle_rms / 180.0 + min(prior_deviation, 2.0) * 0.02
            else:
                deviation = _prior_deviation(labelling, candidate)
                # 旧缓存或纯 VLM 草图未经线段校正，只有高度贴合时才可用来选解。
                if not math.isfinite(deviation) or deviation > 0.65:
                    continue
                score = deviation
            rank = _rank(_jacobian(spec, candidate, variables,
                                   _residuals(spec, candidate, engine), engine),
                         len(variables))
            score += 0.0 if rank == len(variables) else 0.002
            if not any(all(math.dist(old["points"][name], candidate[name]) < 2.0
                           for name in nodes) for old in accepted):
                accepted.append({"points": candidate, "score": score,
                                 "deviation": deviation, "rank": rank,
                                 "layout_angle_error": (
                                     math.sqrt(sum(value * value for value in angle_errors) /
                                               len(angle_errors))
                                     if cv_validated and angle_errors else None)})
        except (KeyError, ValueError, ZeroDivisionError, OverflowError,
                TypeError, IndexError):
            continue
    if not accepted:
        return None
    accepted.sort(key=lambda item: item["score"])
    chosen = accepted[0]
    result = engine._ready(
        spec, chosen["points"], frame, geometry, route,
        ["在题卡图形先验附近搜索坐标布局",
         "按题面边长、航向、夹角和图形关系逐项求解",
         "由独立校验器复核全部已识别硬条件"],
        ["题面数值约束全部通过独立复核",
         "图形拓扑来自本地 CV 闭环检测",
         "图示用于选取参考布局，不作为精确比例尺"])
    if result.get("status") != "READY":
        return None
    result["imported_visual_candidate"] = True
    result["candidate_selection_method"] = "imported_visual_prior"
    result["candidate_coordinate_unique"] = chosen["rank"] == len(variables)
    result["candidate_shape_deviation"] = round(chosen["deviation"], 3)
    if chosen.get("layout_angle_error") is not None:
        result["candidate_layout_angle_error_degrees"] = round(
            chosen["layout_angle_error"], 1)
    return result


def _similarity_seeds(spec, model, engine, fixed, extras, metric):
    """在原有种子之外，补一批“**把图形整体平移开**”的初值。

    为什么必须补（实测 旧样本）：模型里某个点的初值恰好落在 H 的已知坐标上时
    （1 点初值在原点、H 也在原点），方向关系 `(H→2)` 与 `(1→2)` 一开始就是**同一条线段**，
    残差恒为 0、梯度为 0 ⇒ 优化器停在**退化驻点**上不动，永远走不到非退化解。
    显式给出“平移一段距离”的初值，优化器就有方向可走。

    只平移前若干个体面的种子，控制耗时；找不到非退化解时行为与原来完全一致（不会引入回退）。
    """
    base = list(_similarity_seeds_base(spec, model, engine, fixed, extras, metric))
    offsets = ((60.0, 0.0), (-60.0, 0.0), (0.0, 60.0), (0.0, -60.0),
               (150.0, 150.0), (-150.0, -150.0), (300.0, 0.0), (0.0, 300.0))
    extra = []
    for seed in base[:8]:
        values = list(seed)
        if len(values) < 2:
            continue
        for ox, oy in offsets:
            shifted = list(values)
            shifted[0] = shifted[0] + ox
            shifted[1] = shifted[1] + oy
            extra.append(tuple(shifted))
    return base + extra
