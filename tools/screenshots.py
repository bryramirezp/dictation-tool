"""Photograph the real app for the website.

    py -3 tools/screenshots.py

Writes docs/screenshot-{main,settings}-{dark,light}.png.

These went stale once already: the window was redesigned and the site spent a
day showing an app that no longer existed. Taking them by hand is what made that
possible, so this does the whole round -- set the theme, launch, wait, find the
window, capture, restore the theme -- and can be re-run after any UI change.

The window has rounded corners now, and a rectangular grab of the screen carries
four little wedges of whatever was behind it. Rather than cut them at a radius --
which was tried, and left a crescent of desktop in every corner because DWM's
radius is not the one that was guessed -- the app is photographed twice, over
black and over white, and the true transparency is solved from the pair. See
matte(). The PNGs go out with an alpha channel that sits cleanly on either theme
of the page.

Requires the app's own dependencies, because it runs the app. Your own theme
setting is put back at the end, including if this crashes.
"""
import io
import json
import os
import subprocess
import sys
import time

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Kara")
SETTINGS = os.path.join(APP_DIR, "settings.json")

def read_settings():
    try:
        with io.open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_settings(s):
    with io.open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)


def kill_app():
    subprocess.run(["taskkill", "/F", "/IM", "pythonw.exe"],
                   capture_output=True, check=False)
    time.sleep(0.6)


def launch(env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(exe):
        exe = sys.executable
    return subprocess.Popen([exe, os.path.join(ROOT, "kara.py")], cwd=ROOT, env=env)


def find_window(pid):
    """The app's own window, matched by process rather than by title.

    Matching on the title "Kara" alone picked up a browser tab showing the
    project's website and photographed devtools instead of the app. The pid is
    the only thing that cannot collide.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right - r.left > 100 and r.bottom - r.top > 100:
            found.append((r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumWindows(each, 0)
    return found[0] if found else None


def move_window(pid, x, y):
    """Drag the window out from under the desktop.

    It parks itself bottom-right, which on an unactivated Windows is exactly
    where the "Activate Windows" watermark is painted -- and the watermark went
    straight into the first set of screenshots, right across the app.
    """
    import ctypes
    rect = find_window(pid)
    if not rect:
        return None
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    hwnd = find_hwnd(pid)
    if not hwnd:
        return None
    # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
    ctypes.windll.user32.SetWindowPos(hwnd, 0, x, y, 0, 0,
                                      0x0001 | 0x0004 | 0x0010)
    time.sleep(0.5)
    return (x, y, x + w, y + h)


def find_hwnd(pid):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left > 100 and r.bottom - r.top > 100:
                out.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    return out[0] if out else None


def grab(rect):
    # Screen grab through PIL rather than GDI: the window is composited, and a
    # PrintWindow of a Tk canvas comes back with holes in it.
    from PIL import ImageGrab
    return ImageGrab.grab(bbox=rect, all_screens=True).convert("RGB")


def backdrop(colour):
    """A fullscreen sheet of one colour, behind the app."""
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(exe):
        exe = sys.executable
    p = subprocess.Popen([exe, os.path.join(ROOT, "tools", "_backdrop.py"), colour])
    time.sleep(1.4)
    return p


def raise_app(pid):
    """Put the app back on top of the backdrop, which is topmost too."""
    import ctypes
    hwnd = find_hwnd(pid)
    if hwnd:
        # HWND_TOPMOST, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0,
                                          0x0002 | 0x0001 | 0x0010)
        time.sleep(0.4)


def matte(over_black, over_white):
    """Recover the real transparency by photographing the window twice.

    The first version of this cut the corners to a guessed 8px radius. DWM's
    radius is not 8, so a crescent of desktop survived in each corner -- 146
    distinct colours around a one-pixel border, none of them the app's.

    Guessing a better number would be the same mistake with a different value,
    so nothing is guessed. A pixel composited over black and again over white
    gives two equations: black = colour*a, white = colour*a + 255*(1-a). Their
    difference is 255*(1-a) and depends on nothing else, so the alpha falls out
    exactly -- including the part-covered pixels along the curve, which is what
    makes the edge look drawn rather than cut.
    """
    b = over_black.load()
    w = over_white.load()
    W, H = over_black.size
    out = Image.new("RGBA", (W, H))
    o = out.load()
    for y in range(H):
        for x in range(W):
            br, bg, bb = b[x, y]
            wr, wg, wb = w[x, y]
            # One alpha per pixel, from the mean of the three channels: the
            # difference is the same in each for a grey-agnostic compositor, and
            # averaging shrugs off a stray bit of rounding.
            a = 255 - ((wr - br) + (wg - bg) + (wb - bb)) / 3.0
            a = max(0.0, min(255.0, a))
            if a < 8:                      # shadow fringe, not window
                o[x, y] = (0, 0, 0, 0)
                continue
            k = 255.0 / a
            o[x, y] = (min(255, int(br * k + .5)),
                       min(255, int(bg * k + .5)),
                       min(255, int(bb * k + .5)),
                       int(a + .5))

    # A backdrop that covers the app instead of sitting behind it makes both
    # shots a flat sheet, the difference 255 everywhere, and every pixel
    # transparent. That happened, and it wrote four blank PNGs without
    # complaining. It cannot do that twice.
    opaque = sum(1 for y in range(0, H, 4) for x in range(0, W, 4)
                 if o[x, y][3] > 200)
    if opaque < (W // 4) * (H // 4) * 0.5:
        raise SystemExit("the matte came out mostly transparent -- the backdrop "
                         "was probably in front of the app, not behind it")
    return out


def shoot(theme, view, out_name, saved):
    """One screenshot: set the theme, launch, optionally open settings, capture."""
    kill_app()
    s = dict(saved)
    s["theme"] = theme
    write_settings(s)

    proc = launch({"KARA_DEBUG_LOG": "", "KARA_DEMO_LOG": "1"})
    print("  launching for %s/%s ..." % (view, theme))
    rect = None
    for _ in range(40):                     # the model load is the slow part
        time.sleep(1.0)
        rect = find_window(proc.pid)
        if rect:
            break
    if not rect:
        proc.terminate()
        raise SystemExit("could not find the Kara window")
    time.sleep(4.0)                          # let the seeded log land
    rect = move_window(proc.pid, 240, 90) or rect

    if view == "settings":
        click_settings(rect)
        time.sleep(1.4)

    rect = find_window(proc.pid) or rect
    # Park the pointer off the window first. Left where the last click put it,
    # it sits on the gear and photographs it lit up in its hover colour.
    import ctypes
    ctypes.windll.user32.SetCursorPos(10, 10)
    time.sleep(0.4)

    shots = {}
    for name, colour in (("black", "000000"), ("white", "ffffff")):
        sheet = backdrop(colour)
        raise_app(proc.pid)
        shots[name] = grab(find_window(proc.pid) or rect)
        sheet.terminate()
        time.sleep(0.5)
    img = matte(shots["black"], shots["white"])
    check(img, view)
    path = os.path.join(DOCS, out_name)
    img.save(path)
    print("    %s  %dx%d" % (out_name, img.width, img.height))
    proc.terminate()
    time.sleep(0.5)


def click_settings(rect):
    """Click the gear.

    The header packs its buttons right to left -- ✕, then ─, then ⚙ -- each 32
    wide with 4px after it. So the gear's centre is 92 in from the right edge.
    Getting this wrong the first time hit ─ instead, which sent the app to the
    tray and left the script photographing whatever was behind it.
    """
    import ctypes
    user32 = ctypes.windll.user32
    left, top, right, _ = rect
    scale = (right - left) / 320.0           # the window is 320 wide, logically
    x = int(right - round(92 * scale))
    y = int(top + round(24 * scale))
    user32.SetCursorPos(x, y)
    time.sleep(0.3)
    user32.mouse_event(0x02, 0, 0, 0, 0)
    user32.mouse_event(0x04, 0, 0, 0, 0)


def check(img, view):
    """Refuse to save something the wrong shape.

    Matching the window by pid is what actually stops this photographing a
    browser tab; this is the cheap backstop. The app is 320x480 and cannot be
    resized, so anything that is not that ratio is not it.

    Not checked: whether the corners came out transparent. They always do --
    round_corners cuts them whatever it was handed -- so it proves nothing.
    """
    ratio = img.width / img.height
    if abs(ratio - 320 / 480) > 0.02:
        raise SystemExit("grabbed %dx%d for the %s view, which is not the "
                         "window's 2:3" % (img.width, img.height, view))


def main():
    saved = read_settings()
    print("Kara screenshots -> docs/")
    print("  (your theme is %r and will be put back)" % saved.get("theme", "dark"))
    try:
        for theme in ("dark", "light"):
            shoot(theme, "main", "screenshot-main-%s.png" % theme, saved)
            shoot(theme, "settings", "screenshot-settings-%s.png" % theme, saved)
    finally:
        kill_app()
        if saved:
            write_settings(saved)
            print("\n  theme restored to %r" % saved.get("theme", "dark"))
    print("\nDone. Check them before committing -- this drives a real GUI and a "
          "mistimed click lands on the wrong view.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
