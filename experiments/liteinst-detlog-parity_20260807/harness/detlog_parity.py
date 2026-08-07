#!/usr/bin/env python3
"""Score LiteInst DETLOG parity against the ptrace golden, per cell, with denominators.

THREE POLICIES, ALL REPORTED. A single number here would be a lie in one direction
or the other, so every cell carries all three:

  raw      byte-exact DETLOG lines.
  hex      0x<hex> -> HEX. This is NOT a policy I invented to manufacture a green:
           it is verbatim the normalisation ci-hub/parity/prefix_depth.sh already
           applies in commits() (sed -E 's/0x[0-9a-f]+/HEX/g'). Addresses are the
           one field a preload backend cannot match, because LD_PRELOAD changes the
           environment block size and therefore the initial stack pointer.
  cover    order-preserving coverage: of the golden's Z records, how many appear in
           the candidate's stream in the same order (a longest-common-subsequence,
           i.e. exactly what diff computes). Insertions are reported SEPARATELY and
           never subtracted from the score, so "LiteInst adds 736 runtime records"
           cannot masquerade as agreement.

Nothing is dropped or masked. Coverage is a second measurement, not a relaxation of
the first: a cell that is 6/96 on prefix depth still reads 6/96 here.
"""
from __future__ import annotations
import difflib, itertools, re, sys, json
from pathlib import Path

HEX = re.compile(r"0x[0-9a-f]+")


def load(p: Path, policy: str) -> list[str]:
    lines = p.read_text(errors="replace").splitlines()
    if policy == "hex":
        lines = [HEX.sub("HEX", l) for l in lines]
    return lines


def prefix_depth(gold: list[str], cand: list[str]) -> int:
    n = 0
    for a, b in zip(gold, cand):
        if a != b:
            break
        n += 1
    return n


def coverage(gold: list[str], cand: list[str]) -> tuple[int, int, int]:
    """-> (matched, deleted_from_gold, inserted_by_cand)"""
    sm = difflib.SequenceMatcher(a=gold, b=cand, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks())
    return matched, len(gold) - matched, len(cand) - matched


def self_det(paths: list[Path], policy: str) -> tuple[int, int, int, int]:
    """-> (runs, pairs, differing_pairs, denominator)"""
    runs = [load(p, policy) for p in paths]
    denom = len(runs[0])
    bad = 0
    pairs = list(itertools.combinations(range(len(runs)), 2))
    for i, j in pairs:
        a, b = runs[i], runs[j]
        if len(a) != len(b) or any(x != y for x, y in zip(a, b)):
            bad += 1
    return len(runs), len(pairs), bad, denom


def main() -> int:
    root = Path(sys.argv[1])
    cells = sorted({p.name.split(".")[0] for p in root.glob("*.ptrace.1.d")})
    out = []
    print(f"{'CELL':22s} {'POLICY':6s} {'Z':>6s} {'E':>6s} {'Yprefix':>8s} "
          f"{'COVER':>6s} {'DEL':>5s} {'INS':>6s}  SELFDET(ptrace)  SELFDET(liteinst)")
    for cell in cells:
        gp = sorted(root.glob(f"{cell}.ptrace.*.d"))
        cp = sorted(root.glob(f"{cell}.liteinst.*.d"))
        if not gp or not cp:
            print(f"{cell:22s} MISSING-ARM  ptrace={len(gp)} liteinst={len(cp)}")
            continue
        for policy in ("raw", "hex"):
            gold = load(gp[0], policy)
            cand = load(cp[0], policy)
            if not gold:
                print(f"{cell:22s} {policy:6s} NO-GOLDEN (0 DETLOG records) — no denominator, cell void")
                continue
            y = prefix_depth(gold, cand)
            m, d, i = coverage(gold, cand)
            gr, gpr, gbad, gden = self_det(gp, policy)
            cr, cpr, cbad, cden = self_det(cp, policy)
            print(f"{cell:22s} {policy:6s} {len(gold):6d} {len(cand):6d} {y:8d} "
                  f"{m:6d} {d:5d} {i:6d}  {gbad}/{gpr} bad ({gden:4d})  {cbad}/{cpr} bad ({cden:4d})")
            out.append(dict(cell=cell, policy=policy, Z=len(gold), E=len(cand),
                            prefix_depth=y, covered=m, golden_unmatched=d,
                            candidate_inserted=i,
                            ptrace_selfdet_runs=gr, ptrace_selfdet_pairs=gpr,
                            ptrace_selfdet_differing=gbad, ptrace_denom=gden,
                            liteinst_selfdet_runs=cr, liteinst_selfdet_pairs=cpr,
                            liteinst_selfdet_differing=cbad, liteinst_denom=cden))
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
