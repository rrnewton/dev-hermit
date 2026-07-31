# Connector adversarial review, round 8

[adversarial-reviewer agent, gpt-5.6-sol]

Reviewed implementation source:
`fbdf41d03c0334e7ac8d27c93e6fedd9a4fc44e6` (published in parent main
`9de12121d2682c6c0c00ee5bb1f9990223d5039c`).

## Verdict

**REJECT - Blocker.** The registered child callback now keeps the barrier
through all child publications and performs no futex wake, but the separate
skipped-callback recovery path still performs interposable, child-private
futex wakes.

## Blocker

`recover_skipped_atfork_child` reaches wakes through
`ProcessEpochClaim::{publish,drop}`. The shared `futex_wake` helper calls
`libc::syscall`. In the post-fork child there is no sibling thread to wake,
the parent's futex pages are private, and an interposer may allocate, lock, or
block.

On the exact reviewed commit, `strace` of
`registration_added_after_fork_snapshot_recovers_child_epoch` showed the fork
child issue two `FUTEX_WAKE_PRIVATE, INT_MAX` calls immediately after clone.

Remove wakes from every successful and failing skipped-child recovery path.
Add deterministic regressions asserting that both paths issue zero shim futex
wakes.

## Passing evidence

- Barrier owner and disabled call gate span every registered-child
  publication and gate reset.
- The registered callback contains no futex wake.
- The hostile helper resets `SIGPIPE` after Rust startup; `strace` observed
  the disposition change from ignored to default.
- The exact archived shim suite passed 16/16, including publication cuts,
  hostile stderr, pre-claim, nested-fork, and skipped-callback tests.

The reviewer used an isolated archive and modified no repository files.
