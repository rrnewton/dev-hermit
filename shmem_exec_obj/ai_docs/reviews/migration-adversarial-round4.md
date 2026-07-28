# Migration adversarial review, round 4

Date: 2026-07-28

Reviewed implementation: `7e30537aae304a9a2905a30b1a42721683141e9b`

Reviewer role: `[adversarial-reviewer agent, gpt-5.6-sol]`

Verdict: **ACCEPT**

## Result

No Blocker or Major remained. The source witness carries the complete
`MigrationPlan`; safe begin and permit extraction compare it before claiming a
control or taking the `ManuallyDrop` authority. Mismatch returns the intact
witness. Exact duplicate controls cannot be distinguished by the current
format, but the unsafe plan/witness/control contracts explicitly require one
authenticated live or recoverable control per source generation.

An independent two-control probe used the same source with different
transaction, target, and authority. Cross-permit extraction returned
`SourcePlanMismatch`; the original witness then extracted exactly once with its
own permit. Safe plan retargeting was rejected while the control remained
`Uninitialized`.

## Minor follow-up

The existing regression combined multiple mismatched plan fields. Narrow
transaction-only, target-only, authority-only, and same-source safe-retargeting
cases were requested to make future comparison regressions easier to localize.
This was test coverage only; current behavior was correct.

## Validation

Rust 1.96.0 and 1.85.0 both passed migration integration 10/10, migration units
3/3, doctests 8/8, strict all-target Clippy, strict rustdoc, and the schema
migration example.
