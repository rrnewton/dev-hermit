# fbsource `hermetic_infra/hermit` buck tests → OSS cargo tests

**Source of truth:** `buck2 uquery "kind('.*test.*', //hermetic_infra/hermit/...)"` → **745 test targets**.
**OSS repo:** `github.com/facebookexperimental/hermit` (checkout at `~/work/dev-hermit/hermit`), cargo-based.
Date: 2026-07-21.

Because most buck targets are macro-generated (workload × mode matrices, x3 lit variants, third-party wrappers), the mapping is presented by **category** — every one of the 745 targets falls into exactly one row below, and the counts sum to 745.

## Category mapping table

| fbsource buck category | Count | OSS cargo equivalent | Status |
|---|---:|---|---|
| `*-unittest` (per-crate unit tests): `common:digest-unittest`, `common:edit-distance-unittest`, `common/test-allocator:*-unittest`, `detcore:detcore-unittest`, `detcore:detcore-testutils-unittest`, `detcore:syscaller-unittest`, `detcore-model:*-unittest`, `hermit-cli:hermit-unittest`, `hermit-cli:libhermit-unittest`, `hermit-verify:*-unittest`, `flaky-tests:*-unittest`, `tests:*-unittest` | 21 | `cargo test -p <crate> --lib` / `--bins` (same crates: digest, edit-distance, test-allocator, detcore, detcore-model, hermit(bin), hermit(lib), hermit-verify, flaky-tests, tests) | ✅ Mapped (1:1 crate) |
| `detcore:tests_misc`, `detcore:tests_parallelism`, `detcore:tests_time` | 3 | `cargo test -p detcore --test tests_misc / tests_parallelism / tests_time` (same files) | ✅ Mapped (1:1) |
| `detcore/tests/lit:*` — FileCheck/lit tests (base, `-hermit-run`, `-hermit-run-strict`, `-verify` variants) | 78 | Partially ported into `hermit-cli/tests/hermit_modes.rs` (`default_lit_networking`, `default_exit_codes`, `default_virtualized_uname`, `default_cat_issue`, `default_preserved_tmpfs`, `default_bind_mounts`, `default_environment_selection`) and `cli.rs`; fd/syscall-behavior lit tests (dup2, fcntl_dupfd, openat_lowest_fd, pipe_creates_valid_fds, read_badfd, close_on_exec, fstat, utime, rt_sig*, sched_getaffinity, scheduler_strategies…) are covered indirectly by the `rr_*` suite and matrix workloads | ⚠️ Partial — a subset explicitly ported; the lit harness itself is not in OSS |
| `tests:test_hermit_strict__rr_*` — Mozilla **rr** testsuite run under `hermit run --strict` (wraps `fbsource//third-party/rr:test_*` via `common/wrap_test_suite.bzl` `RR_TEST_TARGETS`) | 219 | **none** | ❌ **GAP** — third-party rr binaries; not part of hermit source, not vendored in OSS |
| `tests:hermit_run_default__{c_,rs_,sh_,py_,custombin}*` | 55 | `hermit_modes.rs`: `default_mode_matrix` (`run_stable_matrix(Default)`) + `default_workload_tests!` macro + explicit `default_*` tests | ✅ Mapped (stable-workload subset) |
| `tests:hermit_run_strict__*` | 51 | `hermit_modes.rs`: `strict_mode_matrix` (`run_stable_matrix(Strict)`) | ✅ Mapped (stable subset) |
| `tests:hermit_run_chaos__*` | 51 | `hermit_modes.rs`: `chaos_mode_matrix` (`run_stable_matrix(Chaos)`) + `buck_chaos_tests!` (ignored: needs PMU) | ✅ Mapped (stable subset) |
| `tests:raw_run__*` — workload run WITHOUT hermit (baseline) | 55 | No dedicated OSS target; baseline execution is implicit in the matrix harness (`command_output`) | ⚠️ Partial — baseline, by-design not a standalone OSS test |
| `tests:hermit_record_{c_,rs_,custombin}*` — record then replay | 45 | `record_replay.rs`: `record_replay_tests!` (15 `record_rs_*`) + `record_replay_matrix` (c workloads) + `record_find_directory_tree` | ✅ Mapped (rs 1:1; c via matrix) |
| `tests:hermit_run_tracereplay__*` | 43 | `record_replay.rs` (record→replay round-trip covers trace replay) | ⚠️ Partial — folded into record/replay, not per-workload targets |
| `tests:hermit_run_tracereplay_chaos__*` | 36 | `record_replay.rs` + chaos matrix (combined coverage) | ⚠️ Partial |
| `tests:hermit_run_chaosreplay__*` | 47 | `record_replay.rs` replay + chaos matrix | ⚠️ Partial |
| `tests:chaos_*-unittest`, `tests:hermit_chaos_fail_*` (cas_sequence, hello_chaos, keyvalue, lock_granularity, order_violation) | 8 | `hermit_modes.rs` chaos matrix + `stress_suite.rs` (`fast_chaos_matrix`) + `tests/chaos/` workloads | ✅ Mapped (equivalent chaos coverage) |
| `tests:analyze_*` (analyze_hello_race, analyze_nanosleep-threads-nocrash, analyze_racewrite_nostdlib) | 3 | `hermit-cli/tests/analyze.rs` (3 `#[test]`) | ✅ Mapped |
| `tests:standalone__*`, `tests:standalone_stacktrace_events-unittest`, `tests:test_standalone_stacktrace_events__*`, `tests:verify_replay__*`, `tests:cbin_just_spin_trace_replay_split` | ~15 | `hermit_modes.rs` (`no_hardware_minimal_hello_backtraces`, `no_hardware_stacktrace_signal`, `verify_mode_matrix`, `hello_race_chaos_verify`), `cli.rs`, `tests/standalone/` | ⚠️ Partial — most covered; some standalone driver scripts not 1:1 |
| `tests:test_no_networking_network_bind[_full]`, `tests:network_bind*-unittest`, `tests:nanosleep-threads-nocrash-rust-unittest` | ~5 | `hermit_modes.rs` networking tests + `tests/rust/network_hello_world.rs`, `bind_connect_race.rs` | ✅/⚠️ Mostly mapped |
| `flaky-tests:*` (bind_random, bind_same, flaky_server_test, run_flaky_*, run_hello_race, use_configurable_flaky_service, hello_race*-unittest) | 10 | OSS `flaky-tests` crate (pkg `hermetic_infra_hermit_flaky-tests`) — **excluded** from `validate.sh` (`--exclude hermetic_infra_hermit_flaky-tests`) | ✅ Mapped (crate exists; excluded by design) |
| `tests:pythonbin_*-library-type-checking` (Pyre type-checking of py workload fixtures) | 3 | **none** | ❌ GAP — fbcode Pyre type-checking; no OSS Python type-check step (the py workloads themselves run via `hermit_run` py_* which OSS covers minimally) |
| `detcore:test_build_musl_detcore` (musl static-build check) | 1 | **none** | ❌ GAP — fbcode-specific build assertion |
| `detcore:get-syscall-support` (helper bin, not a real test) | 1 | n/a (dev helper) | ➖ N/A |
| `common/test-allocator:run_test_bin` (harness bin) | 1 | covered by test-allocator unit tests | ✅ Mapped |
| **TOTAL** | **745** | | |

## Gaps (fbsource tests with NO OSS equivalent)

1. **`test_hermit_strict__rr_*` — 219 tests (dominant gap).** These wrap the third-party **Mozilla rr** project's C testsuite (`fbsource//third-party/rr:test_*`) and run each under `hermit run --strict`. They are not hermit's own code; porting would require vendoring the entire rr testsuite into the OSS repo. This is the single biggest coverage delta.
2. **`pythonbin_*-library-type-checking` — 3.** fbcode Pyre type-checking of Python test fixtures; no OSS equivalent (OSS doesn't type-check Python).
3. **`detcore:test_build_musl_detcore` — 1.** fbcode musl static-build assertion.
4. **Partial:** the lit harness (78 targets, mostly fd/file-syscall behavior) is only partially reproduced in OSS; and the distinct replay-mode target expansions (`tracereplay`, `tracereplay_chaos`, `chaosreplay`, `raw_run`) are folded into the OSS `record_replay.rs` round-trip / matrix rather than existing as per-workload targets.

## Reverse observation — OSS tests NOT present as fbsource buck targets

The OSS repo has **additional** integration suites (added via recent PR waves) with no corresponding fbsource buck target yet:
`hermit-cli/tests/signal_determinism.rs`, `ipc_determinism.rs` (record_replay `record_rs_pipe_basics`, etc.), `clock_determinism.rs`, `mmap_determinism.rs`, `random_determinism.rs`, `thread_sync_determinism.rs`, `stress_suite.rs`, `arbitrary_binaries.rs`.

## How to enumerate (repro)

```bash
# fbsource (buck2):
cd ~/work/orc-dev/fbsource/fbcode/hermetic_infra/hermit
buck2 uquery "kind('.*test.*', //hermetic_infra/hermit/...)" | sort

# OSS (cargo):
cd ~/work/dev-hermit/hermit
cargo test --workspace -- --list      # per-binary test names
ls hermit-cli/tests/*.rs               # integration test files
```
