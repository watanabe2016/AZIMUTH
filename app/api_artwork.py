"""模型原图的显示适配；只移除可执行或外部资源，不检查图形和答案是否正确。"""
import re
from xml.etree import ElementTree as ET


def svg_for_display(source):
    """保留 SVG 点位、路径、文字及样式，阻止脚本和外部文件读取。"""
    if not isinstance(source, str) or not source.strip():
        return None
    text = source.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[^\n]*\n|\n```$", "", text).strip()
    if len(text.encode("utf-8")) > 2_000_000:
        return None
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError):
        return None
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        return None
    blocked = {"script", "foreignobject", "iframe", "object", "embed", "audio", "video"}
    for parent in root.iter():
        for child in list(parent):
            if child.tag.rsplit("}", 1)[-1].lower() in blocked:
                parent.remove(child)
        for key, value in list(parent.attrib.items()):
            name = key.rsplit("}", 1)[-1].lower()
            if (name.startswith("on") or
                    (name == "href" and not value.strip().startswith("#")) or
                    any(not uri.startswith("#") for uri in
                        re.findall(r"url\(\s*['\"]?([^'\"\s)]+)", value, re.I))):
                del parent.attrib[key]
        if parent.tag.rsplit("}", 1)[-1].lower() == "style" and parent.text:
            parent.text = re.sub(r"@import[^;]+;|url\([^)]*\)", "", parent.text, flags=re.I)
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    return ET.tostring(root, encoding="utf-8")
