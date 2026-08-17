"""Print one version's section of the CHANGELOG, for the release body.

    py -3 tools/release_notes.py v0.2.4

The release workflow used `gh release create --generate-notes`, which lists
merged pull requests. This project has none, so it produced a body whose entire
contents were a link to the commit comparison -- and the download page said
nothing at all about what had changed, on the day the whole window was redrawn.

So the notes come from CHANGELOG.md, which is written for people rather than
derived from commit subjects. Failing is the point when a section is missing:
publishing an empty release is the thing this exists to stop, so an unknown tag
exits non-zero rather than printing nothing.
"""
import argparse
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")


def section(tag):
    """The body of the "## [x.y.z]" heading matching this tag."""
    version = tag.lstrip("v")
    text = io.open(CHANGELOG, encoding="utf-8").read()

    # Headings look like "## [0.2.4] - 2026-08-16". Take everything up to the
    # next "## " at the start of a line, or the link definitions at the end.
    pattern = (r"^## \[%s\][^\n]*\n(.*?)(?=^## \[|^\[[\d.]+\]: )"
               % re.escape(version))
    m = re.search(pattern, text, re.S | re.M)
    if not m:
        have = re.findall(r"^## \[([^\]]+)\]", text, re.M)
        sys.exit("no section for %s in CHANGELOG.md.\nIt has: %s"
                 % (version, ", ".join(have)))
    body = m.group(1).strip()
    if not body:
        sys.exit("the section for %s is empty" % version)
    return body


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tag", help="the release tag, e.g. v0.2.4")
    ap.add_argument("--out", help="write here instead of stdout")
    args = ap.parse_args()

    version = args.tag.lstrip("v")
    body = section(args.tag)
    body += ("\n\n---\n\n"
             "**Download:** "
             "[Kara-Setup-%s.exe]"
             "(https://github.com/bryramirezp/kara/releases/download/v%s/"
             "Kara-Setup-%s.exe) — Windows 10 and 11.\n\n"
             "Windows will show a blue \"Windows protected your PC\" box, "
             "because the installer is not signed. Click **More info**, then "
             "**Run anyway**. The "
             "[install guide](https://bryramirezp.github.io/kara/install.html)"
             " walks through it and shows how to check the file's SHA256 "
             "against `SHA256SUMS.txt` below.\n"
             % (version, version, version))

    if args.out:
        io.open(args.out, "w", encoding="utf-8", newline="\n").write(body)
        print("wrote %s" % args.out)
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
