"""Build everything a release ships.

    py -3 packaging/build.py           the ordinary installer
    py -3 packaging/build.py --gpu     the NVIDIA one, about 570 MB

Leaves in dist/:
    Kara-Setup-<version>.exe          the installer
    Kara-<version>-portable.zip       the same app, unzip and run
    SHA256SUMS.txt                    hashes of both

and with --gpu, the same three with -GPU in the name.

The version is read from __version__ in kara.py, which is the only
place it is written down.

The two builds cannot share a dist/Kara directory, so run them one at a time;
each starts by clearing what the other left.
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
APP  = os.path.join(DIST, "Kara")

# winget installs Inno Setup per user by default, the GitHub runners get it
# machine-wide through choco, and either way someone may just have it on PATH.
ISCC_CANDIDATES = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def version():
    src = open(os.path.join(ROOT, "kara.py"), encoding="utf-8").read()
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', src, re.M)
    if not m:
        sys.exit("no __version__ found in kara.py")
    return m.group(1)


def run(cmd, env=None):
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, cwd=ROOT, check=True, env=env)


def build_exe(gpu):
    print("== PyInstaller ==")
    env = dict(os.environ)
    if gpu:
        env["KARA_GPU"] = "1"
    else:
        # Explicitly cleared rather than merely absent: a shell where it was set
        # for an earlier build would otherwise produce the 570 MB file under
        # the small one's name, and nothing downstream would notice.
        env.pop("KARA_GPU", None)
    # The previous flavour's files are still in there and PyInstaller does not
    # remove what it no longer produces.
    shutil.rmtree(APP, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm",
         os.path.join("packaging", "kara.spec")], env=env)
    exe = os.path.join(APP, "Kara.exe")
    if not os.path.exists(exe):
        sys.exit(f"expected {exe}")


def build_installer(ver, gpu):
    print("== Inno Setup ==")
    iscc = next((p for p in ISCC_CANDIDATES if p and os.path.exists(p)),
                shutil.which("ISCC"))
    if not iscc:
        sys.exit("Inno Setup 6 not found. winget install JRSoftware.InnoSetup")
    cmd = [iscc, f"/DAppVersion={ver}"]
    if gpu:
        cmd.append("/DGpuBuild=1")
    run(cmd + [os.path.join("packaging", "installer.iss")])


def stable_alias(setup, gpu):
    """A copy of the installer under a name that never changes.

    GitHub only serves /releases/latest/download/<name> for an exact file name,
    so a versioned one stops working the moment a new release goes out. This
    copy is what the download buttons on the website point at, so they keep
    working without editing the page on every release.
    """
    alias = os.path.join(DIST, "Kara-Setup-GPU.exe" if gpu else "Kara-Setup.exe")
    shutil.copy2(setup, alias)
    return alias


def build_zip(ver, gpu):
    print("== portable zip ==")
    tag = "-GPU" if gpu else ""
    out = os.path.join(DIST, f"Kara-{ver}{tag}-portable.zip")
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for folder, _, files in os.walk(APP):
            for name in files:
                full = os.path.join(folder, name)
                # Keep the folder in the archive: unzipping 400 loose files into
                # whatever directory the user happened to be in is unkind.
                z.write(full, os.path.join(
                    "Kara", os.path.relpath(full, APP)))
    return out


def write_hashes(ver, paths, gpu):
    print("== SHA256 ==")
    lines = []
    for p in paths:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        lines.append(f"{h.hexdigest()}  {os.path.basename(p)}")
        print("  " + lines[-1])
    out = os.path.join(DIST, "SHA256SUMS-GPU.txt" if gpu else "SHA256SUMS.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out


def stamp_docs():
    """Write the new version and installer size into the docs that quote them.

    Runs last, because the size is only knowable once the installer exists.
    """
    print("== docs ==")
    run([sys.executable, os.path.join("tools", "stamp_docs.py")])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gpu", action="store_true",
                    help="carry the CUDA libraries: 570 MB to download, "
                         "1.2 GB installed")
    args = ap.parse_args()
    gpu = args.gpu

    ver = version()
    tag = "-GPU" if gpu else ""
    print(f"Kara {ver}{tag}\n")
    build_exe(gpu)
    build_installer(ver, gpu)
    zip_path = build_zip(ver, gpu)
    setup = os.path.join(DIST, f"Kara-Setup{tag}-{ver}.exe")
    if not os.path.exists(setup):
        sys.exit(f"expected {setup}")
    alias = stable_alias(setup, gpu)
    # The alias is byte for byte the versioned file, so one hash covers both.
    write_hashes(ver, [setup, zip_path], gpu)
    print(f"  ({os.path.basename(alias)} is a copy of {os.path.basename(setup)})")
    # The page quotes both sizes, but only once both files exist. Stamping
    # here, from a run that has built one of the two, would write whichever it
    # just made into both places.
    if not gpu:
        stamp_docs()

    print("\n== ready ==")
    for name in sorted(os.listdir(DIST)):
        p = os.path.join(DIST, name)
        if os.path.isfile(p):
            print(f"  {name:44s} {os.path.getsize(p)/1024/1024:7.1f} MB")
    return alias


if __name__ == "__main__":
    main()
