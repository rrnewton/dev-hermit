# Connector adversarial review, round 1

Date: 2026-07-28

Reviewed implementation: `7c66d67dec59e61ab0513060e617aee817c7ffd3`

Reviewer role: `[adversarial-reviewer agent, gpt-5.6-sol]`

Verdict: **REJECT**

## Major findings

1. Transport failures violated the stable status ABI. Artifact descriptor
   type, seal, length, and read failures were reported as
   `IncompatibleImage`; code and state descriptor failures were reported as
   `InitializationFailed`. A direct C entry probe using `/dev/null` as the
   artifact descriptor returned `-3` instead of `InvalidTransport` (`-2`).
2. Concurrent first initialization used an iteration-count cutoff. A waiter
   could fail closed while another thread was still legitimately reading and
   hashing the permitted 256 MiB artifact.
3. A caught panic after publishing `ATTACHED_PID = ATTACH_BUSY` could strand
   that state forever because attachment publication lacked an unwind guard.
4. The finalizer's drain did not cover a hook already executing before gate
   admission, so it was insufficient to make `dlclose` and unmapping safe.

## Minor findings

- `pthread_atfork` registration in the loader constructor did not justify the
  claim that constructor work could not allocate or take libc locks.
- Same-thread signal reentry had a window before `HookGuard` was installed.
- The public C header omitted symbolic `BootstrapStatus` constants.

## Reproduced evidence

- Core connector tests: 9 passed.
- Shim tests: 4 passed.
- No-default connector tests: 9 passed.
- C layout compilation passed.
- Preload proof: 7 processes, 1,407 calls, 7 attachments.
- Ptrace proof resumed after detach with 1 call and 1 attachment.
- Both forged-digest negative probes rejected the context.
- An additional real-fork probe produced 2 processes, 4 calls, and 2
  attachments.

The positive demonstrations were valid, but they did not cover the four
release-blocking lifecycle and status failures above.
