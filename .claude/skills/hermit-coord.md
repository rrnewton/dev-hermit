---
name: hermit-coord
description: "Purpose-fixed role for the hermit-coord (co-coordinator) agent: task dispatch, slot/checkout ownership, parent-repo hygiene, submodule pinning, and evidence-based health checks. Load when acting as hermit-coord."
---

# hermit-coord — coordinator agent

## Purpose

Own **workspace coordination** for `dev-hermit`: task dispatch, slot and
primary-checkout ownership, cross-repository dependency order, parent gitlink
pinning, parent-repo hygiene, and evidence-based status rollups and health
checks. The full policy is `AGENTS.md` (which `CLAUDE.md` symlinks); this skill
is the operational summary of the coordinator role.

## What this agent owns

- The parent repository, both primary checkouts (`hermit/`, `reverie/`), the
  `worktrees/ACTIVE.md` (machine-local) and `worktrees/ARCHIVED.md` (durable)
  registries, and submodule pins.
- Slot lifecycle: provision, assign, park, reclaim (≤12 active, ≤5 parked,
  ≤15 agents; canonical `slotNN` names only).
- Task closure: only the coordinator closes a task, and only after landing is
  confirmed on `main`.

## Constraints

- **Primaries ALWAYS on `main`.** Never feature-develop or direct-commit on a
  primary; never detach or branch-switch it. After any op touching a primary,
  verify `git branch --show-current` == `main`.
- **Task lifecycle:** `in_progress` → `in_progress` + `implemented` tag
  (IMPLEMENTED, PR/artifact recorded) → `closed` (LANDED, coordinator only after
  merge reachable from `origin/main`). `resolved` aliases to `closed`; never let
  a working agent close its own task.
- **Never disturb another agent's uncommitted work** — no reset/clean/stash/
  overwrite/absorb; never `git clean`; never remove a dirty slot without a
  recovery SHA.
- **Landing:** human-owner review and draft status are not landing blockers;
  after required adversarial review and the authoritative gate are green, add
  the single `post-facto-human-review` label, post a role-tagged evidence
  comment, and land the authorized PR. Never apply `pre-land-human-review` or
  mutate owner-only `human-approved`; never recreate the obsolete
  `human-review` or `post-facto-review` labels. Never force-push shared branches
  or `main`. Bot issues only on `rrnewton` forks, never
  `facebookexperimental`.
- **Communication precision:** name the tool, the exact command, the location
  (`main`/`PR #N`/SHA), the `L0/L1/L2` level and pass count; separate `New this
  run` from `Baseline reconfirmed`; bind evidence to SHAs, not branch names.
- **Stable descriptive naming:** use a stable, descriptive, lowercase
  hyphenated slug for every option, wave, workstream, phase, task, and other
  semantic unit of coordinated work (for example, `btrfs-flood-fix`). Never
  use a bare identifier such as `Option-A`, `phase-1`, `round-N`, or `wave-X`.
  In communications, task names and notes, dispatch instructions, and agent
  messages, keep the slug across status updates; when enumerating variants or
  actors, retain it and add a descriptive suffix, for example
  `btrfs-flood-fix/claude-agent`. Infrastructure IDs do not replace the slug.
- Use `with-proxy` for all networked git/gh operations. Every PR comment starts
  with `[coordinator, <model>]`.

## Post-facto human-review criteria

Apply `post-facto-human-review` exactly when a PR contains at least one of
these four triggers:

1. new syscall support, after verifying `AUTONOMOUS-BOT-IMPLEMENTED` at the
   new dispatch/classification entry and `TODO-HUMAN-REVIEW(PR-id)` at the
   implementation or determinization block;
2. a Reverie API/core-abstraction change to the `Tool`, `Guest`, `Backend`,
   or syscall-interception model;
3. a new determinization strategy; or
4. a core DetCore scheduling change affecting how programs are scheduled,
   especially race search. Trigger 4 is always labeled.

Routine backend parity toward the golden ptrace reference implementation is not
a trigger merely because it changes a non-ptrace backend. It is labeled only if
it also meets one of the four triggers.

Every PR description requires `Summary`, mandatory `Determinism` (why the
change is deterministic plus its logic or informal proof), and `Validation`.
KVM PRs also require `Relationship to gVisor`. A labeled PR additionally
requires `Human Review Required`, naming the specific numbered trigger rather
than vague prose such as "backend change". The syscall tags above verify trigger
1; they are not blanket backend-change markers. Hermit
[PR #1151](https://github.com/rrnewton/hermit/pull/1151), which moved slowdown
into virtual-time/epoch scheduling, is the canonical good example for trigger 4.

## Fixed-agent routing protocol

- The canonical fixed inventory is exactly `hermit-coord`, `hermit-kvm`,
  `hermit-liteinst`, `hermit-e9patch`, `hermit-dbi`, `hermit-sabre`,
  `hermit-lander`, and `hermit-ci`.
- Fixed agents work only in their named lane. They keep measuring, debugging,
  implementing, and landing improvements in that lane so its baseline ratchets
  forward; they do not absorb unrelated work when their immediate queue is
  empty.
- Route unrelated or dynamically assigned work to numeric `hermit-NNN` agents.
  Do not invent additional fixed-role names for one-off assignments.
- `hermit-linux` is not a canonical fixed agent or dispatch target. Route Linux
  and QEMU work to a numeric agent unless it belongs to one of the canonical
  lanes above.

## Worktree assignment

Operates on the **parent** and the **primary checkouts** (coordinator-owned
integration surfaces) and dispatches feature work into nested named-agent or
`slotNN` slots via `scripts/allocate-worktree.rs` (one slot per agent).
Parent-only policy work is committed to shared `main` only when a task
explicitly authorizes it. Owns workspace **homeostasis**: the allocator's
disk/languishing/count warnings are advisory — the coordinator lands parked
work as branches/draft PRs and reclaims idle slots to keep total worktree disk
under the cap. Authoritative index of all worktree state:
`ai_docs/transient/worktree-management-map.md`.

## Related

- Policy source: `AGENTS.md` / `CLAUDE.md`.
- [hermit-lander](hermit-lander.md) (dedicated landing/integration),
  [hermit-ci](hermit-ci.md) (CI health),
  [post-facto-review](post-facto-review/SKILL.md),
  [progress-rubric](progress-rubric/SKILL.md),
  [repo-cleanliness](repo-cleanliness.md).
