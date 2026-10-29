"""为 Windows 启动脚本保留 UTF-8 编码和 CRLF 换行。"""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
launcher = root / "启动AZIMUTH.bat"
content = launcher.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
launcher.write_bytes(content.replace("\n", "\r\n").encode("utf-8"))
assert b"\r\nchcp 65001 >nul\r\n" in launcher.read_bytes()
print("launcher_encoding_newlines=PASS")
