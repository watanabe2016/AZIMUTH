"""动效方案对应的计时控制、学习进度与 A4 学习卡入口。"""
import datetime
import time
from PySide6.QtCore import Qt, QRectF, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QPixmap, QColor, QFont
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSpinBox,
                               QDialog, QScrollArea, QGridLayout, QCheckBox, QSlider, QSizePolicy)
import config as cfg
import theme as T
from ui_kit import IconButton, Combo, animations_enabled, show_window_animated
from motion_polish import fold
from motion_runtime import motion_page


def _soft_edge(color, alpha):
    shade = QColor(color)
    shade.setAlpha(alpha)
    return f"rgba({shade.red()},{shade.green()},{shade.blue()},{shade.alpha()})"


def _style_timer_action_buttons(page):
    edge = _soft_edge(T.P['accent_fg'], 28)
    hover_edge = _soft_edge(T.P['accent_fg'], 48)
    focus_edge = _soft_edge(T.P['accent_fg'], 64)
    for button in (getattr(page, 'b_custom', None), getattr(page, 'b_history', None)):
        if button is None:
            continue
        button.setStyleSheet(
            "QPushButton#TimerQuickAction{"
            f"color:{T.P['ink']};background:{T.P['panel_soft']};"
            f"border:1px solid {edge};border-radius:10px;"
            "min-height:38px;max-height:38px;padding:0 10px;}"
            "QPushButton#TimerQuickAction:hover{"
            f"background:{T.P['panel_hover']};border-color:{hover_edge};}}"
            "QPushButton#TimerQuickAction:pressed{"
            f"background:{T.P['accent_soft']};border-color:{hover_edge};}}"
            "QPushButton#TimerQuickAction:focus{"
            f"border-color:{focus_edge};}}"
            "QPushButton#TimerQuickAction:disabled{"
            f"color:{T.P['ink3']};background:{T.P['panel_soft']};border-color:{edge};}}")


def install_timer_controls(page, layout):
    actions = QGridLayout()
    page.timer_action_grid=actions
    page.b_custom = IconButton("自定义时长", "gear", "default", font_size=12)
    page.b_extend = IconButton("增加 1 分钟", "timer", "default", font_size=12)
    page.b_skip = IconButton("结束阅题", "reset", "default", font_size=12)
    page.b_history = IconButton("练习记录", "doc", "default", font_size=12)
    for button in (page.b_custom, page.b_history):
        button.setObjectName('TimerQuickAction')
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        button.setFixedHeight(40)
    for index, (button, callback) in enumerate(((page.b_custom, page.toggle_custom_duration),
                             (page.b_extend, page.extend_time),
                             (page.b_skip, page.skip_stage),
                             (page.b_history, page.toggle_history))):
        button.clicked.connect(callback)
        button.setMinimumHeight(38)
        actions.addWidget(button, index // 2, index % 2)
    _style_timer_action_buttons(page)
    layout.insertLayout(layout.indexOf(page.duration_editor) + 1, actions)
    page.custom_duration = QWidget(page.right_panel)
    page.custom_duration.setObjectName('CustomDurationForm')
    form = QGridLayout(page.custom_duration)
    form.setContentsMargins(12, 12, 12, 12)
    form.setHorizontalSpacing(12)
    form.setVerticalSpacing(8)
    page.custom_read = QSpinBox()
    page.custom_do = QSpinBox()
    for edit, maximum, value in ((page.custom_read, 3600, page.read_total),
                                 (page.custom_do, 7200, page.do_total)):
        edit.setRange(0 if edit is page.custom_read else 10, maximum)
        edit.setSuffix(" 秒")
        edit.setValue(value)
        edit.setFixedHeight(44)
        edit.setMinimumWidth(0)
        edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        edit.setObjectName('CustomTimerSeconds')
    page.custom_read.setAccessibleName('自定义阅题时长，秒')
    page.custom_do.setAccessibleName('自定义作答时长，秒')
    form.addWidget(QLabel("阅题时长"), 0, 0)
    form.addWidget(QLabel("作答时长"), 0, 1)
    form.addWidget(page.custom_read, 1, 0)
    form.addWidget(page.custom_do, 1, 1)
    form.setColumnStretch(0, 1)
    form.setColumnStretch(1, 1)
    apply = IconButton("应用时长", "check", "primary", font_size=13)
    apply.setFixedHeight(44)
    page.b_duration_apply = apply
    apply.clicked.connect(page.apply_custom_duration)
    form.addWidget(apply, 2, 0, 1, 2)
    refresh_timer_inputs(page)
    page.custom_duration.hide()
    layout.insertWidget(layout.indexOf(page.duration_editor) + 2, page.custom_duration)
    page.history_panel = QWidget(page.right_panel)
    history = QVBoxLayout(page.history_panel)
    history.setContentsMargins(4, 4, 4, 4)
    page.history_label = QLabel()
    page.history_label.setWordWrap(True)
    page.history_label.setObjectName("Dim")
    history.addWidget(page.history_label)
    page.history_panel.hide()
    layout.insertWidget(layout.indexOf(page.duration_editor) + 3, page.history_panel)


def refresh_timer_inputs(page):
    """局部样式随主题更新，避免系统原生白底输入框穿透。"""
    edge = _soft_edge(T.P['accent_fg'], 28)
    focus_edge = _soft_edge(T.P['accent_fg'], 64)
    page.custom_duration.setStyleSheet(
        f"QWidget#CustomDurationForm{{background:{T.P['panel_soft']};border:1px solid {edge};border-radius:12px;}}"
        f"QWidget#CustomDurationForm QLabel{{color:{T.P['ink2']};background:transparent;border:0;font-size:12px;}}")
    for edit in (page.custom_read, page.custom_do):
        edit.setStyleSheet(
            f"QSpinBox#CustomTimerSeconds{{background:{T.P['input_bg']};color:{T.P['ink']};"
            f"border:1px solid {edge};border-radius:10px;padding:6px 28px 6px 10px;min-height:30px;max-height:30px;"
            f"font-size:14px;selection-background-color:{T.C_BLUE};}}"
            "QSpinBox::up-button{width:22px;border:0;}QSpinBox::down-button{width:22px;border:0;}"
            f"QSpinBox:focus{{border-color:{focus_edge};}}")


def toggle_custom_duration(page):
    controller = getattr(page.custom_duration, "_polish_fold", None)
    fold(page.custom_duration, not (controller.opened if controller else not page.custom_duration.isHidden()))


def apply_custom_duration(page):
    if page.phase != page.IDLE:
        page.hint.setText("请先重置计时，再修改训练时长。")
        return
    page.set_read_len(page.custom_read.value())
    page.set_do_len(page.custom_do.value())
    page.hint.setText(f"已设置阅题 {page.read_total} 秒、作答 {page.do_total} 秒")
    fold(page.custom_duration, False)


def extend_time(page):
    if page.phase not in (page.READ, page.DOING) or page._phase_deadline is None:
        page.hint.setText("开始计时后可以增加 1 分钟。")
        return
    page._phase_deadline += 60
    page.remain += 60
    page.ring.total += 60
    page.ring.set_remain(page.remain)
    page._push_clock(running=page.tick.isActive())
    page.hint.setText("当前阶段已增加 1 分钟")


def skip_stage(page):
    if page.phase == page.READ:
        page._to_doing(manual=True)
    else:
        page.hint.setText("此操作只用于结束阅题，作答阶段仍按真实时间计时。")


def save_session(page):
    if page._session_started_mono is None:
        return
    elapsed = max(0, int(time.monotonic() - page._session_started_mono - page._elapsed_paused_total))
    stamp = page.start_at.isoformat() if page.start_at else ""
    if getattr(page, "_saved_session_stamp", None) == stamp:
        return
    page._saved_session_stamp = stamp
    records = list(cfg.get("timer_sessions", []))
    records.append({"date": datetime.datetime.now().strftime("%m-%d %H:%M"),
                    "read": page.read_total, "answer": page.do_total, "elapsed": elapsed})
    cfg.set_("timer_sessions", records[-50:])
    update_history(page)


def update_history(page):
    records = cfg.get("timer_sessions", [])
    if records:
        page.history_label.setText("最近练习\n" + "\n".join(
            f"{item['date']}　阅题 {item['read']} 秒 · 作答 {item['answer']} 秒 · 用时 {item['elapsed']} 秒"
            for item in reversed(records[-8:])))
    else:
        page.history_label.setText("还没有完成的限时练习记录。完成一轮后会自动记录。")


def toggle_history(page):
    update_history(page)
    controller = getattr(page.history_panel, "_polish_fold", None)
    fold(page.history_panel, not (controller.opened if controller else not page.history_panel.isHidden()))


def install_teaching_controls(page):
    host = QWidget(page.feature_detail)
    box = QVBoxLayout(host)
    box.setContentsMargins(0, 0, 0, 0)
    first = QHBoxLayout()
    page.demo_pause = IconButton("暂停", "timer", "default", font_size=12)
    page.demo_step = IconButton("下一步", "reset", "default", font_size=12)
    page.demo_speed = Combo()
    for text, value in (("0.5 倍", .5), ("正常速度", 1.0), ("1.5 倍", 1.5)):
        page.demo_speed.addItem(text, value)
    page.demo_speed.setCurrentIndex(1)
    page.demo_speed.changed.connect(page.set_demo_speed)
    page.demo_pause.clicked.connect(page.pause_demo)
    page.demo_step.clicked.connect(page.step_demo)
    for widget in (page.demo_pause, page.demo_speed):
        widget.setMinimumHeight(38)
        first.addWidget(widget)
    box.addLayout(first)
    second = QHBoxLayout()
    page.demo_reset = IconButton("复位", "reset", "default", font_size=12)
    page.demo_card = IconButton("A4 学习卡", "doc", "default", font_size=12)
    page.b_complete = IconButton("已掌握", "check", "primary", font_size=12)
    page.demo_reset.clicked.connect(page.reset_demo)
    page.demo_card.clicked.connect(page.open_lesson_card)
    page.b_complete.clicked.connect(page.complete_lesson)
    for widget in (page.demo_reset, page.demo_card, page.b_complete):
        widget.setMinimumHeight(38)
        second.addWidget(widget)
    page.demo_advanced = QWidget(host)
    advanced = QVBoxLayout(page.demo_advanced)
    advanced.setContentsMargins(0, 8, 0, 0)
    advanced.setSpacing(10)
    first.insertWidget(1, page.demo_reset)
    second.removeWidget(page.demo_reset)
    second.insertWidget(0, page.demo_step)
    advanced.addLayout(second)
    page.b_demo_more = IconButton('更多学习选项', 'gear', 'default', font_size=12)
    page.b_demo_more.setMinimumHeight(38)
    page.b_demo_more.clicked.connect(lambda: fold(page.demo_advanced,
        not (getattr(page.demo_advanced, '_polish_fold', None).opened
             if getattr(page.demo_advanced, '_polish_fold', None) else not page.demo_advanced.isHidden())))
    box.addWidget(page.b_demo_more)
    box.addWidget(page.demo_advanced)
    annotations = QHBoxLayout()
    page.demo_measurements = QCheckBox("相对边长")
    page.demo_angles = QCheckBox("内角")
    page.demo_measurements.toggled.connect(page.route_demo.toggle_measurements)
    page.demo_angles.toggled.connect(page.route_demo.toggle_angles)
    annotations.addWidget(page.demo_measurements)
    annotations.addWidget(page.demo_angles)
    advanced.addLayout(annotations)
    page.feature_detail_layout.itemAt(6).layout().removeWidget(page.demo_replay)
    advanced.addWidget(page.demo_replay)
    # 低频演示设置收纳到同一个展开区，原回调和状态保持不变。
    for widget in (page.demo_zone, page.demo_mode):
        page.feature_detail_layout.removeWidget(widget)
        advanced.addWidget(widget)
    wind_host = QWidget(host)
    wind = QHBoxLayout(wind_host)
    wind.setContentsMargins(0, 0, 0, 0)
    wind.addWidget(QLabel("示意风力"))
    page.demo_strength = QSlider(Qt.Horizontal)
    page.demo_strength.setRange(10, 200)
    page.demo_strength.setValue(100)
    page.demo_strength.setAccessibleName("教学示意风力，不用于计算真实题目")
    page.demo_strength.valueChanged.connect(lambda value: page.concept_demo.set_strength(value / 100))
    wind.addWidget(page.demo_strength, 1)
    page.demo_wind_row = wind_host
    box.addWidget(wind_host)
    wind_host.hide()
    page.course_progress = QLabel()
    page.course_progress.setObjectName("Dim")
    advanced.addWidget(page.course_progress)
    page.demo_advanced.hide()
    page.demo_controls = host
    page.feature_detail_layout.insertWidget(7, host)
    refresh_course_progress(page)


def refresh_feature_controls(page):
    """补齐运行中换肤时新增控件的颜色，避免保留创建时的浅色样式。"""
    if hasattr(page, 'custom_duration'):
        refresh_timer_inputs(page)
    for name in ('b_custom', 'b_extend', 'b_skip', 'b_history', 'b_duration_apply',
                 'demo_pause', 'demo_step', 'demo_reset', 'demo_card', 'b_complete', 'b_demo_more'):
        button = getattr(page, name, None)
        if button is not None:
            button.refresh()
    _style_timer_action_buttons(page)
    if hasattr(page, 'demo_speed'):
        page.demo_speed.refresh()
        page.course_progress.setStyleSheet(f"color:{T.P['ink3']};background:transparent;font-size:12px;")
        page.demo_strength.setStyleSheet(
            f"QSlider::groove:horizontal{{height:4px;background:{T.P['input_border']};border-radius:2px;}}"
            f"QSlider::handle:horizontal{{width:12px;margin:-5px 0;background:{T.C_BLUE};border-radius:6px;}}"
            f"QSlider::sub-page:horizontal{{background:{T.C_BLUE};border-radius:2px;}}")


def active_demo(page):
    if page.figure_stack.currentWidget() is page.hero_figure:
        page._toggle_route_demo()
    return page.route_demo if page.demo_mode.currentData() in (None, "route") else page.concept_demo


def pause_demo(page):
    canvas = active_demo(page)
    canvas.toggle_pause()
    page.demo_pause.setText("继续" if canvas.paused else "暂停")


def step_demo(page):
    active_demo(page).step_forward()
    page.demo_pause.setText("继续")
    page.update_demo_caption("单步观察：核对当前点位、航向和连线关系。")


def set_demo_speed(page):
    speed = float(page.demo_speed.currentData() or 1)
    page.route_demo.set_speed(speed)
    page.concept_demo.set_speed(speed)


def reset_demo(page):
    canvas = active_demo(page)
    canvas.stop() if hasattr(canvas, "stop") else canvas.motion.stop()
    canvas._advance(0.0)
    canvas.paused = True
    page.demo_pause.setText("继续")


def refresh_course_progress(page):
    values = set(cfg.get("lesson_completed_shapes", []))
    page.course_progress.setText(f"图形学习进度　{len(values)} / 8")
    page.b_complete.setText("已掌握 ✓" if page._shape_index in values else "已掌握")


def complete_lesson(page):
    values = set(cfg.get("lesson_completed_shapes", []))
    values.add(page._shape_index)
    cfg.set_("lesson_completed_shapes", sorted(values))
    refresh_course_progress(page)


class A4Preview(QWidget):
    """缓存纸张原图，只在绘制时进行等比缩放。"""
    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.image = image
        self.scale = 1.0
        self.setMinimumSize(300, 400)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(T.P["panel_bg"]))
        w, h = self.image.width() * self.scale, self.image.height() * self.scale
        p.drawPixmap(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h),
                     self.image, QRectF(self.image.rect()))
        p.end()


@motion_page
class LessonPreviewDialog(QDialog):
    def __init__(self, page):
        super().__init__(page)
        self.setWindowTitle("A4 学习卡 · 教学示例")
        self.resize(700, 850)
        root = QVBoxLayout(self)
        tools = QHBoxLayout()
        for text, function in (("整页适配", self.fit_page),
                               ("−", lambda: self.zoom_by(.85)),
                               ("+", lambda: self.zoom_by(1.15)),
                               ("关闭", self.close)):
            button = IconButton(text, None, "default")
            button.setMinimumHeight(38)
            button.clicked.connect(function)
            tools.addWidget(button)
        root.addLayout(tools)
        # 210:297 的纸张尺寸始终不变，图形和文字绘入缓存后不逐帧重排。
        sheet = QPixmap(840, 1188)
        sheet.fill(Qt.white)
        painter = QPainter(sheet)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setPen(QColor("#182638"))
        font = QFont(T.pick_font())
        font.setPixelSize(28)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(50, 40, 740, 65), Qt.AlignCenter, page.hero_title.text())
        illustration = page.hero_figure._source_pixmap
        if illustration is not None and not illustration.isNull():
            factor = min(700 / illustration.width(), 600 / illustration.height())
            w, h = illustration.width() * factor, illustration.height() * factor
            painter.drawPixmap(QRectF((840 - w) / 2, 130, w, h), illustration, QRectF(illustration.rect()))
        font.setPixelSize(21)
        font.setBold(False)
        painter.setFont(font)
        painter.drawText(QRectF(60, 800, 720, 280), Qt.TextWordWrap,
                         page.hero_desc.text() + "\n\n" + page.hero_steps.text())
        painter.drawText(QRectF(60, 1130, 720, 30), Qt.AlignCenter, "教学示例 · 非考试题卡")
        painter.end()
        self.preview = A4Preview(sheet)
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setWidget(self.preview)
        root.addWidget(self.preview_scroll, 1)
        self.motion = QVariantAnimation(self)
        self.motion.setDuration(520)
        self.motion.setEasingCurve(QEasingCurve.InOutCubic)
        self.motion.valueChanged.connect(self._scale)

    def _scale(self, value):
        self.preview.scale = float(value)
        self.preview.setMinimumSize(round(840 * self.preview.scale + 24),
                                    round(1188 * self.preview.scale + 24))
        self.preview.update()

    def zoom_by(self, factor):
        self._animate_scale(max(.1, min(2.0, self.preview.scale * factor)))

    def fit_page(self):
        viewport = self.preview_scroll.viewport()
        self._animate_scale(min((viewport.width() - 24) / 840,
                                (viewport.height() - 24) / 1188))

    def _animate_scale(self, target):
        self.motion.stop()
        if not animations_enabled():
            self._scale(target)
            return
        self.motion.setStartValue(self.preview.scale)
        self.motion.setEndValue(target)
        self.motion.start()

    def hideEvent(self, event):
        self.motion.stop()
        super().hideEvent(event)


def open_lesson_card(page):
    page._lesson_card = LessonPreviewDialog(page)
    show_window_animated(page._lesson_card)
    page._lesson_card.fit_page()
