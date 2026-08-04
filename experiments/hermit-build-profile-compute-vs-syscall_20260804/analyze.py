#!/usr/bin/env python3
"""Answer the owner's clause with numbers that carry their units.

Clause under test: "hermit itself doesn't do much compute" -> an unoptimized
hermit runs guests ~as fast as an optimized one. Reframed by the owner as a CI
build-profile decision: TOTAL = compile + test(runtime); only the total decides.

Outputs:
  medians.csv                         per (profile,guest,mode): median wall & cpu, N
  1. hermit/native runtime ratio      instrumentation cost, per profile+shape (1-CPU box)
  2. debug/release & o0/release ratio  the "does opt-level matter at runtime" clause, per shape
  3. compile wall/cpu per profile     from compile.csv
  4. TOTAL + break-even test-count    when a cheaper-compile profile stops paying off, per shape

Break-even K (release-o0 vs release), using CPU-seconds:
    K* = (compile_o0 - compile_rel) / (rt_rel - rt_o0)
  Below K* test-runs, the fast-compile profile wins overall; above it, it loses.
"""
import csv, statistics, collections, os

HERE = os.path.dirname(os.path.abspath(__file__))

def load(name):
    p = os.path.join(HERE, name)
    return list(csv.DictReader(open(p))) if os.path.exists(p) else []

rows = load(os.environ.get("RESULTS_CSV", "results.csv"))
agg = collections.defaultdict(lambda: {"wall": [], "cpu": []})
dropped = 0
for r in rows:
    # Guard against poison rows from FAILED guest runs (wall='Command', cpu=0):
    # an old harness emitted /usr/bin/time's error line as data. Drop non-numeric
    # or non-positive timings so a failed run never distorts a median.
    try:
        wall, cpu = float(r["wall_s"]), float(r["cpu_s"])
    except (ValueError, TypeError):
        dropped += 1; continue
    if wall <= 0 or cpu <= 0:
        dropped += 1; continue
    k = (r["profile"], r["guest"], r["mode"])
    agg[k]["wall"].append(wall); agg[k]["cpu"].append(cpu)
if dropped:
    print(f"NOTE: dropped {dropped} poison/failed-run row(s) before aggregation")

med = {}
with open(os.path.join(HERE, "medians.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["profile","guest","mode","n","wall_med_s","cpu_med_s"])
    for k, v in sorted(agg.items()):
        wm, cm = statistics.median(v["wall"]), statistics.median(v["cpu"])
        med[k] = (wm, cm); w.writerow([*k, len(v["wall"]), f"{wm:.3f}", f"{cm:.3f}"])

def rt(profile, guest, i):   # hermit runtime median: 0=wall 1=cpu
    return med.get((profile, guest, "hermit"), (float("nan"),)*2)[i]
def nat(guest, i):
    return med.get(("native", guest, "native"), (float("nan"),)*2)[i]

GUESTS = ("compute_bound", "syscall_bound")
PROFS  = ("release", "release-o0", "debug")

print("\n=== 1. hermit/native runtime (instrumentation cost, 1-CPU box) ===")
print(f"{'profile':11}{'guest':15}{'wall x':>9}{'cpu x':>9}")
for p in PROFS:
    for g in GUESTS:
        print(f"{p:11}{g:15}{rt(p,g,0)/nat(g,0):9.2f}{rt(p,g,1)/nat(g,1):9.2f}")

print("\n=== 2. runtime ratio vs release, per guest shape (THE clause) ===")
print(f"{'guest':15}{'o0/rel cpu':>12}{'debug/rel cpu':>14}")
for g in GUESTS:
    print(f"{g:15}{rt('release-o0',g,1)/rt('release',g,1):12.2f}"
          f"{rt('debug',g,1)/rt('release',g,1):14.2f}")
print("compute_bound ~1.0 + syscall_bound >>1.0  =>  hermit is the hot path ONLY")
print("for syscall-bound guests => argues for PER-NODE-CLASS profiles.")

comp = {r["profile"]: r for r in load("compile.csv") if r.get("phase")=="compile"}
if comp:
    print("\n=== 3. compile cost per profile ===")
    print(f"{'profile':11}{'wall_s':>10}{'cpu_s':>10}")
    for p in PROFS:
        c = comp.get(p)
        if c: print(f"{p:11}{float(c['wall_s']):10.1f}{float(c['cpu_s']):10.1f}")

    print("\n=== 4. TOTAL = compile + K*runtime ; break-even K (o0 vs release, cpu-s) ===")
    print(f"{'guest':15}{'K* (test-runs)':>16}   interpretation")
    for g in GUESTS:
        try:
            dc = float(comp['release-o0']['cpu_s']) - float(comp['release']['cpu_s'])
            drt = rt('release',g,1) - rt('release-o0',g,1)   # rel faster => negative
            if abs(drt) < 1e-9:
                print(f"{g:15}{'inf':>16}   runtime identical -> cheaper compile always wins")
            else:
                kstar = dc/drt
                note = ("below K* runs, release-o0 wins overall" if kstar>0
                        else "release-o0 wins at ALL run counts (cheaper compile AND runtime)")
                print(f"{g:15}{kstar:16.1f}   {note}")
        except (KeyError, ValueError):
            print(f"{g:15}{'n/a':>16}   (missing compile or runtime data)")
else:
    print("\n(compile.csv absent — run build_and_run.sh to populate compile timings)")

# --- 5. Critical-path ceiling (hermit-220: strict_compat = ~47% of DAG wall) ---
STRICT_COMPAT_FRAC = 0.47   # hermit-220: strict_compat ~600s @ ~12 cores = 47% of critical path
print(f"\n=== 5. critical-path ceiling (strict_compat = {STRICT_COMPAT_FRAC:.0%} of DAG wall) ===")
print("A build-profile change touches TWO things on the DAG:")
print(f"  (a) COMPILE time — a one-time node, part of the OTHER {1-STRICT_COMPAT_FRAC:.0%}. A")
print(f"      30% compile win there is at most 30%*{1-STRICT_COMPAT_FRAC:.0%} = "
      f"{0.30*(1-STRICT_COMPAT_FRAC):.0%} of DAG wall.")
print("  (b) RUNTIME instrumentation for every hermit-running test — INCLUDING")
print(f"      strict_compat ({STRICT_COMPAT_FRAC:.0%}). If strict_compat is SYSCALL-BOUND, a debug/")
print("      o0 hermit INFLATES the 47% (see the syscall_bound runtime ratio above) —")
print("      that can DWARF any compile saving and flip the profile to a net loss.")
print("So: report the profile's effect on BOTH the compile node AND on strict_compat's")
print("runtime; do NOT claim a compile-% win as a DAG-wall-% win.")

print("\nSemantic guard: release-o0 is behaviour-identical to release by construction")
print("(only opt-level differs). See semantics.txt; 'debug' flips debug-assertions +")
print("overflow-checks => NOT a valid determinism-test CI profile regardless of speed.")
