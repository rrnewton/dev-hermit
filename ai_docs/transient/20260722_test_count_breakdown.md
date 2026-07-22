# Hermit test-count breakdown — reconciling every denominator

Date: 2026-07-22
Task: `impl-test-count-breakdown` (owner: hermit-025)
Scope: research / read-only. No code changed.

## Purpose

Rubric statements like "69/89" and "81/89" and inventory figures like "337 / 40"
or "602 / 43" and "more than 700 integration tests" are not interpretable
without knowing **what set each denominator counts**. This document enumerates
every test bucket, gives per-bucket counts for both `main` and `frontier`, ties
each rubric denominator to a named bucket, and flags where the published numbers
are stale relative to the live tree.

## Checkout provenance (critical — the two primaries disagree by design)

| Checkout | Branch | HEAD | Fail-closed doc says | Role |
| --- | --- | --- | --- | --- |
| `~/work/dev-hermit/hermit/` | `frontier` | `344200e` | **3/89 (STALE)** | Frontier primary (review stack) |
| `~/work/dev-hermit/main/hermit/` | `main` | `589173c` | **69/89 (authoritative)** | Main primary (rebase base) |

The `hermit/` primary is on **frontier**, not `main`. Its
`docs/FAIL_CLOSED_STATUS.md` still reports `3/89`; that number is stale and must
not be cited (see `20260722_progress_rubric_v2.md` lines 261, 269–271). All
"main" numbers below come from `main/hermit/` unless stated otherwise.

---

## Part 1 — What the "89" is (the fail-closed denominator)

**The `89` = 69 passing + 20 known-failing = the applicable, non-ignored
integration test cases that exercise the `hermit run` fail-closed path** (the
Detcore unsupported-syscall → panic diagnostic, enabled with
`HERMIT_FAIL_CLOSED=1`).

It is produced by `scripts/test-fail-closed.sh`, which walks every test in
`hermit-cli/tests/*.rs` and classifies each one:

```
for each test in hermit-cli/tests/*.rs:
    if target in {cli, record_replay}                      -> mode N/A   (whole target)
    elif test == arbitrary_binaries/record_replay_stable_* -> mode N/A   (record/replay path)
    elif test is #[ignore]  (must be in allowed_ignores.tsv)-> ignored
    elif test in fail_closed_known_failures.tsv            -> known failure   ┐ these two
    else -> run under HERMIT_FAIL_CLOSED=1, must exit 0     -> passed          ┘ sum to 89
```

So **89 = passed + known-failures = the tests the ratchet actually attempts
under fail-closed** (each either passes or is catalogued as a first-blocker
failure). Ignored and mode-N/A tests are excluded from the denominator.

### The `main` fail-closed table (from `main/hermit/docs/FAIL_CLOSED_STATUS.md`)

| Target | pass | known-fail | ignored | mode N/A |
| --- | ---: | ---: | ---: | ---: |
| `arbitrary_binaries` | 0 | 1 | 0 | 1 |
| `cli` | 0 | 0 | 0 | 10 |
| `clock_determinism` | 1 | 0 | 0 | 0 |
| `epoll_determinism` | 4 | 1 | 0 | 0 |
| `hermit_modes` | 49 | 14 | 8 | 0 |
| `ipc_determinism` | 1 | 0 | 0 | 0 |
| `mmap_determinism` | 5 | 0 | 0 | 0 |
| `procfs_determinism` | 6 | 0 | 0 | 0 |
| `random_determinism` | 1 | 0 | 0 | 0 |
| `record_replay` | 0 | 0 | 0 | 17 |
| `signal_determinism` | 1 | 4 | 0 | 0 |
| `stress_suite` | 0 | 0 | 3 | 0 |
| `thread_sync_determinism` | 1 | 0 | 0 | 0 |
| Hermit lib+bin unit tests | 0 | 0 | 0 | 33 |
| **Total** | **69** | **20** | **11** | **61** |

- **89 = 69 + 20.** That is the denominator.
- Ignored (11) + mode-N/A (61) are outside the 89.
- The `20` known failures are exactly the 20 data rows in
  `hermit-cli/tests/fail_closed_known_failures.tsv`; their first-blocking
  syscalls are `ioctl`(7), `tgkill`(4), `mkdir`(3), `setitimer`(2),
  `clock_settime`(1), `getrlimit`(1), `kill`(1), `setsockopt`(1).
- The `33` "unit tests" row is a **documentation-only** classification: the
  ratchet script never touches lib/bin unit tests. Its own printed `mode N/A`
  count is only cli(10)+record_replay(17)+arb-record/replay(1) = 28; the doc
  table adds the 33 hermit-cli unit tests for completeness, giving 61.

### Frontier's 3/89

Same 89-shaped table, but frontier's Detcore models fewer of the blocking
syscalls at that snapshot, so only 3 pass (`default_minimal_hello`,
`no_hardware_minimal_hello_backtraces`, `no_hardware_stacktrace_signal`) and 86
fail. **3/89 is stale** — main advanced to 69/89 after the `pread64`/`rseq`/
`lseek`/`fadvise64`/`getpid`/`gettid` batches. Do not cite frontier's 3/89.

---

## Part 2 — The "89" is itself a stale snapshot; the live denominator is larger

The prose table above is a point-in-time snapshot. The **source-controlled
manifests** (validated on every ratchet run) already describe a *larger* live
tree, and the integration inventory has grown since the snapshot was written.

Ground-truth live inventory, obtained by running the already-built test binaries
in `main/hermit/target/debug/deps/*` with `--list` (read-only; no rebuild):

| `hermit-cli/tests/*.rs` target | total tests | ignored | in doc table? |
| --- | ---: | ---: | --- |
| analyze | 3 | 3 | ❌ not in table |
| arbitrary_binaries | 4 | 0 | ⚠ table said 2 |
| cli | 12 | 0 | ⚠ table said 10 |
| clock_determinism | 2 | 0 | ✅ |
| compression | 1 | 0 | ❌ new |
| epoll_determinism | 5 | 0 | ✅ |
| fp_reduction_determinism | 2 | 0 | ❌ new |
| hashseed_determinism | 1 | 0 | ❌ new |
| hermit_modes | 75 | 10 | ⚠ table said 71 total, 8 ignored |
| integration_matrix | 1 | 0 | ❌ new |
| ipc_determinism | 1 | 0 | ✅ |
| language_runtime_determinism | 6 | 6 | ❌ new |
| mmap_determinism | 5 | 0 | ✅ |
| procfs_determinism | 6 | 0 | ✅ |
| python_stdlib | 2 | 1 | ❌ new |
| random_determinism | 1 | 0 | ✅ |
| record_replay | 17 | 0 | ✅ |
| signal_determinism | 10 | 0 | ⚠ table said 5 |
| stress_suite | 5 | 3 | ✅ |
| thread_scheduling_fairness | 3 | 0 | ❌ new |
| thread_sync_determinism | 1 | 0 | ✅ |
| **TOTAL integration tests (live main)** | **163** | **16** | |

The doc table accounts for ~128 integration tests (161 total − 33 unit); the live
tree has **163**. The delta is new files (analyze, compression, fp_reduction,
hashseed, integration_matrix, language_runtime, python_stdlib,
thread_scheduling_fairness) plus growth in hermit_modes (71→75) and
signal_determinism (5→10), added by concurrent agents.

### Manifests already reflect the live tree; the prose table does not

- `fail_closed_known_failures.tsv` = **20** data rows → matches the "20".
- `fail_closed_allowed_ignores.tsv` = **16** data rows. Live `--list --ignored`
  gives exactly 16: hermit_modes 10 (8 `chaos_buck_*` + `default_cargo_bind_connect_race`
  + `default_cargo_clock_total_order`) + analyze 3 + stress_suite 3. The prose
  table's `ignored = 11` is the pre-growth snapshot (before analyze and the 2
  default_cargo ignores were counted).

### Derived live fail-closed denominator (analytic, not a fresh ratchet run)

Applying the ratchet's classification to the 163 live tests:

```
163 total integration tests
 − 30 mode N/A    (cli 12 + record_replay 17 + arbitrary_binaries/record_replay_stable 1)
 − 16 ignored     (matches allowed_ignores.tsv exactly)
 = 117 applicable non-ignored   ← the live "89-equivalent" denominator
     of which 19 are active known-failures* → ~98 passing
```

\* 20 known-failure rows, but `default_cargo_bind_connect_race` is now also
`#[ignore]`; the ratchet's ignore check precedes the known-failure check, so it
counts as ignored, leaving 19 counted as failing.

**Takeaway:** the honest current denominator is ≈**117**, not 89. `89` was
correct for the doc's snapshot but the applicable set has grown. A fresh
`./scripts/test-fail-closed.sh` run (which writes to `target/`, so must be run
from a slot, not a primary) is required to publish exact live pass/fail numbers.

---

## Part 3 — Every test bucket (the task's a–g), counts, and Lx status

Counts are live (`--list` on built binaries). Lx status uses the assurance
ladder in `AGENTS.md` (L0 cargo test; L1 `--strict`; L2 `--strict --verify`;
L3 `+--detlog-heap --detlog-stack`; L4 = L2/L3 × 20 with no divergence) and the
per-axis analysis in `20260722_progress_rubric_v2.md`.

### (a) Unit tests — `cargo test`, no `hermit run`

| Crate suite (main) | tests | frontier |
| --- | ---: | ---: |
| `detcore` (lib) | 64 | 62 |
| `detcore_model` | 16 | 20 |
| `detcore_testutils` | 1 | 1 |
| `digest` | 1 | 1 |
| `edit_distance` | 24 | 24 |
| `hermit` (hermit-cli lib+bin) | 23 | 16 |
| `hermit_verify` | 21 | 21 |
| `test_allocator` | 2 | 2 |
| **Unit subtotal** | **152** | ~147 |

Status: **L0 only** (unit correctness). These are the "33 unit tests" mode-N/A
row in the fail-closed table, scoped to hermit-cli's lib+bin (23 today).

### (b) Integration tests using `hermit run` (the 89)

Live main: **163** in `hermit-cli/tests/*.rs`; applicable non-ignored ≈**117**
(snapshot 89). Status: **L1** for the passing set (unsupported syscalls panic
under fail-closed); **L2** for cases that run `--verify`; **not L4** — no 20×
stress campaign on the full set. Caveat: optimized Detcore subscribes to
selected syscalls only; the coverage audit records 291 release entries with no
active subscription, so an unsubscribed syscall never traps and silently escapes
the L1 guarantee (`ai_docs/syscall-coverage-map.md`).

### (c) Record/replay tests

`record_replay.rs`: **main 17**, **frontier 22** (frontier adds
timeout/curl/hardening cases). Status: **L0** on main (17 pass under
`cargo test`). An **L2-class 13/16** (or branch-local 16/16) byte-identical
result exists only on draft PRs #124/#128/#130/#132 — branch evidence, not
landed. Whole target is **mode-N/A for fail-closed** (does not run Detcore's
`hermit run` syscall policy the same way).

### (d) System-binary smoke tests (`/bin/echo`, etc.)

Live in `hermit_modes.rs` + `cli.rs` (e.g. run smoke, output-determinism,
verify-mode smoke). Status: **L0-relaxed** — these run without `--strict`, so
per rubric §"validate.sh" they establish L0 plus two relaxed smoke runs; they do
**not** reach L1/L2 (memory #19). A green `validate.sh` = L0 workspace-wide, not
L1+.

### (e) rr testsuite ports

- **main:** no Cargo rr suite (0).
- **frontier:** `hermit-cli/tests/rr_suite.rs` = **214 tests** (Mozilla rr guest
  programs run under Hermit; rubric cites "213 enabled + harness invariant").
Status: **L0** (guest programs run under Hermit; not a determinism verdict).
This is where the largest fbsource gap is being closed — see Part 5.

### (f) Full OSS app tests (SQLite, LevelDB, Redis, language runtimes)

Frontier-only, mostly `#[ignore]` (need external assets / heavy):

| Target (frontier) | tests | note |
| --- | ---: | --- |
| `leveldb` | 3 | app workload |
| `sqlite_veryquick` | 2 | app workload |
| `redis_strict` | 4 | app workload |
| `language_runtime_determinism` | 6 (all ignored) | JVM/Node/etc |
| `python_stdlib` | 2 (1 ignored) | |

Status: **L0 / opt-in**. Main has `language_runtime_determinism` (6, ignored)
and `python_stdlib` (2); leveldb/sqlite/redis targets are frontier-side.

### (g) `rustbin_*` / micro guest binaries

These are guest programs in the `tests` and `flaky-tests` workspace members that
integration tests execute under `hermit run` in various modes — they are **not
standalone `#[test]` functions**, so they do not appear in the cargo test count.
The only flaky-tests binary that surfaces as a test suite is `hello_race` (1).
The fbsource `rustbin_*`/matrix expansion of these guests is described in Part 5.

### Non-Lx / other backends (context for the other rubric denominators)

- **DBI (`--backend dbi`):** rubric/PR #126 reports **81/89** guest cases exit 0
  under `cargo test` — **L0 only** (same 89-shaped guest set, dbi backend).
  Cargo's "156/156" headline is not parity (8 xfails returned before executing).
  L1+ undefined for DBI (no deterministic scheduler; PR #138 makes
  `--strict --backend dbi` an explicit error).
- **KVM (`--backend kvm`):** **below L0** — runs a built-in hello/vmcall demo,
  not the requested ELF. PR #117: 1/10 smoke.

---

## Part 4 — The "337 / 40" and "602 / 43" cargo inventories

These come from `cargo nextest list` (rubric lines 94, 97, 266):

- **Main: 337 test functions in 40 suites, 16 `#[ignore]`.**
- **Frontier: 602 functions in 43 suites, 0 ignored in inventory.**

These are **whole-workspace inventories (unit + all integration + detcore
integration), not pass counts at any level.** My live enumeration of built
binaries gives:

| Group | main | frontier |
| --- | ---: | ---: |
| Unit (Part 3a) | 152 | ~147 |
| detcore integration (`tests_misc` 15/26, `tests_parallelism` 16, `tests_time` 12/13) | 43 | 55 |
| hermit-cli integration (`hermit-cli/tests/*.rs`) | 163 | see below |
| flaky-tests (`hello_race`) | 1 | 1 |
| **Live grand total (newest binary per name)** | **359** | **587** |

Main's live 359 vs the doc's 337: the doc used `cargo nextest list` at an earlier
2026-07-22 snapshot; the tree has since grown. Frontier's 587 vs 602 differs
because frontier's `target/` also contains **stale binaries from prior branch
checkouts** (e.g. `fork_exec_determinism`, `pthread_race_determinism`,
`redis_strict`, `sqlite_veryquick` show up in `deps/` but several are not in the
current frontier source `hermit-cli/tests/*.rs` listing). Frontier's dominant
contributor is `rr_suite` = 214. Both 337 and 602 are **inventory, not Lx pass
counts** — frontier CI is red, so 602 is not a pass count.

---

## Part 5 — Where are the 700+ fbsource test IDs? Are they ported?

Source of truth: `ai_docs/reference/fbsource-to-oss-test-map.md`
(`buck2 uquery "kind('.*test.*', //hermetic_infra/hermit/...)"` → **745 test
targets**, 2026-07-21). The "more than 700 integration tests" in `CLAUDE.md`
refers to these ~745 buck targets. Every target falls into one category; the
counts sum to 745. Porting status:

| fbsource category | buck count | OSS status |
| --- | ---: | --- |
| per-crate `*-unittest` | 21 | ✅ 1:1 (`cargo test -p <crate>`) |
| `detcore:tests_{misc,parallelism,time}` | 3 | ✅ 1:1 |
| `detcore/tests/lit:*` (FileCheck/lit) | 78 | ⚠ **partial** — a subset ported into `hermit_modes.rs`/`cli.rs`; lit harness itself not in OSS |
| **`tests:test_hermit_strict__rr_*` (Mozilla rr under `--strict`)** | **219** | ❌ **GAP on main**; ✅ **~214 ported on frontier** via `hermit-cli/tests/rr_suite.rs` |
| `tests:hermit_run_default__*` | 55 | ✅ matrix subset (`default_mode_matrix`) |
| `tests:hermit_run_strict__*` | 51 | ✅ matrix subset (`strict_mode_matrix`) |
| `tests:hermit_run_chaos__*` | 51 | ✅ matrix subset (`chaos_mode_matrix`) |
| `tests:raw_run__*` (no-hermit baseline) | 55 | ⚠ implicit baseline in matrix harness |
| `tests:hermit_record_*` | 45 | ✅ `record_replay.rs` (rs 1:1; c via matrix) |
| `tests:hermit_run_tracereplay__*` | 43 | ⚠ folded into `record_replay.rs` |
| `tests:hermit_run_tracereplay_chaos__*` | 36 | ⚠ folded |
| `tests:hermit_run_chaosreplay__*` | 47 | ⚠ folded |
| chaos unittests / `hermit_chaos_fail_*` | 8 | ✅ chaos matrix + `stress_suite.rs` |
| `tests:analyze_*` | 3 | ✅ `analyze.rs` (3) |
| standalone / verify_replay / stacktrace | ~15 | ⚠ mostly covered |
| networking bind tests | ~5 | ✅/⚠ mostly mapped |
| `flaky-tests:*` | 10 | ✅ crate exists (excluded from `validate.sh`) |
| `pythonbin_*-library-type-checking` (Pyre) | 3 | ❌ GAP (no OSS Pyre) |
| `detcore:test_build_musl_detcore` | 1 | ❌ GAP (fbcode musl build) |
| helper/harness bins | 2 | ➖ n/a / covered |
| **TOTAL** | **745** | |

**Answer:** The 700+ IDs are the ~745 fbsource buck test *targets*, most of them
macro-generated (workload × mode matrices, x3 lit variants, third-party
wrappers). They are **partially** ported:

- The mode matrices (default/strict/chaos, record/replay) are ported to OSS as
  compact macro-generated Cargo matrices rather than one-target-per-workload.
- The single biggest gap — **219 Mozilla `rr` testsuite targets** — is **not on
  main** but is **substantially ported on `frontier`** as `rr_suite.rs`
  (214 cases).
- Remaining hard gaps: the lit harness (78; only a subset reproduced), Pyre
  type-checking (3), and the fbcode musl build assertion (1).

OSS additionally has integration suites with *no* fbsource buck target yet
(`signal_determinism`, `ipc_determinism`, `clock_determinism`,
`mmap_determinism`, `random_determinism`, `thread_sync_determinism`,
`stress_suite`, `arbitrary_binaries`, and the newer app suites) — so the
main↔fbsource mapping is not one-directional.

---

## Denominator → bucket cross-reference (the deliverable "Verify" check)

| Denominator seen in rubric | Bucket it counts | Where |
| --- | --- | --- |
| **89** | pass(69)+known-fail(20) = applicable non-ignored fail-closed integration cases | Part 1 |
| **69/89** | main fail-closed passing set (L1/L2), 2026-07-22 snapshot | Part 1 |
| **3/89** | frontier fail-closed passing set — **STALE, do not cite** | Part 1 |
| **~117** | live applicable denominator after inventory growth | Part 2 |
| **81/89** | DBI guest cases exit 0 under `cargo test` (L0), same guest set | Part 3 |
| **156/156** or **156/11** | full Cargo run of the DBI branch (pass/ignored) — not parity | Part 3 |
| **13/16, 16/16** | record/replay byte-identical workloads (branch-only L2-class) | Part 3c |
| **17 / 22** | record/replay generated tests (main / frontier) | Part 3c |
| **214 (≈213)** | frontier rr_suite ports | Part 3e, 5 |
| **337 / 40** | main whole-workspace cargo inventory (functions / suites) | Part 4 |
| **602 / 43** | frontier whole-workspace cargo inventory | Part 4 |
| **745 (≈700+)** | fbsource buck test targets | Part 5 |
| **291** | release syscall subscription entries missing (fail-closed escape) | Part 1, 3b |
| **20** | `fail_closed_known_failures.tsv` rows | Part 1/2 |
| **11 / 16** | fail-closed ignored (doc snapshot / live manifest) | Part 2 |

## Reproduction

```bash
# Provenance
git -C ~/work/dev-hermit/hermit      rev-parse --abbrev-ref HEAD   # frontier
git -C ~/work/dev-hermit/main/hermit rev-parse --abbrev-ref HEAD   # main

# Fail-closed classification logic + manifests (read-only)
sed -n '80,162p' main/hermit/scripts/test-fail-closed.sh
grep -vcE '^#|^$' main/hermit/hermit-cli/tests/fail_closed_known_failures.tsv   # 20
grep -vcE '^#|^$' main/hermit/hermit-cli/tests/fail_closed_allowed_ignores.tsv  # 16

# Live counts WITHOUT rebuilding (run already-built binaries in target/debug/deps):
#   for each hermit-cli/tests/*.rs, run newest deps/<name>-<hash> --list  | grep -c ': test$'
#   and                                    --list --ignored | grep -c ': test$'
# A fresh, authoritative pass/fail requires running the ratchet from a SLOT (writes target/):
#   ./scripts/test-fail-closed.sh
```
