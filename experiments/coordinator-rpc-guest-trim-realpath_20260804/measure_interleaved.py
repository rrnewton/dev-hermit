#!/usr/bin/env python3
"""Real-path A/B, INTERLEAVED to defeat load drift on this shared box.

Prior sequential runs put det_before and det_after ~90s apart; a contention
spike in the before window produced an implausible 300us/hop "delta" that is
mostly load, not the trim. Here each rep runs before(N) and after(N)
back-to-back (same load window) so the per-rep A/B ratio is drift-immune; we
report the median of per-rep paired ratios and paired deltas. Slope over two N
subtracts fixed hermit startup. K=2 (RPC livelocks at K=1)."""
import subprocess, statistics as st, tempfile, os, re, json

ROOT = "/home/newton/work/dev-hermit"
HERMIT = f"{ROOT}/hermit/target/release/hermit"
SO = {"before": f"{ROOT}/hermit/target/release/libreverie_liteinst.so",
      "after": f"{ROOT}/scratch/coord-rpc-fixes/ab-target/release/libreverie_liteinst.so"}
YL = f"{ROOT}/experiments/coordinator-rpc-guest-trim-realpath_20260804/src/yield_loop"
BOX = f"{ROOT}/scratch/run-on-k-free-cores.py"
REPS = 9
K = 2
NS = (10000, 50000)


def parse_wall(path):
    txt = open(path).read()
    w = re.search(r"Elapsed .*: ([\d:.]+)", txt).group(1)
    p = w.split(":")
    return float(p[-1]) + (float(p[-2]) * 60 if len(p) > 1 else 0)


def one(variant, N):
    tf = tempfile.mktemp()
    cmd = ["python3", BOX, str(K), "--", "/usr/bin/time", "-v", "-o", tf,
           "env", f"HERMIT_LITEINST_RUNTIME={SO[variant]}", HERMIT, "run",
           "--backend", "liteinst", "--", YL, str(N)]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=180)
        return parse_wall(tf)
    except Exception:
        return None
    finally:
        if os.path.exists(tf):
            os.remove(tf)


# Collect interleaved paired samples: per rep, per N, run before then after adjacently.
samples = {("before", n): [] for n in NS}
samples.update({("after", n): [] for n in NS})
paired = {n: [] for n in NS}  # per-rep (before_wall, after_wall) at same load window
for rep in range(REPS):
    for N in NS:
        b = one("before", N)
        a = one("after", N)
        if b is not None:
            samples[("before", N)].append(b)
        if a is not None:
            samples[("after", N)].append(a)
        if b is not None and a is not None:
            paired[N].append((b, a))
    print(f"rep {rep}: " + " | ".join(
        f"N={N} before={paired[N][-1][0]:.2f} after={paired[N][-1][1]:.2f}"
        for N in NS if paired[N]), flush=True)

med = {k: st.median(v) for k, v in samples.items() if v}
# per-hop wall us via median slope of each variant (startup subtracted)
dN = NS[1] - NS[0]
hop = {v: (med[(v, NS[1])] - med[(v, NS[0])]) / dN * 1e6 for v in ("before", "after")}
# Drift-immune: median of per-rep paired slope deltas.
# Pair rep-i N=hi against rep-i N=lo is not same-window; instead report the
# median paired *ratio* at the large N (dominated by hop cost, startup ~5-8%).
ratios_hi = [a / b for (b, a) in paired[NS[1]] if b]
delta_hi_us = [(b - a) / NS[1] * 1e6 for (b, a) in paired[NS[1]]]

out = {
    "reps": REPS, "K": K, "ns": NS,
    "median_wall_s": {f"{v}_{n}": med[(v, n)] for (v, n) in med},
    "hop_us_slope": hop,
    "hop_slope_reduction_pct": (hop["before"] - hop["after"]) / hop["before"] * 100,
    "paired_ratio_hi_after_over_before_median": st.median(ratios_hi),
    "paired_ratio_hi_iqr": [min(ratios_hi), st.median(ratios_hi), max(ratios_hi)],
    "paired_reduction_hi_pct_median": (1 - st.median(ratios_hi)) * 100,
    "paired_delta_hi_us_per_hop_median": st.median(delta_hi_us),
}
print("\n=== slope per-hop wall us (startup-subtracted) ===")
print(f"  before {hop['before']:.1f}  after {hop['after']:.1f}  "
      f"reduction {out['hop_slope_reduction_pct']:.1f}%")
print("=== drift-immune paired ratio at N=%d (per-rep before/after adjacent) ===" % NS[1])
print(f"  median after/before = {out['paired_ratio_hi_after_over_before_median']:.3f}  "
      f"=> {out['paired_reduction_hi_pct_median']:.1f}% reduction")
print(f"  paired delta (incl fixed startup, so a LOWER bound on hop delta): "
      f"{out['paired_delta_hi_us_per_hop_median']:.1f} us/hop")
json.dump(out, open(f"{ROOT}/experiments/coordinator-rpc-guest-trim-realpath_20260804/median-anchors-interleaved.json", "w"), indent=2)
print("\nwrote median-anchors-interleaved.json")
