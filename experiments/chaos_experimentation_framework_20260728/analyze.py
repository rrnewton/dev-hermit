#!/usr/bin/env python3
"""Analyze the chaos experimentation grid: per-config schedule-exploration metrics.

For each parameter config (outer loop) over its inner seed sweep, compute:
  n              seeds run
  distinct       # distinct guest-output signatures (schedule diversity)
  div_rate       distinct/n (new-schedule yield per seed)
  hit_rate       fraction of seeds whose signature != blind baseline (plain/seed1)
  stf_*          seeds-to-first-repro (sig != baseline): bootstrap mean/median/p90
  s2k            seeds-to-discover-K-distinct schedules (coupon-ish), K=min(5,distinct)
  mean_scan      mean 'Scanning:' progress lines (preemption-density proxy)

Ranks configs by seeds-to-first-repro (asc) and by diversity (desc) -> the
parameter regimes that reproduce distinct schedules fastest.
"""
import csv, os, statistics, random, collections

random.seed(1234)
HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results.tsv")
SUM = os.path.join(HERE, "summary.tsv")
TRIALS = 5000

rows = list(csv.DictReader(open(RES), delimiter="\t"))
by = collections.OrderedDict()
for r in rows:
    by.setdefault(r["config"], []).append(r)

# blind baseline = plain/seed1 signature (the schedule a non-chaos user gets)
base = None
for r in rows:
    if r["config"] == "plain" and r["seed"] == "1":
        base = r["sig12"]
if base is None:  # fallback: strict/seed1
    for r in rows:
        if r["config"] == "strict" and r["seed"] == "1":
            base = r["sig12"]

def stf(hits):
    n = len(hits); p = sum(hits)/n if n else 0.0
    if p == 0.0:
        return p, None, None, None
    idx = list(range(n)); firsts = []
    for _ in range(TRIALS):
        random.shuffle(idx)
        firsts.append(next((i+1 for i in range(n) if hits[idx[i]]), n+1))
    return p, statistics.mean(firsts), statistics.median(firsts), sorted(firsts)[int(0.9*len(firsts))-1]

def s2k(sigs, K):
    if len(set(sigs)) < K:
        return None
    n = len(sigs); idx = list(range(n)); outs = []
    for _ in range(TRIALS):
        random.shuffle(idx); seen = set(); k = 0
        for i in idx:
            k += 1; seen.add(sigs[i])
            if len(seen) >= K:
                break
        outs.append(k if len(seen) >= K else n+1)
    return statistics.median(outs)

def fmt(x):
    if x is None: return "never"
    if isinstance(x, float): return f"{x:.2f}"
    return str(x)

recs = []
for cfg, rs in by.items():
    sigs = [r["sig12"] for r in rs]
    scans = [int(r["scanlines"]) for r in rs]
    n = len(sigs); distinct = len(set(sigs))
    hits = [s != base for s in sigs]
    p, mean, med, p90 = stf(hits)
    K = min(5, distinct)
    recs.append({
        "config": cfg, "n": n, "distinct": distinct,
        "div_rate": distinct/n, "hit_rate": p,
        "stf_mean": mean, "stf_median": med, "stf_p90": p90,
        "s2K": s2k(sigs, K), "K": K,
        "mean_scan": statistics.mean(scans),
    })

# write summary.tsv
with open(SUM, "w") as f:
    w = csv.writer(f, delimiter="\t")
    w.writerow(["config","n","distinct","div_rate","hit_rate",
                "stf_mean","stf_median","stf_p90","s2K","K","mean_scan"])
    for r in recs:
        w.writerow([r["config"], r["n"], r["distinct"], f"{r['div_rate']:.3f}",
                    f"{r['hit_rate']:.3f}", fmt(r["stf_mean"]), fmt(r["stf_median"]),
                    fmt(r["stf_p90"]), fmt(r["s2K"]), r["K"], f"{r['mean_scan']:.2f}"])

# rendered table (grid order)
print(f"\nCHAOS EXPERIMENTATION FRAMEWORK  (blind baseline sig = plain/seed1 = {base}; "
      f"N={recs[0]['n']} seeds/config; bootstrap TRIALS={TRIALS})\n")
hdr = f"{'config':<24} {'distinct':>8} {'div%':>6} {'hit%':>6} {'stf_med':>8} {'stf_p90':>8} {'s2K(med)':>9} {'K':>2} {'scan':>5}"
print(hdr); print("-"*len(hdr))
for r in recs:
    print(f"{r['config']:<24} {r['distinct']:>8} {r['div_rate']*100:>5.0f}% {r['hit_rate']*100:>5.0f}% "
          f"{fmt(r['stf_median']):>8} {fmt(r['stf_p90']):>8} {fmt(r['s2K']):>9} {r['K']:>2} {r['mean_scan']:>5.1f}")

# rankings
def key_stf(r):
    return (r["stf_median"] if r["stf_median"] is not None else 1e9, -r["div_rate"])
print("\nTOP 5 configs by seeds-to-first-repro (then diversity):")
for r in sorted(recs, key=key_stf)[:5]:
    print(f"  {r['config']:<24} stf_med={fmt(r['stf_median'])} hit={r['hit_rate']*100:.0f}% div={r['div_rate']*100:.0f}% distinct={r['distinct']}")
print("\nTOP 5 configs by schedule diversity (distinct/n):")
for r in sorted(recs, key=lambda r: -r["div_rate"])[:5]:
    print(f"  {r['config']:<24} div={r['div_rate']*100:.0f}% distinct={r['distinct']} scan={r['mean_scan']:.1f} hit={r['hit_rate']*100:.0f}%")
print(f"\nsummary -> {SUM}")
