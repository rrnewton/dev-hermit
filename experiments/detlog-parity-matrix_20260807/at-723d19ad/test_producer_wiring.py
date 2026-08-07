#!/usr/bin/env python3
"""Is the DETLOG verdict ACTUALLY coming from w5's producer, or only nominally?

A wiring change that leaves every number identical is indistinguishable, from the
output alone, from a wiring change that did nothing. These three cases separate them.

  NON-INTERFERENCE  the unmodified population still scores exactly as before
  LOAD-BEARING      forcing the producer's verdict to FAIL must flip every cell that
                    reaches it; if it does not, the scorer is not really asking
  DETECTION         a planted divergence in one collected run must be DETECTED

FAIL-CLOSED USED TO LIVE HERE and has MOVED to test_verdict_pin.py, which covers it
far better now that the producer is loaded from a pinned commit: missing producer,
moved interface, runtime fault, pin/commit mismatch, and an unpinned closure member.
That is de-duplication, not dropped coverage -- keeping a weaker second copy of those
cases here is the same drift the producer split exists to prevent.
"""
import csv, re, shutil, subprocess as sp, sys, tempfile
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


def git(repo, *a):
    r = sp.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    assert r.returncode == 0, f"git {a}: {r.stderr}"
    return r.stdout.strip()


def score(path, data, out):
    r = sp.run([sys.executable, str(path), str(data), str(out)], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def rows(p):
    return {(r["cell"], r["backend"]): r for r in csv.DictReader(open(p))}


print("case NON-INTERFERENCE -- the unmodified population must score exactly as before")
with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "now.csv"
    rc, log = score(HERE / "matrix_score.py", DATA, out)
    check("scorer exits 0 against the unmodified population", rc == 0, log[-300:])
    now, base = rows(out), rows(BASELINE)
    moved = [k for k in base if any(
        (base[k].get(f) or "") != (now[k].get(f) or "")
        for f in ("tier", "Z", "E", "Y_hex", "cover_hex", "cover_hex_pct"))]
    check(f"all {len(base)} cells unchanged on tier and parity fields", not moved, str(moved[:3]))
    scored = [r for r in now.values() if r["tier"] not in ("not-exercised", "no-result")]
    srcs = {r.get("verdict_source", "") for r in scored}
    check(f"every scored row names its verdict source ({len(scored)} rows)",
          all("detlog_compare.py::self_determinism@" in s for s in srcs), repr(srcs)[:160])
    check("the source pins a COMMIT and a BLOB, not a filename or a bare content digest",
          all(re.search(r"@[0-9a-f]{12}:[0-9a-f]{12}$", s) for s in srcs), repr(srcs)[:160])
    check("exactly one producer revision scored the whole population",
          len(srcs) == 1, repr(srcs)[:160])

print()
print("case LOAD-BEARING -- forcing the producer's verdict to FAIL must flip every cell")
# Build a throwaway repo whose COMMITTED producer always says FAIL, and point a copy
# of the scorer's PIN at it. If the scorer kept a comparison of its own, poisoning the
# producer would change nothing.
sandbox = Path(tempfile.mkdtemp(prefix="w7-wiring-"))
repo = sandbox / "repo"
(repo / "compat-envelope").mkdir(parents=True)
(repo / "compat-envelope" / "detlog_compare.py").write_text(
    "PASS='pass'\nFAIL='fail'\nNOT_MEASURED='not-measured'\n"
    "def self_determinism(a, b):\n"
    "    return {'verdict': FAIL, 'reason': 'poisoned for the wiring test'}\n")
git(repo, "init", "-q")
git(repo, "config", "user.email", "w7@test"); git(repo, "config", "user.name", "w7")
git(repo, "add", "-A"); git(repo, "commit", "-qm", "poisoned producer")
PIN = git(repo, "rev-parse", "HEAD")
BLOBS = {"compat-envelope/detlog_compare.py":
         git(repo, "rev-parse", "HEAD:compat-envelope/detlog_compare.py")}

t = (HERE / "matrix_score.py").read_text()
t = t.replace('REPO_ROOT = Path(__file__).resolve().parents[3]', f'REPO_ROOT = Path("{repo}")')
t = re.sub(r'VERDICT_PIN_COMMIT = "[0-9a-f]+"', f'VERDICT_PIN_COMMIT = "{PIN}"', t)
t = re.sub(r'VERDICT_PIN_BLOBS = \{[^}]*\}', "VERDICT_PIN_BLOBS = " + repr(BLOBS), t, flags=re.S)
poisoned = sandbox / "m.py"
poisoned.write_text(t)
shutil.copy(HERE / "engagement.tsv", sandbox / "engagement.tsv")

rc, log = score(poisoned, DATA, sandbox / "poisoned.csv")
if rc == 0:
    pois = rows(sandbox / "poisoned.csv")
    nondet = sum(1 for v in pois.values() if v["tier"] == "self-nondeterministic")
    inert = sum(1 for v in pois.values() if v["tier"] == "not-exercised")
    # 35, not 42, and that is CORRECT: the 7 e9patch cells are decided by the
    # engagement witness BEFORE the verdict is consulted, because a backend that
    # transformed nothing is inert whatever the comparison says.
    check(f"every cell that REACHES the producer flips ({nondet} of {42-inert} reachable)",
          nondet == 42 - inert, log[-300:])
    check("the 7 not-exercised cells are decided before the verdict and do NOT flip",
          inert == 7, str(inert))
    check("no cell survives as a pass with a poisoned producer",
          all(v["tier"] in ("self-nondeterministic", "not-exercised") for v in pois.values()))
else:
    check("poisoned-producer run completed", False, log[-500:])

print()
print("case DETECTION -- a planted divergence in ONE collected run must be caught")
with tempfile.TemporaryDirectory() as td:
    d = Path(td) / "data"
    shutil.copytree(DATA, d)
    victim = d / "notsc.ptrace.7.d"
    lines = victim.read_text().splitlines()
    lines[40] += "   <<PLANTED>>"
    victim.write_text("\n".join(lines) + "\n")
    out3 = Path(td) / "planted.csv"
    rc, log = score(HERE / "matrix_score.py", d, out3)
    check("planted run still scores (fail-closed did not swallow it)", rc == 0, log[-300:])
    r = rows(out3)[("notsc", "ptrace")]
    check("planted cell is no longer byte-identical", r["tier"] != "byte-identical", r["tier"])
    check("planted cell reads self-nondeterministic",
          r["tier"] == "self-nondeterministic", r["tier"])
    check("its class census shows 2 classes over 30 runs",
          r["selfdet_distinct_classes"] == "2", r.get("selfdet_distinct_classes"))
    untouched = rows(out3)[("notsc", "kvm")]
    check("an untouched sibling cell is UNAFFECTED (detection, not collateral)",
          untouched["tier"] == "diverges", untouched["tier"])

shutil.rmtree(sandbox, ignore_errors=True)
print()
if FAILURES:
    print(f"FAIL ({len(FAILURES)} assertions): {FAILURES}")
    sys.exit(1)
print("PASS")
