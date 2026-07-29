---
name: post-facto-review
description: "Land adversarially reviewed, CI-green changes before human review; use the single post-facto-human-review follow-up label."
---

# Post-Facto-Review Mode

## PR Comment Convention

Every PR description and comment created under this workflow MUST start with
the applicable role tag:

- `[impl agent, MODEL]` for implementation agents
- `[adversarial-reviewer agent, MODEL]` for review agents
- `[coordinator, MODEL]` for coordinator agents
- `[Human]` for the human owner

This is the **canonical and currently active** landing discipline. Changes land
as soon as required adversarial review is resolved and the authoritative CI
gate is green. Human review happens *after* landing, and corrections fix
forward. The old pre-land protocol is retained only as an
[archived historical document](../archived_skills/human-review-first/SKILL.md);
never activate it or apply a pre-land label in the current mode.

## 1. Key changes still get adversarial review

Key changes include new syscalls, major Reverie API changes, scheduler or
determinism-model changes, and record/replay format changes. Before landing:

- Use independent reviewers whose job is to refute the change, with repeated
  author-fix/reviewer-recheck rounds until findings are resolved.
- Cover correctness, determinism, the Reverie/detcore boundary, and security.
- Bind evidence to exact commands and SHAs.

This technical review is a pre-land quality gate. Human-owner review is not.

## 2. Labels

- `post-facto-human-review` is the **single** routing label for a PR awaiting
  the human's after-the-fact review. Apply it to autonomously landed work; it is
  informational and never a landing blocker.
- `pre-land-human-review` remains defined only as a notional opposite. **Never
  apply it** while the canonical post-facto protocol is active.
- **Never apply, remove, or otherwise alter `human-approved`.** It records an
  actual human approval and is owner-only.
- The obsolete `human-review` and `post-facto-review` labels must not be
  recreated or applied.
- `locally-validated` is permitted only when local evidence proves that a
  residual CI failure is baseline or environmental.

## 3. Code markers

Autonomously landed code may carry narrowly scoped breadcrumbs:

- `// AUTONOMOUS-BOT-IMPLEMENTED`
- `// TODO-HUMAN-REVIEW(PR-id)`

Use markers only at the smallest novel function or block, not across untouched
code.

## 4. Land when review and CI are green

Once required adversarial review is resolved and the authoritative gate is
green, land the authorized change without waiting for a human.

- Hermit requires the authoritative GitHub-hosted `Regular tests` gate. Treat
  known-environmental self-hosted failures according to current repository
  policy; never bypass a genuine product failure.
- Reverie requires its repository-defined authoritative gates.
- Add `post-facto-human-review`, post role-tagged evidence, squash-merge, record
  the exact merge SHA, and rebase dependents in dependency order.

## 5. Human reviews after landing

The human reviews landed work after the fact. Corrections use follow-up PRs and
fix forward. Remove a `TODO-HUMAN-REVIEW` marker only when its concern is
addressed.

## No pre-land mode

There is no active pre-land-human-review mode. The archived pre-land skill is
historical context, not an activation path. A future policy change requires a
new owner directive and an update to this canonical skill first.
