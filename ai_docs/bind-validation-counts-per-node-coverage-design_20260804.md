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

## ARCHITECTURE CORRECTION (hermit-coord 2026-08-04, measured after the first draft)

The first draft assumed THREE touch points including a change to the `safe-ci-dag-runner`
(agent-utils) to emit per-node counts. **That change is unnecessary** — established by
direct measurement, not inference:

- `validate.sh` runs each lane as `./ci/run-dag.sh <lane> -j <jobs> -v`. The runner's
  `run` verb parses `-v` as `action="count", default=1` (cli.py:375), so a single `-v`
  yields **verbosity 2**; `scheduler.py:363` sets `stream = self.verbosity >= 2`, so the
  runner **streams every child stdout line prefixed `[<node.tag>] `** into the shared
  `LOG_FILE`. The per-node libtest banners are therefore ALREADY in the log
  (`[test.NODE] test result: ok. N passed; ...; M filtered out`), which is exactly what my
  3-log measurement below aggregated.
- The runner ALSO emits a per-node terminal line `[<node.tag>] ✓ PASS ...` / `✗ FAIL ...`
  at verbosity ≥ 1 (scheduler.py:580-583) for EVERY node that ran. This is the reliable
  "did this node run" signal — independent of whether the node emits libtest banners (a
  shell/e2e test node may run real work and emit none). A skipped/absent node produces
  NO such line, and a skipped required node already forces `RunResult.ok = False`.

**Consequence:** the bind is **2 files in 2 repos, NO agent-utils change, NO temp worktree**:
1. `hermit/validate.sh` (producer) — self-contained log parse → emit `coverage{}` (schema 5).
2. `ci-hub/lib/validate_status.rs` (consumer) — enforce the coverage obligation from the
   receipt, delete the `filtered>0` guard.
`ci-hub/remediation/nonzero_result.py --per-node` is now OPTIONAL parent-side diagnostic
parity (the consumer reads `coverage{}` from the receipt JSON; it does NOT re-parse a log),
and a Hermit PR MUST NOT depend on the parent `ci-hub` at runtime (hermit has no
`nonzero_result.py`; validate.sh on main does not call it — the earlier "validate.sh calls
nonzero_result.py --ledger-fields" note was the PR #1587 *branch*, not main).

### Refined obligation (avoids the shell-node false-positive that would recreate the stall)

Computable from the LOG alone (+ the manifest for the planned test-node set):
1. **ran/absent:** every planned test node (manifest tag prefix `test.`) MUST have a
   `[node] ✓ PASS`/`✗ FAIL` terminal line. A planned test node with none → `absent` →
   violation. (No false positive: shell nodes still emit the terminal line.)
2. **zero-executed:** a node that emitted ≥1 libtest banner MUST have passed-sum > 0. A node
   with NO banner is EXEMPT (legit shell/e2e). This catches the real inert-green (every
   crate filtered-to-empty / compiled-out) without rejecting banner-less test nodes — the
   precise line the blanket "executed>0 for all nodes" draft would have crossed.
`filtered_tests` is retained as a pure diagnostic; the `filtered==0` predicate is DELETED.

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

## Implementation (TWO touch points — corrected; see ARCHITECTURE CORRECTION above)

1. **`hermit/validate.sh` (`append_validation_ledger` + the DAG-lane gate path):** after the
   lane gate(s) run, parse the shared `LOG_FILE` (self-contained, in-repo — awk/bash or a
   small vendored helper, NOT the parent `nonzero_result.py`): collect per-node terminal
   `[node] ✓ PASS`/`✗ FAIL` lines (→ ran-set) and per-node `[node] test result:` banners
   (→ per-node passed/filtered sums + had-banner flag). Cross the ran-set against the
   PLANNED test-node set (union of the `test.*` steps in the lane manifests actually run,
   read from `ci/dag/*.json`) to compute `absent_nodes`; compute `zero_executed_nodes` from
   the refined obligation (banner-emitting node with passed-sum 0). Emit the compact
   `coverage{}` object plus the aggregate `executed_tests`/`filtered_tests` counts, and bump
   `schema_version` to `COUNTS_SCHEMA=5`. Standalone hermit (no DAG lane) degrades
   gracefully: emit `coverage` from whatever banners exist and `planned_test_nodes` it can
   see; NEVER fabricate a satisfied obligation.
2. **`ci-hub/lib/validate_status.rs` (`is_clean_full_pass`):** DELETE the
   `filtered_tests > 0 ⇒ reject` guard. Keep the cheap universal `executed_tests == 0`
   guard. For a count-capable (schema ≥ `COUNTS_SCHEMA`) receipt, require the `coverage`
   obligation satisfied (`planned_test_nodes > 0 && zero_executed_nodes == [] &&
   absent_nodes == []`). A count-capable receipt MISSING `coverage` is a writer defect →
   reject (fail-closed, same pattern as the existing missing-counts rule). Pre-count
   receipts stay grandfathered until `remove_uncounted_receipt_grandfather` (measured at 0).

Producer and consumer encode ONE judgement. Landing order is safe either way: no schema-5
receipt exists yet, so the consumer's new coverage branch is inert until the validate.sh
producer lands — deleting the `filtered>0` guard is pure un-breaking. `nonzero_result.py`
MAY gain a `--per-node` mode later as a parent-side DIAGNOSTIC parity parser; it is NOT
load-bearing for the bind and NOT called by hermit.

## Overlap with `emit_executed_and_filtered` (PR #1587) — coordinator decision

bind's validate.sh change (coverage{} + counts + schema 5) SUBSUMES emit's aggregate-count
work, and PR #1587's raw-`filtered` approach is the thing that would re-break the drain. Two
clean options: (A) one Hermit PR does the full producer (coverage{} + counts + schema 5),
superseding PR #1587 and satisfying `emit_executed_and_filtered` as a stale-premise close;
(B) bind lands only coverage{} + schema 5 and PR #1587 rebases to add nothing but the
aggregate counts. (A) is fewer moving parts and avoids a broken PR lingering. Recommend (A).

## Sequencing

`bind` (this) → `emit_executed_and_filtered` (PR #1587 rebases/conforms to the `coverage`
contract) → `remove_uncounted_receipt_grandfather` (last; only after measuring the
grandfathered population at ZERO, not assuming it).

## Runner integration — RESOLVED: no runner change needed

The safe-ci-dag-runner exploration is complete and settles this: validate.sh invokes the
**Python** engine via `ci/run-dag.sh` (`find_runner` prefers `agent-utils/common/bin`
→ `py/bin/safe-ci-dag-runner`). At the verbosity validate.sh uses (`-v` → 2), the runner
already streams `[node] `-prefixed child stdout (banners) AND emits `[node] ✓ PASS/✗ FAIL`
terminal lines — the two signals the producer needs — into the shared `LOG_FILE`. Planned
node set and classification come from the manifests `hermit/ci/dag/{portable,privileged}.json`
(`test.*` tag prefix). Planned-vs-run is therefore fully reconstructable in validate.sh from
(log terminal lines) ∪ (manifest test nodes); the runner already fails the run when a
required node is skipped (`RunResult.ok=False`). No agent-utils edit is required, so this
bind does not touch the (currently PR-#15-occupied) canonical agent-utils checkout.
