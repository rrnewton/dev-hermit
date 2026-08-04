# Bind validation counts to per-node coverage obligations

**Slug:** `bind-validation-counts-per-node-coverage`
**Date:** 2026-08-04
**Author:** hermit-coord (Opus 4.8), owner of `bind_validation_counts_to`
**Governs:** `hermit/validate.sh` (`append_validation_ledger`), the `safe-ci-dag-runner`
in `agent-utils`, `ci-hub/lib/validate_status.rs` (`is_clean_full_pass`), and
`ci-hub/remediation/nonzero_result.py` (extractor).
**Coordinates with:** `emit_executed_and_filtered` (writer conforms after this lands),
`remove_uncounted_receipt_grandfather` (last; only after the grandfathered population
is measured at ZERO).

## One-line problem

The consumer's `filtered_tests == 0` predicate (validate_status.rs:184-186) is the WRONG
QUESTION. A correct full run legitimately filters ~693 tests, so the predicate rejects
every real full green. The right question is per-node: **did each required test-bearing
DAG node actually execute its tests** — not "was the global filtered count zero."

## Measured evidence (direct, hermit-coord 2026-08-04 — not inferred)

Extractor `nonzero_result.py --ledger-fields` on three genuine **full PASS** logs
(0 FAILED each):

| log | aggregate executed / filtered | overall |
|---|---|---|
| `/tmp/hermit-validate.0sffgn.log` | 749 / **693** | 78 banners, PASS |
| `/tmp/hermit-validate.0trm2Y.log` | 750 / **693** | 77 banners, PASS |
| `/tmp/hermit-validate.1cjyuQ.log` | 768 / **676** | 70 banners, PASS |

Consumer `validate_status.rs:184-186` is an UNCONDITIONAL guard evaluated BEFORE the
grandfather branch: `if filtered_tests > 0 { return false }`. So any schema-5 receipt
carrying the raw filtered sum (693) is rejected `NotValidated`. This is the confirmed
blocker; PR #1587 passed its own check only because it was tested on synthetic logs
where filtered happened to be 0.

### Why banner granularity ALSO fails (the "better vocabulary" trap)

A naive fix — reject if any single libtest banner has `0 passed` — recreates the stall.
Real full PASS logs contain legitimate zero-passed banners:

| log | test-result banners | `0 passed` banners | `running 0 tests` |
|---|---|---|---|
| `0trm2Y` | 77 | **11** | 9 |
| `0sffgn` | 78 | **12** | 10 |
| `1cjyuQ` | 70 | **11** | 9 |

These are legitimate: empty-crate binaries (`running 0 tests; test result: ok. 0 passed`),
doctest runs, and sharded filter-misses (e.g. `[test.rr_suite_contract] test result: ok.
1 passed; 0 failed; 0 ignored; 0 measured; 213 filtered out`). Rejecting on a single
`0 passed` banner would reject every real full run — the exact "reject legitimate
filtering with better vocabulary" failure.

### Why NODE granularity is correct

The `safe-ci-dag-runner` prefixes each banner with a stable node id `[test.NODE]`.
Aggregating `N passed` per node prefix on `0trm2Y.log`, **every** test-bearing node has a
positive executed total, even where individual banners are zero-passed:

```
app_strict_verify=8   arbitrary_binaries=3   cli=32   command_strict_verify=10
detcore_misc=23       detcore_unit=333(2b)   hermit_integration=93(37b)
hermit_modes=17       hermit_unit=191(2b)    ignored_syscall_regressions=2
liteinst_strict=23    rr_suite_contract=1    sabre_examples=3
```

Zero nodes with total executed == 0. The 11 zero-passed banners are all absorbed inside
nodes whose totals are positive (e.g. `hermit_integration` has 37 banners summing to 93).
**This is the positive control that matters (criterion 2), proven on 3 real full runs.**

## The obligation

> **Every test-bearing DAG node the full profile PLANNED must have total executed > 0.**

- Aggregate `executed_tests` / `filtered_tests` and per-node `filtered` are retained as
  **diagnostics only**. The `filtered == 0` predicate is DELETED.
- "Planned/required node set" comes from the `safe-ci-dag-runner` manifest (it knows
  planned vs executed), so a node that is **entirely absent** (never ran, emits no banner)
  is caught — which log-parsing alone cannot do.
- A node is "test-bearing" iff the manifest classifies it as a test node (not build/lint).
  A test node that produced no banner, or whose banners sum to 0 executed, VIOLATES its
  obligation.

### Criteria mapping

1. **Negative (skipped required node fails):** a planned test node with total executed 0
   (filtered-to-empty, feature-gated-out, or absent) → obligation violated → receipt
   rejected. ✅
2. **Positive (693 legit filters still pass):** proven — all 13 nodes on 3 real full PASS
   runs have executed > 0 despite 693 aggregate filtered and ~11 zero-passed banners. ✅
3. **Distinguishable without reading the log:** the producer computes per-node coverage at
   write time and carries a compact obligation outcome in the receipt; the consumer
   decides from receipt fields only. ✅

## Schema (carry the condition with the value)

Add to the receipt (schema stays the `COUNTS_SCHEMA=5` clean anchor; these are additive):

```jsonc
"coverage": {
  "planned_test_nodes": 13,          // from the manifest
  "executed_test_nodes": 13,         // nodes with total executed > 0
  "zero_executed_nodes": [],         // planned test nodes with total executed == 0 (NAMES)
  "absent_nodes": []                 // planned test nodes that produced no result at all
}
```

Aggregate `executed_tests` / `filtered_tests` remain (diagnostics). The obligation is
**satisfied iff `planned_test_nodes > 0 && zero_executed_nodes == [] && absent_nodes ==
[]`**. Carrying the node NAMES (not just a boolean) is deliberate — it lets the consumer
re-derive the verdict and lets a human see WHICH node was inert, per the Proxy-Binding
"carry the condition with the value" rule.

## Implementation (THREE touch points — scope note for coordinator)

The user's instruction named two files (`validate.sh`, `validate_status.rs`); correct
implementation needs a third — the DAG runner — because node granularity and the
planned-set live there.

1. **`safe-ci-dag-runner` (agent-utils):** per node, capture the node's stdout, extract the
   libtest banner(s) via the shared `nonzero_result.py` per-node extractor, and emit a
   machine-readable per-node coverage record (planned set + per-node executed/filtered +
   ran/absent). This is the source of truth for planned-vs-executed. *(Exact hooks pending
   the safe-ci-dag-runner exploration; see §"Runner integration" below.)*
2. **`hermit/validate.sh` (`append_validation_ledger`):** aggregate the runner's per-node
   records (plus any foreground gates' own banners) into the compact `coverage` object and
   emit it in the receipt alongside the aggregate counts. Standalone hermit (no runner
   manifest) degrades gracefully: emit `coverage` from whatever banners exist, never
   fabricate a satisfied obligation.
3. **`ci-hub/lib/validate_status.rs` (`is_clean_full_pass`):** DELETE the
   `filtered_tests > 0 ⇒ reject` guard. Keep the cheap universal `executed_tests == 0`
   guard. For a count-capable (schema ≥ `COUNTS_SCHEMA`) receipt, require the `coverage`
   obligation satisfied. A count-capable receipt MISSING `coverage` is a writer defect →
   reject (fail-closed, same pattern as the existing missing-counts rule). Pre-count
   receipts stay grandfathered until `remove_uncounted_receipt_grandfather` (measured at 0).

Producer and consumer must change in ONE transition (a predicate changed in one but not
the other is exactly the stall this replaces). `nonzero_result.py` gains a `--per-node`
mode so both the runner and any diagnostics use ONE parser (no second banner regex).

## Sequencing

`bind` (this) → `emit_executed_and_filtered` (PR #1587 rebases/conforms to the `coverage`
contract) → `remove_uncounted_receipt_grandfather` (last; only after measuring the
grandfathered population at ZERO, not assuming it).

## Runner integration

*(To be finalized from the safe-ci-dag-runner exploration: exact manifest location, node
classification, existing per-node reporting, node command capture point, planned-vs-run
tracking, and which of the Python/Rust runners validate.sh invokes.)*
