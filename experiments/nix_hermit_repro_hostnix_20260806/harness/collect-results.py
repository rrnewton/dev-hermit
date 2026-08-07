#!/usr/bin/env python3
"""collect-results.py — gather every oracle row emitted during the run into one
normalized results.csv.

Rows are produced by harness/canonical-nrep.sh in three places: appended to
results.csv by screen-batch.sh, and printed to logs/*.out / logs/*.row by the
standalone and dose-sweep runners. This walks all of them, normalizes the
schema, and writes a single deduplicated table.

SCHEMA NOTE: early rows in this run predate the `dose` column (10 fields). They
were all produced with the SUPERSEDED `--no-namespace` mode, so they are
backfilled with that dose and marked `superseded_mode=1`. Rows with 11 fields
carry their own dose.

Usage: collect-results.py [experiment_dir]
"""
import csv
import io
import os
import re
import sys

HDR = ["label", "target_expr", "mode", "dose", "drv", "n", "distinct",
       "verdict", "hashes", "wall_s", "notes", "superseded_mode"]

LEGACY_HERMIT_DOSE = "run --no-namespace --no-rcb-time --max-timeslice disabled +setarch-R"

VERDICTS = {"reproducible", "NONDETERMINISTIC", "INCONCLUSIVE", "error",
            "build-fail", "timeout", "disk-guard"}


def parse_row(line):
    line = line.strip()
    # Strip the screen-batch prefix.
    line = re.sub(r"^(NATIVE|HERMIT)\s+", "", line)
    if not line or line.startswith("#") or line.startswith("["):
        return None
    try:
        fields = next(csv.reader(io.StringIO(line)))
    except Exception:
        return None
    if len(fields) == 11:
        superseded = "0"
    elif len(fields) == 10:
        # legacy: label,expr,mode,drv,n,distinct,verdict,hashes,wall,notes
        mode = fields[2]
        dose = "native" if mode == "native" else LEGACY_HERMIT_DOSE
        fields = fields[:3] + [dose] + fields[3:]
        superseded = "1" if mode != "native" else "0"
    else:
        return None
    if fields[2] not in ("native", "hermit"):
        return None
    if fields[7] not in VERDICTS:
        return None
    return fields + [superseded]


def main():
    exp = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    sources = [os.path.join(exp, "results.csv")]
    logs = os.path.join(exp, "logs")
    if os.path.isdir(logs):
        for f in sorted(os.listdir(logs)):
            if f.endswith((".out", ".row")):
                sources.append(os.path.join(logs, f))

    # One row per configuration (label, mode, dose, drv). A configuration can
    # appear several times because a first attempt failed and was retried; keep
    # the MOST INFORMATIVE attempt -- a real verdict beats an error, and among
    # real verdicts the one with the largest N wins. Keeping the first would
    # silently report a retried-and-succeeded screen as an error.
    def rank(r):
        real = r[7] in ("reproducible", "NONDETERMINISTIC")
        try:
            n = int(r[5])
        except ValueError:
            n = 0
        return (1 if real else 0, n)

    best = {}
    for path in sources:
        if not os.path.exists(path):
            continue
        with open(path, errors="replace") as fh:
            for line in fh:
                r = parse_row(line)
                if r is None:
                    continue
                key = (r[0], r[2], r[3], r[4])
                if key not in best or rank(r) > rank(best[key]):
                    best[key] = r

    rows = sorted(best.values(), key=lambda r: (r[0], r[2], r[3]))
    out = os.path.join(exp, "results.csv")
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HDR)
        w.writerows(rows)
    print(f"wrote {out}: {len(rows)} rows")


if __name__ == "__main__":
    main()
