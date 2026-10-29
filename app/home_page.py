# -*- coding: utf-8 -*-
"""学习首页：把现有功能组织成可直接开始的学习路径。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QFrame, QScrollArea, QProgressBar, QSizePolicy)
import theme as T
import config as cfg
import progress as PG
from ui_kit import IconButton


class HomePage(QWidget):
    navigate = Signal(int)
    practice = Signal(object)
    review = Signal()

    def __init__(self, questions, parent=None):
        super().__init__(parent)
        self.questions = questions
        self._buttons = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea, QScrollArea>QWidget>QWidget {background:transparent;border:0;}")
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(30, 26, 30, 24)
        root.setSpacing(18)

        title = QLabel("学习首页")
        title.setObjectName("H1")
        root.addWidget(title)
        sub = QLabel("从一道题开始，把地面站操作练熟。")
        sub.setObjectName("Dim")
        root.addWidget(sub)

        self.welcome = QFrame()
        self.welcome.setObjectName("Welcome")
        wl = QVBoxLayout(self.welcome)
        wl.setContentsMargins(24, 22, 24, 22)
        wl.setSpacing(12)
        self.heading = QLabel("准备好开始练习了吗？")
        self.heading.setWordWrap(True)
        self.heading.setObjectName("WelcomeTitle")
        wl.addWidget(self.heading)
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setObjectName("WelcomeDescription")
        wl.addWidget(self.description)
        actions = QHBoxLayout()
        self.start = self._button("开始第一题", "book", "primary")
        self.start.clicked.connect(self._continue)
        actions.addWidget(self.start)
        browse = self._button("题库题卡", None, "default")
        browse.clicked.connect(lambda: self.navigate.emit(1))
        actions.addWidget(browse)
        actions.addStretch()
        wl.addLayout(actions)
        root.addWidget(self.welcome)

        section = QLabel("第一次使用，按这个顺序")
        section.setObjectName("H2")
        root.addWidget(section)
        rows = [
            ("1", "先了解操作", "认识航点、方位角和航线参数，再开始练习。", "查看教学", "bulb", 2),
            ("2", "导入并练习", "从本机选择 PDF 或图片，校对题面后即可打开工作台。", "题库题卡", "book", 1),
            ("3", "尝试限时模拟", "熟悉操作后，分别设置阅题和作答时长。", "设置计时", "timer", 0),
        ]
        self.rows = []
        for number, name, detail, action, icon, page in rows:
            row = QFrame()
            row.setObjectName("LearningRow")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 12, 0, 12)
            layout.setSpacing(16)
            badge = QLabel(number)
            badge.setObjectName("StepNumber")
            badge.setFixedSize(32, 32)
            badge.setAlignment(Qt.AlignCenter)
            layout.addWidget(badge)
            text = QVBoxLayout()
            text.setSpacing(5)
            label = QLabel(name)
            label.setObjectName("H3")
            text.addWidget(label)
            desc = QLabel(detail)
            desc.setObjectName("Dim")
            desc.setWordWrap(True)
            text.addWidget(desc)
            layout.addLayout(text, 1)
            button = self._button(action, icon, "default")
            button.clicked.connect(lambda checked=False, p=page: self.navigate.emit(p))
            layout.addWidget(button)
            self.rows.append(row)
            root.addWidget(row)

        progress_head = QHBoxLayout()
        label = QLabel("我的练习")
        label.setObjectName("H2")
        progress_head.addWidget(label)
        progress_head.addStretch()
        self.progress_text = QLabel()
        self.progress_text.setObjectName("Dim")
        progress_head.addWidget(self.progress_text)
        root.addLayout(progress_head)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        root.addWidget(self.bar)
        review_row = QHBoxLayout()
        self.review_text = QLabel()
        self.review_text.setWordWrap(True)
        self.review_text.setObjectName("Dim")
        review_row.addWidget(self.review_text, 1)
        self.review_button = self._button("复习错题", "book", "default")
        self.review_button.clicked.connect(self.review.emit)
        review_row.addWidget(self.review_button)
        root.addLayout(review_row)
        hint = QLabel("练习记录不等于答题正确率。学习数据保存在本机。")
        hint.setObjectName("Dim2")
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addStretch()
        self.theme_changed()

    def _button(self, text, icon, kind):
        button = IconButton(text, icon, kind, font_size=13, radius=10)
        button.setMinimumHeight(40)
        button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self._buttons.append(button)
        return button

    def _continue(self):
        self.practice.emit(self._next_id)

    def on_show(self):
        ids = {int(q["id"]) for q in self.questions}
        done = set(PG._done_ids()) & ids
        wrong = set(PG._wrong()) & {str(i) for i in ids}
        self.bar.setRange(0, max(1, len(ids)))
        self.bar.setValue(len(done))
        self.progress_text.setText(f"已练习 {len(done)} / {len(ids)} 题")
        recent = [(qid, rec) for qid, rec in PG.recent(10000) if qid in ids]
        last = cfg.get("last_question_id")
        self._next_id = last if last in ids else (recent[0][0] if recent else next(iter(sorted(ids)), None))
        self.start.setEnabled(self._next_id is not None)
        if self._next_id is None:
            self.heading.setText("先导入题卡，再开始练习")
            self.description.setText("题目保存在本机。进入「题库题卡」可批量选择 PDF 或图片，也可把文件拖入页面。")
            self.start.setText("暂无可练习题卡")
        elif last in ids or recent:
            self.heading.setText("接着上次的练习继续")
            self.description.setText(f"第 {self._next_id} 题已为你准备好。也可以继续导入题卡。")
            self.start.setText(f"继续第 {self._next_id} 题")
        else:
            self.heading.setText("准备好开始练习了吗？")
            self.description.setText(f"本机已导入 {len(ids)} 张题卡。第一次使用可以先看下方教学，再打开题卡练习。")
            self.start.setText("开始第一题")
        self.review_text.setText(f"还有 {len(wrong)} 道错题待复习，重新作答后再检查。" if wrong
                                 else "暂无错题。判题未通过的题目会自动收录在这里。")
        self.review_button.setEnabled(bool(wrong))

    def theme_changed(self, pref=None):
        p = T.P
        self.setStyleSheet(f"""
            QFrame#Welcome {{background:{p['glass']};border:1px solid {p['input_border']};border-radius:14px;}}
            QLabel#WelcomeTitle {{font-size:23px;font-weight:700;color:{p['ink']};}}
            QLabel#WelcomeDescription {{font-size:14px;color:{p['ink2']};}}
            QFrame#LearningRow {{background:transparent;border:0;border-bottom:1px solid {p['input_border']};}}
            QLabel#StepNumber {{background:{p['panel_soft']};color:{p['accent_fg']};border-radius:8px;font-size:15px;font-weight:700;}}
        """)
        for button in self._buttons:
            button.refresh()
        self.on_show()
