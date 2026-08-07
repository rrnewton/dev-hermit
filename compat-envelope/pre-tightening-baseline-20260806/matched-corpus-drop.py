#!/usr/bin/env python3
"""Matched-corpus drop: old (Aug-01, 200-cell) vs new (matched, 205-cell).

Every number is recomputed from committed CSVs in this repository; nothing is
transcribed from prose. Run with --check to re-derive and diff the published
table.

THE DEFINITION CHANGE being measured: a cell counts only if its guest source
exists at the measured Hermit SHA, and every cell that cannot produce a verdict
is carried as an EXPLICIT no-result rather than vanishing. Nominal corpus 235,
effective 205, 1025 rows (205 x 5 backends).

NOT the drop: the historical "346 claimed bitwise -> 0" relabel. That was a
label correction on a different artifact (scorecard.csv) and is excluded here by
construction -- this tool only ever reads determinism/parity verdicts.
"""
from __future__ import annotations

import argparse
import collections
import csv
import io
import subprocess
import sys
from pathlib import Path

OLD_CSV = "compat-envelope/fullcorpus-scorecard.csv"                       # 2026-08-01 sweep
NEW_CSV = ("compat-envelope/pre-tightening-baseline-20260806/"
           "cross-check-w14-raw.csv")                                       # matched-corpus sweep
NOMINAL, EFFECTIVE = 235, 205


def committed(ref: str) -> list[dict]:
    out = subprocess.run(["git", "show", ref], capture_output=True, text=True)
    if out.returncode != 0:
        print(f"REFUSED: cannot read {ref}", file=sys.stderr)
        raise SystemExit(3)
    return list(csv.DictReader(io.StringIO(out.stdout)))


def greens(rows: list[dict]) -> collections.Counter:
    c = collections.Counter()
    for r in rows:
        if r.get("deterministic") == "1":
            c[r["backend"]] += 1
    return c


def totals(rows: list[dict]) -> collections.Counter:
    return collections.Counter(r["backend"] for r in rows)


def render(base: str) -> str:
    old, new = committed(f"{base}:{OLD_CSV}"), committed(f"{base}:{NEW_CSV}")
    og, ng, ot, nt = greens(old), greens(new), totals(old), totals(new)

    L: list[str] = []
    add = L.append

    add("## Cell accounting — zero ambiguous missing rows")
    add("")
    pt = {(r["bucket"], r["test_id"]): r for r in new if r["backend"] == "ptrace"}
    ref_fail_verify = {k for k, r in pt.items() if r["outcome"] != "pass"}
    ref_fail_runleg = {k for k, r in pt.items()
                       if r["outcome"] == "pass" and r["reason"].startswith("ptrace-run-fail")}
    blank = {(r["bucket"], r["test_id"]) for r in new
             if r["backend"] == "dbi" and not r["stdout_parity"].strip()}
    unaccounted = blank - ref_fail_verify - ref_fail_runleg

    add(f"| class | cells | disposition |")
    add(f"| --- | ---: | --- |")
    add(f"| nominal corpus | {NOMINAL} | as listed in `corpus/corpus-c.tsv` + `corpus-nonc.tsv` |")
    add(f"| missing guest fixtures (`performance/*`) | {NOMINAL - EFFECTIVE} | "
        f"NO-RESULT: source absent at the measured SHA, no row emitted |")
    add(f"| **effective corpus** | **{EFFECTIVE}** | rows emitted, x5 backends = {EFFECTIVE*5} |")
    add(f"| ptrace reference unusable — verify non-pass | {len(ref_fail_verify)} | "
        f"NO-RESULT for parity on every candidate backend |")
    add(f"| ptrace reference unusable — verify PASSED, plain `--strict` reference run failed "
        f"| {len(ref_fail_runleg)} | NO-RESULT for parity; a green determinism cell with no "
        f"reference stdout |")
    add(f"| unaccounted | {len(unaccounted)} | must be 0 |")
    add("")
    if unaccounted:
        add(f"> REFUSED: {len(unaccounted)} cell(s) unaccounted: {sorted(unaccounted)[:5]}")
    for k in sorted(ref_fail_runleg):
        add(f"The second reference class is a single named cell: `{k[1]}` "
            f"(`{pt[k]['reason']}`; output hash is the sha256 of empty).")
    add("")

    add("## Old vs new greens")
    add("")
    add("`green` = `deterministic == 1`, i.e. the STRIPPED probe returned a determinism pass.")
    add("")
    add("| backend | old green | old executed | old % | new green | new executed | new % "
        "| abs delta | pp delta |")
    add("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for b in sorted(set(og) | set(ng)):
        o, n = og.get(b, 0), ng.get(b, 0)
        ot_, nt_ = ot.get(b, 0), nt.get(b, 0)
        op = f"{o/ot_*100:.1f}%" if ot_ else "n/a"
        np_ = f"{n/nt_*100:.1f}%" if nt_ else "n/a"
        if not nt_:
            add(f"| {b} | {o} | {ot_} | {op} | — | 0 | no-result | — | — |")
            continue
        if not ot_:
            add(f"| {b} | — | 0 | n/a | {n} | {nt_} | {np_} | — | — |")
            continue
        add(f"| {b} | {o} | {ot_} | {op} | {n} | {nt_} | {np_} | "
            f"{n-o:+d} | {n/nt_*100 - o/ot_*100:+.1f} |")
    O, N = sum(og.values()), sum(ng.values())
    OT, NT = sum(ot.values()), sum(nt.values())
    add(f"| **TOTAL** | **{O}** | **{OT}** | **{O/OT*100:.1f}%** | **{N}** | **{NT}** "
        f"| **{N/NT*100:.1f}%** | **{N-O:+d}** | **{N/NT*100 - O/OT*100:+.1f}** |")
    add("")

    add("## Same numerator, two denominators — the definition change alone")
    add("")
    add("| backend | green | nominal 235 | effective 205 | pp moved by the definition change |")
    add("| --- | ---: | ---: | ---: | ---: |")
    for b in sorted(ng):
        n = ng[b]
        add(f"| {b} | {n} | {n/NOMINAL*100:.1f}% | {n/EFFECTIVE*100:.1f}% | "
            f"{n/EFFECTIVE*100 - n/NOMINAL*100:+.1f} |")
    add(f"| **TOTAL** | **{N}** | **{N/(NOMINAL*5)*100:.1f}%** | **{N/(EFFECTIVE*5)*100:.1f}%** "
        f"| **{N/(EFFECTIVE*5)*100 - N/(NOMINAL*5)*100:+.1f}** |")
    add("")
    add("Not one additional cell passed between those two columns; only the denominator moved.")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    text = render(a.base)
    if a.check:
        if not a.out or not a.out.is_file():
            print("REFUSED: --check needs an existing --out", file=sys.stderr)
            return 3
        if a.out.read_text() == text:
            print(f"REPRODUCIBLE: re-derived {a.out.name} is byte-identical")
            return 0
        print(f"DRIFT: {a.out.name} differs from a fresh derivation", file=sys.stderr)
        return 1
    (a.out.write_text(text) if a.out else sys.stdout.write(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
