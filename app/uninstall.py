# -*- coding: utf-8 -*-
"""
卸载模块
删除：桌面/开始菜单快捷方式、题库缓存、日志、配置。
不删除程序所在目录（绿色版原则：用户自己决定是否删目录）。
"""
import os
import sys
import shutil
from PySide6.QtWidgets import QMessageBox
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from logger import get_logger, log_dir, app_root

log = get_logger()

APP_TITLE = "AZIMUTH"


def desktop_dir():
    return os.path.join(os.path.expanduser("~"), "Desktop")


def startmenu_dir():
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                        "Microsoft", "Windows", "Start Menu", "Programs")


def config_dir():
    """配置与缓存目录"""
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(base, "CAACGroundStation")


def _remove_quiet(path, is_dir=False):
    """静默删除，返回 (是否成功, 说明)"""
    try:
        if not os.path.exists(path):
            return True, "不存在，跳过"
        if is_dir:
            shutil.rmtree(path, ignore_errors=True)
        else:
            os.remove(path)
        return not os.path.exists(path), "已删除" if not os.path.exists(path) else "删除失败"
    except Exception as e:
        log.error("删除失败 %s: %r", path, e)
        return False, str(e)


def collect_targets():
    """收集所有要删的东西"""
    items = []
    # 快捷方式
    for d in (desktop_dir(), startmenu_dir()):
        for name in (f"{APP_TITLE}.lnk", "CAAC Ground Station.lnk",
                     "CAAC地面站辅助工具.lnk"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                items.append((p, False, "快捷方式"))
    # 日志
    ld = log_dir()
    if os.path.exists(ld):
        items.append((ld, True, "日志目录"))
    # 配置缓存
    cd = config_dir()
    if os.path.exists(cd):
        items.append((cd, True, "配置缓存"))
    return items


def run_uninstall(parent=None):
    """执行卸载流程，返回是否完成"""
    targets = collect_targets()

    if not targets:
        QMessageBox.information(
            parent, "无需卸载",
            "未发现快捷方式、日志或配置缓存。\n\n"
            "如果你想把程序本身也删掉，直接删除下面的目录即可：\n"
            + app_root())
        return False

    lines = []
    for p, is_dir, kind in targets:
        lines.append(f"· [{kind}] {p}")
    detail = "\n".join(lines)

    r = QMessageBox.warning(
        parent, "确认卸载（不可撤销）",
        "将删除以下内容：\n\n" + detail +
        "\n\n注意：程序目录本身不会被删除：\n" + app_root(),
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    if r != QMessageBox.Yes:
        log.info("用户取消卸载")
        return False

    ok, fail = [], []
    for p, is_dir, kind in targets:
        success, msg = _remove_quiet(p, is_dir)
        (ok if success else fail).append(f"{kind}: {p} ({msg})")

    log.warning("卸载完成 成功=%d 失败=%d", len(ok), len(fail))

    if fail:
        QMessageBox.warning(
            parent, "部分未完成",
            "以下内容未能删除，可能被占用或需要管理员权限：\n\n"
            + "\n".join(fail) +
            "\n\n可手动删除后重试。")
    else:
        QMessageBox.information(
            parent, "卸载完成",
            "快捷方式、日志与配置已清理。\n\n"
            "程序目录仍保留在：\n" + app_root() +
            "\n\n确认不再使用后可手动删除该目录。")
    return True


if __name__ == "__main__":
    # 命令行卸载入口（供快捷方式调用）
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", action="store_true")
    a = ap.parse_args()
    if a.cli:
        for p, is_dir, kind in collect_targets():
            success, msg = _remove_quiet(p, is_dir)
            print(("[OK] " if success else "[FAIL] ") + kind + " " + p)
