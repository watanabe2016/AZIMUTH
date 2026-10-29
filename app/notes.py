# -*- coding: utf-8 -*-
"""每题备忘录 —— 文字笔记 + 截图配图

为什么单独一个模块：
  · 笔记是长文本，混进 config.json（那里面全是设置项）不合适；
    单独放 notes/notes.json 更好维护，也方便整体拷走做备份；
  · 截图是二进制，落到 notes/shots/ 下，json 里只记文件名。

存放位置：程序目录/notes/（绿色版随身走）
  notes.json : {"<题卡编号>": {"text": "…",
                      "shots": [{"file": "<截图文件名>.png",
                                 "t": "2026-09-19 21:00"}]}}
"""
import os
import json
import datetime
import shutil
from storage import writable_directory

from PySide6.QtCore import Qt

from logger import get_logger, app_root

log = get_logger()

_cache = None
MAX_SHOT_W = 2400        # 截图最长边上限：整屏原样塞进去太占地方


def notes_dir():
    original = os.path.join(app_root(), "notes")
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    fallback = os.path.join(base, "CAAC Ground Station", "notes")
    d = writable_directory(original, fallback)
    # 只读安装位置首次回落时，保留已有笔记和配图；不覆盖备用位置已有数据。
    if d != original and not os.path.exists(os.path.join(d, "notes.json")):
        if os.path.isfile(os.path.join(original, "notes.json")):
            try:
                shutil.copytree(original, d, dirs_exist_ok=True)
            except OSError as exc:
                log.warning("旧笔记复制失败：%r", exc)
    return d


def shots_dir():
    d = os.path.join(notes_dir(), "shots")
    os.makedirs(d, exist_ok=True)
    return d


def _path():
    return os.path.join(notes_dir(), "notes.json")


def _load(force=False):
    global _cache
    if _cache is not None and not force:
        return _cache
    d = {}
    p = _path()
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                v = json.load(f)
            if isinstance(v, dict):
                d = v
    except Exception as e:
        log.warning("备忘录读取失败：%r", e)
    _cache = d
    return _cache


def _save():
    """先写临时文件再原子替换 —— 直接覆写时中途出问题会丢掉全部笔记"""
    p = _path()
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_load(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception as e:
        log.warning("备忘录写入失败：%r", e)


def _rec(qid, create=False):
    """取某题的记录；create=False 时不存在就返回 None（不写脏文件）"""
    m = _load()
    k = str(int(qid))
    r = m.get(k)
    if not isinstance(r, dict):
        if not create:
            return None
        r = {"text": "", "shots": []}
        m[k] = r
    if not isinstance(r.get("text"), str):
        r["text"] = ""
    if not isinstance(r.get("shots"), list):
        r["shots"] = []
    return r


# ---------------------------------------------------------------- 文字笔记
def get_text(qid):
    r = _rec(qid)
    return r["text"] if r else ""


def set_text(qid, text):
    text = (text or "").strip()
    r = _rec(qid, create=bool(text))
    if r is None:
        return
    r["text"] = text
    _save()


# ---------------------------------------------------------------- 截图
def shots(qid):
    """返回 [{"file": 文件名, "path": 绝对路径, "t": 时间}]，文件不存在的自动跳过"""
    r = _rec(qid)
    if not r:
        return []
    d = shots_dir()
    out = []
    for s in r["shots"]:
        if not isinstance(s, dict):
            continue
        f = str(s.get("file", "") or "")
        if not f:
            continue
        p = os.path.join(d, f)
        if not os.path.exists(p):
            continue
        out.append({"file": f, "path": p, "t": str(s.get("t", "") or "")})
    return out


def add_shot(qid, pm):
    """把一张 QPixmap 存成该题的截图，返回记录 dict（失败返回 None）"""
    if pm is None or pm.isNull():
        return None
    # 超大图先等比压到上限 —— 框选整屏时原图能到 4K 以上
    if max(pm.width(), pm.height()) > MAX_SHOT_W:
        pm = pm.scaled(MAX_SHOT_W, MAX_SHOT_W,
                       Qt.KeepAspectRatio, Qt.SmoothTransformation)
    now = datetime.datetime.now()
    name = "q%03d_%s.png" % (int(qid), now.strftime("%Y%m%d_%H%M%S_%f")[:-3])
    p = os.path.join(shots_dir(), name)
    try:
        if not pm.save(p, "PNG"):
            log.warning("截图保存失败：%s", p)
            return None
    except Exception as e:
        log.warning("截图保存异常：%r", e)
        return None
    r = _rec(qid, create=True)
    r["shots"].append({"file": name, "t": now.strftime("%Y-%m-%d %H:%M")})
    _save()
    log.info("备忘录截图 #%d -> %s", int(qid), name)
    return {"file": name, "path": p, "t": now.strftime("%Y-%m-%d %H:%M")}


def del_shot(qid, fname):
    """删掉一张截图（先摘记录再删文件，避免记录留着但文件没了）"""
    r = _rec(qid)
    if not r:
        return False
    keep = [s for s in r["shots"]
            if not (isinstance(s, dict) and str(s.get("file", "")) == fname)]
    if len(keep) == len(r["shots"]):
        return False
    r["shots"] = keep
    _save()
    try:
        p = os.path.join(shots_dir(), fname)
        if os.path.exists(p):
            os.remove(p)
    except Exception as e:
        log.warning("截图删除失败：%r", e)
    return True


# ---------------------------------------------------------------- 汇总
def has(qid):
    """该题是否有备忘录（有文字或有截图）"""
    r = _rec(qid)
    if not r:
        return False
    return bool(r["text"].strip()) or len(shots(qid)) > 0


def all_text(qid_list):
    """批量取文字笔记 {qid: text}，列表页打标记时用（只读一次 json）"""
    m = _load()
    out = {}
    for qid in qid_list:
        r = m.get(str(int(qid)))
        if isinstance(r, dict) and str(r.get("text", "")).strip():
            out[int(qid)] = r["text"]
    return out


def has_any(qid_list):
    """批量检查题卡笔记标记，只读取一次缓存并复用配图目录。"""
    records = _load()
    marked = set()
    image_root = None
    for qid in qid_list:
        qid = int(qid)
        record = records.get(str(qid))
        if not isinstance(record, dict):
            continue
        if str(record.get("text", "") or "").strip():
            marked.add(qid)
            continue
        shots_list = record.get("shots")
        if not isinstance(shots_list, list) or not shots_list:
            continue
        if image_root is None:
            image_root = shots_dir()
        for shot in shots_list:
            if not isinstance(shot, dict):
                continue
            name = str(shot.get("file", "") or "")
            if name and os.path.exists(os.path.join(image_root, name)):
                marked.add(qid)
                break
    return marked


def revision():
    """返回笔记文件的轻量版本号，供页面切换时判断是否需要重建列表。"""
    try:
        stat = os.stat(_path())
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return 0, 0


def stat():
    """(有笔记的题数, 截图总数)"""
    m = _load()
    n_note = 0
    n_shot = 0
    for v in m.values():
        if not isinstance(v, dict):
            continue
        t = v.get("text") or ""
        s = v.get("shots") if isinstance(v.get("shots"), list) else []
        if str(t).strip() or s:
            n_note += 1
        n_shot += len(s)
    return n_note, n_shot


def clear():
    """清空全部备忘录（文字 + 截图文件）"""
    global _cache
    _cache = {}
    _save()
    d = shots_dir()
    for f in os.listdir(d):
        try:
            os.remove(os.path.join(d, f))
        except Exception:
            pass
    log.info("备忘录已清空")
