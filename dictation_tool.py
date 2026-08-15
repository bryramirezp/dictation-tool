"""
Dictation Tool — Voice to Text
Push-to-talk dictation powered by faster-whisper.
Hold the configured hotkey to record. Release to transcribe and paste.
Default hotkey: Insert
"""

import sys
import os
import json
import subprocess
import threading
import time
import queue
import ctypes

# ── Single instance guard ─────────────────────────────────────────────────────
def ensure_single_instance():
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, "DictationTool_SingleInstance")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(
            None,
            "Dictation Tool is already open.\nLook for the icon near the clock.",
            "Dictation Tool", 0x40)
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

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
import pyperclip
import pyautogui
import pystray
import customtkinter as ctk
from PIL import Image, ImageDraw
from pynput.mouse import Button, Listener as MouseListener
from pynput.keyboard import Key, KeyCode, Listener as KeyboardListener

pyautogui.FAILSAFE = False  # corner-of-screen abort would break paste mid-flow

# ── Paths ─────────────────────────────────────────────────────────────────────
APP_DIR       = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "DictationTool")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
os.makedirs(APP_DIR, exist_ok=True)

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
    return s

def save_settings(s):
    global app_settings
    app_settings = dict(s)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, SETTINGS_FILE)

# ── Theme ─────────────────────────────────────────────────────────────────────
THEMES = {
    "dark": {
        "bg_root":             "#0f0f0f",
        "bg_header":           "#181818",
        "bg_button":           "#1a1a1a",
        "bg_button_hover":     "#252525",
        "option_hover":        "#303030",
        "bg_log":              "#0a0a0a",
        "border":              "#222222",
        # Text colors below are solved for a target contrast ratio against the
        # surface each one is drawn on, not picked by eye. See _solve_gray().
        "text_title":          "#d4d4d4",   # on bg_header  11.98:1
        "text_body":           "#afafaf",   # on bg_log      9.03:1
        "text_caption":        "#898989",   # on bg_root     5.48:1
        "text_hint":           "#727272",   # on bg_root     3.98:1
        "text_faint":          "#636363",   # on bg_root     3.19:1
        "icon_btn":            "#a3a3a3",   # on bg_header   7.04:1
        "mic_btn":             "#909090",   # on bg_root     6.00:1
        # Widget labels: customtkinter's dark-blue theme hardcodes #DCE4EE for
        # both appearance modes, so every button/menu must be told explicitly.
        "text_on_button":      "#b0b0b0",   # on bg_button   8.03:1
        "text_on_accent":      "#cfcfcf",   # on accent_bg   8.03:1
        "text_on_capture":     "#c1c1c1",   # on capture_bg  8.01:1
        "hover_close":         "#3a1010",
        "hover_neutral":       "#222222",
        "hover_settings":      "#1e2a1e",
        "dot_idle":            "#3a3a3a",
        "dot_rec":             "#f72929",   # on bg_header   4.48:1
        "bar_track":           "#1a1a1a",
        "bar_idle":            "#2a2a2a",
        "bar_spin":            "#224422",
        "bar_rec":             "#771111",
        "scrollbar":           "#222222",
        "scrollbar_hover":     "#333333",
        "accent_bg":           "#1e3a1e",
        "accent_hover":        "#254a25",
        "capture_bg":          "#1e2a3e",
        "log_ok":              "#519651",   # on bg_log      5.50:1
        "log_error":           "#f34949",   # on bg_log      5.52:1
        "log_dim":             "#676767",   # on bg_log      3.50:1
        "status_idle":         "#898989",   # on bg_root     5.48:1
        "status_error":        "#de5353",   # on bg_root     4.99:1
        "status_loading":      "#5e8d5e",   # on bg_root     4.97:1
        "status_reload":       "#5e8d5e",
        "status_reload_text":  "#688b68",   # on bg_root     5.00:1
        "model_ready_text":    "#7b7b7b",   # on bg_root     4.53:1
        "rec_status_text":     "#ef4141",   # on bg_root     5.02:1
        "processing_text":     "#898989",   # on bg_root     5.48:1
    },
    "light": {
        "bg_root":             "#f2f2f2",
        "bg_header":           "#e4e4e4",
        "bg_button":           "#e0e0e0",
        "bg_button_hover":     "#d0d0d0",
        "option_hover":        "#c7c7c7",
        "bg_log":              "#ffffff",
        "border":              "#d5d5d5",
        "text_title":          "#252525",   # on bg_header  12.06:1
        "text_body":           "#494949",   # on bg_log      9.00:1
        "text_caption":        "#616161",   # on bg_root     5.53:1
        "text_hint":           "#777777",   # on bg_root     4.00:1
        "text_faint":          "#878787",   # on bg_root     3.21:1
        "icon_btn":            "#4a4a4a",   # on bg_header   6.97:1
        "mic_btn":             "#5c5c5c",   # on bg_root     5.97:1
        "text_on_button":      "#3f3f3f",   # on bg_button   7.98:1
        "text_on_accent":      "#404040",   # on accent_bg   8.02:1
        "text_on_capture":     "#3e3e3e",   # on capture_bg  7.96:1
        "hover_close":         "#f2c9c9",
        "hover_neutral":       "#d6d6d6",
        "hover_settings":      "#cdead0",
        "dot_idle":            "#bdbdbd",
        "dot_rec":             "#c82121",   # on bg_header   4.47:1
        "bar_track":           "#e2e2e2",
        "bar_idle":            "#cfcfcf",
        "bar_spin":            "#bfe0bf",
        "bar_rec":             "#e2a0a0",
        "scrollbar":           "#d0d0d0",
        "scrollbar_hover":     "#bdbdbd",
        "accent_bg":           "#cdeacd",
        "accent_hover":        "#b7dfb7",
        "capture_bg":          "#cfe0f5",
        "log_ok":              "#227928",   # on bg_log      5.48:1
        "log_error":           "#c72c20",   # on bg_log      5.51:1
        "log_dim":             "#898989",   # on bg_log      3.50:1
        "status_idle":         "#616161",   # on bg_root     5.53:1
        "status_error":        "#c52c1f",   # on bg_root     5.00:1
        "status_loading":      "#227727",   # on bg_root     5.02:1
        "status_reload":       "#227727",
        "status_reload_text":  "#227727",   # on bg_root     5.02:1
        "model_ready_text":    "#6f6f6f",   # on bg_root     4.49:1
        "rec_status_text":     "#c52c1f",   # on bg_root     5.00:1
        "processing_text":     "#616161",   # on bg_root     5.53:1
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
def _make_tray_icon(rec=False, size=64, detail=None):
    """The app mark: a microphone on a dark disc.

    Every coordinate is a fraction of the side, so this one drawing serves both
    the 16px tray icon and the 1024px logo (see tools/make_logo.py). Drawn at 4x
    and scaled down because Pillow's shapes are aliased -- at 16px the edge of
    the disc comes out visibly ragged otherwise.

    Below 40px the stand is dropped and the capsule grows to fill the space.
    Kept at full detail, the arc and the stand are about one pixel each and
    smear into the disc, which reads as a blur rather than as a microphone.
    """
    if detail is None:
        detail = size >= 40
    ss  = 4
    s   = size * ss
    end = s - 1
    img  = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def px(*fractions):
        return [round(f * end) for f in fractions]

    bg     = (50, 14, 14)  if rec else (28, 28, 28)
    mc     = (220, 40, 40) if rec else (175, 175, 175)
    sc     = (190, 55, 55) if rec else (130, 130, 130)
    stroke = max(1, round(0.047 * s))

    draw.ellipse(px(0, 0, 1, 1), fill=bg)

    if detail:
        draw.rounded_rectangle(px(0.344, 0.109, 0.656, 0.563),
                               radius=0.156 * end, fill=mc)
        draw.arc(px(0.188, 0.359, 0.813, 0.719), start=0, end=180,
                 fill=sc, width=stroke)
        draw.line(px(0.5, 0.719, 0.5, 0.875),     fill=sc, width=stroke)
        draw.line(px(0.375, 0.875, 0.625, 0.875), fill=sc, width=stroke)
    else:
        draw.rounded_rectangle(px(0.30, 0.16, 0.70, 0.84),
                               radius=0.20 * end, fill=mc)

    return img.resize((size, size), Image.LANCZOS)

TRAY_IDLE = _make_tray_icon(False)
TRAY_REC  = _make_tray_icon(True)

# ── Global state ──────────────────────────────────────────────────────────────
recording          = False
audio_frames       = []
model_obj          = None
model_lock         = threading.Lock()
tray_icon          = None
app_gui            = None
status_lock        = threading.Lock()
stream_lock        = threading.Lock()
ui_queue           = queue.Queue()
SAMPLE_RATE        = 16000

stream             = None      # open only while recording
stream_samplerate  = SAMPLE_RATE

_current_hotkey_str = "key:insert"   # updated from settings at startup / apply
_trigger_down       = False           # tracks press state regardless of kind
_capturing_hotkey   = False           # True while waiting for capture input
_capture_queue      = queue.Queue()   # receives "kind:name" string from listeners

# ── Model loader ──────────────────────────────────────────────────────────────
def load_model(device, compute, model_size, on_done=None, on_error=None):
    global model_obj
    try:
        if device == "cuda" and not cuda_libs_present():
            # Say what to do instead of letting ctranslate2 fail with a DLL name
            # nobody outside this project would recognise.
            raise RuntimeError(
                "Your graphics card is missing some files. Open a terminal and "
                "run:  pip install nvidia-cublas-cu12  ...or set Device back "
                "to auto in settings.")
        with model_lock:
            model_obj = WhisperModel(model_size, device=device, compute_type=compute)
        if on_done:
            on_done(device, compute, model_size)
    except Exception as e:
        if on_error:
            on_error(str(e))

# ── Audio ─────────────────────────────────────────────────────────────────────
def audio_callback(indata, frames, time_info, status):
    if recording:
        audio_frames.append(indata.copy())

def _hold_hint():
    return f"Hold {hotkey_display_name(_current_hotkey_str)} to record"

def _open_stream():
    """Open input stream on the currently selected mic. Called at each record start,
    so device hotplug (headsets, controllers) is picked up automatically."""
    global stream, stream_samplerate
    device = resolve_input_device(app_settings.get("mic", "auto"))
    last_err = None
    for sr in (SAMPLE_RATE, None):
        try:
            if sr is None:  # fall back to the device's native rate
                info = sd.query_devices(device, kind="input") if device is not None \
                       else sd.query_devices(kind="input")
                sr = int(info["default_samplerate"])
            s = sd.InputStream(samplerate=sr, channels=1, device=device,
                               callback=audio_callback, dtype="float32")
            s.start()
            stream = s
            stream_samplerate = sr
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
    with stream_lock:
        ok = _open_stream()
    if not ok:
        with status_lock:
            recording = False
        ui_queue.put(("status", "Can't use the microphone. Check settings.",
                      theme_color("status_error")))
        return
    if tray_icon:
        tray_icon.icon  = TRAY_REC
        tray_icon.title = "Dictation Tool — recording"
    ui_queue.put(("recording", True))

def stop_and_transcribe():
    with stream_lock:
        _close_stream()
    if tray_icon:
        tray_icon.icon  = TRAY_IDLE
        tray_icon.title = "Dictation Tool — ready"
    ui_queue.put(("recording", False))
    if not audio_frames:
        ui_queue.put(("status", _hold_hint(), theme_color("status_idle")))
        return
    data = np.concatenate(audio_frames, axis=0).flatten().astype(np.float32)
    sr = stream_samplerate
    dur = len(data) / sr
    ui_queue.put(("log", f"Recorded {dur:.1f}s", "dim"))
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
            ui_queue.put(("status", _hold_hint(), theme_color("status_idle")))
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

        with model_lock:
            segments, info = model_obj.transcribe(
                audio_data, language=language, beam_size=5, vad_filter=False
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()

        if text:
            ui_queue.put(("log", text, "ok"))
            ui_queue.put(("status", _hold_hint(), theme_color("status_idle")))
            prev = pyperclip.paste()
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.3)  # let the target app read the clipboard before restore
            pyperclip.copy(prev)
        else:
            ui_queue.put(("log", "I didn't hear any words.", "dim"))
            ui_queue.put(("status", _hold_hint(), theme_color("status_idle")))
    except Exception as e:
        ui_queue.put(("log", f"Something went wrong: {e}", "error"))
        ui_queue.put(("status", _hold_hint(), theme_color("status_idle")))

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
        _trigger_down = True
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
            pystray.MenuItem("Dictation Tool — speak to type", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open", _show_window, default=True),
            pystray.MenuItem("Close the app", _quit_tray),
        )
        tray_icon = pystray.Icon("DictationTool", TRAY_IDLE, "Dictation Tool", menu)
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

class DictationToolApp(ctk.CTk):
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

    def __init__(self):
        super().__init__()
        self._settings = load_settings()
        self.theme = THEMES[self._settings.get("theme", "dark")]
        ctk.set_appearance_mode(self._settings.get("theme", "dark"))
        ctk.set_default_color_theme("dark-blue")

        self.title("Dictation Tool")
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

    def _build_main(self):
        t = self.theme
        self._main_frame = ctk.CTkFrame(self, fg_color="transparent")

        # ── Header
        hdr = ctk.CTkFrame(self._main_frame, fg_color=t["bg_header"], corner_radius=0, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        hdr.bind("<ButtonPress-1>", self._drag_start)
        hdr.bind("<B1-Motion>",     self._drag_move)

        self._dot = ctk.CTkLabel(hdr, text="●", font=("Segoe UI", 16),
                                 text_color=t["dot_idle"], width=26)
        self._dot.pack(side="left", padx=(12, 4))

        ctk.CTkLabel(hdr, text="Dictation Tool", font=("Segoe UI Semibold", 15),
                     text_color=t["text_title"]).pack(side="left")

        # header buttons right-to-left
        for symbol, cmd, hover in [
            ("✕", self._on_close,      t["hover_close"]),
            ("─", self._minimize,      t["hover_neutral"]),
            ("⚙", self._show_settings, t["hover_settings"]),
        ]:
            ctk.CTkButton(hdr, text=symbol, width=32, height=28,
                          fg_color="transparent", hover_color=hover,
                          text_color=t["icon_btn"], font=("Segoe UI", 13),
                          command=cmd).pack(side="right", padx=(0, 4))

        # ── Status
        # Rebuilt on every theme switch, so the text has to come from state rather
        # than be hardcoded — otherwise switching theme after load pins the label
        # at "Loading model..." forever.
        if self._model_is_ready:
            status_text, status_color = _hold_hint(), t["status_idle"]
        else:
            status_text, status_color = "Getting ready...", t["status_loading"]
        self._status = ctk.CTkLabel(self._main_frame,
                                    text=status_text,
                                    font=("Segoe UI", 11), text_color=status_color)
        self._status.pack(pady=(12, 4))

        # ── Rec bar
        self._bar = ctk.CTkProgressBar(self._main_frame, width=self.CONTENT_W, height=5,
                                       progress_color=t["bar_idle"], fg_color=t["bar_track"])
        self._bar.set(0)
        self._bar.pack(pady=(0, 10))

        # ── Model info label
        self._model_info = ctk.CTkLabel(self._main_frame, text=self._last_model_label,
                                        font=("Segoe UI", 9), text_color=t["model_ready_text"])
        self._model_info.pack()

        # ── Divider
        ctk.CTkFrame(self._main_frame, height=1, fg_color=t["border"]).pack(
            fill="x", padx=16, pady=(8, 0))

        ctk.CTkLabel(self._main_frame, text="WHAT YOU SAID",
                     font=("Segoe UI", 9), text_color=t["text_caption"]).pack(
            anchor="w", padx=20, pady=(8, 4))

        # ── Log
        self._log = ctk.CTkTextbox(self._main_frame, width=self.CONTENT_W, height=250,
                                   font=("Consolas", 11), fg_color=t["bg_log"],
                                   text_color=t["text_body"], border_color=t["border"],
                                   border_width=1, corner_radius=6,
                                   wrap="word", state="disabled")
        self._log.pack(padx=16, pady=(0, 10))
        for name, color in (("ok", t["log_ok"]), ("error", t["log_error"]), ("dim", t["log_dim"])):
            try:
                self._log.tag_config(name, foreground=color)
            except Exception:
                pass
        self._restore_log()

        self._hold_label = ctk.CTkLabel(
            self._main_frame,
            text=_hold_hint(),
            font=("Segoe UI", 9), text_color=t["text_hint"]
        )
        self._hold_label.pack(pady=(0, 8))

    def _build_settings(self):
        t = self.theme
        self._settings_frame = ctk.CTkFrame(self, fg_color="transparent")

        # ── Header
        hdr = ctk.CTkFrame(self._settings_frame, fg_color=t["bg_header"],
                           corner_radius=0, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        ctk.CTkButton(hdr, text="←", width=36, height=28,
                      fg_color="transparent", hover_color=t["hover_neutral"],
                      text_color=t["icon_btn"], font=("Segoe UI", 15),
                      command=self._show_main).pack(side="left", padx=8)

        ctk.CTkLabel(hdr, text="Settings", font=("Segoe UI Semibold", 14),
                     text_color=t["text_title"]).pack(side="left")

        ctk.CTkButton(hdr, text="✕", width=32, height=28,
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

        ctk.CTkButton(footer, text="Save changes", height=36,
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
                width=self.CONTENT_W, font=("Segoe UI", font_size),
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
                font=("Segoe UI", 12),
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
            height=36,
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
        ctk.CTkButton(mic_row, text="↻", width=24, height=20,
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
        self._device_seg = segmented(["auto", "cpu", "gpu"], self._device_var)
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
            self._hold_label.configure(text=_hold_hint())
        except queue.Empty:
            if _capturing_hotkey:
                self.after(100, self._capture_hotkey_poll)

    # ── View switching ────────────────────────────────────────────────────────
    def _show_main(self):
        self._settings_frame.pack_forget()
        self._main_frame.pack(fill="both", expand=True)
        self._view = "main"

    def _show_settings(self):
        self._refresh_mics()
        self._main_frame.pack_forget()
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
        threading.Thread(target=self._reload_model, daemon=True).start()

    def _reload_model(self):
        device, compute, model_size = resolve_config(self._settings)
        load_model(
            device, compute, model_size,
            on_done=lambda d, c, m: ui_queue.put(("model_ready", d, c, m)),
            on_error=lambda e:      ui_queue.put(("log", f"Could not get ready: {e}", "error")),
        )

    # ── UI queue processor ────────────────────────────────────────────────────
    def _start_loading_spinner(self):
        self._spinner_active = True
        self._spinner_idx = 0
        self._tick_spinner()

    def _tick_spinner(self):
        if not getattr(self, "_spinner_active", False):
            return
        self._spinner_idx += 1
        self._bar.set((self._spinner_idx % 20) / 20)
        self._bar.configure(progress_color=self.theme["bar_spin"])
        self.after(120, self._tick_spinner)

    def _stop_spinner(self):
        self._spinner_active = False
        self._bar.set(0)
        self._bar.configure(progress_color=self.theme["bar_idle"])

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
                    self._stop_spinner()
                    d, c, m = msg[1], msg[2], msg[3]
                    where = "graphics card" if d == "cuda" else "processor"
                    label = f"{m} model  ·  running on your {where}"
                    self._last_model_label = label
                    self._model_is_ready = True
                    self._model_info.configure(text=label, text_color=self.theme["model_ready_text"])
                    self._status.configure(text=_hold_hint(), text_color=self.theme["status_idle"])
                    self._append_log("Ready to use.", "ok")
        except queue.Empty:
            pass
        self.after(80, self._process_ui_queue)

    def _set_recording(self, is_rec):
        t = self.theme
        if is_rec:
            self._dot.configure(text_color=t["dot_rec"])
            self._status.configure(text="Recording...", text_color=t["rec_status_text"])
            self._bar.configure(progress_color=t["bar_rec"])
            self._animate_bar(True, 0.0)
        else:
            self._dot.configure(text_color=t["dot_idle"])
            self._status.configure(text="Writing it down...", text_color=t["processing_text"])
            self._bar.set(0)
            self._bar.configure(progress_color=t["bar_idle"])

    def _animate_bar(self, up, val):
        if not recording:
            self._bar.set(0)
            return
        val += 0.06 if up else -0.06
        if val >= 1.0:  up = False
        elif val <= 0.0: up, val = True, 0.0
        self._bar.set(abs(val))
        self.after(55, lambda: self._animate_bar(up, val))

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
def main():
    global app_gui, _current_hotkey_str

    settings = load_settings()
    _current_hotkey_str = settings.get("hotkey", "key:insert")

    device, compute, model_size = resolve_config(settings)

    app_gui = DictationToolApp()
    ui_queue.put(("log", "Getting ready...", "dim"))
    ui_queue.put(("log", "The first time you run this, it has to download some "
                         "files. That can take a few minutes.", "dim"))
    ui_queue.put(("log", f"Language: {language_name(settings['language'])} · "
                         f"Key: {hotkey_display_name(_current_hotkey_str)}", "dim"))
    ui_queue.put(("status", "Getting ready...", theme_color("status_loading")))
    app_gui.update()
    app_gui.after(200, app_gui._start_loading_spinner)

    def _on_model_ready(d, c, m):
        ui_queue.put(("model_ready", d, c, m))

    def _on_model_error(e):
        ui_queue.put(("log", f"Could not get ready: {e}", "error"))
        ui_queue.put(("status", "Something failed. Check settings.",
                      theme_color("status_error")))

    threading.Thread(
        target=load_model,
        args=(device, compute, model_size, _on_model_ready, _on_model_error),
        daemon=True
    ).start()

    ui_queue.put(("log", f"Microphone: {current_mic_name()}", "dim"))

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
