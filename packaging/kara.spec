# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for Kara.

    py -3 -m PyInstaller --noconfirm packaging/kara.spec        processor only
    set KARA_GPU=1 && py -3 -m PyInstaller ... packaging/kara.spec   with CUDA

Two builds from one spec. The NVIDIA libraries weigh 925 MB against 190 MB for
everything else put together, which is not a reasonable download to hand
everybody for a dictation tool -- but leaving them out of every build meant
nobody with a card could use it either, and the people who had one did not find
out, because the app hid the option rather than explaining it. So there is a
second, large installer for people who have an NVIDIA card, and the ordinary one
still falls back to the processor by itself.

onedir rather than onefile: onefile unpacks the whole thing into a temporary
folder on every single launch, which for 190 MB is a wait before anything even
appears on screen, and for 1.2 GB would be unusable.
"""
import glob
import os
import site
from PyInstaller.utils.hooks import (collect_all, collect_data_files,
                                     collect_dynamic_libs)

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
GPU = bool(os.environ.get("KARA_GPU"))
print("== Kara spec: %s build ==" % ("GPU" if GPU else "processor-only"))

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


def nvidia_dlls():
    """The CUDA DLLs, keeping the nvidia/<lib>/bin shape the wheels ship them in.

    collect_dynamic_libs would flatten them into one directory. The shape is not
    decoration: kara._add_nvidia_dlls walks exactly this layout to hand each
    folder to os.add_dll_directory, which since Python 3.8 is the only way an
    extension module finds a DLL at all. Flattened, ctranslate2 would fail to
    load a model with a message naming cublas64_12.dll, in a build that is
    carrying cublas64_12.dll.
    """
    roots = []
    for getter in ("getsitepackages", "getusersitepackages"):
        if hasattr(site, getter):
            got = getattr(site, getter)()
            roots.extend(got if isinstance(got, list) else [got])

    found = []
    for root in roots:
        for folder in glob.glob(os.path.join(root, "nvidia", "*", "bin")):
            dest = os.path.relpath(folder, root)          # nvidia\<lib>\bin
            for dll in glob.glob(os.path.join(folder, "*.dll")):
                found.append((dll, dest))
    if not found:
        raise SystemExit(
            "KARA_GPU is set but no NVIDIA wheels were found.\n"
            "  pip install -r requirements-gpu.txt")
    print("   %d CUDA DLLs" % len(found))
    return found


if GPU:
    binaries += nvidia_dlls()

EXCLUDES = [
    "torch",        # faster-whisper runs on ctranslate2, not torch
    "matplotlib", "scipy", "pandas", "IPython", "pytest", "setuptools",
]
if not GPU:
    # 925 MB of CUDA libraries, kept out of the ordinary download. The GPU build
    # adds them back through nvidia_dlls() above rather than through the import
    # scan, because the scan cannot preserve the directory layout they need.
    EXCLUDES.append("nvidia")

a = Analysis(
    [os.path.join(ROOT, "kara.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="Kara",
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
    name="Kara",
)
