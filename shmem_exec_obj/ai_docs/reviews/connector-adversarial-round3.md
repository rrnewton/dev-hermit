# Connector adversarial review, round 3

[adversarial-reviewer agent, gpt-5.6-sol]

Reviewed implementation: `9463387db4b73f09425b8e84ca1f327a6f51d665`.

## Verdict

**REJECT.** No blocker was found, but one major lifecycle race remains.

## Major finding

The foreign-owner recovery path for lazy `pthread_atfork` registration has an
ABA race. In a child that inherited `(BUSY, parent_pid)`, caller A can read the
foreign owner and pause. Caller B can reset `BUSY -> EMPTY` and claim
`EMPTY -> BUSY` for the child. Caller A can then perform its stale
`BUSY -> EMPTY` compare-exchange, erasing B's live claim. Both callers may
subsequently register atfork handlers.

A later fork can then invoke `atfork_prepare` twice. The first invocation takes
`FORK_BARRIER`; the second spins forever trying to take the same barrier. The
existing same-PID test does not cover this stale foreign-owner interleaving.

The preceding publication ordering is sound: the relaxed owner-PID store is
sequenced before the release claim CAS, and acquire observation of the state
publishes the PID. The problem is that the subsequent reset does not bind its
CAS to the owner observation or to a versioned claim.

## Passing evidence

- Shim and host tests: 9 passed.
- Connector failure taxonomy: all 11 probes passed, including both write-sealed
  state variants returning `InvalidTransport` (`-2`) before mapping.
- NODELETE test proved callable and resident after `dlclose`.
- Preload stress passed with 15 processes, 4 threads per process, 6,015 calls,
  and 15 attachments.
- Ptrace injection passed and detached.
- An adversarial readiness failure killed and reaped both the group leader and
  its descendant.
- The reviewed `v2` tree matched the implementation commit.

## Required remediation

Make child recovery of a foreign registration claim atomic with respect to its
owner identity, for example by combining owner/version and state in one atomic
word or by using a child-only reset protocol that cannot race a new same-PID
claim. Add a deterministic stale-observer regression test, then repeat a fresh
review.
