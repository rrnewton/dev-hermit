# Connector adversarial review, round 7

[adversarial-reviewer agent, gpt-5.6-sol]

Reviewed published implementation:
`9b9d6325ce29e8db85cd58f8d2eb7dddba8cd757`.

## Verdict

**REJECT.** Pre-claim ownership and raw fail-stop are corrected, but the
barrier ends before the complete child callback and the success path still
uses an interposable syscall wrapper.

## Blockers

1. `atfork_child` re-enables `CALL_GATE` and releases the fork owner before it
   publishes all child-local claim, attachment, failure, and process-epoch
   state. A signal handler can therefore enter an instrumented libc hook while
   those fields disagree. In the claim-child/epoch-parent window, recovery
   rejects the state and reaches ordinary diagnostic I/O, which may block or
   receive `SIGPIPE`. Keep the gate disabled and the child identity owned until
   every child callback mutation is complete.

2. The successful child callback calls `futex_wake_u32` and `futex_wake_i32`,
   which reach interposable `libc::syscall`. An interposer may allocate, lock,
   or block in the single-threaded post-fork child. These wakes cannot notify
   parent waiters and no sibling child thread exists; remove them from the
   child callback or replace them with audited direct syscalls if a real child
   waiter is demonstrated.

## Major finding

The broken-stderr regression does not establish default-`SIGPIPE` behavior.
Its `pre_exec` hook installs `SIG_DFL`, but `strace -ff -e rt_sigaction` shows
the execed Rust helper immediately changing `SIGPIPE` back to `SIG_IGN` before
the test body. Reset the disposition inside the helper after Rust startup and
before triggering the fail-stop path, or use a C helper.

## Passing evidence

- Shim unit suite: 15/15.
- The capacity-filled stderr pipe was truly full and blocking; fail-stop exited
  125 within its bound.
- The pre-claim signal/fork regression observed nested status 125 and ordinary
  child status 0.
- Skipped-callback recovery and concurrent barrier tests passed.
- Source inspection confirmed inline x86-64/AArch64 `exit_group`; x86-64 raw
  PID/TID assembly declares the required `rcx`/`r11` clobbers.

The reviewer used an isolated archive and modified no repository files.
