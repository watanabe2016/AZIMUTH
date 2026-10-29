"""读第三方地面站软件里保存的任务，直接当作答来判题。

为什么走这条路：让用户"把坐标填进我们的软件"等于重做一遍题，
真正的痛点是**他已经在别的地面站里把航线画完了，还要抄一遍坐标**。
LinkGCS2 把任务存成明文 JSON（SQLite 的 ``Mission`` 表），
经纬度、高度、速度、循环次数全在里面，直接读出来就能比对，
比"截图给视觉模型认 6 位小数"精确得多 —— 那是厘米级 vs 猜。

本模块 **零 Qt 依赖**，可以脱离界面单测。

## 多地面站支持（v1.9.4 起）

用户手里的地面站不一定是 LinkGCS2 —— 华科尔、汉鲲、凌动的都用得不少。
所以定位与解析都抽成了 ``STATIONS`` 表里的 **profile**：

- **定位**：每家有自己的数据文件名 / 安装目录 / ini 落点，
  见各 profile 的 ``db_names`` / ``app_names`` / ``table``。
- **解析**：LinkGCS2 的字段名是**实测出来的**（``flySpeed``、``cylcle`` 这些）；
  另外三家的 JSON 结构我们**没有真实样本**，只能走
  :func:`parse_generic` 的宽容启发式（到处找 ``lat``/``lon``/``alt`` 一类字段）。
  猜不出来的**绝不硬编** —— 宁可让用户手填，也别填个错数字让他以为
  "软件判错了"。界面会把"这家是靠猜的"如实讲出来。

数据位置（三级回落，谁先命中用谁）：

1. 显式传入的 ``db_path``
2. 用户手动指定（设置页填的，存在 config）
3. 各 profile 的自动探测（ini → 常规目录）

LinkGCS2 是 Qt 系软件，ini 里路径写的是正斜杠（``E:/Users/.../LinkGCS2``），
Windows 的 ``pathlib`` 能直接吃，不用做转换。
"""

from __future__ import annotations

import configparser
import glob
import json
import os
import pathlib
import re
import sqlite3

import geo

# ------------------------------------------------------- 定位

INI_CANDIDATES = (
    r"%APPDATA%\LinkGCS2.xyz\LinkGCS2.ini",
    r"%APPDATA%\LinkGCS2\LinkGCS2.ini",
    r"%LOCALAPPDATA%\LinkGCS2.xyz\LinkGCS2.ini",
)

# savePath 不好使时的兜底：常规「文档」位置
DIR_CANDIDATES = (
    r"E:\Users\{user}\Documents\LinkGCS2",
    r"%USERPROFILE%\Documents\LinkGCS2",
    r"D:\Users\{user}\Documents\LinkGCS2",
)


# ================================================================ 地面站档案
#
# 一家一条。字段说明：
#   key        内部标识（存 config 用，别改）
#   name       界面上显示的名字
#   db_names   数据文件名（**小写**，扫盘按小写比）
#   app_names  安装/数据目录候选名（各盘根目录下会挨个试）
#   ini_paths  ini 落点（%APPDATA% 那套），可以没有
#   table      任务表名
#   json_col   任务 JSON 所在列名（None = 用 PRAGMA 自己找）
#   parse      解析函数名（本模块内）
#   verified   True = 有真实样本实测过；False = 结构靠推断
#
# ⚠️ 后三家的字段名**没有实测样本**，``verified=False``。
#    这不是占位——定位和手动指定是真能用的（用户指到目录/文件就能读），
#    只有"字段名对不对得上"要靠 generic 启发式。界面必须如实告诉用户。

def parse_generic(raw):
    """通用解析：不认识这家地面站的格式时，到处翻坐标字段。

    策略是"**找得到就用，找不到就留空**"，绝不猜：

    1. 递归走一遍 JSON，把所有 ``{lat, lon}`` 形态的对象揪出来当航点
       （字段名容忍 ``lat``/``latitude``/``Lat`` 这些变体）
    2. 顶层找起飞点（``takeoff`` / ``home`` / ``start`` 打头的键）
    3. 高度/速度按同名变体找；一个航点里找到就带上

    这样即使字段名跟 LinkGCS2 完全不同，只要它是个"存航点的 JSON"，
    坐标就能读出来 —— 而坐标恰恰是判题里最要紧的部分。
    """
    # JSON 坏了就返回 None，**别把异常抛到判题流程里** —— 库里存的是
    # 用户软件写的文本，可能被截断、可能是空串、也可能根本不是 JSON。
    # 判题要退化成"手动填"，不能整个炸掉。
    if isinstance(raw, str):
        try:
            d = json.loads(raw)
        except (ValueError, TypeError):
            return None
    else:
        d = raw or {}
    if not isinstance(d, dict):
        return None

    def _pick(src, *names):
        """按一组候选名取第一个能转 float 的值。"""
        for n in names:
            if n in src:
                v = _f(src.get(n))
                if v is not None:
                    return v
        # 大小写不敏感再找一遍（有些软件用 Latitude / lat 混着来）
        low = {str(k).lower(): k for k in src.keys()}
        for n in names:
            k = low.get(n.lower())
            if k is not None:
                v = _f(src.get(k))
                if v is not None:
                    return v
        return None

    def _text(src, *names):
        """取字符串字段（``_pick`` 只吃数字，名字得单独取）。"""
        for n in names:
            v = src.get(n)
            if isinstance(v, str) and v.strip():
                return v.strip()
        low = {str(k).lower(): k for k in src.keys()}
        for n in names:
            v = src.get(low.get(n.lower()))
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    def _coords(it):
        """从一个 dict 里取 (lat, lon)，取不到返回 (None, None)。

        两条路：① 扁平字段（``lat``/``Longitude`` 及其变体）
        ② MAVLink COMMAND_LONG 七元组 ``params[4] / params[5]`` ——
        LinkGCS2 就长这样，别家也可能照抄这套。
        """
        la = _pick(it, "lat", "latitude")
        lo = _pick(it, "lon", "lng", "longitude")
        if la is not None and lo is not None:
            return la, lo
        pr = it.get("params") or it.get("Params") or []
        if isinstance(pr, (list, tuple)) and len(pr) > 5:
            return _f(pr[4]), _f(pr[5])
        return None, None

    # ---- 起飞点 ----
    # 字段名三种形态都要吃：① 顶层扁平 takeoffLat/takeoffLon，
    # ② 嵌套对象 {"HomePosition": {"Latitude":..., "Longitude":...}}，
    # ③ 二元数组 [lat, lon]。
    takeoff = None
    low_d = {str(k).lower(): k for k in d.keys()}
    for pref in ("takeoff", "home", "start", "base", "launch"):
        kk = [k for k in low_d if k.startswith(pref)]
        if not kk:
            continue
        for k in kk:
            sub = d[low_d[k]]
            la = lo = None
            if isinstance(sub, dict):
                la, lo = _coords(sub)
            elif isinstance(sub, (list, tuple)) and len(sub) >= 2:
                la, lo = _f(sub[0]), _f(sub[1])
            if la is not None and lo is not None:
                takeoff = (la, lo)
                break
        if takeoff:
            break
    if takeoff is None:
        # 扁平形态：takeoffLat / takeoffLon 各自是标量
        la = _pick(d, "takeoffLat", "homeLat", "takeoffLatitude",
                   "homePositionLat", "homePoistionLat")
        lo = _pick(d, "takeoffLon", "homeLon", "homeLng",
                   "takeoffLongitude", "homePositionLon", "homePoistionLon")
        if la is not None and lo is not None:
            takeoff = (la, lo)

    # ---- 航点：递归找所有像航点的对象 ----
    found = []

    def walk(node, depth=0):
        if depth > 8:
            return
        if isinstance(node, dict):
            la, lo = _coords(node)
            if la is not None and lo is not None \
                    and -90 <= la <= 90 and -180 <= lo <= 180:
                # 起飞点本身也可能是列表里的第一项，标出来但不算航点
                found.append(node)
            for v in node.values():
                walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                walk(v, depth + 1)

    for k, v in d.items():
        if str(k).lower() in ("items", "points", "waypoints", "missions",
                             "route", "routes", "path", "nodes"):
            walk(v)
    if not found:
        # 没有明显的航点数组 → 整棵树都翻一遍
        walk(d)

    pts = []
    for i, it in enumerate(found):
        la, lo = _coords(it)
        if la is None or lo is None:
            continue
        if takeoff is not None and abs(la - takeoff[0]) < 1e-9 \
                and abs(lo - takeoff[1]) < 1e-9:
            continue                      # 这就是起飞点，别重复算成航点
        pts.append({
            "lat": la, "lon": lo,
            "alt": _pick(it, "alt", "altitude", "Altitude", "height",
                         "relativeHeight"),
            "idx": i,
            "fly_speed": _pick(it, "flySpeed", "speed", "Speed",
                               "horizontalSpeed", "vel"),
            "climb_speed": _pick(it, "climbSpeed", "climb", "verticalSpeed"),
            "hover": _pick(it, "HoverTime", "hover", "hoverTime", "loiter"),
            "turn": _pick(it, "turningMode", "turnMode", "turn", "yawMode"),
        })
    if not pts:
        return None

    fly = _mode_of(pts, "fly_speed")
    climb = _mode_of(pts, "climb_speed")
    hover = _mode_of(pts, "hover")
    turn = _mode_of(pts, "turn")

    return {
        "name": _text(d, "name", "Name", "TaskName", "taskName", "title",
                      "Title", "MissionName"),
        "takeoff": takeoff,
        "takeoff_alt": _pick(d, "takeoffHeihgt", "takeoffHeight",
                             "homeHeight", "takeoffAlt", "HomeHeight"),
        "points": pts,
        "all": ([{"lat": takeoff[0], "lon": takeoff[1], "idx": -1,
                  "alt": _pick(d, "takeoffHeihgt", "takeoffHeight",
                               "homeHeight"),
                  "takeoff": True}] if takeoff else []) + pts,
        "params": {
            "speed": fly if fly is not None else _pick(
                d, "globalSpeed", "speed", "Speed"),
            "alt": _pick(d, "globalAltitude", "altitude", "Altitude"),
            "max_climb": (climb if climb is not None
                          else _pick(d, "maxClimb", "MaxClimbRate",
                                     "maxClimbRate", "climbSpeed")),
            "max_down": _pick(d, "maxDown", "MaxDownRate", "maxDownRate",
                              "descendSpeed"),
            "cycle": _pick(d, "cylcle", "cycle", "Cycle", "loop",
                           "LoopCount", "loopCount"),
            "home_alt": _pick(d, "takeoffHeihgt", "takeoffHeight",
                              "homeHeight", "HomeHeight", "takeoffAlt"),
            "hover": hover,
            "turn": turn,
            "global_speed": _pick(d, "globalSpeed", "speed", "Speed"),
            "start_speed": _pick(d, "flySpeed", "speed", "Speed"),
        },
        "raw": d,
    }


STATIONS = {
    "linkgcs2": {
        "key": "linkgcs2",
        "name": "LinkGCS2（默认）",
        "db_names": ("linkrobotrics.db",),
        "app_names": ("LinkGCS2", "LinkGCS", "LinkGCS2.xyz"),
        "ini_paths": INI_CANDIDATES,
        "dir_candidates": DIR_CANDIDATES,
        "table": "Mission",
        "parse": "parse_mission",
        "verified": True,
    },
    "walkera": {
        "key": "walkera",
        "name": "华科尔（Walkera）",
        # 华科尔的地面站叫「Flight Controller」「Ground Station」等几种，
        # 数据文件常见 .db / .sqlite / .mission，文件名也百花齐放 ——
        # 所以 db_names 给宽一点，靠"里面有坐标表"来验真。
        "db_names": ("walkera.db", "gs.db", "mission.db", "walkera_gs.db",
                     "flight.db", "wt.db"),
        "app_names": ("Walkera", "WalkeraGCS", "WalkeraGroundStation",
                      "华科尔", "Walkera Flight Controller"),
        "ini_paths": (r"%APPDATA%\Walkera\Walkera.ini",
                      r"%APPDATA%\WalkeraGroundStation\config.ini"),
        "dir_candidates": (r"%USERPROFILE%\Documents\Walkera",
                           r"%USERPROFILE%\Documents\华科尔"),
        "table": None,          # 表名不固定 → 靠 PRAGMA 自己找
        "parse": "parse_generic",
        "verified": False,
    },
    "hankun": {
        "key": "hankun",
        "name": "汉鲲（Hankun）",
        "db_names": ("hankun.db", "hk.db", "mission.db", "hkgs.db",
                     "hankun_gs.db"),
        "app_names": ("Hankun", "HankunGCS", "汉鲲",
                      "汉鲲地面站", "HKGroundStation"),
        "ini_paths": (r"%APPDATA%\Hankun\Hankun.ini",
                      r"%APPDATA%\HankunGCS\config.ini"),
        "dir_candidates": (r"%USERPROFILE%\Documents\Hankun",
                           r"%USERPROFILE%\Documents\汉鲲"),
        "table": None,
        "parse": "parse_generic",
        "verified": False,
    },
    "lingdong": {
        "key": "lingdong",
        "name": "凌动（Lingdong）",
        "db_names": ("lingdong.db", "ld.db", "mission.db", "ldgs.db",
                     "lingdong_gs.db"),
        "app_names": ("Lingdong", "LingDong", "凌动", "凌动地面站",
                      "LDGroundStation"),
        "ini_paths": (r"%APPDATA%\Lingdong\Lingdong.ini",
                      r"%APPDATA%\LingDongGCS\config.ini"),
        "dir_candidates": (r"%USERPROFILE%\Documents\Lingdong",
                           r"%USERPROFILE%\Documents\凌动"),
        "table": None,
        "parse": "parse_generic",
        "verified": False,
    },
}

# 自动探测的顺序：实测过的排前面 —— 认得出 LinkGCS2 就别去猜别家
STATION_ORDER = ("linkgcs2", "walkera", "hankun", "lingdong")


def station(key):
    """取一条 profile（不认得的 key 回落到 linkgcs2）。"""
    return STATIONS.get(key) or STATIONS["linkgcs2"]


def station_names():
    """``[(key, name), ...]``，给设置页的下拉用。"""
    return [(k, STATIONS[k]["name"]) for k in STATION_ORDER]


def _parse_of(prof, raw):
    """按 profile 指定的解析函数解析。

    ``prof`` 为 None（调用方没指定家）时回落到 LinkGCS2 那套 ——
    兼容旧调用点（``Load_task(db_path=...)`` 不带 key 的用法）。
    """
    if not prof:
        prof = STATIONS["linkgcs2"]
    fn = globals().get(prof.get("parse") or "parse_mission")
    if fn is None:
        fn = parse_mission
    return fn(raw)


def _db_in(folder, prof=None):
    """在一个数据目录里找 db 文件，返回 pathlib.Path 或 None。

    ``prof`` 给了就按那家的文件名找；没给就把所有家的名字都试一遍 ——
    这样"我不确定用的是哪家"时也能扫到东西。
    """
    if not folder:
        return None
    p = pathlib.Path(folder)
    names = prof["db_names"] if prof else tuple(
        n for k in STATION_ORDER for n in STATIONS[k]["db_names"])

    # LinkGCS2 的固定落点优先（它是唯一实测过的）
    if not prof or prof["key"] == "linkgcs2":
        for cand in (p / "db" / "LinkRobotrics.db", p / "LinkRobotrics.db"):
            if cand.is_file():
                return cand

    for nm in names:
        for cand in (p / nm, p / "db" / nm, p / "data" / nm,
                     p / "mission" / nm):
            if cand.is_file():
                return cand

    # 换个名字也认：目录里任何 .db 都当候选（下面 _db_is_usable 会验真）
    for hit in sorted(glob.glob(str(p / "**" / "*.db"), recursive=True)):
        base = os.path.basename(hit).lower()
        if any(n == base for n in names) or "ink" in base \
                or "mission" in base or "flight" in base or "gs" in base:
            return pathlib.Path(hit)
    return None


def _db_is_usable(path):
    """这个 sqlite 文件里真的存着航线数据吗？

    为什么必须验真而不是"文件存在就用"：用户手动指到一个随便的 .db
    （浏览器缓存、别的软件的数据），我们照样会去问它要 Mission 表，
    然后报一个"读任务失败"的错，用户一头雾水。先验一下，找不到就老实说
    "这个库里没看到航线数据"。
    """
    try:
        con = sqlite3.connect("file:%s?mode=ro" % pathlib.Path(path).as_posix(),
                              uri=True)
    except Exception:
        return False, "打不开这个数据库文件。"
    try:
        tables = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table'")]
        if not tables:
            return False, "这个数据库里没有任何表。"
        # 找有没有一张表带 JSON/TEXT 大字段 + 名字像任务的
        for t in tables:
            tl = t.lower()
            if any(w in tl for w in ("mission", "task", "flight", "route",
                                     "plan", "waypoint")):
                return True, t
        # 退一步：任何表里有像 JSON 的列也算（generic 解析能吃）
        for t in tables:
            try:
                cols = [r[1] for r in con.execute(
                    "PRAGMA table_info(\"%s\")" % t)]
            except Exception:
                continue
            for c in cols:
                if c.lower() in ("json", "data", "content", "mission",
                                 "missionjson", "task"):
                    return True, t
        return False, "这个库里没找到存航线的表（看看是不是选错文件了）。"
    except Exception as e:
        return False, "读这个数据库出错：%s" % e
    finally:
        con.close()


# ------------------------------------------------------- 用户指定 / 扫盘

# 扫盘时的目标文件名（小写匹配）—— 所有家的名字合起来。
# 宁可窄一点也别把一堆无关的 .db 抓回来，但现在是四家共用，
# 所以后面一定跟一道 _db_is_usable 验真，不是"文件名叫对就算数"。
DB_NAMES = tuple(n for k in STATION_ORDER for n in STATIONS[k]["db_names"])

# 扫盘要跳过的目录：系统盘那些又大又不可能有地面站数据的
SCAN_SKIP = {
    "$recycle.bin", "windows", "system volume information", "program files",
    "program files (x86)", "programdata", "appdata", "node_modules",
    "$windows.~bt", "$windows.~ws", "msys64", "anaconda3", "miniconda3",
    "recovery", "perflogs",
}


def candidates(keys=None):
    """所有「可能装着地面站数据」的目录，去重后的列表。

    扫盘只在这些目录里找，而不是全盘递归 —— 全盘扫一个 2T 的机械盘要
    几分钟，用户点一下就卡在那里，体验上不可接受。这些位置覆盖了
    各家地面站的默认安装/数据落点，够用；找不到还有手动指定兜底。

    ``keys`` 限定只看哪几家（None = 全部）。用户在设置页明确选了
    "我用的是汉鲲"时，就别去翻 LinkGCS2 的目录，省时间也少误报。
    """
    out, seen = [], set()
    keys = keys or STATION_ORDER

    def add(p):
        if not p:
            return
        try:
            s = str(pathlib.Path(p))
        except Exception:
            return
        if s and s not in seen and pathlib.Path(s).is_dir():
            seen.add(s)
            out.append(pathlib.Path(s))

    user = os.environ.get("USERNAME") or pathlib.Path.home().name

    # 1) ini 里记的 savePath —— 最准
    for k in keys:
        for raw in STATIONS[k].get("ini_paths") or ():
            ini = pathlib.Path(os.path.expandvars(raw))
            if ini.is_file():
                add(ini.parent)
                for sp in _ini_save_paths(ini):
                    add(sp)
                    add(pathlib.Path(sp).parent)

    # 2) 常规「文档」位置
    for k in keys:
        for raw in STATIONS[k].get("dir_candidates") or ():
            add(os.path.expandvars(raw.format(user=user)))

    # 3) 各盘符根目录下的常见名字 + 一级子目录
    for drv in _drives():
        for k in keys:
            for name in STATIONS[k].get("app_names") or ():
                add(drv / name)
                add(drv / "Documents" / name)
                add(drv / "Users" / user / "Documents" / name)
    return out


def _ini_save_paths(ini):
    """把 ini 里所有像路径的值取出来（savePath / path / dir …）。"""
    out = []
    try:
        cp = configparser.ConfigParser()
        cp.optionxform = str
        cp.read(str(ini), encoding="utf-8")
    except Exception:
        return out
    for sec in cp.sections():
        for key, val in cp.items(sec):
            k = key.lower()
            if "path" in k or "dir" in k:
                v = (val or "").strip().strip('"').replace("\\\\", "\\")
                if v and (":" in v or v.startswith("\\\\")):
                    out.append(v)
    return out


def _drives():
    """当前可用的盘符。"""
    out = []
    for letter in "CDEFGHIJK":
        p = pathlib.Path("%s:\\" % letter)
        try:
            if p.exists():
                out.append(p)
        except Exception:
            continue
    return out


def scan(max_seconds=25.0, on_step=None, keys=None):
    """扫盘找地面站的航线数据库。

    只扫 :func:`candidates` 里那些目录（及其下的子目录），不碰系统目录。
    返回 ``(path, tried)``：``path`` 是找到的 db（没找到为 None），
    ``tried`` 是实际扫过的目录数 —— 汇报给用户"我翻了 N 个地方"，比
    一句"没找到"有用得多。

    ★ **要验真**：文件名对上只是"嫌疑"，还要 :func:`_db_is_usable`
    确认里面真有存航线的表。四家地面站共用一个名字池，光看名字会把
    汉鲲的 mission.db 当成凌动的抓回来。

    ``on_step`` 每扫一个目录回调一次，签名 ``(n, folder)``，给进度条用。
    """
    import time
    t0 = time.time()
    tried = 0
    for base in candidates(keys):
        for root, dirs, files in os.walk(str(base)):
            # 原地过滤 dirs 才能让 os.walk 真正跳过
            dirs[:] = [d for d in dirs
                       if d.lower() not in SCAN_SKIP
                       and not d.startswith("$")]
            tried += 1
            if on_step is not None:
                try:
                    on_step(tried, root)
                except Exception:
                    pass
            for fn in files:
                if fn.lower() in DB_NAMES:
                    cand = pathlib.Path(root) / fn
                    ok, _why = _db_is_usable(cand)
                    if ok:
                        return cand, tried
            if time.time() - t0 > max_seconds:
                return None, tried
    return None, tried


def probe(folder, key=None):
    """验一个用户手填的路径到底行不行。

    返回 ``(db_path, msg)``：
      · 找到了 → (Path, "识别成功：...")
      · 没找到 → (None, "能照做的一句话原因")
    设置页要拿它做即时的红/绿反馈。

    ``key`` 给了就按那家的文件名找，没给就四家的名字都试。
    """
    prof = station(key) if key else None
    who = prof["name"] if prof else "地面站"
    if not folder:
        return None, "路径是空的 —— 请填%s的数据目录，或直接选它的数据库文件。" % who
    p = pathlib.Path(str(folder).strip().strip('"'))
    try:
        exists = p.exists()
    except Exception:
        return None, "这个路径系统读不了，检查一下有没有写错字符。"
    if not exists:
        return None, "这个路径不存在：%s" % p
    if p.is_file():
        if p.suffix.lower() in (".db", ".sqlite", ".sqlite3", ".mission"):
            ok, why = _db_is_usable(p)
            if ok or p.suffix.lower() == ".mission":
                return p, "识别成功：直接用了这个文件。"
            return None, why
        return None, "选的是文件，但看起来不是地面站的数据库（.db / .sqlite）。"
    got = _db_in(p, prof)
    if got:
        return got, "识别成功：在这个目录里找到了 %s。" % got.name
    # 目录存在但没找到 —— 给出下一步能照做的建议
    for root, dirs, files in os.walk(str(p)):
        dirs[:] = [d for d in dirs if d.lower() not in SCAN_SKIP]
        for fn in files:
            if fn.lower() in DB_NAMES:
                cand = pathlib.Path(root) / fn
                ok, _why = _db_is_usable(cand)
                if ok:
                    return cand, ("识别成功：在子目录 %s 里找到了。" % root)
    names = "、".join(prof["db_names"][:3]) if prof else "LinkRobotrics.db"
    return None, ("这个目录里没找到%s的航线数据库（会找这些名字：%s）。\n"
                  "可以在软件里先随便保存一条航线，再回来扫一次 —— "
                  "或者直接把那个 .db 文件拖进来。"
                  % (who, names))


def user_station():
    """用户在设置里选的「地面站类型」。

    返回 ``(状态, key)``：

      · ``("auto", None)``   没选 / 选了自动 → 四家都探
      · ``("fixed", "hankun")`` 明确指定了某家 → 只探那家

    和 :func:`_user_db` 一样是三态思路：**一旦用户说了"我用的是汉鲲"，
    就别再拿 LinkGCS2 的数据去凑** —— 那不是省事，是拿别人的航线判他的题。
    """
    try:
        import config as cfg
        v = (cfg.get("gcs_station", "") or "").strip()
    except Exception:
        return "auto", None
    if not v or v == "auto" or v not in STATIONS:
        return "auto", None
    return "fixed", v


def _user_db():
    """用户手动指定的路径。

    返回 ``(状态, path)``：

      · ``("unset", None)``  没设过 → 交给自动探测
      · ``("ok", Path)``     设了且可用 → 就用它
      · ``("bad", None)``    设了但读不到 → :func:`find_db` **不再自动回落**

    为什么要三态而不是简单的 "有就返回、没有就 None"：如果"填错了"和
    "没填"都返回 None，find_db 就会在用户填错时**悄悄改用自动找到的另一份
    数据库**去判题 —— 用户以为在核对 A 那套航线，软件拿的是 B 那套，
    结果怎么算都对不上，还找不到原因。宁可如实报"读不到"。
    """
    try:
        import config as cfg
        v = (cfg.get("linkgcs_path", "") or "").strip()
    except Exception:
        return "unset", None
    if not v:
        return "unset", None
    p = pathlib.Path(v)
    if p.is_file():
        return "ok", p
    got = _db_in(p)
    if got:
        return "ok", got
    return "bad", None


def find_db(explicit=None, key=None):
    """定位地面站的航线数据库。找不到返回 None。

    优先级：显式传入 > 用户手动指定（设置里填的）> 环境变量 > ini > 常规目录。
    「用户手动指定」排在自动探测前面，且**一旦填了就完全以它为准**：
    填错时直接返回 None（不偷偷回落到自动找到的另一份），免得判的是
    另一套航线数据，用户还蒙在鼓里。

    ``key`` 指定地面站；没给就问 :func:`user_station`（用户在设置里选的）。
    选了"自动"时按 :data:`STATION_ORDER` 依次试 ——
    **实测过的 LinkGCS2 排第一**，认得出就别去猜别家。
    """
    if explicit:
        p = pathlib.Path(str(explicit))
        if p.is_file():
            return p
        got = _db_in(p, station(key) if key else None)
        if got:
            return got
        return None

    state, got = _user_db()
    if state == "ok":
        return got
    if state == "bad":
        return None          # 填了却读不到：如实报错，别换个库来判

    env = os.environ.get("CAAC_LINKGCS_DB")
    if env:
        p = pathlib.Path(env)
        if p.is_file():
            return p

    # 谁决定"找哪家"：
    #   ① 调用方显式传了 key（load_task/probe 等）→ 用它的
    #   ② 否则看用户在设置里选的 → 选了某家就只探那家
    #   ③ 都没说 → 按 STATION_ORDER 全试（实测过的排前面）
    # ★ ① 必须优先于 ②：不然代码明确说"我要凌动的库"，
    #   却因为设置里是"自动"而把 LinkGCS2 的库拿回来 —— 判的是别人的航线。
    if key and key in STATIONS:
        keys = [key]
    else:
        st_state, st_key = user_station()
        keys = [st_key] if (st_state == "fixed" and st_key) else list(
            STATION_ORDER)

    # 先问 ini
    for k in keys:
        for raw in STATIONS[k].get("ini_paths") or ():
            ini = pathlib.Path(os.path.expandvars(raw))
            if not ini.is_file():
                continue
            prof = STATIONS[k]
            for sp in _ini_save_paths(ini):
                got = _db_in(sp, prof)
                if got:
                    return got
            try:
                cp = configparser.ConfigParser()
                # 这类 ini 是 Qt 写的，键名大小写不一定规范
                cp.optionxform = str
                cp.read(str(ini), encoding="utf-8")
            except Exception:
                continue
            for sec in cp.sections():
                for key_ in ("savePath", "savepath", "SavePath", "path",
                             "dataPath"):
                    if cp.has_option(sec, key_):
                        got = _db_in(cp.get(sec, key_), prof)
                        if got:
                            return got

    # 再挨个试常规目录
    user = os.environ.get("USERNAME") or pathlib.Path.home().name
    for k in keys:
        prof = STATIONS[k]
        for raw in prof.get("dir_candidates") or ():
            got = _db_in(os.path.expandvars(raw.format(user=user)), prof)
            if got:
                return got
        # 各盘符根目录下的安装目录名也顺手看一眼 —— 有的用户把数据
        # 就放在安装目录里（尤其是便携版）
        for drv in _drives():
            for name in prof.get("app_names") or ():
                got = _db_in(drv / name, prof)
                if got:
                    return got
    return None


# ------------------------------------------------------- 读任务

# LinkGCS2 的 Mission 表就两列有意义：name / json（外加 createDate 等）。

# 各家的表名/列名不一定一样，这里做一层探测：
TASK_TABLE_HINTS = ("mission", "task", "flight", "route", "plan", "waypoint",
                    "path", "record", "history")
JSON_COL_HINTS = ("json", "data", "content", "mission", "missionjson",
                  "task", "payload", "body", "detail", "value")
NAME_COL_HINTS = ("name", "title", "taskname", "missionname", "label",
                  "caption", "remark")
DATE_COL_HINTS = ("createdate", "createtime", "date", "time", "addtime",
                  "updatetime", "modified", "timestamp", "ctime", "mtime")


def _col_match(cols, hints):
    """在一堆列名里按 hint 找最合适的那一个。

    两轮：先精确相等（``name`` == ``name``），再**包含**匹配
    （hint ``time`` 命中 ``AddTime``）。顺序不能反 —— 包含匹配很容易
    误中（``name`` 会命中 ``FileName`` / ``UserName``），精确的先来。
    """
    low = {c.lower(): c for c in cols}
    for h in hints:
        if h in low:
            return low[h]
    for h in hints:
        for lc, c in low.items():
            if h in lc:
                return c
    return None


def _detect_layout(db_path, prof=None):
    """探测这个库里"任务表"叫什么、各列叫什么。

    为什么不能写死 ``Mission`` / ``json``：四家地面站的表结构各不相同，
    写死的话除了 LinkGCS2，其余三家全都读不出来。探测出来的东西
    **在界面上要如实说明**（"我把 xxx 表当成了任务表"），
    因为猜错了得让用户有办法发现。

    返回 ``{table, json, name, date}``（找不到的字段为 None）。
    """
    table = json_col = name_col = date_col = None
    try:
        con = sqlite3.connect(
            "file:%s?mode=ro" % pathlib.Path(db_path).as_posix(), uri=True)
    except Exception:
        return {"table": None, "json": None, "name": None, "date": None}
    try:
        # 1) profile 指定了表名就直接用
        if prof and prof.get("table"):
            t = prof["table"]
            try:
                con.execute("select 1 from \"%s\" limit 1" % t)
                table = t
            except Exception:
                table = None

        tables = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table'")]

        # 2) 没指定 / 指定了但不认 → 按名字猜
        if table is None:
            for t in tables:
                tl = t.lower()
                if any(h in tl for h in TASK_TABLE_HINTS):
                    table = t
                    break

        # 3) 还是没找到 → 找哪张表里有像 JSON 的长文本列
        if table is None:
            best = None
            for t in tables:
                try:
                    cols = [r[1] for r in con.execute(
                        "PRAGMA table_info(\"%s\")" % t)]
                except Exception:
                    continue
                for c in cols:
                    if c.lower() in JSON_COL_HINTS:
                        n = con.execute(
                            "select count(*) from \"%s\"" % t).fetchone()[0]
                        if n and (best is None or n > best[1]):
                            best = (t, n, c)
            if best:
                table = best[0]
                json_col = best[2]
        if table is None:
            return {"table": None, "json": None, "name": None, "date": None}

        try:
            cols = [r[1] for r in con.execute(
                "PRAGMA table_info(\"%s\")" % table)]
        except Exception:
            cols = []
        low = {c.lower(): c for c in cols}

        if json_col is None:
            json_col = _col_match(cols, JSON_COL_HINTS)
        if json_col is None:
            # 退一步：挑一个类型是 TEXT 的列（JSON 一定存成 TEXT）
            for c in cols:
                try:
                    ty = con.execute(
                        "select typeof(\"%s\") from \"%s\" "
                        "where \"%s\" is not null limit 1"
                        % (c, table, c)).fetchone()
                except Exception:
                    continue
                if ty and ty[0] in ("text", "blob"):
                    json_col = c
                    break
        name_col = _col_match(cols, NAME_COL_HINTS)
        date_col = _col_match(cols, DATE_COL_HINTS)
        return {"table": table, "json": json_col, "name": name_col,
                "date": date_col}
    except Exception:
        return {"table": None, "json": None, "name": None, "date": None}
    finally:
        con.close()


def list_tasks(db_path, limit=None, order="newest", key=None):
    """列出任务。返回 ``[{rowid, name, create_date}]``，新的在前。

    ``order`` 取 ``"newest"``（按 rowid 倒序，实际就是保存顺序）
    或 ``"oldest"``。
    """
    prof = station(key) if key else None
    lay = _detect_layout(db_path, prof)
    if not lay["table"]:
        return []
    con = sqlite3.connect("file:%s?mode=ro" % pathlib.Path(db_path).as_posix(),
                          uri=True)
    try:
        t = lay["table"]
        sel = ["rowid"]
        if lay["date"]:
            sel.append('"%s"' % lay["date"])
        if lay["name"]:
            sel.append('"%s"' % lay["name"])
        q = "select %s from \"%s\"" % (", ".join(sel), t)
        q += " order by rowid %s" % ("desc" if order == "newest" else "asc")
        if limit:
            q += " limit %d" % int(limit)
        out, i = [], 1
        for row in con.execute(q):
            out.append({
                "rowid": row[0],
                "create_date": (row[i] if lay["date"] else ""),
                "name": (row[i + (1 if lay["date"] else 0)]
                         if lay["name"] else "任务 #%s" % row[0]),
            })
        return out
    finally:
        con.close()


def _f(v):
    """能转 float 就转，转不了返回 None。"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _mode_of(pts, key):
    """取航点里出现次数最多的那个值（众数）。

    航点级参数不一定处处相同 —— 用户可能只改了某几个航点。
    取众数比"取第一个"稳：个别漏改的点不会把整体值带偏。
    """
    from collections import Counter
    vals = [p.get(key) for p in pts if p.get(key) is not None]
    if not vals:
        return None
    return Counter(vals).most_common(1)[0][0]


def parse_mission(raw):
    """把 Mission.json 那一坨解析成统一结构。

    返回::

        {
          "name": str,
          "takeoff": (lat, lon) 或 None,   # 起飞点（起降点）
          "points": [ {lat, lon, alt, idx, fly_speed, climb_speed,
                       hover, turn} ... ],   # 除起飞点外的航点，按顺序
          "all": [ ... ],                  # 含起飞点的完整序列
          "params": {...},                 # 飞行参数（已按航点级优先解析）
          "raw": {...},
        }

    LinkGCS2 每个 item 的 ``params`` 是 MAVLink 的 COMMAND_LONG 七元组：
    ``[HoverTime, 0, 0, null, lat, lon, alt]`` —— 坐标在下标 4 / 5。
    起飞点单独存在顶层 ``takeoffLat`` / ``takeoffLon``，不在 items 里。

    ★ **参数要读「航点级」，不是「全局级」**（这一条踩过坑）：
    顶层有 ``globalSpeed``，每个航点内部也有 ``flySpeed``。用户在界面上点的是
    「**修改所有航点的速度**」—— 改的是每个航点的 ``flySpeed``，而
    ``globalSpeed`` 是全局默认值、**根本不会被那个按钮改动**。
    早期版本只读 ``globalSpeed``，结果用户明明把速度改成 6 了，
    判题还拿着 8 去比他填的 6，判成"错"—— 用户说"读的速度和我在软件里写的不一致"
    就是这个。所以下面一律**航点级优先、全局值兜底**。
    """
    # 同上：坏 JSON 返回 None，别让判题流程炸在数据问题上。
    if isinstance(raw, str):
        try:
            d = json.loads(raw)
        except (ValueError, TypeError):
            return None
    else:
        d = raw or {}
    if not isinstance(d, dict):
        return None

    takeoff = None
    # 起飞点字段名两代都兼容：新版 homePoistionLat/Lon，早期 takeoffLat/Lon
    tlat = _f(d.get("takeoffLat") if d.get("takeoffLat") is not None
              else d.get("homePoistionLat"))
    tlon = _f(d.get("takeoffLon") if d.get("takeoffLon") is not None
              else d.get("homePoistionLon"))
    if tlat is not None and tlon is not None:
        takeoff = (tlat, tlon)

    pts = []
    for i, it in enumerate(d.get("items") or []):
        if not isinstance(it, dict):
            continue
        lat = lon = None
        pr = it.get("params") or []
        if isinstance(pr, (list, tuple)) and len(pr) > 5:
            lat, lon = _f(pr[4]), _f(pr[5])
        # 有些版本直接把坐标写在 item 上
        if lat is None:
            lat = _f(it.get("lat") or it.get("latitude"))
        if lon is None:
            lon = _f(it.get("lon") or it.get("longitude") or it.get("lng"))
        if lat is None or lon is None:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue

        # 停留时间：优先 HoverTime 字段；老版本塞在 params[0] 里
        hover = _f(it.get("HoverTime"))
        if hover is None and isinstance(pr, (list, tuple)) and pr:
            hover = _f(pr[0])

        pts.append({
            "lat": lat, "lon": lon,
            "alt": _f(it.get("Altitude") if it.get("Altitude") is not None
                      else it.get("alt")),
            "idx": i,
            "fly_speed": _f(it.get("flySpeed")),        # 航点水平速度
            "climb_speed": _f(it.get("climbSpeed")),    # 航点垂直/爬升速度
            "hover": hover,                             # 航点停留时间
            "turn": it.get("turningMode"),              # 航点转弯方式
        })

    allp = ([{"lat": takeoff[0], "lon": takeoff[1],
              "alt": _f(d.get("takeoffHeihgt")), "idx": -1, "takeoff": True}]
            if takeoff else []) + pts

    # ---- 参数：航点级优先，全局兜底 ----
    fly = _mode_of(pts, "fly_speed")
    climb = _mode_of(pts, "climb_speed")
    hover = _mode_of(pts, "hover")
    turn = _mode_of(pts, "turn")

    return {
        "name": d.get("name") or "",
        "takeoff": takeoff,
        "takeoff_alt": _f(d.get("takeoffHeihgt")),
        "points": pts,
        "all": allp,
        "params": {
            # 这些字段名是 LinkGCS2 里的原文，别改 —— 改了就取不到值
            "speed": fly if fly is not None else _f(d.get("globalSpeed")),
            "alt": _f(d.get("globalAltitude")),
            "max_climb": (climb if climb is not None
                          else _f(d.get("maxClimb"))),
            "max_down": _f(d.get("maxDown")),
            "cycle": d.get("cylcle"),          # 注意软件里拼错了，少个 c
            "home_alt": _f(d.get("takeoffHeihgt")),
            "hover": hover,                    # 停留时间（每航点）
            "turn": turn,                      # 转弯方式（每航点）
            "global_speed": _f(d.get("globalSpeed")),   # 留档，便于排查
            "start_speed": _f(d.get("flySpeed")),       # 顶层起始速度
        },
        "raw": d,
    }


def load_tasks(db_path=None, key=None, limit=80):
    """读库里**全部**任务（新→旧，最多 ``limit`` 条），结构同 :func:`load_task`。

    ★ v1.9.9 新增 —— 修"自动扫描填的是别道题的数据"：

      根因：在地面站里**切换任务不会写数据库**。库里 rowid 最大的永远是
      "最后点了保存的那条"。用户切到第 1 题、地面站里也把第 1 题的任务
      加载出来了（只是加载、没重新保存），软件按"取最新"读到的却还是
      做上一题时保存的那条航线 → 回填自然全不对。

      现在把整库列出来，由调用方挑"跟当前题起飞点吻合"的那条。
    """
    st_state, st_key = user_station()
    if key is None:
        key = st_key if st_state == "fixed" else None

    db = find_db(db_path, key=key)
    if db is None:
        return []
    prof = station(key) if key else STATIONS["linkgcs2"]
    lay = _detect_layout(db, prof)
    if not lay["table"] or not lay["json"]:
        return []
    con = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
    try:
        t, jc = lay["table"], lay["json"]
        sel = '"%s"' % jc
        if lay["date"]:
            sel += ', "%s"' % lay["date"]
        rows = con.execute(
            'select rowid, %s from "%s" order by rowid desc limit %d'
            % (sel, t, int(limit))).fetchall()
        out = []
        for row in rows:
            ts = _parse_of(prof, row[1])
            if ts is None:
                continue
            ts["rowid"] = row[0]
            ts["create_date"] = row[2] if lay["date"] else ""
            ts["db_path"] = str(db)
            ts["station"] = (prof or STATIONS["linkgcs2"])["key"]
            ts["station_name"] = (prof or STATIONS["linkgcs2"])["name"]
            ts["layout"] = lay
            out.append(ts)
        return out
    finally:
        con.close()


def load_task(rowid=None, db_path=None, key=None):
    """读一条任务。``rowid`` 为 None 时取最新的一条。

    返回 ``parse_mission()`` / ``parse_generic()`` 的统一结构，
    外加 ``rowid`` / ``create_date`` / ``station``；库里没有任务时返回 None。
    """
    st_state, st_key = user_station()
    if key is None:
        # 没指定家时**别默认 LinkGCS2** —— 用户在设置里选了"汉鲲"的话，
        # find_db 会去找汉鲲的库，解析也得跟着用汉鲲那套，
        # 否则会拿 LinkGCS2 的字段名去解汉鲲的 JSON，读出一堆空。
        key = st_key if st_state == "fixed" else None

    db = find_db(db_path, key=key)
    if db is None:
        return None
    # 还认不出是哪家（auto 模式）→ 用"最可能"的那个 profile 去挑表和解析。
    # 优先 LinkGCS2（实测过），它的表名/列名探测规则也最宽松，
    # 对别家的库同样能靠 _detect_layout 的通用规则认出来。
    prof = station(key) if key else STATIONS["linkgcs2"]
    lay = _detect_layout(db, prof)
    if not lay["table"] or not lay["json"]:
        return None
    con = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
    try:
        t, jc = lay["table"], lay["json"]
        if rowid is None:
            r = con.execute("select rowid from \"%s\" "
                            "order by rowid desc limit 1" % t).fetchone()
            if not r:
                return None
            rowid = r[0]
        sel = ['"%s"' % jc]
        if lay["date"]:
            sel.append('"%s"' % lay["date"])
        row = con.execute(
            "select %s from \"%s\" where rowid=?" % (", ".join(sel), t),
            (rowid,)).fetchone()
        if not row:
            return None
        raw = row[0]
        out = _parse_of(prof, raw)
        if out is None:
            return None
        out["rowid"] = rowid
        out["create_date"] = row[1] if lay["date"] else ""
        out["db_path"] = str(db)
        out["station"] = (prof or STATIONS["linkgcs2"])["key"]
        out["station_name"] = (prof or STATIONS["linkgcs2"])["name"]
        out["layout"] = lay       # 界面要如实说明"把哪张表当成了任务表"
        return out
    finally:
        con.close()


# ------------------------------------------------------- 反查题目

# 起飞点坐标在题库里的匹配容差（度）。0.0005° ≈ 55m ——
# 题面给的是 4 位小数（约 11m 精度），画图时又可能手抖几个像素，
# 太严会漏，太松会认错题。55m 足够区分不同题（真题起飞点间距远大于此）。
BASE_TOL_M = 60.0


def find_question(items, takeoff, extra_tol=1e9):
    """按起飞点坐标在题库里反查题目。

    返回 ``[(距离米, 题目dict), ...]`` 已按距离升序。超出 ``extra_tol`` 的丢掉。
    """
    out = []
    if not takeoff:
        return out
    for q in items:
        txt = "%s %s" % (q.get("conditions_text") or "",
                         q.get("requirements_text") or "")
        _role, blat, blon = _AN_extract_base(txt)
        if blat is None or blon is None:
            continue
        d = geo.distance_of(blat, blon, takeoff[0], takeoff[1])
        if d is None:
            continue
        if d <= extra_tol:
            out.append((d, q))
    out.sort(key=lambda x: x[0])
    return out


def pick_question(items, task, tol=BASE_TOL_M):
    """在容差内挑出题目。

    返回 ``(题目或None, 候选列表, 说明文本)``。多个候选取第一个，
    但会把全部候选报出来 —— 真题里同一起飞点常对应好几道题
    （第 194/283/284 题就是），靠条件描述区分，得让用户自己确认。
    """
    cand = find_question(items, task.get("takeoff"))
    near = [(d, q) for d, q in cand if d <= tol]
    if not near:
        if cand:
            d, q = cand[0]
            return None, cand, ("题库里没找到起飞点吻合的题，"
                                "最接近的是第 %s 题（差 %.0f 米）"
                                % (q.get("id"), d))
        return None, cand, "题库里没有任何一道题的起飞点坐标可解析。"

    q = near[0][1]
    if len(near) == 1:
        return q, cand, "起飞点吻合：第 %s 题（差 %.0f 米）" % (q.get("id"), near[0][0])
    ids = "、".join("第%s题" % x[1].get("id") for x in near[:5])
    return q, cand, ("起飞点吻合的有 %d 道（%s），已按顺序取第 %s 题 —— "
                     "如果不是这道，用上一题/下一题切过去再判一次"
                     % (len(near), ids, q.get("id")))


# ------------------------------------------------------- 作答提取

def _nearest_pt(std_pt, gcs_pts, used=None):
    """在 GCS 航点里找离标准点最近的一个。

    返回 ``(航点, 距离米, 下标)``。``used`` 是已占用的下标集合 ——
    同一个航点不该同时充当两个标准点的答案（会虚高判分）。
    """
    best, bd, bi = None, None, None
    for i, gp in enumerate(gcs_pts):
        if used and i in used:
            continue
        d = geo.distance_of(std_pt[0], std_pt[1], gp["lat"], gp["lon"])
        if d is None:
            continue
        if bd is None or d < bd:
            best, bd, bi = gp, d, i
    return best, bd, bi


def _shifted_task_point_index(name):
    """返回启用避让点规则后的航点列表下标；不适用时返回 None。"""
    normalized = re.sub(r"\s+", "", str(name or ""))
    if normalized in {"避让点", "过渡点", "过度点", "转场点", "中转点",
                      "会让点", "过分点", "临时会让点", "绕行点", "A", "A点"}:
        return 0
    match = re.fullmatch(r"(?:题卡)?(?:图形)?(\d+)(?:号)?点", normalized)
    if match:
        number = int(match.group(1))
        if number > 0:
            # 航点列表下标从 0 开始，图形第 N 点顺延到第 N+1 个航点。
            return number
    return None


def align_points(ans, task, max_dist=400.0, q=None):
    """把标准答案关键点与地面站航点对齐。

    普通题按空间最近邻匹配；题干涉及避让点或空域限制时按航点编号匹配，
    首个航点作为避让点，图形 1 号点及后续点依次顺延。
    """
    std = ans.get("points") or {}
    gcs = task.get("points") or []
    used = set()
    user, details = {}, []
    import qtag as QT
    shift_point_numbers = QT.requires_avoidance_point_shift(q or {})

    # 起飞点单独处理，它来自地面站首页，不在任务航点列表中。
    if "起飞点" in std and task.get("takeoff"):
        tk = task["takeoff"]
        user["起飞点"] = (tk[0], tk[1])
        details.append({
            "name": "起飞点", "matched": True,
            "dist": geo.distance_of(std["起飞点"]["lat"], std["起飞点"]["lon"],
                                    tk[0], tk[1]),
            "src_idx": "起飞点(软件首页填写)",
            "lat": tk[0], "lon": tk[1], "alt": task.get("takeoff_alt"),
        })

    order = [name for name in std if name != "起飞点"]
    matched_coordinates = {}
    for name in order:
        point = std[name]
        coordinate = (point["lat"], point["lon"])
        route_index = (_shifted_task_point_index(name)
                       if shift_point_numbers else None)
        if route_index is not None:
            # 题目规定的编号优先于最近距离，避免避让点与图形点互换。
            i = route_index
            if i < len(gcs) and i not in used:
                gp = gcs[i]
                distance = geo.distance_of(point["lat"], point["lon"],
                                           gp["lat"], gp["lon"])
            else:
                gp, distance = None, None
        elif coordinate in matched_coordinates:
            gp, distance, i = matched_coordinates[coordinate]
        else:
            gp, distance, i = _nearest_pt(coordinate, gcs, used)

        if gp is None or distance is None or distance > max_dist:
            details.append({
                "name": name, "matched": False, "dist": distance,
                "src_idx": None,
                "expected_src_idx": route_index + 1 if route_index is not None else None,
            })
            continue
        used.add(i)
        if route_index is None:
            matched_coordinates[coordinate] = (gp, distance, i)
        user[name] = (gp["lat"], gp["lon"])
        details.append({
            "name": name, "matched": True, "dist": distance,
            # 面向用户的航点序号从 1 开始，内部列表下标从 0 开始。
            "src_idx": i + 1,
            "lat": gp["lat"], "lon": gp["lon"], "alt": gp.get("alt"),
        })
    return user, details

def align_params(ans, task):
    """把 GCS 任务里的参数映射到题面变量。

    题面用 a/b/c/d/e/f 字母，GCS 用 globalSpeed / maxClimb 这种语义名，
    中间没有通用对应表。这里按**参数含义**去猜对应关系，猜得到才填：

    - 含"速度""爬升""下降"的 → 对 ``speed`` / ``max_climb`` / ``max_down``
      （这三个值本身已在 ``parse_mission`` 里从**每航点字段**取众数，
      不是顶层的 ``globalSpeed``，见那里的注释）
    - 含"高度"且是某个航点号的（"123航点相对地面高度"）→ 取对应航点的 ``Altitude``
    - 含"边长""长度"的 → 取相邻航点间距（同航线里出现最多的那个长度）
    - 含"停留"的 → 每航点的 ``HoverTime``

    猜不出来的就 **不猜** —— 宁可让用户手填，也别填个错的数字
    让他以为"软件判错了"。
    """
    P = task.get("params") or {}
    out = {}
    pts = task.get("points") or []

    # 相邻航点间距（含返场那一段），供边长用
    segs = []
    for a, b in zip(pts, pts[1:]):
        d = geo.distance_of(a["lat"], a["lon"], b["lat"], b["lon"])
        if d:
            segs.append(d)

    def _alt_of(mean):
        """按题面写的航点号取高度。

        **坑点**：题面写「123航点相对地面高度」，这里的 123 不是第 123 号航点，
        而是「1、2、3 号航点」的连写 —— 直接 findall 出 '123' 会去找第 123 个点，
        必然越界返回 None。所以多位数要**逐位拆开**当日号用。
        号是 1-based，items 是 0-based。
        """
        raw = re.findall(r"\d+", mean)
        nums = []
        for r in raw:
            if len(r) == 1:
                nums.append(int(r))
            else:
                # 多位数：优先整体解释（真有个"12号航点"这种），
                # 越界就逐位拆（123 → 1,2,3）
                nums.append(int(r))
                nums.extend(int(c) for c in r)
        vals = []
        for n in nums:
            i = n - 1
            if 0 <= i < len(pts) and pts[i].get("alt") is not None:
                vals.append(pts[i]["alt"])
        if not vals:
            return None
        # 同一档有几个就取众数（避免边界航点号取到不同高度）
        return Counter(vals).most_common(1)[0][0]

    from collections import Counter

    for p in ans.get("params") or []:
        k = p.get("key")
        mean = p.get("meaning") or ""
        if k is None:
            continue
        if "速度" in mean and ("垂直" in mean or "爬升" in mean):
            if P.get("max_climb") is not None:
                out[k] = P["max_climb"]
        elif "下降" in mean:
            if P.get("max_down") is not None:
                out[k] = P["max_down"]
        elif "速度" in mean:
            if P.get("speed") is not None:
                out[k] = P["speed"]
        elif "高度" in mean:
            v = _alt_of(mean)
            if v is None and mean == "航线高度":
                # 统一航线高度从飞行航点取值，排除与已知起飞点重合的地面节点。
                home = (ans.get("points") or {}).get("起飞点")
                heights = [p["alt"] for p in pts if p.get("alt") is not None and
                           (not home or geo.distance_of(home["lat"], home["lon"],
                                                       p["lat"], p["lon"]) > 10)]
                if heights:
                    v = Counter(heights).most_common(1)[0][0]
            if v is not None:
                out[k] = v
        elif "边" in mean or "长度" in mean or "距离" in mean:
            if segs:
                # 边长取出现次数最多的那一档（图形题里各边通常等长）
                c = Counter(round(s, 1) for s in segs)
                out[k] = c.most_common(1)[0][0]
        elif "停留" in mean:
            # 每航点的 HoverTime。早期版本以为软件没存这项、直接跳过，
            # 其实塞在 item 里（新版是 HoverTime 字段，老版在 params[0]）。
            if P.get("hover") is not None:
                out[k] = P["hover"]

    return out


# ------------------------------------------------------- 一键判题

# LinkGCS2 的 turningMode 取值 → 题面用词。
# 0 = 停止转弯（到点停再转），1 = 协调转弯（边飞边转，画圆弧）。
# 依据：MAV_CMD_NAV_WAYPOINT 的 param4（yaw）在 QGC 系里用 0/1 表示
# 直角/圆弧转弯；实测用户"停止转弯"的任务存的就是 0。
TURN_WORDS = {0: "停止转弯", 1: "协调转弯"}


def align_extras(ans, task):
    """把 GCS 任务的非数值项映射到题面的文字要求。

    三项都能读到了（早期版本只认循环次数，另两项被误判成"软件没存"）：

    - **循环次数** ← ``cylcle``（软件里拼错了，少个 c）
    - **转弯方式** ← 每航点的 ``turningMode``（0=停止转弯，1=协调转弯）
    - **停留时间** ← 每航点的 ``HoverTime``（老版本塞在 ``params[0]``）

    **坑点**：题面写的往往是定性描述（"循环执行" / "不循环"、"停止转弯"），
    而 GCS 存的是数值。直接拿字符串比 ``"循环执行" == "2"`` 必然判错，
    所以这里要**反向翻译**：把数值翻回题面用的那套词。
    """
    P = task.get("params") or {}
    want = ans.get("extras") or {}
    out = {}

    if "循环次数" in want:
        c = P.get("cycle")
        if c is not None and str(c).strip() != "":
            txt = str(c).strip()
            try:
                n = float(txt)
            except ValueError:
                n = None
            # 题面用词表：能翻成题面用词就翻，翻不了就原样给数值
            if n is not None and want["循环次数"] in ("循环执行", "不循环"):
                out["循环次数"] = "循环执行" if n >= 2 else "不循环"
            else:
                out["循环次数"] = txt

    if "转弯方式" in want:
        t = P.get("turn")
        if t is not None:
            try:
                t = int(float(t))
            except (TypeError, ValueError):
                t = None
            if t in TURN_WORDS:
                out["转弯方式"] = TURN_WORDS[t]

    # 停留时间：题面抽出来的是「原文」（"35s" / "f" 这种），
    # 只在与建议值对得上时才填 —— 由 judge 走参数通道比对更准，
    # 这里不回填 extras，避免把原文和数值混在一起比。
    return out


def judge_task(q, task, ans=None, max_dist=400.0):
    """把「LinkGCS2 里画好的任务」当作答，判这道题。

    返回 ``(judge的result, meta)``。``meta`` 里带对齐明细，
    界面用它说明"你的哪个航点被当成了哪个标准点"。
    """
    import answers as AN
    import judge as JG
    ans = ans or AN.derive_for_grading(q)
    user_pts, details = align_points(ans, task, max_dist=max_dist, q=q)
    user_pm = align_params(ans, task)
    user_ex = align_extras(ans, task)
    res = JG.judge(q, user_pts, user_pm, user_ex, ans)

    # GCS 模式下「读不到」≠「填错了」：Mission.json 里就没有停留时间这类字段，
    # 标成红叉会让人觉得是自己做错了。所以把读不到的项从统计里摘掉，
    # 重算 ok / n_total，界面用中性标记单独提示。
    def _missing(y):
        return str(y or "") in ("未填", "未作答", "未选", "")

    skipped = []
    for key in ("points", "params", "extras"):
        keep = []
        for it in res.get(key) or []:
            if not it.get("ok") and _missing(it.get("your")):
                it["skipped"] = True
                skipped.append(it.get("name") or it.get("meaning")
                               or it.get("key"))
                continue
            keep.append(it)
        res[key] = keep

    res["n_total"] = sum(len(res.get(k) or [])
                         for k in ("points", "params", "extras"))
    res["n_ok"] = sum(1 for k in ("points", "params", "extras")
                      for it in (res.get(k) or []) if it.get("ok"))
    res["ok"] = res["n_total"] > 0 and res["n_ok"] == res["n_total"]
    res["skipped"] = skipped
    res["source"] = "linkgcs"
    res["gcs_rowid"] = task.get("rowid")
    res["gcs_name"] = task.get("name")
    res["align"] = details
    # ★ 回填作答栏要用的**原始读值**。为什么不在界面层从 align 里再抠一遍：
    #   align 的 details 只为"讲清对齐关系"服务，参数和文字项根本没进去
    #   （它们不在 points 里）。与其在界面里重新算一遍、算错了还难查，
    #   不如在判题这一层就原样带出来 —— 界面上看到的数 = 判题用的数，
    #   这个等式一旦破了，用户就会觉得"软件读的和判的不一样"。
    res["_gcs_user_points"] = user_pts
    res["_gcs_user_params"] = user_pm
    res["_gcs_user_extras"] = user_ex
    return res, {"details": details, "n_matched": len(user_pts),
                 "skipped": skipped,
                 "points": user_pts, "params": user_pm, "extras": user_ex}


# ------------------------------------------------------- 延迟导入 answers
# find_question 要用 answers.extract_base，但 answers 会 import geo。
# 走薄包装是为了避免模块级循环 import 的风险（answers 反过来也可能用到这里）。

def _AN_extract_base(txt):
    import answers as AN
    return AN.extract_base(txt)
