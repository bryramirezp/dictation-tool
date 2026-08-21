"""Read trace.jsonl and say where the time went.

    py -3 tools/trace_report.py                        this machine's log
    py -3 tools/trace_report.py path\\to\\dir           every log under a directory
    py -3 tools/trace_report.py Kara-diagnostics-*.zip  a zip somebody sent back

The question this answers is the one the raw file cannot: pressing the key takes
eight milliseconds on one machine and two seconds on another, and the totals
alone do not say which step is responsible. So it breaks each cycle into its
steps, reports the median and the worst case per machine, and names the step
that accounts for most of the wait.

Medians, not averages: one cold start after the laptop wakes up would drag an
average somewhere misleading and hide the number that describes normal use.
"""
import argparse
import collections
import glob
import io
import json
import os
import statistics
import sys
import zipfile

# Everything between the key going down and the window saying LISTENING. These
# are the ones that decide whether the app feels instant.
OPEN_STEPS = ["lock_wait", "resolve_device", "query_native_rate",
              "stream_ctor", "stream_start"]
LATER = ["model_lock_wait", "transcribe", "paste"]

DEFAULT = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                       "Kara", "trace.jsonl")


def _parse(text, label, rows):
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            print("  skipped %s:%d, not JSON" % (label, n), file=sys.stderr)


def load(target):
    """Read every cycle under a path: a file, a directory, or a diagnostics zip.

    The zip is the shape this actually arrives in. A beta tester presses Export
    diagnostics and sends the file over chat, and asking them to unzip it first
    is one more step for no reason -- the zip is right there and its layout is
    known.
    """
    if os.path.isdir(target):
        paths = sorted(glob.glob(os.path.join(target, "**", "*.jsonl"), recursive=True)
                       + glob.glob(os.path.join(target, "**", "*.zip"), recursive=True))
    else:
        paths = [target]

    rows = []
    for p in paths:
        if not os.path.exists(p):
            continue
        if p.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(p) as z:
                    for name in z.namelist():
                        if name.endswith(".jsonl"):
                            _parse(z.read(name).decode("utf-8", "replace"),
                                   "%s!%s" % (os.path.basename(p), name), rows)
            except (zipfile.BadZipFile, OSError) as e:
                print("  skipped %s: %s" % (p, e), file=sys.stderr)
            continue
        with io.open(p, encoding="utf-8") as f:
            _parse(f.read(), p, rows)
    return rows, paths


def stat(values):
    if not values:
        return None
    return (statistics.median(values), max(values), len(values))


def report(rows):
    by_machine = collections.defaultdict(list)
    for r in rows:
        by_machine[r.get("machine", "?")].append(r)

    for machine in sorted(by_machine):
        rs = by_machine[machine]
        print("\n" + "=" * 74)
        print("%s   %d cycles   version %s" % (
            machine, len(rs), ", ".join(sorted({r.get("version", "?") for r in rs}))))

        mics = collections.Counter(
            "%s  via %s" % (r.get("mic", "?"), r.get("hostapi", "?")) for r in rs)
        for name, n in mics.most_common():
            print("  mic: %s   (%d)" % (name, n))

        # Which engine actually ran. Worth its own line because the two reports
        # that started all this were both read as model quality problems, and
        # both machines turned out to be on the processor.
        setups = collections.Counter(
            "device=%s threads=%s beam=%s" % (r.get("device", "?"),
                                              r.get("threads", "?"),
                                              r.get("beam", "?")) for r in rs)
        for name, n in setups.most_common():
            print("  ran: %s   (%d)" % (name, n))

        print("=" * 74)
        print("%-24s %9s %9s %7s" % ("step", "median ms", "worst ms", "n"))
        print("-" * 74)

        totals = {}
        for step in OPEN_STEPS:
            s = stat([r["ms"][step] for r in rs if step in r.get("ms", {})])
            if s:
                totals[step] = s[0]
                print("%-24s %9.1f %9.1f %7d" % (step, s[0], s[1], s[2]))

        s = stat([r["ms"]["press_to_listening"] for r in rs
                  if "press_to_listening" in r.get("ms", {})])
        if s:
            print("-" * 74)
            print("%-24s %9.1f %9.1f %7d   <- what the user feels"
                  % ("PRESS TO LISTENING", s[0], s[1], s[2]))
            if totals:
                worst = max(totals, key=totals.get)
                share = 100.0 * totals[worst] / s[0] if s[0] else 0
                print("%-24s %s, %.0f%% of it" % ("  mostly:", worst, share))
            if s[0] > 300:
                print("%-24s %s" % ("  verdict:", "slow enough to feel wrong"))
            elif s[0] > 120:
                print("%-24s %s" % ("  verdict:", "noticeable"))
            else:
                print("%-24s %s" % ("  verdict:", "feels instant"))

        print("-" * 74)
        for step in LATER:
            s = stat([r["ms"][step] for r in rs if step in r.get("ms", {})])
            if s:
                print("%-24s %9.1f %9.1f %7d" % (step, s[0], s[1], s[2]))

        # Audio that never made it in. If the key was held for three seconds and
        # only two arrived, the microphone spent one second waking up and the
        # first word of the sentence is gone.
        lost = [r["ms"]["hold"] / 1000.0 - r["audio_s"]
                for r in rs
                if "hold" in r.get("ms", {}) and "audio_s" in r]
        s = stat([x for x in lost if x > 0])
        if s:
            print("-" * 74)
            print("%-24s %9.2f %9.2f %7d   (seconds of speech lost at the start)"
                  % ("hold minus audio", s[0], s[1], s[2]))

        errs = collections.Counter(r["err"] for r in rs if r.get("err"))
        if errs:
            print("-" * 74)
            for name, n in errs.most_common():
                print("  %-30s %d" % (name, n))

        durs = [r["audio_s"] for r in rs if "audio_s" in r]
        if durs:
            print("-" * 74)
            print("  utterance length: median %.1fs, longest %.1fs"
                  % (statistics.median(durs), max(durs)))
        chars = [r["chars"] for r in rs if r.get("chars")]
        if chars:
            print("  characters typed: median %d, most %d"
                  % (statistics.median(chars), max(chars)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("target", nargs="?", default=DEFAULT,
                    help="a trace.jsonl, a diagnostics zip, or a directory of "
                         "either (default: this machine's)")
    args = ap.parse_args()

    rows, paths = load(args.target)
    if not rows:
        print("nothing to read at %s" % args.target)
        print("Kara writes this file on its own now, so an empty one means the "
              "app has not transcribed anything yet on that machine.")
        return 1
    print("read %d cycles from %d file(s)" % (len(rows), len(paths)))
    report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
