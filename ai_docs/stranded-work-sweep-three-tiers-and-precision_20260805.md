# Stranded-work sweep: three tiers, two measurement traps, and the precision problem

**Date:** 2026-08-05
**Task:** `sweep-for-work-stranded-uncommitted-by-the-commit-is-destructive-rule`
**Status:** implemented locally; committed to the parent, **not pushed** (egress 403)
**Scope:** local analysis + implementation + verification. No egress, no product change.

## Premise check first: instance 3 is stale

The task's headline is that `ci-hub/validate/attribute_reds.py` and its CLI wiring are "on
NEITHER HEAD NOR origin". **That is no longer true.** Verified locally:

| location | `ci-hub/validate/attribute_reds.py` |
|---|---|
| working tree | PRESENT |
| `HEAD` | PRESENT |
| `origin/main` | PRESENT |

It landed after the task was filed. Instances 1 and 2 are historical and unrecoverable from
here. The *capability* the task asks for is still missing, so that is what was built — but no
one should go hunting for instance 3.

## Strandedness is three tiers, not one

They are destroyed by different things, so collapsing them into one number hides the risk.

| tier | what it is | what destroys it | measured now |
|---|---|---|---|
| **T1 UNCOMMITTED** | untracked/modified in a working tree | `git clean`, worktree reclaim, another agent's `git add -A` | 24 checkouts, 11 untracked paths |
| **T2 UNPUSHED** | committed locally, on no remote | removal of the checkout | 15 checkouts, **65 commits** |
| **T3 UNLANDED** | a CLOSED task's artifact is not on `origin/main` | being forgotten | **82** paths across 287 tasks |

T2 is currently the dominant tier purely because egress is down: 28 of those 65 commits are in
the parent alone. Every hour of outage adds more.

## Trap 1 — the shared object store (an ~870x overcount)

The obvious phrasing for "unpushed work in this worktree" is
`git log --branches --not --remotes`. It is wrong. Linked worktrees **share one object store**,
so `--branches` enumerates every stale local branch in the whole repository and returns the
*same* number for every worktree. Measured here:

```
--branches phrasing : 1050 per checkout, 54,706 total   <- meaningless
HEAD-anchored       :   63 across 14 checkouts          <- the real number
```

A ~870x overcount, and it looks precise. The tool anchors to `HEAD` and never to `--branches`.
This is the "interrogate a surprising ratio's denominator first" rule paying for itself: 54,706
stranded commits would have been an alarming and entirely fictional headline.

## Trap 2 — a stale `origin/main` is not evidence of absence

With egress down, `origin/main` is frozen at the last fetch. "Path not on origin/main" may
simply mean "landed after our last fetch". So **presence is definitive; absence is not.** An
absence is reported as `UNVERIFIABLE` unless the ref is fresh (default 6h), and the report
states the ref age and whether absence counts as evidence at all.

## The precision problem, which is the whole ballgame

The task states the acceptance bar plainly: *"a checker that flags everything is useless and
gets disabled"*. The first working version flagged **278 of 414** references — 67%. Useless.
Four distinct precision bugs, each found by running it against the real corpus:

1. **Sentence punctuation.** `see ai_docs/x.md.` yields the path `ai_docs/x.md.` — guaranteed
   phantom. Trailing `.,;:` are stripped.
2. **Prose globs and brace lists.** `ai_docs/*.md`,
   `experiments/q/{README.md,metadata.json,results.csv}` are shorthand, not paths. Rejected.
3. **Absent from the tip ≠ never landed.** A doc written in July, landed, then renamed or
   superseded is absent today but was *never stranded* — it reached the repo, which is the
   only question. The check now consults history, not just the tip. This single fix moved
   ~140 references out of the flagged set.
4. **Git-ignored paths are local BY DESIGN.** The repo's own experiment hygiene writes bulky
   evidence under `ignored/`. Both live `UNCOMMITTED` hits were `ignored/*.log` evidence files.
   They get their own `IGNORED_LOCAL` disposition rather than a false alarm.

Final: **266 LANDED (not flagged), 2 IGNORED_LOCAL, 82 MISSING** across 287 closed tasks /
258 distinct paths. The task asked for the landed population to be confirmed unflagged with N
stated: **N = 266.**

Spot-checked three survivors by hand — `experiments/lulesh-openmp/run.sh`,
`experiments/ninja-strict/run.sh`, `ai_docs/language-coverage.md`: all are absent from
`origin/main`, have **zero** commits touching them in all of history, and are absent from disk.
True positives.

One subtlety worth naming: the history check must be scoped to `--remotes`, **not** `--all`.
With `--all`, a local-only commit counts as evidence of landing, which launders a
`PENDING_PUSH` into a `LANDED` and hides the exact condition the tool hunts. Both directions
are bracketed by tests.

## Four bugs the tests caught before shipping

The scan and rescue paths are where silent data loss lives, and the tests earned their keep:

1. **Porcelain leading-space corruption.** `git status --porcelain` uses a *space* in column 1
   for an unstaged modification, so `.strip()`-ing the output eats it and every path loses its
   first character: ` M AGENTS.md` → `GENTS.md`. Invisible in a count; fatal to a rescue, which
   would look for a file that does not exist and record it as skipped. Caught live against the
   real parent tree.
2. **Rescue silently skipped untracked directories.** `git status` collapses a wholly-untracked
   directory into one entry, `sub/`. A file-only copy loop dropped every file beneath it —
   data loss in the one tool whose job is preventing data loss. Directories are now copied whole.
3. **T2 false positive on a remoteless checkout.** With zero remote refs, `--not --remotes`
   excludes nothing and the entire history reads as unpushed. That is a missing denominator,
   not a finding; it is now reported once as `no_remote_refs`.
4. **`--all` vs `--remotes`** in the history check, as above.

## Safety contract

- **Read-only** on anything another agent may own. Every git read uses `--no-optional-locks`,
  so a sweep across 92 checkouts cannot contend on an `index.lock` with a live agent mid-commit.
- **No `git clean`, `reset`, `checkout --`, or `stash`** — enforced by a test that greps the
  source for those verbs, not merely by intent.
- **Rescue is additive.** It `copy2`s files into a quarantine tree and captures unpushed
  commits as a verified `git bundle`; the source is left byte-identical. Verified live against
  `worktrees/227b/reverie`: status hash before == after.
- Attribution is a **hint, never authority** — slot, ACTIVE.md row, branch, last-commit author,
  newest mtime. `ACTIVE.md` is machine-local and often stale; stranded work hides precisely in
  the gap between the three registries, which is why discovery walks the filesystem for `.git`
  entries instead of trusting any registry.

## Verification

- `stranded_sweep.py selftest` → **PASS, 22 tests**, including both required brackets:
  planted-absent artifact → flagged; landed artifact → not flagged.
- Live worktree sweep: 92 checkouts, 34 flagged.
- Live artifact check: 287 closed tasks, 266 LANDED unflagged.
- Live rescue: source provably untouched.

## Limits and what remains

1. **Not wired into the hourly tick.** The task asks for recurrence, which is the difference
   between a capability and a sweep. `worktrees` and `artifacts` both exit non-zero when they
   find something, so they are ready to be driven; the tick integration is not done here.
2. **Only `origin/main` is consulted for T3.** An artifact landed on a *feature branch* that
   was never merged reads as MISSING. That is arguably correct, but it is a policy choice.
3. **The 82 MISSING are not triaged.** They are a work list, not a verdict: some are genuinely
   lost, some were never created despite the note claiming otherwise, and distinguishing those
   needs a human or the original agent.
4. **Not pushed.** Egress is 403, so this artifact and the tool are local-only.
