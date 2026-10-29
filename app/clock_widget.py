# -*- coding: utf-8 -*-
"""桌面倒计时悬浮时钟

设计要点
  · 直接铺整张「桌面时钟」素材，文字画进白色气泡内部
    为什么不把气泡 / 角色切片再拼：切片是按固定百分比硬裁的
    （左 62% / 上 62%），切出来的位置和比例跟原图对不上，
    拼回去文字必然飘出气泡 —— 实测就是这么飘的。
    铺整图 + 按原图比例定位文字，一次到位。
  · 字体用得意黑（随包分发，不依赖系统安装）
  · 可自由缩放（Ctrl+滚轮 / 右下角拖拽 / 设置页滑块）
  · 始终置顶，位置记忆，可在设置里开关
"""
import math
import os

from PySide6.QtCore import (Qt, QPoint, QPointF, QRectF, QSize, QTimer,
                            QPropertyAnimation, QEasingCurve, Signal, Property)
from PySide6.QtGui import (QColor, QPainter, QPen, QBrush, QPixmap, QFont,
                           QFontMetrics, QPainterPath, QLinearGradient,
                           QCursor, QIcon)
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout,
                               QLabel, QPushButton, QGraphicsDropShadowEffect,
                               QApplication, QGraphicsOpacityEffect,
                               QStylePainter, QStyleOptionButton, QStyle)

import theme as T
import config as cfg
from ui_kit import Icons, add_shadow, glass, glass_style, btn_style, animations_enabled
from motion_runtime import motion_page

ASSET = T.asset_path


class ClockActionButton(QPushButton):
    """悬浮时钟的圆形图标按钮，支持错峰显隐。"""

    size_changed = Signal()

    def __init__(self, label, icon, tooltip, parent=None):
        super().__init__("", parent)
        self._label = label
        self._icon = icon
        self._scale = 1.0
        self._reveal_progress = 0.0
        self._reveal_offset = 8.0
        self._reveal_effect = QGraphicsOpacityEffect(self)
        self._reveal_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._reveal_effect)
        self._reveal_motion = QPropertyAnimation(self, b"revealProgress", self)
        self._reveal_motion.setDuration(155)
        self._reveal_motion.setEasingCurve(QEasingCurve.OutCubic)
        self.setIcon(QIcon(icon))
        self.setAccessibleName(label)
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.set_scale(1.0)

    def _get_reveal_progress(self):
        return self._reveal_progress

    def _set_reveal_progress(self, value):
        self._reveal_progress = max(0.0, min(1.0, float(value)))
        self._reveal_effect.setOpacity(self._reveal_progress)
        self._reveal_offset = 8.0 * self._scale * (1.0 - self._reveal_progress)
        self.update()

    revealProgress = Property(float, _get_reveal_progress, _set_reveal_progress)

    def animate_reveal(self, visible):
        """从当前进度继续或反向，保证快速进出时动画不跳回起点。"""
        target = 1.0 if visible else 0.0
        self._reveal_motion.stop()
        if not animations_enabled():
            self._set_reveal_progress(target)
            return
        if abs(self._reveal_progress - target) < 0.001:
            return
        self._reveal_motion.setStartValue(self._reveal_progress)
        self._reveal_motion.setEndValue(target)
        self._reveal_motion.start()

    def paintEvent(self, event):
        if self._reveal_offset <= 0.1:
            super().paintEvent(event)
            return
        painter = QStylePainter(self)
        painter.translate(self._reveal_offset, 0)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter.drawControl(QStyle.CE_PushButton, option)

    def set_label(self, label):
        self._label = label
        self.setAccessibleName(label)
        self.setText("")

    def set_action_icon(self, icon):
        self._icon = icon
        self.setIcon(QIcon(icon))

    def set_scale(self, scale):
        self._scale = max(0.45, float(scale))
        self._reveal_offset = 8.0 * self._scale * (1.0 - self._reveal_progress)
        diameter = max(24, int(round(30 * self._scale)))
        icon_size = max(11, int(round(14 * self._scale)))
        self.setIconSize(QSize(icon_size, icon_size))
        self.setFixedSize(diameter, diameter)
        self.size_changed.emit()


@motion_page
class ClockBubble(QWidget):
    """桌面悬浮倒计时：整张素材铺底，倒计时写进白色气泡里。

    文字位置由 BUBBLE 常量控制 —— 它是白色气泡在素材图里的相对区域
    （按 clock.png 986x1000 量的）。改素材就得同步改这四个数。
    """

    BASE_W, BASE_H = 375, 380
    MIN_SCALE = 0.85
    # 白色气泡在素材中的相对位置：(x, y, w, h)，单位是相对整图的比例
    BUBBLE = (0.018, 0.022, 0.511, 0.330)
    CHIYO_BUBBLE = (0.045, 0.070, 0.490, 0.340)
    ANNA_BUBBLE = (0.105, 0.125, 0.425, 0.270)
    timer_action = Signal(str)

    def __init__(self, parent=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool |
                         Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("倒计时时钟")
        self.setToolTip("鼠标移到人物上显示圆形控制按钮；按住空白处拖动可移动")
        self.scale = float(cfg.get("clock_scale", 1.0) or 1.0)
        self.hours = 0
        self.minutes = 0
        self.seconds = 0
        self.title = "作答倒计时"
        self.running = False
        self._pct = 0.0

        self.art = None
        self._art_theme = None
        self._load_art()
        self.phase = "idle"
        self.read_total = int(cfg.get("read_sec", 60))

        self._press = None
        self._drag_offset = None
        self._dragging = False
        self._resize = None
        self.setMouseTracking(True)
        self._build_controls()
        self._apply_size()

    def _build_controls(self):
        """在人物左下侧竖排四个圆形快捷操作。"""
        self._controls_bar = QWidget(self)
        self._controls_bar.setAttribute(Qt.WA_TranslucentBackground, True)
        self._controls_bar.setAttribute(Qt.WA_NoSystemBackground, True)
        self._controls_bar.setStyleSheet("background:transparent;border:none;")

        self.btn_toggle = ClockActionButton(
            "开始", self._action_icon("play"), "开始或继续限时模拟计时",
            self._controls_bar)
        self.btn_toggle.clicked.connect(lambda: self.timer_action.emit("toggle"))
        self.btn_reset = ClockActionButton(
            "重置", Icons.reset(T.P["accent_fg"], 18), "重置当前倒计时",
            self._controls_bar)
        self.btn_reset.clicked.connect(lambda: self.timer_action.emit("reset"))
        self.btn_custom = ClockActionButton(
            "自定义", Icons.timer(T.P["accent_fg"], 18), "设置自定义倒计时",
            self._controls_bar)
        self.btn_custom.clicked.connect(lambda: self.timer_action.emit("custom"))
        self.btn_stage = ClockActionButton(
            "换人物", self._action_icon("person"), "切换桌面时钟人物",
            self._controls_bar)
        self.btn_stage.clicked.connect(lambda: self.timer_action.emit("cycle_character"))
        self._controls_buttons = (self.btn_toggle, self.btn_reset,
                                  self.btn_custom, self.btn_stage)
        for button in self._controls_buttons:
            button.size_changed.connect(self._layout_controls)
        self._controls_target_visible = False
        self._controls_motion_id = 0
        self._controls_stagger_ms = 42
        self._controls_reveal_ms = 155
        self._controls_hover_timer = QTimer(self)
        self._controls_hover_timer.setInterval(45)
        self._controls_hover_timer.timeout.connect(self._refresh_control_hover)
        self._controls_hide_timer = QTimer(self)
        self._controls_hide_timer.setSingleShot(True)
        self._controls_hide_timer.setInterval(1000)
        self._controls_hide_timer.timeout.connect(self._hide_controls_after_grace)
        self._controls_bar.hide()

    def _action_icon(self, kind):
        """绘制桌面时钟的小型线框图标，避免依赖系统字体字形。"""
        if kind == "reset":
            return Icons.reset(T.P["accent_fg"], 18)
        if kind == "custom":
            return Icons.timer(T.P["accent_fg"], 18)
        pm = QPixmap(24, 24)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(T.P["accent_fg"]), 2.0, Qt.SolidLine,
                   Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if kind == "person":
            painter.drawEllipse(QPointF(12, 7), 3.3, 3.3)
            path = QPainterPath()
            path.moveTo(4.5, 20)
            path.cubicTo(5.0, 14.0, 19.0, 14.0, 19.5, 20)
            painter.drawPath(path)
        elif kind == "pause":
            painter.drawRoundedRect(QRectF(6.0, 4.5, 4.1, 15.0), 1.2, 1.2)
            painter.drawRoundedRect(QRectF(13.9, 4.5, 4.1, 15.0), 1.2, 1.2)
        else:
            path = QPainterPath()
            path.moveTo(7.0, 4.5)
            path.lineTo(19.0, 12.0)
            path.lineTo(7.0, 19.5)
            path.closeSubpath()
            painter.setBrush(QColor(T.P["accent_fg"]))
            painter.drawPath(path)
        painter.end()
        return pm

    def _style_controls(self):
        if not hasattr(self, "_controls_bar"):
            return
        accent = T.P["accent_fg"]
        border = T.P["glass_soft_edge"]
        # 使用不透明的主题表面色，避免深色主题的半透明输入底色在悬停时透出桌面。
        glass_color = QColor(T.P["glass"])
        bg = glass_color.name() if glass_color.isValid() else "#FFFFFF"
        fg = T.P["ink"]
        for button in (self.btn_toggle, self.btn_reset,
                       self.btn_custom, self.btn_stage):
            # 先更新尺寸，再计算圆角，避免缩小时沿用旧半径而变成方形。
            button.set_scale(self.scale)
            radius = max(12, button.width() // 2)
            style = (
                f"QPushButton{{background:{bg};color:{fg};border:1px solid {border};"
                f"border-radius:{radius}px;padding:0;}}"
                f"QPushButton:hover{{border:2px solid {accent};background:{bg};}}"
                f"QPushButton:pressed{{border:2px solid {accent};background:{bg};}}"
                f"QPushButton:focus{{border-color:{accent};}}"
            )
            button.setStyleSheet(style)
        self._layout_controls()

    def _layout_controls(self):
        if not hasattr(self, "_controls_bar"):
            return
        diameter = self.btn_toggle.width()
        # 小尺寸按钮沿时钟左下侧竖排，避开气泡文字和角色面部。
        gap = max(5.0, 7.0 * self.scale)
        character_gap = max(1, round(1.5 * self.scale))
        margin_x = round(self._character_hover_rect().left()
                         - diameter - character_gap)
        margin_x = min(max(0, margin_x), max(0, self.width() - diameter))
        margin_bottom = max(6, round(10 * self.scale))
        total_height = (len(self._controls_buttons) * diameter
                        + (len(self._controls_buttons) - 1) * round(gap))
        left = min(margin_x, max(0, self.width() - diameter))
        top = max(0, self.height() - margin_bottom - total_height)
        self._controls_bar.setGeometry(left, top, diameter, total_height)

        # 从上到下为换人物、自定义、重置、开始/暂停，保持常用操作靠下。
        display_order = (self.btn_stage, self.btn_custom,
                         self.btn_reset, self.btn_toggle)
        step = diameter + round(gap)
        for index, button in enumerate(display_order):
            button.move(0, index * step)

    def _show_controls(self):
        self._set_controls_visible(True)

    def _character_hover_rect(self):
        """按素材的角色区域判断悬停，不把气泡和透明留白当成人物。"""
        art_rect = self._art_rect()
        regions = {
            "default": (0.31, 0.29, 0.69, 0.71),
            "chiyo": (0.31, 0.35, 0.69, 0.65),
            "anna": (0.31, 0.34, 0.69, 0.66),
        }
        x, y, width, height = regions.get(self._art_theme, regions["default"])
        return QRectF(art_rect.x() + art_rect.width() * x,
                      art_rect.y() + art_rect.height() * y,
                      art_rect.width() * width,
                      art_rect.height() * height)

    def _controls_hovered(self, point=None):
        local = self.mapFromGlobal(point or QCursor.pos())
        if self._character_hover_rect().contains(local):
            return True
        if self._controls_bar.isVisible():
            return any(button.isVisible()
                       and button._reveal_progress > 0.05
                       and button.geometry().translated(
                           self._controls_bar.pos()).contains(local)
                       for button in self._controls_buttons)
        return any(button.hasFocus() for button in self._controls_buttons)

    def _refresh_control_hover(self):
        if self._controls_hovered():
            self._controls_hide_timer.stop()
            self._set_controls_visible(True)
        elif self._controls_target_visible and not self._controls_hide_timer.isActive():
            self._controls_hide_timer.start()

    def _hide_controls_after_grace(self):
        """鼠标离开人物和按钮区域一秒后，再启动错峰退场。"""
        if self._controls_hovered():
            self._refresh_control_hover()
            return
        self._set_controls_visible(False)

    def _set_controls_visible(self, visible):
        visible = bool(visible)
        self._controls_hide_timer.stop()
        if visible == self._controls_target_visible:
            return
        self._controls_target_visible = visible
        self._controls_motion_id += 1
        motion_id = self._controls_motion_id
        if visible:
            self._layout_controls()
            self._controls_bar.show()
            self._controls_bar.raise_()
            self._controls_hover_timer.start()
            order = self._controls_buttons
        else:
            order = tuple(reversed(self._controls_buttons))

        if not animations_enabled():
            for button in self._controls_buttons:
                button.animate_reveal(visible)
            if not visible:
                self._controls_bar.hide()
                self._controls_hover_timer.stop()
            return

        for index, button in enumerate(order):
            delay = index * self._controls_stagger_ms

            def animate_button(target=button, expected=motion_id,
                               show=visible):
                if (expected != self._controls_motion_id
                        or show != self._controls_target_visible):
                    return
                target.animate_reveal(show)

            QTimer.singleShot(delay, animate_button)

        if not visible:
            delay = ((len(order) - 1) * self._controls_stagger_ms
                     + self._controls_reveal_ms + 20)

            def finish_hide(expected=motion_id):
                if expected != self._controls_motion_id:
                    return
                if self._controls_hovered():
                    self._set_controls_visible(True)
                    return
                self._controls_bar.hide()
                self._controls_hover_timer.stop()

            QTimer.singleShot(delay, finish_hide)

    def enterEvent(self, ev):
        super().enterEvent(ev)
        self._refresh_control_hover()

    def leaveEvent(self, ev):
        QTimer.singleShot(0, self._refresh_control_hover)
        super().leaveEvent(ev)

    # ---------------------------------------------------- 尺寸
    def _load_art(self):
        """载入已解锁且由用户选择的时钟人物。"""
        from skin_manager import valid_clock_skin
        key = valid_clock_skin(cfg.get("clock_skin", "default"))
        if not isinstance(key, str):
            key = "default"
        if key == self._art_theme:
            return
        self._art_theme = key
        path = ""
        theme_id = key.split(":", 1)[1] if key.startswith("skin:") else key
        try:
            from skin_manager import clock_path
            path = clock_path(theme_id)
        except (ImportError, OSError, ValueError):
            path = ""
        if not path:
            filename = {"chiyo": "clock_chiyo.png",
                        "anna": "clock_anna.png"}.get(key, "clock.png")
            path = ASSET(filename)
        self.art = QPixmap(path) if os.path.exists(path) else None
        if self.art is not None and self.art.isNull() and key != "default":
            fallback = ASSET("clock.png")
            self.art = QPixmap(fallback) if os.path.exists(fallback) else None

    def theme_changed(self):
        self._style_controls()
        self.update()

    def set_skin(self, skin):
        from skin_manager import valid_clock_skin
        skin = valid_clock_skin(skin)
        cfg.set_("clock_skin", skin)
        self._art_theme = None
        self._load_art()
        self._style_controls()
        self.update()

    def _apply_size(self):
        w = int(self.BASE_W * self.scale)
        h = int(self.BASE_H * self.scale)
        self.setFixedSize(max(180, w), max(184, h))
        self._keep_on_screen()
        self._style_controls()
        self.update()

    def set_scale(self, s):
        area = self._available_geometry()
        max_scale = min(2.6, (area.width() - 12) / self.BASE_W,
                        (area.height() - 12) / self.BASE_H)
        self.scale = max(self.MIN_SCALE, min(max_scale, s))
        cfg.set_("clock_scale", round(self.scale, 3))
        self._apply_size()

    def _available_geometry(self, point=None):
        screen = QApplication.screenAt(point) if point is not None else None
        screen = screen or QApplication.primaryScreen()
        return screen.availableGeometry()

    def _keep_on_screen(self):
        center = self.frameGeometry().center()
        area = self._available_geometry(center)
        x = max(area.left(), min(self.x(), area.right() - self.width() + 1))
        y = max(area.top(), min(self.y(), area.bottom() - self.height() + 1))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def _bubble_rect(self):
        bubble = self._bubble_geometry()
        art = self._art_rect()
        return QRectF(art.x() + art.width() * bubble[0],
                      art.y() + art.height() * bubble[1],
                      art.width() * bubble[2], art.height() * bubble[3])

    def _bubble_geometry(self):
        return {"chiyo": self.CHIYO_BUBBLE, "anna": self.ANNA_BUBBLE,
                "default": self.BUBBLE}.get(self._art_theme, self.BUBBLE)

    def _art_rect(self):
        """返回按绘制时的等比缩放规则计算出的素材区域。"""
        if self.art and not self.art.isNull():
            factor = min(self.width() / self.art.width(),
                         self.height() / self.art.height())
            art_width = max(1, round(self.art.width() * factor))
            art_height = max(1, round(self.art.height() * factor))
            return QRectF((self.width() - art_width) // 2,
                          (self.height() - art_height) // 2,
                          art_width, art_height)
        return QRectF(0, 0, self.width(), self.height())

    # ---------------------------------------------------- 数据
    def set_time(self, total, remain, title="作答倒计时", running=True,
                 phase="idle", read_total=None):
        self.title = title
        remaining = max(0, int(remain))
        self.hours = remaining // 3600
        self.minutes = (remaining % 3600) // 60
        self.seconds = remaining % 60
        self._pct = (remain / total) if total else 0.0
        self.running = running
        self.phase = phase
        if read_total is not None:
            self.read_total = int(read_total)
        if hasattr(self, "btn_toggle"):
            label = ("暂停" if running else
                     ("开始" if phase in ("idle", "done") else "继续"))
            self.btn_toggle.set_label(label)
            self.btn_toggle.setToolTip(
                "暂停当前倒计时" if running else
                ("开始当前倒计时" if phase in ("idle", "done") else
                 "继续当前倒计时"))
            self.btn_toggle.set_action_icon(
                self._action_icon("pause" if running else "play"))
            self._layout_controls()
        self.update()

    def set_pct(self, pct):
        self._pct = max(0.0, min(1.0, pct))
        self.update()

    # ---------------------------------------------------- 绘制
    def paintEvent(self, ev):
        self._load_art()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        W, H = self.width(), self.height()

        if self.art and not self.art.isNull():
            art_rect = self._art_rect()
            sc = self.art.scaled(int(art_rect.width()), int(art_rect.height()),
                                 Qt.KeepAspectRatio, Qt.SmoothTransformation)
            ox = int(art_rect.x())
            oy = int(art_rect.y())
            p.drawPixmap(ox, oy, sc)
            aw, ah = sc.width(), sc.height()
        else:
            # 素材缺失时的兜底：自己画一颗圆角白气泡
            ox = oy = 0
            aw, ah = W, H
            p.setPen(QPen(QColor("#2A3B27"), max(2.0, 4.0 * self.scale)))
            p.setBrush(QBrush(QColor("#FBFBF6")))
            p.drawRoundedRect(
                QRectF(W * self.BUBBLE[0], H * self.BUBBLE[1],
                       W * self.BUBBLE[2], H * self.BUBBLE[3]), 46, 46)

        bubble = self._bubble_geometry()
        bx = ox + aw * bubble[0]
        by = oy + ah * bubble[1]
        bw = aw * bubble[2]
        bh = ah * bubble[3]
        self._draw_text(p, QRectF(bx, by, bw, bh))
        p.end()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._layout_controls()

    def _draw_text(self, p, box):
        """把倒计时写进气泡区域 box（已换算成控件坐标）。"""
        fam = T.pick_hand_font()

        # 状态配色：归零 → 红；剩余不足 20% → 橙；否则蓝
        if self.hours <= 0 and self.minutes <= 0 and self.seconds <= 0:
            col = QColor(T.C_RED)
            sub = "时间到啦～"
        elif self._pct <= 0.2:
            col = QColor(T.C_AMBER)
            sub = self.title
        else:
            col = QColor(T.C_BLUE)
            sub = self.title

        txt = ("%d:%02d:%02d" % (self.hours, self.minutes, self.seconds)
               if self.hours else "%02d:%02d" % (self.minutes, self.seconds))

        # --- 时间本体：占气泡高度约 44%，垂直位置略偏上 ---
        fs = max(14, int(box.height() * 0.36))
        f = QFont(fam)
        f.setPixelSize(fs)
        f.setBold(True)
        while fs > 12 and QFontMetrics(f).horizontalAdvance(txt) > box.width() * 0.84:
            fs -= 1
            f.setPixelSize(fs)
        p.setFont(f)
        tbox = QRectF(box.x(), box.y() + box.height() * 0.22,
                      box.width(), box.height() * 0.42)
        # 浅色描边让数字在米白气泡上更"跳"
        p.setPen(QColor(255, 255, 255, 190))
        p.drawText(tbox.adjusted(1.6, 1.8, 1.6, 1.8), Qt.AlignCenter, txt)
        p.setPen(col)
        p.drawText(tbox, Qt.AlignCenter, txt)

        # --- 标题 / 状态：气泡下半部 ---
        fs2 = max(10, int(box.height() * 0.15))
        f2 = QFont(fam)
        f2.setPixelSize(fs2)
        f2.setBold(True)
        p.setFont(f2)
        sbox = QRectF(box.x(), box.y() + box.height() * 0.66,
                      box.width(), box.height() * 0.22)
        p.setPen(QColor(T.C_BLUE_DEEP))
        p.drawText(sbox, Qt.AlignCenter, sub)

        # --- 运行状态角标：气泡左上角，小字 ---
        fs3 = max(9, int(box.height() * 0.11))
        f3 = QFont(T.pick_font())
        f3.setPixelSize(fs3)
        p.setFont(f3)
        p.setPen(QColor(120, 132, 118))
        rbox = QRectF(box.x(), box.y() + box.height() * 0.075,
                      box.width(), box.height() * 0.12)
        p.drawText(rbox, Qt.AlignCenter, "运行中" if self.running else "已暂停")

    # ---------------------------------------------------- 交互
    def _in_resize_zone(self, pos):
        g = self.geometry()
        return (pos.x() > g.width() - 30 and pos.y() > g.height() - 30)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            g = self.geometry()
            if self._in_resize_zone(ev.position()):
                self._resize = (ev.globalPosition().toPoint(), g.width(),
                                self.scale)
            else:
                self._press = (ev.position().toPoint(),
                               ev.globalPosition().toPoint())
                self._drag_offset = (ev.globalPosition().toPoint()
                                     - self.frameGeometry().topLeft())
                self._dragging = False
            ev.accept()

    def mouseMoveEvent(self, ev):
        self._refresh_control_hover()
        if self._in_resize_zone(ev.position()) and not self._press and not self._resize:
            self.setCursor(Qt.SizeFDiagCursor)
        elif not self._press and not self._resize:
            self.setCursor(Qt.PointingHandCursor if self._bubble_rect().contains(
                ev.position())
                else Qt.OpenHandCursor)

        if self._press and ev.buttons() & Qt.LeftButton:
            origin, global_origin = self._press
            if (ev.position().toPoint() - origin).manhattanLength() >= QApplication.startDragDistance():
                self._dragging = True
                self.setCursor(Qt.ClosedHandCursor)
                self.move(ev.globalPosition().toPoint() - self._drag_offset)
        elif self._resize and ev.buttons() & Qt.LeftButton:
            p0, w0, s0 = self._resize
            d = (ev.globalPosition().toPoint().x() - p0.x())
            self.set_scale(s0 * (1.0 + d / max(120.0, w0)))

    def mouseReleaseEvent(self, ev):
        if self._dragging:
            self._keep_on_screen()
            cfg.set_("clock_x", self.x())
            cfg.set_("clock_y", self.y())
        elif self._press and not self._resize:
            if self._bubble_rect().contains(ev.position()):
                self.timer_action.emit("toggle")
            elif ev.position().y() > self.height() * .42:
                self.timer_action.emit("cycle_stage")
        self._press = None
        self._drag_offset = None
        self._dragging = False
        self._resize = None
        self.setCursor(Qt.OpenHandCursor)
        ev.accept()

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.ControlModifier:
            self.set_scale(self.scale * (1.0015 ** ev.angleDelta().y()))
        else:
            ev.ignore()

    def restore_pos(self):
        x = cfg.get("clock_x", -1)
        y = cfg.get("clock_y", -1)
        primary = QApplication.primaryScreen()
        if primary is None:
            return
        if x is None or y is None or int(x) < 0 or int(y) < 0:
            scr = primary.availableGeometry()
            self.move(scr.right() - self.width() - 40,
                      scr.bottom() - self.height() - 40)
        else:
            # 优先恢复到原来的显示器；显示器移除后回退到主屏并收回窗口。
            x, y = int(x), int(y)
            point = QPoint(x + self.width() // 2, y + self.height() // 2)
            screen = QApplication.screenAt(point) or primary
            scr = screen.availableGeometry()
            x = max(scr.left(), min(x, scr.right() - self.width() + 1))
            y = max(scr.top(), min(y, scr.bottom() - self.height() + 1))
            self.move(x, y)
