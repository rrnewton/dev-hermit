# `executed_tests` scope resolution + historical red reclassification

**Date:** 2026-08-05 · **Author:** hermit-coord (orc-coord) · **Host:** devbig014 (ledger is machine-local)

Closes two coupled questions: (1) the scope of the `executed_tests` ledger field
(`executed-tests-field-scope-ambiguity`), and (2) how the historical failure rows
reclassify under the correct discriminator (`ledger-records-reds-without-distinguishing-flake-from-defect`).

## 1. Scope — SETTLED: per-run sum, identical writer, across ALL schemas (code-proven)

`executed_tests` is a **per-run running-sum-until-halt over all gates**, in schema
versions 3/4/5 alike. Proven at the source, not merely inferred:

- The field entered the ledger writer at **exactly one** hermit commit — `589d0eeac`
  "Record executed counts in validation receipts" (emitted `schema_version=3`). That
  schema-3 block and the current schema-4/5 block both invoke the **identical** helper
  `python3 $DEV_HERMIT_PARENT/ci-hub/remediation/nonzero_result.py --ledger-fields "$LOG_FILE"`
  over the single whole-run log. `git log -S"executed_tests" -- validate.sh` shows only
  two touch points (`589d0eeac` introduce, `fc0b76adc` cache-reuse); neither re-scopes.
  **There was never a per-gate writer.** `aggregate.py:324` imports the same
  `executed_test_count` symbol, so writer and analytics agree by construction.
- Empirical corroboration: full-pass `executed_tests` by schema — sv3 750–961 (n=47),
  sv4 760–792 (n=21), sv5 427–748 (n=44); all the same per-run full-suite magnitude.
  53 sv3 rows carry ≥700, impossible per-gate.
- The coordinator's `75edd745` datum (`fail 6chk tests=464` → `no_result 3chk tests=463`)
  is **two separate runs halting at different distances**, not a per-gate/per-run
  difference: both counts are per-run partial sums; `checks` counts coarse gates so
  6→3 reflects the second run stopping before the lane gates, not phantom-gate counting.
- `17b59fc6` yields 359 and 760 across two runs (halt at `command_strict_verify` vs
  `liteinst_strict` TIMEOUT) — same field, same scope, different stop-point.

**Consequence:** the historical failure window is commensurable end-to-end; no
schema≥4-only fallback is needed.

## 2. The band-count classifier is REFUTED — `executed_tests` is a proxy in BOTH directions

Keying the three-way classifier on the count (`≤1` no-result · 350–700 truncated ·
`≥700` trust) was **abandoned**. A count cannot distinguish "ran fewer tests" from
"covered fewer nodes." Proof from the PASS side: `ee303899` has 8 `pass` rows at
`executed_tests=427` (in-band) that are correctly **not** full greens —
`coverage.executed_test_nodes=4 / planned=19`, 15 `absent_nodes`; the authority that
catches them is `coverage.absent_nodes`, not the count. The "21-row truncated band"
was an artifact of the wrong discriminator: **only 1/21 is a real no-verdict
truncation** (external SIGTERM `3a404879`); the rest are eager-exit stop-points of one
real node failing (`safe-ci-dag-runner` short-circuits on first node failure, so the
per-run sum stops mid-count).

**The correct discriminators:**
- GREEN trustworthy IFF `result==pass` AND `coverage.absent_nodes==[]` AND
  `executed_test_nodes==planned_test_nodes` AND `zero_executed_nodes==[]` AND `executed_tests>0`.
- RED durable IFF a `gates[]` entry names a real failing node WITH `exit_code` at
  `gates_run==gates_expected`.
- `executed_tests`: use ONLY as the `≤1` no-execution floor; never as the trust/rerun signal.

## 3. Corrected reclassification of the historical window

Window = **49 field-bearing full fail/timeout rows** (this host, 568-row ledger; sv 27/20/2).
Classified by the floor + named-node discriminator (NOT the band):

| Class | Count | Basis |
|---|---|---|
| **DO-NOT-TOUCH** (`executed≥700`, ok-tier) | **12** | passes the floor; durable-FAILED still requires `gates_run==expected` + named node |
| **NO-RESULT** (`≤1` exec, 127-storm, sub-second collapse, external SIGTERM) | **14** | gates could not run / run torn down — carries no verdict |
| **REAL-RED, eager-exit stop-point** (a named node genuinely failed) | **23** | `gates[]`/log names one failing node; the band value is only where the per-run sum halted |

**The "45 false reds" framing is wrong.** Most reclassified rows are **real reds** — a
named node failed and the DAG short-circuited — not no-results. From the log-read
distribution over the 21-row band snapshot (findings in `ignored/truncated-band-findings.txt`,
captured when the `/tmp` logs still existed):

- **8 deterministic** build/lint/doc reds (clippy ×3, rustdoc ×3, dbi_release build ×2) —
  genuinely condemn the code; will NOT recover on re-run.
- **~12 per-node test failures** — mix of genuine and known-flaky/transient (`util-c`
  exit 126 re-ran GREEN; `blocking-sigsuspend` known load-flaky; `liteinst_strict`
  load-dependent hang).
- **1 external SIGTERM** (`3a404879`, et=430/75s) — the only true no-verdict truncation.

**DO-NOT-TOUCH set (≥700):** f1aa47e1, fb7672aa, bff3cd32, 975c9fa8, ea5f65d7,
71bc3856, 1de856c2, 3801a7df, 17b59fc6, a0f3d8e8, 893991ac.

## 4. No mutation; the landed gate is conservative and safe

- The reclassification is **read-time**; the ledger is append-only, so the raw
  `result=fail` is always still written and demotion happens on read by every consumer.
  Nothing is "baked in" — the HOLD concern ("reclassifying on an ambiguous field bakes
  it in permanently") is structurally moot for a read-time gate.
- The landed `executed_tests` gate (`d05874e`, both engines) demotes conservatively:
  band → `needs-rerun` = re-dispatch, never a terminal green or failed. On LIVE rows it
  only ever demotes (`needs-rerun`→`no-result`); it never rescues to durable FAILED,
  because live rows lack producer conditions (`dag_jobs`/`concurrent_validates`/
  `known_flaky`/bound origin) — only `bff3cd32` reaches `classify()=defect` live.
- The authoritative red discriminator (split `manifest_check` vs `lane_run` +
  `failing_cells[]`, promote only at `gates_run>=gates_expected` with a named substep)
  is the schema-4 producer design already tracked on
  `ledger-records-reds-without-distinguishing-flake-from-defect`. The only correction
  for historical rows is to **not** treat the `executed_tests` band as a rerun signal —
  which is already the accepted position.
