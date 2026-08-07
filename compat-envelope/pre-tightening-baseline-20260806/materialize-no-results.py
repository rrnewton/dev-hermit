#!/usr/bin/env python3
"""Materialize the FULL nominal matrix so no cell is silently absent.

The measured CSV has 1025 rows = 205 effective cells x 5 backends. The nominal
corpus is 235 cells, so 150 rows (30 fixtures x 5 backends) are simply NOT
THERE. Describing them in prose is not the same as carrying them: a consumer
that reads the CSV sees 1025 rows and no signal that 150 more were owed. That is
an ambiguous missing row, and it is indistinguishable from a cell nobody thought
to run.

This writes a complete 1175-row matrix in which every cell carries a TYPED
absence_class, so the denominator is legible from the data alone:

  measured                  the cell produced a verdict
  fixture-source-missing    guest .c absent at the measured Hermit SHA; the
                            collector never emitted a row (30 cells x 5)
  reference-unusable-verify ptrace's --verify leg failed, so no parity
                            reference exists for this cell (22 cells)
  reference-unusable-runleg ptrace's --verify leg PASSED but its plain --strict
                            reference run failed, so the reference stdout is
                            empty (1 cell: c-programs/dbi-pid-virtualization,
                            ptrace-run-fail-exit124)

A no-result is never a zero and never a failure: `deterministic` and
`stdout_parity` stay EMPTY on an unmeasured row rather than being filled with 0.
"""
from __future__ import annotations

import argparse
import csv
import io
import subprocess
import sys
from pathlib import Path

RAW = ("compat-envelope/pre-tightening-baseline-20260806/cross-check-w14-raw.csv")
CORPUS_C = "compat-envelope/corpus/corpus-c.tsv"
NOMINAL = 235

OUT_COLS = ["bucket", "test_id", "backend", "absence_class", "outcome",
            "deterministic", "stdout_parity", "reason"]


def committed(ref: str) -> str:
    r = subprocess.run(["git", "show", ref], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"REFUSED: cannot read {ref}", file=sys.stderr)
        raise SystemExit(3)
    return r.stdout


def build(base: str) -> tuple[list[dict], dict]:
    raw = list(csv.DictReader(io.StringIO(committed(f"{base}:{RAW}"))))
    backends = sorted({r["backend"] for r in raw})
    measured = {(r["bucket"], r["test_id"], r["backend"]): r for r in raw}
    measured_cells = sorted({(r["bucket"], r["test_id"]) for r in raw})

    # the nominal-but-unbuildable cells, read from the corpus rather than assumed
    corpus = [ln.split("|")[0] for ln in committed(f"{base}:{CORPUS_C}").splitlines()
              if ln.strip() and not ln.startswith("#")]
    missing = [c for c in corpus if c.startswith("performance/")]

    # ptrace reference classes, derived not hardcoded
    pt = {(r["bucket"], r["test_id"]): r for r in raw if r["backend"] == "ptrace"}
    ref_verify = {k for k, r in pt.items() if r["outcome"] != "pass"}
    ref_runleg = {k for k, r in pt.items()
                  if r["outcome"] == "pass" and r["reason"].startswith("ptrace-run-fail")}

    rows: list[dict] = []
    for (bucket, test_id) in measured_cells:
        for b in backends:
            r = measured[(bucket, test_id, b)]
            cls = "measured"
            if b != "ptrace" and (bucket, test_id) in ref_verify:
                cls = "reference-unusable-verify"
            elif b != "ptrace" and (bucket, test_id) in ref_runleg:
                cls = "reference-unusable-runleg"
            rows.append({"bucket": bucket, "test_id": test_id, "backend": b,
                         "absence_class": cls, "outcome": r["outcome"],
                         "deterministic": r["deterministic"],
                         "stdout_parity": r["stdout_parity"], "reason": r["reason"]})
    for test_id in missing:
        for b in backends:
            rows.append({"bucket": test_id.split("/")[0], "test_id": test_id, "backend": b,
                         "absence_class": "fixture-source-missing", "outcome": "",
                         "deterministic": "", "stdout_parity": "",
                         "reason": "guest source absent at the measured hermit SHA; "
                                   "no row was emitted by the collector"})
    rows.sort(key=lambda r: (r["backend"], r["bucket"], r["test_id"]))
    stats = {"backends": len(backends), "measured_cells": len(measured_cells),
             "missing_cells": len(missing), "rows": len(rows),
             "required": NOMINAL * len(backends)}
    return rows, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    rows, st = build(a.base)
    if st["rows"] != st["required"]:
        print(f"REFUSED: built {st['rows']} rows, nominal matrix requires "
              f"{st['required']} -- the matrix is still incomplete", file=sys.stderr)
        return 3

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=OUT_COLS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    text = buf.getvalue()

    if a.check:
        if not a.out.is_file():
            print(f"REFUSED: {a.out} missing", file=sys.stderr)
            return 3
        if a.out.read_text() == text:
            print(f"REPRODUCIBLE: {a.out.name} byte-identical ({st['rows']} rows, "
                  f"complete nominal matrix)")
            return 0
        print(f"DRIFT: {a.out.name} differs", file=sys.stderr)
        return 1

    a.out.write_text(text)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["absence_class"]] = counts.get(r["absence_class"], 0) + 1
    print(f"wrote {a.out} — {st['rows']} rows = {NOMINAL} nominal x {st['backends']} backends")
    for k in sorted(counts):
        print(f"  {k:28s} {counts[k]:>5d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
