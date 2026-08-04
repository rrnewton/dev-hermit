# Transition design: the executed/filtered count schema tightening

**Slug:** `count-schema-tightening-transition`
**Date:** 2026-08-04
**Author:** successor coordinator (Opus 4.8), lane owner of `reject_missing_or_filtered`
**Coordinates with:** `emit_executed_and_filtered` (hermit-231b, producer)
**Governs:** `ci-hub/lib/validate_status.rs` `is_clean_full_pass` and `hermit/validate.sh`
`append_validation_ledger`.

## One-line problem

A green must carry what it verified (`da98bdd` AGENTS.md:270-275: full profile, nonzero
executed, zero unexpected filtering, zero failures). The consumer was tightened to enforce
that at `ea43e23`. But **the producer travels with the branch**, so tightening the consumer
ahead of the producers strands every receipt written by a producer that does not yet emit the
counts — identically to the `bfb0a9ef` anchor transition that already stranded 57/74 PRs.

## Measured blast radius (established, not inferred — 2026-08-04)

Source: `ignored/validate-run-ledger.jsonl`, 260 rows.

- **35 / 35** clean+anchored+full-profile+full-selection **`pass`** receipts (i.e. 100% of
  everything previously VALIDATED) **flip to NOT-VALIDATED** under `ea43e23`. **Zero** carry
  counts today.
- `schema_version` distribution: `{1: 76, 2: 20, 3: 162, 4: 2}`.
- **The gate is already LIVE, strict-on-everything, at `ea43e23` on `origin/main`.** It is the
  live landing gate, not a future risk:
  - `land-pr.sh:183-188` — GitHub-free landing calls `validate-status --sha`; any non-VALIDATED
    verdict **ABANDONS the land** (rc 4). Escape hatch: a pre-existing `locally-validated` label.
  - `apply-local-label` (ci-hub.rs:3044) mints that label **only** on `Verdict::Validated` — same
    strict predicate. So **no new label can be minted**; only PRs that already held the label
    before `ea43e23` can still land GitHub-free.
  - `parallel-prevalidate.sh:148-153` — non-zero verdict rc ⇒ "NOT landable".

Net: the ledger-PASS landing path is 100% dead right now; only pre-existing labels leak through.

## Why `schema_version` alone cannot key the rule (the divergence trap)

The three live writers disagree about what a version number means:

| Writer | stamps `schema_version` | emits counts? |
|---|---|---|
| `aggregate.py:355` | **1** | **yes** (unconditional, :365-366) |
| `hermit/validate.sh:1037` (main) | **3** | **no** |
| PR branch `35ce59f3` | **4** | **no** |

`aggregate.py` carries counts under schema 1; schema 3 and 4 both exist *without* counts. Keying
strictness on "schema ≥ N" is therefore wrong on day one, and it is exactly the "two mechanisms
encoding one judgement" failure that split the `-j` default across `:3655`/`:3769`. The condition
("this receipt measured coverage") must **travel with the value** as the presence of the count
fields, not be inferred from an out-of-band integer the writers do not maintain coherently.

## The three transition options

1. **Grace period** — accept absent counts until a date/commit, then reject.
   *Cheap; the deadline is invisible state that gets forgotten, so the gate either never tightens
   or fires as a cliff. Does not generalize.*
2. **Explicit rebase requirement** — reject immediately, publish the affected list.
   *Honest and immediate, but costs a simultaneous rebase on every open PR, stacked on top of the
   57/74 already owing a `bfb0a9ef` rebase — and it recreates the problem at the next tightening.
   This is what `ea43e23` did, without the list, which is why we are latently broken now. It is
   also the model `emit_executed_and_filtered`'s task text currently assumes ("pre-writer PRs must
   rebase onto it and validate at the rebased SHA as one operation").*
3. **Version-aware acceptance** — the consumer applies the rule that matches each receipt's
   declared capability. *More code; strands nobody; monotone (old receipts keep their old trust,
   new receipts are strictly gated); self-liquidating (the grandfather branch is schema/presence-
   based, not time-based, so it never expires-and-strands); and it is the **only** option that does
   not recreate this problem the third time we tighten the schema.*

## CHOICE: version-aware acceptance, presence-keyed with a schema escalator

Rule for `is_clean_full_pass` (over and above the unchanged clean/anchored/full-profile/
full-selection/`result==pass` gates):

```
count_capable = schema_version >= COUNTS_SCHEMA   // new writer's clean anchor
counts_present = executed_tests.is_some() && filtered_tests.is_some()

if count_capable || counts_present:
    // STRICT: enforce what the receipt is able to prove
    require executed_tests == Some(n) with n > 0 && filtered_tests == Some(0)
else:
    // GRANDFATHER: a genuinely pre-count receipt — the pre-ea43e23 rule
    accept (clean/full/full/pass already checked)
```

Why this is correct for every live writer:

| Writer | count_capable | counts_present | branch taken | verdict on a real full green |
|---|---|---|---|---|
| `aggregate.py` (schema 1, counts) | no | **yes** | STRICT | correct — it can prove coverage, so we check it |
| old `validate.sh` (schema 3, none) | no | no | GRANDFATHER | correct — pre-count, un-breaks the 35 flipped rows |
| branch `35ce59f3` (schema 4, none) | no | no | GRANDFATHER | correct — pre-count |
| **new** `validate.sh` (schema `COUNTS_SCHEMA`, counts) | **yes** | yes | STRICT | correct — new contract enforced |
| new `validate.sh` with a bug (schema `COUNTS_SCHEMA`, none) | **yes** | no | STRICT ⇒ reject | correct — catches "should have emitted but didn't" |

The schema escalator exists solely so a count-capable writer that emits *nothing* (a bug) is still
caught, which pure presence-keying cannot do. Presence handles `aggregate.py`'s old-schema-with-
counts case that pure version-keying cannot. Both clauses are needed; each covers the other's gap.

## Consequence for sequencing (this changes the wired graph dependency)

`reject_missing_or_filtered` is currently blocked behind `emit_executed_and_filtered`
("producer first, gate second"). That ordering is mandatory **only under the strict-on-everything
model** (option 2), where the gate strands old receipts until the producer + rebases arrive.

**Under version-aware acceptance the ordering decouples:** the safe (version-aware) consumer
grandfathers old receipts, so it can land **first** and **immediately un-break the currently-live
drain**, restoring the 35 flipped receipts to VALIDATED without waiting for the producer or a mass
rebase. New receipts then pick up strict enforcement automatically as `COUNTS_SCHEMA` writers roll
out. Recommendation to coordinator: replace `ea43e23`'s strict-on-everything predicate with the
version-aware predicate as a hotfix, and relax (not remove) the `emit → reject` dependency to
"emit upgrades enforcement," not "emit unblocks the gate."

## Coordination contract with hermit-231b (`emit_executed_and_filtered`)

To keep producer and consumer encoding ONE judgement (avoid the `:3655/:3769` divergence):

1. **Pick `COUNTS_SCHEMA = 5`.** 1/2/3 are in use and 4 is already contaminated in the ledger
   with null-count rows. 5 is the first clean anchor. The count-emitting `validate.sh` stamps
   `schema_version: 5`.
2. **Emit both counts UNCONDITIONALLY** at the write point (use `0` where appropriate, never
   `null`) via `nonzero_result.py --ledger-fields`. "Always present for a count-capable writer" is
   the invariant that makes presence a sound discriminator and lets the schema-5 STRICT branch
   treat absent counts as a defect.
3. **Single source of truth for the boundary.** `COUNTS_SCHEMA` is defined once (const in
   `validate_status.rs`, referenced in this doc); the writer's `schema_version: 5` and the
   consumer's `>= 5` cite that one definition. Do not hard-code the integer in two places with
   separate comments.
4. **Standalone hermit** (no dev-hermit parent) must still emit the fields from its own banners and
   must not fabricate counts — a real zero-test run emits `executed_tests: 0`, which STRICT then
   correctly rejects.

The next tightening (schema 6, e.g. adding discovered/selected counts) reuses this machinery
verbatim: bump the anchor, add the clause, grandfather everything below.
