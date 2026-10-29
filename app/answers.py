"""本地判题字段解析；导入题卡的参考答案由用户配置的视觉 API 生成。

考试真正要判的东西只有两类：

1. **关键点坐标**：起飞点 → 避让点/过渡点 → 图形 1 号点。
   题干用「方位角 + 距离」的链条描述它们的关系，全部可以用球面正算还原。
2. **飞行参数**：边长、各点高度、水平/垂直速度、停留时间、转弯方式、循环次数。
   题干里的 ``suggested_text``（"a 值建议为 300 米"）就是标准值来源。

推导可信度分三档，写进 ``conf`` 字段：

- ``full``    关键点全部算出且链条完整 → 参与判分
- ``partial`` 只算出部分点 → 只判算出来的那些
- ``none``    题干信息不足（纯图形题、坐标给在图形点上等）→ 只展示参考，不判分
"""

from __future__ import annotations

import re

import geo
import config as cfg
import geometry

# 角色名（统一内部标识）
TAKEOFF = "起飞点"
AVOID = "避让点"
TRANSIT = "过渡点"
# 图形点统一成 "图形N号点"

# 角色识别：文本片段 → 内部标识。
# "起飞" 就够了 —— 题干里写成"起飞后""起飞点""从起飞点"都有，
# 只认"起飞点"会把最常见的"起飞后需向…飞行"整句漏掉（链子直接断）。
_ROLE_PATTERNS = (
    (re.compile(r"起飞"), TAKEOFF),
    (re.compile(r"避让"), AVOID),
    (re.compile(r"过渡"), TRANSIT),
    (re.compile(r"转场|中转"), TRANSIT),
)

# 图形点：兼容 "题卡图形1号点" / "图形1号点" / "1号点" / "航点5" / "航点12"
_PT_RE = re.compile(r"(?:题卡|图形)?\s*(\d+)\s*号?\s*航?\s*点|航\s*点\s*(\d+)")
_AVOID_REF_PATTERNS = (
    re.compile(r"避让点\s*(?:编号(?:为|是)?|为|是|取|选为|对应|设为|[=:：])\s*(?:题卡|图形)?\s*(\d+)\s*号?\s*点?"),
    re.compile(r"(?:将|把)?\s*(?:题卡|图形)?\s*(\d+)\s*号点\s*(?:作为|设为|定义为|即为)\s*避让点"),
)
_TRANSIT_REF_PATTERNS = (
    re.compile(r"(?:过渡点|转场点|中转点)\s*(?:编号(?:为|是)?|明确为|为|是|取|选为|对应|设为|[=:：])\s*(?:题卡|图形)?\s*(\d+)\s*号?点?"),
    re.compile(r"(?:将|把)?\s*(?:题卡|图形)?\s*(\d+)\s*号点\s*(?:作为|设为|定义为|即为)\s*(?:过渡点|转场点|中转点)"),
)
_AVOID_CONTEXT_RE = re.compile(r"避让点|禁飞区|空域限制|限制空域|空域.{0,4}限制|不得进入空域|禁止进入空域")
_TRANSIT_CONTEXT_RE = re.compile(r"过渡点|转场点|中转点")


def _iter_pt(text):
    """产出 (位置, "图形N号点")。"""
    for m in _PT_RE.finditer(text):
        n = m.group(1) or m.group(2)
        yield m.start(), "图形%s号点" % n


def _role_at(text):
    """从一小段文字里认出角色名（含图形点）。认不出返回 None。"""
    for pat, name in _ROLE_PATTERNS:
        if pat.search(text):
            return name
    for _, name in _iter_pt(text):
        return name
    return None


def _find_roles(text):
    """按出现顺序列出文本里所有角色：[(位置, 角色名), ...]"""
    out = []
    for pat, name in _ROLE_PATTERNS:
        for m in pat.finditer(text):
            out.append((m.start(), name))
    out.extend(_iter_pt(text))
    # 同一位置可能被多个模式命中（"避让点" 同时命中 "避让" 和图形点正则的边缘
    # 情况），按位置排序后把重叠的同类项去掉。
    out.sort()
    ded = []
    for pos, name in out:
        if ded and ded[-1][1] == name and pos - ded[-1][0] <= 4:
            continue
        ded.append((pos, name))
    return ded


def _avoid_point_reference(cond_text):
    """返回避让点关联的明确图形点号；多项互相冲突时保留为歧义。"""
    found = []
    for pattern in _AVOID_REF_PATTERNS:
        found.extend(int(m.group(1)) for m in pattern.finditer(cond_text or ""))
    for clause in re.split(r"[。；;\n]", cond_text or ""):
        if (_AVOID_CONTEXT_RE.search(clause) and
                re.search(r"可能|任选|不确定|或者|\s或\s|(?:^|，|,)或(?:图形)?", clause)):
            found.extend(int(m.group(1)) for m in re.finditer(
                r"(?:题卡|图形)\s*(\d+)\s*号点", clause))
    distinct = sorted(set(found))
    if len(distinct) == 1:
        return distinct[0], distinct
    return None, distinct


def _resolve_avoid_point(q, pts, meta):
    """补齐可推导的避让点别名，并返回可展示的判定依据。"""
    cond = geo.norm(q.get("conditions_text") or "")
    explicit, candidates = _avoid_point_reference(cond)
    qid = str(q.get("id", ""))
    overrides = cfg.get("avoid_point_overrides", {})
    manual = overrides.get(qid) if isinstance(overrides, dict) else None
    if manual is None:
        manual = q.get("avoid_point_override")

    if manual is not None and str(manual).isdigit():
        selected = int(manual)
        basis = "用户手动指定图形%d号点" % selected
        source = "manual"
    elif candidates and explicit is None:
        return {"point": None, "basis": "题干同时关联多个图形点，无法安全确定避让点",
                "source": "ambiguous", "candidates": candidates}
    elif explicit is not None:
        selected = explicit
        basis = "题干明确指定避让点为图形%d号点" % selected
        source = "explicit"
    elif AVOID in pts:
        return {"point": AVOID, "basis": meta.get(AVOID, {}).get("why", "由题干航点关系推导"),
                "source": "text", "candidates": []}
    elif _AVOID_CONTEXT_RE.search(cond):
        selected = 1
        basis = "题干提到避让点或空域限制但未标编号，按默认图形1号点处理"
        source = "default"
    else:
        return {"point": None, "basis": "题干未提及避让点或空域限制", "source": "none",
                "candidates": []}

    graphic_name = "图形%d号点" % selected
    if graphic_name not in pts:
        return {"point": None, "basis": basis + "；该图形点坐标尚未推导出来",
                "source": source, "candidates": candidates or [selected]}
    latlon = pts[graphic_name]
    pts[AVOID] = latlon
    point_meta = dict(meta.get(graphic_name, {}))
    point_meta["why"] = basis
    point_meta["ref"] = graphic_name
    meta[AVOID] = point_meta
    return {"point": AVOID, "basis": basis, "source": source,
            "candidates": candidates or [selected], "graphic_point": graphic_name}


def _resolve_transit_point(q, pts, meta):
    """按题干点号或默认规则把过渡点映射到题卡图形航点。"""
    cond = geo.norm(q.get("conditions_text") or "")
    found = []
    for pattern in _TRANSIT_REF_PATTERNS:
        found.extend(int(match.group(1)) for match in pattern.finditer(cond))
    distinct = sorted(set(found))
    if len(distinct) > 1:
        return {"point": None, "basis": "题干对过渡点给出了多个图形点编号，需人工确认",
                "source": "ambiguous", "candidates": distinct}
    if TRANSIT in pts:
        return {"point": TRANSIT,
                "basis": meta.get(TRANSIT, {}).get("why", "由题干航点关系推导过渡点"),
                "source": "text", "candidates": distinct}
    if not _TRANSIT_CONTEXT_RE.search(cond):
        return {"point": None, "basis": "题干未提及过渡点", "source": "none",
                "candidates": []}
    selected = distinct[0] if distinct else 1
    basis = ("题干明确将图形%d号点指定为过渡点" % selected if distinct else
             "题干提到过渡点但未标编号，按默认图形1号点处理")
    graphic_name = "图形%d号点" % selected
    if graphic_name not in pts:
        return {"point": None,
                "basis": basis + "；该图形点坐标尚未推导出来",
                "source": "unresolved", "candidates": distinct or [selected]}
    pts[TRANSIT] = pts[graphic_name]
    transit_meta = dict(meta.get(graphic_name, {}))
    transit_meta.update({"why": basis,
                         "ref": graphic_name})
    meta[TRANSIT] = transit_meta
    return {"point": TRANSIT, "basis": transit_meta["why"],
            "source": "explicit" if distinct else "default", "candidates": distinct or [selected],
            "graphic_point": graphic_name}


# ------------------------------------------------------- 距离 / 方向

# "265米" / "265 m" / "1.5公里"
_DIST_RE = re.compile(r"([\d.]+)\s*(公里|千米|km|米|m)")
# "飞行/平飞" 这类动词暗示是一段位移
_MOVE_RE = re.compile(r"飞行|平飞|到达|直线|之间")


def _find_dist(text):
    """找句子里的距离（米）。找不到返回 None。"""
    best = None
    for m in _DIST_RE.finditer(text):
        v = float(m.group(1))
        if m.group(2) in ("公里", "千米", "km"):
            v *= 1000.0
        # 高度也写成"XX米"，优先取带"距离/飞行/到达"语境的
        ctx = text[max(0, m.start() - 12):m.start()]
        score = 2 if re.search(r"距离|飞行|平飞|到达|为|是|=", ctx) else 1
        if best is None or score > best[0]:
            best = (score, v)
    return best[1] if best else None


# ------------------------------------------------------- 约束提取

class Seg:
    """一段「A = dest(B, 方位, 距离)」的约束。"""

    def __init__(self, a, b, bearing, dist, raw):
        self.a = a          # 目标点（未知）
        self.b = b          # 参照点（已知或可推出）
        self.bearing = bearing
        self.dist = dist
        self.raw = raw

    def __repr__(self):
        return "<%s = %s @%s %sm>" % (self.a, self.b, self.bearing, self.dist)


def extract_segs(cond_text):
    """从条件文本里抽出所有方位约束。

    先按大句切分（。；换行），再把「方位词 + 距离 + 角色」配对。
    """
    cond_text = geo.norm(cond_text or "")
    big = re.split(r"[。;\n]", cond_text)
    segs = []
    for part in big:
        if not part.strip():
            continue
        roles = _find_roles(part)
        if len(roles) < 2:
            continue                      # 至少要有起点和终点
        bearing = geo.parse_bearing(part)
        dist = _find_dist(part)
        # 三点夹角不是地理方位，不能把 ∠H12=90° 当成向东飞行。
        if re.search(r"∠|夹角", part) and not re.search(
                r"方位角|方向角|[北南东西]|\b(?:NE|NW|SE|SW|N|E|S|W)\b", part):
            continue
        if bearing is None or dist is None:
            continue

        pos = [p for p, _ in roles]
        names = [n for _, n in roles]

        if re.search(r"位于|在.{0,6}(方向|方位)", part):
            # "A 位于 B 的 X 方向 D 米" → 取"位于"前最近的角色为 A，后最近的角色为 B
            m = re.search(r"位于|在", part)
            anchor = m.start() if m else len(part) // 2
            before = [i for i, p in enumerate(pos) if p < anchor]
            after = [i for i, p in enumerate(pos) if p >= anchor]
            if before and after:
                a = names[before[-1]]
                b = names[after[0]]
                if a != b:
                    segs.append(Seg(a, b, bearing, dist, part.strip()))
                    continue
            if after:
                # 只有后半段有角色：无从判断主宾，跳过（宁缺勿错）
                continue

        # 没有"位于"：当成位移句 —— "起飞后…飞行…到达 X"
        if any(n == TAKEOFF for n in names) or re.search(r"起飞", part):
            tgt = next((n for n in names if n != TAKEOFF), None)
            if tgt:
                segs.append(Seg(tgt, TAKEOFF, bearing, dist, part.strip()))
                continue
        # "从 A 到 B" 句式
        m = re.search(r"从\s*(.{0,8}?)\s*(?:到|至)\s*(.{0,8})", part)
        if m:
            a, b = _role_at(m.group(1)), _role_at(m.group(2))
            if a and b and a != b:
                segs.append(Seg(a, b, bearing, dist, part.strip()))
                continue
        # 兜底：把第一个角色当参照、第二个当目标（"图形1号点到图形3号点…"）
        if len(names) >= 2 and _MOVE_RE.search(part):
            segs.append(Seg(names[1], names[0], bearing, dist, part.strip()))

    return segs


def extract_base(cond_text):
    """找题面直接给出的经纬度基准点，返回 (角色名, lat, lon)。"""
    cond_text = geo.norm(cond_text or "")
    for part in re.split(r"[。;\n]", cond_text):
        if not part.strip():
            continue
        lat, lon = geo.parse_latlon(part)
        if lat is None or lon is None:
            continue
        role = _role_at(part)
        if role is None:
            role = TAKEOFF                  # "起飞点经纬度…" 之外默认按起飞点算
        return role, lat, lon
    return None, None, None


def solve_points(cond_text):
    """解出所有能定的点。

    返回 ``(pts, meta, segs)``：

    - ``pts``  ``{角色: (lat, lon)}``
    - ``meta`` ``{角色: {"ref","bearing","dist","why"}}`` —— 推导依据，
      判错讲解要用："你这个点相对起飞点应该是南偏西 30° 走 265 米"
    - ``segs`` 全部抽到的约束（含没解出来的），用于诊断
    """
    base_role, blat, blon = extract_base(cond_text)
    pts = {}
    meta = {}
    if base_role and blat is not None:
        pts[base_role] = (blat, blon)
        meta[base_role] = {"ref": None, "bearing": None, "dist": None,
                           "why": "题面直接给出"}

    segs = extract_segs(cond_text)
    # 迭代到不动点。两个方向都要推：
    #   正向 A = dest(B, 方位, 距离)          —— B 已知时算 A
    #   反向 B = dest(A, 方位+180, 距离)      —— A 已知时反推 B
    # 题干里两种写法都常见（"1号点位于避让点NW方向" vs "避让点位于1号点SW方向"），
    # 只会正向推的话，第二类会整条断链。
    for _ in range(len(segs) + 2):
        grew = False
        for s in segs:
            if s.a not in pts and s.b in pts:
                b_lat, b_lon = pts[s.b]
                la, lo = geo.dest_point(b_lat, b_lon, s.bearing, s.dist)
                if la is None:
                    continue
                pts[s.a] = (la, lo)
                meta[s.a] = {
                    "ref": s.b, "bearing": s.bearing, "dist": s.dist,
                    "why": "%s 沿 %s 走 %s 米" % (
                        s.b, geo.fmt_bearing(s.bearing),
                        ("%g" % s.dist) if s.dist else "?"),
                }
                grew = True
            elif s.b not in pts and s.a in pts:
                a_lat, a_lon = pts[s.a]
                back = (s.bearing + 180.0) % 360.0
                la, lo = geo.dest_point(a_lat, a_lon, back, s.dist)
                if la is None:
                    continue
                pts[s.b] = (la, lo)
                meta[s.b] = {
                    "ref": s.a, "bearing": back, "dist": s.dist,
                    "why": "%s 的 %s 方向 %s 米处（题面写的是反向）" % (
                        s.a, geo.fmt_bearing(back),
                        ("%g" % s.dist) if s.dist else "?"),
                }
                grew = True
        if not grew:
            break

    return pts, meta, segs


# ------------------------------------------------------- 飞行参数

# "a 值建议为 300 米" / "f值为35s" / "b 为100米" / "c值为18km/h"
#   ★ 单位必须**按长度从长到短**排，且 km/h 要排在 m/s 前面 ——
#     正则 alternation 是从左往右取第一个能匹配的，把 `m/s` 写在 `km/h` 前面时，
#     "18km/h" 会先被 `...18` + 空单位命中，剩下的 "km/h" 留在串里
#     继续被后面的规则误吃成变量名（实测 #13/#17 显示成 "c18"）。
_VAL_RE = re.compile(
    r"([A-Za-z])\s*(?:的)?\s*值?\s*(?:建议|设定)?\s*(?:为|是|=)\s*"
    r"([\d.]+)\s*"
    r"(km/h|km/H|KM/H|千米/小时|公里/小时|千米每小时|公里每小时|"
    r"m/s|米/秒|米每秒|"
    r"km|千米|公里|"
    r"米|m|s|秒)?")

# 速度单位换算到 m/s（题面会用 km/h 写水平速度，而地面站存的是 m/s）
_SPEED_FACTORS = {
    "km/h": 1 / 3.6, "km/H": 1 / 3.6, "KM/H": 1 / 3.6,
    "千米/小时": 1 / 3.6, "公里/小时": 1 / 3.6,
    "千米每小时": 1 / 3.6, "公里每小时": 1 / 3.6,
    "m/s": 1.0, "米/秒": 1.0, "米每秒": 1.0,
}


def norm_unit(value, unit):
    """把 (值, 单位) 归一到项目内部统一的三个单位。

    统一后：速度一律 ``m/s``、时间一律 ``秒``、长度一律 ``米``。
    ★ 不换算的话，"18km/h" 会和地面站里的 "5 m/s" 直接比 —— 一倍的量级差，
      用户明明画对了却判错。
    """
    u = (unit or "").strip()
    if u in _SPEED_FACTORS:
        v = float(value) * _SPEED_FACTORS[u]
        # 保留 2 位小数就够（5.0 / 2.78），别把 18/3.6 印成 5.000000000001
        return round(v, 2), "m/s"
    if u in ("km", "千米", "公里"):
        return float(value) * 1000.0, "米"
    if u in ("s", "秒", "分钟", "min"):
        if u in ("分钟", "min"):
            return float(value) * 60.0, "秒"
        return float(value), "秒"
    if u in ("米", "m"):
        return float(value), "米"
    return float(value), ""          # 没有单位就不猜


def extract_params(req_text, sug_text):
    """抽出飞行参数列表：``[{key, meaning, value, unit, raw}]``。

    ``meaning`` 来自要求段（"边长为a" → 边长），值来自建议段。
    ``value`` / ``unit`` 已**归一**（速度 m/s、时间 秒、长度 米）；
    原始写法留在 ``raw`` 里（界面要照原样显示）。
    """
    req = geo.norm(req_text or "")
    sug = geo.norm(sug_text or "")
    if not sug:
        return []

    # 1) 先建"变量含义表"：要求段里 "…为 a" 的描述。
    #    排除集里必须含 "." —— 有些题用半角句点断句，不排掉会把
    #    "456航点…为c.水平速度为d" 一整段吃成 d 的含义，把 c 挤没。
    #    "为|是|等于" 必须**可选**：题面常直接写 "停留时间f。"，
    #    只认带"为"的写法会让 f 拿不到含义，界面显示成"变量 F"。
    meaning = {}
    for m in re.finditer(
            r"([^，,。；;：:（）().]{2,22}?)\s*(?:为|是|等于)?\s*"
            r"([a-z])(?:\s*[，,。；;、.]|$)", req):
        desc, var = m.group(1).strip(), m.group(2).lower()
        # 只去掉"（一）""1、"这种题号前缀；不能连裸数字一起删 ——
        # "123航点相对地面高度" 的 123 是有意义的信息。
        desc = re.sub(r"^[（(][一二三四五六七八九十\d]+[）)]\s*|^\d+\s*[、．.，,]\s*",
                      "", desc)
        if desc and var not in meaning:
            meaning[var] = desc

    # 2) 值表
    out = []
    seen = set()
    for m in _VAL_RE.finditer(sug):
        var = m.group(1).lower()
        if var in seen:
            continue
        seen.add(var)
        raw_val = float(m.group(2))
        val, unit = norm_unit(raw_val, m.group(3))
        if val == int(val):
            val = int(val)
        out.append({
            "key": var,
            "meaning": meaning.get(var, ""),
            "value": val,
            "unit": unit,
            "raw": m.group(0).strip(),
        })
    return out


def extract_extras(req_text):
    """抽非数值要求：转弯方式、循环次数等。"""
    req = geo.norm(req_text or "")
    out = {}
    m = re.search(r"(停止转弯|协调转弯|转弯方式为\s*[^，,。；;]{1,6})", req)
    if m:
        out["转弯方式"] = "协调转弯" if "协调" in m.group(1) else "停止转弯"
    m = re.search(r"(?:并)?循环\s*([\d一二三四五六七八九十]+)\s*次", req)
    if m:
        out["循环次数"] = m.group(1)
    elif "循环执行" in req or "循环" in req:
        out["循环次数"] = "循环执行"
    if "不循环" in req:
        out["循环次数"] = "不循环"
    m = re.search(r"停留时间\s*(?:为|是)?\s*([^，,。；;]{1,8})", req)
    if m:
        out["停留时间原文"] = m.group(1).strip()
    return out


# ------------------------------------------------------- 主入口

def derive(q):
    """推导一道题的标准答案。"""
    # 新导入题的作答只能来自用户选定的视觉 API；本函数仅读取已保存的
    # API 坐标/参数供旧页面兼容，绝不再调用本地强引擎或弱解析生成答案。
    if isinstance(q, dict) and q.get("imported"):
        return _derive_imported_api_answer(q)
    cond = q.get("conditions_text") or ""
    req = q.get("requirements_text") or ""
    sug = q.get("suggested_text") or ""

    pts, meta, segs = solve_points(cond)
    avoid_info = _resolve_avoid_point(q, pts, meta)
    transit_info = _resolve_transit_point(q, pts, meta)
    params = extract_params(req, sug)
    extras = extract_extras(req)

    base_role, blat, blon = extract_base(cond)

    # 可信度 = 「能判多少」，不是「答案完不完整」：
    #   none     一个点都推不出来 → 只能做参数对照，不判坐标
    #   full     起飞点 + (避让点 或 图形1号点) → 考试真正要算的那几步齐了
    #   partial  只要推得出点就至少能判这些点（哪怕基准是图形某点）
    # 早期版本把"没有两个关键点"一律打成 none，导致像「题面给图形3点坐标、
    # 只推出起飞点」这种明明能判的题被判成不可判 —— 这里的口径要按"可判性"来。
    if not pts:
        conf = "none"
    elif TAKEOFF in pts and (AVOID in pts or TRANSIT in pts or "图形1号点" in pts):
        conf = "full"
    else:
        conf = "partial"

    points = {}
    for name, (la, lo) in pts.items():
        m = meta.get(name, {})
        points[name] = {
            "lat": round(la, 6),
            "lon": round(lo, 6),
            "why": m.get("why", ""),
            "ref": m.get("ref"),
            "bearing": m.get("bearing"),
            "dist": m.get("dist"),
        }

    # 判分顺序：起飞点 → 避让点/过渡点 → 图形 1 号点 → 其余图形点
    order = {TAKEOFF: 0, AVOID: 1, TRANSIT: 2, "图形1号点": 3}
    ordered = dict(sorted(points.items(),
                          key=lambda kv: (order.get(kv[0], 9), kv[0])))

    return {
        "id": q.get("id"),
        "conf": conf,
        "shape": geometry.recognize(q),
        "shape_source": req,
        "base": base_role,
        "points": ordered,
        "params": params,
        "extras": extras,
        "unresolved": [s.__repr__() for s in segs
                       if s.a not in pts and s.b not in pts],
        "avoid_point": avoid_info,
        "transit_point": transit_info,
    }


def derive_for_grading(q):
    """提取本地判题依据；参考答案展示仍保持原有 API 流程。"""
    local = dict(q)
    local["imported"] = False
    ans = derive(local)
    text = "\n".join(str(q.get(key) or "") for key in (
        "conditions_text", "requirements_text", "suggested_text"))
    existing = {p["meaning"] for p in ans["params"]}
    patterns = (
        ("水平速度", r"水平速度\s*(?:为|是|[:：=])?\s*([\d.]+)\s*(km/h|公里/小时|m/s|米/秒|米每秒)"),
        ("垂直速度", r"垂直速度\s*(?:为|是|[:：=])?\s*([\d.]+)\s*(km/h|公里/小时|m/s|米/秒|米每秒)"),
        ("航线高度", r"(?:航线高度|整体高度)\s*(?:为|是|[:：=])?\s*([\d.]+)\s*(米|m)"),
        ("停留时间", r"(?:停留|悬停)时间\s*(?:为|是|[:：=])?\s*([\d.]+)\s*(秒|s)"),
    )
    for meaning, pattern in patterns:
        values = {(float(m.group(1)), m.group(2).lower()) for m in
                  re.finditer(pattern, text, re.IGNORECASE)}
        if len(values) != 1 or any(meaning in item for item in existing):
            continue
        value, unit = norm_unit(*next(iter(values)))
        ans["params"].append({"key": meaning, "meaning": meaning,
                              "value": value, "unit": unit, "raw": meaning})
    ans["extras"] = extract_extras(text)
    saved = _derive_imported_api_answer(q) if q.get("imported") else None
    if saved and not saved.get("api_answer_pending"):
        ans["points"].update(saved["points"])
        # API 参数缺少单位或语义时不能覆盖可确定的本地参数。
        for item in saved["params"]:
            if item.get("unit") and not any(p["key"] == item["key"] for p in ans["params"]):
                ans["params"].append(item)
    pts = {name: (p["lat"], p["lon"]) for name, p in ans["points"].items()}
    meta = {name: dict(p) for name, p in ans["points"].items()}
    ans["avoid_point"] = _resolve_avoid_point(q, pts, meta)
    ans["transit_point"] = _resolve_transit_point(q, pts, meta)
    for name, (lat, lon) in pts.items():
        ans["points"][name] = {**meta.get(name, {}), "lat": lat, "lon": lon}
    hint = str(q.get("graph_hint") or "")
    for index, symbol in enumerate("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳", 1):
        hint = hint.replace(symbol, " %d " % index)
    labels = {"图形%s号点" % n for n in re.findall(r"\d+", hint)}
    # 参数或基准点核对不能冒充完整航线通过。
    ans["partial_grading"] = not (labels and labels <= set(ans["points"]))
    ans["conf"] = "partial" if (ans["points"] or ans["params"] or
        (ans["extras"].keys() - {"停留时间原文"})) else "none"
    if not ans["partial_grading"]:
        ans["conf"] = "full"
    return ans


def _derive_imported_api_answer(q):
    source = q.get("reference_source") or q.get("_reference_source") or {}
    answer = source.get("direct_answer") if isinstance(source, dict) else None
    answer = answer if isinstance(answer, dict) else {}
    saved_fields = answer.get("source_fields")
    if isinstance(saved_fields, dict):
        current_fields = {key: str(q.get(key) or "") for key in (
            "graph_hint", "conditions_text", "requirements_text", "suggested_text")}
        if saved_fields != current_fields:
            answer = {}
    role = {"H": TAKEOFF, "A": AVOID, "T": TRANSIT}
    points = {}
    for item in answer.get("coordinates", [])[:32]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("point") or "").strip()
        label = role.get(name.upper()) or (
            "图形%s号点" % name if name.isdigit() else name)
        try:
            latitude = float(item.get("latitude"))
            longitude = float(item.get("longitude"))
        except (TypeError, ValueError, OverflowError):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        point = {"lat": latitude, "lon": longitude,
                 "why": "视觉 API 直接作答；未作本地重算"}
        if item.get("altitude_m") is not None:
            try:
                point["alt"] = float(item["altitude_m"])
            except (TypeError, ValueError, OverflowError):
                pass
        points[label] = point
    raw_parameters = answer.get("parameters", [])
    if not isinstance(raw_parameters, list):
        raw_parameters = []
    params = [{"key": str(item.get("name") or "模型参数"),
               "value": item.get("value"), "unit": str(item.get("unit") or ""),
               "meaning": str(item.get("name") or "视觉 API 参考值")}
              for item in raw_parameters[:40] if isinstance(item, dict)]
    conf = ("full" if TAKEOFF in points and
            (AVOID in points or TRANSIT in points or "图形1号点" in points)
            else "partial" if points else "none")
    return {
        "id": q.get("id"), "conf": conf,
        "shape": geometry.recognize(q), "shape_source": q.get("requirements_text", ""),
        "base": TAKEOFF if TAKEOFF in points else "",
        "points": points, "params": params, "extras": {}, "unresolved": [],
        "avoid_point": None, "transit_point": None,
        "api_answer_pending": not bool(answer),
    }


def quality_report(items):
    """批量统计推导覆盖率（跑题做体检用）。"""
    from collections import Counter
    c = Counter()
    detail = []
    for q in items:
        a = derive(q)
        c[a["conf"]] += 1
        detail.append(a)
    return c, detail


# ------------------------------------------------------- 自检

if __name__ == "__main__":
    import json
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    import bankdata as BD
    items = BD.load()
    if not items:
        print("当前没有用户导入的题卡。")
        raise SystemExit(0)

    c, detail = quality_report(items)
    n = len(items)
    print("推导覆盖率（共 %d 题）" % n)
    for k in ("full", "partial", "none"):
        print("  %-8s %4d  (%.1f%%)" % (k, c[k], 100.0 * c[k] / n))

    print("\n--- 抽 3 题看推导结果 ---")
    for a in detail[:3]:
        q = next(x for x in items if x["id"] == a["id"])
        print("=" * 56)
        print("id %s  %s  conf=%s" % (a["id"], q["source"], a["conf"]))
        for name, p in a["points"].items():
            print("   %-10s %s   (%s)" % (name, geo.fmt_latlon(p["lat"], p["lon"]),
                                          p["why"]))
        print("   参数:", ", ".join("%s=%s%s" % (p["meaning"] or p["key"],
                                               p["value"], p["unit"])
                                   for p in a["params"]))
        print("   其他:", a["extras"])


