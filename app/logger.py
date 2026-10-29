# -*- coding: utf-8 -*-
"""
日志模块
统一把运行日志写到用户目录下的 logs/ 里，方便考试出问题时排查。
支持导出日志（打包给开发者看）。
"""
import os
import sys
import logging
import zipfile
import platform
import datetime
from logging.handlers import RotatingFileHandler
from storage import writable_directory

APP_NAME = "CAAC Ground Station"
APP_VER = "1.10.0"


def app_root():
    """返回应用根目录：打包后是 exe 所在目录，开发时是项目目录"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return writable_directory(os.path.join(app_root(), "logs"),
                              os.path.join(base, APP_NAME, "logs"))


_logger = None


def get_logger():
    global _logger
    if _logger is not None:
        return _logger

    lg = logging.getLogger("caac")
    lg.setLevel(logging.DEBUG)
    lg.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # 滚动文件：单文件最大 2MB，保留 5 个
    fh = RotatingFileHandler(
        os.path.join(log_dir(), "app.log"),
        maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    lg.addHandler(fh)

    # 开发模式下同时输出到控制台
    if not getattr(sys, "frozen", False):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(logging.INFO)
        lg.addHandler(sh)

    _logger = lg
    return lg


def export_logs(dest_zip: str) -> str:
    """把 logs 目录打包成 zip，返回 zip 路径"""
    lg = get_logger()
    lg.info("开始导出日志到 %s", dest_zip)
    src = log_dir()
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for name in os.listdir(src):
            p = os.path.join(src, name)
            if os.path.isfile(p):
                z.write(p, name)
        # 附上环境信息，方便排查
        env = [
            "app=%s" % APP_NAME,
            "version=%s" % APP_VER,
            "time=%s" % datetime.datetime.now().isoformat(),
            "os=%s" % platform.platform(),
            "python=%s" % sys.version.replace("\n", " "),
            "frozen=%s" % bool(getattr(sys, "frozen", False)),
            "root=%s" % app_root(),
        ]
        z.writestr("environment.txt", "\n".join(env))
    lg.info("日志导出完成")
    return dest_zip
