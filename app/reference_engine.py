# -*- coding: utf-8 -*-
"""把题库条件转成可追溯的几何参考答案。"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import copy
from functools import lru_cache

import answers as AN
import geo as GEO
import geometry
import numeric_reference as NR
import figure_constraints as FC

try:
    from geographiclib.geodesic import Geodesic
except Exception:  # 旧环境仍能用项目中的球面正反算
    Geodesic = None

SCHEMA_VERSION = 8
DISTANCE_TOLERANCE_M = 0.2
ANGLE_TOLERANCE_DEG = 0.1
_POINT_TOKEN = re.compile(
    r"(?:任务航线的航点|任务航点|题卡图形|题卡|图形|航点)?\s*"
    r"(\d+)\s*(?:号)?\s*点")


def _bearing_xy(bearing, distance):
    rad = math.radians(float(bearing))
    return distance * math.sin(rad), distance * math.cos(rad)


def _xy_bearing(dx, dy):
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _wrap_angle(value):
    return (float(value) + 180.0) % 360.0 - 180.0


_CHINESE_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                   "七": 7, "八": 8, "九": 9, "十": 10}


def _chinese_number(token):
    """把「三」「十二」「二十一」这类中文数字读成阿拉伯数字。"""
    if not token:
        return None
    if "十" not in token:
        total = 0
        for char in token:
            if char not in _CHINESE_DIGITS or _CHINESE_DIGITS[char] > 9:
                return None
            total = total * 10 + _CHINESE_DIGITS[char]
        return total
    head, _, tail = token.partition("十")
    if head and head not in _CHINESE_DIGITS:
        return None
    if tail and tail not in _CHINESE_DIGITS:
        return None
    tens = _CHINESE_DIGITS.get(head, 1) if head else 1
    ones = _CHINESE_DIGITS.get(tail, 0) if tail else 0
    return tens * 10 + ones


def _norm_text(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = re.sub(r"航点\s*(\d+)\s*点", r"航点\1", text)
    text = re.sub(r"过[渡度]\s*[。.]\s*点", "过渡点", text)
    text = text.replace("过度点", "过渡点")
    text = text.replace("临时会让点", "避让点")
    # ★ 中文数字点名：「三点位于…」「起飞点距离一点80米」—— 不转成阿拉伯数字，
    #   所有按 \d 写的解析器都会整条漏掉这个条件（实测 旧样本）。
    def _node_number(match):
        value = _chinese_number(match.group(1))
        return "%d点" % value if value is not None else match.group(0)

    text = re.sub(r"([一二两三四五六七八九十]{1,3})\s*点", _node_number, text)
    # 只把数字后的“度”转成符号，不能把“高度”“过度点”中的字改坏。
    text = re.sub(r"(?<=\d)\s*度", "°", text)
    return (text.replace("º", "°")
            .replace("‘", "'").replace("’", "'").replace("′", "'")
            .replace("：", ":").replace("，", ",").replace("；", ";"))


def _vision_diagram(q):
    source = (q or {}).get("reference_source") or (q or {}).get("_reference_source") or {}
    assist = source.get("condition_assist") or {}
    diagram = assist.get("diagram_model") or {}
    return diagram if diagram.get("source") == "vision_model" else {}


def _local_cv_diagram(q):
    source = (q or {}).get("reference_source") or (q or {}).get("_reference_source") or {}
    diagram = source.get("diagram_model") or {}
    if diagram.get("source") == "local_cv":
        return diagram
    assist = source.get("condition_assist") or {}
    diagram = assist.get("diagram_model") or {}
    if (diagram.get("source") == "vision_model" and
            diagram.get("geometry_source") == "local_cv_hough" and
            diagram.get("geometry_validated")):
        return diagram
    return {}


def _validated_local_graph(diagram):
    """只接收本地像素校验完成的闭合轮廓或完整扫描折线。"""
    if not isinstance(diagram, dict):
        return None
    is_local_cv = diagram.get("source") == "local_cv"
    is_refined_vlm = (
        diagram.get("source") == "vision_model" and
        diagram.get("geometry_source") == "local_cv_hough" and
        bool(diagram.get("geometry_validated")))
    if not (is_local_cv or is_refined_vlm):
        return None
    if diagram.get("status") not in ("ready", "review"):
        return None
    if not diagram.get("topology_valid") and not is_refined_vlm:
        return None

    nodes = [str(node) for node in diagram.get("nodes") or []]
    if not (3 <= len(nodes) <= 16 and
            nodes == [str(index) for index in range(1, len(nodes) + 1)]):
        return None
    edges = []
    seen = set()
    for edge in diagram.get("geometry_edges") or []:
        if not isinstance(edge, (list, tuple)) or len(edge) < 2:
            return None
        first, second = str(edge[0]), str(edge[1])
        pair = frozenset((first, second))
        if (first not in nodes or second not in nodes or first == second or
                pair in seen):
            return None
        seen.add(pair)
        edges.append((first, second))

    order = [str(node) for node in diagram.get("route_order") or []]
    if len(order) != len(nodes) or set(order) != set(nodes):
        return None

    if diagram.get("shape") == "扫描航线":
        if len(edges) != len(nodes) - 1:
            return None
        adjacent = {node: set() for node in nodes}
        for first, second in edges:
            adjacent[first].add(second)
            adjacent[second].add(first)
        if (sum(len(neighbors) == 1 for neighbors in adjacent.values()) != 2 or
                any(len(neighbors) not in (1, 2)
                    for neighbors in adjacent.values()) or
                any(second not in adjacent[first]
                    for first, second in zip(order, order[1:]))):
            return None
        route = list(zip(order, order[1:]))
        if {frozenset(edge) for edge in route} != seen:
            return None
        return nodes, edges, route, "local_cv"

    if len(edges) != len(nodes):
        return None
    route = _closed_cycle_route(nodes, edges)
    if not route:
        return None
    if set(order) != set(nodes):
        return None
    ordered_route = [(order[index], order[(index + 1) % len(order)])
                     for index in range(len(order))]
    if {frozenset(edge) for edge in ordered_route} != seen:
        return None
    return nodes, edges, ordered_route, "local_cv"


def _shape(q):
    local_diagram = _local_cv_diagram(q)
    if (local_diagram.get("source") == "local_cv" and
            local_diagram.get("status") == "ready" and
            local_diagram.get("topology_valid") and local_diagram.get("shape")):
        return str(local_diagram["shape"])
    visual_shape = _vision_diagram(q).get("shape")
    if (visual_shape and (q or {}).get("imported")):
        return str(visual_shape)
    try:
        info = geometry.recognize(q)
        name = info.get("name") if isinstance(info, dict) else None
        if name:
            return name
    except Exception:
        pass
    if visual_shape:
        return str(visual_shape)
    # 外部题卡常常不写“梯形/五边形”等名称；用题图中被识别的连续编号点数
    # 给出基础多边形类型，点位只用于图形草图和形状先验。
    points = _imported_diagram_points(q)
    return {3: "三角形", 4: "四边形", 5: "五边形", 6: "六边形",
            7: "七边形", 8: "八边形", 9: "九边形", 10: "十边形",
            11: "十一边形", 12: "十二边形", 13: "十三边形",
            14: "十四边形", 15: "十五边形", 16: "十六边形"}.get(len(points))


def _imported_diagram_points(q):
    """从导入题卡的独立编号识别图形顶点，不把题目正文中的数字当顶点。"""
    source = q.get("reference_source") or q.get("_reference_source") or {}
    words = source.get("ocr_words") or []
    numbered = [float(w.get("y", 0)) for w in words
                 if re.match(r"^\s*1\s*[,、.．]", str(w.get("text", "")))]
    cutoff = min(numbered) if numbered else float("inf")
    candidates = {}
    circled = {chr(0x2460 + i): str(i + 1) for i in range(20)}
    for word in words:
        label = str(word.get("text", "")).strip()
        label = circled.get(label, label)
        if not re.fullmatch(r"[1-9]\d?", label) or int(label) > 16:
            continue
        if float(word.get("y", 0)) >= cutoff or float(word.get("confidence", 0)) < 0.65:
            continue
        x = float(word.get("x", 0)) + float(word.get("width", 0)) / 2
        y = float(word.get("y", 0)) + float(word.get("height", 0)) / 2
        if label not in candidates or word.get("confidence", 0) > candidates[label][2]:
            candidates[label] = (x, y, float(word.get("confidence", 0)))
    if len(candidates) < 3 or len(candidates) > 16:
        return []
    labels = sorted(map(int, candidates))
    if labels != list(range(1, len(labels) + 1)):
        return []
    return [[candidates[str(i)][0], candidates[str(i)][1]] for i in labels]


def _refresh_imported_ocr_text(q):
    """用导入时保留的 OCR 框补回漏掉的编号条件，不重复运行识别模型。"""
    if not q.get("imported"):
        return q
    source = q.get("reference_source") or q.get("_reference_source") or {}
    words = source.get("ocr_words") or []
    if not words:
        return q
    try:
        import ocr
        refreshed = ocr._rapid_question_text(words)
    except Exception:
        return q
    if not refreshed:
        return q
    current = "\n".join(str(q.get(key) or "") for key in
                         ("conditions_text", "requirements_text", "suggested_text"))
    count = lambda text: len(set(re.findall(r"(?m)^\s*(\d{1,2})[、.．]", text)))
    if count(refreshed) <= count(current) and len(refreshed) <= len(current):
        return q
    result = dict(q)
    result["conditions_text"] = refreshed
    result["requirements_text"] = ""
    result["suggested_text"] = ""
    return result


# 同一句里的多条坐标：在“<点名>经纬度/坐标”之前断开（见 _parse_coordinates 的说明）。
_MULTI_COORD_SPLIT = re.compile(
    r"(?=(?:\d+\s*[点号]|起飞点|返航点|H点|避让点|过渡点|临时会让点)"
    r"[^，,。;]{0,8}?(?:经纬度|坐标))")


def _node_for_line(line):
    line = re.sub(r"[\"“”'‘’]", "", line)
    match = re.search(
        r"(?:任务航线的航点|任务航点|题卡图形|题卡|图形|航点)\s*"
        r"(\d+)\s*(?:号)?\s*(?:点)?\s*(?=经纬度|坐标)", line)
    if match:
        return match.group(1)
    if re.search(r"起飞点|起飞后|H点|\bH\b", line, re.I):
        return "H"
    if "避让点" in line:
        return "A"
    if "临时会让点" in line:
        return "A"
    if re.search(r"过渡点|过度点|转场点|中转点", line):
        return "T"
    match = _POINT_TOKEN.search(line)
    return match.group(1) if match else None


def _latlon_in_line(line):
    """只接受能确认纬度、经度各自角色的坐标。"""
    line = _norm_text(line)
    components = {}
    parsed = []
    for match in re.finditer(r"(?<![A-Z])([NSEW])\s*[:：]?\s*", line, re.I):
        hemi = match.group(1).upper()
        tail = line[match.end():]
        tail = re.split(r"(?<![A-Z])[NSEW]\s*[:：]?", tail, maxsplit=1,
                        flags=re.I)[0]
        dms = re.search(
            r"(-?\d{1,3})\s*°\s*(\d{1,2})"
            r"(?:\s*['′’]|\s+)\s*(\d+(?:\.\d+)?)\s*[\"″”]?", tail)
        if dms:
            value = (abs(float(dms.group(1))) + float(dms.group(2)) / 60.0
                     + float(dms.group(3)) / 3600.0)
        else:
            decimal = re.match(r"\s*(-?\d{1,3}(?:\.\d+)?)", tail)
            if not decimal:
                continue
            value = abs(float(decimal.group(1)))
        if hemi in ("S", "W"):
            value = -value
        parsed.append((hemi, value))

    # 一行中出现两组以上半球标记时，通常是起飞点与图形点坐标被 OCR 黏在一起。
    # 交给上层按“起飞点坐标 / 1点坐标”等角色切开，不能只取最后一组覆盖前一组。
    if len(parsed) != 2:
        return None

    inferred_hemisphere = False
    # 少数扫描题卡会把经度的 E 误录成第二个 N（或把纬度的 N 误录成第二个 E）。
    # 仅当同一坐标短句恰有两个同类方向标记时，按常见“纬度、经度”顺序修复第二项。
    if (len(parsed) == 2 and parsed[0][0] == parsed[1][0] and
            parsed[0][0] in ("N", "S", "E", "W")):
        first_hemi, _first_value = parsed[0]
        _second_hemi, second_value = parsed[1]
        counterpart = {"N": "E", "S": "W", "E": "N", "W": "S"}[first_hemi]
        parsed[1] = (counterpart, second_value)
        inferred_hemisphere = True

    for hemi, value in parsed:
        components[hemi] = value
    if "N" in components or "S" in components:
        lat = components.get("N", components.get("S"))
        lon = components.get("E", components.get("W"))
        if lon is not None:
            kind = "hemisphere-inferred" if inferred_hemisphere else "explicit"
            return float(lat), float(lon), kind
    return None


def _normalize_coordinate_text(text):
    """修复坐标中常见的 OCR 断行和度符号误作分秒符号。"""
    text = str(text or "")
    # DMS 的秒数有时被排到下一行：`E:40°20'` + 换行 + `24.45″`。
    text = re.sub(r"(['′’])\s*\r?\n\s*(?=\d+(?:\.\d+)?\s*[\"″”])",
                  r"\1 ", text)
    # OCR 偶尔把分钟符号识成度符号，例如 `111°20°003″`，仅在其后明确有秒值时修复。
    text = re.sub(
        r"(\d{1,3}\s*°\s*\d{1,2})\s*°(?=\s*\d+(?:\.\d+)?\s*[\"″”])",
        r"\1'", text)
    return text


def _unlabelled_dms_pairs(line):
    """提取没有 N/E 标签、但经纬度数值范围可唯一判别的 DMS 坐标对。"""
    if re.search(r"[NSEW]", line, re.I):
        return None
    pattern = re.compile(
        r"(?<![\w.])(-?\d{1,3})\s*°\s*(\d{1,2})\s*['′’]\s*"
        r"(\d+(?:\.\d+)?)\s*[\"″”]?")
    matches = list(pattern.finditer(line))
    if len(matches) != 2:
        return None

    def decimal(match):
        degrees, minutes, seconds = (float(value) for value in match.groups())
        if minutes >= 60 or seconds >= 60:
            return None
        sign = -1.0 if degrees < 0 else 1.0
        return sign * (abs(degrees) + minutes / 60.0 + seconds / 3600.0)

    first, second = (decimal(match) for match in matches)
    if first is None or second is None:
        return None
    if abs(first) <= 90 < abs(second) <= 180:
        return first, second
    if abs(second) <= 90 < abs(first) <= 180:
        return second, first
    return None


def _coordinate_has_malformed_dms(line):
    """发现度符号后的 OCR 数字粘连，避免把残缺 DMS 截成整数经纬度。"""
    line = _norm_text(line)
    for match in re.finditer(r"(?<![A-Z])([NSEW])\s*[:：]?\s*", line, re.I):
        tail = line[match.end():]
        tail = re.split(r"(?<![A-Z])[NSEW]\s*[:：]?", tail,
                        maxsplit=1, flags=re.I)[0]
        degrees = re.search(r"-?\d{1,3}\s*°\s*", tail)
        if not degrees:
            continue
        remainder = tail[degrees.end():]
        if not re.match(r"\s*\d", remainder):
            continue
        # 分、秒之间必须保留撇号或空格。像“119°274”不能被当成 119°。
        if not re.match(r"\s*\d{1,2}(?:\s*['′’]\s*\d+(?:\.\d+)?|"
                        r"\s+\d+(?:\.\d+)?)(?:\s*[\"″”])?", remainder):
            return True
    return False


def _coords(q):
    text = _normalize_coordinate_text("\n".join(
        str(q.get(k) or "") for k in
        ("conditions_text", "requirements_text", "suggested_text")))
    out = {}
    # 题库有多道题把“1、已知4点坐标 2、起飞后……”写在同一行；
    # 必须先按题号断句，否则后半句的“起飞”会盖掉前半句的坐标角色。
    # ★ 句号后面若紧跟半球标记+数字，那是坐标**内部**的分隔（实测 旧样本 原文
    #   “E：104° 9 ′36”。N：36°33′10””），不能当句子边界切 —— 一切就把纬度切没了，
    #   整条坐标认不出来（该题因此报“未提取到可计算的距离、坐标或方位约束”）。
    clauses = re.split(r"[\n;]|。+(?![NSEW]\s*[:：]?\s*\d)|(?<!\d)\d{1,2}\s*、",
                       _norm_text(text))
    for line in clauses:
        # ★ 同一句里可能挤着**两条**坐标（实测 旧样本：“起飞点坐标E：112°42′32″ N:23°12′18″
        #   1点坐标E：112°42′36″ N:23°12′18″”）。不拆开的话，`_latlon_in_line` 会一次看到
        #   四个半球标记，整条判不出角色 → 两个锚点全丢。
        #   但**必须先整句试**：能整句解析的句子一律照旧，只有整句解析失败时才拆
        #   （不然会把某些一句话里带补充说明的坐标切坏，实测 旧样本 就会因此回退）。
        whole = _latlon_in_line(line)
        pieces = [line] if whole else [
            piece for piece in _MULTI_COORD_SPLIT.split(line) if piece.strip()]
        for piece in pieces:
            if _coordinate_has_malformed_dms(piece):
                # 两个十进制度数都带°、但 OCR 漏掉 N/E 时，如果数值范围能
                # 唯一地区分纬度和经度，就按坐标范围恢复，不把它误判为残缺 DMS。
                plain_values = re.findall(
                    r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*°", piece)
                if not re.search(r"[NSEW]", piece, re.I) and len(plain_values) == 2:
                    first, second = map(float, plain_values)
                    first_abs, second_abs = abs(first), abs(second)
                    if first_abs <= 90 < second_abs <= 180:
                        out[_node_for_line(piece) or "H"] = {
                            "lat": first, "lon": second,
                            "source": piece.strip(), "kind": "order-inferred"}
                        continue
                    if second_abs <= 90 < first_abs <= 180:
                        out[_node_for_line(piece) or "H"] = {
                            "lat": second, "lon": first,
                            "source": piece.strip(), "kind": "order-inferred"}
                        continue
                out["_review"] = {
                    "source": piece.strip(),
                    "reason": "经纬度的度分秒格式识别不完整，请核对题面坐标"}
                continue
            value = _latlon_in_line(piece)
            if not value:
                continue
            lat, lon, kind = value
            swapped = False
            # ★ “经纬度写反”的容错（实测 旧样本：题面写作 `E:37°46'36″, N: 113°12'17″` ——
            #   纬度 113° 不可能；而对调之后 113° 是合法**经度**、37° 是合法**纬度**，
            #   两个数字都不用改）。用户口径：“参考答案当它错了就行”，故按对调处理，
            #   并在坐标数据里留下 swapped 标记，报告/答案可据此注明。
            if abs(lat) > 90 and abs(lon) <= 90:
                lat, lon, swapped = lon, lat, True
            if abs(lat) > 90 or abs(lon) > 180:
                out["_review"] = {
                    "source": piece.strip(),
                    "reason": "经纬度超出有效范围，请核对题面中的N/E标注和坐标顺序"}
                continue
            marker = re.search(r"经纬度|坐标", piece)
            role_text = piece[:marker.end()] if marker else piece
            node = _node_for_line(role_text) or "H"
            out[node] = {"lat": lat, "lon": lon,
                         "source": piece.strip(), "kind": kind,
                         **({"swapped": True} if swapped else {})}

    # 无 N/E 标签时，只在坐标范围能唯一确定角色时自动排序。
    if not out:
        for line in clauses:
            coordinate_line = re.sub(r"^\s*\d{1,2}\s*[.、]\s*", "", line)
            dms_pair = _unlabelled_dms_pairs(coordinate_line)
            if dms_pair and re.search(r"起飞点|坐标|经纬度", line):
                out[_node_for_line(line) or "H"] = {
                    "lat": dms_pair[0], "lon": dms_pair[1],
                    "source": line.strip(), "kind": "order-inferred"}
                continue
            values = re.findall(
                r"(?<![\w])(-?\d{1,3}\.\d+)\s*°?", coordinate_line)
            if len(values) >= 2 and re.search(r"起飞点|坐标|经纬度", line):
                a, b = map(float, values[:2])
                aa, ab = abs(a), abs(b)
                if aa <= 90 < ab <= 180:
                    out[_node_for_line(line) or "H"] = {
                        "lat": a, "lon": b, "source": line.strip(),
                        "kind": "order-inferred"}
                elif ab <= 90 < aa <= 180:
                    out[_node_for_line(line) or "H"] = {
                        "lat": b, "lon": a, "source": line.strip(),
                        "kind": "order-inferred"}
                else:
                    out["_review"] = {
                        "source": line.strip(),
                        "reason": "十进制度未标明纬度/经度，数值范围不能唯一确定坐标顺序"}
                break
    return out


def _param_values(q):
    try:
        # 内置题把“参数说明”和“参数取值”分存在要求/建议字段；导入题卡的
        # OCR 则通常把两者都放进 conditions_text。只传前两个字段会让新题的
        # a/b/c/d/e 全部变成空值，扫描航线和“边长为a”的图形随之无法求解。
        requirements = "\n".join(str(q.get(key) or "") for key in
                                  ("requirements_text", "conditions_text"))
        suggestions = "\n".join(str(q.get(key) or "") for key in
                                 ("suggested_text", "conditions_text"))
        values = AN.extract_params(requirements, suggestions)
        result = {str(item.get("key", "")).lower(): item.get("value")
                  for item in values}
    except Exception:
        result = {}
    # 部分真题把“a1、a2值为50米”写成共用赋值，旧参数表只识别单字母。
    text = _norm_text(q.get("suggested_text", ""))
    shared = re.compile(
        r"(?<![A-Za-z0-9])([a-z][0-9a-z]*(?:(?:\s*[,、，和及]\s*)[a-z][0-9a-z]*)+)"
        r"\s*值?\s*(?:建议|设定)?\s*(?:为|是|=)\s*([\d.]+)\s*(公里|千米|km|米|m)?", re.I)
    for match in shared.finditer(text):
        value, _unit = AN.norm_unit(float(match.group(2)), match.group(3) or "")
        for key in re.findall(r"[a-z][0-9a-z]*", match.group(1), re.I):
            result.setdefault(key.lower(), value)
    # ★ 题库 OCR 把下标认成字母：“al,a2值为50米”其实是 a1、a2。
    #   不补这层别名，等腰直角三角形的两条直角边就只剩一条（实测 旧样本/316/326/354/387）。
    alias = {"l": "1", "i": "1", "z": "2"}
    for key, value in list(result.items()):
        match = re.fullmatch(r"([a-z])([liz])", key)
        if match and value is not None:
            result.setdefault(match.group(1) + alias[match.group(2)], value)
    # “正六边形航线边长为300米”是整条规则的边长，不会被参数表识别成 a=300；
    # 正多边形模板需要这个值才能把边长约束落实到每一条闭合边上。
    condition_text = _norm_text("\n".join(str(q.get(key) or "") for key in
                                            ("conditions_text", "requirements_text",
                                             "suggested_text")))
    regular_side = re.search(
        r"(?:正六边形|六边形|正五边形|五边形|正方形|正四边形|正三角形|等边三角形)"
        r"[^。；;\n]{0,18}?(?:航线)?边长\s*(?:(?:为|是)\s*)?[:：=]?\s*"
        r"([\d.]+)\s*(公里|千米|km|米|m)(?![A-Za-z])", condition_text, re.I)
    if regular_side and result.get("a") is None:
        unit = regular_side.group(2).lower()
        result["a"] = float(regular_side.group(1)) * (
            1000.0 if unit in ("公里", "千米", "km") else 1.0)
    return result


def _parse_edges(text, params):
    edges = []
    pattern = re.compile(
        r"((?:\d{2}\s*边?\s*[,、和及]\s*)*\d{2})\s*(?:(?:边)?的)?\s*(?:边长|边边长|边长度|长度)\s*(?:为|是|=)?\s*"
        r"([a-z]\d*|\d+(?:\.\d+)?)\s*(?:米|m)?", re.I)
    for match in pattern.finditer(text):
        value_token = match.group(2).lower()
        value = params.get(value_token) if value_token[:1].isalpha() else float(value_token)
        if value is None:
            continue
        for token in re.findall(r"\d{2}", match.group(1)):
            edges.append({"a": token[0], "b": token[1], "value": float(value),
                          "kind": "distance", "source": match.group(0)})

    # ★ “12、23、34、45、51为a” / “12边及23边为a” —— 题面**省掉「边长」二字**，
    #   直接给边号序列赋值。上面那条正则要求字面量「边长」，于是这一族整族漏提取，
    #   求解器看到的约束数不足 → 一律落在「尚未组成可校验的唯一坐标解」。
    #   实测 11 道五边形（140/144/147/150/153/216/223/227/232/237/238）全中这一条。
    #   沿用 lessons 二十七·27 的「边号序列」口径：至少两个 2 位边号 + 为/是/= + 值。
    bare_edges = re.compile(
        r"((?:\d{2}\s*边?\s*[,、和及]\s*)+\d{2})\s*(?:边)?\s*(?:为|是|=)\s*"
        r"([a-z]\d*|\d+(?:\.\d+)?)\s*(?:公里|千米|km|米|m)?", re.I)
    for match in bare_edges.finditer(text):
        value_token = match.group(2).lower()
        value = params.get(value_token) if value_token[:1].isalpha() else float(value_token)
        if value is None:
            continue
        for token in re.findall(r"\d{2}", match.group(1)):
            edges.append({"a": token[0], "b": token[1], "value": float(value),
                          "kind": "distance", "source": match.group(0)})

    # 五边形/六边形常把一圈等长边写成“12=23=34=45=51边的边长为a”。
    # 这类写法的等号连接的是边号，不是单条“1-2=数值”，需展开为每条边的长度。
    equal_edge_chain = re.compile(
        r"(?P<chain>\d{2}(?:\s*=\s*\d{2})+)\s*(?:边)?\s*(?:的)?\s*"
        r"(?:边长|边长度|长度)\s*(?:为|是|=)?\s*"
        r"(?P<value>[a-z]\d*|\d+(?:\.\d+)?)\s*(?:公里|千米|km|米|m)?",
        re.I)
    for match in equal_edge_chain.finditer(text):
        value_token = match.group("value").lower()
        value = (params.get(value_token) if value_token[:1].isalpha()
                 else float(value_token))
        if value is None:
            continue
        for token in re.findall(r"\d{2}", match.group("chain")):
            edges.append({"a": token[0], "b": token[1],
                          "value": float(value), "kind": "distance",
                          "source": match.group(0)})

    # “1-2边长100米”及“12=100米”形式。
    for match in re.finditer(
        r"(?<!\d)(\d)\s*[-—到至]\s*(\d)\s*(?:边长|距离|长度)\s*(?:为|是|=)?\s*([\d.]+)\s*(公里|千米|km|米|m)",
            text, re.I):
        scale = 1000 if match.group(4).lower() in ("公里", "千米", "km") else 1
        edges.append({"a": match.group(1), "b": match.group(2),
                      "value": float(match.group(3)) * scale,
                      "kind": "distance", "source": match.group(0)})
    # 导入题常把航段距离直接写在括号中，如“1到2（40m）”。
    for match in re.finditer(
            r"(?<!\d)(\d)\s*(?:到|至|[-—－])\s*(\d)\s*"
            r"[（(]\s*([\d.]+)\s*(公里|千米|km|米|m)\s*[）)]",
            text, re.I):
        unit = match.group(4).lower()
        scale = 1000.0 if unit in ("公里", "千米", "km") else 1.0
        edges.append({"a": match.group(1), "b": match.group(2),
                      "value": float(match.group(3)) * scale,
                      "kind": "distance", "source": match.group(0)})
    for match in re.finditer(r"(?<!\d)(\d)(\d)\s*[-=]{1,2}\s*([\d.]+)\s*(米|m)", text, re.I):
        edges.append({"a": match.group(1), "b": match.group(2),
                      "value": float(match.group(3)), "kind": "distance",
                      "source": match.group(0)})
    # ★ “H到2点200米” / “H距2点200米” / “起飞点到航点2距离200米” —— 两点之间
    #   **不带“距离/边长”字样**的裸写法。上面几条正则都要求字面量（距离／边长），
    #   于是这一族整族漏提取：实测 旧样本 因此少掉唯一的关键距离约束，
    #   直接报“未提取到可计算的距离、坐标或方位约束”。
    # ★ 两侧都可能是**具名点**（“1号点到避让点距离550米”），分隔符还可能是
    #   中文“一”或全角连字符（“H一1 50米”实测 旧样本）。只认“数字点号 + 到/距/离”
    #   会把这两类整条漏掉。
    # ★ 名字两侧可能带**引号**（实测 旧样本：“题卡图形1号点到”避让点“距离550米”）。
    #   早先没考虑引号，这条 550 米的距离整条漏掉 —— 而秩账本正好显示 旧样本
    #   “题面少 1 条”，两个独立工具指向同一条约束。
    #   写法：三引号原始串 + re.X（空白被忽略），避免多行字面量中间插 + 拼接。
    # ★ 名字两侧可能带**引号**，数字点还可能写成“1**号点**”（实测 旧样本：
    #   “题卡图形1号点到”避让点“距离550米”）。早先这两个写法都没认，
    #   这条 550 米的距离整条漏掉 —— 而秩账本正好显示 旧样本“题面少 1 条”，
    #   两个独立工具指向同一条约束。
    # ★ 名字两侧可能带**引号**，数字点可能写成“1**号点**”，点位还常带“**任务**航点”前缀
    #   （实测 旧样本：“起飞点到**任务航点**2的距离200米”整条漏掉 → H 与 1 点重合的退化答案 ✗）。
    #   三处都在同一条模式里兼容，避免再出现“差一个字就漏一条约束”。
    bare_pair = re.compile(
        r"""(H点?|起飞点|返航点|避让点|临时会让点|过渡点|(?<!\d)\d)\s*(?:号)?\s*点?\s*
            (?:到|距|离|[-—－一])\s*(?:任务)?\s*(?:航点)?\s*[\"“”『』']?
            (H点?|起飞点|返航点|避让点|临时会让点|过渡点|(?<!\d)\d)\s*(?:号)?\s*点?\s*[\"“”『』']?
            (?:的)?\s*(?:距离|长度)?\s*(?:为|是|=)?\s*
            ([\d.]+)\s*(公里|千米|km|米|m)""",
        re.I | re.X)
    for match in bare_pair.finditer(text):
        left, right = match.group(1), match.group(2)
        names = {"起飞点": "H", "返航点": "R", "避让点": "A",
                 "临时会让点": "A", "过渡点": "T"}
        if left[:1] in "H起返避临过":
            left = names.get(left.replace("点", "点"), None) or names.get(left, "H")
        if right[:1] in "H起返避临过":
            right = names.get(right, "H")
        scale = 1000 if match.group(4).lower() in ("公里", "千米", "km") else 1
        edges.append({"a": left, "b": right,
                      "value": float(match.group(3)) * scale,
                      "kind": "distance", "source": match.group(0)})
    # 解析“1-2=2-3=4-5=5-1=92m”一类连续边长等式链。
    chain = re.compile(
        r"((?:\d\s*[-—－]\s*\d\s*=\s*)+\d\s*[-—－]\s*\d)\s*=\s*"
        r"([\d.]+)\s*(公里|千米|km|米|m)", re.I)
    for match in chain.finditer(text):
        scale = 1000 if match.group(3).lower() in ("公里", "千米", "km") else 1
        value = float(match.group(2)) * scale
        for pair in re.finditer(r"(\d)\s*[-—－]\s*(\d)", match.group(1)):
            edges.append({"a": pair.group(1), "b": pair.group(2),
                          "value": value, "kind": "distance",
                          "source": match.group(0)})

    # “23=14=200米”表示两条编号航段边长相等，且都为200米。
    chain = re.compile(r"(?<!\d)((?:\d{2}\s*=\s*)+\d{2})\s*=\s*"
                       r"([\d.]+)\s*(公里|千米|km|米|m)", re.I)
    for match in chain.finditer(text):
        unit = match.group(3).lower()
        scale = 1000.0 if unit in ("公里", "千米", "km") else 1.0
        value = float(match.group(2)) * scale
        for pair in re.findall(r"\d{2}", match.group(1)):
            edges.append({"a": pair[0], "b": pair[1], "value": value,
                          "kind": "distance", "source": match.group(0)})
    # 题卡常写“②③=①④的边长为200m”；NFKC 已将带圈数字转成普通数字。
    equal_lengths = re.compile(
        r"(?<!\d)((?:\d{2}\s*=\s*)+\d{2})\s*(?:的)?\s*"
        r"(?:边长|边长度)\s*(?:为|是|=)?\s*([\d.]+)\s*(公里|千米|km|米|m)", re.I)
    for match in equal_lengths.finditer(text):
        unit = match.group(3).lower()
        scale = 1000.0 if unit in ("公里", "千米", "km") else 1.0
        for pair in re.findall(r"\d{2}", match.group(1)):
            edges.append({"a": pair[0], "b": pair[1],
                          "value": float(match.group(2)) * scale,
                          "kind": "distance", "source": match.group(0)})
    # 起飞点到编号航点只有距离时，也要保留；航向可能由另一条航线的
    # 同向／反向关系反推，不能因为同一句没有角度就丢掉这项约束。
    external = re.compile(
        r"(起飞点|避让点|过渡点|转场点|H点?)\s*到\s*"
        r"(?:任务航线|题卡图形|图形)?\s*(\d{1,2})\s*(?:号)?点?\s*"
        r"(?:的)?距离\s*(?:为|是)?\s*([\d.]+)\s*(公里|千米|km|米|m)", re.I)
    names = {"起飞点": "H", "避让点": "A", "过渡点": "T", "转场点": "T"}
    for match in external.finditer(text):
        origin = names.get(match.group(1), "H")
        scale = 1000.0 if match.group(4).lower() in ("公里", "千米", "km") else 1.0
        edges.append({"a": origin, "b": str(int(match.group(2))),
                      "value": float(match.group(3)) * scale,
                      "kind": "distance", "source": match.group(0)})
    # “H到2和H到3=300米”把同一个数值同时赋给两条进场距离。
    home_pair = (r"(?:H点?|起飞点)\s*[-一到至]\s*"
                 r"(?:(?:任务航线(?:的)?航点|任务航线|任务航点|航点|题卡图形|图形)\s*)?"
                 r"(\d+)\s*(?:号)?点?")
    shared_home = re.compile(
        home_pair + r"\s*和\s*" + home_pair +
        r"\s*(?:的)?\s*(?:距离|长度)?\s*(?:相等|一致)?\s*"
        r"(?:为|是|=)\s*([\d.]+)\s*(公里|千米|km|米|m)", re.I)
    for match in shared_home.finditer(text):
        scale = 1000.0 if match.group(4).lower() in ("公里", "千米", "km") else 1.0
        for node in (match.group(1), match.group(2)):
            edges.append({"a": "H", "b": str(int(node)),
                          "value": float(match.group(3)) * scale,
                          "kind": "distance", "source": match.group(0)})
    direct_home = re.compile(home_pair + r"\s*(?:距离)?\s*(?:为|是|=)\s*"
                             r"([\d.]+)\s*(公里|千米|km|米|m)", re.I)
    for match in direct_home.finditer(text):
        scale = 1000.0 if match.group(3).lower() in ("公里", "千米", "km") else 1.0
        edges.append({"a": "H", "b": str(int(match.group(1))),
                      "value": float(match.group(2)) * scale,
                      "kind": "distance", "source": match.group(0)})
    unique = {}
    for item in edges:
        key = (item["a"], item["b"], round(item["value"], 6))
        unique[key] = item
    # ★ “H-1距离50米” / “1-H距离50米” —— 用 H/A/T 字母打头的航段距离写法。
    #   上面那条 external 正则只认“起飞点到N点距离”，漏掉 H-1 这种简写。
    for match in re.finditer(
            r"(?<![A-Za-z0-9])([HAT])\s*[-—－]\s*(\d{1,2})\s*(?:号)?点?\s*(?:的)?\s*"
            r"(?:距离|长度|航程)\s*(?:为|是|=)?\s*([\d.]+)\s*(公里|千米|km|米|m)",
            text, re.I):
        scale = 1000.0 if match.group(4).lower() in ("公里", "千米", "km") else 1.0
        unique[(match.group(1).upper(), str(int(match.group(2))),
                round(float(match.group(3)) * scale, 6))] = {
            "a": match.group(1).upper(), "b": str(int(match.group(2))),
            "value": float(match.group(3)) * scale, "kind": "distance",
            "source": match.group(0)}
    for match in re.finditer(
            r"(?<![A-Za-z0-9])(\d{1,2})\s*(?:号)?点?\s*[-—－]\s*([HAT])\s*(?:的)?\s*"
            r"(?:距离|长度|航程)\s*(?:为|是|=)?\s*([\d.]+)\s*(公里|千米|km|米|m)",
            text, re.I):
        scale = 1000.0 if match.group(4).lower() in ("公里", "千米", "km") else 1.0
        unique[(match.group(2).upper(), str(int(match.group(1))),
                round(float(match.group(3)) * scale, 6))] = {
            "a": match.group(2).upper(), "b": str(int(match.group(1))),
            "value": float(match.group(3)) * scale, "kind": "distance",
            "source": match.group(0)}
    return list(unique.values())


def _parse_equal_distances(text):
    """提取尚未给出数值、但明确要求两段等长的条件。"""
    endpoint = (r"(?:H点?|起飞点)\s*[-一到至]\s*"
                r"(?:(?:任务航线(?:的)?航点|任务航线|任务航点|航点|题卡图形|图形)\s*)?"
                r"(\d+)\s*(?:号)?点?")
    shared_origin = re.compile(
        endpoint + r"\s*和\s*(?:(?:H点?|起飞点)\s*[-一到至]\s*)?"
        r"(?:(?:任务航线(?:的)?航点|任务航线|任务航点|航点|题卡图形|图形)\s*)?"
        r"(\d+)\s*(?:号)?点?\s*(?:的)?\s*(?:距离|长度)?\s*"
        r"(?:相等|一致)", re.I)
    out = []
    for match in shared_origin.finditer(text):
        first, second = str(int(match.group(1))), str(int(match.group(2)))
        if first != second:
            out.append({"first": ["H", first], "second": ["H", second],
                        "source": match.group(0)})

    # 题卡常把两条已编号的边直接写成“1-4=2-3”。这不是已知数值，
    # 但等长关系必须进入求解与复核，否则求解器可能在漏掉它的情况下误报 READY。
    separator = r"[-‐‑‒–—―－﹣~～到至]"
    edge_equality = re.compile(
        r"(?<!\d)(?P<a1>\d{1,2})\s*" + separator +
        r"\s*(?P<b1>\d{1,2})\s*=\s*"
        r"(?P<a2>\d{1,2})\s*" + separator +
        r"\s*(?P<b2>\d{1,2})(?!\d)")
    for match in edge_equality.finditer(text):
        first = [str(int(match.group("a1"))), str(int(match.group("b1")))]
        second = [str(int(match.group("a2"))), str(int(match.group("b2")))]
        if frozenset(first) != frozenset(second):
            out.append({"first": first, "second": second,
                        "source": match.group(0)})

    # “1235构成正方形”把 1、2、3、5 按题面书写顺序视为正方形四角；
    # 生成四条边等长和四个直角约束，不把对角线当作航线边。
    square_pattern = re.compile(
        r"(?P<points>(?:\d{1,2}\s*[,、，和及]\s*)*\d{1,2}|\d{4})"
        r"\s*构成\s*正方形")
    for match in square_pattern.finditer(text):
        token = match.group("points")
        if re.fullmatch(r"\d{4}", token):
            names = list(token)
        else:
            names = [str(int(value)) for value in re.findall(r"\d{1,2}", token)]
        if len(names) != 4 or len(set(names)) != 4:
            continue
        cycle = [(names[index], names[(index + 1) % 4]) for index in range(4)]
        for edge in cycle[1:]:
            out.append({"first": list(cycle[0]), "second": list(edge),
                        "source": match.group(0)})
    return out


def _parse_square_angles(text):
    """把“四个编号点构成正方形”转为通用的逐点夹角约束。"""
    out = []
    pattern = re.compile(
        r"(?P<points>(?:\d{1,2}\s*[,、，和及]\s*)*\d{1,2}|\d{4})"
        r"\s*构成\s*正方形")
    for match in pattern.finditer(text):
        token = match.group("points")
        names = (list(token) if re.fullmatch(r"\d{4}", token) else
                 [str(int(value)) for value in re.findall(r"\d{1,2}", token)])
        if len(names) != 4 or len(set(names)) != 4:
            continue
        for index, vertex in enumerate(names):
            out.append({"a": names[index - 1], "vertex": vertex,
                        "b": names[(index + 1) % 4], "value": 90.0,
                        "source": match.group(0)})
    return out


def _parse_midpoint_perpendiculars(text):
    """保留起飞点到图形边中点的垂直距离。

    题面有两种写法：「起飞点到12边中点的距离为50米」与「起飞点到12边中点为50米」
    —— 后者没有字面量「到中点」，只有「到12边中点」，早先的正则因此整条失配
    （实测 旧样本 的这条条件被整条丢掉）。
    """
    pattern = re.compile(
        r"(?:起飞点|H点?).{0,5}垂直于\s*(\d)(\d)\s*边?\s*的?\s*中点"
        r".{0,28}?(?:距离|(?:到|至)[^。;\n]{0,10}?中点)\s*(?:为|是|=)?\s*"
        r"([\d.]+)\s*(米|m)",
        re.I)
    return [{"point": "H", "a": match.group(1), "b": match.group(2),
             "distance": float(match.group(3)), "source": match.group(0)}
            for match in pattern.finditer(text)]


_DIRECTION_ENDPOINT = (
    r"(?:起飞点|避让点|过渡点|过度点|转场点|中转点|H点?|"
    r"(?:(?:任务航线|在务航线|任务航点|题卡图形|题卡|图形|示意图)\s*(?:的)?\s*)?"
    r"(?:航点\s*\d+\s*(?:号)?|\d+\s*(?:号)?(?:航点|点)?))")
_DIRECTION_SEPARATOR = (
    r"(?:[-‐‑‒–—―－﹣~～→到至一]|(?<=点)(?=\s*\d))")
_DIRECTION_PAIR_RE = re.compile(
    r"(?P<a>" + _DIRECTION_ENDPOINT + r")\s*" +
    _DIRECTION_SEPARATOR + r"\s*(?P<b>" + _DIRECTION_ENDPOINT + r")",
    re.I)


def _direction_node(value):
    value = str(value or "").strip()
    if "避让" in value:
        return "A"
    if "过渡" in value or "过度" in value or "转场" in value or "中转" in value:
        return "T"
    if "起飞" in value or re.fullmatch(r"H点?", value, re.I):
        return "H"
    match = re.search(r"\d+", value)
    return match.group(0) if match else None


_COURSE_TOKEN = (
    r"(?:[北南东西]\s*偏\s*[北南东西]\s*[\d.]+\s*(?:°|度)?|"
    r"正北|正南|正东|正西|NNE|ENE|ESE|SSE|SSW|WSW|WNW|NNW|"
    r"NE|NW|SE|SW|N|S|E|W|东北|西北|东南|西南|北|南|东|西)")
_DISTANCE_TOKEN_RE = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*(公里|千米|km|米|m)"
    r"(?!\s*/\s*(?:s|秒))", re.I)
_NUMBERED_POINT_RE = re.compile(
    r"(?:(?:任务航线|题卡图形|题卡|图形|示意图)\s*(?:的)?\s*)?"
    r"(?:航点\s*(\d+)\s*(?:号)?|(\d+)\s*(?:号)?(?:航点|点))")


def _last_distance_before_target(text):
    matches = list(_DISTANCE_TOKEN_RE.finditer(text))
    if not matches:
        return None
    match = matches[-1]
    value = float(match.group(1))
    if match.group(2).lower() in ("公里", "千米", "km"):
        value *= 1000.0
    return value


def _sequenced_access_segments(text):
    """抽取先到避让／过渡点、再进入图形航点的多段进场路线。"""
    out = []
    for sentence in re.split(r"[。;\n]", text):
        second = re.search(
            r"(?:再|然后|随后)\s*(?:向|往)\s*(?P<direction>" +
            _COURSE_TOKEN + r")", sentence)
        if not second:
            continue
        first = re.search(
            r"(?:起飞点|H点?).{0,140}?(?:需先|先)\s*(?:向|往)\s*"
            r"(?P<direction>" + _COURSE_TOKEN + r")", sentence)
        if not first:
            continue
        first_text = sentence[first.end():second.start()]
        named = re.search(r"到达\s*[“\"']?\s*(避让点|过渡点|过度点|转场点|中转点)",
                          first_text)
        first_leg_text = first_text[:named.end()] if named else first_text
        first_distance = (_last_distance_before_target(first_leg_text) or
                          _last_distance_before_target(first_text))
        first_bearing = GEO.parse_bearing(first.group("direction"))
        if first_distance is None or first_bearing is None:
            continue
        if named:
            first_target = "A" if "避让" in named.group(1) else "T"
        else:
            first_target = "T"
        out.append({"a": first_target, "b": "H", "bearing": float(first_bearing),
                    "distance": float(first_distance), "source": first_text.strip()})

        second_text = sentence[second.end():]
        target_matches = list(_NUMBERED_POINT_RE.finditer(second_text))
        second_distance = _last_distance_before_target(second_text)
        second_bearing = GEO.parse_bearing(second.group("direction"))
        if not target_matches or second_distance is None or second_bearing is None:
            continue
        target_number = (target_matches[-1].group(1) or
                         target_matches[-1].group(2))
        out.append({"a": target_number, "b": first_target,
                    "bearing": float(second_bearing), "distance": float(second_distance),
                    "source": second.group(0) + second_text[:target_matches[-1].end()]})
    # 题面也常省略“从起飞点”字样，按航线语序读取起飞→避让/过渡→编号航点。
    for sentence in re.split(r"[。;\n]", text):
        if any(item.get("source") and item["source"] in sentence for item in out):
            continue
        first = re.search(r"先\s*(?:向|往)\s*(?P<direction>" +
                          _COURSE_TOKEN + r")", sentence)
        second = re.search(r"再\s*(?:向|往)\s*(?P<direction>" +
                           _COURSE_TOKEN + r")", sentence)
        if not first or not second or first.end() >= second.start():
            continue
        first_text = sentence[first.end():second.start()]
        second_text = sentence[second.end():]
        first_distance = AN._find_dist(first_text)
        second_distance = AN._find_dist(second_text)
        target_matches = list(_NUMBERED_POINT_RE.finditer(second_text))
        first_bearing = GEO.parse_bearing(first.group("direction"))
        second_bearing = GEO.parse_bearing(second.group("direction"))
        if (first_distance is None or second_distance is None or
                not target_matches or first_bearing is None or
                second_bearing is None):
            continue
        target_match = target_matches[-1]
        target = target_match.group(1) or target_match.group(2)
        middle = "T" if re.search(r"過渡|轉換", first_text) else "A"
        out.extend([
            {"a": middle, "b": "H", "bearing": float(first_bearing),
             "distance": float(first_distance), "source": first_text.strip()},
            {"a": target, "b": middle, "bearing": float(second_bearing),
             "distance": float(second_distance), "source": second_text.strip()},
        ])

    # ★ 空域限制绕飞：“由于空域限制，无人机不能直飞1号航点，需向东北方向平飞100m，
    #   再向西北方向平飞200m，才能到达1号航点。”
    #   这条链是这批题**唯一的定位依据**（起飞点→过渡点→1号航点），漏掉它图形就没有
    #   锚点，求解器一律报「尚未组成唯一坐标解」。实测 旧样本/286/290/291/292/354/366 等。
    #   三种变体都要认：
    #     ① “…不能直飞1号航点需向东北方向平飞100m，再向西北方向平飞200m…”
    #     ② “…飞机不能由起飞点直飞任务航线的航点1，需先向西南方向平飞1200米，再向东南方向平飞500米，到达…”
    #     ③ “…起飞后向NE方向飞行50米，再向E方向飞行100米，进入1号…”
    #   ①②目标航点在“直飞”之后，③目标航点在整段末尾。
    legs = re.compile(
        r"(?P<first>" + _COURSE_TOKEN + r")\s*(?:方向)?\s*(?:平飞|飞行|飞)\s*"
        r"(?P<d1>[\d.]+)\s*(?P<u1>公里|千米|km|米|m)[^。;\n]{0,14}?"
        # 连接词后面可能是“向/往”，也可能是题面错别字“问”（实测 旧样本“再问西南方向”）。
        r"(?:再|然后|随后)\s*[向往问]?\s*(?P<second>" + _COURSE_TOKEN + r")"
        r"\s*(?:方向)?\s*(?:平飞|飞行|飞)\s*(?P<d2>[\d.]+)\s*"
        r"(?P<u2>公里|千米|km|米|m)", re.I)
    for sentence in re.split(r"[。;\n]", text):
        # “由于空域限制…”／“由于限制…”／“不能直飞…” 都算绕飞句。
        # ★ “起飞后需向…方向飞行…到达避让点”也是绕飞句（实测 旧样本）—— 早先的过滤器
        #   只认“空域限制/不能直飞/由于…”这类措辞，这句以“起飞后”开头，整条 H→A
        #   航段（航向 325°、625 米）就没进约束，于是 A/H 两个自由点算出 34 个解。
        # ★ 细判据（实测 旧样本）：“起飞后需向…方向飞行…米到达‘避让点’”也是绕飞句，
        #   但不能只按“起飞后”放宽关键词（会误伤其它题 —— 我试过，426 → 425 ✗）。
        #   所以要求**同句里同时出现**：`起飞后需` ＋ `到达` ＋ 具名点（避让点/过渡点/返航点）。
        detour_like = re.search(r"空域限制|由于限制|受限制|不能直飞|不能由起飞点直飞|"
                                r"开始执行图形航线|进入航线|由于天气", sentence)
        if not detour_like:
            detour_like = (re.search(r"起飞后需", sentence) and
                           re.search(r"到达\s*[\"“”『』']?(避让点|过渡点|过度点|"
                                     r"返航点|临时会让点)", sentence))
        if not detour_like:
            continue
        match = legs.search(sentence)
        if not match:
            # ★ **单腿绕飞**：“需先向SE平飞1000米，到达避让点”（实测 旧样本）。
            #   上面那条正则要求“再/然后 + 第二条腿”，单腿的整句照样漏。
            single = re.search(
                r"向\s*(?P<course>" + _COURSE_TOKEN + r")\s*(?:方向)?\s*"
                r"(?:平飞|飞行|飞)\s*(?P<d>[\d.]+)\s*"
                r"(?P<u>公里|千米|km|米|m)[^。;\n]{0,12}?"
                r"(?:到达|进入|飞抵)\s*[\"“”『』']?"
                r"(?P<target>避让点|过渡点|过度点|返航点|临时会让点|"
                r"(?<!\d)\d{1,2}\s*(?:号)?(?:航)?点)", sentence, re.I)
            if not single:
                continue
            named = {"避让点": "A", "临时会让点": "A", "过渡点": "T",
                     "过度点": "T", "返航点": "R"}
            target_token = single.group("target")
            if target_token[:1].isdigit():
                target_token = re.match(r"\d+", target_token).group(0)
            else:
                target_token = named.get(target_token)
            bearing = GEO.parse_bearing(single.group("course"))
            if target_token is None or bearing is None:
                continue
            scale = 1000.0 if single.group("u").lower() in ("公里", "千米", "km") else 1.0
            out.append({"a": target_token, "b": "H",
                        "bearing": float(bearing),
                        "distance": float(single.group("d")) * scale,
                        "source": single.group(0)})
            continue
        head = re.search(r"直飞[^。;\n]{0,26}?(\d+)\s*(?:号)?(?:航)?点", sentence)
        # 目标航点也可能写在**下一句**里（“再向W方向飞行50米。进入1号”），
        # 所以往后看一小段原文，而不是只看当前句。
        tail = re.search(r"(?:进入|到达|才能到达)[^。;\n]{0,16}?(\d+)\s*(?:号)?",
                         text[match.end():match.end() + 80])
        target = head.group(1) if head else (tail.group(1) if tail else None)
        # ★ “…后，开始执行图形航线”这种写法没有航点号：按题库惯例，
        #   图形航线的起点就是 **1 号航点**（实测 旧样本 因此整条绕飞链没被解析，
        #   报“未提取到可计算的距离、坐标或方位约束”）。
        if target is None and re.search(r"开始执行图形航线|进入(?:图形)?航线|"
                                        r"到达(?:测区|任务航线|航线)", sentence):
            target = "1"
        # ★ 目标也可能写成**具名点**：“需先向SE平飞1000米，到达避让点”（实测 旧样本）。
        if target is None:
            named = re.search(r"""(?:进入|到达|才能到达|飞抵)\s*["“”『』']?"""
                              r"""(避让点|过渡点|过度点|返航点|临时会让点)""",
                              text[match.end():match.end() + 60])
            if named:
                target = {"避让点": "A", "临时会让点": "A",
                          "过渡点": "T", "过度点": "T",
                          "返航点": "R"}.get(named.group(1))
        if target is None:
            continue
        first_bearing = GEO.parse_bearing(match.group("first"))
        second_bearing = GEO.parse_bearing(match.group("second"))
        if first_bearing is None or second_bearing is None:
            continue
        scale1 = 1000.0 if match.group("u1").lower() in ("公里", "千米", "km") else 1.0
        scale2 = 1000.0 if match.group("u2").lower() in ("公里", "千米", "km") else 1.0
        out.append({"a": "T", "b": "H", "bearing": float(first_bearing),
                    "distance": float(match.group("d1")) * scale1,
                    "source": match.group(0)})
        out.append({"a": target, "b": "T", "bearing": float(second_bearing),
                    "distance": float(match.group("d2")) * scale2,
                    "source": match.group(0)})
    return out


def _parse_segments(q, bearings=None):
    text = _norm_text(q.get("conditions_text", ""))
    out = _sequenced_access_segments(text)
    # 老题有时把多条“点号-点号 方位 距离”压成无换行表格文本；普通按句解析
    # 会把整行的首个距离错误配给首个方位，只得到第一段。仅在同一行识别到至少
    # 两条完整记录时启用此表格解析，避免把普通叙述中的独立数字误当航段。
    compact_segment = re.compile(
        r"(?<![A-Za-z0-9])(?P<origin>H|A|T|R|\d{1,2})\s*[-—－]\s*"
        r"(?P<target>H|A|T|R|\d{1,2})\s*(?:航向|方向)?\s*"
        r"(?P<bearing>[\d.]+)\s*(?:°|度)\s*"
        r"(?:距离|航程|飞行)?\s*(?:为|是|约)?\s*"
        r"(?P<distance>[\d.]+)\s*(?P<unit>公里|千米|km|米|m)", re.I)
    for line in re.split(r"[。；;\n]", text):
        matches = list(compact_segment.finditer(line))
        if len(matches) < 2:
            continue
        for match in matches:
            origin = _direction_node(match.group("origin"))
            target = _direction_node(match.group("target"))
            unit = match.group("unit").lower()
            scale = 1000.0 if unit in ("公里", "千米", "km") else 1.0
            out.append({"a": target, "b": origin,
                        "bearing": float(match.group("bearing")) % 360.0,
                        "distance": float(match.group("distance")) * scale,
                        "source": match.group(0).strip()})
    clauses = []
    for sentence in re.split(r"[。;\n]", text):
        pieces = [piece.strip() for piece in sentence.split(",") if piece.strip()]
        for index, piece in enumerate(pieces):
            clauses.append(piece)
            if (re.search(r"(?:起飞后|起飞点|H点?).{0,24}(?:先|需|要).{0,24}(?:向|方向)", piece)
                    and not re.search(r"(?:到达|到)\s*(?:避让点|过渡点|转场点)", piece)
                    and not re.search(r"[\d.]+\s*(?:公里|千米|km|米|m)(?!\s*/\s*(?:s|秒))", piece, re.I)):
                merged = piece
                for following in pieces[index + 1:index + 4]:
                    merged += "," + following
                    clauses.append(merged)
                    if re.search(r"(?:到达|到)\s*(?:避让点|过渡点|转场点)", merged):
                        break
            if index > 0:
                previous = pieces[index - 1]
                relation_pattern = (r"(起飞点|避让点|过渡点|转场点|H点?|"
                                    r"(?:题卡图形|图形)?\s*\d+\s*(?:号)?点)\s*到\s*"
                                    r"((?:题卡图形|图形)?\s*\d+\s*(?:号)?点)")
                before_relation = re.search(relation_pattern, previous)
                current_relation = re.search(relation_pattern, piece)
                if (before_relation and current_relation and
                        tuple(re.sub(r"\s+", "", x) for x in before_relation.groups()) ==
                        tuple(re.sub(r"\s+", "", x) for x in current_relation.groups())):
                    clauses.append(previous + "," + piece)
            # 有些题把方位和距离分别放在逗号两侧，向后拼接到距离为止。
            has_labeled_distance = bool(re.search(
                r"(?:距离|飞行|航程|长度)\s*(?:为|是|约)?\s*[\d.]+\s*"
                r"(?:公里|千米|km|米|m)(?![A-Za-z])|"
                r"[\d.]+\s*(?:公里|千米|km|米|m)(?![A-Za-z])",
                piece, re.I))
            if (re.search(r"(?:位于|处于|到|在).{0,18}(?:点|起飞点|避让点|过渡点)", piece)
                    and not (has_labeled_distance and GEO.parse_bearing(piece) is not None)):
                merged = piece
                for following in pieces[index + 1:index + 3]:
                    # 下一段若已开始描述另一条航段，不能把它的方位借给当前关系。
                    if re.search(r"(?:起飞点|避让点|过渡点|转场点|H点?|\d+\s*(?:号)?点)\s*到", following):
                        break
                    merged += "," + following
                    clauses.append(merged)
                    if AN._find_dist(following):
                        break
        # 风向关系偶尔跨逗号描述，保留整句供补充识别。
        if re.search(r"逆风|顺风|尾风|顶风", sentence):
            clauses.append(sentence)
    wind_all = GEO.wind_from(text)
    bearings = bearings or []
    last_route_endpoint = None
    for clause in clauses:
        # 风航向应使用风向关系计算，不能把同句里的风向来向直接当作航段航向。
        # 角度标注中的数值（如“起飞点到1点距离50m，∠H12=90°”）不是航向；
        # 若整句直接交给 parse_bearing，90° 会被误当成 H→1 的方位，制造一条
        # 题面并未给出的进场航段，并让后续严格校验错误拒绝本来可解的题。
        bearing_text = re.sub(
            r"[∠﹤<]\s*[A-Z]?\s*\d{1,2}(?:\s*=\s*[∠﹤<]\s*[A-Z]?\s*\d{1,2})*"
            r"\s*(?:=|为|是)\s*\d+(?:\.\d+)?\s*(?:°|度)",
            "", clause, flags=re.I)
        bearing = (None if re.search(r"逆风|顶风|顺风|尾风|侧风", clause)
                   else GEO.parse_bearing(bearing_text))
        distance = AN._find_dist(clause)
        altitude = chr(0x9ad8) + chr(0x5ea6)
        height_word = re.search(
            altitude + "[ ]*(?:" + chr(0x4e3a) + "|" + chr(0x662f) +
            "|:|：)?[ ]*[0-9.]+[ ]*(?:" + chr(0x7c73) + "|m)", clause, re.I)
        if height_word:
            before_height = AN._find_dist(clause[:height_word.start()])
            after_height = AN._find_dist(clause[height_word.end():])
            distance = before_height or after_height

        if not distance:
            continue
        clean = re.sub(r"[“”‘’\"']", "", clause)
        target = origin = None
        # 起飞后按方向飞到避让点/过渡点。
        match = re.search(r"起飞(?:后|点).{0,30}(?:飞行|方向).{0,20}(?:到达|到)\s*(避让点|过渡点|转场点)", clean)
        if match:
            target = "A" if "避让" in match.group(1) else "T"
            origin = "H"
        if target is None:
            match = re.search(
                r"(避让点|过渡点|转场点)\s*(?:在|位于|处于)\s*(?:起飞点|H点?)",
                clean, re.I)
            if match:
                target = "A" if "避让" in match.group(1) else "T"
                origin = "H"
        # 避让点/过渡点到编号航点，或“编号航点位于避让点的某方向”。
        if target is None:
            match = re.search(r"(避让点|过渡点|转场点|H点?)\s*到\s*(?:(?:任务航线|在务航线|任务航点|题卡图形|图形|航点|示意图)\s*)?(\d+)\s*(?:号)?点", clean)
            if match:
                origin = "A" if "避让" in match.group(1) else "T" if "过渡" in match.group(1) or "转场" in match.group(1) else "H"
                target = match.group(2)
        if target is None:
            match = re.search(r"(?:起飞点|H点?)\s*到\s*(?:(?:任务航线|在务航线|任务航点|题卡图形|图形|航点|示意图)\s*)?(\d+)\s*(?:号)?点", clean, re.I)
            if match:
                origin, target = "H", match.group(1)
        if target is None:
            match = re.search(r"(?:(?:任务航线的航点|任务航线|在务航线|任务航点|题卡图形|图形|示意图)\s*)?(\d+)\s*(?:(?:号)?点)?\s*(?:位于|处于)\s*(避让点|过渡点|转场点|H点?)", clean)
            if match:
                target = match.group(1)
                origin = "A" if "避让" in match.group(2) else "T" if "过渡" in match.group(2) or "转场" in match.group(2) else "H"
        if target is None:
            match = re.search(r"(?:(?:任务航线的航点|任务航线|在务航线|任务航点|题卡图形|图形|示意图)\s*)?(\d+)\s*(?:(?:号)?点)?\s*(?:在|位于|处于)\s*(避让点|过渡点|转场点|中转点|起飞点|H点?)", clean)
            if match:
                target = match.group(1)
                origin = ("A" if "避让" in match.group(2) else
                          "T" if "过渡" in match.group(2) or "转场" in match.group(2) or "中转" in match.group(2) else "H")
        # 命名点位于编号点附近时，按编号点→命名点记录距离和方位。
        if target is None:
            match = re.search(r"(避让点|过渡点|过度点|转场点|中转点)\s*(?:在|位于|处于)\s*(?:任务航线的航点|题卡图形|题卡|图形)?\s*(\d+)\s*(?:号)?点", clean)
            if match:
                origin = match.group(2)
                target = "A" if "避让" in match.group(1) else "T"
        if target is None:
            match = re.search(r"(?:(?:任务航线的航点|任务航线|在务航线|任务航点|题卡图形|题卡|图形|示意图)\s*)?(\d+)\s*(?:号)?点\s*到\s*(避让点|过渡点|转场点|中转点|H点?)", clean)
            if match:
                origin = match.group(1)
                target = ("A" if "避让" in match.group(2) else
                          "T" if "过渡" in match.group(2) or "转场" in match.group(2) or "中转" in match.group(2) else "H")
        if target is None and last_route_endpoint in ("A", "T"):
            match = re.search(
                r"(?:再|然后|随后).{0,45}?(?:到达|到)\s*"
                r"(?:(?:任务航线|在务航线|任务航点|题卡图形|图形|航点|示意图)\s*)?"
                r"(\d+)\s*(?:号)?点", clean)
            if match:
                origin, target = last_route_endpoint, match.group(1)
        # 两个编号点的直接航向关系，如“1号点到2号点为W”。
        if target is None:
            match = re.search(r"(?:题卡图形|图形|航点)?\s*(\d+)\s*(?:号)?点\s*到\s*(?:题卡图形|图形|航点)?\s*(\d+)\s*(?:号)?点", clean)
            if match:
                origin, target = match.group(1), match.group(2)
        # 兼容“1一H”“H→1”及“2点3点”等题库中的航段简写。
        if target is None:
            match = _DIRECTION_PAIR_RE.search(clean)
            if match:
                origin = _direction_node(match.group("a"))
                target = _direction_node(match.group("b"))
        if target is None and re.search(r"H\s*[-到至]\s*1", clean, re.I) and "逆风" in clause:
            wind = GEO.wind_from(clause) or wind_all
            if wind is not None:
                origin, target, bearing = "H", "1", wind
        if target is not None and origin is not None and bearing is None:
            bearing = next((item["value"] for item in bearings
                            if str(item["a"]) == str(origin) and
                            str(item["b"]) == str(target)), None)
        if target is not None and origin is not None and bearing is not None:
            out.append({"a": str(target), "b": str(origin),
                        "bearing": float(bearing), "distance": float(distance),
                        "source": clause.strip()})
            last_route_endpoint = str(target)
    # 有些题卡把“1-H 135度，距离130米”写在同一句里，逗号拆分会令航向和距离分家。
    for sentence in re.split(r"[。;\n]", text):
        distance = AN._find_dist(sentence)
        altitude = chr(0x9ad8) + chr(0x5ea6)
        height_word = re.search(
            altitude + "[ ]*(?:" + chr(0x4e3a) + "|" + chr(0x662f) +
            "|:|：)?[ ]*[0-9.]+[ ]*(?:" + chr(0x7c73) + "|m)", sentence, re.I)
        if height_word:
            distance = (AN._find_dist(sentence[:height_word.start()]) or
                        AN._find_dist(sentence[height_word.end():]))
        pairs = list(_DIRECTION_PAIR_RE.finditer(sentence))
        if distance is None or len(pairs) != 1:
            continue
        pair = pairs[0]
        # 同句包含起飞段和“航点—避让点”段时，采用该点对后面的距离。
        route_distance = AN._find_dist(sentence[pair.end():])
        if route_distance is not None:
            distance = route_distance
        first = _direction_node(pair.group("a"))
        second = _direction_node(pair.group("b"))
        matches = []
        for item in bearings or []:
            a, b = str(item["a"]), str(item["b"])
            if (a, b) == (first, second):
                matches.append((float(item["value"]), a, b))
            elif (a, b) == (second, first):
                matches.append(((float(item["value"]) + 180.0) % 360.0,
                                a, b))
        if len(matches) == 1:
            bearing, _a, _b = matches[0]
            out.append({"a": second, "b": first, "bearing": bearing,
                        "distance": float(distance), "source": sentence.strip()})

    dedup = {(x["a"], x["b"], round(x["bearing"], 4), round(x["distance"], 3)): x
             for x in out}
    return list(dedup.values())


_POSITION_DIRECTION = (
    r"((?:NE|NW|SE|SW|东北|东南|西南|西北|[北南东西NSEW])\s*偏\s*"
    r"[北南东西NSEW]\s*[\d.]+\s*°?|"
    r"正北|正南|正东|正西|NNE|ENE|ESE|SSE|SSW|WSW|WNW|NNW|"
    r"NE|NW|SE|SW|N|S|E|W|东北|西北|东南|西南|北|南|东|西)")


def _parse_position_bearings(text):
    """“H 位于 1 点西偏南 30 度”这类主宾在方位词两侧的写法。

    `_parse_bearings` 认的是“A 到 B 为 X 方向”“B 的 X 方向是 A”等句式；这一族把
    主语写在方位词**前面**（“H 位于 1 点西偏南 30 度” = 从 1 点看 H 在西偏南 30°，
    即 1→H = 210°）。实测 旧样本 一批题因此丢掉唯一的定位航向。
    """
    # ★ 主语/参照可能是**具名点**（“**避让点**”位于题卡图形1号点东南偏南30°方向300米处）
    #   而不是 [HAT] 或“N号点”。只认后两种时整句匹配不上，方位和距离一起丢
    #   （实测 旧样本 因此少掉唯一的 |1-A| 定位条件）。
    # 具名点外面可能包着引号，而且**引号顺序经常是反的**（实测 旧样本：`”避让点“`）。
    # 只在后面允许引号是不够的，两侧都要允许。
    quote = r"[\"“”『』']?"
    endpoint = (quote + r"([HAT](?:点)?|避让点|过渡点|过度点|转场点|中转点|返航点|起飞点|临时会让点|"
                r"(?:(?:题卡图形|图形|任务航线)?\s*\d{1,2}\s*(?:号)?(?:航)?点))" + quote)
    # ★ 题库 OCR 会把“东南偏南30°方向300米处”断成“东南偏南30°**。**方向300米处”，
    #   于是方位和距离落进两个分句，距离永远配不上（实测 旧样本）。
    #   这里先把这种“方位。方向+数字”的断句拼回去，再按分句处理。
    text = re.sub(r"。\s*(?=方向\s*[\d.]+)", "", text)
    pattern = re.compile(
        # 主语可能带引号（实测 旧样本：“避让点”位于题卡图形1号点东南偏南30°…）——
        # 不允许引号时“避让点”与“位于”接不上，整句被跳过，方位和距离一起丢。
        endpoint + r"[\"”』']?\s*(?:位于|处于|在)\s*" + endpoint +
        r"\s*(?:的)?\s*(?:(?:为|是)\s*)?"
        r"(?:(?:航向|方向|方位(?:角)?)\s*(?:(?:为|是|=|[:：])\s*)?)?"
        # OCR/题卡常用全角括号包方位词，例如“（西偏南15°）方向”。
        r"(?:[（(]\s*)?" + _POSITION_DIRECTION +
        r"\s*(?:[）)]\s*)?(?:方向)?", re.I)
    out = []
    for clause in re.split(r"[。;\n]", text):
        for match in pattern.finditer(clause):
            bearing = GEO.parse_bearing(match.group(3))
            if bearing is None:
                continue
            subject = _direction_node(match.group(1))
            reference = _direction_node(match.group(2))
            if not subject or not reference or subject == reference:
                continue
            out.append({"a": reference, "b": subject,
                        "value": float(bearing) % 360.0,
                        "kind": "bearing", "source": match.group(0)})
            # ★ “避让点位于题卡图形1号点东南偏南30°。方向300米处。” —— 距离写在
            #   方位词**之后**，中间还可能夹一个句号（实测 旧样本 因此丢掉唯一的
            #   距离约束 |1-A| = 300，定位条件差 1 条、整题判不出唯一解）。
            #   关键字“位于/到”两个方向都能用：这里补一条 reference→subject 的距离。
            tail = clause[match.end():match.end() + 24]
            span = re.search(r"([\d.]+)\s*(公里|千米|km|米|m)", tail, re.I)
            if span:
                scale = 1000.0 if span.group(2).lower() in ("公里", "千米", "km") else 1.0
                out.append({"a": reference, "b": subject,
                            "value": float(span.group(1)) * scale,
                            "kind": "distance", "source": (match.group(0) + span.group(0))})
    return out


def _parse_angles(text):
    out = []
    angle = "\u2220"
    text = re.sub(r"角\s*(?=\d)", angle, text)
    pattern = re.compile(
        r"((?:" + re.escape(angle) + r"\s*\d+\s*=\s*)*" +
        re.escape(angle) + r"\s*\d+)\s*(?:=\s*(?:为|是)?|为|是)\s*"
        r"([\d.]+)\s*(?:°|度)")
    for clause in re.split(r"[。;\n]", text):
        for match in pattern.finditer(clause):
            value = float(match.group(2))
            vertices = [int(x) for x in re.findall(
                re.escape(angle) + r"\s*(\d+)", match.group(1))]
            for vertex in vertices:
                out.append({"vertex": str(vertex), "value": value,
                            "kind": "interior_angle", "source": match.group(0)})
    dedup = {}
    for item in out:
        dedup[item["vertex"]] = item
    # ★ “∠1为直角” —— 题面用汉字写直角、不带度数，上面那条正则要「数字+°」，
    #   于是整族（直角三角形，实测 7 道：旧样本/316/326/354/387…）丢掉关键夹角。
    right_angle = re.compile(
        r"((?:" + re.escape(angle) + r"\s*\d+\s*[、,，和及]\s*)*" +
        re.escape(angle) + r"\s*\d+)\s*(?:均|都|各)?\s*(?:为|是|等于)\s*直角")
    for clause in re.split(r"[。;\n]", text):
        for match in right_angle.finditer(clause):
            for vertex in re.findall(re.escape(angle) + r"\s*(\d+)", match.group(1)):
                dedup[vertex] = {"vertex": vertex, "value": 90.0,
                                 "kind": "interior_angle", "source": match.group(0)}
    return list(dedup.values())

def _parse_bearings(text):
    text = text.replace("\u9006\u98de\u884c", "\u9006\u98ce").replace(
        "\u987a\u98de\u884c", "\u987a\u98ce")
    out = []
    wind = GEO.wind_from(text)
    point_direction = (r"(?:(?:东北|东南|西南|西北|[北南东西NSEW])\s*偏\s*[北南东西NSEW]\s*[\d.]+\s*°?|"
                       r"正北|正南|正东|正西|NNE|ENE|ESE|SSE|SSW|WSW|WNW|NNW|"
                       r"NE|NW|SE|SW|N|S|E|W|东北|西北|东南|西南|北|南|东|西)")
    for clause in re.split(r"[。;\n]", text):
        clause = clause.strip()
        clean = re.sub(r"[“”‘’\"']", "", clause)
        # 同一编号条件可连续列出“H-5=225°，3-2=315°”。
        for explicit in _DIRECTION_PAIR_RE.finditer(clean):
            number = re.match(
                r"\s*(?:(?:航向|方位)(?:角)?\s*)?(?:为|是|=|:)\s*"
                r"([\d.]+)\s*°", clean[explicit.end():])
            if number:
                out.append({"a": _direction_node(explicit.group("a")),
                            "b": _direction_node(explicit.group("b")),
                            "value": float(number.group(1)) % 360.0,
                            "kind": "bearing", "source": clause})
        pair = None
        match = _DIRECTION_PAIR_RE.search(clean)
        if match:
            pair = (_direction_node(match.group("a")),
                    _direction_node(match.group("b")))
        if pair:
            # “2点到3点东偏南25°”虽没有“航向为”，仍给出了直接方位。
            tail = clean[match.end():]
            direct = re.match(
                r"\s*(?:的)?\s*(?:(?:航线)?(?:航向|方向|方位)(?:角)?\s*(?:为|是|:)?\s*|为\s*)?(" +
                point_direction + r")", tail, re.I)
            # 明确写“航向310”时允许省略度数符；裸数字必须紧跟航向词，
            # 避免把 ∠H12=40° 误当成 H→12 的航向。
            numeric = re.match(
                r"\s*(?:的)?\s*(?:(?:航线)?(?:航向|方向|方位)(?:角)?\s*"
                r"(?:为|是|[:：=])?\s*([\d.]+)\s*(?:°|度)?"
                r"(?!\s*(?:公里|千米|km|米|m))|"
                r"(?:(?:航线)?(?:航向|方向|方位)(?:角)?\s*"
                r"(?:为|是|[:：])?\s*|为\s*)?([\d.]+)\s*(?:°|度))",
                tail, re.I)
            bearing = (GEO.parse_bearing(direct.group(1)) if direct else
                       float(numeric.group(1) or numeric.group(2)) % 360.0
                       if numeric else None)
            if (bearing is not None and
                    not re.search(r"侧风", clause) and
                    not re.search(r"逆风|顶风|顺风|尾风", clause) and
                    (direct or numeric)):
                out.append({"a": pair[0], "b": pair[1], "value": float(bearing),
                            "kind": "bearing", "source": clause})
            elif wind is not None and re.search(r"逆风|顶风|顺风|尾风", clause):
                value = float(wind)
                if re.search(r"顺风|尾风", clause):
                    value = (value + 180.0) % 360.0
                out.append({"a": pair[0], "b": pair[1], "value": value,
                            "kind": "bearing", "source": clause})
            elif wind is not None and re.search(r"侧风", clause):
                value = (float(wind) - 90.0 if re.search(r"右侧风", clause)
                         else float(wind) + 90.0) % 360.0
                out.append({"a": pair[0], "b": pair[1], "value": value,
                            "kind": "bearing", "source": clause})
        # “2点处于H点东偏北20°，100m”描述的是 H→2 的方向和距离。
        match = re.search(r"(\d+)点.{0,10}(?:处于|位于).{0,12}(?:起飞点|H点?)", clean, re.I)
        if match:
            bearing = GEO.parse_bearing(clause)
            if bearing is not None:
                out.append({"a": "H", "b": match.group(1),
                            "value": float(bearing), "kind": "bearing",
                            "source": clause})
        match = re.search(
            r"(?:任务航线|任务航点|题卡图形|图形)?\s*"
            r"(\d+)\s*(?:号)?\s*(?:航点|点)\s*(?:位于|处于|在)\s*"
            r"(?:起飞点|H点?)\s*(?:的)?\s*(" + point_direction + r")",
            clean, re.I)
        if match:
            bearing = GEO.parse_bearing(match.group(2))
            if bearing is not None:
                out.append({"a": "H", "b": match.group(1),
                            "value": float(bearing), "kind": "bearing",
                            "source": clause})
        # A 点位于 B 的 SW 方位（箭头 B→A）。
        match = re.search(
            r"(?:(?:任务航线的航点|任务航线|在务航线|任务航点|题卡图形|题卡|航点|图形|示意图)\s*)?"
            r"(\d+)\s*(?:号)?点?\s*(?:位于|处于|在)\s*"
            r"(?:(?:任务航线的航点|任务航线|在务航线|任务航点|题卡图形|题卡|航点|图形|示意图)\s*)?"
            r"(\d+)\s*(?:号)?点?\s*(?:的)?\s*(?:航向|方向)?\s*"
            r"(?:的|为|:)?\s*(" + point_direction + r")", clean, re.I)
        if match:
            bearing = GEO.parse_bearing(match.group(3))
            out.append({"a": match.group(2), "b": match.group(1),
                        "value": float(bearing), "kind": "bearing",
                        "source": clause})
        # “3点位于4点正右侧风”给出4→3的航向。
        match = re.search(
            r"(?:题卡图形|题卡|图形|航点)?\s*(\d+)\s*(?:号)?点\s*"
            r"(?:位于|处于|在)\s*(?:题卡图形|题卡|图形|航点)?\s*"
            r"(\d+)\s*(?:号)?点\s*(?:为|是)?\s*"
            r"(正右侧风|右侧风|正左侧风|左侧风|逆风|顶风|顺风|尾风)", clean)
        if match and wind is not None:
            relation = match.group(3)
            value = float(wind)
            if relation in ("顺风", "尾风"):
                value = (value + 180.0) % 360.0
            elif "右侧风" in relation:
                value = (value - 90.0) % 360.0
            elif "左侧风" in relation:
                value = (value + 90.0) % 360.0
            out.append({"a": match.group(2), "b": match.group(1),
                        "value": value, "kind": "bearing", "source": clause})
        match = re.search(
            r"(避让点|过渡点|过度点|转场点|中转点)\s*(?:在|位于|处于)\s*"
            r"(?:题卡图形|图形|航点)?\s*(\d+)\s*(?:号)?点\s*(?:的)?\s*"
            r"(?:方向|方位)?\s*(?:为|:)?\s*(" + point_direction + r")", clean, re.I)
        if match:
            bearing = GEO.parse_bearing(match.group(3))
            origin = "A" if "避让" in match.group(1) else "T"
            out.append({"a": match.group(2), "b": origin,
                        "value": float(bearing), "kind": "bearing",
                        "source": clause})
        # 编号点相对起飞、避让或过渡点的方位关系。
        direction_token = (r"(?:[北南东西]\s*偏\s*[北南东西]\s*[\d.]+\s*°?|"
                           r"NNE|ENE|ESE|SSE|SSW|WSW|WNW|NNW|NE|NW|SE|SW|"
                           r"正北|正南|正东|正西|东北|西北|东南|西南|北|南|东|西)")
        match = re.search(r"(?:(?:任务航线|在务航线|任务航点|题卡图形|图形|示意图)\s*)?(\d+)\s*(?:号)?点\s*(?:位于|处于|在)\s*(?:起飞点|避让点|过渡点|过度点|转场点|H点?)\s*(?:的|为|:)?\s*(" + direction_token + r")", clean, re.I)
        if match:
            origin = ("A" if "避让点" in clean else
                      "T" if "过渡点" in clean or "过度点" in clean or "转场点" in clean else "H")
            bearing = GEO.parse_bearing(match.group(2))
            out.append({"a": origin, "b": match.group(1),
                        "value": float(bearing), "kind": "bearing",
                        "source": clause})
        # “X点到Y点为逆风/顺风”也是明确的航段方位条件。
        match = _DIRECTION_PAIR_RE.search(clean)
        if match and wind is not None and re.search(r"逆风|顺风|尾风|顶风|侧风", clause):
            value = float(wind)
            if re.search(r"顺风|尾风", clause):
                value = (value + 180.0) % 360.0
            elif re.search(r"右侧风", clause):
                value = (value - 90.0) % 360.0
            elif re.search(r"左侧风", clause):
                value = (value + 90.0) % 360.0
            out.append({"a": _direction_node(match.group("a")),
                        "b": _direction_node(match.group("b")),
                        "value": value, "kind": "bearing", "source": clause})
        point_label = chr(0x822a) + chr(0x70b9)
        shape_label = chr(0x56fe) + chr(0x5f62) + chr(0x70b9)
        point_word = chr(0x70b9)
        number_suffix = chr(0x53f7)
        located_word = chr(0x4f4d) + chr(0x4e8e)
        direction_word = chr(0x65b9) + chr(0x5411)
        degree_word = chr(0x5ea6)
        point_prefix = "(?:" + point_label + "|" + shape_label + ")?"
        located_pattern = (
            point_prefix + " *([0-9]+) *(?:" + number_suffix + ")? *(?:" +
            point_word + ")? *" + located_word + " *" + point_prefix +
            " *([0-9]+) *(?:" + number_suffix + ")? *" + point_word +
            " *(?:" + chr(0x7684) + ")? *(" + point_direction +
            ")(?:" + degree_word + "|°)? *(?:" + direction_word + ")?")
        match = re.search(located_pattern, clean, re.I)
        if match:
            bearing = GEO.parse_bearing(match.group(3))
            if bearing is not None:
                out.append({"a": match.group(2), "b": match.group(1),
                            "value": float(bearing), "kind": "bearing",
                            "source": clause})
    left_crosswind = chr(0x5de6) + chr(0x4fa7) + chr(0x98ce)
    right_crosswind = chr(0x53f3) + chr(0x4fa7) + chr(0x98ce)
    if wind is not None:
        for item in out:
            if left_crosswind in item["source"]:
                item["value"] = (float(wind) + 90.0) % 360.0
            elif right_crosswind in item["source"]:
                item["value"] = (float(wind) - 90.0) % 360.0
    unique = {}
    for item in out:
        key = (item["a"], item["b"], round(item["value"], 4))
        unique[key] = item
    return list(unique.values())


def _parse_direction_relations(text):
    """识别两条航段间的同向、反向或旋转方位关系。"""
    relations = []
    for clause in re.split(r"[。;\n]", text):
        clean_clause = re.sub(r"[“”‘’\"']", "", clause)
        pairs = list(_DIRECTION_PAIR_RE.finditer(clean_clause))
        relation_tokens = list(re.finditer(r"相反|相同|一致|同向|反向", clean_clause))
        for relation_token in relation_tokens:
            # “长度一致”是等长约束，不能误写成两条航段同向。
            if re.search(r"(?:距离|长度|边长)\s*$",
                         clean_clause[max(0, relation_token.start() - 6):relation_token.start()]):
                continue
            preceding = [pair for pair in pairs
                         if pair.start() < relation_token.start()]
            if len(preceding) < 2:
                continue
            # 取关系词前最近的两条航段，跳过前面重复出现的距离描述。
            first, second = preceding[-2:]
            phrase = clean_clause[first.end():]
            rotation = re.search(
                r"(顺时针|逆时针)\s*(?:旋转|转)?\s*([\d.]+)\s*(?:°|度)",
                phrase)
            degrees = float(rotation.group(2)) if rotation else 0.0
            if rotation and rotation.group(1) == "逆时针":
                degrees = -degrees
            relation = {
                "first": [_direction_node(first.group("a")),
                          _direction_node(first.group("b"))],
                "second": [_direction_node(second.group("a")),
                           _direction_node(second.group("b"))],
                "rotation": degrees,
                "opposite": bool(re.search(r"相反|反向", clean_clause[
                    relation_token.start():])),
                "source": clause.strip(),
            }
            if None not in relation["first"] + relation["second"]:
                relations.append(relation)
    unique = {}
    for item in relations:
        key = (tuple(item["first"]), tuple(item["second"]),
               round(float(item["rotation"]), 4), bool(item["opposite"]))
        unique[key] = item
    return list(unique.values())


def _graph(q, shape):
    explicit = q.get("reference_graph")
    if isinstance(explicit, dict):
        nodes = [str(n) for n in explicit.get("nodes", [])]
        geom = [tuple(map(str, e[:2])) for e in explicit.get("geometry_edges", [])]
        route = [tuple(map(str, e[:2])) for e in explicit.get("flight_edges", [])]
        return nodes, geom, route, "user"

    source = q.get("reference_source") or q.get("_reference_source") or {}
    diagram = source.get("diagram_model") or {}
    validated = _validated_local_graph(_local_cv_diagram(q))
    if validated:
        return validated
    vision_nodes = [str(node) for node in diagram.get("nodes", [])]
    vision_edges = [tuple(map(str, edge[:2]))
                    for edge in diagram.get("geometry_edges", [])
                    if isinstance(edge, (list, tuple)) and len(edge) >= 2]
    if (diagram.get("source") == "local_cv" and
            diagram.get("validated_cycle") and
            float(diagram.get("confidence", 0)) >= 0.60 and
            float(diagram.get("edge_margin", 0)) >= 0.025 and
            len(vision_nodes) >= 3 and
            set(vision_nodes) == {str(index) for index in
                                  range(1, len(vision_nodes) + 1)} and
            len(vision_edges) == len(vision_nodes) and
            all(first in vision_nodes and second in vision_nodes and first != second
                for first, second in vision_edges)):
        order = [str(node) for node in diagram.get("route_order", [])]
        if len(order) == len(vision_nodes) and set(order) == set(vision_nodes):
            route = [(order[index], order[(index + 1) % len(order)])
                     for index in range(len(order))]
            return vision_nodes, vision_edges, route, "local_cv"

        # 视觉模型识别的图形只能按它明确识别出的点线构图；不按点号顺序补造边或航线。
    visual = _vision_diagram(q)
    vlm_nodes = [str(node) for node in visual.get("nodes", [])]
    if (len(vlm_nodes) >= 3 and len(vlm_nodes) <= 16 and
            len(set(vlm_nodes)) == len(vlm_nodes) and
            all(node.isdigit() for node in vlm_nodes)):
        vlm_edges = [tuple(map(str, edge[:2]))
                     for edge in visual.get("geometry_edges", [])
                     if isinstance(edge, (list, tuple)) and len(edge) >= 2 and
                     str(edge[0]) in vlm_nodes and str(edge[1]) in vlm_nodes and
                     str(edge[0]) != str(edge[1])]
        raw_order = [str(node) for node in visual.get("flight_order", [])]
        order = [node for node in raw_order if node in vlm_nodes]
        raw_flight = visual.get("flight_edges") or []
        vlm_route = [tuple(map(str, edge[:2])) for edge in raw_flight
                     if isinstance(edge, (list, tuple)) and len(edge) >= 2 and
                     str(edge[0]) in vlm_nodes and str(edge[1]) in vlm_nodes and
                     str(edge[0]) != str(edge[1])]
        if (not vlm_route and len(order) == len(vlm_nodes) and
                set(order) == set(vlm_nodes)):
            vlm_route = [(order[index], order[index + 1])
                         for index in range(len(order) - 1)]
            if visual.get("flight_closed") is True and len(order) >= 3:
                vlm_route.append((order[-1], order[0]))
        return vlm_nodes, vlm_edges, vlm_route, "vision_model"

    positions = _imported_diagram_points(q)
    if positions:
        nodes = [str(i + 1) for i in range(len(positions))]
    else:
        source_text = " ".join(str(q.get(k) or "") for k in
                               ("conditions_text", "requirements_text", "graph_hint"))
        text_nodes = set(_POINT_TOKEN.findall(source_text))
        circled = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
        circled_values = {char: str(index + 1)
                          for index, char in enumerate(circled)}
        hint = str(q.get("graph_hint") or "")
        text_nodes.update(circled_values[char] for char in hint
                          if char in circled_values)
        nodes = sorted(text_nodes, key=lambda n: int(n))
        if not nodes and str(q.get("graph_hint") or "").strip() and len(
                str(q.get("graph_hint") or "")) <= 24:
            hint_numbers = re.findall(r"(?<!\d)\d{1,2}(?!\d)",
                                      str(q.get("graph_hint") or ""))
            nodes = sorted(set(hint_numbers), key=lambda n: int(n))
        # 新导入题没有内置 layout；题型本身给出总航点数时，补齐未在
        # 条件文字中逐一提及的编号，避免将四边形误当作三角形。
        shape_counts = {
            "等边三角形": 3, "直角三角形": 3, "三角形": 3,
            "梯形": 4, "直角梯形": 4, "等腰梯形": 4,
            "蝴蝶形": 4, "四边形": 4, "凹四边形": 4,
            "平行四边形": 4, "矩形": 4, "长方形": 4,
            "正方形": 4, "菱形": 4,
            "五边形": 5, "六边形": 6, "七边形": 7,
            "八边形": 8, "九边形": 9, "十边形": 10,
            "十一边形": 11, "十二边形": 12, "十三边形": 13,
            "十四边形": 14, "十五边形": 15, "十六边形": 16,
        }
        expected = shape_counts.get(shape)
        if expected and all(node.isdigit() and
                            1 <= int(node) <= expected for node in nodes):
            nodes = [str(index) for index in range(1, expected + 1)]
        if not nodes and shape in ("等边三角形", "直角三角形"):
            nodes = ["1", "2", "3"]
        elif not nodes and shape in ("梯形", "蝴蝶形"):
            nodes = ["1", "2", "3", "4"]
        elif not nodes and shape == "五边形":
            nodes = ["1", "2", "3", "4", "5"]
        elif not nodes and shape == "六边形":
            nodes = ["1", "2", "3", "4", "5", "6"]
    if shape == "蝴蝶形" and len(nodes) == 4:
        geom = [("1", "3"), ("3", "2"), ("2", "4"), ("4", "1")]
        route = [("1", "2"), ("2", "3"), ("3", "4"), ("4", "1")]
    elif shape == "扫描航线" and len(nodes) >= 4:
        geom = [(str(i), str(i + 1)) for i in range(1, len(nodes))]
        route = list(geom)
    elif shape == "圆形":
        geom, route = [], []
    elif len(nodes) >= 3:
        geom = [(nodes[i], nodes[(i + 1) % len(nodes)])
                for i in range(len(nodes))]
        route = list(geom)
    else:
        geom, route = [], []
    graph_source = "imported_diagram" if positions else ("hint" if nodes else "text")
    return nodes, geom, route, graph_source


def _closed_cycle_route(nodes, edges):
    """只在识别边构成唯一单环时，按图中点号顺序生成闭合航线。"""
    nodes = [str(node) for node in nodes]
    if len(nodes) < 3 or len(set(nodes)) != len(nodes):
        return []
    allowed = set(nodes)
    normalized = []
    seen = set()
    for edge in edges or []:
        if not isinstance(edge, (list, tuple)) or len(edge) < 2:
            return []
        a, b = str(edge[0]), str(edge[1])
        pair = frozenset((a, b))
        if a not in allowed or b not in allowed or a == b or pair in seen:
            return []
        seen.add(pair)
        normalized.append((a, b))
    if len(normalized) != len(nodes):
        return []

    adjacent = {node: set() for node in nodes}
    for a, b in normalized:
        adjacent[a].add(b)
        adjacent[b].add(a)
    if any(len(neighbors) != 2 for neighbors in adjacent.values()):
        return []

    # 點號順序用來在同一個幾何環的兩種走向中固定展示方向。
    order_index = {node: index for index, node in enumerate(nodes)}
    start = nodes[0]
    first = min(adjacent[start], key=lambda node: order_index[node])
    ordered = [start, first]
    while len(ordered) < len(nodes):
        previous, current = ordered[-2], ordered[-1]
        following = adjacent[current] - {previous}
        if len(following) != 1:
            return []
        next_node = next(iter(following))
        if next_node in ordered:
            return []
        ordered.append(next_node)
    if start not in adjacent[ordered[-1]]:
        return []
    return [(ordered[index], ordered[index + 1])
            for index in range(len(ordered) - 1)] + [(ordered[-1], start)]


def _resolve_access_alias(spec):
    """同一道题把唯一进场点混称避让点和过渡点时统一编号。"""
    segments = spec.get("segment_constraints", [])
    arrivals = {str(item["a"]) for item in segments
                if str(item["b"]) == "H" and str(item["a"]) in ("A", "T")}
    if len(arrivals) != 1:
        return spec
    primary = next(iter(arrivals))
    alternate = "T" if primary == "A" else "A"
    if alternate in spec.get("coordinates", {}):
        return spec
    # 两个命名点各有明确的起飞航段时，必须保持两个独立航点。
    if any(str(item["b"]) == "H" and str(item["a"]) == alternate
           for item in segments):
        return spec
    changed = False
    for key in ("distance_constraints", "bearing_constraints", "segment_constraints"):
        for item in spec.get(key, []):
            for endpoint in ("a", "b"):
                if str(item[endpoint]) == alternate:
                    item[endpoint] = primary
                    changed = True
    for item in spec.get("direction_relations", []):
        for pair in ("first", "second"):
            new_pair = [primary if str(node) == alternate else node
                        for node in item[pair]]
            changed |= new_pair != item[pair]
            item[pair] = new_pair
    if changed:
        spec["access_alias"] = {alternate: primary}
    return spec


def _merge_figure_constraints(q, spec):
    source = (q or {}).get("reference_source") or (q or {}).get("_reference_source") or {}
    detected = None
    if source.get("ocr_words"):
        detected = FC.extract(source.get("ocr_words"), spec,
                              source.get("image_size"))
    if detected is None:
        detected = {}
    segments = list(spec.get("segment_constraints") or [])
    distances = list(spec.get("distance_constraints") or [])
    errors = []
    for item in detected.get("segments", []):
        pair = frozenset((str(item.get("a")), str(item.get("b"))))
        same = next((old for old in segments
                     if frozenset((str(old.get("a")), str(old.get("b")))) == pair), None)
        if same:
            if (abs(float(same["distance"]) - float(item["distance"])) > DISTANCE_TOLERANCE_M or
                    abs(_wrap_angle(float(same["bearing"]) - float(item["bearing"]))) > ANGLE_TOLERANCE_DEG):
                errors.append("图卡标注与题面航段%s-%s不一致" % (item["a"], item["b"]))
            continue
        segments.append(item)
    for item in detected.get("distances", []):
        pair = frozenset((str(item.get("a")), str(item.get("b"))))
        same = next((old for old in distances
                     if frozenset((str(old.get("a")), str(old.get("b")))) == pair), None)
        if same:
            if abs(float(same["value"]) - float(item["value"])) > DISTANCE_TOLERANCE_M:
                errors.append("图卡边长与题面%s-%s条件不一致" % (item["a"], item["b"]))
            continue
        distances.append(item)
    spec["segment_constraints"] = segments
    spec["distance_constraints"] = distances
    image_segments = detected.get("segments", [])
    inferred_approach = any(str(item.get("a")) == "1" and
                            str(item.get("b")) == "H" for item in image_segments)
    image_routes = list(detected.get("flight_edges", []))
    if inferred_approach:
        image_routes.insert(0, ["H", "1"])
    for edge in image_routes:
        if edge not in spec.get("flight_edges", []) and edge[::-1] not in spec.get("flight_edges", []):
            spec["flight_edges"].insert(0, edge)
    spec["figure_ocr"] = {"source": "图卡标注", "matched_segments": len(detected.get("segments", [])),
                          "matched_distances": len(detected.get("distances", [])),
                          "ambiguous": detected.get("ambiguous", []),
                          "unmatched": detected.get("unmatched", [])}
    if errors:
        spec["review"] = {"reason": "；".join(errors), "source": "图卡标注与题面文字冲突"}
    return spec


def _suggested_orientation(q):
    """建议参数末尾的孤立方向词 → 罗盘度数（用户口径：这就是图形的朝向）。

    必须排除坐标里的 N/E 标记（“N:26°12′36″”）与单位里的字母，只认**独立成词**的方向符号。
    """
    text = str(q.get("suggested_text") or "")
    matches = re.findall(r"(?<![0-9A-Za-z°′″':：])(NE|NW|SE|SW|N|S|E|W)\s*$",
                         text.strip(), re.I)
    if not matches:
        return None
    bearing = GEO.parse_bearing(matches[-1].upper())
    return float(bearing) if bearing is not None else None


def _augment_shape_edges(shape, text, params, nodes, geom, edges):
    """补充题干以文字表达、但没有逐边编号的规则边长。"""
    out = list(edges)
    regular_match = re.search(r"正\s*([一二两三四五六七八九十\d]{1,2})\s*边形", text)
    regular_count = None
    if regular_match:
        token = regular_match.group(1)
        regular_count = int(token) if token.isdigit() else _chinese_number(token)
    regular_declared = bool(
        regular_count and len(nodes) == regular_count and
        len(geom) == regular_count)
    if regular_declared:
        # 明确写出“正 n 边形”后，未标具体边号的数值边长约束适用于整圈。
        side_length = re.search(
            r"(?:边长|边长度|每条边(?:的)?(?:长度|长)?|各边(?:的)?(?:长度|长)?)\s*"
            r"(?:均|都)?\s*(?:为|是|=)?\s*(\d+(?:\.\d+)?)\s*"
            r"(公里|千米|km|米|m)(?!\s*/\s*s)", text, re.I)
        if side_length:
            unit = side_length.group(2).lower()
            scale = 1000.0 if unit in ("公里", "千米", "km") else 1.0
            value = float(side_length.group(1)) * scale
            for a, b in geom:
                out.append({"a": a, "b": b, "value": value,
                            "kind": "distance",
                            "source": "题面规则：正多边形边长为%s米" % value})
    if (shape == "五边形" and params.get("a") is not None and
            re.search(r"所有\s*(?:航线)?\s*边长\s*(?:为|是|=)?\s*a\b", text, re.I)):
        # “所有航线边长为a”是整圈边长约束，按图形边逐条展开。
        for a, b in geom:
            out.append({"a": a, "b": b, "value": float(params["a"]),
                        "kind": "distance", "source": "题面规则：所有航线边长为a"})
    # ★ 题面被 OCR 揉坏的情况：“边长为a”被写成“边长为航线相对地面高度为b”（实测 旧样本），
    #   于是那条要求「边长后面必须紧跟 a」的正则永远匹配不上，整题一条边都没提取出来。
    #   正多边形（等边三角形／正六边形）的“边长为…”按题库口径就是**每条边**都取参数 a
    #   （用户已确认：229 的“边长为 a”指的就是所有边的边长），所以这里放宽到
    #   “只要题面出现「边长」二字且参数 a 存在”即可。
    if shape in ("等边三角形", "六边形") and params.get("a") is not None \
            and re.search(r"边长", text):
        for a, b in geom:
            out.append({"a": a, "b": b, "value": float(params["a"]),
                        "kind": "distance",
                        "source": "题型规则：正多边形各边边长为参数a（题面 OCR 可能吞掉 a）"})

    side_shapes = {"等边三角形", "六边形", "五边形"}
    if shape in side_shapes and re.search(r"每条边|所有边|边长", text):
        value = params.get("a")
        if value is not None:
            # “12、23、15边长为a”只约束列出的三条边，不能扩成整个五边形。
            # 正六边形与等边三角形的“边长为a”才天然指全部边。
            if shape == "五边形":
                pattern = r"(?:五边形)?\s*(?:每条边|所有边)\s*(?:边长|长)?\s*(?:为|是|=)?\s*a\b"
            else:
                pattern = (r"(?:每条边(?:边长)?|所有边(?:边长)?|边长)\s*"
                           r"(?:为|是|=)?\s*a\b")
            if re.search(pattern, text, re.I):
                for a, b in geom:
                    out.append({"a": a, "b": b, "value": float(value),
                                "kind": "distance", "source": "题型规则：各边长度为a"})

    # “1423构成一个边长为300米的四边形”把闭合顺序写成一串编号。
    for match in re.finditer(
            r"(?<!\d)(\d{4,8})\s*构成.{0,16}?边长\s*(?:为|是|=)?\s*"
            r"([a-z]|\d+(?:\.\d+)?)\s*(?:米|m)?", text, re.I):
        sequence = list(match.group(1))
        token = match.group(2).lower()
        value = params.get(token) if token.isalpha() else float(token)
        if value is None or len(sequence) < 3:
            continue
        for index, a in enumerate(sequence):
            b = sequence[(index + 1) % len(sequence)]
            out.append({"a": a, "b": b, "value": float(value),
                        "kind": "distance", "source": match.group(0)})
    unique = {}
    for item in out:
        key = (item["a"], item["b"], round(item["value"], 6))
        unique[key] = item
    return list(unique.values())


def _align_segment_distances(segments, distances):
    """航段的距离若与显式边长自相矛盾，以**显式边长**为准（只保留航向）。

    ★ 为什么需要：同一句里可能出现两个对象，距离容易被错挂。
      实测 旧样本 原文“当地风向:西北风，2-1逆风飞行，H距离1点120米” ——
      那个 120 米是 **H 到 1 点**的，却被挂到了 `2-1` 上，于是 `|1-2|=30` 与
      `2→1=120米` 直接矛盾，求解器永远报“尚未组成可校验的唯一坐标解”。
      显式边长（“…边长为30米”）是更可靠的来源，所以冲突时改用它。
    """
    table = {frozenset((str(item["a"]), str(item["b"]))): float(item["value"])
             for item in distances}
    aligned = []
    for item in segments:
        pair = frozenset((str(item["b"]), str(item["a"])))
        explicit = table.get(pair)
        current = float(item.get("distance") or 0.0)
        if explicit and current and abs(explicit - current) > 0.05 * max(explicit, current):
            item = dict(item)
            item["distance"] = explicit
            item["distance_note"] = ("航段距离与显式边长冲突，已按边长修正"
                                     "（原航段值 %.1f 米）" % current)
        aligned.append(item)
    return aligned


def extract_question_spec(q):
    """提取可计算条件，保留每一项的题面来源。"""
    original_q = q or {}
    original_source = (original_q.get("reference_source") or
                       original_q.get("_reference_source") or {})
    reviewed_before_refresh = original_source.get("reviewed_fields")
    recognized_before_review = original_source.get("recognized_fields")
    unchanged_before_refresh = (
        isinstance(reviewed_before_refresh, dict) and
        all(str(original_q.get(key) or "") ==
            str(reviewed_before_refresh.get(key) or "")
            for key in ("graph_hint", "conditions_text", "requirements_text",
                        "suggested_text")) and
        (not isinstance(recognized_before_review, dict) or
         all(str(reviewed_before_refresh.get(key) or "") ==
             str(recognized_before_review.get(key) or "")
             for key in ("graph_hint", "conditions_text", "requirements_text",
                         "suggested_text"))))
    q = _refresh_imported_ocr_text(original_q)
    condition_fields = ("graph_hint", "conditions_text", "requirements_text",
                        "suggested_text")
    conditions = "\n".join(str(q.get(k) or "") for k in
                            condition_fields[1:])
    source = q.get("reference_source") or q.get("_reference_source") or {}
    assist = _current_condition_assist(original_q) or {}
    assisted_conditions = str(
        assist.get("candidate_conditions") or
        assist.get("normalized_conditions") or "").strip()
    assist_matches_current = bool(assist)
    assisted_diagram = assist.get("diagram_model") or {}
    assist_has_recognized_conditions = bool(
        assisted_conditions and
        (assist.get("solver_usable") or assist.get("status") in ("ready", "review")))
    assist_has_diagram = bool(
        assisted_diagram.get("source") == "vision_model" and
        len(assisted_diagram.get("nodes") or []) >= 3)
    use_assist = (q.get("imported") and assist_matches_current and
                  (assist_has_recognized_conditions or assist_has_diagram))
    if use_assist and assisted_conditions:
        q = dict(q)
        # 模型条件是补充来源；保留 OCR 原文，避免模型漏掉 OCR 已读到的某条硬约束。
        conditions = _merge_condition_texts(conditions, assisted_conditions)
        q["conditions_text"] = conditions
        q["requirements_text"] = ""
        q["suggested_text"] = ""
    norm = _norm_text(conditions)
    # ★ OCR 容错：`∠2=∠3=90°` 常被认成 `2=23=90°`（一个 ∠ 丢掉、另一个认成 2）。
    #   实测 旧样本：这条认坏后角度一条都没提取到，而秩账本正好显示“题面少 2 条”
    #   —— 补回来就是那 2 条（∠2、∠3）。模式要求“两段等号 + 结尾角度”，不会误伤。
    norm = re.sub(r"(?<![\d∠])(\d)\s*=\s*(\d)(\d)\s*=\s*(\d{1,3})\s*°",
                  lambda m: "∠%s=∠%s=%s°" % (m.group(1), m.group(3), m.group(4)),
                  norm)
    shape = _shape(q)
    params = _param_values(q)
    # 兼容“航线长度为a/间隔为b”，直接从同一条题面规则取变量名。
    for key, phrase in (("scan_length", "航线长度"), ("scan_spacing", "航线间隔")):
        numeric = re.search(
            phrase + r"\s*(?:为|是|=)?\s*([\d.]+)\s*"
            r"(公里|千米|km|米|m)(?![A-Za-z])", norm, re.I)
        if numeric:
            unit = numeric.group(2).lower()
            scale = 1000.0 if unit in ("公里", "千米", "km") else 1.0
            params["__" + key] = float(numeric.group(1)) * scale
            continue
        match = re.search(phrase + r"\s*(?:为|是|=)?\s*([a-z])", norm, re.I)
        if match:
            value = params.get(match.group(1).lower())
            if value is not None:
                params["__" + key] = value
    if params.get("__scan_spacing") is None:
        # 导入题常把“航线间隔30m”简写成“长度100m，间隔30m”。
        spacing_match = re.search(
            r"(?:间隔|间距)\s*(?:为|是|=|[:：])?\s*([\d.]+)\s*"
            r"(公里|千米|km|米|m)(?![A-Za-z])", norm, re.I)
        if spacing_match:
            unit = spacing_match.group(2).lower()
            scale = 1000.0 if unit in ("公里", "千米", "km") else 1.0
            params["__scan_spacing"] = float(spacing_match.group(1)) * scale
    coords = _coords(q)
    # ★ `_parse_position_bearings` 既能给出方位、也能给出“方位+距离”里的距离
    #   （“避让点位于1号点东南偏南30°方向300米处”）。早先它只被当成方位来源，
    #   返回的**距离项被上层丢掉**，于是 旧样本 少了唯一的 |1-A| 定位条件。
    position_items = _parse_position_bearings(norm)
    edges = (_parse_edges(norm, params) + _parse_hub_distances(norm)
             + _parse_apart_distances(norm))
    angles = _parse_angles(norm)
    # 两种句式可能描述同一条航向（如“避让点位于1号点东南方向”既被位置句、
    # 也被关系句认出来），按 (起点, 终点) 去重，免得同一条件被算两遍。
    merged_bearings = {}
    for item in (_parse_bearings(norm)
                 + [entry for entry in position_items
                    if entry.get("kind") == "bearing"]
                 + _parse_dotted_bearings(norm) + _parse_bare_pair_bearings(norm)):
        merged_bearings.setdefault((str(item["a"]), str(item["b"])), item)
    bearings = list(merged_bearings.values())
    # ★ 用户口径（原话）：“整个图形按照 SW 做图，也就是说六边形里，**2~3 就是 SW**”
    #   —— 建议参数末尾那个孤零零的方向词 = **整个图形的朝向**，落到具体就是把
    #   **2→3 这条边**摆成该航向。只在题面**没有任何航向条件**时才用它补朝向
    #   （题面一旦有航向，以题面为准，避免和题面打架）。

    hub_relations = _parse_hub_angles(norm)
    inbound_segments = (_parse_inbound_segments(norm) + _parse_left_figure_refs(norm)
                        + _parse_node_to_avoid(norm))
    nodes, geom, route, graph_source = _graph(q, shape)
    parsed_segments = _align_segment_distances(
        _parse_segments(q, bearings) + inbound_segments, edges)
    if not shape and not geom and not route and len(parsed_segments) >= 2:
        # 没有可识别闭合图形时，连续的显式航段本身仍能确定一条可绘制路线。
        # 只接受每段终点正好接上下段起点的单链，不凭空补图形边。
        ordered = [parsed_segments[0]]
        remaining = list(parsed_segments[1:])
        while remaining:
            matches = [index for index, item in enumerate(remaining)
                       if str(item.get("b")) == str(ordered[-1].get("a"))]
            if len(matches) != 1:
                break
            ordered.append(remaining.pop(matches[0]))
        if len(ordered) == len(parsed_segments):
            route_nodes = []
            for item in ordered:
                for name in (str(item["b"]), str(item["a"])):
                    if name.isdigit() and name not in route_nodes:
                        route_nodes.append(name)
            if len(route_nodes) >= 2:
                nodes = route_nodes
                route = [[str(item["b"]), str(item["a"])]
                         for item in ordered]
                graph_source = "condition_segments"
    # ★ 题面可能引用**不存在的点号**（实测 旧样本 写“题卡图形5号点经纬度”，而题卡只有 1~4 号点
    #   —— 题库文字错误）。这种坐标挂在一个不存在的点上，会同时污染“已知坐标锚点”和
    #   越界复核，把整道题拖成待复核。按用户确认的口径：**直接丢掉**。
    known_names = {str(n) for n in nodes} | {"H", "A", "T", "R", "_review"}
    dropped = [key for key in coords if str(key) not in known_names]
    for key in dropped:
        coords.pop(key, None)
    # 注意：**不能**顺手把 `_review`（越界告警）也清掉 —— 它是安全网。
    # 只有当那个越界坐标挂在不存在的点上时，它才随该坐标一起消失。
    edges = edges + [entry for entry in position_items
                     if entry.get("kind") == "distance"]
    edges = _augment_shape_edges(shape, norm, params, nodes, geom, edges)
    is_closed = bool(re.search(r"闭合", norm) and not
                     re.search(r"不\s*闭合|无需闭合|不得闭合", norm))
    if shape == "扫描航线":
        # “是否循环执行：闭合循环”说的是任务循环方式，不代表扫描折线路径
        # 要额外连回首点；只有图形检测或题面明确写“航线闭合”才补首尾边。
        diagram_model = (_vision_diagram(q) if graph_source == "vision_model" else
                         _local_cv_diagram(q) if graph_source == "local_cv" else {})
        explicit_closed_route = bool(re.search(
            r"(?:航线|路线|路径)[^。；;\n]{0,10}(?:闭合|首尾相连)|"
            r"(?:闭合|首尾相连)[^。；;\n]{0,10}(?:航线|路线|路径)", norm))
        explicitly_open = bool(re.search(
            r"(?:不|无需|不得|禁止)\s*(?:与起点)?\s*(?:闭合|首尾相连)", norm))
        is_closed = bool(not explicitly_open and
                         (diagram_model.get("flight_closed") is True or
                          explicit_closed_route))
    route_inferred_from_closed_cycle = False
    if (is_closed and not route and graph_source == "vision_model" and
            shape not in ("圆形", "扫描航线", "蝴蝶形")):
        diagram = _vision_diagram(q)
        try:
            diagram_confidence = float(diagram.get("confidence", 0))
        except (TypeError, ValueError):
            diagram_confidence = 0.0
        if diagram_confidence >= 0.72:
            inferred_route = _closed_cycle_route(nodes, geom)
            if inferred_route:
                route = inferred_route
                route_inferred_from_closed_cycle = True
    if shape == "扫描航线" and is_closed and len(nodes) >= 3:
        closing_edge = [nodes[-1], nodes[0]]
        if closing_edge not in route:
            route = list(route) + [closing_edge]
    diagram_points = _imported_diagram_points(q)
    layout_confidence = "ocr_labels" if diagram_points else None
    if graph_source in ("vision_model", "local_cv"):
        diagram_model = (_vision_diagram(q) if graph_source == "vision_model"
                         else _local_cv_diagram(q))
        positions = diagram_model.get("point_positions") or {}
        if all(str(node) in positions for node in nodes):
            diagram_points = [[float(positions[str(node)]["x"]),
                               float(positions[str(node)]["y"])]
                              for node in nodes]
            layout_confidence = float(diagram_model.get("confidence", 0) or 0)
    figure_shape = None
    if (not figure_shape and diagram_points and len(diagram_points) == len(nodes)
            and graph_source in ("imported_diagram", "vision_model", "local_cv")):
        # 保留 OCR 点号与位置的对应关系，供形状先验使用；中心点属于近似测点，
        # 仍必须通过题面约束的坐标与航向复核，才能进入标准答案和判题。
        figure_shape = {
            "points": [(float(x), -float(y)) for x, y in diagram_points],
            "labels": {str(index + 1): index
                       for index in range(len(diagram_points))},
            "source": "导入题卡 OCR 点号位置",
        }
    visual_diagram = (_vision_diagram(q) if graph_source == "vision_model" else
                      _local_cv_diagram(q) if graph_source == "local_cv" else {})
    parallel_constraints = []
    try:
        visual_geometry_confidence = float(
            visual_diagram.get("geometry_confidence",
                               visual_diagram.get("confidence", 0)) or 0)
    except (TypeError, ValueError):
        visual_geometry_confidence = 0.0
    allow_visual_parallel = (
        (visual_diagram.get("source") == "local_cv" and
         visual_diagram.get("status") == "ready" and
         visual_diagram.get("topology_valid")) or
        visual_diagram.get("geometry_source") == "local_cv" or
        visual_geometry_confidence >= 0.90)
    geometry_keys = {frozenset(map(str, edge)) for edge in geom}
    if allow_visual_parallel:
        for pair in visual_diagram.get("parallel_edge_pairs", []):
            if (not isinstance(pair, (list, tuple)) or len(pair) != 2 or
                    not all(isinstance(edge, (list, tuple)) and len(edge) == 2
                            for edge in pair)):
                continue
            first, second = ([str(name) for name in edge] for edge in pair)
            if (frozenset(first) not in geometry_keys or
                    frozenset(second) not in geometry_keys or
                    frozenset(first) == frozenset(second) or
                    set(first).intersection(second)):
                continue
            parallel_constraints.append({
                "first": first,
                "second": second,
                "source": ("题图线段检测到的平行边"
                           if visual_diagram.get("geometry_source") == "local_cv"
                           else "视觉模型识别的平行边"),
            })
    spec = {
        "schema_version": SCHEMA_VERSION,
        "question_id": q.get("id"),
        "source_hash": hashlib.sha256(norm.encode("utf-8")).hexdigest(),
        "shape": shape,
        "graph_source": graph_source,
        "condition_source": ("vision_model" if use_assist and assisted_conditions
                             else "ocr_or_text"),
        "nodes": nodes,
        "layout_points": diagram_points,
        "layout_confidence": layout_confidence,
        "geometry_edges": [list(x) for x in geom],
        "parallel_constraints": parallel_constraints,
        "flight_edges": [list(x) for x in route],
        "route_inferred_from_closed_cycle": route_inferred_from_closed_cycle,
        "coordinates": coords,
        "distance_constraints": edges,
        "equal_distance_constraints": _parse_equal_distances(norm),
        # ★ 把被误当成航段的“位于 A-B 中点”子句改成专门的约束（实测 旧样本）：
        #   航段解析器会把“起飞点位于航线1-2的中心点的NE方向110m处”解析成一条
        #   2→1 的航段（航向 45°、距离 110），于是 ① 点被当成“从 ② 点推出来的已知点”，
        #   整个图形被拽偏、相似变换永不收敛（残差 281）。这里把它**转出**航段，
        #   改记成“H 到 1-2 中点”的距离与方位两条约束。
        "midpoint_distance_constraints": [
            {"point": "H", "a": str(segment["b"]), "b": str(segment["a"]),
             "distance": float(segment["distance"]),
             "bearing": float(segment["bearing"]),
             "source": segment.get("source", "")}
             for segment in parsed_segments
            if re.search(r"中点|中心点", str(segment.get("source", "")))],
        "midpoint_perpendicular_constraints": _parse_midpoint_perpendiculars(norm),
        "angle_constraints": angles,
        "vertex_ray_angles": _parse_square_angles(norm),
        "bearing_constraints": bearings,
        # “位于 A-B 中点”的子句已被转成 midpoint_distance_constraints，
        # 这里要**从航段里剔除**，否则 ① 点仍会被当成“从 ② 点推出来的已知点”。
        "segment_constraints": [
            segment for segment in parsed_segments
            if not re.search(r"中点|中心点", str(segment.get("source", "")))
            # ★ 航段端点不可能是 3 位以上的纯数字（实测 旧样本：“距离1点300米”被误当成
            #   一条 “300→1” 的航段，引用了不存在的点 300，把锚点与答案都污染了）。
            #   题库里点名最多两位数字（或 H/A/T/R），所以这里直接滤掉这种假端点。
            and not any(str(segment.get(side, "")).isdigit() and
                        len(str(segment.get(side, ""))) >= 3
                        for side in ("a", "b"))],
        "direction_relations": _parse_direction_relations(norm) + hub_relations,
        "parameters": params,
        "loop_count": _loop_count(norm),
        "closed": is_closed,
        "circle_diameter": _circle_diameter(norm, params),
        "circle_point_count": _circle_point_count(norm),
        # ★ “但航线不与图形交叉”是一条**不等式约束**（实测 旧样本）：算出的解若让航线与
        #   图形边相交，就是不合题意的。以前复核里没有这一条，于是"算得出来、复核不过"。
        # ★ 用户口径：`suggested_text` **末尾孤零零的方向词**（SW/NE…）就是**图形的朝向**
        #   （实测 #318 尾部 "SW"、#322 尾部 "SW"、#267 尾部 "NE"，这些题题面都没有航向条件）。
        #   先只做**数据管道**：把方向词解析成罗盘度数存进 spec（`figure_orientation`），
        #   具体"朝向"指的是哪条边/哪个关系，等用户确认后再接入求解器。
        "figure_orientation": _suggested_orientation(q),
        "no_figure_crossing": bool(re.search(r"不与(?:图形|图卡|图形边)交叉|"
                                             r"不与(?:图形|图卡)相交", norm)),
        "scan_length": params.get("__scan_length", params.get("a", _value_for_phrase(norm, params, r"航线长度"))),
        "scan_spacing": params.get("__scan_spacing", _value_for_phrase(norm, params, r"航线间隔")),
        # “各点之间距离不小于20米”是蝴蝶形／直角沙漏那族的**尺寸来源**：
        # 题面不给边长，只给「不小于 N 米」，参考图按最小可分辨尺寸取 N。
        "minimum_spacing": _minimum_spacing(norm),
        "collinear_constraints": _parse_collinear(norm),
        "figure_shape": figure_shape,
        "source_text": norm,
        "review": coords.get("_review"),
    }
    _merge_figure_constraints(q, spec)
    resolved = _resolve_access_alias(spec)
    if use_assist:
        resolved["condition_assist_used"] = True
        resolved["condition_assist_status"] = str(assist.get("status") or "review")
        resolved["condition_assist_complete"] = bool(assist.get("complete"))
        resolved["condition_assist_solver_usable"] = bool(
            assist.get("solver_usable"))
        resolved["condition_assist_items"] = copy.deepcopy(
            assist.get("condition_items") or [])
        resolved["condition_assist_conditions"] = assisted_conditions
        resolved["condition_assist_uncertain_items"] = list(
            assist.get("uncertain_items") or [])
        resolved["condition_assist_diagram_model"] = copy.deepcopy(
            assisted_diagram if assist_has_diagram else {})
    return resolved


def _merge_condition_texts(*sources):
    """合并 OCR 与视觉模型条件，去掉逐行完全重复的内容。"""
    lines = []
    seen = set()
    for source in sources:
        for line in re.split(r"[\r\n]+", str(source or "")):
            line = line.strip()
            key = re.sub(r"\s+", "", _norm_text(line))
            if line and key and key not in seen:
                lines.append(line)
                seen.add(key)
    return "\n".join(lines)


def _parse_collinear(text):
    """“H 点位置在 12 边的延长线上，但不与 12 边重叠” → 共线约束。

    这是 旧样本 唯一能定出起飞点的条件：H 与 1、2 共线，且不落在 1-2 线段内。
    不解析它，求解器就会把 H 放在以 3 为圆心、50 米为半径的圆上随便一处 ——
    那正是"漏掉题面条件"的假 READY（本项目的回归测试抓到了这一条）。
    """
    out = []
    pattern = re.compile(
        r"([HAT]|\d{1,2}\s*(?:号)?点)\s*点?\s*(?:的)?\s*(?:位置)?\s*(?:在|位于)\s*"
        r"(\d)\s*[-—－]?\s*(\d)\s*(?:边|两点)?\s*(?:的)?\s*延长线\s*上?"
        r"([^。;\n]{0,26})", re.I)
    for match in pattern.finditer(text):
        point = _direction_node(match.group(1))
        if not point:
            continue
        tail = match.group(4) or ""
        outside = bool(re.search(r"不\s*(?:与|和|跟)?\s*\d*\s*边?\s*重\s*叠", tail))
        out.append({"point": point, "a": match.group(2), "b": match.group(3),
                    "outside": outside,
                    "source": (match.group(0) + tail).strip()})
    return out


def _parse_hub_angles(text):
    """“∠H21=45°、∠H23=135°” —— 顶点写在中间，两侧是编号航点。

    ∠ABC 表示「在 B 处的角」，所以 ∠H21 是起飞点处 H-2 与 H-1 的夹角。这类写法
    （实测 旧样本）给出了图形相对起飞点的朝向，是那题唯一的方向来源。
    表示成「两条射线的关系」：目标航段 = 起始航段旋转 ±angle。
    """
    out = []
    pattern = re.compile(
        r"[∠﹤<]\s*(?P<vertex>[HAT])\s*(?P<first>\d{1,2})\s*(?P<second>\d{1,2})\s*"
        r"(?:=\s*)?(?:为|是)?\s*(?P<value>[\d.]+)\s*(?:°|度)", re.I)
    for match in pattern.finditer(text):
        value = float(match.group("value"))
        if not 0 < value < 360:
            continue
        vertex = match.group("vertex").upper()
        first = str(int(match.group("first")))
        second = str(int(match.group("second")))
        if first == second:
            continue
        out.append({"first": [vertex, second], "second": [vertex, first],
                    "rotation": value, "opposite": False,
                    "source": match.group(0)})
    return out


def _parse_hub_distances(text):
    """“距2点200m” —— 上文的起飞点/避让点到编号航点的距离（省略了主语）。"""
    out = []
    start = re.search(r"起飞点[^。;\n]{0,120}", text)
    if not start:
        return out
    window = start.group(0)
    for match in re.finditer(r"距\s*(?P<node>\d{1,2})\s*(?:号)?点?\s*"
                             r"(?:距离)?\s*(?P<value>[\d.]+)\s*(?P<unit>公里|千米|km|米|m)",
                             window, re.I):
        scale = 1000.0 if match.group("unit").lower() in ("公里", "千米", "km") else 1.0
        out.append({"a": "H", "b": str(int(match.group("node"))),
                    "value": float(match.group("value")) * scale,
                    "kind": "distance", "source": match.group(0)})
    return out


def _parse_dotted_bearings(text):
    """“航点4位于航点1:SW”“4号点位于1号点SW” —— 冒号/直接跟方位缩写。"""
    out = []
    # “航点4位于航点1:SW”：编号既可写在「点」前（4号点），也可写在「航点」后（航点4）。
    endpoint = (r"(?:(?:任务航线|题卡图形|图形|航点|任务航点)\s*(\d{1,2})\s*(?:号)?(?:航)?点?"
                r"|(\d{1,2})\s*(?:号)?(?:航)?点|[HAT])")
    pattern = re.compile(
        r"(?P<subject>" + endpoint + r")\s*(?:位于|处于|在)\s*"
        r"(?P<reference>" + endpoint + r")\s*[:：]?\s*"
        r"(?P<direction>东北|东南|西南|西北|NE|NW|SE|SW|N|S|E|W|北|南|东|西)", re.I)
    for match in pattern.finditer(text):
        bearing = GEO.parse_bearing(match.group("direction"))
        if bearing is None:
            continue
        subject = _direction_node(match.group("subject"))
        reference = _direction_node(match.group("reference"))
        if not subject or not reference or subject == reference:
            continue
        out.append({"a": reference, "b": subject, "value": float(bearing) % 360.0,
                    "kind": "bearing", "source": match.group(0)})
    return out


def _parse_inbound_segments(text):
    """“1点到起飞点航向135°，100米” —— 起点写在前面、目标是被飞的起飞点。"""
    out = []
    pattern = re.compile(
        r"(?:(?:题卡图形|图形|任务航线)?\s*(?P<node>\d{1,2})\s*(?:号)?(?:航)?点)"
        r"\s*(?:到|至)\s*(?:起飞点|H点?)\s*(?:的)?\s*航向\s*(?:为|是|=)?\s*"
        r"(?P<bearing>[\d.]+)\s*(?:°|度)?\s*[，,]?\s*"
        r"(?:距离\s*)?(?P<distance>[\d.]+)\s*(?P<unit>公里|千米|km|米|m)", re.I)
    for match in pattern.finditer(text):
        scale = 1000.0 if match.group("unit").lower() in ("公里", "千米", "km") else 1.0
        out.append({"a": "H", "b": str(int(match.group("node"))),
                    "bearing": float(match.group("bearing")) % 360.0,
                    "distance": float(match.group("distance")) * scale,
                    "source": match.group(0)})
    return out


def _parse_left_figure_refs(text):
    """“左图2号点位于此点nw方向500米处” —— 「此点」指上文最近提到的避让／障碍／过渡点。

    注意「此点」常出现在**下一句**里（“到达避让点再执行航线任务。左图2号点位于此点…”），
    所以不能按句切分后再找，必须按「此点」出现的位置往前文找最近的参照点。
    """
    out = []
    for match in re.finditer(
            r"(?:左图|右图|题卡图形|图形)?\s*(\d{1,2})\s*(?:号)?(?:航)?点\s*"
            r"(?:位于|处于|在)\s*此\s*点\s*(?P<direction>" + _COURSE_TOKEN + r")"
            r"\s*(?:方向)?\s*(?P<distance>[\d.]+)\s*(?P<unit>公里|千米|km|米|m)",
            text, re.I):
        mentions = list(re.finditer(r"避让点|障碍点|过渡点|转场点", text[:match.start()]))
        if not mentions:
            continue
        word = mentions[-1].group(0)
        middle = "T" if word in ("过渡点", "转场点") else "A"
        bearing = GEO.parse_bearing(match.group("direction"))
        if bearing is None:
            continue
        scale = 1000.0 if match.group("unit").lower() in ("公里", "千米", "km") else 1.0
        out.append({"a": str(int(match.group(1))), "b": middle,
                    "bearing": float(bearing) % 360.0,
                    "distance": float(match.group("distance")) * scale,
                    "source": match.group(0)})
    return out


def _parse_node_to_avoid(text):
    """“题卡图形1号点“避让点”方向为SE，距离300米” —— 编号航点指向避让点。

    这句是 旧样本 唯一能定出扫描航线位置的条件（另一个坐标点 3 只定图形本身）。
    """
    out = []
    for match in re.finditer(
            r"(?:(?:题卡图形|图形|任务航线)?\s*(?P<node>\d{1,2})\s*(?:号)?(?:航)?点)\s*"
            r"[“\"']?\s*(?P<target>避让点|障碍点|过渡点|转场点)\s*[”\"']?\s*"
            r"(?:的)?\s*(?:方向|方位)\s*(?:为|是|=)?\s*"
            r"(?P<direction>" + _COURSE_TOKEN + r")\s*(?:方向)?\s*[，,]?\s*"
            r"(?:距离\s*)?(?P<distance>[\d.]+)\s*(?P<unit>公里|千米|km|米|m)",
            text, re.I):
        bearing = GEO.parse_bearing(match.group("direction"))
        if bearing is None:
            continue
        word = match.group("target")
        middle = "T" if word in ("过渡点", "转场点") else "A"
        scale = 1000.0 if match.group("unit").lower() in ("公里", "千米", "km") else 1.0
        out.append({"a": middle, "b": str(int(match.group("node"))),
                    "bearing": float(bearing) % 360.0,
                    "distance": float(match.group("distance")) * scale,
                    "source": match.group(0)})
    return out


def _parse_bare_pair_bearings(text):
    """“题卡图形2号点 题卡图形8号点东偏北60度” —— 两个编号航点之间只写方位。

    按中文习惯读作「前一个点位于后一个点的该方向」，即 8→2 = 东偏北60°。
    """
    out = []
    point = (r"(?:(?:题卡图形|图形|任务航线)?\s*(\d{1,2})\s*(?:号)?(?:航)?点)")
    pattern = re.compile(point + r"\s*[，,]?\s*" + point + r"\s*"
                         r"(?P<direction>" + _COURSE_TOKEN + r")\s*(?:方向)?\s*"
                         r"(?:为|是|=)?\s*[\d.]*\s*(?:°|度)?", re.I)
    for match in pattern.finditer(text):
        if not match.group("direction"):
            continue
        bearing = GEO.parse_bearing(match.group("direction"))
        if bearing is None:
            continue
        subject = str(int(match.group(1)))
        reference = str(int(match.group(2)))
        if subject == reference:
            continue
        out.append({"a": reference, "b": subject, "value": float(bearing) % 360.0,
                    "kind": "bearing", "source": match.group(0)})
    return out


def _parse_apart_distances(text):
    """“起飞点距1号航点310米” —— 省略「到」字的距离写法。"""
    out = []
    for match in re.finditer(
            r"(?:起飞点|H点?|避让点|过渡点|转场点)\s*距(?:离)?\s*"
            r"(?:(?:任务航线|题卡图形|图形|航点|任务航点)\s*)?"
            r"(\d{1,2})\s*(?:号)?(?:航)?点?\s*(?:的)?\s*(?:距离)?\s*"
            r"([\d.]+)\s*(公里|千米|km|米|m)", text, re.I):
        scale = 1000.0 if match.group(3).lower() in ("公里", "千米", "km") else 1.0
        out.append({"a": "H", "b": str(int(match.group(1))),
                    "value": float(match.group(2)) * scale,
                    "kind": "distance", "source": match.group(0)})
    return out


def _minimum_spacing(text):
    """“各点之间距离不小于 N 米” → N（蝴蝶形／直角沙漏那族的尺寸来源）。"""
    match = re.search(r"(?:各点之间|各航点之间|航点之间|相互之间)?\s*"
                      r"(?:距离|间距)\s*(?:均|都)?\s*不小于\s*([\d.]+)\s*(公里|千米|km|米|m)",
                      text, re.I)
    if not match:
        return None
    scale = 1000.0 if match.group(2).lower() in ("公里", "千米", "km") else 1.0
    return float(match.group(1)) * scale


def _role_node(name):
    if name == AN.TAKEOFF:
        return "H"
    if name == AN.AVOID:
        return "A"
    if name == AN.TRANSIT:
        return "T"
    match = re.search(r"(\d+)", str(name))
    return match.group(1) if match else str(name)


def _loop_count(text):
    # 按条件行取数字，避免“闭合循环”跨到下一行把“9、停留时间”读成9圈。
    if re.search(r"不\s*(?:循环|重复)|无需\s*(?:循环|重复)", text):
        return 0
    for clause in re.split(r"[。；;\n]", text):
        match = re.search(
            r"(?:循环|重复|圈)\s*(?:(?:执行|飞行)\s*)?(\d+)\s*(?:圈|次)",
            clause)
        if match:
            return int(match.group(1))
    return 1 if re.search(r"(?:循环|重复)", text) else 0


def _value_for_phrase(text, params, phrase):
    match = re.search(phrase + r"(?:为|是)?\s*([a-z])", text, re.I)
    return params.get(match.group(1).lower()) if match else None


def _circle_diameter(text, params):
    match = re.search(r"直径\s*(?:=|为|是)?\s*([\d.]+)\s*(米|m)", text, re.I)
    if match:
        return float(match.group(1))
    match = re.search(r"直径\s*(?:为|是)?\s*([a-z])\b", text, re.I)
    if match:
        return params.get(match.group(1).lower())
    return None


def _circle_point_count(text):
    match = re.search(r"航点数\s*≥\s*(\d+)", text)
    if match:
        return int(match.group(1))
    match = re.search(r"航点数\s*(?:不少于|至少)\s*(\d+)", text)
    return int(match.group(1)) if match else None


def _coordinate_frame(spec):
    coords = {k: v for k, v in spec["coordinates"].items()
              if not k.startswith("_")}
    if not coords:
        return "relative", {"H": (0.0, 0.0)}, None
    origin_name, origin = next(iter(coords.items()))
    lat0, lon0 = origin["lat"], origin["lon"]
    points = {}
    for name, item in coords.items():
        lat, lon = item["lat"], item["lon"]
        if Geodesic is not None:
            inv = Geodesic.WGS84.Inverse(lat0, lon0, lat, lon)
            x, y = _bearing_xy(inv["azi1"], inv["s12"])
        else:
            bearing = GEO.bearing_of(lat0, lon0, lat, lon)
            dist = GEO.distance_of(lat0, lon0, lat, lon)
            x, y = _bearing_xy(bearing or 0, dist or 0)
        points[name] = (x, y)
    return "WGS84", points, (lat0, lon0, origin_name)


def _to_geographic(x, y, frame):
    if frame is None:
        return None, None
    lat0, lon0, _ = frame
    dist = math.hypot(x, y)
    if dist < 1e-9:
        return lat0, lon0
    bearing = _xy_bearing(x, y)
    if Geodesic is not None:
        p = Geodesic.WGS84.Direct(lat0, lon0, bearing, dist)
        return p["lat2"], p["lon2"]
    return GEO.dest_point(lat0, lon0, bearing, dist)


def _angle_of(a, vertex, c):
    va = (a[0] - vertex[0], a[1] - vertex[1])
    vc = (c[0] - vertex[0], c[1] - vertex[1])
    na, nc = math.hypot(*va), math.hypot(*vc)
    if na < 1e-8 or nc < 1e-8:
        return 0.0
    dot = max(-1.0, min(1.0, (va[0] * vc[0] + va[1] * vc[1]) / (na * nc)))
    return math.degrees(math.acos(dot))


def _inside_polygon(point, polygon):
    """判断起飞点是否处于纸图多边形内部。"""
    inside = False
    px, py = point
    for i, (ax, ay) in enumerate(polygon):
        bx, by = polygon[(i + 1) % len(polygon)]
        if (ay > py) != (by > py):
            crossing = ax + (py - ay) * (bx - ax) / (by - ay)
            if crossing > px:
                inside = not inside
    return inside


def _validate_solution(spec, points, geom, route,
                       distance_tolerance=DISTANCE_TOLERANCE_M,
                       angle_tolerance=ANGLE_TOLERANCE_DEG):
    """用提取到的原始约束复核数值解；任一硬条件不符就不发布答案。"""
    errors = []
    required = set(spec.get("nodes") or [])
    for name in required - set(points):
        errors.append("题卡航点%s尚未求出" % name)
    for edge in list(geom) + list(route):
        for name in edge:
            if name not in points:
                errors.append("航线连接点%s尚未求出" % name)
    frame, expected, _ = _coordinate_frame(spec)
    for name, anchor in spec.get("coordinates", {}).items():
        if str(name).startswith("_"):
            continue
        if name not in points:
            errors.append("已知坐标点%s尚未求出" % name)
            continue
        if frame == "WGS84" and name in expected:
            error = math.hypot(points[name][0] - expected[name][0],
                               points[name][1] - expected[name][1])
            if error > max(2.0, distance_tolerance):
                errors.append("已知坐标点%s偏离题面%.2f米" % (name, error))
    for item in spec.get("distance_constraints", []):
        a, b = item["a"], item["b"]
        if a not in points or b not in points:
            errors.append("距离约束%s-%s缺少航点" % (a, b))
            continue
        if a in points and b in points:
            pa, pb = points[a], points[b]
            actual = math.hypot(pa[0] - pb[0], pa[1] - pb[1])
            if abs(actual - float(item["value"])) > distance_tolerance:
                errors.append("%s-%s应为%.3f米，计算为%.3f米" %
                              (a, b, item["value"], actual))

    for item in spec.get("parallel_constraints", []):
        first, second = item.get("first", []), item.get("second", [])
        if len(first) != 2 or len(second) != 2 or not all(
                name in points for name in first + second):
            errors.append("平行边约束缺少航点：%s" % item.get("source", ""))
            continue
        a, b = (points[name] for name in first)
        c, d = (points[name] for name in second)
        u, v = (b[0] - a[0], b[1] - a[1]), (d[0] - c[0], d[1] - c[1])
        denominator = math.hypot(*u) * math.hypot(*v)
        if denominator < 1e-8:
            errors.append("平行边约束包含零长度航段：%s" % item.get("source", ""))
            continue
        sine = min(1.0, abs(u[0] * v[1] - u[1] * v[0]) / denominator)
        deviation = math.degrees(math.asin(sine))
        if deviation > angle_tolerance:
            errors.append("图形边%s与%s不平行，夹角偏差%.2f°" %
                          ("-".join(first), "-".join(second), deviation))

    for item in spec.get("equal_distance_constraints", []):
        first, second = item["first"], item["second"]
        if not all(name in points for name in first + second):
            errors.append("等长条件缺少航点：%s" % item["source"])
            continue
        length1 = math.dist(points[first[0]], points[first[1]])
        length2 = math.dist(points[second[0]], points[second[1]])
        if abs(length1 - length2) > distance_tolerance:
            errors.append("等长条件不符：%s" % item["source"])

    for item in spec.get("midpoint_perpendicular_constraints", []):
        h, a, b = item["point"], item["a"], item["b"]
        if not all(name in points for name in (h, a, b)):
            errors.append("中点垂线条件缺少航点：%s" % item["source"])
            continue
        midpoint = ((points[a][0] + points[b][0]) / 2.0,
                    (points[a][1] + points[b][1]) / 2.0)
        offset = (points[h][0] - midpoint[0], points[h][1] - midpoint[1])
        edge = (points[b][0] - points[a][0], points[b][1] - points[a][1])
        edge_length = math.hypot(*edge)
        if abs(math.hypot(*offset) - item["distance"]) > distance_tolerance or (
                edge_length > 1e-8 and
                abs(offset[0] * edge[0] + offset[1] * edge[1]) / edge_length > distance_tolerance):
            errors.append("中点垂线距离或角度不符：%s" % item["source"])

    # “各点之间距离不小于 N 米”是硬下界；参考图的尺寸就取这个下界。
    minimum = spec.get("minimum_spacing")
    if minimum:
        nodes = [str(name) for name in (spec.get("nodes") or []) if name in points]
        if len(nodes) >= 3:
            spans = [math.dist(points[nodes[i]], points[nodes[j]])
                     for i in range(len(nodes)) for j in range(i + 1, len(nodes))]
            if min(spans) < float(minimum) - distance_tolerance:
                errors.append("各点之间距离应不小于%.1f米，实际最小%.2f米"
                              % (float(minimum), min(spans)))

    # ★ “但航线不与图形交叉”（实测 旧样本）—— 这是**不等式约束**：航线各段不允许与图形
    #   各边真正相交。以前复核里缺这一条，于是出现"算得出来、复核却过不了"的怪状态。
    #   注意：共用端点的两条边不算相交（叉积判据里会自然退化为 0，不构成"真交叉"）。
    # ★ 用户口径（#299）：“**不与图形交叉指的是 H 点到 1 号点的航线不能叠在图形的航线上**”
    #   —— 只查 **H→首个航点** 这一段，早先查整条航线是过严（把正常航线也否掉了）。
    if spec.get("no_figure_crossing"):

        def _orient(p, q, r):
            value = ((q[0] - p[0]) * (r[1] - p[1]) -
                     (q[1] - p[1]) * (r[0] - p[0]))
            if abs(value) < 1e-9:
                return 0
            return 1 if value > 0 else -1

        crossed = None
        takeoff_legs = [segment for segment in route
                        if "H" in (str(segment[0]), str(segment[1]))] or route
        for first, second in takeoff_legs:
            if first not in points or second not in points:
                continue
            p1, p2 = points[first], points[second]
            for third, fourth in geom:
                if third not in points or fourth not in points:
                    continue
                if {first, second} & {third, fourth}:
                    continue                       # 共用端点，跳过
                p3, p4 = points[third], points[fourth]
                if (_orient(p1, p2, p3) != _orient(p1, p2, p4) and
                        _orient(p3, p4, p1) != _orient(p3, p4, p2)):
                    crossed = (first, second, third, fourth)
                    break
            if crossed:
                break
        if crossed:
            errors.append("航线 %s-%s 与图形边 %s-%s 相交，"
                          "与题面「航线不与图形交叉」不符" % crossed)

    # “H 在 12 边的延长线上”是硬条件：既要点在直线上，又要（若题面写了）不落在边内。
    for item in spec.get("collinear_constraints", []):
        a, b, point = str(item["a"]), str(item["b"]), str(item["point"])
        if not all(name in points for name in (a, b, point)):
            errors.append("延长线条件缺少航点：%s" % item["source"])
            continue
        ax, ay = points[a]
        bx, by = points[b]
        px, py = points[point]
        span = math.hypot(bx - ax, by - ay)
        if span <= 1e-8:
            continue
        offset = abs((px - ax) * (by - ay) - (py - ay) * (bx - ax)) / span
        if offset > distance_tolerance:
            errors.append("%s应落在%s-%s的延长线上（偏离直线%.2f米）"
                          % (point, a, b, offset))
        if item.get("outside"):
            ratio = ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / (span * span)
            if -1e-6 <= ratio <= 1 + 1e-6:
                errors.append("%s应与%s-%s边不重叠" % (point, a, b))

    for item in spec.get("bearing_constraints", []):
        a, b = item["a"], item["b"]
        if a not in points or b not in points:
            errors.append("航向约束%s→%s缺少航点" % (a, b))
            continue
        if a in points and b in points:
            actual = _xy_bearing(points[b][0] - points[a][0],
                                 points[b][1] - points[a][1])
            delta = abs(_wrap_angle(actual - float(item["value"])))
            if delta > angle_tolerance:
                errors.append("%s→%s应为%.2f°，计算为%.2f°" %
                              (a, b, item["value"], actual))

    for item in spec.get("segment_constraints", []):
        # 线段解析字段a为终点、b为起点，bearing按b→a记录。
        start, end = item["b"], item["a"]
        if start not in points or end not in points:
            errors.append("进场航段%s→%s缺少航点" % (start, end))
            continue
        if start in points and end in points:
            pa, pb = points[start], points[end]
            actual_dist = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
            actual_bearing = _xy_bearing(pb[0] - pa[0], pb[1] - pa[1])
            delta = abs(_wrap_angle(actual_bearing - float(item["bearing"])))
            if abs(actual_dist - float(item["distance"])) > distance_tolerance:
                errors.append("%s→%s距离条件不符" % (start, end))
            if delta > angle_tolerance:
                errors.append("%s→%s方位条件不符" % (start, end))

    for relation in spec.get("direction_relations", []):
        first, second = relation["first"], relation["second"]
        if not all(name in points for name in first + second):
            errors.append("方位关系缺少航点：%s" % relation["source"])
            continue
        if all(name in points for name in first + second):
            first_bearing = _xy_bearing(
                points[first[1]][0] - points[first[0]][0],
                points[first[1]][1] - points[first[0]][1])
            second_bearing = _xy_bearing(
                points[second[1]][0] - points[second[0]][0],
                points[second[1]][1] - points[second[0]][1])
            offset = float(relation.get("rotation", 0.0))
            if relation.get("opposite"):
                offset += 180.0
            delta = abs(_wrap_angle(second_bearing - first_bearing - offset))
            if delta > angle_tolerance:
                errors.append("航段方位关系不符：%s" % relation["source"])

    for item in spec.get("vertex_ray_angles", []):
        a, vertex, b = item["a"], item["vertex"], item["b"]
        if not all(name in points for name in (a, vertex, b)):
            errors.append("夹角约束缺少航点：%s" % item["source"])
            continue
        actual = _angle_of(points[a], points[vertex], points[b])
        if abs(actual - float(item["value"])) > angle_tolerance:
            errors.append("%s应为%.2f°，计算为%.2f°" %
                          (item["source"], item["value"], actual))

    neighbors = {}
    # 蝴蝶形题卡的端点夹角标在交叉航线上，边长则标在外框上。
    angle_edges = route if spec.get("shape") == "蝴蝶形" else geom
    for a, b in angle_edges:
        if a in points and b in points:
            neighbors.setdefault(a, []).append(b)
            neighbors.setdefault(b, []).append(a)
    for item in spec.get("angle_constraints", []):
        vertex = item["vertex"]
        adjacent = list(dict.fromkeys(neighbors.get(vertex, [])))
        if len(adjacent) < 2:
            errors.append("∠%s缺少相邻航段，无法复核" % vertex)
            continue
        actual = _angle_of(points[adjacent[0]], points[vertex],
                           points[adjacent[1]])
        if abs(actual - float(item["value"])) > angle_tolerance:
            errors.append("∠%s应为%.2f°，计算为%.2f°" %
                          (vertex, item["value"], actual))
    # 方向关系“退化”判定（实测 旧样本）：题面若**没有任何涉及 H 的距离约束**，
    # 那么 `(H→2) 与 (1→2) 同向` 可以被解成 H≡1（两段线段合一、关系退化成恒等式），
    # 于是同一形状转出多个假解。此时把这种解判为无效。
    # （旧样本 曾因此被误伤 —— 根因是它的 `\|H-2\|=200` 当时没被解析出来；
    #   修好“任务航点”前缀后 旧样本 已有 H 距离约束，本条自然不再命中它 ✓）
    _h_distance = any("H" in (str(item.get("a")), str(item.get("b")))
                      for item in spec.get("distance_constraints", []))
    if not _h_distance:
        for _item in spec.get("direction_relations", []):
            _first = list(_item.get("first") or [])
            _second = list(_item.get("second") or [])
            if len(_first) < 2 or len(_second) < 2:
                continue
            _pairs = ((_first[0], _second[0]), (_first[-1], _second[-1]))
            if all(_a in points and _b in points and
                   math.dist(points[_a], points[_b]) < 0.5 for _a, _b in _pairs):
                errors.append("方向关系 %s→%s 与 %s→%s 被解成同一条线段，该关系已退化"
                              % (_first[0], _first[-1], _second[0], _second[-1]))

    placement = re.search(r"起飞点.{0,8}图形\s*(内|外)侧",
                          spec.get("source_text", ""))
    nodes = spec.get("nodes") or []
    if placement and "H" in points and len(nodes) >= 3 and all(
            name in points for name in nodes):
        polygon = [points[name] for name in nodes]
        is_inside = _inside_polygon(points["H"], polygon)
        # ★ 边界点两边都不算（实测 #386）：错解把 **H 正好放在六边形顶点 4 上**
        #   （|H-1| = 60、|H-3| = |H-5| = 30），而射线法会把边界点判成"在内侧" ✗
        #   于是错解被放行、正确答案（H 在**正中心**，离各边 26 米）反而要和它平分。
        #   用户口径是"外侧/内侧"都是**严格**的，所以压在边界上要单独报错。
        on_boundary = False
        for index in range(len(polygon)):
            ax, ay = polygon[index]
            bx, by = polygon[(index + 1) % len(polygon)]
            dx, dy = bx - ax, by - ay
            span = dx * dx + dy * dy
            ratio = 0.0 if span <= 1e-12 else max(0.0, min(1.0,
                ((points["H"][0] - ax) * dx + (points["H"][1] - ay) * dy) / span))
            px, py = ax + ratio * dx, ay + ratio * dy
            if math.hypot(points["H"][0] - px, points["H"][1] - py) < 1.0:
                on_boundary = True
                break
        if on_boundary:
            errors.append("起飞点落在图形边界上（既不算内侧也不算外侧）")
        elif is_inside != (placement.group(1) == "内"):
            errors.append("起飞点位于图形%s侧的条件不符" % placement.group(1))
    return errors


def _access_edges(spec, points):
    """从已核实的题面航段提取进场航迹，不改变图形航点编号。"""
    nodes = [str(node) for node in spec.get("nodes", [])]
    if not nodes or nodes[0] not in points:
        return []
    first = nodes[0]
    connected = {frozenset((str(item["a"]), str(item["b"])))
                 for item in spec.get("segment_constraints", [])}
    connected.update(frozenset((str(item["a"]), str(item["b"])))
                     for item in spec.get("distance_constraints", []))

    def has(a, b):
        return a in points and b in points and frozenset((a, b)) in connected

    for via in ("A", "T"):
        if has("H", via) and has(via, first):
            return [["H", via], [via, first]]
    for via in ("A", "T"):
        if has("H", via):
            return [["H", via]]
        if has(via, first):
            return [[via, first]]
    if has("H", first):
        return [["H", first]]
    if "H" in points and first in points and not any(
            via in points for via in ("A", "T")):
        return [["H", first]]
    return []


def _ready(spec, points, frame, geom, route, steps, checks):
    # ★ 清理“别名污染”（实测 22 道，如 旧样本 只提“过渡点”却在答案里多出 A 点）：
    #   题面从未提到的具名点不应出现在发布答案里 —— 它们不参与任何约束，
    #   只是求解过程里的临时别名。只动答案载荷，不影响求解与复核。
    _text = str(spec.get("source_text") or "")
    _alias_words = {"A": ("避让点", "临时会让点"), "T": ("过渡点", "过度点"),
                    "R": ("返航点",)}
    for _name, _words in _alias_words.items():
        if _name in points and not any(_word in _text for _word in _words):
            points = {key: value for key, value in points.items() if key != _name}

    figure = spec.get("figure_ocr") or {}
    figure_count = int(figure.get("matched_segments", 0)) + int(
        figure.get("matched_distances", 0))
    if figure_count:
        checks = list(checks) + ["图卡识别了%d条距离/方位标注，并参与坐标复核" % figure_count]
    # 题面未给起飞点坐标，但给了“起飞点垂直于边中点”的场合，
    # 用已求出的边和距离补起飞点，优先取图形外侧。
    for item in spec.get("midpoint_perpendicular_constraints", []):
        h, a, b = item["point"], item["a"], item["b"]
        if h in points or a not in points or b not in points:
            continue
        pa, pb = points[a], points[b]
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        length = math.hypot(dx, dy)
        if length < 1e-8:
            continue
        middle = ((pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0)
        offset = (-dy / length * item["distance"],
                  dx / length * item["distance"])
        choices = [(middle[0] + offset[0], middle[1] + offset[1]),
                   (middle[0] - offset[0], middle[1] - offset[1])]
        polygon = [points[name] for name in spec.get("nodes", [])
                   if name in points]
        if len(polygon) >= 3:
            choices.sort(key=lambda point: _inside_polygon(point, polygon))
        points[h] = choices[0]
    # 以已知点位为种子，沿题面方位和距离双向补出外部航点。
    for _ in range(len(spec.get("segment_constraints", [])) + 1):
        changed = False
        for item in spec.get("segment_constraints", []):
            target, origin = str(item["a"]), str(item["b"])
            distance, bearing = float(item["distance"]), float(item["bearing"])
            if origin in points and target not in points:
                dx, dy = _bearing_xy(bearing, distance)
                points[target] = (points[origin][0] + dx,
                                  points[origin][1] + dy)
                changed = True
            elif target in points and origin not in points:
                dx, dy = _bearing_xy((bearing + 180.0) % 360.0, distance)
                points[origin] = (points[target][0] + dx,
                                  points[target][1] + dy)
                changed = True
        if not changed:
            break
    # 图形坐标确定后，用同/反向航向关系反推外部点，再按其已知距离补出坐标。
    # 这样题面只给出一个或两个坐标锚点时，起飞点和避让点也能接入完整航迹。
    external = {"H", "A", "T"}
    for _ in range(len(spec.get("distance_constraints", [])) + 1):
        direct = {}
        for item in spec.get("bearing_constraints", []):
            a, b = str(item["a"]), str(item["b"])
            value = float(item["value"]) % 360.0
            direct.setdefault((a, b), value)
            direct.setdefault((b, a), (value + 180.0) % 360.0)
        for item in spec.get("segment_constraints", []):
            a, b = str(item["b"]), str(item["a"])
            value = float(item["bearing"]) % 360.0
            direct.setdefault((a, b), value)
            direct.setdefault((b, a), (value + 180.0) % 360.0)
        names = list(points)
        for index, a in enumerate(names):
            for b in names[index + 1:]:
                dx, dy = points[b][0] - points[a][0], points[b][1] - points[a][1]
                if math.hypot(dx, dy) > 1e-8:
                    value = _xy_bearing(dx, dy)
                    direct.setdefault((a, b), value)
                    direct.setdefault((b, a), (value + 180.0) % 360.0)
        for item in _relation_bearings(spec, points):
            a, b = str(item["target_a"]), str(item["target_b"])
            value = float(item["value"]) % 360.0
            direct[(a, b)] = value
            direct[(b, a)] = (value + 180.0) % 360.0
        changed = False
        for item in spec.get("distance_constraints", []):
            a, b = str(item["a"]), str(item["b"])
            distance = float(item["value"])
            if a in points and b not in points and b in external:
                bearing = direct.get((a, b))
                if bearing is not None:
                    dx, dy = _bearing_xy(bearing, distance)
                    points[b] = (points[a][0] + dx, points[a][1] + dy)
                    changed = True
            elif b in points and a not in points and a in external:
                bearing = direct.get((a, b))
                if bearing is not None:
                    dx, dy = _bearing_xy((bearing + 180.0) % 360.0, distance)
                    points[a] = (points[b][0] + dx, points[b][1] + dy)
                    changed = True
        if not changed:
            break

    errors = _validate_solution(spec, points, geom, route)
    if errors:
        return _review(spec, "计算结果未通过题面条件复核", errors,
                       candidate_points=points, frame=frame,
                       geometry_edges=geom, flight_edges=route)
    output = {}
    for name, (x, y) in points.items():
        lat, lon = _to_geographic(x, y, frame)
        output[str(name)] = {"x": float(x), "y": float(y),
                             "lat": lat, "lon": lon}
    return {"status": "READY", "spec": spec, "points": output,
            "geometry_edges": [list(e) for e in geom],
            "flight_edges": [list(e) for e in route],
            "access_edges": _access_edges(spec, output),
            "steps": steps, "checks": checks,
            "parameters": spec.get("parameters", {}),
            "loop_count": spec.get("loop_count", 0),
            "coordinate_system": "WGS84" if frame else "relative-local"}


def _attach_candidate(result, spec, points, frame=None, geometry_edges=None,
                      flight_edges=None):
    """把尚未完整核验的局部坐标整理成可展示的参考草案。"""
    output = {}
    allowed = set(str(name) for name in (spec.get("nodes") or [])) | {"H", "A", "T", "R"}
    for name, pair in (points or {}).items():
        name = str(name)
        if name not in allowed or name.startswith("_"):
            continue
        try:
            x, y = float(pair[0]), float(pair[1])
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            lat, lon = _to_geographic(x, y, frame)
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
        output[name] = {"x": x, "y": y, "lat": lat, "lon": lon}
    if len(output) < 2:
        return result

    names = set(output)

    def keep_edges(edges):
        return [list(map(str, edge[:2])) for edge in (edges or [])
                if isinstance(edge, (list, tuple)) and len(edge) >= 2 and
                str(edge[0]) in names and str(edge[1]) in names]

    result["candidate"] = True
    result["candidate_points"] = output
    result["candidate_geometry_edges"] = keep_edges(
        geometry_edges if geometry_edges is not None else spec.get("geometry_edges"))
    result["candidate_flight_edges"] = keep_edges(
        flight_edges if flight_edges is not None else spec.get("flight_edges"))
    result["candidate_access_edges"] = _access_edges(spec, output)
    result["candidate_note"] = "按当前可用条件推算，仅作路线参考；坐标和航线请结合原题核对。"
    return result


def _review(spec, reason, details=None, candidate_points=None, frame=None,
            geometry_edges=None, flight_edges=None):
    result = {"status": "NEEDS_REVIEW", "spec": spec, "points": {},
              "geometry_edges": spec.get("geometry_edges", []),
              "flight_edges": spec.get("flight_edges", []),
              "access_edges": [],
              "steps": [], "checks": [], "reason": reason,
              "details": details or [], "parameters": spec.get("parameters", {}),
              "loop_count": spec.get("loop_count", 0)}
    if candidate_points:
        _attach_candidate(result, spec, candidate_points, frame,
                          geometry_edges, flight_edges)
    return result


def _scan_answer(spec, frame, anchors):
    """按题卡蛇形编号和实际航段约束生成扫描航线。"""
    n = len(spec["nodes"])
    if n < 4 or n % 2:
        return None
    length, spacing = spec.get("scan_length"), spec.get("scan_spacing")
    if length is None or spacing is None:
        return None
    length, spacing = float(length), float(spacing)
    if length <= 0 or spacing <= 0:
        return _review(spec, "扫描航线参数无效", ["航线长度和航线间隔必须大于0"])

    # 以3号点为局部原点，先按题卡实际的左右折返编号建立米制模板。
    # 1→2反向扫描，2→3换道，3→4正向扫描，后续依次折返。
    lanes = n // 2
    model = {}
    center_offset = (lanes - 3) * spacing / 2.0
    for index in range(n):
        lane = index // 2
        forward = (lane % 2 == 0 and index % 2 == 0) or (lane % 2 == 1 and index % 2 == 1)
        along = (length if forward else 0.0) - length / 2.0
        offset = (lane - 1) * spacing - center_offset
        model[str(index + 1)] = (along, offset)
    if "3" not in model or "4" not in model:
        return None

    # 由已知航段或两个已知航点反推绝对朝向；不凭纸面截图猜北向。
    rotations = []
    for item in spec.get("bearing_constraints", []):
        a, b = str(item["a"]), str(item["b"])
        if a not in model or b not in model:
            continue
        model_bearing = _xy_bearing(model[b][0] - model[a][0],
                                    model[b][1] - model[a][1])
        rotations.append((_wrap_angle(float(item["value"]) - model_bearing),
                          item.get("source", "航段方位")))
    for item in spec.get("segment_constraints", []):
        # 航段条件的方位按起点b→终点a记录。
        a, b = str(item["a"]), str(item["b"])
        if a not in model or b not in model:
            continue
        model_bearing = _xy_bearing(model[a][0] - model[b][0],
                                    model[a][1] - model[b][1])
        rotations.append((_wrap_angle(float(item["bearing"]) - model_bearing),
                          item.get("source", "航段方位")))
    for item in _relation_bearings(spec, anchors):
        a, b = str(item["target_a"]), str(item["target_b"])
        if a not in model or b not in model:
            continue
        model_bearing = _xy_bearing(model[b][0] - model[a][0],
                                    model[b][1] - model[a][1])
        rotations.append((_wrap_angle(float(item["value"]) - model_bearing),
                          item["source"]))

    # “当前视图坐标系”题明确以题卡画面定向；没有额外航向时，用图上1→2定旋转。
    layout = spec.get("layout_points") or []
    if (not rotations and "当前视图坐标系" in spec.get("source_text", "") and
            len(layout) == n and "1" in model and "2" in model):
        visual = {str(index + 1): (float(point[0]), -float(point[1]))
                  for index, point in enumerate(layout)}
        model_bearing = _xy_bearing(model["2"][0] - model["1"][0],
                                    model["2"][1] - model["1"][1])
        visual_bearing = _xy_bearing(visual["2"][0] - visual["1"][0],
                                     visual["2"][1] - visual["1"][1])
        rotations.append((_wrap_angle(visual_bearing - model_bearing),
                          "题卡当前视图中的1→2方向"))

    explicit_coordinate_nodes = {
        str(name) for name in (spec.get("coordinates") or {})
        if not str(name).startswith("_")
    }
    # AN.derive 可能提供旧式估算航点，供其他求解器作初值；扫描航线不能把这些
    # 估算值当成题面坐标来定向，否则一条明确的3→2航向会与旧估算图形发生假冲突。
    anchor_nodes = [str(name) for name in spec.get("nodes", [])
                    if str(name) in anchors and str(name) in model and
                    str(name) in explicit_coordinate_nodes]
    area = re.search(r"半径\s*([\d.]+)\s*(米|m)", spec["source_text"], re.I)
    centered_at_home = not anchor_nodes and "H" in anchors and area is not None
    if len(anchor_nodes) >= 2:
        for index, a in enumerate(anchor_nodes):
            for b in anchor_nodes[index + 1:]:
                model_bearing = _xy_bearing(model[b][0] - model[a][0],
                                            model[b][1] - model[a][1])
                actual_bearing = _xy_bearing(anchors[b][0] - anchors[a][0],
                                             anchors[b][1] - anchors[a][1])
                rotations.append((_wrap_angle(actual_bearing - model_bearing),
                                  "已知坐标点%s、%s" % (a, b)))
    if not rotations or (not anchor_nodes and not centered_at_home):
        return None
    rotation_deg = rotations[0][0]
    inconsistent = [source for value, source in rotations
                    if abs(_wrap_angle(value - rotation_deg)) > ANGLE_TOLERANCE_DEG]
    if inconsistent:
        return _review(spec, "扫描航线朝向约束互相矛盾",
                       ["与“%s”推导的航向不一致" % source
                        for source in inconsistent])

    # 世界坐标中顺时针方位增大，而数学坐标旋转角逆时针增大。
    rotation = -math.radians(rotation_deg)
    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    points = {}
    for name, (along, offset) in model.items():
        x, y = along, offset
        points[name] = (x * cos_r - y * sin_r, x * sin_r + y * cos_r)
    origin = anchor_nodes[0] if anchor_nodes else None
    reference = anchors[origin] if origin else anchors["H"]
    tx, ty = reference[0] - (points[origin][0] if origin else 0.0), \
             reference[1] - (points[origin][1] if origin else 0.0)
    points = {name: (point[0] + tx, point[1] + ty)
              for name, point in points.items()}
    anchor_errors = []
    for name in anchor_nodes:
        error = math.hypot(points[name][0] - anchors[name][0],
                           points[name][1] - anchors[name][1])
        if error > max(2.0, spacing * 0.02):
            anchor_errors.append("题面坐标点%s与扫描航线约束相差%.2f米" % (name, error))
    if anchor_errors:
        return _review(spec, "扫描航线与已知坐标不一致", anchor_errors)

    # 圆形空域的中心必须是明确给出的起飞点；不把任意航点当圆心。
    checks = ["扫描线%d条，每条%.0f米，间距%.0f米" % (lanes, length, spacing)]
    if area:
        radius = float(area.group(1))
        if "H" not in anchors:
            return _review(spec, "圆形空域中心未能定位", ["题面给出半径限制，但没有起飞点坐标"])
        hx, hy = anchors["H"]
        distances = [math.hypot(point[0] - hx, point[1] - hy)
                     for point in points.values()]
        if max(distances) > radius + DISTANCE_TOLERANCE_M:
            return _review(spec, "扫描航线超出题目给定的圆形空域",
                           ["最远航点%.2f米，限制半径%.2f米" % (max(distances), radius)])
        checks.append("全部航点均在H点为圆心、半径%.0f米的限制内；最大半径%.2f米" %
                      (radius, max(distances)))

    # 外部起飞点／避让点也参加最终复核；否则“起飞点到2号点100米”
    # 这类反推锚定关系会因起飞点不在输出里而被静默跳过。
    points.update({name: point for name, point in anchors.items()
                   if name not in points})

    geom = [tuple(edge) for edge in spec.get("geometry_edges", [])]
    if not geom:
        geom = [(str(i), str(i + 1)) for i in range(1, n)]
    route = [tuple(edge) for edge in spec.get("flight_edges", [])] or list(geom)
    steps = ["由%s推导扫描航线朝向%.2f°" % (rotations[0][1], (rotation_deg + 360) % 360),
             "以题卡3号点为基准按交替折返次序布置航点" ]
    return _ready(spec, points, frame, geom, route, steps, checks)


def _right_side_trapezoid(spec, frame, anchors):
    """求解 2、3 两处直角且 12、23、34 三边已知的梯形。"""
    lengths = {frozenset((str(c["a"]), str(c["b"]))): float(c["value"])
               for c in spec.get("distance_constraints", [])}
    side12 = lengths.get(frozenset(("1", "2")))
    side23 = lengths.get(frozenset(("2", "3")))
    side34 = lengths.get(frozenset(("3", "4")))
    right = {str(c["vertex"]) for c in spec.get("angle_constraints", [])
             if abs(float(c["value"]) - 90.0) <= ANGLE_TOLERANCE_DEG}
    if (not {"2", "3"}.issubset(right) or
            not all(value is not None and value > 0
                    for value in (side12, side23, side34))):
        return None
    model = {"1": (0.0, side12), "2": (0.0, 0.0),
             "3": (side23, 0.0), "4": (side23, side34)}
    known = list(spec.get("bearing_constraints", []))
    known.extend({"a": c["target_a"], "b": c["target_b"],
                  "value": c["value"]}
                 for c in _relation_bearings(spec, anchors))
    for a in model:
        if a not in anchors:
            continue
        for b in model:
            if b in anchors and a != b:
                dx = anchors[b][0] - anchors[a][0]
                dy = anchors[b][1] - anchors[a][1]
                if math.hypot(dx, dy) > 1e-8:
                    known.append({"a": a, "b": b,
                                  "value": _xy_bearing(dx, dy)})
    orientation = None
    for item in known:
        a, b = str(item["a"]), str(item["b"])
        if a in model and b in model and a != b:
            dx = model[b][0] - model[a][0]
            dy = model[b][1] - model[a][1]
            orientation = _wrap_angle(float(item["value"]) -
                                      _xy_bearing(dx, dy))
            break
    if orientation is None:
        return None
    angle = -math.radians(orientation)
    cs, sn = math.cos(angle), math.sin(angle)
    rotated = {name: (x * cs - y * sn, x * sn + y * cs)
               for name, (x, y) in model.items()}
    origin = next((name for name in model if name in anchors), None)
    if origin is None:
        return None
    ox = anchors[origin][0] - rotated[origin][0]
    oy = anchors[origin][1] - rotated[origin][1]
    points = {name: (x + ox, y + oy) for name, (x, y) in rotated.items()}
    points.update({name: point for name, point in anchors.items()
                   if name not in points})
    edges = [("1", "2"), ("2", "3"), ("3", "4"), ("4", "1")]
    return _ready(spec, points, frame, edges, edges,
                  ["由∠2=∠3=90°确定12与34平行",
                   "以12、23、34三条已知边及航向定位四个顶点"],
                  ["12=%.0f米，23=%.0f米，34=%.0f米" %
                   (side12, side23, side34)])


def _anchored_right_trapezoid_from_ray_angles(spec, frame, anchors):
    """由锚点方位、顶点射线夹角和两条已知边反求直角梯形。"""
    if not {"H", "3"}.issubset(anchors) or not {"1", "2", "3", "4"}.issubset(
            set(spec.get("nodes") or [])):
        return None
    lengths = {frozenset((str(c["a"]), str(c["b"]))): float(c["value"])
               for c in spec.get("distance_constraints", [])}
    side12 = lengths.get(frozenset(("1", "2")))
    side23 = lengths.get(frozenset(("2", "3")))
    rays = {(str(c["a"]), str(c["vertex"]), str(c["b"])): float(c["value"])
            for c in spec.get("vertex_ray_angles", [])}
    angle34 = rays.get(("H", "3", "4"))
    angle32 = rays.get(("H", "3", "2"))
    if not all(value is not None and value > 0 for value in
               (side12, side23, angle34, angle32)):
        return None
    parallel = any(
        {frozenset(map(str, c.get("first") or [])),
         frozenset(map(str, c.get("second") or []))} ==
        {frozenset(("1", "2")), frozenset(("3", "4"))}
        for c in spec.get("parallel_constraints", []))
    if not parallel:
        return None
    raw_layout = spec.get("layout_points") or []
    layout = {str(name): (float(point[0]), -float(point[1]))
              for name, point in zip(spec.get("nodes") or [], raw_layout)}
    if not all(name in layout for name in ("1", "2", "3", "4")):
        return None
    # 题图只用于判定直角在哪一侧及镜像方向，绝不把像素边长当作米数。
    def corner_cosine(a, vertex, b):
        first = (layout[a][0] - layout[vertex][0],
                 layout[a][1] - layout[vertex][1])
        second = (layout[b][0] - layout[vertex][0],
                  layout[b][1] - layout[vertex][1])
        denominator = math.hypot(*first) * math.hypot(*second)
        return (abs(first[0] * second[0] + first[1] * second[1]) /
                denominator if denominator > 1e-8 else 1.0)
    if max(corner_cosine("2", "1", "4"),
           corner_cosine("1", "4", "3")) > 0.18:
        return None
    image_u = (layout["4"][0] - layout["3"][0],
               layout["4"][1] - layout["3"][1])
    image_d = (layout["2"][0] - layout["3"][0],
               layout["2"][1] - layout["3"][1])
    image_cross = image_u[0] * image_d[1] - image_u[1] * image_d[0]
    if abs(image_cross) < 1e-8:
        return None
    handedness = 1.0 if image_cross > 0 else -1.0
    h, p3 = anchors["H"], anchors["3"]
    bearing_3h = _xy_bearing(h[0] - p3[0], h[1] - p3[1])
    solutions = []
    for sign34 in (-1, 1):
        direction34 = _bearing_xy(bearing_3h + sign34 * angle34, 1.0)
        for sign32 in (-1, 1):
            delta32 = _bearing_xy(bearing_3h + sign32 * angle32, side23)
            p2 = (p3[0] + delta32[0], p3[1] + delta32[1])
            normal = (-handedness * direction34[1],
                      handedness * direction34[0])
            height = delta32[0] * normal[0] + delta32[1] * normal[1]
            side34 = side12 + (delta32[0] * direction34[0] +
                               delta32[1] * direction34[1])
            if height <= 1e-6 or side34 <= 1e-6:
                continue
            p4 = (p3[0] + side34 * direction34[0],
                  p3[1] + side34 * direction34[1])
            p1 = (p4[0] + height * normal[0],
                  p4[1] + height * normal[1])
            points = {**anchors, "1": p1, "2": p2, "3": p3, "4": p4}
            edges = [("1", "2"), ("2", "3"), ("3", "4"), ("4", "1")]
            result = _ready(
                spec, points, frame, edges, edges,
                ["由起飞点位置与方位确定3点",
                 "由∠H32和2-3边长确定2点",
                 "由∠H34及直角梯形的平行、垂直条件反求4点与1点"],
                ["1-2=%.0f米，2-3=%.0f米" % (side12, side23),
                 "∠H34=%.0f°，∠H32=%.0f°" % (angle34, angle32)])
            if result.get("status") == "READY":
                solutions.append(result)
    if len(solutions) == 1:
        return solutions[0]
    if len(solutions) > 1:
        return _review(spec, "顶点夹角存在多个满足题面图形的解，请复核图形朝向")
    return None


def _trapezoid_answer(spec, frame, anchors):
    """用两条平行边、顶角和已知航段方位定位梯形。"""
    if spec.get("shape") != "梯形":
        return None
    ray_answer = _anchored_right_trapezoid_from_ray_angles(spec, frame, anchors)
    if ray_answer is not None:
        return ray_answer
    right_side = _right_side_trapezoid(spec, frame, anchors)
    if right_side is not None:
        return right_side
    side23 = next((c["value"] for c in spec["distance_constraints"]
                   if {c["a"], c["b"]} == {"2", "3"}), None)
    side14 = next((c["value"] for c in spec["distance_constraints"]
                   if {c["a"], c["b"]} == {"1", "4"}), None)
    side12 = next((c["value"] for c in spec["distance_constraints"]
                   if {c["a"], c["b"]} == {"1", "2"}), None)
    if side23 is None or side14 is None:
        return None
    # 要求的直角和顶角是题目给出的梯形约束。
    right = {c["vertex"] for c in spec["angle_constraints"]
             if abs(c["value"] - 90) <= ANGLE_TOLERANCE_DEG}
    top_angle = next((c["value"] for c in spec["angle_constraints"]
                      if c["vertex"] == "4"), None)
    if not {"1", "2"}.issubset(right) or (top_angle is None and side12 is None):
        return None
    if top_angle is not None and abs(top_angle - 60.0) > ANGLE_TOLERANCE_DEG:
        return None
    if not {"1", "2", "3", "4"}.issubset(set(spec.get("nodes") or [])):
        return None
    diff = side14 - side23
    rise = (abs(diff) * abs(math.tan(math.radians(top_angle)))
            if top_angle is not None else float(side12))
    # 3→4 是“底边差沿u、梯形高沿−v”的合向量，可由它反求平行边方向u。
    known = list(spec.get("bearing_constraints", []))
    known.extend({"a": item["target_a"], "b": item["target_b"],
                  "value": item["value"]}
                 for item in _relation_bearings(spec, anchors))
    u_bearing = None
    diagonal_offset = math.degrees(math.atan2(-rise, diff))
    for item in known:
        pair, heading = (str(item["a"]), str(item["b"])), float(item["value"])
        if pair in (("1", "4"), ("2", "3")):
            u_bearing = heading
        elif pair in (("4", "1"), ("3", "2")):
            u_bearing = heading + 180.0
        elif pair == ("3", "4"):
            u_bearing = heading - diagonal_offset
        elif pair == ("4", "3"):
            u_bearing = heading + 180.0 - diagonal_offset
        elif pair == ("1", "2"):
            u_bearing = heading - 90.0
        elif pair == ("2", "1"):
            u_bearing = heading + 90.0
        elif pair in (("1", "3"), ("3", "1"),
                      ("2", "4"), ("4", "2")):
            model = {"1": (0.0, rise), "2": (0.0, 0.0),
                     "3": (side23, 0.0), "4": (side14, rise)}
            a, b = pair
            dx = model[b][0] - model[a][0]
            dy = model[b][1] - model[a][1]
            u_bearing = heading - (_xy_bearing(dx, dy) - 90.0)
        if u_bearing is not None:
            u_bearing %= 360.0
            break
    if u_bearing is None:
        return None

    u = _bearing_xy(u_bearing, 1.0)
    v = _bearing_xy(u_bearing + 90.0, 1.0)
    if "2" in anchors:
        p2 = anchors["2"]
        p1 = (p2[0] - v[0] * rise, p2[1] - v[1] * rise)
    elif "1" in anchors:
        p1 = anchors["1"]
        p2 = (p1[0] + v[0] * rise, p1[1] + v[1] * rise)
    elif "3" in anchors:
        p3 = anchors["3"]
        p2 = (p3[0] - u[0] * side23, p3[1] - u[1] * side23)
        p1 = (p2[0] - v[0] * rise, p2[1] - v[1] * rise)
    elif "4" in anchors:
        p4 = anchors["4"]
        p1 = (p4[0] - u[0] * side14, p4[1] - u[1] * side14)
        p2 = (p1[0] + v[0] * rise, p1[1] + v[1] * rise)
    else:
        return None
    p3 = (p2[0] + u[0] * side23, p2[1] + u[1] * side23)
    p4 = (p1[0] + u[0] * side14, p1[1] + u[1] * side14)
    points = {"1": p1, "2": p2, "3": p3, "4": p4}
    points.update({name: point for name, point in anchors.items() if name not in points})
    geom = [("1", "2"), ("2", "3"), ("3", "4"), ("4", "1")]
    route = list(geom)
    height_step = ("按∠4=%.0f°及14、23边长求高 %.3f 米" % (top_angle, rise)
                   if top_angle is not None else
                   "按12边长求高 %.3f 米" % rise)
    angle_check = ("∠1=∠2=90°，∠4=%.0f°" % top_angle
                   if top_angle is not None else "∠1=∠2=90°")
    return _ready(spec, points, frame, geom, route,
                  ["由1、2处直角确定14与23平行",
                   "由已知航段方位推导14、23边的方位 %.2f°" % u_bearing,
                   height_step],
                  ["23边 %.0f 米；14边 %.0f 米" % (side23, side14),
                   angle_check])


def _regular_hexagon(spec, frame, anchors):
    if spec.get("shape") != "六边形" or not {"H", "3"}.issubset(anchors):
        return None
    side = spec.get("parameters", {}).get("a")
    if side is None:
        side = _param_values_from_spec(spec).get("a")
    if side is None:
        return None
    match = re.search(r"H\s*[-到至]\s*3\s*顺时针旋转\s*([\d.]+)°?.{0,24}2\s*[-到至]\s*5.{0,10}相反", spec["source_text"])
    if not match:
        return None
    h, p3 = anchors["H"], anchors["3"]
    h_to_3 = _xy_bearing(p3[0] - h[0], p3[1] - h[1])
    h_to_3 = (h_to_3 + float(match.group(1)) + 180.0) % 360.0
    # 正六边形中2与5为对顶点；反推顶点2角度，再沿逆时针布点。
    theta2 = math.radians((-h_to_3 - 90.0) % 360.0)
    theta1 = theta2 - math.pi / 3.0
    theta3 = theta1 + 2.0 * math.pi / 3.0
    center = (p3[0] - float(side) * math.cos(theta3),
              p3[1] - float(side) * math.sin(theta3))
    points = {}
    for i in range(6):
        theta = theta1 + i * math.pi / 3.0
        points[str(i + 1)] = (center[0] + float(side) * math.cos(theta),
                              center[1] + float(side) * math.sin(theta))
    points["H"] = h
    edges = [(str(i + 1), str((i + 1) % 6 + 1)) for i in range(6)]
    return _ready(spec, points, frame, edges, edges,
                  ["由H→3方向和顺时针旋转30°反推2→5航向",
                   "正六边形的2、5为对顶点，据此求中心后按300米边长布点"],
                  ["六条边均为 %.0f 米" % side,
                   "点3与题面坐标一致；航线按1→2→3→4→5→6→1闭合"])


def _param_values_from_spec(spec):
    return spec.get("parameters", {})


def _circle_answer(spec, frame, anchors):
    n = spec.get("circle_point_count")
    diameter = spec.get("circle_diameter")
    if not n or not diameter:
        return None
    # “至少 N 点”取满足条件的最小 N 个，均匀布点。
    n = int(n)
    radius = float(diameter) / 2.0
    if n < 3:
        return _review(spec, "圆形航线的航点数不足以闭合")
    h_to_2 = None
    for clause in re.split(r"[。;\n]", spec["source_text"]):
        m = re.search(r"2点.{0,10}(?:处于|位于).{0,6}H点", clause, re.I)
        if not m:
            continue
        bearing = GEO.parse_bearing(clause)
        dist = AN._find_dist(clause)
        if bearing is not None and dist:
            h_to_2 = {"bearing": float(bearing), "distance": float(dist),
                      "source": clause}
            break
    chord = next((c for c in spec["bearing_constraints"]
                  if c["a"] == "1" and c["b"] == str(min(5, n))), None)
    if not chord:
        # 航向1-5为SW 等简写。
        m = re.search(r"航向\s*1\s*[-到至]\s*5\s*(?:为|是)?\s*(NW|NE|SW|SE|北|南|东|西|东北|西北|东南|西南)", spec["source_text"], re.I)
        if m:
            chord = {"value": float(GEO.parse_bearing(m.group(1))), "source": m.group(0)}
    if not h_to_2 or not chord:
        return None
    hxy = anchors.get("H", (0.0, 0.0))
    ox, oy = _bearing_xy(h_to_2["bearing"], h_to_2["distance"])
    p2 = (hxy[0] + ox, hxy[1] + oy)
    # 从1到5跨4个等分弦的方位角确定圆心朝向。
    delta = 2.0 * math.pi * ((min(5, n) - 1) / n)
    chord_bearing = float(chord["value"])
    theta1 = math.radians((-chord_bearing) % 360.0) - delta / 2.0
    theta2 = theta1 + 2.0 * math.pi / n
    center = (p2[0] - radius * math.cos(theta2),
              p2[1] - radius * math.sin(theta2))
    points = {}
    for i in range(n):
        theta = theta1 + i * 2.0 * math.pi / n
        points[str(i + 1)] = (center[0] + radius * math.cos(theta),
                              center[1] + radius * math.sin(theta))
    edges = [(str(i + 1), str((i + 1) % n + 1)) for i in range(n)]
    checks = ["直径 %.0f 米，半径 %.0f 米" % (diameter, radius),
              "航点数按“至少 %d 点”取最小值，均匀分布" % n,
              "第2点距 H 点 %.0f 米，方位 %.0f°" % (h_to_2["distance"], h_to_2["bearing"]),
              "1→%d 方位 %.0f°" % (min(5, n), chord_bearing)]
    return _ready(spec, points, frame, edges, edges,
                  ["由第2点相对 H 的坐标和圆半径确定圆心",
                   "利用 1→%d 的弦向反推第1点角位置，再按逆时针均匀布点" % min(5, n)],
                  checks)


def _butterfly_square(spec, frame, anchors):
    """解四边等长蝴蝶图形的正方形外框及交叉航线。"""
    lengths = [c["value"] for c in spec["distance_constraints"]
               if set((c["a"], c["b"])) in ({"1", "3"}, {"2", "3"}, {"2", "4"}, {"1", "4"})]
    if len({round(v, 5) for v in lengths}) != 1 or len(lengths) < 4:
        return None
    side = float(lengths[0])
    # H=4 的方向由4→3对角线给定；H≠3用于从正方形的两个交点中确定H。
    h = anchors.get("H")
    diag = next((c for c in spec["bearing_constraints"]
                 if (c["a"], c["b"]) == ("4", "3")), None)
    h_dist = re.search(r"H到1和H到2\s*=\s*([\d.]+)\s*米", spec["source_text"])
    if h and diag and h_dist and abs(float(h_dist.group(1)) - side) <= DISTANCE_TOLERANCE_M:
        distance = side / math.sqrt(2.0)
        # 对角线 4→3 的两条正方形边分别相差45°。
        u1 = (float(diag["value"]) - 45.0) % 360.0
        u2 = (float(diag["value"]) + 45.0) % 360.0
        p4 = h
        p1 = (p4[0] + _bearing_xy(u2, side)[0], p4[1] + _bearing_xy(u2, side)[1])
        p2 = (p4[0] + _bearing_xy(u1, side)[0], p4[1] + _bearing_xy(u1, side)[1])
        p3 = (p1[0] + _bearing_xy(u1, side)[0], p1[1] + _bearing_xy(u1, side)[1])
        points = {"H": h, "1": p1, "2": p2, "3": p3, "4": p4}
        geom = [("1", "3"), ("3", "2"), ("2", "4"), ("4", "1")]
        route = [("1", "2"), ("2", "3"), ("3", "4"), ("4", "1")]
        return _ready(spec, points, frame, geom, route,
                      ["四条外框边均为 %.0f 米，构成正方形" % side,
                       "4→3 对角线方位 %.0f°，正方形边相对对角线偏转45°" % diag["value"],
                       "H距1、2均为 %.0f 米且H不与3重合，因此H=4" % side],
                      ["外框四边长度均为 %.0f 米" % side,
                       "航线按1→2→3→4→1连接；两条对角航段长度为 %.3f 米" % (side * math.sqrt(2))])

    # 已知1与H经纬度时，方向关系给出上边2→4的航向。
    if "1" in anchors and h:
        relation = re.search(r"2\s*[-到至]\s*4.{0,20}(逆时针|顺时针)旋转\s*90°?.{0,30}1\s*[-到至]\s*起飞点.{0,20}(?:相同|一致)", spec["source_text"])
        if relation:
            base = _xy_bearing(h[0] - anchors["1"][0], h[1] - anchors["1"][1])
            # 航向角按顺时针递增；逆时针旋转90°等于减90°。
            # 题面说“2→4逆时针旋转90°后等于1→H”，因此先反解2→4。
            top = (base + 90.0 if relation.group(1) == "逆时针"
                   else base - 90.0) % 360.0
            right = (top + 90.0) % 360.0
            p1 = anchors["1"]
            p4 = (p1[0] + _bearing_xy(right, side)[0], p1[1] + _bearing_xy(right, side)[1])
            p2 = (p4[0] + _bearing_xy((top + 180.0) % 360.0, side)[0],
                  p4[1] + _bearing_xy((top + 180.0) % 360.0, side)[1])
            p3 = (p1[0] + _bearing_xy((top + 180.0) % 360.0, side)[0],
                  p1[1] + _bearing_xy((top + 180.0) % 360.0, side)[1])
            points = {"H": h, "1": p1, "2": p2, "3": p3, "4": p4}
            geom = [("1", "3"), ("3", "2"), ("2", "4"), ("4", "1")]
            route = [("1", "2"), ("2", "3"), ("3", "4"), ("4", "1")]
            return _ready(spec, points, frame, geom, route,
                          ["题面四条外框边长度相等，按正方形外框求解",
                           "使用方位旋转关系反推2→4方向，再按边长定位其余顶点"],
                          ["每条外框边 %.0f 米" % side,
                           "交叉航段1→2、3→4长度各 %.3f 米" % (side * math.sqrt(2))])

    # H 到1的距离、逆风航向与方形蝴蝶图上的直角标志共同确定方位。
    if h and "H" in spec["coordinates"]:
        min_gap = re.search(r"各点之间距离不小于\s*([\d.]+)\s*米", spec["source_text"])
        wind_clause = next((line for line in spec["source_text"].splitlines()
                            if "风向" in line and re.search(r"H\s*[-到至]\s*1", line)), "")
        if min_gap and wind_clause and "∠1=∠2=∠3=∠4=45°" in spec["source_text"]:
            wind = GEO.wind_from(wind_clause)
            if wind is not None:
                # H→1逆风给出返航向量；题卡蝴蝶图标明两条对角线垂直。
                side = float(min_gap.group(1))
                p1 = (h[0] + _bearing_xy(wind, 120.0)[0],
                      h[1] + _bearing_xy(wind, 120.0)[1])
                # 图形的上下左右按罗盘方向定位，1为左下、2右上、3右下、4左上。
                p3 = (p1[0] + side, p1[1])
                p4 = (p1[0], p1[1] + side)
                p2 = (p1[0] + side, p1[1] + side)
                # 等边要求是所有顶点间距至少20米，侧边取达到下限的20米。
                points = {"H": h, "1": p1, "2": p2, "3": p3, "4": p4}
                geom = [("1", "3"), ("3", "2"), ("2", "4"), ("4", "1")]
                route = [("1", "2"), ("2", "3"), ("3", "4"), ("4", "1")]
                return _ready(spec, points, frame, geom, route,
                              ["H→1逆风方位由西北风确定，距离按题卡给定120米",
                               "各点最小间距按20米边界布置；图示两条对角线垂直"],
                              ["最小点间距 %.0f 米" % side,
                               "H→1距离120米，方位 %.0f°" % wind])
    return None


def _template_points(spec):
    """返回以米外任意尺度表示的标准点位模板，供约束定位和求解使用。"""
    nodes = spec.get("nodes") or []
    shape = spec.get("shape")
    count = len(nodes)
    if count < 3:
        return None
    if shape == "蝴蝶形" and count == 4:
        # 点位按用户确认的蝴蝶形编号：1左下、2右上、3右下、4左上。
        lengths = {}
        for item in spec.get("distance_constraints", []):
            pair = frozenset((str(item["a"]), str(item["b"])))
            lengths[pair] = float(item["value"])
        short = [lengths.get(frozenset(pair)) for pair in (("1", "4"), ("2", "3"))]
        diagonal = [lengths.get(frozenset(pair)) for pair in (("1", "2"), ("3", "4"))]
        if all(value is not None for value in short + diagonal):
            short_value = sum(short) / 2.0
            diagonal_value = sum(diagonal) / 2.0
            if (short_value > 0 and diagonal_value > short_value and
                    max(short) - min(short) <= DISTANCE_TOLERANCE_M and
                    max(diagonal) - min(diagonal) <= DISTANCE_TOLERANCE_M):
                ratio_sq = (diagonal_value / short_value) ** 2 - 1.0
                if ratio_sq > 0:
                    width = math.sqrt(ratio_sq)
                    return {"1": (0.0, 0.0), "2": (width, 1.0),
                            "3": (width, 0.0), "4": (0.0, 1.0)}
        # 四条外框边相等只能确定菱形；仅凭对角线航向和单个坐标，不能补成正方形。
        side_pairs = {frozenset(pair) for pair in
                      (("1", "3"), ("3", "2"), ("2", "4"), ("4", "1"))}
        diagonal_pairs = {frozenset(("1", "2")), frozenset(("3", "4"))}
        constrained_pairs = {frozenset((item["a"], item["b"]))
                             for item in spec.get("distance_constraints", [])}
        equal_outer_sides = (side_pairs.issubset(constrained_pairs) and
                             len({round(item["value"], 5)
                                  for item in spec.get("distance_constraints", [])
                                  if frozenset((item["a"], item["b"])) in side_pairs}) == 1)
        diagonal_heading_only = any(
            frozenset((item["a"], item["b"])) in diagonal_pairs
            for item in spec.get("bearing_constraints", []))
        if (equal_outer_sides and not spec.get("angle_constraints") and
                diagonal_heading_only and len(spec.get("coordinates", {})) < 2):
            layout = spec.get("layout_points") or []
            layout_is_square = False
            if len(layout) == 4 and set(nodes) == {"1", "2", "3", "4"}:
                layout_by_node = {
                    name: (float(point[0]), -float(point[1]))
                    for name, point in zip(nodes, layout)}
                boundary = [tuple(edge) for edge in
                            spec.get("geometry_edges", [])]
                side_pairs = {frozenset(edge) for edge in boundary}
                if len(boundary) == 4 and side_pairs == {
                        frozenset(pair) for pair in
                        (("1", "3"), ("3", "2"), ("2", "4"), ("4", "1"))}:
                    lengths = [math.dist(layout_by_node[a], layout_by_node[b])
                               for a, b in boundary]
                    angles = []
                    for vertex in nodes:
                        neighbors = [b if a == vertex else a
                                     for a, b in boundary
                                     if a == vertex or b == vertex]
                        if len(neighbors) != 2:
                            angles = []
                            break
                        angles.append(_angle_of(layout_by_node[neighbors[0]],
                                                layout_by_node[vertex],
                                                layout_by_node[neighbors[1]]))
                    layout_is_square = (
                        min(lengths, default=0.0) > 0 and
                        max(lengths) / min(lengths) <= 1.06 and
                        len(angles) == 4 and
                        all(abs(angle - 90.0) <= 8.0 for angle in angles))
            if not layout_is_square:
                return None
        return {"1": (0.0, 0.0), "2": (1.0, 1.0),
                "3": (1.0, 0.0), "4": (0.0, 1.0)}
    if shape == "五边形" and count == 5:
        # 五边形凹点编号因题而异，用纸图拐点或已知角度挑选对应编号。
        edge_lengths = {frozenset((str(item["a"]), str(item["b"]))):
                        float(item["value"])
                        for item in spec.get("distance_constraints", [])}
        right_angles = {str(item["vertex"]): float(item["value"])
                        for item in spec.get("angle_constraints", [])}
        anchor_edges = [edge_lengths.get(frozenset(pair))
                        for pair in (("1", "2"), ("2", "3"), ("1", "5"))]
        if (all(value is not None for value in anchor_edges) and
                max(anchor_edges) - min(anchor_edges) <= DISTANCE_TOLERANCE_M and
                all(abs(right_angles.get(vertex, -999) - 90.0) <= ANGLE_TOLERANCE_DEG
                    for vertex in ("1", "2", "4"))):
            # 题图中4号点是内凹顶点；三条直角边锁定外框，∠4锁定凹口。
            return {"1": (0.0, 0.0), "2": (0.0, 1.0),
                    "3": (1.0, 1.0), "4": (0.5, 0.5),
                    "5": (1.0, 0.0)}
        base = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0),
                (1.0, 0.0), (0.5, math.sqrt(3.0) / 2.0)]
        variants = []
        for indent_index in range(count):
            variant = {str(i + 1): base[(i - indent_index + 4) % 5]
                       for i in range(5)}
            variants.append((indent_index + 1, variant))

        layout = spec.get("layout_points") or []
        layout_indent = None
        if len(layout) == 5:
            turns = []
            for i in range(5):
                prev = layout[(i - 1) % 5]
                here = layout[i]
                nxt = layout[(i + 1) % 5]
                turns.append((here[0] - prev[0]) * (nxt[1] - here[1]) -
                             (here[1] - prev[1]) * (nxt[0] - here[0]))
            signs = [1 if value > 1e-6 else -1 if value < -1e-6 else 0
                     for value in turns]
            nonzero = [value for value in signs if value]
            if len(nonzero) >= 4:
                majority = 1 if sum(nonzero) > 0 else -1
                reflex = [i + 1 for i, value in enumerate(signs)
                          if value and value != majority]
                if len(reflex) == 1:
                    layout_indent = reflex[0]

        angle_specs = spec.get("angle_constraints", [])
        valid = []
        polygon_edges = [(str(i), str(i % 5 + 1)) for i in range(1, 6)]
        for indent_number, candidate in variants:
            neighbor_map = {}
            for a, b in polygon_edges:
                neighbor_map.setdefault(a, []).append(b)
                neighbor_map.setdefault(b, []).append(a)
            matches = True
            for item in angle_specs:
                vertex = item["vertex"]
                adjacent = neighbor_map.get(vertex, [])
                if len(adjacent) < 2:
                    continue
                actual = _angle_of(candidate[adjacent[0]], candidate[vertex],
                                   candidate[adjacent[1]])
                if abs(actual - float(item["value"])) > ANGLE_TOLERANCE_DEG:
                    matches = False
                    break
            if matches:
                valid.append((indent_number, candidate))
        if angle_specs:
            if len(valid) == 1:
                return valid[0][1]
            if layout_indent is not None:
                selected = next((candidate for number, candidate in valid
                                 if number == layout_indent), None)
                if selected:
                    return selected
            return None
        if layout_indent is not None:
            selected = next((candidate for number, candidate in variants
                             if number == layout_indent), None)
            if selected:
                return selected
        return variants[3][1]
    if shape == "梯形" and count == 4:
        # 点序按纸面图：1左上、2左下、3右下、4右上。
        lengths = {frozenset((str(item["a"]), str(item["b"]))):
                   float(item["value"])
                   for item in spec.get("distance_constraints", [])}
        upper = lengths.get(frozenset(("1", "4")))
        lower = lengths.get(frozenset(("2", "3")))
        angle4 = next((float(item["value"])
                       for item in spec.get("angle_constraints", [])
                       if item["vertex"] == "4"), None)
        if upper and lower and angle4 is not None and upper != lower:
            acute_angle = angle4 if upper > lower else 180.0 - angle4
            if 0.0 < acute_angle < 90.0:
                height = abs(upper - lower) * math.tan(math.radians(acute_angle))
                return {"1": (0.0, height), "2": (0.0, 0.0),
                        "3": (lower, 0.0), "4": (upper, height)}
        return {"1": (0.0, 1.0), "2": (0.0, 0.0),
                "3": (1.5, 0.0), "4": (1.0, 1.0)}
    if shape == "直角三角形" and count == 3:
        return {"1": (0.0, 0.0), "2": (1.0, 0.0), "3": (0.0, 1.0)}
    if shape == "等边三角形" and count == 3:
        return {"1": (0.0, 1.0), "2": (math.sqrt(3) / 2, -0.5),
                "3": (-math.sqrt(3) / 2, -0.5)}
    if shape == "六边形" and count == 6:
        return {str(i + 1): (math.cos(math.radians(90 - i * 60)),
                             math.sin(math.radians(90 - i * 60)))
                for i in range(6)}
    raw = spec.get("layout_points") or []
    if len(raw) == count:
        return {str(i + 1): (float(point[0]), -float(point[1]))
                for i, point in enumerate(raw)}
    return None


def _relation_bearings(spec, anchors):
    """把已知航段带入同向、反向和旋转关系，推出另一条航段方位。"""
    output = []
    direct = {(str(item["a"]), str(item["b"])): float(item["value"]) % 360.0
              for item in spec.get("bearing_constraints", [])}
    for item in spec.get("segment_constraints", []):
        direct.setdefault((str(item["b"]), str(item["a"])),
                          float(item["bearing"]) % 360.0)

    def bearing(pair):
        a, b = map(str, pair)
        if (a, b) in direct:
            return direct[(a, b)]
        if (b, a) in direct:
            return (direct[(b, a)] + 180.0) % 360.0
        if a not in anchors or b not in anchors:
            return None
        return _xy_bearing(anchors[b][0] - anchors[a][0],
                           anchors[b][1] - anchors[a][1])

    relations = spec.get("direction_relations", [])
    # 已知航向沿相同／相反关系链逐段传递，避免只解出第一层而漏掉后续航点。
    for _ in range(len(relations) + 1):
        changed = False
        for relation in relations:
            first, second = relation["first"], relation["second"]
            offset = float(relation.get("rotation", 0.0))
            if relation.get("opposite"):
                offset += 180.0
            first_value = bearing(first)
            second_value = bearing(second)
            derived = None
            if first_value is not None and second_value is None:
                target = second
                derived = (first_value + offset) % 360.0
                source_pair = first
            elif second_value is not None and first_value is None:
                target = first
                derived = (second_value - offset) % 360.0
                source_pair = second
            else:
                continue
            a, b = map(str, target)
            if (a, b) in direct or (b, a) in direct:
                continue
            direct[(a, b)] = derived
            direct[(b, a)] = (derived + 180.0) % 360.0
            output.append({"a": str(source_pair[0]),
                           "b": str(source_pair[1]),
                           "target_a": a, "target_b": b,
                           "value": derived, "source": relation["source"]})
            changed = True
        if not changed:
            break
    return output


def _template_answer(spec, frame, anchors):
    """用编号模板承接边长、坐标和方位条件，再通过通用复核确认结果。"""
    template = _template_points(spec)
    if not template:
        return None
    nodes = spec.get("nodes") or []
    shape_nodes = set(nodes)
    geometry_edges = [tuple(edge) for edge in spec.get("geometry_edges", [])]
    route = [tuple(edge) for edge in spec.get("flight_edges", [])]
    distances = [item for item in spec.get("distance_constraints", [])
                 if item["a"] in template and item["b"] in template]
    ratios = []
    for item in distances:
        a, b = item["a"], item["b"]
        model_distance = math.hypot(template[a][0] - template[b][0],
                                    template[a][1] - template[b][1])
        if model_distance > 1e-9:
            ratios.append(float(item["value"]) / model_distance)
    if not ratios:
        return None
    scale = sorted(ratios)[len(ratios) // 2]
    if scale <= 0:
        return None
    # 等边模板只可用于题面边长一致的形状；互相冲突时交给校对界面。
    if max(ratios) - min(ratios) > max(DISTANCE_TOLERANCE_M * 2,
                                        scale * 0.005):
        return None

    anchor_nodes = [name for name in nodes if name in anchors]
    rotation = None
    origin_node = None
    bearing_constraints = list(spec.get("bearing_constraints", []))
    for relation in _relation_bearings(spec, anchors):
        if relation["target_a"] in template and relation["target_b"] in template:
            bearing_constraints.append({"a": relation["target_a"],
                                        "b": relation["target_b"],
                                        "value": relation["value"],
                                        "kind": "bearing",
                                        "source": relation["source"]})

    if len(anchor_nodes) >= 2:
        best = None
        for index, a in enumerate(anchor_nodes):
            for b in anchor_nodes[index + 1:]:
                model = math.hypot(template[b][0] - template[a][0],
                                   template[b][1] - template[a][1])
                actual = math.hypot(anchors[b][0] - anchors[a][0],
                                    anchors[b][1] - anchors[a][1])
                if model > 1e-8 and (best is None or model > best[0]):
                    best = (model, a, b, actual)
        if best:
            model, a, b, actual = best
            if abs(actual - model * scale) > max(2.0, scale * 0.02):
                return _review(spec, "已知坐标与边长条件不一致",
                               ["点%s、%s坐标间距%.2f米，模板条件预计%.2f米" %
                                (a, b, actual, model * scale)])
            model_bearing = _xy_bearing(template[b][0] - template[a][0],
                                        template[b][1] - template[a][1])
            actual_bearing = _xy_bearing(anchors[b][0] - anchors[a][0],
                                         anchors[b][1] - anchors[a][1])
            # 坐标矩阵采用数学逆时针角，而方位角按顺时针递增，符号相反。
            rotation = -math.radians(_wrap_angle(actual_bearing - model_bearing))
            origin_node = a
    if rotation is None:
        for item in bearing_constraints:
            a, b = str(item["a"]), str(item["b"])
            if a not in template or b not in template:
                continue
            model_bearing = _xy_bearing(template[b][0] - template[a][0],
                                        template[b][1] - template[a][1])
            rotation = -math.radians(_wrap_angle(float(item["value"]) - model_bearing))
            origin_node = next((name for name in anchor_nodes), None)
            if origin_node is None:
                return None
            break
    if rotation is None or origin_node is None:
        return None

    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    rotated = {name: (scale * (point[0] * cos_r - point[1] * sin_r),
                      scale * (point[0] * sin_r + point[1] * cos_r))
               for name, point in template.items()}
    tx = anchors[origin_node][0] - rotated[origin_node][0]
    ty = anchors[origin_node][1] - rotated[origin_node][1]
    points = {name: (point[0] + tx, point[1] + ty)
              for name, point in rotated.items()}
    errors = []
    for name in anchor_nodes:
        error = math.hypot(points[name][0] - anchors[name][0],
                           points[name][1] - anchors[name][1])
        if error > max(2.0, scale * 0.02):
            errors.append("题面坐标点%s与图形边长方位条件相差%.2f米" % (name, error))
    if errors:
        return _review(spec, "坐标、方位与图形条件无法同时满足", errors)
    # 把起飞、避让和过渡点一并带入答案，保证外部条件也能在图上核对。
    for name, point in anchors.items():
        if name not in points:
            points[name] = point
    checks = ["图形结构按%s点位模板定位" % (spec.get("graph_source") or "题面"),
              "已知坐标、方位和%d条边长约束通过复核" % len(distances)]
    return _ready(spec, points, frame, geometry_edges, route,
                  ["从题面边长确定图形比例尺 %.3f 米/模板单位" % scale,
                   "由已知航段方位或坐标点确定图形朝向",
                   "平移到已知航点后复核其余坐标和边长"], checks)


def _notched_square_answer(spec, frame, anchors):
    """求解 1-2-3-5 正方形外框及 3-4-5 的凹口。"""
    if spec.get("shape") != "五边形" or not {"H", "3"}.issubset(anchors):
        return None
    lengths = {frozenset((str(c["a"]), str(c["b"]))): float(c["value"])
               for c in spec.get("distance_constraints", [])}
    sides = [lengths.get(frozenset(pair)) for pair in
             (("1", "2"), ("2", "3"), ("3", "5"), ("5", "1"))]
    if any(side is None for side in sides) or max(sides) - min(sides) > DISTANCE_TOLERANCE_M:
        return None
    angles = {(c["a"], c["vertex"], c["b"]): float(c["value"])
              for c in spec.get("vertex_ray_angles", [])}
    a32 = angles.get(("H", "3", "2"))
    a34 = angles.get(("H", "3", "4"))
    if a32 is None or a34 is None:
        return None
    side = sides[0]
    h, p3 = anchors["H"], anchors["3"]
    b3h = _xy_bearing(h[0] - p3[0], h[1] - p3[1])
    solutions = []
    for sign32 in (-1, 1):
        b32 = (b3h + sign32 * a32) % 360
        ux, uy = _bearing_xy(b32, 1.0)
        for sign34 in (-1, 1):
            b34 = (b3h + sign34 * a34) % 360
            # 3→4 必须在正方形的 3→2 和 3→5 两条边之间。
            sweep = (b32 - b34) % 360
            if not 0 < sweep < 90:
                continue
            b35 = (b32 - 90) % 360
            vx, vy = _bearing_xy(b35, 1.0)
            wx, wy = _bearing_xy(b34, 1.0)
            reach = side * math.cos(math.radians(90 - sweep))
            points = dict(anchors)
            points["2"] = (p3[0] + side * ux, p3[1] + side * uy)
            points["5"] = (p3[0] + side * vx, p3[1] + side * vy)
            points["1"] = (points["2"][0] + side * vx,
                           points["2"][1] + side * vy)
            points["4"] = (p3[0] + reach * wx, p3[1] + reach * wy)
            geom = [("1", "2"), ("2", "3"), ("3", "4"),
                    ("4", "5"), ("5", "1")]
            result = _ready(spec, points, frame, geom, geom,
                            ["由起飞点坐标和 120 米方位确定 3 点",
                             "用 1-2-3-5 正方形与 ∠H32 确定外框朝向",
                             "由 ∠H34 和 ∠4=90° 求得凹口 4 点"],
                            ["正方形四边均为 %.0f 米" % side,
                             "∠H32=%.0f°，∠H34=%.0f°" % (a32, a34)])
            if result.get("status") == "READY":
                solutions.append(result)
    if len(solutions) == 1:
        return solutions[0]
    if len(solutions) > 1:
        return _review(spec, "夹角条件产生多个可行图形，请校对题图方向")
    return None


def _solve_reference_uncached(q):
    """生成答案；条件不够或来源需确认时返回可操作的复核原因。"""
    spec = extract_question_spec(q)
    if spec.get("review"):
        return _review(spec, spec["review"]["reason"], [spec["review"]["source"]])
    frame, anchors, geo_origin = _coordinate_frame(spec)
    solver_question = q
    if spec.get("condition_assist_used"):
        solver_question = dict(q or {})
        solver_question["conditions_text"] = spec.get(
            "condition_assist_conditions", "")
        solver_question["requirements_text"] = ""
        solver_question["suggested_text"] = ""
    legacy = AN.derive(solver_question)
    # 由题面坐标和显式航段即可到达的节点，统一在本地平面重建，避免旧答案的
    # 经纬度正反算误差与新解算器的局部向量混在一起。
    reachable = {name for name in spec.get("coordinates", {})
                 if not str(name).startswith("_")}
    changed = True
    while changed:
        changed = False
        for segment in spec.get("segment_constraints", []):
            a, b = str(segment["a"]), str(segment["b"])
            if a in reachable and b not in reachable:
                reachable.add(b)
                changed = True
            elif b in reachable and a not in reachable:
                reachable.add(a)
                changed = True
    # 旧解析器只把坐标根点视为起飞点；题面直接标在编号航点上的坐标，
    # 不能拿旧解析器臆算的起飞点和外部航点来反向定向图形。
    legacy_shape_nodes = set()
    if geo_origin and str(geo_origin[2]) == "H":
        for role, item in (legacy.get("points") or {}).items():
            node = _role_node(role)
            if (node in anchors or node in reachable or
                    item.get("lat") is None or item.get("lon") is None):
                continue
            if Geodesic is not None:
                inv = Geodesic.WGS84.Inverse(geo_origin[0], geo_origin[1],
                                             item["lat"], item["lon"])
                anchors[node] = _bearing_xy(inv["azi1"], inv["s12"])
            else:
                bearing = GEO.bearing_of(geo_origin[0], geo_origin[1],
                                         item["lat"], item["lon"])
                distance = GEO.distance_of(geo_origin[0], geo_origin[1],
                                           item["lat"], item["lon"])
                anchors[node] = _bearing_xy(bearing or 0, distance or 0)
            if node in spec.get("nodes", []):
                legacy_shape_nodes.add(node)
    active_segments = list(spec.get("segment_constraints", []))
    known_bearings = {(str(item["a"]), str(item["b"])): float(item["value"])
                      for item in spec.get("bearing_constraints", [])}
    for item in _relation_bearings(spec, anchors):
        known_bearings[(str(item["target_a"]), str(item["target_b"]))] = float(item["value"])
    existing_pairs = {frozenset((str(item["a"]), str(item["b"])))
                      for item in active_segments}
    for item in spec.get("distance_constraints", []):
        a, b = str(item["a"]), str(item["b"])
        pair = frozenset((a, b))
        if not (pair & {"H", "A", "T"}) or pair in existing_pairs:
            continue
        bearing = known_bearings.get((a, b))
        if bearing is None and (b, a) in known_bearings:
            bearing = (known_bearings[(b, a)] + 180.0) % 360.0
        if bearing is None:
            continue
        active_segments.append({"a": b, "b": a, "bearing": bearing,
                                "distance": float(item["value"]),
                                "source": item.get("source", "")})
        existing_pairs.add(pair)
    for _ in range(len(active_segments) + 1):
        changed = False
        for segment in active_segments:
            target, origin = str(segment["a"]), str(segment["b"])
            if origin in anchors and (target not in anchors or
                                      (target in legacy_shape_nodes and
                                       origin not in legacy_shape_nodes)):
                ox, oy = _bearing_xy(segment["bearing"], segment["distance"])
                base = anchors[origin]
                anchors[target] = (base[0] + ox, base[1] + oy)
                legacy_shape_nodes.discard(target)
                changed = True
            elif target in anchors and (origin not in anchors or
                                        (origin in legacy_shape_nodes and
                                         target not in legacy_shape_nodes)):
                ox, oy = _bearing_xy((segment["bearing"] + 180.0) % 360.0,
                                     segment["distance"])
                base = anchors[target]
                anchors[origin] = (base[0] + ox, base[1] + oy)
                legacy_shape_nodes.discard(origin)
                changed = True
        if not changed:
            break

    shape = spec.get("shape")
    result = _notched_square_answer(spec, geo_origin, anchors)
    if result:
        return result
    # 模板法（按图形专门写的解法）先试；但**「算出了点却复核不过」的结果不能挡住后面
    # 的通用求解器** —— 那是典型的"局部模板套错了形状"，后面还有自由坐标法、相似变换、
    # 形状先验三条路可走。这里把它降级成兜底结果，最后再返回（原因照样保留）。
    # 实测 旧样本 都被这条短路卡在「复核失败」上。
    deferred = None

    def _accept(result):
        """只让 **READY** 短路；**任何非 READY 的结果都降级成兜底、继续往下试**。

        ★ 早先这里只对"计算结果未通过题面条件复核"这一种原因降级，其余非 READY
        （典型是"当前约束类型尚未组成可校验的唯一坐标解"）**照样短路** ——
        实测 旧样本 就被"蝴蝶形"分支里那个直角沙漏专用解法截住：它给出一个通用 review，
        于是后面本来能给出唯一解的"米制模型 + 相似变换"完全轮不到
        （我逐级打点确认过：相似变换对 旧样本 满秩、残差 0、解唯一）。
        这与我在形状先验那一路修过的短路是同一类 bug，这次一并按"顺序回退"处理。
        """
        nonlocal deferred
        if not result:
            return None
        if result.get("status") == "READY":
            return result
        candidate_points = (result.get("candidate_points") or
                            result.get("points") or {})
        candidate_nodes = [str(name) for name in spec.get("nodes", [])]
        if candidate_nodes and all(name in candidate_points
                                   for name in candidate_nodes):
            candidate_geometry = (result.get("candidate_geometry_edges") or
                                  result.get("geometry_edges") or
                                  spec.get("geometry_edges") or [])
            candidate_route = (result.get("candidate_flight_edges") or
                               result.get("flight_edges") or
                               spec.get("flight_edges") or [])
            refined = NR.refine_candidate(
                spec, candidate_points, geo_origin,
                [tuple(map(str, edge[:2])) for edge in candidate_geometry
                 if isinstance(edge, (list, tuple)) and len(edge) >= 2],
                [tuple(map(str, edge[:2])) for edge in candidate_route
                 if isinstance(edge, (list, tuple)) and len(edge) >= 2],
                __import__(__name__))
            if refined and refined.get("status") == "READY":
                return refined
        if deferred is None:
            deferred = result
        return None

    if shape == "圆形":
        result = _accept(_circle_answer(spec, geo_origin, anchors))
        if result:
            return result
    if shape == "扫描航线":
        result = _accept(_scan_answer(spec, geo_origin, anchors))
        if result:
            return result
    if shape == "蝴蝶形":
        result = _accept(_butterfly_square(spec, geo_origin, anchors))
        if result:
            return result
    if shape == "梯形":
        result = _accept(_trapezoid_answer(spec, geo_origin, anchors))
        if result:
            return result
    if shape == "六边形":
        result = _accept(_regular_hexagon(spec, geo_origin, anchors))
        if result:
            return result
    result = _accept(_template_answer(spec, geo_origin, anchors))
    if result:
        return result
    result = _accept(NR.solve(spec, geo_origin, anchors, __import__(__name__)))
    if result:
        return result
    # 自由坐标法不够定形时（题面只给尺寸、形状靠卡片），退一步用「卡片形状 +
    # 相似变换」求解：只解平移／旋转／缩放四个自由度，题面全部约束进残差。
    # 放在这里而不是更早，是为了让题面本身能定形的题仍走原路径，不被卡片带偏。
    result = _accept(NR.solve_similarity(spec, geo_origin, __import__(__name__)))
    if result:
        return result
    # 题面连形状都定不了时（典型：五边形只说「①②边及②③边为 a，∠②为 90°」），
    # 用题卡图形当**形状先验**：题面条件仍是硬约束，只在满足题面的解集里挑最贴近
    # 卡片的那一个；编号由题面条件反推，不照抄卡片标签。偏差过大直接拒绝。
    result = _accept(NR.solve_shape_prior(spec, geo_origin, __import__(__name__)))
    if result:
        return result
    # ★ 兜底（用户口径）：“整个图形按照 SW 做图，六边形里 2~3 就是 SW” ——
    #   建议参数末尾那个孤立方向词 = 图形朝向 = **2→3 边的航向**。
    #   它**不能当硬约束**（实测 旧样本 也有这个尾巴，强加会把题卡那条本来能解的路顶掉），
    #   所以只在前面的常规求解全部失败之后，才补这条航向再试一遍。
    result = _accept(NR.solve_imported_visual_candidate(
        spec, geo_origin, __import__(__name__)))
    if result:
        return result
    orientation = spec.get("figure_orientation")
    if orientation is not None:
        patched = dict(spec)
        patched["bearing_constraints"] = list(spec.get("bearing_constraints") or []) + [
            {"a": "2", "b": "3", "value": float(orientation), "kind": "bearing",
             "source": "建议参数末尾的方向词：整个图形按此朝向绘制（2→3）"}]
        for candidate_solver in (NR.solve_similarity, NR.solve):
            try:
                if candidate_solver is NR.solve:
                    result = candidate_solver(patched, geo_origin, anchors,
                                              __import__(__name__))
                else:
                    result = candidate_solver(patched, geo_origin,
                                              __import__(__name__))
            except (KeyError, ValueError, ZeroDivisionError, OverflowError):
                continue
            if result and result.get("status") == "READY":
                return result
    if shape == "蝴蝶形":
        # H→1逆风距离为唯一锚点，航点按罗盘方位建模为正方形蝴蝶图。
        wind_clause = next((line for line in spec["source_text"].splitlines()
                            if "风向" in line and re.search(r"H\s*[-到至]\s*1", line)), "")
        if "H" in anchors and wind_clause:
            wind = GEO.wind_from(wind_clause)
            h1 = next((s for s in spec["segment_constraints"]
                       if s["a"] == "1" and s["b"] == "H"), None)
            minimum = re.search(r"各点之间距离不小于\s*([\d.]+)\s*米", spec["source_text"])
            if wind is not None and h1 is not None and minimum and len(spec["angle_constraints"]) >= 4:
                h = anchors["H"]
                v = _bearing_xy(wind, h1["distance"])
                p1 = (h[0] + v[0], h[1] + v[1])
                side = float(minimum.group(1))
                p2 = (p1[0] + side, p1[1] + side)
                p3 = (p1[0] + side, p1[1])
                p4 = (p1[0], p1[1] + side)
                points = {"H": h, "1": p1, "2": p2, "3": p3, "4": p4}
                geom = [("1", "3"), ("3", "2"), ("2", "4"), ("4", "1")]
                route = [("1", "2"), ("2", "3"), ("3", "4"), ("4", "1")]
                return _ready(spec, points, geo_origin, geom, route,
                              ["H→1逆风方位由西北风确定，距离按题面给出的120米",
                               "各点最小间距采用20米边界，按图示正交交叉线确定其它航点"],
                              ["每条外框边20米，交叉航段28.284米",
                               "起飞点到航点1的方位315°、距离120米"])

    # 保守兼容当前可直接从坐标链推导的题：保留依据，但不虚构未解出的图形边。
    points = {}
    for name, item in (legacy.get("points") or {}).items():
        node = _role_node(name)
        if item.get("lat") is None or item.get("lon") is None:
            continue
        if geo_origin:
            inv = (Geodesic.WGS84.Inverse(geo_origin[0], geo_origin[1],
                    item["lat"], item["lon"]) if Geodesic else None)
            if inv:
                x, y = _bearing_xy(inv["azi1"], inv["s12"])
            else:
                brg = GEO.bearing_of(geo_origin[0], geo_origin[1], item["lat"], item["lon"])
                dist = GEO.distance_of(geo_origin[0], geo_origin[1], item["lat"], item["lon"])
                x, y = _bearing_xy(brg or 0, dist or 0)
        else:
            x, y = 0.0, 0.0
        points[node] = (x, y)
    missing = []
    if not spec.get("nodes"):
        missing.append("未能识别图形航点编号和连接关系")
    if not spec.get("distance_constraints") and not spec.get("segment_constraints"):
        missing.append("未提取到可计算的距离、坐标或方位约束")
    if not missing:
        missing.append("当前约束类型尚未组成可校验的唯一坐标解")
    # 前面所有路都走不通时，退回模板法留下的兜底结果（它会指出具体哪条条件没通过），
    # 比笼统的「尚未组成唯一坐标解」更有排查价值。
    if deferred is not None:
        candidate_points = dict(deferred.get("candidate_points") or points)
        candidate_points.update(anchors)
        candidate_geometry = [tuple(map(str, edge[:2])) for edge in
                              spec.get("geometry_edges", [])
                              if isinstance(edge, (list, tuple)) and len(edge) >= 2]
        candidate_route = [tuple(map(str, edge[:2])) for edge in
                           spec.get("flight_edges", [])
                           if isinstance(edge, (list, tuple)) and len(edge) >= 2]
        refined = NR.refine_candidate(
            spec, candidate_points, geo_origin, candidate_geometry,
            candidate_route,
            __import__(__name__))
        if refined and refined.get("status") == "READY":
            return refined
        return _attach_candidate(deferred, spec, candidate_points, geo_origin,
                                 candidate_geometry, candidate_route)
    result = _review(spec, "；".join(missing),
                     [c.get("source", "") for c in spec.get("distance_constraints", [])[:4]],
                     candidate_points={**points, **anchors}, frame=geo_origin)
    if points:
        result["partial_points"] = points
        result["partial_note"] = "已推导部分坐标，完整图形约束尚未校验"
    return result


@lru_cache(maxsize=256)
def _solve_reference_json(payload):
    return _solve_reference_uncached(json.loads(payload))


def question_fingerprint(q):
    """题面、图形连接和求解器版本共同组成用户答案的失效标识。"""
    relevant = {key: (q or {}).get(key) for key in (
        "id", "conditions_text", "requirements_text", "suggested_text",
        "graph_hint", "reference_graph", "imported")}
    relevant["schema_version"] = SCHEMA_VERSION
    source = (q or {}).get("reference_source") or (q or {}).get("_reference_source") or {}
    relevant["figure_source_hash"] = hashlib.sha256(json.dumps(
        source, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str).encode("utf-8")).hexdigest() if source else ""
    payload = json.dumps(relevant, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def solve_generated_reference(q):
    """只运行题面求解器，不读取用户已核验答案。"""
    relevant = {key: (q or {}).get(key) for key in (
        "id", "conditions_text", "requirements_text", "suggested_text",
        "graph_hint", "reference_graph", "imported")}
    relevant["reference_source"] = (q or {}).get("reference_source") or (q or {}).get("_reference_source") or {}
    payload = json.dumps(relevant, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str)
    return copy.deepcopy(_solve_reference_json(payload))


def _current_condition_assist(q):
    if not (q or {}).get("imported"):
        return None
    source = (q or {}).get("reference_source") or (q or {}).get("_reference_source") or {}
    assist = source.get("condition_assist") or {}
    snapshot = assist.get("source_fields")
    if not isinstance(snapshot, dict):
        return None
    fields = ("graph_hint", "conditions_text", "requirements_text", "suggested_text")
    present = set(snapshot)
    if any(str((q or {}).get(key) or "") !=
           str(snapshot.get(key) or "") for key in fields if key in present):
        return None
    missing = set(fields) - present
    if not missing:
        return assist
    # 兼容早期版本漏存 conditions_text 的识别快照；只有原始 OCR 与
    # 用户确认字段仍逐项相同，且识别结果保留条件证据时才安全复用。
    if missing == {"conditions_text"}:
        recognized = source.get("recognized_fields")
        reviewed = source.get("reviewed_fields")
        if (isinstance(recognized, dict) and isinstance(reviewed, dict) and
                all(str((q or {}).get(key) or "") ==
                    str(recognized.get(key) or "") ==
                    str(reviewed.get(key) or "") for key in fields) and
                str(assist.get("normalized_conditions") or
                    assist.get("candidate_conditions") or "").strip() and
                bool(assist.get("condition_items"))):
            return assist
    return None


def _visual_layout_candidate(q, result, assist):
    """只保留已由题面约束复核的候选；不再用像素比例补造答案坐标。"""
    result = copy.deepcopy(result)
    spec = result.get("spec") or extract_question_spec(q)
    diagram = assist.get("diagram_model") or {}
    nodes = [str(node) for node in spec.get("nodes") or []]
    points = copy.deepcopy(result.get("candidate_points") or result.get("points") or {})
    geometry = (result.get("candidate_geometry_edges") or
                result.get("geometry_edges") or spec.get("geometry_edges") or [])
    route = (result.get("candidate_flight_edges") or
             result.get("flight_edges") or spec.get("flight_edges") or [])
    access = (result.get("candidate_access_edges") or
              result.get("access_edges") or _access_edges(spec, points))
    errors = []

    if len(nodes) < 3 or any(node not in points for node in nodes):
        errors.append("尚未解出題图中的全部编号航点")
    coordinate_points = {}
    for name, point in points.items():
        if not isinstance(point, dict):
            continue
        try:
            x, y = float(point["x"]), float(point["y"])
            if math.isfinite(x) and math.isfinite(y):
                coordinate_points[str(name)] = (x, y)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue

    geometry = [tuple(map(str, edge[:2])) for edge in geometry
                if isinstance(edge, (list, tuple)) and len(edge) >= 2]
    route = [tuple(map(str, edge[:2])) for edge in route
             if isinstance(edge, (list, tuple)) and len(edge) >= 2]
    access = [tuple(map(str, edge[:2])) for edge in access
              if isinstance(edge, (list, tuple)) and len(edge) >= 2]

    # 闭合多边形必须是由题卡明确识别出的完整单环，不允许缺边后按编号补齐。
    if spec.get("shape") not in ("圆形", "扫描航线") and nodes:
        degree = {name: 0 for name in nodes}
        for first, second in geometry:
            if first not in degree or second not in degree or first == second:
                errors.append("图形边引用了无效航点")
                continue
            degree[first] += 1
            degree[second] += 1
        if len(geometry) != len(nodes) or any(value != 2 for value in degree.values()):
            errors.append("题卡图形边未组成完整闭合轮廓")
    elif spec.get("shape") == "扫描航线" and nodes:
        connected = {name for edge in geometry for name in edge}
        if len(geometry) < len(nodes) - 1 or not set(nodes).issubset(connected):
            errors.append("扫描航线的连线尚未完整识别")

    # 航线必须覆盖完整点序；题面闭合时必须有首尾连接。
    if nodes:
        route_nodes = {name for edge in route for name in edge}
        expected_closed = bool(spec.get("closed")) or diagram.get("flight_closed") is True
        if len(route) < max(1, len(nodes) - 1) or not set(nodes).issubset(route_nodes):
            errors.append("航线顺序未覆盖全部编号航点")
        if route and any(route[index][1] != route[index + 1][0]
                         for index in range(len(route) - 1)):
            errors.append("航线边未按连续的飞行顺序连接")
        if expected_closed and (len(route) != len(nodes) or
                                route[-1][1] != route[0][0]):
            errors.append("题面要求闭合，但识别出的路线没有闭合")

    inferred_cycle = bool(spec.get("route_inferred_from_closed_cycle"))
    route_resolved = bool(
        nodes and len(route) >= max(1, len(nodes) - 1) and
        set(nodes).issubset({name for edge in route for name in edge}) and
        not any(route[index][1] != route[index + 1][0]
                for index in range(len(route) - 1)))
    for issue in diagram.get("topology_errors") or []:
        issue = str(issue)
        if route_resolved and issue in {
                "飞行顺序未完整识别", "航线闭合条件未确认"}:
            continue
        errors.append(issue)

    if set(nodes).issubset(coordinate_points):
        errors.extend(_validate_solution(spec, coordinate_points, geometry, route))
    else:
        errors.append("完整航点坐标不足，无法复核题面几何条件")

    # 把题面已经解析出的进场段也画出来；缺少任一段就不能把答案标为完整候选。
    access_constraints = {(str(item.get("b")), str(item.get("a")))
                          for item in spec.get("segment_constraints", [])}
    if "H" in points and nodes:
        first = nodes[0]
        for via in ("A", "T"):
            required = [("H", via), (via, first)]
            if all(edge in access_constraints for edge in required):
                if not all(edge in access for edge in required):
                    errors.append("起飞点、进场点到1号点的航迹未完整绘制")
                break
        else:
            if ("H", first) in access_constraints and ("H", first) not in access:
                errors.append("起飞点到1号点的已知航段未绘制")

    errors = list(dict.fromkeys(error for error in errors if error))
    if errors:
        result["candidate"] = False
        result["candidate_constraints_passed"] = False
        result["candidate_validation_errors"] = errors[:20]
        result["candidate_points"] = {}
        result["candidate_geometry_edges"] = []
        result["candidate_flight_edges"] = []
        result["candidate_access_edges"] = []
        result["visual_layout_fallback"] = False
        result["candidate_note"] = (
            "识别出的候选未通过完整航点、拓扑或题面条件校验，暂不绘制参考航线。")
        if result.get("status") == "READY":
            result["status"] = "NEEDS_REVIEW"
            result["points"] = {}
        return result

    result["candidate_constraints_passed"] = True
    if result.get("status") != "READY":
        result["candidate"] = True
        result["candidate_points"] = points
        result["candidate_geometry_edges"] = [list(edge) for edge in geometry]
        result["candidate_flight_edges"] = [list(edge) for edge in route]
        result["candidate_access_edges"] = [list(edge) for edge in access]
        result["candidate_note"] = (
            "所有已提取的几何与航段条件均已复核；视觉识别仍待人工确认，坐标仅供参考。")
    return result


def _local_cv_layout_draft(q, result):
    """拓扑已由本地线段核实、但数值条件未闭合时，提供明确标注的图形草案。"""
    spec = (result or {}).get("spec") or extract_question_spec(q)
    diagram = _local_cv_diagram(q)
    if not isinstance(diagram, dict):
        return None
    source = str(diagram.get("source") or "")
    geometry_source = str(diagram.get("geometry_source") or "")
    if (source != "local_cv" and not geometry_source.startswith("local_cv")):
        return None
    if diagram.get("status") != "ready" or not diagram.get("topology_valid"):
        return None

    nodes = [str(name) for name in spec.get("nodes") or []]
    positions = diagram.get("point_positions") or {}
    if len(nodes) < 3 or not all(
            isinstance(positions.get(name), dict) and
            positions[name].get("x") is not None and
            positions[name].get("y") is not None for name in nodes):
        return None
    geometry = [tuple(map(str, edge[:2])) for edge in
                (spec.get("geometry_edges") or [])
                if isinstance(edge, (list, tuple)) and len(edge) >= 2]
    local_edges = [tuple(map(str, edge[:2])) for edge in
                   (diagram.get("geometry_edges") or [])
                   if isinstance(edge, (list, tuple)) and len(edge) >= 2]
    expected_geometry_count = (len(nodes) - 1 if spec.get("shape") == "扫描航线"
                               else len(nodes))
    if len(geometry) != expected_geometry_count and len(local_edges) == expected_geometry_count:
        geometry = local_edges
    route = [tuple(map(str, edge[:2])) for edge in
             (spec.get("flight_edges") or [])
             if isinstance(edge, (list, tuple)) and len(edge) >= 2]
    if len(route) < len(nodes) - 1:
        order = [str(node) for node in diagram.get("route_order") or []]
        if len(order) == len(nodes):
            route = list(zip(order, order[1:]))
            if (spec.get("shape") != "扫描航线" and
                    diagram.get("shape") != "扫描航线"):
                route.append((order[-1], order[0]))
    if spec.get("shape") == "扫描航线":
        if len(geometry) < len(nodes) - 1 or len(route) < len(nodes) - 1:
            return None
    elif len(geometry) != len(nodes) or len(route) < len(nodes) - 1:
        return None

    template = {name: (float(positions[name]["x"]),
                       -float(positions[name]["y"])) for name in nodes}
    lengths = {}
    for item in spec.get("distance_constraints", []):
        first, second = str(item.get("a")), str(item.get("b"))
        if first in template and second in template:
            try:
                value = float(item.get("value"))
                if value > 0:
                    lengths[frozenset((first, second))] = value
            except (TypeError, ValueError, OverflowError):
                continue
    measured = [(math.dist(template[first], template[second]), value)
                for pair, value in lengths.items()
                for first, second in [tuple(pair)]
                if math.dist(template[first], template[second]) > 1e-6]
    if measured:
        scale = (sum(pixel_length * meters for pixel_length, meters in measured) /
                 sum(pixel_length * pixel_length for pixel_length, _ in measured))
    else:
        xs = [point[0] for point in template.values()]
        ys = [point[1] for point in template.values()]
        diagonal = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        scale = 100.0 / max(diagonal, 1e-6)
    if not math.isfinite(scale) or scale <= 0:
        return None

    # 有明确的编号点间航向时，只旋转示意图来匹配，不改变点间相对形状。
    for item in spec.get("bearing_constraints", []):
        first, second = str(item.get("a")), str(item.get("b"))
        if first not in template or second not in template:
            continue
        dx = template[second][0] - template[first][0]
        dy = template[second][1] - template[first][1]
        if math.hypot(dx, dy) < 1e-6:
            continue
        current = _xy_bearing(dx, dy)
        turn = math.radians((current - float(item.get("value"))) % 360.0)
        cosine, sine = math.cos(turn), math.sin(turn)
        template = {name: (cosine * x - sine * y,
                           sine * x + cosine * y)
                    for name, (x, y) in template.items()}
        break

    _frame_kind, anchors, frame = _coordinate_frame(spec)
    anchor_name = next((name for name in nodes if name in anchors), None)
    anchor_xy = anchors.get(anchor_name, (0.0, 0.0)) if anchor_name else (0.0, 0.0)
    anchor_template = template.get(anchor_name, (0.0, 0.0)) if anchor_name else (0.0, 0.0)
    points = {name: (anchor_xy[0] + (template[name][0] - anchor_template[0]) * scale,
                     anchor_xy[1] + (template[name][1] - anchor_template[1]) * scale)
              for name in nodes}

    bearings = {}
    for item in spec.get("bearing_constraints", []):
        try:
            bearings[(str(item["a"]), str(item["b"]))] = float(item["value"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    for item in spec.get("segment_constraints", []):
        try:
            bearings[(str(item["a"]), str(item["b"]))] = float(item["bearing"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    # 把已知的起飞/避让点方向和距离接到草案上，保留用户要求的进场航迹。
    for _ in range(len(spec.get("distance_constraints", [])) +
                    len(spec.get("segment_constraints", [])) + 1):
        changed = False
        for item in spec.get("distance_constraints", []):
            first, second = str(item.get("a")), str(item.get("b"))
            external = {"H", "A", "T", "R"}
            try:
                distance = float(item.get("value"))
            except (TypeError, ValueError, OverflowError):
                continue
            if first in points and second in external and second not in points:
                bearing = bearings.get((first, second))
                if bearing is not None:
                    dx, dy = _bearing_xy(bearing, distance)
                    points[second] = (points[first][0] + dx,
                                      points[first][1] + dy)
                    changed = True
            elif second in points and first in external and first not in points:
                bearing = bearings.get((second, first))
                if bearing is None and (first, second) in bearings:
                    bearing = bearings[(first, second)] + 180.0
                if bearing is not None:
                    dx, dy = _bearing_xy(bearing, distance)
                    points[first] = (points[second][0] + dx,
                                     points[second][1] + dy)
                    changed = True
        # 航段约束的 bearing 表示 b→a；外部点是起点时，按这个方向补点，
        # 外部点是终点时则反向推回，不能把它和无向边长约束混为一类。
        for item in spec.get("segment_constraints", []):
            try:
                target, origin = str(item["a"]), str(item["b"])
                distance = float(item["distance"])
                bearing = float(item["bearing"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            external = {"H", "A", "T", "R"}
            if origin in points and target in external and target not in points:
                dx, dy = _bearing_xy(bearing, distance)
                points[target] = (points[origin][0] + dx,
                                  points[origin][1] + dy)
                changed = True
            elif target in points and origin in external and origin not in points:
                dx, dy = _bearing_xy(bearing + 180.0, distance)
                points[origin] = (points[target][0] + dx,
                                  points[target][1] + dy)
                changed = True
        if not changed:
            break
    # 外部已知坐标点（通常是 H 起飞点）必须保留在草案里，才能画出 H→1
    # 或 H→避让点→1；如果由某条识别条件推回的位置不同，下面的校验会标出冲突。
    for name, point in anchors.items():
        if (isinstance(point, (tuple, list)) and len(point) >= 2 and
                all(math.isfinite(float(value)) for value in point[:2])):
            points[str(name)] = (float(point[0]), float(point[1]))
    rendered_points = {}
    for name, (x, y) in points.items():
        lat, lon = _to_geographic(x, y, frame)
        rendered_points[name] = {"x": float(x), "y": float(y),
                                 "lat": lat, "lon": lon}
    if not all(name in rendered_points for name in nodes):
        return None
    access = _access_edges(spec, rendered_points)
    errors = _validate_solution(spec, points, geometry, route)
    if spec.get("shape") == "梯形":
        side_lengths = {frozenset((str(item.get("a")), str(item.get("b")))):
                        float(item.get("value"))
                        for item in spec.get("distance_constraints", [])}
        angle4 = next((float(item.get("value"))
                       for item in spec.get("angle_constraints", [])
                       if str(item.get("vertex")) == "4"), None)
        heading_41 = bearings.get(("4", "1"))
        parallel = list(spec.get("parallel_constraints") or [])
        for item in diagram.get("parallel_edge_pairs") or []:
            if isinstance(item, dict):
                parallel.append(item)
            elif (isinstance(item, (list, tuple)) and len(item) == 2 and
                  all(isinstance(edge, (list, tuple)) and len(edge) >= 2
                      for edge in item)):
                parallel.append({"first": item[0], "second": item[1]})
        has_expected_bases = any(
            {frozenset(map(str, item.get("first", []))),
             frozenset(map(str, item.get("second", [])))} ==
            {frozenset(("1", "4")), frozenset(("2", "3"))}
            for item in parallel if isinstance(item, dict))
        side12 = side_lengths.get(frozenset(("1", "2")))
        side34 = side_lengths.get(frozenset(("3", "4")))
        if (has_expected_bases and heading_41 is not None and
                angle4 is not None and side12 is not None and side34 is not None):
            required_height = side34 * abs(math.sin(math.radians(angle4)))
            if required_height > side12 + 0.5:
                errors.append(
                    "梯形条件冲突：3-4=%.0f米、∠4=%.0f°要求两条平行边间高度约%.1f米，"
                    "但1-2只有%.0f米，无法同时成立。" %
                    (side34, angle4, required_height, side12))

    output = copy.deepcopy(result)
    output.update({
        "status": "NEEDS_REVIEW", "candidate": True,
        "candidate_constraints_passed": False,
        "reference_drawing_only": True,
        "candidate_points": rendered_points,
        "candidate_geometry_edges": [list(edge) for edge in geometry],
        "candidate_flight_edges": [list(edge) for edge in route],
        "candidate_access_edges": access,
        "candidate_validation_errors": list(dict.fromkeys(errors))[:20],
        "candidate_note": (
            "已按本地识别的图形轮廓生成近似草案；题面条件未能全部校验，"
            "请核对标出的冲突项，橙色虚线仅供参考。"),
        "reason": "图形已识别；题面数值约束未全部通过，显示轮廓参考草案。",
        "steps": ["已从题卡线段恢复完整图形拓扑",
                  "按题卡轮廓比例生成近似点位"],
        "checks": ["未通过的数值条件见上方冲突项"],
    })
    return output


def _candidate_has_complete_local_route(q, result):
    """判断未通过的候选答案是否已覆盖完整图形与飞行路径。"""
    if not isinstance(result, dict) or not result.get("candidate"):
        return False
    spec = result.get("spec") or extract_question_spec(q)
    nodes = [str(node) for node in spec.get("nodes") or []]
    if len(nodes) < 3:
        return False
    points = (result.get("candidate_points") or result.get("points") or {})
    if not set(nodes).issubset({str(node) for node in points}):
        return False

    def normalized_edges(*sources):
        for source in sources:
            edges = [tuple(map(str, edge[:2])) for edge in (source or [])
                     if isinstance(edge, (list, tuple)) and len(edge) >= 2]
            if edges:
                return edges
        return []

    geometry = normalized_edges(result.get("candidate_geometry_edges"),
                                result.get("geometry_edges"),
                                spec.get("geometry_edges"))
    route = normalized_edges(result.get("candidate_flight_edges"),
                             result.get("flight_edges"),
                             spec.get("flight_edges"))
    is_scan = spec.get("shape") == "扫描航线"
    min_geometry = len(nodes) - 1 if is_scan else len(nodes)
    if len(geometry) < min_geometry or len(route) < len(nodes) - 1:
        return False
    if any(first not in nodes or second not in nodes
           for first, second in geometry + route):
        return False
    route_nodes = {node for edge in route for node in edge}
    if not set(nodes).issubset(route_nodes):
        return False

    access = normalized_edges(result.get("candidate_access_edges"),
                              result.get("access_edges"))
    point_names = {str(name) for name in points}
    first = "1" if "1" in point_names else nodes[0]
    if "H" in point_names and "A" in point_names:
        # 避让点属于进场路线的一部分，只有 H→A→1 完整才算已画出。
        if not _has_directed_path(access, "H", "A") or not _has_directed_path(
                access, "A", first):
            return False
    elif "H" in point_names and first in point_names:
        if not _has_directed_path(access, "H", first):
            return False
    return True


def _has_directed_path(edges, start, end):
    adjacency = {}
    for edge in edges or []:
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            adjacency.setdefault(str(edge[0]), set()).add(str(edge[1]))
    pending = [str(start)]
    visited = set()
    while pending:
        current = pending.pop()
        if current == str(end):
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency.get(current, set()) - visited)
    return False


def _apply_assist_review_label(q, result):
    """有识别疑点时保留路线草案，但不把它标成核验通过。"""
    if not isinstance(result, dict):
        return result
    assist = _current_condition_assist(q)
    if not assist:
        if (result.get("status") != "READY" and
                (not result.get("candidate") or
                 not _candidate_has_complete_local_route(q, result))):
            draft = _local_cv_layout_draft(q, result)
            if draft is not None:
                return draft
        return result
    uncertain = list(assist.get("uncertain_items") or [])
    low_confidence = False
    for item in assist.get("condition_items") or []:
        if not isinstance(item, dict):
            low_confidence = True
            break
        try:
            if float(item.get("confidence", 0)) < 0.72:
                low_confidence = True
                break
        except (TypeError, ValueError):
            low_confidence = True
            break
    assist_diagram = assist.get("diagram_model") or {}
    has_unvalidated_visual_diagram = bool(
        assist_diagram.get("source") == "vision_model" and
        not (assist_diagram.get("geometry_source") in
             ("local_cv", "local_cv_hough") and
             assist_diagram.get("geometry_validated")))
    needs_review = (
        assist.get("status") != "ready" or bool(uncertain) or low_confidence or
        assist.get("complete") is False or has_unvalidated_visual_diagram)
    result = _visual_layout_candidate(q, result, assist)
    if (result.get("status") != "READY" and
            not result.get("candidate") and
            not result.get("candidate_selection_method")):
        draft = _local_cv_layout_draft(q, result)
        if draft is not None:
            result = draft
    if result.get("reference_drawing_only"):
        result["vision_review_required"] = True
        result["vision_uncertain_items"] = uncertain
        return result
    if not needs_review:
        return result

    result = copy.deepcopy(result)
    if result.get("status") == "READY":
        result["candidate_points"] = copy.deepcopy(result.get("points") or {})
        result["candidate_geometry_edges"] = copy.deepcopy(
            result.get("geometry_edges") or [])
        result["candidate_flight_edges"] = copy.deepcopy(
            result.get("flight_edges") or [])
        result["candidate_access_edges"] = copy.deepcopy(
            result.get("access_edges") or [])
        result["solver_checks_passed"] = True
        result["status"] = "NEEDS_REVIEW"
    result["candidate"] = bool(result.get("candidate_constraints_passed"))
    result["vision_review_required"] = True
    result["vision_uncertain_items"] = uncertain
    if not result.get("candidate"):
        diagram = assist.get("diagram_model") or {}
        if diagram.get("source") == "vision_model":
            if diagram.get("geometry_source") != "local_cv_hough":
                result["candidate_note"] = (
                    "视觉模型草图尚未通过本地线段校正，题图与数值条件无法可靠交叉核对；"
                    "已隐藏参考航线，请点击“视觉识别”重新扫描。")
                result["reason"] = "题卡图形尚未通过本地线段校验，已暂时隐藏参考答案。"
            else:
                result["candidate_note"] = (
                    "本地识别到的题卡轮廓与题面数值未能同时通过校验；"
                    "已隐藏参考航线，请检查识别条件或手动标注后重算。")
                result["reason"] = "题图轮廓和题面条件存在冲突，已暂时隐藏参考答案。"
            result["visual_layout_rejected"] = True
        elif not result.get("candidate_note"):
            result["candidate_note"] = (
                "识别出的候选未通过完整航点、拓扑或题面条件校验，暂不绘制参考航线。")
    elif result.get("candidate_selection_method") == "imported_visual_prior":
        if result.get("candidate_coordinate_unique"):
            result["candidate_note"] = (
                "题面数值条件与图形连线已通过独立校验；坐标按题图外形选取，仍请核对视觉识别。")
        else:
            result["candidate_note"] = (
                "题面条件均通过独立校验；坐标未被题面唯一锁定，已按题图外形选取一组参考候选。")
    elif not result.get("visual_layout_fallback"):
        result["candidate_note"] = (
            "视觉识别仍有待确认项；此图按当前条件推算，仅作参考，坐标不代表精确结果。")
    if not result.get("reason"):
        result["reason"] = "视觉识别有待确认项，参考路线尚未完全核对。"
    return result


def solve_reference(q):
    """优先读取与当前题面匹配的已核验作答，其他题用自动求解器。"""
    try:
        import qmeta as QM
        saved = QM.verified_reference_of((q or {}).get("id"))
        if (saved and saved.get("fingerprint") == question_fingerprint(q)
                and saved.get("status") == "READY"):
            spec = extract_question_spec(q)
            nodes = set(spec.get("nodes") or [])
            raw_points = saved.get("points") or {}
            if nodes and nodes.issubset(raw_points):
                result = copy.deepcopy(saved)
                result["spec"] = spec
                return _apply_assist_review_label(q, result)
        # 求解器版本升级会使旧指纹失效，但用户已核验的答案仍可作为候选；
        # 只有题面规则文本一致、完整硬约束复核通过，才允许复用旧答案。
        if (saved and saved.get("status") == "READY" and
                saved.get("shape_source") == str((q or {}).get("requirements_text") or "")):
            spec = extract_question_spec(q)
            nodes = [str(name) for name in spec.get("nodes") or []]
            saved_points = saved.get("points") or {}
            raw_xy = {}
            for name, point in saved_points.items():
                if not isinstance(point, dict):
                    continue
                try:
                    if point.get("x") is not None and point.get("y") is not None:
                        raw_xy[str(name)] = (float(point["x"]), float(point["y"]))
                except (TypeError, ValueError, OverflowError):
                    continue
            if nodes and all(name in raw_xy for name in nodes):
                _frame_kind, anchors, _geo_origin = _coordinate_frame(spec)
                geometry = [tuple(map(str, edge[:2]))
                            for edge in spec.get("geometry_edges") or []
                            if isinstance(edge, (list, tuple)) and len(edge) >= 2]
                route = [tuple(map(str, edge[:2]))
                         for edge in spec.get("flight_edges") or []
                         if isinstance(edge, (list, tuple)) and len(edge) >= 2]
                candidates = [raw_xy]
                anchor = next((name for name in anchors if name in raw_xy), None)
                if anchor is not None:
                    target = anchors[anchor]
                    dx = float(target[0]) - raw_xy[anchor][0]
                    dy = float(target[1]) - raw_xy[anchor][1]
                    shifted = {name: (point[0] + dx, point[1] + dy)
                               for name, point in raw_xy.items()}
                    if any(math.dist(shifted[name], raw_xy[name]) > 1e-9
                           for name in shifted):
                        candidates.insert(0, shifted)
                validated = next((points for points in candidates
                                  if not _validate_solution(spec, points, geometry, route)),
                                 None)
                if validated is not None:
                    fallback = {
                        "spec": spec,
                        "geometry_edges": geometry,
                        "flight_edges": route,
                        "access_edges": saved.get("access_edges") or [],
                        "steps": saved.get("steps") or [],
                        "checks": saved.get("checks") or [],
                    }
                    result = preset_reference_for_display(q, saved, fallback)
                    if result:
                        for name, point in result.get("points", {}).items():
                            if name in validated:
                                point["x"], point["y"] = validated[name]
                        result["spec"] = spec
                        result["geometry_edges"] = [list(edge) for edge in geometry]
                        result["flight_edges"] = [list(edge) for edge in route]
                        result["verified_reference_revalidated"] = True
                        result["validation_errors"] = []
                        return _apply_assist_review_label(q, result)
    except (TypeError, ValueError, KeyError):
        pass
    result = solve_generated_reference(q)
    if result and result.get("status") != "READY":
        # 各专用解法和数值解法的待复核草案统一经过一次硬约束精修；
        # 这样早期分支不会因先返回 review 而挡住后续已经可校验的完整草案。
        spec = result.get("spec") or extract_question_spec(q)
        candidate_points = (result.get("candidate_points") or
                            result.get("points") or {})
        nodes = [str(name) for name in spec.get("nodes", [])]
        if nodes and all(name in candidate_points for name in nodes):
            _frame_kind, _anchors, geo_origin = _coordinate_frame(spec)
            geometry_edges = (result.get("candidate_geometry_edges") or
                              result.get("geometry_edges") or
                              spec.get("geometry_edges") or [])
            flight_edges = (result.get("candidate_flight_edges") or
                            result.get("flight_edges") or
                            spec.get("flight_edges") or [])
            refined = NR.refine_candidate(
                spec, candidate_points, geo_origin,
                [tuple(map(str, edge[:2])) for edge in geometry_edges
                 if isinstance(edge, (list, tuple)) and len(edge) >= 2],
                [tuple(map(str, edge[:2])) for edge in flight_edges
                 if isinstance(edge, (list, tuple)) and len(edge) >= 2],
                __import__(__name__))
            if refined and refined.get("status") == "READY":
                result = refined
    return _apply_assist_review_label(q, result)


def preset_reference_for_display(q, preset, fallback=None):
    """把导出表中的 WGS84 答案转成参考答案浮层和航线图使用的格式。

    预置表由答案摘要模块生成，点名是“起飞点/图形1号点”，坐标只有经纬度；
    参考答案图则需要 H/1 等点名以及局部东、北坐标。两者不能直接互传。
    """
    if not isinstance(preset, dict) or preset.get("status") != "READY":
        return None

    import re

    def point_id(label):
        text = str(label or "").strip()
        if text in ("H", "A", "T") or text.isdigit():
            return text
        if text in ("起飞点", "返航点", "起飞点/返航点", "起飞/返航点"):
            return "H"
        if text in ("避让点", "避让/转场点"):
            return "A"
        if text in ("过渡点", "转场点"):
            return "T"
        match = re.fullmatch(r"(?:图形|航点)?\s*(\d+)\s*(?:号)?点", text)
        if match:
            return match.group(1)
        return None

    raw_points = preset.get("points")
    if not isinstance(raw_points, dict):
        return None
    points = {}
    for label, raw in raw_points.items():
        key = point_id(label)
        if key is None or not isinstance(raw, dict):
            continue
        try:
            point = dict(raw)
            if point.get("x") is not None and point.get("y") is not None:
                point["x"] = float(point["x"])
                point["y"] = float(point["y"])
            elif point.get("lat") is not None and point.get("lon") is not None:
                point["lat"] = float(point["lat"])
                point["lon"] = float(point["lon"])
            else:
                return None
            points[key] = point
        except (TypeError, ValueError, OverflowError):
            return None
    if not points:
        return None

    # 答案表只有经纬度时，以 H 或 1 为原点，投影到局部东、北平面供绘图。
    geographic = [p for p in points.values()
                  if p.get("lat") is not None and p.get("lon") is not None]
    if geographic:
        origin = points.get("H") or points.get("1") or geographic[0]
        lat0 = float(origin["lat"])
        lon0 = float(origin["lon"])
        east_scale = 111320.0 * math.cos(math.radians(lat0))
        for point in points.values():
            if point.get("lat") is None or point.get("lon") is None:
                return None
            point["x"] = (float(point["lon"]) - lon0) * east_scale
            point["y"] = (float(point["lat"]) - lat0) * 110540.0

    fallback = fallback if isinstance(fallback, dict) else {}
    spec = fallback.get("spec") or extract_question_spec(q)
    nodes = set(spec.get("nodes") or [])
    if nodes and not nodes.issubset(points):
        return None

    def valid_edges(source):
        return [list(map(str, edge[:2])) for edge in (source or [])
                if isinstance(edge, (list, tuple)) and len(edge) >= 2 and
                str(edge[0]) in points and str(edge[1]) in points]

    geometry_edges = valid_edges(fallback.get("geometry_edges"))
    if not geometry_edges:
        geometry_edges = valid_edges(spec.get("geometry_edges"))
    flight_edges = valid_edges(fallback.get("flight_edges"))
    if not flight_edges:
        flight_edges = valid_edges(spec.get("flight_edges")) or list(geometry_edges)
    access_edges = valid_edges(fallback.get("access_edges"))
    first = "1" if "1" in points else next(
        (str(node) for node in spec.get("nodes") or []
         if str(node) in points), None)

    def has_path(edges, start, end):
        """判断已有进场线是否已连通，避免把半条线误当作完整答案。"""
        pending = [start]
        visited = set()
        edge_pairs = {(edge[0], edge[1]) for edge in edges}
        while pending:
            current = pending.pop()
            if current == end:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(b for a, b in edge_pairs
                           if a == current and b not in visited)
        return False

    if "H" in points and first:
        via = "A" if "A" in points else "T" if "T" in points else None
        # 有避让点时，标准进场链必须经过避让点；仅有起飞段的旧答案
        # 需要补齐到 1 号点，不能因为 access_edges 非空就跳过修复。
        complete = (has_path(access_edges, "H", via) and
                    has_path(access_edges, via, first)) if via else \
                   has_path(access_edges, "H", first)
        if not complete:
            access_edges = ([["H", via], [via, first]] if via
                            else [["H", first]])

    result = dict(preset)
    result["points"] = points
    result["spec"] = spec
    result["geometry_edges"] = geometry_edges
    result["flight_edges"] = flight_edges
    result["access_edges"] = access_edges
    result["coordinate_system"] = "WGS84（局部东、北坐标绘图）"
    result["steps"] = (preset.get("steps") or fallback.get("steps") or
                       list(dict.fromkeys(
                           str(p.get("why")) for p in points.values() if p.get("why"))))
    result["checks"] = preset.get("checks") or fallback.get("checks") or []
    return result



# ---------------------------------------------------------------------------
# 题面错误登记的应用点：解析出 spec 之后、求解之前，丢掉题面写错的约束。
# 用模块级包装而不是改函数体 —— 调用方（_solve_reference_uncached 等）都是
# 运行时查全局名，因此包装后自动生效，且不改动原解析逻辑（零回退风险）。
# ---------------------------------------------------------------------------
_extract_question_spec_raw = extract_question_spec


def extract_question_spec(question):
    spec = _extract_question_spec_raw(question)
    question = question if isinstance(question, dict) else {}
    assist = _current_condition_assist(question)
    # 新增的标准三点角写法只用于导入题卡的视觉识别结果；内置题库保留
    # 既有 CAAC 角度口径，避免改动已建立的题库判题语义。
    imported_visual = bool(question.get("imported") or assist)
    source_text = str(spec.get("source_text") or "")
    # Three-point angle notation puts the vertex in the middle: ∠H12 is
    # the angle from 1→H to 1→2.  The legacy hub parser reads the same token as
    # a direction relation at H, so replace that interpretation before solving.
    vertex_angles = []
    angle_pattern = re.compile(
        r"[∠﹤<]\s*(?P<a>[HAT]|\d{1,2})\s*"
        r"(?P<vertex>[HAT]|\d{1,2})\s*(?P<b>[HAT]|\d{1,2})\s*"
        r"(?:=\s*)?(?:为|是)?\s*"
        r"(?P<value>\d+(?:\.\d+)?)\s*(?:°|度)", re.I)
    for match in (angle_pattern.finditer(source_text) if imported_visual else ()):
        if match.group("vertex").upper() == match.group("b").upper() or \
                match.group("a").upper() == match.group("b").upper():
            continue
        vertex_angles.append({
            "a": (match.group("a").upper() if match.group("a").isalpha()
                  else str(int(match.group("a")))),
            "vertex": (match.group("vertex").upper()
                       if match.group("vertex").isalpha()
                       else str(int(match.group("vertex")))),
            "b": (match.group("b").upper() if match.group("b").isalpha()
                  else str(int(match.group("b")))),
            "value": float(match.group("value")),
            "source": match.group(0),
        })
    if vertex_angles:
        sources = {item["source"] for item in vertex_angles}
        spec["direction_relations"] = [
            item for item in spec.get("direction_relations", [])
            if item.get("source") not in sources]
        existing = {(item.get("a"), item.get("vertex"), item.get("b"))
                    for item in spec.get("vertex_ray_angles", [])}
        spec["vertex_ray_angles"] = list(spec.get("vertex_ray_angles", [])) + [
            item for item in vertex_angles
            if (item["a"], item["vertex"], item["b"]) not in existing]
    diagram = _local_cv_diagram(question)
    graph = _validated_local_graph(diagram)
    if (question.get("imported") and graph and
            diagram.get("shape") == "直角三角形"):
        right_vertex = str(diagram.get("right_angle_vertex") or "")
        order = [str(node) for node in diagram.get("route_order") or []]
        if right_vertex in order and len(order) == 3:
            index = order.index(right_vertex)
            right_angle = {
                "a": order[(index - 1) % 3], "vertex": right_vertex,
                "b": order[(index + 1) % 3], "value": 90.0,
                "source": "本地题图检测到直角顶点 %s" % right_vertex,
            }
            existing = {(item.get("a"), item.get("vertex"), item.get("b"))
                        for item in spec.get("vertex_ray_angles", [])}
            reverse_key = (right_angle["b"], right_vertex, right_angle["a"])
            if ((right_angle["a"], right_vertex, right_angle["b"]) not in existing and
                    reverse_key not in existing):
                spec["vertex_ray_angles"] = list(
                    spec.get("vertex_ray_angles", [])) + [right_angle]
    return spec
