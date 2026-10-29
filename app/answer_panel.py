"""AI 作答面板 + 判题结果窗口。

- ``AnswerPanel``  题卡工作台右侧的可开合作答栏：按点填经纬度、填参数、提交判题
- ``JudgeDialog``  判题结果弹窗：全对给「✅ 做题正确」，有错逐项列出**错在哪**

为什么把作答栏塞进工作台而不是做成独立窗口：用户是**一边看题卡一边算**的，
题卡和输入框必须同屏。独立窗口会在双屏/单屏之间来回切，做题节奏直接断掉。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                                QLineEdit, QPushButton, QScrollArea, QWidget,
                                QSizePolicy, QComboBox)

import theme as T
import geo
import answers as AN
import judge as JG
import reference_engine as RE
import config as cfg
from workflow_feedback import recovery_advice
from ui_kit import (glass, glass_style, refresh_glass, btn_style, IconButton,
                    Combo, Icons, attach_shadow)
from motion_runtime import motion_page


def _inp_style(focus=False):
    P = T.P
    return (f"QLineEdit{{background:{P['input_bg']};"
            f" border:1px solid {P['input_border']}; border-radius:8px;"
            f" padding:5px 9px; color:{P['ink']}; font-size:12px;"
            f" selection-background-color:{T.C_BLUE};}}"
            f"QLineEdit:focus{{border:1px solid {T.C_BLUE};"
            f" background:{P['input_bg_focus']};}}")


# ================================================================ 作答面板

@motion_page
class AnswerPanel(QFrame):
    """右侧作答栏。"""

    # 判题完成（结果字典）—— 工作台拿它去标"已做"
    judged = Signal(dict)

    W = 380          # 设计宽度（会乘 scale）

    def __init__(self, parent=None, scale=1.0):
        super().__init__(parent)
        self._scale = scale
        self._theme_mode = T.mode
        self.setObjectName("AnsPanel")
        self.setStyleSheet(
            f"QFrame#AnsPanel{{background:{T.P['panel_soft']};"
            f" border:1px solid {T.P['glass_soft_edge']};"
            f" border-radius:14px;}}")
        self.setFixedWidth(int(self.W * scale))

        self.q = None
        self.ans = None
        self.last_result = None
        self._dlg = None         # 判题结果窗口（必须持引用，否则会被 GC 掉）
        self._pt_edits = {}      # {点名: (lat_edit, lon_edit)}
        self._pm_edits = {}      # {变量: edit}
        self._ex_combos = {}     # {名称: Combo}
        self._body = None
        self._build_shell()

    # -------------------------------------------------- 骨架
    def _build_shell(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QWidget()
        head.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(int(14 * self._scale), int(12 * self._scale),
                              int(12 * self._scale), int(8 * self._scale))
        hl.setSpacing(8)
        self.title = QLabel("作答")
        self.title.setStyleSheet(
            f"font-size:14px; font-weight:700; color:{T.P['ink']};"
            f"background:transparent;")
        hl.addWidget(self.title)
        hl.addStretch()
        self.conf_lb = QLabel("")
        self.conf_lb.setStyleSheet(
            f"font-size:11px; color:{T.P['ink3']}; background:transparent;")
        hl.addWidget(self.conf_lb)
        root.addWidget(head)

        # 可滚动的表单区。横向滚动条必须关掉：参数行 / 长错因文案偶尔会超宽，
        # 一旦冒出横向滚动条，底部就被切出一条，视觉上很脏。
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollArea>QWidget>QWidget{background:transparent;}")
        self.scroll = scroll
        root.addWidget(scroll, 1)

        # 底部按钮
        #
        # 三个按钮的分工（v1.9.5 定）：
        #   清空     —— 把这一屏填的东西清掉
        #   自动扫描 —— 去地面站里读画好的航线，填进上面那些框（读不到就只提示）
        #   提交判题 —— 拿框里的值判对错
        # 「自动扫描」紧挨「提交判题」，因为它是"填答案"的一种方式 ——
        # 用户路径：点「判题」开作答栏 → 点「自动扫描」→ 点「提交判题」。
        foot = QWidget()
        foot.setStyleSheet("background:transparent;")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(int(14 * self._scale), int(8 * self._scale),
                              int(14 * self._scale), int(12 * self._scale))
        fl.setSpacing(6)
        self.b_clear = IconButton("清空", None, "default",
                                  scale=self._scale, font_size=12)
        self.b_clear.clicked.connect(self.clear_inputs)
        self.b_scan = IconButton("自动扫描", "search", "default",
                                 scale=self._scale, font_size=12)
        self.b_scan.setToolTip(
            "读地面站（LinkGCS2 / 华科尔 / 汉鲲 / 凌动）里画好的航线，\n"
            "把坐标和参数自动填进上面的输入框，然后判一次对错。\n"
            "读进来的值可以直接改，改完点「提交判题」重判。")
        self.b_scan.clicked.connect(self._on_scan)
        self.b_submit = IconButton("提交判题", "check", "primary",
                                   scale=self._scale, font_size=12)
        self.b_submit.clicked.connect(self.do_submit)
        fl.addWidget(self.b_clear)
        fl.addWidget(self.b_scan)
        fl.addWidget(self.b_submit, 1)
        root.addWidget(foot)

        self._rebuild_body()

    # -------------------------------------------------- 自动扫描
    # 工作台注入：无参回调，去读地面站数据并判当前题。
    # 用回调而不是在这里直接 import linkgcs：作答栏只管"显示与收集"，
    # 读哪份库、认哪道题、怎么记账都是工作台的事。
    scan_cb = None

    def _on_scan(self):
        """「自动扫描」：把活交给工作台（读数据 → 回填 → 判题）。"""
        cb = self.scan_cb
        if cb is None:
            self._show_dialog(errors=["自动扫描没接上（内部错误）。"])
            return
        try:
            cb()
        except Exception as e:
            self._show_dialog(errors=["自动扫描出错：%s" % e])

    def _avoid_pick_changed(self, index):
        """保存人工校正；derive() 与 judge() 读取同一份配置。"""
        if not self.q:
            return
        overrides = cfg.get("avoid_point_overrides", {})
        if not isinstance(overrides, dict):
            overrides = {}
        qid = str(self.q.get("id"))
        value = self.avoid_pick.itemData(index)
        if value == "auto":
            overrides.pop(qid, None)
        else:
            overrides[qid] = str(value)
        cfg.set_("avoid_point_overrides", overrides)
        self.ans = AN.derive_for_grading(self.q)
        self._rebuild_body()

    # -------------------------------------------------- 切题
    def set_question(self, q):
        """换一道题：重推标准答案并重建表单。"""
        self.q = q
        self.ans = AN.derive_for_grading(q) if q else None
        self.title.setText("作答 · 第 %s 题" % q["id"] if q else "作答")
        self._rebuild_body()

    # -------------------------------------------------- 内容
    def _rebuild_body(self):
        """清掉旧内容，按当前题重建表单。"""
        old = self.scroll.takeWidget()
        if old is not None:
            old.setParent(None)
            old.deleteLater()
        self._pt_edits.clear()
        self._pm_edits.clear()
        self._ex_combos.clear()

        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        self.scroll.setWidget(inner)
        v = QVBoxLayout(inner)
        m = int(14 * self._scale)
        v.setContentsMargins(m, int(4 * self._scale), m, int(16 * self._scale))
        v.setSpacing(int(10 * self._scale))

        if self.q is None:
            self.conf_lb.setText("")
            lab = QLabel("打开一道题后，这里可以填答案并自动判分。")
            lab.setWordWrap(True)
            lab.setStyleSheet(
                f"font-size:12px; color:{T.P['ink3']}; background:transparent;")
            v.addWidget(lab)
            v.addStretch()
            return

        ans = self.ans or AN.derive_for_grading(self.q)
        self.ans = ans

        # 图形类型只决定参考模型，不拿示意图推断未知坐标。
        conf = ans["conf"]
        try:
            import logging as _lg
            _lg.getLogger("azimuth").info(
                "答案面板 #%s：conf=%s 点数=%d 来源=%s",
                (self.q or {}).get("id"), conf, len(ans.get("points") or {}),
                ans.get("source"))
        except Exception:
            pass
        shape = ans.get("shape")
        if shape:
            self.conf_lb.setText("图形参考 · %s" % shape["name"])
            self.conf_lb.setToolTip("图形是题型示意；未知坐标仍按题目条件填写。")
            self.conf_lb.setStyleSheet(
                f"font-size:11px; color:{T.P['ink2']}; background:transparent;")
        elif conf == "full":
            self.conf_lb.setText("坐标已推导")
            self.conf_lb.setStyleSheet(
                f"font-size:11px; color:{T.P['ok_fg']}; background:transparent;")
        else:
            self.conf_lb.setText("仅参数对照")
            self.conf_lb.setStyleSheet(
                f"font-size:11px; color:{T.P['ink3']}; background:transparent;")

        # 导入题的云端答案可能尚未生成避让/过渡点推断，answers.derive
        # 会以 None 表示“当前没有依据”；面板显示层统一按空信息处理。
        avoid = ans.get("avoid_point") or {}
        basis = QLabel("避让点依据：" + avoid.get("basis", "题干未提及避让点"))
        basis.setWordWrap(True)
        basis.setStyleSheet(
            f"font-size:11px; color:{T.P['ink2']}; background:transparent;")
        v.addWidget(basis)

        transit = ans.get("transit_point") or {}
        transit_basis = QLabel("过渡点依据：" + transit.get("basis", "题干未提及过渡点"))
        transit_basis.setWordWrap(True)
        transit_basis.setStyleSheet(
            f"font-size:11px; color:{T.P['ink2']}; background:transparent;")
        v.addWidget(transit_basis)
        candidates = sorted({
            int(name[2:name.index("号点")])
            for name in ans["points"]
            if name.startswith("图形") and "号点" in name
            and name[2:name.index("号点")].isdigit()
        })
        pick_row = QHBoxLayout()
        pick_row.addWidget(QLabel("手动指定避让点"))
        self.avoid_pick = QComboBox()
        self.avoid_pick.addItem("自动判定", "auto")
        for number in candidates:
            self.avoid_pick.addItem("图形%d号点" % number, str(number))
        overrides = cfg.get("avoid_point_overrides", {})
        current_override = (overrides.get(str(self.q.get("id")))
                            if isinstance(overrides, dict) else None)
        idx = (self.avoid_pick.findData(str(current_override))
               if current_override is not None else 0)
        self.avoid_pick.setCurrentIndex(max(0, idx))
        self.avoid_pick.setEnabled(bool(candidates))
        self.avoid_pick.setAccessibleName("手动选择避让点编号")
        self.avoid_pick.setToolTip("此选择会用于路线检查和自动判题")
        self.avoid_pick.activated.connect(self._avoid_pick_changed)
        self.avoid_pick.setStyleSheet(
            f"QComboBox{{color:{T.P['ink']};background:{T.P['input_bg']};"
            f"border:1px solid {T.P['input_border']};border-radius:7px;padding:4px 8px;}}")
        pick_row.addWidget(self.avoid_pick, 1)
        v.addLayout(pick_row)
        if not candidates:
            note = QLabel("目前没有已推导坐标的图形点；补齐题目坐标后即可手动指定。")
            note.setWordWrap(True)
            note.setStyleSheet(f"font-size:10px;color:{T.P['ink3']};background:transparent;")
            v.addWidget(note)

        if not ans["points"]:
            tip = QLabel(
                "暂未得到可核验的参考坐标，可能是坐标基准缺失、图形条件不足或识别未完成。"
                "请对照原题卡核对坐标和点位；也可以在“参考答案”窗口校对或录入答案。\n\n"
                "下方参数仍可与题面建议值对照。")
            tip.setWordWrap(True)
            tip.setStyleSheet(
                f"font-size:12px; color:{T.P['warn_fg']};"
                f" background:transparent; line-height:165%;")
            v.addWidget(tip)

        # ---- 坐标点 ----
        # 旧判题器只列出已推导点；补齐题卡编号点，完整作答才有机会
        # 接受独立条件核验并成为参考答案。未填写仍不会被视为正确。
        display_points = dict(ans["points"])
        spec = RE.extract_question_spec(self.q)
        for name in spec.get("nodes") or []:
            display_points.setdefault("图形%s号点" % name, {})
        external = {name for item in spec.get("segment_constraints", [])
                    for name in (item["a"], item["b"])
                    if name in ("H", "A", "T")}
        for node, label in (("H", AN.TAKEOFF), ("A", AN.AVOID),
                            ("T", AN.TRANSIT)):
            if node in external or node in spec.get("coordinates", {}):
                display_points.setdefault(label, {})
        if display_points:
            v.addWidget(self._sec("①  航线关键点坐标"))
            for name, p in display_points.items():
                v.addWidget(self._point_block(name, p))

        # ---- 参数 ----
        if ans["params"]:
            v.addWidget(self._sec("②  飞行参数"))
            for p in ans["params"]:
                v.addWidget(self._param_row(p))

        # ---- 文字项 ----
        ex = [k for k in sorted(ans["extras"].keys()) if k != "停留时间原文"]
        if ex:
            v.addWidget(self._sec("③  其他要求"))
            for name in ex:
                v.addWidget(self._extra_row(name, ans["extras"][name]))

        v.addStretch()

    def _sec(self, text):
        lab = QLabel(text)
        lab.setStyleSheet(
            f"font-size:12px; font-weight:700; color:{T.P['ink2']};"
            f"background:transparent; padding-top:6px;")
        return lab

    def _point_block(self, name, p):
        box = QWidget()
        box.setStyleSheet("background:transparent;")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        top = QWidget()
        top.setStyleSheet("background:transparent;")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(6)
        nm = QLabel(name)
        nm.setStyleSheet(
            f"font-size:12.5px; font-weight:600; color:{T.P['ink']};"
            f"background:transparent;")
        tl.addWidget(nm)
        why = p.get("why") or ""
        if why:
            w = QLabel("·  " + why)
            w.setStyleSheet(
                f"font-size:10.5px; color:{T.P['ink3']};"
                f"background:transparent;")
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            tl.addWidget(w, 1)
        else:
            tl.addStretch()
        v.addWidget(top)

        row = QWidget()
        row.setStyleSheet("background:transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        lat = QLineEdit()
        lat.setPlaceholderText("纬度 N")
        lat.setStyleSheet(_inp_style())
        lat.setMinimumHeight(int(30 * self._scale))
        lon = QLineEdit()
        lon.setPlaceholderText("经度 E")
        lon.setStyleSheet(_inp_style())
        lon.setMinimumHeight(int(30 * self._scale))
        rl.addWidget(lat, 1)
        rl.addWidget(lon, 1)
        v.addWidget(row)

        self._pt_edits[name] = (lat, lon)
        return box

    def _param_row(self, p):
        key = str(p.get("key") or p.get("name") or "value")
        unit = str(p.get("unit") or "")
        label = str(p.get("meaning") or ("变量 %s" % key.upper()))
        row = QWidget()
        row.setStyleSheet("background:transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)

        lb = QLabel(label)
        lb.setWordWrap(True)
        lb.setStyleSheet(
            f"font-size:12px; color:{T.P['ink']}; background:transparent;")
        lb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        rl.addWidget(lb, 1)

        ed = QLineEdit()
        ed.setPlaceholderText("?")
        ed.setStyleSheet(_inp_style())
        ed.setFixedWidth(int(84 * self._scale))
        ed.setMinimumHeight(int(30 * self._scale))
        ed.setAlignment(Qt.AlignRight)
        rl.addWidget(ed)

        un = QLabel(unit or "")
        un.setFixedWidth(int(30 * self._scale))
        un.setStyleSheet(
            f"font-size:11px; color:{T.P['ink3']}; background:transparent;")
        rl.addWidget(un)

        self._pm_edits[key] = ed
        return row

    def _extra_row(self, name, want):
        row = QWidget()
        row.setStyleSheet("background:transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        lb = QLabel(name)
        lb.setStyleSheet(
            f"font-size:12px; color:{T.P['ink']}; background:transparent;")
        rl.addWidget(lb, 1)

        cb = Combo(scale=self._scale)
        if name == "转弯方式":
            for t in ("停止转弯", "协调转弯"):
                cb.addItem(t, t)
        elif name == "循环次数":
            for t in ("循环执行", "不循环", "2", "3", "4", "5"):
                cb.addItem(t, t)
        else:
            cb.addItem(str(want), str(want))
        cb.setFixedWidth(int(118 * self._scale))
        rl.addWidget(cb)
        self._ex_combos[name] = cb
        return row

    # -------------------------------------------------- 收集与提交
    def clear_inputs(self):
        for lat, lon in self._pt_edits.values():
            lat.clear()
            lon.clear()
        for ed in self._pm_edits.values():
            ed.clear()

    # -------------------------------------------------- 从 GCS 回填
    def fill_from(self, res=None, points=None, params=None, extras=None):
        """把「读 LinkGCS2 读到的值」填进各输入框，让用户**看得见、改得动**。

        为什么要回填而不是直接给结论：用户点一次「判题」，如果只是弹一句
        "做题正确"，他没法知道软件**到底读到了什么** —— 万一读错了位置
        （画的是第 1 题、读的是第 2 题），屏幕上显示"错误"他会以为是自己
        算错，显示"正确"他又会以为自己蒙对了。把读到的原始值摊在输入框里，
        "软件读对了没有"和"我算得对不对"就变成两件能分开看的事。

        入参优先用 ``res``（``judge_task`` 的返回），里面带 ``_gcs_user``；
        也可以直接传三个字典，方便单测。

        返回 ``(填了几个点, 填了几个参数, 填了几个文字项)``。
        """
        src = res or {}
        pts = points if points is not None else src.get("_gcs_user_points")
        pm = params if params is not None else src.get("_gcs_user_params")
        exs = extras if extras is not None else src.get("_gcs_user_extras")
        n_pt = n_pm = n_ex = 0

        # ---- 坐标点 ----
        for name, ed in (self._pt_edits or {}).items():
            if not pts or name not in pts:
                continue
            v = pts[name]
            try:
                la, lo = float(v[0]), float(v[1])
            except (TypeError, ValueError, IndexError):
                continue
            # 固定 8 位小数：跟 LinkGCS2 界面上的显示精度一致，
            # 用户拿去和他的软件界面对数才不会因为"少两位"而对不上。
            ed[0].setText("%.8f" % la)
            ed[1].setText("%.8f" % lo)
            n_pt += 1

        # ---- 参数 ----
        for key, ed in (self._pm_edits or {}).items():
            if not pm or key not in pm:
                continue
            val = pm[key]
            if val is None:
                continue
            # 整数就不显示小数点（高度 30 比 30.0 好看，也跟题面一致）
            try:
                f = float(val)
                txt = ("%d" % int(f)) if abs(f - int(f)) < 1e-9 else ("%g" % f)
            except (TypeError, ValueError):
                txt = str(val)
            ed.setText(txt)
            n_pm += 1

        # ---- 文字项 ----
        for name, cb in (self._ex_combos or {}).items():
            if not exs or name not in exs:
                continue
            want = str(exs[name])
            idx = cb.findData(want)
            if idx < 0 and hasattr(cb, "findText"):
                # data 没对上就试显示文本：加项时 data 可能给的是数值
                # （比如循环次数存 2，"循环执行" 只是显示文字）
                idx = cb.findText(want)
            if idx < 0:
                # 读到题面用词表之外的写法（比如循环次数读到 "3" 而
                # 下拉里只有「循环执行/不循环/2..5」）——**补一项进去**，
                # 而不是悄悄丢掉。否则用户看到下拉停在旧值上，
                # 会以为软件没读到这项。
                cb.addItem(want, want)
                idx = cb.count() - 1
            cb.setCurrentIndex(idx)
            n_ex += 1
        return n_pt, n_pm, n_ex

    def has_any_input(self):
        """作答栏里现在有没有内容（判断回填前要不要先清空）。"""
        for lat, lon in (self._pt_edits or {}).values():
            if lat.text().strip() or lon.text().strip():
                return True
        for ed in (self._pm_edits or {}).values():
            if ed.text().strip():
                return True
        return False

    def _collect(self):
        """把界面输入收集成 judge() 要的结构。返回 (points, params, extras, bad)。"""
        pts, pm, ex, bad = {}, {}, {}, []
        for name, (lat_e, lon_e) in self._pt_edits.items():
            lt, gt = lat_e.text().strip(), lon_e.text().strip()
            if not lt and not gt:
                continue                      # 整行空着 = 未作答
            la = geo.parse_angle(lt, "lat")
            lo = geo.parse_angle(gt, "lon")
            if la is None:
                bad.append("%s 的纬度看不懂：%s" % (name, lt or "（空）"))
                continue
            if lo is None:
                bad.append("%s 的经度看不懂：%s" % (name, gt or "（空）"))
                continue
            pts[name] = (la, lo)

        for key, ed in self._pm_edits.items():
            t = ed.text().strip()
            if not t:
                continue
            try:
                pm[key] = float(t)
            except ValueError:
                bad.append("参数 %s 不是数字：%s" % (key.upper(), t))

        for name, cb in self._ex_combos.items():
            ex[name] = cb.currentData()
        return pts, pm, ex, bad

    def do_submit(self):
        if self.q is None:
            return
        pts, pm, ex, bad = self._collect()
        if bad:
            self._show_dialog(errors=bad)
            return
        if not pts and not pm:
            self._show_dialog(errors=["还没有填任何答案——至少填一项再提交。"])
            return
        res = JG.judge(self.q, pts, pm, ex, self.ans)
        res["_submission_points"] = pts
        res["_submission_params"] = pm
        res["_submission_extras"] = ex
        self.last_result = res
        self.judged.emit(res)
        self._show_dialog(res, qid=self.q.get("id"))

    def _show_dialog(self, res=None, qid=None, errors=None):
        """弹判题结果窗口。

        必须把窗口引用挂在 self 上：QWidget 没有父对象时，Python 侧引用一旦
        被回收，底层 C++ 对象会被一起析构 —— 表现就是判题窗口"闪一下就消失"
        （已实测复现）。这是本轮验收里 D8/D11 截图拍空的原因。
        """
        self._dlg = JudgeDialog(res, parent=self.window(), scale=self._scale,
                                qid=qid, errors=errors)
        self._dlg.show_at_parent()

    # -------------------------------------------------- 主题
    def theme_changed(self, pref=None):
        old = T.PALETTES.get(getattr(self, "_theme_mode", T.mode),
                             T.PALETTES["light"])
        P = T.P
        self.setStyleSheet(
            f"QFrame#AnsPanel{{background:{P['panel_soft']};"
            f" border:1px solid {P['glass_soft_edge']};"
            f" border-radius:14px;}}")
        self.title.setStyleSheet(
            f"font-size:14px; font-weight:700; color:{P['ink']};"
            f"background:transparent;")
        for label in self.findChildren(QLabel):
            style = label.styleSheet()
            for key in ("ink", "ink2", "ink3", "ok_fg", "warn_fg",
                        "err_fg", "accent_fg"):
                before, after = old.get(key), P[key]
                if before and before != after:
                    style = style.replace(before, after)
            label.setStyleSheet(style)
        for edit in self.findChildren(QLineEdit):
            edit.setStyleSheet(_inp_style())
        for combo in self._ex_combos.values():
            combo.refresh()
        for button in (self.b_clear, self.b_scan, self.b_submit):
            button.refresh()
        self._theme_mode = T.mode


# ================================================================ 判题结果

@motion_page
class JudgeDialog(QFrame):
    """判题结果弹窗。

    可拖动：鼠标按住顶部标题条就能挪窗。判题结果经常要和题卡、LinkGCS2
    界面对照着看，窗口被钉死在屏幕中央、挡住要看的东西时非常碍事。
    """

    def __init__(self, result=None, parent=None, scale=1.0, qid=None,
                 errors=None, gcs_note=""):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self._workbench = parent
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self._scale = scale
        self._errors = errors or []
        self._res = result
        self._gcs_note = gcs_note or ""
        self._drag = None        # 拖动起点（按下时相对窗口左上角的偏移）

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(0)
        self.card = glass("GlassStrong", radius=18)
        attach_shadow(self.card, blur=54, dy=12, alpha=125)
        outer.addWidget(self.card)

        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        # ---- 头部 ----
        head = QWidget()
        head.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(int(26 * scale), int(20 * scale),
                              int(14 * scale), int(10 * scale))
        hl.setSpacing(int(14 * scale))

        if self._errors:
            mark, color, big, sub = ("!", T.P["warn_fg"], "输入有问题",
                                     "改一下再提交")
        elif result and result.get("partial_grading") and result.get("judgeable"):
            mark, color, big, sub = ("?", T.P["ink2"], "部分核验",
                "已核对 %d 项，符合 %d 项；完整航线尚未核验" % (
                    result.get("n_total", 0), result.get("n_ok", 0)))
        elif result and result.get("ok"):
            if result.get("reference_saved"):
                detail = "完整航线已核验并收录为参考答案"
            elif result.get("reference_review") == "MATCHED":
                detail = "完整作答与现有参考答案一致"
            elif result.get("reference_reason"):
                detail = "本次已核对项通过；参考答案未收录：" + result["reference_reason"]
            else:
                detail = "已核对 %d 项" % result.get("n_total", 0)
            mark, color, big, sub = ("√", T.P["ok_fg"], "做题正确", detail)
        elif result and not result.get("judgeable"):
            mark, color, big, sub = ("?", T.P["ink2"], "无法自动判分",
                                     "下方是本题的参考信息")
        elif result:
            n_bad = result.get("n_total", 0) - result.get("n_ok", 0)
            mark, color, big, sub = ("×", T.P["err_fg"], "有 %d 处需要修正" % n_bad,
                                     "看下面的错因，逐项对一遍")
        else:
            mark, color, big, sub = ("?", T.P["ink2"], "提示", "")

        ic = QLabel()
        ic.setFixedSize(int(46 * scale), int(46 * scale))
        ic.setPixmap(self._mark_pix(mark, color, scale))
        ic.setAlignment(Qt.AlignCenter)
        hl.addWidget(ic)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(int(2 * scale))
        t1 = QLabel(("第 %s 题 · " % qid if qid else "") + big)
        t1.setStyleSheet(
            f"font-size:{round(19 * scale)}px; font-weight:800;"
            f" color:{color}; background:transparent;")
        col.addWidget(t1)
        t2 = QLabel(sub)
        t2.setWordWrap(True)
        t2.setStyleSheet(
            f"font-size:12px; color:{T.P['ink3']}; background:transparent;")
        col.addWidget(t2)
        hl.addLayout(col)
        hl.addStretch()

        b_close = QPushButton("关闭")
        b_close.setCursor(Qt.PointingHandCursor)
        b_close.setFixedHeight(int(30 * scale))
        b_close.setStyleSheet(btn_style("default", scale=scale, font_size=12))
        b_close.clicked.connect(self.close)
        hl.addWidget(b_close)

        # ---- 标题条即拖动区 ----
        # 只把「头部」变成拖动把手，而不是整个窗口 —— 否则正文里想选中
        # 一段错因文字都会被当成拖动，反而更难用。
        # 两个坑（跟工作台标题栏同一个）：
        # 1) 必须记的是 `globalPos - frameGeometry().topLeft()`，
        #    按 localPos 算会在窗口移动后产生累积漂移、手感和鼠标脱节；
        # 2) 鼠标事件默认被下面的子控件（QLabel）吃掉，得给它们装上
        #    鼠标穿透，否则只有点在没有文字的空隙里才能拖动。
        head.setCursor(Qt.SizeAllCursor)
        head.mousePressEvent = self._t_press
        head.mouseMoveEvent = self._t_move
        head.mouseReleaseEvent = self._t_release
        for child in head.findChildren(QLabel):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._head = head
        cl.addWidget(head)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{T.P['glass_soft_edge']};")
        cl.addWidget(sep)

        # ---- 明细 ----
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollArea>QWidget>QWidget{background:transparent;}")
        cl.addWidget(scroll, 1)

        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        scroll.setWidget(inner)
        v = QVBoxLayout(inner)
        v.setContentsMargins(int(24 * scale), int(14 * scale),
                             int(24 * scale), int(18 * scale))
        v.setSpacing(int(9 * scale))

        if self._errors:
            for e in self._errors:
                v.addWidget(self._line("×", T.P["err_fg"], e, "", "", scale))
        elif result:
            if self._gcs_note:
                v.addWidget(self._gcs_banner(scale))
            self._fill(v, result, scale)

        v.addStretch()

        # ---- 底部 ----
        foot = QWidget()
        foot.setStyleSheet("background:transparent;")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(int(24 * scale), int(6 * scale),
                              int(24 * scale), int(16 * scale))
        fl.setSpacing(8)
        fl.addStretch()
        b_ok = QPushButton("知道了")
        b_ok.setCursor(Qt.PointingHandCursor)
        b_ok.setMinimumHeight(int(34 * scale))
        b_ok.setStyleSheet(btn_style("primary", scale=scale, font_size=13))
        b_ok.clicked.connect(self.close)
        fl.addWidget(b_ok)
        cl.addWidget(foot)

        w = int(660 * scale)
        h = int(580 * scale)
        self.setMinimumSize(int(430 * scale), int(320 * scale))
        self.resize(w, h)

    # -------------------------------------------------- 明细渲染
    def _gcs_banner(self, scale):
        """顶部来源条：说明这份作答是从 LinkGCS2 读来的，以及点是怎么对齐的。

        必须讲清楚「你画的第几个航点被当成了哪个标准点」——
        否则用户看到"避让点错了"，第一反应是"软件读错数据了"，
        而不是"我画错了"。对齐关系摆出来，争议就没了。
        """
        box = QFrame()
        box.setObjectName("GcsBanner")
        box.setStyleSheet(
            f"QFrame#GcsBanner{{background:{T.P['panel_soft']};"
            f" border:1px solid {T.P['glass_soft_edge']};"
            f" border-radius:10px;}}")
        v = QVBoxLayout(box)
        m = int(11 * scale)
        v.setContentsMargins(m, int(9 * scale), m, int(9 * scale))
        v.setSpacing(int(3 * scale))

        t = QLabel(self._gcs_note)
        t.setWordWrap(True)
        t.setStyleSheet(
            f"font-size:12px; font-weight:600; color:{T.P['ink']};"
            f" background:transparent;")
        v.addWidget(t)

        align = (self._res or {}).get("align") or []
        if align:
            bits = []
            for d in align:
                if d.get("matched"):
                    src = d.get("src_idx")
                    src = ("#%s" % src) if not isinstance(src, str) else src
                    bits.append("%s→%s" % (d["name"], src))
                else:
                    bits.append("%s→未找到" % d["name"])
            sub = QLabel("对齐关系：" + "　".join(bits))
            sub.setWordWrap(True)
            sub.setStyleSheet(
                f"font-size:11px; color:{T.P['ink3']};"
                f" background:transparent; line-height:160%;")
            v.addWidget(sub)
        return box

    def _open_reference_correction(self):
        workbench = self._workbench
        self.close()
        reference = getattr(workbench, "_ref", None)
        if reference is not None and reference.isVisible():
            reference.raise_()
            reference.activateWindow()
        elif callable(getattr(workbench, "toggle_ref", None)):
            workbench.toggle_ref()

    @staticmethod
    def _is_missing(it):
        """这一项是「软件里没这个数据」，而不是「用户填错了」。

        GCS 模式下一个字段在 Mission.json 里压根不存在（比如停留时间），
        标成红色的"×"会让用户以为自己做错了 —— 其实是读不到。
        这种要单独用中性标记，并在最后解释一句。
        """
        y = str(it.get("your") or "")
        return y in ("未填", "未作答", "未选", "")

    def _fill(self, v, res, scale):
        if not res.get("judgeable"):
            tip = QLabel(res.get("reason") or "这道题无法自动判分。")
            tip.setWordWrap(True)
            tip.setStyleSheet(
                f"font-size:13px; color:{T.P['ink2']};"
                f"background:transparent; line-height:170%;")
            v.addWidget(tip)
            category, advice = recovery_advice(res.get("reason"), res.get("details"))
            label = QLabel(category)
            label.setStyleSheet(f"font-size:14px;font-weight:700;color:{T.P['ink']};background:transparent;")
            v.addWidget(label)
            help_text = QLabel(advice)
            help_text.setWordWrap(True)
            help_text.setStyleSheet(f"font-size:13px;color:{T.P['ink2']};background:transparent;")
            v.addWidget(help_text)
            if callable(getattr(self._workbench, "toggle_ref", None)):
                button = IconButton("打开参考答案校对", "pen", "default", font_size=12)
                button.clicked.connect(self._open_reference_correction)
                v.addWidget(button, 0, Qt.AlignLeft)
            return

        missing = []

        def _mark(it):
            if it["ok"]:
                return "√", T.P["ok_fg"]
            if self._is_missing(it):
                return "–", T.P["ink3"]
            return "×", T.P["err_fg"]

        for it in res["points"]:
            mk, col = _mark(it)
            if not it["ok"] and self._is_missing(it):
                missing.append(it["name"])
            v.addWidget(self._line(
                mk, col, it["name"], it["your"],
                ("" if it["ok"] else it["want"]), scale,
                why=it.get("why") or "", hint=it.get("hint") or ""))

        for it in res["params"]:
            mk, col = _mark(it)
            if not it["ok"] and self._is_missing(it):
                missing.append(it.get("meaning") or it.get("key"))
            s = "%s%s" % (it["your"], it.get("unit") or "")
            w = "%s%s" % (it["want"], it.get("unit") or "")
            v.addWidget(self._line(
                mk, col, it["meaning"], s, ("" if it["ok"] else w), scale,
                hint=it.get("hint") or ""))

        for it in res["extras"]:
            mk, col = _mark(it)
            if not it["ok"] and self._is_missing(it):
                missing.append(it["name"])
            v.addWidget(self._line(
                mk, col, it["name"], str(it["your"]),
                ("" if it["ok"] else str(it["want"])), scale))

        if missing:
            note = QLabel(
                "标记为「–」的 %d 项（%s）不是判你错 —— 这些字段 LinkGCS2 的"
                "任务里没有保存，软件读不到，所以只能空着。要核对它们请照着"
                "软件界面自己看一眼。" % (len(missing), "、".join(missing[:4])))
            note.setWordWrap(True)
            note.setStyleSheet(
                f"font-size:11.5px; color:{T.P['ink3']};"
                f" background:transparent; line-height:165%;"
                f" padding-top:{int(6 * scale)}px;")
            v.addWidget(note)

    def _line(self, mark, color, name, your, want, scale, why="", hint=""):
        box = QWidget()
        box.setStyleSheet("background:transparent;")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(int(3 * scale))

        row = QWidget()
        row.setStyleSheet("background:transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(int(9 * scale))

        mk = QLabel(mark)
        mk.setFixedWidth(int(16 * scale))
        mk.setStyleSheet(
            f"font-size:15px; font-weight:900; color:{color};"
            f"background:transparent;")
        rl.addWidget(mk)

        nm = QLabel(name)
        nm.setFixedWidth(int(132 * scale))
        nm.setWordWrap(True)
        nm.setStyleSheet(
            f"font-size:12.5px; font-weight:600; color:{T.P['ink']};"
            f"background:transparent;")
        rl.addWidget(nm)

        yr = QLabel(str(your))
        yr.setWordWrap(True)
        yr.setStyleSheet(
            f"font-size:12.5px; color:{T.P['ink2']}; background:transparent;")
        rl.addWidget(yr, 1)

        if want:
            wl = QLabel("应为 %s" % want)
            wl.setWordWrap(True)
            wl.setStyleSheet(
                f"font-size:12.5px; font-weight:700; color:{T.P['ok_fg']};"
                f"background:transparent;")
            rl.addWidget(wl, 1)
        v.addWidget(row)

        if why and not hint:
            sub = QLabel("依据：%s" % why)
            sub.setWordWrap(True)
            sub.setStyleSheet(
                f"font-size:11px; color:{T.P['ink3']}; background:transparent;"
                f" padding-left:{int(25 * scale)}px;")
            v.addWidget(sub)
        if hint:
            sub = QLabel(hint)
            sub.setWordWrap(True)
            sub.setStyleSheet(
                f"font-size:11.5px; color:{T.P['warn_fg']};"
                f"background:transparent; line-height:160%;"
                f" padding-left:{int(25 * scale)}px;")
            v.addWidget(sub)
        return box

    # -------------------------------------------------- 小工具
    @staticmethod
    def _mark_pix(mark, color, scale):
        """画一个圆形底 + 符号的图标。"""
        s = int(46 * scale)
        pm = QPixmap(s, s)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(color)
        bg = QColor(c)
        bg.setAlpha(38)
        p.setBrush(bg)
        p.setPen(Qt.NoPen)
        p.drawEllipse(1, 1, s - 2, s - 2)
        p.setPen(c)
        f = p.font()
        f.setPixelSize(int(s * 0.52))
        f.setBold(True)
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignCenter, mark)
        p.end()
        return pm

    def show_at_parent(self):
        self.show()
        par = self.parent()
        if par is not None and par.isVisible():
            g = par.frameGeometry()
            self.move(g.center().x() - self.width() // 2,
                      g.center().y() - self.height() // 2)
        else:
            scr = self.screen().availableGeometry()
            self.move(scr.center().x() - self.width() // 2,
                      scr.center().y() - self.height() // 2)
        self.raise_()
        self.activateWindow()

    # -------------------------------------------------- 标题条拖动
    def _t_press(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag = (ev.globalPosition().toPoint()
                          - self.frameGeometry().topLeft())

    def _t_move(self, ev):
        if self._drag is not None and (ev.buttons() & Qt.LeftButton):
            self.move(ev.globalPosition().toPoint() - self._drag)

    def _t_release(self, ev):
        self._drag = None
