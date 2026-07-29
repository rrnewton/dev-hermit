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

## 1. Exact human-review trigger set

Apply `post-facto-human-review` if and only if the PR contains at least one of
these four triggers:

1. **New syscall support.** Verify that the in-code determinization audit tags
   are present: `AUTONOMOUS-BOT-IMPLEMENTED` at the new dispatch/classification
   entry and `TODO-HUMAN-REVIEW(PR-id)` at the implementation or determinization
   block.
2. **A Reverie API or core-abstraction change**, including the `Tool`, `Guest`,
   `Backend`, or syscall-interception model.
3. **A new determinization strategy**, rather than an implementation of an
   already established strategy.
4. **A core DetCore scheduling change**: anything that affects how programs are
   scheduled, especially race-search behavior. This trigger is always labeled.
   [Hermit PR #1151](https://github.com/rrnewton/hermit/pull/1151), which moved
   slowdown into virtual-time/epoch scheduling, is the canonical good example
   of both this trigger and the determinism rationale reviewers need.

Routine backend-parity work toward the golden ptrace reference implementation
does **not** trigger human review merely because it changes KVM, DBI, SaBRe,
LiteInst, or another non-ptrace backend. Apply the label only when that work also
meets one of the four triggers above; "backend parity change" is not a valid
review rationale by itself.

Before landing a triggered change:

- Use independent reviewers whose job is to refute the change, with repeated
  author-fix/reviewer-recheck rounds until findings are resolved.
- Cover correctness, determinism, the Reverie/detcore boundary, and security.
- Bind evidence to exact commands and SHAs.

This technical review is a pre-land quality gate. Human-owner review is not.

## 2. Mandatory PR description sections

Every PR description must contain:

- **Summary**.
- **Determinism** — mandatory for every PR; explain why the change is
  deterministic and give the logic or informal proof, not only test results.
- **Validation** — exact commands, outcomes, limitations, and relaxations.
- **Relationship to gVisor** — required for KVM changes; state the relevant
  comparison or explicitly explain why none applies.
- **Human Review Required** — mandatory whenever
  `post-facto-human-review` is applied. Name the specific numbered trigger(s)
  above; vague prose such as "backend change" is insufficient.

PR #1151 is the canonical good example for trigger 4: its slowdown model is
explained as weighted virtual-time progression with deterministic epochs and
replay evidence, rather than asserted from passing tests alone. New PRs must use
the section names above and identify trigger 4 explicitly.

## 3. Labels

- `post-facto-human-review` is the **single** routing label for a PR awaiting
  the human's after-the-fact review. Apply it only for the four triggers above;
  it is informational and never a landing blocker.
- `pre-land-human-review` remains defined only as a notional opposite. **Never
  apply it** while the canonical post-facto protocol is active.
- **Never apply, remove, or otherwise alter `human-approved`.** It records an
  actual human approval and is owner-only.
- The obsolete `human-review` and `post-facto-review` labels must not be
  recreated or applied.
- `locally-validated` is permitted only when local evidence proves that a
  residual CI failure is baseline or environmental.

## 4. New-syscall code markers

New syscall support authored by a bot must carry both narrowly scoped
determinization breadcrumbs:

- `// AUTONOMOUS-BOT-IMPLEMENTED` at the new dispatch/classification entry.
- `// TODO-HUMAN-REVIEW(PR-id)` at the implementation or determinization block.

Verify both markers before labeling or landing trigger 1. They are not blanket
markers for bot-authored code, backend changes, or routine parity fixes. Use
them only at the smallest new-syscall regions, not across untouched code.

## 5. Land when review and CI are green

Once required adversarial review is resolved and the authoritative gate is
green, land the authorized change without waiting for a human.

- Hermit requires the authoritative GitHub-hosted `Regular tests` gate. Treat
  known-environmental self-hosted failures according to current repository
  policy; never bypass a genuine product failure.
- Reverie requires its repository-defined authoritative gates.
- When one of the four triggers applies, add `post-facto-human-review`, verify
  the `Human Review Required` section names it, post role-tagged evidence,
  squash-merge, record the exact merge SHA, and rebase dependents in dependency
  order. Do not add the label to a routine non-triggering PR.

## 6. Human reviews after landing

The human reviews landed work after the fact. Corrections use follow-up PRs and
fix forward. Remove a `TODO-HUMAN-REVIEW` marker only when its concern is
addressed.

## No pre-land mode

There is no active pre-land-human-review mode. The archived pre-land skill is
historical context, not an activation path. A future policy change requires a
new owner directive and an update to this canonical skill first.
