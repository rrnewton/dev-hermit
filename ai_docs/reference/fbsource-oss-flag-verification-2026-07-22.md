# fbsource → OSS test coverage: flag-for-flag verification

**Date:** 2026-07-22
**Task:** `research-verify-fbsource-mapping` — independently verify whether the OSS
cargo test suite *truly subsumes* the fbsource `hermetic_infra/hermit` buck suite,
comparing the actual run flags (`--strict`, `--verify`, relaxations) rather than
category counts.

**Verdict: NO — the OSS suite does NOT strictly subsume fbsource.** The earlier
category map (`fbsource-to-oss-test-map.md`, 2026-07-21) is stale and, on the two
most important rows, factually wrong. There are four genuine *strictness* gaps
where fbsource exercises determinism verification (`--verify` / the `hermit-verify`
record→replay harness) that OSS does not. The "strict superset" claim is false.

## Sources of truth

- fbsource flags: `hermetic_infra/hermit/tests/helpers.bzl`,
  `hermetic_infra/hermit/tests/BUCK`,
  `hermetic_infra/common/wrap_test_suite.bzl`
  (checkout: `~/work/orc-dev/fbsource/fbcode/hermetic_infra/hermit`).
- OSS flags: `hermit-cli/tests/{hermit_modes,record_replay,rr_suite}.rs`,
  `hermit-verify/tests/cli.rs` (checkout: `~/work/dev-hermit/hermit`).

## fbsource flag matrix (what each category actually runs)

| Category | Count | Runner | Flags | Determinism verified? |
|---|--:|---|---|---|
| `raw_run__` | 55 | guest, no hermit | `--no-sequentialize-threads --no-deterministic-io` | no (baseline) |
| `hermit_run_default__` | 55 | `hermit run` | `run --no-sequentialize-threads --no-deterministic-io --env=HERMIT_MODE=default` | no |
| `hermit_run_strict__` | 51 | **hermit-verify** | `run --isolate-workdir` + `--base-env=empty --env=HERMIT_MODE=strict --preemption-timeout=80000000` | **yes (record→replay)** |
| `hermit_run_chaos__` | 51 | **hermit-verify** | `run --isolate-workdir` + `--chaos --base-env=empty --preemption-timeout=1000000` | **yes** |
| `hermit_run_tracereplay__` | 43 | **hermit-verify** | `--verbose trace-replay --strip-times --isolate-workdir` + `--base-env=empty` | **yes** |
| `hermit_run_tracereplay_chaos__` | 36 | **hermit-verify** | `... trace-replay --strip-times --chaos ...` | **yes** |
| `hermit_run_chaosreplay__` | 47 | **hermit-verify** | `chaos-replay --isolate-workdir` + `--chaos --base-env=empty` | **yes** |
| `hermit_record_*` | 45 | `hermit record` | `record start --verify -- <bin>` | **yes** |
| `test_hermit_strict__rr_*` | 219 | `hermit run` | `run --base-env=minimal --verify --preemption-timeout=80000000 --workdir=/tmp` | **yes (`--verify`)** |
| `hermit_chaos_fail_*` | 5 | hermit-verify | `chaos-stress --max-iterations-count=N --chaos --base-env=minimal --preemption-timeout=<interval>` | n/a (fault injection) |

Key semantics:
- **"strict"** in fbsource = *full determinism* (sequentialize-threads + deterministic-io
  left ON) + deterministic preemptions (`--preemption-timeout=80000000`) + `--base-env=empty`,
  wrapped in `hermit-verify` (record then replay, assert identical).
- **`--verify`** on `hermit run` = run twice and compare (used for the rr suite).
- fbsource strict/chaos/tracereplay/chaosreplay all go through the **`hermit-verify`
  binary**, not plain `hermit run`.

## OSS flag matrix (what the cargo harness actually runs)

Base command (`hermit_modes.rs::hermit_command`):
`run --base-env=<env> --no-virtualize-cpuid --preemption-timeout=disabled`;
`compatibility_hermit_command` additionally adds `--allow-passthrough`; the whole
default/chaos/verify matrix runs through `compatibility_hermit_command("minimal")`.

| OSS test | Flags | Notes vs fbsource |
|---|---|---|
| `default_mode_matrix` / `RunMode::Default` | `+ --no-sequentialize-threads --no-deterministic-io --env=HERMIT_MODE=default` | **matches** fbsource default ✅ |
| `verify_mode_matrix` / `RunMode::Verify` | `+ --verify --env=HERMIT_MODE=verify` (full determinism; `--preemption-timeout=disabled`, `--base-env=minimal`) | closest thing to strict, but **no preemptions**, base-env differs |
| `chaos_mode_matrix` / `RunMode::Chaos` | `+ --chaos --env=HERMIT_MODE=chaos` (`--preemption-timeout=disabled`, **no `--verify`**) | weaker than fbsource chaos |
| `buck_chaos_tests!` (8 workloads) | `run --verify --chaos --base-env=empty --preemption-timeout=1000000 --env=HERMIT_MODE=chaos` | **matches fbsource chaos + verify**, but **PMU-gated** (skips w/o perf counters) ✅⚠️ |
| `strict_mode_matrix` | single fail-closed check: `run --base-env=minimal --strict <unsupported_syscall>` | **NOT a workload matrix** — misnomer |
| `strict_panics_on_unsupported_syscalls` | `run --strict` on one workload | policy check only |
| `record_replay_matrix` / `record_rs_*` | `record start --verify --record-timeout=30`; asserts `"Success: replay matched recording."` | **matches fbsource record, stronger** ✅ |
| `rr_suite.rs` (213 programs) | `run --base-env=minimal --preemption-timeout=80000000` (**no `--verify`**) | **missing `--verify`** ❌ |
| `hello_race_chaos_verify` | `run --verify --verify-allow=both --chaos --base-env=minimal` | verified chaos, 1 workload ✅ |

## The four strictness gaps

### Gap 1 — rr suite runs without `--verify` (largest)
fbsource runs all 219 enabled rr programs under `hermit run --verify` (twice,
determinism-checked). OSS `rr_suite.rs` runs each **once** and asserts only the exit
code (plus, for `rr_pause`, a stdout marker). So OSS covers rr *functional*
correctness but not *determinism*. ~213 programs affected.

This simultaneously **corrects** the old map: rr is NOT an unported 219-test gap.
It is ported (via the pinned `third-party/rr` submodule @ `39e5c18`,
`docs/rr-test-suite.md`), just at lower strictness (no `--verify`). Enabled counts:
OSS 213 vs fbsource 219.

### Gap 2 — no strict-mode workload matrix
fbsource `hermit_run_strict__` (51) runs every stable workload under full
determinism + `--preemption-timeout=80000000` + `--base-env=empty`, verified via
`hermit-verify`. OSS has no equivalent: `strict_mode_matrix` only does a single
fail-closed unsupported-syscall check, and there is no `RunMode::Strict` (the enum
is `Default | Chaos | Verify`). The closest analog, `verify_mode_matrix`, uses
`--preemption-timeout=disabled` (so *no deterministic preemptions are exercised*)
and `--base-env=minimal`. The old map's row 18 ("`strict_mode_matrix
(run_stable_matrix(Strict))`") is factually wrong.

### Gap 3 — chaos matrix mostly unverified / no preemptions
fbsource `hermit_run_chaos__` (51) = `--chaos --preemption-timeout=1000000
--base-env=empty` verified via `hermit-verify`. OSS `chaos_mode_matrix` =
`--chaos --preemption-timeout=disabled --base-env=minimal`, **no verify**. Only the
8 `buck_chaos_tests!` match fbsource's chaos flags and add `--verify`, but they are
PMU-gated and skip/fail without hardware perf counters. On a non-PMU host OSS has
effectively no verified chaos-with-preemptions coverage over the matrix.

### Gap 4 — trace-replay / chaos-replay matrix not exercised end-to-end
fbsource tracereplay (43) + tracereplay_chaos (36) + chaosreplay (47) = 126 targets
run the workload matrix through `hermit-verify trace-replay --strip-times` /
`chaos-replay`. OSS folds "replay" into `record_replay.rs` (record→replay round trip
with `record start --verify`) — good, and arguably stronger for record/replay — but
does not exercise the standalone `trace-replay`/`chaos-replay` verification paths
over the matrix. OSS `hermit-verify/tests/cli.rs` only checks the CLI surface (help
lists the subcommands; `schedule diff/inspect/print`), not actual guest trace-replay
verification.

## Secondary flag deltas (OSS matrix marginally less strict)
- OSS default/chaos/verify matrix passes `--allow-passthrough` (unsupported syscalls
  pass through instead of failing); fbsource does not. Fail-closed behavior is tested
  separately but not applied during the matrix.
- OSS passes `--no-virtualize-cpuid` across the matrix; fbsource does not (relaxes
  CPUID virtualization — likely a host/VM accommodation).
- `--base-env`: OSS matrix uses `minimal`; fbsource strict/chaos use `empty` (stricter).
- OSS matrix omits `--isolate-workdir` (uses tempdirs / fresh cwd — functionally OK).

## Where OSS matches or exceeds fbsource
- Default matrix flags match exactly.
- Record/replay is at least as strict and adds coverage fbsource lacks
  (`--record-timeout`, partial-data cleanup, descendant teardown, SIGALRM-blocked
  timeout, explicit success-message assertion).
- rr programs are ported (213 vs 219).
- OSS-only suites with no fbsource buck target: signal / ipc / clock / mmap / random
  / thread_sync / epoll / procfs / fp_reduction determinism, `stress_suite`,
  `arbitrary_binaries`, `leveldb`, `no_silent_skips`.

## Recommendations (to actually reach parity)
1. Add `--verify` to `rr_suite.rs` runs (highest value, closes Gap 1). Guard for the
   handful of rr programs that are legitimately nondeterministic if any.
2. Add a real strict workload matrix: a `RunMode::Strict` (or reuse Verify) that runs
   the stable matrix with `--base-env=empty` and a non-disabled
   `--preemption-timeout` (PMU-gated like `buck_chaos_tests!`). Rename the current
   `strict_mode_matrix` to reflect that it is a fail-closed policy check.
3. Enable `--verify` on `chaos_mode_matrix` (or clearly document it as the
   non-PMU functional-only tier and rely on `buck_chaos_tests!` for verified chaos).
4. Add end-to-end `hermit-verify trace-replay`/`chaos-replay` coverage over a few
   matrix workloads, or document that record→replay round-trip is the intended OSS
   substitute.
