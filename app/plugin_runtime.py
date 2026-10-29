"""为网页插件提供隔离的离线窗口和仅包含外观的宿主上下文。"""
import json
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout
from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile,
                                    QWebEngineScript, QWebEngineSettings,
                                    QWebEngineUrlRequestInterceptor)
from PySide6.QtWebEngineWidgets import QWebEngineView

import theme as T
from plugin_manager import get_plugin, plugins_dir


def allowed_url(url, directory):
    if url.scheme() == "data":
        return True
    if not url.isLocalFile():
        return False
    path = Path(url.toLocalFile()).resolve()
    return path.is_relative_to(Path(directory).resolve()) and path.is_file()


class PluginRequests(QWebEngineUrlRequestInterceptor):
    def __init__(self, directory, parent=None):
        super().__init__(parent)
        self.directory = directory

    def interceptRequest(self, info):
        if not allowed_url(info.requestUrl(), self.directory):
            info.block(True)


class PluginPage(QWebEnginePage):
    def __init__(self, profile, directory, parent):
        super().__init__(profile, parent)
        self.directory = directory

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return allowed_url(url, self.directory)

    def chooseFiles(self, mode, old_files, accepted_types):
        return []

    def createWindow(self, window_type):
        return None


def theme_context():
    color = QColor(T.C_BLUE)
    channels = [channel / 12.92 if channel <= 0.04045 else
                ((channel + 0.055) / 1.055) ** 2.4
                for channel in (color.redF(), color.greenF(), color.blueF())]
    luminance = sum(value * weight for value, weight in zip(channels, (0.2126, 0.7152, 0.0722)))
    on_accent = "#000000" if luminance > 0.179 else "#FFFFFF"
    return {"protocolVersion": 1, "locale": "zh-CN", "theme": {
        "mode": T.mode, "name": T.app_theme,
        "colors": {"canvas": T.P["shell"], "surface": T.P["dialog_bg"],
                   "ink": T.P["ink"], "muted": T.P["ink2"],
                   "accent": T.C_BLUE, "border": T.P["accent_edge"],
                   "onAccent": on_accent}}}


def context_script(context):
    return '''(()=>{
const context=CONTEXT;
Object.freeze(context.theme.colors);Object.freeze(context.theme);Object.freeze(context);
window.azimuth=context;
function apply(){const root=document.documentElement;if(!root)return;
const names={canvas:'canvas',surface:'surface',ink:'ink',muted:'muted',accent:'accent',border:'border',onAccent:'on-accent'};
for(const [key,name] of Object.entries(names))root.style.setProperty('--az-'+name,context.theme.colors[key]);
root.dataset.azMode=context.theme.mode;root.style.colorScheme=context.theme.mode;
window.dispatchEvent(new CustomEvent('azimuth-theme-change',{detail:context.theme}));}
if(document.documentElement)apply();else document.addEventListener('DOMContentLoaded',apply,{once:true});
})();'''.replace("CONTEXT", json.dumps(context, ensure_ascii=False))


class PluginWindow(QDialog):
    def __init__(self, plugin_id, parent=None):
        super().__init__(parent)
        manifest = get_plugin(plugin_id)
        if not manifest:
            raise ValueError("插件入口不可用，请重新导入插件包。")
        self.plugin_id = plugin_id
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(manifest["name"] + " · AZIMUTH 插件")
        self.resize(900, 640)
        self.setMinimumSize(640, 480)
        directory = (plugins_dir() / plugin_id).resolve()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.error = QLabel("插件加载失败，请关闭窗口，检查入口和资源后重新导入。")
        self.error.setWordWrap(True)
        self.error.setContentsMargins(20, 16, 20, 16)
        self.error.hide()
        layout.addWidget(self.error)
        self.view = QWebEngineView(self)
        layout.addWidget(self.view, 1)
        # 无名称配置是临时配置；先销毁页面/视图，再释放其配置对象。
        self.profile = QWebEngineProfile()
        self.view.destroyed.connect(self.profile.deleteLater)
        self.profile.setHttpCacheType(QWebEngineProfile.NoCache)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)
        self.interceptor = PluginRequests(directory, self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.profile.downloadRequested.connect(lambda download: download.cancel())
        self.page = PluginPage(self.profile, directory, self.view)
        self.view.setPage(self.page)
        settings = self.page.settings()
        for attribute in (QWebEngineSettings.JavascriptCanOpenWindows,
                          QWebEngineSettings.LocalContentCanAccessRemoteUrls,
                          QWebEngineSettings.JavascriptCanAccessClipboard,
                          QWebEngineSettings.PluginsEnabled,
                          QWebEngineSettings.FullScreenSupportEnabled,
                          QWebEngineSettings.ScreenCaptureEnabled,
                          QWebEngineSettings.AllowRunningInsecureContent):
            settings.setAttribute(attribute, False)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebRTCPublicInterfacesOnly, True)
        self.page.featurePermissionRequested.connect(
            lambda origin, feature: self.page.setFeaturePermission(
                origin, feature, QWebEnginePage.PermissionDeniedByUser))
        self.page.renderProcessTerminated.connect(lambda *_: self.error.show())
        self.view.loadFinished.connect(lambda ok: self.error.setVisible(not ok))
        self._context = None
        self._refresh_theme()
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._refresh_theme)
        self.timer.start()
        self.view.setUrl(QUrl.fromLocalFile(str(directory / manifest["entry"])))

    def _refresh_theme(self):
        context = theme_context()
        if context == self._context:
            return
        self._context = context
        script = QWebEngineScript()
        script.setName("azimuth-plugin-context")
        script.setInjectionPoint(QWebEngineScript.DocumentCreation)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(context_script(context))
        for previous in self.page.scripts().toList():
            if previous.name() == script.name():
                self.page.scripts().remove(previous)
        self.page.scripts().insert(script)
        self.page.setBackgroundColor(QColor(T.P["shell"]))
        self.page.runJavaScript(script.sourceCode())

    def closeEvent(self, event):
        self.timer.stop()
        self.view.stop()
        super().closeEvent(event)
