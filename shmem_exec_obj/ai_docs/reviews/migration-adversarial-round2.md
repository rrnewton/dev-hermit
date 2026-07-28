# Migration adversarial review, round 2

Date: 2026-07-28

Reviewed tip: `9945098`

Reviewer role: `[adversarial-reviewer agent, gpt-5.6-sol]`

Verdict: **REJECT**

## Major finding

`AdmissionQuiescence<Authority>` stored an unconstrained authority normally.
Dropping `Migration`, `TargetReadyMigration`, or `CommittedMigration`, or
supplying a mismatched reclamation permit, could therefore drop the authority
before reclamation. An external probe instantiated `Authority = &mut u64`,
dropped a live migration, and then safely mutated the source. A second probe
showed that wrapping `&mut T` in `ManuallyDrop` alone does not preserve borrow
exclusivity after the holder is dropped.

The type needs an explicit unsafe fail-closed authority contract which excludes
borrow-mediated regain and release-on-drop behavior, plus leak-on-abort semantics
until a matching reclamation permit releases the authority.

## Minor findings

- The crash test synchronized through a later `child_started` release store,
  so it did not independently prove that the migration phase CAS published the
  target bytes. The parent should acquire the phase transition directly.
- The guide described non-overlapping regions without making equally clear
  that source and target must also have distinct backing identities.
- The target backing contract should state its destructor obligations
  explicitly, although the observed abort order correctly poisoned before
  dropping the private target capability.

## Reproduced evidence

Debug, release, and Rust 1.85 migration tests passed 9/9. The example,
no-default checks/tests, strict Clippy, rustdoc, and seven doctests also passed.
Those gates did not exercise authority destructor or borrow-regain behavior.
