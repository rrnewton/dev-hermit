# Coordinator skills (parent-owned)

This is a **real directory** — deliberately NOT a symlink into
`hermit/.claude/skills/`. It holds the **coordinator-level** skills for agents
launched from the `dev-hermit` parent workspace (loaded via
`.llms/skills -> ../.claude/skills`).

Coordinator vs. repo skills are separated on purpose:

- **Here (`dev-hermit/.claude/skills/`, coordinator):** agent roles, workspace
  discipline, landing/review protocol, and the CORE-memory mirror. These govern
  task dispatch, slot/checkout ownership, PR landing, and status rollups — the
  coordinator role described in `AGENTS.md`.
  - `hermit-{coord,ci,dbi,kvm,lander,liteinst,opt,sabre}.md` — purpose-fixed
    agent-role charters.
  - `repo-cleanliness.md` — where artifacts belong; keep hermit/reverie clean.
  - `post-facto-review/`, `human-review-first/` — landing/review discipline.
  - `backend-reality-reviewer/` — auditing backend completion claims.
  - `progress-rubric/` — evidence-based progress reports.
  - `core-memory/` — generated mirrors of CORE memories. **Do not hand-edit;**
    regenerate with `scripts/sync-memory-skill.rs` (verify:
    `scripts/lint-memory-skill-sync.rs`). Both scripts root at this parent, so
    they write/check here.

- **`hermit/.claude/skills/` (repo, implementor):** debugging and coding
  workflows for implementor agents working inside a product checkout —
  `deadlock-debugging.md`, `hermit-debugging/`, `fabler/`. Implementor agents in
  a worktree slot resolve these via `hermit/.llms/skills -> hermit/.claude/skills`.

The agent-role and protocol skills currently also exist in
`hermit/.claude/skills/` (their original landing home, hermit PR #759 et al.).
This parent copy is the coordinator-authoritative one going forward; a follow-up
hermit PR may prune the coordinator-only skills from the product repo to remove
the duplication. Until then both trees carry them, which is backward-compatible.
