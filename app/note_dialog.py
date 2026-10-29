# -*- coding: utf-8 -*-
"""备忘录弹窗 —— 每道题一条文字笔记 + 若干张截图。

为什么单独一个弹窗而不是塞进题库页：详情区已经被「文字 + 题卡预览」占满，
再嵌一块编辑区会挤压题卡。独立弹窗里文字区宽、截图能铺一排，随手记也顺手。
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                               QTextEdit, QScrollArea, QWidget)

import theme as T
import notes as NT
from logger import get_logger
from ui_kit import btn_style, IconButton
from shot_tool import ShotOverlay, ShotViewer

log = get_logger()

THUMB_H = 92        # 缩略图高度（像素）
MIN_W = 520         # 弹窗最小宽
MIN_H = 480         # 弹窗最小高


class NoteDialog(QFrame):
    """一题的备忘录：文字笔记 + 截图。随题切换内容，常驻单例。"""

    note_changed = Signal()   # 笔记/截图有增删，通知列表刷新标记

    def __init__(self, qid, title="", scale=1.0):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.qid = int(qid)
        self._scale = scale or 1.0
        self._drag = None
        self._overlay = None
        self._viewer = None
        self._thumb_box = None
        self._thumb_lay = None
        # 自动保存防抖：连续打字不频繁落盘，停 600ms 再写
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save)
        self._build(title)
        self._load()

    # ------------------------------------------------------------ UI
    def _build(self, title):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        card = QFrame()
        card.setObjectName("NoteCard")
        card.setStyleSheet(
            f"QFrame#NoteCard{{background:{T.P['shell']};"
            f"border-radius:16px;border:1px solid {T.P['glass_soft_edge']};}}")
        outer.addWidget(card)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 14, 18, 16)
        cl.setSpacing(10)

        # 标题栏
        bar = QHBoxLayout()
        self.cap = QLabel(title or "备忘录")
        self.cap.setStyleSheet(
            f"font-size:15px;font-weight:800;color:{T.P['ink']};"
            f"background:transparent;")
        bar.addWidget(self.cap)
        bar.addStretch()
        tip = QLabel("ESC 关闭 · 文字自动保存")
        tip.setStyleSheet(f"font-size:11px;color:{T.P['ink3']};"
                          f"background:transparent;")
        bar.addWidget(tip)
        cl.addLayout(bar)

        # 文字笔记
        self.ed = QTextEdit()
        self.ed.setPlaceholderText("写点心得：这题的坑、航线怎么规划、易错点…")
        self.ed.setMinimumHeight(120)
        self.ed.textChanged.connect(self._on_text)
        self.ed.setStyleSheet(
            f"QTextEdit{{background:{T.P['panel_soft']};"
            f"color:{T.P['ink']};border:1px solid {T.P['glass_soft_edge']};"
            f"border-radius:12px;padding:10px;font-size:14px;}}"
            f"QTextEdit:focus{{border:1px solid {T.C_BLUE};}}")
        cl.addWidget(self.ed, 1)

        # 截图区
        sh = QHBoxLayout()
        sl = QLabel("截图")
        sl.setStyleSheet(f"font-size:13px;font-weight:700;"
                         f"color:{T.P['accent_fg']};background:transparent;")
        sh.addWidget(sl)
        sh.addStretch()
        self.b_shot = IconButton("截图", "camera", "primary",
                                 font_size=12, radius=10)
        self.b_shot.setMinimumHeight(32)
        self.b_shot.setToolTip("框选屏幕任意区域，存为本题截图")
        self.b_shot.clicked.connect(self._take_shot)
        sh.addWidget(self.b_shot)
        cl.addLayout(sh)

        # 缩略图滚动区
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(THUMB_H + 46)
        self.scroll.setStyleSheet(
            f"QScrollArea{{background:{T.P['panel_soft']};"
            f"border:1px solid {T.P['glass_soft_edge']};border-radius:12px;}}"
            f"QScrollArea > QWidget > QWidget{{background:transparent;}}"
            f"QScrollBar:horizontal{{height:8px;background:transparent;}}"
            f"QScrollBar::handle:horizontal{{background:{T.P['scroll']};"
            f"border-radius:4px;min-width:30px;}}")
        self._thumb_box = QWidget()
        self._thumb_lay = QHBoxLayout(self._thumb_box)
        self._thumb_lay.setContentsMargins(10, 10, 10, 10)
        self._thumb_lay.setSpacing(10)
        self._thumb_lay.addStretch()
        self.scroll.setWidget(self._thumb_box)
        cl.addWidget(self.scroll)

        # 标题栏可拖动（把 mousePress 绑到 card 上）
        card.mousePressEvent = self._press
        card.mouseMoveEvent = self._move
        card.mouseReleaseEvent = self._release

        self.resize(max(MIN_W, int(560 * self._scale)),
                    max(MIN_H, int(520 * self._scale)))
        self._center()

    # ------------------------------------------------------------ 数据
    def _load(self):
        self.ed.blockSignals(True)
        self.ed.setPlainText(NT.get_text(self.qid))
        self.ed.blockSignals(False)
        self._rebuild_thumbs()

    def set_question(self, qid, title):
        self._save()                      # 切题前先把上一题写盘
        self.qid = int(qid)
        self.cap.setText(title or "备忘录")
        self._load()

    def _on_text(self):
        self._save_timer.start()

    def _save(self):
        NT.set_text(self.qid, self.ed.toPlainText())

    def _rebuild_thumbs(self):
        # 清空旧缩略图（保留末尾的 stretch）
        while self._thumb_lay.count() > 1:
            it = self._thumb_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        shots = NT.shots(self.qid)
        if not shots:
            empty = QLabel("还没有截图，点右上「截图」框选记录")
            empty.setStyleSheet(
                f"font-size:12px;color:{T.P['ink3']};background:transparent;")
            self._thumb_lay.insertWidget(0, empty)
            return
        for s in shots:
            self._thumb_lay.insertWidget(self._thumb_lay.count() - 1,
                                         self._thumb(s))

    def _thumb(self, s):
        """一张缩略图：点开看大图，下方一个删除按钮"""
        box = QFrame()
        box.setFixedWidth(THUMB_H + 8)
        box.setStyleSheet("background:transparent;")
        vl = QVBoxLayout(box)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(3)

        pm = QPixmap(s["path"])
        if pm.isNull():
            pm = QPixmap(THUMB_H, THUMB_H)
            pm.fill(Qt.transparent)
        else:
            pm = pm.scaledToHeight(THUMB_H, Qt.SmoothTransformation)

        lb = QLabel()
        lb.setAlignment(Qt.AlignCenter)
        lb.setFixedSize(THUMB_H + 8, THUMB_H)
        lb.setPixmap(pm)
        lb.setCursor(Qt.PointingHandCursor)
        lb.setStyleSheet("background:transparent;")
        lb.setToolTip(s["t"] + "\n点击查看大图")
        lb.mousePressEvent = lambda e, p=s["path"], t=s["t"]: self._view(p, t)
        vl.addWidget(lb)

        db = QLabel("删除")
        db.setAlignment(Qt.AlignCenter)
        db.setCursor(Qt.PointingHandCursor)
        db.setStyleSheet(
            f"font-size:11px;color:{T.P['ink3']};background:transparent;")
        db.mousePressEvent = lambda e, f=s["file"]: self._del(f)
        vl.addWidget(db)
        return box

    def _view(self, path, title):
        self._viewer = ShotViewer(path, title=title)
        self._viewer.show()

    def _del(self, fname):
        if NT.del_shot(self.qid, fname):
            self._rebuild_thumbs()
            self.note_changed.emit()
            log.info("删除备忘录截图 #%d %s", self.qid, fname)

    # ------------------------------------------------------------ 截图
    def _take_shot(self):
        # 先藏起自己，否则截图会把弹窗一起框进去
        self.hide()
        self._overlay = ShotOverlay()
        self._overlay.picked.connect(self._on_picked)
        # 无论框选成功还是按 ESC 取消，overlay 销毁后都要把弹窗唤回来
        self._overlay.destroyed.connect(self._reappear)
        self._overlay.show()

    def _on_picked(self, pm):
        if NT.add_shot(self.qid, pm):
            self._rebuild_thumbs()
            self.note_changed.emit()

    def _reappear(self):
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ 窗口
    def _center(self):
        from PySide6.QtGui import QGuiApplication
        scr = QGuiApplication.primaryScreen()
        if not scr:
            return
        a = scr.availableGeometry()
        self.move(int(a.x() + (a.width() - self.width()) / 2),
                  int(a.y() + (a.height() - self.height()) / 2))

    def _press(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _move(self, ev):
        if self._drag is not None:
            self.move(ev.globalPosition().toPoint() - self._drag)

    def _release(self, ev):
        self._drag = None

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.close()

    def closeEvent(self, ev):
        self._save()
        self.note_changed.emit()
        super().closeEvent(ev)

    def theme_changed(self, pref=None):
        self._rebuild_thumbs()
