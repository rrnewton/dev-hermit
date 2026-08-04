#!/usr/bin/env python3
"""Compute per-variant ns/syscall as the CPU-time slope across two N values.
slope_ns = (median_cpu(N_hi) - median_cpu(N_lo)) / (N_hi - N_lo) * 1e9
Dispersion via low/high medians' MAD propagated to the slope endpoints.
"""
import csv, statistics, sys
from collections import defaultdict

path = sys.argv[1] if len(sys.argv) > 1 else "results.csv"
rows = list(csv.DictReader(open(path)))
# group cpu samples by (variant, N)
g = defaultdict(list)
guard = defaultdict(set)  # (variant,N) -> set of (direct_hook, ptrace_installation)
rcs = defaultdict(list)
for r in rows:
    key = (r["variant"], int(r["N"]))
    try:
        g[key].append(float(r["cpu_s"]))
    except ValueError:
        pass
    rcs[key].append(r["rc"])
    if r["variant"] != "native":
        guard[key].add((r["direct_hook"], r["ptrace_installation"]))

def med(xs): return statistics.median(xs)
def mad(xs):
    m = med(xs); return med([abs(x-m) for x in xs])

variants = []
seen = set()
for r in rows:
    if r["variant"] not in seen:
        seen.add(r["variant"]); variants.append(r["variant"])

Ns = sorted({int(r["N"]) for r in rows})
N_lo, N_hi = Ns[0], Ns[-1]
dN = N_hi - N_lo

print(f"N_lo={N_lo}  N_hi={N_hi}  dN={dN}\n")
hdr = f"{'variant':<10} {'cpu_lo(s)':>10} {'cpu_hi(s)':>10} {'MAD_lo':>8} {'MAD_hi':>8} {'ns/syscall':>11} {'n':>3} {'guard(dh,pi)':>20} {'rc':>6}"
print(hdr); print("-"*len(hdr))
base = None
out = {}
for v in variants:
    lo = g.get((v, N_lo), []); hi = g.get((v, N_hi), [])
    if not lo or not hi:
        continue
    slope = (med(hi) - med(lo)) / dN * 1e9
    out[v] = slope
    gset = guard.get((v, N_hi), set())
    gstr = ";".join(f"{a}/{b}" for a,b in sorted(gset)) if v!="native" else "-"
    rcset = ",".join(sorted(set(rcs.get((v,N_hi),[]))))
    print(f"{v:<10} {med(lo):>10.4f} {med(hi):>10.4f} {mad(lo):>8.4f} {mad(hi):>8.4f} {slope:>11.1f} {len(hi):>3} {gstr:>20} {rcset:>6}")

print()
if "A" in out:
    a = out["A"]
    print("Relative to variant A (release default opt3/cu16/lto=off), ns/syscall:")
    for v in variants:
        if v in out and v not in ("native",):
            print(f"  {v}: {out[v]:.1f} ns  ({out[v]/a*100:.1f}% of A, delta {out[v]-a:+.1f} ns)")
    if "native" in out:
        print(f"  native raw getpid: {out['native']:.1f} ns  (handler overhead A-native = {a-out['native']:+.1f} ns)")
