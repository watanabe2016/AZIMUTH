# -*- coding: utf-8 -*-
"""可移植构建入口：使用当前解释器，产物和缓存均位于项目内部。"""
from pathlib import Path
import subprocess
import sys
import os
import shutil
from datetime import datetime

root = Path(__file__).resolve().parents[1]
if any(argument in ("-h", "--help") for argument in sys.argv[1:]):
    print("用法：python build/build_release.py [--output-dir 目录] [--debug-console]")
    print("默认输出到 dist-final；调试控制台输出到 dist-debug。")
    raise SystemExit(0)
subprocess.run([sys.executable, str(root / 'build' / 'repair_launcher.py')],
               cwd=root, check=True)
output_root = None
for index, argument in enumerate(sys.argv):
    if argument == '--output-dir' and index + 1 < len(sys.argv):
        output_root = Path(sys.argv[index + 1]).expanduser().resolve()
        break
    if argument.startswith('--output-dir='):
        output_root = Path(argument.split('=', 1)[1]).expanduser().resolve()
        break
if output_root is None:
    output_root = root / ('dist-debug' if '--debug-console' in sys.argv else 'dist-final')
dist = output_root / 'AZIMUTH'
# PyInstaller 会重建发布目录，先保存绿色版已有的设置、记录与用户题库。
state_names = ('config.json', 'data', 'notes', 'logs')
state_backup = root / '.build-cache' / ('portable-state-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
for name in state_names:
    source = dist / name
    if source.is_dir():
        shutil.copytree(source, state_backup / name)
    elif source.is_file():
        state_backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, state_backup / name)
ocr_source = root / 'tools' / 'ocr' / 'Tesseract-OCR'
ocr_exe = ocr_source / 'tesseract.exe'
ocr_runtime = root / '.build-cache' / 'ocr-runtime'
if ocr_exe.is_file():
    ocr_runtime.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ocr_exe, ocr_runtime / ocr_exe.name)
    for dll in ocr_source.glob('*.dll'):
        shutil.copy2(dll, ocr_runtime / dll.name)
    model_source = ocr_source / 'tessdata'
    model_dest = ocr_runtime / 'tessdata'
    model_dest.mkdir(parents=True, exist_ok=True)
    for model in model_source.glob('*.traineddata'):
        shutil.copy2(model, model_dest / model.name)
    for folder in ('configs', 'tessconfigs'):
        source = model_source / folder
        if source.is_dir():
            shutil.copytree(source, model_dest / folder, dirs_exist_ok=True)
    for name in ('LICENSE-Tesseract.txt', 'LICENSE-2.0.txt',
                 'THIRD_PARTY_NOTICES.txt'):
        license_file = root / 'tools' / 'ocr' / name
        if license_file.is_file():
            shutil.copy2(license_file, ocr_runtime / name)

# 发布构建清理编译缓存，避免源码已更新而归档仍复用旧动画字节码。
args = [sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
        '--console' if '--debug-console' in sys.argv else '--windowed',
        '--name', 'AZIMUTH', '--distpath', str(output_root),
        '--workpath', str(root / '.build-cache'), '--specpath', str(root / '.build-cache'),
        '--add-data', str(root / 'assets') + ';assets',
        '--add-data', str(root / 'app' / 'web') + ';web',
        '--icon', str(root / 'assets' / 'icon.ico'), '--hidden-import', 'pymupdf',
        '--hidden-import', 'fitz',
        '--hidden-import', 'PySide6.QtWebChannel',
        '--hidden-import', 'PySide6.QtMultimedia',
        '--collect-all', 'rapidocr',
        '--collect-all', 'onnxruntime',
        '--runtime-hook', str(root / 'app' / 'dll_paths.py')]
if ocr_exe.is_file():
    args += ['--add-data', str(ocr_runtime) + ';tools/ocr/Tesseract-OCR']
else:
    print('未找到项目内 Tesseract；导入仍可手工校对，但不会执行 OCR。')
for module in ('PySide6.Qt3DCore', 'PySide6.QtCharts', 'PySide6.QtDataVisualization',
               'PySide6.QtQuick', 'PySide6.QtQml',
               'tkinter', 'matplotlib', 'scipy'):
    args += ['--exclude-module', module]
args.append(str(root / 'app' / 'main.py'))
try:
    subprocess.run(args, cwd=root, check=True)
finally:
    # 构建失败时也保留原有用户文件，避免中断导致学习记录丢失。
    for name in state_names:
        saved = state_backup / name
        if saved.is_dir():
            shutil.copytree(saved, dist / name, dirs_exist_ok=True)
        elif saved.is_file():
            dist.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, dist / name)
# 避免分发构建环境自动收集的可选运行库覆盖系统/Qt 自带版本。
internal = dist / '_internal'
for pattern in ('api-ms-win-*.dll', 'ucrtbase.dll', 'icu*.dll'):
    for dll in internal.glob(pattern):
        dll.unlink()
print(dist)
