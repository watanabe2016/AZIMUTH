# -*- coding: utf-8 -*-
"""错题集窗口 —— 错过哪几题、各错几次、错在哪。

为什么重做（v1.9.6）：
  原来是一个 QMessageBox，把 18 条记录拼成一大段纯文本塞进去 —— 不换行、
  不对齐、不能滚动、看不出层次，五三的原话是「错题集这个界面能不能美化下，
  太丑了」。

现在改成跟「成绩趋势」同一套自绘风格：
  · 每道错题一张卡片：题号 + 场次 + 错过次数徽标 + 错项摘要
  · 可直接点卡片跳到那道题（跳之前会把筛选复位，免得目标被筛没了）
  · 滚动列表，不再砍到 18 条
  · 顶部一条提示，说清"判对了会自动移出"这个机制

★ 仍然是 QFrame 不是 QDialog：主窗口无边框半透明，原生对话框在它上面会黑底。
★ 引用必须被调用方持有（bank_page._wrong_dlg），否则无父窗口的 QWidget
  会被 Python GC 静默回收 —— 判题窗当初就踩过"闪一下没了"。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QWidget, QSizePolicy)

import theme as T
import progress as PG
from ui_kit import (glass, glass_style, refresh_glass, btn_style, IconButton,
                    attach_shadow, Icons, show_window_animated,
                    close_window_animated)


class WrongCard(QFrame):
    """一道错题的卡片。点它跳进那道题。"""

    picked = Signal(int)

    def __init__(self, qid, rec, source="", parent=None):
        super().__init__(parent)
        self.qid = int(qid)
        self._source_text = source or "（不在当前题库）"
        self._detail_text = (rec.get("detail") or "").strip()
        self._last_text = (rec.get("last") or "").strip()
        self._wrong_count = int(rec.get("n", 0) or 0)
        self.setObjectName("WrongCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"QFrame#WrongCard{{background:{T.P['panel_soft']};"
            f" border:1px solid {T.P['glass_soft_edge']};"
            f" border-radius:12px;}}"
            f"QFrame#WrongCard:hover{{background:{T.P['panel_hover']};"
            f" border:1px solid {T.C_RED};}}")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(13, 10, 13, 10)
        lay.setSpacing(11)

        # 左侧：题号（主色大字）
        no = QLabel("#%d" % self.qid)
        self._number_label = no
        no.setFixedWidth(52)
        no.setStyleSheet(f"font-size:14px; font-weight:800; color:{T.C_BLUE};"
                         f"background:transparent;")
        no.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lay.addWidget(no)

        # 中间：场次 + 错项摘要
        mid = QVBoxLayout()
        mid.setSpacing(2)
        src = QLabel(self._source_text)
        self._source_label = src
        src.setStyleSheet(f"font-size:12px; font-weight:700; color:{T.P['ink']};"
                          f"background:transparent;")
        mid.addWidget(src)
        sub = QLabel(self._detail_text or "（判错时没记下具体错项，可点进去重判一次）")
        self._detail_label = sub
        sub.setWordWrap(True)
        sub.setStyleSheet(f"font-size:11px; color:{T.P['ink2']};"
                          f"background:transparent;")
        mid.addWidget(sub)
        self._time_label = None
        if self._last_text:
            t = QLabel("最近 " + self._last_text)
            self._time_label = t
            t.setStyleSheet(f"font-size:10px; color:{T.P['ink3']};"
                            f"background:transparent;")
            mid.addWidget(t)
        lay.addLayout(mid, 1)

        # 右侧：错过次数徽标
        badge = QLabel("错 %d 次" % self._wrong_count)
        self._badge = badge
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            f"font-size:11px; font-weight:700; color:#ffffff;"
            f"background:{T.C_RED}; border-radius:9px; padding:2px 9px;")
        badge.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        lay.addWidget(badge, 0, Qt.AlignVCenter)

        arrow = QLabel()
        self._arrow = arrow
        arrow.setPixmap(Icons.chevron(T.P["ink3"], size=14))
        arrow.setStyleSheet("background:transparent;")
        lay.addWidget(arrow, 0, Qt.AlignVCenter)
        self.theme_changed()

    def theme_changed(self):
        self.setStyleSheet(
            f"QFrame#WrongCard{{background:{T.P['panel_soft']};"
            f" border:1px solid {T.P['glass_soft_edge']};"
            f" border-radius:12px;}}"
            f"QFrame#WrongCard:hover{{background:{T.P['panel_hover']};"
            f" border:1px solid {T.C_RED};}}")
        self._number_label.setStyleSheet(
            f"font-size:14px; font-weight:800; color:{T.C_BLUE}; background:transparent;")
        self._source_label.setStyleSheet(
            f"font-size:12px; font-weight:700; color:{T.P['ink']}; background:transparent;")
        self._detail_label.setStyleSheet(
            f"font-size:11px; color:{T.P['ink2']}; background:transparent;")
        if self._time_label is not None:
            self._time_label.setStyleSheet(
                f"font-size:10px; color:{T.P['ink3']}; background:transparent;")
        self._badge.setStyleSheet(
            f"font-size:11px; font-weight:700; color:#ffffff; background:{T.C_RED};"
            "border-radius:9px; padding:2px 9px;")
        self._arrow.setPixmap(Icons.chevron(T.P["ink3"], size=14))

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.picked.emit(self.qid)


class WrongSetDialog(QFrame):
    """错题集窗口。

    ★ v1.9.9：改成**独立顶层窗口**（``Qt.Window | FramelessWindowHint`` +
      半透明背景），结构照抄参考答案浮层（外层透明 + 内层玻璃卡片挂阴影）。

      为什么要改 —— v1.9.8 的四个症状其实是**同一个根因**：
      旧版是带 parent 的普通 QFrame（= 主窗口的**子控件**），而 place_near
      给的是**屏幕全局坐标**，子控件的 move() 却按**父窗口局部坐标**解释 →
      ① 弹出位置错到右下角；② 超出父窗口的部分被裁掉；
      ③ 子控件拖不出父窗口边界；④ 每次拖动 move 都触发半透明主窗口
      全量重绘 → 卡顿。顶层窗口 move 是窗口系统级操作，四个一起解决。

      参考答案浮层自 v1.9.7 就是这个结构，从未黑底 —— 旧注释里
      "原生对话框会黑底"怕的是**系统原生对话框**，不是自绘顶层窗口。
    """

    jump_to = Signal(int)          # 请求跳到某题（由题库页处理，含筛选复位）

    def __init__(self, rows, source_of=None, total_all=0, parent=None):
        """
        rows       : [(qid, {"n","last","detail"}), ...]，已排好序
        source_of  : callable(qid) -> 场次字符串
        total_all  : 题库总题数（用于说明"其中几题不在当前题库"）
        """
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setWindowTitle("错题集")
        self.setMinimumWidth(660)
        self.setMinimumHeight(520)
        self._drag = None
        self._rows = list(rows)
        self._source_of = source_of or (lambda qid: "")
        self._total_all = total_all
        self._window_motion = None
        self._window_closing = False

        # 外层透明容器 + 内层玻璃卡片（阴影挂卡片，不挂窗体自身 ——
        # QGraphicsDropShadowEffect 会让同一控件的 QSS 圆角变方，Toast 踩过）
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
        # ★ 只认头部拖，不再整窗可拖 —— 列表区拖动会跟卡片点击打架，
        #   而且在滚动区上按下就拖窗口，手感是"卡顿 + 误触"。
        self.head = QWidget()
        self.head.setFixedHeight(40)
        self.head.setStyleSheet("background:transparent;")
        head = QHBoxLayout(self.head)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(9)
        t = QLabel("错题集")
        self._title = t
        t.setObjectName("H2")
        t.setStyleSheet(f"font-size:18px; font-weight:800; color:{T.P['ink']};"
                        f"background:transparent;")
        self.sub = QLabel("")
        self.sub.setStyleSheet(f"font-size:12px; color:{T.P['ink3']};"
                               f"background:transparent;")
        head.addWidget(t)
        head.addWidget(self.sub)
        head.addStretch()

        self.b_filter = QPushButton("只看错题")
        self.b_filter.setMinimumHeight(30)
        self.b_filter.setStyleSheet(btn_style("primary", font_size=12))
        self.b_filter.clicked.connect(self._do_filter)

        self.b_clear = QPushButton("清空")
        self.b_clear.setMinimumHeight(30)
        self.b_clear.setStyleSheet(btn_style("ghost", font_size=12))

        b_close = QPushButton("关闭")
        self._close_button = b_close
        b_close.setMinimumHeight(30)
        b_close.setStyleSheet(btn_style("default", font_size=12))
        b_close.clicked.connect(self.close)

        head.addWidget(self.b_filter)
        head.addWidget(self.b_clear)
        head.addWidget(b_close)
        root.addWidget(self.head)
        # 头部文字放行鼠标事件 —— 拖动把手装在 head 容器上，
        # 文字不吃事件才能"按着标题就拖得动"
        for w in (t, self.sub):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.head.mousePressEvent = self._p_press
        self.head.mouseMoveEvent = self._p_move
        self.head.mouseReleaseEvent = self._p_release

        # ---- 机制说明 ----
        tip = QLabel(
            "判错自动收进来，判对了自动移出。点任意一条可跳到那道题重做。")
        tip.setWordWrap(True)
        self._tip = tip
        tip.setStyleSheet(f"font-size:11px; color:{T.P['ink3']};"
                          f"background:transparent;")
        root.addWidget(tip)

        # ---- 列表（可滚动） ----
        box = glass()
        self._list_box = box
        bl = QVBoxLayout(box)
        bl.setContentsMargins(11, 11, 11, 11)
        bl.setSpacing(8)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet(
            f"QScrollArea{{background:transparent; border:none;}}"
            f"QScrollArea > QWidget > QWidget{{background:transparent;}}")
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        self.il = QVBoxLayout(inner)
        self.il.setContentsMargins(0, 0, 0, 0)
        self.il.setSpacing(8)
        self.scroll.setWidget(inner)
        bl.addWidget(self.scroll)
        self.inner = inner
        self._build_cards()
        root.addWidget(box, 1)

        self._refresh_head()
        self.theme_changed()

    # ---------------------------------------------------------- 内部
    def _build_cards(self):
        """清空并重建卡片。

        ★ 必须 `setParent(None)` **立刻**摘掉旧控件，不能只靠 `deleteLater()` ——
          deleteLater 是延迟到事件循环空闲才删，而此时新内容已经加进同一个布局，
          旧控件（比如空态那段"错题集是空的🎉"）会**继续画在新卡片下面**，
          截图里看到的就是"空态文案叠在错题列表上"。
        """
        while self.il.count():
            it = self.il.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if not self._rows:
            empty = QLabel(
                "错题集是空的 🎉\n\n"
                "在「题卡工作台」里点「判题」，判出来有错项的题会自动收进这里。")
            empty.setAlignment(Qt.AlignCenter)
            empty.setObjectName("WrongEmptyState")
            empty.setStyleSheet(f"font-size:13px; color:{T.P['ink3']};"
                                f"background:transparent; padding:40px 0;")
            self.il.addWidget(empty)
            self.il.addStretch()
            return
        for qid, rec in self._rows:
            c = WrongCard(qid, rec, self._source_of(qid))
            c.picked.connect(self.jump_to.emit)
            self.il.addWidget(c)
        self.il.addStretch()

    def _refresh_head(self):
        n = len(self._rows)
        miss = sum(1 for qid, _ in self._rows if not self._source_of(qid))
        txt = "共 %d 道（错得多的排前面）" % n
        if miss:
            txt += " · 其中 %d 道不在当前题库" % miss
        self.sub.setText(txt)
        self.b_filter.setEnabled(n > 0)

    def set_rows(self, rows):
        self._rows = list(rows)
        self._build_cards()
        self._refresh_head()

    def attach_clear(self, cb):
        self.b_clear.clicked.connect(cb)

    def theme_changed(self, pref=None):
        refresh_glass(self)
        for frame in self.findChildren(QFrame):
            refresh_glass(frame)
        self._title.setStyleSheet(
            f"font-size:18px; font-weight:800; color:{T.P['ink']}; background:transparent;")
        self.sub.setStyleSheet(
            f"font-size:12px; color:{T.P['ink3']}; background:transparent;")
        self._tip.setStyleSheet(
            f"font-size:11px; color:{T.P['ink3']}; background:transparent;")
        self.b_filter.setStyleSheet(btn_style("primary", font_size=12))
        self.b_clear.setStyleSheet(btn_style("ghost", font_size=12))
        self._close_button.setStyleSheet(btn_style("default", font_size=12))
        for card in self.findChildren(WrongCard):
            card.theme_changed()
        for label in self.findChildren(QLabel):
            if label.objectName() == "WrongEmptyState":
                label.setStyleSheet(
                    f"font-size:13px; color:{T.P['ink3']}; background:transparent; padding:40px 0;")
        self.update()

    def show_animated(self):
        show_window_animated(self)

    def closeEvent(self, ev):
        if close_window_animated(self, None):
            ev.ignore()
        else:
            ev.accept()

    def _do_filter(self):
        if self._rows:
            self.jump_to.emit(-1)      # -1 = 只要筛，不跳题

    # ---------------------------------------------------------- 拖动
    # ★ v1.9.9：只认头部条（_p_* 三个事件已装到 self.head 上）——
    #   整窗可拖的旧版在列表上按住一滑就把窗口拖走了，跟"点卡片跳题"打架。
    def _p_press(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag = (ev.globalPosition().toPoint()
                          - self.frameGeometry().topLeft())

    def _p_move(self, ev):
        if self._drag is not None and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag)

    def _p_release(self, ev):
        self._drag = None
