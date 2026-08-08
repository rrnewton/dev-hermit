[impl agent, opus-5] detcore: derive getrusage CPU times from logical time, not zero

Stack 2.1 of the Part-B topic plan (`tg coalesce-staged-work-into-topic-prs`, PART B).

## Summary

`getrusage(2)` reported `ru_utime` and `ru_stime` as exactly **zero**. That is the "return 3"
anti-pattern of #140: deterministic, but wrong — and it contradicted Detcore's own virtual clock.

The sharpest symptom, from `experiments/rusage_determinism_20260806/`, in a single ptrace run:

```
uptime-1: 123.00 0.00   times-1: 0 0 0 0
   ...300,000-iteration shell loop...
uptime-2: 130.00 0.00   times-2: 0 0 0 0
```

Seven virtual seconds elapse and the process's own CPU accounting records none of them. A guest
computing `utilization = utime / elapsed` gets `0 / 7` — deterministically, confidently wrong.

`times(2)` **already** derives `tms_utime`/`tms_stime` from the per-process logical CPU accounting
(`ProcessCpuSnapshot`, fed by `thread_logical_time`). This change makes `getrusage` read that *same*
source, so the two views of one virtual timeline can no longer disagree.

- `RUSAGE_THREAD` → the calling thread's own logical clock
- `RUSAGE_SELF` → the process aggregate
- `RUSAGE_CHILDREN` → totals reaped from exited children

Three distinct sources, as on Linux, rather than one number relabelled.

`ru_maxrss` is unchanged — the sweep identified it as the in-tree template for "deterministic *and*
evolving", and it already behaves correctly.

### Deliberately out of scope

`ru_minflt`, `ru_majflt`, `ru_nvcsw`, `ru_nivcsw` still report zero. They need event counts Detcore
mediates but does not currently accumulate. Guessing them here would trade one wrong-but-stable
answer for another; they belong in a separate change.

## Determinism

**The change removes a wrong constant and replaces it with an existing deterministic quantity. It
introduces no new source of nondeterminism** — no host clock, no host counter, no scheduling- or
timing-dependent read.

Argument from construction, not from test results:

1. The value written is `ProcessCpuSnapshot.user`/`.system` (or the thread's `thread_logical_time`),
   which is **logical time**: it advances only as Detcore accounts guest work it has already
   serialized. It is the same quantity `times(2)` has read since #797, and the same accounting
   `/proc/uptime` derives from.
2. Logical time is a deterministic function of the guest's own event sequence, which Detcore's fixed
   schedule already determines. No host wall-clock, `clock_gettime`, or kernel counter is consulted
   on this path.
3. The conversion `timeval_from_logical` is pure integer arithmetic on nanoseconds
   (`ns → µs → (sec, usec)`), truncating toward zero exactly as the kernel does. Same input, same
   output, on every host.
4. It **strictly reduces** cross-view divergence: previously `getrusage` and `times` could disagree
   about the same process's CPU time (0 vs 58 ticks, measured below). They now cannot, because there
   is one source.

**Why microseconds and not clock ticks.** `times(2)` quantizes to USER_HZ (10 ms); `rusage` carries
microseconds. The conversion deliberately does *not* round-trip through `clock_ticks`, because doing
so would reintroduce the original bug for any guest whose entire run is shorter than one tick — it
would round back to zero. Continuity and fine granularity are the point (#140), and a regression test
pins this.

**Honest limitation, measured not assumed:** `ru_utime` stays at `0.000125` both before *and* after
the guest's 30,000,000-iteration floating-point loop, while `ru_stime` advances
(`0.586500 → 0.588125`). Detcore's logical *user* CPU accounting does not currently advance for pure
computation between syscalls. This change faithfully surfaces the accounting that exists; it does not
add compute-proportional user-time accrual. That deeper gap is real and is not fixed here.

## Linux Semantics

- `who` validation is unchanged: `RUSAGE_SELF`/`RUSAGE_CHILDREN`/`RUSAGE_THREAD` accepted, anything
  else `EINVAL`.
- `RUSAGE_CHILDREN` reports only *terminated, reaped* children, matching Linux.
- `ru_utime`/`ru_stime` are `struct timeval` with `tv_usec` normalized to `[0, 1e6)`.
- Nanosecond→microsecond conversion truncates toward zero, as the kernel's does.
- Zero-valued fault/context-switch counters remain a divergence from Linux, now documented in the
  function's doc comment rather than implied.

## Validation

**No validate receipt — disclosed, not omitted.** No validate can be admitted from an agent sandbox:
`ci-hub/validate/preflight_validate.py::resolve_current_base()` runs `with-proxy git fetch` and raises
`AdmissionError` on non-zero exit, with no offline flag; `herdr-run` refuses `python3`
(`Allowed: cargo, gh, git`). **Do not land without a receipt at
`c5587b10db2a7ab298cf2190e6a5df903908d048`.**

What did run, on `devbig014`, `--features` default, ptrace backend, relaxations
`--no-virtualize-cpuid --max-timeslice=disabled`:

| check | result |
|---|---|
| `cargo build -p hermit-detcore` | clean |
| `cargo build --release -p hermit` | clean |
| `cargo test -p hermit-detcore --lib` | **389 passed, 0 failed** |
| 3 new unit tests (`sysinfo::tests::rusage_*`) | pass |
| `cargo fmt --all -- --check` | clean |
| `cargo clippy -p hermit-detcore --all-targets` | no warnings |

**End-to-end bracket** — same guest as the sweep (`guest_rusage`), this build vs. an unmodified
build of main:

```
BASELINE (main):  BEFORE rusage utime=0.000000 stime=0.000000 | times stime=58
                  AFTER  rusage utime=0.000000 stime=0.000000 | times stime=58
FIXED (this PR):  BEFORE rusage utime=0.000125 stime=0.586500 | times stime=58
                  AFTER  rusage utime=0.000125 stime=0.588125 | times stime=58
```

The cross-check that matters: `0.586500 s` = 58.65 USER_HZ ticks, which truncates to the **58** that
`times()` reports. Before this change the two views disagreed (0 vs 58); now they agree.
`ru_stime` also *advances* across the loop, and `ru_maxrss` still evolves `1564 → 1692` as before.

**Determinism check:** byte-identical output across two separate `hermit run` invocations.

**Assurance level: L0 + L1** (ptrace). Not L2 — no `--verify-strict` run and no receipt.
KVM untested (livelocks at guest startup on this host, a pre-existing host limitation).

## Human Review Required

Not applied. Checked against the four triggers: (1) no new syscall support — `getrusage` is already
dispatched and classified; (2) no Reverie API or core-abstraction change; (3) **not** a new
determinization strategy — it reuses the existing logical-CPU accounting that `times(2)` already
reads, rather than introducing a new one; (4) no DetCore scheduling change.

Flagging the judgment rather than burying it: a reviewer who reads "CPU accounting now derived from
virtual time" as a *new* determinization strategy should apply `post-facto-human-review` under
trigger 3. My reading is that the strategy already existed and `getrusage` was simply not wired to
it — the same accounting, one more consumer.

Base `4c70658e785834737cbe1524f77330c781a6f5ea` · head `c5587b10db2a7ab298cf2190e6a5df903908d048`
