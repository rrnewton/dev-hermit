#!/usr/bin/env python3
"""Derive per-syscall cost and the two overhead components from measurements.csv.

WHY A SLOPE, NOT A DIVISION. Every arm pays a fixed startup: process exec for
native, sandbox boot for runsc, backend attach for Hermit, plus ~0.3 s for the
core-box helper's /proc/stat sampling. At N=100k that fixed cost is most of the
wall time for the fast arms, so `wall / N` measures startup, not syscalls. Two
points cancel it exactly:

    ns_per_syscall = (T(N2) - T(N1)) / (N2 - N1)

Reported as median over reps of the per-rep slope, so one contended rep cannot
drag the estimate. A NEGATIVE slope means the two points were within noise of
each other at that rep count and the cell is reported as noise-dominated rather
than as a number.

THE TWO COMPONENTS the task asks to separate:
  instrumentation   = exp1, single-threaded guest, K=1. Nobody has parallelism
                      to lose, so this is interception cost alone.
  sequentialization = exp2, 4-thread guest, unconstrained vs K=1. An
                      uninstrumented runtime spreads threads over cores; Hermit
                      serializes them. The ratio is what determinism costs on
                      top of instrumentation.
"""
from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
RAW = HERE / "raw" / "measurements.csv"


def load():
    rows = [r for r in csv.DictReader(RAW.open())]
    for r in rows:
        r["wall_s"] = float(r["wall_s"])
        r["n"] = int(r["n"])
        r["rep"] = int(r["rep"])
        r["ok"] = r["rc"] == "0"
    return rows


def median_or_none(xs):
    return statistics.median(xs) if xs else None


def fmt_ns(v):
    if v is None:
        return "no-result"
    if v < 0:
        return "noise"
    return f"{v:,.0f}" if v >= 100 else f"{v:.1f}"


def sig(x, n=3):
    """Significant figures, per the presentation rules (186.743x -> 187x)."""
    if x is None:
        return "n/a"
    if x >= 100:
        return f"{x:,.0f}"
    if x >= 10:
        return f"{x:.1f}"
    return f"{x:.2f}"


def main() -> int:
    rows = load()
    if not rows:
        print("REFUSED: no rows", file=sys.stderr)
        return 3

    # ---- experiment 1: instrumentation, K=1, two-point slope -----------------
    per_rep = defaultdict(dict)          # arm -> rep -> {n: wall}
    status = defaultdict(list)
    for r in rows:
        if r["experiment"] != "exp1":
            continue
        status[r["arm"]].append(r)
        if r["ok"]:
            per_rep[r["arm"]].setdefault(r["rep"], {})[r["n"]] = r["wall_s"]

    slopes = {}
    denom = {}
    for arm, reps in per_rep.items():
        vals = []
        for _rep, pts in reps.items():
            if 100000 in pts and 300000 in pts:
                vals.append((pts[300000] - pts[100000]) / 200000 * 1e9)
        slopes[arm] = median_or_none(vals)
        denom[arm] = len(vals)

    for arm in {r["arm"] for r in rows if r["experiment"] == "exp1"}:
        if arm not in slopes:
            slopes[arm], denom[arm] = None, 0

    base = slopes.get("native")
    print("== EXP1  instrumentation cost — single-threaded getpid, K=1 core box ==")
    print(f"   ns/syscall = median over reps of (T(300k)-T(100k))/200k; native anchor = {fmt_ns(base)} ns")
    print(f"   {'arm':18s} {'ns/syscall':>12s} {'x native':>10s} {'reps used':>10s}  status")
    for arm in sorted(slopes, key=lambda a: (slopes[a] is None, slopes[a] if slopes[a] is not None else 0)):
        v = slopes[arm]
        bad = [r for r in status[arm] if not r["ok"]]
        note = ""
        if bad:
            note = f"{len(bad)}/{len(status[arm])} runs {bad[0]['note'] or 'failed'}"
        ratio = (v / base) if (v and base and v > 0 and base > 0) else None
        print(f"   {arm:18s} {fmt_ns(v):>12s} {sig(ratio)+'x' if ratio else 'n/a':>10s} "
              f"{denom[arm]:>10d}  {note}")

    # ---- experiment 2: sequentialization, 4 threads, K=1 vs unconstrained ----
    print()
    print("== EXP2  sequentialization cost — 4-thread getpid, K=1 vs unconstrained ==")
    print("   Ratio = median(K=1 wall) / median(unconstrained wall). ~1.0 means the arm")
    print("   was already serial; >1 means it genuinely used the extra cores.")
    # The core-box helper sits in BOTH regimes (that is deliberate -- it makes its
    # cost common-mode instead of a fake handicap on K=1). But leaving it in
    # DILUTES the ratio toward 1: native's parallel run is ~0.01 s of guest under
    # ~0.41 s of helper, so the raw ratio understates the real effect by orders of
    # magnitude. Subtract the measured calibration median from each side.
    calib = defaultdict(list)
    for r in rows:
        if r["experiment"] == "calib" and r["ok"]:
            calib[r["regime"]].append(r["wall_s"])
    cal_k1 = median_or_none(calib.get("k1", [])) or 0.0
    cal_par = median_or_none(calib.get("par", [])) or 0.0
    cal_spread = (max(calib.get("k1", [0]) + calib.get("par", [0]))
                  - min(calib.get("k1", [0]) + calib.get("par", [0])))
    print(f"   helper calibration subtracted: K=1 {cal_k1:.3f} s, K=4 {cal_par:.3f} s "
          f"(spread {cal_spread:.3f} s over {len(calib.get('k1', []))+len(calib.get('par', []))} samples)")

    e2 = defaultdict(lambda: defaultdict(list))
    e2bad = defaultdict(list)
    for r in rows:
        if r["experiment"] != "exp2":
            continue
        (e2[r["arm"]][r["regime"]].append(r["wall_s"]) if r["ok"] else e2bad[r["arm"]].append(r))
    print(f"   {'arm':18s} {'K=1 s':>9s} {'K=4 s':>9s} {'guest K=1':>10s} {'guest K=4':>10s} "
          f"{'ratio':>8s} {'reps':>5s}  status")
    for arm in sorted(e2):
        k1 = median_or_none(e2[arm].get("k1", []))
        par = median_or_none(e2[arm].get("par", []))
        n = min(len(e2[arm].get("k1", [])), len(e2[arm].get("par", [])))
        note = f"{len(e2bad[arm])} no-result" if e2bad[arm] else ""
        g1 = (k1 - cal_k1) if k1 is not None else None
        gp = (par - cal_par) if par is not None else None
        ratio = None
        if g1 is not None and gp is not None and gp > 0 and g1 > 0:
            ratio = g1 / gp
            # a corrected value smaller than the calibration's own spread cannot
            # be distinguished from the instrument
            if gp < cal_spread or g1 < cal_spread:
                note = (note + " " if note else "") + "noise-dominated (guest time < helper spread)"
        print(f"   {arm:18s} {sig(k1):>9s} {sig(par):>9s} {sig(g1):>10s} {sig(gp):>10s} "
              f"{sig(ratio)+'x' if ratio else 'n/a':>8s} {n:>5d}  {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
