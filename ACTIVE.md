# Active Coordinator Checkouts

This registry records the shared coordinator-owned integration checkouts. These
are not feature-development slots. Their observed state must match the checkout
before integration or parent gitlink pinning; a dirty, missing, or feature-branch
state blocks those operations until its owner resolves it.

## Observed 2026-07-23

| Path | Repository | Branch / SHA | State | Purpose / required action |
| --- | --- | --- | --- | --- |
| `hermit/` | Hermit | `impl-qemu-demo-script` / `0b241392473a` (remote branch deleted) | DIRTY, occupied | Preserve current agent work; restore a clean coordinator branch only after attribution |
| `reverie/` | Reverie | `main` / `e4ff635f1661` | DIRTY, occupied | Preserve untracked nested worktree; integration blocked pending attribution |
| `main/hermit/` | Hermit | missing | MISSING | Re-provision a clean `main` rebase-base checkout before use |
| `main/reverie/` | Reverie | missing | MISSING | Re-provision a clean `main` rebase-base checkout before use |

Feature worktree ownership is machine-local in `worktrees/ACTIVE.md` and
`worktrees_reverie/ACTIVE.md`. Those registries are intentionally ignored by
the parent repository.
