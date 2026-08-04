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

rows = load("results.csv")
agg = collections.defaultdict(lambda: {"wall": [], "cpu": []})
for r in rows:
    k = (r["profile"], r["guest"], r["mode"])
    agg[k]["wall"].append(float(r["wall_s"])); agg[k]["cpu"].append(float(r["cpu_s"]))

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

print("\nSemantic guard: release-o0 is behaviour-identical to release by construction")
print("(only opt-level differs). See semantics.txt; 'debug' flips debug-assertions +")
print("overflow-checks => NOT a valid determinism-test CI profile regardless of speed.")
