# Skill Scope Map

The parent and product repositories have separate skill audiences. Keep this
split mechanical; do not copy coordinator skills into a product repository.

## Parent Coordinator Skills

Agents launched from `dev-hermit/` discover this real directory through
`.llms/skills -> ../.claude/skills` and the workspace skill hook. The parent
does not currently carry an `.agents/skills` link.

**Always-load contract for purpose-fixed roles.** Every fixed-function worker
(`hermit-ci`, `hermit-coord`, `hermit-dbi`, `hermit-e9patch`, `hermit-kvm`,
`hermit-lander`, `hermit-liteinst`, `hermit-sabre`, plus `hermit-linux` /
`hermit-opt`) has a same-named role charter here. Each charter's `description`
frontmatter ends with "Load when acting as `hermit-<name>` or dispatching
`<name>` work", which is the reminder hook the skill-evaluation step reads to
surface and load the charter **every time** that agent is engaged or that lane
is dispatched. Keeping the `Load when acting as …` clause in every role
`description` is what makes the always-load behavior automatic — do not drop it.
This is portable across clients (Claude/codex) because it rides the skill
`description`, not a client-specific `agents` definition.

The active set is:

- Purpose-fixed roles: `hermit-ci.md`, `hermit-coord.md`,
  `hermit-dbi.md`, `hermit-e9patch.md`, `hermit-kvm.md`,
  `hermit-lander.md`, `hermit-linux.md`, `hermit-liteinst.md`,
  `hermit-opt.md`, and `hermit-sabre.md`.
- Coordinator review and reporting:
  `backend-reality-reviewer.md`, `benchmarking.md`, `post-facto-review.md`, and
  `progress-rubric.md`.
- Memory-backed policy skills: the 22 remaining flat Markdown files.
  Edit their source memories and run the sync tool.

Every active coordinator skill is a flat `.claude/skills/<memory-slug>.md` file.

### CI implementation home

Skills describe when and why CI work should happen. All current CI code, live
health/history entrypoints, runner operations, and project-specific tick
configuration live under [`ci-hub/`](../../ci-hub/README.md). Do not add new CI
implementations under `.claude/skills`, `scripts/`, `ops/`, or a dated memory;
link the hub instead. Generic engines remain pinned in `agent-utils` and are
used through `ci-hub/bin/agent-tool`, never copied into this repository.

`human-review-first` is dormant and archived at
`.claude/archived_skills/human-review-first/SKILL.md`. Archived skills are not
part of normal discovery or the active memory coverage gate.

Every active parent skill has exactly one source memory. The memory frontmatter
declares the exact `core_skill` path. Run:

```bash
scripts/sync-memory-skill.rs --check
scripts/lint-memory-skill-sync.rs
```

Use `scripts/sync-memory-skill.rs --adopt-skill <path>...` when adding an
existing hand-written coordinator skill to the contract.

## Product Worker Skills

Hermit keeps only workflows usable by an implementation agent with a Hermit
checkout and no parent-harness role:

- `deadlock-debugging.md`
- `fabler/SKILL.md`
- `hermit-debugging/SKILL.md`
- `repo-cleanliness.md`

Reverie currently keeps only `repo-cleanliness.md`. A future Reverie worker
skill must describe Reverie product implementation, not parent coordination.

The cleanliness skill is intentionally duplicated between the two independent
product histories. It is worker policy, not a parent coordinator skill.

Neither product repository may contain `core-memory/`, purpose-fixed
harness-role charters, landing policy, coordinator progress reporting, or
backend completion-review policy.

## Discovery Check

This workspace run confirmed that the project hook discovers the parent flat
Markdown skills through `.llms/skills -> ../.claude/skills`. Parent discovery
does not depend on recursive folder scanning.

A separate raw-client check was unavailable: both installed `claude --help`
and `codex --help` abort in the Meta launcher because it injects the
development-only `META_DANGEROUSLY_DISABLE_LINUX_SANDBOX` setting. Treat the
workspace hook result as environment-specific evidence, not a portable client
guarantee.

Product repositories may retain their own documented skill shapes. The parent
