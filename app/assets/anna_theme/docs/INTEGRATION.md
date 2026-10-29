# 前端接入
## Web
在宿主根容器设 data-anna-theme="light" 或 "dark"，加载 styles/theme.css。复制 assets、styles，维持相对层级即可离线运行。
```html
<link rel="stylesheet" href="./styles/theme.css">
<div data-anna-theme="light">
  <section class="anna-hero">
    <div class="anna-hero__sky" aria-hidden="true"></div>
    <img class="anna-hero__character" src="./assets/characters/anna-hero.png" alt="">
    <div class="anna-hero__copy">
      <div class="anna-chapter">01 · 学习首页</div>
      <h1>这一页，<br>轮到你掌握<em>航线</em>。</h1>
      <button class="anna-button anna-button--primary" type="button">继续练习</button>
    </div>
  </section>
</div>
```
人物与背景为独立图层。业务点击事件由原应用接管。切换模式只更新属性，不要反色处理图片。可参考 components.html 的实际组件。
SVG 图标建议内联并继承 currentColor。外链方式请用 assets/icons/light 或 dark 定色版本。几何图形同样提供 light/dark 两套SVG。

## Qt / PySide6
推荐直接使用 adapters/qt_theme.py 的 apply_anna_theme(window, mode, bundle_dir)；该函数会解析字体和图标的绝对路径，避免工作目录变化造成资源失效。这是接入辅助代码，未在宿主程序内执行。
styles/qt-light.qss 和 qt-dark.qss 为属性作用域样式，覆盖基础容器、按钮、输入、文本、滚动条与选中态。不是软件“导入皮肤包”格式。
根QWidget设动态属性 annaTheme=true，面板设 annaRole=panel，侧栏 annaRole=sidebar，主按钮 annaRole=primary，标题标签 annaRole=title，副文案 annaRole=muted；原有代码用何种对象名不影响这些属性。
```python
from pathlib import Path
from PySide6.QtGui import QFontDatabase, QFont
bundle = Path("anna-theme-v1")
font_id = QFontDatabase.addApplicationFont(str(bundle / "assets/fonts/NotoSansSC-Variable.ttf"))
families = QFontDatabase.applicationFontFamilies(font_id)
if families:
    window.setFont(QFont(families[0], 11))
window.setProperty("annaTheme", True)
start_button.setProperty("annaRole", "primary")
qss = (bundle / "styles/qt-light.qss").read_text(encoding="utf-8")
qss = qss.replace("url(assets/", 'url("' + (bundle.resolve() / "assets").as_posix() + "/").replace("check.svg);", 'check.svg");')
window.setStyleSheet(qss)
```
QSS请与宿主样式合并，避免覆盖现有业务特殊控件。改变动态属性后需要 unpolish/polish 刷新。若当前Qt版本不支持可变字重，使用已注册字体的常规/粗体，由QFont控制。
题卡图不做暗色滤镜。正式题目文本、计时状态、练习数据继续读取原业务层。实际导入格式与信号绑定不在本素材包中伪造。

## 验收
1. 对比 screens 同页面浅深效果图，确认视觉方向。
2. 使用打包logo与字体，检查125%/150%显示缩放。
3. 焦点能完整遍历；按钮、输入、禁用态文字可读。
4. 长题干独立滚动，人物不进入题图区、计时区或标题栏。
5. 切换主题不重置计时、不丢失题目位置及学习数据。

