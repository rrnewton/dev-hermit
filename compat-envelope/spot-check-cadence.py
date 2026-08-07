#!/usr/bin/env python3
"""Stack/heap SPOT-CHECK cadence for large tests — define it, date it, age it.

WHY THIS EXISTS. `comparison_tier` admits two qualifying values:

    full-stdout-info-stack-heap          stdout + INFO + stack + heap, EVERY run
    stdout-info-stack-heap-spot-check    stdout + INFO every run, stack/heap on a CADENCE

The second is honest only if the cadence is real. Measured 2026-08-07, zero of
2284 published rows carried EITHER tier, and no cadence, scheduler, or date
field existed anywhere in the tree. A tier whose cheaper standard is never
actually run is the same unqualified green under a new name, and a spot-check
with no date cannot be told apart from one that has gone stale.

THE THRESHOLD IS MEASURED, NOT PICKED. Stack/heap is a FLAT ~1.5x multiplier,
not a superlinear blowup: on one guest scaled over a 180x range of work the
ratio was 1.44 / 1.15 / 1.50 / 1.50. So the constraint is absolute added wall on
the large tail, which is arithmetic:

    completing cells                1184      33.6 min wall
    LARGE  (>= 5000 ms)               41      18.3 min   54.4% of wall
    SHORT  (< 5000 ms)              1143      15.3 min
    full tier on SHORT, every run:  +7.6 min   -- affordable
    full tier on LARGE, every run:  +9.2 min   -- this is what the cadence buys

THREE STATES, AND THE DISTINCTION IS THE WHOLE POINT:

    CURRENT  spot-checked within the cadence          -> may count as qualified
    STALE    spot-checked, but longer ago than the cadence -> MUST NOT count
    NEVER    no spot-check on record                  -> MUST NOT count

NEVER is not a subspecies of STALE. Every large cell starts at NEVER, and
reporting NEVER as merely "stale" would imply a measurement once existed. Both
are non-counting, but they are different claims and the ledger keeps them apart.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# --- the cadence -------------------------------------------------------------
# Interval: a large cell's stack/heap evidence is good for this long.
CADENCE_DAYS = 14
# Trigger: any of these invalidate every large cell's spot-check regardless of
# age, because they change what the hash is a hash OF. A pure interval would let
# a backend rewrite slip through on a 13-day-old receipt.
CADENCE_TRIGGERS = (
    "hermit_sha changed in a way that touches the backend under test",
    "reverie pin bump",
    "comparator/tier definition change",
)
# Measured knee: p95 of completing-cell duration is 3654 ms; 5000 ms captures the
# 41 cells carrying 54.4% of corpus wall while leaving 1143 short cells full-tier.
LARGE_MS = 5000

LEDGER = HERE / "spot-check-ledger.csv"
LEDGER_FIELDS = (
    "test_id", "test_mode", "backend", "duration_ms",
    "spot_check_utc", "hermit_sha", "result", "detail",
)

CURRENT, STALE, NEVER = "CURRENT", "STALE", "NEVER"


def _parse(ts: str):
    ts = (ts or "").strip()
    if not ts:
        return None
    try:
        d = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def age_state(spot_check_utc: str, now: _dt.datetime, cadence_days: int = CADENCE_DAYS):
    """(state, age_days). NEVER is distinct from STALE, deliberately."""
    when = _parse(spot_check_utc)
    if when is None:
        return NEVER, None
    age = (now - when).total_seconds() / 86400.0
    return (CURRENT if age <= cadence_days else STALE), round(age, 2)


def large_cells(scorecard: Path, large_ms: int = LARGE_MS):
    """Completing cells at or over the threshold.

    Timeouts are excluded on purpose: a timeout is already a NO-RESULT, and
    spot-checking one cannot produce stack/heap evidence about anything.
    """
    out = []
    with scorecard.open() as fh:
        for r in csv.DictReader(fh):
            if r.get("outcome") not in {"pass", "fail", "diverge"}:
                continue
            ms = r.get("duration_ms") or ""
            if not ms.isdigit() or int(ms) < large_ms:
                continue
            out.append(r)
    return out


def load_ledger(path: Path = LEDGER):
    if not path.exists():
        return {}
    with path.open() as fh:
        return {(r["test_id"], r["test_mode"], r["backend"]): r for r in csv.DictReader(fh)}


def write_ledger(rows, path: Path = LEDGER):
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(LEDGER_FIELDS), lineterminator="\n")
        w.writeheader()
        for r in sorted(rows, key=lambda x: (x["test_id"], x["backend"])):
            w.writerow({k: r.get(k, "") for k in LEDGER_FIELDS})


def report(scorecard: Path, now: _dt.datetime, cadence_days: int = CADENCE_DAYS):
    cells = large_cells(scorecard)
    led = load_ledger()
    counts = {CURRENT: 0, STALE: 0, NEVER: 0}
    rows = []
    for c in cells:
        k = (c["test_id"], c["test_mode"], c["backend"])
        rec = led.get(k, {})
        state, age = age_state(rec.get("spot_check_utc", ""), now, cadence_days)
        counts[state] += 1
        rows.append((state, age, k, c.get("duration_ms"), rec.get("result", "")))
    return cells, counts, rows


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scorecard", default=str(HERE / "fullcorpus-scorecard.csv"))
    p.add_argument("--cadence-days", type=int, default=CADENCE_DAYS)
    p.add_argument("--large-ms", type=int, default=LARGE_MS)
    p.add_argument("--now", default=None, help="ISO instant; defaults to real now")
    p.add_argument("--init-ledger", action="store_true",
                   help="seed every large cell at NEVER (no dates invented)")
    a = p.parse_args(argv)

    now = _parse(a.now) or _dt.datetime.now(_dt.timezone.utc)
    sc = Path(a.scorecard)
    if not sc.exists():
        print(f"spot-check-cadence: no scorecard at {sc}", file=sys.stderr)
        return 2

    if a.init_ledger:
        led = load_ledger()
        seeded = 0
        for c in large_cells(sc, a.large_ms):
            k = (c["test_id"], c["test_mode"], c["backend"])
            if k in led:
                continue
            led[k] = {"test_id": k[0], "test_mode": k[1], "backend": k[2],
                      "duration_ms": c.get("duration_ms", ""),
                      "spot_check_utc": "", "hermit_sha": "",
                      "result": "", "detail": "no spot-check on record"}
            seeded += 1
        write_ledger(led.values())
        print(f"seeded {seeded} large cell(s) at {NEVER}; ledger rows={len(led)}")

    cells, counts, rows = report(sc, now, a.cadence_days)
    total = len(cells)
    print(f"cadence: every {a.cadence_days} days, or on any trigger:")
    for t in CADENCE_TRIGGERS:
        print(f"  trigger: {t}")
    print(f"LARGE threshold: duration_ms >= {a.large_ms} (completing cells only; "
          f"timeouts excluded as already NO-RESULT)")
    print(f"large cells: {total}")
    for s in (CURRENT, STALE, NEVER):
        pct = (100.0 * counts[s] / total) if total else 0.0
        print(f"  {s:<8}{counts[s]:>5}  ({pct:5.1f}%)")
    assert counts[CURRENT] + counts[STALE] + counts[NEVER] == total, "counts must sum"
    print(f"  SUM {counts[CURRENT]}+{counts[STALE]}+{counts[NEVER]} = {total}  OK")
    print(f"\nQUALIFIABLE under spot-check tier right now: {counts[CURRENT]} of {total}")
    print("  STALE and NEVER are both non-counting, and are NOT merged: NEVER means no "
          "measurement ever existed, STALE means one existed and expired.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
