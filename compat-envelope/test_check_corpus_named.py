#!/usr/bin/env python3
"""Two-sided bracket for the corpus-naming gate.

NEGATIVE side: plant the violating case (a backend ratio with no corpus) and
confirm the gate REFUSES it.
POSITIVE side: plant the qualifying case (the same ratio, corpus named) and
confirm the gate ACCEPTS it -- so a gate that simply refused everything, or that
had been made inert, would fail this file rather than look green.

Run: python3 compat-envelope/test_check_corpus_named.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_corpus_named import scan  # noqa: E402
from corpus_registry import REGISTRY, describe, resolve_for_csv  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(label)


# ---------------------------------------------------------------- NEGATIVE ---
# Each of these is a real shape observed in the tree on 2026-08-07.
REFUSED = [
    "  OK: e9patch det 179/200 (floor 150)",                       # collect-fullcorpus.sh
    "| **e9patch** | 183/184 (99.46%) stdout parity AND det |",    # REPORT.md:133
    "ptrace **179/200** L2, KVM det 130/200",                      # SCORECARD.md:8
    "dbi               21/23",                                     # REPORT.md:207
    "sabre reach: patched_sites=4",                                # a bare scoped count
    "kvm parity 65%",                                              # percentage form
]
for line in REFUSED:
    v = scan([line], "neg")
    check(len(v) == 1, f"NEGATIVE not refused: {line!r} -> {len(v)} violations")

# An invented corpus name must not satisfy the gate: otherwise any emitter could
# pass by printing `corpus=whatever`.
v = scan(["corpus=made-up-population", "  e9patch det 20/20"], "neg")
check(len(v) >= 1, "an unregistered corpus= declaration was accepted")

# ---------------------------------------------------------------- POSITIVE ---
ACCEPTED = [
    "  OK: e9patch det 179/200 over corpus=fullcorpus (floor 150)",
    "e9patch reach 20/20 on the e9patch-dedicated corpus",
    "e9patch 4/137 built on the fullcorpus population",
]
for line in ACCEPTED:
    v = scan([line], "pos")
    check(not v, f"POSITIVE wrongly refused: {line!r} -> {[str(x) for x in v]}")

# A registered declaration qualifies the lines beneath it (the emitter shape).
block = [
    "corpus=fullcorpus  population=235 cells",
    "  OK: e9patch det 179/200 (floor 150)",
    "  OK: kvm det 130/200 (floor 120)",
]
check(not scan(block, "pos"), "a registered corpus= declaration failed to qualify its block")

# A markdown heading qualifies its section...
section = [
    "## e9patch on the e9patch-dedicated corpus",
    "e9patch reach 20/20",
]
check(not scan(section, "pos"), "a corpus-naming heading failed to qualify its section")

# ...but --strict-lines still reports it, which is how the REPORT.md gap is seen.
check(len(scan(section, "pos", strict_lines=True)) == 1,
      "--strict-lines failed to report a section-qualified-only ratio")

# A later heading must CLEAR the previous section's corpus, or one heading would
# silently qualify the whole rest of the document.
drifted = [
    "## e9patch on the e9patch-dedicated corpus",
    "e9patch reach 20/20",
    "## Whole-tree summary",
    "e9patch 183/184 stdout parity",
]
check(len(scan(drifted, "pos")) == 1,
      "a corpus heading leaked past the next heading")

# ------------------------------------------------------------- NON-RATIOS ---
# Prose that mentions a backend but states no ratio must not be refused, or the
# gate becomes noise and gets switched off.
for line in ["e9patch rewrites the main ELF", "see compat-envelope/corpus/corpus-c.tsv",
             "measured at 2026/08/07 on hermit/ci path"]:
    check(not scan([line], "neutral"), f"non-ratio line wrongly refused: {line!r}")

# ---------------------------------------------------------------- REGISTRY ---
check(resolve_for_csv("compat-envelope/e9patch-scorecard.csv").name == "e9patch-dedicated",
      "resolve_for_csv missed e9patch-scorecard.csv")
check(resolve_for_csv("/abs/path/fullcorpus-scorecard.csv").name == "fullcorpus",
      "resolve_for_csv missed fullcorpus-scorecard.csv")
check(resolve_for_csv("ignored/some-unknown.csv") is None,
      "resolve_for_csv invented a corpus for an unregistered CSV")
for name in REGISTRY:
    check("corpus=" in describe(name), f"describe({name}) omitted the corpus= token")
# describe() must refuse an unknown name rather than return a plausible string.
try:
    describe("not-a-corpus")
    check(False, "describe() accepted an unregistered corpus")
except KeyError:
    check(True, "describe() refuses unregistered")

# ------------------------------------------------------- WIDE-TABLE SCOPE ---
# The backend lives in the COLUMN HEADER and the ratio in the ROW. A line-local
# check calls this clean, which is exactly how render-scorecard.rs --tsv escaped
# the gate while carrying no population at all.
tsv_unqualified = [
    "bucket\tptrace\te9patch_stdout_parity_pct\te9patch_det_pct\te9patch_ran",
    "applications\t1\t100.0\t100.0\t1/1",
    "c-programs\t159\t93.7\t93.7\t149/159",
]
check(len(scan(tsv_unqualified, "tsv")) == 2,
      f"wide-table rows escaped the gate: {[str(x) for x in scan(tsv_unqualified,'tsv')]}")

tsv_qualified = ["# corpus=fullcorpus"] + tsv_unqualified
check(not scan(tsv_qualified, "tsv"),
      "a corpus= declaration above the table failed to qualify its rows")

# A blank line closes the table scope, so unrelated later numbers are not
# retroactively treated as backend ratios.
check(not scan(tsv_unqualified[:1] + [""] + ["total elapsed 12/13 minutes"], "tsv"),
      "table scope leaked past a blank line")

# A one-backend header is not a wide table; do not open a scope on it.
check(not scan(["bucket\tptrace", "applications\t1"], "tsv"),
      "a single-backend header wrongly opened a table scope")

# ------------------------------------------------------------------- VERDICT ---
# This must stay LAST. An assertion placed above a later block of checks records
# their failures into FAILURES and then never looks at them again -- an inert
# test that reports ok. Keep every check above this line.
if FAILURES:
    print(f"FAIL: {len(FAILURES)} of {CHECKS} checks failed")
    for f in FAILURES:
        print(f"  - {f}")
    raise SystemExit(1)
print(f"ok: {CHECKS} checks passed "
      f"({len(REFUSED)} planted violations refused, {len(ACCEPTED)} qualified ratios accepted, "
      f"wide-table scope bracketed both ways)")
