# -*- coding: utf-8 -*-
"""从导入的题卡图片中提取带置信度的航点与图形边。"""
import math
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QImageReader


def _point_labels(words, image_height, image_width=None):
    """优先读取题干正文前的独立数字，避免把条件编号当成图形顶点。"""
    words = [word for word in (words or []) if isinstance(word, dict)]
    body_y = min((float(word.get("y", 0)) for word in words
                  if re.match(r"^\s*1\s*[,、.．)]", str(word.get("text", "")))),
                 default=float(image_height) * 0.76)
    if image_width is None:
        image_width = max((float(word.get("x", 0)) +
                           float(word.get("width", 0)) for word in words),
                          default=float(image_height))
    title_bottom = max((float(word.get("y", 0)) +
                        float(word.get("height", 0))
                        for word in words
                        if float(word.get("y", 0)) < image_height * 0.18 and
                        image_width * 0.20 <=
                        float(word.get("x", 0)) +
                        float(word.get("width", 0)) * 0.5 <= image_width * 0.85 and
                        re.search(r"[\u4e00-\u9fff]", str(word.get("text", ""))) and
                        len(str(word.get("text", "")).strip()) >= 3),
                       default=0.0)
    circled = {chr(0x2460 + index): str(index + 1) for index in range(20)}
    found = {}
    for word in words:
        label = str(word.get("text", "")).strip()
        label = circled.get(label, label)
        confidence = float(word.get("confidence", 0) or 0)
        y = float(word.get("y", 0))
        if (not re.fullmatch(r"[1-9]\d?", label) or int(label) > 16 or y >= body_y or
                confidence < 0.48 or
                y + float(word.get("height", 0)) * 0.5 <
                title_bottom + image_height * 0.005):
            continue
        point = (float(word.get("x", 0)) + float(word.get("width", 0)) / 2,
                 y + float(word.get("height", 0)) / 2)
        found.setdefault(label, []).append((point, confidence))
    if not found:
        return []

    # 多个日期、题号也可能被 OCR 识别成单个数字。按空间聚类选取图形
    # 区域内的编号；即使漏识一个点号，也保留其余可见航点。
    best = []
    best_score = -1.0
    for _seed_label, options in found.items():
        for seed, _ in options:
            cluster = {}
            for label, candidates in found.items():
                nearby = [(point, score) for point, score in candidates
                          if math.dist(point, seed) <= image_height * 0.92]
                if nearby:
                    point, score = max(
                        nearby, key=lambda item: (item[1], -math.dist(item[0], seed)))
                    cluster[label] = (point, score)
            if len(cluster) >= 3:
                points = [cluster[label][0] for label in
                          sorted(cluster, key=int)]
                width = max(point[0] for point in points) - min(
                    point[0] for point in points)
                height = max(point[1] for point in points) - min(
                    point[1] for point in points)
                if width >= image_height * 0.08 and height >= image_height * 0.06:
                    candidate = [(label, *cluster[label][0])
                                 for label in sorted(cluster, key=int)]
                    confidence = sum(cluster[label][1] for label in cluster)
                    score = len(candidate) * 10 + confidence
                    if score > best_score:
                        best_score = score
                        best = candidate
    return best if 3 <= len(best) <= 16 else []


def _edge_support(pixels, width, height, stride, first, second, scale):
    """沿两个标号之间采样，检查是否存在连续的图形线段。"""
    x1, y1 = first
    x2, y2 = second
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < scale * 0.08:
        return 0.0
    steps = max(32, min(180, int(length / max(2.0, scale * 0.003))))
    px, py = -dy / length, dx / length
    radius = max(3, int(scale * 0.012))
    offset_step = max(1, radius // 8)
    offsets = list(range(-radius, radius + 1, offset_step))
    if 0 not in offsets:
        offsets.append(0)
    offsets.sort()
    supported = []
    # 顶点标记通常盖住线段端点，略过两端，只判别中间线段。
    for index in range(steps):
        fraction = 0.14 + 0.72 * index / max(1, steps - 1)
        x = x1 + dx * fraction
        y = y1 + dy * fraction
        centre = 255
        for offset in offsets:
            sx, sy = round(x + px * offset), round(y + py * offset)
            if 0 <= sx < width and 0 <= sy < height:
                centre = min(centre, pixels[sy * stride + sx])
        # 取线段法线两侧的背景亮度作局部对照，不把灰底、纸张阴影误判为边。
        flank = radius + max(3, int(scale * 0.004))
        surroundings = []
        for offset in (-flank, flank):
            sx, sy = round(x + px * offset), round(y + py * offset)
            if 0 <= sx < width and 0 <= sy < height:
                surroundings.append(pixels[sy * stride + sx])
        background = max(surroundings, default=centre)
        visible = centre < 185 and background - centre >= 18
        supported.append(visible)
    coverage = sum(supported) / max(1, len(supported))
    longest = run = 0
    for value in supported:
        run = run + 1 if value else 0
        longest = max(longest, run)
    continuity = longest / max(1, len(supported))
    return max(coverage * 0.72 + continuity * 0.28, continuity * 0.88)


def _cycle_order(nodes, edges):
    adjacency = {node: [] for node in nodes}
    for first, second in edges:
        adjacency[first].append(second)
        adjacency[second].append(first)
    if any(len(neighbors) != 2 for neighbors in adjacency.values()):
        return []
    if not nodes:
        return []
    start = min(nodes, key=int)
    order = [start]
    previous = None
    current = start
    for _ in range(len(nodes)):
        options = sorted((node for node in adjacency[current] if node != previous),
                         key=int)
        if not options:
            return []
        next_node = options[0]
        if next_node == "1":
            break
        if next_node in order:
            return []
        order.append(next_node)
        previous, current = current, next_node
    if len(order) != len(nodes) or start not in adjacency[current]:
        return []
    return order


def _crossing_count(points):
    """统计闭合折线中非相邻边的真实交叉数。"""
    def orient(a, b, c):
        return ((b[0] - a[0]) * (c[1] - a[1]) -
                (b[1] - a[1]) * (c[0] - a[0]))

    count = len(points)
    crossings = 0
    for first in range(count):
        a, b = points[first], points[(first + 1) % count]
        for second in range(first + 1, count):
            if (second == first or second == (first + 1) % count or
                    (second + 1) % count in (first, (first + 1) % count)):
                continue
            c, d = points[second], points[(second + 1) % count]
            if (orient(a, b, c) * orient(a, b, d) < -1e-5 and
                    orient(c, d, a) * orient(c, d, b) < -1e-5):
                crossings += 1
    return crossings


def _shape_hint(words, explicit=None):
    """从显式模型结论或 OCR 题干中读取八类图形提示。"""
    aliases = {
        "等边三角形": ("等边三角形", "正三角形"),
        "直角三角形": ("直角三角形", "直角△"),
        "梯形": ("梯形", "直角梯形", "等腰梯形"),
        "五边形": ("五边形", "五角形"),
        "蝴蝶形": ("蝴蝶形", "蝶形"),
        "六边形": ("六边形", "正六边形"),
        "扫描航线": ("扫描航线", "扫描航线", "扫描式航线", "往复式航线"),
        "圆形": ("圆形", "圆周", "圆弧航线"),
    }
    text = str(explicit or "") + " " + " ".join(
        str(word.get("text", "")) for word in (words or [])
        if isinstance(word, dict))
    for shape, names in aliases.items():
        if any(name in text for name in names):
            return shape
    return ""


def _route_bearing_hints(text):
    """提取能决定图上编号朝向的明确点间航向。"""
    import geo as GEO

    endpoint = r"(H|[1-9]\d?)\s*(?:号)?点?"
    direction = (
        r"(?:东北|东南|西南|西北|[北南东西NSEW])\s*偏\s*"
        r"[北南东西NSEW]\s*[\d.]+\s*(?:°|度)?|"
        r"NNE|ENE|ESE|SSE|SSW|WSW|WNW|NNW|NE|NW|SE|SW|"
        r"N|S|E|W|东北|西北|东南|西南|正北|正南|正东|正西|北|南|东|西")
    pattern = re.compile(
        endpoint + r"\s*(?:位于|处于|在)\s*" + endpoint +
        r"\s*(?:的)?\s*(?:(?:为|是)\s*)?"
        r"(?:(?:航向|方向|方位(?:角)?)\s*(?:(?:为|是|=|[:：])\s*)?)?"
        r"(?P<direction>" + direction + r")", re.I)
    output = []
    for match in pattern.finditer(str(text or "")):
        bearing = GEO.parse_bearing(match.group("direction"))
        if bearing is None:
            continue
        # 句式是“目标位于参照点的方位”，转换成参照点→目标点。
        output.append((str(match.group(2)).upper(), str(match.group(1)).upper(),
                       float(bearing) % 360.0))
    return output


def _fit_circle(points):
    """用可见航点中心拟合圆心和半径，返回拟合残差。"""
    if len(points) < 3:
        return None
    try:
        import numpy as np
        values = np.asarray(list(points.values()), dtype=float)
        matrix = np.column_stack((2.0 * values[:, 0],
                                  2.0 * values[:, 1],
                                  np.ones(len(values))))
        target = (values[:, 0] ** 2 + values[:, 1] ** 2)
        solution, _residuals, rank, _singular = np.linalg.lstsq(
            matrix, target, rcond=None)
        if rank < 3:
            return None
        cx, cy, constant = map(float, solution)
        radius_squared = cx * cx + cy * cy + constant
        if radius_squared <= 0:
            return None
        radius = math.sqrt(radius_squared)
        residuals = [abs(math.dist((cx, cy), point) - radius)
                     for point in values]
        return cx, cy, radius, sum(residuals) / len(residuals)
    except Exception:
        return None


def _circle_support(pixels, width, height, stride, centre, radius, anchors):
    """检查拟合圆周上是否确有连续印刷弧线，避免把普通多边形误认成圆。"""
    cx, cy = centre
    label_angles = [math.atan2(point[1] - cy, point[0] - cx)
                    for point in anchors.values()]
    tolerance = max(3, int(radius * 0.018))
    supported = total = 0
    for degree in range(0, 360, 3):
        angle = math.radians(degree)
        if any(abs((angle - mark + math.pi) % (2 * math.pi) - math.pi) <
               math.radians(11) for mark in label_angles):
            continue
        x, y = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
        total += 1
        dark = False
        for offset in range(-tolerance, tolerance + 1, max(1, tolerance // 3)):
            sx = round(cx + (radius + offset) * math.cos(angle))
            sy = round(cy + (radius + offset) * math.sin(angle))
            if 0 <= sx < width and 0 <= sy < height and pixels[sy * stride + sx] < 190:
                dark = True
                break
        supported += int(dark)
    return supported / max(total, 1)


def _best_open_path(nodes, edge_scores):
    """从线段置信度中找经过全部航点的最高分开放路径。"""
    if len(nodes) < 3:
        return [], []
    adjacency = {node: [] for node in nodes}
    for (first, second), score in edge_scores.items():
        if score >= 0.27:
            adjacency[first].append((second, score))
            adjacency[second].append((first, score))
    for node in nodes:
        adjacency[node].sort(key=lambda item: item[1], reverse=True)
    best_order, best_score = [], -1.0
    visits = 0
    limit = 24000

    def visit(path, total):
        nonlocal best_order, best_score, visits
        visits += 1
        if visits > limit:
            return
        if len(path) == len(nodes):
            average = total / max(1, len(path) - 1)
            if average > best_score:
                best_order, best_score = list(path), average
            return
        for neighbor, score in adjacency[path[-1]]:
            if neighbor not in path:
                visit(path + [neighbor], total + score)

    for start in nodes:
        visit([start], 0.0)
        if visits > limit:
            break
    if best_score < 0.45:
        return [], []
    edges = [[best_order[index], best_order[index + 1]]
             for index in range(len(best_order) - 1)]
    return best_order, edges


def _looks_like_scan(order, points):
    """没有文字提示时，只把有往返平行航段的长折线识别成扫描航线。"""
    if len(order) < 4:
        return False
    vectors = []
    for index in range(len(order) - 1):
        first, second = points[order[index]], points[order[index + 1]]
        dx, dy = second[0] - first[0], second[1] - first[1]
        length = math.hypot(dx, dy)
        if length <= 1e-8:
            continue
        vectors.append((dx / length, dy / length))
    for first in range(len(vectors)):
        for second in range(first + 2, len(vectors)):
            cross = abs(vectors[first][0] * vectors[second][1] -
                        vectors[first][1] * vectors[second][0])
            if cross <= 0.18:
                return True
    return False


def analyze(image_path, ocr_words, shape_hint=None):
    """识别题卡八类图形和航点；不确定时只返回诊断，不猜连接关系。"""
    try:
        reader = QImageReader(str(image_path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            return {"source": "local_cv", "status": "unavailable"}
        image = image.convertToFormat(QImage.Format.Format_Grayscale8)
        original_width, original_height = image.width(), image.height()
        hint = _shape_hint(ocr_words, shape_hint)
        labels = _point_labels(ocr_words, original_height, original_width)
        if len(labels) < 3:
            recovered = _recover_numbered_route(image, ocr_words,
                                                shape_hint or hint, image_path)
            if recovered:
                return recovered
            return {"source": "local_cv", "status": "labels_incomplete",
                    "shape": hint, "nodes": [item[0] for item in labels],
                    "geometry_edges": [], "confidence": 0.0,
                    "topology_valid": False,
                    "reason": "图形编号不足，无法确认顶点连接关系"}

        # 扫描航线即使 OCR 已经读全编号，也应先按连续墨线恢复开放路径；
        # 两两连线评分容易把图中的多条平行航段误合成错误的稠密图。
        if hint == "扫描航线":
            recovered = _recover_numbered_route(
                image, ocr_words, shape_hint or hint, image_path)
            if recovered and recovered.get("shape") == "扫描航线":
                return recovered

        scale = min(1.0, 1800.0 / max(original_width, original_height))
        if scale < 1.0:
            image = image.scaled(round(original_width * scale),
                                 round(original_height * scale),
                                 Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        points = {label: (x * scale, y * scale)
                  for label, x, y in labels}
        width, height = image.width(), image.height()
        stride = image.bytesPerLine()
        pixels = bytes(image.constBits())
        diagonal = math.hypot(width, height)
        nodes = [label for label, _, _ in labels]
        edge_scores = {}
        detected = []
        for index, first in enumerate(nodes):
            for second in nodes[index + 1:]:
                score = _edge_support(pixels, width, height, stride,
                                      points[first], points[second], diagonal)
                edge_scores[(first, second)] = score
                if score >= 0.27:
                    detected.append([first, second])
        circle = _fit_circle(points) if hint == "圆形" or len(nodes) >= 4 else None
        if circle:
            cx, cy, radius, residual = circle
            label_error = residual / max(radius, 1.0)
            radius_ok = diagonal * 0.055 <= radius <= diagonal * 0.49
            inside = (0 <= cx < width and 0 <= cy < height)
            angles = sorted(math.atan2(points[node][1] - cy,
                                       points[node][0] - cx) % (2 * math.pi)
                            for node in nodes)
            gaps = [angles[(index + 1) % len(angles)] - angles[index]
                    if index + 1 < len(angles) else
                    angles[0] + 2 * math.pi - angles[index]
                    for index in range(len(angles))]
            spread_ok = max(gaps, default=2 * math.pi) <= math.radians(205)
            support = _circle_support(pixels, width, height, stride,
                                      (cx, cy), radius, points)
            support_floor = 0.18 if hint == "圆形" else 0.34
            circle_valid = (radius_ok and inside and spread_ok and
                            label_error <= 0.22 and support >= support_floor)
            if circle_valid:
                projected = {}
                for node in nodes:
                    dx, dy = points[node][0] - cx, points[node][1] - cy
                    length = math.hypot(dx, dy) or 1.0
                    projected[node] = (cx + radius * dx / length,
                                       cy + radius * dy / length)
                ring_edges = [[nodes[index], nodes[(index + 1) % len(nodes)]]
                              for index in range(len(nodes))]
                confidence = max(0.72, min(0.98,
                                           0.70 + 0.20 * support - 0.20 * label_error))
                return {
                    "source": "local_cv", "status": "ready", "shape": "圆形",
                    "nodes": nodes, "geometry_edges": ring_edges,
                    "geometry_kind": "arc", "route_order": nodes,
                    "point_positions": {
                        node: {"x": round(projected[node][0] / width * 1000, 1),
                               "y": round(projected[node][1] / height * 1000, 1)}
                        for node in nodes},
                    "confidence": round(confidence, 3),
                    "geometry_confidence": round(confidence, 3),
                    "validated_cycle": True, "topology_valid": True,
                    "image_size": [original_width, original_height],
                    "warnings": ["圆弧已由题卡圆周和航点位置确认"],
                }

        order = _cycle_order(nodes, detected)
        cycle_edges = ([[order[index], order[(index + 1) % len(order)]]
                        for index in range(len(order))] if order else [])
        cycle_scores = [edge_scores.get(tuple(sorted(edge, key=int)), 0.0)
                        for edge in cycle_edges]
        other_scores = [score for edge, score in edge_scores.items()
                        if tuple(sorted(edge, key=int)) not in
                        {tuple(sorted(item, key=int)) for item in cycle_edges}]
        confidence = sum(cycle_scores) / len(cycle_scores) if cycle_scores else 0.0
        edge_margin = (min(cycle_scores) - max(other_scores)
                       if cycle_scores and other_scores else 1.0)
        cycle_valid = bool(order and confidence >= 0.58 and
                           min(cycle_scores, default=0.0) >= 0.27 and
                           edge_margin >= 0.025)
        path_order, path_edges = _best_open_path(nodes, edge_scores)
        path_scores = [edge_scores.get(tuple(sorted(edge, key=int)), 0.0)
                       for edge in path_edges]
        path_confidence = sum(path_scores) / len(path_scores) if path_scores else 0.0
        scan_valid = bool(path_order and path_confidence >= 0.55 and
                          min(path_scores, default=0.0) >= 0.40 and
                          (hint == "扫描航线" or
                           (not cycle_valid and _looks_like_scan(path_order, points))))
        if scan_valid:
            order = path_order
            detected = path_edges
            confidence = path_confidence
            edge_margin = min(path_scores, default=0.0) - max(
                (score for edge, score in edge_scores.items()
                 if tuple(sorted(edge, key=int)) not in
                 {tuple(sorted(item, key=int)) for item in path_edges}),
                default=0.0)
            validated = True
            visual_shape = "扫描航线"
        else:
            validated = cycle_valid
            visual_shape = ""
        parallel_pairs = []
        right_angle_vertex = None
        if validated and cycle_valid and len(order) == 3:
            cycle_points = [points[node] for node in order]
            lengths = [math.dist(cycle_points[index],
                                 cycle_points[(index + 1) % 3])
                       for index in range(3)]
            if max(lengths) <= min(lengths) * 1.10:
                visual_shape = "等边三角形"
            else:
                right = False
                for index in range(3):
                    first = cycle_points[(index - 1) % 3]
                    vertex = cycle_points[index]
                    second = cycle_points[(index + 1) % 3]
                    ax, ay = first[0] - vertex[0], first[1] - vertex[1]
                    bx, by = second[0] - vertex[0], second[1] - vertex[1]
                    denominator = math.hypot(ax, ay) * math.hypot(bx, by)
                    cosine = (ax * bx + ay * by) / denominator if denominator else 1
                    if abs(cosine) <= 0.14:
                        right = True
                        right_angle_vertex = order[index]
                        break
                visual_shape = "直角三角形" if right else "三角形"
        elif validated and cycle_valid and len(order) == 4:
            cycle_edges = [(order[index], order[(index + 1) % 4])
                           for index in range(4)]
            def parallel_error(first, second):
                ax, ay = points[first[1]][0] - points[first[0]][0], points[first[1]][1] - points[first[0]][1]
                bx, by = points[second[1]][0] - points[second[0]][0], points[second[1]][1] - points[second[0]][1]
                denominator = math.hypot(ax, ay) * math.hypot(bx, by)
                if denominator <= 1e-8:
                    return 90.0
                sine = min(1.0, abs(ax * by - ay * bx) / denominator)
                return math.degrees(math.asin(sine))

            for first_index, second_index in ((0, 2), (1, 3)):
                first, second = cycle_edges[first_index], cycle_edges[second_index]
                if parallel_error(first, second) <= 6.0:
                    parallel_pairs.append([list(first), list(second)])
            if _crossing_count([points[node] for node in order]) == 1:
                visual_shape = "蝴蝶形"
            elif len(parallel_pairs) == 1:
                visual_shape = "梯形"
            elif len(parallel_pairs) == 2:
                visual_shape = "平行四边形"
            else:
                visual_shape = "四边形"
        elif validated and cycle_valid and len(order) == 5:
            visual_shape = "五边形"
        elif validated and cycle_valid and len(order) == 6:
            visual_shape = "六边形"
        if hint and validated:
            # 具体形状提示只有在像素连接结构已经通过核验后才覆盖通用名称。
            visual_shape = hint
        if not cycle_valid and hint != "圆形":
            recovered = _recover_numbered_route(
                image, ocr_words, shape_hint or hint, image_path)
            if recovered and recovered.get("topology_valid") and (
                    recovered.get("shape") == "扫描航线" or
                    recovered.get("validated_cycle")):
                return recovered
            refined = _recover_partial_polygon(
                image_path, ocr_words, [original_width, original_height],
                nodes, hint)
            if refined:
                return refined

        return {
            "source": "local_cv", "status": "ready" if validated else "review",
            "nodes": nodes, "geometry_edges": detected,
            "shape": visual_shape,
            "right_angle_vertex": right_angle_vertex,
            "parallel_edge_pairs": parallel_pairs,
            "point_positions": {
                # 保持既有题库内部 CV 坐标格式，避免改变已核验答案指纹。
                # 新导入题的等比例坐标由 refine_partial_polygon 单独提供。
                label: {"x": round(points[label][0] / width * 1000, 1),
                        "y": round(points[label][1] / height * 1000, 1)}
                for label in nodes},
            "route_order": order if validated else [],
            "confidence": round(confidence, 3), "edge_margin": round(edge_margin, 3),
            "validated_cycle": bool(validated and cycle_valid),
            "topology_valid": bool(validated),
            "image_size": [original_width, original_height],
        }
    except Exception as exc:
        return {"source": "local_cv", "status": "unavailable",
                "nodes": [], "geometry_edges": [], "confidence": 0.0,
                "reason": str(exc)[:240]}


def _recover_partial_polygon(image_path, ocr_words, image_size, nodes, hint):
    """按 OCR 航点锚点和实线轮廓修复残缺的三至六边形拓扑。"""
    nodes = [str(node) for node in nodes]
    count = len(nodes)
    if not image_path or not 3 <= count <= 6:
        return None
    generic_shapes = {3: "三角形", 4: "四边形", 5: "五边形", 6: "六边形"}
    candidates = []
    if count == 4 and hint not in ("梯形", "平行四边形", "蝴蝶形"):
        # 交叉四边形的中心交点不是航点，单靠连通图检测会误判为五个顶点。
        candidates.append("蝴蝶形")
    if hint and hint not in candidates:
        candidates.append(hint)
    fallback = generic_shapes.get(count)
    if fallback and fallback not in candidates:
        candidates.append(fallback)
    cycle_edges = [[nodes[index], nodes[(index + 1) % count]]
                   for index in range(count)]
    for shape in candidates:
        refined = refine_partial_polygon(
            image_path, ocr_words, image_size,
            {"nodes": nodes, "geometry_edges": cycle_edges, "shape": shape})
        if not refined:
            continue
        refined["route_order"] = list(nodes)
        refined["validated_cycle"] = True
        refined["flight_closed"] = True
        refined["topology_valid"] = True
        refined["status"] = "ready"
        return refined
    return None


def _line_clusters(segments, short_side, gap_ratio=0.075, min_length=None,
                   min_cluster_length=None, offset_ratio=0.018):
    """合并霍夫检测到的同一条线段，压低重复边和水印碎线的影响。"""
    records = []
    for x1, y1, x2, y2 in segments:
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        minimum = (max(36.0, short_side * 0.075) if min_length is None
                   else float(min_length))
        if length < minimum:
            continue
        angle = math.atan2(dy, dx) % math.pi
        direction = (math.cos(angle), math.sin(angle))
        normal = (-direction[1], direction[0])
        middle = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
        records.append({
            "angle": angle,
            "direction": direction,
            "offset": normal[0] * middle[0] + normal[1] * middle[1],
            "interval": sorted((direction[0] * x1 + direction[1] * y1,
                                 direction[0] * x2 + direction[1] * y2)),
            "weight": length,
        })

    parent = list(range(len(records)))

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    angle_tolerance = math.radians(4.5)
    offset_tolerance = max(6.0, short_side * offset_ratio)
    gap_tolerance = short_side * gap_ratio
    for first in range(len(records)):
        a = records[first]
        for second in range(first + 1, len(records)):
            b = records[second]
            difference = abs(a["angle"] - b["angle"])
            difference = min(difference, math.pi - difference)
            if difference > angle_tolerance or abs(a["offset"] - b["offset"]) > offset_tolerance:
                continue
            gap = max(0.0, max(a["interval"][0], b["interval"][0]) -
                      min(a["interval"][1], b["interval"][1]))
            if gap > gap_tolerance:
                continue
            ra, rb = root(first), root(second)
            if ra != rb:
                parent[rb] = ra

    groups = {}
    for index, record in enumerate(records):
        groups.setdefault(root(index), []).append(record)
    result = []
    for group in groups.values():
        vx = vy = offset = total = 0.0
        for item in group:
            angle = item["angle"]
            weight = item["weight"]
            vx += math.cos(2.0 * angle) * weight
            vy += math.sin(2.0 * angle) * weight
            offset += item["offset"] * weight
            total += weight
        if total <= 0:
            continue
        angle = (math.atan2(vy, vx) / 2.0) % math.pi
        direction = (math.cos(angle), math.sin(angle))
        normal = (-direction[1], direction[0])
        offset /= total
        projections = [value for item in group for value in item["interval"]]
        low, high = min(projections), max(projections)
        cluster_minimum = (max(54.0, short_side * 0.10)
                           if min_cluster_length is None
                           else float(min_cluster_length))
        if high - low < cluster_minimum:
            continue
        result.append({
            "direction": direction, "normal": normal, "offset": offset,
            "low": low, "high": high, "length": high - low,
        })
    result.sort(key=lambda item: item["length"], reverse=True)
    return result[:28]


def _route_graph_from_pixels(image, ocr_words, image_path=None):
    """从清晰直线图中恢复连通拓扑，补救小圆点编号未被 OCR 读出的题卡。"""
    try:
        import cv2
        import numpy as np

        gray = None
        if image_path:
            try:
                gray = cv2.imdecode(np.fromfile(str(image_path), dtype=np.uint8),
                                    cv2.IMREAD_GRAYSCALE)
            except Exception:
                gray = None
        if gray is None:
            width, height = image.width(), image.height()
            stride = image.bytesPerLine()
            pixels = np.frombuffer(bytes(image.constBits()), dtype=np.uint8)
            gray = pixels.reshape(height, stride)[:, :width].copy()
        height, width = gray.shape[:2]

        # 条件区若在右栏，图形通常位于左栏；让识别区域避开正文数字。
        condition_left = min((float(word.get("x", width))
                              for word in (ocr_words or [])
                              if isinstance(word, dict) and re.match(
                                  r"^\s*1\s*[,、.．)]",
                                  str(word.get("text", "")))),
                             default=width)
        roi_right = (min(int(width * 0.40),
                         max(1, int(condition_left - max(2, width * 0.005))))
                     if condition_left >= width * 0.22 else width)
        body_top = min((float(word.get("y", height))
                        for word in (ocr_words or [])
                        if isinstance(word, dict) and re.match(
                            r"^\s*1\s*[,、.．)]",
                            str(word.get("text", ""))) and
                        # 题干常在图形右侧排版，不能据它的纵坐标裁掉左侧图形。
                        float(word.get("x", 0)) < roi_right),
                       default=height * 0.72)
        title_bottom = max((float(word.get("y", 0)) +
                            float(word.get("height", 0))
                            for word in (ocr_words or [])
                            if isinstance(word, dict) and any(
                                token in str(word.get("text", ""))
                                for token in ("地面站", "真题"))),
                           default=height * 0.02)
        # 外部扫描题卡的示意图常在纸张上半部，固定从 25% 起裁会直接
        # 切掉顶点和上边。用标题与题干位置约束图形区域，保留完整示意图。
        top = max(0, int(min(height * 0.12, title_bottom + height * 0.015)))
        bottom = max(top + int(height * 0.25),
                     int(min(height * 0.72, body_top - height * 0.015)))
        roi = gray[top:bottom, :roi_right]
        if roi.size == 0:
            return None

        short_side = float(min(roi.shape[:2]))
        edges = cv2.Canny(cv2.GaussianBlur(roi, (3, 3), 0), 35, 110)
        raw = cv2.HoughLinesP(
            edges, 1, np.pi / 180,
            threshold=22, minLineLength=28, maxLineGap=10
        )
        if raw is None:
            return None
        segments = [tuple(map(float, row)) for row in raw.reshape(-1, 4)]
        lines = _line_clusters(segments, short_side, gap_ratio=0.025,
                                min_length=24, min_cluster_length=24,
                                offset_ratio=0.05)
        if len(lines) < 3:
            return None

        per_line = {index: [] for index in range(len(lines))}
        for first in range(len(lines)):
            for second in range(first + 1, len(lines)):
                point = _line_intersection(lines[first], lines[second])
                if point is None:
                    continue
                x, y = point
                if not (-10 <= x <= roi_right + 10 and
                        -10 <= y <= bottom - top + 10):
                    continue
                per_line[first].append(point)
                per_line[second].append(point)

        # 开放航线每条端部直线有一个未与其他线段相交的端点。
        for index, line in enumerate(lines):
            direction, normal = line["direction"], line["normal"]
            for projection in (line["low"], line["high"]):
                point = (direction[0] * projection + normal[0] * line["offset"],
                         direction[1] * projection + normal[1] * line["offset"])
                if not any(math.dist(point, old) <= max(22, short_side * 0.07)
                           for old in per_line[index]):
                    per_line[index].append(point)

        tolerance = max(10, short_side * 0.035)
        node_points = []
        node_hits = {}

        def node_for(point):
            for node, old in enumerate(node_points):
                if math.dist(point, old) <= tolerance:
                    node_hits[node] += 1
                    count = node_hits[node]
                    node_points[node] = ((old[0] * (count - 1) + point[0]) / count,
                                         (old[1] * (count - 1) + point[1]) / count)
                    return node
            node_points.append(point)
            node_hits[len(node_points) - 1] = 1
            return len(node_points) - 1

        line_nodes = {}
        edges_set = set()
        for index, line in enumerate(lines):
            found = []
            for point in per_line[index]:
                node = node_for(point)
                if node not in found:
                    found.append(node)
            direction = line["direction"]
            found.sort(key=lambda node: direction[0] * node_points[node][0] +
                       direction[1] * node_points[node][1])
            line_nodes[index] = found
            for first, second in zip(found, found[1:]):
                if first != second:
                    edges_set.add(tuple(sorted((first, second))))

        adjacency = {index: set() for index in range(len(node_points))}
        for first, second in edges_set:
            adjacency[first].add(second)
            adjacency[second].add(first)
        components, seen = [], set()
        for start in adjacency:
            if start in seen or not adjacency[start]:
                continue
            pending, component = [start], set()
            while pending:
                current = pending.pop()
                if current in component:
                    continue
                component.add(current)
                pending.extend(adjacency[current] - component)
            seen.update(component)
            component_edges = {edge for edge in edges_set
                               if edge[0] in component and edge[1] in component}
            components.append((component, component_edges))
        if not components:
            return None
        component, component_edges = max(
            components, key=lambda item: (len(item[0]),
                sum(math.dist(node_points[a], node_points[b]) for a, b in item[1])))
        if len(component) < 4:
            return None

        points = {node: (node_points[node][0], node_points[node][1] + top)
                  for node in component}
        graph = {node: set() for node in component}
        for first, second in component_edges:
            graph[first].add(second)
            graph[second].add(first)

        degrees = [len(graph[node]) for node in graph]
        path = (degrees.count(1) == 2 and
                all(degree in (1, 2) for degree in degrees))
        cycle = len(component) <= 8 and all(degree == 2 for degree in degrees)
        if not path and not cycle:
            return None

        # 将多个霍夫碎段还原出的同一条路径按端点或环路顺序展开。
        def trace(start, next_node=None):
            order = [start]
            previous = None
            current = start
            while len(order) < len(component):
                options = sorted(graph[current] - ({previous} if previous is not None else set()))
                if previous is None and next_node is not None:
                    options = [next_node] if next_node in options else options
                options = [node for node in options if node not in order]
                if not options:
                    break
                previous, current = current, options[0]
                order.append(current)
            return order

        return {"points": points, "graph": graph, "path": path,
                "cycle": cycle, "trace": trace, "image_size": [width, height]}
    except Exception:
        return None


def _recover_numbered_route(image, ocr_words, shape_hint, image_path=None):
    """只在线段拓扑完整时编号，点1优先由题卡起飞点标记定位。"""
    topology = _route_graph_from_pixels(image, ocr_words, image_path)
    if not topology:
        return None
    points, graph = topology["points"], topology["graph"]
    count = len(points)
    path, cycle = topology["path"], topology["cycle"]
    hint = _shape_hint(ocr_words, shape_hint)
    bearing_hints = _route_bearing_hints(" ".join(
        [str(shape_hint or "")] + [str(word.get("text", ""))
                                    for word in (ocr_words or [])
                                    if isinstance(word, dict)]))

    def image_bearing(first, second):
        dx = points[second][0] - points[first][0]
        dy = points[second][1] - points[first][1]
        return math.degrees(math.atan2(dx, -dy)) % 360.0

    def heading_error(first, second, expected):
        difference = abs((image_bearing(first, second) - expected) % 360.0)
        return min(difference, 360.0 - difference)

    def nearby_h_marker(candidates):
        words = [word for word in (ocr_words or []) if isinstance(word, dict) and
                 str(word.get("text", "")).strip().startswith(("☆", "★", "✩", "✪"))]
        if not words:
            words = [word for word in (ocr_words or []) if isinstance(word, dict) and
                     any(token in str(word.get("text", ""))
                         for token in ("起飞点", "返航点", "退航点", "降落点"))]
        if not words:
            return None
        marker = max(words, key=lambda word: float(word.get("confidence", 0) or 0))
        center = (float(marker.get("x", 0)) + float(marker.get("width", 0)) * 0.5,
                  float(marker.get("y", 0)) + float(marker.get("height", 0)) * 0.5)
        nearest = min(candidates, key=lambda node: math.dist(points[node], center))
        width, height = topology["image_size"]
        if math.dist(points[nearest], center) > min(width, height) * 0.08:
            return None
        return nearest

    parallel_pairs = []
    if path and (hint == "扫描航线" or count >= 8):
        endpoints = [node for node in graph if len(graph[node]) == 1]
        if len(endpoints) != 2:
            return None
        h_words = [word for word in (ocr_words or []) if isinstance(word, dict) and
                   str(word.get("text", "")).strip().startswith(("☆", "★", "✩", "✪"))]
        if not h_words:
            h_words = [word for word in (ocr_words or []) if isinstance(word, dict) and
                       any(token in str(word.get("text", ""))
                           for token in ("起飞点", "返航点", "退航点", "降落点"))]
        if h_words:
            marker = max(h_words, key=lambda word: float(word.get("confidence", 0) or 0))
            h_point = (float(marker.get("x", 0)) + float(marker.get("width", 0)) * 0.5,
                       float(marker.get("y", 0)) + float(marker.get("height", 0)) * 0.5)
            start = min(endpoints, key=lambda node: math.dist(points[node], h_point))
        else:
            # 没有返航点标记时，从图形左上端开始，结果仍附带待核对提示。
            start = min(endpoints, key=lambda node: (points[node][1], points[node][0]))
        order = topology["trace"](start)
        numbered_bearings = [item for item in bearing_hints
                             if item[0].isdigit() and item[1].isdigit() and
                             1 <= int(item[0]) <= count and
                             1 <= int(item[1]) <= count]
        if numbered_bearings:
            candidates = [topology["trace"](endpoint) for endpoint in endpoints]
            candidates = [candidate for candidate in candidates
                          if len(candidate) == count]
            if candidates:
                order = min(candidates, key=lambda candidate: sum(
                    heading_error(candidate[int(first) - 1],
                                  candidate[int(second) - 1], expected)
                    for first, second, expected in numbered_bearings))
        if len(order) != count:
            return None
        nodes = [str(index + 1) for index in range(count)]
        edges = [[nodes[index], nodes[index + 1]] for index in range(count - 1)]
        shape = "扫描航线"
        closed = False
    elif cycle and 3 <= count <= 6:
        # H 点附近通常就是1点；小圆圈 OCR 只作弱校验，不单独决定起点。
        h_words = [word for word in (ocr_words or []) if isinstance(word, dict) and
                   str(word.get("text", "")).strip().startswith(("☆", "★", "✩", "✪"))]
        if not h_words:
            h_words = [word for word in (ocr_words or []) if isinstance(word, dict) and
                       any(token in str(word.get("text", ""))
                           for token in ("起飞点", "返航点", "退航点", "降落点"))]
        if h_words:
            marker = max(h_words, key=lambda word: float(word.get("confidence", 0) or 0))
            h_point = (float(marker.get("x", 0)) + float(marker.get("width", 0)) * 0.5,
                       float(marker.get("y", 0)) + float(marker.get("height", 0)) * 0.5)
            start = min(graph, key=lambda node: math.dist(points[node], h_point))
        else:
            start = min(graph, key=lambda node: (points[node][1], points[node][0]))
        neighbors = sorted(graph[start])
        if len(neighbors) != 2:
            return None
        candidates = [topology["trace"](start, neighbor) for neighbor in neighbors]
        numbered_bearings = [item for item in bearing_hints
                             if item[0].isdigit() and item[1].isdigit() and
                             1 <= int(item[0]) <= count and
                             1 <= int(item[1]) <= count]
        if numbered_bearings:
            candidates = [topology["trace"](candidate_start, neighbor)
                          for candidate_start in graph
                          for neighbor in sorted(graph[candidate_start])]
        candidates = [order for order in candidates if len(order) == count]
        candidates = list({tuple(order): order for order in candidates}.values())
        if len(candidates) != 2:
            if not numbered_bearings or len(candidates) < 2:
                return None

        text = " ".join([str(shape_hint or "")] + [
            str(word.get("text", "")) for word in (ocr_words or [])
            if isinstance(word, dict)])
        distance_pairs = {}
        for first, second in ((1, 2), (3, 4)):
            match = re.search(
                r"%d\s*(?:到|[-－—])\s*%d[^\d]{0,12}(\d+(?:\.\d+)?)\s*(?:m|米)" %
                (first, second), text, re.I)
            if match:
                distance_pairs[(first, second)] = float(match.group(1))
        angle_match = re.search(r"∠\s*4\s*=\s*(\d+(?:\.\d+)?)", text)
        target_id = None
        target_match = (re.search(r"H\s*点\s*在\s*(\d+)\s*点", text, re.I) or
                        re.search(r"(\d+)\s*点\s*位于\s*H\s*点", text, re.I))
        if target_match:
            target_id = int(target_match.group(1))

        def score_order(order):
            score = 0.0
            for first, second, expected in numbered_bearings:
                score -= heading_error(order[int(first) - 1],
                                       order[int(second) - 1], expected) / 30.0
            if target_id == 1 and h_words:
                # 环路起点已选为离H标记最近的顶点。
                score += 1.0
            if (1, 2) in distance_pairs and (3, 4) in distance_pairs and count == 4:
                pixels_12 = math.dist(points[order[0]], points[order[1]])
                pixels_34 = math.dist(points[order[2]], points[order[3]])
                expected = distance_pairs[(1, 2)] / max(distance_pairs[(3, 4)], 1e-6)
                observed = pixels_12 / max(pixels_34, 1e-6)
                score -= abs(math.log(max(observed, 1e-6) / max(expected, 1e-6)))
            if angle_match and count == 4:
                a, vertex, b = points[order[2]], points[order[3]], points[order[0]]
                va, vb = (a[0] - vertex[0], a[1] - vertex[1]), (b[0] - vertex[0], b[1] - vertex[1])
                denominator = math.hypot(*va) * math.hypot(*vb)
                cosine = ((va[0] * vb[0] + va[1] * vb[1]) / denominator
                          if denominator else 1.0)
                angle = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
                score -= abs(angle - float(angle_match.group(1))) / 90.0
            return score

        order = max(candidates, key=score_order)
        nodes = [str(index + 1) for index in range(count)]
        edges = [[nodes[index], nodes[(index + 1) % count]]
                 for index in range(count)]
        closed = True
        cycle_points = [points[node] for node in order]

        def parallel_error(first, second):
            ax, ay = first[1][0] - first[0][0], first[1][1] - first[0][1]
            bx, by = second[1][0] - second[0][0], second[1][1] - second[0][1]
            denominator = math.hypot(ax, ay) * math.hypot(bx, by)
            if denominator <= 1e-8:
                return 90.0
            return math.degrees(math.asin(min(1.0, abs(ax * by - ay * bx) / denominator)))

        edge_points = [(cycle_points[index], cycle_points[(index + 1) % count])
                       for index in range(count)]
        if count == 4:
            for first, second in ((0, 2), (1, 3)):
                if parallel_error(edge_points[first], edge_points[second]) <= 6.0:
                    parallel_pairs.append([[nodes[first], nodes[(first + 1) % count]],
                                           [nodes[second], nodes[(second + 1) % count]]])
        shape = ("梯形" if len(parallel_pairs) == 1 else
                 "平行四边形" if len(parallel_pairs) == 2 else
                 {3: "三角形", 4: "四边形", 5: "五边形", 6: "六边形"}.get(count, ""))
    else:
        return None

    width, height = topology["image_size"]
    positions = {label: {"x": round(points[node][0] / width * 1000, 1),
                         "y": round(points[node][1] / height * 1000, 1)}
                 for label, node in zip(nodes, order)}
    return {
        "source": "local_cv", "status": "ready", "shape": shape,
        "nodes": nodes, "geometry_edges": edges, "route_order": nodes,
        "parallel_edge_pairs": parallel_pairs,
        "point_positions": positions,
        "confidence": 0.76, "geometry_confidence": 0.76,
        "validated_cycle": closed, "topology_valid": True,
        "image_size": topology["image_size"],
        "warnings": ["小号航点编号未被OCR完整读出，已按线段拓扑和起飞点标记恢复；请核对编号。"],
    }


def _line_intersection(first, second):
    ax, ay = first["normal"]
    bx, by = second["normal"]
    determinant = ax * by - ay * bx
    if abs(determinant) < 0.035:
        return None
    c1, c2 = first["offset"], second["offset"]
    x = (c1 * by - ay * c2) / determinant
    y = (ax * c2 - c1 * bx) / determinant
    point = (x, y)
    margin = max(12.0, min(first["length"], second["length"]) * 0.04)
    for line in (first, second):
        projection = line["direction"][0] * x + line["direction"][1] * y
        if projection < line["low"] - margin or projection > line["high"] + margin:
            return None
    return point


def _simple_polygon(points):
    def orientation(a, b, c):
        return ((b[0] - a[0]) * (c[1] - a[1]) -
                (b[1] - a[1]) * (c[0] - a[0]))

    def crosses(a, b, c, d):
        first = orientation(a, b, c)
        second = orientation(a, b, d)
        third = orientation(c, d, a)
        fourth = orientation(c, d, b)
        return first * second < -1e-5 and third * fourth < -1e-5

    count = len(points)
    for first in range(count):
        a, b = points[first], points[(first + 1) % count]
        for second in range(first + 1, count):
            if second in (first, (first + 1) % count) or (second + 1) % count in (first, (first + 1) % count):
                continue
            c, d = points[second], points[(second + 1) % count]
            if crosses(a, b, c, d):
                return False
    return True


def _polygon_area(points):
    return abs(sum(points[index][0] * points[(index + 1) % len(points)][1] -
                   points[index][1] * points[(index + 1) % len(points)][0]
                   for index in range(len(points))) / 2.0)


def _cycle_candidates(lines, count, intersections):
    adjacency = {index: set() for index in range(len(lines))}
    for first, second in intersections:
        adjacency[first].add(second)
        adjacency[second].add(first)
    found, seen = [], set()

    def visit(start, path):
        if len(path) == count:
            if start not in adjacency[path[-1]] or path[1] > path[-1]:
                return
            key = tuple(path)
            if key not in seen:
                seen.add(key)
                found.append(key)
            return
        for node in sorted(adjacency[path[-1]]):
            if node <= start or node in path:
                continue
            visit(start, path + [node])

    for start in range(len(lines)):
        visit(start, [start])
    return found[:1200]


def refine_partial_polygon(image_path, ocr_words, source_image_size,
                           diagram_model):
    """用题卡墨线和已识别的顶点编号修正视觉模型的多边形轮廓。"""
    try:
        import cv2
        import numpy as np

        nodes = [str(node) for node in (diagram_model or {}).get("nodes", [])]
        edges = [list(map(str, edge[:2])) for edge in
                 (diagram_model or {}).get("geometry_edges", [])
                 if isinstance(edge, (list, tuple)) and len(edge) >= 2]
        count = len(nodes)
        shape = str((diagram_model or {}).get("shape") or "")
        if count < 3 or count > 6 or shape in ("圆形", "扫描航线"):
            return None
        order = _cycle_order(nodes, edges)
        if len(order) != count:
            return None

        reader = QImageReader(str(image_path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            return None
        image = image.convertToFormat(QImage.Format.Format_Grayscale8)
        width, height, stride = image.width(), image.height(), image.bytesPerLine()
        gray = np.frombuffer(bytes(image.constBits()), dtype=np.uint8)
        gray = gray.reshape(height, stride)[:, :width].copy()
        short_side = float(min(width, height))

        try:
            source_width, source_height = map(float, source_image_size or ())
            if source_width <= 0 or source_height <= 0:
                raise ValueError
        except (TypeError, ValueError):
            source_width, source_height = float(width), float(height)
        scale_x, scale_y = width / source_width, height / source_height

        body_y = min((float(word.get("y", 0)) for word in (ocr_words or [])
                      if isinstance(word, dict) and re.match(
                          r"^\s*1\s*[,、.．)]", str(word.get("text", "")))),
                     default=height * 0.76)
        body_y *= scale_y
        circled = {chr(0x2460 + index): str(index + 1) for index in range(20)}
        labels = {}
        known = set(nodes)
        for word in (ocr_words or []):
            if not isinstance(word, dict):
                continue
            label = circled.get(str(word.get("text", "")).strip(),
                                str(word.get("text", "")).strip())
            if label not in known:
                continue
            try:
                confidence = float(word.get("confidence", 0) or 0)
                x = (float(word.get("x", 0)) + float(word.get("width", 0)) / 2) * scale_x
                y = (float(word.get("y", 0)) + float(word.get("height", 0)) / 2) * scale_y
            except (TypeError, ValueError, OverflowError):
                continue
            if confidence < 0.48 or y >= body_y:
                continue
            labels.setdefault(label, []).append((x, y, confidence))
        if len(labels) < 2:
            return None
        anchors = {label: max(items, key=lambda item: item[2])[:2]
                   for label, items in labels.items()}

        model_positions = (diagram_model or {}).get("point_positions") or {}
        model_points = {}
        for label in nodes:
            raw = model_positions.get(label)
            if not isinstance(raw, dict):
                continue
            try:
                model_points[label] = (float(raw["x"]), float(raw["y"]))
            except (KeyError, TypeError, ValueError, OverflowError):
                continue

        def image_axis(axis):
            pairs = []
            for label, anchor in anchors.items():
                if label not in model_points:
                    continue
                for other, other_anchor in anchors.items():
                    if other <= label or other not in model_points:
                        continue
                    model_delta = model_points[other][axis] - model_points[label][axis]
                    image_delta = other_anchor[axis] - anchor[axis]
                    if abs(model_delta) > 1.0 and abs(image_delta) > short_side * 0.04:
                        pairs.append(image_delta / model_delta)
            scale = sorted(pairs)[len(pairs) // 2] if pairs else short_side / 1000.0
            if not math.isfinite(scale) or abs(scale) < 0.01:
                scale = short_side / 1000.0
            offsets = [anchor[axis] - model_points[label][axis] * scale
                       for label, anchor in anchors.items() if label in model_points]
            offset = sorted(offsets)[len(offsets) // 2] if offsets else 0.0
            return scale, offset

        axis_maps = [image_axis(0), image_axis(1)]
        projected = []
        for point in model_points.values():
            projected.append(tuple(point[axis] * axis_maps[axis][0] +
                                   axis_maps[axis][1] for axis in (0, 1)))
        bounds_points = list(anchors.values()) + projected
        pad_x = short_side * (0.20 if projected else 0.36)
        pad_y = short_side * (0.20 if projected else 0.36)
        x0 = max(0, int(min(point[0] for point in bounds_points) - pad_x))
        x1 = min(width, int(max(point[0] for point in bounds_points) + pad_x))
        title_bottoms = []
        for word in ocr_words or []:
            if not isinstance(word, dict):
                continue
            if "地面站" not in str(word.get("text", "")) and "真题" not in str(word.get("text", "")):
                continue
            try:
                title_bottoms.append((float(word.get("y", 0)) +
                                      float(word.get("height", 0))) * scale_y)
            except (TypeError, ValueError, OverflowError):
                continue
        title_floor = max(title_bottoms, default=0.0) + short_side * 0.02
        y0 = max(0, int(max(min(point[1] for point in bounds_points) - pad_y,
                            title_floor)))
        y1 = min(height, int(min(body_y - short_side * 0.045,
                                 max(point[1] for point in anchors.values()) + pad_y)))
        if x1 - x0 < short_side * 0.30 or y1 - y0 < short_side * 0.18:
            return None

        roi = gray[y0:y1, x0:x1]
        edges_image = cv2.Canny(cv2.GaussianBlur(roi, (3, 3), 0), 45, 145)
        threshold = max(18, int(short_side * 0.026))
        min_length = max(42, int(short_side * 0.085))
        max_gap = max(10, int(short_side * 0.035))
        raw_lines = cv2.HoughLinesP(
            edges_image, 1, np.pi / 360, threshold=threshold,
            minLineLength=min_length, maxLineGap=max_gap)
        if raw_lines is None:
            return None
        segments = []
        for raw_line in raw_lines[:800]:
            x_start, y_start, x_end, y_end = map(int, np.asarray(raw_line).reshape(-1)[:4])
            segments.append((x_start + x0, y_start + y0,
                             x_end + x0, y_end + y0))
        lines = _line_clusters(segments, short_side)
        if len(lines) < count:
            return None

        intersections = {}
        for first in range(len(lines)):
            for second in range(first + 1, len(lines)):
                point = _line_intersection(lines[first], lines[second])
                if point is None:
                    continue
                if not (x0 - short_side * 0.05 <= point[0] <= x1 + short_side * 0.05 and
                        y0 - short_side * 0.05 <= point[1] <= y1 + short_side * 0.05):
                    continue
                intersections[(first, second)] = point
        cycles = _cycle_candidates(lines, count, intersections)
        if not cycles:
            return None

        max_anchor_distance = short_side * 0.065
        candidates = []
        pixel_bytes = gray.tobytes()
        for cycle in cycles:
            vertices = [intersections[tuple(sorted((cycle[index],
                          cycle[(index + 1) % count])))] for index in range(count)]
            crossing_count = _crossing_count(vertices)
            if shape == "蝴蝶形":
                if crossing_count != 1:
                    continue
                xs = [point[0] for point in vertices]
                ys = [point[1] for point in vertices]
                extent = (max(xs) - min(xs)) * (max(ys) - min(ys))
                if extent < short_side * short_side * 0.045:
                    continue
            elif (crossing_count or
                  _polygon_area(vertices) < short_side * short_side * 0.012):
                continue
            if min(math.dist(vertices[index], vertices[(index + 1) % count])
                   for index in range(count)) < short_side * 0.08:
                continue
            distances = {}
            for label, anchor in anchors.items():
                distances[label] = sorted(
                    ((math.dist(anchor, vertex), index) for index, vertex in enumerate(vertices)))
            if any(not values or values[0][0] > max_anchor_distance
                   for values in distances.values()):
                continue

            for rotation in range(count):
                for direction in (1, -1):
                    mapped = {order[index]: vertices[(rotation + direction * index) % count]
                              for index in range(count)}
                    mapped_indices = {(name, (rotation + direction * index) % count)
                                      for index, name in enumerate(order)}
                    if any((label, distances[label][0][1]) not in mapped_indices
                           for label in anchors):
                        continue
                    anchor_error = sum(math.dist(mapped[label], anchor)
                                       for label, anchor in anchors.items()) / len(anchors)
                    edge_scores = []
                    for index, line_index in enumerate(cycle):
                        first = vertices[index]
                        second = vertices[(index + 1) % count]
                        edge_scores.append(_edge_support(
                            pixel_bytes, width, height, width, first, second,
                            math.hypot(width, height)))
                    mean_support = sum(edge_scores) / len(edge_scores)
                    if min(edge_scores, default=0.0) < 0.25:
                        continue
                    score = (0.50 * mean_support + 0.30 * min(edge_scores) +
                             0.20 * (1.0 - anchor_error / max_anchor_distance))
                    candidates.append((score, anchor_error, mean_support,
                                       min(edge_scores), mapped, vertices))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], -item[2], -item[3]), reverse=True)
        score, anchor_error, mean_support, min_support, mapped, _vertices = candidates[0]
        parallel_pairs = []
        visual_shape = shape
        if count == 4:
            cycle_edges = [(order[index], order[(index + 1) % count])
                           for index in range(count)]

            def parallel_error(first, second):
                ax = mapped[first[1]][0] - mapped[first[0]][0]
                ay = mapped[first[1]][1] - mapped[first[0]][1]
                bx = mapped[second[1]][0] - mapped[second[0]][0]
                by = mapped[second[1]][1] - mapped[second[0]][1]
                denominator = math.hypot(ax, ay) * math.hypot(bx, by)
                if denominator <= 1e-8:
                    return 90.0
                sine = min(1.0, abs(ax * by - ay * bx) / denominator)
                return math.degrees(math.asin(sine))

            for first_index, second_index in ((0, 2), (1, 3)):
                first, second = cycle_edges[first_index], cycle_edges[second_index]
                if parallel_error(first, second) <= 6.0:
                    parallel_pairs.append([list(first), list(second)])
            if len(parallel_pairs) == 1:
                visual_shape = "梯形"
            elif len(parallel_pairs) == 2:
                visual_shape = "平行四边形"
        if shape == "蝴蝶形" and _crossing_count(
                [mapped[node] for node in order]) == 1:
            visual_shape = "蝴蝶形"

        right_angle_vertex = None
        if visual_shape == "直角三角形" and count == 3:
            for index, label in enumerate(order):
                vertex = mapped[label]
                first = mapped[order[(index - 1) % count]]
                second = mapped[order[(index + 1) % count]]
                ax, ay = first[0] - vertex[0], first[1] - vertex[1]
                bx, by = second[0] - vertex[0], second[1] - vertex[1]
                denominator = math.hypot(ax, ay) * math.hypot(bx, by)
                cosine = (ax * bx + ay * by) / denominator if denominator else 1.0
                if abs(cosine) <= 0.14:
                    right_angle_vertex = label
                    break

        return {
            "source": "local_cv", "status": "ready", "shape": visual_shape,
            "right_angle_vertex": right_angle_vertex,
            "nodes": nodes, "geometry_edges": edges,
            "route_order": list(order), "validated_cycle": True,
            "flight_closed": True,
            "parallel_edge_pairs": parallel_pairs,
            "point_positions": {
                node: {"x": round(mapped[node][0] / max(width, height) * 1000, 1),
                       "y": round(mapped[node][1] / max(width, height) * 1000, 1)}
                for node in nodes},
            "geometry_source": "local_cv_hough",
            "geometry_validated": True,
            "geometry_confidence": round(max(0.90, min(0.99, score)), 3),
            "confidence": round(max(0.90, min(0.99, score)), 3),
            "warnings": ["本地线段检测已恢复题卡图形轮廓"],
            "topology_errors": [],
            "topology_valid": True,
            "image_size": [width, height],
        }
    except Exception:
        return None
