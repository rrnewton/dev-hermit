#!/usr/bin/env python3
"""Assemble ONE cross-backend DETLOG parity matrix.

Every cell carries: executed counts (Z golden / E candidate), tier, corpus, and its
OWN self-determinism status. Nothing is inherited from a sibling cell, from another
backend, or from another dimension.

TIER LADDER (cumulative; each presupposes the ones below):
  no-result             candidate produced 0 DETLOG records, or the golden did
  not-exercised         the backend RAN but its own engagement witness says it did
                        nothing to this guest. A green here is the fallback path's,
                        not the backend's, and counting it is the ambiguous-zero
                        failure this repo has already been burned by twice.
  self-nondeterministic candidate's own runs disagree -> parity is NOT-MEASURABLE,
                        and the parity number is withheld rather than printed low
  diverges              self-deterministic, but does not cover the whole golden
  hex-identical         identical to the golden modulo 0x<hex> addresses
  byte-identical        identical to the golden byte for byte

A parity number under a failed self-determinism baseline is not a weak result, it is
a NON-result: you cannot say a stream differs from the golden by N when it differs
from ITSELF by an unknown amount. Those cells print NOT-MEASURABLE and their parity
fields are blank.
"""
from __future__ import annotations
import csv, difflib, importlib.util, itertools, json, os, re, subprocess, sys, tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# THE VERDICT IS NOT OURS. Ratified boundary, 2026-08-07:
#   hermit-w5 owns compat-envelope/detlog_compare.py -- the single source of
#     truth for "do two DETLOG streams agree".
#   hermit-w7 owns collection and the cross-backend coverage number (this file
#     and harness/matrix_collect.sh).
#   The dependency runs w7 -> w5 and never the reverse.
#
# FAIL CLOSED. If the module is missing or its interface has moved, ABORT. Do
# NOT fall back to a local equality check: a fallback would silently recreate
# the two-implementations drift the split exists to prevent, and it would do it
# invisibly, which is worse than not running.
# ---------------------------------------------------------------------------
# PINNED TO A COMMIT, NOT TO THE WORKING TREE. The previous revision loaded the
# producer from `compat-envelope/detlog_compare.py` on disk and stamped a digest of
# whatever it found. That made a stale score DETECTABLE AFTER THE FACT; it did not
# PREVENT one -- and it was not hypothetical: on 2026-08-07 at 01:51 the file changed
# under a running score. This loads the blobs out of the object store at a fixed
# commit, so an edit to the working tree cannot alter a score at all.
#
# THE CLOSURE IS TWO FILES, NOT ONE. detlog_compare.py is now a thin contract surface
# that imports strict_verdict. Pinning only the entry point would leave the actual
# comparison reading from the working tree, which is the whole defect wearing a hat.
# strict_verdict imports nothing but hashlib and re, so the closure ends there;
# _assert_closure_unchanged below re-checks that on every run rather than trusting
# this comment.
VERDICT_PIN_COMMIT = "d2d74fc70f188921217360d8e27f75a5f4808dde"
VERDICT_PIN_SUBJECT = "compat-envelope: one verdict module for detlog, stack and heap"
VERDICT_PIN_BLOBS = {
    "compat-envelope/detlog_compare.py": "60a590e46e437b0dad3d9658a92af4fcf77d538b",
    "compat-envelope/strict_verdict.py": "52ece2e8c833f6881dc9d17cef335f85f3e63ce5",
}
VERDICT_ENTRY = "compat-envelope/detlog_compare.py"
REPO_ROOT = Path(__file__).resolve().parents[3]

#: Development escape hatch. Deliberately NOT the default, and it stamps provenance
#: differently so a working-tree score can never be quoted as a pinned one.
USE_WORKTREE = os.environ.get("DETLOG_VERDICT_USE_WORKTREE") == "1"


def _git(*args: str) -> bytes:
    r = subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                       capture_output=True)
    if r.returncode != 0:
        raise SystemExit(
            f"ABORT: git {' '.join(args)} failed (rc={r.returncode}): "
            f"{r.stderr.decode(errors='replace').strip()}"
        )
    return r.stdout


def _materialise_pinned(dest: Path) -> None:
    """Write the pinned blobs into `dest`, verifying each hash before use."""
    for rel, want in VERDICT_PIN_BLOBS.items():
        got = _git("rev-parse", f"{VERDICT_PIN_COMMIT}:{rel}").decode().strip()
        if got != want:
            raise SystemExit(
                f"ABORT: pin mismatch for {rel} at {VERDICT_PIN_COMMIT[:12]}.\n"
                f"  pinned blob {want}\n  found  blob {got}\n"
                "  The recorded pin does not describe the recorded commit. Re-pin "
                "deliberately; do not adjust one of the two to make this pass."
            )
        (dest / Path(rel).name).write_bytes(
            _git("cat-file", "blob", f"{VERDICT_PIN_COMMIT}:{rel}"))


def _report_worktree_drift() -> str:
    """Say so, loudly, if the working tree no longer matches the pin.

    Not fatal -- the pinned score is still valid. But silently ignoring a newer
    producer is its own failure mode: the owner edits the file, re-runs, sees the
    same numbers, and concludes their change had no effect. Name it instead.
    """
    drifted = []
    for rel, want in VERDICT_PIN_BLOBS.items():
        p = REPO_ROOT / rel
        if not p.is_file():
            drifted.append(f"{rel}: ABSENT from the working tree")
            continue
        got = _git("hash-object", str(p)).decode().strip()
        if got != want:
            drifted.append(f"{rel}: worktree {got[:12]} != pinned {want[:12]}")
    if drifted:
        print("NOTE: the verdict producer has moved since this pin. The score below "
              "used the PINNED revision and is unaffected; your edit did NOT take "
              "effect here.", file=sys.stderr)
        for d in drifted:
            print(f"      {d}", file=sys.stderr)
        print(f"      To adopt it, re-pin VERDICT_PIN_COMMIT/VERDICT_PIN_BLOBS in "
              f"{Path(__file__).name} deliberately.", file=sys.stderr)
    return "drifted" if drifted else "matches-worktree"


def _load_verdict_module():
    """Import the producer, from the pin by default and the worktree only on request."""
    tmp = Path(tempfile.mkdtemp(prefix="w7-verdict-pin-"))
    if USE_WORKTREE:
        for rel in VERDICT_PIN_BLOBS:
            src = REPO_ROOT / rel
            if not src.is_file():
                raise SystemExit(f"ABORT: DETLOG_VERDICT_USE_WORKTREE=1 but {src} is absent.")
            (tmp / Path(rel).name).write_bytes(src.read_bytes())
    else:
        _materialise_pinned(tmp)

    entry = tmp / Path(VERDICT_ENTRY).name
    sys.path.insert(0, str(tmp))          # so its `import strict_verdict` resolves here
    spec = importlib.util.spec_from_file_location("detlog_compare", entry)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        raise SystemExit(
            f"ABORT: the verdict producer failed to import: {type(exc).__name__}: {exc}\n"
            f"  source: {'WORKING TREE (DETLOG_VERDICT_USE_WORKTREE=1)' if USE_WORKTREE else VERDICT_PIN_COMMIT[:12]}\n"
            "  This scorer has no fallback comparison on purpose. Do not edit the "
            "producer to make this pass -- it is not ours."
        ) from exc

    missing = [n for n in ("self_determinism", "PASS", "FAIL", "NOT_MEASURED")
               if not hasattr(mod, n)]
    if missing:
        raise SystemExit(
            f"ABORT: the verdict producer does not expose {missing}. The interface "
            "moved; update this scorer to match rather than guessing."
        )
    return mod


def _assert_closure_unchanged() -> None:
    """The pin covers a closure, so re-derive the closure instead of trusting it."""
    src = _git("cat-file", "blob",
               f"{VERDICT_PIN_COMMIT}:{VERDICT_ENTRY}").decode(errors="replace")
    imported = set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][\w]*)", src, re.M))
    local = {m for m in imported
             if (REPO_ROOT / "compat-envelope" / f"{m}.py").exists()
             or f"compat-envelope/{m}.py" in VERDICT_PIN_BLOBS}
    unpinned = {m for m in local if f"compat-envelope/{m}.py" not in VERDICT_PIN_BLOBS}
    if unpinned:
        raise SystemExit(
            f"ABORT: the verdict producer now imports {sorted(unpinned)} from "
            "compat-envelope/, which is NOT in VERDICT_PIN_BLOBS.\n"
            "  Pinning only part of the closure leaves the rest reading from the "
            "working tree, which is the exact defect the pin exists to remove.\n"
            "  Add the missing file(s) to the pin deliberately."
        )


DRIFT = "n/a (worktree mode)" if USE_WORKTREE else _report_worktree_drift()
if not USE_WORKTREE:
    _assert_closure_unchanged()
DC = _load_verdict_module()
VERDICT_SHA256 = (
    f"worktree:{_git('hash-object', str(REPO_ROOT / VERDICT_ENTRY)).decode().strip()[:12]}"
    if USE_WORKTREE else
    f"{VERDICT_PIN_COMMIT[:12]}:{VERDICT_PIN_BLOBS[VERDICT_ENTRY][:12]}"
)

HEX = re.compile(r"0x[0-9a-f]+")
BACKENDS = ["ptrace", "kvm", "dbi", "sabre", "e9patch", "liteinst"]

# Provenance of each guest. A cell whose corpus is unstated is a cell nobody can re-run.
CORPUS = {
    "notsc":                ("w27-tsc probe", "scratch/w27-tsc/notsc.c, gcc -O0"),
    "detlog_syscalls":      ("ci-hub/parity pinned reference guest", "ci-hub/parity/guests/detlog_syscalls.c"),
    "heap_fragment_reuse":  ("ci-hub/parity pinned reference guest", "ci-hub/parity/guests/heap_fragment_reuse.c"),
    "stack_deep_recursion": ("ci-hub/parity pinned reference guest", "ci-hub/parity/guests/stack_deep_recursion.c"),
    "stdout_bytes":         ("ci-hub/parity pinned reference guest", "ci-hub/parity/guests/stdout_bytes.c"),
    "bin_true":             ("system binary", "/bin/true"),
    "bin_echo":             ("system binary", "/bin/echo"),
}

# e9patch is NOT a backend (hermit/AGENTS.md "Backend Definition"), and the CLI says so
# itself: "Preprocess the main ELF with e9patch, then use the ptrace runtime". Any green
# it scores is the attached ptracer's, not its own.
INHERITED = {"e9patch": "INHERITED from the attached ptrace runtime — e9patch is ELF "
                        "preprocessing, not a backend; its determinism is the ptracer's"}

# Engagement witness per (cell, backend), collected in a dedicated pass and keyed by
# backend because it was uniform across all 7 cells. A backend that RAN is not a
# backend that DID ANYTHING; without this column an inert fallback scores a perfect
# green. Loaded from mx/engagement.tsv at runtime and cross-checked against this.
NOT_EXERCISED_IF = {
    # e9patch found no rewritable site in any guest here, and produced no artifact.
    "e9patch": lambda w: "candidate_sites=0" in w and "artifact_sha256=none" in w,
}

# Deeper measurements that OVERRIDE this matrix's own 3-run sample. One differing pair
# establishes nondeterminism, so a failure at higher n strictly dominates a pass at
# lower n -- never the reverse. Recording the override rather than silently taking the
# better number is the point.
SELFDET_OVERRIDE = {
    ("detlog_syscalls", "liteinst"): dict(
        tier="self-nondeterministic",
        selfdet="2 outcome classes over 30 runs (17|13)",
        note="OVERRIDE: this matrix's own 3-run sample read 0/3 pairs differing, but a "
             "30-run measurement at this same binary found 2 outcome classes. The "
             "minority class is 13/30, so a 3-run sample misses it about 60% of the "
             "time. Differing records are exactly the 32 clock_gettime(CLOCK_MONOTONIC) "
             "records at a constant 6720 ns = 672 RCB offset. See "
             "experiments/liteinst-detlog-parity_20260807.",
    ),
}


def load(p: Path, policy: str) -> list[str]:
    lines = p.read_text(errors="replace").splitlines()
    return [HEX.sub("HEX", l) for l in lines] if policy == "hex" else lines


def prefix_depth(g, c):
    n = 0
    for a, b in zip(g, c):
        if a != b:
            break
        n += 1
    return n


def cover(g, c):
    sm = difflib.SequenceMatcher(a=g, b=c, autojunk=False)
    m = sum(b.size for b in sm.get_matching_blocks())
    return m, len(g) - m, len(c) - m


def selfdet(paths, policy):
    """Self-determinism over N runs, with the PAIR VERDICT delegated to w5's module.

    THE SPLIT, concretely. `DC.self_determinism` is a PAIR comparison and stays
    that way -- w5 does not have to change their signature for this. What lives
    here is the N-RUN AGGREGATION, because collecting N runs is the collection
    side of the boundary.

    WHY N MATTERS, measured not assumed. Two runs is structurally insufficient:
    liteinst on detlog_syscalls has a 9/30 minority class, so a 2-run sample
    draws both runs from one class and reports a false PASS about 60% of the
    time. A pair count also cannot distinguish "one minority class"
    (liteinst 21|9) from "every run is unique" (dbi: 30 distinct classes in 30
    runs), and those are different defects needing different fixes -- so the
    class census is reported alongside the pair count, never instead of it.
    """
    from collections import Counter
    texts = [p.read_text(errors="replace") for p in paths]
    runs = [load(p, policy) for p in paths]
    pairs = list(itertools.combinations(range(len(runs)), 2))

    bad = 0
    not_measured = 0
    for i, j in pairs:
        # The import-time guard only proves the SYMBOLS exist. A producer can import
        # cleanly and still fail when called -- observed while testing this pin, where
        # renaming a helper left `self_determinism` importable but broken. Fail closed
        # here too, and name the producer, so the traceback does not read as a bug in
        # the collector.
        try:
            v = DC.self_determinism(texts[i], texts[j])["verdict"]
        except Exception as exc:
            raise SystemExit(
                f"ABORT: the verdict producer raised while comparing "
                f"{paths[i].name} vs {paths[j].name}: {type(exc).__name__}: {exc}\n"
                f"  source: {VERDICT_SHA256}\n"
                "  It imported cleanly, so this is a runtime fault in the producer, not "
                "a missing symbol. This scorer has no fallback comparison on purpose."
            ) from exc
        if v == DC.FAIL:
            bad += 1
        elif v == DC.NOT_MEASURED:
            not_measured += 1

    cls = Counter("\n".join(r) for r in runs)
    sizes = "|".join(str(n) for _, n in cls.most_common())
    return (len(runs), len(pairs), bad, len(runs[0]), len(cls), sizes, not_measured)


def load_engagement(root: Path) -> dict:
    out = {}
    p = root / "engagement.tsv"
    if p.exists():
        for r in csv.DictReader(p.open(), delimiter="\t"):
            out[(r["cell"], r["backend"])] = r["witness"]
    return out


def main() -> int:
    root, out_csv = Path(sys.argv[1]), Path(sys.argv[2])
    eng = load_engagement(root)
    cells = [c for c in CORPUS if list(root.glob(f"{c}.ptrace.1.d"))]
    rows, population, accounted = [], 0, {}
    for cell in cells:
        gp = sorted(root.glob(f"{cell}.ptrace.*.d"))
        gold_raw, gold_hex = load(gp[0], "raw"), load(gp[0], "hex")
        Z = len(gold_raw)
        g_runs, g_pairs, g_bad, _, g_cls, g_sizes, g_nm = selfdet(gp, "raw")
        for be in BACKENDS:
            population += 1
            cp = sorted(root.glob(f"{cell}.{be}.*.d"))
            witness = eng.get((cell, be), "NO-WITNESS-COLLECTED")
            row = dict(cell=cell, backend=be, corpus=CORPUS[cell][0],
                       guest_source=CORPUS[cell][1], engagement=witness,
                       golden_selfdet=f"{g_cls} class(es) over {g_runs} runs ({g_sizes})",
                       inherited=INHERITED.get(be, ""))
            if not cp:
                row.update(tier="no-result", note="no runs collected")
                rows.append(row); accounted["no-result"] = accounted.get("no-result", 0) + 1
                continue
            E = len(load(cp[0], "raw"))
            c_runs, c_pairs, c_bad, _, c_cls, c_sizes, c_nm = selfdet(cp, "raw")
            row.update(Z=Z, E=E,
                       selfdet=(f"{c_cls} class{'es' if c_cls != 1 else ''} over {c_runs} runs"
                                f" ({c_sizes})"),
                       selfdet_distinct_classes=c_cls, selfdet_class_sizes=c_sizes,
                       selfdet_differing_pairs=c_bad, selfdet_pairs=c_pairs, selfdet_runs=c_runs,
                       selfdet_not_measured_pairs=c_nm,
                       verdict_source=f"compat-envelope/detlog_compare.py::self_determinism@{VERDICT_SHA256}")
            if E == 0 or Z == 0:
                row.update(tier="no-result",
                           note="0 DETLOG records — no denominator, so no parity claim")
            elif be in NOT_EXERCISED_IF and NOT_EXERCISED_IF[be](witness):
                row.update(tier="not-exercised",
                           note="the backend ran but its own witness says it transformed "
                                "nothing; any agreement here belongs to the fallback path")
            elif c_bad:
                row.update(tier="self-nondeterministic",
                           note="parity WITHHELD: a stream that differs from itself cannot be "
                                "said to differ from the golden by a definite amount")
            else:
                craw, chex = load(cp[0], "raw"), load(cp[0], "hex")
                yr = prefix_depth(gold_raw, craw); mr, dr, ir = cover(gold_raw, craw)
                yh = prefix_depth(gold_hex, chex); mh, dh, ih = cover(gold_hex, chex)
                if yr == Z and E == Z:
                    tier = "byte-identical"
                elif yh == Z and E == Z:
                    tier = "hex-identical"
                else:
                    tier = "diverges"
                row.update(tier=tier, Y_raw=yr, cover_raw=mr, uncovered_raw=dr, inserted_raw=ir,
                           Y_hex=yh, cover_hex=mh, uncovered_hex=dh, inserted_hex=ih,
                           cover_hex_pct=round(100.0 * mh / Z, 1))
            if (cell, be) in SELFDET_OVERRIDE and c_runs < 30:
                ov = SELFDET_OVERRIDE[(cell, be)]
                row["matrix_sample_selfdet"] = row["selfdet"]
                row.update(ov)
                for k in ("Y_raw", "cover_raw", "uncovered_raw", "inserted_raw", "Y_hex",
                          "cover_hex", "uncovered_hex", "inserted_hex", "cover_hex_pct"):
                    row.pop(k, None)   # parity withheld under a failed baseline
            rows.append(row)
            accounted[row["tier"]] = accounted.get(row["tier"], 0) + 1

    cols = ["cell", "backend", "corpus", "guest_source", "tier", "Z", "E",
            "selfdet", "selfdet_runs", "selfdet_pairs", "selfdet_differing_pairs",
            "golden_selfdet", "Y_raw", "cover_raw", "uncovered_raw", "inserted_raw",
            "Y_hex", "cover_hex", "uncovered_hex", "inserted_hex", "cover_hex_pct",
            "selfdet_distinct_classes", "selfdet_class_sizes",
            "selfdet_not_measured_pairs", "verdict_source",
            "inherited", "engagement", "matrix_sample_selfdet", "note"]
    with out_csv.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"{'CELL':21s} {'BACKEND':9s} {'TIER':22s} {'Z':>5s} {'E':>5s} "
          f"{'Yhex':>5s} {'COV%':>6s}  SELFDET")
    for r in rows:
        print(f"{r['cell']:21s} {r['backend']:9s} {r['tier']:22s} "
              f"{r.get('Z',''):>5} {r.get('E',''):>5} {r.get('Y_hex',''):>5} "
              f"{r.get('cover_hex_pct',''):>6}  {r.get('selfdet','-')}"
              + ("   [INHERITED]" if r["inherited"] else ""))
    print()
    print(f"POPULATION: {len(cells)} cells x {len(BACKENDS)} backends = {population}")
    tot = 0
    for t, n in sorted(accounted.items()):
        print(f"   {t:24s} {n:4d}")
        tot += n
    print(f"   {'TOTAL ACCOUNTED':24s} {tot:4d}"
          f"   {'-> sums to population' if tot == population else '-> MISMATCH'}")
    return 0 if tot == population else 1


if __name__ == "__main__":
    raise SystemExit(main())
