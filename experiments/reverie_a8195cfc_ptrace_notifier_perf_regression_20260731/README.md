# Reverie a8195cfc ptrace notifier perf regression (~10x per ptrace stop)

**Question.** Reverie commit `a8195cfc` ("reverie-liteinst: add ptrace-owned
hybrid runtime", #270) was bisected by hermit-220 as the cause of demo5's
second regression (green boot 74s → HPET stall at 238s, >3x). What does it do,
and can it be fixed forward?

**Answer.** a8195cfc adds ~10x wall-clock overhead **per ptrace syscall-stop**
on the reverie-ptrace backend, dominated by **system time** (kernel/handoff),
by replacing `safeptrace/src/notifier.rs`'s lock-free status handoff with a
contended `parking_lot::Mutex<StatusState>` + `Condvar` + wait-owner ownership
transaction. It is NOT the liteinst code (gated off for ptrace) and NOT an added
thread hop (both eras use one worker thread per guest thread).

## Method (clean isolation: same hermit, swap only the reverie pin)

Hold hermit at `9c964fce` (first-bad Hermit, pins reverie `fb2cf7e0`). Re-pin
reverie to `a8195cfc~1` (`689424fe`, GREEN) vs `a8195cfc` (BAD). `a8195cfc` does
not touch reverie's public API (`reverie/src`), so `9c964fce` compiles against
both. Microbench: a getpid loop under `hermit run --strict` (ptrace backend,
default log, no relaxations); getpid is trapped, so wall time is per-stop cost.

```
cc -O2 -o syscall_loop syscall_loop.c            # N getpid()
cc -O2 -pthread -o syscall_loop_mt syscall_loop_mt.c   # T threads x N getpid()
hermit run --strict -- ./syscall_loop 100000
hermit run --strict -- ./syscall_loop_mt 8 40000
```

## Results (back-to-back)

| workload            | GREEN a8195cfc~1 | BAD a8195cfc | ratio |
|---------------------|------------------|--------------|-------|
| single 100k getpid  | 3.12s            | 35.25s       | 11.3x |
| MT T=1 x40k         | 1.30s            | 12.33s       | 9.5x  |
| MT T=4 x40k         | 5.19s            | 50.62s       | 9.8x  |
| MT T=8 x40k         | 10.37s           | 101.78s      | 9.8x  |

CPU split (BAD, 20k getpid): User 0.62s / System 5.50s / wall 6.13s → ~100% CPU,
kernel/handoff-bound (~275us system-time added PER event). `strace -f -c` is
blind (nested ptrace: launcher shows ~118 syscalls total); `/usr/bin/time`
system-time is the reliable signal. `perf trace` tracepoints are gated on this
host.

## Root cause (code)

`safeptrace/src/notifier.rs`: GREEN=464 lines, `Event{status:AtomicI32,
status_waker}` — worker `waitid::waitpid(pid, WEXITED|WSTOPPED)` → `update()` =
atomic swap + `waker.wake()`; `poll_status` = register waker + atomic load
(lock-free SPSC). BAD=7832 lines: `Event{status:Mutex<StatusState{VecDeque,
terminal}>, status_changed:Condvar}` + a wait-owner ownership transaction
(`claim_notifier_wait` + `begin_status_return` + `ReturnTransaction::commit` =
3x `wait_owner_lock` + `wait_owner_changed.notify_all`) per consumed event, and
worker switched to `waitid::waitpidfd(pidfd)`. The contended Mutex+Condvar
worker↔poller handoff (replacing the lock-free atomic) is the dominant cost.

## Fix-forward

The effective fix is restoring a **lock-free (SPSC) status handoff** for the
common non-terminal single-status case, retaining the Mutex/VecDeque/ownership
machinery only for multi-pending / terminal / synchronous / cancellation cases.
This is race-critical (a8195cfc carries extensive cancellation/generation
handling + a 586-line `reverie-liteinst/tests/hybrid.rs` race suite) → core
Reverie wait/notifier semantics → requires dual adversarial review + owner
sign-off before land.

**Low-risk increment measured (NOT the fix):** a guarded lock-free fast path in
`claim_notifier_wait` (skip the per-poll `wait_owner_lock` in steady state)
recovers only ~13% single / ~4% MT (still ~10x vs green) — empirically proving
the `wait_owner_lock` is not the dominant cost; the contended status Mutex is.

## Metadata
- Hermit: 9c964fce (isolation), aa5258b6 (current main). Reverie: 689424fe (green)
  vs a8195cfc (bad); fast-path increment branch claude/measure-fix-on-a8195cfc
  @ 1c35a2d3. Host: devbig, shared (other agents active — absolute numbers vary;
  the same-host back-to-back green-vs-bad ratio is the robust signal).

## CORRECTION + FIX (hermit-238, 2026-07-30)

The "Root cause (code)" section above (hermit-233) misattributed the cost to
the contended `Mutex<StatusState>` + `Condvar` worker<->poller handoff. That is
**wrong**: context-switch counts are identical green vs bad, so it is not lock
contention. A supervisor-attached strace (the launcher strace is blind through
nested ptrace — find the supervisor with `pgrep -P <launcher>` and
`strace -f -c -p <sup>`) shows a per-stop **procfs + pidfd syscall storm**.

**Actual cause.** a8195cfc bound every notifier `Event` to a captured procfs
generation (`WorkerIdentity`) and wired `Notifier::current_or_new` to *always*
call `capture_identity` -> `WorkerIdentity::capture` (`/proc/<pid>/stat` +
`/proc/<pid>/status` read twice, `pidfd_open`, O_PATH-open `/proc/<pid>`) before
the registry fast-return. `current_or_new` is reached once per
`Stopped::new_unchecked` = `TracedTask::assume_stopped`, which reverie-ptrace
runs many times per trapped syscall. The async `Mutex`/`Condvar` handoff is
already fast in steady state (`claim_notifier_wait` == `Existing` short-circuit),
so the proposed lock-free SPSC status-slot rewrite was unnecessary.

**Fix (reverie PR #305 @7172f41).** Fast path at the top of `current_or_new`:
adopt a still-live registered generation via a single pidfd-liveness syscall
(a live exact pidfd proves the same kernel task generation), skipping the procfs
capture; non-live/errored identities fall through to the unchanged slow path.

**Result (100k getpid, `hermit --strict`, ptrace, same host, back-to-back):**

| pin | real | sys |
|---|---|---|
| a8195cfc~1 (green) | 3.13-3.18s | 2.72s |
| a8195cfc / current main | 30.5-32.5s | 27.4s |
| PR #305 fix | 3.43s | 2.82s |

~8.9x recovery to green. safeptrace notifier 93/93 + reverie-liteinst hybrid
20/20 pass; fmt+clippy clean.
