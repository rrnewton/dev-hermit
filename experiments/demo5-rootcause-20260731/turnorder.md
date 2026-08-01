# Demo 05 turn order at the HPET wedge

**Task:** `demo5-turnorder-thread-analysis` (feeds `demo5-rigorous-rootcause`,
hermit-226)  
**Date:** 2026-07-31  
**Host/backend:** devbig014, ptrace  
**Verdict:** the terminal QEMU actors are dettids **5, 11, and 13**. Dettid 13,
not dettid 7, is the vCPU/TCG thread. Enabling the PMU backstop forces dettid 13
to check in and materially changes the turn interleaving, but no captured trace
contains evidence of a fatal PMU skid. The PMU-enabled/no-RCB-clock control still
wedges at HPET.

## Inputs and scope

No new boot was run for this analysis. It reads the existing private captures:

| cell | Hermit flags after `run --strict` | outcome | INFO log |
|---|---|---|---|
| C1, no PMU | `--no-rcb-time --target-timeslice 100000 --max-timeslice disabled` | reaches the BusyBox shell, but the full controller workflow times out (124) | `ignored/h/a/wedge-nrt/.work/boot-manual/hermit-info.log` |
| C3, PMU-only isolation | `--no-rcb-time --target-timeslice 100000 --max-timeslice 200000000` | freezes at `hpet0` (guest 0.724403 s), then times out | `ignored/h/a/wedge-nrt/.work/boot-c3-preempt-only/hermit-info.log` |

Both keep `no_rcb_time=true`, so the difference is the finite 200 ms PMU
maximum, not RCB-derived virtual-time accounting. The configurations are sourced
from the seven notes on task `demo5-fix-detcore-deadlineless`; the captures used
Hermit `0ca0dec2`. These two runs were not load-matched, so differences below are
direct observations, not a boot-success causality claim.

Thread identity is cross-checked against the committed native QEMU strace in
[`README-native-qemu-strace-baseline.md`](README-native-qemu-strace-baseline.md).
That baseline maps QEMU's syscall fingerprints as follows:

- main/BQL event loop: `ppoll` + eventfd `read` + `futex`;
- vCPU/TCG: `write`/`writev` + eventfd + `futex`;
- RCU: `clock_nanosleep` + `madvise` + a small amount of `futex`.

## Correct dettid map

The controller (dettid 3) clones QEMU as dettid 5. QEMU dettid 5 then creates
7, 9, 11, and 13, in that order. The trace fingerprints give this map:

| dettid | identity | C3 turns / syscalls | direct trace fingerprint |
|---:|---|---:|---|
| 3 | Python controller | 241,723 / 188,268 | `clock_gettime`, serial-log `read`/`lseek`/`newfstatat` |
| 5 | QEMU process leader, main/BQL event loop | 175,601 / 88,806 | `futex` 85,660; `ppoll` 194; QEMU `execve` occurs on this dettid |
| 7 | QEMU RCU/maintenance thread | 4,059 / 2,598 | `futex` 1,338; `madvise` 1,164; `clock_nanosleep` 83 |
| 9 | short-lived block-image worker | 39 / 33 | `pread64` + eventfd `write`, then `exit(0)` |
| 11 | QEMU socket/fd poller | 238,652 / 221,144 | `clock_gettime` 189,541; `poll` 15,795; `read` 15,795 |
| 13 | QEMU vCPU/TCG execution thread | 330,465 / 329,873 | `write` 158,660; `futex` 87,136; `gettimeofday` 65,652; `writev` 17,869 |

### Correction to the earlier vCPU hypothesis

Earlier task notes called dettid 7 the vCPU because it waits on futex word
`0x5555570f6708`. That identification is wrong:

1. In the native trace, QEMU's first cloned thread has the same
   `clock_nanosleep`/`madvise`/`futex` signature and is independently identified
   as RCU. That is dettid 7 in these Hermit traces.
2. Dettid 13 has the native vCPU's console/eventfd signature.
3. With the PMU maximum enabled, **460 of the 467** zero-syscall forced
   timeslice ends occur on dettid 13. Long syscall-free guest execution is what
   should reach the PMU backstop.

Thus dettid 7's final indefinite wait is an RCU-helper wait, not proof that the
vCPU was parked. The vCPU dettid 13 remains active through C3 turn 990,537.

## Exact terminal turn picture

C3 records 990,539 scheduler turns. Dettid 7's last turn is 169,487, when it
enters:

```text
COMMIT turn 169487, dettid 7 ...
[dtid 7] inbound syscall: futex(0x5555570f6708, 0, -1, NULL, NULL, 0) = ?
```

It never returns during the remaining 821,051 turn IDs (82.9% of the trace).
That leaves the controller and the three productive QEMU actors:

| dettid | committed turns among final 100,000 turn IDs | runs of consecutive commits | mean / maximum run |
|---:|---:|---:|---:|
| 13, vCPU/TCG | 30,286 | 30,282 | 1.00 / 4 |
| 11, socket poller | 30,302 | 30,287 | 1.00 / 16 |
| 5, QEMU main/BQL | 20,237 | 20,203 | 1.00 / 15 |
| 3, controller | 16,778 | 16,778 | 1.00 / 1 |

The missing turn IDs are `NONCOMMIT` skips. This is effectively a one-commit
rotation, not runnable-thread starvation. In that window:

- dettid 13 continues the vCPU/eventfd loop (`writev`, `write`, `futex`);
- dettid 11 continues `poll`/`read` socket handling;
- dettid 5 alternates BQL `FutexWait` completions and immediate work;
- dettid 3 tails the serial log and performs future absolute sleeps.

There are 83,078 immediate `SleepUntil(LogicalTime(0))` commits, 2,397 commits
whose resource carries a nonzero absolute sleep target, and 2,396 actual
`NONCOMMIT ... SleepUntil(...) (blocking)` skips, all belonging to controller
dettid 3 (plus one internal-I/O polling skip). The QEMU 5/11/13 trio exposes no
future timed wait at the host layer; its guest HPET deadline exists only inside
QEMU's `-icount` clock.

For comparison, C1's final 100,000 turn IDs contain only the QEMU trio after the
controller has gone quiet:

| dettid | commits | mean / maximum consecutive run |
|---:|---:|---:|
| 5 | 43,215 | 3.51 / 12 |
| 11 | 28,522 | 4.78 / 16 |
| 13 | 23,984 | 2.25 / 121 |

All 82,404 sleep-resource commits in that window are
`SleepUntil(LogicalTime(0))`; the other 13,317 commits use non-sleep resources.
There are zero future-sleep blocking skips and 4,279 internal-I/O polling skips.
C1 and C3 are at different workflow phases, so this table describes—not
causally attributes—the changed ordering.

## What the PMU and skid do

Hermit's source separates three effects:

1. `use_rcb_time()` is false when either `--no-rcb-time` is set or the maximum
   is disabled (`detcore-model/src/config.rs:631-637`). Both C1 and C3 therefore
   use scheduler-turn virtual-time accounting.
2. A finite maximum installs `max_timeslice_end`, converts the remaining logical
   duration to RCBs, and arms a precise PMU timer
   (`detcore/src/lib.rs:535-638`). With `--max-timeslice disabled`,
   `next_timeslice` clears the maximum and timer state
   (`detcore/src/tool_local.rs:2089-2095`).
3. Reverie's precise timer requests delivery early by the configured skid
   margin, then single-steps to the exact RCB target
   (`reverie-ptrace/src/timer.rs:634-665,800-825`). If delivery has already
   overshot the target, it asserts with `Clock perf counter exceeds target ...
   Consider increasing skid margin` rather than silently choosing another
   schedule.

Operationally, C3 contains 467 zero-syscall timeslice ends:

```text
dettid 13 (vCPU/TCG)  460
dettid 5                4
dettid 3                3
```

C1 has only seven startup cases (dettid 5 = 4, dettid 3 = 3) and **none on
dettid 13**. This is the direct witness that the PMU maximum interrupts long
TCG bursts and returns the vCPU to the deterministic scheduler. It changes the
interleaving from multi-commit chunks to the near one-commit rotation shown
above. It does not, by itself, make C3 boot: the guest remains at HPET and C3 is
eventually terminated.

No examined demo5 trace contains any of these fatal-skid/overshoot markers:

```text
Clock perf counter exceeds target
Consider increasing skid margin
rcb timer overshot / rcb overshoot
panicked at ... timer.rs
```

This zero-match result covers C1, C3, the RCB-on `off-run3` and `on-run3`
controls, and the controlled socket/no-socket pair. INFO logs do not expose the
size of an ordinary within-margin hardware skid, so they cannot prove physical
delivery had zero skid. They do show that any ordinary skid was corrected by
the precise-timer path and that **no beyond-margin skid panic is evidence for
this wedge**. The schedule-visible effect is the forced PMU check-in, not an
observed skid failure.

## Conclusion for hermit-226

- The HPET terminal loop is not “11/13/5 while the vCPU dettid 7 is starved.”
  It is **11/13/5 with vCPU dettid 13 still running**, plus controller dettid 3
  in C3. Dettid 7 is RCU and is legitimately blocked.
- The extra socket poller is dettid 11. It consumes roughly the same terminal
  committed-turn share as the vCPU and perturbs the main/BQL/vCPU handshake.
- PMU preemption demonstrably checks the vCPU in (460 times) and reshapes turn
  order, but the PMU-only cell still wedges. This rules out “the vCPU merely
  needed a PMU preemption deadline” as a sufficient explanation.
- There is no positive PMU-skid failure witness. Do not label the captured wedge
  a PMU-skid bug without a `timer.rs` overshoot/panic line.
- The evidence supports a deadline-less QEMU-host-wait topology (QEMU has no
  future host timer for guest HPET) whose exact entry is sensitive to the
  controller/socket interleaving. It does **not** support runnable-vCPU
  starvation or a fatal skid as the terminal mechanism.

## Reproduction of the counts

The large raw logs stay ignored. The analysis used only streaming `rg`/`awk`
queries, for example:

```bash
# Per-dettid scheduler activity and syscall fingerprints.
rust-script experiments/demo5-rootcause-20260731/log-science/dtid_activity.rs \
  < ignored/h/a/wedge-nrt/.work/boot-c3-preempt-only/hermit-info.log

# Forced, zero-syscall check-ins by dettid.
rg 'ending timeslice.*0 syscalls and 0 signals' \
  ignored/h/a/wedge-nrt/.work/boot-c3-preempt-only/hermit-info.log \
  | sed -nE 's/.*dtid ([0-9]+).*/\1/p' | sort | uniq -c | sort -nr

# Fatal skid witnesses (expected: no matches).
rg -i 'clock perf counter exceeds target|consider increasing skid margin|\
rcb timer overshot|rcb overshoot|panicked at .*timer.rs' \
  ignored/h/a/wedge-nrt/.work/boot-{manual,c3-preempt-only}/hermit-info.log
```
