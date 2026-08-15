"""Render the terminal card used on the website to show the winget command.

    py -3 tools/make_winget_image.py

Deliberately shows the command and nothing else. Screenshotting a successful
install would mean staging output that has not happened: the package is not in
the winget repository until Microsoft merges it. An illustration of what to type
is honest; a fabricated transcript is not.
"""
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT  = os.path.join(ROOT, "docs", "winget-command.png")

SS   = 2                      # drawn at 2x and scaled down, for crisp text
W, H = 720 * SS, 176 * SS

BG      = (12, 12, 12)
CHROME  = (32, 32, 32)
BORDER  = (58, 58, 58)
TITLE   = (170, 170, 170)
PROMPT  = (128, 128, 128)
COMMAND = (238, 238, 238)
CARET   = (238, 238, 238)


def mono(size):
    for name in ("consola.ttf", "consolas.ttf", "cour.ttf", "DejaVuSansMono.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def ui(size):
    for name in ("segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main():
    img  = Image.new("RGB", (W, H), BG)
    d    = ImageDraw.Draw(img)
    bar  = 38 * SS

    d.rectangle([0, 0, W, bar], fill=CHROME)
    d.line([0, bar, W, bar], fill=BORDER, width=SS)
    d.text((16 * SS, bar // 2), "Windows PowerShell",
           font=ui(13 * SS), fill=TITLE, anchor="lm")

    # Window buttons, drawn rather than faked from a real title bar.
    for i, x in enumerate((W - 108 * SS, W - 68 * SS, W - 28 * SS)):
        y = bar // 2
        if i == 0:
            d.line([x - 7 * SS, y, x + 7 * SS, y], fill=TITLE, width=SS)
        elif i == 1:
            d.rectangle([x - 6 * SS, y - 6 * SS, x + 6 * SS, y + 6 * SS],
                        outline=TITLE, width=SS)
        else:
            d.line([x - 6 * SS, y - 6 * SS, x + 6 * SS, y + 6 * SS], fill=TITLE, width=SS)
            d.line([x - 6 * SS, y + 6 * SS, x + 6 * SS, y - 6 * SS], fill=TITLE, width=SS)

    f = mono(15 * SS)
    x, y = 18 * SS, bar + 26 * SS

    prompt = "PS C:\\> "
    d.text((x, y), prompt, font=f, fill=PROMPT)
    x += d.textlength(prompt, font=f)

    cmd = "winget install bryramirezp.DictationTool"
    d.text((x, y), cmd, font=f, fill=COMMAND)
    x += d.textlength(cmd, font=f)

    # Block caret: this is a command waiting to be run, not one that already ran.
    d.rectangle([x + 3 * SS, y, x + 11 * SS, y + 19 * SS], fill=CARET)

    img.resize((W // SS, H // SS), Image.LANCZOS).save(OUT)
    print(f"  {os.path.relpath(OUT, ROOT)}  {os.path.getsize(OUT)/1024:.1f} KB")


if __name__ == "__main__":
    main()
