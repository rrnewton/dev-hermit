# demo5 good-vs-broken trace diff — exact scheduler/clock divergence

Task: `demo5-good-vs-broken-trace-diff` (hermit-238). Date: 2026-07-31.
P0 differential trace-debug on the demos-green critical path.

## TL;DR — the divergence point

demo5 boot wedges because the **guest `CLOCK_MONOTONIC` runs ~8.5 s BEHIND the
scheduler's committed virtual time**, which turns the python QEMU-controller's
`clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, now+~100ms)` poll into an
*already-expired* absolute sleep. An expired abs-deadline sleep is **COMMITTED
(run immediately), not blocked**, so the controller busy-polls forever and
**QEMU (dtid 9) is never scheduled again**. QEMU dies after 34 syscalls (still in
ld.so, loading libpixman), never creates the QMP socket, and the controller
times out ~15 s later.

This is the **clock-domain regression (#1095 / clock-domain fix owner)**, not the
a8195cfc ptrace perf regression (that is a separate 10x throughput issue, PR
rrnewton/reverie#305, and is NOT what wedges the boot).

## Traces diffed

Reused the prior bisect's full-`--log INFO` DETLOG captures (no re-run needed;
demo5 boots are load-sensitive). Canonicalized as:

- GOOD  = `ignored/demo5-good-trace.log` -> `ignored/logs/demo5-good-boot-2a7ca98-rerun-1785460783-197-1542507.log`
  - hermit `2a7ca98f` (#1077), exit=0, 75.7 s, 7,443,967 lines, QEMU=112,506 inbound syscalls, boots.
- BROKEN = `ignored/demo5-broken-trace.log` -> `ignored/logs/demo5-bad-boot-aa5258b-rerun600-1785460998-903-1644132.log`
  - hermit `aa5258b6` (#1186 "Pin Reverie deps to landed main tip"), exit=1,
    35.7 s, 347,521 lines, QEMU=34 inbound syscalls, "timed out waiting for
    socket qmp.sock".

Both share byte-for-line-number-identical early trace: python controller execve
at L3880 (dtid 7), QEMU execve at ~L21071/L21114 (dtid 9).

Note: GOOD baseline is `2a7ca98f`, an even-earlier known-good than the task's
named `f6c836b18`; it does not affect the conclusion — the wedge mechanism is
clock-skew-driven and independent of which good SHA is used.

## Exact first divergence

Both runs: QEMU (dtid 9) finishes inbound syscall **#34** `read(3, .., 832) =
Ok(832)` and ends timeslice **T36**. The read COMPLETED — QEMU is runnable, not
blocked.

- **GOOD** (`demo5-good-trace.log:21602`): QEMU immediately proceeds to syscall
  #35 `fstat(3)=Ok(0)` and runs 112,506 syscalls total, reaching
  `socket()/bind()/listen()` (L29770-29835) to create the QMP socket.
- **BROKEN** (`demo5-broken-trace.log:21308`): QEMU (dtid 9) **never appears
  again** in the entire remaining log (0 occurrences after L21308). Only the
  controller (dtid 7) runs, in an infinite poll loop
  (`clock_gettime` / `newfstatat(qmp.sock)=ENOENT` / `wait4(9,WNOHANG)=0` /
  `clock_nanosleep`).

## Mechanism (why QEMU is starved)

The controller polls for the QMP socket with:
`clock_gettime(CLOCK_MONOTONIC)` then
`clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME=1, deadline=now+~100ms)`.

Detcore converts that abs-deadline into a `SleepUntil(LogicalTime(deadline))`
resource. The scheduler blocks the thread only if the deadline is in the FUTURE
relative to committed virtual time; an expired deadline is run immediately.

GOOD (`demo5-good-trace.log`, turns 3604-3606):
```
[dtid 7] clock_gettime(CLOCK_MONOTONIC) -> { tv_sec: 1767225638, tv_nsec: 924625000 }   # 638.924 s
[dtid 7] clock_nanosleep(CLOCK_MONOTONIC, 1, ...)
NONCOMMIT turn 3605, SKIP dettid 7 which wanted resource SleepUntil(LogicalTime(1767225639024625000)) (blocking)  # 639.024 s = FUTURE
COMMIT   turn 3606, dettid 9 using resources {Path("/lib64/libcapstone.so.4"): R} ...    # QEMU runs
```
Guest clock (638.924 s) ~= committed time (638.928 s). Deadline 639.024 s is in
the future -> poller BLOCKS -> QEMU scheduled.

BROKEN (`demo5-broken-trace.log`, turns 3605-3606):
```
[dtid 7] clock_gettime(CLOCK_MONOTONIC) -> { tv_sec: 1767225630, tv_nsec: 250250000 }   # 630.250 s (!!)
COMMIT turn 3605, dettid 7 ... SleepUntil(LogicalTime(0)) ... committed 1_767_225_639.031_375_000s
[dtid 7] clock_nanosleep(CLOCK_MONOTONIC, 1, ...)
COMMIT turn 3606, dettid 7 using resources {SleepUntil(LogicalTime(1767225630500250000)): W}, on previously committed 1_767_225_639.034_375_000s
```
Guest clock (~630.4 s) is **~8.53 s BEHIND** committed time (639.034 s). The
computed deadline 630.500 s is ~8.53 s IN THE PAST -> the abs-sleep is already
expired -> the scheduler **COMMITs** dtid 7 (runs it) instead of skipping it.
The poll loop never yields, the run queue never empties, and QEMU (dtid 9),
runnable since committed 638.862 s, is never selected.

Quantified asymmetry (whole run):
- dtid 7 blocking future-sleep SKIPs: GOOD 60,446  vs  BROKEN **0**.
- dtid 7 `clock_nanosleep` inbound: GOOD 60,448  vs  BROKEN 4,122 (all non-blocking).
- dtid 7 `SleepUntil(LogicalTime(0))` COMMITs: BROKEN 27,599.

## Routing

- **Clock-domain fix owner (agent 220, and per user note 227):** ROOT CAUSE.
  Guest `CLOCK_MONOTONIC` lags committed virtual time by ~8.5 s (the #1095
  split/reset clock domain — fork deep-copies + exec resets guest_clock, so its
  history-erased domain no longer tracks the advancing committed epoch clock).
  Fix so guest-now tracks committed_time -> abs deadlines land in the future ->
  poller blocks -> QEMU runs. See
  [[pr1095-fake-determinism-clock-review-lesson]].
- **Scheduler foundation (vtime-jump):** expired-abs-deadline pollers behave as
  unproductive pollers that keep the run queue non-empty and starve runnable
  peers; see [[scheduler-vtime-jump-unproductive-pollers]]. Secondary hardening.
- **Perf fix owner (agent 238, me):** the a8195cfc 10x ptrace slowdown
  (rrnewton/reverie#305) is a SEPARATE throughput regression and does NOT cause
  this boot wedge. Do not conflate.

## Artifacts

- `ignored/demo5-good-trace.log`, `ignored/demo5-broken-trace.log` (symlinks).
- Divergence anchors: broken L21308 (QEMU last syscall), L21369 + turn 3606
  (past-deadline clock_nanosleep); good L21602 (QEMU continues), turns 3605-3606
  (blocking SKIP then QEMU commit).
