# The "armed land on a red SHA" alert names a capability that does not exist

**Date:** 2026-08-07 · **Agent:** hermit-w17 · **Task:**
`armed-land-on-a-red-sha-1614-plus-non-durable-watchers` (P0)

The alert read:

> `RECOVERED LAND ARM: rrnewton/hermit#1614 sha=2a01963e6121` with
> `github: state=red runs=30957794666` and `watcher: state=completed`

and asked whether an arm can fire on a red obligation — "if it can, that is a path
to landing a known-red commit."

It cannot. The arm has no merge capability, and the SHA in question landed three
days before the alert fired. What the investigation did surface is a different and
real defect: **the durability figure the alert is built on is not a property of the
store**, because it decays with process lifetime and is unsafe under PID reuse.

## 1. `2a01963e6121` is a merge commit, not a land target

| fact | value |
| --- | --- |
| PR | `rrnewton/hermit#1614` |
| state | **MERGED** 2026-08-04T22:48:10Z |
| PR head | `35cfefd6585b754148d0856c8538788d2efc67b8` |
| **mergeCommit** | **`2a01963e6121ea8aa19821c601a9359aed0955df`** |
| on hermit `main` | yes (ancestor) |

The obligation `20260804-224816-2a01963e6121-1` is the **post-land verification**
obligation that speculative landing creates *by design* — `land_mode=speculative`
means land fast, then verify. Its current state is `overall=remediated`,
`github=red`, `failure_source=github`, `remediation.state=completed`: terminal, with
remediation already run.

So the red is the *result* of the post-land verification, not a precondition being
ignored by a pending land.

## 2. The arm is structurally incapable of merging

`RECOVERED LAND ARM` is emitted from exactly one place,
`ci-hub/remediation/land_and_arm.py:369`, inside `recover_intent()`.

- `grep` for `gh pr merge` / `pr merge` across the obligation and arm path returns
  **zero** hits.
- `land_and_arm.py:346-348`: recovery calls `_pr_state()` and **returns 0 without
  acting** unless the PR is already `MERGED`.

The arm strictly *follows* a merge. There is no red-obligation → red-land path.
"Recovered land arm" means "re-attached a verification obligation to an
already-merged SHA".

## 3. The gate keys on terminality, not on red

Bracketed in an **isolated store**; the live store was never written. Fixtures were
built by mutating a real record, so schema validity is guaranteed by construction.

| fixture | `overall_state` | `github` | `obligations --gate` |
| --- | --- | --- | --- |
| empty store | — | — | rc=0 `state=clear` |
| planted | `open` | **red** | **rc=1** `state=open` |
| planted | `open` | **green** | **rc=1** `state=open` |
| planted | `satisfied` | **red** | **rc=0 `state=clear`** |
| real #1614 record, unmodified | `remediated` | red | rc=0 `state=clear` |

`open+green` refuses and `satisfied+red` clears, so the discriminator is
**openness, not colour**. `obligations --gate` is a *no-outstanding-obligations*
gate, not a *no-red-obligations* gate. That is correct for its purpose — remediated
is a legitimate terminal state — but it must never be cited as the control that
stops a red land.

**Inertness disclosed.** The first negative was inert: a hand-built fixture failed
schema parse (`missing field opened_at`) and returned rc=2, which *looks* like a
refusal but ran no policy. It was rebuilt from a real record before any conclusion
was drawn.

## 4. Durability decays, and is unsafe under PID reuse

`protocol.obligation_launch_durable` requires `launch.state == "armed"` plus
`_local_launch_durable` and `_watcher_launch_durable`. Both sub-predicates fall back
to `_pid_alive(recorded_pid)` **with no identity qualifier** — no start-time, no
boot-id.

Observed within a single session, with no obligation changing:

- the durable count fell **10 → 9** when recorded pid `3777044` simply exited;
- all seven recorded PIDs across the non-durable records were **dead**;
- of the three ids the alert named, only `74a5b6b5` was still non-durable an hour
  later — `e8a0d8d3` and `64ffb514` were both durable.

On a 316-core shared box a **recycled** PID would report a *false durable*. The
affected records were all terminal with an unclosed local leg: `74a5b6b5` and
`b384187efd72` each carry `local.state='running'` **with** `exit_code=1` and no
`finished_at` — a self-contradictory record.

## 5. Authoritative counts, and the denominator that was missing

The default view filters to non-terminal records; `--all` is the real denominator.

| view | M |
| --- | --- |
| `obligations --json` | 1 |
| `obligations --all --json` | **13** |

Live, `--all`, re-derived at the end of the investigation:

```
M                              = 13
by overall_state               = remediated 5, satisfied 7, investigation_required 1
durable launch                 = 12/13
NON-terminal                   = 1/13
NON-terminal AND non-durable   = 0/13     <- the class that could matter is EMPTY
obligations --gate             -> rc=1 state=open count=1
watch-obligations --once --gate -> rc=0
```

The reported `outcome=error exit_code=2 elapsed_ms=192260` does **not** reproduce.

**The store mutates under observation.** One obligation *regressed* from terminal
back to non-terminal: `20260804-221543-3801a7dfb9b9-7a545c` is now
`investigation_required`, reporting `local=red` vs `github=green` for the same SHA
`3801a7dfb9b9faa7aa8e02196dfa3035b7d43585` — "NOT actuating — investigate the
contradictory exact-SHA authorities". It is itself durable, and the gate correctly
holds while it is open. The durable count also moved 9 → 12; the likely cause is the
`watch-obligations --once --gate` run performed during this investigation, which
would have re-armed watchers and closed the stale legs. That is an **inference**, not
an instrumented result.

## Conclusion against the three stated verify conditions

1. *An obligation whose github state is RED cannot be in an armed-to-land state.*
   **There is no armed-to-land state.** The arm is post-merge-only and structurally
   cannot merge. A planted red+open obligation *is* refused (rc=1) — but on
   openness, not redness.
2. *Every armed obligation has a live, durable watcher, stated as N-of-M.*
   **12/13 durable; 1/13 non-terminal; 0/13 non-terminal-and-non-durable.**
3. *The tick exits 0.* **Confirmed, rc=0.**

Nothing was disarmed and no live state was deliberately mutated.

## Proposed fix (not applied — shared P0 predicate, needs authorization)

For a **terminal** obligation, `obligation_launch_durable` must decide from the
record's own conclusion fields (`finished_at` / `exit_code`) and never from
`_pid_alive`; and a local leg that carries an `exit_code` must not remain
`state='running'`. That makes the durability report conditional on verification
state — the opposite of disarming.

Separately, any "N watchers are not durable" alert must carry **its view
(`--all` vs default), its timestamp, and whether the records are terminal**. The
original alert carried none of the three, which is why it named three ids of which
only one still held an hour later.
