#!/usr/bin/env python3
"""Is the DETLOG verdict ACTUALLY coming from w5's producer, or only nominally?

A wiring change that leaves every number identical is indistinguishable, from the
output alone, from a wiring change that did nothing. These four cases separate them.

  NON-INTERFERENCE  the unmodified population still scores exactly as before
  LOAD-BEARING      forcing the producer's verdict to FAIL must flip every cell;
                    if it does not, the scorer is not really asking the producer
  DETECTION         a planted divergence in one run must be DETECTED
  FAIL-CLOSED       a missing / interface-moved producer must ABORT, never fall
                    back to a local comparison
"""
import csv, importlib.util, shutil, subprocess, subprocess as sp, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(sys.argv[1] if len(sys.argv) > 1
            else "/home/newton/work/dev-hermit/scratch/w7-liteinst-maps/mx723n30")
BASELINE = HERE / "detlog-matrix-723-n30.csv"
FAILURES = []


def check(label, ok, detail=""):
    print(f"  {'ok   ' if ok else 'FAIL '} {label}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(label)


def score(data, out, env=None):
    r = sp.run([sys.executable, str(HERE / "matrix_score.py"), str(data), str(out)],
               capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


def rows(p):
    return {(r["cell"], r["backend"]): r for r in csv.DictReader(open(p))}


print("case NON-INTERFERENCE -- the unmodified population must score exactly as before")
with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "now.csv"
    rc, _ = score(DATA, out)
    check("scorer exits 0 against the unmodified population", rc == 0)
    now, base = rows(out), rows(BASELINE)
    same = [k for k in base if all(
        (base[k].get(f) or "") == (now[k].get(f) or "")
        for f in ("tier", "Z", "E", "Y_hex", "cover_hex", "cover_hex_pct"))]
    check(f"all {len(base)} cells unchanged on tier and parity fields",
          len(same) == len(base), f"{len(base)-len(same)} moved")
    # Provenance must identify WHICH revision scored the row: the producer is untracked
    # and moving (it changed under this scorer mid-run at 01:51), so the bare filename
    # does not identify what actually ran.
    scored = [r for r in now.values() if r["tier"] not in ("not-exercised", "no-result")]
    srcs = {r.get("verdict_source", "") for r in scored}
    check(f"every scored row names its verdict source ({len(scored)} rows)",
          all("detlog_compare.py::self_determinism@" in s for s in srcs), repr(srcs)[:160])
    check("the source pins a producer revision, not just a filename",
          all(len(s.rsplit("@", 1)[-1]) == 16 for s in srcs), repr(srcs)[:160])
    check("exactly one producer revision scored the whole population",
          len(srcs) == 1, repr(srcs)[:160])

print()
print("case LOAD-BEARING -- forcing the producer's verdict to FAIL must flip every cell")
# If the scorer kept its own comparison, poisoning the producer would change nothing.
poison = Path(tempfile.mkdtemp()) / "detlog_compare.py"
poison.write_text(
    "PASS='pass'\nFAIL='fail'\nNOT_MEASURED='not-measured'\n"
    "def self_determinism(a, b):\n"
    "    return {'verdict': FAIL, 'reason': 'poisoned for the wiring test'}\n")
patched = (HERE / "matrix_score.py").read_text().replace(
    'VERDICT_MODULE = (Path(__file__).resolve().parents[3]\n'
    '                  / "compat-envelope" / "detlog_compare.py")',
    f'VERDICT_MODULE = Path("{poison}")')
tmp_scorer = poison.parent / "matrix_score.py"
tmp_scorer.write_text(patched)
shutil.copy(HERE / "engagement.tsv", poison.parent / "engagement.tsv")
out2 = poison.parent / "poisoned.csv"
r = sp.run([sys.executable, str(tmp_scorer), str(DATA), str(out2)], capture_output=True, text=True)
if r.returncode == 0:
    pois = rows(out2)
    nondet = sum(1 for v in pois.values() if v["tier"] == "self-nondeterministic")
    inert = sum(1 for v in pois.values() if v["tier"] == "not-exercised")
    # 35, not 42, and that is CORRECT: the 7 e9patch cells are decided by the
    # engagement witness BEFORE the verdict is consulted, because a backend that
    # transformed nothing is inert whatever the comparison says. So the number to
    # assert is "every cell that reaches the producer flips", i.e. 42 - inert.
    check(f"every cell that REACHES the producer flips ({nondet} of {42-inert} reachable)",
          nondet == 42 - inert, r.stdout[-300:])
    check("the 7 not-exercised cells are decided before the verdict and do NOT flip",
          inert == 7, str(inert))
    check("no cell survives as a pass with a poisoned producer",
          all(v["tier"] in ("self-nondeterministic", "not-exercised") for v in pois.values()))
else:
    check("poisoned-producer run completed", False, r.stdout[-400:] + r.stderr[-400:])

print()
print("case DETECTION -- a planted divergence in ONE run must be caught")
with tempfile.TemporaryDirectory() as td:
    d = Path(td) / "data"
    shutil.copytree(DATA, d)
    victim = d / "notsc.ptrace.7.d"
    lines = victim.read_text().splitlines()
    lines[40] += "   <<PLANTED>>"
    victim.write_text("\n".join(lines) + "\n")
    out3 = Path(td) / "planted.csv"
    rc, log = score(d, out3)
    r = rows(out3)[("notsc", "ptrace")]
    check("planted cell is no longer byte-identical", r["tier"] != "byte-identical", r["tier"])
    check("planted cell reads self-nondeterministic",
          r["tier"] == "self-nondeterministic", r["tier"])
    check("its class census shows 2 classes over 30 runs",
          r["selfdet_distinct_classes"] == "2", r.get("selfdet_distinct_classes"))
    untouched = rows(out3)[("notsc", "kvm")]
    check("an untouched sibling cell is UNAFFECTED (no collateral)",
          untouched["tier"] == "diverges", untouched["tier"])

print()
print("case FAIL-CLOSED -- a missing or moved producer must ABORT, never fall back")
missing = (HERE / "matrix_score.py").read_text().replace(
    'VERDICT_MODULE = (Path(__file__).resolve().parents[3]\n'
    '                  / "compat-envelope" / "detlog_compare.py")',
    'VERDICT_MODULE = Path("/nonexistent/detlog_compare.py")')
p2 = Path(tempfile.mkdtemp()) / "m.py"; p2.write_text(missing)
r = sp.run([sys.executable, str(p2), str(DATA), "/tmp/never.csv"], capture_output=True, text=True)
check("missing producer aborts nonzero", r.returncode != 0, str(r.returncode))
check("and says so explicitly", "ABORT" in (r.stdout + r.stderr), (r.stdout + r.stderr)[:200])
check("and writes no scorecard", not Path("/tmp/never.csv").exists())

stub = Path(tempfile.mkdtemp()) / "detlog_compare.py"
stub.write_text("PASS='pass'\n")          # interface moved: no self_determinism
moved = (HERE / "matrix_score.py").read_text().replace(
    'VERDICT_MODULE = (Path(__file__).resolve().parents[3]\n'
    '                  / "compat-envelope" / "detlog_compare.py")',
    f'VERDICT_MODULE = Path("{stub}")')
p3 = stub.parent / "m.py"; p3.write_text(moved)
r = sp.run([sys.executable, str(p3), str(DATA), "/tmp/never2.csv"], capture_output=True, text=True)
check("producer present but interface moved also aborts", r.returncode != 0)
check("and names the missing symbols",
      "self_determinism" in (r.stdout + r.stderr), (r.stdout + r.stderr)[:200])

print()
if FAILURES:
    print(f"FAIL ({len(FAILURES)} assertions): {FAILURES}")
    sys.exit(1)
print("PASS")
