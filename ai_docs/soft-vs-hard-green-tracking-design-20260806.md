# Soft green vs hard green — recording the class on the ledger row

**Task:** `soft-green-vs-hard-green-is-not-tracked-anywhere-in-ci-hub` (P0, OWNER)
**Date:** 2026-08-06 · **Author:** hermit-design
**Status:** designed + implemented locally in ci-hub; 27/27 brackets pass; no validate run, no egress.
**Code:** `ci-hub/validate/green_class.py` · **Brackets:** `ci-hub/validate/test_green_class.py`
**Evidence base:** ci-hub at working-tree HEAD; ledger `ignored/validate-run-ledger.jsonl` @ 585 rows.

---

## 0. The definitions

**HARD green** — validation actually executed at *this exact head*.
**SOFT green** — validation executed at an *ancestor* head and is being **speculatively trusted**
here, because the head was rebased and nothing on the branch changed.

Soft green is a deliberate policy, not a bug: *"We DO speculatively trust rebased PRs that were green
just before."* The defect is that nothing records **which kind a green is**, so the speculative bet is
invisible to every consumer that acts on it.

---

## 1. What is actually true today — a correction worth making before designing

The task states the distinction is "NOT IMPLEMENTED … not tracked anywhere in ci-hub." That is right
about the ledger and the landing predicate, and **wrong about ci-hub as a whole** in a way that
changes the design. Measured at HEAD:

**(a) The ledger genuinely cannot express it.** `HistoryRow` (`ci-hub/lib/records.rs:95`) has exactly
one SHA field, `commit` (`:117`), which doubles as "the head this row describes" and "the SHA
validation ran on". No `validated_head_sha`, no `inherited_from`, no soft/hard flag. The two
SHA-adjacent booleans, `commit_anchored` (`:119`) and `tree_dirty` (`:121`), describe *this run versus
`commit`* — neither catches a carry-forward, because a carry-forward writer sets them just as easily.

**(b) The landing predicate has no class clause.** `qualifying-receipt.json` `require{}` is
`{commit_anchored, tree_dirty, profile, selection_mode, result, failures_max, executed_tests_min}`.

**(c) But a soft-green mechanism DOES exist — somewhere else.**
`ci-hub/landing/rebase_wrapper.py` (1208 lines) models soft green as a **confidence level**, with a
closed judgement set (`retained-soft-green` | `needs-full-validate`), refusal-on-absent-judgement
(`derive_verdict`, `:280`), and two distinct levels: `soft-green(zero-conflict)` and
`soft-green(resolver-judged)`. It keeps its records in a **separate store**,
`ignored/rebase-records.jsonl`.

**(d) And that mechanism does NOT let soft substitute for hard.** Its landability conjunction is
`soft_green AND base_clears_floor AND receipt_present`, where `receipt_present` means
`ci-hub validate-status --sha Z` reads VALIDATED **at the pushed head**. Its own refusal text says so:
*"no-receipt-at-pushed-head: … The push rewrites the head, so a receipt on the pre-push SHA does not
count — that is the gap that cost an afternoon."*

**(e) There is no soft-green producer at all.** `validate.sh` and `scripts/validate.rs` both stamp the
head they actually ran on. Measured on the live ledger:

| Measurement | Value |
| --- | ---: |
| Rows | 585 |
| Rows carrying **any** of `validated_head_sha` / `inherited_from` / `green_class` / `soft` | **0** |
| Rows classified **hard** by the derivation in §4 | **585 (100%)** |
| Rows classified soft | 0 |

### Why this correction matters

The natural framing — *"a soft green reads identical to a hard one, so receipts are being laundered
today"* — is not what is happening. **Today's safety comes from an absence, not a guard**: no soft
green can masquerade as hard because nothing writes one.

That is the fragile kind of safety. It holds exactly until the first soft producer lands, and **three
in-flight workstreams are each designed to be that producer**:

1. `green-inheritance-test-selection-anchored-on-full-main-validates` — its `inherited_green` receipt
   obligation is literally an inherited green in a receipt.
2. `soft-inherited-validation-across-clean-rebase` (hermit-243) — the zero-diff rebase case.
3. Any relaxation of the rebase wrapper's `receipt_present` clause.

The moment one lands, an inherited green enters the ledger **byte-identical** to a hard one.

---

## 2. Why the ordering is load-bearing (build it before the hazard, not after)

This repo's producers travel with their branches, so the schema contract is **version-aware
acceptance**: a consumer that starts *requiring* a new field breaks every producer that predates it.
That is not hypothetical here — it is the incident that once rejected 254 of 255 ledger rows
fleet-wide and forced a validate pause.

Consequence: the class field must be introduced **with a defined default for existing rows**, and it
must be introduced **before** the first soft producer. Adding it now costs one derivable default.
Adding it after a soft producer exists costs a flag day, during which soft rows are indistinguishable
— exactly the window in which a fake green lands.

---

## 3. The schema: three fields, one of them load-bearing

```jsonc
"validated_head_sha": "<40-hex>",   // the SHA validation ACTUALLY ran on   <-- authority
"inherited_from": {                 // present IFF validated_head_sha != commit
  "delta_kind":       "rebase-only" | "rebase-plus-upstream" | "new-branch-commits",
  "upstream_commits": 0,            // new commits pulled in from the moved base
  "branch_commits":   0,            // NEW commits on the branch itself (>0 ⇒ not green)
  "patch_identical":  true,         // the branch's own patch set is unchanged (git patch-id)
  "force_full_paths": [],           // force_full-class paths inside the pulled-in delta
  "recorded_by":      "<tool>"
},
"green_class": "hard" | "soft-…"    // CACHE ONLY — recomputed and refused on mismatch
```

`inherited_from` deliberately does **not** repeat the ancestor SHA: that is `validated_head_sha`. Two
copies of one fact is a drift source.

### The class is DERIVED from provenance, never read from a label

A `soft: bool` — the minimum fix sketched in the earlier note — is a **proxy**. A carry-forward writer
can stamp `soft=false` exactly as easily as a real one, and the row is byte-identical to a hard green
again: the original defect, one level up. So:

* the **authority** is the provenance (`validated_head_sha` vs `commit`, plus `inherited_from`);
* `green_class` is a **cache**, written for greppability and human reading;
* a verifier recomputes the class and **REFUSES the row when label and provenance disagree**.

That refusal is what makes the label safe to write at all. Both directions are bracketed
(`test_label_disagreeing_with_provenance_is_refused` / `test_label_agreeing_with_provenance_is_allowed`)
— because a rule of "never write the label" would be a different design, and would make the field
useless for the human-facing purpose it exists for.

---

## 4. The default for rows that predate the field

> `validated_head_sha` absent ⇒ derive it as `commit` ⇒ **HARD**.

This is a **derivation, not a guess**: every producer that exists today stamps the head it ran on, so
"absent" means precisely "ran here". All 585 existing rows classify hard, with zero fleet breakage —
which is what the version-aware contract requires of any new field.

Defaulting *toward* green is only safe because the other half is fail-closed: a row that **claims**
inheritance without carrying provenance to justify it is **REFUSED**, not accepted. For a future soft
producer to launder a row as hard it would have to omit `validated_head_sha` *as well* — i.e. write a
row positively asserting it ran at this head. That is a producer defect a lint can catch, not an
ambiguity a reader has to resolve.

---

## 5. The decay rule — name the boundary, do not treat all soft alike

| Derived class | When | Strength |
| --- | --- | --- |
| `hard` | validated at this exact head | full |
| `soft-rebase-only` | branch patches unchanged, **no** new upstream commits | strongest soft |
| `soft-upstream-delta` | rebase pulled in N new upstream commits | weakens with N |
| `soft-force-full-touched` | …and one of them touches a force_full-class path | weakest |
| `not-green` | the **branch itself** gained commits | neither (owner's rule) |
| `refused` | the row claims more than its provenance supports | not a verdict |

**The boundary inside `soft-upstream-delta` is derived, not a picked N.** If any pulled-in commit
touches a force_full-class path (`Cargo.toml`/`Cargo.lock`, `ci/**`, `validate.sh`,
`rust-toolchain.toml`, `.cargo/**`, gated workflows), the blast radius is the entire suite and the
ancestor's green covers none of it. This is the same monotonic `force_full` boundary the
test-selection decay measurement found — where it fires, selection saves exactly zero, and
inheritance is worth exactly as little. Reusing one boundary across both mechanisms is deliberate:
two different "when does inherited evidence stop counting" rules would drift apart.

The recorded field is the **list of offending paths**, not a bare boolean, so a reviewer sees *which*
path forced the downgrade.

---

## 6. The landing predicate must say which classes it accepts

Add to `qualifying-receipt.json`:

```json
"accepts_green_class": ["hard"]
```

Defaulting to `["hard"]` keeps today's behavior **exactly** as it is, and converts it from an
accident (nothing soft exists) into a **stated policy**. Widening it later is then one reviewed edit
in the single shared predicate file — and every consumer that reads that file inherits the decision,
instead of each one quietly deciding for itself what a soft row means.

The widening is class-by-class, not a boolean: `["hard", "soft-rebase-only"]` admits the strongest
soft class and still refuses `soft-upstream-delta`
(`test_widening_the_policy_admits_the_named_class_only`).

---

## 7. Consumers — who must distinguish, and how

The predicate already has one shared definition read by every consumer, which is what makes this a
one-edit change rather than an eleven-edit one. Consumers of `qualifying-receipt.json` /
`row_qualifies`:

| Consumer | What changes |
| --- | --- |
| `lib/qualifying_receipt.rs` | read `accepts_green_class`; add the derived class to the qualification |
| `lib/validate_status.rs` | `is_clean_full_pass` gains the class clause; `Assessment` carries the class |
| `qualifying_receipt.py` | same, Python side |
| `validation/verify_receipt.sh` | merge gate: refuse a class the policy does not accept |
| `landing/reconcile_receipts.py` | reconciliation must not treat a soft row as a hard receipt |
| `validation/publish_receipt.py` | publish the class alongside the receipt |
| `history/query.py` | corroboration population should be class-aware |
| `validate/preflight_anchor.py` | an anchor must be hard (see below) |
| `validate/anchor_select.py` | already refuses non-full receipts; add the class refusal |
| `ci-hub.rs` (`validate-status`, `newest-green`) | **surface the class in the verdict output** |

Two rules that are not just plumbing:

1. **An anchor must be HARD.** Green inheritance is one hop by design — inheriting *from* an inherited
   green is a claim about a claim. The class clause is what makes that structural rather than
   conventional, in the same way `selection_mode == "full"` already prevents anchoring on a selective
   receipt.
2. **`newest-green` must not silently return a soft head.** Its whole job is to answer "what is the
   validated frontier"; a soft answer is a *speculative* frontier and must say so.

---

## 8. Producer obligations

* Stamp `validated_head_sha` on **every** row. A producer that runs at the head it describes writes
  `validated_head_sha == commit`; the field is then redundant but explicit, and its presence is what
  lets a lint distinguish "old producer" from "new producer that forgot".
* A carry-forward producer additionally writes `inherited_from`, built by
  `green_class.classify_delta(checkout, ancestor, head, old_base, new_base)` **at record time** —
  never at read time, because by the time a consumer reads a receipt the checkout may be gone.
* `classify_delta` **raises** rather than guessing when the delta cannot be established. An
  unclassifiable delta must not silently become the strongest class.
* The rebase wrapper is the natural first producer: it already knows X (source), Y (base), Z (result)
  and the conflict set. Its store keeps the *judgement*; the ledger row gains the *class*. The two
  should agree, and reconciling them is the follow-up named in §11.

---

## 9. Verification — the owner's mutation, run for real

> *"rebase a hard-green PR → its row becomes SOFT with the ancestor recorded. Add a commit → it
> becomes NEITHER. A classifier that calls everything soft-green is the label-with-no-backing problem
> again."*

Run against a throwaway git repository so the delta is derived from real history, not fixture fields
(`MutationSequence` in `test_green_class.py`):

| Step | Observed |
| --- | --- |
| Before rebase | `hard` |
| Rebase onto a main that moved by 2 commits, one touching `ci/run-node.sh` | `soft-force-full-touched`; `validated_head_sha` = the ancestor; `branch_commits=0`, `upstream_commits=2`, `force_full_paths=["ci/run-node.sh"]` |
| Add a commit on the branch | `not-green`; `delta_kind=new-branch-commits`, `branch_commits=1` |
| Force-rebase onto the **same** base (head rewritten, nothing else moved) | `soft-rebase-only` |

The last row is the **anti-vacuity** case: without it, a classifier that returned the weakest class
for everything would pass every downgrade test for the wrong reason.

**Full bracket suite: 27/27 pass**, each gate bracketed on both sides (planted violation refused,
planted qualifying case fires). Refusals covered: soft-without-provenance; inheritance claimed at the
same head; label disagreeing with provenance; unknown `delta_kind`; `rebase-only` contradicting a
non-zero upstream count; `rebase-plus-upstream` with no count; missing commit; malformed policy.

**Live baseline:** `green_class.py --ledger ignored/validate-run-ledger.jsonl` → 585 rows, 0 malformed,
**585 hard, 0 soft, 0 refused**, `accepts for landing: hard`.

---

## 10. What this design deliberately does not do

* **It does not widen the landing policy.** `accepts_green_class` defaults to `["hard"]`; nothing that
  cannot land today becomes landable. Whether to ever accept `soft-rebase-only` is an owner decision,
  and now a visible one-line one.
* **It does not merge the rebase wrapper's store into the ledger.** The wrapper's soft-green judgement
  and the ledger's green class are different facts (a judgement about a *resolution* vs a class of a
  *receipt*). They should be reconciled, not conflated — §11.
* **It does not change any producer.** `validate.sh` and `validate.rs` are untouched; §8 states what
  they must stamp when the field lands.

---

## 11. Next, in order

1. **Add `accepts_green_class: ["hard"]`** to `qualifying-receipt.json` — behavior-neutral, and it
   turns today's implicit policy into a stated one. Extend the existing five-consumer panel in
   `validate/tests/test_qualifying_receipt_mutation.py` with a soft-row scenario; the panel already
   asserts unanimity, so it is the right place.
2. **Add `validated_head_sha` to `HistoryRow`** (`records.rs`) with the §4 default, plus
   `inherited_from` as a typed struct. **Shape-exactness is load-bearing** — `coverage` demonstrated
   that a present-but-wrong-shaped typed field makes `parse_ledger` drop the *whole row* into a
   `skipped` counter nobody reads, so a malformed provenance block would make a green *vanish*.
3. **Wire the class into `is_clean_full_pass` and surface it in `validate-status` / `newest-green`.**
4. **Make the rebase wrapper the first producer** of `inherited_from`, and reconcile its judgement
   with the derived class.
5. **Only then** consider widening the accepted set.

---

## 12. Not established

* No validate run, no network. Every claim is a read of ci-hub at working-tree HEAD plus the local
  ledger.
* `green_class.py` is a **new module that no consumer calls yet**. The classification logic is
  bracketed, but the integration in §7 is specified, not built — no Rust was changed, and
  `qualifying-receipt.json` is untouched.
* The claim "no soft producer exists today" is from reading `validate.sh`, `scripts/validate.rs`, and
  the rebase wrapper, plus the measured 0-of-585 provenance-field count. A producer outside ci-hub
  writing directly to the ledger would not be caught by that check.
* `classify_delta` needs `old_base` and `new_base` from the caller. Whether the rebase wrapper can
  always supply both (it records `source`, `base`, `result`) is unverified — it looks like it can,
  since `base` is the new base and the old base is derivable from the ancestor, but that path has not
  been exercised.
