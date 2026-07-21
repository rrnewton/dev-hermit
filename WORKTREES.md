# Nested Worktree Protocol

Use the permanent opaque slots `slot01` through `slot04` for isolated parent
work. Each parent worktree contains independent `hermit/` and `reverie/`
submodule checkouts at the revisions pinned by its parent commit.

Create a slot with the helper instead of invoking `git worktree add` directly:

```bash
./slot-init.sh slot01
```

That command is equivalent to creating `worktrees/slot01` on the parent branch
`devbig-lead-slot01`, synchronizing submodule configuration, and running:

```bash
git -C worktrees/slot01 submodule update --init --recursive
```

Pass an explicit parent branch and start point when needed:

```bash
./slot-init.sh slot02 issue-123 devbig-lead
```

The helper refuses an occupied path and never resets or cleans an existing
slot. Submodules start at detached pinned commits, which is normal. Before
editing product source, create a task-specific branch inside the relevant
nested `hermit/` or `reverie/` checkout.
