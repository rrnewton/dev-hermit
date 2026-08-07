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
import csv, sys, collections, json
path = sys.argv[1]
reader = csv.DictReader(open(path))
if "relaxation_set" not in (reader.fieldnames or []):
    print("check-determinism-earned: REFUSED: schema has no relaxation_set column", file=sys.stderr)
    sys.exit(1)
rows = list(reader)
if not rows:
    print("check-determinism-earned: empty CSV", file=sys.stderr); sys.exit(2)

# Modes that actually execute and compare more than one run. `counter` qualifies:
# collect-reverie-compat runs each cell --reps times (>=2, enforced at the flag) and
# asserts the syscall counter is identical across them. It is the WEAKEST such mode --
# one integer, not stdout, not a log -- but it is not a single run claiming
# determinism, and treating it as one made the whole Reverie path structurally red.
TWO_RUN = {"verify", "counter"}

# THE ACCEPTANCE RULE, and it is deliberately TIER-AWARE.
#
# An earlier cut refused `tier=bitwise` only when the comparator was literally
# "stripped". That is a not-equal test, not an allowlist: an unrecognised
# comparator name sailed through, as did bitwise claims with missing or 0|0
# counts. All three were planted and all three returned PASS.
#
# But a blanket "nonzero counts always" rule is wrong in the other direction:
# the `guest` tier compares stdout+exit and deliberately does NOT compare the log
# stream, so absent counts there are CORRECT rather than missing. Requiring them
# would refuse the 130 legitimate KVM guest-visible rows. So each tier states
# exactly what evidence it needs.
BITWISE_CAPABLE = {"canonical"}          # allowlist: unknown policy => no bitwise
# Every tier a deterministic=1 row may claim. A blank or unrecognised tier is now a
# REFUSAL, not a pass -- see the note on the legacy bypass below.
#   bitwise            DETLOG identical under a bitwise-capable comparator
#   stripped           DETLOG compared under Stripped, with counts
#   stripped-uncounted DETLOG compared under Stripped, count NOT recorded (historical
#                      rows only; explicit and self-describing rather than blank)
#   guest              stdout+exit compared, log deliberately not
#   counter            a syscall counter compared across >=2 reps (weakest)
#   gap                no positive claim
KNOWN_TIERS = {"bitwise", "stripped", "stripped-uncounted", "guest", "counter", "gap"}
COUNTLESS_TIERS = {"guest", "counter", "stripped-uncounted"}
# `gap` means "no positive claim". A row cannot simultaneously be a gap and a
# determinism positive; accepting that combination let a cell assert green while
# declaring it had nothing to assert.
NON_POSITIVE_TIERS = {"gap"}
# Comparators any tier may name. Previously only `bitwise` had an allowlist, so a
# stripped-tier row could cite any string at all and still pass -- the same
# unknown-policy hole, one rung down.
KNOWN_COMPARATORS = {"stripped", "canonical", "syscall-count-across-reps"}
# Comparators that record the ABSENCE of a verdict. These are legitimate values --
# a producer that could not obtain a typed verdict must say so rather than leave the
# field blank, because blank cannot distinguish "no verdict existed" from "a verdict
# existed and was stripped". But they can never AUTHORISE a positive: absence of a
# verdict is a no-result, never a pass.
#
# This was the last fail-open path. The sentinel had been added to the allowlist
# above so that rows carrying it would parse, which also made it a valid basis for
# deterministic=1 at every tier -- exactly defeating the tightening it was
# introduced to support. Refused ahead of the tier logic so no tier can readmit it.
NO_VERDICT_COMPARATORS = {"unavailable:no-verify-json"}


def parse_counts(raw):
    """Return (left, right) or None when the field is absent/malformed."""
    text = (raw or "").strip()
    if not text:
        return None
    if "|" not in text:
        return None
    left, _, right = text.partition("|")
    try:
        return int(left), int(right)
    except ValueError:
        return None


unearned, unlabelled, overtiered, invalid_relaxations = [], [], [], []
non_strict_or_unknown_rows = 0
for i, r in enumerate(rows, start=2):
    raw_relaxations = (r.get("relaxation_set") or "").strip()
    try:
        relaxations = json.loads(raw_relaxations)
    except (json.JSONDecodeError, TypeError) as error:
        invalid_relaxations.append((i, r.get("test_id", ""), f"malformed JSON: {error}"))
        relaxations = None
    if relaxations is not None and (
        not isinstance(relaxations, list)
        or any(not isinstance(item, str) or not item for item in relaxations)
        or len(relaxations) != len(set(relaxations))
    ):
        invalid_relaxations.append(
            (i, r.get("test_id", ""),
             "must be a JSON array of unique non-empty strings")
        )
        relaxations = None
    if relaxations:
        non_strict_or_unknown_rows += 1
    if r.get("deterministic") != "1":
        continue
    mode = r.get("test_mode", "")
    compare = (r.get("verify_compare") or "").strip()
    tier = (r.get("tier") or "").strip()
    parity = (r.get("bitwise_parity") or "").strip()
    counts = parse_counts(r.get("compared_log_messages"))
    tid = r.get("test_id", "")

    if relaxations is None:
        overtiered.append((i, compare, tier or "<blank>",
                           f"{tid} :: deterministic=1 has no valid relaxation binding"))
        continue
    if relaxations:
        overtiered.append((i, compare, tier or "<blank>",
                           f"{tid} :: relaxation_set={relaxations!r}; a relaxed run cannot authorise deterministic=1"))
        continue

    if mode not in TWO_RUN:
        unearned.append((i, mode, tid))
        continue
    if not compare:
        unlabelled.append((i, mode, tid))
        continue

    def reject(why):
        overtiered.append((i, compare, tier or "<blank>", f"{tid} :: {why}"))

    if compare in NO_VERDICT_COMPARATORS:
        reject(f"comparator {compare!r} records that NO verdict was obtained; "
               f"a no-result can never authorise deterministic=1")
        continue

    # THE LEGACY BYPASS IS GONE. Previously a deterministic=1 row with a BLANK tier
    # skipped every tier check, so a positive with an unknown comparator and no counts
    # passed silently. A positive must now name the comparison that earned it.
    if not tier:
        reject("deterministic=1 with no tier — a positive must name the comparison "
               "that earned it (historical rows carry tier=stripped-uncounted)")
    elif tier not in KNOWN_TIERS:
        reject(f"unknown tier {tier!r} (known: {sorted(KNOWN_TIERS)})")
    elif tier in NON_POSITIVE_TIERS:
        reject(f"tier={tier} declares no positive claim, so deterministic=1 "
               f"contradicts it")
    elif compare not in KNOWN_COMPARATORS:
        reject(f"comparator {compare!r} is not a known comparison policy "
               f"(known: {sorted(KNOWN_COMPARATORS)})")
    elif tier == "bitwise":
        if compare not in BITWISE_CAPABLE:
            reject(f"comparator {compare!r} is not bitwise-capable "
                   f"(allowlist: {sorted(BITWISE_CAPABLE)})")
        elif parity != "1":
            reject(f"bitwise_parity={parity!r}, must be '1'")
        elif counts is None:
            reject("compared_log_messages missing or malformed")
        elif counts[0] <= 0 or counts[1] <= 0:
            reject(f"compared_log_messages={counts[0]}|{counts[1]} — an empty "
                   f"comparison matches trivially")
    elif tier == "stripped":
        # The log WAS compared, so it must say over how many messages.
        if counts is None:
            reject("tier=stripped compared a log but has no compared_log_messages")
        elif counts[0] <= 0 or counts[1] <= 0:
            reject(f"tier=stripped with empty comparison {counts[0]}|{counts[1]}")
    elif tier in COUNTLESS_TIERS:
        # guest / counter / stripped-uncounted compare no log (or record no count),
        # so absent counts are CORRECT here rather than missing. Demanding them would
        # refuse the legitimate KVM guest-visible rows and the whole Reverie path.
        # What they still owe is a named comparator.
        if counts is not None and (counts[0] <= 0 or counts[1] <= 0):
            reject(f"tier={tier} recorded an empty comparison {counts[0]}|{counts[1]}")

by_mode = collections.Counter(r["test_mode"] for r in rows if r.get("deterministic") == "1")
blank    = sum(1 for r in rows if not (r.get("deterministic") or "").strip())
print(f"rows={len(rows)}  non_strict_or_unknown={non_strict_or_unknown_rows}  "
      f"deterministic=1 by mode: {dict(by_mode)}  blank(unmeasured)={blank}")

rc = 0
if invalid_relaxations:
    rc = 1
    print(f"\nINVALID relaxation bindings: {len(invalid_relaxations)}", file=sys.stderr)
    for line, tid, why in invalid_relaxations[:10]:
        print(f"  line {line}: test={tid} :: {why}", file=sys.stderr)
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
          f"(a tier claim whose own record does not support it: non-allowlisted "
          f"comparator, bitwise_parity!=1, or missing/zero/malformed counts)", file=sys.stderr)
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
