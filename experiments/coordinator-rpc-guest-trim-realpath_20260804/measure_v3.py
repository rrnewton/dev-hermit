#!/usr/bin/env python3
"""Real-path A/B v3 for coordinator-RPC guest-side trim (reverie PR #369).

Controlled swap: same hermit coordinator binary, only libreverie_liteinst.so
differs. BEFORE = primary hermit/target/release build (reverie 04a46b43 = parent
pin). AFTER = branch build (975c9fa, ab-target). Each sched_yield = 1 det-mode
coordinator RPC hop.

Fully interleaved within a rep: before_lo, after_lo, before_hi, after_hi run
back-to-back so both variants share the same load window. Per-rep marginal
slope over (lo,hi) subtracts fixed hermit startup. Report median + IQR across
reps for wall AND cpu(user+sys). K=2 (K=1 same-core livelocks). Deltas at same
box/load are the robust signal; absolutes are load-inflated on this shared box.
"""
import subprocess, statistics as st, tempfile, os, re, json, sys

ROOT = "/home/newton/work/dev-hermit"
HERMIT = f"{ROOT}/hermit/target/release/hermit"
SO = {"before": f"{ROOT}/hermit/target/release/libreverie_liteinst.so",
      "after": f"{ROOT}/scratch/coord-rpc-fixes/ab-target/release/libreverie_liteinst.so"}
YL = f"{ROOT}/experiments/coordinator-rpc-guest-trim-realpath_20260804/src/yield_loop"
BOX = f"{ROOT}/scratch/run-on-k-free-cores.py"
REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
K = int(sys.argv[2]) if len(sys.argv) > 2 else 2
LO, HI = 20000, 120000
DN = HI - LO


def parse_time(path):
    txt = open(path).read()
    u = float(re.search(r"User time \(seconds\): ([\d.]+)", txt).group(1))
    s = float(re.search(r"System time \(seconds\): ([\d.]+)", txt).group(1))
    w = re.search(r"Elapsed .*: ([\d:.]+)", txt).group(1)
    p = w.split(":")
    wall = float(p[-1]) + (float(p[-2]) * 60 if len(p) > 1 else 0)
    return {"user": u, "sys": s, "cpu": u + s, "wall": wall}


def one(variant, N):
    tf = tempfile.mktemp()
    cmd = ["python3", BOX, str(K), "--", "/usr/bin/time", "-v", "-o", tf,
           "env", f"HERMIT_LITEINST_RUNTIME={SO[variant]}", HERMIT, "run",
           "--backend", "liteinst", "--", YL, str(N)]
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=180)
        if r.returncode != 0:
            return None
        return parse_time(tf)
    except Exception:
        return None
    finally:
        if os.path.exists(tf):
            os.remove(tf)


def loadavg():
    return float(open("/proc/loadavg").read().split()[0])


per_rep = []  # each: {metric: {"before":hop, "after":hop, "delta":..}}
load = [loadavg()]
for rep in range(REPS):
    runs = {}
    ok = True
    for variant in ("before", "after"):
        for N in (LO, HI):
            r = one(variant, N)
            if r is None:
                ok = False
            runs[(variant, N)] = r
    load.append(loadavg())
    if not ok:
        print(f"rep {rep}: FAILED run, skipping", flush=True)
        continue
    row = {}
    for m in ("wall", "cpu", "sys", "user"):
        hb = (runs[("before", HI)][m] - runs[("before", LO)][m]) / DN * 1e6
        ha = (runs[("after", HI)][m] - runs[("after", LO)][m]) / DN * 1e6
        row[m] = {"before": hb, "after": ha, "delta": hb - ha}
    per_rep.append(row)
    print(f"rep {rep}: wall b/a {row['wall']['before']:.2f}/{row['wall']['after']:.2f}  "
          f"cpu b/a {row['cpu']['before']:.2f}/{row['cpu']['after']:.2f} us/hop", flush=True)


def summ(metric, field):
    xs = sorted(r[metric][field] for r in per_rep)
    n = len(xs)
    if n == 0:
        return None
    q = lambda p: xs[min(n - 1, int(p * n))]
    return {"p50": st.median(xs), "p25": q(0.25), "p75": q(0.75), "n": n}


out = {"reps_requested": REPS, "reps_ok": len(per_rep), "K": K,
       "N_lo": LO, "N_hi": HI, "loadavg_samples": load,
       "SO_before": SO["before"], "SO_after": SO["after"],
       "per_hop_us": {}}
for m in ("wall", "cpu", "sys", "user"):
    out["per_hop_us"][m] = {
        "before": summ(m, "before"), "after": summ(m, "after"),
        "delta": summ(m, "delta")}
    b = out["per_hop_us"][m]["before"]
    a = out["per_hop_us"][m]["after"]
    if b and a and b["p50"]:
        out["per_hop_us"][m]["reduction_pct_p50"] = (b["p50"] - a["p50"]) / b["p50"] * 100

print("\n=== per-hop us/hop (median [p25-p75] across reps) ===")
for m in ("wall", "cpu", "sys"):
    d = out["per_hop_us"][m]
    if d["before"]:
        print(f"  {m:5s}  before {d['before']['p50']:.2f} [{d['before']['p25']:.2f}-{d['before']['p75']:.2f}]"
              f"  after {d['after']['p50']:.2f} [{d['after']['p25']:.2f}-{d['after']['p75']:.2f}]"
              f"  reduction {d.get('reduction_pct_p50', float('nan')):.1f}%")
json.dump(out, open(f"{ROOT}/experiments/coordinator-rpc-guest-trim-realpath_20260804/median-anchors-v3.json", "w"), indent=2)
print("\nwrote median-anchors-v3.json")
