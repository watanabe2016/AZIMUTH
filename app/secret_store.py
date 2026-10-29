# -*- coding: utf-8 -*-
"""在 Windows 上使用当前用户 DPAPI 保护 API 密钥。"""
import base64
import ctypes
import os
from ctypes import wintypes


_DPAPI_PREFIX = "dpapi:"
_PLAIN_PREFIX = "plain:"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data):
    buffer = ctypes.create_string_buffer(data)
    value = _DataBlob(len(data), ctypes.cast(
        buffer, ctypes.POINTER(ctypes.c_ubyte)))
    return value, buffer


def protect_secret(value):
    """返回可写入设置文件的密钥；Windows 使用用户级 DPAPI。"""
    value = str(value or "")
    if not value:
        return ""
    raw = value.encode("utf-8")
    if os.name == "nt":
        try:
            crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            source, source_buffer = _blob(raw)
            destination = _DataBlob()
            ok = crypt32.CryptProtectData(
                ctypes.byref(source), "AZIMUTH Vision API Key", None, None,
                None, 0, ctypes.byref(destination))
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                protected = ctypes.string_at(destination.pbData,
                                             destination.cbData)
            finally:
                kernel32.LocalFree(destination.pbData)
            return _DPAPI_PREFIX + base64.b64encode(protected).decode("ascii")
        except Exception as exc:
            raise RuntimeError("Windows 无法保护 API Key") from exc
    return _PLAIN_PREFIX + value


def reveal_secret(value):
    """读取新旧设置格式；无法解密时返回空串，绝不把密文发给 API。"""
    value = str(value or "")
    if value.startswith(_PLAIN_PREFIX):
        return value[len(_PLAIN_PREFIX):]
    if not value.startswith(_DPAPI_PREFIX):
        return value
    if os.name != "nt":
        return ""
    try:
        encoded = base64.b64decode(value[len(_DPAPI_PREFIX):], validate=True)
        source, source_buffer = _blob(encoded)
        destination = _DataBlob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0,
            ctypes.byref(destination))
        if not ok:
            return ""
        try:
            return ctypes.string_at(destination.pbData,
                                    destination.cbData).decode("utf-8")
        finally:
            kernel32.LocalFree(destination.pbData)
    except Exception:
        return ""
