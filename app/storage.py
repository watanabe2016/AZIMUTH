# -*- coding: utf-8 -*-
"""本地存储基础设施：可写目录选择与原子 JSON 写入。"""
import json
import os
from pathlib import Path
import tempfile


def writable_directory(primary, fallback):
    """实际试写后选择目录，避免程序放在只读目录时启动失败。"""
    errors = []
    for candidate in (primary, fallback):
        try:
            directory = Path(candidate)
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=directory) as probe:
                probe.write(b"ok")
                probe.flush()
            return str(directory)
        except OSError as exc:
            errors.append(str(exc))
    raise OSError("没有可写的数据目录：" + "；".join(errors))


def atomic_json(path, value):
    """完整落盘后替换旧文件；失败时保留旧数据并清理临时文件。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=target.parent, prefix=target.name + ".",
                                         suffix=".tmp", delete=False) as stream:
            temp = stream.name
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)
