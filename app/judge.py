"""AI 判题引擎 —— 把用户作答和推导出的标准答案做几何比对，并讲清错在哪。

判定分三类，UI 逐项渲染：

1. **关键点坐标**（起飞点 / 避让点 / 过渡点 / 图形 1 号点）—— 硬判定
2. **飞行参数**（边长、高度、速度、停留时间）—— 与题面"建议值"对照，容差较宽
3. **文字项**（转弯方式、循环次数）—— 精确匹配

比"对/错"更重要的是**错因**：用户填错往往不是手滑，而是把「南偏西」
算成了 150°、把 NW 当成东北、或者主宾看反。这里按偏差角度做模式识别，
直接点出最可能的原因。

输出结构（``judge()`` 的返回值）::

    {
      "judgeable": bool,          # 本题能不能自动判分
      "reason": str,              # 不能判分时的说明
      "ok": bool,                 # 全部通过
      "points": [ {name, ok, your, want, why, ref, diff, hint}, ... ],
      "params": [ {key, meaning, ok, your, want, unit, hint}, ... ],
      "extras": [ {name, ok, your, want}, ... ],
      "n_ok": int, "n_total": int,
    }
"""

from __future__ import annotations

import geo
import answers as AN

# ------------------------------------------------------- 容差

TOL_DIST = 10.0          # 坐标点：距离误差阈值（米）
TOL_ANG = 3.0            # 坐标点：方位角误差阈值（度）
TOL_NEAR = 40.0          # 超过它就别说"接近了"


def _param_tol(unit):
    """参数容差 (绝对, 相对)。题面写的是"建议值"，不必卡太死。"""
    if unit == "m/s":
        return 0.5, 0.10
    if unit == "秒":
        return 2.0, 0.15
    return 10.0, 0.10     # 米


# ------------------------------------------------------- 错因诊断

def _bearing_hint(diff, want_b, got_b):
    """按偏差角度猜最常见的错误原因。"""
    if diff is None or diff <= TOL_ANG:
        return ""
    if abs(diff - 180) < 20:
        return ("方向正好反了 180° —— 检查「A 位于 B 的 X 方向」里 A、B 有没有看反，"
                "或者把「偏西」当成了「偏东」")
    if abs(diff - 90) < 12:
        return ("差了 90° —— 多半是方位缩写认错："
                "NE=东北=45°、NW=西北=315°、SE=东南=135°、SW=西南=225°")
    if abs(diff - 60) < 12:
        return ("差了 60° —— 常见原因是「X 偏 Y」的偏向写反："
                "南偏西 30° 是 180°+30°=210°，不是 180°−30°=150°")
    if diff < TOL_NEAR:
        return "方位角差了 %.1f°（你 %s，应为 %s）" % (
            diff, geo.fmt_bearing(got_b), geo.fmt_bearing(want_b))
    return "方位角差了 %.0f°（你 %s，应为 %s）" % (
        diff, geo.fmt_bearing(got_b), geo.fmt_bearing(want_b))


def _diagnose_point(name, want, got, ref_name, ref_pt, meta):
    """生成一个点的错因说明。返回 (diff_text, hint)。"""
    d = geo.distance_of(want[0], want[1], got[0], got[1])
    diff_text = "坐标偏差 %.0f 米" % d

    if ref_name is None or ref_pt is None:
        return diff_text, "这个点的坐标题面直接给出，直接照抄即可"

    wb = geo.bearing_of(ref_pt[0], ref_pt[1], want[0], want[1])
    wd = geo.distance_of(ref_pt[0], ref_pt[1], want[0], want[1])
    gb = geo.bearing_of(ref_pt[0], ref_pt[1], got[0], got[1])
    gd = geo.distance_of(ref_pt[0], ref_pt[1], got[0], got[1])

    db = geo.ang_diff(wb, gb)
    dd = gd - wd

    parts = []
    if db is not None and db > TOL_ANG:
        h = _bearing_hint(db, wb, gb)
        if h:
            parts.append(h)
    if abs(dd) > TOL_DIST:
        parts.append("距离差了 %.0f 米（你 %g 米，应为 %g 米）"
                     % (dd, round(gd), round(wd)))
    if not parts:
        parts.append("几何关系对上了，但坐标偏差 %.0f 米 —— 检查一下舍入精度" % d)
    return diff_text, "；".join(parts)


# ------------------------------------------------------- 出题（界面渲染用）

def plan(q, ans=None):
    """列出这道题需要作答的字段，给界面生成输入框。"""
    ans = ans or AN.derive_for_grading(q)
    return {
        "conf": ans["conf"],
        "points": list(ans["points"].keys()),
        "params": [{"key": p["key"], "meaning": p["meaning"],
                    "unit": p["unit"], "ref": p["value"]}
                   for p in ans["params"]],
        "extras": sorted(ans["extras"].keys() - {"停留时间原文"}),
    }


# ------------------------------------------------------- 主入口

def judge(q, user_points=None, user_params=None, user_extras=None, ans=None):
    """判一道题。

    - ``user_points``  ``{角色: (lat, lon)}``，缺的项别放进来
    - ``user_params``  ``{变量key: 数值}``
    - ``user_extras``  ``{名称: 文本}``
    """
    ans = ans or AN.derive_for_grading(q)
    user_points = user_points or {}
    user_params = user_params or {}
    user_extras = user_extras or {}

    out = {
        "judgeable": ans["conf"] != "none",
        "reason": "",
        "ok": False,
        "points": [],
        "params": [],
        "extras": [],
        "n_ok": 0,
        "n_total": 0,
    }

    out["partial_grading"] = bool(ans.get("partial_grading"))
    if not ans["points"] and not ans["params"] and not (ans["extras"].keys() - {"停留时间原文"}):
        out["judgeable"] = False
        out["reason"] = "未能从本题提取有效坐标、参数或转弯/循环要求，请检查识别文本或生成 API 参考答案。"
        return out

    pts = ans["points"]

    # ---- 关键点 ----
    for name, p in pts.items():
        want = (p["lat"], p["lon"])
        got = user_points.get(name)
        ref_name = p.get("ref")
        ref_pt = None
        if ref_name and ref_name in pts:
            ref_pt = (pts[ref_name]["lat"], pts[ref_name]["lon"])

        if got is None or got[0] is None or got[1] is None:
            out["points"].append({
                "name": name, "ok": False, "your": "未填写",
                "want": geo.fmt_latlon(*want), "why": p.get("why") or "",
                "ref": ref_name, "diff": "未作答",
                "hint": "这一项还没填",
            })
            out["n_total"] += 1
            continue

        d = geo.distance_of(want[0], want[1], got[0], got[1])
        ok = d is not None and d <= TOL_DIST
        diff_text, hint = (("", "") if ok else
                           _diagnose_point(name, want, got, ref_name, ref_pt, p))
        if ok:
            out["n_ok"] += 1
        out["points"].append({
            "name": name, "ok": ok,
            "your": geo.fmt_latlon(got[0], got[1]),
            "want": geo.fmt_latlon(*want),
            "why": p.get("why") or "",
            "ref": ref_name,
            "diff": ("偏差 %.1f 米" % d) if d is not None else "",
            "hint": hint,
        })
        out["n_total"] += 1

    # ---- 参数 ----
    for p in ans["params"]:
        key = p["key"]
        want = p["value"]
        unit = p["unit"]
        got = user_params.get(key)
        label = (p["meaning"] or "变量 %s" % key.upper())
        if got is None:
            out["params"].append({
                "key": key, "meaning": label, "ok": False, "unit": unit,
                "your": "未填", "want": ("%g" % want), "hint": "这一项还没填",
            })
            out["n_total"] += 1
            continue
        a_tol, r_tol = _param_tol(unit)
        tol = max(a_tol, abs(want) * r_tol)
        ok = abs(float(got) - want) <= tol
        hint = ""
        if not ok:
            hint = "%s 这里应为 %g%s（你的 %g%s 差了 %g）" % (
                label, want, unit, float(got), unit, float(got) - want)
        else:
            out["n_ok"] += 1
        out["params"].append({
            "key": key, "meaning": label, "ok": ok, "unit": unit,
            "your": "%g" % float(got), "want": "%g" % want, "hint": hint,
        })
        out["n_total"] += 1

    # ---- 文字项 ----
    for name in sorted(ans["extras"].keys() - {"停留时间原文"}):
        want = str(ans["extras"][name])
        got = user_extras.get(name)
        if got is None:
            out["extras"].append({"name": name, "ok": False,
                                  "your": "未选", "want": want})
            out["n_total"] += 1
            continue
        ok = str(got).strip() == want.strip()
        if ok:
            out["n_ok"] += 1
        out["extras"].append({"name": name, "ok": ok,
                              "your": str(got), "want": want})
        out["n_total"] += 1

    out["ok"] = out["n_total"] > 0 and out["n_ok"] == out["n_total"]
    if out["n_total"] == 0:
        out["judgeable"] = False
        out["reason"] = "这道题没有可作答的字段。"
    return out


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
    q = items[0]

    print("=== 正确答案提交 ===")
    ans = AN.derive_for_grading(q)
    pts = {n: (p["lat"], p["lon"]) for n, p in ans["points"].items()}
    ps = {p["key"]: p["value"] for p in ans["params"]}
    ex = {k: v for k, v in ans["extras"].items() if k != "停留时间原文"}
    r = judge(q, pts, ps, ex, ans)
    print("ok=%s  %d/%d" % (r["ok"], r["n_ok"], r["n_total"]))
    for it in r["points"]:
        print("  [%s] %s  you=%s want=%s  %s" % (
            "√" if it["ok"] else "×", it["name"], it["your"], it["want"],
            it["hint"]))

    print("\n=== 故意把避让点算成南偏东 30°（错 60°）===")
    bad = dict(pts)
    b = ans["points"].get("避让点")
    if b:
        la, lo = geo.dest_point(pts["起飞点"][0], pts["起飞点"][1], 150, 265)
        bad["避让点"] = (la, lo)
    ps2 = dict(ps)
    if ps2:
        k0 = next(iter(ps2))
        ps2[k0] = ps2[k0] * 2
    r2 = judge(q, bad, ps2, ex, ans)
    print("ok=%s  %d/%d" % (r2["ok"], r2["n_ok"], r2["n_total"]))
    for it in r2["points"]:
        print("  [%s] %s  you=%s want=%s" % (
            "√" if it["ok"] else "×", it["name"], it["your"], it["want"]))
        if it["hint"]:
            print("       错因:", it["hint"])
    for it in r2["params"]:
        if not it["ok"]:
            print("  × %s: %s" % (it["meaning"], it["hint"]))
