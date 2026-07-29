# Connector adversarial review, round 5

[adversarial-reviewer agent, gpt-5.6-sol]

Reviewed implementation: `89d1e931d3a8f39e940aacf136650b818d6bd9da`.

## Verdict

**REJECT.** The skipped-callback PID-epoch recovery is sound under its stated
bounds, but one major nested-fork deadlock remains.

## Major finding

`ForkBarrier::prepare` uses a non-reentrant spin lock and holds it until the
parent or child completion callback. Prepare handlers run in reverse
registration order. After the lazily registered shim handler acquires the
barrier, an older third-party prepare handler can call libc `fork()` once. The
nested fork invokes the shim prepare again on the same thread, which spins
forever trying to acquire its own barrier.

The documented unsupported set names raw fork syscalls, `vfork`, and fork from
an admitted hook, but not this ordinary libc nested-fork path. The barrier must
identify same-thread recursion atomically and fail stop rather than hang, or
the path needs an equally enforceable explicit policy and regression test.

## Minor finding

The helper and tests for resetting a foreign BUSY registration now exercise an
obsolete policy. Production correctly rejects ambiguous foreign BUSY without
waiting, resetting, or re-registering. Remove the unreachable reset helper and
its stale-observer tests so coverage reflects the shipped policy.

## Passing evidence

- Skipped-callback recovery stress: 100/100.
- Shim and connector tests: 11/11 each.
- Preload stress: 15 processes, 45,015 calls, 15 attachments.
- Ptrace injection/detach passed.
- All transport/status taxonomy probes passed.
- NODELETE and call-after-`dlclose` passed.
- Foreign EMPTY/READY recovery, ambiguous BUSY fail-stop, callback rebinding,
  concurrent recovery publication, panic/drop, and fork/exec inheritance
  matched their documented contracts.

No files were modified by the reviewer.
