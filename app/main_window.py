# -*- coding: utf-8 -*-
"""主窗口 —— 无边框真圆角 + 侧栏导航 + 页面栈 + 遮罩 + 主题/缩放联动"""

import os
import sys
import datetime
import math

from PySide6.QtCore import (Qt, QTimer, QSize, QRect, QRectF, QPoint, QEvent,
                            QPropertyAnimation, QEasingCurve, QParallelAnimationGroup,
                            Property, QPointF, QVariantAnimation, QThreadPool,
                            QThread, Signal)
from PySide6.QtGui import (QIcon, QPixmap, QColor, QPainter, QPen, QBrush, QCursor,
                           QLinearGradient, QRadialGradient,
                           QPainterPath, QKeySequence, QShortcut, QRegion)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                                QHBoxLayout, QLabel, QPushButton, QFrame,
                                QStackedWidget, QMessageBox, QGraphicsOpacityEffect,
                                QGraphicsColorizeEffect)

import theme as T
import config as cfg
from logger import get_logger, app_root, APP_NAME, APP_VER
from ui_kit import (Icons, glass_style, refresh_glass, btn_style, IconButton,
                    add_shadow, attach_shadow, replay_shadow, icon_label,
                    repaint_icon, fade_in, slide_fade, card_in, pm_to_icon,
                    animations_enabled)
from settings_page import SettingsPage
from web_home_page import WebHomePage

log = get_logger()

APP_TITLE = "AZIMUTH"


def brand_pixmap(size):
    """按显示尺寸读取品牌图标，保持高 DPI 下的边缘清晰。"""
    source = QPixmap(T.asset_path("icon.png"))
    return source.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def motion_enabled():
    """读取 Windows 的动画偏好；不可用时沿用应用默认动效。"""
    return animations_enabled()


def snapshot_matches_widget(snapshot, size):
    """截图使用物理像素，窗口使用逻辑像素；比较前统一 DPI 单位。"""
    return (not snapshot.isNull() and
            snapshot.deviceIndependentSize().toSize() == size)


class ThemeRevealLayer(QWidget):
    """用新旧主题快照的圆形裁剪交接，避免透明孔穿透顶层窗口。"""

    def __init__(self, parent, snapshot, center, duration=720, radial=True):
        super().__init__(parent)
        self._old_snapshot = snapshot
        self._new_snapshot = QPixmap()
        self._center = QPointF(center)
        self._radius = 0.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setGeometry(parent.rect())
        self.raise_()
        self._motion = QVariantAnimation(self)
        self._motion.setDuration(duration)
        self._motion.setStartValue(0.0)
        self._motion.setEndValue(self._max_radius() if radial else 1.0)
        self._motion.setEasingCurve(QEasingCurve.OutCubic)
        self._motion.valueChanged.connect(self._set_progress)
        self._motion.finished.connect(self._finish)
        self._radial = radial
        self.show()

    def start_reveal(self):
        """两张主题画面就绪后播放圆形交接。"""
        if (self._old_snapshot.isNull() or
                not snapshot_matches_widget(self._new_snapshot, self.size())):
            return False
        self._radius = 0.0
        self._motion.setStartValue(0.0)
        self._motion.setEndValue(self._max_radius() if self._radial else 1.0)
        self._motion.start()
        return True

    def set_target_snapshot(self, snapshot):
        """在真实界面完成换肤并恢复绘制后，固定新主题画面。"""
        self._new_snapshot = snapshot

    def _max_radius(self):
        x, y = self._center.x(), self._center.y()
        return max(math.hypot(px - x, py - y)
                   for px, py in ((0, 0), (self.width(), 0),
                                  (0, self.height()),
                                  (self.width(), self.height()))) + 4

    def _set_progress(self, value):
        self._radius = float(value)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawPixmap(self.rect(), self._old_snapshot)
        if self._radial:
            painter.setRenderHint(QPainter.Antialiasing, True)
            if not self._new_snapshot.isNull():
                reveal = QPainterPath()
                reveal.addEllipse(self._center, self._radius, self._radius)
                painter.setClipPath(reveal)
                painter.drawPixmap(self.rect(), self._new_snapshot)
        else:
            painter.setOpacity(min(1.0, max(0.0, self._radius)))
            if not self._new_snapshot.isNull():
                painter.drawPixmap(self.rect(), self._new_snapshot)
        painter.end()

    def _finish(self):
        self.hide()
        self.deleteLater()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._radial and self._motion.state() == QVariantAnimation.Running:
            self._motion.setEndValue(self._max_radius())


def fit_screen(w, h, ratio=0.90):
    """把期望尺寸压进屏幕可用区域（已扣掉任务栏）。

    无边框窗口不会自己适配屏幕：写死尺寸的后果是小屏 / 高缩放的机器上
    窗口整块超出屏幕，底部按钮被裁掉、用户点都点不到。
    所有顶层窗口都应该先过一遍这个函数。
    """
    try:
        scr = QApplication.primaryScreen()
        if scr is not None:
            a = scr.availableGeometry()
            w = max(360, min(int(w), int(a.width() * ratio)))
            h = max(320, min(int(h), int(a.height() * ratio)))
    except Exception:
        pass
    return int(w), int(h)


# 边缘命中 -> 鼠标指针形状
_EDGE_CURSORS = {
    Qt.TopLeftCorner: Qt.SizeFDiagCursor,
    Qt.BottomRightCorner: Qt.SizeFDiagCursor,
    Qt.TopRightCorner: Qt.SizeBDiagCursor,
    Qt.BottomLeftCorner: Qt.SizeBDiagCursor,
    Qt.LeftEdge: Qt.SizeHorCursor,
    Qt.RightEdge: Qt.SizeHorCursor,
    Qt.TopEdge: Qt.SizeVerCursor,
    Qt.BottomEdge: Qt.SizeVerCursor,
}


# ---------------------------------------------------------------- 侧栏导航
class NavSelectionPlate(QWidget):
    """选中底板与出框蝴蝶结一起移动，保留按钮本身的点击区域。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        source = QPixmap(T.asset_path("chiyo_pack", "nav_bow.png"))
        self._bow = source.scaled(58, 46, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        # 只有千代蝴蝶结需要出框留白，其他主题底板与导航按钮完全对齐。
        panel = (QRectF(1, 9, self.width() - 32, self.height() - 18)
                 if T.app_theme == "chiyo" else
                 QRectF(self.rect()).adjusted(.5, .5, -.5, -.5))
        if T.app_theme == "chiyo":
            painter.setBrush(QColor("#E83E55"))
            painter.setPen(QPen(QColor("#FF8390" if T.mode == "dark" else "#D93349"), 1))
        elif T.app_theme == "industrial" and T.mode == "dark":
            painter.setBrush(QColor("#242A2D"))
            painter.setPen(QPen(QColor("#394145"), 1))
        elif T.app_theme == "industrial":
            painter.setBrush(QColor("#FFFFFF"))
            painter.setPen(QPen(QColor("#CFD4D7"), 1))
        elif T.app_theme == "anna":
            painter.setBrush(QColor("#1769E8"))
            painter.setPen(QPen(QColor("#1769E8"), 1))
        elif T.mode == "dark":
            painter.setBrush(QColor(112, 151, 202, 56))
            painter.setPen(QPen(QColor(171, 202, 244, 62), 1))
        else:
            painter.setBrush(QColor(255, 255, 255, 225))
            painter.setPen(QPen(QColor("#D7E1ED"), 1))
        painter.drawRoundedRect(panel, 14, 14)
        if T.app_theme == "chiyo" and not self._bow.isNull():
            painter.translate(panel.right() - 5, 18)
            painter.rotate(30)
            painter.drawPixmap(-self._bow.width() // 2, -self._bow.height() // 2,
                               self._bow)
        painter.end()


class NavButton(QPushButton):
    """侧栏导航项：矢量图标 + 文字，选中态高亮，带滑动指示条与交互动画"""

    def __init__(self, icon_name, text, parent=None):
        super().__init__(text, parent)
        self._icon_name = icon_name
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setMinimumHeight(48)
        self._ind = 0.0     # 指示条不透明度
        self._anim = QPropertyAnimation(self, b"ind", self)
        self._icon_anim = QPropertyAnimation(self, b"iconSize", self)
        self._ripple_t = 1.0
        self._ripple_center = QPointF(24, 24)
        self._ripple_motion = QVariantAnimation(self)
        self._ripple_motion.setDuration(820)
        self._ripple_motion.setEasingCurve(QEasingCurve.OutCubic)
        self._ripple_motion.valueChanged.connect(self._advance_ripple)
        self.clicked.connect(lambda: self._start_ripple())
        self.refresh(False)

    def _advance_ripple(self, value):
        self._ripple_t = float(value)
        self.update()

    def _start_ripple(self, center=None):
        if not motion_enabled() or not self.isVisible():
            return
        self._ripple_center = QPointF(center) if center is not None else QPointF(self.rect().center())
        self._ripple_motion.stop()
        self._ripple_motion.setStartValue(0.)
        self._ripple_motion.setEndValue(1.)
        self._ripple_motion.start()

    def _paint_ripple(self):
        if self._ripple_t >= 1 or not motion_enabled():
            return
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 14, 14)
        p.setClipPath(clip)
        radius = max(self.width(), self.height()) * (.08 + .9 * self._ripple_t)
        color = QColor('#FFFFFF' if self.isChecked() else '#F45B70' if T.app_theme == 'chiyo' else T.C_BLUE)
        color.setAlpha(round(65 * (1 - self._ripple_t)))
        grad = QRadialGradient(self._ripple_center, radius)
        clear = QColor(color); clear.setAlpha(0)
        grad.setColorAt(0, clear); grad.setColorAt(.72, color); grad.setColorAt(1, clear)
        p.setPen(Qt.NoPen); p.setBrush(grad)
        p.drawEllipse(self._ripple_center, radius, radius)
        color.setAlpha(round(110 * (1 - self._ripple_t)))
        p.setBrush(Qt.NoBrush); p.setPen(QPen(color, 1.4))
        p.drawEllipse(self._ripple_center, radius * .8, radius * .8)
        p.end()

    def hideEvent(self, event):
        self._ripple_motion.stop()
        self._ripple_t = 1.
        super().hideEvent(event)

    def _get_ind(self):
        return float(self._ind)

    def _set_ind(self, v):
        self._ind = float(v)
        self.update()

    # 必须用 Qt 的 Property，不能用 Python 内建 property：
    # QPropertyAnimation 走 Qt 元对象系统，内建 property 它看不见，
    # 只会静默报 "trying to animate a non-existing property ind"，
    # 指示条动画等于没做。
    ind = Property(float, _get_ind, _set_ind)

    def animate(self, on):
        self._anim.stop()
        if not motion_enabled():
            self._set_ind(1.0 if on else 0.0)
            return
        self._anim.setDuration(260)
        self._anim.setStartValue(self._ind)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()

    def _animate_icon_size(self, pressed):
        base = 24 if T.app_theme == "anna" else 19
        self._icon_anim.stop()
        target = max(14, round(base * (
            0.96 if pressed else 1.03 if self.underMouse() else 1.0)))
        if not motion_enabled():
            self.setIconSize(QSize(target, target))
            return
        self._icon_anim.setDuration(190)
        self._icon_anim.setStartValue(self.iconSize())
        self._icon_anim.setEndValue(QSize(target, target))
        self._icon_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._icon_anim.start()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._animate_icon_size(True)
            self._start_ripple(event.position())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._animate_icon_size(False)

    def enterEvent(self, event):
        self._animate_icon_size(False)
        self._start_ripple(QPointF(self.width() * .32, self.height() / 2))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_icon_size(False)
        super().leaveEvent(event)

    def refresh(self, checked):
        self.setChecked(checked)
        chiyo = T.app_theme == "chiyo"
        industrial = T.app_theme == "industrial"
        # ★ v1.9.12：侧栏配色按主题区分（五三要求浅色/深色下有明显区别）。
        #   浅色 = 亮 CAAC 蓝 + 纯白图标；深色 = 深海军蓝 + 冰蓝图标。
        dark = (T.mode == "dark")
        if chiyo:
            col = "#FFFFFF" if checked else ("#E8E1DB" if dark else "#33343A")
            bg_checked, bd_checked, hover_bg = "#E83E55", "1px solid #FF8390", "#F15367"
        elif T.app_theme == "anna":
            col = "#FFFFFF" if checked else (
                "#E4ECFA" if dark else "#12356E")
            bg_checked, bd_checked, hover_bg = (
                "#1769E8", "1px solid #1769E8",
                "#1C2E49" if dark else "#EAF2FF")
        elif industrial:
            col = T.P["accent_fg"] if checked else T.P["ink2"]
            bg_checked = T.P["panel_bg"]
            bd_checked = f"1px solid {T.P['glass_edge']}"
            hover_bg = T.P["panel_hover"]
        elif dark:
            col = "#F0F5FC" if checked else "#AAB9CC"
            bg_checked, bd_checked, hover_bg = "#26384E", \
                "1px solid rgba(160,200,255,0.20)", "#1B2A3D"
        else:
            col = "#174A91" if checked else "#465C78"
            bg_checked, bd_checked, hover_bg = "#FFFFFF", \
                "1px solid #D7E1ED", "#E1EAF6"
        anna = T.app_theme == "anna"
        self.setMinimumHeight(56 if anna else 48)
        isz = 24 if anna else 19
        icon_col = ("#E83E55" if chiyo else T.P["accent_fg"]
                    if industrial and checked else col)
        self.setIcon(pm_to_icon(getattr(Icons, self._icon_name)(icon_col, size=isz)))
        self.setIconSize(QSize(isz, isz))
        self.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 14px;
                padding: 12px 16px;
                text-align: left;
                font-size: %spx;
                font-weight: %s;
                color: %s;
            }
            QPushButton:hover { background: transparent; color: %s; }
            QPushButton:focus-visible { background: rgba(120,170,245,.14); }
        """ % (18 if anna else 15, "700" if checked else "600",
               col,
                ("#FFFFFF" if chiyo else T.P["accent_fg"]
                 if industrial and checked else
                 ("#EDF4FF" if dark else "#203E65"))))
        self.animate(checked)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        chiyo = T.app_theme == "chiyo"
        if self.underMouse() and not self.isChecked():
            hover = QColor(232, 62, 82, 25 if T.mode == "dark" else 18) if chiyo else \
                QColor("#1E3F71" if T.mode == "dark" else "#DCEAFF") \
                if T.app_theme == "anna" else \
                QColor(255, 255, 255, 20 if T.mode == "dark" else 118)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(hover)
            p.drawRoundedRect(QRectF(1, 2, self.width() - 2,
                                     self.height() - 4), 14, 14)
        s = min(34, max(30, self.iconSize().width() + 12))
        r = QRectF(10, (self.height() - s) / 2, s, s)
        p.setPen(Qt.PenStyle.NoPen)
        if T.app_theme == "anna":
            p.end()
            super().paintEvent(event)
            self._paint_ripple()
            return
        elif chiyo:
            plate_top = QColor("#31343C" if T.mode == "dark" else "#FFFFFF")
            plate_bottom = QColor("#24262D" if T.mode == "dark" else "#F3F1ED")
            dot_color = QColor("#F45B70")
        elif T.app_theme == "industrial":
            plate_top = QColor(T.P["panel_bg"])
            plate_bottom = QColor(T.P["panel_soft"])
            dot_color = QColor(T.P["accent_fg"])
        elif T.mode == "dark":
            plate_top = QColor("#243850")
            plate_bottom = QColor("#16273C")
            dot_color = QColor(190, 210, 245, 50)
        else:
            plate_top = QColor("#FFFFFF" if self.isChecked() else "#F8FAFD")
            plate_bottom = QColor("#E5EEFA" if self.isChecked() else "#EAF0F7")
            dot_color = QColor("#4678BB")
        p.setBrush(QColor(3, 10, 22, 34 if self.underMouse() else 14))
        p.drawRoundedRect(r.translated(0, 2), 12, 12)
        gradient = QLinearGradient(r.topLeft(), r.bottomRight())
        gradient.setColorAt(0, plate_top)
        gradient.setColorAt(1, plate_bottom)
        p.setBrush(gradient)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, 12, 12)
        # 细小像素点呼应首页纹理，作为图标底板的局部细节。
        p.setPen(Qt.PenStyle.NoPen)
        for row in range(2):
            for col in range(3):
                alpha = (58 if self.underMouse() else 34) - row * 9 - col * 5
                dot = QColor(dot_color)
                dot.setAlpha(min(alpha, dot.alpha()))
                p.setBrush(dot)
                p.drawRect(QRectF(r.right() - 8 + col * 2.2,
                                  r.bottom() - 8 + row * 2.2, 1.2, 1.2))
        p.end()
        super().paintEvent(event)
        self._paint_ripple()
        if self._ind > 0.01:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            color = QColor("#FF536B" if chiyo else T.P["accent_fg"]
                           if T.app_theme == "industrial" else
                           ("#82AEF5" if T.mode == "dark" else "#1A56C4"))
            color.setAlpha(round(235 * min(1.0, self._ind)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            height = 20 * min(1.0, self._ind)
            p.drawRoundedRect(QRectF(3, (self.height() - height) / 2,
                                     3, height), 2, 2)
            p.end()


# ---------------------------------------------------------------- 主窗口
class ShellWidget(QWidget):
    """局部绘制外壳背景，换肤时不触发整棵子控件的样式重算。"""

    corner_mask_changed = Signal()

    def __init__(self, corner=22, parent=None):
        super().__init__(parent)
        self._corner_radius = float(corner)

    def set_corner_radius(self, radius):
        radius = max(0.0, float(radius))
        if radius == self._corner_radius:
            return
        self._corner_radius = radius
        self._update_corner_mask()
        self.update()

    def _update_corner_mask(self):
        """同时裁剪子控件，防止网页和滚动条盖住外壳圆角。"""
        if self._corner_radius <= 0 or self.size().isEmpty():
            self.clearMask()
            self.corner_mask_changed.emit()
            return
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self._corner_radius,
                            self._corner_radius)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        self.corner_mask_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_corner_mask()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.corner_mask_changed.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(T.P["shell"]))
        painter.drawRoundedRect(QRectF(self.rect()),
                                self._corner_radius, self._corner_radius)
        painter.end()


@__import__('motion_runtime').motion_page
class MainWindow(QMainWindow):
    """无边框 + 真圆角窗口。
    自己实现标题栏拖拽、双击最大化、三键窗口控制，
    顶层四角使用透明像素和抗锯齿阴影，内部外壳遮罩负责裁剪子控件。"""

    CORNER = 22
    SHADOW = 14          # 窗口四周留给自绘阴影的透明留白
    SHADOW_EDGE = 3.0    # 阴影与系统窗口边界之间保留透明像素，避免直边残影

    def __init__(self, startup_event_pump=None):
        super().__init__()
        self._startup_event_pump = startup_event_pump
        # 应用使用系统 DPI 缩放；旧版自定义缩放会销毁并重建整个页面栈。
        self.ui_scale = 1.0
        if float(cfg.get("ui_scale", 1.0) or 1.0) != 1.0:
            cfg.set_("ui_scale", 1.0)
        # 窗口阴影：开关 + 强度。留白（SHADOW）始终保留，关掉阴影只是不画，
        # 这样开关切换时窗口几何和内部布局都不跳动。
        self.shadow_on = bool(cfg.get("shadow_on", True))
        self.shadow_strength = self._clamp_strength(cfg.get("shadow_strength", 1.0))
        self.clock = None
        self.timeup = None
        self._drag_pos = None
        self._re_edge = None       # 正在拖拽缩放的边缘
        self._re_geo = None        # 拖拽起始时的窗口几何
        self._re_origin = None     # 拖拽起始时的鼠标全局坐标
        self._page_motion = None
        self._page_motion_overlay = None
        self._page_motion_incoming = None
        self._bank_exit_active = False
        self._bank_exit_target = None
        self._theme_dirty_pages = set()
        self._startup_played = False
        self._startup_prepared = False
        self._page_switch_generation = 0
        self._theme_reveal = None
        T.set_app_theme(cfg.get("app_theme", "default"))
        from skin_manager import valid_clock_skin
        cfg.set_("clock_skin", valid_clock_skin(cfg.get("clock_skin", "default")))
        self.SHADOW = 14
        self.CORNER = (18 if T.app_theme == "anna" else
                       14 if T.app_theme == "industrial" else 22)
        from sound import manager as sound_manager
        self._sound = sound_manager()

        self.setWindowTitle(APP_TITLE)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # 初始尺寸必须落在屏幕可用区域内。
        # 之前写死 resize(1400, 880)，在 1366x768 或系统开了缩放的机器上
        # 会整块超出屏幕，底部被裁 —— 用户看到的"界面混乱"、以及
        # "开始考试点不到"就是这么来的。
        # 注意：窗口总尺寸 = 内容尺寸 + 四周阴影留白（阴影要画在留白里）。
        cw, ch = fit_screen(1584, 992)
        m = self.SHADOW
        self.resize(cw, ch)
        self.setMinimumSize(*fit_screen(1024, 660, ratio=0.98))
        self.setWindowIcon(self._icon())

        self._build()
        self._pump_startup_events()
        self._apply_theme(T.resolve_theme(cfg.get("theme", "system")),
                          pref=cfg.get("theme", "system"), first=True)
        self._pump_startup_events()
        self.switch(4)
        self._sync_clock()
        self.p_set.app_theme_request.connect(self.set_app_theme)
        self.p_set.card_layout_request.connect(self.set_card_layout)
        self.p_set.sound_settings_changed.connect(self.set_sound_settings)
        self.p_set.sound_preview.connect(self.preview_sound)
        self.p_set.wallpaper_request.connect(self.set_wallpaper)
        self.p_timer.state_changed.connect(self._refresh_clock)
        # 注意：入场动画【不能】在这里定时触发。
        # 构造 MainWindow 时窗口还没 show()（启动画面还在前面挡着，要等
        # main.finish() 才显示），若此时调 play_intro()，card_in 里的
        # QGraphicsOpacityEffect 会把"布局尺寸还没收敛"的一帧缓存下来，
        # 之后不再刷新 —— 屏幕上就残留一帧错位的计时页（圆环错位、右侧
        # 面板重叠），而 widget.grab() 抓到的却是正确画面，极难排查。
        # 正确做法：由 main.finish() 在 win.show() 之后调 self.first_intro()。
        log.info("主窗口就绪 theme=%s scale=%.2f", T.mode, self.ui_scale)

    def _pump_startup_events(self):
        """控件树分段构建时让启动画面继续接收视频帧。"""
        pump = self._startup_event_pump
        if callable(pump):
            pump()

    # ---------------------------------------------------- 构建
    def _build(self):
        # 外层 wrap 负责在窗口四周留出透明边距，阴影由 MainWindow.paintEvent
        # 自绘。这里【不能】再用 QGraphicsDropShadowEffect：实测它会把圆角外
        # 本应完全透明的区域糊成半透明（alpha 50~150），深色模式下阴影与
        # 窗口底色几乎同色，屏幕上四角看起来就是直角。
        wrap = QWidget()
        wrap.setAttribute(Qt.WA_TranslucentBackground, True)
        self.wrap = wrap
        self.setCentralWidget(wrap)
        wl = QVBoxLayout(wrap)
        self.wrap_lay = wl
        wl.setContentsMargins(self.SHADOW, self.SHADOW, self.SHADOW, self.SHADOW)
        wl.setSpacing(0)

        shell = ShellWidget(self.CORNER)
        shell.setObjectName("Shell")
        self.shell = shell
        shell.corner_mask_changed.connect(self._sync_window_corner_mask)
        wl.addWidget(shell)
        self._refresh_shell()

        root = QVBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_titlebar())
        self._pump_startup_events()

        # 主体：stage 画背景，content 绝对铺满（层级天然正确）
        body_wrap = QWidget()
        body_wrap.setAttribute(Qt.WA_TranslucentBackground, True)
        self.body_wrap = body_wrap
        body_lay = QVBoxLayout(body_wrap)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        from main import StageWidget, LockOverlay, MosaicOverlay
        self.stage = StageWidget(body_wrap)
        self.stage.set_corner(self.CORNER)
        body_lay.addWidget(self.stage, 1)
        self._mosaic_timer = QTimer(self)
        self._mosaic_timer.setInterval(33)
        self._mosaic_timer.timeout.connect(self._sync_mosaic_cursor)
        self._mosaic_timer.start()

        self.content = QWidget(self.stage)
        self.content.setAttribute(Qt.WA_TranslucentBackground, True)
        content_lay = QVBoxLayout(self.content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)
        cl = QHBoxLayout()
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        content_lay.addLayout(cl)

        # ---- 侧栏 ----
        self.side = QFrame()
        self.side.setFixedWidth(int(
            (228 if T.app_theme == "anna" else
             224 if T.app_theme == "industrial" else 192) * self.ui_scale))
        self.side.setObjectName("SideBar")
        self._refresh_side()
        sl = QVBoxLayout(self.side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)

        brand = QWidget()
        brand.setStyleSheet("background:transparent;")
        bl = QVBoxLayout(brand)
        m = int(22 * self.ui_scale)
        bl.setContentsMargins(m, m, m, int(16 * self.ui_scale))
        bl.setSpacing(3)
        self.brand_layout = bl

        brand_row = QHBoxLayout()
        brand_row.setSpacing(int(9 * self.ui_scale))
        self.brand_ic = QLabel()
        self.brand_ic.setFixedSize(int(32 * self.ui_scale), int(32 * self.ui_scale))
        brand_row.addWidget(self.brand_ic)
        bl_ = QVBoxLayout()
        bl_.setSpacing(1)
        self.brand_t = QLabel("AZIMUTH")
        self.brand_t.setStyleSheet(
            "font-size:%dpx; font-weight:900; color:#fff;"
            "letter-spacing:1.5px; background:transparent;"
            % int(23 * self.ui_scale))
        self.brand_s = QLabel("CAAC Ground Station")
        self.brand_s.setStyleSheet(
            "font-size:%dpx; color:rgba(255,255,255,0.72);"
            "background:transparent;" % int(11 * self.ui_scale))
        bl_.addWidget(self.brand_t)
        bl_.addWidget(self.brand_s)
        brand_row.addLayout(bl_)
        self.brand_mascot = QLabel()
        self.brand_mascot.setFixedSize(24, 24)
        self.brand_mascot.setVisible(T.app_theme == "chiyo")
        brand_row.addWidget(self.brand_mascot)
        brand_row.addStretch()
        bl.addLayout(brand_row)
        sl.addWidget(brand)

        sep = QFrame()
        sep.setObjectName("SideDivider")
        sep.setFixedHeight(1)
        sl.addWidget(sep)
        self.nav_gap = QWidget(self.side)
        self.nav_gap.setStyleSheet("background:transparent;")
        self.nav_gap.setFixedHeight(
            int((29 if T.app_theme == "anna" else 11) * self.ui_scale))
        sl.addWidget(self.nav_gap)

        self.navs = []
        self.nav_plate = NavSelectionPlate(self.side)
        self.nav_plate.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.nav_plate.lower()
        self._nav_motion = None
        self._nav_layout_timer = QTimer(self)
        self._nav_layout_timer.setSingleShot(True)
        self._nav_layout_timer.timeout.connect(self._sync_nav_plate_layout)
        for i, ic, name in ((4, "home" if T.app_theme == "anna" else "book", "学习首页"),
                            (1, "doc", "题库题卡"),
                            (0, "timer", "限时模拟"),
                            (2, "monitor" if T.app_theme == "anna" else "bulb", "地面站教学"),
                            (3, "gear", "设置")):
            b = NavButton(ic, "  " + name)
            b.page_index = i
            b.clicked.connect(lambda _=False, x=i: self.switch(x))
            holder = QWidget()
            holder.setStyleSheet("background:transparent;")
            hl = QHBoxLayout(holder)
            nav_vertical = 5 if T.app_theme == "anna" else 3
            hl.setContentsMargins(int(14 * self.ui_scale), int(nav_vertical * self.ui_scale),
                                  int(14 * self.ui_scale),
                                  int(nav_vertical * self.ui_scale))
            hl.setSpacing(0)
            hl.addWidget(b)
            b._holder = holder
            holder._nav_holder = True
            sl.addWidget(holder)
            self.navs.append(b)

        self._signature_stretch_index = sl.count()
        sl.addStretch(2 if T.app_theme == "anna" else 3)

        motto = QLabel("守护蓝天　从我起航")
        motto.setAlignment(Qt.AlignCenter)
        motto.setStyleSheet(
            "font-size:%dpx; color:rgba(255,255,255,0.80);"
            "letter-spacing:2px; padding:10px 0 18px 0;"
            "background:transparent;" % int(12 * self.ui_scale))
        self.motto = motto
        self.side_chiyo_art = QLabel()
        self.side_chiyo_art.setAlignment(Qt.AlignCenter)
        self.side_chiyo_art.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.side_chiyo_art.setMaximumHeight(int(258 * self.ui_scale))
        self.side_chiyo_art.setVisible(T.app_theme == "chiyo")
        self.side_signature = QLabel()
        self.side_signature.setAlignment(Qt.AlignCenter)
        self.side_signature.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.side_signature.setFixedHeight(int(92 * self.ui_scale))
        self.side_signature.setStyleSheet("background:transparent;")
        signature = QPixmap(T.asset_path(
            "anna_theme", "assets", "characters", "wider-sky-signature.png"))
        if not signature.isNull():
            self.side_signature.setPixmap(signature.scaled(
                int(190 * self.ui_scale), int(90 * self.ui_scale),
                Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.side_signature_effect = QGraphicsColorizeEffect(self.side_signature)
        self.side_signature_effect.setColor(QColor("#8FC4FF"))
        self.side_signature_effect.setStrength(1.0)
        self.side_signature.setGraphicsEffect(self.side_signature_effect)
        self.side_signature.setVisible(T.app_theme == "anna")
        self.side_signature_divider = QFrame()
        self.side_signature_divider.setObjectName("AnnaSignatureDivider")
        self.side_signature_divider.setFixedHeight(1)
        self.side_signature_divider.setVisible(T.app_theme == "anna")
        sl.addWidget(self.side_chiyo_art)
        sl.addWidget(self.side_signature)
        sl.addStretch(1)
        sl.addWidget(self.side_signature_divider)
        sl.addWidget(motto)
        cl.addWidget(self.side)
        self._pump_startup_events()

        # ---- 右侧 ----
        right = QWidget()
        right.setAttribute(Qt.WA_TranslucentBackground, True)
        self.right = right
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        self.stack = QStackedWidget()
        self.stack.setAttribute(Qt.WA_TranslucentBackground, True)
        from main import TimerPage
        from bank_page import BankPage
        from knowledge_page import KnowledgePage
        self.p_timer = TimerPage()
        self._pump_startup_events()
        self.p_bank = BankPage()
        self._pump_startup_events()
        self.p_know = KnowledgePage()
        self._pump_startup_events()
        self.p_set = SettingsPage()
        self._pump_startup_events()
        self.p_home = WebHomePage(self.p_bank.all)
        self.p_home.set_ui_scale(self.ui_scale)
        self._pump_startup_events()
        self.p_home.navigate.connect(self.switch)
        self.p_home.practice.connect(self.start_practice)
        self.p_home.review.connect(self.review_wrong)
        self.p_set.theme_request.connect(self.set_theme)
        self.p_set.clock_toggle.connect(self.set_clock_on)
        self.p_set.clock_scale_request.connect(self.set_clock_scale)
        self.p_set.clock_skin_request.connect(self.set_clock_skin)
        self.p_set.shadow_request.connect(self.set_shadow)
        for p in (self.p_timer, self.p_bank, self.p_know, self.p_set, self.p_home):
            self.stack.addWidget(p)
        from chiyo_art import ChiyoArtwork, ChiyoDecoration
        # 稿纸整图包含不透明灰底，不能铺在页面控件后面；页面装饰改用
        # 单独的插画槽，避免纹理和纸张背景进入文字及操作区。
        self.chiyo_page_decor = {}
        self.chiyo_page_art = {
            "timer": ChiyoArtwork("timer", self.p_timer.chiyo_art_slot),
            "knowledge": ChiyoArtwork("knowledge", self.p_know.chiyo_art_slot),
        }
        self.page_accent = QFrame()
        self.page_accent.setFixedHeight(3)
        self.page_accent.setStyleSheet("background:#1A56C4; border:0;")
        rl.addWidget(self.page_accent)
        rl.addWidget(self.stack, 1)

        # 状态栏
        sbar = QWidget()
        sbar.setStyleSheet("background:transparent;")
        sbl = QHBoxLayout(sbar)
        sbl.setContentsMargins(int(30 * self.ui_scale), 0,
                               int(30 * self.ui_scale),
                               int(11 * self.ui_scale))
        self.status = QLabel("就绪")
        self.status.setStyleSheet(
            "font-size:%dpx; color:%s; background:transparent;"
            % (int(11 * self.ui_scale), T.P["ink3"]))
        sbl.addWidget(self.status)
        sbl.addStretch()
        self.status_r = QLabel("")
        self.status_r.setStyleSheet(
            "font-size:%dpx; color:%s; background:transparent;"
            % (int(11 * self.ui_scale), T.P["ink3"]))
        sbl.addWidget(self.status_r)
        self.status_bar = sbar
        sbar.setVisible(T.app_theme != "anna")
        rl.addWidget(sbar)
        cl.addWidget(right, 1)

        root.addWidget(body_wrap, 1)

        # 锁定遮罩
        self.overlay = LockOverlay(self.stage)
        self.mosaic_layer = MosaicOverlay(self.stage, parent=self.content)
        self.mosaic_layer.raise_()
        self.overlay.hide()
        self.stage.installEventFilter(self)

        self.setMouseTracking(True)
        self._install_shortcuts()
        self._install_edge_filters()
        self._update_bank_info()
        self._pump_startup_events()

    def _install_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+D"), self,
                  lambda: self.set_theme("dark" if T.mode == "light" else "light"))
        QShortcut(QKeySequence("Ctrl+1"), self, lambda: self.switch(4))
        QShortcut(QKeySequence("Ctrl+2"), self, lambda: self.switch(1))
        QShortcut(QKeySequence("Ctrl+3"), self, lambda: self.switch(0))
        QShortcut(QKeySequence("Ctrl+4"), self, lambda: self.switch(2))
        QShortcut(QKeySequence("Ctrl+5"), self, lambda: self.switch(3))
        QShortcut(QKeySequence("Ctrl+F"), self, self.focus_search)

    def focus_search(self):
        self.switch(1)
        self.p_bank.q.setFocus()
        self.p_bank.q.selectAll()

    def start_practice(self, qid):
        self.p_bank.clear_filters()
        self.switch(1)
        if qid is not None:
            self.p_bank.goto_question(int(qid))

    def review_wrong(self):
        self.p_bank.clear_filters()
        self.p_bank.cb_p.setCurrentIndex(self.p_bank.cb_p.findData("wrong"), emit=True)
        self.switch(1)

    def prepare_startup(self):
        """在启动视频期间预热首页数据和首屏布局。"""
        if self._startup_prepared:
            return
        self._startup_prepared = True
        self.p_home.on_show()

        page = self.stack.currentWidget()
        widgets = (self, self.shell, self.body_wrap, self.content,
                   self.right, self.stack, page)
        for widget in widgets:
            if widget is not None:
                widget.ensurePolished()
        layouts = (self.wrap_lay, self.shell.layout(), self.body_wrap.layout(),
                   self.content.layout(), self.right.layout(),
                   self.stack.layout(), page.layout() if page is not None else None)
        for layout in layouts:
            if layout is not None:
                layout.activate()
        # 首次切页前预热原生页的样式、字体和布局；不复用隐藏页截图作为入场帧。
        for target in (self.p_bank, self.p_know, self.p_set):
            target.ensurePolished()
            target.resize(self.stack.size())
            layout = target.layout()
            if layout is not None:
                layout.activate()
            target.grab()
        # 启动遮罩仍在显示时预热教学菜单大卡片，避免首进页面同步抓图。
        self.p_know.warm_up_teaching_menu()
        initial_page = self.stack.currentWidget()
        prepare_intro = getattr(initial_page, "prepare_intro", None)
        if callable(prepare_intro):
            prepare_intro()

    def first_intro(self):
        """窗口显示回调内先准备目标页起点，再开始原有入场节奏。"""
        self.prepare_startup()
        self._move_nav_plate(self.stack.currentIndex(), False)
        if self._startup_played:
            return
        self._startup_played = True
        page = self.stack.currentWidget()
        original = self.side.pos()
        if motion_enabled():
            self._startup_side_home = original
            self.side.move(original.x() - 12, original.y())
            group = QParallelAnimationGroup(self)
            slide = QPropertyAnimation(self.side, b"pos", group)
            slide.setDuration(460)
            slide.setStartValue(self.side.pos())
            slide.setEndValue(original)
            slide.setEasingCurve(QEasingCurve.OutCubic)
            group.addAnimation(slide)
            group.finished.connect(
                lambda current=group: self._clear_startup_motion(current))
            group.start(QPropertyAnimation.DeleteWhenStopped)
            self._startup_motion = group
        intro = getattr(page, "play_intro", None)
        if callable(intro):
            intro()

    def _clear_startup_motion(self, group):
        if self._startup_motion is group:
            self._startup_motion = None

    # ---------------------------------------------------- 主题
    def set_app_theme(self, key, source=None):
        if key not in ("default", "chiyo", "industrial", "anna"):
            valid = False
            if isinstance(key, str) and key.startswith("skin:"):
                try:
                    from skin_manager import get_theme
                    valid = bool(get_theme(key.split(":", 1)[1]))
                except (ImportError, OSError, ValueError):
                    valid = False
            if not valid:
                key = "default"
        if key == T.app_theme:
            self.p_set.set_app_theme_state(key)
            return
        self._prepare_theme_reveal(source)
        cfg.set_("app_theme", key)
        T.set_app_theme(key)
        self.SHADOW = 14
        self.CORNER = 18 if key == "anna" else 14 if key == "industrial" else 22
        margin = 0 if self.isMaximized() else self.SHADOW
        self.wrap_lay.setContentsMargins(margin, margin, margin, margin)
        self.stage.set_corner(0 if self.isMaximized() else self.CORNER)
        self.p_set.set_app_theme_state(key)
        clock = getattr(self, "clock", None)
        if clock is not None:
            clock.set_skin(cfg.get("clock_skin", "default"))
        self._apply_theme(T.mode, pref=cfg.get("theme", "system"))

    def set_wallpaper(self, path, opacity, blur):
        self.stage.set_custom_background(path, opacity, blur, T.app_theme)

    def set_card_layout(self, layout):
        layout = layout if layout in ("standard", "paper") else "standard"
        cfg.set_("card_layout", layout)
        setter = getattr(self.p_bank, "set_card_layout", None)
        if callable(setter):
            setter(layout)

    def set_sound_settings(self, master, ui_enabled, stage_enabled, volume):
        cfg.set_("sound_master", bool(master))
        cfg.set_("sound_ui", bool(ui_enabled))
        cfg.set_("sound_stage", bool(stage_enabled))
        cfg.set_("sound_volume", int(volume))
        self._sound.configure(enabled=master, ui_enabled=ui_enabled,
                              stage_enabled=stage_enabled, volume=volume)

    def preview_sound(self):
        self._sound.preview()

    # ---------------------------------------------------- 主题
    def set_theme(self, pref, source=None):
        """pref: light / dark / system"""
        next_mode = T.resolve_theme(pref)
        changed = next_mode != T.mode
        cfg.set_("theme", pref)
        if changed:
            self._prepare_theme_reveal(source)
            self._apply_theme(next_mode, pref=pref)
        else:
            self.p_set.set_theme_state(pref)

    def _prepare_theme_reveal(self, source):
        """从触发按钮中心截取旧画面，供新主题圆形揭示交接。"""
        self._clear_theme_reveal()
        if (source is None or not isinstance(source, QWidget)
                or not self.isVisible() or not self.shell.isVisible()
                or not motion_enabled()):
            return False
        try:
            global_center = source.mapToGlobal(source.rect().center())
            local_center = self.shell.mapFromGlobal(global_center)
            if not self.shell.rect().contains(local_center):
                return False
            old_snapshot = self.shell.grab()
        except RuntimeError:
            return False
        if not snapshot_matches_widget(old_snapshot, self.shell.size()):
            return False

        layer = ThemeRevealLayer(
            self.shell, old_snapshot, QPointF(local_center), duration=480,
            radial=True)
        layer._motion.finished.connect(
            lambda reveal=layer: self._theme_reveal_finished(reveal))
        self._theme_reveal = layer
        layer.raise_()
        return True

    def _apply_theme(self, mode, pref=None, first=False):
        T.apply(mode)
        self.status_bar.setVisible(T.app_theme != "anna")
        self.side.setFixedWidth(int(
            (228 if T.app_theme == "anna" else
             224 if T.app_theme == "industrial" else 192) * self.ui_scale))
        self.nav_gap.setFixedHeight(int(
            (29 if T.app_theme == "anna" else 11) * self.ui_scale))
        for button in self.navs:
            if button.page_index == 4:
                button._icon_name = "home" if T.app_theme == "anna" else "book"
            elif button.page_index == 2:
                button._icon_name = "monitor" if T.app_theme == "anna" else "bulb"
            holder = button._holder.layout()
            side_margin = int(14 * self.ui_scale)
            vertical_margin = int((5 if T.app_theme == "anna" else 3)
                                  * self.ui_scale)
            holder.setContentsMargins(side_margin, vertical_margin,
                                      side_margin, vertical_margin)
        self.side.layout().setStretch(
            self._signature_stretch_index, 2 if T.app_theme == "anna" else 3)
        if self.clock is not None:
            self.clock.theme_changed()
        app = QApplication.instance()
        pages = (self.p_timer, self.p_bank, self.p_know,
                 self.p_set, self.p_home)
        active_page = self.stack.currentWidget()
        if first:
            self._set_theme_stylesheet(app)
            self._refresh_shell()
            self._refresh_side()
            for b in self.navs:
                b.refresh(b.isChecked())
            self.stage.theme_changed()
            for page in pages:
                if hasattr(page, "theme_changed"):
                    page.theme_changed(pref)
            self._style_module_headings()
            self.p_set.set_theme_state(pref or cfg.get("theme", "system"))
            self._refresh_titlebar()
            self.overlay.theme_changed()
            self.status.setStyleSheet(
                f"font-size:11px; color:{T.P['ink3']}; background:transparent;")
            self.status_r.setStyleSheet(
                f"font-size:11px; color:{T.P['ink3']}; background:transparent;")
            self._refresh_side()
        else:
            # 主题刷新涉及多张卡片与图标。短暂冻结舞台更新，把样式、矢量图和
            # 页面内容合并为一次绘制，避免每段改色都排队触发整页重绘。
            if not getattr(self, "_theme_updates_frozen", False):
                self._theme_updates_frozen = True
                self.shell.setUpdatesEnabled(False)
            settings_page_active = active_page is self.p_set
            if settings_page_active:
                self._refresh_page_theme(self.p_set, pref)
            self._theme_dirty_pages.update(
                page for page in pages if page is not active_page and
                hasattr(page, "theme_changed"))
            self.p_set.set_theme_state(pref or cfg.get("theme", "system"))
            # 先让 Qt 完成当前帧绘制，再分帧刷新壳层和可见内容；避免
            # WebView 与题库大图的同步重绘堵住主题按钮的点击反馈。
            self._theme_refresh_revision = getattr(
                self, "_theme_refresh_revision", 0) + 1
            revision = self._theme_refresh_revision
            jobs = [
                lambda: self._set_theme_stylesheet(app),
                self._refresh_shell,
                self._refresh_side,
                lambda: [b.refresh(b.isChecked()) for b in self.navs],
                self.stage.theme_changed,
                lambda page=active_page: self._style_module_headings((page,)),
                self._refresh_titlebar,
                self.overlay.theme_changed,
                lambda: self.status.setStyleSheet(
                    f"font-size:11px; color:{T.P['ink3']}; background:transparent;"),
                lambda: self.status_r.setStyleSheet(
                    f"font-size:11px; color:{T.P['ink3']}; background:transparent;"),
            ]
            if hasattr(active_page, "theme_changed") and not settings_page_active:
                jobs.append(lambda page=active_page, choice=pref:
                            self._refresh_page_theme(page, choice))
            # 在静态换肤遮罩保护下预热隐藏页，避免首次切页时临时做整树样式重算。
            jobs.extend(
                lambda target=page, choice=pref: self._refresh_page_theme(
                    target, choice, force=True)
                for page in pages if page is not active_page and
                hasattr(page, "theme_changed"))
            def run_jobs():
                if revision != self._theme_refresh_revision:
                    return
                try:
                    for index, job in enumerate(jobs):
                        if revision != self._theme_refresh_revision:
                            return
                        try:
                            job()
                        except Exception as exc:
                            log.exception("主题刷新任务 %d 失败：%s", index, exc)
                finally:
                    if revision != self._theme_refresh_revision:
                        return
                    # 换肤会重排卡片和导航。撤销此期间启动的旧入场，避免
                    # 透明图层继续套在刷新后的控件上，或导航底板停在旧位置。
                    current_page = self.stack.currentWidget()
                    cancel_intro = getattr(current_page, "cancel_intro", None)
                    if callable(cancel_intro):
                        cancel_intro()
                    self.side.layout().activate()
                    self._move_nav_plate(self.stack.currentIndex(), False)
                    if getattr(self, "_theme_updates_frozen", False):
                        self._theme_updates_frozen = False
                        self.shell.setUpdatesEnabled(True)
                        self.shell.update()
                    if self._theme_reveal is not None:
                        QTimer.singleShot(0, lambda current=revision:
                                          self._start_theme_reveal(current))
            QTimer.singleShot(0, run_jobs)
        log.info("主题切换 -> %s (pref=%s)", T.mode, pref)

    def _set_theme_stylesheet(self, app):
        """只把工业主题限定在主窗口，保留其他主题原有的控件度量。"""
        stylesheet = T.stylesheet()
        anna = T.app_theme == "anna"
        self.setProperty("annaTheme", anna)
        if anna:
            mode = "dark" if T.mode == "dark" else "light"
            qss_path = T.asset_path("anna_theme", "styles", f"qt-{mode}.qss")
            try:
                with open(qss_path, "r", encoding="utf-8") as qss_file:
                    anna_qss = qss_file.read()
                asset_root = T.asset_path("anna_theme", "assets").replace("\\", "/")
                anna_qss = anna_qss.replace(
                    "url(assets/", f'url("{asset_root}/').replace(
                        ".svg);", '.svg");')
                stylesheet += "\n" + anna_qss
            except (OSError, UnicodeError) as exc:
                log.warning("八奈见主题样式资源无法读取，使用基础主题样式：%s", exc)
        if T.app_theme == "industrial":
            if app.styleSheet():
                app.setStyleSheet("")
            self.setStyleSheet(stylesheet)
        else:
            self.setStyleSheet("")
            app.setStyleSheet(stylesheet)

    def _start_theme_reveal(self, revision=None):
        """捕获已换肤画面，再用裁剪交接播放圆形揭示。"""
        if revision is not None and revision != self._theme_refresh_revision:
            return
        layer = self._theme_reveal
        if layer is None:
            return
        # 遮罩是 shell 的子控件；抓图时先隐藏它，防止新快照再次包含旧遮罩。
        layer.hide()
        layer.setGeometry(self.shell.rect())
        target = self.shell.grab()
        if not snapshot_matches_widget(target, layer.size()):
            self._clear_theme_reveal()
            return
        layer.set_target_snapshot(target)
        if not layer.start_reveal():
            self._clear_theme_reveal()
            return
        log.info("主题圆形揭示开始")
        layer.show()
        layer.raise_()

    def _refresh_page_theme(self, page, pref=None, force=False):
        """只在页面可见时重绘它，避免换肤同步重建隐藏页面。"""
        if page is not self.stack.currentWidget() and not force:
            self._theme_dirty_pages.add(page)
            return
        self._theme_dirty_pages.discard(page)
        if not getattr(page, "_handles_theme_stylesheet", False):
            # 主窗口样式表会自然继承到页面；清空页面本地旧表，防止上一款
            # 皮肤留下的控件规则压过当前主题。
            page.setStyleSheet("")
        refresh = getattr(page, "theme_changed", None)
        if callable(refresh):
            refresh(pref)

    def _clear_theme_reveal(self):
        layer = self._theme_reveal
        self._theme_reveal = None
        if layer is not None:
            layer._motion.stop()
            layer.hide()
            layer.deleteLater()

    def _thaw_theme_updates(self):
        """窗口尺寸改变或关闭打断主题更新时，恢复真实界面的正常绘制。"""
        if getattr(self, "_theme_updates_frozen", False):
            self._theme_updates_frozen = False
            self.shell.setUpdatesEnabled(True)
            self.shell.update()

    def _theme_reveal_finished(self, layer):
        """换肤揭示自然结束后清除窗口对遮罩的引用�?"""
        if self._theme_reveal is layer:
            self._theme_reveal = None

    def _pulse(self):
        """换肤时来一次轻微的淡入，让变化更柔和"""
        eff = QGraphicsOpacityEffect(self.content)
        eff.setOpacity(0.55)
        self.content.setGraphicsEffect(eff)
        a = QPropertyAnimation(eff, b"opacity", self)
        a.setDuration(230)
        a.setStartValue(0.55)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.finished.connect(lambda: self.content.setGraphicsEffect(None))
        a.start()
        self._pulse_anim = a

    # ---------------------------------------------------- 桌面时钟
    def set_clock_on(self, on):
        cfg.set_("clock_on", bool(on))
        self._sync_clock()

    def set_clock_scale(self, s):
        if self.clock:
            self.clock.set_scale(s)

    def set_clock_skin(self, skin):
        from skin_manager import valid_clock_skin
        skin = valid_clock_skin(skin)
        cfg.set_("clock_skin", skin)
        if self.clock is not None:
            self.clock.set_skin(skin)
        self.p_set.sync_clock_skin(skin)

    def _handle_clock_action(self, action):
        if action == "toggle":
            self.p_timer.toggle_from_clock()
        elif action == "cycle_character":
            from skin_manager import clock_skins
            keys = [key for key, name in clock_skins()]
            current = cfg.get("clock_skin", "default")
            index = keys.index(current) if current in keys else 0
            self.set_clock_skin(keys[(index + 1) % len(keys)])
        elif action == "cycle_stage":
            current = self.p_timer.phase
            target = "answer" if current == self.p_timer.READ else "read"
            if target == "read" and self.p_timer.read_total <= 0:
                target = "answer"
            self.p_timer.select_stage_from_clock(target)
        elif action == "read":
            self.p_timer.select_stage_from_clock("read")
        elif action == "answer":
            self.p_timer.select_stage_from_clock("answer")
        elif action == "custom":
            self.p_timer.custom_from_clock()
        elif action == "reset":
            self.p_timer.reset()

    def _sync_clock(self):
        want = bool(cfg.get("clock_on", False))
        if want:
            if self.clock is None:
                from clock_widget import ClockBubble
                self.clock = ClockBubble()
                self.clock.timer_action.connect(self._handle_clock_action)
                self.clock.restore_pos()
            self.clock.show()
            self.clock.raise_()
            self._refresh_clock()
            self.status_r.setText("桌面时钟已开启")
        else:
            if self.clock:
                self.clock.hide()
            self.status_r.setText("")
        self.p_set.sync_clock_ui(want)

    def _refresh_clock(self):
        """桌面时钟只读取限时模拟页的唯一计时状态，不创建自己的计时器。"""
        if self.clock is None or not self.clock.isVisible():
            return
        self.clock.set_time(self.p_timer.ring.total,
                            self.p_timer.remain,
                            self.p_timer.ring.title,
                            self.p_timer.tick.isActive(),
                            "read" if self.p_timer.phase == self.p_timer.READ else
                            "answer" if self.p_timer.phase == self.p_timer.DOING else
                            "done" if self.p_timer.phase == self.p_timer.LOCK else "idle",
                            self.p_timer.read_total)

    # ---------------------------------------------------- 到时提醒
    def on_time_up(self, reason):
        """作答时间到：显示遮罩 + 置顶提醒窗，点「确定」解锁"""
        self.overlay.setGeometry(self.stage.rect())
        self.overlay.start(reason)
        if self.timeup is not None:
            try:
                self.timeup.close()
            except Exception:
                pass
        from main import TimeUpDialog
        self.timeup = TimeUpDialog(reason=reason, scale=self.ui_scale)
        self.timeup.confirmed.connect(self.on_time_up_dismiss)
        self.timeup.show()
        self.timeup.raise_()
        self.timeup.activateWindow()
        log.warning("到时提醒弹出：%s", reason)

    def on_time_up_dismiss(self):
        self._sound.stop_stage()
        self.overlay.stop()
        if self.timeup is not None:
            self.timeup.close()
            self.timeup = None
        self.set_status("已解除锁定　·　"
                        + datetime.datetime.now().strftime("%H:%M:%S"))
        log.info("用户点击确定，解除锁定")

    def _icon(self):
        p = T.asset_path("icon.ico")
        return QIcon(p) if os.path.exists(p) else QIcon()

    # ---------------------------------------------------- 外壳样式
    def _sync_window_corner_mask(self):
        """顶层依靠逐像素透明，避免 Windows 区域掩码露出矩形边界。"""
        self.clearMask()

    def _window_shadow_rect(self, inset=0.0):
        """返回阴影所在的圆角边界，四边都留出透明像素。"""
        edge = self.SHADOW_EDGE + max(0.0, float(inset))
        return QRectF(
            edge, edge,
            max(0.0, self.width() - 2.0 * edge),
            max(0.0, self.height() - 2.0 * edge),
        )

    def _refresh_shell(self):
        """外壳只负责画圆角底，阴影交给 MainWindow.paintEvent 自绘。

        为什么不能再用 QGraphicsDropShadowEffect：它会把控件先渲染进
        离屏 pixmap 再合成，结果圆角外本该 alpha=0 的区域被阴影糊成
        半透明（实测角部 26x26 里「透明像素」从 86 变成 0）。深色模式下
        阴影与窗口底色几乎同色 —— 屏幕上四角看起来就是直角。
        """
        r = 0 if self.isMaximized() else self.CORNER
        self.shell.set_corner_radius(r)
        self.update()          # 阴影随主题/最大化状态重画

    def paintEvent(self, ev):
        """在窗口四周的透明留白里自绘圆角阴影。

        必须自己画：只要挂 QGraphicsDropShadowEffect，圆角外的透明区域
        就会被糊掉（原因见 _refresh_shell）。

        画法是「由外向内、alpha 递增的同心圆角矩形」，但**必须用
        CompositionMode_Source 覆盖而不是默认的 SourceOver 叠加**：
        叠加模式下 14 层的 alpha 会连乘累积（1-(1-a1)(1-a2)...）直接顶到
        255，于是强度超过 100% 后几乎看不出变化（实测 100%→210、150%→237，
        已经饱和）。改成覆盖后，每个像素的最终 alpha 就等于覆盖它的最内层
        的 alpha，与「强度」严格成正比。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = self.SHADOW
        # 阴影可关：关掉之后不再往四周留白里画任何东西，四角保持完全透明。
        # 注意留白本身不回收 —— 一收一放会让窗口几何和内部布局来回跳。
        if self.isMaximized() or m <= 0 or not self.shadow_on:
            p.end()
            return
        r, g, b, a = T.P["shadow"]
        k = self.shadow_strength
        p.setPen(Qt.NoPen)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        for inset in range(m):           # inset=0 窗口最外沿，m-1 紧贴外壳
            t = (inset + 1) / float(m)   # 越靠内越浓
            # 强度直接乘在每层的不透明度上（0.5 = 淡一半，1.5 = 浓一半），
            # 比去改模糊半径更符合"强度"的直觉，也不改变阴影的扩散范围。
            alpha = min(255, int(a * k * t ** 1.6))
            if alpha <= 0:
                continue
            p.setBrush(QColor(r, g, b, alpha))
            p.drawRoundedRect(
                self._window_shadow_rect(inset),
                self.CORNER + inset * 0.5, self.CORNER + inset * 0.5)
        p.end()

    @staticmethod
    def _clamp_strength(v):
        """阴影强度归一到 0.0 ~ 1.5（1.5 已经足够浓，再高就糊成一块黑边）"""
        try:
            return max(0.0, min(1.5, float(v)))
        except (TypeError, ValueError):
            return 1.0

    def set_shadow(self, on, strength=None):
        """设置页回调：窗口阴影开关 / 强度。

        只影响自绘阴影这一层，不碰窗口几何 —— 所以调强度时界面不会重排。
        """
        self.shadow_on = bool(on)
        if strength is not None:
            self.shadow_strength = self._clamp_strength(strength)
        cfg.set_("shadow_on", self.shadow_on)
        cfg.set_("shadow_strength", self.shadow_strength)
        self.update()
        log.info("窗口阴影 on=%s strength=%.2f",
                 self.shadow_on, self.shadow_strength)

    def _refresh_side(self):
        r = 0 if self.isMaximized() else self.CORNER
        if T.app_theme == "industrial" and T.mode == "dark":
            bg, border = "#171B1E", "#394145"
            logo, title, secondary, motto = "#F3F5F5", "#F3F5F5", "#A0AAAE", "#A0AAAE"
        elif T.app_theme == "industrial":
            bg, border = "#E7EAEC", "#CFD4D7"
            logo, title, secondary, motto = "#15191C", "#15191C", "#70787D", "#70787D"
        elif T.app_theme == "chiyo" and T.mode == "dark":
            bg, border = "#17191F", "#3B3D46"
            logo, title, secondary, motto = "#F4EEE7", "#F4EEE7", "#BEB8B2", "#BEB8B2"
        elif T.app_theme == "chiyo":
            bg, border = "#FFFDF9", "#25262B"
            logo, title, secondary, motto = "#25262B", "#25262B", "#65646A", "#65646A"
        elif T.app_theme == "anna" and T.mode == "dark":
            bg, border = "#131E31", "#233550"
            logo, title, secondary, motto = "#F3F5FB", "#F3F5FB", "#B4C0D5", "#B4C0D5"
        elif T.app_theme == "anna":
            bg, border = "#FFFDF8", "#D6DDEC"
            logo, title, secondary, motto = "#12356E", "#12356E", "#5C6880", "#5C6880"
        elif T.mode == "dark":
            bg, border = "#111B29", "#26364A"
            logo, title, secondary, motto = "#D7E6FF", "#F2F6FC", "#A8B6C8", "#A8B6C8"
        else:
            bg, border = "#E9EEF5", "#D9E2EC"
            logo, title, secondary, motto = "#1A56C4", "#18283B", "#637286", "#65758A"
        self.side.setStyleSheet("""
            QFrame#SideBar {
                background: %s;
                border: none;
                border-right: 1px solid %s;
                border-top-left-radius: 0px;
                border-bottom-left-radius: %dpx;
            }
            QFrame#SideDivider { background: %s; border: none; }
            QFrame#AnnaSignatureDivider { background: %s; border: none;
                margin-left: 22px; margin-right: 22px; }
        """ % (bg, border, r, border, border))
        if hasattr(self, "page_accent"):
            self.page_accent.setVisible(
                T.app_theme not in ("industrial", "anna"))
        if hasattr(self, "brand_t"):
            self.brand_t.setStyleSheet(
                f"font-size:{int((21 if T.app_theme == 'chiyo' else 24)*self.ui_scale)}px; font-weight:900;"
                f"color:{title}; letter-spacing:1px; background:transparent;")
            if hasattr(self, "brand_layout"):
                margin = int((12 if T.app_theme == "chiyo" else
                              16 if T.app_theme == "anna" else 22) * self.ui_scale)
                self.brand_layout.setContentsMargins(
                    margin, int((24 if T.app_theme == "anna" else 18)*self.ui_scale),
                    margin, int((15 if T.app_theme == "anna" else 14)*self.ui_scale))
            self.brand_s.setText("方位角地面站" if T.app_theme == "chiyo"
                                 else "CAAC Ground Station")
            self.brand_s.setStyleSheet(
                f"font-size:{int(11*self.ui_scale)}px; color:{secondary};"
                "background:transparent;")
            icon_size = int((42 if T.app_theme == "anna" else 32) * self.ui_scale)
            self.brand_ic.setFixedSize(icon_size, icon_size)
            self.brand_ic.setPixmap(brand_pixmap(icon_size))
            if hasattr(self, "brand_mascot"):
                # 品牌名与副标题优先完整显示，角色留给侧栏底部插画位。
                self.brand_mascot.setVisible(False)
            if hasattr(self, "side_chiyo_art"):
                from chiyo_art import sidebar_art_pixmap
                self.side_chiyo_art.setPixmap(sidebar_art_pixmap(
                    int(184 * self.ui_scale), int(258 * self.ui_scale)))
                self.side_chiyo_art.setVisible(T.app_theme == "chiyo")
            if hasattr(self, "side_signature"):
                signature_on = T.app_theme == "anna"
                self.side_signature.setVisible(signature_on)
                self.side_signature_divider.setVisible(signature_on)
                self.side_signature_effect.setEnabled(
                    signature_on and T.mode == "dark")
            self.motto.setStyleSheet(
                f"font-size:{int(12*self.ui_scale)}px; color:{motto};"
                "letter-spacing:1px; padding:10px 0 18px 0; background:transparent;")
        if hasattr(self, "nav_plate"):
            self.nav_plate.update()
            self.nav_plate.lower()
        if hasattr(self, "chiyo_page_art"):
            self._layout_chiyo_page_artwork()

    def _layout_chiyo_page_artwork(self):
        chiyo_on = T.app_theme == "chiyo"
        for page in (getattr(self.p_timer, "chiyo_art_slot", None),
                     getattr(self.p_know, "chiyo_art_slot", None),
                     getattr(self.p_set, "chiyo_art_slot", None)):
            if page is not None:
                page.setVisible(chiyo_on)
        for art in getattr(self, "chiyo_page_art", {}).values():
            art.refresh_art()
        dialogue = getattr(self.p_know, "chiyo_dialogue", None)
        if dialogue is not None:
            dialogue.raise_()
        figure_note = getattr(self.p_know, "chiyo_figure_note", None)
        if figure_note is not None:
            figure_note.raise_()
        for art in getattr(self, "chiyo_page_decor", {}).values():
            art.refresh_art()

    # ---------------------------------------------------- 标题栏
    def _build_titlebar(self):
        tb = QWidget()
        self.tbar = tb
        tb.setObjectName("TitleBar")
        tb.setFixedHeight(int(46 * self.ui_scale))
        tb.mousePressEvent = self._tb_press
        tb.mouseMoveEvent = self._tb_move
        tb.mouseReleaseEvent = self._tb_release
        tb.mouseDoubleClickEvent = lambda e: self._toggle_max()

        lay = QHBoxLayout(tb)
        lay.setContentsMargins(int(20 * self.ui_scale), 0,
                               int(16 * self.ui_scale), 0)
        lay.setSpacing(8)
        self.tb_lay = lay

        self.tb_ic = QLabel()
        self.tb_ic.setFixedSize(int(20*self.ui_scale), int(20*self.ui_scale))
        lay.addWidget(self.tb_ic)
        self.tb_title = QLabel(APP_TITLE)
        self.tb_title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.tb_title)
        # 作者署名（v1.9.6）—— 单独一个 QLabel 而不是拼进 APP_TITLE：
        # 标题要粗、署名要淡，两种字重塞进同一个 label 就调不出来了。
        # ★ 必须放行鼠标事件，否则这一小段变成"拖不动"的死区。
        self.tb_by = QLabel("by Gomigosan")
        self.tb_by.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.tb_by)
        lay.addStretch()

        self.b_min = self._wbtn("min")
        self.b_max = self._wbtn("max")
        self.b_close = self._wbtn("close", danger=True)
        for b in (self.b_min, self.b_max, self.b_close):
            lay.addWidget(b)
        # 部件齐了再统一样式
        self._refresh_titlebar()
        return tb

    def _refresh_titlebar(self):
        r = 0 if self.isMaximized() else self.CORNER
        self.tbar.setFixedHeight(int(
            (50 if T.app_theme == "anna" else 46) * self.ui_scale))
        self.tbar.setStyleSheet(f"""
            QWidget#TitleBar {{
                background: {T.P['titlebar']};
                border-top-left-radius: {r}px;
                border-top-right-radius: {r}px;
                border-bottom: 1px solid {T.P['titlebar_line']};
            }}
        """)
        self.tb_ic.setPixmap(brand_pixmap(int(20*self.ui_scale)))
        self.tb_title.setText("AZIMUTH · 千代·月刊编辑部"
                              if T.app_theme == "chiyo" else APP_TITLE)
        self.tb_title.setStyleSheet(
            f"font-size:{int(13*self.ui_scale)}px; font-weight:700;"
            f"color:{T.P['ink']}; background:transparent;")
        # 署名：比标题小一号、字重轻、颜色退一档，做到"看得见但不抢戏"
        self.tb_by.setText("by Gomigosan")
        self.tb_by.setStyleSheet(
            f"font-size:{int(11*self.ui_scale)}px; font-weight:600;"
            f"color:{T.P['ink3']}; background:transparent;"
            f"padding-left:2px;")
        for b in (self.b_min, self.b_max, self.b_close):
            b.refresh_icon()

    def _wbtn(self, kind, danger=False):
        b = _WinButton(kind, danger, self.ui_scale)
        if kind == "min":
            b.clicked.connect(self.showMinimized)
        elif kind == "max":
            b.clicked.connect(self._toggle_max)
        else:
            b.clicked.connect(self.close)
        return b

    def _tb_press(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag_pos = (ev.globalPosition().toPoint()
                              - self.frameGeometry().topLeft())

    def _tb_move(self, ev):
        if self._drag_pos and ev.buttons() & Qt.LeftButton and not self.isMaximized():
            self.move(ev.globalPosition().toPoint() - self._drag_pos)

    def _tb_release(self, ev):
        self._drag_pos = None

    def _toggle_max(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() != QEvent.Type.WindowStateChange or not hasattr(self, "stage"):
            return
        # 最大化时收掉阴影留白（否则四周会露出一条透明缝），
        # 恢复时再把留白放回来，并让各处四角重新收圆。
        mx = self.isMaximized()
        m = 0 if mx else self.SHADOW
        self.wrap_lay.setContentsMargins(m, m, m, m)
        self.stage.set_corner(0 if mx else self.CORNER)
        self._refresh_shell()
        self._refresh_side()
        self._refresh_titlebar()
        self.update()

    # ---------------------------------------------------- 事件
    def eventFilter(self, obj, ev):
        if (hasattr(self, "_nav_layout_timer") and
                (obj is getattr(self, "side", None) or
                 getattr(obj, "_nav_holder", False) or
                 isinstance(obj, NavButton)) and
                ev.type() in (QEvent.Type.Resize, QEvent.Type.Move,
                              QEvent.Type.LayoutRequest, QEvent.Type.Show)):
            # 等当前布局完成后读取真实坐标，换肤、缩放及页面入场都能重新对齐。
            self._nav_layout_timer.start(0)
        if obj is self.stage and ev.type() == QEvent.Type.Resize:
            r = self.stage.rect()
            self.content.setGeometry(r)
            self.overlay.setGeometry(r)
            self.mosaic_layer.setGeometry(self.content.rect())
            self.content.raise_()
            self.mosaic_layer.raise_()
            if self.overlay.isVisible():
                self.overlay.raise_()
        if self._edge_forward(obj, ev):
            return True
        return super().eventFilter(obj, ev)

    # ---------------------------------------------------- 窗口缩放（拖边缘/角）
    def _install_edge_filters(self):
        """给所有子控件装事件过滤器。

        边缘缩放必须在子控件上也能命中：stage / content 铺满整个窗口，
        鼠标永远落在它们（或其子控件）身上，主窗口自己的 mousePressEvent
        根本收不到事件 —— 这也正是旧版"能看到光标变化却拖不动"的原因
        （旧代码只改光标，从来没真的 resize 过）。
        """
        for w in self.findChildren(QWidget):
            w.installEventFilter(self)

    def _edge(self, pos):
        """命中测试：返回鼠标所在的边缘 / 角。

        窗口四周有 SHADOW 像素的透明阴影留白，用户看到的"窗口边缘"在
        留白内侧，所以判定范围要相应放宽，否则对着看得见的边框按下却
        拖不动。
        """
        m = self.SHADOW + 6
        corner = max(34, m + 12)
        r = self.rect()
        left, right = pos.x() <= m, pos.x() >= r.width() - m
        top, bottom = pos.y() <= m, pos.y() >= r.height() - m
        corner_left = pos.x() <= corner
        corner_right = pos.x() >= r.width() - corner
        corner_top = pos.y() <= corner
        corner_bottom = pos.y() >= r.height() - corner
        if corner_top and corner_left:
            return Qt.TopLeftCorner
        if corner_top and corner_right:
            return Qt.TopRightCorner
        if corner_bottom and corner_left:
            return Qt.BottomLeftCorner
        if corner_bottom and corner_right:
            return Qt.BottomRightCorner
        if left:
            return Qt.LeftEdge
        if right:
            return Qt.RightEdge
        if top:
            return Qt.TopEdge
        if bottom:
            return Qt.BottomEdge
        return None

    def _begin_resize(self, edge, gp):
        self._re_edge = edge
        self._re_geo = self.geometry()
        self._re_origin = QPoint(gp)

    def _apply_resize(self, gp):
        """按拖拽位移重算窗口几何。

        布局是 QVBoxLayout / QHBoxLayout，宽度高度一变会自动重排，
        所以缩放窗口不会把界面挤坏。
        """
        if self._re_edge is None or self._re_geo is None:
            return
        g, o, e = self._re_geo, self._re_origin, self._re_edge
        dx, dy = gp.x() - o.x(), gp.y() - o.y()
        mw, mh = self.minimumWidth(), self.minimumHeight()
        x, y, w, h = g.x(), g.y(), g.width(), g.height()
        if e in (Qt.LeftEdge, Qt.TopLeftCorner, Qt.BottomLeftCorner):
            w = max(mw, w - dx)
            x = g.x() + g.width() - w       # 拖左边时右边界保持不动
        if e in (Qt.RightEdge, Qt.TopRightCorner, Qt.BottomRightCorner):
            w = max(mw, w + dx)
        if e in (Qt.TopEdge, Qt.TopLeftCorner, Qt.TopRightCorner):
            h = max(mh, h - dy)
            y = g.y() + g.height() - h      # 拖上边时下边界保持不动
        if e in (Qt.BottomEdge, Qt.BottomLeftCorner, Qt.BottomRightCorner):
            h = max(mh, h + dy)
        self.setGeometry(x, y, w, h)

    def _end_resize(self):
        self._re_edge = None
        self._re_geo = None
        self._re_origin = None

    def _edge_forward(self, obj, ev):
        """子控件上的鼠标事件：若落在窗口边缘带，转为主窗口的缩放操作。

        返回 True 表示已消费，子控件不要再处理 —— 否则贴着边缘的控件
        会把拖拽吃掉。
        """
        t = ev.type()
        if t not in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress,
                     QEvent.Type.MouseButtonRelease):
            return False
        if self.isMaximized():
            return False
        # 标题栏三键优先，别在它们身上做缩放，否则点不到关闭
        if hasattr(self, "b_close") and obj in (self.b_min, self.b_max,
                                                self.b_close):
            return False
        gp = ev.globalPosition().toPoint()
        e = self._edge(self.mapFromGlobal(gp))
        if t == QEvent.Type.MouseButtonPress:
            if ev.button() != Qt.LeftButton or e is None:
                return False
            self._begin_resize(e, gp)
            return True
        if t == QEvent.Type.MouseMove:
            if self._re_edge is not None and (ev.buttons() & Qt.LeftButton):
                self._apply_resize(gp)
                return True
            cur = _EDGE_CURSORS.get(e)
            if cur is not None:
                obj.setCursor(cur)
            else:
                obj.unsetCursor()
            return False
        # MouseButtonRelease
        if self._re_edge is not None:
            self._end_resize()
            return True
        return False

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and not self.isMaximized():
            e = self._edge(ev.position().toPoint())
            if e is not None:
                self._begin_resize(e, ev.globalPosition().toPoint())
                return
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._re_edge is not None:
            self._end_resize()
            return
        super().mouseReleaseEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._re_edge is not None and ev.buttons() & Qt.LeftButton:
            self._apply_resize(ev.globalPosition().toPoint())
            return
        if self.isMaximized():
            self.setCursor(Qt.ArrowCursor)
            return
        self.setCursor(_EDGE_CURSORS.get(
            self._edge(ev.position().toPoint()), Qt.ArrowCursor))
        super().mouseMoveEvent(ev)

    def showEvent(self, ev):
        super().showEvent(ev)
        # 页面里的控件是动态生成的，每次显示都补装一遍过滤器
        self._install_edge_filters()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_F11:
            self._toggle_max()
        else:
            super().keyPressEvent(ev)

    # ---------------------------------------------------- 杂项
    def _update_bank_info(self):
        qs = self.p_bank.all
        if not qs:
            self.p_set.set_bank_info("尚无题库题卡。可在「题库题卡」页面批量选择 PDF 或图片。")
            self.set_status("等待导入题卡")
            return
        r = len({q.get("region") for q in qs if q.get("region")})
        d = len({q.get("date") for q in qs if q.get("date")})
        self.p_set.set_bank_info(
            f"本机已导入 {len(qs)} 张题卡，涉及 {r} 个地区、{d} 个考次。"
            "题目仅来自你导入的文件。")
        self.set_status(f"已导入 {len(qs)} 张题卡")

    def set_status(self, txt):
        self.status.setText(txt)

    def switch(self, i):
        if i < 0 or i >= self.stack.count():
            return
        if self._bank_exit_active:
            if i == 1:
                self._bank_exit_target = None
                self._bank_exit_active = False
                self.p_bank.cancel_exit()
            else:
                self._bank_exit_target = i
            return
        if (self.stack.currentIndex() == 1 and i != 1
                and self.isVisible() and motion_enabled()):
            self._bank_exit_target = i
            self._bank_exit_active = True
            self.p_bank.play_exit(self._finish_bank_page_exit)
            return
        self._switch_page_now(i)

    def _finish_bank_page_exit(self):
        self._bank_exit_active = False
        target = self._bank_exit_target
        self._bank_exit_target = None
        if target is not None:
            self._switch_page_now(target)

    def _switch_page_now(self, i, _intro_prepared=False, _ticket=None):
        if i < 0 or i >= self.stack.count():
            return
        if _intro_prepared:
            if _ticket != self._page_switch_generation:
                return
        else:
            self._page_switch_generation += 1
            _ticket = self._page_switch_generation
        previous_index = self.stack.currentIndex()
        if previous_index == i:
            if i == 2:
                self.p_know.show_teaching_menu()
            return
        self._cancel_page_motion()
        previous_page = self.stack.widget(previous_index)
        cancel_intro = getattr(previous_page, "cancel_intro", None)
        if callable(cancel_intro):
            cancel_intro()
        timer_exit_frames = (previous_page.timer_exit_frames(self.stack)
            if previous_index == 0 and self.isVisible() and motion_enabled() else None)
        page = self.stack.widget(i)
        if i == 2:
            page.prepare_teaching_menu()
        # 主题刷新在目标页仍隐藏时完成，避免用户已经看到新页后再经历
        # 一次全树样式重算与布局更新。
        if page in self._theme_dirty_pages:
            self._refresh_page_theme(page, cfg.get("theme", "system"),
                                     force=True)
        prepare_intro = getattr(page, "prepare_intro", None)
        if callable(prepare_intro):
            if (not _intro_prepared and i == 4
                    and getattr(page, "async_intro_prepare", False)):
                if prepare_intro(
                        lambda target=i, ticket=_ticket:
                        self._resume_page_switch(target, ticket)):
                    return
            elif not _intro_prepared:
                prepare_intro()
        self.stack.setCurrentIndex(i)
        self._animate_page_change(previous_index, i)
        if timer_exit_frames:
            from workbench_ui import TimerVisualTransition
            self._timer_stack_exit = TimerVisualTransition(self.stack, (), entering=False, frames=timer_exit_frames)
        accents = {
            4: "qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #ba4967,stop:.5 #7753a1,stop:1 #c14a29)",
            1: "#ba4967", 0: "#7753a1", 2: "#c14a29", 3: "#1A56C4",
        }
        if T.app_theme == "chiyo":
            accents = {index: "#E8485D" for index in accents}
        elif T.app_theme == "industrial":
            accents = {index: "#FF453E" for index in accents}
        self.page_accent.setStyleSheet("background:%s; border:0;" % accents.get(i, "#1A56C4"))
        self.page_accent.setVisible(
            T.app_theme not in ("industrial", "anna"))
        self._style_module_headings((page,))
        for j, b in enumerate(self.navs):
            b.refresh(b.page_index == i)
        self._move_nav_plate(i)
        if hasattr(page, "on_show"):
            page.on_show()
        # 页面位置保持稳定，避免动画中点击目标移动。
        if i == 4:
            self.p_home.questions = self.p_bank.all
        if i != 1:
            self.set_status({4: "选择教学、练习或限时模拟，开始你的学习。",
                             0: "计时可独立使用；切换页面后仍会继续。",
                             2: "选择课程、工具或图形展示，开始学习。",
                             3: "设置会自动保存在本机。"}.get(i, "就绪"))
        intro = getattr(page, "play_intro", None)
        if self.isVisible() and callable(intro):
            intro()
        log.info("切换到页面 %d", i)

    def _resume_page_switch(self, index, ticket):
        """网页首帧准备完成后继续切页，避免先露出完整首页。"""
        if ticket != self._page_switch_generation:
            return
        self._switch_page_now(index, _intro_prepared=True, _ticket=ticket)

    def _play_page_intro(self, index):
        """页面真正显示后再依次播放卡片入场，不干扰后台计时。"""
        if self.stack.currentIndex() != index or not self.isVisible():
            return
        page = self.stack.widget(index)
        intro = getattr(page, "play_intro", None)
        if callable(intro):
            intro()

    def _style_module_headings(self, only_pages=None):
        """为原生模块标题加上与首页卡片对应的强调色识别。"""
        accents = {1: "#ba4967", 0: "#7753a1", 2: "#c14a29", 3: "#1A56C4"}
        if T.app_theme == "chiyo":
            accents = {index: "#E8485D" for index in accents}
        elif T.app_theme == "industrial":
            accents = {index: T.P["accent_fg"] for index in accents}
        pages = {0: self.p_timer, 1: self.p_bank, 2: self.p_know, 3: self.p_set}
        if only_pages is not None:
            allowed = set(only_pages)
            pages = {index: page for index, page in pages.items()
                     if page in allowed}
        for index, page in pages.items():
            for title in page.findChildren(QLabel):
                if title.objectName() == "H1":
                    title.setStyleSheet(
                        f"font-size:{int(30*self.ui_scale)}px; font-weight:800;"
                        f"color:{T.P['ink']}; letter-spacing:1px;"
                        f"background:transparent; border-left:4px solid {accents[index]};"
                        "padding-left:11px;")

    def _sync_nav_plate_layout(self):
        if hasattr(self, "stack"):
            self._move_nav_plate(self.stack.currentIndex(), motion_enabled())

    def _move_nav_plate(self, page_index, animate=True):
        """让同一块选中底板沿导航项当前位置连续移动。"""
        button = next((b for b in self.navs if b.page_index == page_index), None)
        if button is None or not button.isVisible():
            return
        point = button.mapTo(self.side, QPoint(0, 0))
        target = (QRect(point.x(), point.y() - 8,
                        button.width() + 30, button.height() + 16)
                  if T.app_theme == "chiyo" else QRect(point, button.size()))
        # 导航刷新会重复产生布局事件，目标未变时让现有动画继续。
        if animate and target == getattr(self, '_nav_target', None):
            return
        self._nav_target = target
        previous = getattr(self, "_nav_motion", None)
        self._nav_motion = None
        if previous is not None:
            # DeleteWhenStopped 动画由 Qt 自行释放；从当前底板位置接续新目标。
            try:
                previous.stop()
            except RuntimeError:
                # 自然结束后 Qt 已回收动画，旧 Python 引用只用于识别该状态。
                pass
        button = next((b for b in self.navs if b.page_index == page_index), None)
        if button is None or not button.isVisible():
            return
        point = button.mapTo(self.side, QPoint(0, 0))
        target = (QRect(point.x(), point.y() - 8,
                        button.width() + 30, button.height() + 16)
                  if T.app_theme == "chiyo" else QRect(point, button.size()))
        if target.width() <= 0 or target.height() <= 0:
            return
        self.nav_plate.raise_()
        for nav in self.navs:
            nav.raise_()
        self.nav_plate.lower()
        if not animate or not motion_enabled() or self.nav_plate.geometry().isNull():
            self.nav_plate.setGeometry(target)
            return
        motion = QPropertyAnimation(self.nav_plate, b"geometry", self)
        motion.setDuration(620)
        motion.setStartValue(self.nav_plate.geometry())
        motion.setEndValue(target)
        motion.setEasingCurve(QEasingCurve.InOutCubic)
        motion.finished.connect(
            lambda current=motion: self._clear_nav_motion(current))
        motion.start(QPropertyAnimation.DeleteWhenStopped)
        self._nav_motion = motion

    def _clear_nav_motion(self, motion):
        if getattr(self, "_nav_motion", None) is motion:
            self._nav_motion = None

    def _animate_page_change(self, previous_index, next_index, snapshot=None):
        """清除旧版截图过渡；页面切换由页面自身的入场与导航底板完成。"""
        self.page_accent.setMaximumWidth(16777215)
        self._cancel_page_motion()

    def _cancel_page_motion(self):
        """在页面销毁/重建前清理切换色带动画。"""
        timer_exit = getattr(self, '_timer_stack_exit', None)
        if timer_exit is not None:
            timer_exit.dispose()
            self._timer_stack_exit = None
        group = self._page_motion
        layer = self._page_motion_overlay
        self._page_motion = None
        self._page_motion_overlay = None
        if group is not None:
            group.stop()
            self.page_accent.setMaximumWidth(16777215)
        if layer is not None:
            layer.hide()
            layer.deleteLater()
        self._page_motion_incoming = None
        self._page_motion_overlay = None

    def _sync_mosaic_cursor(self):
        if not self.isVisible() or self.isMinimized():
            return
        point = self.stage.mapFromGlobal(QCursor.pos())
        if self.stage.rect().contains(point):
            self.stage.set_mosaic_cursor(point)
        else:
            self.stage.set_mosaic_cursor(QPoint(-10000, -10000))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        page = getattr(self, "stack", None)
        page = page.currentWidget() if page is not None else None
        cancel_intro = getattr(page, "cancel_intro", None)
        if callable(cancel_intro):
            cancel_intro()
        startup = getattr(self, "_startup_motion", None)
        if startup is not None:
            startup.stop()
            self._startup_motion = None
            side_home = getattr(self, "_startup_side_home", None)
            if side_home is not None:
                self.side.move(side_home)
        if getattr(self, "_page_motion_overlay", None) is not None:
            self._cancel_page_motion()
        reveal = getattr(self, "_theme_reveal", None)
        if reveal is not None:
            if reveal._motion.state() == QVariantAnimation.Running:
                self._clear_theme_reveal()
            else:
                # 换肤布局可能调整窗口尺寸，快照准备期间继续保留过渡层。
                reveal.setGeometry(self.shell.rect())
        if getattr(self, "_theme_reveal", None) is None:
            self._thaw_theme_updates()
        if hasattr(self, "stage"):
            r = self.stage.rect()
            self.content.setGeometry(r)
            self.overlay.setGeometry(r)
            self.mosaic_layer.setGeometry(self.content.rect())
            if hasattr(self, 'stack'):
                # 延后布局结束时读取当前页，避免旧页面回调覆盖新选中位置。
                QTimer.singleShot(0, lambda: self._move_nav_plate(self.stack.currentIndex(), False))
        self._layout_chiyo_page_artwork()
        self.update()          # 阴影要随窗口尺寸重画

    def closeEvent(self, ev):
        if not getattr(self, "_exit_confirmed", False) and self.p_timer.phase in (0, 1, 2) and \
                self.p_timer.phase in (self.p_timer.READ, self.p_timer.DOING):
            r = QMessageBox.question(
                self, "计时进行中",
                "计时正在进行，确定退出吗？本次计时将丢失。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                ev.ignore()
                return
        self._exit_confirmed = True
        # 网络任务异步结束后再销毁窗口，取消和退出时界面仍可响应。
        pending_answers = False
        for widget in QApplication.topLevelWidgets():
            shutdown = getattr(widget, "shutdown_workers", None)
            if callable(shutdown) and shutdown() is False:
                pending_answers = True
        if pending_answers:
            ev.ignore()
            self.p_bank.set_status("正在停止后台解题并回收连接…")
            QTimer.singleShot(100, self.close)
            return
        # 先中止拥有明确归属的界面动画与后台题图任务，再销毁控件树。
        # 否则窗口关闭与 WAAPI/Qt 动画回调同时结束时，回调可能碰到已销毁目标。
        self._cancel_page_motion()
        self._clear_theme_reveal()
        for page in (getattr(self, "p_timer", None),
                     getattr(self, "p_bank", None),
                     getattr(self, "p_know", None),
                     getattr(self, "p_set", None),
                     getattr(self, "p_home", None)):
            cancel_intro = getattr(page, "cancel_intro", None)
            if callable(cancel_intro):
                cancel_intro()
        # 参考答案浮层是独立窗口，关闭时先取消其网络任务并等待线程退出。
        for widget in QApplication.topLevelWidgets():
            shutdown_workers = getattr(widget, "shutdown_workers", None)
            if callable(shutdown_workers):
                try:
                    shutdown_workers()
                    widget.close()
                except Exception:
                    log.exception("关闭后台答案任务失败")
        # 图片导入和 PDF 预览线程支持协作停止，退出前等它们完成收尾。
        for worker in self.findChildren(QThread):
            if not worker.isRunning():
                continue
            stop = getattr(worker, "cancel", None) or getattr(worker, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    log.exception("停止后台线程失败")
            worker.requestInterruption()
            worker.wait()
        try:
            pool = QThreadPool.globalInstance()
            pool.clear()
            pool.waitForDone()
        except Exception:
            log.exception("等待图片后台任务退出失败")
        try:
            self._sound.shutdown()
        except Exception:
            log.exception("关闭音效播放器失败")
        if self.timeup:
            try:
                self.timeup.close()
            except Exception:
                pass
        if self.clock:
            cfg.set_("clock_x", self.clock.x())
            cfg.set_("clock_y", self.clock.y())
            # ★ 时钟是无父置顶窗，不关它进程就退不干净 ——
            #   实测主窗口关了、时钟留在桌面上，任务管理器里挂着
            #   两个 "CAAC Ground Station"（五三 2026-09-21 报的 bug）。
            try:
                self.clock.close()
                self.clock.deleteLater()
            except Exception:
                pass
            self.clock = None
        cfg.save()
        log.info("程序退出")
        ev.accept()


class _WinButton(QPushButton):
    """标题栏三键：最小化 / 最大化 / 关闭。矢量图标，随主题换色"""

    KINDS = {"min": "minimize", "max": "maximize", "close": "close"}
    TIPS = {"min": "最小化", "max": "最大化", "close": "关闭"}

    def __init__(self, kind, danger=False, scale=1.0):
        super().__init__()
        self.kind = kind
        self.danger = danger
        self._scale = scale
        self.setFixedSize(int(34 * scale), int(28 * scale))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(self.TIPS.get(kind, ""))
        self.refresh_icon()

    def _size(self):
        return max(13, int(15 * self._scale))

    def _paint(self, col):
        name = self.KINDS.get(self.kind, "close")
        sz = self._size()
        self.setIcon(pm_to_icon(getattr(Icons, name)(col, size=sz)))
        self.setIconSize(QSize(sz, sz))

    def enterEvent(self, ev):
        # 关闭键 hover 后底色转红，图标要跟着变白，否则对比度太低看不清
        if self.danger:
            self._paint("#ffffff")
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        if self.danger:
            self._paint(T.P["ink2"])
        super().leaveEvent(ev)

    def refresh_icon(self):
        """标题栏三键图标。

        旧实现是手搓坐标系：先 pt.scale(3, 3) 再 pt.setWindow(0, 0, 16, 16)。
        后者会把前者的变换整个覆盖掉 —— 那行 scale 纯属死代码，还误导后来的人
        以为坐标系是 3 倍。现在统一走 Icons._canvas / _out，不再手搓。
        """
        self._paint(T.P["ink2"])
        hover = ("rgba(229,72,77,0.92)" if self.danger
                 else ("rgba(26,86,196,0.14)" if T.mode == "light"
                       else "rgba(255,255,255,0.16)"))
        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                border-radius: {max(4, int(8 * self._scale))}px;
                padding: 0px;
            }}
            QPushButton:hover {{ background: {hover}; }}
            QPushButton:pressed {{ background: rgba(0, 0, 0, 0.10); }}
        """)
