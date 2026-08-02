# Detcore global (committed) vs local (per-thread) virtual-time audit

- **Task:** `audit-detcore-global-vs-local-vtime` (P1)
- **Date:** 2026-07-31
- **Auditor:** opus-4.8 (impl/audit agent)
- **Ground:** hermit `main` @ HEAD `0ca0dec2` (all line numbers at this SHA)
- **Method:** source read + `git log -S`/`git blame` + focused unit test
  (`cargo test -p detcore --lib guest_clock` = 4/4 PASS). Every claim is bound
  to a file:line or a commit SHA, per the "validate, don't assert" directive.

## 1. The two clocks and how they relate

| | LOCAL (per-thread) | GLOBAL (committed) |
|---|---|---|
| Type | `DetTime` | `GlobalTime` |
| Where | `ThreadState.thread_logical_time` (`detcore/src/tool_local.rs:1266`) | `GlobalState.global_time` (`detcore-model/src/time.rs:706`) |
| Value | own retired work only: `starting_micros*1000 + extra_nanos + syscall_nanos*mult + rcb_nanos*mult + nondet*25*mult` (`time.rs:603+`) | `total == sum_up() == starting_nanos + Σ(all threads' time_vector) + extra_time` (`time.rs:772-788`) |
| Guest read (default) | — | `as_nanos()` via `GlobalTimeLowerBound` (`tool_global.rs:900-902`) |

`Scheduler.committed_time` (`scheduler.rs:307`) mirrors `GlobalTime.as_nanos()`
each turn (`scheduler.rs:2538-2560`) and is **the** clock used to judge absolute
deadlines (`pop_if_before` `scheduler.rs:1657`; `target<=committed_time`
`scheduler.rs:2207`).

Guest-visible reads: `guest_clock_time` (`detcore/src/syscalls/time.rs:96-107`).
Default `use_thread_local_clock_reads=false` (`config.rs:66`) →
`raw = global_time.as_nanos()` (the GLOBAL total). The `true` path (SaBRe,
PR-845) reads the LOCAL clock. Both pass through `GuestClock::observe` =
`now = max(now, raw)` monotonic floor (`tool_local.rs:1174-1179`), shared
`Arc` across the process tree (#1190).

## 2. The local↔scheduler invariant — exists, but targets the OWN slice

Owner's recollection: *interaction with the scheduler bumps the thread's local
time forward to the global time.* **Verdict: the bump exists, but its target is
`threads_time(dtid)` (the thread's own vector-clock slice), never `as_nanos()`
(the total).**

Round-trip per RPC (`send_and_update_time` `tool_global.rs:2054-2085`):
1. local→global: `update_global_time(dtid, time_from_guest)` writes the thread's
   own `time_vector` slice + bumps total (`tool_global.rs:681-685`,
   `time.rs:741-770`).
2. global→local: if the response carries `Some(time)`,
   `thread_logical_time.advance_to(time)` (`tool_global.rs:2078-2083`;
   `advance_to` folds the gap into `extra_nanos`, `time.rs:592-596`).

The payload (`tool_global.rs:1026-1037`):
```
time_from_sched = global_time.threads_time(dtid)   // starting_nanos + time_vector[dtid], OWN slice (time.rs:811-815)
time_update     = if time_from_sched > time_from_guest { Some(time_from_sched) } else { None }
```
On a normal RPC `time_from_sched == time_from_guest` → `None` → **no bump**. A
bump only occurs when the scheduler independently advanced this thread's slice
(sleep/deadline handlers: `update_global_time` at `tool_global.rs:2807/3381/3422`).

**Violation location:** the gap `total − threads_time(dtid) = Σ(other threads)
+ extra_time` is structurally **never** folded into any thread's local clock.
No path bumps a local clock up to the global total. Local threads never "catch
up" to committed; committed is a strict over-estimate that only the *guest-visible
read* (default path) is pinned to, via the #1190 max-floor — not the thread's
own `DetTime`.

## 3. Why committed races ~1000x ahead of guest/local

Root term: **`extra_time`**. `add_scheduler_time()` adds
`NANOS_PER_SCHED*multiplier = 500_000ns * clock_multiplier(=1.0) = 0.5ms` into
`extra_time`→total on **every** scheduler turn that is not Skip / internal /
external-only (`time.rs:98,793-796,804-809`; `scheduler.rs:2515-2537`).
(`NANOS_PER_SCHED` is scaled by `GlobalTime.multiplier` only, **not** by the
500× `DetTime` `additional_multiplier` at `time.rs:513`.)

`extra_time` is in `total` but in **no** thread's `threads_time`. So each turn
moves committed +0.5ms while a busy poller's own clock moves only by its
per-syscall cost (`FAST_NS=250`, `CLOCK_NS=10_000`, `syscall_time.rs:11-13`;
`NANOS_PER_SYSCALL=10_000`, `NANOS_PER_RCB=10`, `time.rs:36,39`). Fast-poll
ratio (default RCB mode) ≈ `500_000/250 ≈ 2000×` — this is the "~1000x."

Amplifier: unproductive polling. `SleepUntil(0)`/`sched_yield` keep `run_queue`
non-empty, so step2d's committed jump (fires only when `run_queue` empty) never
fires; every poll retry is another full turn → another +0.5ms. demo5/QEMU
(`--no-rcb-time`, single-core, controller polling `qmp.sock`) is the canonical
trigger: committed balloons while QEMU `-icount` boot progress lags → absolute
deadlines vs committed expire before the guest progresses → wedge.

So the discrepancy is **not** "local failing to sync" nor "global advancing
without local work" — it is `add_scheduler_time` being a **global-only** term by
construction, magnified by unproductive-poll turn count.

## 4. Bisect of recent (last ~day) time-policy changes

**Blame proves the structural race is foundational, not a recent regression.**
`git log -S` on `add_scheduler_time`, `threads_time`, `as_nanos`, the
`time_from_sched` bump in `recv_rpc`, and `GlobalTimeLowerBound => as_nanos()`
all resolve to `c6d05ef2` (2022-11-12 "Initial commit").

Recent changes, classified:
- **#1095 / `c4a4bba2` "normalize guest clock after exec" (07-28)** — the genuine
  recent clock-**DOMAIN** regression: guest reads subtracted a per-task/per-exec
  origin, so deadlines were computed in a reset domain but judged against
  absolute committed → deadlines land in the scheduler's past → poller
  starvation / demo5 wedge.
- **#1190 = `cc3730fd` + `3ac51e11` (07-31)** — the FIX-FORWARD, not a
  regression. `git show cc3730fd`: touches only `lib.rs`, `syscalls/time.rs`,
  `tool_local.rs`; **removes** the origin subtraction
  (`-let epoch = DetTime::from(&guest.config().epoch).as_nanos();`) and routes
  reads through `GuestClock::observe` shared across the tree. Does **not** touch
  `add_scheduler_time`/`extra_time`/`threads_time`/`as_nanos`.
- `0ca0dec2` (clock_getres NULL, #1208), `39cfb5a7` (SaBRe loopback yield, #1182)
  — unrelated to the coupling.

**Test (before/after evidence):** `cargo test -p detcore --lib guest_clock`
@ `0ca0dec2` → 4/4 PASS, incl.
`guest_clock_absolute_deadline_stays_ahead_of_committed_time`
(`observe(committed)==committed` and `deadline>committed`) and
`guest_clock_process_tree_shares_one_monotonic_domain`. #1190 mechanism live and
correct at HEAD.

**Conclusion:** (1) the last-day clock-DOMAIN regression was #1095/`c4a4bba2`,
already fixed-forward by #1190. (2) "committed races ~1000x ahead of local work"
is a LATENT/FOUNDATIONAL property (`extra_time` global-only) — bisect finds no
window culprit (consistent with the earlier "demo5 bisect INVALID" finding).

## 5. Recommended fix (continuity-preserving — no blunting)

Do **not** coarsen/round/freeze committed and do **not** lower `NANOS_PER_SCHED`
as a "fix" — that is fake determinism. Advance committed by **real deadline
distance** instead of manufacturing per-turn scheduler time:

1. **PRIMARY (owner-designed, trigger#4 core-scheduling — do not freelance):**
   make step2d's vtime-jump fire when `run_queue` holds only unproductive
   pollers, jumping `committed_time` to the nearest future `timed_waiter`
   deadline instead of accruing +0.5ms/retry. Keeps time continuous (advances to
   the exact next real event) while removing the turn-count over-count. Design:
   ai_doc @`4ac9ab2a` (memory `scheduler-vtime-jump-unproductive-pollers`).
2. **SECONDARY (cheaper interim):** suppress `add_scheduler_time` on
   `last_turn_was_polling` retries (`scheduler.rs:2524-2530` already special-cases
   these for detlog) so pure IO-poll spin doesn't inflate committed; genuinely
   sleeping threads' deadlines still drive the jump. Partial but low-risk.
3. **RESIDUAL to flag (not demo5):** `use_thread_local_clock_reads=true` (SaBRe)
   still reads the lagging `thread_logical_time`; the #1190 max-floor only helps
   if a prior default-path read pinned `now` to committed. A pure SaBRe
   thread-local process can still trail committed → deadline-in-past risk.
   Warrants a targeted SaBRe deadline test.

## Coordination with agent 220 (RCB-time demo5 prototype)

demo5's wedge has two independent causes: (A) the #1095 clock-domain split,
already fixed by #1190; and (B) the foundational `extra_time` over-count. 220's
`--rcb-time` approach attacks (B) by giving real per-thread progress signal so
committed isn't dominated by scheduler `extra_time`. Both are needed; neither
alone fully un-wedges demo5. Recommend 220 validate against the vtime-jump design
@`4ac9ab2a` rather than only tuning `--no-rcb-time` constants.
