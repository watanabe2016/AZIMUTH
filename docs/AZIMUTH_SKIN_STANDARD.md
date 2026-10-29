# AZIMUTH 皮肤包标准（`.aztheme`）

`.aztheme` 是一个 ZIP 压缩包，包内只使用 `manifest.json`、说明文档和 PNG、JPG、WEBP 图片。软件读取颜色和图片，不执行包内代码；设计者可以把主题包发给其他 AZIMUTH 用户直接导入。

## 包内文件

```text
manifest.json
README.txt                 可选
background-light.jpg       可选
background-dark.jpg        可选
```

背景文件名可自定义，但必须与清单中的路径完全一致。推荐背景至少为 1920 × 1080；每张不超过 18 MB，整个主题包不超过 30 MB。

## 清单格式

```json
{
  "format": "azimuth-theme",
  "version": 1,
  "id": "sample-skin",
  "name": "示例主题",
  "author": "设计者",
  "description": "简短的主题说明",
  "palettes": {
    "light": {
      "canvas": "#F7F5F1",
      "surface": "#FFFFFF",
      "ink": "#20232A",
      "muted": "#62656B",
      "accent": "#E5484D",
      "border": "#D7D4D0"
    },
    "dark": {
      "canvas": "#191B22",
      "surface": "#242731",
      "ink": "#EAE6DF",
      "muted": "#B8B4B0",
      "accent": "#F34D63",
      "border": "#55545E"
    }
  },
  "backgrounds": {
    "light": "background-light.jpg",
    "dark": "background-dark.jpg"
  },
  "background_opacity": 55,
  "background_blur": 0
}
```

`palettes` 与 `backgrounds` 都可以按外观分别提供，也可以只提供其中一种外观；没有背景图时使用空对象 `{}`。颜色使用 `#RRGGBB`，允许的颜色字段为 `canvas`、`surface`、`ink`、`muted`、`accent`、`border`。背景透明度范围为 0–100，高斯模糊范围为 0–24。

## 导入限制

`id` 只能由小写英文字母、数字和连字符组成，最长 48 个字符。主题包最多 24 个文件；图片单张最多 18 MB；路径不能跳出主题目录。软件拒绝脚本、字体、程序和其他不在标准内的文件。
