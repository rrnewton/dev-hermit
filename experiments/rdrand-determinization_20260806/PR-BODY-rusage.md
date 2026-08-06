[impl agent, claude-opus-5]

Implements Part-B **Stack 2.1** (`rusage-resource-accounting-determinism`, #140).

## Summary

`getrusage(2)` reported `ru_utime` and `ru_stime` as **exactly zero** while
`times(2)` — reading Detcore's per-process logical CPU accounting — reported a
**nonzero, advancing** value in the same run. Two projections of one virtual
timeline contradicted each other, and a guest computing `utilization =
utime / elapsed` got a deterministic, confidently wrong zero.

Measured on `main` before this change (ptrace, `--strict`, guest samples
`getrusage` and `times` either side of a 30M-iteration loop):

```
BEFORE rusage utime=0.000000   BEFORE times utime=0
AFTER  rusage utime=0.000000   AFTER  times utime=30      <- 0.30s, from the same clock
```

Zero was never required for determinism. The logical accounting is already
deterministic, continuous, and fine-grained — which is why `times(2)` was
allowed to use it — and `ru_maxrss`, in the same struct, was already
deterministic, evolving, and correct. Zeroing these fields was the "stable
because it is a constant" anti-pattern that #140 names, not a determinism
measure.

After:

```
AFTER  rusage utime=0.300480   AFTER  times utime=30      <- agree
```

## What changed

`ru_utime`/`ru_stime` now come from the same `ProcessCpuTime` snapshot
`handle_times` uses, so the two syscalls cannot disagree. Each `who` reports the
scope Linux specifies, from accounting Detcore already maintains:

| `who` | source |
| --- | --- |
| `RUSAGE_SELF` | process totals (`process.user` / `process.system`) |
| `RUSAGE_THREAD` | the calling thread's own `thread_logical_time` |
| `RUSAGE_CHILDREN` | `children_user` / `children_system` — already reaped-only, matching Linux |

Conversion keeps nanosecond source resolution and truncates only to `timeval`'s
microsecond field, so `getrusage` stays **finer-grained** than the 100 Hz
`clock_t` grid `times(2)` is obliged to use — the continuity #140 requires.

## Determinism

- The value is a pure function of `LogicalTime`, which is derived from retired
  conditional branches and intercepted-syscall counts — quantities Detcore
  already schedules deterministically. No host clock, no `/proc` CPU field, and
  no wall time is read.
- It is the **same source** `times(2)` already used, so this removes a
  contradiction rather than adding a second timeline. The new unit test
  `rusage_and_times_report_the_same_cpu_duration` pins that invariant: across a
  range of durations the two projections must agree to within one clock tick,
  and `getrusage` must never report *less* CPU than `times`.
- `process_cpu_time()` folds the calling thread's outstanding delta into the
  shared process accounting before snapshotting, so the value includes work done
  since the last accounting point rather than lagging it — the same call
  `handle_times` makes, at the same point in the handler.
- **Not** a constant and not frozen: measured 0.000475 → 0.300484 across the
  guest's loop, and it tracks the work done.

**Measured determinism, stated precisely:** N=6 separate `--strict` invocations
of the same guest against one hermit build gave byte-identical
`utime=0.300480 stime=0.000845`, and `--strict --verify` passes (rc=0).
**Caveat worth stating rather than hiding:** the value differs slightly across
*different hermit builds* (I observed `0.300484` on one build and `0.300480` on
another), because RCB attribution depends on the tracer's own interception
pattern. That is the same property virtual time already has, and it is why the
unit tests assert the *conversion* and the *cross-syscall invariant* rather than
hardcoding a golden runtime value — a CI test must not pin `0.300480`.

## Linux Semantics

`ru_utime`/`ru_stime` are `timeval`s of CPU consumed, which is what a guest
expects; they now advance monotonically with executed work, as on Linux.
`RUSAGE_CHILDREN` continues to aggregate reaped children only, matching Linux's
rule that a still-running child contributes nothing. `RUSAGE_THREAD` reports the
calling thread alone rather than the process, which is the distinction Linux
draws and which the previous all-zero behaviour erased. `ru_maxrss` behaviour is
unchanged. Invalid `who` still returns `EINVAL`.

Page-fault and context-switch counters (`ru_minflt`, `ru_majflt`, `ru_nvcsw`,
`ru_nivcsw`) are **deliberately left at zero**. They need event counts Detcore
mediates but does not yet aggregate per process; that is separate work. The
docstring now says so explicitly instead of implying the whole struct is
modelled — zero there is still a wrong-but-stable answer, and it should not be
mistaken for a completed field.

## Validation

**Head:** `27757cd23b9051f2836a1e74c52833bfb39bf719`
**Base:** `origin/main` `4c70658e785834737cbe1524f77330c781a6f5ea` (0 behind, 1 ahead)
**Backend:** ptrace · **Log level:** default · **Relaxations:** none

| Check | Result |
| --- | --- |
| `getrusage` CPU now advances | `utime 0.000475 → 0.300480`, `stime 0.000842 → 0.000845` (was `0.000000 → 0.000000`) |
| Agrees with `times(2)` | rusage `0.300480 s` vs times `30` ticks = `0.30 s` ✓ |
| Finer-grained than `times` | rusage resolves `0.000475 s`, below one 10 ms tick |
| Determinism, N=6 separate invocations | 6/6 byte-identical |
| `hermit run --strict --verify` | rc=0 |
| Regression on real guests | `/bin/echo`, `wc` — rc=0 |
| `cargo test -p hermit-detcore --lib` | **388 passed, 0 failed** (2 new tests) |
| `cargo fmt --all -- --check`, `cargo clippy -p hermit-detcore --all-targets` | clean |

**Premise correction worth recording.** The originating sweep
(`experiments/rusage_determinism_20260806`) also reported `times()` frozen and
`tms_utime` zeroed. That part is an artifact of its `--max-timeslice=disabled`
flag: under **default** flags `times()` advances correctly (`ret 12000 → 12030`,
`tms_utime 0 → 30`). The unconditional defect — reproducing under both flag sets
— was `getrusage` alone, which is what this PR fixes. The sweep's other
recommendations (fault/context-switch counters, `/proc/self/stat` fields 14–17,
cross-backend `times()` reconciliation) remain open.

**Not claimed.** ptrace only — DBI/SaBRe/KVM not re-measured at this head. KVM is
untestable on this box (livelocks at guest startup). No validate receipt: see
below.

## Blocker

**No validate receipt.** `ci-hub validate-run` refuses at admission —
`preflight_validate.py` shells out to `with-proxy git fetch`, which is 403 from
an agent shell; the only working egress is `herdr-run`, whose allowlist
(`cargo, gh, git`) refuses `ci-hub`. This blocks every stack in the serial
landing plan, not just this one. Admission predicate computed locally:
moving-base PASS (head descends from fresh `origin/main`), fixed-floor PASS
(anchored past `bfb0a9ef`).
