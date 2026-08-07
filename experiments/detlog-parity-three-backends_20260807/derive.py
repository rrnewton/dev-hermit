#!/usr/bin/env python3
"""Recompute every figure in README.md from raw/dl-*.d. `--check` re-derives.

WHY THE RAW LOGS ARE COMMITTED HERE. They were produced into `scratch/`, which is
gitignored, so the evidence for this measurement was transient: a scratch clean
would have left the numbers with nothing behind them. 368 KB of ASCII is a cheap
price for a result that can be re-derived by someone else.

DENOMINATORS ARE NOT SHARED AND ARE ALWAYS PRINTED BOTH WAYS. The three backends
emit 141, 368 and 1245 detlog records for the same guest, so a single "parity %"
is not defined: the SAME pair reads 2.8% or 1.1% depending on which side divides.
Every cross-backend row therefore carries both.
"""
from __future__ import annotations
import argparse, csv, io, sys
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
BACKENDS = ["ptrace", "sabre", "liteinst"]


def recs(b: str, rep: int) -> list[str]:
    return (RAW / f"dl-{b}-{rep}.d").read_text().splitlines()


def lcp(a: list[str], b: list[str]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def build() -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["kind", "subject", "denominator_a", "denominator_b", "differing",
                "common_prefix", "prefix_over_a_pct", "prefix_over_b_pct",
                "identical", "self_det_a", "self_det_b", "note"])

    self_det = {}
    for b in BACKENDS:
        r1, r2 = recs(b, 1), recs(b, 2)
        differing = sum(1 for x, y in zip(r1, r2) if x != y) + abs(len(r1) - len(r2))
        self_det[b] = differing == 0
        w.writerow(["self-determinism", b, len(r1), len(r2), differing, "", "", "",
                    r1 == r2, "PASS" if self_det[b] else "FAIL", "", "run1 vs run2, whole stream"])

    for a, b in combinations(BACKENDS, 2):
        A, B = recs(a, 1), recs(b, 1)
        p = lcp(A, B)
        w.writerow(["cross-backend-parity", f"{a} vs {b}", len(A), len(B), "", p,
                    f"{p / len(A) * 100:.1f}", f"{p / len(B) * 100:.1f}", A == B,
                    "PASS" if self_det[a] else "FAIL", "PASS" if self_det[b] else "FAIL",
                    "denominators differ; both shown deliberately"])
    return out.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE / "results.csv")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    text = build()
    if a.check:
        if not a.out.is_file():
            print(f"REFUSED: {a.out} missing", file=sys.stderr)
            return 3
        if a.out.read_text() == text:
            print(f"REPRODUCIBLE: {a.out.name} is byte-identical")
            return 0
        print(f"DRIFT: {a.out.name} differs from a fresh derivation", file=sys.stderr)
        return 1
    a.out.write_text(text)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
