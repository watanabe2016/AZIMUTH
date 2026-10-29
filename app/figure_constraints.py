"""从题卡 OCR 文字框提取图上航段；模糊配对不得进入自动答案。"""

import math
import re
import unicodedata


_METRIC = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*m(?:\s*[,，/]?\s*(\d+(?:\.\d+)?)\s*[°º])?\s*$", re.I)


def _centre(word):
    return (float(word["x"]) + float(word["width"]) / 2,
            float(word["y"]) + float(word["height"]) / 2)


def _bearing(a, b):
    return math.degrees(math.atan2(b[0] - a[0], a[1] - b[1])) % 360


def _difference(a, b):
    return abs((a - b + 180) % 360 - 180)


def _edge_score(position, a, b, label_bearing=None, label_angle=None):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 20:
        return None
    fraction = ((position[0] - a[0]) * dx + (position[1] - a[1]) * dy) / length**2
    offset = abs((position[0] - a[0]) * dy - (position[1] - a[1]) * dx) / length
    if not -0.2 <= fraction <= 1.2 or offset > max(70, length * 0.42):
        return None
    score = offset / length + max(0, abs(fraction - 0.5) - 0.5) * 0.6
    reverse = False
    if label_bearing is not None:
        forward = _difference(_bearing(a, b), label_bearing)
        backward = _difference(_bearing(b, a), label_bearing)
        reverse = backward < forward
        difference = min(forward, backward)
        if difference > 32:
            return None
        score += difference / 100
    if label_angle is not None:
        axis = math.degrees(math.atan2(dy, dx)) % 180
        rotation = abs((axis - label_angle + 90) % 180 - 90)
        if rotation > 30:
            return None
        score += rotation / 120
    return score, reverse


def extract(words, spec, image_size=None):
    """返回可追溯的标注约束；只接收唯一匹配到航段的高置信文字。"""
    nodes = set(str(node) for node in spec.get("nodes") or [])
    points = {}
    metrics = []
    for word in words or []:
        token = unicodedata.normalize("NFKC", str(word.get("text") or "")).strip()
        try:
            confidence = float(word.get("confidence", 0))
        except (TypeError, ValueError):
            continue
        if confidence > 1:
            confidence /= 100
        if confidence < 0.88 or not token:
            continue
        if token in nodes or token.upper() == "H":
            points.setdefault(token.upper() if token.upper() == "H" else token,
                              []).append(_centre(word))
        match = _METRIC.fullmatch(token)
        if match:
            distance = float(match.group(1))
            bearing = float(match.group(2)) if match.group(2) else None
            if 5 <= distance <= 100000 and (bearing is None or 0 <= bearing <= 360):
                metrics.append((word, distance, bearing, token, confidence))
    positions = {key: values[0] for key, values in points.items() if len(values) == 1}
    edge_set = set()
    edges = []
    for a, b in list(spec.get("geometry_edges") or []) + list(spec.get("flight_edges") or []):
        a, b = str(a), str(b)
        pair = frozenset((a, b))
        if a in positions and b in positions and pair not in edge_set:
            edges.append((a, b))
            edge_set.add(pair)
    if "1" in positions and "H" in positions and frozenset(("H", "1")) not in edge_set:
        edges.append(("H", "1"))
    result = {"segments": [], "distances": [], "flight_edges": [],
              "unmatched": [], "ambiguous": []}
    matched = set()
    for word, distance, bearing, token, confidence in metrics:
        place = _centre(word)
        candidates = []
        for a, b in edges:
            scored = _edge_score(place, positions[a], positions[b], bearing,
                                 float(word.get("angle", 0))
                                 if "angle" in word else None)
            if scored is not None:
                candidates.append((scored[0], a, b, scored[1]))
        candidates.sort()
        if not candidates or (len(candidates) > 1 and
                              candidates[1][0] - candidates[0][0] < 0.12):
            # 起飞点 H 常被 OCR 漏掉；图卡只存在一条额外进场航段时，
            # 由标注到 1 号点的方向确认 H→1。
            if bearing is not None and "H" not in positions and "1" in positions:
                first = positions["1"]
                from_label = _bearing(place, first)
                if (_difference(from_label, bearing) < 28 and
                        math.dist(place, first) < max(280, min(image_size or (0, 0)) * 0.4)):
                    if not any(item["a"] == "1" and item["b"] == "H"
                               for item in result["segments"]):
                        result["segments"].append({
                            "a": "1", "b": "H", "bearing": bearing,
                            "distance": distance, "confidence": confidence,
                            "source": "图卡 OCR：" + token})
                        result["flight_edges"].append(["H", "1"])
                        continue
            result["ambiguous" if candidates else "unmatched"].append(token)
            continue
        _, a, b, reverse = candidates[0]
        pair = frozenset((a, b))
        if pair in matched:
            result["ambiguous"].append(token)
            continue
        matched.add(pair)
        if bearing is None:
            result["distances"].append({"a": a, "b": b, "value": distance,
                                        "kind": "distance", "confidence": confidence,
                                        "source": "图卡 OCR：" + token})
        else:
            start, end = (b, a) if reverse else (a, b)
            result["segments"].append({"a": end, "b": start,
                                       "bearing": bearing, "distance": distance,
                                       "confidence": confidence,
                                       "source": "图卡 OCR：" + token})
    return result
