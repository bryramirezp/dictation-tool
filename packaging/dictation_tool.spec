# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for Dictation Tool.

    py -3 -m PyInstaller --noconfirm packaging/dictation_tool.spec

Processor only, on purpose. The NVIDIA libraries weigh 925 MB against 190 MB for
everything else put together, which is not a reasonable download for a dictation
tool. Anyone who wants the graphics card runs it from source; the app falls back
to the processor by itself when those libraries are missing.

onedir rather than onefile: onefile unpacks the whole thing into a temporary
folder on every single launch, which for 190 MB is a wait before anything even
appears on screen.
"""
import os
from PyInstaller.utils.hooks import (collect_all, collect_data_files,
                                     collect_dynamic_libs)

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [(os.path.join(ROOT, "assets", "icon.ico"), "assets")]
binaries = []
hiddenimports = []

# Reads its theme JSON and its fonts from disk at runtime.
datas += collect_data_files("customtkinter")
# Ships the PortAudio DLL as package data.
datas += collect_data_files("sounddevice")
# Ships the Silero voice-activity model as an .onnx file.
datas += collect_data_files("faster_whisper")
# The engine itself: a 57 MB DLL that no import scan would ever notice.
binaries += collect_dynamic_libs("ctranslate2")

# Runs the voice-activity model. Loads its providers dynamically, so nothing
# short of collect_all finds them.
for pkg in ("onnxruntime", "av"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(ROOT, "dictation_tool.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "nvidia",       # 925 MB of CUDA libraries, see the note above
        "torch",        # faster-whisper runs on ctranslate2, not torch
        "matplotlib", "scipy", "pandas", "IPython", "pytest", "setuptools",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="DictationTool",
    debug=False,
    strip=False,
    upx=False,          # UPX-packed binaries trip antivirus heuristics
    console=False,      # tray app: a console window would sit there empty
    icon=os.path.join(ROOT, "assets", "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DictationTool",
)
