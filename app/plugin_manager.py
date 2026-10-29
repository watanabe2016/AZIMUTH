"""独立网页工具插件的版本化清单、导入和生命周期管理。"""
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import config as cfg

FORMAT = "azimuth-plugin"
VERSION = 1
MAX_BYTES = 20 * 1024 * 1024
MAX_FILES = 128
EXTENSIONS = {".html", ".css", ".js", ".json", ".png", ".jpg", ".jpeg",
              ".webp", ".svg", ".md", ".txt"}
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


def plugins_dir():
    path = Path(cfg.config_path()).parent / "plugins"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_name(name):
    """使用唯一、跨平台的相对路径，拒绝 Windows 特殊文件名和别名。"""
    if not isinstance(name, str) or not name or "\\" in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or str(path) != name or any(
        part in (".", "..") or part.endswith((".", " ")) or
        any(char in part for char in ':<>"|?*\x00') or
        any(ord(char) < 32 for char in part) or
        re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
        for part in path.parts
    ):
        return False
    return bool(path.parts)


def valid_manifest(data):
    if not isinstance(data, dict):
        return False
    if (data.get("format") != FORMAT or type(data.get("version")) is not int or
            data["version"] != VERSION or data.get("type") != "web-tool"):
        return False
    if not isinstance(data.get("id"), str) or not SLUG.fullmatch(data["id"]):
        return False
    for key, limit in (("name", 48), ("author", 80), ("description", 240)):
        value = data.get(key, "")
        if not isinstance(value, str) or len(value) > limit:
            return False
        if key == "name" and not value.strip():
            return False
    entry = data.get("entry")
    return (safe_name(entry) and PurePosixPath(entry).suffix.lower() == ".html"
            and data.get("permissions", []) == [])


def get_plugin(plugin_id):
    if not isinstance(plugin_id, str) or not SLUG.fullmatch(plugin_id):
        return None
    root = plugins_dir() / plugin_id
    if root.is_symlink():
        return None
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if (valid_manifest(manifest) and manifest["id"] == plugin_id and
                (root / manifest["entry"]).is_file() and
                (root / manifest["entry"]).resolve().is_relative_to(root.resolve())):
            return manifest
    except (OSError, ValueError, TypeError):
        pass
    return None


def list_plugins():
    result = []
    for path in plugins_dir().iterdir():
        if path.is_dir() and (manifest := get_plugin(path.name)):
            result.append(manifest)
    return sorted(result, key=lambda item: item["name"].casefold())


def import_plugin(source):
    """完整验证后原子替换同 ID 插件；失败时保留原安装。"""
    source = Path(source)
    if source.suffix.lower() != ".azplugin" or not source.is_file():
        raise ValueError("请选择 .azplugin 插件包。")
    if source.stat().st_size > MAX_BYTES:
        raise ValueError("插件压缩包不能超过 20 MB。")
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_FILES or sum(item.file_size for item in entries) > MAX_BYTES:
            raise ValueError("插件最多 128 个条目，解压后不能超过 20 MB。")
        files, names = {}, set()
        for entry in entries:
            if entry.orig_filename != entry.filename:
                raise ValueError("插件路径不能包含反斜杠或空字符。")
            name = entry.filename.rstrip("/") if entry.is_dir() else entry.filename
            if not safe_name(name) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("插件包含不安全的路径或符号链接。")
            if name.casefold() in names:
                raise ValueError("插件包含重复文件名或大小写冲突。")
            names.add(name.casefold())
            if entry.is_dir():
                continue
            if PurePosixPath(name).suffix.lower() not in EXTENSIONS:
                raise ValueError("插件仅支持网页、脚本、样式、JSON、图片和说明文档。")
            files[name] = entry
        if "manifest.json" not in files or files["manifest.json"].file_size > 128 * 1024:
            raise ValueError("插件根目录缺少有效的 manifest.json。")
        try:
            manifest = json.loads(archive.read(files["manifest.json"]).decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise ValueError("manifest.json 必须是 UTF-8 JSON。") from exc
        if not valid_manifest(manifest):
            raise ValueError("插件清单不符合协议 v1；请检查格式、类型、版本、ID、名称和入口。")
        if manifest["entry"] not in files:
            raise ValueError("清单指定的 HTML 入口不存在。")
        root = plugins_dir()
        target = root / manifest["id"]
        if target.is_symlink():
            raise ValueError("插件安装位置不能是符号链接。")
        staging = Path(tempfile.mkdtemp(prefix=".install-", dir=root))
        backup = None
        try:
            for name, entry in files.items():
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(entry))
            if target.exists():
                backup = Path(tempfile.mkdtemp(prefix=".previous-", dir=root)) / "plugin"
                target.rename(backup)
            staging.rename(target)
        except Exception:
            if backup is not None and backup.exists() and not target.exists():
                backup.rename(target)
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
            if backup is not None and backup.parent.exists():
                shutil.rmtree(backup.parent)
    return manifest


def remove_plugin(plugin_id):
    if not get_plugin(plugin_id):
        raise ValueError("插件不存在或清单无效。")
    target = plugins_dir() / plugin_id
    if target.is_symlink() or target.resolve().parent != plugins_dir().resolve():
        raise ValueError("插件目录无效。")
    shutil.rmtree(target)


def template_manifest():
    return {"format": FORMAT, "version": VERSION, "id": "heading-helper",
            "name": "航向换算示例", "author": "AZIMUTH 开发示例",
            "description": "输入航向，计算反向航向，演示主题适配与离线交互。",
            "type": "web-tool", "entry": "index.html", "permissions": []}


EXAMPLE_HTML = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>航向换算</title><style>
*{box-sizing:border-box}body{margin:0;padding:32px;background:var(--az-canvas);
color:var(--az-ink);font:16px "Microsoft YaHei",sans-serif}
main{max-width:640px;margin:auto}h1{font-size:28px;margin:0 0 12px}
p{color:var(--az-muted);line-height:1.7}label{display:block;margin:28px 0 10px}
input,button{font:inherit;padding:12px 16px;border-radius:10px}
input{width:180px;background:var(--az-surface);color:var(--az-ink);border:1px solid var(--az-border)}
button{margin-left:12px;background:var(--az-accent);color:var(--az-on-accent);border:0;cursor:pointer}
:focus-visible{outline:2px solid var(--az-accent);outline-offset:3px}
output{display:block;margin-top:32px;font-size:24px;font-variant-numeric:tabular-nums}
@media(max-width:480px){body{padding:20px}button{margin:12px 0 0;display:block}}
</style></head><body><main><h1>航向换算</h1>
<p>本地离线计算。示例插件使用软件提供的主题变量。</p>
<form id="form"><label for="heading">航向（0° ≤ 航向 &lt; 360°）</label>
<input id="heading" type="number" min="0" max="359.999999" step="any" value="45" required>
<button type="submit">计算反向航向</button></form>
<output id="result" aria-live="polite">反向航向：225°</output>
</main><script>
document.querySelector('#form').addEventListener('submit',event=>{
event.preventDefault();const value=Number(document.querySelector('#heading').value);
document.querySelector('#result').textContent=Number.isFinite(value)&&value>=0&&value<360?
'反向航向：'+Number(((value+180)%360).toFixed(6))+'°':'请输入 0° 到 360° 之间的有效数值';
});
</script></body></html>'''


def export_template(destination):
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(template_manifest(), ensure_ascii=False, indent=2))
        archive.writestr("index.html", EXAMPLE_HTML)
    return str(destination)
