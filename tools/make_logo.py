"""Render every logo file the project ships, from the one drawing in the app.

Run it after changing _make_tray_icon so the icon, the logo and the app itself
never drift apart:

    py -3 tools/make_logo.py

Importing kara would start the app, so this pulls the drawing function
out of the source without executing the module-level setup.
"""
import os
import sys
import types

ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
DOCS   = os.path.join(ROOT, "docs")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402


def _font(size):
    """Segoe UI if this is Windows, otherwise whatever Pillow can find."""
    for name in ("segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _load_drawing():
    """Return _make_tray_icon() without running the app.

    The module opens a mutex, shows a message box if one is already running and
    builds a Tk window at import time, so it cannot simply be imported here.
    """
    src = open(os.path.join(ROOT, "kara.py"), encoding="utf-8").read()
    start = src.index("MARK_STATES = {")   # the palette the mark reads its colour from
    end   = src.index("TRAY_IDLE", start)
    mod = types.ModuleType("_logo_drawing")
    mod.__dict__.update(Image=Image, ImageDraw=ImageDraw)
    exec(compile(src[start:end], "kara.py", "exec"), mod.__dict__)
    return mod._make_tray_icon


def _svg():
    """Hand-written master, same fractions as the Pillow drawing."""
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <title>Kara</title>
  <circle cx="32" cy="32" r="32" fill="#171717"/>
  <path d="M43.22 8.99A25.6 25.6 0 1 1 20.78 8.99" fill="none" stroke="#abdb25"
        stroke-width="5.44" stroke-linecap="round"/>
  <rect x="27.84" y="21.12" width="8.32" height="21.76" rx="4.16" fill="#e8e8e8"/>
</svg>
"""


def main():
    make = _load_drawing()
    os.makedirs(ASSETS, exist_ok=True)
    os.makedirs(DOCS, exist_ok=True)
    written = []

    def save(img, path):
        img.save(path)
        written.append((os.path.relpath(path, ROOT), os.path.getsize(path)))

    # Windows wants every size in one file: 16 for the tray and title bar, 256
    # for the large view in Explorer.
    #
    # Each frame is drawn at its own size rather than scaled down from the 256,
    # so the small ones get the simplified mark and a stroke width that lands on
    # whole pixels. Downscaling one big frame is what made 16px look like a smudge.
    ico_sizes = [16, 32, 48, 64, 128, 256]
    frames = [make(False, n) for n in ico_sizes]
    ico = os.path.join(ASSETS, "icon.ico")
    frames[-1].save(ico, format="ICO",
                    sizes=[(n, n) for n in ico_sizes],
                    append_images=frames[:-1])
    written.append(("assets/icon.ico", os.path.getsize(ico)))

    # Linux .desktop / hicolor, and general use.
    save(make(False, 512),  os.path.join(ASSETS, "icon-512.png"))
    save(make(False, 1024), os.path.join(ASSETS, "icon-1024.png"))

    with open(os.path.join(ASSETS, "logo.svg"), "w", encoding="utf-8") as f:
        f.write(_svg())
    written.append(("assets/logo.svg", os.path.getsize(os.path.join(ASSETS, "logo.svg"))))

    # README header.
    save(make(False, 256), os.path.join(DOCS, "logo.png"))

    # Repo social preview: what GitHub shows when the link is shared.
    card = Image.new("RGB", (1280, 640), (15, 15, 15))
    mark = make(False, 260)
    card.paste(mark, (510, 90), mark)
    d = ImageDraw.Draw(card)
    for text, y, size, fill in (
        ("Kara",                     400, 64, (255, 255, 255)),
        ("Hold a key, speak, and it types.",   490, 30, (204, 204, 204)),
        ("Runs offline on Windows",            540, 26, (153, 153, 153)),
    ):
        d.text((640, y), text, font=_font(size), fill=fill, anchor="ma")
    save(card, os.path.join(DOCS, "social-preview.png"))

    for name, size in written:
        print(f"  {name:34s} {size/1024:8.1f} KB")


if __name__ == "__main__":
    sys.exit(main())
