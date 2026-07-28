# Connector adversarial review, round 2

Date: 2026-07-28

Reviewed implementation: `8c758008a977357092f2c62ac479b14dcb4a8c7a`

Reviewer role: `[adversarial-reviewer agent, gpt-5.6-sol]`

Verdict: **REJECT**

## Major findings

1. `ATFORK_OWNER_PID` was stored before an Acquire-only compare-exchange
   published `ATFORK_STATE = BUSY`. The state publication therefore did not
   release-publish the owner PID. A waiter could observe BUSY with a stale PID,
   take the child-recovery path, and register duplicate at-fork handlers. Their
   two prepare callbacks would deadlock on the non-reentrant fork barrier.
2. State transport accepted an `O_RDWR` memfd carrying `F_SEAL_WRITE` or
   `F_SEAL_FUTURE_WRITE`. Writable mapping then failed as runtime
   `InitializationFailed` (`-6`) instead of transport `InvalidTransport`
   (`-2`). A real READY-state, write-sealed memfd probe returned `-6`.

## Minor finding

An error from the ptrace fixture's pre-ready wait bypassed the later
process-group kill/reap cleanup and could leave a target alive.

## Accepted corrections

The prior round's other findings were fixed: transport/identity/runtime status
probes, non-spurious futex initialization waiting, fail-stop owner-loss policy,
panic-safe attachment publication, pre-syscall recursion guarding, C constants,
immutable artifact/code authentication, ptrace detach/error cleanup, and ELF
`NODELETE` lifetime all behaved as documented.

Current and Rust 1.85 connector checks passed; core connector tests passed
10/10, shim tests 7/7, no-default connector tests 10/10, all four integration
scripts passed, and `readelf` plus a post-`dlclose` call confirmed `NODELETE`.
