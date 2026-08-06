# Stale backup branches: nothing left to prune, and the checker was the real bug

**Task:** `prune-stale-local-backup-branches-blocking-pin-check`
**Date:** 2026-08-06
**Agent:** `egress-probe2` (opus-5)
**Change:** `39c89a0b0f6407efaa0f4e21f28aab84ebb4cb78` — `scripts/check-agent-utils-pin.rs`
(+350/−7) and one `Makefile` help line. Parent `main`, local only; egress is 403.
**Branches deleted: ZERO.**

---

## 1. The prune half: premise refuted

`make check-agent-utils-pin` cannot run at all right now — it fetches first, and
`with-proxy git fetch` returns `CONNECT tunnel failed, response 403` (exit 2). So
I ran the checker's *exact* query directly against the last-fetched `origin/*`:

```
git -C agent-utils rev-list HEAD --branches --not --remotes=origin   →  5 commits
```

Full attribution — commit → branch → live git worktree:

| commit | date | branch | worktree |
| --- | --- | --- | --- |
| `0cb9576` | 08-05 | `worker-thread-exception-fails-loudly` | `scratch/au-worker-exc` |
| `7dc20de` | 08-05 | `codex/cpu-timeout-platform-multiplier` | `scratch/au-cputo-mult` |
| `bcb82a6` | 08-05 | `recovery/primary-mode-flip-20260804` | **none** |
| `3c64150` | 08-03 | `codex/cgroups-cap-land` | `scratch/recovered-agent-utils-primary-20260805/au-cap-land` |
| `d9d9437` | 08-03 | `codex/cgroups-cap-land` | (same) |

**Four of five are held by live worktrees** — in-flight work, unpushed only
because egress is down. (`7dc20de` is my own commit from the immediately
preceding task.) A name sweep for `backup|stale|old|tmp|wip|delete` returns
nothing; the single `archive` hit is `codex/pr-landing-planner-archive-plans`, a
feature branch with upstream `origin/main` and zero unpushed commits.

**The stale backup branches this task was filed about are already gone.** They
were pruned preserve-first on 2026-08-04 (`backup-enum-onto-ec4ddf0-8eacb56`,
`backup-enum-redesign-9bef79f`; recovery refs pushed to `origin/recovery/*`
first; count 12 → 2). Nothing has accumulated since.

Two independent reasons deletion was also *unsafe* today, had any candidate
existed: the dispatch's own guard ("never delete anything unpushed/unbacked"),
and the fact that egress being 403 makes the 08-04 preserve-first protocol
(push to `origin/recovery/*`, verify by `ls-remote`, *then* `git branch -D`)
impossible to execute.

## 2. The one genuinely stranded ref, characterised

`recovery/primary-mode-flip-20260804` @ `bcb82a6` — "Preserve recovered cpuset
allocator mode change", authored 2026-08-05. `git branch --contains` lists only
that branch, and `git cherry origin/main` reports `+` (not patch-equal upstream),
so it is the sole copy.

Its entire unique content is **one file-mode bit**:

```
:100644 100755 0cd9740 0cd9740 M  py/safe_ci_dag_runner/cpuset_allocator.py
```

0 insertions, 0 deletions. And the surrounding facts:

* `origin/main` has since **rewritten that file's content** (blob `853f9b1`, vs
  the branch's `0cd9740`) and kept it `100644`.
* `origin/main`'s version is a plain library module — it opens with a docstring,
  no shebang.
* 22 of the 23 non-symlink files in `py/safe_ci_dag_runner/` are `100644`; the
  only `100755` is `__main__.py`, the actual entrypoint.

So the preserved artifact is `+x` on a non-executable library module, contrary to
the package norm, against a superseded version of the file. It looks like an
accidental mode change that was recovered mechanically rather than a deliberate
fix.

**I did not delete it**, because it is another agent's explicitly-named recovery
ref, it is unpushed and unbacked, and I cannot preserve it remotely. But the
owner's decision is now a one-liner, and nothing of substance is at risk either
way — if the exec bit ever turns out to be wanted it is
`git update-index --chmod=+x`. Recovery command if it is deleted and later
regretted: `git branch recovery/primary-mode-flip-20260804 bcb82a63d842dbdf1249dcc86d6b72603ef3bac8`
(valid while the object is unreferenced-but-unpruned).

## 3. The valuable half: the checker was the actual defect

The task called this out itself — *"should the checker DISTINGUISH 'unpushed
commits on an ACTIVE branch' from 'abandoned backup branches'? … the state space
is wider than the indicator."* Today's data proves the point exactly: **5 of 5
unpushed commits are legitimate, and the old rule failed on all five.**

`local_unpushed_commits` collapsed two conditions that demand opposite responses:

* **IN FLIGHT** — on a branch some worktree has checked out. Someone is working
  on it; during an egress outage it *cannot* be pushed. Normal.
* **STRANDED** — on a branch no worktree holds. Nothing is keeping this work; one
  `git branch -D` and it is unrecoverable. Actionable.

Failing on the union makes the checker permanently red for the benign case, which
trains readers to ignore it — and then a real stranded commit looks identical to
the noise. A signal dies not by breaking but by being permanently slightly wrong.

### What changed

`scripts/check-agent-utils-pin.rs` now attributes every unpushed commit to the
branches carrying it and to the worktree (if any) holding each branch, and
reports three counts plus the branch names:

```
  local_unpushed_commits=5
  unpushed_in_flight_commits=4
  unpushed_stranded_commits=1
  unpushed_unattributed_commits=0
  in_flight_branches=codex/cgroups-cap-land (2 commit(s), …/au-cap-land), …
  stranded_branches=recovery/primary-mode-flip-20260804 (1 commit(s))
```

Only **stranded** and **unattributed** fail. Two judgement calls worth recording:

* A commit reachable from **both** a held and a parked branch counts as
  in-flight. Something is still holding it, so it is not at risk; calling it
  stranded would raise an alarm no action can clear — the same signal-killing
  pattern in miniature.
* A commit on **no local branch at all** (detached HEAD only) gets its own
  `UNATTRIBUTED` class instead of being folded into either, because no branch
  name can be offered as the place to fix it. It still fails.

The failure message names the branches, worktrees, counts and SHAs, and states
the two available actions ("Publish or deliberately retire"), so the number is
never anonymous.

### Verification

Live, on this workspace — the same run exercises both classes, so the classifier
is demonstrably discriminating rather than returning a constant:

```
unpushed_in_flight_commits=4   (3 branches, each with its worktree path printed)
unpushed_stranded_commits=1    (recovery/primary-mode-flip-20260804)
ERROR: 1 local commit(s) are unreachable from every origin/* ref AND from every
       worktree — nothing is holding this work, so a `git branch -D` loses it.
```

The residual `state=drift` is **real, separate drift**: the agent-utils checkout
is detached and the pin is 64 behind `origin/main`. That needs a deliberate
gitlink bump and is not part of this task.

Unit tests, both directions bracketed — **10 pass** (7 new, 3 pre-existing):

| test | asserts |
| --- | --- |
| `splits_in_flight_worktree_branches_from_stranded_ones` | the exact 5-commit shape observed today → 4 in-flight, 1 stranded |
| `work_in_flight_on_worktree_branches_is_not_stranded` | a fleet mid-outage with every branch held is **clean** (the old rule failed it) |
| `an_abandoned_branch_with_no_worktree_is_stranded` | the negative half — genuinely abandoned work still fails, so the split is not just a way of never failing |
| `a_commit_held_by_any_worktree_branch_is_not_stranded` | held-and-parked → in-flight |
| `commits_on_no_local_branch_are_unattributed_not_dropped` | detached-HEAD commits are not silently dropped |
| `renders_branches_with_counts_and_worktrees` | the message carries names, counts, paths |
| `parses_worktree_porcelain_branch_mapping` | a *detached* worktree holds no branch and must not appear |

Repo gates: `python3 -m unittest discover -s scripts` → 37 pass;
`scripts/check-harness-help.py` → ok (24 safe-probe invocations, no side effects);
`--help` exits 0.

## 4. Residue

1. **Nothing pushed** — egress 403. `39c89a0` is on local parent `main` only.
2. **`recovery/primary-mode-flip-20260804` still stranded**, by design. Once
   egress returns: either push it to `origin/recovery/*` (matching the 08-04
   protocol) or retire it after confirming the `+x` bit is unwanted (§2 says it
   almost certainly is). Until then the checker correctly flags exactly this one
   thing.
3. **Real pin drift is untouched**: checkout detached, gitlink 64 behind
   `origin/main`. Deliberate bump, separate decision.
4. **Scope note.** The dispatch said "reverie pin-check"; there is no such
   checker — `scripts/check-agent-utils-pin.rs` is the only tool computing
   `local_unpushed_commits`, and it is what the task title names. Reverie has 488
   local branches and hermit 2574, many `archive/*` and `cleanup-preserve/*`;
   none of them feeds this check. If branch-population hygiene in those repos is
   wanted, it is a different task and needs its own preserve-first design.
