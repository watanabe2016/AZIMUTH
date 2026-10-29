# -*- coding: utf-8 -*-
"""安全读取、安装和导出 AZIMUTH 皮肤包。"""
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import config as cfg


FORMAT = "azimuth-theme"
VERSION = 1
MAX_ARCHIVE_BYTES = 30 * 1024 * 1024
MAX_IMAGE_BYTES = 18 * 1024 * 1024
MAX_BUILTIN_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_BUILTIN_CONTENT_BYTES = 120 * 1024 * 1024
MAX_BUILTIN_FILES = 400
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_PALETTE_KEYS = {"canvas", "surface", "ink", "muted", "accent", "border"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_TEXT_EXTENSIONS = {".md", ".txt"}
_BUILTIN_EXTENSIONS = _IMAGE_EXTENSIONS | _TEXT_EXTENSIONS | {
    ".json", ".svg", ".ttf", ".otf", ".ico", ".qss"
}
_BUILTIN_DIRS = {"anna": "anna_theme", "chiyo": "chiyo_pack"}


def themes_dir():
    path = Path(cfg.config_path()).parent / "themes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_manifest(directory):
    try:
        data = json.loads((Path(directory) / "manifest.json").read_text(
            encoding="utf-8"))
        if _valid_manifest(data):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return None


def _valid_manifest(data):
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return False
    if type(data.get("version")) is not int or data["version"] != VERSION:
        return False
    if not isinstance(data.get("id"), str) or not _SLUG.fullmatch(data["id"]):
        return False
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        return False
    if len(data["name"]) > 48:
        return False
    builtin_theme = data.get("builtin_theme")
    if builtin_theme is None and data["id"] in _BUILTIN_DIRS:
        return False
    if builtin_theme is not None and (
            builtin_theme not in _BUILTIN_DIRS or data["id"] != builtin_theme):
        return False
    for key in ("author", "description"):
        if key in data and (not isinstance(data[key], str) or len(data[key]) > 160):
            return False
    palettes = data.get("palettes", {})
    if not isinstance(palettes, dict) or set(palettes) - {"light", "dark"}:
        return False
    for palette in palettes.values():
        if not isinstance(palette, dict) or set(palette) - _PALETTE_KEYS:
            return False
        if any(not isinstance(value, str) or not _HEX.fullmatch(value)
               for value in palette.values()):
            return False
    backgrounds = data.get("backgrounds", {})
    if not isinstance(backgrounds, dict) or set(backgrounds) - {"light", "dark"}:
        return False
    for name in backgrounds.values():
        if not isinstance(name, str):
            return False
        path = PurePosixPath(name.replace("\\", "/"))
        if (path.is_absolute() or ".." in path.parts or
                re.match(r"^[a-zA-Z]:", name) or "\x00" in name or
                path.suffix.lower() not in _IMAGE_EXTENSIONS):
            return False
    clock = data.get("clock", "")
    if clock:
        if not isinstance(clock, str):
            return False
        path = PurePosixPath(clock.replace("\\", "/"))
        if (path.is_absolute() or ".." in path.parts or
                re.match(r"^[a-zA-Z]:", clock) or "\x00" in clock or
                path.suffix.lower() not in _IMAGE_EXTENSIONS):
            return False
    try:
        opacity = int(data.get("background_opacity", 55))
        blur = int(data.get("background_blur", 0))
    except (TypeError, ValueError, OverflowError):
        return False
    return 0 <= opacity <= 100 and 0 <= blur <= 24


def list_themes():
    result = []
    for child in themes_dir().iterdir():
        if not child.is_dir():
            continue
        manifest = _read_manifest(child)
        if manifest and manifest["id"] == child.name:
            result.append(manifest)
    return sorted(result, key=lambda item: item["name"].casefold())


def get_theme(theme_id):
    if not isinstance(theme_id, str) or not _SLUG.fullmatch(theme_id):
        return None
    directory = themes_dir() / theme_id
    manifest = _read_manifest(directory)
    if manifest and manifest["id"] == theme_id:
        return manifest
    return None


def builtin_theme_available(theme_id):
    """检查随程序提供或用户导入的内置主题资源是否齐全。"""
    asset_dir = _BUILTIN_DIRS.get(theme_id)
    if not asset_dir:
        return False
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    if (base / "assets" / asset_dir).is_dir():
        return True
    manifest = get_theme(theme_id)
    return bool(manifest and manifest.get("builtin_theme") == theme_id)


def theme_path(theme_id, relative_path):
    manifest = get_theme(theme_id)
    if not manifest or not isinstance(relative_path, str):
        return ""
    base = (themes_dir() / theme_id).resolve()
    path = (base / relative_path.replace("\\", "/")).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return ""
    return str(path) if path.is_file() else ""


def background_path(theme_id, mode):
    manifest = get_theme(theme_id)
    if not manifest:
        return ""
    filename = manifest.get("backgrounds", {}).get(mode)
    if not filename:
        return ""
    return theme_path(theme_id, filename)


def clock_path(theme_id):
    """返回主题包内可选的桌面时钟插画。"""
    manifest = get_theme(theme_id)
    if not manifest:
        return ""
    filename = manifest.get("clock", "")
    return theme_path(theme_id, filename) if filename else ""


def clock_skins():
    """默认人物始终可用，其余人物由已安装的主题包解锁。"""
    result = [("default", "默认人物")]
    for manifest in list_themes():
        builtin = manifest.get("builtin_theme")
        if builtin in ("anna", "chiyo"):
            result.append((builtin, manifest["name"]))
        elif clock_path(manifest["id"]):
            result.append(("skin:" + manifest["id"], manifest["name"]))
    return result


def valid_clock_skin(skin):
    return skin if skin in dict(clock_skins()) else "default"


def palette(theme_id, mode):
    manifest = get_theme(theme_id)
    if not manifest:
        return {}
    selected = manifest.get("palettes", {}).get(mode, {})
    return dict(selected) if isinstance(selected, dict) else {}


def import_theme(archive_path):
    """校验并安装皮肤包；只接受 JSON、说明文档和位图，不执行代码。"""
    archive_path = Path(archive_path)
    if archive_path.suffix.lower() != ".aztheme":
        raise ValueError("请选择 .aztheme 皮肤包文件。")
    if not archive_path.is_file() or archive_path.stat().st_size > MAX_BUILTIN_ARCHIVE_BYTES:
        raise ValueError("皮肤包不存在或超过 30 MB。")
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if (len(entries) > MAX_BUILTIN_FILES or
                sum(entry.file_size for entry in entries) > MAX_BUILTIN_CONTENT_BYTES):
            raise ValueError("皮肤包文件数量或解压后大小超出限制。")
        files = {}
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if (path.is_absolute() or ".." in path.parts or not path.parts or
                    re.match(r"^[a-zA-Z]:", name) or "\x00" in name):
                raise ValueError("皮肤包包含不安全的文件路径。")
            if entry.is_dir():
                continue
            suffix = path.suffix.lower()
            if name == "manifest.json" or suffix in _BUILTIN_EXTENSIONS:
                if suffix in _IMAGE_EXTENSIONS and entry.file_size > MAX_IMAGE_BYTES:
                    raise ValueError("单张背景图不能超过 18 MB。")
                files[name] = entry
            else:
                raise ValueError("皮肤包只能包含 JSON、说明文档和 PNG/JPG/WEBP 图片。")
        entry = files.get("manifest.json")
        if entry is None or entry.file_size > 128 * 1024:
            raise ValueError("皮肤包缺少有效的 manifest.json。")
        try:
            manifest = json.loads(archive.read(entry).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("manifest.json 不是有效的 UTF-8 JSON。") from exc
        if not _valid_manifest(manifest):
            raise ValueError("主题清单不符合 AZIMUTH 皮肤包标准。")
        builtin_theme = manifest.get("builtin_theme")
        if builtin_theme is not None:
            expected_dir = _BUILTIN_DIRS.get(builtin_theme)
            if (builtin_theme != manifest["id"] or not expected_dir or
                    not any(name.startswith("assets/" + expected_dir + "/")
                            for name in files)):
                raise ValueError("内置主题包的资源目录与主题标识不匹配。")
            if any(not (name == "manifest.json" or name == "README.txt" or
                        name.startswith("assets/" + expected_dir + "/"))
                   for name in files):
                raise ValueError("内置主题包包含了不属于该主题的文件。")
        elif (len(files) > 24 or
              sum(info.file_size for info in files.values()) > MAX_ARCHIVE_BYTES):
            raise ValueError("自定义皮肤包的文件数量或解压后大小超出限制。")
        elif any(PurePosixPath(name).suffix.lower() not in
                 _IMAGE_EXTENSIONS | _TEXT_EXTENSIONS | {".json"}
                 for name in files if name != "manifest.json"):
            raise ValueError("自定义皮肤仅支持说明文档、JSON 与 PNG/JPG/WEBP 图片。")
        for filename in manifest.get("backgrounds", {}).values():
            if filename not in files:
                raise ValueError(f"清单引用的背景图不存在：{filename}")
        clock = manifest.get("clock", "")
        if clock and clock not in files:
            raise ValueError(f"清单引用的时钟插画不存在：{clock}")
        install = themes_dir() / manifest["id"]
        staging = Path(tempfile.mkdtemp(prefix="aztheme-", dir=str(themes_dir())))
        backup = None
        try:
            for name, info in files.items():
                destination = staging.joinpath(*PurePosixPath(name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(info))
            if install.exists():
                backup = install.with_name(install.name + ".previous")
                if backup.exists():
                    shutil.rmtree(backup)
                install.rename(backup)
            staging.rename(install)
            if backup is not None:
                shutil.rmtree(backup)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if backup is not None and backup.exists() and not install.exists():
                backup.rename(install)
            raise
    return manifest


def template_manifest():
    """协议与模板共用同一份有效清单，避免字段规范漂移。"""
    return {
        "format": FORMAT,
        "version": VERSION,
        "id": "my-azimuth-skin",
        "name": "我的 AZIMUTH 主题",
        "author": "设计者",
        "description": "主题说明",
        "palettes": {
            "light": {"canvas": "#F7F5F1", "surface": "#FFFFFF",
                      "ink": "#20232A", "muted": "#62656B",
                      "accent": "#E5484D", "border": "#D7D4D0"},
            "dark": {"canvas": "#191B22", "surface": "#242731",
                     "ink": "#EAE6DF", "muted": "#B8B4B0",
                     "accent": "#F34D63", "border": "#55545E"},
        },
        "backgrounds": {},
        "clock": "",
        "background_opacity": 55,
        "background_blur": 0,
    }
def export_template(destination):
    """导出可直接编辑的主题设计模板和字段规范。"""
    manifest = template_manifest()
    guide = """AZIMUTH 皮肤包设计说明

1. 修改 manifest.json 中的 id、名称、作者和颜色值；id 使用小写英文字母、数字和连字符。
2. 将浅色和深色背景分别保存为 background-light.jpg、background-dark.jpg，建议 1920×1080 或更大，单张不超过 18 MB。
3. 可选：把桌面时钟角色图放在 clock.png，并将 manifest.json 的 clock 填为 "clock.png"；留空时沿用默认时钟图。
4. 保留 manifest.json 和图片在压缩包根目录，压缩为 ZIP 后将扩展名改为 .aztheme。
5. 皮肤包只允许包含 JSON、说明文本和 PNG/JPG/WEBP 图片，不支持脚本、字体或可执行文件。
6. 颜色使用 #RRGGBB；背景透明度范围 0–100，高斯模糊范围 0–24。
7. 没有背景图时，将 backgrounds 设置为空对象：{}。
"""
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(
            manifest, ensure_ascii=False, indent=2))
        archive.writestr("README.txt", guide)
    return str(destination)
