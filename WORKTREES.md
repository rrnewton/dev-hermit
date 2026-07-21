# Nested Worktree Protocol

Use only opaque `slotNN` names, from `slot01` through `slot12`, for isolated
parent work. At most twelve worktrees may be active and at most five additional
clean slots may be parked for cache reuse. Each parent worktree contains
independent `hermit/` and `reverie/` submodule checkouts at the revisions
pinned by its parent commit.

Create a slot with the helper instead of invoking `git worktree add` directly:

```bash
./slot-init.sh slot01 --owner hermit-api --task impl-example \
  --purpose "Implement the example change"
```

The helper refuses non-canonical names, an unregistered owner/task, a
thirteenth active slot, or allocation while more than five parked slots exist.
It creates `worktrees/slot01` on `devbig-lead-slot01`, initializes the
submodules, and writes the `worktrees/ACTIVE.md` row before returning.

Pass an explicit parent branch and start point when needed:

```bash
./slot-init.sh slot02 --owner hermit-api --task issue-123 \
  --purpose "Fix issue 123" --branch issue-123 --start-point devbig-lead
```

The helper never resets or cleans an existing slot. Submodules start at
detached pinned commits, which is normal. Before editing product source,
create a task-specific branch inside the relevant nested `hermit/` or
`reverie/` checkout. At closeout, archive the exact SHAs, remove the ACTIVE
row, and keep no more than five clean parked slots for cache reuse.
