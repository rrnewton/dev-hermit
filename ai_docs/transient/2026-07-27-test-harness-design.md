# Multi-Mode E2E Test Harness Design

Date: 2026-07-27

## Decision

Replace command inventories with a test-centric harness. Each semantic test is
one executable shell file under `tests/e2e/<category>/`; an embedded JSON
annotation declares which modes, backends, and runner capabilities are part of
that test's contract. A Rust-script runner expands those declarations into
explicit test cells and emits one structured result for every planned cell.

The four modes answer different questions:

- **naked**: does the workload expose the nondeterminism that the test claims
  to exercise when Hermit is absent?
- **verify**: does `hermit run --strict --verify` produce an identical repeat
  on every allowlisted execution backend?
- **replay**: does `hermit record start --strict --verify` reproduce the
  recorded execution on every genuinely supported record runtime?
- **chaos**: do different deterministic seeds explore distinct outcomes while
  each witnessed seed remains reproducible?

CI has two capability lanes, **portable** and **privileged**. These names
describe host requirements, not test duration. Runtime, mode, and category are
independent dimensions within each lane.

## Current baseline

The current `ci/e2e_commands_bucketed.sh` at Hermit
`2ac20b725bad7eaa976b7271242b088a96893c97` is an executable inventory for five
examples. It checks the `examples/` directory and runs every example through
ptrace at L2 with:

```sh
hermit --log=off run --backend ptrace --strict --verify \
  --no-virtualize-cpuid --max-timeslice=disabled -- ./examples/TEST
```

It has one hosted-safe bucket and no naked, replay, chaos, per-test backend, or
machine-readable result model.

The parent research inventory contains 1,163 direct `hermit run` command
lines: 258 hosted-fast, 896 hosted-medium, eight hardware, and one occasional.
That file is intentionally non-executable because many rows depend on fixtures
and shell variables supplied by their original Rust test. It is migration
input, not a script to run wholesale and not a denominator of 1,163 distinct
behaviors.

The active CI DAG is currently named `hosted.json` and `hardware.json`.
`hosted.json` already serializes Hermit guests and explicitly disables PMU
timeslicing and CPUID virtualization where needed. `hardware.json` has
separate PMU, CPUID-faulting, and KVM probes. The new lanes preserve those
capability boundaries while giving them clearer names.

## Goals

1. Give every test one stable identity independent of mode or backend.
2. Make every claimed passing combination an explicit allowlisted contract.
3. Assert relationships across executions, not merely that commands exit 0.
4. Produce exact per-cell evidence suitable for CI summaries and trend data.
5. Detect newly working backend combinations without weakening required CI.
6. Keep portable work off scarce self-hosted runners.
7. Eliminate inline `bash -c` test bodies from CI configuration.

## Non-goals

- This harness does not imply that every backend supports every mode.
- It does not turn e9patch preprocessing into an independent execution
  backend.
- It does not make probabilistic failures acceptable in required CI.
- It does not replace Cargo unit tests, compile checks, lints, or focused
  hardware probes.
- It does not duplicate every historical command when several commands test
  the same semantic behavior.

## Repository layout

```text
tests/e2e/
|-- system-utils/
|   |-- date-wall-clock.sh
|   `-- random-device.sh
|-- data-handling/
|   |-- archive-roundtrip.sh
|   `-- parallel-file-write.sh
|-- determinism-stress/
|   |-- order-violation.sh
|   `-- allocator-race.sh
|-- language-runtimes/
|   |-- python-hash-seed.sh
|   `-- go-build.sh
|-- applications/
|   |-- sqlite-transaction.sh
|   `-- local-http.sh
`-- lib/
    `-- common.bash

ci/e2e-harness.rs
ci/dag/e2e-portable.json
ci/dag/e2e-privileged.json
```

Discovery is exactly `tests/e2e/<known-category>/*.sh`. Files under `lib/` and
fixtures with extensions other than `.sh` are not tests. Each discovered file
must be executable, have a unique ID, contain valid metadata, and pass
`bash -n`. Adding an unannotated `.sh` file is a CI error.

The five category directory names are closed vocabulary:

| Category | Intended coverage |
| --- | --- |
| `system-utils` | Small Linux commands, procfs/sysfs readers, identity, time, entropy |
| `data-handling` | Files, streams, text transforms, compression, archives, databases |
| `determinism-stress` | Threads, processes, races, scheduling, signals, IPC, chaos targets |
| `language-runtimes` | Interpreters, managed runtimes, compilers, runtime entropy |
| `applications` | Multi-step programs and services such as Git, SQLite, Redis, HTTP, LevelDB |

## Test file contract

A test is a guest workload, not its own CI driver. The harness controls
repetition, Hermit invocation, backend selection, timeout, capture, and
comparison. The test must:

- start with a Bash shebang and `set -euo pipefail`;
- use `E2E_TMPDIR` for all generated state and never modify the source tree;
- read only declared fixtures and environment variables;
- write the observation under test to stdout or declared artifact paths;
- leave orchestration and pass/fail comparison to the harness;
- avoid network access unless the metadata declares a loopback-only or
  external-network capability;
- be deterministic for a fixed Hermit mode/backend/seed except when the mode's
  assertion explicitly compares distinct executions.

Tests may source `tests/e2e/lib/common.bash`. A complex fixture may have source
or data files beside the test, but the `.sh` file remains the sole test
identity and entrypoint. Build steps must write into `E2E_TMPDIR`; preparation
that cannot safely run inside the guest should become an explicit harness
fixture dependency rather than an inline CI command.

## Embedded JSON annotation

Metadata is embedded so the workload and its coverage contract cannot drift.
The harness extracts the text between the two exact markers, removes one
leading `# ` from each line, and parses the result as strict JSON. Unknown
fields fail validation.

```bash
#!/usr/bin/env bash
# HERMIT_E2E_META_BEGIN
# {
#   "schema": 1,
#   "id": "determinism-stress/order-violation",
#   "category": "determinism-stress",
#   "description": "Seeded schedules expose and reproduce an ordering bug",
#   "lane": "portable",
#   "requires": ["linux", "x86_64", "userns", "ptrace"],
#   "timeout_seconds": 60,
#   "observation": {
#     "status": true,
#     "stdout": true,
#     "stderr": false,
#     "artifacts": []
#   },
#   "modes": {
#     "naked": {
#       "runs": 4,
#       "assert": {"min_distinct": 2}
#     },
#     "verify": {
#       "backends": ["ptrace", "dbi"]
#     },
#     "replay": {
#       "backends": ["ptrace"]
#     },
#     "chaos": {
#       "backends": ["ptrace"],
#       "seeds": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
#       "assert": {"min_distinct": 2, "min_failures": 1}
#     }
#   },
#   "disabled_modes": {}
# }
# HERMIT_E2E_META_END
set -euo pipefail
```

Every mode must appear in either `modes` or `disabled_modes`. A disabled mode
requires a nonempty reason, for example:

```json
"disabled_modes": {
  "naked": "This is a compatibility test, not a nondeterminism control",
  "chaos": "The guest is single-threaded and has no schedule-sensitive oracle"
}
```

This makes the standard denominator visible without pretending that all tests
belong in all modes.

### Schema rules

- `id` must equal `<category>/<filename-without-.sh>`.
- `category` must match the containing directory.
- `lane` is `portable` or `privileged`.
- `requires` uses a closed capability vocabulary.
- Backend names are `ptrace`, `dbi`, `kvm`, `sabre`, and `liteinst`.
- E9patch is represented by `preprocessors: ["e9patch"]` on a ptrace cell,
  never as another backend.
- `naked` has no backend list.
- Every other enabled mode has a nonempty backend allowlist.
- Per-test arbitrary Hermit argument strings are rejected. Typed fields and
  lane profiles supply flags so tests cannot quietly weaken strictness.
- `timeout_seconds` is bounded by policy; a larger value requires an explicit
  `slow_reason` and scheduled-lane placement.
- Observation artifact paths must be relative to `E2E_TMPDIR` and may not
  contain `..` or symlinks escaping that directory.

## Mode semantics

### Naked: negative control

The harness executes the script directly, with no Hermit process, in freshly
initialized scratch directories. It hashes the declared observation tuple:

```text
(exit status, stdout bytes, optional stderr bytes, declared artifact bytes)
```

The default assertion is at least two distinct hashes across three runs. Tests
can increase the run count or require a pass/fail mix. A naked test fails when
its claimed nondeterminism is not observed within its fixed budget.

Naked controls must use a source expected to vary reliably, such as kernel
entropy, wall-clock output with sufficient resolution, or a deliberately racy
program. A compiler timestamp is valid only after proving that the selected
toolchain actually embeds one; modern reproducible-build settings can make
that premise false. Scratch paths, PIDs printed only by the harness, and
timestamps introduced by setup are excluded so they cannot create a false
divergence.

### Verify: deterministic repeat

For each allowlisted backend, the harness runs:

```sh
hermit --backend BACKEND --log=off run --strict --verify -- TEST
```

The portable profile adds `--no-virtualize-cpuid` and
`--max-timeslice=disabled`. The privileged profile retains PMU/CPUID behavior
only when the test declares those capabilities. A cell passes only if Hermit
exits zero and its built-in repeat comparison succeeds. The result records the
exact backend, effective arguments, log level, relaxations, binary SHA, and
test SHA.

The allowlist is a positive contract: every listed combination is required.
A missing backend binary, skipped run, timeout, or unavailable declared
capability is not green.

### Replay: record/replay comparison

The required replay cell runs in an isolated data directory:

```sh
hermit --backend BACKEND record start --strict --verify \
  --data-dir E2E_RECORD_DIR --record-timeout SECONDS -- TEST
```

`record start --verify` records, replays, compares output/status/logs, and
deletes the successful temporary recording. The harness still owns an outer
timeout and process-subtree teardown.

Record/replay is coupled to the sequentialized ptrace scheduler. It is not
currently a valid Cartesian product over run backends:

| Selection | Current record/replay status |
| --- | --- |
| `ptrace` | Real record and replay runtime; initial allowlist |
| `dbi` | Must be rejected by the harness today; CLI selection is not dispatched by `record_start` and would exercise ptrace |
| `kvm` | CLI rejects record/replay; KVM run does not provide the required scheduler trace |
| `liteinst` | CLI rejects non-run use |
| `sabre` | No independent record/replay runtime |
| ptrace + e9patch preprocessing | Valid separate variant for explicitly allowlisted ELF tests; replay remains ptrace |

Therefore the initial replay allowlist is `ptrace` only. Metadata must not
claim DBI/KVM/LiteInst/SaBRe replay until the product has an actual recording
and replay implementation for that runtime. The harness must proactively
reject DBI record cells to prevent a false backend pass.

The requested `{backend} x {run,replay}` matrix is represented as planned
cells, not assumed support: a backend may have a required verify/run cell and
no replay cell, with an explicit disabled reason. Adding a replay backend is a
product milestone followed by an allowlist change.

### Chaos: distinct seeded schedules

For every allowlisted backend and seed, the harness runs the test with:

```sh
hermit --backend BACKEND --log=off run --strict --chaos \
  --sched-heuristic=random --seed=SEED -- TEST
```

Portable chaos tests use scheduling points and
`--max-timeslice=disabled`; PMU-preemption chaos is privileged. The assertion
is relational across seeds and configurable with:

- `min_distinct`: minimum distinct observation hashes;
- `min_passes`: minimum zero-status outcomes;
- `min_failures`: minimum nonzero-status outcomes;
- `required_markers`: output markers that must be witnessed;
- `seeds`: a fixed, reviewed seed set.

Once distinct witness seeds are found, the harness repeats those seeds and
requires each seed to reproduce its own observation. Thus a chaos pass means
both cross-seed diversity and within-seed reproducibility. Randomly choosing
seeds in required CI is forbidden; scheduled exploration may add random seeds
but must print and retain them.

## Backend allowlists and gap audits

Each mode's `backends` array is the only source of required backend cells. The
harness never guesses support from a global backend maturity table.

Allowlist policy:

1. A combination enters the allowlist only with a local passing result and a
   CI result at the exact Hermit SHA.
2. Removing a combination requires a reviewed regression record; it may not be
   deleted merely to make CI green.
3. Backend availability and test semantic support are separate. An installed
   DBI runtime does not imply DBI is allowlisted for every test.
4. SaBRe and LiteInst cells must prove the shared Detcore Tool path rather than
   only launcher success.
5. E9patch results are labeled `backend=ptrace, preprocessor=e9patch`.

A scheduled `audit-gaps` job expands every meaningful non-allowlisted
combination. These cells are nonblocking and use the following classifications:

- `EXPECTED_GAP`: failed with the recorded unsupported reason;
- `XPASS`: succeeded and should be proposed for allowlisting;
- `CHANGED_FAILURE`: still failed, but differently from the recorded reason;
- `INFRA_ERROR`: the runtime or runner was unavailable, so no product result
  was obtained.

An XPASS is prominent output, not an automatic allowlist edit. The job uploads
its full matrix and opens or updates a task when a combination passes on two
consecutive scheduled runs.

## Harness interface

`ci/e2e-harness.rs` follows the repository's Rust-script convention and owns
schema parsing, discovery, planning, execution, capture, comparison, and
reporting.

Proposed commands:

```sh
# Validate metadata and print the standard denominator.
ci/e2e-harness.rs validate

# Print all planned cells without executing them.
ci/e2e-harness.rs plan --lane portable --format json

# Run required cells for one lane.
ci/e2e-harness.rs run --lane portable \
  --results target/e2e/portable/results.jsonl \
  --junit target/e2e/portable/junit.xml

# Run only one dimension while iterating.
ci/e2e-harness.rs run --lane privileged --mode verify --backend kvm \
  --test system-utils/date-wall-clock

# Probe combinations outside the allowlist.
ci/e2e-harness.rs audit-gaps --lane privileged \
  --results target/e2e/gaps/results.jsonl
```

`plan` is deterministic: sorting is category, test ID, mode, backend,
preprocessor, then seed. The plan includes disabled-mode reasons and excluded
backend reasons, so the denominator can be audited without executing tests.

The runner may execute independent cells concurrently, but cells using Hermit
share a configurable `hermit_guest` semaphore. PMU and KVM cells additionally
claim the DAG runner's `pmu` or `kvm` resource. A single test's relational
repeats remain on one worker and run serially.

## Structured results

Every planned required cell produces exactly one JSONL record. There are no
silent skips.

```json
{
  "schema": 1,
  "run_id": "github-123456-2",
  "hermit_sha": "40-hex",
  "test_sha256": "hex",
  "test": "determinism-stress/order-violation",
  "category": "determinism-stress",
  "lane": "portable",
  "mode": "chaos",
  "backend": "ptrace",
  "preprocessor": null,
  "seed": 9,
  "attempt": 1,
  "outcome": "PASS",
  "classification": "required",
  "exit_code": 1,
  "duration_ms": 427,
  "observation_sha256": "hex",
  "stdout_sha256": "hex",
  "stderr_sha256": "hex",
  "effective_args": ["--strict", "--chaos", "--seed=9"],
  "log_level": "off",
  "relaxations": ["max-timeslice=disabled", "no-virtualize-cpuid"],
  "reason": null
}
```

`outcome` is `PASS`, `FAIL`, or `ERROR` for required cells. Gap-audit records
add `EXPECTED_GAP`, `XPASS`, `CHANGED_FAILURE`, and `INFRA_ERROR`. A nonzero
guest exit can be a passing chaos observation; the relational assertion, not
the raw status alone, determines the cell outcome.

The runner also writes:

- JUnit XML for GitHub's test UI;
- `summary.json` with unique-test and planned-cell denominators;
- captured stdout/stderr for failed or error cells;
- a Markdown step summary grouped by category, mode, and backend;
- exact plan and environment/capability probe JSON.

The summary reports both levels:

```text
tests: 84 unique
planned required cells: 217
naked: 12/12 passed
verify: 151/153 passed
replay: 38/38 passed
chaos: 14/14 passed
gap audit: 2 XPASS, 47 EXPECTED_GAP, 1 INFRA_ERROR
```

It must never report `84/84` when only a subset of the 217 required cells ran.

## Isolation and failure handling

Each cell receives a fresh directory under `target/e2e/runs/<run-id>/` and a
fresh record data directory. The harness:

- sets `LC_ALL=C`, `TZ=UTC`, a controlled `PATH`, and an empty stdin;
- passes a stable logical work path to the guest;
- captures stdout and stderr separately as bytes;
- runs with an outer timeout, foreground process group, and kill grace period;
- kills the entire process subtree on timeout;
- records host capabilities before the first test;
- limits output retained in the CI log while uploading complete failure logs;
- never shares mutable fixtures, record directories, ports, or Unix sockets;
- allocates loopback ports rather than hard-coding them;
- rejects source-tree changes after every cell;
- treats missing tools and capability mismatches as errors, not skips.

Tests that intentionally inspect time, entropy, or process identity declare
which bytes are part of their observation. Normalizers are named built-in
functions, not arbitrary shell filters, and both raw and normalized hashes are
retained. This prevents a normalizer from erasing a real difference.

## Portable and privileged CI DAGs

### Portable lane

The portable lane runs on GitHub-hosted x86-64 Linux with namespaces, ptrace,
and seccomp, but assumes no user PMU, CPUID faulting, or `/dev/kvm`.

Proposed nodes:

```text
metadata.validate
build.workspace
build.backend_artifacts
e2e.naked
e2e.verify_ptrace
e2e.verify_dbi
e2e.verify_liteinst
e2e.replay_ptrace
e2e.chaos_syscall_points
results.merge
```

`metadata.validate` has no build dependency and fails quickly. Independent
backend cells may run concurrently after their artifacts exist. Every Hermit
guest node claims `hermit_guest: 1` until isolated parallel execution has been
measured. The portable profile explicitly disables PMU timeslicing and CPUID
virtualization; this relaxation is present in every result.

SaBRe portable cells should be enabled only after its required runtime assets
are reproducibly installed on hosted workers. E9patch preprocessing cells are
separate ptrace variants and likewise depend on a reproducible e9tool asset.

### Privileged lane

The privileged lane runs only on the protected self-hosted runner. Its first
node probes PMU overflow delivery, CPUID faulting, and read/write `/dev/kvm`
independently. A KVM failure must not be reported as a PMU failure.

Proposed nodes:

```text
capability.probe
build.hardware_tests
e2e.verify_kvm               [resource: kvm]
e2e.verify_ptrace_pmu         [resource: pmu]
e2e.replay_ptrace_pmu         [resource: pmu]
e2e.chaos_pmu                 [resource: pmu]
results.merge
```

The DAG retains separate resource caps `pmu: 1` and `kvm: 1`; tests request
only what they need. The lane does not rerun all portable tests. It contains
only metadata cells whose `requires` include `pmu`, `cpuid_faulting`, or `kvm`,
plus a small reviewed cross-lane sentinel set.

### Workflow migration

The existing required GitHub job names and merge-gate inputs must remain
stable during rollout. Initially add `e2e-portable.json` nodes behind the
current hosted workflow and `e2e-privileged.json` nodes behind the current
self-hosted workflow. Rename outer workflows or branch-protection checks only
after the new jobs have a green history. `ci/run-dag.sh` should temporarily
accept both old (`hosted`, `hardware`) and new (`portable`, `privileged`) lane
names with a deprecation message.

## Migration plan

### Phase 0: inventory and schema

1. Implement `validate` and `plan` without executing tests.
2. Import the current five example scripts as wrapper tests with ptrace verify
   allowlists and explicit disabled-mode reasons.
3. Add a CI invariant comparing discovered IDs with the generated plan.
4. Publish plan artifacts while the existing five-example script remains the
   blocking source.

### Phase 1: mode sentinels

Add a small end-to-end set that proves each relation:

- naked wall-clock or entropy divergence;
- ptrace strict verification;
- ptrace record/replay verification;
- chaos cross-seed diversity plus within-seed reproduction;
- one portable DBI or LiteInst allowlisted cell;
- one privileged KVM allowlisted cell.

Run old and new paths together for one week. Compare exact commands and results
before making the harness blocking.

### Phase 2: semantic migration

Migrate the 258 hosted-fast inventory rows first, consolidating duplicate
command variants into semantic tests. Preserve every original source label in
a generated migration map. Then migrate the hosted-medium DBI, LiteInst,
language, and application behaviors with their fixture setup.

Do not create 856 nearly identical LiteInst shell files. One semantic test may
allowlist multiple backends, producing one required cell per backend. The
unique-test denominator will therefore be smaller than the historical command
count while the cell plan retains or increases backend coverage.

### Phase 3: privileged and scheduled coverage

Migrate the eight hardware commands with explicit capabilities. Add PMU chaos
and record/replay only where the test requires PMU-strength scheduling. Move
the occasional Java case to a scheduled profile until it is stable enough for
required CI.

Enable `audit-gaps` nightly after the required matrix is stable. Its results
must never satisfy a required check, but XPASS records feed allowlist growth.

### Phase 4: remove command inventories

Delete `ci/e2e_commands_bucketed.sh` only after:

- every current example has a test ID and required cell;
- the migration map accounts for every retained historical semantic case;
- two consecutive main runs of both lanes are green;
- result artifacts are retained and visible;
- the old and new required check names have been reconciled with merge gate.

Keep the parent research inventory as provenance; do not execute it in CI.

## Acceptance criteria

The implementation is complete when all of the following are true:

1. `ci/e2e-harness.rs validate` rejects missing, duplicate, malformed, or
   contradictory metadata.
2. Every discovered test explicitly enables or disables all four modes.
3. Every enabled Hermit mode has a backend allowlist.
4. Every required planned cell emits exactly one structured result.
5. Naked controls fail when their declared nondeterminism is absent.
6. Verify cells execute `--strict --verify` on the named backend.
7. Replay cells cannot silently fall back to ptrace under another backend
   label.
8. Chaos cells prove cross-seed diversity and reproduce witness seeds.
9. Portable CI assumes no PMU, CPUID faulting, or KVM access.
10. Privileged CI probes and allocates PMU, CPUID, and KVM independently.
11. Non-allowlisted combinations are visible in scheduled gap-audit results.
12. CI summaries report both unique-test and required-cell denominators.
13. No CI DAG node contains a test body or inline `bash -c` workload.
14. The old five-example blocking path is removed only after evidence-equivalent
    replacement and two green main runs.

## Immediate implementation sequence

1. Land schema, discovery, validation, and deterministic plan output.
2. Convert one sentinel per mode and one test per category.
3. Add JSONL/JUnit reporting and process-subtree timeout handling.
4. Wire reporting-only portable and privileged DAG nodes.
5. Compare against current CI, then promote the required cells.
6. Add scheduled gap audits and begin semantic migration of the larger
   inventory.

This order establishes coverage accounting before scaling execution. It also
prevents unsupported backend labels, especially DBI record/replay, from
creating false-positive maturity evidence.
