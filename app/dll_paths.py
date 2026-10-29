# -*- coding: utf-8 -*-
"""保证 Windows 绿色版优先从随包目录加载 Qt 与其运行库。"""
import os
import sys
import ctypes

if sys.platform == "win32" and getattr(sys, "frozen", False):
    # 先加载 Windows 自带 ICU，防止运行环境中的同名 ICU DLL 覆盖 Qt 所需版本。
    system_icu = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icuuc.dll")
    if os.path.isfile(system_icu):
        try:
            _system_icu_handle = ctypes.WinDLL(system_icu, winmode=0x00000800)
        except OSError:
            _system_icu_handle = None

    root = getattr(sys, "_MEIPASS", "")
    if root and os.path.isdir(root):
        handles = [os.add_dll_directory(root)]
        for name in ("PySide6", "shiboken6", "pymupdf"):
            path = os.path.join(root, name)
            if os.path.isdir(path):
                handles.append(os.add_dll_directory(path))
        _dll_directory_handles = handles
