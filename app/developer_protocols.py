"""可复制给第三方 Agent 的实际开发契约及完整入门资料。"""
import json
import tempfile
import zipfile
from pathlib import Path

import skin_manager
import plugin_manager


def skin_protocol():
    sample = json.dumps(skin_manager.template_manifest(), ensure_ascii=False, indent=2)
    return '''# AZIMUTH 皮肤开发协议 v1 · 给开发 Agent 的完整任务

你是 AZIMUTH 桌面软件的第三方皮肤开发 Agent。请依据用户提出的视觉需求，
制作符合本协议、能够直接导入软件的 .aztheme 文件。交付成品文件和可编辑源码，
不要只返回建议、伪代码或一个改了后缀但内容不是 ZIP 的文件。

## 当前宿主能力与边界
皮肤是数据包，可调整浅色/深色配色、两种背景图和可选桌面时钟角色插画。
宿主保持现有页面布局、按钮与动效；本协议不提供自由重排页面、替换任意控件、
执行 Python/JavaScript/QSS、载入字体或向宿主注入代码的能力。
不要声称通过本协议可以制作与官方专用人物主题同等的全界面重构。
第三方皮肤不得设置 builtin_theme、不得使用 anna、chiyo 作为 ID。

## 包格式
文件扩展名 .aztheme，内部为普通 ZIP；manifest.json 必须直接位于 ZIP 根目录，
不能再套一个文件夹。JSON 使用 UTF-8，无注释。格式和版本必须精确一致。
示例清单如下（请修改 ID、名称、作者和视觉内容）：

```json
''' + sample + '''
```

## 字段约束
- format 必须为 azimuth-theme；version 为整数 1。
- id 匹配 ^[a-z0-9][a-z0-9-]{0,47}$，最长 48 字符；发布后保持稳定。
  同 ID 导入会更新已有皮肤，首次发布请选自己的唯一 ID。
- name 为非空字符串，最长 48 字符；author、description 各最长 160 字符。
- palettes 仅包含 light、dark；每个模式仅允许 canvas、surface、ink、muted、accent、border。
  所有颜色为 #RRGGBB，不能写透明色、CSS 变量、rgb() 或颜色名。
  canvas=页面底色，surface=卡片/控件底色，ink=正文，muted=次要文字，
  accent=主题强调色，border=弱边框。建议两套模式六个值都提供。
- backgrounds 仅包含 light、dark，值为包内 PNG/JPG/JPEG/WEBP 相对路径；
  无背景时写 {}。例如 {"light":"images/light.jpg","dark":"images/dark.jpg"}。
- clock 为可选 PNG/JPG/JPEG/WEBP 相对路径；不提供时为 ""。
  人物插画建议透明 PNG，保留完整轮廓及适当留白。
- background_opacity 为 0–100 整数；background_blur 为 0–24 整数。
- 任何引用的图片必须真实包含在包中。相对路径统一用 /，区分大小写；
  不得使用 ..、绝对路径、盘符、符号链接、重复文件名或 Windows 保留文件名。

## 文件与资源预算
第三方包最多 24 个文件，解压后合计不超过 30 MiB；最终压缩包也控制在 30 MiB 内。
单张图片不超过 18 MiB。仅允许 .json、.md、.txt、.png、.jpg、.jpeg、.webp。
不允许 SVG、脚本、QSS、字体、可执行文件。背景建议 1920×1080；避免过度细节
干扰题卡阅读，文字与背景对比至少 4.5:1。不得依赖联网下载或系统绝对路径。

## 交付与自检（必须执行）
1. 根据需求设计两套完整配色；准备用户确实授权使用的图片，缺少素材时说明。
2. 用标准 ZIP 工具生成 .aztheme；重新打开 ZIP，解析 manifest.json 并核对全部字段。
3. 检查每个引用路径存在，检查图片可解码、扩展名、文件数、尺寸与大小预算。
4. 用示例模板对照检查，交付 .aztheme、manifest.json、素材及中文 README。
5. 告诉使用者在“设置 → 外观主题 → 导入皮肤包”导入，然后点击新主题，
   分别切换浅色/深色，检查文字、按钮、背景和桌面时钟。
6. 说明已执行的检查及未实际运行宿主的验证边界，不声称未经执行的导入已成功。

用户的具体设计需求将写在本协议之后。请按需求制作最终文件。
'''


def plugin_protocol():
    sample = json.dumps(plugin_manager.template_manifest(), ensure_ascii=False, indent=2)
    return '''# AZIMUTH 插件开发协议 v1 · 给开发 Agent 的完整任务

你是 AZIMUTH 桌面软件的第三方插件开发 Agent。请根据用户需求制作能够在
“设置 → 开发与扩展”直接导入并运行的 .azplugin 文件，交付成品和可编辑源码。

## 宿主与插件类型
协议 v1 支持 web-tool：独立窗口运行的离线 HTML/CSS/JavaScript 网页工具，
适合航向/角度换算、单位换算、教学文档、交互演示、纯本地计算和测验。
这不是 Python 插件、Qt 控件插件或飞控插件；不能改宿主页面、读写题库、调用
宿主 API Key、连接地面站、访问本机文件或联网。没有原生桥接对象。
无需安装 Node、Python 或 pip。第三方库必须本地打包，无 CDN/远程字体/远程 API。

## 包结构与清单
.azplugin 是普通 ZIP，根目录必须有 manifest.json；入口可为 index.html 或其他
相对 HTML 路径。JSON 使用 UTF-8，无注释。示例：

```json
''' + sample + '''
```

- format=azimuth-plugin，version=整数 1，type=web-tool，permissions=[]。
  协议版本决定宿主兼容性，不能改成自己的插件版本号。
- id 匹配 ^[a-z0-9][a-z0-9-]{0,47}$；name 为非空字符串，最长 48 字符。
  author 最长 80 字符、description 最长 240 字符。作者发布时使用独立 ID。
- entry 必须是已打包的 .html 文件相对路径。全部路径用 /，大小写精确匹配。
- 不允许绝对路径、..、盘符、反斜杠、符号链接、重复/大小写冲突文件名，
  不允许 CON/PRN/AUX/NUL/COM1/LPT1 等 Windows 保留名。
- 最多 128 个 ZIP 条目；压缩包及解压后内容各不超过 20 MiB；manifest 不超过 128 KiB。
- 允许 .html、.css、.js、.json、.png、.jpg、.jpeg、.webp、.svg、.md、.txt。
  不支持 .py、.exe、.dll、字体、压缩嵌套文件和安装依赖。
- 同 ID 导入会替换已有插件；运行中请先关闭插件窗口再更新或移除。

## 网页运行环境与主题 API
宿主在独立、临时的 Qt WebEngine 配置中运行插件。默认无持久存储；关闭窗口后
不保证 localStorage/IndexedDB 保留，不应把它们当成学习记录的持久保存接口。
插件仅能加载自身目录内资源与 data: 内嵌资源；不开放网络、本地任意文件、
下载、弹出窗口、系统文件选择、摄像头、麦克风或定位权限。
不要使用 <input type=file>、window.open、远程 fetch、Service Worker 或下载导出。

页面脚本开始前，宿主会注入 window.azimuth：
```javascript
{
  protocolVersion: 1,
  locale: "zh-CN",
  theme: {
    mode: "light", // 或 dark
    name: "default", // 当前应用主题标识，不要写死可选值
    colors: {
      canvas: "#...", surface: "#...", ink: "#...", muted: "#...",
      accent: "#...", border: "#...", onAccent: "#..."
    }
  }
}
```
以上示例颜色是结构说明，实际值均为宿主提供的有效颜色。
宿主同时设置 CSS 变量 --az-canvas、--az-surface、--az-ink、--az-muted、
--az-accent、--az-border、--az-on-accent，以及 document.documentElement.dataset.azMode。
强调按钮的文字使用 --az-on-accent，确保当前主题下可读。
切换主题时宿主更新变量和 window.azimuth，并派发：
```javascript
window.addEventListener('azimuth-theme-change', event => {
  // event.detail 与 window.azimuth.theme 结构一致；只更新图形颜色，保留用户输入。
  console.log(event.detail.mode);
});
```
纯 CSS 界面只引用上述变量即可自动适配；SVG/canvas 需在事件里重绘颜色。
不要覆盖 window.azimuth。计算、图形和交互均在插件自身页面内完成。

## 界面与数据要求
界面中文、键盘可操作；包含清晰的输入标签、结果和输入错误提示。
适配 640×480 起的窗口，窄窗口可滚动；不要固定整页像素高度或依赖全屏。
采用宿主主题变量；避免硬编码黑白背景；提供 :focus-visible 和足够文字对比。
计算器应明确单位、范围、四舍五入规则；说明仅用于学习，不能冒充已验证飞行数据。
不要隐藏收费请求、埋点、账号采集或自行修改宿主数据。

## 交付与验收（必须执行）
1. 实现完整离线网页并在入口 HTML 使用相对资源路径。
2. 将 manifest.json 与网页素材直接压为 .azplugin；重新读取检查结构和预算。
3. 测试正常、边界、非法输入；确认断网可用、浅深色适配和主题切换不清空输入。
4. 交付 .azplugin、源码和中文 README（功能、计算规则、测试记录、限制）。
5. 指导用户在“设置 → 开发与扩展 → 导入插件”导入，再选择并运行。
   如果未运行 AZIMUTH，只报告已完成的离线检查，不虚构宿主验收。

用户的具体插件需求将写在本协议之后。请按需求制作最终文件。
'''


def export_kit(destination):
    """导出完整可移交资料，两个样例本身就是可导入包。"""
    with tempfile.TemporaryDirectory(prefix="azimuth-developer-") as directory:
        root = Path(directory)
        skin_manager.export_template(root / "皮肤示例.aztheme")
        plugin_manager.export_template(root / "航向换算示例.azplugin")
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("皮肤开发协议.md", skin_protocol())
            archive.writestr("插件开发协议.md", plugin_protocol())
            archive.writestr("README.txt", "将对应协议复制给开发 Agent，并在末尾写具体需求。\n"
                             "皮肤示例到设置→外观主题导入；插件示例到设置→开发与扩展导入。\n"
                             "协议 v1 支持配色/背景皮肤与独立离线网页工具插件。\n")
            for path in root.iterdir():
                archive.write(path, path.name)
    return str(destination)
