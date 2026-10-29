"""导入流程共用的圆角窗口外壳与主题样式。"""

from PySide6.QtCore import Qt, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QFrame, QGraphicsDropShadowEffect,
                               QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

import theme as T


def _palette():
    palette = T.P
    surface = palette.get("panel_bg", palette.get("glass", "#FFFFFF"))
    return {
        "surface": surface,
        "raised": palette.get("panel_soft", palette.get("glass_hi", surface)),
        "edge": palette.get("glass_edge", "#536172"),
        "ink": palette.get("ink", "#F1F4F8"),
        "muted": palette.get("ink3", "#AEB8C6"),
        "blue": T.C_BLUE,
        "red": T.C_RED,
        "selection": palette.get("select", T.C_BLUE),
    }


class ImportDialogShell(QDialog):
    """为导入中的进度、管理和校对窗口提供统一圆角表面。"""

    def __init__(self, title, parent=None, size=(720, 500), minimum=(420, 260)):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.resize(*size)
        self.setMinimumSize(*minimum)
        self._drag_offset = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(0)
        self.surface = QFrame(self)
        self.surface.setObjectName("ImportDialogSurface")
        shadow = QGraphicsDropShadowEffect(self.surface)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 92))
        self.surface.setGraphicsEffect(shadow)
        outer.addWidget(self.surface)

        shell = QVBoxLayout(self.surface)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        self.header = QWidget(self.surface)
        self.header.setObjectName("ImportDialogHeader")
        header = QHBoxLayout(self.header)
        header.setContentsMargins(18, 0, 10, 0)
        header.setSpacing(10)
        self.title_label = QLabel(title, self.header)
        self.title_label.setObjectName("ImportDialogTitle")
        header.addWidget(self.title_label, 1)
        self.header_close_button = QPushButton("×", self.header)
        self.header_close_button.setObjectName("ImportDialogClose")
        self.header_close_button.setFixedSize(30, 30)
        self.header_close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header_close_button.setToolTip("关闭窗口")
        self.header_close_button.clicked.connect(self.reject)
        header.addWidget(self.header_close_button)
        self.header.setFixedHeight(54)
        self.header.mousePressEvent = self._drag_press
        self.header.mouseMoveEvent = self._drag_move
        self.header.mouseReleaseEvent = self._drag_release
        shell.addWidget(self.header)

        self.separator = QFrame(self.surface)
        self.separator.setObjectName("ImportDialogSeparator")
        self.separator.setFixedHeight(1)
        shell.addWidget(self.separator)
        self.body = QWidget(self.surface)
        self.body.setObjectName("ImportDialogBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(18, 14, 18, 16)
        self.body_layout.setSpacing(12)
        shell.addWidget(self.body, 1)
        self.refresh_dialog_theme()

    def refresh_dialog_theme(self):
        colors = _palette()
        self.surface.setStyleSheet(
            "QFrame#ImportDialogSurface{background:%s;border:1px solid %s;"
            "border-radius:16px;}" % (colors["surface"], colors["edge"]))
        self.header.setStyleSheet("background:transparent;")
        self.body.setStyleSheet("background:transparent;")
        self.title_label.setStyleSheet(
            "font-size:14px;font-weight:700;color:%s;background:transparent;" %
            colors["ink"])
        self.separator.setStyleSheet("background:%s;border:0;" % colors["edge"])
        self.header_close_button.setStyleSheet(
            "QPushButton{color:%s;background:transparent;border:0;border-radius:8px;"
            "font-size:21px;} QPushButton:hover{background:%s;color:%s;}" %
            (colors["muted"], colors["raised"], colors["ink"]))
        self.setStyleSheet(
            "QDialog{color:%s;} QLabel{color:%s;background:transparent;}"
            "QLineEdit,QPlainTextEdit,QListWidget,QTableWidget{color:%s;"
            "background:%s;border:1px solid %s;border-radius:8px;padding:6px;"
            "selection-background-color:%s;}"
            "QHeaderView::section{color:%s;background:%s;border:0;"
            "border-bottom:1px solid %s;padding:8px;font-weight:700;}"
            "QTableWidget{gridline-color:%s;}"
            "QTableWidget::item{padding:5px;}"
            "QPushButton{color:%s;background:%s;border:0;"
            "border-radius:9px;padding:7px 13px;min-height:20px;}"
            "QPushButton:hover{background:%s;}"
            "QPushButton:disabled{color:%s;background:%s;}"
            "QDialogButtonBox QPushButton{min-width:86px;}" % (
                colors["ink"], colors["ink"], colors["ink"], colors["raised"],
                colors["edge"], colors["selection"], colors["ink"],
                colors["surface"], colors["edge"], colors["edge"],
                colors["ink"], colors["raised"], colors["selection"],
                colors["muted"], colors["surface"]))

    def _drag_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _drag_move(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def _drag_release(self, _event):
        self._drag_offset = None


class ImportProgressDialog(ImportDialogShell):
    """兼容原 QProgressDialog 调用方式的导入进度窗口。"""

    canceled = Signal()

    def __init__(self, maximum, parent=None):
        super().__init__("正在导入题卡", parent, size=(460, 252), minimum=(420, 230))
        self._maximum = max(1, int(maximum))
        self._value = 0
        self._was_canceled = False
        self.title_label.setText("正在导入题卡")
        self.header_close_button.clicked.disconnect()
        self.header_close_button.clicked.connect(self.cancel)

        self.status_label = QLabel("正在准备文件…", self.body)
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(38)
        self.body_layout.addWidget(self.status_label)
        self.stage_labels = []
        stages = QHBoxLayout()
        for text in ('准备文件', '识别题卡', '等待校对'):
            label = QLabel(text, self.body)
            label.setAlignment(Qt.AlignCenter)
            stages.addWidget(label, 1)
            self.stage_labels.append(label)
        self.body_layout.addLayout(stages)
        self.progress = QProgressBar(self.body)
        self.progress.setRange(0, self._maximum)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.body_layout.addWidget(self.progress)
        self.percent_label = QLabel("0%", self.body)
        self.body_layout.addWidget(self.percent_label)
        self.cancel_button = QPushButton("取消导入", self.body)
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.cancel)
        self.body_layout.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignRight)
        self._style_progress()
        self._progress_motion = QVariantAnimation(self)
        self._progress_motion.setDuration(520)
        self._progress_motion.setEasingCurve(QEasingCurve.InOutCubic)
        self._progress_motion.valueChanged.connect(lambda value: self.progress.setValue(round(value)))
        self._set_stage(0)

    def _set_stage(self, active):
        colors = _palette()
        for i, label in enumerate(self.stage_labels):
            label.setText(('✓ ' if i < active else '• ' if i == active else '○ ') +
                          ('准备文件', '识别题卡', '等待校对')[i])
            label.setStyleSheet('color:%s;background:%s;border-radius:7px;padding:5px;font-size:11px;' %
                               (colors['ink'] if i == active else colors['muted'],
                                colors['raised'] if i == active else 'transparent'))

    def _style_progress(self):
        colors = _palette()
        self.progress.setStyleSheet(
            "QProgressBar{border:0;background:%s;border-radius:4px;}"
            "QProgressBar::chunk{background:%s;border-radius:4px;}" %
            (colors["raised"], colors["blue"]))
        self.status_label.setStyleSheet(
            "font-size:12px;color:%s;background:transparent;" % colors["ink"])
        self.percent_label.setStyleSheet(
            "font-size:11px;color:%s;background:transparent;" % colors["muted"])

    def setMinimumDuration(self, _milliseconds):
        return None

    def setLabelText(self, text):
        self.status_label.setText(str(text or "正在处理…"))

    def setValue(self, value):
        self._value = max(0, min(self._maximum, int(value)))
        from ui_kit import animations_enabled
        self._progress_motion.stop()
        if self.isVisible() and animations_enabled():
            self._progress_motion.setStartValue(float(self.progress.value()))
            self._progress_motion.setEndValue(float(self._value))
            self._progress_motion.start()
        else:
            self.progress.setValue(self._value)
        self._set_stage(2 if self._value == self._maximum else 1 if self._value else 0)
        self.percent_label.setText("%d%%" % round(100 * self._value / self._maximum))

    def hideEvent(self, event):
        if hasattr(self, '_progress_motion'):
            self._progress_motion.stop()
            self.progress.setValue(self._value)
        super().hideEvent(event)

    def wasCanceled(self):
        return self._was_canceled

    def cancel(self):
        if self._was_canceled:
            return
        self._was_canceled = True
        self.status_label.setText("正在安全停止当前页面…已完成部分会按取消策略清理。")
        self.cancel_button.setText("正在停止…")
        self.cancel_button.setEnabled(False)
        self.canceled.emit()
