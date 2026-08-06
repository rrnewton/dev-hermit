# Giving tg the ancestry check: the mechanism works; the population number does not yet

**Task:** `tg-cannot-verify-landing-which-is-why-the-directives-ledger-exists` · hermit-clone (opus-5), 2026-08-06
**Local, no egress.** Delivered: `ci-hub/directives/tg_landed.py` +
`ci-hub/directives/tests/test_tg_landed.py` (15 tests). Directives suite: **24 passed**.

## The gap, restated in one line

tg's status is **asserted by an agent**. The directives ledger's status is **derived** by checking
ancestry against a freshly-fetched target. That difference — not the storage — is the whole reason
two systems exist.

`tg_landed.py` teaches the ancestry check to tg's own data without forking tg: it reads the task DB
read-only via `tg sql`, extracts the implementation references an agent named in its notes (40-hex
commits, PR numbers), and derives a landed state per task with
`git merge-base --is-ancestor`. **The derived state never inherits the asserted one.**

`PARTIAL` — a task naming several commits of which only some landed — is reported rather than
rounded, because rounding a half-landed obligation is how it reads as done. It is the state tg
cannot express at all.

## Verify by mutation, both directions — done

15 tests, covering exactly the bar the task set:

- an ancestor reports **landed**
- a non-ancestor reports **not_landed while the task is tagged `implemented` and status `closed`** —
  the derivation contradicting both assertions is the point
- **absent-from-checkout is `UNVERIFIABLE`, not `not_landed`** — conflating them would manufacture
  false negatives on any partial clone
- some-landed-some-not is **PARTIAL**, not rounded
- a task naming no commit is `NO_REFERENCE` — "the asserted status is all there is", which *is* the gap
- a stale target **annotates every positive derivation** and the report says so in its header
- a short SHA is not accepted as a reference (a prefix cannot be compared safely)
- **positive control**, the third leg the task names explicitly: given three landed commits the
  reporter says three landed and gap 0 — it is not a checker that says "no" to everything
- **self-consistency**: the state counts must sum to the population (see the regression below)

## The population run, and why its number is not yet a measurement

Over the full live population, target **not freshly fetched** (no egress):

```
examined       : 810      derived: landed 28 | partial 319 | not_landed 241
asserted impl  : 810               unverifiable 114 | no_reference 108
DERIVED LANDED : 28       consistent: True (sums to 810)
ASSERTION GAP  : 782
```

**I am not reporting 782 as the assertion gap.** Before believing it I checked whether the
unresolved SHAs were actually missing — they are mostly in a *different repository*:

| sampled | result |
|---|---|
| 25 SHAs "absent from hermit" | **16 exist in the dev-hermit parent**, 3 in `reverie/` — 19/25 are simply elsewhere |
| 25 SHAs "unlanded in hermit" | **7 exist in `reverie/`** — checked against the wrong repo's main |

So the checker resolves against **one** checkout while the population is **multi-repo** (hermit,
reverie, dev-hermit parent). That inflates `partial`, `not_landed` and `unverifiable`, and it
explains why `partial` is the largest bucket: a task naming a landed hermit SHA *and* a reverie SHA
lands in `PARTIAL` purely as an artefact.

**Correct disposition: the mechanism is verified, the population number is a lower bound on landed
and an upper bound on the gap.** The fix is per-reference repo resolution — try each SHA against
hermit, reverie, and the parent, and record which repo answered — not a re-run of the same query.

## Two corrections to my own work, disclosed

1. **A denominator bug in the reporter itself** — this task's own defect class. `tg sql` renders one
   row per line, so multi-line notes were parsed as extra rows: 40 tasks classified as **51**. Fixed
   by stripping newlines inside SQL; a `test_report_counts_sum_to_the_population` regression now
   pins it, and the full run reports `consistent: True`.
2. **A false rationale in my own docstring.** I wrote that the exact JSON-element tag match was
   needed because a substring match "returns 808 vs ~106 genuinely tagged". Measured: **both forms
   return the same count (812)**. The 106 was the owner's 2026-08-04 figure and the population has
   simply grown; it was never a substring artefact. The docstring now says so, and keeps the element
   form for a stated forward-looking reason instead of a false past one.

Also worth recording: the count moved **808 → 810 → 812 during this session** as other agents tagged
work. The denominator is live, not fixed — any figure quoted from it needs its timestamp.

## On consolidation (items 2 and 3 of the design)

The task's step (2) — "the ledger becomes a view over tasks tagged `owner-directive`" — is
deliberately **not** done. It is a data migration that would retire a working, owner-visible store
on the strength of a derivation whose population resolution is still single-repo (above). Doing it
now would replace a correct ledger with an artefact-ridden view. Sequence: fix repo resolution,
reconcile the derived states against the ledger's existing `satisfied 8 / partial 3 / open 7 /
not_landed 1 / missing_owner 1`, and only then collapse.

Step (3) — keep the hourly tick — needs no work here: the value is that *something re-derives on a
schedule*, and `hermit-health-tick.timer` plus the relay restored earlier today already provide that
substrate.

## Honest limits

- **Single-repo resolution**, measured above — the one thing to fix before any number is quotable.
- **No fresh fetch** (egress down), so every positive derivation carries `[STALE TARGET]` and the
  CLI exits non-zero. That is deliberate: a stale answer must not read as success.
- **PR references are extracted but not resolved.** `mergeCommit.oid` needs `gh`; the extraction is
  there so the resolution is a small addition, but today only 40-hex commits are dereferenced.
- **Nothing calls this yet.** It is a reporter, not a gate — flagged rather than left implicit.

## Files

`ci-hub/directives/tg_landed.py` (new) · `ci-hub/directives/tests/test_tg_landed.py` (new).
Uncommitted — egress down.
