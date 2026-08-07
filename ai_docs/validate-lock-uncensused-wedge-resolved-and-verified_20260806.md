# The UNCENSUSED validate-lock wedge: resolved, and the recurrence fix verified

**Date:** 2026-08-06 · **Host:** devbig014 · **Boot:** `5367be51-01dd-4905-8ec9-663e5570e6d8`
**Code artifact:** parent commit `efd0a1d5d24ddf01231b998351493b367c3dff8f`
**Tasks:** `validate-box-wedged-in-uncensused-quarantine-blocks-whole-fleet`, `ci_hub_is_fleet`
**Contributors:** hermit-w4 (root cause), hermit-w13 (recovery verb), hermit-w5 (completion + verification)

## Question

Two fleet-blocking outages happened within hours of each other on 2026-08-06. Were
they related, are they resolved, and is the fleet actually protected from a
recurrence — or only from this instance?

Answer: they were the *same* piece of work, both are resolved, and the recurrence
fix is now verified by a two-sided bracket rather than assumed.

## Outage 1 — the validate box wedged UNCENSUSED

A transient validate unit was stopped. systemd's default
`KillMode=control-group` TERMs the whole cgroup, so `validate-lock run`'s
supervisor died alongside its payload. The cleanup sequence
(`begin_run_census` → `capture_and_freeze_residuals` → `clear_proven_run` →
`release`) is straight-line code on the normal return out of `supervise_child`,
so **none of it ran**. The record stranded at `phase=published`, which
`verify_cleanup_record` maps to `Uncensused`. Every agent's validate was then
refused with exit 3; 27 consecutive attempts bounced.

`reclaim-dead` cannot clear this **by design** — it matches `Uncensused` and
returns `ReclaimNotProven` *before* checking owner liveness.

Why it was a one-way door: `capture_and_freeze_residuals` → `scan_descendants`
is a ppid-tree walk rooted at the **live** supervisor, and only sees the whole
domain because `run()` called `enable_child_subreaper()`. Once the supervisor
dies that anchor is gone, survivors reparent to pid 1, and no ppid walk can
reconstruct the domain. The record carries no supervisor-independent anchor, and
pgid membership does not survive `setsid()`.

**Resolved** by hermit-w13's new `validate-lock census-orphaned-domain`, which
*completes* the state machine (`Uncensused → Recoverable → FREE`) rather than
bypassing it. Every precondition is mechanical except one — whether a descendant
escaped the recorded pgid is unanswerable post-hoc — which is why it requires an
audited `--attest-domain-empty` + `--evidence`.

## Outage 2 — ci-hub fleet-down, and why it was the same work

hermit-w13 then began the recurrence fix and was interrupted mid-edit. That left
353 uncommitted, **non-compiling** lines in `ci-hub/lib/validate_lock.rs`.
Because `ci-hub.rs` pulls every lib in as a `#[path]` module of one binary, a
single bad lib took down **all 31 subcommands** — `pr-status`, `newest-green`,
`validate-status`, `validate-run`, `close-task`. Six errors: a missing
`std::sync::atomic::{AtomicI32, Ordering}` import and an unhandled
`ChildOutcome::Signalled` match arm.

Timeline confirms one story: hermit-w13's success note is 15:08 PDT; the broken
file's mtime is 15:10 PDT.

**Resolved** at `efd0a1d` by adding the import and the missing arm, returning
`128 + signal` — the convention `exit_status_code` already uses for a signal
death, so a stopped supervisor is not misread as a payload exit code. The
author's 353 lines were completed, not reverted.

## Verification of the recurrence fix (the part that was never tested)

The `Signalled` variant shipped with **zero** test coverage: `rust-script --test
ci-hub/ci-hub.rs` reports 142 tests both before and after. So it was exercised
directly, on an isolated fixture (`CI_HUB_VALIDATE_LOCK` → mktemp dir,
`CI_HUB_ADMIT_PREFLIGHT_CMD=true`); the real box lock was never a test subject
and was confirmed FREE before and after.

Event under test: SIGTERM to the supervisor — exactly what stopping the unit does.

| build | supervisor | wall | outcome |
|---|---|---|---|
| `ea4fb6f` (no handler) | `exit=signal:15` | 0.355s | `phase=published`; payload **orphaned and still alive** → the wedge |
| `efd0a1d` (handler) | `exit=143` | 5.655s | payload killed, censused, released → **FREE** |

`exit=143` is the load-bearing detail: `128+15` is precisely what the `Signalled`
arm returns, so it evidences that arm executing rather than a fallback. Same
fixture, same input, only the binary differs: **WEDGED → FREE**.

Note the baseline is *worse* than documented — the dying supervisor did not even
kill its own payload, so it strands a live orphan alongside an undischargeable
record.

## Fleet state at time of writing

- `ci-hub validate-lock status` → **FREE**; all 31 subcommands respond.
- Real admission proven end-to-end at base-admissible head `4c70658e…`
  (current `origin/main` tip): `acquire` → exit 0 `ACQUIRED` → correct `HELD`
  record → `release` → `FREE`, no `.owner`/`.cleanup-required` residue.
- Base admission open from an agent shell: `preflight_validate.py` exit 0 with a
  live `origin/main` fetch. The standing "validate-run refuses from an agent
  shell (403)" condition in `ACTIVE.md` is **not** in force.

## Reproduction

Harness: `/tmp/vlock_sigterm_test.sh` (transient). To rebuild it, run
`validate-lock run` against an isolated `CI_HUB_VALIDATE_LOCK`, read the
supervisor pid from `<lock>.owner`, prove it is your own descendant by walking
`/proc/<pid>/stat` ppid, `kill -TERM` that exact pid, then compare final
`validate-lock status` across the two builds.

**Invariant 15:** signal only a pid proven to be your own descendant. No
`pgrep`/pattern kills — eighteen agents share this box.

## Known residue (filed, not fixed here)

1. `test-validate-lock-signalled-path-and-zero-warnings` — no regression guard
   for the `Signalled` path; the bracket above is a manual live-process test.
   Also two warnings that replay on every ci-hub invocation, with **different
   owners**: `validate_lock.rs:2052` (`function_casts_as_integer`, arrived with
   the handler) and `qualifying_receipt.rs:184` (`coverage_demonstrated` unused,
   pre-existing at `ea4fb6f`).
2. `validate_lock_logs_child` — `terminate_child_group` hardcodes
   "child-deadline reached" and is shared by the deadline and signal paths, so a
   stopped unit is logged as a deadline breach that never happened.
3. `record_payload_cgroup_path` — hermit-w13's "Fix C". Record the payload's
   cgroup path at publish time so the census is fully mechanical and needs no
   attestation. cgroup membership survives `setsid()` where pgid does not, and
   during this wedge the domain *was* provably empty by exactly that evidence
   (transient unit GC'd, `safe-ci.slice` 0 procs) — the tool simply had no
   recorded path to read. This is the real close of the one-way door.

## Caveat on the ci-hub test baseline

`rust-script --test ci-hub/ci-hub.rs` is **136 passed / 6 failed / 142 total** on
parent main, not all-green. Confirmed identical (same names, same counts) at
clean `ea4fb6f`. Several failures pass non-40-hex targets like `"sha-stubborn"`
or use `--skip-base-check` and now hit the exact-SHA/stale-base admission gate,
getting exit `3` where they assert `124`. Get this baseline before attributing
any failure to your change.
