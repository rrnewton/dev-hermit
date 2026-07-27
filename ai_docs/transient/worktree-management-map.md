# Worktree Management Map

**Status:** transient (living doc); regenerate when the layout or tooling changes.
**Owner:** coordinator (`hermit-coord`).
**Last updated:** 2026-07-27.

This document is the single index of *every* place worktree/slot information
lives in the `dev-hermit` harness, what each place is authoritative for, who
writes it, and how the places are kept consistent. If you touch worktrees,
read this first.

---

## 1. Canonical layout (v3, NESTED)

```
worktrees/<slot>/hermit    # Hermit worktree  (from the hermit/ primary)
worktrees/<slot>/reverie   # Reverie worktree (from the reverie/ primary)
worktrees/<slot>/liteinst2 # LiteInst2 worktree (from the liteinst2/ primary)
```

- **One slot per agent.** `<slot>` is either a **named agent** (`kvm`, `dbi`,
  `sabre`, `liteinst`, `ci`, `coord`, `lander`, `opt`) or a **generic**
  `slotNN` (`slot01`, `slot02`, …). The `hermit-` prefix on an agent name is
  stripped for the slot (agent `hermit-kvm` → `worktrees/kvm`).
- New slots contain all three children by default. Explicit single-product
  allocations and the legacy `both` (Hermit + Reverie) selector remain
  available for exceptional lightweight use.
- Primaries `hermit/`, `reverie/`, and `liteinst2/` are **never**
  feature-development surfaces. Hermit and Reverie stay on `main`; LiteInst2
  stays clean at the parent-pinned commit except during coordinator-owned
  integration. Primaries are used only for integration, inspection, cache
  donation, and as the source repos for `git worktree add`.

### Deprecated layouts (do NOT create new ones; migrate opportunistically)

- **Flat:** `worktrees/slotNN` *is* the hermit worktree, with reverie split
  into a sibling `worktrees_reverie/slotNN`. Superseded by nested. The
  top-level `worktrees_reverie/` tree is deprecated; remove its registrations
  as each owning task closes.
- **Primary-nested:** `hermit/.worktrees/<name>/hermit` (worktrees registered
  *inside* the primary checkout). These are legacy CI/land scratch trees. Do
  not add more; retire them when their task closes.

The physical `git worktree` registries are the **ground truth** for what
exists on disk. Everything else (below) is derived bookkeeping that must be
reconciled against them.

---

## 2. Where worktree information lives

| # | Location | Tracked? | Authoritative for | Writer | Lifetime |
|---|----------|----------|-------------------|--------|----------|
| 1 | **git worktree registries** — `git -C hermit worktree list`, `git -C reverie worktree list`, `git -C liteinst2 worktree list`, `git worktree list` (parent) | git-internal | **Physical truth**: which worktrees exist, their path, HEAD, branch | `git worktree add/remove/prune` (via the scripts) | until removed |
| 2 | **`worktree-state.json`** (repo root) | **gitignored** (`/worktree-state.json`) | Machine-readable slot→owner map: agents, branches, task, status, purpose, paths | `scripts/allocate-worktree.rs`, `scripts/release-worktree.rs` (SINGLE writer) | machine-local |
| 3 | **`worktrees/ACTIVE.md`** | **gitignored** (`worktrees/*` except ARCHIVED.md) | Human-readable current ownership. Two zones: (a) freeform human notes, (b) a **script-managed table block** between `<!-- BEGIN worktree-state … -->` / `<!-- END worktree-state -->` | Managed block: the two scripts. Freeform: humans/coordinator | machine-local |
| 4 | **`worktrees/ARCHIVED.md`** | **tracked** (durable history) | Append-only closeout record: slot, branch, exact SHAs, validation, disposition | coordinator at closeout | permanent |
| 5 | **`AGENTS.md`** (= `CLAUDE.md` symlink) | tracked | **Policy**: layout rules, hard invariants, slot lifecycle, registry protocol | coordinator (policy change) | permanent |
| 6 | **`scripts/allocate-worktree.rs`** | tracked | Tool: create a slot, enforce one-owner-per-slot + one-slot-per-agent, write (2)+(3) | coordinator | permanent |
| 7 | **`scripts/release-worktree.rs`** | tracked | Tool: release/clean a slot, warn on uncommitted work, update (2)+(3) | coordinator | permanent |
| 8 | **`scripts/slot-init.sh`** | tracked | Tool: quick manual detached scaffolding of `worktrees/<slot>/{hermit,reverie,liteinst2}` (does NOT touch registry) | coordinator | permanent |
| 9 | **`hermit/.claude/skills/hermit-*.md`** (exposed via `.llms/skills` + `.agents/skills` symlinks) | tracked (in hermit) | Per-agent worktree assignment: which named slot an agent owns + its constraints | coordinator (via hermit PR) | permanent |
| 10 | **Auto-memory** (`…/memory/*.md` + `MEMORY.md`) | machine-local | Durable gotchas: parked-slot reuse is racy; cleanup is unsafe; detached≠idle | coordinator/agents | machine-local |
| 11 | **tmux** (`orc-hermit` etc. windows/panes) | runtime | Which agent processes are actually alive and their CWD | ORC / humans | session |
| 12 | **ORC plugin** `.orc/plugins/hermit-dev/` | tracked | Reads `AGENTS.md` at activation; issue-create wrapper; does not store slot state | coordinator | permanent |

Note: `.gitmodules` + parent gitlinks record submodule *commit* pins, not
worktree state; they are out of scope here but live in the parent repo.

---

## 3. Consistency model

The rule that keeps 12 places from drifting is **one writer per fact**:

1. **git registries (1) are physical truth.** If the scripts' view (2)/(3)
   disagrees with the registries, the registries win; reconcile the JSON to
   match, never the reverse.
2. **The allocate/release scripts are the single writer of (2) and (3).**
   `allocate-worktree.rs` creates the worktree *and* writes both
   `worktree-state.json` and the ACTIVE.md managed block in one operation, so
   they cannot diverge from each other. `release-worktree.rs` is the only tool
   that tears them down. Never hand-edit the managed block or the JSON.
3. **ACTIVE.md has a human zone and a machine zone.** Freeform text outside the
   markers is preserved verbatim by the scripts; only the table between the
   markers is regenerated. Humans edit outside the markers; scripts own inside.
4. **ARCHIVED.md (4) is the durable audit trail.** It is written *once*, by the
   coordinator, at closeout, with exact SHAs — the only tracked, permanent
   record of a slot's life. Machine-local (2)/(3) are disposable; (4) is not.
5. **AGENTS.md (5) is policy, code (6–8) enforces it, skills (9) assign it.**
   When the layout changes, all four must move together: update the policy,
   update the scripts to produce the new shape, update the per-agent skill
   assignments. This doc is the checklist for that fan-out.
6. **Memory (10) and tmux (11) are advisory, never authoritative.** A memory
   may name a stale slot; a live tmux pane in a detached/clean worktree is
   still a busy agent. Verify against (1) before acting. See memories
   `parked-slot-reuse-is-racy`, `worktree-cleanup-is-unsafe-for-agents`,
   `detached-clean-merged-slot-can-be-busy`.

### Reconciliation procedure (run before dispatch / cleanup)

```bash
cd ~/work/dev-hermit
git worktree list --porcelain
git -C hermit worktree list --porcelain
git -C reverie worktree list --porcelain
git -C liteinst2 worktree list --porcelain
find worktrees -mindepth 1 -maxdepth 3 -name .git -print | sort
cat worktree-state.json
```

Resolve any of: a physical checkout not in its repo's registry; a registered
worktree whose dir is missing; a live slot absent from ACTIVE.md; an ACTIVE.md
row for a missing slot; duplicate rows; a branch checked out by two worktrees;
any path not matching `worktrees/<slot>/{hermit,reverie,liteinst2}`.

---

## 4. Lifecycle (who writes what, when)

| Phase | Command | Updates |
|-------|---------|---------|
| **Allocate** | `scripts/allocate-worktree.rs --agent <name> [--slot S] [--task T] [--product hermit\|reverie\|liteinst2\|both\|all] [--i-promise-this-agent-is-read-mostly]` | (1) git add, (2) JSON, (3) ACTIVE.md block |
| **Work** | edits/commits on a feature branch inside `worktrees/<slot>/<product>` | (1) branch HEAD moves |
| **Release (retain cache)** | `scripts/release-worktree.rs --slot S` | (2)+(3): status→released; worktree kept |
| **Release (remove)** | `scripts/release-worktree.rs --slot S --clean` | (1) worktree removed, (2) slot dropped, (3) regenerated |
| **Drop one sharer** | `scripts/release-worktree.rs --slot S --agent A` | (2)+(3): sharer removed, slot stays active |
| **Closeout** | coordinator records SHAs → `worktrees/ARCHIVED.md` | (4) permanent record |

Feature branches are **never** auto-deleted by release; they are kept until the
work is merged/reachable and the coordinator archives them.

---

## 5. Disk hygiene

`target/` build dirs dominate disk (primary `hermit/target` alone is ~132G).
Per-worktree `target/` dirs are cheap to rebuild and should be blasted when a
slot is idle:

```bash
find ~/work/dev-hermit/worktrees -name target -type d -maxdepth 3 -exec rm -rf {} +
```

Primary `hermit/target`, `reverie/target`, and `liteinst2/target` are shared
cache donors (`cp -a --reflink=auto`); do NOT blast them unless under real disk
pressure. Never commit `target/` or any build artifact (binary policy in
AGENTS.md).

---

## 6. Invariants recap (see AGENTS.md for the full list)

- One slot per agent; one mutating owner per slot (read-mostly sharers allowed
  with the explicit flag + disjoint paths).
- Never feature-develop or direct-commit on a primary; primaries stay on main.
- Never remove a dirty slot without a recorded recovery SHA.
- Never `git clean`, `reset --hard`, `stash`, or overwrite another agent's WIP.
- A detached/clean worktree may still host a live agent — git idleness ≠ dead.
- `worktree-state.json` and `ACTIVE.md` are machine-local (gitignored);
  `ARCHIVED.md` is the tracked durable history.
