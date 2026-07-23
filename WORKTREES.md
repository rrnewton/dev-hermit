# Worktree Protocol

Feature work happens in **flat, direct** Git worktrees created from the primary
checkouts. Each slot is the checkout root itself, not a nested subdirectory:

```text
worktrees/slotNN            # Hermit worktree  (from the hermit/ primary)
worktrees_reverie/slotNN    # Reverie worktree (from the reverie/ primary)
```

Hermit and Reverie worktrees are independent trees that share a slot number
only when one task changes both products.

Create a slot with the helper instead of invoking `git worktree add` directly:

```bash
./scripts/slot-init.sh slot01            # Hermit + Reverie worktrees for slot01
./scripts/slot-init.sh slot02 hermit     # Hermit worktree only
./scripts/slot-init.sh slot03 reverie    # Reverie worktree only
```

The helper adds the worktree from the owning primary (`hermit` for
`worktrees/slotNN`, `reverie` for `worktrees_reverie/slotNN`), detached at the
primary's current HEAD. Pass an explicit start point when needed:

```bash
./scripts/slot-init.sh slot03 reverie main
```

The helper refuses an occupied path and never resets or cleans an existing
slot. Each worktree tree is self-contained: register a new slot in the
`ACTIVE.md` beside it (`worktrees/ACTIVE.md` for Hermit,
`worktrees_reverie/ACTIVE.md` for Reverie) before editing product source, and
record completed/parked slots in the sibling `ARCHIVED.md`.
