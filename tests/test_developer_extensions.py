"""开发资料与插件包的真实导入、更新失败回滚和边界验证。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import config as cfg
import developer_protocols
import plugin_manager as PM
import skin_manager


class DeveloperExtensionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = patch.object(cfg, "_path", str(self.root / "config.json"))
        self.config.start()

    def tearDown(self):
        self.config.stop()
        self.temp.cleanup()

    def package(self, manifest=None, extra=None):
        path = self.root / "test.azplugin"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest or PM.template_manifest()))
            archive.writestr("index.html", PM.EXAMPLE_HTML)
            for name, content in (extra or {}).items():
                info = zipfile.ZipInfo(name)
                info.filename = name
                archive.writestr(info, content)
        return path

    def test_exported_kit_contains_two_importable_examples_and_complete_protocols(self):
        kit = self.root / "kit.zip"
        developer_protocols.export_kit(kit)
        with zipfile.ZipFile(kit) as archive:
            for filename in ("皮肤开发协议.md", "插件开发协议.md"):
                self.assertGreater(len(archive.read(filename).decode("utf-8")), 1000)
            for filename in ("皮肤示例.aztheme", "航向换算示例.azplugin"):
                (self.root / filename).write_bytes(archive.read(filename))
        theme = skin_manager.import_theme(self.root / "皮肤示例.aztheme")
        plugin = PM.import_plugin(self.root / "航向换算示例.azplugin")
        self.assertEqual(theme["id"], "my-azimuth-skin")
        self.assertEqual(PM.get_plugin(plugin["id"])["entry"], "index.html")

    def test_update_and_remove_work_without_touching_another_plugin(self):
        manifest = PM.import_plugin(self.package())
        other = PM.template_manifest()
        other["id"] = "other-tool"
        PM.import_plugin(self.package(other))
        manifest["name"] = "更新后的名字"
        PM.import_plugin(self.package(manifest))
        self.assertEqual(PM.get_plugin(manifest["id"])["name"], "更新后的名字")
        PM.remove_plugin(manifest["id"])
        self.assertIsNone(PM.get_plugin(manifest["id"]))
        self.assertIsNotNone(PM.get_plugin("other-tool"))

    def test_rejects_traversal_windows_aliases_and_executables(self):
        for name in ("../escape.js", "C:/evil.js", "dir\\evil.js", "CON.txt",
                     "folder/aux.js", "file.js.", "run.exe", "tool.py"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                PM.import_plugin(self.package(extra={name: "bad"}))
        self.assertFalse((self.root / "escape.js").exists())

    def test_rejects_missing_entry_future_version_and_permissions(self):
        for key, value in (("entry", "missing.html"), ("version", 2),
                           ("permissions", ["network"]), ("type", "python")):
            manifest = PM.template_manifest()
            manifest[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                PM.import_plugin(self.package(manifest))

    def test_invalid_update_preserves_original(self):
        installed = PM.import_plugin(self.package())
        original = (PM.plugins_dir() / installed["id"] / "index.html").read_bytes()
        with self.assertRaises(ValueError):
            PM.import_plugin(self.package(extra={"../bad.js": "bad"}))
        self.assertEqual((PM.plugins_dir() / installed["id"] / "index.html").read_bytes(), original)

    def test_rejects_case_collisions_and_symbolic_links(self):
        with self.assertRaises(ValueError):
            PM.import_plugin(self.package(extra={"INDEX.HTML": "duplicate"}))
        path = self.package()
        with zipfile.ZipFile(path, "a") as archive:
            info = zipfile.ZipInfo("link.js")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info, "../../outside")
        with self.assertRaises(ValueError):
            PM.import_plugin(path)

    def test_rejects_content_budget(self):
        path = self.package()
        with patch.object(PM, "MAX_BYTES", 10), self.assertRaises(ValueError):
            PM.import_plugin(path)

    def test_copy_button_copies_the_selected_complete_protocol(self):
        from PySide6.QtWidgets import QApplication
        from developer_ui import DeveloperDocs
        app = QApplication.instance() or QApplication([])
        widget = DeveloperDocs()
        for index, text in enumerate((developer_protocols.skin_protocol(), developer_protocols.plugin_protocol())):
            widget.tabs.setCurrentIndex(index)
            widget.copy_button.click()
            self.assertEqual(app.clipboard().text(), text)
        widget.close()


if __name__ == "__main__":
    unittest.main()
