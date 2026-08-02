# H6: Why the deadline-less (no future timed_waiter) state arises — native QEMU reference

Follow-up to `README-native-qemu-strace-baseline.md`, answering the lead's H6
question (`demo5-rigorous-rootcause` ledger): *is the demo5 wedge
controller-specific, or a general QEMU-under-`--no-rcb-time` gap?* Characterize
the HPET/clocksource-calibration phase in the **native** (no-Hermit) boot and
determine whether native QEMU issues any **host-level future timed_waiter** that
Hermit's scheduler could jump its `committed_time` to.

Same trace as the baseline: `/var/tmp/demo5-native-strace-tt.txt` (84 MB,
`strace -f -tt -T`, self-terminating demo5 image, host devbig014, qemu 10.1.2,
kernel 6.17.13). This boot **succeeds** natively — it is the reference for the
phase where Hermit wedges.

## The calibration phase (guest timeline)

From `native-selfterm-console.log`:

```
[0.000000] tsc: Fast TSC calibration using PIT
[0.000000] tsc: Detected 1000.012 MHz processor
[0.069684] clocksource: hpet: mask ... max_idle_ns: 19112604467 ns
[0.072708] clocksource: tsc-early: ...
[0.716740] hpet0: at MMIO 0xfed00000, IRQs 2, 8, 0      <-- where Hermit wedges
[0.716762] hpet0: 3 comparators, 64-bit 100.000000 MHz counter
[0.719855] clocksource: Switched to clocksource tsc-early
[0.733912] clocksource: acpi_pm: ...
```

So the timer/clocksource bring-up is guest **[0.0]–[0.73]**. The guest needs its
timer sources (PIT→HPET→TSC) to *appear to advance* to complete calibration and
switch clocksource.

## Decisive finding: native QEMU issues ZERO host-visible future timed_waiters

Counts over the **entire** boot+shutdown trace:

| host timed-wait mechanism                         | count |
|---------------------------------------------------|-------|
| `timerfd_create` / `timerfd_settime`              | **0** |
| `timer_create` / `timer_settime` / `setitimer`    | **0** |
| `epoll_wait` / `epoll_pwait`                       | **0** |
| `select` / `pselect6`                             | **0** |
| plain `nanosleep`                                 | **0** |
| `ppoll` with a finite non-zero timeout            | **0** |
| `FUTEX_WAIT*` with a finite timespec              | **0** |

What QEMU's vCPU + main threads actually block on:

| wait                                    | count   | meaning |
|-----------------------------------------|---------|---------|
| `ppoll(..., NULL, ...)`                 | 61,816  | wait **forever** for an fd (eventfd kick) |
| `ppoll(..., {tv_sec=0,tv_nsec=0}, ...)` | 2       | non-blocking probe |
| `FUTEX_WAIT*(..., NULL)`                | 148,058 | wait **forever** for a wake (BQL) |

And the **only** host timed-wait of any kind in the whole boot: the QEMU **RCU
thread** (tid 2241988) issues **36 `clock_nanosleep(CLOCK_REALTIME, 0 /*rel*/,
{0,10000000})`** — a fixed **10 ms relative** call-RCU grace-period poll. It is
independent of the guest clock, is relative (not an absolute guest-derived
deadline), and belongs to memory reclaim, not timekeeping. The vCPU thread
(2241990) **never** blocks on a finite host wait — 0 occurrences across the boot.

Concrete calibration-window slice (host 20:01:30, guest early kernel boot):

```
2241990 futex(..., FUTEX_WAKE_PRIVATE, INT_MAX) = 0     # vCPU makes progress, kicks
2241988 clock_nanosleep(CLOCK_REALTIME, 0, {0,10000000})# RCU 10ms grace poll
2241990 futex(..., FUTEX_WAKE_PRIVATE, INT_MAX) = 0
...
```

Window syscall mix: madvise (RCU reclaim) 103, futex 40, write 15, read 11,
ppoll 11, clock_nanosleep 6 — no timerfd, no finite ppoll/futex timeout.

## Why this explains the deadline-less wedge (answer to H6)

Under `-icount shift=0,sleep=off`, **QEMU's guest timers are virtual**: HPET/PIT/
TSC/PM-timer deadlines are expressed in the instruction-count (icount) virtual
clock and are serviced when the **vCPU thread executes far enough** — QEMU never
translates a guest timer deadline into a host `timerfd`, a finite host `ppoll`/
`futex` timeout, or a real-time sleep. With `sleep=off` it does not even
real-time-sleep on guest idle; it fast-forwards the virtual clock. The guest
clock advances **only** because the vCPU keeps retiring instructions.

Natively this closes fine: the vCPU thread advances icount → the virtual HPET
ticks → an eventfd kick wakes the main loop's `ppoll(NULL)` → boot proceeds. The
`ppoll(NULL)`/`futex(NULL)` are infinite waits that are *always* released by the
counterpart thread's forward progress.

Under Hermit `--no-rcb-time`, RCBs do **not** advance virtual time and no
timeslice deadline is installed, so the vCPU's forward progress no longer moves
`committed_time`. The scheduler's step2d vtime-jump can only advance
`committed_time` to a **future timed_waiter** — but this trace proves there is
**no host-visible future timed_waiter to jump to**: every QEMU wait is
`ppoll(NULL)`/`FUTEX_WAIT(NULL)` (infinite), and the sole host timer is the RCU
thread's guest-independent 10 ms relative poll. So `committed_time` has nothing
to advance to; the guest's virtual HPET never appears to tick; clocksource
calibration stalls exactly at `hpet0`. This is the host-syscall-level witness for
[[scheduler-vtime-jump-unproductive-pollers]] ("demo5 has NO future
timed_waiter → committed RACES AHEAD of guest -icount") and
[[demo5-wedge-clock-skew-past-deadline-poller]].

**H6 verdict: GENERAL, not controller-specific.** The deadline-less state is
intrinsic to how `-icount` maps guest timers to host syscalls — it maps them to
*nothing* at the host layer. It is present in a plain busybox boot with **no QMP
controller at all**. The qmp.sock controller-poll is a downstream victim (it,
too, waits on a deadline that never arrives), not the cause. Any QEMU `-icount`
guest under `--no-rcb-time` hits this; a QMP controller is not required to
reproduce the deadline-less wedge.

### Corollaries for the fix (evidence, not a fix proposal)

- A scheduler that advances `committed_time` on **vCPU RCB progress** (i.e. NOT
  `--no-rcb-time`) closes the loop the native way — the vCPU's instruction
  retirement is the real clock, and every native wait is released by that
  progress. cf. [[demo5-icount-sleep-on-neutral-under-strict]]: under `--strict`
  (RCB time on) demo5 crawls to boot rather than hard-livelocking.
- There is no host timerfd/absolute deadline to synthesize a `timed_waiter`
  from; the only guest-time signal available at the host layer is vCPU forward
  progress (RCB). So "jump committed_time to the next host deadline" cannot fix
  this class — there is no such deadline.

## Reproduce the H6 counts

```bash
RAW=/var/tmp/demo5-native-strace-tt.txt   # from the baseline strace -f -tt -T
grep -cE 'timerfd_|timer_create|setitimer|epoll_wait|pselect6| nanosleep\(' "$RAW"   # 0
grep -E ' ppoll\(' "$RAW" | grep -c 'NULL, NULL,'                 # infinite ppolls
grep -E ' ppoll\(' "$RAW" | grep -E '\{tv_sec=[1-9]|tv_nsec=[1-9]' | grep -vc '{tv_sec=0, tv_nsec=0}'  # 0 finite
grep -E 'futex\(.*FUTEX_WAIT' "$RAW" | grep -cE '\{tv_sec='       # 0 finite futex waits
grep -E 'clock_nanosleep\(' "$RAW" | grep -v resumed | head       # 36 RCU 10ms rel polls
```
