---
name: core-memory-skill-sync-tooling
description: "Every active coordinator skill maps 1-1 to an ORC memory; scripts/{lint,sync}-memory-skill-sync enforce content and coverage"
---

Every active coordinator skill has exactly one ORC memory so policies cannot
silently diverge across the two context channels.

Contract:
- Active skills are Markdown skill files recursively discovered under
  `.claude/skills/`; README files and `.claude/archived_skills/` are not
  active skills.
- Each source memory declares `core_memory: true` and the exact stable
  `core_skill: .claude/skills/...` path, and carries a visible
  `> **CORE-MEMORY**` tag. The memory is the source of truth.
- Policy memories without a hand-written skill generate
  `.claude/skills/core-memory/<slug>.md` with BEGIN/END mirror markers.
  Hand-written role, review, and reporting skills retain their normal paths;
  synchronization compares and regenerates their frontmatter and body in place.
- Exactly one source memory must map to every active skill. Missing, duplicate,
  stale, invalid, and orphan mappings fail lint.

Tooling:
- `scripts/lint-memory-skill-sync.rs` is read-only and checks complete active
  skill coverage plus body equality.
- `scripts/sync-memory-skill.rs --adopt-skill <path>...` creates source
  memories from existing coordinator skills.
- Running `scripts/sync-memory-skill.rs` regenerates every mapped skill;
  `--promote <slug>...` creates generated policy mirrors, `--demote` removes
  mappings, and `--check` is a dry run.

The memory store is file-based Markdown at
`~/.claude/projects/-home-newton-work-dev-hermit/memory/`, overridable with
`HERMIT_MEMORY_DIR`. After changing a mapped memory, run sync and lint.
