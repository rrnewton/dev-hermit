# Ptrace determinism stress tests + real-time-signal-delivery bug

**Date:** 2026-07-27
**Task:** `compat-ptrace-stress-tests` (P1: Ptrace compat — stress tests)
**Backend:** ptrace (default). **Mode:** run + `--verify` (L2). No relaxations.
**Hermit:** `worktrees/275/hermit` `target/release/hermit`, built from
`origin/main` (slot HEAD `15293c34`).
**Reverie dependency:** pinned git rev
`9233c0d099ce3bdc820958aa3071a6c1439bc249` (read-only cargo checkout).

## Question

Do concurrency/fork/IPC/signal-heavy workloads execute **deterministically**
(bitwise-identical across two runs, `hermit run --strict --verify`) on the
ptrace backend? Each program prints an **order-DEPENDENT** FNV-1a digest (not
just an order-independent checksum) so a nondeterministic scheduler shows up as
a `--verify` divergence, not a silently-different total.

## Result summary

| Family | Program | `--strict --verify` | Notes |
|--------|---------|---------------------|-------|
| 1. Multi-threaded mutex/condvar contention | `mutex_contention.c` | **PASS (L2)** | 8 workers × 2000 iters + condvar ping-pong 400. 6225 DETLOG/COMMIT msgs, no diff. |
| 2. Fork + execve chains | `forkexec_chain.c` | **PASS (L2)** | depth-6 self re-exec chain + width-3 fork+exec fanout/level. 3707 DETLOG/COMMIT msgs, no diff. |
| 4. Pipe + socket IPC | `socket_ipc.c` | **PASS (L2)** | AF_UNIX socketpair ping-pong (500 rounds) + 6-proc pipe ring (20 laps). 8849 DETLOG/COMMIT msgs, no diff. |
| 3. Signal delivery ordering | `signal_ordering.c` | **FAIL** | Aborts with `EINVAL` — see bug below. Standard signals fine; **real-time** signal delivery is broken. |

3 of 4 families are **L2** on ptrace/run mode. The 4th family exposes a
genuine, well-localized Reverie/safeptrace bug.

## Bug: real-time signals cannot be delivered to a guest handler (ptrace)

**Symptom.** Any program that installs a handler for a real-time signal
(`SIGRTMIN`+k, i.e. signal number ≥ 34) and causes it to be delivered aborts
under `--strict`:

```
Error: wait after seccomp resume failed for tracee 3: -22 EINVAL (Invalid argument)
```

**Minimal reproducer** (`rt_signal_repro.c`): install a handler for
`SIGRTMIN`, `raise(SIGRTMIN)` once, print. Native prints `A c=1`; under
`hermit run --strict` it dies with the EINVAL above and never runs the handler.
Standard signals (`SIGUSR1`/`SIGUSR2`), including blocked + queued + burst
patterns, work fine (`sig_min1`/`sig_min2`/`sig_min4` in scratch all pass).

**Root cause.** The signal type threaded through the entire reverie-ptrace
signal path is `nix::sys::signal::Signal` (re-exported by `safeptrace` as
`pub use nix::sys::signal::Signal;`). The nix `Signal` enum (v0.30/0.31)
**has no real-time-signal variants — it stops at `SIGSYS` (31).** When a
tracee stops to deliver signal 34 to its handler, the wait-status decoder

```
safeptrace/src/lib.rs:345
    let sig = Signal::try_from(libc::WSTOPSIG(status)).map_err(|_| Errno::EINVAL)?;
```

calls `Signal::try_from(34)`, which fails, and the `.map_err(|_| Errno::EINVAL)`
turns it into `EINVAL`. That error propagates out of
`Stopped::next_state()` and is wrapped at `reverie-ptrace/src/task.rs:1464`
(`"wait after seccomp resume"`), aborting the whole run. Detcore itself is
**not** at fault: the trace (`rt_signal_repro.trace_tail.log`) shows detcore
correctly services the raise as `tgkill(3, 3, 34) = Ok(0)` and runs its
posthook; the failure is in the immediately-following wait when the kernel
tries to hand the RT signal to the guest and safeptrace can't name it.

Same conversion (and thus same limitation) appears at
`safeptrace/src/lib.rs:337,348` and `safeptrace/src/lib.rs:1400,1411`, and the
type is used as `Event::Signal(Signal)`, `Stopped::resume(Option<Signal>)`,
`pending_signal: Option<Signal>`, and the `handle_signal` match arms in
`reverie-ptrace/src/task.rs`.

**Fix scope (needs approval — core Reverie contract).** Supporting RT signals
means replacing `nix::sys::signal::Signal` in the safeptrace/reverie-ptrace
signal-delivery path with a representation that can hold any valid signal
number (e.g. a `safeptrace` newtype wrapping `c_int` with associated
`SIGSEGV`/`SIGTRAP`/… consts so existing match arms keep working). This
touches a core Reverie abstraction (`Event::Signal`, `resume`/`step`
signatures, signal comparisons) and the crate is a pinned read-only git
dependency, so per the parent `AGENTS.md` Reverie API Policy it requires user
sign-off, a reverie feature branch, and a coordinated parent pin bump. It is
**not** an additive change and was **not** applied here. Filed as an issue on
`rrnewton/reverie`.

## Reproduction

```bash
# build the hermit under test
cd ~/work/dev-hermit/worktrees/275/hermit && cargo build --release

# build the tests (static, no PIE)
cd <this dir>
for f in mutex_contention forkexec_chain socket_ipc signal_ordering; do
  gcc -O2 -static -fno-pie -no-pie "$f.c" -o "$f" -pthread
done
gcc -O2 -static -fno-pie -no-pie rt_signal_repro.c -o rt_signal_repro

HERMIT=~/work/dev-hermit/worktrees/275/hermit/target/release/hermit
"$HERMIT" run --strict --verify -- ./mutex_contention   # PASS
"$HERMIT" run --strict --verify -- ./forkexec_chain     # PASS
"$HERMIT" run --strict --verify -- ./socket_ipc         # PASS
"$HERMIT" run --strict -- ./rt_signal_repro             # EINVAL (bug)
```

## Files

- `mutex_contention.c`, `forkexec_chain.c`, `socket_ipc.c`,
  `signal_ordering.c` — the four stress families.
- `rt_signal_repro.c` — minimal single-`raise(SIGRTMIN)` reproducer.
- `rt_signal_repro.trace_tail.log` — `--log trace` tail showing
  `tgkill(...,34)=Ok(0)` then `detcore shut down` then the EINVAL.
- `metadata.json` — SHAs, host, commands, native baselines.
