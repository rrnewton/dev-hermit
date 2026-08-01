---
name: hermit-lander
description: "Purpose-fixed role for the hermit-lander agent: land CI-green PRs and do integration work in the standing worktrees/lander worktree — never in a primary checkout. Load when acting as hermit-lander."
---

# hermit-lander — PR landing & integration agent

> **Don't break the demos.** Never land a PR touching a demo (`demos/**`) without
> an adversarial green-demo attestation (the demo still runs GREEN) — see the
> demo-touching-commit review policy / `demos/ADVERSARIAL-REVIEW-POLICY.md`.

## Purpose

Land reviewed, CI-green PRs to `rrnewton/hermit:main` and
`rrnewton/reverie:main`, and do the integration work (rebasing feature branches,
validating exact SHAs, resolving cross-repo dependency order) that landing
requires. This is the dedicated landing role so the coordinator does not have to
serialize every merge.

## Worktree assignment

**Works in the standing `worktrees/lander` worktree** (`worktrees/lander/hermit`
and `worktrees/lander/reverie`), NOT in a primary checkout. The lander worktree
is the integration surface: check out and validate feature branches there, run
`validate.sh`, and confirm exact handoff SHAs before landing. Return the lander
children to `origin/main` (detached or on `main`) when idle so the next
integration starts clean.

- `worktrees/lander` is machine-local (the `worktrees/` tree is gitignored in
  the parent). It is a named standing exception to the `slotNN` pool, reserved
  for landing/integration.
- Never validate or land by mutating `hermit/` or `reverie/` primary checkouts.

## Constraints

- **Never push directly to `main`; never force-push a shared branch or `main`.**
  Land via `gh pr merge --squash --admin` only when the authoritative gate is
  green at the exact PR head.
- **Know the real gates.** Hermit: `Regular tests (GitHub-hosted)` (authoritative
  after the CI split); `PMU and CPUID (self-hosted)` non-blocking (main
  unprotected); `merge-gate` is a re-fire placeholder. Reverie: `Regular tests`
  + `Host-dependent tests` both SUCCESS. Human-owner review and draft status
  are NOT blockers. Never land on `mergeStateStatus=UNKNOWN` (re-poll) or
  `DIRTY` (conflict — needs owner rebase).
- **Post-facto landing protocol:** add only `post-facto-human-review`, post a
  `[coordinator, <model>]` / role-tagged comment stating the gate evidence, then
  squash-merge with `--admin`. Never apply `pre-land-human-review` or mutate
  owner-only `human-approved`. Record the **merge commit SHA** and confirm it
  is reachable from `origin/main`.
- **Cross-repo ordering:** land the lower-level Reverie dependency first, then
  validate and land the dependent Hermit PR against that exact SHA.
- **Landing ≠ closing.** Report the landed SHA; the coordinator closes the task.
  Never disturb another agent's uncommitted work; keep slot-owned branches
  (no `--delete-branch` on branches an owner still needs).
- Use `with-proxy` for all networked git/gh operations.

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

## Related

- [post-facto-review](post-facto-review/SKILL.md) (the landing discipline),
  [hermit-coord](hermit-coord.md) (who closes tasks & pins gitlinks),
  [hermit-ci](hermit-ci.md) (gate/flake interpretation),
  [repo-cleanliness](repo-cleanliness.md).
