# -*- coding: utf-8 -*-
"""做题记录 —— 区分「已做 / 未做」，并保留练习历史。

为什么要单独一个模块：
  · 记录散落在题库页 / 题卡工作台 / 设置页三处，集中起来才不会各写各的；
  · config 的默认值里 list/dict 是可变对象，直接 append 会污染 DEFAULTS，
    所以这里统一「取出来拷贝一份 → 改 → 整个写回去」。

存储落在 config.json：
  done_ids : [3, 7, 12]                        已做题目 id（用于快速判断）
  history  : {"3": {"count": 2, "last": "..."}} 做题次数与最后时间
  wrong    : {"9": {"n": 2, "last": "...", "detail": "..."}} 错题集
  trend    : {"2026-09-20": {"ok": 5, "bad": 2, "qs": [3, 7, 12]}} 按日成绩
"""
import datetime

import config as cfg


def _today():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def _done_ids():
    """取已做 id 列表的副本（绝不原地改 DEFAULTS 里的那个 list）"""
    v = cfg.get("done_ids", []) or []
    return [int(x) for x in v if isinstance(x, (int, float, str)) and str(x).isdigit()]


def _history():
    v = cfg.get("history", {}) or {}
    return dict(v) if isinstance(v, dict) else {}


def is_done(qid):
    return int(qid) in _done_ids()


def info(qid):
    """某题的记录：{"count": 次数, "last": 最后时间}，没做过返回空"""
    h = _history().get(str(int(qid)))
    if not isinstance(h, dict):
        return {}
    return {"count": int(h.get("count", 0) or 0),
            "last": str(h.get("last", "") or "")}


def mark(qid, count=1):
    """标记为已做。count>1 时累加次数（打开一次算一次）。"""
    qid = int(qid)
    ids = _done_ids()
    if qid not in ids:
        ids.append(qid)
    h = _history()
    rec = h.get(str(qid))
    if not isinstance(rec, dict):
        rec = {"count": 0, "last": ""}
    rec["count"] = int(rec.get("count", 0) or 0) + max(1, int(count))
    rec["last"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    h[str(qid)] = rec
    cfg.set_("done_ids", ids)
    cfg.set_("history", h)


def unmark(qid):
    """取消已做标记（历史次数保留，避免误操作丢数据）"""
    qid = int(qid)
    ids = [i for i in _done_ids() if i != qid]
    cfg.set_("done_ids", ids)


def toggle(qid):
    if is_done(qid):
        unmark(qid)
        return False
    mark(qid)
    return True


def done_count():
    return len(_done_ids())


def clear():
    """清空全部做题记录（**错题集与成绩趋势一并清掉**，它们同属一份练习账本）"""
    cfg.set_("done_ids", [])
    cfg.set_("history", {})
    cfg.set_("wrong", {})
    cfg.set_("trend", {})


def recent(n=8):
    """最近做过的 n 题：[(qid, {"count":..,"last":..}), ...]"""
    h = _history()
    rows = []
    for k, v in h.items():
        if not str(k).isdigit() or not isinstance(v, dict):
            continue
        rows.append((int(k), {"count": int(v.get("count", 0) or 0),
                              "last": str(v.get("last", "") or "")}))
    rows.sort(key=lambda r: r[1]["last"], reverse=True)
    return rows[:n]


# ==================================================== 错题集（判题结果记账）

def _wrong():
    """错题表：{"<id>": {"n": 次数, "last": 时间, "detail": "错在哪"}}"""
    v = cfg.get("wrong", {}) or {}
    return dict(v) if isinstance(v, dict) else {}


def _write_wrong(w):
    cfg.set_("wrong", w)


def mark_judge(qid, ok, src=None, detail=""):
    """判题结果记账 —— 这是错题集的**唯一写入口**。

    · ok=True   通过：把这题标为已做（顺带累加练习次数），并从错题集里**移除**
      —— 改对了还挂在错题集里，用户下次复习时会一脸问号。
    · ok=False  不通过：**不标已做**（他没做出来），但收进错题集，记下错在哪。

    ``src`` 只用于日志与"在哪判的"回溯（``linkgcs`` / ``manual``）；
    ``detail`` 是错项摘要，复习时直接显示，省得再进去判一遍。
    """
    qid = int(qid)
    w = _wrong()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    # 成绩趋势逐日落一笔 —— 放在这里是因为 mark_judge 是判题结果的唯一漏斗，
    # 挂别处迟早会漏记（比如以后再加一个判题入口）。
    note_trend(ok, qid)
    if ok:
        w.pop(str(qid), None)
        _write_wrong(w)
        mark(qid)
        return True
    rec = w.get(str(qid))
    if not isinstance(rec, dict):
        rec = {"n": 0, "last": "", "detail": ""}
    rec["n"] = int(rec.get("n", 0) or 0) + 1
    rec["last"] = now
    if detail:
        rec["detail"] = str(detail)
    if src:
        rec["src"] = str(src)
    w[str(qid)] = rec
    _write_wrong(w)
    return False


def is_wrong(qid):
    return str(int(qid)) in _wrong()


def wrong_info(qid):
    """某题的错题记录：{"n": 错过几次, "last": 最后时间, "detail": 错项摘要}"""
    r = _wrong().get(str(int(qid)))
    if not isinstance(r, dict):
        return {}
    return {"n": int(r.get("n", 0) or 0),
            "last": str(r.get("last", "") or ""),
            "detail": str(r.get("detail", "") or "")}


def wrong_count():
    return len(_wrong())


def wrong_ids():
    """错题 id 列表，**错得多的排在前面**（先复习老问题）。

    主序：错过次数倒序；
    次序：最后一次错的时间倒序（同次数的，最近错的先看）。

    坑：`list.sort(key=...)` 只有升序，而时间字符串直接升序会把「更早」
    排在前面，跟"最近先看"正好反了。两次稳定排序解决 —— 先按时间倒序，
    再按次数倒序（后一次会保住前一次在次数相同时的相对顺序）。
    """
    rows = []
    for k, v in _wrong().items():
        if not str(k).isdigit():
            continue
        n = int(v.get("n", 0) or 0) if isinstance(v, dict) else 0
        last = str(v.get("last", "") or "") if isinstance(v, dict) else ""
        rows.append((n, last, int(k)))
    rows.sort(key=lambda r: r[1], reverse=True)    # 时间倒序
    rows.sort(key=lambda r: r[0], reverse=True)    # 次数倒序（稳定，保住上面）
    return [r[2] for r in rows]


def wrong_clear():
    """清空错题集（已做标记与练习历史不动）"""
    _write_wrong({})


def wrong_recent(n=6):
    """最近错的 n 题：[(qid, {n,last,detail}), ...]，新的在前。

    题库页「错题集」弹窗靠它列清单（bank_page.show_wrong_set）。
    """
    rows = []
    for k, v in _wrong().items():
        if not str(k).isdigit() or not isinstance(v, dict):
            continue
        rows.append((int(k), {"n": int(v.get("n", 0) or 0),
                              "last": str(v.get("last", "") or ""),
                              "detail": str(v.get("detail", "") or "")}))
    rows.sort(key=lambda r: r[1]["last"], reverse=True)
    return rows[:n]


# ==================================================== 成绩趋势（按日聚合）

def _trend():
    """成绩趋势表：{"2026-09-20": {"ok": 5, "bad": 2, "qs": [3, 7]}}

    · ok  / bad —— 当天判对 / 判错的**次数**（不是题数，同一题重判也计）
    · qs        —— 当天碰过的题 id（去重），用来算"今天练了几道题"
    """
    v = cfg.get("trend", {}) or {}
    return dict(v) if isinstance(v, dict) else {}


def note_trend(ok, qid=None, day=None):
    """记一笔判题结果到当天。**由 mark_judge 自动调用**，界面别直接调。

    为什么不复用 done_ids / history 现算：
      history 只存"最后时间"和总次数，**没有逐日账** —— 昨天做 10 道、今天做 5 道，
      只能看到总数 15，画不出趋势线。趋势必须逐日单独落一笔。
    """
    d = day or _today()
    t = _trend()
    rec = t.get(d)
    if not isinstance(rec, dict):
        rec = {"ok": 0, "bad": 0, "qs": []}
    if ok:
        rec["ok"] = int(rec.get("ok", 0) or 0) + 1
    else:
        rec["bad"] = int(rec.get("bad", 0) or 0) + 1
    qs = rec.get("qs")
    if not isinstance(qs, list):
        qs = []
    if qid is not None:
        try:
            q = int(qid)
            if q not in qs:
                qs.append(q)
        except (TypeError, ValueError):
            pass
    rec["qs"] = qs[:400]          # 单日封顶，防止异常刷量把 config 撑爆
    t[d] = rec
    cfg.set_("trend", t)


def _days(n):
    """最近 n 天的日期串（含今天），升序 —— 画趋势线用，**没有数据的日子也要有**，
    否则界面上的折线会把"空了一周"压缩掉，看着像天天都在练。"""
    today = datetime.date.today()
    return [(today - datetime.timedelta(days=n - 1 - i)).strftime("%Y-%m-%d")
            for i in range(n)]


def trend_days(n=14, fill=True):
    """按日成绩：[(日期, {ok, bad, total, rate, qs}), ...] 升序（早→晚）。

    ``fill=True`` 把没有记录的日子补成全 0，供折线图使用；
    ``fill=False`` 只返回真有记录的日子（列表/汇总用）。
    rate 是当天正确率（0~100 的整数），当天没判题则为 None。
    """
    t = _trend()
    keys = _days(n) if fill else sorted(t.keys())[-n:]
    rows = []
    for k in keys:
        r = t.get(k)
        if not isinstance(r, dict):
            if not fill:
                continue
            r = {}
        ok = int(r.get("ok", 0) or 0)
        bad = int(r.get("bad", 0) or 0)
        qs = r.get("qs") if isinstance(r.get("qs"), list) else []
        tot = ok + bad
        rows.append((k, {"ok": ok, "bad": bad, "total": tot,
                         "rate": (round(ok * 100.0 / tot) if tot else None),
                         "qs": [int(x) for x in qs
                                if str(x).lstrip("-").isdigit()]}))
    return rows


def trend_summary(n=14):
    """最近 n 天的总览：判题次数 / 对 / 错 / 正确率 / 练过的题数 / 有练习的天数。

    正确率按**判题次数**算（错题重判会重复计入）—— 这反映的是"当下手感"，
    跟"题库覆盖率"是两个不同口径，界面上要说清楚，别让用户误会成"我做对了 X%"。
    """
    rows = trend_days(n, fill=False)
    ok = sum(r["ok"] for _, r in rows)
    bad = sum(r["bad"] for _, r in rows)
    tot = ok + bad
    done = set()
    for _, r in rows:
        done.update(r["qs"])
    active = sum(1 for _, r in rows if r["total"] > 0)
    # 当前连续练习天数（从今天往回数，断一天就停）
    t = _trend()
    streak = 0
    today = datetime.date.today()
    for i in range(400):
        k = (today - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        r = t.get(k) or {}
        if int(r.get("ok", 0) or 0) + int(r.get("bad", 0) or 0) > 0:
            streak += 1
        else:
            break
    return {"ok": ok, "bad": bad, "total": tot, "active_days": active,
            "questions": len(done), "streak": streak,
            "rate": (round(ok * 100.0 / tot) if tot else None)}


def trend_clear():
    """清空成绩趋势（已做标记 / 错题集不动）"""
    cfg.set_("trend", {})

