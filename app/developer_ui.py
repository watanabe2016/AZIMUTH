"""设置页开发协议与插件管理，沿用软件现有原生控件。"""
from pathlib import Path
import zipfile

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
                              QPlainTextEdit, QLabel, QApplication,
                              QFileDialog, QListWidget, QListWidgetItem, QMessageBox)

import developer_protocols as protocols
import plugin_manager
import theme as T
from ui_kit import IconButton


class DeveloperDocs(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.editors = []
        for title, document in (("皮肤开发协议", protocols.skin_protocol()),
                                ("插件开发协议", protocols.plugin_protocol())):
            editor = QPlainTextEdit()
            editor.setReadOnly(True)
            editor.setPlainText(document)
            editor.setMinimumHeight(240)
            editor.setMaximumHeight(340)
            self.tabs.addTab(editor, title)
            self.editors.append(editor)
        layout.addWidget(self.tabs)
        actions = QHBoxLayout()
        self.copy_button = IconButton("复制当前协议", "note", "primary", font_size=12)
        self.save_button = IconButton("导出当前协议", "save", "default", font_size=12)
        self.kit_button = IconButton("导出开发资料包", "folder", "default", font_size=12)
        for button in (self.copy_button, self.save_button, self.kit_button):
            button.setMinimumHeight(40)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.status = QLabel("将协议复制给其他 Agent，在末尾补充你的需求；资料包含两份协议与可导入示例。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.copy_button.clicked.connect(self.copy_current)
        self.save_button.clicked.connect(self.save_current)
        self.kit_button.clicked.connect(self.export_kit)
        self.refresh_theme()

    def refresh_theme(self):
        self.tabs.setStyleSheet(
            "QTabWidget::pane{border:0;background:transparent;}"
            f"QTabBar::tab{{color:{T.P['ink2']};background:transparent;padding:9px 12px;"
            "border:0;border-bottom:2px solid transparent;font-size:13px;font-weight:600;}"
            f"QTabBar::tab:selected{{color:{T.P['accent_fg']};border-bottom-color:{T.C_BLUE};}}")
        for editor in self.editors:
            editor.setStyleSheet(
                f"QPlainTextEdit{{background:transparent;color:{T.P['ink']};border:0;"
                f"selection-background-color:{T.C_BLUE};selection-color:white;"
                "font-size:13px;font-weight:400;padding:8px 2px;}")
        self.status.setStyleSheet(
            f"QLabel{{color:{T.P['ink2']};background:transparent;font-size:12px;font-weight:400;}}")

    def copy_current(self):
        QApplication.clipboard().setText(self.editors[self.tabs.currentIndex()].toPlainText())
        self.status.setText("已完整复制当前协议，可直接粘贴给开发 Agent。")

    def save_current(self):
        name = self.tabs.tabText(self.tabs.currentIndex()) + "_v1.md"
        path, _ = QFileDialog.getSaveFileName(self, "导出开发协议", name, "Markdown 文档 (*.md)")
        if path:
            try:
                Path(path).write_text(self.editors[self.tabs.currentIndex()].toPlainText(), encoding="utf-8")
                self.status.setText("已导出：" + path)
            except OSError as exc:
                QMessageBox.warning(self, "导出失败", str(exc))

    def export_kit(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出开发资料包", "AZIMUTH_开发资料_v1.zip", "ZIP 资料包 (*.zip)")
        if path:
            try:
                protocols.export_kit(path)
                self.status.setText("已导出两份完整协议和两个可导入示例：" + path)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "导出失败", str(exc))


class PluginTools(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.windows = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        actions = QHBoxLayout()
        self.import_button = IconButton("导入插件", "folder", "primary", font_size=12)
        self.run_button = IconButton("运行选中插件", "play", "default", font_size=12)
        self.remove_button = IconButton("移除插件", "trash", "default", font_size=12)
        for button in (self.import_button, self.run_button, self.remove_button):
            button.setMinimumHeight(40)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.list = QListWidget()
        self.list.setMinimumHeight(112)
        self.list.setMaximumHeight(192)
        self.list.setWordWrap(True)
        layout.addWidget(self.list)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.list.itemSelectionChanged.connect(self._selection)
        self.list.itemDoubleClicked.connect(lambda _: self.run_selected())
        self.import_button.clicked.connect(self.import_package)
        self.run_button.clicked.connect(self.run_selected)
        self.remove_button.clicked.connect(self.remove_selected)
        self.refresh()

    def selected_id(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def refresh(self, selected=None):
        selected = selected or self.selected_id()
        self.list.clear()
        for manifest in plugin_manager.list_plugins():
            item = QListWidgetItem(manifest["name"] + "　·　" + manifest["id"])
            item.setData(Qt.UserRole, manifest["id"])
            item.setToolTip(manifest.get("description", ""))
            self.list.addItem(item)
            if manifest["id"] == selected:
                self.list.setCurrentItem(item)
        if self.list.count() and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        self._selection()

    def _selection(self):
        manifest = plugin_manager.get_plugin(self.selected_id())
        self.run_button.setEnabled(bool(manifest))
        self.remove_button.setEnabled(bool(manifest))
        self.status.setText((manifest.get("description") or "独立离线网页工具") if manifest else
                            "尚未导入插件。可从开发资料包取出“航向换算示例.azplugin”体验。")

    def import_package(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入 AZIMUTH 插件", "", "AZIMUTH 插件 (*.azplugin)")
        if not path:
            return
        if self.windows:
            QMessageBox.information(self, "先关闭插件窗口", "请关闭正在运行的插件窗口，再导入或更新插件。")
            return
        try:
            manifest = plugin_manager.import_plugin(path)
            self.refresh(manifest["id"])
            self.status.setText("已导入“" + manifest["name"] + "”，选择“运行选中插件”即可使用。")
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
            QMessageBox.warning(self, "插件导入失败", str(exc))

    def run_selected(self):
        plugin_id = self.selected_id()
        if not plugin_id:
            return
        if plugin_id in self.windows:
            self.windows[plugin_id].showNormal()
            self.windows[plugin_id].raise_()
            return
        try:
            from plugin_runtime import PluginWindow
            window = PluginWindow(plugin_id, self.window())
            window.destroyed.connect(lambda _=None, key=plugin_id: self.windows.pop(key, None))
            self.windows[plugin_id] = window
            window.show()
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "插件启动失败", str(exc))

    def remove_selected(self):
        plugin_id = self.selected_id()
        if not plugin_id:
            return
        if plugin_id in self.windows:
            QMessageBox.information(self, "先关闭插件窗口", "请关闭该插件窗口后再移除。")
            return
        if QMessageBox.question(self, "移除插件", "移除选中的插件及其本地文件？") != QMessageBox.Yes:
            return
        try:
            plugin_manager.remove_plugin(plugin_id)
            self.refresh()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "移除失败", str(exc))
