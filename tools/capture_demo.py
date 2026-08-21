"""Capture one real dictation for the website's hero animation.

    py -3 tools/capture_demo.py

Launches Kara for real -- your own mic, your own model, your own machine.
Hold your hotkey, say one natural sentence out loud, let go, and close the
app once you're happy with what landed. This then saves what Kara actually
recorded and transcribed -- the loudness envelope, the real text, the real
gap before the paste -- to docs/demo-data.json.

Run it again for another take if the first one is awkward or too long; each
run only keeps the last dictation that went through before you closed the
app. No invented sentences: see the comment above the demo block in
docs/index.html for why that matters here.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "demo-data.json")


def _kara_python():
    """The interpreter Kara's own dependencies live in, not whichever one is
    running this script -- same lookup as launch.bat. Whatever ran this file
    (a bare `py -3`, say) may be a different Python with no numpy/faster-
    whisper installed at all.
    """
    for var in ("KARA_PYTHON", "DICTATION_PYTHON"):
        exe = os.environ.get(var)
        if exe and os.path.exists(exe):
            return exe
    return sys.executable


def main():
    fd, capture_path = tempfile.mkstemp(suffix=".json", prefix="kara-capture-")
    os.close(fd)
    os.remove(capture_path)  # kara.py only writes here after a real dictation

    env = dict(os.environ)
    env["KARA_CAPTURE_DEMO"] = capture_path

    exe = _kara_python()
    print("Launching Kara...")
    print("Hold your hotkey, say a sentence, let go, wait for it to paste.")
    print("Don't like it? Say it again right there, same window -- no need "
          "to close and restart. Only close Kara once you're happy with the "
          "last thing you said. That's the one this keeps.\n")

    subprocess.run([exe, os.path.join(ROOT, "kara.py")],
                   cwd=ROOT, env=env, check=False)

    if not os.path.exists(capture_path):
        raise SystemExit(
            "No capture was written -- the app closed before a dictation "
            "went through. Run it again and wait for the text to paste "
            "before closing the window.")

    with open(capture_path, encoding="utf-8") as f:
        data = json.load(f)
    os.remove(capture_path)

    print("Captured: %r (%d ms listening, %d ms gap before paste)" %
          (data["text"], data["listenMs"], data["gapMs"]))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print("Saved to %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
