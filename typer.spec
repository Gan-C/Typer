# -*- mode: python ; coding: utf-8 -*-
# Build of Typer: bundles CPU PyTorch + transformers + DistilGPT-2 so the offline
# "revise" feature works with ZERO setup (no pip, no model download by the user).
#
# This is a ONEDIR build (a folder, not a single file) because bundling PyTorch makes a
# single-file exe start very slowly; the installer ships the whole folder. Build it with
# build.bat, or by hand:
#
#   set TYPER_MODELS_DIR=<folder containing a distilgpt2\ subfolder>
#   pyinstaller typer.spec
#
# The result is dist\Typer\Typer.exe (+ an _internal folder with torch and the model).

import os
from PyInstaller.utils.hooks import collect_all

datas = [('typer_icon.png', '.'), ('typer_icon.ico', '.')]
binaries = []
hiddenimports = ['typer_engine', 'typer_revise_local']

# the pre-downloaded, self-contained models -> bundled under "models/" in the app
models_dir = os.environ.get('TYPER_MODELS_DIR', 'models')
if os.path.isdir(models_dir):
    datas.append((models_dir, 'models'))

# pull in the ML stack completely so nothing is missing at runtime
for pkg in ('torch', 'transformers', 'tokenizers', 'safetensors', 'huggingface_hub', 'regex'):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

block_cipher = None

a = Analysis(
    ['typer_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['pynput', 'typer_backends_posix', 'matplotlib', 'pandas', 'cv2', 'tensorflow'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # onedir: binaries live beside the exe, not inside it
    name='Typer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # UPX can corrupt PyTorch DLLs - keep it off
    console=False,                  # no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='typer_icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Typer',
)
