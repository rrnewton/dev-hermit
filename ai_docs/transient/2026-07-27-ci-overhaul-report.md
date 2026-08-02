# CI Overhaul Outcomes Report

Date: 2026-07-27

Audited Hermit revision: `3056ae2808c97ed6798c2dbd1ff9371a33d10a8f`

This report evaluates the current `rrnewton/hermit:main` tree against the five
requested CI-overhaul outcomes. It reports implemented behavior and observed
validation only; design intent is not counted as completion.

## Executive verdict

| Question | Verdict | Short answer |
| --- | --- | --- |
| Trivial tests removed and meaningful tests added? | PARTIAL, substantial progress | The obsolete command inventory and bucket script were deleted, active CI has no help/version/no-argument test cases, and semantic tests were added. The new multi-mode harness is still shallow outside verify mode: 1 replay cell and 1 chaos cell. Remaining smoke coverage does not have a dedicated retirement/upgrade task. |
| One `X.Y.Z` test namespace? | NO | The multi-mode harness uses `category/name`; backend parity, Rust integration tests, QEMU tests, reproducible-build tests, compatibility checks, and the suites under `tests/e2e/lib/` use separate naming and discovery rules. |
| Declarative backend matrix in YAML/TOML? | NO, with a partial declarative foundation | Harness allowlists are embedded JSON in each shell test. Backend parity uses a separate TSV file. CI lanes use JSON DAGs. There is no single YAML/TOML test-by-mode-by-backend matrix. |
| Inter-backend comparison against a ptrace golden copy? | NO, except one targeted check | Each backend normally passes against an expected output or its own repeated output. Only DBI `random_sources` performs a ptrace-reference comparison. There is no general ptrace-golden output or INFO-log comparison for DBI, KVM, SaBRe, or LiteInst. |
| Machine-readable and human-readable result tables? | PARTIAL | The harness emits JSONL, summary JSON, and JUnit; backend parity can emit TSV; `validate.sh` prints and writes compatibility summaries. These are not aggregated into one complete test/category/mode/backend result matrix, and CI does not generate a single human-readable table from all structured results. |

The overhaul is a useful foundation, but questions 2 through 4 are not complete
and question 5 is only locally satisfied by several disconnected formats.

## Audit path correction

The task named `scripts/validate.sh` and `tests/ci/`, but neither path exists in
the audited tree. The canonical entry point is root `validate.sh`; current CI
implementation lives in `ci/`, `ci/dag/`, `tests/e2e/`,
`tests/backend-parity/`, and `.github/workflows/`.

## 1. Test population

### What was removed

Commit `6044c2d3` (`Implement multi-mode E2E test harness (#1014)`) deleted:

- `ignored/all_test_cmds.sh`, a 1,962-line generated command inventory.
- `ci/e2e_commands_bucketed.sh`, an 80-line imperative bucket list.
- The old hosted/self-hosted workflow and DAG names, replacing them with
  portable/privileged terminology.
- CLI tests whose only assertion was help, version, or no-argument behavior.
- The broad synthetic LiteInst compatibility inventory in favor of focused
  runtime coverage.

A current-tree search found no active help/version-only cases in `ci/`,
`tests/e2e/`, `tests/backend-parity/`, or the relevant `validate.sh` gates.
The remaining matches are option parsers and cargo-nextest installation/version
selection, not product tests.

### What was added

The multi-mode harness currently validates 11 shell tests and expands them to
17 required cells:

| Dimension | Current required cells |
| --- | ---: |
| verify | 11 |
| naked control | 4 |
| replay | 1 |
| chaos | 1 |
| ptrace | 12 |
| KVM | 1 |
| backend-free naked control | 4 |

The 11 tests span the five requested categories: 2 applications, 1
data-handling, 4 determinism-stress, 1 language-runtime, and 3 system-utility
tests. Recent main commits also added semantic application, data-handling,
language-runtime, stress, QEMU, reproducible-build, and hard-program corpus
coverage outside this 11-test inventory.

These additions execute programs and assert deterministic observations rather
than merely checking that a binary starts. The limitation is mode depth: only
`system-utils/record-getpid` exercises replay through this harness, and only
`determinism-stress/order-violation` exercises chaos. Basic capability probes
also remain in quick and backend-parity paths. The active task graph contains
`debug-ci-4-failures` for integration failures, but no dedicated task to
retire or upgrade the remaining smoke cases.

## 2. Test identity and namespace

`ci/test_harness.sh` discovers one `.sh` level beneath the five category
directories and derives IDs such as:

```text
applications/timed-progress-bar
determinism-stress/thread-contention
system-utils/record-getpid
```

That is a consistent two-part namespace for the new harness, not the requested
single `X.Y.Z` namespace for all end-to-end tests. Important tests remain in
other inventories:

- `tests/e2e/lib/` contains separately orchestrated category suites.
- `tests/backend-parity/matrix.tsv` uses flat case names.
- Rust integration tests use Cargo target and test names.
- `tests/qemu-boot/` and `tests/reproducible-builds/` have independent runners.
- `validate.sh` still owns compatibility program labels and focused gates.

There is therefore no common identifier that can join all test definitions,
CI cells, result rows, and follow-up tasks.

## 3. Declarative backend matrix

There are three declarative mechanisms, but no canonical backend matrix:

1. Each harness `.sh` file embeds JSON metadata declaring lane, requirements,
   timeout, observation, enabled modes, and backend allowlists.
2. `tests/backend-parity/matrix.tsv` declares 22 ptrace/DBI/KVM expectations and
   gap reasons. Its ratchet is ptrace 22/22, DBI 21/22, and KVM 20/22.
3. `ci/dag/portable.json` and `ci/dag/privileged.json` declare CI execution
   dependencies and resources, not the semantic backend matrix.

The harness metadata is useful and validated, but it is distributed across
executable shell files. The parity TSV is a second source of backend policy and
does not encode harness modes. SaBRe and LiteInst do not appear in the current
17-cell harness plan. A single YAML/TOML source covering test ID, category,
mode, lane, backend, expected status, requirements, and gap rationale does not
exist.

## 4. Inter-backend comparison

The backend-parity runner repeats each expected passing case three times. It
checks either a fixed expected stdout value or equality with the first output
from that same backend. That proves backend-local repeatability, not equality
with ptrace.

The one exception is `random_sources` under DBI:

- The runner first executes a ptrace reference.
- It extracts the root-thread random stream.
- It requires each DBI run to match that ptrace stream.

No equivalent ptrace reference is generated for the other 21 parity cases or
for KVM. The multi-mode harness hashes each cell's own observation and does not
join observations across backends. It also does not normalize and compare
Hermit INFO logs across backends. Consequently a KVM result can be repeatable
and pass its case-specific assertion while differing from ptrace output.

## 5. Results and reporting

Implemented structured output:

- `ci/test_harness.sh`: one JSONL row per cell, `summary.json`, and JUnit XML.
- Portable and privileged workflows: upload `ignored/e2e/<lane>` artifacts for
  14 days.
- `tests/backend-parity/run_matrix.py --output`: TSV with test, backend,
  expectation, result, duration, and detail.
- `validate.sh`: TSV compatibility inputs plus a rendered compatibility report
  grouped by program category and backend, and a human pass/fail summary.

Missing integration:

- No single schema combines harness, parity, Rust, QEMU, reproducible-build,
  compatibility, and focused validation results.
- Harness `summary.json` aggregates only totals and modes; it does not emit a
  category-by-backend table.
- The workflows upload raw artifacts but do not generate one Markdown or text
  summary from all structured records.
- There is no durable cross-backend observation table suitable for comparing
  ptrace golden output with every allowed backend.

Machine-readable and human-readable pieces exist, but consumers must correlate
several files and console formats manually.

## Validation observed

At the audited SHA:

```text
./ci/test_harness.sh validate
PASS: 11 E2E tests have valid syntax and metadata

python3 tests/backend-parity/run_matrix.py --check
RATCHET ptrace: 22/22 (100.0%)
RATCHET dbi: 21/22 (95.5%)
RATCHET kvm: 20/22 (90.9%)

bash -n ci/test_harness.sh validate.sh
PASS

jq empty ci/dag/portable.json ci/dag/privileged.json
PASS
```

GitHub status observed at 2026-07-27 20:53 PDT for current main
`3056ae28`:

- `CI (privileged)`: success.
- `Docs`: success.
- `CI (GitHub-managed portable)`: in progress.
- The last completed portable run was successful at older SHA `b3500a31`.

This audit did not run the complete local `./validate.sh full` suite and does
not label current main fully green while its portable workflow is incomplete.
The active P0 task `debug-ci-4-failures` is investigating four failures seen in
overhaul integration: record/replay corpus instability, detcore-liteinst
nextest discovery, `analyze_hello_race`, and KVM parity timeout.

## Closure criteria

The remaining work is concrete:

1. Define a stable three-part ID and migrate every end-to-end inventory to it.
2. Generate the required mode/backend plan from one versioned declarative
   matrix, including explicit gap reasons for every unsupported cell.
3. Add ptrace-golden observation comparison for every backend where semantic
   equality is expected, with documented normalization for legitimate
   backend-specific output.
4. Expand replay and chaos beyond one cell each, and explicitly track any
   retained smoke probes as capability checks or replacements.
5. Aggregate all runner outputs into one machine-readable result set and one
   generated human table keyed by test ID, category, mode, backend, status,
   duration, and reason.
6. Resolve the four integration failures and record a complete green result at
   one exact main SHA.

Until those conditions are met, the CI overhaul should be described as landed
infrastructure with partial acceptance coverage, not as completion of all five
requested outcomes.
