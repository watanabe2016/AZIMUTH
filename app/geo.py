"""地理计算引擎 —— AI 判题的地基。

纯 Python 实现，不依赖 Qt，可单独在命令行里跑。四个能力：

1. 方向语义解析：``南偏西30°`` → 210.0 度；``NE`` → 45；``西北风`` → 315（风向=来向）
2. 经纬度解析：``N：64°36′12″`` / ``E: 114.4306°`` → 十进制度
3. 球面正算：给定基准点 + 方位角 + 距离 → 目标点经纬度
4. 球面反算：两点 → 方位角 / 距离

约定：方位角以正北为 0°，顺时针递增（正东 90°、正南 180°、正西 270°）。
"""

from __future__ import annotations

import math
import re

# 地球平均半径（米）。考题都在几十公里量级，球面模型误差远小于判分阈值。
EARTH_R = 6371000.0


# ==================================================== 方向语义

# 中文方位 → 基准角度
_BASE = {
    "北": 0.0, "东": 90.0, "南": 180.0, "西": 270.0,
    "N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0,
    "正北": 0.0, "正东": 90.0, "正南": 180.0, "正西": 270.0,
}

# 八方位英文缩写 → 角度
_OCTANT = {
    "N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0,
    "S": 180.0, "SW": 225.0, "W": 270.0, "NW": 315.0,
    "NNE": 22.5, "ENE": 67.5, "ESE": 112.5, "SSE": 157.5,
    "SSW": 202.5, "WSW": 247.5, "WNW": 292.5, "NNW": 337.5,
}

# 中文八方位（"东北""西南"这类）
_CN_OCTANT = {
    "东北": 45.0, "东南": 135.0, "西南": 225.0, "西北": 315.0,
}

# 归一化：把各种"斜杠引号空格全角"统一掉。
# 注意：**不要**把中文句号 "。" 归一化成 "." —— 下游按句号切分句子，
# 转成半角点后既切不开句，又让 "." 混进小数点匹配里，会串句吃参数。
_NORM = str.maketrans({
    "：": ":", "，": ",", "、": ",",
    "（": "(", "）": ")", "－": "-", "—": "-", "～": "~",
    "°": "°", "º": "°", "˚": "°",
    "′": "'", "＇": "'", "’": "'", "‘": "'",
    "″": '"', "＂": '"', "”": '"', "“": '"',
})


def norm(s):
    """归一化全角标点与引号，便于正则匹配。"""
    return (s or "").translate(_NORM)


def parse_bearing(text):
    """把方位描述解析成 0~360 的方位角。

    支持：
      - ``南偏西30°`` / ``南偏西 30 度`` → 210
      - ``西偏南30°`` → 240（从西向南偏）
      - ``NE`` / ``东北`` / ``东北方向`` → 45
      - ``290°方向`` / ``方位角 290`` → 290
      - ``正南`` → 180

    解析不出返回 None。
    """
    if not text:
        return None
    s = norm(text).upper().strip()

    # 八方位也可作为主方位，例如“东南偏东10°”是125°。
    m = re.search(r"(东北|东南|西南|西北)\s*偏\s*([北南东西])\s*([\d.]+)", s)
    if m:
        base = _CN_OCTANT[m.group(1)]
        target = _BASE[m.group(2)]
        delta = (target - base + 180.0) % 360.0 - 180.0
        if abs(delta) == 45.0:
            return (base + math.copysign(float(m.group(3)), delta)) % 360.0

    m = re.search(r"(NE|NW|SE|SW)\s*偏\s*([北南东西NSEW])\s*([\d.]+)", s)
    if m:
        base = _OCTANT[m.group(1)]
        target_name = {"N": "北", "S": "南", "E": "东", "W": "西"}.get(
            m.group(2), m.group(2))
        target = _BASE[target_name]
        delta = (target - base + 180.0) % 360.0 - 180.0
        if abs(delta) == 45.0:
            return (base + math.copysign(float(m.group(3)), delta)) % 360.0

    # 1) "X偏Y<数字>°" —— 从主方位 X 往 Y 偏
    m = re.search(r"([北南东西NSEW])\s*偏\s*([北南东西NSEW])\s*([\d.]+)", s)
    if m:
        letters = {"N": "北", "S": "南", "E": "东", "W": "西"}
        base = letters.get(m.group(1), m.group(1))
        side = letters.get(m.group(2), m.group(2))
        deg = float(m.group(3))
        return _offset(_BASE.get(base), base, side, deg)

    # 2) 英文八方位缩写（要放在纯数字之前，避免 "N" 被当成别的）。
    #    不能用 \b —— 中文也算 \w，"为NE方向" 里 NE 两边没有词边界，整条会失配。
    m = re.search(r"(NNE|ENE|ESE|SSE|SSW|WSW|WNW|NNW|NE|NW|SE|SW|N|E|S|W)", s)
    if m:
        return _OCTANT[m.group(1)]

    # 3) 中文八方位
    for k, v in _CN_OCTANT.items():
        if k in s:
            return v

    # 4) 纯角度："290°方向" / "方位角290" / "角度为 290"
    m = re.search(r"([\d.]+)\s*°", s)
    if m:
        return float(m.group(1)) % 360.0
    m = re.search(r"(?:方位角|方向角|角度)\s*(?:为|是|:)?\s*([\d.]+)", s)
    if m:
        return float(m.group(1)) % 360.0

    # 5) 单个正方位字（放最后，避免 "南偏西" 被 "南" 抢先命中）
    for k in ("正北", "正东", "正南", "正西"):
        if k in s:
            return _BASE[k]
    m = re.search(r"[北南东西]", s)
    if m:
        return _BASE[m.group(0)]

    return None


def _offset(base, base_name, side, deg):
    """主方位 base 往 side 侧偏 deg 度。

    北偏东 30 → 30       北偏西 30 → 330
    南偏东 30 → 150      南偏西 30 → 210
    东偏北 30 → 60       东偏南 30 → 120
    西偏北 30 → 300      西偏南 30 → 240

    判正负的规则：方位角顺时针递增。若从 base 顺时针转 90° 正好是 side，
    说明是"顺时针偏"，取 +；否则取 −。不能简单按"东=+"来定 —— 从南往西偏
    明明是 +（180→270），从南往东偏才是 −。
    """
    if base is None:
        return None
    side_v = _BASE.get(side)
    if side_v is None:
        return None
    sign = 1.0 if abs((side_v - base) % 360.0 - 90.0) < 1e-6 else -1.0
    return (base + sign * deg) % 360.0


def wind_from(text):
    """风向 → 风从哪个方位吹来（= 来向方位角）。

    ``东南风`` = 风自东南来 = 135°。
    注意：这与"飞机逆风飞行时的航向"相同（机头迎风）。
    """
    if not text:
        return None
    s = norm(text).upper()
    # “西偏北20°”等复合风向要先于单个方位词解析，否则会被截成“西”。
    m = re.search(
        r"风向\s*(?:为|是|[:：])?\s*([北南东西]\s*偏\s*[北南东西]\s*[\d.]+\s*(?:°|度)?)",
        norm(text))
    if m:
        return parse_bearing(m.group(1))
    m = re.search(r"风向\s*(?:为|是|[:：])?\s*(NNE|ENE|ESE|SSE|SSW|WSW|WNW|NNW|NE|NW|SE|SW|N|E|S|W)", s)
    if m:
        return _OCTANT[m.group(1)]
    m = re.search(r"风向\s*(?:为|是|[:：])?\s*(东北|东南|西南|西北|正北|正东|正南|正西|北|南|东|西)", norm(text))
    if m:
        direction = m.group(1)
        base = direction[-1] if direction.startswith("正") else direction[0]
        return _CN_OCTANT.get(direction, _BASE.get(base))
    m = re.search(r"(NNE|ENE|ESE|SSE|SSW|WSW|WNW|NNW|NE|NW|SE|SW|N|E|S|W)\s*风", s)
    if m:
        return _OCTANT[m.group(1)]
    m = re.search(r"(东北|东南|西南|西北|正北|正东|正南|正西|[北南东西])风", norm(text))
    if m:
        w = m.group(1)
        base = w[-1] if w.startswith("正") else w[0]
        return _CN_OCTANT.get(w, _BASE.get(base))
    return None


# ==================================================== 经纬度

def parse_angle(text, kind="lat"):
    """解析**单个**角度分量（纬度或经度），返回十进制度（带符号）。

    界面把纬度和经度分成两个输入框，用户可能怎么写都有：
    ``36°15′16.9″`` / ``36.254694`` / ``N36.2547`` / ``-36.25`` / ``361516.9``
    解析不出返回 None。

    ``kind`` 只用于判断半球字母的方向（N/E 为正，S/W 为负）。
    """
    if text is None:
        return None
    s = norm(str(text)).strip().upper()
    if not s:
        return None

    neg_sign = "-" in s or "S" in s or "W" in s
    s = s.replace("-", "")
    num = r"(\d+(?:\.\d+)?)"

    # 度分秒：36°15'16.9"  （分秒的符号可有可无，天符号跳过分号/秒号）
    m = re.search(num + r"\s*(?:°|度)\s*" + num + r"\s*(?:'|′|分)\s*" +
                  num + r"\s*(?:\"|″|秒)?", s)
    if m:
        v = _dms(m.group(1), m.group(2), m.group(3))
        return -v if neg_sign else v

    # 度分：36°15.5'  /  36度15分
    m = re.search(num + r"\s*(?:°|度)\s*" + num + r"\s*(?:'|′|分)?", s)
    if m:
        v = _dms(m.group(1), m.group(2), None)
        return -v if neg_sign else v

    # 光秃秃一个数：36.254694 / 36
    m = re.search(num, s)
    if m:
        v = float(m.group(1))
        return -v if neg_sign else v
    return None


def parse_latlon(text):
    """从一段文本里抠出 (纬度, 经度) 十进制度。解析不出返回 (None, None)。

    支持：
      - ``N：64°36′12″  E：20°42′12″``
      - ``E: 114.4306°，N: 36.2547°``（题面里经纬度顺序会反着写）
      - ``N 51.1277 E 39.2866``
    """
    if not text:
        return None, None
    s = norm(text)
    lat = lon = None

    # 带半球字母的分/秒/十进制形式。
    # 度数那里必须写成 \d+(?:\.\d+)? —— 只用 [\d.]+ 会匹配到孤立的 "."（题面里
    # "N：36.2547。" 这种句号紧跟的情况会炸出 float('.') 异常）。
    num = r"(\d+(?:\.\d+)?)"
    for m in re.finditer(
            r"([NSEW])\s*[:：]?\s*" + num + r"\s*(?:°|度)?\s*"
            r"(?:" + num + r"\s*(?:'|′|分)\s*)?"
            r"(?:" + num + r"\s*(?:\"|″|秒)\s*)?", s):
        hemi, d, mi, sec = m.group(1), m.group(2), m.group(3), m.group(4)
        val = _dms(d, mi, sec)
        if hemi in ("N", "S"):
            lat = val if hemi == "N" else -val
        else:
            lon = val if hemi == "E" else -val

    if lat is None and lon is None and re.search(r"经纬度|坐标", s):
        # 只在明确坐标语境中识别无半球字母的十进制度，避免把边长或夹角当坐标。
        tail = re.split(r"经纬度|坐标", s)[-1]
        pair = re.search(r"([-+]?\d{1,3}(?:\.\d+)?)\s*°?\s*[,，、]\s*([-+]?\d{1,3}(?:\.\d+)?)\s*°?", tail)
        if pair:
            first, second = map(float, pair.groups())
            if 90 < abs(first) <= 180 and abs(second) <= 90:
                lon, lat = first, second
            elif 90 < abs(second) <= 180 and abs(first) <= 90:
                lat, lon = first, second
            # 两项都小于90度时无法仅凭数值判断顺序，保留为未解析。
    return lat, lon


def _dms(d, mi, sec):
    """度分秒 → 十进制度。"""
    v = float(d)
    if mi:
        v += float(mi) / 60.0
    if sec:
        v += float(sec) / 3600.0
    return v


def fmt_latlon(lat, lon, style="dms"):
    """格式化经纬度。

    style="dms" → ``N64°36′12″ E20°42′12″``
    style="dec" → ``N64.603333 E20.703333``
    """
    if lat is None or lon is None:
        return "—"
    if style == "dec":
        return "N%.6f E%.6f" % (lat, lon)
    return "%s %s" % (_dms_str(lat, "N", "S"), _dms_str(lon, "E", "W"))


def _dms_str(v, pos, neg):
    hemi = pos if v >= 0 else neg
    v = abs(v)
    d = int(v)
    mi = int((v - d) * 60)
    sec = (v - d - mi / 60.0) * 3600.0
    # 秒四舍五入到 0.1 秒；进位别越界
    if round(sec, 1) >= 60:
        sec = 0.0
        mi += 1
    if mi >= 60:
        mi = 0
        d += 1
    return "%s%d°%02d′%04.1f″" % (hemi, d, mi, sec)


def fmt_dms_short(v, pos, neg):
    """只格式化一个分量，给界面用。"""
    return _dms_str(v, pos, neg)


# ==================================================== 球面计算

def dest_point(lat, lon, bearing, dist_m):
    """正算：从 (lat, lon) 沿 bearing 方位角走 dist_m 米，返回新的 (lat, lon)。

    标准球面公式（大圆航线）。方位角单位度，距离单位米。
    """
    if None in (lat, lon, bearing, dist_m):
        return None, None
    br = math.radians(bearing % 360.0)
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    dr = dist_m / EARTH_R

    sp2 = (math.sin(p1) * math.cos(dr)
           + math.cos(p1) * math.sin(dr) * math.cos(br))
    p2 = math.asin(max(-1.0, min(1.0, sp2)))
    l2 = l1 + math.atan2(math.sin(br) * math.sin(dr) * math.cos(p1),
                         math.cos(dr) - math.sin(p1) * sp2)

    return math.degrees(p2), _wrap_lon(math.degrees(l2))


def bearing_of(lat1, lon1, lat2, lon2):
    """反算：从点 1 看点 2 的方位角（0~360）。"""
    if None in (lat1, lon1, lat2, lon2):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = (math.cos(p1) * math.sin(p2)
         - math.sin(p1) * math.cos(p2) * math.cos(dl))
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def distance_of(lat1, lon1, lat2, lon2):
    """反算：两点间大圆距离（米）。"""
    if None in (lat1, lon1, lat2, lon2):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(a)))


def _wrap_lon(v):
    """经度归一到 (-180, 180]。"""
    while v > 180.0:
        v -= 360.0
    while v <= -180.0:
        v += 360.0
    return v


# ==================================================== 角度工具

def ang_diff(a, b):
    """两个方位角的最小差（0~180），用于判分容差。"""
    if a is None or b is None:
        return None
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def fmt_bearing(v):
    """方位角格式化：210.3 → ``210.3°``。"""
    if v is None:
        return "—"
    return "%.1f°" % (v % 360.0)


# ==================================================== 自检

if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ok = 0
    bad = []

    def ck(name, got, want, tol=0.01):
        global ok
        good = (got is not None and abs(got - want) <= tol)
        if good:
            ok += 1
        else:
            bad.append("%s: got %r want %r" % (name, got, want))

    ck("南偏西30", parse_bearing("向南偏西30°方向飞行265米"), 210)
    ck("南偏东30", parse_bearing("起飞后需向南偏东30°方向飞行355米"), 150)
    ck("西偏北25", parse_bearing("向西偏北25°方向飞行300米"), 295)
    ck("北偏西25", parse_bearing("向北偏西25°方向飞行300米"), 335)
    ck("NE", parse_bearing('位于"避让点"为NE方向'), 45)
    ck("NW", parse_bearing('位于"避让点"为NW方向'), 315)
    ck("SW", parse_bearing("位于题卡图形1号点SW方向500米处"), 225)
    ck("290度", parse_bearing("题卡图形1号点的290°方向200米处"), 290)
    ck("正南", parse_bearing("位于起飞点南偏东32°"), 148)
    ck("东南风", wind_from("本场风向：东南风。"), 135)
    ck("西北风", wind_from("本场风向西北风"), 315)

    lat, lon = parse_latlon("起飞点经纬度：E：114.4306°，N：36.2547°")
    ck("lat", lat, 36.2547)
    ck("lon", lon, 114.4306)

    lat2, lon2 = parse_latlon("N：64°36′12″ E：20°42′12″")
    ck("lat2", lat2, 64 + 36 / 60 + 12 / 3600)
    ck("lon2", lon2, 20 + 42 / 60 + 12 / 3600)

    # 正算：起飞点往南偏西30°(210) 走 265 米 —— 应该往南略偏西
    la, lo = dest_point(36.2547, 114.4306, 210, 265)
    d = distance_of(36.2547, 114.4306, la, lo)
    ck("正算距离闭环", d, 265, 0.5)
    b = bearing_of(36.2547, 114.4306, la, lo)
    ck("正算方位闭环", ang_diff(b, 210), 0, 0.05)

    print("geo 自检：%d 通过 / %d 失败" % (ok, len(bad)))
    for x in bad:
        print("  FAIL", x)
