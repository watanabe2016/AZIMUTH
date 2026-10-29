# -*- coding: utf-8 -*-
"""
一键安装到本机 —— 创建桌面快捷方式与开始菜单项

用法：
    python install.py            # 安装（打包版 exe）
    python install.py --dev      # 安装（开发模式，指向 python main.py）
"""
import os
import sys
import argparse
import winreg

APP_NAME = "AZIMUTH"


def desktop_dir():
    return os.path.join(os.path.expanduser("~"), "Desktop")


def startmenu_dir():
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                        "Microsoft", "Windows", "Start Menu", "Programs")


def app_root():
    return os.path.dirname(os.path.abspath(__file__))


def find_exe():
    """在自身目录下找 exe"""
    root = app_root()
    for name in os.listdir(root):
        if name.lower().endswith(".exe"):
            return os.path.join(root, name)
    return None


def make_shortcut(path, target, args="", workdir="", icon=""):
    """用 Windows Script Host 创建快捷方式。
    注意：沙箱环境会拦 COM，但这里是给用户双击运行的，不在沙箱内。"""
    import subprocess
    vbs = f'''
Set oWS = WScript.CreateObject("WScript.Shell")
Set oLink = oWS.CreateShortcut("{path}")
oLink.TargetPath = "{target}"
oLink.Arguments = "{args}"
oLink.WorkingDirectory = "{workdir}"
oLink.IconLocation = "{icon}"
oLink.Description = "{APP_NAME}"
oLink.Save
'''
    tmp = os.path.join(os.environ.get("TEMP", "."), "_mklnk.vbs")
    with open(tmp, "w", encoding="gbk") as f:
        f.write(vbs)
    r = subprocess.run(["cscript", "//nologo", tmp],
                       capture_output=True, text=True)
    try:
        os.remove(tmp)
    except Exception:
        pass
    return r.returncode == 0


def install(dev=False):
    root = app_root()

    if dev:
        py = sys.executable
        target = py
        args = '"%s"' % os.path.join(root, "app", "main.py")
        workdir = root
        icon = os.path.join(root, "assets", "icon.ico")
    else:
        exe = find_exe()
        if not exe:
            print("× 没有找到 exe。请先运行打包脚本，或用 --dev 装开发版。")
            return False
        target = exe
        args = ""
        workdir = os.path.dirname(exe)
        icon = os.path.join(workdir, "assets", "icon.ico")
        if not os.path.exists(icon):
            icon = exe

    ok = []
    lnk = os.path.join(desktop_dir(), APP_NAME + ".lnk")
    if make_shortcut(lnk, target, args, workdir, icon):
        ok.append("桌面：" + lnk)
    lnk2 = os.path.join(startmenu_dir(), APP_NAME + ".lnk")
    if make_shortcut(lnk2, target, args, workdir, icon):
        ok.append("开始菜单：" + lnk2)

    print("安装完成，已创建：")
    for x in ok:
        print("  ·", x)
    print("\n双击桌面图标即可启动。")
    return len(ok) > 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", action="store_true", help="安装开发模式快捷方式")
    a = ap.parse_args()
    install(a.dev)
