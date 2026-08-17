"""Write the facts that change every release into the files that quote them.

    py -3 tools/stamp_docs.py           fix the files in place
    py -3 tools/stamp_docs.py --check   fail if any of them is stale

The version lives in exactly one place, __version__ in kara.py, and the
installer's size is whatever the installer happens to weigh. Everywhere else
that mentions either is a copy, and copies go stale quietly -- the JSON-LD on
the website sat at 0.1.0 for three releases before anyone noticed. This walks
the copies and rewrites them from the originals.

It is idempotent: running it twice changes nothing the second time, which is
what lets --check work by running it against a scratch copy and comparing.

Not everything here is derived. The speech model's size is a property of the
model, not of a build, so it is a constant below and only moves when the
default model does.

packaging/build.py calls this after it builds, so a normal release picks the
new numbers up on its own.
"""
import argparse
import datetime
import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The installer the website links to. Its size is only knowable after a build,
# so when it is missing the size rules below sit out this run rather than
# guessing -- checking out the repo and running --check must not fail just
# because nothing has been built yet.
INSTALLER = os.path.join(ROOT, "dist", "Kara-Setup.exe")

SITE = "https://bryramirezp.github.io/kara/"


def version():
    src = io.open(os.path.join(ROOT, "kara.py"), encoding="utf-8").read()
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', src, re.M)
    if not m:
        sys.exit("no __version__ found in kara.py")
    return m.group(1)


def installer_mb():
    """Megabytes, rounded the way a person would say it out loud."""
    if not os.path.exists(INSTALLER):
        return None
    return round(os.path.getsize(INSTALLER) / 1_000_000)


def last_commit_date(rel_path):
    """The date git last recorded a change to this file, or today if it is new.

    Using the commit date rather than today's keeps <lastmod> honest: a sitemap
    that claims every page changed today teaches crawlers to ignore the field.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", rel_path],
            cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.CalledProcessError):
        pass
    return datetime.date.today().isoformat()


def rules(ver, mb):
    """(file, pattern, replacement) triples, applied in order.

    Each pattern is anchored on something structural -- a JSON key, an XML tag,
    a preprocessor define -- rather than on prose, so rewording a sentence never
    silently stops a rule from matching. A rule that matches nothing is an
    error, not a shrug; see main().
    """
    r = [
        # ── The version ──────────────────────────────────────────────────────
        ("docs/index.html",
         r'("softwareVersion":\s*")[^"]*(")',
         r"\g<1>%s\g<2>" % ver),

        # Only the hand-run fallback: build.py passes /DAppVersion, which wins.
        ("packaging/installer.iss",
         r'(#define AppVersion ")[^"]*(")',
         r"\g<1>%s\g<2>" % ver),
    ]

    # ── The download links ───────────────────────────────────────────────────
    # These used to point at releases/latest/download/Kara-Setup.exe, a copy of
    # the installer under a name that never changes, so the button never needed
    # editing. The cost was the filename: everybody downloaded "Kara-Setup.exe"
    # with no clue which version it was, and a Downloads folder full of them is
    # indistinguishable.
    #
    # So the links carry the version now, and keeping them working is this
    # script's job -- which is only safe because the release workflow runs
    # --check before it builds, and refuses to publish a release whose download
    # button points at the previous one.
    # The character classes exclude whitespace and a closing paren, not just a
    # quote. Markdown does not quote its URLs -- they sit inside ](...) -- and a
    # class of "anything but a quote" matches newlines, so the first attempt ran
    # from the first link in the README to the last ".exe" in the file and ate
    # the thirty-four lines in between.
    URL = (r"(https://github\.com/bryramirezp/kara/releases/download/)"
           r"v[^/\s\"')]+(/Kara-Setup-)[^\s\"')]+(\.exe)")
    for rel in ("README.md", "docs/index.html", "docs/install.html"):
        r.append((rel, URL, r"\g<1>v%s\g<2>%s\g<3>" % (ver, ver)))

    if mb is not None:
        size = "%d MB" % mb
        r += [
            # ── The installer's size, as quoted in prose ──────────────────────
            # Marked spans, because the surrounding sentence is prose and will
            # be rewritten; the markers are invisible in HTML and in Markdown.
            ("README.md",
             r"(<!--dl-size-->)[^<]*(<!--/dl-size-->)",
             r"\g<1>%s\g<2>" % size),
            ("docs/index.html",
             r"(<!--dl-size-->)[^<]*(<!--/dl-size-->)",
             r"\g<1>%s\g<2>" % size),
            ("docs/install.html",
             r"(<!--dl-size-->)[^<]*(<!--/dl-size-->)",
             r"\g<1>%s\g<2>" % size),
            # JSON-LD cannot carry an HTML comment, so this one is anchored on
            # the sentence's own shape inside the string.
            ("docs/install.html",
             r"(It is about )\d+ MB(\.)",
             r"\g<1>%s\g<2>" % size),
        ]

    # ── The sitemap ─────────────────────────────────────────────────────────
    for page, src in (("", "docs/index.html"), ("install.html", "docs/install.html")):
        r.append((
            "docs/sitemap.xml",
            r"(<loc>%s</loc>\s*<lastmod>)[^<]*(</lastmod>)" % re.escape(SITE + page),
            r"\g<1>%s\g<2>" % last_commit_date(src),
        ))

    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if a file would change, and write nothing")
    args = ap.parse_args()

    ver = version()
    mb  = installer_mb()
    print("Kara %s%s" % (ver, "" if mb is None else ", installer %d MB" % mb))
    if mb is None:
        print("  (no dist/Kara-Setup.exe -- leaving the size alone)")

    pending = {}
    for rel, pattern, repl in rules(ver, mb):
        path = os.path.join(ROOT, rel)
        text = pending.get(rel)
        if text is None:
            text = io.open(path, encoding="utf-8").read()
        new, n = re.subn(pattern, repl, text)
        if n == 0:
            # Silence here is the failure mode this whole script exists to
            # prevent, so it is loud instead.
            sys.exit("no match in %s for /%s/\n"
                     "The file was reworded past its anchor. Fix the rule or "
                     "put the marker back." % (rel, pattern))
        pending[rel] = new

    stale = []
    for rel, new in sorted(pending.items()):
        path = os.path.join(ROOT, rel)
        if io.open(path, encoding="utf-8").read() == new:
            continue
        stale.append(rel)
        if not args.check:
            io.open(path, "w", encoding="utf-8", newline="\n").write(new)

    if not stale:
        print("  everything already current")
        return 0

    if args.check:
        print("\nstale, and not rewritten because of --check:")
        for rel in stale:
            print("  " + rel)
        print("\nRun:  py -3 tools/stamp_docs.py")
        return 1

    print("\nrewritten:")
    for rel in stale:
        print("  " + rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
