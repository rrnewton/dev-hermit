# Archived worktree slots

Append completed or reclaimed slot state here before removing its physical
checkout. Branch refs are retained unless a separate, explicit cleanup proves
they are merged or otherwise disposable. Exact stash SHAs preserve any dirty
state recovered during stale-slot cleanup.

| Path | Repository | Branch / state | Final HEAD | Closed | Disposition / recovery |
| --- | --- | --- | --- | --- | --- |
| `worktrees/slot03` | parent | `devbig-lead-slot03` | `2a578c0` | 2026-07-21 | Clean parked slot reclaimed; branch retained. |
| `worktrees/slot04` | parent | `devbig-lead-slot04` | `2a578c0` | 2026-07-21 | Clean parked slot reclaimed; branch retained. |
| `worktrees/ci-fix` | Hermit | `codex/fix-integration-ci` | `936f5ee` | 2026-07-21 | Clean stale checkout removed; branch retained and published. |
| `worktrees/hermit-legacy/slot01` | Hermit | `impl-debugging-fixes-slot01` | `8a65252` | 2026-07-21 | Clean stale checkout removed; commit is reachable from `main`. |
| `worktrees/hermit-legacy/slot02` | Hermit | `impl-port-tests-wave1-slot02-integrate` | `53649eb` | 2026-07-21 | Clean stale checkout removed; commit is reachable from `main`. |
| `worktrees/hermit-legacy/slot03` | Hermit | `impl-enhance-validate-slot03` | `c3d7014` | 2026-07-21 | Dirty stale checkout removed after saving stash `b47d2b277e5ce39e72860505380d265a6741d8a1`. |
| `worktrees/slot-batch-b` | Hermit | `codex/test-port-batch-b` | `0a62ce5` | 2026-07-21 | Clean stale checkout removed; branch retained and published. |
| `worktrees/slot-batch-c` | Hermit | `impl-test-port-batch-c` | `c3d7014` | 2026-07-21 | Clean stale checkout removed; branch retained. |
| `worktrees/slot-batch-d` | Hermit | `impl-test-port-batch-d` | `c83df41` | 2026-07-21 | Clean stale checkout removed; branch retained and published. |
| `worktrees/slot-bug-70` | Hermit | `impl-fix-bug-70` | `fb7f073` | 2026-07-21 | Clean stale checkout removed; branch retained and published. |
| `worktrees/slot-bug-73` | Hermit | `impl-fix-bug-73` | `a9e2ca5` | 2026-07-21 | Clean stale checkout removed; branch retained and published. |
| `worktrees/slot-port` | Hermit | `impl-test-porting-wave2-slot-port` | `c3d7014` | 2026-07-21 | Dirty stale checkout removed after saving stash `f6d5fb9c8319100291e59579b9475548ae54ccf4`. |
| `/tmp/hermit-slot-batch-c` | Hermit | `impl-test-port-batch-c-work` | `ea53f08` | 2026-07-21 | Clean temporary checkout removed; branch retained and published. |
| `/home/newton/work/hermit` | Hermit | missing detached checkout | `74d7a1f` | 2026-07-21 | Prunable registration removed; no physical directory existed. |
| `worktrees/slot-bug-74` | Hermit | `codex/fix-bug-74` | `7cd4621` | 2026-07-21 | Closed task checkout reclaimed after green CI; branch retained and published in PR 4. |
| `worktrees/hermit-legacy/slot04` | Hermit | `impl-runner-namespaces-slot04` | `ba8153c` | 2026-07-21 | Completed dirty checkout removed after saving stash `1b7e762db0fdc457de4e162edd15423e57c88714`. |
| `worktrees/slot03` | parent / Hermit | `impl-expand-selfhosted-ci` / `impl-expand-selfhosted-ci-slot03` | `7b1a5ea` / `fbb6771` | 2026-07-21 | Completed 17 GB slot reclaimed; Hermit commit is published on `origin/main`. |

| `worktrees/slot86` | Hermit | `impl-ensure-agents-md-tags` | `0671dfdbc4669d957197cfb1669d38cf51c2bd9c` | 2026-07-23 | Fast-forwarded to rrnewton/hermit main; AGENTS.md-only diff; exact tag grep and git diff --check passed. |

## Archived remote branches (rrnewton/dev-hermit)

Cleanup of accumulated agent branches on the parent fork
`rrnewton/dev-hermit`, converging the remote to the intended set
`{main, devbig-lead, fedora-desktop, demo}` (`fedora-desktop` and `demo` did
not yet exist; nothing was created). Each non-kept branch was analyzed against
current `origin/main` (`262d813`) before deletion. Content that was genuinely
net-new was cherry-picked onto `main` first (see task
`impl-cleanup-parent-branches`, 2026-07-22).

| Branch | Tip SHA | Closed | Disposition |
| --- | --- | --- | --- |
| `codex/hermit-dev-plugin` | `fed8844` | 2026-07-22 | Deleted. Content (coordinator plugin, `reverie` pin `075d1ef`, `ai_docs/test-coverage-status.md`, AGENTS/CLAUDE restructure) already on `main` via PR #1 (`726f0d2`); `main` is strictly ahead. Nothing net-new. |
| `impl-docs-update-session` | `236c25e` | 2026-07-22 | Deleted. Patch-identical to `main` via PR #2 (`16e05eb`); `git cherry` reports it already present. Nothing net-new. |
| `impl-update-agents-md-slot04` | `f3e5440` | 2026-07-22 | Deleted. Tree identical to `main` via PR #3 (`262d813`, "Require committed feature-branch handoffs"). Nothing net-new. |
| `impl-profile-hermit-overhead` | `659c585` | 2026-07-22 | Deleted. Its only net-new file `ai_docs/performance-profile.md` is byte-identical to the copy carried by `impl-commit-new-research`, which was merged to `main`. |
| `codex/pr-status-health` | `16365ac` | 2026-07-22 | Merged then deleted. Cherry-picked to `main` as `2ace563` (`scripts/pr_status.py`, `scripts/test_pr_status.py`, `.orc/plugins/hermit-dev/index.ts` +30). |
| `impl-commit-new-research` | `09c0b71` | 2026-07-22 | Merged then deleted. Cherry-picked to `main` as `0d7684e` (`ai_docs/` research set + `experiments/qemu_under_hermit_20260721/`, 3054 insertions). |

`main` advanced `262d813` -> `0d7684e` (fast-forward) carrying the two merged
commits above. `devbig-lead` retained. Local branches and worktrees for these
remote branches were left untouched (owned by concurrent agents); only the
remote refs on `rrnewton/dev-hermit` were removed.
