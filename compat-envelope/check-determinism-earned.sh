#!/usr/bin/env bash
# check-determinism-earned.sh — assert every `deterministic=1` cell EARNED it.
#
# THE RULE: determinism is a claim about run1==run2. Only a mode that actually
# executes a second run and compares it can establish that. `verify` does;
# `strict`, `chaos`, `custom` and `replay` are single runs and must record BLANK
# (unmeasured), never 1 and never 0 — a single run cannot observe either.
#
# WHY THIS GUARD EXISTS: the collector previously wrote `pass => deterministic=1`
# for EVERY mode. That minted 105 single-run determinism claims (strict 102,
# chaos 1, custom 1, replay 1) against 63 that genuinely compared, so most of the
# scorecard's determinism evidence was an artifact of the rule rather than a
# measurement. Nothing failed when that was true, which is exactly why it needs a
# standing check rather than a one-time recompute.
#
# Also asserts the converse: a `deterministic=1` row must say WHAT the two runs
# were compared by (`verify_compare`). `stripped` normalises addresses and tmp
# paths and does not compare the detlog, so it is a weaker claim than bitwise and
# must stay legible in the row instead of hiding behind a bare `1`.
#
# Usage: check-determinism-earned.sh [CSV]        (default: scorecard.csv beside this script)
# Exit:  0 = every determinism claim is earned; 1 = at least one is not.
set -uo pipefail

CSV="${1:-$(dirname "$0")/scorecard.csv}"
[ -f "$CSV" ] || { echo "check-determinism-earned: no such CSV: $CSV" >&2; exit 2; }

python3 - "$CSV" <<'PY'
import csv, sys, collections
path = sys.argv[1]
rows = list(csv.DictReader(open(path)))
if not rows:
    print("check-determinism-earned: empty CSV", file=sys.stderr); sys.exit(2)

TWO_RUN = {"verify"}     # the only mode that executes and compares a second run

# A row may only claim the `bitwise` tier if its own record earned it: the
# comparator was NOT the stripped one AND bitwise_parity is set. Without this a
# producer could write tier=bitwise beside verify_compare=stripped -- exactly the
# over-tiering that made all 346 greens read as DETLOG-bitwise.
STRIPPED_COMPARATORS = {"stripped"}

unearned, unlabelled, overtiered = [], [], []
for i, r in enumerate(rows, start=2):
    if r.get("deterministic") != "1":
        continue
    mode = r.get("test_mode", "")
    compare = (r.get("verify_compare") or "").strip()
    tier = (r.get("tier") or "").strip()
    if mode not in TWO_RUN:
        unearned.append((i, mode, r.get("test_id", "")))
    elif not compare:
        unlabelled.append((i, mode, r.get("test_id", "")))
    if tier == "bitwise" and (
        compare in STRIPPED_COMPARATORS or (r.get("bitwise_parity") or "").strip() != "1"
    ):
        overtiered.append((i, compare, tier, r.get("test_id", "")))

by_mode = collections.Counter(r["test_mode"] for r in rows if r.get("deterministic") == "1")
blank    = sum(1 for r in rows if not (r.get("deterministic") or "").strip())
print(f"rows={len(rows)}  deterministic=1 by mode: {dict(by_mode)}  blank(unmeasured)={blank}")

rc = 0
if unearned:
    rc = 1
    print(f"\nUNEARNED determinism claims: {len(unearned)} "
          f"(single-run mode claiming run1==run2)", file=sys.stderr)
    for line, mode, tid in unearned[:10]:
        print(f"  line {line}: mode={mode} test={tid}", file=sys.stderr)
if unlabelled:
    rc = 1
    print(f"\nUNLABELLED determinism claims: {len(unlabelled)} "
          f"(deterministic=1 with no verify_compare — a bare 1 hides whether the "
          f"comparison was stripped or bitwise)", file=sys.stderr)
    for line, mode, tid in unlabelled[:10]:
        print(f"  line {line}: mode={mode} test={tid}", file=sys.stderr)
if overtiered:
    rc = 1
    print(f"\nOVER-TIERED determinism claims: {len(overtiered)} "
          f"(tier=bitwise on a stripped comparison or without bitwise_parity=1 — "
          f"a bitwise claim must rest on a bitwise comparison)", file=sys.stderr)
    for line, compare, tier, tid in overtiered[:10]:
        print(f"  line {line}: verify_compare={compare!r} tier={tier!r} test={tid}",
              file=sys.stderr)

# POSITIVE CONTROL: a guard that can only ever fail is useless. Assert the
# scorecard still contains genuinely earned determinism, so a recompute that
# blanked EVERYTHING would also be caught.
earned = sum(v for m, v in by_mode.items() if m in TWO_RUN)
if earned == 0:
    rc = 1
    print("\nNO EARNED determinism at all — every cell is unmeasured. That is not a "
          "pass; either the verify cells stopped running or the rule is too strict.",
          file=sys.stderr)

print("\ncheck-determinism-earned: " + ("PASS" if rc == 0 else "FAIL"))
sys.exit(rc)
PY
