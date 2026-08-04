#!/usr/bin/env python3
"""Median CPU/wall per-syscall anchors for the Detcore coordinator-RPC path.
K=2 box (coordinator RPC livelocks at K=1 -- see README). Marginal = slope
across two N to subtract fixed startup. Reports absolute us/syscall + user:sys
split (the codegen-addressable fraction)."""
import subprocess, statistics as st, tempfile, os, re, sys, json

ROOT = "/home/newton/work/dev-hermit"
HERMIT = f"{ROOT}/hermit/target/release/hermit"
SO = f"{ROOT}/hermit/target/release/libreverie_liteinst.so"
YL = f"{ROOT}/experiments/coordinator-rpc-codegen-sensitivity_20260804/src/yield_loop"
BOX = f"{ROOT}/scratch/run-on-k-free-cores.py"
REPS = 5
K = 2

def parse_time(path):
    txt = open(path).read()
    u = float(re.search(r"User time \(seconds\): ([\d.]+)", txt).group(1))
    s = float(re.search(r"System time \(seconds\): ([\d.]+)", txt).group(1))
    w = re.search(r"Elapsed .*: ([\d:.]+)", txt).group(1)
    parts = w.split(":"); wall = float(parts[-1]) + (float(parts[-2])*60 if len(parts) > 1 else 0)
    return u, s, wall

def run(mode, N):
    reps = []
    for _ in range(REPS):
        tf = tempfile.mktemp()
        base = ["python3", BOX, str(K), "--", "/usr/bin/time", "-v", "-o", tf]
        if mode == "det":
            cmd = base + ["env", f"HERMIT_LITEINST_RUNTIME={SO}", HERMIT, "run",
                          "--backend", "liteinst", "--", YL, str(N)]
        else:
            cmd = base + [YL, str(N)]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=70)
            reps.append(parse_time(tf))
        except Exception as e:
            reps.append(None)
        finally:
            if os.path.exists(tf): os.remove(tf)
    ok = [r for r in reps if r]
    if not ok:
        return None
    med = lambda i: st.median([r[i] for r in ok])
    return {"n_ok": len(ok), "user": med(0), "sys": med(1), "wall": med(2)}

out = {"reps": REPS, "K": K, "points": {}}
for mode in ("det", "native"):
    for N in (10000, 50000):
        r = run(mode, N)
        out["points"][f"{mode}_{N}"] = r
        print(f"{mode:7s} N={N:6d}  {r}", flush=True)

def marginal(mode):
    lo, hi = out["points"][f"{mode}_10000"], out["points"][f"{mode}_50000"]
    dN = 50000 - 10000
    return {k: (hi[k]-lo[k])/dN*1e6 for k in ("user", "sys", "wall")}  # us/call

md = marginal("det"); mn = marginal("native")
det_over_native = {k: md[k]-mn[k] for k in md}
tot = det_over_native["user"] + det_over_native["sys"]
out["marginal_det_us_per_call"] = md
out["marginal_native_us_per_call"] = mn
out["coordinator_rpc_us_per_call"] = {  # det minus native = pure determinism cost
    **det_over_native,
    "user_frac_of_cpu": det_over_native["user"]/tot if tot else None,
    "sys_frac_of_cpu": det_over_native["sys"]/tot if tot else None,
}
print("\n=== marginal det us/call ===", md)
print("=== marginal native us/call ===", mn)
print("=== coordinator RPC (det - native) us/call ===", out["coordinator_rpc_us_per_call"])
json.dump(out, open(f"{ROOT}/experiments/coordinator-rpc-codegen-sensitivity_20260804/median-anchors.json", "w"), indent=2)
print("\nwrote median-anchors.json")
