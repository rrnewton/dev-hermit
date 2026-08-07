#!/usr/bin/env python3
"""Does pinning PREVENT a stale score, or only notice one afterwards?

The previous revision read the producer from the working tree and stamped a digest
of whatever it found. That is detection: you learn after the fact that the thing you
scored with has changed. This asserts the stronger property -- an edit to the working
tree cannot change a score at all -- plus the three guards that make the pin honest:
the closure is pinned (not just the entry point), a pin that misdescribes its commit
aborts, and the development escape hatch stamps itself so it cannot be quoted as
pinned.

Runs entirely against a THROWAWAY repo. It never writes to compat-envelope/, which
belongs to hermit-w5.
"""
import csv, os, re, shutil, subprocess as sp, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REAL = HERE.parents[2]
DATA = Path(sys.argv[1] if len(sys.argv) > 1
            else "/home/newton/work/dev-hermit/scratch/w7-liteinst-maps/mx723n30")
FAILURES = []


def check(label, ok, detail=""):
    print(f"  {'ok   ' if ok else 'FAIL '} {label}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(label)


def git(repo, *a):
    r = sp.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    assert r.returncode == 0, f"git {a}: {r.stderr}"
    return r.stdout.strip()


# ---- a throwaway repo holding the producer closure, so nothing real is touched ----
sandbox = Path(tempfile.mkdtemp(prefix="w7-pin-sandbox-"))
repo = sandbox / "repo"
(repo / "compat-envelope").mkdir(parents=True)
for name in ("detlog_compare.py", "strict_verdict.py"):
    shutil.copy(REAL / "compat-envelope" / name, repo / "compat-envelope" / name)
git(repo, "init", "-q")
git(repo, "config", "user.email", "w7@test"); git(repo, "config", "user.name", "w7")
git(repo, "add", "-A"); git(repo, "commit", "-qm", "pinned producer")
PIN = git(repo, "rev-parse", "HEAD")
BLOBS = {f"compat-envelope/{n}": git(repo, "rev-parse", f"HEAD:compat-envelope/{n}")
         for n in ("detlog_compare.py", "strict_verdict.py")}


def scorer(pin=PIN, blobs=None, root=repo, extra=""):
    """A copy of the real scorer, repointed at the sandbox."""
    blobs = blobs or BLOBS
    t = (HERE / "matrix_score.py").read_text()
    t = t.replace('REPO_ROOT = Path(__file__).resolve().parents[3]', f'REPO_ROOT = Path("{root}")')
    t = re.sub(r'VERDICT_PIN_COMMIT = "[0-9a-f]+"', f'VERDICT_PIN_COMMIT = "{pin}"', t)
    t = re.sub(r'VERDICT_PIN_BLOBS = \{[^}]*\}',
               "VERDICT_PIN_BLOBS = " + repr(blobs), t, flags=re.S)
    p = Path(tempfile.mkdtemp(dir=sandbox)) / "m.py"
    p.write_text(t + extra)
    shutil.copy(HERE / "engagement.tsv", p.parent / "engagement.tsv")
    return p


def run(path, out, env=None):
    e = dict(os.environ); e.update(env or {})
    r = sp.run([sys.executable, str(path), str(DATA), str(out)],
               capture_output=True, text=True, env=e)
    return r.returncode, r.stdout, r.stderr


def tiers(p):
    return {(r["cell"], r["backend"]): r["tier"] for r in csv.DictReader(open(p))}


def srcs(p):
    return {r["verdict_source"] for r in csv.DictReader(open(p)) if r["verdict_source"]}


print("case BASELINE — the pinned scorer runs and stamps the pin")
s = scorer()
rc, out, err = run(s, sandbox / "base.csv")
check("pinned scorer exits 0", rc == 0, err[-300:])
base = tiers(sandbox / "base.csv")
check("42 cells scored", len(base) == 42, str(len(base)))
src = srcs(sandbox / "base.csv")
check("every scored row names commit:blob, not a bare filename",
      all(re.search(r"@[0-9a-f]{12}:[0-9a-f]{12}$", x) for x in src), repr(src)[:150])
check(f"the recorded commit is the pin", all(PIN[:12] in x for x in src), repr(src)[:150])
check("no drift note when the worktree matches the pin", "has moved since this pin" not in err)

print()
print("case PREVENTION — mutating the working tree must NOT change the score")
victim = repo / "compat-envelope" / "strict_verdict.py"
orig = victim.read_text()
victim.write_text(orig.replace("def detlog_verdict(", "def detlog_verdict_DISABLED("))
rc, out, err = run(s, sandbox / "after.csv")
check("scorer still exits 0 against the pin", rc == 0, err[-300:])
check("score is BYTE-IDENTICAL despite the worktree edit",
      tiers(sandbox / "after.csv") == base, "tiers moved")
print("case DETECTION — ...and it says so, so nobody thinks their edit took effect")
check("a drift NOTE is emitted", "has moved since this pin" in err, err[-200:])
check("the note names the drifted file", "strict_verdict.py" in err)
check("the note says the edit did NOT take effect", "did NOT take effect" in err)
check("the note tells you how to adopt it", "re-pin" in err.lower())

print()
print("case ESCAPE HATCH — worktree mode must adopt the edit AND stamp itself")
rc, out, err = run(s, sandbox / "wt.csv", env={"DETLOG_VERDICT_USE_WORKTREE": "1"})
# A producer that IMPORTS cleanly but FAILS WHEN CALLED must still fail closed, and
# the message must point at the producer rather than reading as a collector bug.
check("worktree mode with a runtime-broken producer aborts nonzero", rc != 0, f"rc={rc}")
check("...with a clean ABORT, not a raw traceback", "ABORT" in (out + err), (out + err)[-250:])
check("...naming the producer as the source",
      "verdict producer raised" in (out + err), (out + err)[-250:])
check("...and writing no scorecard", not (sandbox / "wt.csv").exists())
victim.write_text(orig)                       # restore, then re-check the happy path
rc, out, err = run(s, sandbox / "wt2.csv", env={"DETLOG_VERDICT_USE_WORKTREE": "1"})
check("worktree mode scores when the producer is sound", rc == 0, err[-300:])
wsrc = srcs(sandbox / "wt2.csv")
check("worktree-mode provenance is stamped 'worktree:' and cannot be read as pinned",
      all(":worktree:" in x or "worktree:" in x.split("@")[-1] for x in wsrc), repr(wsrc)[:150])
check("and it is DIFFERENT from the pinned stamp", wsrc != src, repr(wsrc)[:150])

print()
print("case PIN INTEGRITY — a pin that misdescribes its own commit must abort")
bad = dict(BLOBS); bad["compat-envelope/strict_verdict.py"] = "0" * 40
rc, out, err = run(scorer(blobs=bad), sandbox / "never.csv")
check("blob mismatch aborts nonzero", rc != 0, str(rc))
check("and says pin mismatch", "pin mismatch" in (out + err), (out + err)[:200])
check("and writes no scorecard", not (sandbox / "never.csv").exists())

print()
print("case CLOSURE — pinning only the entry point must be refused")
# Add a new compat-envelope import to the producer and re-commit: the pin now covers
# the entry point but not everything it reaches, which is the defect wearing a hat.
(repo / "compat-envelope" / "extra_helper.py").write_text("X = 1\n")
dc = repo / "compat-envelope" / "detlog_compare.py"
dc.write_text("import extra_helper\n" + dc.read_text())
git(repo, "add", "-A"); git(repo, "commit", "-qm", "producer grows a dependency")
PIN2 = git(repo, "rev-parse", "HEAD")
partial = {f"compat-envelope/{n}": git(repo, "rev-parse", f"HEAD:compat-envelope/{n}")
           for n in ("detlog_compare.py", "strict_verdict.py")}   # extra_helper NOT pinned
rc, out, err = run(scorer(pin=PIN2, blobs=partial), sandbox / "never2.csv")
check("an unpinned closure member aborts nonzero", rc != 0, str(rc))
check("and names the unpinned module", "extra_helper" in (out + err), (out + err)[:250])
check("and writes no scorecard", not (sandbox / "never2.csv").exists())

shutil.rmtree(sandbox, ignore_errors=True)
print()
if FAILURES:
    print(f"FAIL ({len(FAILURES)} assertions): {FAILURES}")
    sys.exit(1)
print("PASS")
