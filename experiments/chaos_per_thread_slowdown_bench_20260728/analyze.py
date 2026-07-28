#!/usr/bin/env python3
"""Summarize per-thread-slowdown benchmark: distinct schedules, hit-rate, and
seeds-to-first-reproduce vs the plain-chaos baseline signature.

Metrics (per config, over the N seeds):
  distinct  - # distinct output signatures (schedule-exploration diversity)
  hit_rate  - fraction of seeds whose signature != the baseline (seed-1 plain)
  stf_med   - median seeds-to-first-repro (first seed index whose sig != baseline),
              censored to 'never' if no seed differs
  stf_p90   - 90th percentile of the same
Baseline signature = the modal signature of chaos_plain (the "you didn't find
the race" schedule). A LOWER stf and HIGHER distinct/hit_rate is better.
"""
import csv
import statistics
from collections import Counter, defaultdict
from pathlib import Path

EXP = Path(__file__).resolve().parent
rows = list(csv.DictReader((EXP / "results.tsv").open(), delimiter="\t"))

by_cfg = defaultdict(list)
for r in rows:
    by_cfg[r["config"]].append(r)
for cfg in by_cfg:
    by_cfg[cfg].sort(key=lambda r: int(r["seed"]))

# Baseline = modal signature across chaos_plain (the un-flipped schedule).
base_sigs = [r["sig12"] for r in by_cfg.get("chaos_plain", [])]
baseline = Counter(base_sigs).most_common(1)[0][0] if base_sigs else None


def stf(seq_sigs):
    for i, s in enumerate(seq_sigs, start=1):
        if s != baseline:
            return i
    return None  # never


ORDER = [
    "chaos_plain",
    "chaos_pts_r10",
    "chaos_pts_r100",
    "chaos_plain_ts1e4",
    "chaos_pts_r10_ts1e4",
]
out = EXP / "summary.tsv"
lines = ["config\tN\tdistinct\thit_rate\tstf_med\tstf_p90\tmean_ms"]
table = ["| config | N | distinct | hit% | stf_med | stf_p90 | mean_ms |",
         "|---|---|---|---|---|---|---|"]
for cfg in [c for c in ORDER if c in by_cfg] + [c for c in by_cfg if c not in ORDER]:
    rs = by_cfg[cfg]
    sigs = [r["sig12"] for r in rs]
    n = len(sigs)
    distinct = len(set(sigs))
    hits = [1 if s != baseline else 0 for s in sigs]
    hit_rate = sum(hits) / n if n else 0.0
    # stf over the natural seed order 1..N; also a bootstrap-free simple estimate.
    first = stf(sigs)
    # per-shuffle stf via prefix scan of the observed order (single realization):
    # median/p90 estimated by scanning increasing seed prefixes' first-hit.
    firsts = []
    for start in range(n):
        rot = sigs[start:] + sigs[:start]
        f = next((i + 1 for i, s in enumerate(rot) if s != baseline), None)
        firsts.append(f if f is not None else n + 1)  # censor as N+1
    firsts_sorted = sorted(firsts)
    stf_med = statistics.median(firsts_sorted)
    p90i = min(len(firsts_sorted) - 1, int(round(0.9 * (len(firsts_sorted) - 1))))
    stf_p90 = firsts_sorted[p90i]
    mean_ms = statistics.mean(int(r["elapsed_ms"]) for r in rs if r["elapsed_ms"].isdigit())
    med_str = "never" if stf_med > n else str(int(stf_med))
    p90_str = "never" if stf_p90 > n else str(int(stf_p90))
    lines.append(f"{cfg}\t{n}\t{distinct}\t{hit_rate:.2f}\t{med_str}\t{p90_str}\t{mean_ms:.0f}")
    table.append(f"| {cfg} | {n} | {distinct} | {hit_rate*100:.0f}% | {med_str} | {p90_str} | {mean_ms:.0f} |")

out.write_text("\n".join(lines) + "\n")
(EXP / "TABLE.txt").write_text("\n".join(table) + "\n")
print(f"baseline sig (modal chaos_plain) = {baseline}")
print("\n".join(table))
