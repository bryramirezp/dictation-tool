"""Build everything a release ships.

    py -3 packaging/build.py

Leaves in dist/:
    DictationTool-Setup-<version>.exe     the installer
    DictationTool-<version>-portable.zip  the same app, unzip and run
    SHA256SUMS.txt                        hashes of both

The version is read from __version__ in dictation_tool.py, which is the only
place it is written down.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
APP  = os.path.join(DIST, "DictationTool")

# winget installs Inno Setup per user by default, the GitHub runners get it
# machine-wide through choco, and either way someone may just have it on PATH.
ISCC_CANDIDATES = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def version():
    src = open(os.path.join(ROOT, "dictation_tool.py"), encoding="utf-8").read()
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', src, re.M)
    if not m:
        sys.exit("no __version__ found in dictation_tool.py")
    return m.group(1)


def run(cmd):
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def build_exe():
    print("== PyInstaller ==")
    run([sys.executable, "-m", "PyInstaller", "--noconfirm",
         os.path.join("packaging", "dictation_tool.spec")])
    exe = os.path.join(APP, "DictationTool.exe")
    if not os.path.exists(exe):
        sys.exit(f"expected {exe}")


def build_installer(ver):
    print("== Inno Setup ==")
    iscc = next((p for p in ISCC_CANDIDATES if p and os.path.exists(p)),
                shutil.which("ISCC"))
    if not iscc:
        sys.exit("Inno Setup 6 not found. winget install JRSoftware.InnoSetup")
    run([iscc, f"/DAppVersion={ver}", os.path.join("packaging", "installer.iss")])


def stable_alias(setup):
    """A copy of the installer under a name that never changes.

    GitHub only serves /releases/latest/download/<name> for an exact file name,
    so a versioned one stops working the moment a new release goes out. This
    copy is what the download button on the website points at, so the button
    keeps working without editing the page on every release.
    """
    alias = os.path.join(DIST, "DictationTool-Setup.exe")
    shutil.copy2(setup, alias)
    return alias


def build_zip(ver):
    print("== portable zip ==")
    out = os.path.join(DIST, f"DictationTool-{ver}-portable.zip")
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for folder, _, files in os.walk(APP):
            for name in files:
                full = os.path.join(folder, name)
                # Keep the folder in the archive: unzipping 400 loose files into
                # whatever directory the user happened to be in is unkind.
                z.write(full, os.path.join(
                    "DictationTool", os.path.relpath(full, APP)))
    return out


def write_hashes(ver, paths):
    print("== SHA256 ==")
    lines = []
    for p in paths:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        lines.append(f"{h.hexdigest()}  {os.path.basename(p)}")
        print("  " + lines[-1])
    out = os.path.join(DIST, "SHA256SUMS.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out


def main():
    ver = version()
    print(f"Dictation Tool {ver}\n")
    build_exe()
    build_installer(ver)
    zip_path = build_zip(ver)
    setup = os.path.join(DIST, f"DictationTool-Setup-{ver}.exe")
    if not os.path.exists(setup):
        sys.exit(f"expected {setup}")
    alias = stable_alias(setup)
    # The alias is byte for byte the versioned file, so one hash covers both.
    write_hashes(ver, [setup, zip_path])
    print(f"  (DictationTool-Setup.exe is a copy of {os.path.basename(setup)})")
    return alias

    print("\n== ready ==")
    for name in sorted(os.listdir(DIST)):
        p = os.path.join(DIST, name)
        if os.path.isfile(p):
            print(f"  {name:44s} {os.path.getsize(p)/1024/1024:7.1f} MB")


if __name__ == "__main__":
    main()
