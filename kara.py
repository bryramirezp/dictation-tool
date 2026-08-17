"""
Kara — speak, and it types
Push-to-talk dictation powered by faster-whisper.
Hold the configured hotkey to record. Release to transcribe and paste.
Default hotkey: Insert
"""

__version__ = "0.2.4"

import sys
import os
import io
import json
import math
import shutil
import subprocess
import threading
import time
import queue
import ctypes
import datetime
import platform
from collections import deque

# True in the downloadable build. It leaves the CUDA libraries out on purpose --
# they weigh 925 MB against 190 MB for the whole rest of the program -- so a
# graphics card is simply not on offer there, and pretending otherwise strands
# anyone who picks it.
IS_PACKAGED = getattr(sys, "frozen", False)
SOURCE_URL  = "https://github.com/bryramirezp/kara"

# ── Single instance guard ─────────────────────────────────────────────────────
def ensure_single_instance():
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, "Kara_SingleInstance")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(
            None,
            "Kara is already open.\nLook for the icon near the clock.",
            "Kara", 0x40)
        sys.exit(0)

ensure_single_instance()

# ── CUDA DLL fix (nvidia-cublas-cu12 wheel) ───────────────────────────────────
_dll_dirs = []      # keeps the os.add_dll_directory handles alive

def _add_nvidia_dlls():
    """Make the DLLs from the NVIDIA wheels findable.

    Since Python 3.8, extension modules only search the directories handed to
    os.add_dll_directory(), so putting them on PATH alone is not enough. PATH is
    still set because the child processes we spawn read it.

    site.getsitepackages() is missing from a frozen build, hence the hasattr.
    """
    import site
    roots = []
    for getter in ("getsitepackages", "getusersitepackages"):
        if hasattr(site, getter):
            got = getattr(site, getter)()
            roots.extend(got if isinstance(got, list) else [got])

    for root in roots:
        for name in ("cublas", "cudnn", "cuda_runtime"):
            d = os.path.join(root, "nvidia", name, "bin")
            if not os.path.isdir(d):
                continue
            if d not in os.environ.get("PATH", ""):
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            try:
                # Held onto on purpose: os.add_dll_directory undoes itself when
                # the handle it returns is garbage collected.
                _dll_dirs.append(os.add_dll_directory(d))
            except (AttributeError, OSError):
                pass

def cuda_libs_present():
    """True when the libraries ctranslate2 needs for CUDA can really load.

    Counting CUDA devices is not enough: ctranslate2 answers that from the
    driver, so it reports a card even with no maths libraries installed, and the
    trouble only surfaces later as "Library cublas64_12.dll is not found" while
    loading a model.

    cuBLAS is the one to test. cuDNN is not: ctranslate2 ships its own copy, so
    it always loads and would make this always say yes. Trying to load it by
    name covers both the pip wheels and a system-wide CUDA install.
    """
    try:
        import ctranslate2  # noqa: F401  -- registers its own DLL directory
    except Exception:
        return False
    for name in ("cublas64_12.dll", "cublas64_11.dll"):
        try:
            ctypes.WinDLL(name)
            return True
        except OSError:
            continue
    return False

_add_nvidia_dlls()

# ── Model cache layout ────────────────────────────────────────────────────────
# Some huggingface_hub versions fill snapshots/ with symlinks pointing into
# blobs/. Plain files instead: nothing else on the machine shares this cache, so
# they cost the same disk space, and they take one thing out of the picture when
# a model will not load. Not the cause of issue #1 -- that was a lock -- but a
# symlinked cache is one more way for that failure to look like a different one.
#
# Set before faster_whisper is imported: huggingface_hub reads it at import.
# Versions that predate the variable ignore it, which is why the check further
# down does not trust it on its own.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from faster_whisper.utils import download_model
import pyperclip
import pyautogui
import pystray
import customtkinter as ctk
import tkinter as tk
from PIL import Image, ImageDraw
from pynput.mouse import Button, Listener as MouseListener
from pynput.keyboard import Key, KeyCode, Listener as KeyboardListener

pyautogui.FAILSAFE = False  # corner-of-screen abort would break paste mid-flow

# ── Paths ─────────────────────────────────────────────────────────────────────
_LOCAL        = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
APP_DIR       = os.path.join(_LOCAL, "Kara")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
os.makedirs(APP_DIR, exist_ok=True)

# The app was called Dictation Tool until 0.2.0 and kept its settings under that
# name. Carry them across once, so a rename does not quietly hand everybody back
# the default hotkey and language.
if not os.path.exists(SETTINGS_FILE):
    _old = os.path.join(_LOCAL, "DictationTool", "settings.json")
    if os.path.exists(_old):
        try:
            shutil.copyfile(_old, SETTINGS_FILE)
        except OSError:
            pass

# ── Development instrumentation ───────────────────────────────────────────────
# Off unless KARA_DEBUG_LOG is set, so a normal build never writes this file and
# never can: there is no setting that turns it on and no network code anywhere
# in it. It exists to answer questions the developer cannot answer from his own
# machine -- "why does pressing the key take two seconds on that laptop and
# eight milliseconds on this one" -- by timing each step separately on the
# machine that is actually slow.
#
# What it records is timings, sizes and device names. What it never records is
# what anybody said: the transcript is not passed in, and the only thing derived
# from it is how many characters long it was. Keep it that way. A file that
# cannot leak speech is worth more than one with a redaction pass.
#
#     setx KARA_DEBUG_LOG 1
#     setx KARA_MACHINE   notebook-a
#
# Writes one JSON object per line to  %LOCALAPPDATA%\Kara\trace.jsonl
TRACE_FILE = os.path.join(APP_DIR, "trace.jsonl")


class _Trace:
    """One JSONL line per press-to-typed cycle, or nothing at all."""

    def __init__(self):
        self.enabled = bool(os.environ.get("KARA_DEBUG_LOG"))
        self.machine = os.environ.get("KARA_MACHINE") or platform.node()
        self._seq = 0
        self._lock = threading.Lock()
        self._cur = None

    def start(self):
        """Begin a cycle. Called on key down, discarding any half-finished one."""
        if not self.enabled:
            return
        self._seq += 1
        self._cur = {"t0": time.perf_counter(), "ms": {}, "seq": self._seq}

    def mark(self, name):
        """Milliseconds from key down to now, under `name`."""
        if not self.enabled or self._cur is None:
            return
        self._cur["ms"][name] = round((time.perf_counter() - self._cur["t0"]) * 1000, 1)

    def span(self, name):
        """Time one call: `with trace.span('stream_start'): ...`"""
        return _Span(self, name)

    def _record(self, name, ms):
        if self._cur is not None:
            self._cur["ms"][name] = round(ms, 1)

    def set(self, **fields):
        if self.enabled and self._cur is not None:
            self._cur.update(fields)

    def finish(self, **fields):
        """Write the line and close the cycle out."""
        if not self.enabled or self._cur is None:
            return
        cur, self._cur = self._cur, None
        cur.pop("t0", None)
        cur.update(fields)
        cur["ts"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        cur["machine"] = self.machine
        cur["version"] = __version__
        try:
            with self._lock, io.open(TRACE_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(cur, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            # Instrumentation that can break dictation is worse than no
            # instrumentation, so a failure here is dropped on the floor.
            pass


class _Span:
    def __init__(self, trace, name):
        self.trace, self.name = trace, name

    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *exc):
        if self.trace.enabled:
            self.trace._record(self.name, (time.perf_counter() - self.t) * 1000)
        return False


trace = _Trace()

# ── Settings ──────────────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "device":   "auto",
    "model":    "auto",
    "hotkey":   "key:insert",
    "mic":      "auto",
    "language": "es",
    "theme":    "dark",
}

# Languages you can dictate in. Stored as codes, shown as names.
# There is no "auto" on purpose: see _migrate().
LANGUAGE_NAMES = {
    "es": "Spanish",
    "en": "English",
    "pt": "Portuguese",
    "fr": "French",
    "de": "German",
    "it": "Italian",
}
LANGUAGE_CODES = list(LANGUAGE_NAMES)
NAME_TO_CODE   = {name: code for code, name in LANGUAGE_NAMES.items()}

def language_name(code):
    return LANGUAGE_NAMES.get(code, LANGUAGE_NAMES[DEFAULT_SETTINGS["language"]])

def language_code(name):
    return NAME_TO_CODE.get(name, DEFAULT_SETTINGS["language"])

app_settings = dict(DEFAULT_SETTINGS)  # module-wide view of current settings

def load_settings():
    global app_settings
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE) as f:
                s = json.load(f)
                app_settings = _migrate({**DEFAULT_SETTINGS, **s})
                return dict(app_settings)
        except Exception:
            pass
    app_settings = dict(DEFAULT_SETTINGS)
    return dict(app_settings)

def _migrate(s):
    """Bring a settings file written by an older build up to date."""
    # "auto" language used to mean "let Whisper guess". Guessing costs a detection
    # pass on every recording and gets it wrong on short clips, so the option is
    # gone and old files fall back to the default language.
    if s.get("language") not in LANGUAGE_CODES:
        s["language"] = DEFAULT_SETTINGS["language"]

    # The downloadable build has no CUDA libraries, so "gpu" can only ever fail
    # there. It happens to people who used the source version first and then
    # installed this one over the same settings file.
    if IS_PACKAGED and s.get("device") == "gpu":
        s["device"] = "auto"
    return s

def save_settings(s):
    global app_settings
    app_settings = dict(s)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, SETTINGS_FILE)

# ── Theme ─────────────────────────────────────────────────────────────────────
# ── Brand palette ─────────────────────────────────────────────────────────────
# Five colours carry the identity. Everything else is a surface underneath them.
#
#   #abdb25  lime     the mark, and only the mark
#   #ffffff  white    loudest text
#   #cccccc            body
#   #999999            secondary
#   #666666            quiet, and hairlines
#
# The lime is a surface, never ink on a pale background: it measures 11.77:1 on
# near-black and 1.63:1 on white. Filled with near-black text on top it is
# 11.77:1 either way, which is why the primary button looks the same in both
# themes -- the one piece of the interface a person should recognise instantly.
#
# The ramp is native to dark: white through #666666 lands at 19.2, 11.9, 6.7 and
# 3.3 against near-black, a clean four-step hierarchy. On white only #666666
# survives as text (5.74:1), so the light theme extends the same neutral ramp
# darker rather than inventing a second family.
BRAND_LIME       = "#abdb25"
BRAND_LIME_DARK  = "#5c7615"   # the same hue, taken down to 5.18:1 on white
BRAND_INK        = "#0f0f0f"

THEMES = {
    "dark": {
        "bg_root":             "#0f0f0f",
        "bg_header":           "#181818",
        "bg_button":           "#1f1f1f",
        "bg_button_hover":     "#2a2a2a",
        "option_hover":        "#333333",
        "bg_log":              "#0a0a0a",
        "border":              "#262626",
        "text_title":          "#ffffff",   # on bg_header  17.93:1
        "text_body":           "#cccccc",   # on bg_log     12.86:1
        "text_caption":        "#999999",   # on bg_root     6.73:1
        "text_hint":           "#666666",   # on bg_root     3.34:1
        "text_faint":          "#666666",
        "icon_btn":            "#cccccc",
        "mic_btn":             "#999999",
        # Widget labels: customtkinter's dark-blue theme hardcodes #DCE4EE for
        # both appearance modes, so every button/menu must be told explicitly.
        "text_on_button":      "#cccccc",
        "text_on_accent":      BRAND_INK,   # on the lime   11.77:1
        "text_on_capture":     "#cccccc",
        "hover_close":         "#3a1010",
        "hover_neutral":       "#262626",
        "hover_settings":      "#1f2a0c",
        "bar_idle":            "#333333",
        "scrollbar":           "#333333",
        "scrollbar_hover":     "#4d4d4d",
        "accent_bg":           BRAND_LIME,
        "accent_hover":        "#bce62f",
        "capture_bg":          "#26300f",
        "log_ok":              BRAND_LIME,  # the transcription itself, in the
        "log_error":           "#f34949",   # brand colour: it is the payoff
        "log_dim":             "#666666",
        "status_idle":         "#999999",
        "status_error":        "#de5353",
        "status_loading":      BRAND_LIME,
        "status_reload":       BRAND_LIME,
        "status_reload_text":  BRAND_LIME,
        "model_ready_text":    "#666666",
        "rec_status_text":     "#ef4141",
        "processing_text":     "#999999",
    },
    "light": {
        "bg_root":             "#ffffff",
        "bg_header":           "#f2f2f2",
        "bg_button":           "#f2f2f2",
        "bg_button_hover":     "#e6e6e6",
        "option_hover":        "#dcdcdc",
        "bg_log":              "#ffffff",
        "border":              "#cccccc",
        "text_title":          "#333333",   # on bg_header  11.62:1
        "text_body":           "#595959",   # on bg_log      7.00:1
        "text_caption":        "#6e6e6e",   # on bg_root     5.10:1
        "text_hint":           "#8a8a8a",   # on bg_root     3.54:1
        "text_faint":          "#999999",   # on bg_root     2.85:1, decorative
        "icon_btn":            "#595959",
        "mic_btn":             "#6e6e6e",
        "text_on_button":      "#444444",
        "text_on_accent":      BRAND_INK,   # on the lime   11.77:1
        "text_on_capture":     "#333333",
        "hover_close":         "#f2c9c9",
        "hover_neutral":       "#e6e6e6",
        "hover_settings":      "#eaf5d0",
        "bar_idle":            "#cccccc",
        "scrollbar":           "#cccccc",
        "scrollbar_hover":     "#999999",
        "accent_bg":           BRAND_LIME,
        "accent_hover":        "#9ac91f",
        "capture_bg":          "#eef7d6",
        "log_ok":              BRAND_LIME_DARK,
        "log_error":           "#c72c20",
        "log_dim":             "#8a8a8a",
        "status_idle":         "#6e6e6e",
        "status_error":        "#c52c1f",
        "status_loading":      BRAND_LIME_DARK,
        "status_reload":       BRAND_LIME_DARK,
        "status_reload_text":  BRAND_LIME_DARK,
        "model_ready_text":    "#8a8a8a",
        "rec_status_text":     "#c52c1f",
        "processing_text":     "#6e6e6e",
    },
}

def theme_color(key):
    """Resolve a semantic color for the currently saved theme. Used by module-level
    functions (no App instance available) so status messages match the active theme."""
    name = app_settings.get("theme", "dark")
    return THEMES.get(name, THEMES["dark"]).get(key, THEMES["dark"][key])

# ── Contrast math (WCAG 2.1) ──────────────────────────────────────────────────
def _srgb_to_linear(channel_byte):
    c = channel_byte / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

def relative_luminance(hex_color):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return (0.2126 * _srgb_to_linear(r)
            + 0.7152 * _srgb_to_linear(g)
            + 0.0722 * _srgb_to_linear(b))

def contrast_ratio(fg_hex, bg_hex):
    a, b = relative_luminance(fg_hex), relative_luminance(bg_hex)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)

def solve_gray(bg_hex, target_ratio):
    """Return the gray that hits target_ratio against bg_hex.

    Every text color in THEMES was generated with this instead of being chosen by
    eye — that is why the two themes keep the same visual hierarchy. Picking hex
    values by hand is what produced 1.52:1 captions in dark and a flat 15.5:1 for
    three different tiers in light.
    """
    bg = relative_luminance(bg_hex)
    lighter = target_ratio * (bg + 0.05) - 0.05
    darker  = (bg + 0.05) / target_ratio - 0.05
    options = [v for v in (lighter, darker) if 0.0 <= v <= 1.0]
    if not options:
        raise ValueError(f"{target_ratio}:1 unreachable against {bg_hex}")
    lum = max(options, key=lambda v: abs(v - bg))  # pick the readable direction
    c = 12.92 * lum if lum <= 0.0031308 else 1.055 * (lum ** (1 / 2.4)) - 0.055
    v = max(0, min(255, round(c * 255)))
    return "#%02x%02x%02x" % (v, v, v)

# ── Hotkey helpers ────────────────────────────────────────────────────────────
_MOUSE_DISPLAY = {
    "left": "Left Click", "right": "Right Click",
    "middle": "Middle Click", "x1": "Mouse 4", "x2": "Mouse 5",
}

def parse_hotkey_str(s):
    """Return (kind, value): kind in 'mouse'|'key'|'char'."""
    try:
        if s and s.startswith("mouse:"):
            return ("mouse", getattr(Button, s[6:]))
        elif s and s.startswith("key:"):
            return ("key", getattr(Key, s[4:]))
        elif s and s.startswith("char:"):
            return ("char", KeyCode.from_char(s[5:]))
    except AttributeError:
        pass
    return ("key", Key.insert)

def hotkey_display_name(s):
    if not s:
        return "Insert"
    if s.startswith("mouse:"):
        name = s[6:]
        return _MOUSE_DISPLAY.get(name, name.replace("_", " ").title())
    elif s.startswith("key:"):
        name = s[4:]
        return name.replace("_", " ").title()
    elif s.startswith("char:"):
        return s[5:].upper()
    return s

# ── Microphone helpers ────────────────────────────────────────────────────────
def list_input_devices():
    """Unique input device names, in PortAudio order."""
    names, seen = [], set()
    try:
        for d in sd.query_devices():
            if d["max_input_channels"] > 0 and d["name"] not in seen:
                seen.add(d["name"])
                names.append(d["name"])
    except Exception:
        pass
    return names

def resolve_input_device(pref):
    """'auto' → None (system default). Else device index by exact, then substring match."""
    if not pref or pref == "auto":
        return None
    try:
        devs = sd.query_devices()
        for idx, d in enumerate(devs):
            if d["max_input_channels"] > 0 and d["name"] == pref:
                return idx
        for idx, d in enumerate(devs):
            if d["max_input_channels"] > 0 and pref.lower() in d["name"].lower():
                return idx
    except Exception:
        pass
    return None

def current_mic_name():
    """Name of the device that would be used right now."""
    try:
        idx = resolve_input_device(app_settings.get("mic", "auto"))
        if idx is None:
            return sd.query_devices(kind="input")["name"]
        return sd.query_devices(idx)["name"]
    except Exception:
        return "unknown"

# ── Hardware auto-detection ───────────────────────────────────────────────────
_hardware = None      # cached: the answer cannot change while we run

def detect_hardware():
    """Returns (device, compute_type, model_size).

    Worked out once. resolve_config() runs on every settings change and this
    spawns nvidia-smi, which costs a few hundred milliseconds.
    """
    global _hardware
    if _hardware is not None:
        return _hardware

    _hardware = ("cpu", "int8", "small")
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0 and cuda_libs_present():
            vram = _get_vram_mb()
            if vram >= 6000:
                _hardware = ("cuda", "float16", "large-v3-turbo")
            elif vram >= 3000:
                _hardware = ("cuda", "float16", "small")
            else:
                _hardware = ("cuda", "int8", "small")
    except Exception:
        pass
    return _hardware

def _get_vram_mb():
    """Free video memory, in MB.

    Free rather than total: a card with 6 GB installed but 3 GB taken by the
    browser cannot hold large-v3-turbo, and asking for total would promise it.
    """
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        # splitlines(), not split("\n"): on Windows the first field of a
        # multi-GPU answer keeps its \r and int() would refuse it.
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return 0

def resolve_config(settings):
    """Turn settings dict into (device, compute_type, model_size)."""
    device_pref = settings.get("device", "auto")
    model_pref  = settings.get("model",  "auto")

    auto_device, auto_compute, auto_model = detect_hardware()

    if device_pref == "auto":
        device  = auto_device
        compute = auto_compute
    elif device_pref == "gpu":
        device  = "cuda"
        compute = "float16"
    else:  # cpu
        device  = "cpu"
        compute = "int8"

    if model_pref != "auto":
        model = model_pref
    elif device == auto_device:
        model = auto_model
    else:
        # The device was overridden, so the automatic pick no longer applies:
        # it was chosen for the hardware we found, not for what was asked for.
        # large-v3-turbo on a processor takes minutes per sentence.
        model = "small"

    return device, compute, model

# ── Tray icons ────────────────────────────────────────────────────────────────
MARK_STATES = {
    "idle":      (171, 219, 37),    # the brand lime
    "recording": (231, 76, 60),
    "working":   (255, 196, 60),
}

def _make_tray_icon(rec=False, size=64, state=None, compact=None):
    """Kara's mark: a status ring around a capsule, on a dark disc.

    The ring is the whole idea. It is the one thing the two references share --
    the LED at Kara's temple and the ring of light on a 360 -- and it is not
    decoration here, because the app really does move through states. Colour is
    the message: lime waiting, red recording, amber working.

    Every coordinate is a fraction of the side, so this one drawing serves the
    16px tray icon and the 1024px artwork alike (see tools/make_logo.py). Drawn
    at 4x and scaled down: Pillow's shapes are aliased and the ring would come
    out ragged otherwise.

    Below 40px it inverts -- a solid disc of colour with the capsule knocked out
    of it. At that size a ring is a one-pixel hair that eats its own middle, and
    what survives is a blob. A filled shape survives, and in a tray full of grey
    it is the colour that makes it findable at all.

    `compact` forces that second drawing at any size, which is what the header
    mark wants: it is displayed at 20px but has to be rendered large enough for
    a 150% display, and picking the form by render size would have handed it the
    ring at a scale the ring does not survive.
    """
    colour = MARK_STATES[state or ("recording" if rec else "idle")]
    if compact is None:
        compact = size < 40
    ss  = 4
    s   = size * ss
    end = s - 1
    img  = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def px(*fractions):
        return [round(f * end) for f in fractions]

    DISC    = (23, 23, 23)
    CAPSULE = (232, 232, 232)

    if not compact:
        draw.ellipse(px(0, 0, 1, 1), fill=DISC)
        # Left open at the top, like an indicator part way round its travel.
        # A closed ring reads as a frame; a broken one reads as a state.
        draw.arc(px(0.10, 0.10, 0.90, 0.90), start=-64, end=244,
                 fill=colour, width=max(1, round(0.085 * s)))
        draw.rounded_rectangle(px(0.435, 0.33, 0.565, 0.67),
                               radius=0.065 * end, fill=CAPSULE)
    else:
        draw.ellipse(px(0, 0, 1, 1), fill=colour)
        draw.rounded_rectangle(px(0.40, 0.26, 0.60, 0.74),
                               radius=0.10 * end, fill=DISC)

    return img.resize((size, size), Image.LANCZOS)

TRAY_IDLE = _make_tray_icon(False)
TRAY_REC  = _make_tray_icon(True)

# The same mark, in the window. It replaces a "●" glyph that was the only piece
# of chrome not carrying the brand, and it is the app's own drawing rather than
# a file: nothing here opens an image at runtime, and there is no _MEIPASS
# helper, so a bundled PNG would resolve against the wrong directory once
# PyInstaller has had it. Rendered at 96 and shown at 20 so a 150% display has
# real pixels to scale into.
MARK_PX  = 20
_MARK_HI = 96
_mark_cache = {}

def mark_image(state, px=MARK_PX):
    """A CTkImage of the mark in one of MARK_STATES, built once per state.

    Built on demand rather than beside TRAY_IDLE: CTkImage registers itself with
    customtkinter's scaling tracker, and at import time there is no root window
    for it to register against yet.
    """
    key = (state, px)
    if key not in _mark_cache:
        art = _make_tray_icon(size=_MARK_HI, state=state, compact=True)
        _mark_cache[key] = ctk.CTkImage(light_image=art, dark_image=art, size=(px, px))
    return _mark_cache[key]

# ── Global state ──────────────────────────────────────────────────────────────
recording          = False
audio_frames       = []
model_obj          = None
model_lock         = threading.Lock()
# "loading" | "ready" | "error". Kept apart from model_obj on purpose: while a
# new model loads after a settings change the old object is still there, so
# "model_obj is not None" would answer yes to "can we record now?".
model_state        = "loading"
model_error_msg    = ""
tray_icon          = None
app_gui            = None
status_lock        = threading.Lock()
stream_lock        = threading.Lock()
ui_queue           = queue.Queue()
SAMPLE_RATE        = 16000

stream             = None      # open only while recording
stream_samplerate  = SAMPLE_RATE
input_level        = 0.0       # 0..1, written by the audio thread for the meter

_current_hotkey_str = "key:insert"   # updated from settings at startup / apply
_trigger_down       = False           # tracks press state regardless of kind
_capturing_hotkey   = False           # True while waiting for capture input
_capture_queue      = queue.Queue()   # receives "kind:name" string from listeners
_last_not_ready_log = 0.0             # rate limit for the "not ready yet" notice

# ── Model loader ──────────────────────────────────────────────────────────────
def model_is_ready():
    with model_lock:
        return model_state == "ready"


def _hf_cache_dir():
    return os.environ.get("HF_HUB_CACHE") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface", "hub")


# A file the loader cannot open is two different problems wearing the same
# error, and they want opposite treatment. Missing or truncated bytes are worth
# downloading again. A file some other program has open is not: the bytes are
# fine, the lock clears on its own, and deleting half a gigabyte to work around
# a virus scanner would be the worst possible reaction. Hence three verdicts
# rather than a boolean.
_LOCK_TRIES = 5
_LOCK_WAIT  = 3.0        # seconds


def _file_verdict(path):
    """"ok", "locked" or "broken" for one file ctranslate2 is going to open."""
    try:
        with open(path, "rb") as f:
            # A zero-byte file opens fine and is still useless.
            return "ok" if f.read(1) else "broken"
    except PermissionError:
        return "locked"
    except OSError as e:
        # 32 ERROR_SHARING_VIOLATION, 33 ERROR_LOCK_VIOLATION. Windows raises
        # these as plain OSError, not as PermissionError.
        if getattr(e, "winerror", None) in (32, 33):
            return "locked"
        return "broken"


def _snapshot_verdict(path):
    """Worst verdict across the files ctranslate2 needs, with locked winning."""
    try:
        entries = os.listdir(path)
    except OSError:
        return "broken"

    # vocabulary is .txt on some models and .json on others, hence the prefix.
    vocabulary = [e for e in entries if e.startswith("vocabulary.")]
    if not vocabulary:
        return "broken"

    verdict = "ok"
    for name in ["model.bin", "config.json", "tokenizer.json"] + vocabulary:
        v = _file_verdict(os.path.join(path, name))
        if v == "locked":
            return "locked"   # never delete a file something else is holding
        if v == "broken":
            verdict = "broken"
    return verdict


def ensure_model_files(model_size):
    """Return a model directory ctranslate2 can open, repairing the cache once.

    Reuses faster_whisper's own downloader so the repo id, the file list and the
    cache location stay whatever that library decided -- there is no second copy
    of that mapping to keep in step here.
    """
    # A path the user pointed at is theirs; download and repair are not ours to do.
    if os.path.isdir(model_size):
        return model_size

    path = download_model(model_size)

    # Wait a lock out before saying anything about it. An antivirus scanning a
    # model that was downloaded seconds ago holds it for seconds, not minutes,
    # and the whole failure looks like a broken install if we give up first.
    verdict = "locked"
    for attempt in range(_LOCK_TRIES):
        verdict = _snapshot_verdict(path)
        if verdict != "locked":
            break
        if attempt == 0:
            ui_queue.put(("log", "Something else has the model files open. "
                                 "Waiting for it.", "dim"))
        time.sleep(_LOCK_WAIT)

    if verdict == "ok":
        return path
    if verdict == "locked":
        raise RuntimeError(_locked_model_msg())

    # Names present, bytes not. snapshots/<revision>/ up to models--<org>--<name>/:
    # deleting only the snapshot can restore it from the same bad cache entry,
    # so the whole repo folder goes and the download starts over.
    repo_dir = os.path.dirname(os.path.dirname(path))
    if os.path.basename(repo_dir).startswith("models--"):
        ui_queue.put(("log", "The downloaded model files are incomplete. "
                             "Getting them again.", "dim"))
        shutil.rmtree(repo_dir, ignore_errors=True)
        path = download_model(model_size)
        if _snapshot_verdict(path) == "ok":
            return path

    raise RuntimeError(_unreadable_model_msg(repo_dir))


def _cannot_open_msg():
    return ("Kara could not open the model files. Another program may have them "
            "open -- antivirus software does this for a while after a download. "
            "Wait a minute and open Kara again, or restart the computer if it "
            "keeps happening.")


def _locked_model_msg():
    return ("Another program has the model files open, so Kara can't read them. "
            "Antivirus software does this for a while after a download. Wait a "
            "minute and open Kara again, or restart the computer if it keeps "
            "happening.")


def _unreadable_model_msg(where):
    return ("The model files can't be read, even after downloading them again. "
            f"Close Kara, delete this folder, and open it again:  {where}")


def load_model(device, compute, model_size, on_done=None, on_error=None):
    global model_obj, model_state, model_error_msg
    with model_lock:
        model_state = "loading"
        model_error_msg = ""
    try:
        if device == "cuda" and not cuda_libs_present():
            # Say what to do instead of letting ctranslate2 fail with a DLL name
            # nobody outside this project would recognise. The advice differs:
            # telling someone running the downloaded build to use pip would send
            # them looking for something that is not there.
            if IS_PACKAGED:
                raise RuntimeError(
                    "This download only uses your processor. To use your "
                    f"graphics card, install from the source code: {SOURCE_URL}")
            raise RuntimeError(
                "Your graphics card is missing some files. Open a terminal and "
                "run:  pip install nvidia-cublas-cu12  ...or set Device back "
                "to auto in settings.")
        # Downloading outside the lock: it can take minutes on a first run, and
        # holding the lock there would freeze a transcription already in flight.
        model_path = ensure_model_files(model_size)
        with model_lock:
            model_obj = WhisperModel(model_path, device=device, compute_type=compute)
            model_state = "ready"
        if on_done:
            on_done(device, compute, model_size)
    except Exception as e:
        text = str(e)
        # ctranslate2 reports this one as a C++ file error naming a cache path
        # nobody chose to have. The check above already read those files, so
        # reaching here means something took them in between -- which is also
        # what the report behind issue #1 turned out to be, since a reboot
        # fixed it without touching the cache.
        if "Unable to open file" in text:
            text = _cannot_open_msg()
        with model_lock:
            model_state = "error"
            model_error_msg = text
        if on_error:
            on_error(text)

# ── Audio ─────────────────────────────────────────────────────────────────────
def audio_callback(indata, frames, time_info, status):
    global input_level
    if recording:
        audio_frames.append(indata.copy())
        # Loudness of this block, for the meter. A plain float assignment, read
        # by the UI thread without a lock: one number, last writer wins, and a
        # dropped frame at 30 fps is invisible. Cheap enough to run in the audio
        # callback, which must not block.
        rms = float(np.sqrt(np.mean(np.square(indata), dtype=np.float64)))
        db  = 20.0 * math.log10(max(rms, 1e-6))
        # -55 dB is a quiet room, -10 dB is talking close to the mic. Working in
        # decibels rather than raw amplitude is what makes normal speech fill
        # the meter instead of hugging the floor.
        input_level = min(1.0, max(0.0, (db + 55.0) / 45.0))

def _idle_status():
    return "READY"

def _key_label():
    """Just the key, for the keycap under the log."""
    return hotkey_display_name(_current_hotkey_str).upper()

def _mic_facts(device):
    """Which device and which Windows audio backend, for the trace.

    The backend is the point. MME, DirectSound, WASAPI and WDM-KS have wildly
    different costs to open, and a Bluetooth headset on one of them can take
    over a second to switch into its recording profile. Without this the trace
    would show a slow open and no way to tell what was slow about it.
    """
    if not trace.enabled:
        return {}
    try:
        info = sd.query_devices(device, kind="input") if device is not None \
               else sd.query_devices(kind="input")
        api = sd.query_hostapis(info["hostapi"])["name"]
        return {"mic": info["name"], "hostapi": api}
    except Exception:
        return {}


def _open_stream():
    """Open input stream on the currently selected mic. Called at each record start,
    so device hotplug (headsets, controllers) is picked up automatically."""
    global stream, stream_samplerate
    with trace.span("resolve_device"):
        device = resolve_input_device(app_settings.get("mic", "auto"))
    last_err = None
    for sr in (SAMPLE_RATE, None):
        try:
            if sr is None:  # fall back to the device's native rate
                with trace.span("query_native_rate"):
                    info = sd.query_devices(device, kind="input") if device is not None \
                           else sd.query_devices(kind="input")
                sr = int(info["default_samplerate"])
                trace.set(fell_back_to_native_rate=True)
            # Split from .start() on purpose: opening the device and getting it
            # running are different costs, and on a Bluetooth headset it is the
            # second one that takes a second and a half.
            with trace.span("stream_ctor"):
                s = sd.InputStream(samplerate=sr, channels=1, device=device,
                                   callback=audio_callback, dtype="float32")
            with trace.span("stream_start"):
                s.start()
            stream = s
            stream_samplerate = sr
            trace.set(sr=sr, **_mic_facts(device))
            return True
        except Exception as e:
            last_err = e
    ui_queue.put(("log", f"Could not open the microphone: {last_err}", "error"))
    return False

def _close_stream():
    global stream
    if stream is not None:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass
        stream = None

def start_recording():
    global audio_frames, recording
    audio_frames = []
    with trace.span("lock_wait"):
        stream_lock.acquire()
    try:
        ok = _open_stream()
    finally:
        stream_lock.release()
    if not ok:
        with status_lock:
            recording = False
        ui_queue.put(("status", "Can't use the microphone. Check settings.",
                      theme_color("status_error")))
        trace.finish(err="mic_open_failed")
        return
    if tray_icon:
        tray_icon.icon  = TRAY_REC
        tray_icon.title = "Kara — recording"
    ui_queue.put(("recording", True))
    # Everything above is what the user waits through before the window says
    # LISTENING, which is the number the whole trace exists to explain.
    trace.mark("press_to_listening")

def stop_and_transcribe():
    with stream_lock:
        _close_stream()
    if tray_icon:
        tray_icon.icon  = TRAY_IDLE
        tray_icon.title = "Kara — ready"
    ui_queue.put(("recording", False))
    if not audio_frames:
        ui_queue.put(("status", _idle_status(), theme_color("status_idle")))
        trace.finish(err="no_audio")
        return
    data = np.concatenate(audio_frames, axis=0).flatten().astype(np.float32)
    sr = stream_samplerate
    dur = len(data) / sr
    ui_queue.put(("log", f"Recorded {dur:.1f}s", "dim"))
    # How much speech actually landed, against how long the key was down: the
    # gap between the two is audio lost to the microphone still waking up.
    trace.set(audio_s=round(dur, 2))
    threading.Thread(target=_transcribe, args=(data, sr), daemon=True).start()

def _transcribe(audio_data, sr):
    if model_obj is None:
        ui_queue.put(("log", "Still getting ready. Try again in a moment.", "error"))
        return
    try:
        dur  = len(audio_data) / sr
        peak = float(np.max(np.abs(audio_data))) if len(audio_data) else 0.0
        # Silence guard: normalizing near-silence to full scale makes Whisper
        # hallucinate text, so bail out early instead.
        if dur < 0.2 or peak < 0.005:
            ui_queue.put(("log", "Too short or too quiet — nothing to type.", "dim"))
            ui_queue.put(("status", _idle_status(), theme_color("status_idle")))
            trace.finish(err="too_short_or_quiet", peak=round(peak, 4))
            return

        if sr != SAMPLE_RATE:
            n = int(len(audio_data) * SAMPLE_RATE / sr)
            audio_data = np.interp(
                np.linspace(0, len(audio_data), n, endpoint=False),
                np.arange(len(audio_data)), audio_data
            ).astype(np.float32)

        gain = min(0.95 / peak, 30.0)  # cap so background noise isn't blown up
        audio_data = audio_data * gain

        # Always an explicit language: skipping detection saves a pass over the
        # audio and avoids wrong guesses on short clips.
        language = app_settings.get("language", DEFAULT_SETTINGS["language"])
        if language not in LANGUAGE_CODES:
            language = DEFAULT_SETTINGS["language"]

        with trace.span("model_lock_wait"):
            model_lock.acquire()
        try:
            with trace.span("transcribe"):
                segments, info = model_obj.transcribe(
                    audio_data, language=language, beam_size=5, vad_filter=False
                )
                text = " ".join(seg.text.strip() for seg in segments).strip()
        finally:
            model_lock.release()

        # The length of what was said, never what was said. See _Trace.
        trace.set(chars=len(text), peak=round(peak, 4), lang=language)

        if text:
            ui_queue.put(("log", text, "said"))
            ui_queue.put(("status", _idle_status(), theme_color("status_idle")))
            with trace.span("paste"):
                prev = pyperclip.paste()
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.3)  # let the target app read the clipboard before restore
                pyperclip.copy(prev)
            trace.finish()
        else:
            ui_queue.put(("log", "I didn't hear any words.", "dim"))
            ui_queue.put(("status", _idle_status(), theme_color("status_idle")))
            trace.finish(err="no_words")
    except Exception as e:
        ui_queue.put(("log", f"Something went wrong: {e}", "error"))
        ui_queue.put(("status", _idle_status(), theme_color("status_idle")))
        # The class name, not str(e): a message can carry a file path, and a
        # path can carry the user's name.
        trace.finish(err=type(e).__name__)

# ── Trigger guard ─────────────────────────────────────────────────────────────
def _may_record():
    """False, and one line in the log, while there is no model to transcribe with.

    The check used to live in _transcribe, which meant the key worked, the meter
    moved, and the refusal only arrived after you had already said your sentence.
    """
    global _last_not_ready_log
    with model_lock:
        state, err = model_state, model_error_msg
    if state == "ready":
        return True

    # Held keys repeat and mice get clicked twice; one notice is the message.
    now = time.monotonic()
    if now - _last_not_ready_log > 3.0:
        _last_not_ready_log = now
        if state == "error":
            ui_queue.put(("log", err or "Kara could not get ready.", "error"))
        else:
            ui_queue.put(("log", "Still getting ready — hold on a moment.", "dim"))
    return False

# ── Mouse listener ────────────────────────────────────────────────────────────
def on_mouse_click(x, y, button, pressed):
    global recording, _trigger_down, _capturing_hotkey

    # Capture mode: record and exit
    if _capturing_hotkey and pressed:
        _capture_queue.put(f"mouse:{button.name}")
        return

    kind, value = parse_hotkey_str(_current_hotkey_str)
    if kind != "mouse" or button != value:
        return

    if pressed and not _trigger_down:
        if not _may_record():
            return
        _trigger_down = True
        with status_lock:
            recording = True
        start_recording()
    elif not pressed and _trigger_down:
        _trigger_down = False
        with status_lock:
            recording = False
        stop_and_transcribe()

# ── Keyboard listener ─────────────────────────────────────────────────────────
def on_key_press(key):
    global recording, _trigger_down, _capturing_hotkey

    # Capture mode: record and exit
    if _capturing_hotkey:
        if isinstance(key, Key):
            _capture_queue.put(f"key:{key.name}")
        elif isinstance(key, KeyCode) and key.char:
            _capture_queue.put(f"char:{key.char}")
        return

    kind, value = parse_hotkey_str(_current_hotkey_str)
    if kind not in ("key", "char"):
        return
    if key != value:
        return
    if not _trigger_down:
        if not _may_record():
            return
        _trigger_down = True
        trace.start()
        with status_lock:
            recording = True
        start_recording()

def on_key_release(key):
    global recording, _trigger_down

    kind, value = parse_hotkey_str(_current_hotkey_str)
    if kind not in ("key", "char"):
        return
    if key != value:
        return
    if _trigger_down:
        _trigger_down = False
        trace.mark("hold")      # how long the key was actually held down
        with status_lock:
            recording = False
        stop_and_transcribe()

# ── Shutdown ──────────────────────────────────────────────────────────────────
def shutdown():
    with stream_lock:
        _close_stream()
    os._exit(0)

# ── Tray ──────────────────────────────────────────────────────────────────────
def _show_window(icon=None, item=None):
    if app_gui:
        app_gui.after(0, app_gui.deiconify)

def _quit_tray(icon, item):
    icon.stop()
    shutdown()

def run_tray():
    global tray_icon
    try:
        menu = pystray.Menu(
            pystray.MenuItem("Kara — speak to type", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open", _show_window, default=True),
            pystray.MenuItem("Close the app", _quit_tray),
        )
        tray_icon = pystray.Icon("Kara", TRAY_IDLE, "Kara", menu)
        tray_icon.run()
    except Exception as e:
        ui_queue.put(("log", f"Could not add the icon near the clock: {e}", "error"))
        ui_queue.put(("status", "No tray icon — use the X button to close.",
                      theme_color("status_error")))

# ── GUI ───────────────────────────────────────────────────────────────────────
MODEL_OPTIONS = ["auto", "tiny", "small", "medium", "large-v3-turbo"]

MODEL_DESCRIPTIONS = {
    "auto":            "Picks the best model your computer can run.",
    "tiny":            "The fastest, but it makes more mistakes.",
    "small":           "A good middle choice. Best if you have no graphics card.",
    "medium":          "More accurate, but slow without a graphics card.",
    "large-v3-turbo":  "The most accurate. You need a graphics card for this one.",
}

class KaraApp(ctk.CTk):
    W, H = 320, 480

    # Horizontal budget for the settings panel, derived rather than guessed:
    #   320 root
    #   -16 CTkScrollbar (vertical, grid column 1)
    #   - 6 canvas padx=(border_spacing, 0), border_spacing = corner_radius 6 + border 0
    #   -32 _inner CTkFrame padx=16 on both sides
    #   = 266 usable
    # The old hardcoded 280 overflowed by 14px, which Tk clipped 7px per side.
    SCROLLBAR_W    = 16
    BORDER_SPACING = 6
    INNER_PAD      = 16
    CONTENT_W = W - SCROLLBAR_W - BORDER_SPACING - 2 * INNER_PAD   # 266
    WRAP_W    = CONTENT_W - 8                                      # 258, keeps text inside

    # Level meter: it shares the stage's foot with the keycap and the phase
    # word now, so it gets the gap between them rather than the full width.
    METER_W_PX = 122                    # canvas width, px
    METER_N = 20                        # bars
    METER_W = 4                         # bar thickness, px
    METER_H = 26                        # canvas height, px

    def __init__(self):
        super().__init__()
        self._settings = load_settings()
        self.theme = THEMES[self._settings.get("theme", "dark")]
        ctk.set_appearance_mode(self._settings.get("theme", "dark"))
        ctk.set_default_color_theme("dark-blue")

        self.title("Kara")
        self.geometry(self._bottom_right())
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.attributes("-toolwindow", True)
        self.overrideredirect(True)
        self.configure(fg_color=self.theme["bg_root"])

        self._drag_x = self._drag_y = 0
        self._view = "main"   # "main" or "settings"

        # Preserved across theme rebuilds
        self._log_lines = []          # [(text, tag), ...]
        self._last_model_label = ""   # e.g. "small · cpu · int8"
        self._model_is_ready = False

        self._build_main()
        self._build_settings()
        self._show_main()
        # After the widgets exist: the window has to be mapped before Windows
        # will give it a shape.
        self.after(60, self._round_corners)

        self.after(80, self._process_ui_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Layout ────────────────────────────────────────────────────────────────
    def _bottom_right(self):
        # customtkinter scales the width and height of a geometry string by the
        # monitor's DPI factor but leaves x/y untouched, so the offsets have to be
        # computed in scaled pixels here. Using unscaled W/H put the right edge at
        # sw - 24 + W*(s-1): 56px off-screen at 125%, 136px at 150%.
        s  = self._get_window_scaling()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = sw - round(self.W * s) - round(24 * s)
        y = sh - round(self.H * s) - round(56 * s)
        return f"{self.W}x{self.H}+{max(0, x)}+{max(0, y)}"

    # ── How the history reads ─────────────────────────────────────────────────
    # One textbox holds two very different kinds of line: sentences you dictated,
    # and the app talking about itself ("Recorded 3.1s", "Ready to use."). They
    # used to look identical -- same 11pt monospace, one after another -- so
    # finding what you actually said meant reading past the bookkeeping.
    #
    # Size sorts them out, barely. Your words are 11pt and white; the app's own
    # lines are 9pt and grey. Two points and a shade is enough to skim past the
    # bookkeeping, and it was worth less than that: at 14pt one ordinary
    # sentence wrapped onto three lines in a 320px window and the history
    # stopped reading as a list at all.
    LOG_FONTS = {
        "said":  ("Segoe UI", 11),
        "ok":    ("Segoe UI", 9),
        "dim":   ("Segoe UI", 9),
        "error": ("Segoe UI", 9),
    }
    # (above, below) in px. A sentence gets a little air; the small lines sit
    # tight against whatever they are annotating.
    LOG_SPACING = {
        "said":  (6, 4),
        "ok":    (1, 1),
        "dim":   (1, 1),
        "error": (1, 1),
    }

    def _log_font(self, tag):
        family, size = self.LOG_FONTS[tag]
        return ctk.CTkFont(family=family, size=size)

    def _style_log_tags(self):
        """Give each tag its own size, going around CTkTextbox to do it.

        CTkTextbox.tag_config refuses a font outright -- "'font' option
        forbidden, because would be incompatible with scaling" -- because it
        scales its own font and a tag would escape that. The Tk widget
        underneath has no such objection, so the tags are set on it directly and
        the scaling CustomTkinter would have applied is applied here instead.

        Worth knowing: this failed silently for a while behind a bare except,
        which swallowed the colours along with the fonts and made every line in
        the history look identical. Hence no except here -- if the private
        attribute ever moves, it should be loud.
        """
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        t = self.theme
        # Everything the app says about itself is grey, including "Ready to
        # use." -- it used to be lime, which made a routine startup line the
        # brightest thing on screen. The exception is a failure: a line telling
        # you something broke has to look different from one telling you a
        # recording was 1.8 seconds long.
        colours = {"ok": t["log_dim"], "error": t["log_error"],
                   "dim": t["log_dim"], "said": t["text_title"]}
        for name, (family, size) in self.LOG_FONTS.items():
            above, below = self.LOG_SPACING[name]
            self._log._textbox.tag_config(
                name,
                foreground=colours[name],
                font=(family, max(1, round(size * scale))),
                spacing1=round(above * scale),
                spacing3=round(below * scale),
            )

    def _build_main(self):
        t = self.theme
        self._main_frame = ctk.CTkFrame(self, fg_color="transparent")

        # ── Header
        hdr = ctk.CTkFrame(self._main_frame, fg_color=t["bg_header"], corner_radius=0, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        hdr.bind("<ButtonPress-1>", self._drag_start)
        hdr.bind("<B1-Motion>",     self._drag_move)

        # The mark, not a bullet. Same drawing as the tray icon and the installer
        # artwork, so the window agrees with every other place the app appears.
        self._dot = ctk.CTkLabel(hdr, text="", image=mark_image("idle"), width=26)
        self._dot.pack(side="left", padx=(12, 6))

        ctk.CTkLabel(hdr, text="Kara", font=("Segoe UI Semibold", 15),
                     text_color=t["text_title"]).pack(side="left")

        # header buttons right-to-left
        for symbol, cmd, hover in [
            ("✕", self._on_close,      t["hover_close"]),
            ("─", self._minimize,      t["hover_neutral"]),
            ("⚙", self._show_settings, t["hover_settings"]),
        ]:
            ctk.CTkButton(hdr, text=symbol, width=32, height=28, corner_radius=8,
                          fg_color="transparent", hover_color=hover,
                          text_color=t["icon_btn"], font=("Segoe UI", 13),
                          command=cmd).pack(side="right", padx=(0, 4))

        # ── The live view
        # No card, no border, no second panel. There used to be a bordered stage
        # here with the history in another bordered box below it, and at a glance
        # that read as two windows stuck together rather than one app. The window
        # itself is the card now (see _round_corners); everything inside sits flat
        # on it.
        #
        # The mark, and what is loaded: the two things worth saying while nothing
        # is happening.
        bar = ctk.CTkFrame(self._main_frame, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(14, 0))
        self._stage_mark = ctk.CTkLabel(bar, text="", image=mark_image("idle", 13),
                                        width=15)
        self._stage_mark.pack(side="left", padx=(0, 7))
        self._model_info = ctk.CTkLabel(bar, text=self._last_model_label,
                                        font=("Segoe UI", 9),
                                        text_color=t["model_ready_text"])
        self._model_info.pack(side="left")

        # ── What you said, all of it, right here
        # The last sentence used to appear alone, typed out one letter at a time,
        # with everything before it hidden behind a button. Reading back is the
        # common case and the animation was in the way of it, so the history is
        # the view now: newest at the bottom, nothing to click through.
        #
        # No border and the window's own background, so it is not a box inside a
        # box. What separates a sentence from the noise around it is type size,
        # not a frame: see _log_tag_fonts.
        self._log = ctk.CTkTextbox(
            self._main_frame, font=self._log_font("dim"),
            fg_color=t["bg_root"], text_color=t["text_body"],
            border_width=0, corner_radius=0, wrap="word", state="disabled",
            scrollbar_button_color=t["scrollbar"],
            scrollbar_button_hover_color=t["scrollbar_hover"])
        self._log.pack(fill="both", expand=True, padx=(16, 6), pady=(14, 0))
        self._style_log_tags()
        self._restore_log()

        # Foot: key on the left, meter in the middle, phase on the right.
        foot = ctk.CTkFrame(self._main_frame, fg_color="transparent")
        foot.pack(fill="x", side="bottom", padx=20, pady=(8, 16))

        self._hold_label = ctk.CTkLabel(
            foot, text=_key_label(), font=("Consolas", 9, "bold"),
            text_color=t["text_body"], fg_color=t["bg_button"],
            corner_radius=8, width=0, height=20, padx=8,
        )
        self._hold_label.pack(side="left")

        # Rebuilt on every theme switch, so the text has to come from state rather
        # than be hardcoded — otherwise switching theme after load pins the label
        # at "Loading model..." forever.
        if self._model_is_ready:
            status_text, status_color = _idle_status(), t["status_idle"]
        else:
            status_text, status_color = "GETTING READY", t["status_loading"]
        # A phase word in monospace, not a sentence. It changes several times a
        # minute beside a meter that is also moving, and a short fixed-width word
        # reads as a state changing rather than as text being replaced.
        self._status = ctk.CTkLabel(foot, text=status_text, font=("Consolas", 9),
                                    text_color=status_color, anchor="e", width=76)
        self._status.pack(side="right")

        # ── Level meter
        # A canvas of bars rather than a progress bar. While recording these are
        # the real loudness of the microphone, so it doubles as the answer to
        # "is it even hearing me?" -- the question a flat animated bar cannot
        # answer, because it moves exactly the same whether you speak or not.
        #
        # bg has to match whatever is behind it: this is a raw tk.Canvas, which
        # knows nothing about the CTk frame it sits inside, and a mismatch shows
        # up as a pale rectangle across the foot of the window.
        self._meter = tk.Canvas(foot, width=self.METER_W_PX,
                                height=self.METER_H, highlightthickness=0,
                                bd=0, bg=t["bg_root"])
        self._meter.pack(side="left", padx=(10, 6))
        self._meter_bars = []
        step = self.METER_W_PX / self.METER_N
        for i in range(self.METER_N):
            x = step * (i + 0.5)
            self._meter_bars.append(self._meter.create_line(
                x, 0, x, 0, width=self.METER_W, capstyle="round",
                fill=t["bar_idle"]))
        self._levels = deque([0.0] * self.METER_N, maxlen=self.METER_N)
        # Survives a theme switch: this runs again on rebuild, and resetting to
        # idle would freeze the loading meter half way through the model load.
        self._meter_state = getattr(self, "_meter_state", "idle")
        self._meter_phase = getattr(self, "_meter_phase", 0)
        self._draw_meter()

        # Rebuilt on every theme switch, so the greyed-out look has to be
        # re-applied from state rather than set once at startup.
        self._set_hold_enabled(self._model_is_ready)

    def _set_hold_enabled(self, enabled):
        """Grey out the keycap while the key does nothing.

        The key is refused until the model is loaded, and a keycap drawn as if
        it worked is the thing that made that refusal look like a bug.
        """
        t = self.theme
        self._hold_label.configure(
            text_color=t["text_body"] if enabled else t["text_hint"],
            fg_color=t["bg_button"] if enabled else "transparent",
        )

    def _build_settings(self):
        t = self.theme
        self._settings_frame = ctk.CTkFrame(self, fg_color="transparent")

        # ── Header
        hdr = ctk.CTkFrame(self._settings_frame, fg_color=t["bg_header"],
                           corner_radius=0, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        ctk.CTkButton(hdr, text="←", width=36, height=28, corner_radius=8,
                      fg_color="transparent", hover_color=t["hover_neutral"],
                      text_color=t["icon_btn"], font=("Segoe UI", 15),
                      command=self._show_main).pack(side="left", padx=8)

        ctk.CTkLabel(hdr, text="Settings", font=("Segoe UI Semibold", 14),
                     text_color=t["text_title"]).pack(side="left")

        ctk.CTkButton(hdr, text="✕", width=32, height=28, corner_radius=8,
                      fg_color="transparent", hover_color=t["hover_close"],
                      text_color=t["icon_btn"], font=("Segoe UI", 13),
                      command=self._on_close).pack(side="right", padx=8)

        # ── Footer, packed before the body so it always keeps its space.
        # Apply used to live at the bottom of the scroll area, 320px below the
        # fold — the primary action of the panel was the hardest thing to reach.
        footer = ctk.CTkFrame(self._settings_frame, fg_color=t["bg_header"],
                              corner_radius=0)
        footer.pack(side="bottom", fill="x")

        self._apply_status = ctk.CTkLabel(footer, text="", font=("Segoe UI", 9),
                                          text_color=t["status_idle"])
        self._apply_status.pack(pady=(6, 0))

        ctk.CTkButton(footer, text="Save changes", height=36, corner_radius=9,
                      fg_color=t["accent_bg"], hover_color=t["accent_hover"],
                      text_color=t["text_on_accent"],
                      font=("Segoe UI Semibold", 13),
                      command=self._apply_settings).pack(fill="x", padx=16, pady=(4, 12))

        body = ctk.CTkScrollableFrame(self._settings_frame, fg_color="transparent",
                                      scrollbar_button_color=t["scrollbar"],
                                      scrollbar_button_hover_color=t["scrollbar_hover"])
        body.pack(fill="both", expand=True, padx=0, pady=8)
        # inner padding via a child frame so content has left/right margin
        _inner = ctk.CTkFrame(body, fg_color="transparent")
        _inner.pack(fill="both", expand=True, padx=self.INNER_PAD)
        body = _inner

        def section(text):
            ctk.CTkLabel(body, text=text, font=("Segoe UI", 9),
                         text_color=t["text_caption"]).pack(anchor="w", pady=(0, 6))

        def hint(text, pady=(0, 14)):
            lbl = ctk.CTkLabel(body, text=text, font=("Segoe UI", 9),
                               text_color=t["text_hint"],
                               wraplength=self.WRAP_W, justify="left")
            lbl.pack(anchor="w", pady=pady)
            return lbl

        def option_menu(variable, values, font_size=12, command=None):
            menu = ctk.CTkOptionMenu(
                body, variable=variable, values=values,
                width=self.CONTENT_W, font=("Segoe UI", font_size), corner_radius=9,
                fg_color=t["bg_button"], button_color=t["bg_button_hover"],
                button_hover_color=t["option_hover"],
                # customtkinter's dark-blue theme hardcodes #DCE4EE here for both
                # appearance modes, which lands at 1.03:1 on the light background.
                text_color=t["text_on_button"],
                dropdown_fg_color=t["bg_header"], dropdown_text_color=t["text_title"],
                dropdown_hover_color=t["option_hover"],
                command=command,
            )
            menu.pack(fill="x", pady=(0, 6))
            return menu

        def segmented(values, variable, command=None):
            seg = ctk.CTkSegmentedButton(
                body, values=values, variable=variable,
                font=("Segoe UI", 12), corner_radius=9,
                fg_color=t["bg_button"], selected_color=t["accent_bg"],
                selected_hover_color=t["accent_hover"], unselected_color=t["bg_button"],
                text_color=t["text_on_button"],
                command=command,
            )
            # CTkSegmentedButton inherits CTkFrame, which place()s its canvas — so a
            # place-managed child never propagates size and the width= argument is
            # silently dropped. Its real width was the sum of its segments' natural
            # widths (~140px of the 266 asked for). fill="x" lets the parent's pack
            # impose the width instead, which does work.
            seg.pack(fill="x", pady=(0, 6))

            # One text_color for every segment is all customtkinter offers, and it
            # never touches it on selection: it swaps the background to the accent
            # and leaves the label alone. That put #cccccc on the lime, which is
            # 1.02:1 — the selected option was the one you could not read.
            #
            # So the labels get repainted by hand whenever the value changes.
            # _buttons_dict is private, hence the guard: a customtkinter upgrade
            # that renames it should cost the contrast fix, not the settings panel.
            def repaint(*_):
                try:
                    chosen = variable.get()
                    for value, btn in seg._buttons_dict.items():
                        btn.configure(text_color=t["text_on_accent"] if value == chosen
                                      else t["text_on_button"])
                except Exception:
                    pass

            variable.trace_add("write", repaint)
            repaint()
            return seg

        # ── Appearance
        section("APPEARANCE")
        self._theme_var = ctk.StringVar(value=self._settings.get("theme", "dark"))
        self._theme_seg = segmented(["dark", "light"], self._theme_var,
                                    command=self._on_theme_change)
        hint("Changes right away. You do not need to save.")

        # ── Hotkey
        section("HOTKEY")
        current_hk = self._settings.get("hotkey", "key:insert")
        self._hotkey_btn = ctk.CTkButton(
            body,
            text=f"[ {hotkey_display_name(current_hk)} ]",
            height=36, corner_radius=9,
            fg_color=t["bg_button"], hover_color=t["bg_button_hover"],
            text_color=t["text_on_button"],
            font=("Segoe UI Semibold", 13),
            command=self._capture_hotkey_start,
        )
        self._hotkey_btn.pack(fill="x", pady=(0, 4))
        self._hotkey_hint = hint("Click to change it. You can use a key or a mouse button.")

        # ── Microphone
        mic_row = ctk.CTkFrame(body, fg_color="transparent")
        mic_row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(mic_row, text="MICROPHONE", font=("Segoe UI", 9),
                     text_color=t["text_caption"]).pack(side="left")
        ctk.CTkButton(mic_row, text="↻", width=24, height=20, corner_radius=6,
                      fg_color="transparent", hover_color=t["hover_neutral"],
                      text_color=t["mic_btn"], font=("Segoe UI", 11),
                      command=self._refresh_mics).pack(side="right")

        self._mic_var = ctk.StringVar(value=self._settings.get("mic", "auto"))
        self._mic_menu = option_menu(self._mic_var, self._mic_values(), font_size=11)
        self._mic_hint = hint(
            "Leave this on auto to use your normal Windows microphone. The app "
            "checks again every time you record, so you can plug in or unplug a "
            "headset while it is open.")

        # ── Language
        section("LANGUAGE")
        self._lang_var = ctk.StringVar(
            value=language_name(self._settings.get("language", DEFAULT_SETTINGS["language"])))
        self._lang_menu = option_menu(
            self._lang_var, [LANGUAGE_NAMES[c] for c in LANGUAGE_CODES])
        hint("Pick the language you speak. The app is much faster when it does "
             "not have to work out the language on its own, and it makes fewer "
             "mistakes on short recordings.")

        # ── Device
        section("DEVICE")
        self._device_var = ctk.StringVar(value=self._settings.get("device", "auto"))
        # No "gpu" in the downloadable build: it cannot honour it, so offering
        # it would only be a button that breaks the app.
        self._device_seg = segmented(
            ["auto", "cpu"] if IS_PACKAGED else ["auto", "cpu", "gpu"],
            self._device_var)
        self._device_hint = hint(self._device_hint_text())
        self._device_var.trace_add("write", lambda *_: self._device_hint.configure(
            text=self._device_hint_text()))

        # ── Model
        section("MODEL")
        self._model_var = ctk.StringVar(value=self._settings.get("model", "auto"))
        self._model_menu = option_menu(
            self._model_var, MODEL_OPTIONS,
            command=lambda _: self._model_hint.configure(
                text=MODEL_DESCRIPTIONS.get(self._model_var.get(), "")))
        self._model_hint = hint(
            MODEL_DESCRIPTIONS.get(self._settings.get("model", "auto"), ""))

        # ── Hardware info
        self._hw_label = ctk.CTkLabel(body, text="", font=("Consolas", 9),
                                      text_color=t["text_faint"],
                                      wraplength=self.WRAP_W, justify="left")
        self._hw_label.pack(anchor="w", pady=(0, 10))
        threading.Thread(target=self._fill_hw_info, daemon=True).start()

    # ── Theme ─────────────────────────────────────────────────────────────────
    def _on_theme_change(self, name):
        if name == self._settings.get("theme", "dark"):
            return
        self._settings["theme"] = name
        save_settings(dict(self._settings))
        self.theme = THEMES[name]
        ctk.set_appearance_mode(name)
        self._rebuild_ui()

    def _rebuild_ui(self):
        keep_view = self._view
        # Before the widgets go: a queued type or caret callback would come back
        # to a destroyed label and raise inside Tk's main loop. _build_main puts
        # the text back afterwards, whole rather than typed again.
        self.configure(fg_color=self.theme["bg_root"])
        self._main_frame.destroy()
        self._settings_frame.destroy()
        self._build_main()
        self._build_settings()
        if keep_view == "settings":
            self._show_settings()
        else:
            self._show_main()

    # ── Mic helpers ───────────────────────────────────────────────────────────
    def _mic_values(self):
        return ["auto"] + list_input_devices()

    def _refresh_mics(self):
        values = self._mic_values()
        self._mic_menu.configure(values=values)
        if self._mic_var.get() not in values:
            self._mic_var.set("auto")

    # ── Hotkey capture ────────────────────────────────────────────────────────
    def _capture_hotkey_start(self):
        global _capturing_hotkey
        # Drain any stale entries
        while not _capture_queue.empty():
            try:
                _capture_queue.get_nowait()
            except queue.Empty:
                break
        _capturing_hotkey = True
        self._hotkey_btn.configure(
            text="Press a key or a mouse button",
            fg_color=self.theme["capture_bg"],
            text_color=self.theme["text_on_capture"],
        )
        self._hotkey_hint.configure(text="Waiting for you to press something...")
        self.after(100, self._capture_hotkey_poll)

    def _capture_hotkey_poll(self):
        global _capturing_hotkey, _current_hotkey_str, _trigger_down
        try:
            result = _capture_queue.get_nowait()
            _capturing_hotkey = False

            # Left click would turn every normal click into a recording
            if result == "mouse:left":
                self._hotkey_btn.configure(
                    text=f"[ {hotkey_display_name(_current_hotkey_str)} ]",
                    fg_color=self.theme["bg_button"],
                    text_color=self.theme["text_on_button"],
                )
                self._hotkey_hint.configure(
                    text="You can't use left click — it would record every time "
                         "you click. Try another button.")
                return

            # Apply immediately — no Apply button needed for hotkey
            _current_hotkey_str = result
            _trigger_down = False

            self._settings["hotkey"] = result
            save_settings({**app_settings, "hotkey": result})

            name = hotkey_display_name(result)
            self._hotkey_btn.configure(
                text=f"[ {name} ]",
                fg_color=self.theme["bg_button"],
                text_color=self.theme["text_on_button"],
            )
            self._hotkey_hint.configure(text="Saved. Click again to change it.")
            self._hold_label.configure(text=_key_label())
        except queue.Empty:
            if _capturing_hotkey:
                self.after(100, self._capture_hotkey_poll)

    # ── View switching ────────────────────────────────────────────────────────
    # ── Rounded corners ───────────────────────────────────────────────────────
    CORNER_R = 14        # matches the cards on the website

    def _hwnd(self):
        """The top-level window handle Windows knows this by."""
        self.update_idletasks()
        wid = self.winfo_id()
        return ctypes.windll.user32.GetParent(wid) or wid

    def _round_corners(self):
        """Round the window itself rather than the boxes inside it.

        overrideredirect(True) throws away the native frame, and with it the
        rounding Windows 11 would have applied for free -- which is why this app
        was a hard rectangle while its own website was all soft corners. Drawing
        rounded cards inside it did not fix that; it just put a second rectangle
        inside the first one.

        Two ways to get the shape, and which one runs depends on the version:

          DWM, on Windows 11. The compositor rounds it, so the edge is
          antialiased and it matches every other window on the desktop.

          SetWindowRgn, on Windows 10. Clips the window to a rounded rectangle.
          The corners are aliased because a region is a hard mask, but a slightly
          jagged curve reads better than no curve, and 10 has no other option.

        Failing is fine. A square window is the status quo, not a bug, so every
        error here leaves the app exactly as it was.
        """
        try:
            hwnd = self._hwnd()
            if sys.getwindowsversion().build >= 22000:
                # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2
                pref = ctypes.c_int(2)
                hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
                if hr == 0:
                    return
            s = self._get_window_scaling()
            w, h = round(self.W * s), round(self.H * s)
            r = round(self.CORNER_R * 2 * s)
            rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, r, r)
            ctypes.windll.user32.SetWindowRgn(hwnd, rgn, True)
        except Exception:
            pass

    def _hide_all(self):
        for name in ("_main_frame", "_settings_frame"):
            frame = getattr(self, name, None)
            if frame is not None:
                frame.pack_forget()

    def _show_main(self):
        self._hide_all()
        self._main_frame.pack(fill="both", expand=True)
        self._view = "main"

    def _show_settings(self):
        self._refresh_mics()
        self._hide_all()
        self._settings_frame.pack(fill="both", expand=True)
        self._view = "settings"


    # ── Drag ──────────────────────────────────────────────────────────────────
    def _drag_start(self, e):
        self._drag_x, self._drag_y = e.x, e.y

    def _drag_move(self, e):
        self.geometry(f"+{self.winfo_x() + e.x - self._drag_x}"
                      f"+{self.winfo_y() + e.y - self._drag_y}")

    # ── Settings helpers ──────────────────────────────────────────────────────
    def _device_hint_text(self):
        v = self._device_var.get()
        if v == "auto":
            if IS_PACKAGED:
                return ("Uses your processor. This download does not come with "
                        "graphics card support, to keep it small.")
            return ("Uses your graphics card if you have one, and your processor "
                    "if you do not. This is the safe choice.")
        if v == "cpu":
            return ("Always uses the processor. Slower, but it works on any computer.")
        return ("Always uses the graphics card. Much faster, but it only works "
                "with an NVIDIA card.")

    def _fill_hw_info(self):
        lines = []
        try:
            import ctranslate2
            n = ctranslate2.get_cuda_device_count()
            if n > 0:
                vram = _get_vram_mb()
                lines.append(f"Graphics card: yes ({vram} MB)")
            else:
                lines.append("Graphics card: none the app can use")
        except Exception:
            lines.append("Graphics card: none the app can use")
        import psutil
        ram = psutil.virtual_memory().total // (1024 ** 3)
        lines.append(f"Memory: {ram} GB")
        text = "\n".join(lines)
        self.after(0, lambda: self._hw_label.configure(text=text))

    def _apply_settings(self):
        global _trigger_down
        new_settings = {
            "device":   self._device_var.get(),
            "model":    self._model_var.get(),
            "hotkey":   self._settings.get("hotkey", "key:insert"),
            "mic":      self._mic_var.get(),
            "language": language_code(self._lang_var.get()),
            "theme":    self._settings.get("theme", "dark"),
        }
        save_settings(new_settings)
        self._settings = new_settings
        _trigger_down = False

        self._apply_status.configure(text="Getting ready...",
                                     text_color=self.theme["status_reload_text"])
        self._show_main()
        ui_queue.put(("log", "Settings saved. Getting ready...", "dim"))
        ui_queue.put(("log", f"Microphone: {current_mic_name()}", "dim"))
        ui_queue.put(("log", f"Language: {language_name(new_settings['language'])} · "
                             f"Key: {hotkey_display_name(new_settings['hotkey'])}", "dim"))
        ui_queue.put(("status", "Getting ready...", self.theme["status_reload"]))
        # The old model is still loaded and would happily transcribe, but with
        # settings the user has already been told were applied. Off until the
        # new one is up.
        ui_queue.put(("model_loading",))
        threading.Thread(target=self._reload_model, daemon=True).start()

    def _reload_model(self):
        device, compute, model_size = resolve_config(self._settings)
        load_model(
            device, compute, model_size,
            on_done=lambda d, c, m: ui_queue.put(("model_ready", d, c, m)),
            on_error=self._reload_failed,
        )

    def _reload_failed(self, message):
        ui_queue.put(("log", f"Could not get ready: {message}", "error"))
        ui_queue.put(("status", "Something failed. Check settings.",
                      theme_color("status_error")))
        ui_queue.put(("model_error",))

    # ── UI queue processor ────────────────────────────────────────────────────
    # ── Level meter ───────────────────────────────────────────────────────────
    # One timer and one state, on purpose. There used to be two after() chains
    # writing to the same widget: a loading spinner that only stopped once the
    # model was ready, and a recording animation that started on the first
    # recording. Recording before the model finished loading left both running,
    # at 120ms and 55ms, fighting over the same bar with different colours.

    def _set_meter(self, state):
        self._meter_state = state
        if state != "recording":
            self._levels.extend([0.0] * self.METER_N)

    def _start_meter(self):
        if getattr(self, "_meter_running", False):
            return
        self._meter_running = True
        self._tick_meter()

    def _tick_meter(self):
        if not getattr(self, "_meter_running", False):
            return
        self._meter_phase += 1

        if self._meter_state == "recording":
            self._levels.append(input_level)
        elif self._meter_state == "loading":
            # A bump travelling left to right: it says "busy" without pretending
            # to know how far along the download is, which nothing here does.
            centre = (self._meter_phase / 3.0) % (self.METER_N + 10) - 5
            self._levels.clear()
            for i in range(self.METER_N):
                d = (i - centre) / 3.2
                self._levels.append(math.exp(-d * d) * 0.9)

        self._draw_meter()
        self.after(33, self._tick_meter)          # ~30 fps

    def _draw_meter(self):
        t = self.theme
        # log_ok, not accent_bg: these bars are read, not just filled, and raw
        # lime is 1.63:1 on the light theme's white. log_ok is the brand green
        # already solved per theme -- lime on dark, #5c7615 on light.
        #
        # Loading is deliberately grey. It keeps the brand colour meaning "this
        # is your voice" instead of also meaning "something is happening".
        colour = {"recording": t["log_ok"],
                  "loading":   t["text_hint"]}.get(self._meter_state, t["bar_idle"])
        mid = self.METER_H / 2
        floor_h = self.METER_W / 2                # a dot when silent, not a gap
        for bar, level in zip(self._meter_bars, self._levels):
            half = floor_h + level * (self.METER_H / 2 - floor_h - 1)
            self._meter.coords(bar, self._meter.coords(bar)[0], mid - half,
                               self._meter.coords(bar)[0], mid + half)
            self._meter.itemconfigure(bar, fill=colour)

    def _process_ui_queue(self):
        try:
            while True:
                msg = ui_queue.get_nowait()
                if msg[0] == "recording":
                    self._set_recording(msg[1])
                elif msg[0] == "status":
                    self._status.configure(text=msg[1], text_color=msg[2])
                elif msg[0] == "log":
                    self._append_log(msg[1], msg[2])
                elif msg[0] == "model_ready":
                    self._set_meter("idle")
                    d, c, m = msg[1], msg[2], msg[3]
                    where = "graphics card" if d == "cuda" else "processor"
                    label = f"{m} model  ·  running on your {where}"
                    self._last_model_label = label
                    self._model_is_ready = True
                    self._set_hold_enabled(True)
                    self._model_info.configure(text=label, text_color=self.theme["model_ready_text"])
                    self._status.configure(text=_idle_status(), text_color=self.theme["status_idle"])
                    self._append_log("Ready to use.", "ok")
                elif msg[0] == "model_loading":
                    self._model_is_ready = False
                    self._set_hold_enabled(False)
                    self._set_meter("loading")
                    self._start_meter()
                elif msg[0] == "model_error":
                    self._model_is_ready = False
                    self._set_hold_enabled(False)
                    self._set_meter("idle")
        except queue.Empty:
            pass
        self.after(80, self._process_ui_queue)

    def _set_recording(self, is_rec):
        t = self.theme
        if is_rec:
            self._dot.configure(image=mark_image("recording"))
            self._stage_mark.configure(image=mark_image("recording", 13))
            self._status.configure(text="LISTENING", text_color=t["rec_status_text"])
            self._set_meter("recording")
        else:
            self._dot.configure(image=mark_image("idle"))
            self._stage_mark.configure(image=mark_image("idle", 13))
            self._status.configure(text="WRITING IT DOWN", text_color=t["processing_text"])
            self._set_meter("idle")

    def _append_log(self, text, tag):
        self._log_lines.append((text, tag))
        if len(self._log_lines) > 14:
            self._log_lines = self._log_lines[-14:]
        self._append_log_widget(text, tag)

    def _append_log_widget(self, text, tag):
        self._log.configure(state="normal")
        try:
            self._log.insert("end", text + "\n", tag)
        except Exception:
            self._log.insert("end", text + "\n")
        lines = int(self._log.index("end-1c").split(".")[0])
        if lines > 14:
            self._log.delete("1.0", f"{lines - 14}.0")
        self._log.configure(state="disabled")
        self._log.see("end")

    def _restore_log(self):
        for text, tag in self._log_lines:
            self._append_log_widget(text, tag)

    def set_model_ready(self, device, compute, model_size):
        ui_queue.put(("model_ready", device, compute, model_size))

    # ── Window controls ───────────────────────────────────────────────────────
    def _minimize(self):
        # No taskbar entry (overrideredirect) — without a tray icon the window
        # would be unrecoverable, so refuse to hide in that case.
        if tray_icon is None:
            ui_queue.put(("log", "There is no icon near the clock, so you could "
                                 "not get this window back. Hiding is turned off.", "dim"))
            return
        self.withdraw()

    def _on_close(self):
        shutdown()


# ── Main ──────────────────────────────────────────────────────────────────────
def _seed_demo_log():
    """Fill the history with a sample session, for KARA_DEMO_LOG only."""
    app_gui._log_lines = []
    app_gui._log.configure(state="normal")
    app_gui._log.delete("1.0", "end")
    app_gui._log.configure(state="disabled")
    for text, tag in [
        ("Ready to use.", "dim"),
        ("Recorded 2.8s", "dim"),
        ("Send me the report before Friday, please.", "said"),
        ("Recorded 2.1s", "dim"),
        ("I'll call you back in about ten minutes.", "said"),
        ("Recorded 3.4s", "dim"),
        ("Remember to buy coffee on the way home.", "said"),
    ]:
        app_gui._append_log(text, tag)


def main():
    global app_gui, _current_hotkey_str

    settings = load_settings()
    _current_hotkey_str = settings.get("hotkey", "key:insert")

    device, compute, model_size = resolve_config(settings)

    app_gui = KaraApp()
    ui_queue.put(("log", "Getting ready...", "dim"))
    ui_queue.put(("log", "The first time you run this, it has to download some "
                         "files. That can take a few minutes.", "dim"))
    ui_queue.put(("log", f"Language: {language_name(settings['language'])} · "
                         f"Key: {hotkey_display_name(_current_hotkey_str)}", "dim"))
    ui_queue.put(("status", "Getting ready...", theme_color("status_loading")))
    app_gui.update()
    app_gui.after(200, lambda: (app_gui._set_meter("loading"), app_gui._start_meter()))

    def _on_model_ready(d, c, m):
        ui_queue.put(("model_ready", d, c, m))

    def _on_model_error(e):
        ui_queue.put(("log", f"Could not get ready: {e}", "error"))
        ui_queue.put(("status", "Something failed. Check settings.",
                      theme_color("status_error")))
        ui_queue.put(("model_error",))

    threading.Thread(
        target=load_model,
        args=(device, compute, model_size, _on_model_ready, _on_model_error),
        daemon=True
    ).start()

    ui_queue.put(("log", f"Microphone: {current_mic_name()}", "dim"))

    # The website's screenshots need a window with something in it, and an empty
    # history photographs badly. KARA_DEMO_LOG fills it with a plausible session
    # so tools/screenshots.py can photograph the real UI rather than a mockup --
    # real widgets, real fonts, real spacing, sample sentences. Never set in
    # normal use, and it only ever writes to the log.
    if os.environ.get("KARA_DEMO_LOG"):
        app_gui.after(1200, _seed_demo_log)

    mouse_listener = MouseListener(on_click=on_mouse_click)
    mouse_listener.start()

    keyboard_listener = KeyboardListener(on_press=on_key_press, on_release=on_key_release)
    keyboard_listener.start()

    threading.Thread(target=run_tray, daemon=True).start()

    app_gui.mainloop()
    with stream_lock:
        _close_stream()
    mouse_listener.stop()
    keyboard_listener.stop()


if __name__ == "__main__":
    main()
