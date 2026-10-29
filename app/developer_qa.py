"""在真实构建入口隔离验收协议、插件执行和主题切换，不调用模型 API。"""
import copy
import json
from pathlib import Path
import tempfile
import time
import traceback
import zipfile


def run(app, output):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QFont, QPalette, QColor
    from PySide6.QtTest import QTest
    import config as cfg
    import theme as T
    import developer_protocols as docs
    import plugin_manager as PM
    from settings_page import SettingsPage
    from plugin_runtime import PluginWindow, allowed_url, theme_context
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    previous = cfg._path, cfg._cache
    old_theme, old_mode = T.app_theme, T.mode
    records, errors = [], []
    settings = window = None

    def wait_for(predicate, message, timeout=15000):
        deadline = time.monotonic() + timeout / 1000
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(40)
        if not predicate():
            raise AssertionError(message)

    def evaluate(script):
        result = []
        window.page.runJavaScript(script, lambda value: result.append(value))
        wait_for(lambda: bool(result), "网页执行回调超时")
        return result[0]

    with tempfile.TemporaryDirectory(prefix="azimuth-developer-qa-") as folder:
        cfg._path = str(Path(folder) / "config.json")
        cfg._cache = copy.deepcopy(cfg.DEFAULTS)
        cfg._cache["animations_enabled"] = False
        try:
            T.load_app_fonts()
            app.setFont(QFont(T.pick_font()))
            docs.export_kit(output / "AZIMUTH_开发资料_v1.zip")
            package = Path(folder) / "sample.azplugin"
            PM.export_template(package)
            with zipfile.ZipFile(package, "a") as archive:
                archive.writestr("probe.js", "window.externalResourceReady=true;")
            manifest = PM.import_plugin(package)
            entry = PM.plugins_dir() / manifest["id"] / "index.html"
            entry.write_text(entry.read_text(encoding="utf-8").replace(
                "</head>", '<script src="probe.js"></script></head>'), encoding="utf-8")
            for mode in ("light", "dark"):
                T.set_app_theme("default")
                T.apply(mode)
                app.setStyleSheet(T.stylesheet())
                if settings is None:
                    settings = SettingsPage()
                    settings._select_settings_group("developer", save=False)
                    settings.show()
                settings.theme_changed()
                palette = settings.palette()
                palette.setColor(QPalette.Window, QColor(T.P["shell"]))
                settings.setPalette(palette)
                settings.setAutoFillBackground(True)
                for width, height in ((1380, 940), (1024, 760)):
                    settings.resize(width, height)
                    QTest.qWait(700)
                    settings.scroll.verticalScrollBar().setValue(0)
                    for index, function in ((0, docs.skin_protocol), (1, docs.plugin_protocol)):
                        settings.developer_docs.tabs.setCurrentIndex(index)
                        settings.developer_docs.copy_current()
                        assert app.clipboard().text() == function(), "协议复制不完整"
                    for button in (settings.developer_docs.copy_button,
                                   settings.developer_docs.save_button,
                                   settings.developer_docs.kit_button):
                        assert button.width() >= button.sizeHint().width(), "协议操作按钮被压缩"
                    settings.grab().save(str(output / f"settings-{mode}-{width}.png"))
                    records.append({"check": "settings", "mode": mode, "width": width, "copy_exact": True})
            settings.hide()
            T.apply("light")
            window = PluginWindow(manifest["id"])
            loaded = []
            window.view.loadFinished.connect(lambda ok: loaded.append(ok))
            window.show()
            wait_for(lambda: bool(loaded), "插件未完成加载")
            assert loaded[-1], "插件 HTML 加载失败"
            assert evaluate("window.externalResourceReady === true"), "相对路径脚本未加载"
            assert evaluate("window.azimuth.protocolVersion") == 1, "宿主上下文未注入"
            assert evaluate("document.querySelector('#result').textContent") == "反向航向：225°"
            evaluate("document.querySelector('#heading').value='135';document.querySelector('#form').requestSubmit();true")
            assert evaluate("document.querySelector('#result').textContent") == "反向航向：315°"
            records.append({"check": "plugin-execution", "relative_js": True, "input": 135, "result": 315})
            for app_theme, mode in (("default", "light"), ("default", "dark"),
                                    ("industrial", "light"), ("industrial", "dark")):
                T.set_app_theme(app_theme)
                T.apply(mode)
                window._refresh_theme()
                QTest.qWait(150)
                assert evaluate("window.azimuth.theme.mode") == mode
                assert evaluate("document.querySelector('#heading').value") == "135", "主题切换丢失输入"
                assert evaluate("getComputedStyle(document.documentElement).getPropertyValue('--az-canvas').trim()") == theme_context()["theme"]["colors"]["canvas"]
                window.grab().save(str(output / f"plugin-{app_theme}-{mode}.png"))
                records.append({"check": "plugin-theme", "theme": app_theme, "mode": mode, "input_preserved": True})
            directory = PM.plugins_dir() / manifest["id"]
            secret = Path(folder) / "outside.txt"
            secret.write_text("qa-only-outside-marker", encoding="utf-8")
            assert not allowed_url(QUrl.fromLocalFile(str(secret)), directory)
            assert not allowed_url(QUrl("https://example.com/"), directory)
            evaluate("window.outsideRead='waiting';fetch(" + json.dumps(QUrl.fromLocalFile(str(secret)).toString()) +
                     ").then(r=>r.text()).then(()=>window.outsideRead='unexpected').catch(()=>window.outsideRead='blocked');true")
            wait_for(lambda: evaluate("window.outsideRead") != "waiting", "越界文件读取检查超时")
            assert evaluate("window.outsideRead") == "blocked", "插件可读取目录外文件"
            records.append({"check": "isolation", "outside_file_blocked": True, "network_policy_blocked": True})
        except Exception:
            errors.append(traceback.format_exc())
        finally:
            if window is not None:
                window.close()
            if settings is not None:
                settings.close()
            QTest.qWait(200)
            cfg._path, cfg._cache = previous
            T.set_app_theme(old_theme)
            T.apply(old_mode)
    (output / "result.json").write_text(json.dumps({"ok": not errors, "api_calls": 0,
        "records": records, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if not errors else 2
