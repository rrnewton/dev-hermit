#!/usr/bin/env python3
"""Real-path A/B: does the guest-side coordinator-RPC trim (reverie PR #369)
reduce the det-mode per-`sched_yield` hop?

Controlled A/B differing ONLY in the runtime `libreverie_liteinst.so`:
  before = base reverie 04a46b43 (primary hermit build)
  after  = base + PR #369 (perf/coordinator-rpc-guest-side-trim @975c9fa)
Same hermit coordinator binary for both (the trim is guest-side only), swapped
via HERMIT_LITEINST_RUNTIME. Marginal slope N=10000->50000 subtracts fixed
hermit startup; native is .so-independent (measured once). K=2 (RPC livelocks at
K=1). Deltas at the same box/load are the robust signal; absolutes are
load-inflated on this shared 316-core host."""
import subprocess, statistics as st, tempfile, os, re, json

ROOT = "/home/newton/work/dev-hermit"
HERMIT = f"{ROOT}/hermit/target/release/hermit"
SO_BEFORE = f"{ROOT}/hermit/target/release/libreverie_liteinst.so"
SO_AFTER = f"{ROOT}/scratch/coord-rpc-fixes/ab-target/release/libreverie_liteinst.so"
YL = f"{ROOT}/experiments/coordinator-rpc-guest-trim-realpath_20260804/src/yield_loop"
BOX = f"{ROOT}/scratch/run-on-k-free-cores.py"
REPS = 5
K = 2
NS = (10000, 50000)


def parse_time(path):
    txt = open(path).read()
    u = float(re.search(r"User time \(seconds\): ([\d.]+)", txt).group(1))
    s = float(re.search(r"System time \(seconds\): ([\d.]+)", txt).group(1))
    w = re.search(r"Elapsed .*: ([\d:.]+)", txt).group(1)
    parts = w.split(":")
    wall = float(parts[-1]) + (float(parts[-2]) * 60 if len(parts) > 1 else 0)
    return u, s, wall


def run(kind, N, so):
    reps = []
    for _ in range(REPS):
        tf = tempfile.mktemp()
        base = ["python3", BOX, str(K), "--", "/usr/bin/time", "-v", "-o", tf]
        if kind == "det":
            cmd = base + ["env", f"HERMIT_LITEINST_RUNTIME={so}", HERMIT, "run",
                          "--backend", "liteinst", "--", YL, str(N)]
        else:
            cmd = base + [YL, str(N)]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=120)
            reps.append(parse_time(tf))
        except Exception:
            reps.append(None)
        finally:
            if os.path.exists(tf):
                os.remove(tf)
    ok = [r for r in reps if r]
    if not ok:
        return None
    med = lambda i: st.median([r[i] for r in ok])
    return {"n_ok": len(ok), "user": med(0), "sys": med(1), "wall": med(2)}


out = {"reps": REPS, "K": K, "ns": NS, "points": {}}
plan = [("det_before", "det", SO_BEFORE), ("det_after", "det", SO_AFTER),
        ("native", "native", None)]
for label, kind, so in plan:
    for N in NS:
        r = run(kind, N, so)
        out["points"][f"{label}_{N}"] = r
        print(f"{label:12s} N={N:6d}  {r}", flush=True)


def marginal(label):
    lo, hi = out["points"][f"{label}_{NS[0]}"], out["points"][f"{label}_{NS[1]}"]
    dN = NS[1] - NS[0]
    return {k: (hi[k] - lo[k]) / dN * 1e6 for k in ("user", "sys", "wall")}


mb, ma, mn = marginal("det_before"), marginal("det_after"), marginal("native")
# det - native = pure coordinator-RPC hop cost, us/hop
hop_before = {k: mb[k] - mn[k] for k in mb}
hop_after = {k: ma[k] - mn[k] for k in ma}
out["marginal_us_per_yield"] = {"det_before": mb, "det_after": ma, "native": mn}
out["hop_us_before"] = hop_before
out["hop_us_after"] = hop_after
out["hop_wall_delta_us"] = hop_before["wall"] - hop_after["wall"]
out["hop_wall_reduction_pct"] = (
    (hop_before["wall"] - hop_after["wall"]) / hop_before["wall"] * 100
    if hop_before["wall"] else None)
print("\n=== per-hop wall us (det - native) ===")
print(f"  before (base)      : {hop_before['wall']:.2f} us/hop")
print(f"  after  (PR #369)   : {hop_after['wall']:.2f} us/hop")
print(f"  delta              : {out['hop_wall_delta_us']:.2f} us/hop "
      f"({out['hop_wall_reduction_pct']:.1f}% reduction)")
json.dump(out, open(f"{ROOT}/experiments/coordinator-rpc-guest-trim-realpath_20260804/median-anchors.json", "w"), indent=2)
print("\nwrote median-anchors.json")
