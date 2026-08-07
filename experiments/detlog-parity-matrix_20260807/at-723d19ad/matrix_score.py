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
import csv, difflib, itertools, json, re, sys
from pathlib import Path

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
    runs = [load(p, policy) for p in paths]
    pairs = list(itertools.combinations(range(len(runs)), 2))
    bad = sum(1 for i, j in pairs if runs[i] != runs[j])
    # Distinct outcome classes. A pair count cannot distinguish "one minority class"
    # from "every run is unique", and those are different defects.
    from collections import Counter
    cls = Counter("\n".join(r) for r in runs)
    sizes = "|".join(str(n) for _, n in cls.most_common())
    return len(runs), len(pairs), bad, len(runs[0]), len(cls), sizes


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
        g_runs, g_pairs, g_bad, _, g_cls, g_sizes = selfdet(gp, "raw")
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
            c_runs, c_pairs, c_bad, _, c_cls, c_sizes = selfdet(cp, "raw")
            row.update(Z=Z, E=E,
                       selfdet=(f"{c_cls} class{'es' if c_cls != 1 else ''} over {c_runs} runs"
                                f" ({c_sizes})"),
                       selfdet_distinct_classes=c_cls, selfdet_class_sizes=c_sizes,
                       selfdet_differing_pairs=c_bad, selfdet_pairs=c_pairs, selfdet_runs=c_runs)
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
