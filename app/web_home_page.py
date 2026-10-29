# -*- coding: utf-8 -*-
"""使用 Qt WebEngine 呈现的学习首页，并将操作桥接回原生业务层。"""
import json
import os

import config as cfg
import progress as PG
import theme as T
from PySide6.QtCore import QObject, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

from logger import app_root
from ui_kit import animations_enabled


class _HomeBridge(QObject):
    """仅开放首页必需的只读数据与导航操作。"""

    def __init__(self, page):
        super().__init__(page)
        self.page = page

    @Slot(result=str)
    def dashboardData(self):
        return json.dumps(self.page.dashboard_data(), ensure_ascii=False)

    @Slot(int)
    def navigate(self, page_index):
        self.page.navigate.emit(int(page_index))

    @Slot()
    def startPractice(self):
        data = self.page.dashboard_data()
        qid = data.get("nextQuestionId")
        if qid is not None:
            self.page.practice.emit(qid)

    @Slot()
    def reviewWrong(self):
        if self.page.dashboard_data()["wrongCount"]:
            self.page.review.emit()


class WebHomePage(QWidget):
    """内嵌浏览器首页；题库、计时和教学仍由现有页面及业务模块处理。"""

    async_intro_prepare = True

    navigate = Signal(int)
    practice = Signal(object)
    review = Signal()

    def __init__(self, questions, parent=None):
        super().__init__(parent)
        self.questions = questions
        self._loaded = False
        self._intro_pending = False
        self._ui_scale = 1.0
        self.view = QWebEngineView(self)
        self.view.setObjectName("WebHomeView")
        self.view.setAttribute(Qt.WA_TranslucentBackground, True)
        self.view.setStyleSheet("background: transparent; border: 0;")
        self.view.page().setBackgroundColor(Qt.transparent)
        # 隐藏 WebEngine 的 about:blank 首帧，待本地首页完成主题与动效准备后再显示。
        self.view.hide()

        self.channel = QWebChannel(self.view.page())
        self.bridge = _HomeBridge(self)
        self.channel.registerObject("caac", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self.view.loadFinished.connect(self._on_loaded)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.view)
        path = os.path.join(app_root(), "web", "index.html")
        if not os.path.isfile(path):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "web", "index.html")
        self.view.load(QUrl.fromLocalFile(path))

    def _on_loaded(self, ok):
        self._loaded = bool(ok)
        if not ok:
            self.view.show()
            return

        self.theme_changed()
        self._sync_motion_preference()

        def reveal_prepared_page(_result=None):
            self.view.show()
            if self._intro_pending and self.isVisible() and animations_enabled():
                self._intro_pending = False
                QTimer.singleShot(0, lambda: self.view.page().runJavaScript(
                    "window.playHomeEntrance && window.playHomeEntrance();"))

        # 等主题和动效设置执行后再恢复页面，确保可见的第一帧已经就绪。
        self.view.page().runJavaScript(
            "window.prepareHomeEntrance && window.prepareHomeEntrance();",
            reveal_prepared_page)
        # 页面脚本异常时仍展示本地内容，避免首页永久空白。
        QTimer.singleShot(1500, lambda: self.view.show() if not self.view.isVisible()
                          and self._loaded else None)

    def dashboard_data(self):
        ids = {int(q["id"]) for q in self.questions if q.get("id") is not None}
        done = set(PG._done_ids()) & ids
        wrong = set(PG._wrong()) & {str(i) for i in ids}
        recent = [(qid, rec) for qid, rec in PG.recent(len(ids)) if qid in ids]
        last = cfg.get("last_question_id")
        next_id = last if last in ids else (recent[0][0] if recent else next(iter(sorted(ids)), None))
        regions = len({q.get("region") for q in self.questions if q.get("region")})
        exams = len({q.get("date") for q in self.questions if q.get("date")})
        return {
            "questionCount": len(ids),
            "doneCount": len(done),
            "wrongCount": len(wrong),
            "regionCount": regions,
            "examCount": exams,
            "nextQuestionId": next_id,
            "hasResume": bool(last in ids or recent),
            "hasQuestions": bool(ids),
        }

    def on_show(self):
        self._sync_motion_preference()
        self.questions = getattr(getattr(self, "_window", None), "p_bank", None).all \
            if getattr(getattr(self, "_window", None), "p_bank", None) else self.questions
        if self._loaded:
            payload = json.dumps(self.dashboard_data(), ensure_ascii=False)
            self.view.page().runJavaScript("window.updateDashboard && window.updateDashboard(%s);" % payload)

    def play_intro(self):
        """重新播放首页版式与仪表插画的入场动画。"""
        if not animations_enabled():
            self.cancel_intro()
            return
        if self._loaded:
            self._intro_pending = False
            self.view.page().runJavaScript(
                "window.playHomeEntrance && window.playHomeEntrance();")
        else:
            self._intro_pending = True

    def prepare_intro(self, ready_callback=None):
        """先在网页隐藏态准备动画，再允许 Qt 显示首页。"""
        if not animations_enabled() or not self._loaded:
            return False
        script = "window.prepareHomeEntrance && window.prepareHomeEntrance();"
        if callable(ready_callback):
            self.view.page().runJavaScript(script, lambda _result: ready_callback())
            return True
        self.view.page().runJavaScript(script)
        return False

    def cancel_intro(self):
        self._intro_pending = False
        if self._loaded:
            self.view.page().runJavaScript(
                "window.cancelHomeEntrance && window.cancelHomeEntrance();")

    def _sync_motion_preference(self):
        if self._loaded:
            disabled = "true" if not animations_enabled() else "false"
            self.view.page().runJavaScript(
                "document.documentElement.classList.toggle('motion-off', %s);" % disabled)

    def theme_changed(self, pref=None):
        mode = "dark" if T.mode == "dark" else "light"
        if self._loaded:
            self.view.page().runJavaScript(
                "document.documentElement.dataset.theme = '%s';" % mode)
            if T.app_theme == "anna":
                character_path = T.asset_path(
                    "anna_theme", "assets", "characters", "anna-yanami-dynamic.png")
            else:
                character_path = T.asset_path("chiyo_pack", "home_banner.png")
            character = QUrl.fromLocalFile(character_path).toString()
            assets = {
                css_key: QUrl.fromLocalFile(T.asset_path(
                    "chiyo_pack", f"{name}_{mode}.png")).toString()
                for css_key, name in {
                    "--home-paper-stack": "home_editor_papers",
                    "--home-cover-note": "home_cover_note",
                    "--home-bank-art": "home_card_bank",
                    "--home-timer-art": "home_card_timer",
                    "--home-review-art": "home_card_review",
                }.items()
            }
            assets["--anna-sky"] = QUrl.fromLocalFile(T.asset_path(
                "anna_theme", "assets", "backgrounds",
                f"sky-{mode}.png")).toString()
            assets["--anna-home-reference"] = QUrl.fromLocalFile(T.asset_path(
                "anna_theme", "assets", "reference", mode,
                "home-clean.png")).toString()
            assets["--anna-font"] = QUrl.fromLocalFile(T.asset_path(
                "anna_theme", "assets", "fonts",
                "NotoSansSC-Variable.ttf")).toString()
            for css_key, filename in {
                "--anna-bank-icon": "document.svg",
                "--anna-timer-icon": "timer.svg",
                "--anna-review-icon": "review.svg",
            }.items():
                assets[css_key] = QUrl.fromLocalFile(T.asset_path(
                    "anna_theme", "assets", "icons", "on-primary",
                    filename)).toString()
            for css_key, filename in {
                "--anna-bank-art": "practice.svg",
                "--anna-timer-art": "timer-card.svg",
                "--anna-review-art": "review-card.svg",
            }.items():
                assets[css_key] = QUrl.fromLocalFile(T.asset_path(
                    "anna_theme", "assets", "figures", mode,
                    filename)).toString()
            self.view.page().runJavaScript(
                "window.setApplicationTheme && window.setApplicationTheme(%s,%s,%s,%s);"
                % (json.dumps(T.app_theme), json.dumps(""),
                   json.dumps(character), json.dumps(assets)))
            self.view.page().runJavaScript(
                "document.documentElement.dataset.appTheme = %s;"
                % json.dumps(T.app_theme))
            self.on_show()

    def set_ui_scale(self, scale):
        self._ui_scale = max(0.8, min(1.4, float(scale)))
        self.view.setZoomFactor(self._ui_scale)
