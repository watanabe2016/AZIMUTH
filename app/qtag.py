# -*- coding: utf-8 -*-
"""考点标签引擎 —— 从题干自动认出"这道题考什么"。

为什么要它：
  · 判题引擎（answers.py）的能力其实跟题目结构强相关 —— 给绝对坐标的题它
    算得最准，整题只有相对方位的题它没法起算（找不到基准）；
  · 标签也是给用户看的：做之前先知道这题吃哪一套，心里有底。

★ 两套标签，互不干扰（用户 2026-09-21 明确要求）：
  ── **考点**（EXAM）──────── 这题考什么知识：风向 / 夹角 / 航向相同相反 /
      空域限制 / 时钟 / 起飞点双条件 / 双经纬度 / 罗盘 / 三角函数 / 空域范围
  ── **形状**（SHAPE）─────── 题卡画的是什么图：六边形 / 五边形 / 三角形 / …
  两者独立。一道"六边形的风向题"应该同时挂 `wind` 和 `graph:六边形`。

设计原则：
  **零 Qt 依赖**（只 import re），能脱离界面直接单测 —— 跟 geo.py / answers.py 一致。
  输入只要一个题卡 dict，输出一个 code 列表。

★ 一题可能同时命中多个考点，这是正常的、也是必须的 ——
  用户原话："航向相同就可能会和空域限制出在一起"。不要硬塞进单一分类。

★ 认不出来就**空着**，不要瞎猜（用户 2026-09-21 明确："没有就空着，
  可能后面导入新的题目就有了"）。三类当前 0 命中的考点（时钟/罗盘/三角函数）
  保留成"预留位"：规则在、菜单在，将来导入的题命中了就自动挂上。

识别采用题面关键字与条件表达式，题卡文字校对后可重新分类。
"""
import re

# ==================================================== 规则表

# ---------- 坐标基准 ----------
# "XXX经纬度" 的主语是谁：吃得到 起飞点经纬度 / 题卡图形1号点经纬度 / 题卡 4 号点经纬度
RE_COORD_HEAD = re.compile(
    r"(起飞点|题卡图形\s*\d+\s*号?点|题卡\s*\d+\s*号点)\s*经纬度")

# "有坐标"的存在性判断。只数 `E:` / `N：` 开头的个数，不解析具体数值 ——
# 题面坐标写法五花八门（E：114.4306° / N: 51.1277 / N：28°30′32″ / E：115° 06' 32"），
# 想用一个正则吃全所有形态很容易漏；只判断"这里有没有一个坐标"反而稳。
RE_COORD_ANY = re.compile(r"[ENen]\s*[:：]\s*\d")

# ★ 「没有绝对坐标」不能用"没有 E:/N: 前缀"当替身 —— 实测有 5 道题
#   （319/328/331/413/423）坐标写成 `119.705833°32°09′14"`，前缀丢了，
#   于是被误判成"没坐标"。裸小数度也算有坐标。
RE_DEG_PAIR = re.compile(r"\d{1,3}\.\d{3,}\s*°")

# 「A点到B点」式相对方位关系 —— 双夹角的第二层夹角靠它认出来
RE_REL_BRANCH = re.compile(r"[^，。；]{0,8}点到[^，。；]{0,8}点为?[^，。；]{0,12}方向")

# 中转点（避让点/过渡点/会让点/过分点）
RE_AVOID = re.compile(r"避让点|过渡点|过度点|会让点|过分点")

# 空域类词。注意要搜**全文**四个字段，不能只搜 conditions ——
# 实测有题库把"因受空域限制"写在 requirements 里（如 #88）。
# 「场地限制」是同一考点的换词版（#190）。
RE_AIRSPACE = re.compile(
    r"空域限制|空域范围|禁飞|限制区|危险区|管制|净空|场地限制|场地原因|"
    r"由于空域|因受空域|空域")

# ★ 空域范围题（区别于空域限制题）：给的是**半径/范围**约束，不是"绕开某片区域"
#   实测只有 #244："航线空域以起飞点威半径300米，不得超出"
RE_AIRSPACE_RANGE = re.compile(
    r"空域.{0,10}半径|半径\s*\d+\s*米.{0,10}(不得|不能)?超出|"
    r"不得超出|不能超出|限制在.{0,12}范围内")

# 「航向角 N°」式相对约束（#302 "任务航点4到3航向角105°"）
RE_HEADING_ANGLE = re.compile(r"航向角\s*\d|航向\s*[为是]?\s*\d{1,3}\s*°")

# ★ 航向相同 / 相反题 —— 真存在的一类，112 题
#   形态极多："A点到B点的航向与C点到D点的航向相反" /
#            "起飞点到1点和4点到5点航向相同" / "2-3的航向和H-1的航向相反" /
#            "3-5与2-H航向相同" / "H-3顺时针旋转30°方向与2-5航向相反"
RE_SAME_OPP = re.compile(
    r"航向\s*(?:相同|相反|一致)"            # "航向相同" / "航向相反"
    r"|(?:相同|相反|一致)\s*的?航向"          # "相同的航向"
    r"|航向[^，。；\n]{0,6}(?:相同|相反|一致)"  # "航向…相同"
)

# ★ 夹角题 —— 给角度约束（用户 2026-09-21 定义："给角度约束（∠1=∠2=90°）"）
#   ∠ 符号直接给角度 / "∠2=∠1=90°" / "∠4=60°"
RE_ANGLE_CONSTRAINT = re.compile(
    r"∠"                                     # 角度符号，203 题
    r"|角\s*[为是]?\s*\d{1,3}\s*[°度]"          # "角为90°"
)

# ★ 风向题 —— 给风向（判题时风速/风向要参与）
#   实测 136 题：本场风向：东南风 / 当地风向为东北风 / 逆风飞行 / 顺风飞行
RE_WIND = re.compile(
    r"风向|风力|风速|"
    r"(?:东南|东北|西南|西北|偏北|偏南|偏东|偏西|正北|正南|正东|正西)风|"
    r"逆风|顺风|侧风|顺风飞行|逆风飞行")

# ★ 双经度/双纬度题 —— 给了绝对经纬度坐标
#   424 题命中（E:/N: 前缀）；加上裸小数度兜底
RE_LATLON_WORD = re.compile(r"经纬度|经纬是|经度|纬度")

# ---------- ★ 预留位：当前 0 命中的三类（规则先写好，等导入题命中） ----------
# 时钟题：用钟面表述方位（"3点钟方向" / "几点钟方向" / "钟面" / "表盘"）。
# ★ 必须带"钟"字 —— 真题里"A点方向"是"A号点方向"的简写（实测 32 题假命中），
#   写成 `\d点方向` 会把"1点方向"（1号点方向）全吃进来。
# ★ 也不能只写"时针"——"顺时针"/"逆时针"是**旋转方向**，跟钟面方位是两回事
#   （实测 5 题假命中：#57/#66/#68/#81/#174 全是顺时针/逆时针旋转）。
#   所以用 `点钟`（必须带钟）或 `钟面/表盘` 这种明确词。
RE_CLOCK = re.compile(r"\d{1,2}\s*点钟|时钟方向|钟面|表盘|点钟方向")
# 罗盘题：磁罗盘 / 磁航向 / 磁偏角 / 指南针
RE_COMPASS = re.compile(r"罗盘|磁航向|磁罗盘|磁偏角|磁北|指南针|磁方位")
# 三角函数题：正弦/余弦/正切/勾股/斜边对边邻边
RE_TRIG = re.compile(
    r"三角函数|正弦|余弦|正切|[Ss]in\s*[（(]|[Cc]os\s*[（(]|[Tt]an\s*[（(]|"
    r"勾股|斜边|直角边|对边|邻边")

# ---------- 形状（独立一套，与考点互不干扰） ----------
# 从 requirements 提，顺序有意义（细分类在前，大类在后）
GRAPH_RULES = (
    ("等边三角形", r"等边三角形|等边三角"),
    ("直角三角形", r"直角三角形|直角三角"),
    ("梯形", r"梯形"),
    ("五边形", r"五边形"),
    ("蝴蝶形", r"蝴蝶形|蝴蝶航线"),
    ("六边形", r"六边形"),
    ("扫描航线", r"扫描航线|扫描线|航线扫描|网格航线"),
    ("扫描航线", r"扫描航线|扫描路线|扫掠航线|扫描式航线|蛇形航线|蛇形|弓字形|往返航线|[Ss]形航线|[Ss]形"),
    ("圆形", r"圆形|圆周航线|圆航线"),
    # 老数据兼容分类：保留自动识别，但不作为教学页八类的替代项。
	("等腰三角形", r"等腰三角形|等腰三角"),
	("三角形", r"三角形"),
	("正方形", r"正方形"),
)

# 教员画图重规划的**本质特征**：图形点之间用相对方位+距离串起来
#   （"2-3 45° 20米" / "题卡图形2号点到题卡图形1号点东偏南20度"），
#   整题没有任何一个绝对坐标基准。
RE_REL_SEG = re.compile(
    r"\d+\s*[-—~至]\s*\d+\s*[^\n，。；]{0,12}?\d+\s*(?:°|度)\s*[^\n，。；]{0,10}?\d+\s*(?:米|m|M)")
# 「图形N号点」+「到/位于」+方位词 —— 表示图形点之间靠相对关系定位
RE_GRAPH_REL = re.compile(r"(号点|点)\s*(?:到|位于)[^\n，。；]{0,14}"
                          r"(?:方向|°|度|NE|NW|SE|SW|东|南|西|北)")
# 「A点到B点…方向」/「A位于B…方向」
RE_REL_PAIR = re.compile(r"[^\n，。；]{1,16}(?:点|处)\s*(?:到|位于|在)"
                         r"[^\n，。；]{1,16}(?:点|处|避让点|过渡点)"
                         r"[^\n，。；]{0,12}(?:方向|°|度|NE|NW|SE|SW)")


# ==================================================== 对外常量

# ★ 考点标签（十类）。顺序 = 界面下拉里的排列顺序。
#   前 7 个能自动认；后 3 个（时钟/罗盘/三角函数）当前 0 命中，属"预留位"——
#   规则在、菜单在，导入新题命中了就自动挂上，用户也能手动勾。
EXAM_TAGS = (
    ("wind",         "风向题"),
    ("angle",        "夹角题"),
    ("same_opp",     "航向相同相反题"),
    ("airspace",     "空域限制题型"),
    ("takeoff_base", "起飞点双条件题"),
    ("dual_coord",   "双经纬度题"),
    ("airspace_rng", "空域范围限制题"),
    # 以下几类保留为可由新导入题卡命中的分类。
    ("clock",        "时钟题"),
    ("compass",      "罗盘题"),
    ("trig",         "三角函数题"),
)

# 形状标签（独立一套）
CURRICULUM_SHAPES = (
    ("等边三角形", "等边三角形"), ("直角三角形", "直角三角形"),
    ("梯形", "梯形"), ("五边形", "五边形"), ("蝴蝶形", "蝴蝶形"),
    ("六边形", "六边形"), ("扫描航线", "扫描航线"), ("圆形", "圆形"),
)

# 旧版标签继续可见，保证此前手动标注不丢；教学页八类固定排在前面。
SHAPE_TAGS = tuple(("graph:" + code, name)
                   for code, name in CURRICULUM_SHAPES + (
                       ("等腰三角形", "等腰三角形"), ("三角形", "三角形"),
                       ("正方形", "正方形"),
                   ))

# 其余结构标签（不属十类考点，但筛选有用）
OTHER_TAGS = (
    ("avoid",   "避让点/中转点"),
    ("dual_ang", "双夹角结构"),
    ("no_base", "教员画图重规划"),
)

TAGS = EXAM_TAGS + OTHER_TAGS + SHAPE_TAGS

# code → 显示名
LABEL = dict(TAGS)

# 显示名 → code（界面下拉里用 currentData 存 code，不靠文案）
CODE = {v: k for k, v in TAGS}

# 考点类的 code 集合（用于界面分组）
EXAM_CODES = tuple(c for c, _ in EXAM_TAGS)
SHAPE_CODES = tuple(c for c, _ in SHAPE_TAGS)

# ★ 三组标签的 code 集合 —— 界面按这个**把下拉拆开**
#   （用户 2026-09-21：「形状跟题型分开」）
#   exam  → 「考点」下拉（十类，考什么）
#   shape → 「形状」下拉（画什么图）
#   other → 结构类，跟考点混在一起会稀释十类考点，单独归入 shape 那一档
OTHER_CODES = tuple(c for c, _ in OTHER_TAGS)


def group_of(code):
    """code 属于哪一组：``exam`` / ``shape`` / ``other``。

    ★ 界面靠它决定"这个标签该进哪个下拉" —— 不要在界面里写
      ``code.startswith("graph:")`` 这种按前缀猜的判定：
      形状以后可能加不带 ``graph:`` 前缀的编码，前缀判定会静默漏掉。
    """
    if code in EXAM_CODES:
        return "exam"
    if code in SHAPE_CODES:
        return "shape"
    return "other"

# 当前 0 命中的"预留位"考点 —— 界面上如果题数为 0 就不列进下拉，
# 但手动打标菜单里要有（用户可能是为将来导入的题准备的）
RESERVED = ("clock", "compass", "trig")

# 下拉里"题数少于这个就不列"的门槛 —— **只对结构类/形状类生效**。
# 十类考点（EXAM_CODES）不受此限：用户钦定的筛选维度，哪怕只有 1 题也得能筛。
MIN_COUNT = 3


# ==================================================== 取字段

def _s(q, key):
    v = q.get(key) if isinstance(q, dict) else None
    return str(v) if v else ""


def _req(q):
    return _s(q, "requirements_text")


def _cond(q):
    return _s(q, "conditions_text")


def _full(q):
    """全文 —— 空域类词可能在任意字段，必须搜全"""
    return "\n".join((_req(q), _cond(q), _s(q, "suggested_text"),
                      _s(q, "graph_hint")))

def requires_avoidance_point_shift(q):
    """判断题目是否要求首个航点作为避让或过渡点。"""
    text = _full(q)
    return bool(RE_AVOID.search(text) or RE_AIRSPACE.search(text))


# ==================================================== 主分类

def classify(q):
    """给一道题打**自动**考点标签，返回 code 列表（稳定顺序，不含手动标签）。

    返回的 code 是稳定顺序的（按 TAGS 里的排列），方便界面直接展示，
    也方便验收脚本比对 —— 不要输出 set，顺序飘了 diff 起来很烦。
    """
    c = _cond(q)
    r = _req(q)
    f = _full(q)
    got = []

    # ---- 坐标基准 ----
    heads = RE_COORD_HEAD.findall(c)
    n_coord = len(RE_COORD_ANY.findall(c))
    has_takeoff_coord = any("起飞点" in h for h in heads)
    has_graph_coord = any("图形" in h or "号点" in h for h in heads)
    has_any_coord = bool(n_coord or RE_DEG_PAIR.search(c)
                         or RE_LATLON_WORD.search(c))

    # ---- ①双经纬度题：题面给了 ≥2 组坐标，或基准点被指定成图形点 ----
    if n_coord >= 4 or (has_graph_coord and not has_takeoff_coord):
        got.append("dual_coord")

    # ---- ⑤起飞点双条件题：起飞点同时给了「距离」+「方位」两个条件 ----
    #   实测 17 题形态："起飞点到题卡图形1号点的距离是400米，起飞点到题卡图形
    #   1号点北偏东4°方向" / "起飞点距1号航点310米；起飞点到1号航点的航向与…"
    if has_takeoff_coord:
        got.append("takeoff_base")
    else:
        # 没有"起飞点经纬度"表头，但有"起飞点到X的距离是N米" + 方位 → 双条件
        has_dist = re.search(r"起飞点\s*(?:到|距|至)[^，。；\n]{0,14}?"
                             r"(?:距离|距)\s*(?:是|为)?\s*\d+\s*(?:米|m|M)", c)
        has_dir = re.search(r"起飞点\s*(?:到|至)[^，。；\n]{0,16}?"
                            r"(?:方向|航向|[东南西北]{1,2}偏|[NSEW]{2})", c)
        if has_dist and has_dir:
            got.append("takeoff_base")

    # ---- ②夹角题：给了角度约束（∠符号 / "角为90°"）----
    if RE_ANGLE_CONSTRAINT.search(r) or RE_ANGLE_CONSTRAINT.search(c):
        got.append("angle")

    # ---- ③航向相同相反题 ----
    if RE_SAME_OPP.search(f):
        got.append("same_opp")

    # ---- ④空域限制题型 ----
    if RE_AIRSPACE.search(f):
        got.append("airspace")

    # ---- ⑩空域范围限制题：给了半径/不得超出的范围约束 ----
    if RE_AIRSPACE_RANGE.search(f):
        got.append("airspace_rng")

    # ---- ①风向题 ----
    if RE_WIND.search(c):
        got.append("wind")

    # ---- 预留位三类（当前 0 命中，规则先备着）----
    if RE_CLOCK.search(f):
        got.append("clock")
    if RE_COMPASS.search(f):
        got.append("compass")
    if RE_TRIG.search(f):
        got.append("trig")

    # ---- 结构类（非十类考点，筛选辅助）----
    has_avoid = bool(RE_AVOID.search(c))
    n_branch = len(RE_REL_BRANCH.findall(c))
    second_layer = (n_branch >= 1 or
                    bool(re.search(r"(?:避让点|过渡点|过度点|会让点).{0,6}为?\s*"
                                   r"(NE|NW|SE|SW|正北|正南|正东|正西)", c)))
    if not has_avoid:
        second_layer = (n_branch >= 1 or bool(RE_REL_PAIR.search(c))
                        or bool(RE_HEADING_ANGLE.search(c)))
    if has_avoid:
        got.append("dual_ang" if second_layer else "avoid")
    elif second_layer and not has_takeoff_coord:
        got.append("dual_ang")

    rel_structure = bool(RE_REL_SEG.search(f) or RE_GRAPH_REL.search(f)
                         or RE_HEADING_ANGLE.search(c))
    if rel_structure and not has_any_coord:
        got.append("no_base")

    # ---- 形状（独立一套，只取第一个命中的，细分类优先）----
    for code, pat in GRAPH_RULES:
        if re.search(pat, f):
            got.append("graph:" + code)
            break

    return _order(got)


def tags(q, manual=None, off=None):
    """**界面与筛选都用这个** —— 自动标签 + 手动标签 − 手动排除。

    manual: 手动打的 code 列表（存在题库数据里，见 qmeta.manual_tags）。
            手动标签优先：用户手勾的就算自动认不出来也一定在。
    off:    用户**手动排除**的 code 列表（qmeta.manual_tags_off，v1.9.11）。
            用户在右键菜单里取消勾选一个自动认出的标签时记到这里 ——
            不记的话并集语义会让"关不掉认错的标签"。
            排除是显式覆盖，规则将来改了重新认出来也照样被压住。
    """
    got = classify(q)
    for code in (manual or []):
        if code and code not in got:
            got.append(code)
    if off:
        got = [c for c in got if c not in off]
    return _order(got)


def has(q, code, manual=None, off=None):
    return code in tags(q, manual, off)


def _order(codes):
    """按 TAGS 的声明顺序排，未知 code 排最后（不丢）"""
    rank = {c: i for i, (c, _) in enumerate(TAGS)}
    return sorted(set(codes), key=lambda c: rank.get(c, 999))


# ==================================================== 汇总与筛选

def table(questions, manual_map=None, include_reserved=False, min_count=MIN_COUNT,
          group=None, off_map=None):
    """全库标签统计：[(code, 显示名, 题数), ...]，按题数倒序。

    界面上的题型下拉用它决定"哪些题型值得列出来" ——
    结构类/形状类标签题数 < min_count 的纯属噪音，不列（include_reserved=True 时，
    预留位照样列，用于"手动打标"菜单 —— 那里需要十类齐全）。

    ★ 十类考点（EXAM_CODES）**不走门槛**：哪怕全库只命中 1 题也列出来 ——
      它们是用户明确要的筛选维度，「空域范围限制题」实测就 1 题。

    ★ manual_map 的键统一用**字符串题号**（跟 config / qmeta 一致）——
      用 int 键会静默取不到手动标签（验收 B15/D8 实测踩到）。

    ★ ``group`` 参数（2026-09-21 加）：只取某一组标签 —— 界面用它把
      「考点」和「形状」拆成两个独立下拉（用户要求"形状跟题型分开"）。
      取 ``"exam"`` → 只要十类考点；``"shape"`` → 形状 + 结构类；
      ``None``（默认）→ 全部，兼容旧调用。

    ★ ``off_map``（v1.9.11）：手动排除表 {题号str: [code, ...]}，
      语义见 tags() 的 off 参数。
    """
    from collections import Counter
    manual_map = manual_map or {}
    off_map = off_map or {}
    cnt = Counter()
    for q in questions:
        for c in tags(q, manual_map.get(str(q.get("id"))),
                      off_map.get(str(q.get("id")))):
            cnt[c] += 1

    # ★ 分组过滤放在统计**之后** —— 统计必须遍历全部题，
    #   否则形状组的题数会被漏算（cnt 是按 code 累加的，过滤 code 不影响累加）。
    if group == "exam":
        wanted = EXAM_TAGS
    elif group == "shape":
        # 形状下拉里也放结构类：它们都是"这道题长什么样/什么结构"的描述，
        # 跟"考什么"是两回事。不给它们第三档下拉，是免得筛题栏控件又变多。
        wanted = SHAPE_TAGS + OTHER_TAGS
    else:
        wanted = TAGS

    rows = []
    for c, name in wanted:
        n = cnt.get(c, 0)
        if n == 0:
            # ★ 预留位即使 0 命中，include_reserved 时也要列出来 ——
            #   手动打标菜单需要十类齐全（用户可能要手动勾时钟题）。
            if include_reserved and c in RESERVED:
                rows.append((c, name, 0))
            elif group == "shape" and c in {
                    "graph:" + code for code, _ in CURRICULUM_SHAPES}:
                # 教学页列出的形状固定显示，零命中也要能筛选与补标。
                rows.append((c, name, 0))
            continue
        if c in RESERVED and not include_reserved:
            continue
        # ★ 十类考点是用户钦定的筛选维度（2026-09-21）——**不管几题都要能筛**，
        #   哪怕全库只有 1 题（如「空域范围限制题」）。门槛只对结构类/形状类生效，
        #   那些是自动附带出来的描述性标签，长尾噪音多。
        if (c not in RESERVED and c not in EXAM_CODES
                and not (group == "shape" and c in {
                    "graph:" + code for code, _ in CURRICULUM_SHAPES})
                and n < min_count and not include_reserved):
            continue
        rows.append((c, name, n))
    # 有题数的排前面（按题数倒序），0 题的在最后（保留声明顺序）
    rows.sort(key=lambda r: (-r[2], r[1]))
    return rows


def filter_by(questions, code, manual_map=None, off_map=None):
    """按标签筛题。code 为空/None → 原样返回（"全部"）。"""
    if not code:
        return list(questions)
    manual_map = manual_map or {}
    off_map = off_map or {}
    return [q for q in questions
            if has(q, code, manual_map.get(str(q.get("id"))),
                   off_map.get(str(q.get("id"))))]


def naked(questions, manual_map=None, off_map=None):
    """一个标签都没有的题（含自动和手动）。

    导入题（图片/PDF 扫描件）大批落在这里 —— 题面没文字，认不出任何考点。
    界面上"只看无标签"筛的就是它们，方便用户手工补。
    """
    manual_map = manual_map or {}
    off_map = off_map or {}
    return [q.get("id") for q in questions
            if not tags(q, manual_map.get(str(q.get("id"))),
                        off_map.get(str(q.get("id"))))]
