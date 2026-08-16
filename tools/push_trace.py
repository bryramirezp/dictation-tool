"""Copy this machine's trace into a private repo and push it.

    py -3 tools/push_trace.py C:\\path\\to\\kara-traces

Two laptops, one place to read them from. Each machine writes to its own file,
named after itself, so two machines pushing the same day cannot overwrite each
other and `git pull` never has to merge inside a line.

Nothing here runs from the app. Kara has no network code and this script is not
imported by it -- it is run by hand, by the developer, when there is something
worth looking at. That is the whole reason the app can keep saying it does not
phone home.

Before the first run, make the private repo and clone it:

    gh repo create kara-traces --private
    git clone https://github.com/<you>/kara-traces C:\\path\\to\\kara-traces
"""
import argparse
import io
import json
import os
import platform
import shutil
import subprocess
import sys

SRC = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                   "Kara", "trace.jsonl")


def run(cmd, cwd):
    print("  $", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, check=True)


def summarise(path):
    """A quick count, so the commit message says what it is carrying."""
    n, machines = 0, set()
    try:
        with io.open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                n += 1
                try:
                    machines.add(json.loads(line).get("machine", "?"))
                except ValueError:
                    pass
    except OSError:
        pass
    return n, sorted(machines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("repo", help="path to a clone of the private traces repo")
    ap.add_argument("--machine", default=os.environ.get("KARA_MACHINE") or platform.node(),
                    help="name this machine's file (default: KARA_MACHINE, else hostname)")
    ap.add_argument("--source", default=SRC, help="trace file to copy")
    ap.add_argument("--dry-run", action="store_true", help="copy nothing, push nothing")
    args = ap.parse_args()

    if not os.path.exists(args.source):
        sys.exit("no trace at %s\nTurn it on with:  setx KARA_DEBUG_LOG 1" % args.source)
    if not os.path.isdir(os.path.join(args.repo, ".git")):
        sys.exit("%s is not a git clone" % args.repo)

    n, machines = summarise(args.source)
    if n == 0:
        sys.exit("the trace is empty, nothing to push")

    dest_dir = os.path.join(args.repo, "traces")
    dest = os.path.join(dest_dir, "%s.jsonl" % args.machine)
    print("%d cycles from %s" % (n, ", ".join(machines)))
    print("  %s\n  -> %s" % (args.source, dest))

    if args.dry_run:
        print("\n--dry-run, stopping here")
        return 0

    os.makedirs(dest_dir, exist_ok=True)
    # Replace rather than append: the source file is the full history for this
    # machine already, so appending would duplicate every earlier cycle.
    shutil.copyfile(args.source, dest)

    run(["git", "add", os.path.relpath(dest, args.repo)], args.repo)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=args.repo)
    if changed.returncode == 0:
        print("\nnothing new since the last push")
        return 0
    run(["git", "commit", "-m", "%s: %d cycles" % (args.machine, n)], args.repo)
    run(["git", "push"], args.repo)
    print("\npushed. Read them all with:")
    print("  py -3 tools/trace_report.py %s" % dest_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
