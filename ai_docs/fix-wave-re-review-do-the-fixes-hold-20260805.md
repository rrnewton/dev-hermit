# Re-review leg: do the fix-wave fixes actually hold? — four hold, one is split

**Task:** `surface_superseded_fail_count` (re-review leg) · hermit-clone (opus-5), 2026-08-05
**Local, no egress, no validate-run.** Every result below is a re-plant against the **current**
tree, not a restatement of an earlier run — other agents have been editing `ci-hub/` throughout.

## Verdicts

| # | claim under re-review | verdict |
|---|---|---|
| 1 | superseded-fail count surfaces in the VALIDATED banner | **REAL — fires** |
| 2 | `green_class` wired into `qualified_rows` refuses a laundered soft green | **REAL — fires** |
| 3 | `.gitmodules` hazard ratchet | **REAL — fires** |
| 4 | cpu_timeout enforces | **gate REAL; enforcement INERT ON CI** |
| 5 | ledger rejects a 5-of-6 partial | **SPLIT — one engine rejects, two accept** |

## 1-3: still fire against the current tree

```
# validate WARNING d6e3607b… -- 1 SUPERSEDED clean full-coverage FAIL record(s) …   [plant]
qualified-rows: 3/4 qualified          [plant: laundered soft green refused]
qualified-rows: 107/585 qualified      [real ledger: unchanged, no producer newly rejected]
gitmodules-lint planted-shallow exit = 1     live-tree exit = 0
```

All three survived a session of concurrent edits by other agents. The `qualified_rows` control
number is the load-bearing one: **107/585 unchanged** means the green-class clause still rejects no
existing producer.

## 4. cpu_timeout — the gate is real, the enforcement is not

**The gate itself is REAL.** Planted five distinct violations against `port_gate.audit_node`, each
refused with its own verdict, plus a genuine positive control:

```
POSITIVE CONTROL  correct value, enforced path, sane wall  -> PASS
PLANT  declared on UNENFORCED path                         -> NOT_ENFORCED
PLANT  declared != derived                                 -> UNDERIVED
PLANT  missing on an enforceable path                      -> MISSING_CPU
PLANT  bloated wall (600s vs 10s est)                      -> BLOATED
PLANT  invented constant, only 1 sample                    -> UNDERIVED
floor-at-1: max_cpu 0.2s -> derived=1, "0 means DISABLED in the scheduler"
```

**But cpu_timeout does not enforce on CI.** Running the gate over the live manifests:

```
nodes_audited: 55
verdicts:  55  NOT_ENFORCED        <-- every single node
any_ci_path_boxes: False    any_ci_path_routed: True
derivable_now: 53           bloated_wall_nodes: 18
port_gate CLI exit = 1      <-- the gate is FAILING LOUDLY today
```

Cause, from the gate's own path analysis: `reexec_in_scope()` short-circuits when `CI` or
`GITHUB_ACTIONS` is set, and **GitHub sets `GITHUB_ACTIONS` on hosted *and* self-hosted runners**, so
no systemd scope is entered; `resolve_cgroups` then yields `cg=None`, and the CPU-time monitor lives
inside `if let Some(c) = &cg` — **no cgroup manager, no monitor thread, a declared `cpu_timeout` is
silently inert**. Only the local developer / `validate-run` path is boxed.

So the honest answer to "cpu_timeout enforces": **locally yes, on CI no** — the state the standing
memory recorded, still true. 53 of 55 nodes already have enough samples to derive a budget; what is
missing is the enforcement path, not the numbers. The gate is doing its job by refusing to certify
any of them.

## 5. The 5-of-6 partial — a three-way split (the headline finding)

Same one-gate-short row, three deciders:

| decider | verdict on `gates_run=4` of `gates_expected=5` |
|---|---|
| `qualified_rows.is_qualified` (Python accessor) | **False — rejects** ✅ |
| `anchor_select.row_qualifies` (the SHARED qualifying-receipt predicate) | **True, `'qualifies'`** ❌ |
| Rust `ci-hub validate-status` (authoritative parser) | **VALIDATED** ❌ |

Only the qualified-rows accessor enforces `ran >= expected` (via `flake_class.gate_counts`). The
**shared** predicate — the one whose entire purpose is to be the single authority every consumer
reads — does not check gate completeness at all, and neither does the Rust path built on it. A
receipt that ran 4 of its 5 gates is accepted as VALIDATED.

This is a **one-verifier-per-authority violation**: two implementations of "qualifying receipt"
disagreeing on completeness is precisely the drift the shared-predicate work exists to remove.

**Calibration — it is LATENT, not live:**

```
real ledger rows: 585
  rows carrying BOTH ran and expected: 74
  of those, ran < expected (a live partial): 4
  rows where the two predicates DISAGREE today: 0
```

The 4 live partials are refused by both predicates for other reasons (not `result == pass`). The
divergence needs a row that is *otherwise fully qualifying* **and** one gate short — which no
producer emits today. It will bite the first time one does.

**Recommended fix:** add the completeness clause to the shared `qualifying-receipt.json` predicate
and its two implementations, rather than leaving it only in the accessor. That keeps the fix in the
one place all three consumers read.

## Two errors in my own harness, disclosed

1. **Wrong root for `port_gate`.** I first ran it against the *hermit checkout*; `load_nodes` expects
   `root/hermit/ci/dag/*.json`, i.e. the **dev-hermit parent**. It reported `nodes_audited: 0` and I
   was one step from filing "the gate is vacuous on the live tree". Re-run with the gate's own
   `_root()`: 55 nodes. The false verdict was mine, not the gate's.
2. **A vacuous control in the first Rust 5-of-6 attempt.** My synthetic control row was refused for
   an unrelated reason, so both arms said NOT-VALIDATED and the test could not distinguish "rejects
   partials" from "rejects everything I build". Rebuilt from a row that genuinely validates — which
   is what exposed finding 5.

Both were caught only because a control was required to *succeed*. That is the third and fourth time
this session a control has caught my own harness rather than the code under test.

## Status of the parent task

`surface_superseded_fail_count` is complete and re-verified: counters, both banner lines, both JSON
fields, the corrected FAILED-banner count, 9 Rust brackets, Rust suite 117/117, plant re-confirmed
above. Artifact: `ai_docs/surface-superseded-fail-count-in-validated-banner-20260805.md`.

## Reproduction

```
./ci-hub/ci-hub validate-status --sha d6e3607b… --ledger scratch/superseded-plant/planted.jsonl
python3 ci-hub/validate/qualified_rows.py --ledger scratch/wire-plant/planted-ledger.jsonl
python3 ci-hub/validate/gitmodules_lint.py --file <planted>   # exit 1
python3 ci-hub/timeout_audit/port_gate.py gate --json          # exit 1, 55 NOT_ENFORCED
# finding 5: perturb a real qualifying row to gates_run = gates_expected - 1 and compare
# qualified_rows.is_qualified vs anchor_select.row_qualifies vs ci-hub validate-status
```
