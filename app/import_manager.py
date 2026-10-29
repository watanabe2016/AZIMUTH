# -*- coding: utf-8 -*-
"""管理用户导入的 PDF 与图片来源。"""

import os

from PySide6.QtCore import Qt, QParallelAnimationGroup, QVariantAnimation, QEasingCurve
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QTableWidget, QTableWidgetItem, QPushButton,
                               QMessageBox, QAbstractItemView, QHeaderView)

import bankdata as BD
import theme as T
from ui_kit import animations_enabled
from import_dialog_ui import ImportDialogShell


def _size_text(size):
    try:
        value = max(0, int(size))
    except (TypeError, ValueError, OverflowError):
        value = 0
    if value < 1024:
        return "%d B" % value
    if value < 1024 * 1024:
        return "%.1f KB" % (value / 1024.0)
    return "%.2f MB" % (value / (1024.0 * 1024.0))


class ImportManagerDialog(ImportDialogShell):
    """查看来源文件及其题卡，并按选中的来源批量删除。"""

    def __init__(self, parent=None, on_deleted=None):
        super().__init__("已导入文件", parent,
                         size=(880, 500), minimum=(680, 400))
        self._on_deleted = on_deleted
        self._records = []
        self._animations = []
        root = self.body_layout
        root.setSpacing(10)
        self.caption = QLabel("")
        self.caption.setStyleSheet(
            "font-size:12px;font-weight:600;color:%s;background:transparent;" % T.P["ink2"])
        self.caption.setWordWrap(True)
        root.addWidget(self.caption)
        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(["文件名", "类型", "大小", "导入时间", "题卡数"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(210)
        self.table.setStyleSheet(
            "QTableWidget{border-radius:10px;padding:0;}"
            "QTableWidget::item:alternate{background:rgba(127,147,170,0.09);}"
            "QTableWidget::item:selected{background:%s;color:#FFFFFF;}" % T.C_BLUE)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.select_all = QPushButton("全选")
        self.select_all.clicked.connect(self.table.selectAll)
        self.delete_selected = QPushButton("删除选中")
        self.delete_selected.clicked.connect(self.delete_selected_sources)
        self.clear_all = QPushButton("清空导入")
        self.clear_all.clicked.connect(self.clear_sources)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.accept)
        for button in (self.select_all, self.delete_selected,
                       self.clear_all, self.close_button):
            button.setMinimumHeight(34)
        self.delete_selected.setStyleSheet("color:%s;" % T.C_RED)
        self.clear_all.setStyleSheet("color:%s;" % T.C_RED)
        row.addWidget(self.select_all)
        row.addWidget(self.delete_selected)
        row.addWidget(self.clear_all)
        row.addStretch(1)
        row.addWidget(self.close_button)
        root.addLayout(row)
        self.refresh(animate=True)

    def refresh(self, animate=False):
        self._records = BD.imported_sources()
        self.table.setRowCount(0)
        self.caption.setText("共 %d 个来源文件，关联 %d 张题卡。删除来源会同时删除它导入的题卡图片和练习记录。" % (
            len(self._records), sum(len(row.get("question_ids") or []) for row in self._records)))
        self.table.setRowCount(len(self._records))
        self._animations = []
        for row_index, record in enumerate(self._records):
            values = (str(record.get("file_name") or "本地导入"),
                      str(record.get("file_type") or "图片"),
                      _size_text(record.get("size_bytes", 0)),
                      str(record.get("imported_at") or "历史导入"),
                      str(len(record.get("question_ids") or [])))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 4:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 0:
                    item.setToolTip("来源标识：%s" % record.get("source_id", ""))
                self.table.setItem(row_index, column, item)
            target = 34
            self.table.setRowHeight(row_index, 0 if animate and animations_enabled() else target)
            if animate and animations_enabled():
                motion = QVariantAnimation(self)
                motion.setDuration(190)
                motion.setStartValue(0)
                motion.setEndValue(target)
                motion.setEasingCurve(QEasingCurve.OutCubic)
                motion.valueChanged.connect(
                    lambda height, index=row_index: self.table.setRowHeight(index, int(height)))
                motion.start()
                self._animations.append(motion)
        self.delete_selected.setEnabled(bool(self._records))
        self.clear_all.setEnabled(bool(self._records))

    def _selected_sources(self):
        selected_rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self._records[index] for index in selected_rows
                if 0 <= index < len(self._records)]

    def _confirm_delete(self, records):
        names = [str(row.get("file_name") or "本地导入") for row in records]
        preview = "\n".join("• " + name for name in names[:8])
        if len(names) > 8:
            preview += "\n…以及其他 %d 个文件" % (len(names) - 8)
        count = sum(len(row.get("question_ids") or []) for row in records)
        return QMessageBox.question(
            self, "确认删除导入文件",
            "确定删除选中的 %d 个来源文件及其 %d 张题卡吗？\n\n%s\n\n此操作无法撤销。" % (
                len(records), count, preview),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _animate_delete(self, records):
        if not records or not self._confirm_delete(records):
            return
        source_ids = {str(row.get("source_id") or "") for row in records}
        if not animations_enabled():
            return self._delete_now(source_ids)
        row_indices = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if not row_indices:
            return self._delete_now(source_ids)
        group = QParallelAnimationGroup(self)
        for row_index in row_indices:
            motion = QVariantAnimation(group)
            motion.setDuration(170)
            motion.setStartValue(self.table.rowHeight(row_index))
            motion.setEndValue(0)
            motion.setEasingCurve(QEasingCurve.InCubic)
            motion.valueChanged.connect(
                lambda height, index=row_index: self.table.setRowHeight(index, int(height)))
            group.addAnimation(motion)
        group.finished.connect(lambda: self._delete_now(source_ids))
        self._animations.append(group)
        group.start()

    def delete_selected_sources(self):
        self._animate_delete(self._selected_sources())

    def clear_sources(self):
        self.table.selectAll()
        self._animate_delete(list(self._records))

    def _delete_now(self, source_ids):
        count = BD.remove_imported_sources(source_ids)
        if count <= 0:
            QMessageBox.warning(self, "删除未完成", "未能删除题卡，请检查题库目录是否可写。")
            self.refresh()
            return
        self.refresh(animate=True)
        if callable(self._on_deleted):
            self._on_deleted()
