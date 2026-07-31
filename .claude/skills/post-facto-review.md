---
name: post-facto-review
description: "Land CI-green changes before human review while enforcing dual Claude+Codex adversarial review for post-facto-human-review and core determinism, virtual-time, or scheduling changes."
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

Apply `post-facto-human-review` automatically if the PR contains at least one
of these four triggers. Do not omit or remove the label to avoid the elevated
review gate:

1. **New syscall support.** Verify that the in-code determinization audit tags
   are present: `AUTONOMOUS-BOT-IMPLEMENTED` at the new dispatch/classification
   entry and `TODO-HUMAN-REVIEW(PR-id)` at the implementation or determinization
   block.
2. **A Reverie API or core-abstraction change**, including the `Tool`, `Guest`,
   `Backend`, or syscall-interception model.
3. **A new or changed core determinization strategy**, rather than a routine
   implementation of an already established strategy. This includes changes
   to guest-visible virtual-time or clock semantics.
4. **A core DetCore scheduling change**: anything that affects how programs are
   scheduled, preempted, blocked, awakened, or explored during race search.
   This trigger is always labeled.
   [Hermit PR #1151](https://github.com/rrnewton/hermit/pull/1151), which moved
   slowdown into virtual-time/epoch scheduling, is the canonical good example
   of both this trigger and the determinism rationale reviewers need.

Routine backend-parity work toward the golden ptrace reference implementation
does **not** trigger human review merely because it changes KVM, DBI, SaBRe,
LiteInst, or another non-ptrace backend. Apply the label only when that work also
meets one of the four triggers above; "backend parity change" is not a valid
review rationale by itself.

Any change to core determinism, virtual time, clocks, timers, or scheduling is
an elevated core change even if its diff is small or described as a bug fix.
It automatically triggers `post-facto-human-review` and the dual-review gate
below. Backend-parity work becomes elevated when it changes one of those core
contracts.

This technical review is a pre-land quality gate. Human-owner review is not:
the owner still reviews the already-landed change after the fact.

## 2. Dual adversarial-review gate and labels

Every PR carrying `post-facto-human-review` requires two independent
adversarial reviews before landing: one by a Claude-family reviewer and one by
a Codex-family reviewer. Neither reviewer may be the author; when the author
uses one of those model families, use a separate reviewer instance from that
family. Reviewers try to refute the change and cover correctness,
determinism, Linux semantics, the Reverie/Detcore boundary, security, and the
validation claims at the exact PR head.

Record the review audit trail with these labels:

- `adversarial-review-codexN` and `adversarial-review-claudeN` mean review
  round `N` happened. Start with `N=1` and add the next numbered label after
  each author-fix/reviewer-recheck round. These labels record activity; they do
  not mean approval.
- `passed-review-codex` and `passed-review-claude` mean the corresponding
  reviewer approved the current head after all findings were resolved.

Do not land unless all four conditions are visible: at least one numbered
Codex review label, at least one numbered Claude review label,
`passed-review-codex`, and `passed-review-claude`. Bind both approvals to the
exact head SHA in role-tagged review comments. A head change invalidates both
passed labels: remove them, run both reviewers again, add the next numbered
round labels, and restore passed labels only after fresh exact-head approval.
Changes-requested findings likewise remove the affected passed label until the
reviewer approves a corrected head.

## 3. Mandatory PR description sections

Every PR description must contain:

- **Summary**.
- **Determinism** — mandatory for every PR; explain why the change is
  deterministic and give the logic or informal proof, not only test results.
  For time-related changes, prove that time remains continuous and
  fine-grained across deterministic execution; a deterministic first sample is
  not a clock model.
- **Linux Semantics** — mandatory for every PR. State the relevant Linux
  kernel/userspace contract, how the implementation preserves it, and every
  intentional deviation. For a non-semantic change, say explicitly why Linux
  behavior is unchanged.
- **Validation** — exact commands, outcomes, limitations, and relaxations. For
  time-related changes, validate continuous evolution across multiple reads,
  deterministic work/events, timers or waits, and relevant fork/exec process
  trees; do not validate only the first observed timestamp.
- **Relationship to gVisor** — required for KVM changes; state the relevant
  comparison or explicitly explain why none applies.
- **Human Review Required** — mandatory whenever
  `post-facto-human-review` is applied. Name the specific numbered trigger(s)
  above; vague prose such as "backend change" is insufficient.

PR #1151 is the canonical good example for trigger 4: its slowdown model is
explained as weighted virtual-time progression with deterministic epochs and
replay evidence, rather than asserted from passing tests alone. New PRs must use
the section names above and identify trigger 4 explicitly.

## 4. Continuous virtual time is sacred

Treat any weakening of continuous, fine-grained guest virtual time as a
landing red flag. Virtual time must remain a coherent process-tree clock whose
evolution follows deterministic guest progress and preserves the promised
Linux clock, timer, timeout, and ordering semantics.

Stop the landing and require both adversarial reviewers to resolve the design
if an implementation:

- special-cases or fabricates only the first clock read;
- pins time to a first-read epoch, resets or reseeds it per syscall or process,
  or gives related processes incoherent clocks;
- advances time only when it is observed, or derives guest-visible progress
  from host wall time;
- makes a demo's first timestamp pass while later reads, timers, sleeps,
  polling deadlines, fork, or exec do not continuously evolve under the same
  model; or
- cites a single sample as validation for a continuously evolving clock.

An immediate pair of reads need not differ when no deterministic progress
occurred. The invariant is coherent fine-grained evolution across meaningful
guest work and events, not artificial movement on every read.

## 5. Labels

- `post-facto-human-review` is the **single** routing label for a PR awaiting
  the human's after-the-fact review. Apply it automatically for the four
  triggers above. It never waits for pre-land human approval, but it does
  activate the mandatory dual adversarial-review gate in section 2.
- The numbered `adversarial-review-{codex,claude}N` labels are an append-only
  review-round audit trail. The `passed-review-{codex,claude}` labels are
  exact-head approvals and must both be current before landing.
- `pre-land-human-review` remains defined only as a notional opposite. **Never
  apply it** while the canonical post-facto protocol is active.
- **Never apply, remove, or otherwise alter `human-approved`.** It records an
  actual human approval and is owner-only.
- The obsolete `human-review` and `post-facto-review` labels must not be
  recreated or applied.
- `locally-validated` is permitted only when local evidence proves that a
  residual CI failure is baseline or environmental.

## 6. New-syscall code markers

New syscall support authored by a bot must carry both narrowly scoped
determinization breadcrumbs:

- `// AUTONOMOUS-BOT-IMPLEMENTED` at the new dispatch/classification entry.
- `// TODO-HUMAN-REVIEW(PR-id)` at the implementation or determinization block.

Verify both markers before labeling or landing trigger 1. They are not blanket
markers for bot-authored code, backend changes, or routine parity fixes. Use
them only at the smallest new-syscall regions, not across untouched code.

## 7. Land when review and CI are green

Once required adversarial review is resolved and the authoritative gate is
green, land the authorized change without waiting for a human.

- Hermit requires the authoritative GitHub-hosted `Regular tests` gate. Treat
  known-environmental self-hosted failures according to current repository
  policy; never bypass a genuine product failure.
- Reverie requires its repository-defined authoritative gates.
- When one of the four triggers applies, add `post-facto-human-review`, verify
  every required PR section, verify both numbered review trails and both
  exact-head passed labels, post role-tagged evidence, squash-merge, record the
  exact merge SHA, and rebase dependents in dependency order. Do not add the
  label to a routine non-triggering PR.

## 8. Human reviews after landing

The human reviews landed work after the fact. Corrections use follow-up PRs and
fix forward. Remove a `TODO-HUMAN-REVIEW` marker only when its concern is
addressed.

## No pre-land mode

There is no active pre-land-human-review mode. The archived pre-land skill is
historical context, not an activation path. A future policy change requires a
new owner directive and an update to this canonical skill first.
