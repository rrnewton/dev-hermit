# fbsource → OSS Hermit Test-Suite Port Audit

- **Date:** 2026-07-27
- **Task:** `impl-tests-fbsource-port-audit` (owner: hermit-275)
- **Question (user):** "Not everything is ported from fbsource" — verify the
  `hermetic_infra/hermit` test suite is fully present in the OSS/dev-hermit tree,
  and confirm Jason White's individual-syscall `#[test]` infrastructure ported.
- **Verdict:** **No gap. The suspicion is not borne out.** Every substantive test
  in fbsource is present in OSS, and OSS/dev-hermit is a **strict superset** —
  substantially *ahead* of the fbsource import (which is an older one-way copy).
  The only files unique to fbsource are Meta-internal Buck build files
  (`BUCK` / `PACKAGE` / `TARGETS` / `build_defs.bzl`), which intentionally do not
  port to OSS (OSS uses Cargo + `tests/helpers.bzl`).

## Trees compared

| role | path |
|---|---|
| fbsource (import, reference) | `$HOME/fbsource/fbcode/hermetic_infra/hermit` (+ `../reverie`) |
| OSS / dev-hermit (active dev) | `~/work/dev-hermit/hermit` (+ `~/work/dev-hermit/reverie`) |

Direction note: fbsource is a copy of `fbcode/hermetic_infra/hermit` (see the
`fbsource-import-lint-deltas` finding); active development happens on the OSS
fork `rrnewton/hermit`, so OSS leads fbsource.

## Quantitative gap table

| test location | fbsource | OSS/dev-hermit | delta |
|---|---:|---:|---:|
| `tests/c/*.c` (C determinism tests)     | 45  | 148 | **+103** |
| `hermit-cli/tests` `#[test]` fns        | 185 | 316 | **+131** |
| `detcore/tests` `#[test]` fns           | 53  | 59  | +6 |
| `detcore/src` `#[test]` fns (unit)      | 83  | 245 | **+162** |
| reverie **whole-crate** `#[test]` fns   | 310 | 539 | **+229** |
| `reverie/tests/*.rs` top-level `#[test]`| 59  | 59  | **0 (identical)** |

Every count is **≥** fbsource; most are far larger. The deltas are net-new
frontier work landed in OSS (syscall-classification `*_enosys.c` probes,
`meminfo_*`, `name_to_handle_*`, `netlink_*`, backend-parity, etc.).

## Set-difference results (files unique to fbsource)

Computed with `comm -23` on relative paths for every parallel test location
(`tests`, `detcore/tests`, `hermit-cli/tests`, `hermit-verify/tests`, and
reverie `tests` + `reverie-*/tests`). After excluding build files:

- `hermit/tests/`            → only-in-fbsource: **none** (9 raw = all `BUCK`/`PACKAGE`)
- `hermit/detcore/tests/`    → only-in-fbsource: **none** (2 raw = `lit/BUCK`, `lit/build_defs.bzl`)
- `hermit/hermit-cli/tests/` → only-in-fbsource: **none**
- `hermit/hermit-verify/tests/` → only-in-fbsource: **none**
- reverie `tests` + `reverie-*/tests` (whole crate) → only-in-fbsource: **none**
- `hermit/tests/` subdirectories only-in-fbsource: only `BUCK`. OSS additionally
  has `backend-parity/`, `compat/`, `qemu-boot/`, `shared-futex-verify/` that
  fbsource lacks.
- Every fbsource `tests/c/*.c` file is present in OSS (missing-C set: **empty**).

## Jason White's individual-syscall `#[test]` infrastructure

This is the **reverie integration-test suite** at `reverie/tests/*.rs` — one file
per subsystem, `#[test]` functions that spawn a guest under a Reverie tool and
assert per-syscall / per-instruction behavior. Files (all present and identical
in both trees, 59 `#[test]` across 20 files):

`basics.rs` (noop/counter tool, execve, fd inheritance, segfault, orphans),
`signal.rs`, `signalfd.rs`, `delay_signal.rs`, `stat.rs`, `vfork.rs`,
`thread_start.rs`, `timer_semantics.rs`, `cpuid.rs`, `rdtsc.rs`, `vdso.rs`,
`busywait.rs`, `spinlock.rs`, `stack.rs`, `backtrace.rs`, `exit.rs`,
`parallelism.rs`, `state.rs`, `suppression.rs`, `convert.rs`, plus
`tests/standalone/` (`at_random.rs`, `inject_then_tail_inject.rs`,
`parallel_tasks.rs`), `tests/c_tests/` C guests, and per-backend harnesses
(`reverie-kvm/tests/{static_elf,strace,vmcall}.rs`,
`reverie-liteinst/tests/strace.rs`, `reverie-dbi/tests/`).

**Status: FULLY PORTED** — identical file set; top-level `#[test]` count 59 == 59;
whole-crate reverie `#[test]` total is *higher* in OSS (539 vs 310) from added
KVM/DBI/liteinst backend tests.

## Method / reproduction

```bash
FB=$HOME/fbsource/fbcode/hermetic_infra/hermit
OSS=~/work/dev-hermit/hermit
# per-location set difference (files only in fbsource):
comm -23 <(cd $FB/tests && find . -type f|sort) <(cd $OSS/tests && find . -type f|sort) \
  | grep -vE '/(BUCK|PACKAGE|TARGETS)$|\.bzl$'
# #[test] parity:
grep -rc '#\[test\]' $FB/hermit-cli/tests | awk -F: '{s+=$2}END{print s}'
grep -rc '#\[test\]' $OSS/hermit-cli/tests | awk -F: '{s+=$2}END{print s}'
# reverie whole-crate:
grep -rc '#\[test\]' <reverie-root> --include='*.rs' | awk -F: '{s+=$2}END{print s}'
```

## Conclusion & recommendation

- **No porting action required.** All substantive fbsource tests (C determinism
  tests, hermit-cli integration tests, detcore unit + lit tests, and Jason
  White's reverie syscall `#[test]` suite) are present in OSS/dev-hermit, which
  is a strict superset.
- The only fbsource-unique artifacts are Meta-internal Buck files, which are
  correctly excluded from the OSS Cargo-based build.
- If the intent is the reverse direction (OSS → fbsource sync), that is a
  separate export task; this audit found **131 hermit-cli, 103 C, and 229
  reverie** `#[test]`/test files that exist in OSS but not in the current
  fbsource import — i.e., fbsource is stale relative to OSS.
