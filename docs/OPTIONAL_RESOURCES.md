# 可选运行资源

仓库包含软件源码和界面所需的一般资源，以下文件不存放在 Git 历史中。

## OCR

Python 依赖包含 RapidOCR 和 ONNX Runtime。软件也支持 Tesseract。

如需将 Tesseract 随 Windows 构建打包，请从 Tesseract 官方项目指向的可信 Windows 发行渠道准备运行文件和所需语言模型，放到 `tools/ocr/Tesseract-OCR/`，并保留发行版的许可证与第三方声明。`build/build_release.py` 会探测该目录；不存在时会提示未打包 Tesseract，导入后仍可手工校对。

- Tesseract：<https://github.com/tesseract-ocr/tesseract>
- RapidOCR：<https://github.com/RapidAI/RapidOCR>

## 音频格式兼容

软件优先使用 Qt 播放音频。某些自定义音频的兼容转换会调用 `assets/ffmpeg.exe`；仓库不包含该第三方可执行文件。如需该功能，请自行准备 FFmpeg，并保留所选构建对应的许可证和源码获取信息。

FFmpeg：<https://ffmpeg.org/download.html>。

原开发环境中的大型音乐文件未收录。使用者可在软件中导入自己拥有使用权限的音频。
