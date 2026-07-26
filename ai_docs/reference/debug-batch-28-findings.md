# Debug Batch 28 — `--strict --verify` failure root causes

- **Task:** `impl-debug-batch-28` (agent hermit-209, opus-4.8)
- **Date:** 2026-07-26
- **Binary:** `worktrees/slot209/hermit/target/release/hermit`, branch
  `debug-batch28-slot209` reset to `origin/main` **d0556287** ("Preserve SaBRe
  coordinator across fork" #771).
- **Backend:** ptrace. **Log level:** `HERMIT_LOG=debug`. **Relaxations:** none.
- **Method:** hermit-debugging skill — read the detcore trace, not the source
  first; source consulted only where the trace pointed.

Three programs were picked from `validate.sh`
`COMPAT_SUMMARY_KNOWN_FAILURES`, deliberately spanning three *distinct* failure
classes so the batch is not a single bug repeated.

Note on capturing the trace: `--log debug`/`--log-file` produced only the
wrapper summary under `--verify` (per-run traces are written to
`/tmp/run{1,2}_log_*`). Use `HERMIT_LOG=debug` on a **single** `--strict` run
(no `--verify`) to get the full guest trace on stderr.

---

## 1. `flock` — fail-closed abort on an unsupported syscall (fails at L1)

**Command:** `hermit run --strict --verify -- /usr/bin/flock -n /tmp/f.lock -c true`
**Result:** aborts in run 1: `Error: Sandbox container exited unexpectedly …
Exited(1)`.

**Trace (the decisive lines):**
```
DETLOG [syscall][detcore, dtid 3] finish syscall #110: openat(... "/tmp/slot209.lock", O_CREAT|O_NOCTTY ...) = Ok(3)
DETLOG [syscall][detcore, dtid 3] inbound syscall: flock(3, 6) = ?
ERROR detcore: [detcore, dtid 3] inbound syscall: flock(3, 6) = ?     <-- fail-closed abort
```
`flock` opens the lockfile (fd 3) then issues `flock(3, 6)` where
`6 = LOCK_EX(2) | LOCK_NB(4)`. detcore logs the inbound syscall, then emits an
`ERROR` and terminates the container.

**Root cause:** `flock` is in the **UNSUPPORTED SYSCALLS** arm of
`detcore/src/syscall_classification.rs:487`. Post-#644, plain `--strict`
fail-closes on unsupported syscalls (it used to forward them), so any `flock`
user aborts. This is a **syscall gap**, not a determinism bug.

**Fix direction:** add a deterministic `flock` handler under
`detcore/src/syscalls/` and move `Sysno::flock` out of the unsupported arm.
`flock` advisory locks on a determinized fd table are straightforward to
model deterministically (the fd already round-trips through detcore, per
`helpers.rs:558 Syscall::Flock(s) => Some(s.fd())`).

---

## 2. `free` — live `/proc/meminfo` passthrough (passes L1, fails L2)

**Command:** `hermit run --strict --verify -- /usr/bin/free`
**Result:** single `--strict` run succeeds; `--verify` reports
`:: Failure: nondeterministic.` / `Mismatch between run 1 and run 2 outputs`.

**Key observation:** the DETLOG comparison is **clean** —
`Done processing logs, no substantive differences found.` The schedule and all
syscall results are byte-identical between the two runs. The divergence is in
the **guest stdout only**:
```
run A: Mem:  791462432   319667588   23947028   5746912   459296512   471794844
run B: Mem:  791462432   319454512   24157188   5746912   459299480   472007920
```
Column 1 (`MemTotal` = 791462432) is stable; columns 2–6
(used/free/shared/buff+cache/available) differ every run.

**Root cause:** `free` opens `/proc/meminfo` (confirmed in the trace's openat
list) and detcore passes it through to the **live host kernel** file, whose
used/free/available fields change moment-to-moment as other processes run.
`MemTotal` is constant so column 1 matches; everything derived from live usage
diverges. This is an **unvirtualized data source**, i.e. a real determinism
bug.

**Fix direction:** virtualize `/proc/meminfo` (serve synthetic, stable
contents) — this is exactly what **PR #761** proposes (meminfo + stat +
vmstat). It is confirmed **not effective on main d0556287** (the divergence
still reproduces). See memory `proc-meminfo-live-passthrough-nondet`.

---

## 3. `timeout` — POSIX timer signal delivery not emulated (wrong result / hang)

**Command:** `hermit run --strict -- /usr/bin/timeout 1 sleep 3`
**Result (finite-child case):** completes with **exit 0** at virtual time
**3.05s** — i.e. the 1-second timeout **never fired**; `sleep` ran its full 3s
and `timeout` reaped it normally. Native `timeout 1 sleep 3` fires at 1s and
exits **124**. So hermit silently produces the *wrong* result.
**validate.sh corpus case:** the `COMPAT_SUMMARY_KNOWN_FAILURES[timeout]` note —
"parent waits indefinitely in `rt_sigsuspend` for the delayed child" — is the
same bug when the child would only ever be killed by the (never-delivered)
timer: the parent blocks until the host `STRICT_COMPAT_TIMEOUT` kills the run.

**Trace (the decisive lines):**
```
timer_create(CLOCK_REALTIME, ...) => deterministic timer id 0 (arming tracked; signal delivery not emulated)
timer_settime(id=0, interval_ns=0, value_ns=1000000000) armed against virtual clock (not delivered)
inbound syscall: rt_sigsuspend(0x..., 8) = ?
finish syscall #127: rt_sigsuspend(0x..., 8) = Err(Errno(ERESTARTNOHAND))
```
`timeout` arms a 1s POSIX timer, then `rt_sigsuspend`s waiting for its
`SIGALRM`. The timer's expiration signal is **never delivered**, so the
suspend only returns because the child's `SIGCHLD` arrives at 3s
(`ERESTARTNOHAND`). The timer that is `timeout`'s entire reason to exist is
inert.

**Root cause:** `detcore/src/syscalls/time.rs:319` — `handle_timer_create`
explicitly *"arming is tracked … but expiration signals are not delivered"*;
`handle_timer_settime` (`time.rs:382`) arms "against virtual clock (not
delivered)". `detcore/src/lib.rs:1657` confirms expiration signals are not
delivered. Matches memories `timeout-needs-posix-timer-signal-delivery` and
`timer-create-neutralize-armed-breaks-verify`.

**Fix direction:** deliver POSIX timer expiration signals against the virtual
clock — enqueue the timer's `sigevent` signal at the armed virtual deadline so
a blocked `rt_sigsuspend`/`ppoll` wakes deterministically. This is the harder
of the three (the arming neutralization was deliberately chosen because an
*armed-and-delivered* timer previously broke `--verify` via the RCB clock —
see `timer-create-neutralize-armed-breaks-verify`). A correct fix must deliver
the signal on the *virtual* deadline without perturbing the RCB-derived clock.

---

## Summary table

| Program | L1 (`--strict`) | L2 (`--verify`) | Class | Root cause | Fix locus |
| --- | --- | --- | --- | --- | --- |
| `flock` | **FAIL** (fail-closed abort) | n/a | unsupported syscall | `flock` in UNSUPPORTED arm | `syscall_classification.rs:487` + new handler |
| `free` | PASS | **FAIL** (stdout diverges) | unvirtualized source | live `/proc/meminfo` passthrough | virtualize meminfo (PR #761) |
| `timeout` | "passes" but **wrong result** (exit 0 vs 124) | hangs / wrong | missing signal delivery | POSIX timer expiration not delivered | `syscalls/time.rs:319/382`, `lib.rs:1657` |

All three are pre-existing, already-catalogued known failures; batch 28
confirmed each still reproduces on main d0556287 and localized the exact trace
signature and source line for each. No code changes made (debug/analysis task).
