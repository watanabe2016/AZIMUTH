# -*- coding: utf-8 -*-
"""成绩趋势 —— 按日期看正确率与练习量。

这是**只读展示**：数据由 progress.mark_judge() 在每次判题时自动落一笔，
这里只负责把 progress.trend_days() 的结果画成图。

★ 为什么要画这么多图（v1.9.8 五三反馈："光有答案数据不够直观"）：
  原来只有 4 张数字卡 + 一张柱状折线图。数字卡的问题是**要读、要比较** ——
  "判对 3 / 判错 5"到底算好还是差？得在心里算一遍。图不用算，扫一眼就有结论。
  所以补齐三件视觉件，各回答一个"一眼"问题：
    · 环形图 —— "我现在的正确率大概几成？"（角度比数字快）
    · 对错条 —— "对和错的比例差多少？"（长度比数字快）
    · 柱状+折线 —— "我在变好还是变差？"（趋势只有图能表达）

★ 空态要有**专属画面**，不能只画网格 —— 没数据时满屏网格线看起来像"出错了"，
  用户截图里就是这个效果。现在空态直接画一个大号引导提示。

★ 数据点太少时**不画折线**：只有 1 个点连不成线，硬画出来是个孤零零的圆点，
  比不画更容易让人困惑。少于 2 个点就只留柱子。

零外部依赖：环形 / 柱子 / 折线都是 QPainter 现画的，不引入 matplotlib
（打包体积极敏感，且这图简单到不值一个依赖）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (QPainter, QColor, QPen, QFont, QPainterPath)
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QScrollArea, QWidget)

import theme as T
import progress as PG
from ui_kit import (glass, glass_style, refresh_glass, btn_style,
                    attach_shadow, show_window_animated,
                    close_window_animated)

# 字体名从 theme 里问，别硬编码 —— 换字体时这里会跟着变
FONT = T.pick_font()

# ★ 柱状图"空日"占的槽宽权重（有记录的日子固定 1.0）。
#   0.30 是实调出来的：再小断档就看不清（空日挤成一条缝，时间轴失去意义），
#   再大又回到"后几天没练就空半张图"的老问题。
#   提到模块级是为了让验收脚本能直接断言，不必去 paintEvent 里抠。
EMPTY_W = 0.30


class Donut(QWidget):
    """正确率环形图 —— 一眼看出"大概几成"。

    为什么用环形不用实心饼：中间那块空白正好放百分比大字，
    一个控件同时给出"形状"和"精确值"，比饼图 + 旁边贴数字省地方。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ok = 0
        self.bad = 0
        self.rate = None          # 0~100，None = 还没数据
        self.setMinimumSize(150, 150)
        self.setMaximumHeight(178)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

    def set_data(self, ok, bad, rate):
        self.ok, self.bad, self.rate = ok, bad, rate
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        W, H = self.width(), self.height()
        side = min(W, H) - 8
        if side < 40:
            p.end()
            return
        box = QRectF((W - side) / 2.0, (H - side) / 2.0, side, side)
        th = max(9.0, side * 0.13)            # 环宽
        total = self.ok + self.bad

        # ★ 环用"粗圆头画笔"画，不用 drawPie 掏空 ——
        #   drawPie 掏空要拿背景色盖一个圆，而 panel_bg 在无边框半透明窗口下
        #   是带 alpha 的，盖出来是一块**黑饼**（第一版实测中心全黑）。
        #   改用 QPen(宽度=环宽, RoundCap) 直接画弧，中心天然是空的。
        p.setBrush(Qt.NoBrush)

        # 底环（灰）
        # ★ alpha 别太低：48 时在浅色主题下几乎看不见，"剩下三成"就没了参照物。
        #   88 正好 —— 看得出是环，又不跟绿色抢注意力。
        ring_bg = QColor(T.P["ink3"])
        ring_bg.setAlpha(88)
        pen_bg = QPen(ring_bg)
        pen_bg.setWidthF(th)
        pen_bg.setCapStyle(Qt.FlatCap)        # 底环用平头，免得两端鼓出来
        p.setPen(pen_bg)
        # 从 12 点顺时针整整一圈：起点 90°、sweep -360°
        # （Qt 的 QPainterPath 角度：0°=3 点钟、正方向=逆时针，所以顺时针要负 sweep）
        p.drawArc(box.adjusted(th / 2, th / 2, -th / 2, -th / 2),
                  int(90 * 16), int(-360 * 16))

        # 前景弧：从 12 点**顺时针**铺正确率
        if total > 0 and self.rate is not None:
            frac = max(0.0, min(1.0, self.rate / 100.0))
            col = QColor(T.C_GREEN) if self.rate >= 60 else (
                QColor(T.C_AMBER) if self.rate >= 30 else QColor(T.C_RED))
            pen_fg = QPen(col)
            pen_fg.setWidthF(th)
            pen_fg.setCapStyle(Qt.RoundCap)
            p.setPen(pen_fg)
            # ★ 起止都顺时针算：起点 90°（12 点），扫过 -frac*360°
            p.drawArc(box.adjusted(th / 2, th / 2, -th / 2, -th / 2),
                      int(90 * 16), int(-frac * 360 * 16))

        # 中心文字：正确率 + 副标（中心是空的，直接写字）
        p.setPen(QColor(T.P["ink"] if total else T.P["ink3"]))
        big = "—" if self.rate is None else "%d%%" % self.rate
        f1 = QFont(FONT, max(13, int(side * 0.17)))
        f1.setBold(True)
        p.setFont(f1)
        p.drawText(box, Qt.AlignCenter, big)
        p.setPen(QColor(T.P["ink3"]))
        f2 = QFont(FONT, max(7, int(side * 0.062)))
        p.setFont(f2)
        p.drawText(QRectF(box.left(), box.center().y() + side * 0.11,
                          box.width(), side * 0.16),
                   Qt.AlignHCenter | Qt.AlignTop, "正确率")
        p.end()


class RatioBar(QWidget):
    """对错比分条 —— 长度比数字快，一眼看出"对多还是错多"。

    ★ 一段都没有（total=0）时画一条整条的灰槽 + 中间文字，
      不要留空白 —— 空白跟"渲染失败"长得一样。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ok = 0
        self.bad = 0
        self.setMinimumHeight(46)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_data(self, ok, bad):
        self.ok, self.bad = ok, bad
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        W, H = self.width(), self.height()
        if W < 40 or H < 24:
            p.end()
            return
        total = self.ok + self.bad
        bh = 16.0
        y = (H - bh) / 2.0
        box = QRectF(0, y, W, bh)
        rad = bh / 2.0

        # 槽底
        p.setPen(Qt.NoPen)
        slot = QColor(T.P["ink3"])
        slot.setAlpha(40)
        p.setBrush(slot)
        p.drawRoundedRect(box, rad, rad)

        if total > 0:
            frac_ok = self.ok / float(total)
            # 圆角矩形左对齐画绿段，再把右端"切平"——否则中间会出现圆角的缝
            path = QPainterPath()
            path.addRoundedRect(box, rad, rad)
            p.save()
            p.setClipPath(path)
            w_ok = W * frac_ok
            p.setBrush(QColor(T.C_GREEN))
            p.drawRect(QRectF(0, y, w_ok, bh))
            p.setBrush(QColor(T.C_RED))
            p.drawRect(QRectF(w_ok, y, W - w_ok, bh))
            p.restore()
        else:
            p.setPen(QColor(T.P["ink3"]))
            p.setFont(QFont(FONT, 8))
            p.drawText(box, Qt.AlignCenter, "还没有判题记录")

        # 两端标注：左绿对、右红错
        p.setFont(QFont(FONT, 8))
        p.setPen(QColor(T.C_GREEN))
        p.drawText(QRectF(0, 0, W, y), Qt.AlignLeft | Qt.AlignVCenter,
                   "对 %d" % self.ok)
        p.setPen(QColor(T.C_RED))
        p.drawText(QRectF(0, 0, W, y), Qt.AlignRight | Qt.AlignVCenter,
                   "错 %d" % self.bad)
        p.end()


class TrendChart(QWidget):
    """柱状（对/错次数）+ 折线（正确率）的合成图。"""

    PAD_L, PAD_R = 40, 44     # 左留次数轴、右留百分比轴
    PAD_T, PAD_B = 16, 30     # 上留顶、下留日期

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_rows(self, rows):
        self.rows = list(rows or [])
        self.update()

    # ---------------------------------------------------------- 画
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        W, H = self.width(), self.height()
        if W < 60 or H < 60:
            p.end()
            return

        # ★ 空态：画引导语，不要只画一张空网格 ——
        #   满屏网格线看起来像"图表坏了"，用户截图里就是这个效果。
        has_data = any((r["total"] or 0) > 0 for _, r in self.rows)
        if not self.rows or not has_data:
            p.setPen(QColor(T.P["ink3"]))
            f = QFont(FONT, 10)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter,
                       "还没有判题记录\n\n"
                       "去「题卡工作台」点一次「判题」，这里立刻就有曲线了")
            p.end()
            return

        x0, x1 = self.PAD_L, W - self.PAD_R
        y0, y1 = self.PAD_T, H - self.PAD_B
        if x1 <= x0 or y1 <= y0:
            p.end()
            return

        P = T.P
        n = len(self.rows)

        # 次数轴上限：至少 5，免得"今天只判 1 题"时那根柱子顶到天花板
        vmax = max(5, max(r["total"] for _, r in self.rows))
        # 取整到 5 的倍数，刻度好看
        vmax = int((vmax + 4) // 5 * 5)
        if vmax <= 0:
            vmax = 5

        def vx(v):
            """次数 -> 像素 y"""
            return y1 - (y1 - y0) * (v / float(vmax))

        # ---- 网格 + 左轴刻度 ----
        grid = QColor(P["ink3"])
        grid.setAlpha(58)
        p.setPen(QPen(grid, 1, Qt.DashLine))
        p.setFont(QFont(FONT, 7))
        steps = 4
        for i in range(steps + 1):
            v = vmax * i / float(steps)
            y = vx(v)
            p.setPen(QPen(grid, 1, Qt.DashLine))
            p.drawLine(QPointF(x0, y), QPointF(x1, y))
            p.setPen(QColor(P["ink3"]))
            p.drawText(QRectF(0, y - 8, x0 - 6, 16),
                       Qt.AlignRight | Qt.AlignVCenter, "%d" % round(v))

        # ---- ★ 弹性槽宽 ----
        #   等宽槽在"后面几天没练"时会留一大片空白（第一版实测：14 天里后 7 天
        #   没练，右半张图全空，看着像没画完）。
        #   改成：**有记录的日子占 1.0 权重，空日只占 EMPTY_W** —— 空白被压扁，
        #   有数据的日子自然展开，但时间顺序和"断档"仍然看得出来。
        weights = [1.0 if (r["total"] or 0) > 0 else EMPTY_W for _, r in self.rows]
        total_w = sum(weights) or 1.0
        unit = (x1 - x0) / total_w
        # 每天的槽中心 x（累加权重算，不再用等距 slot）
        centers, acc = [], 0.0
        for wgt in weights:
            centers.append(x0 + acc + wgt * unit / 2.0)
            acc += wgt * unit

        bw = max(3.0, min(22.0, unit * 0.5))
        c_ok, c_bad = QColor(T.C_GREEN), QColor(T.C_RED)
        c_rate = QColor(T.C_BLUE)

        # ---- ★ 空白日底纹 ----
        #   完全没判题的日子原来是"什么都没有"，看着像图表坏了。
        #   给每个空日槽画一根**很矮的灰桩** = "这天有、但没练"。
        #   ★ 颜色用 ink3 + 高 alpha（浅色主题下 46 太淡看不见，深色主题下更看不见，
        #     两个主题都要能用 —— 88 实测两边都看得清）。
        empty_col = QColor(P["ink3"])
        empty_col.setAlpha(88)
        for i, (day, r) in enumerate(self.rows):
            if (r["total"] or 0) > 0:
                continue
            p.setPen(Qt.NoPen)
            p.setBrush(empty_col)
            p.drawRect(QRectF(centers[i] - bw / 2, y1 - 3, bw, 3))

        # ---- 柱子（对：绿在下；错：红在上，堆叠） ----
        for i, (day, r) in enumerate(self.rows):
            cx = centers[i]
            ok, bad = r["ok"], r["bad"]
            if ok <= 0 and bad <= 0:
                continue
            # 从底往上堆：先绿（对）再红（错）
            yb = y1
            if ok > 0:
                yt = vx(ok)
                rr = QRectF(cx - bw / 2, yt, bw, yb - yt)
                p.setPen(Qt.NoPen); p.setBrush(c_ok)
                p.drawRect(rr)
                yb = yt
            if bad > 0:
                yt = vx(ok + bad)
                rr = QRectF(cx - bw / 2, yt, bw, yb - yt)
                p.setPen(Qt.NoPen); p.setBrush(c_bad)
                p.drawRect(rr)

        # ---- 折线：每天的正确率 ----
        # ★ 少于 2 个点不画折线 —— 一个孤点是"没有趋势"，画出来只会让人困惑。
        pts = []
        for i, (day, r) in enumerate(self.rows):
            if r["rate"] is None:
                continue
            yy = y1 - (y1 - y0) * (r["rate"] / 100.0)
            pts.append(QPointF(centers[i], yy))
        if len(pts) >= 2:
            path = QPainterPath(pts[0])
            for q in pts[1:]:
                path.lineTo(q)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(c_rate, 2.0))
            p.drawPath(path)
        if pts:
            p.setPen(Qt.NoPen); p.setBrush(c_rate)
            for q in pts:
                p.drawEllipse(q, 2.6, 2.6)

        # ---- 右轴：正确率 0 / 50 / 100 ----
        p.setFont(QFont(FONT, 7))
        for v in (0, 50, 100):
            y = y1 - (y1 - y0) * (v / 100.0)
            p.setPen(QColor(P["ink3"]))
            p.drawText(QRectF(x1 + 5, y - 8, self.PAD_R - 8, 16),
                       Qt.AlignLeft | Qt.AlignVCenter, "%d%%" % v)

        # ---- 日期刻度 ----
        # ★ 改口径：只标**有记录的日子**（原来固定标首/中/尾，压缩槽宽后
        #   首尾可能都是空日，标出来的日期跟柱子对不上，白标）。
        #   有记录的日子超过 6 个就抽稀，隔一个标一个。
        p.setPen(QColor(P["ink3"]))
        marks = [i for i, (_, r) in enumerate(self.rows) if (r["total"] or 0) > 0]
        if len(marks) > 6:
            step = (len(marks) + 5) // 6
            marks = marks[::step]
        for i in marks:
            day = self.rows[i][0]
            cx = centers[i]
            md = day[5:]                    # 只留 MM-DD
            p.drawText(QRectF(cx - 26, y1 + 5, 52, 16),
                       Qt.AlignHCenter | Qt.AlignTop, md)
        p.end()


class TrendDialog(QFrame):
    """成绩趋势窗口。

    ★ v1.9.9：改成**独立顶层窗口**（同错题集，结构照抄参考答案浮层）。
      旧版是带 parent 的普通 QFrame（子控件）—— place_near 给屏幕全局坐标、
      子控件 move() 按父内局部坐标解释 → 弹出位置错到右下角、被父窗口裁剪、
      拖不出主窗口、拖动还带着半透明主窗口全量重绘（卡顿）。四个症状一个根因。
    """

    def __init__(self, parent=None, days=14):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setWindowTitle("成绩趋势")
        # ★ v1.9.8 加了环形图 + 对错条，窗口要跟着放大 ——
        #   660×520 是"只有数字卡 + 一张图"时代的尺寸，现在塞不下会挤成豆腐块。
        self.setMinimumWidth(720)
        self.setMinimumHeight(600)
        self._days = days
        self._drag = None
        self._theme_mode = T.mode
        self._window_motion = None
        self._window_closing = False

        # 外层透明容器 + 内层玻璃卡片（阴影挂卡片 —— 挂窗体自身会把圆角变方）
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)
        card = glass("GlassStrong", radius=16)
        attach_shadow(card, blur=44, dy=12, alpha=110)
        outer.addWidget(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(20, 16, 20, 18)
        root.setSpacing(12)

        # ---- 头部（唯一拖动把手） ----
        self.head = QWidget()
        self.head.setFixedHeight(40)
        self.head.setStyleSheet("background:transparent;")
        head = QHBoxLayout(self.head)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(9)
        t = QLabel("成绩趋势")
        self._title = t
        t.setObjectName("H2")
        t.setStyleSheet(f"font-size:18px; font-weight:800; color:{T.P['ink']};"
                        f"background:transparent;")
        s = QLabel("每次判题自动记账，这里看走势")
        self._subtitle = s
        s.setStyleSheet(f"font-size:12px; color:{T.P['ink3']};"
                        f"background:transparent;")
        head.addWidget(t)
        head.addWidget(s)
        head.addStretch()

        self.b_days = QPushButton("最近 14 天")
        self.b_days.setMinimumHeight(30)
        self.b_days.setStyleSheet(btn_style("ghost", font_size=12))
        self.b_days.clicked.connect(self._toggle_range)

        self.b_clear = QPushButton("清空趋势")
        self.b_clear.setMinimumHeight(30)
        self.b_clear.setStyleSheet(btn_style("ghost", font_size=12))

        b_close = QPushButton("关闭")
        self._close_button = b_close
        b_close.setMinimumHeight(30)
        b_close.setStyleSheet(btn_style("default", font_size=12))
        b_close.clicked.connect(self.close)

        head.addWidget(self.b_days)
        head.addWidget(self.b_clear)
        head.addWidget(b_close)
        root.addWidget(self.head)
        for w in (t, s):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.head.mousePressEvent = self._p_press
        self.head.mouseMoveEvent = self._p_move
        self.head.mouseReleaseEvent = self._p_release

        # ---- ★ 顶部视觉区：左环形图 + 右（对错条 + 关键数字）----
        #   v1.9.8 五三："光有答案数据不够直观" —— 环形图和比条负责"一眼看结论"，
        #   数字卡负责"要精确值时能读到"，两者互补，删掉哪个都不行。
        top = QHBoxLayout()
        top.setSpacing(12)

        donut_box = glass()
        dl = QVBoxLayout(donut_box)
        dl.setContentsMargins(12, 10, 12, 10)
        dl.setSpacing(4)
        dlab = QLabel("本次统计区间正确率")
        dlab.setAlignment(Qt.AlignCenter)
        dlab.setStyleSheet(f"font-size:11px; color:{T.P['ink3']};"
                           f"background:transparent;")
        self.donut = Donut()
        dl.addWidget(dlab)
        dl.addWidget(self.donut, 1, Qt.AlignHCenter)
        top.addWidget(donut_box, 0)

        right = QVBoxLayout()
        right.setSpacing(9)

        bar_box = glass()
        bl = QVBoxLayout(bar_box)
        bl.setContentsMargins(14, 9, 14, 7)
        bl.setSpacing(3)
        blab = QLabel("判对 / 判错 比例")
        blab.setStyleSheet(f"font-size:11px; color:{T.P['ink3']};"
                           f"background:transparent;")
        self.ratio = RatioBar()
        bl.addWidget(blab)
        bl.addWidget(self.ratio)
        right.addWidget(bar_box, 0)

        # ---- 概览卡（紧凑版：数字 + 标签一行内）----
        self.cards = {}
        crow = QHBoxLayout()
        crow.setSpacing(8)
        for key, label, col in (("total", "判题次数", T.C_BLUE),
                                ("ok", "判对", T.C_GREEN),
                                ("bad", "判错", T.C_RED),
                                ("streak", "连续打卡", T.C_AMBER)):
            c = glass("GlassSoft")
            cl = QVBoxLayout(c)
            cl.setContentsMargins(10, 7, 10, 7)
            cl.setSpacing(0)
            v = QLabel("0")
            v.setAlignment(Qt.AlignCenter)
            v.setStyleSheet(f"font-size:19px; font-weight:800; color:{col};"
                            f"background:transparent;")
            l = QLabel(label)
            l.setAlignment(Qt.AlignCenter)
            l.setStyleSheet(f"font-size:10px; color:{T.P['ink3']};"
                            f"background:transparent;")
            cl.addWidget(v)
            cl.addWidget(l)
            self.cards[key] = (v, l)
            crow.addWidget(c, 1)
        right.addLayout(crow)
        right.addStretch()
        top.addLayout(right, 1)
        root.addLayout(top)

        # ---- 图 ----
        chart_box = glass()
        cl2 = QVBoxLayout(chart_box)
        cl2.setContentsMargins(14, 12, 14, 10)
        cl2.setSpacing(7)

        legend = QHBoxLayout()
        legend.setSpacing(14)
        for txt, col in (("判对（次）", T.C_GREEN), ("判错（次）", T.C_RED),
                         ("正确率（%）", T.C_BLUE)):
            dot = QLabel("●")
            dot.setStyleSheet(f"font-size:12px; color:{col};"
                              f"background:transparent;")
            lab = QLabel(txt)
            lab.setStyleSheet(f"font-size:11px; color:{T.P['ink2']};"
                              f"background:transparent;")
            legend.addWidget(dot)
            legend.addWidget(lab)
        legend.addStretch()
        cl2.addLayout(legend)

        self.chart = TrendChart()
        cl2.addWidget(self.chart, 1)
        root.addWidget(chart_box, 1)

        # ---- 底部说明 + 明细 ----
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"font-size:11px; color:{T.P['ink3']};"
                                f"background:transparent;")
        root.addWidget(self.hint)

        self.reload()
        self.theme_changed()

    # ---------------------------------------------------------- 数据
    def reload(self):
        rows = PG.trend_days(self._days, fill=False)
        s = PG.trend_summary(self._days)

        self.cards["total"][0].setText(str(s["total"]))
        self.cards["ok"][0].setText(str(s["ok"]))
        self.cards["bad"][0].setText(str(s["bad"]))
        self.cards["streak"][0].setText("%d天" % s["streak"])
        self.cards["total"][1].setText("判题次数（近%d天）" % self._days)

        # ★ 三个视觉件都吃同一份 summary —— 数字卡的「正确率」搬进环形图中心了，
        #   所以卡里不再重复放一个正确率（同一屏出现两个"正确率"很啰嗦）。
        self.donut.set_data(s["ok"], s["bad"], s["rate"])
        self.ratio.set_data(s["ok"], s["bad"])

        # 画图用补齐版（空日子也要出现在时间轴上）
        self.chart.set_rows(PG.trend_days(self._days, fill=True))

        if not rows:
            self.hint.setText(
                "最近 %d 天还没有判题记录。\n"
                "在「题卡工作台」里点「判题」跑一次，这里就会有数据了 —— "
                "判对算一次「对」，判错算一次「错」。" % self._days)
        else:
            self.hint.setText(
                "近 %d 天共判题 %d 次（对 %d / 错 %d），练过 %d 道题，"
                "有 %d 天在练，当前连续 %d 天。\n"
                "正确率按**判题次数**算 —— 同一道题反复判会重复计入，"
                "它反映的是当下的手感，不是题库覆盖率。"
                % (self._days, s["total"], s["ok"], s["bad"],
                   s["questions"], s["active_days"], s["streak"]))

    def _toggle_range(self):
        self._days = 30 if self._days == 14 else 14
        self.b_days.setText("最近 %d 天" % self._days)
        self.reload()

    def attach_clear(self, cb):
        """把"清空趋势"接到外部回调（清空要弹确认框，逻辑放调用方）"""
        self.b_clear.clicked.connect(cb)

    def theme_changed(self, pref=None):
        old = T.PALETTES.get(self._theme_mode, T.PALETTES["light"])
        for label in self.findChildren(QLabel):
            style = label.styleSheet()
            for key in ("ink", "ink2", "ink3", "accent_fg"):
                before, after = old.get(key), T.P[key]
                if before and before != after:
                    style = style.replace(before, after)
            label.setStyleSheet(style)
        self._theme_mode = T.mode
        self._title.setStyleSheet(
            f"font-size:18px; font-weight:800; color:{T.P['ink']}; background:transparent;")
        self._subtitle.setStyleSheet(
            f"font-size:12px; color:{T.P['ink3']}; background:transparent;")
        self.b_days.setStyleSheet(btn_style("ghost", font_size=12))
        self.b_clear.setStyleSheet(btn_style("ghost", font_size=12))
        self._close_button.setStyleSheet(btn_style("default", font_size=12))
        for frame in self.findChildren(QFrame):
            refresh_glass(frame)
        self.donut.update()
        self.ratio.update()
        self.chart.update()
        self.update()

    def show_animated(self):
        show_window_animated(self)

    def closeEvent(self, ev):
        if close_window_animated(self, None):
            ev.ignore()
        else:
            ev.accept()

    # ---------------------------------------------------------- 拖动
    # ★ v1.9.9：只认头部条（_p_* 三个事件装在 self.head 上），
    #   图表/按钮区不再触发拖动。
    def _p_press(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag = (ev.globalPosition().toPoint()
                          - self.frameGeometry().topLeft())

    def _p_move(self, ev):
        if self._drag is not None and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag)

    def _p_release(self, ev):
        self._drag = None
