# Dictation Tool

Push-to-talk voice dictation for Windows, powered by [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
Hold a hotkey, speak, release — the transcription is pasted into whatever app has focus.

Runs fully offline. No audio ever leaves your machine.

## Features

- **Push-to-talk** with any keyboard key or mouse button (default: `Insert`)
- **Microphone selection** — pick a specific input device or use the system default; devices are resolved fresh on every recording, so plugging/unplugging headsets or controllers just works
- **Language** — always explicit (Spanish, English, Portuguese, French, German, Italian). There is
  no auto-detect on purpose: it costs a detection pass on every recording and gets it wrong on
  short clips
- **Hardware aware** — auto-picks model and precision based on GPU/VRAM, with manual override (CPU int8 / CUDA float16)
- **Light and dark theme**, switchable from Settings
- **Minimal always-on-top UI** + system tray icon with recording indicator
- Mic is only open while you hold the hotkey (privacy + battery friendly)

## Requirements

- Windows 10/11
- Python 3.10+
- Optional: NVIDIA GPU with CUDA for fast transcription with the large model

## Install

```bat
pip install -r requirements.txt
```

For NVIDIA GPU acceleration also install:

```bat
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

## Run

```bat
launch.bat
```

or silently (no console window):

```bat
wscript launch.vbs
```

First run downloads the Whisper model (~1.6 GB for large-v3-turbo, ~460 MB for small).

To start automatically with Windows, put a shortcut to `launch.vbs` in
`shell:startup` (Win+R → `shell:startup`).

## Usage

1. Hold the hotkey (default `Insert`) and speak.
2. Release. The text is transcribed and pasted at the cursor via Ctrl+V.
3. Click ⚙ to change hotkey, microphone, language, device, model, or theme.

Settings are stored in `%LOCALAPPDATA%\DictationTool\settings.json`.

## Notes

- The hotkey is *not* suppressed system-wide: if you bind a character key, that
  character will still reach the focused app while you hold it. Prefer keys like
  `Insert`, `F13+`, or extra mouse buttons.
- Paste works via clipboard (contents are restored ~0.3 s after pasting).
