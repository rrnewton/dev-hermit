# Soft-inherited validation across a clean rebase: corroboration, and the debt

**Task:** `soft-inherited-validation-across-clean-rebase` · hermit-clone (opus-5), 2026-08-06
**Local, no egress.** Delivered: `ci-hub/landing/soft_inherit.py` +
`ci-hub/landing/test_soft_inherit.py` (16 tests). Full landing suite: **90 passed**.

## What already existed, checked before building

`rebase_wrapper.py` already implements the **driven** path correctly: its `rebase` subcommand *runs*
the rebase and **observes** cleanliness (`returncode == 0`), collects `conflicted_files()` on
failure, aborts, and refuses to soft-green. It already carries soft-green as a distinct confidence
level (`SOFT_ZERO_CONFLICT`, `SOFT_RESOLVER_JUDGED`), and `green_class.py` already models
soft-vs-hard from provenance. None of that needed rebuilding.

**The hole is the `record` subcommand.** It accepts `--conflicts` as an agent-supplied claim
**defaulting to `"none"`**, so an agent that resolved conflicts out of band and then calls `record`
receives `soft-green(zero-conflict)` on an unverified assertion. That is precisely what the task
warns about: *"soft-inherit becomes a way to launder an unvalidated head into a validated one —
the `locally-validated`-with-no-backing-run defect in a new costume."*

## The mechanism: evidence beats claim

`classify_rebase()` decides inheritance from three bases, in a deliberate order:

| basis | meaning | may inherit? |
|---|---|---|
| `observed` | the tool ran the rebase and watched it succeed | **yes** |
| `corroborated` | nobody watched, but `git patch-id` proves the branch's own patches are unchanged | **yes** |
| `claimed` | an agent said so and nothing checked | **no** |

`git patch-id` is what makes *"the content is unchanged"* checkable rather than asserted: a rebase
**preserves patch-ids while changing every SHA**, so comparing SHAs would report a total change and
comparing trees would miss an added commit. Compared as a **multiset**, so a reordering that changes
nothing is not read as a change. The primitive was already in `green_class._patch_ids`; this reuses
the idea rather than reinventing it.

## The negative test the task names

```python
classify_rebase(RebaseEvidence(
    claimed_conflicts=[],                                  # the agent says: clean
    patch_ids_before=[P1, P2], patch_ids_after=[P1, P3]))  # the artefact says: not
# -> NO_INHERIT, basis=corroborated, reason contains "CONTRADICTED"
```

Three more refusals in the same family:

- **observed conflicts beat a clean claim** — the record carries `claim_contradicted`
- **a bare claim with nothing to corroborate it does not inherit** (`"laundered"` in the reason)
- **reported conflicts are believed even when patch-ids match** — a resolution can be
  patch-id-preserving (taking one side wholesale), and the conservative answer is the one that does
  not inherit

And the case I think matters most, because the tempting implementation guesses:

> **observed clean but patch-ids moved → `REFUSED`, not resolved either way.**
> The observation and the artefact disagree; picking one would be inventing a fact.

## The debt is queryable

`soft_inherit.py --records <json>` answers *how many commits on main are soft, and since when* —
and **exits 1 while any debt is outstanding**, so it cannot read as success:

```
  [OUTSTANDING] aaaaaaaaaaaa landed 2026-08-04T10:00:00Z (soft-inherited from 111111111111, basis=observed)
  [OUTSTANDING] bbbbbbbbbbbb landed 2026-08-05T09:00:00Z (soft-inherited from 222222222222, basis=corroborated)
  [OUTSTANDING] cccccccccccc landed 2026-08-05T11:00:00Z (soft-inherited from 333333333333, basis=observed)
soft commits on main: 3  (outstanding 3, upgraded 0, redeemed 0)
OLDEST OUTSTANDING: 2026-08-04T10:00:00Z -- a soft green on main is a debt, not a state to settle into
exit=1
```

Both discharge routes from the owner's design are implemented and distinguished:

- **UPGRADED** — a full green recorded *at that commit*
- **REDEEMED** — a later full green *on main*, which requires the green to be **strictly later**; a
  test pins that an *earlier* green does not redeem, because it says nothing about a commit that had
  not landed yet

With both routes exercised, the same corpus reports `outstanding 0, upgraded 1, redeemed 2` and
exits 0. `oldest_outstanding` is always reported, because one soft commit outstanding for days is a
different situation from five from this hour, and a bare count cannot tell them apart.

## Honest limits

- **The corroboration is not yet wired into `rebase_wrapper record`.** This module is the predicate
  and the debt query; the wrapper still accepts `--conflicts` on trust. Wiring is a small change —
  call `classify_rebase` with patch-ids derived from X and Z before writing `soft_green` — but it
  edits a file another agent has been active in today, and doing that unasked during a live drain is
  the wrong trade. **Flagged rather than done**, because an unwired predicate is the present-but-inert
  shape this lane keeps finding.
- **The debt query reads a supplied JSON, not the live ledger.** Deriving the soft set from the
  ledger needs the provenance fields (`validated_head_sha`/`inherited_from`) that **no producer
  writes yet** — measured earlier today: 0 of 585 rows carry them. So the query is correct and
  testable but has no live input until a soft producer exists. That ordering is deliberate and
  matches `green_class`'s own argument: the field must exist before the first producer, or adding it
  later is a fleet flag-day.
- **Patch-id corroboration is textual.** It proves the *patches* are unchanged, not that the result
  is semantically valid on a new base — a clean rebase onto a base that changed an API still
  compiles-or-not independently of patch identity. Soft-inherit is a **speculative** protocol by the
  owner's own framing; this module makes the speculation *observed* rather than *asserted*, not
  risk-free.

## Files

`ci-hub/landing/soft_inherit.py` (new) · `ci-hub/landing/test_soft_inherit.py` (new, 16 tests).
Uncommitted — egress down.
