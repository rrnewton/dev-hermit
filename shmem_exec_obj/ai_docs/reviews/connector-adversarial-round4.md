# Connector adversarial review, round 4

[adversarial-reviewer agent, gpt-5.6-sol]

Reviewed implementation: `138026ea1f4226037e6087b64252d0b198da7bf9`.

## Verdict

**REJECT.** No blocker was found, but one major fork/registration race remains.

## Major finding

Lazy `pthread_atfork` registration can race a fork whose handler list has
already been snapshotted. A different prepare handler may be running while the
shim's first caller registers its handler. The current fork then skips the new
shim handler even though the eventual child inherits the newly inserted list
entry and the shim's process-local state.

Two unsafe outcomes follow from the current owner/state protocol:

- If registration publishes ownerless `READY` before the kernel fork, the child
  cannot tell that its callback was skipped. It can inherit live call-gate or
  initialization state and later wait forever or retain a phantom token.
- If the child inherits the packed `BUSY` claim after the handler was inserted
  but before readiness publication, foreign-owner recovery registers the
  already inherited handler again. A later fork can invoke `atfork_prepare`
  twice and self-deadlock on `FORK_BARRIER`.

Packing `(BUSY, owner_pid)` correctly fixed the stale-observer ABA from round
three, but it does not encode whether the handler was installed or whether it
ran for the fork that created this process. The documentation therefore
overstates the lifecycle guarantee.

## Minor finding

The status-taxonomy prose says later runtime errors are `-6`, while a state
generation mismatch detected after mapping is classified as incompatible image
(`-3`). The text should describe this boundary precisely.

## Passing evidence

- Shim unit tests: 10 passed.
- Connector unit tests: 10 passed.
- Default preload: 7 processes, 1,407 calls, 7 attachments.
- Stress preload: 40 processes, 320,040 calls, 40 attachments.
- Ptrace injection and detach passed.
- All 11 failure-taxonomy probes passed.
- NODELETE, call-after-`dlclose`, and residency checks passed.
- `git show --check 138026e` passed.

## Required remediation

Child correctness must not depend on a lazily registered handler having been
included in the current fork's callback snapshot. Every public hook should
validate a process PID epoch before admission and recover process-local copied
state in a proven fork child. A ready registration must retain its owner PID so
a skipped callback is detectable. An inherited in-progress registration whose
installation status is ambiguous must fail closed rather than re-register.
Add a deterministic test that registers the shim from another thread while a
pre-existing prepare callback holds an in-progress fork.
