# Rescue fan-out, 2026-08-07 — stranded in-flight work

Task: `rescue-in-flight-local-work-into-prs` (owner P0). Agent: `probe-tmux`
(claude-opus-5). Slot: `worktrees/rescue-fanout`.

`patches/` holds the **durable protection copy** of every stranded delta examined.
It was taken FIRST, before any worktree was touched, so a rescue could not lose
work it failed to land. `git diff HEAD` alone was not sufficient: untracked files
(`untracked/`) and unmerged `UU` conflict copies (`unmerged/`) are captured
separately because a plain diff would have dropped both.

## Denominator examined

| Population | Count |
| --- | ---: |
| Dirty children under `worktrees/<slot>/{hermit,reverie,liteinst2}` | 30 |
| — with tracked modifications | 26 |
| — untracked-only | 4 |
| Parent `rescue/*` branches | 84 |
| — `rescue/fsck-20260806/<sha>` | 33 |
| — `rescue/orphan-<sha>` | 49 |
| — named | 2 |

`hermit` and `reverie` have **zero** `rescue/*` branches; all 84 are in the parent
repo, and the 82 sha-named ones are fsck/orphan sweeps of parent history, not
stranded product work.

## The structural finding

**10 of 13 candidate branches were `ahead=0` against `origin/main` — their commits
had already landed.** The only value at risk was the *uncommitted working-tree
delta*. This confirms the task premise: a rescue branch cannot protect a dirty
tree.

Naive `git diff origin/main` is the wrong novelty signal here — these trees were
7–122 commits behind, so such a diff reads as reverting main's landed work.
Novelty was measured instead as *added lines in the uncommitted delta that are
absent from fetched `origin/main`*.

## Bases

* hermit `origin/main` = `d2cdd2317c643ca9ed4f2ff149e6505524cf2054`
* reverie `origin/main` = `038e993926e45514264d30367b70df9b6ac3b9b8`
* newest ancestry-confirmed green (LAST-GREEN-BASE, unused — main was usable) =
  `d53550510d1e7d13e84cc8af9bb90269e90b3f07`, validated 2026-08-05, profile=full

## Disposition

Full reasoning is in the task notes. Summary in `DISPOSITION.md`.
