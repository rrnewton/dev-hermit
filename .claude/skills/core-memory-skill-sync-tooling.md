---
name: core-memory-skill-sync-tooling
description: "Every active coordinator skill maps 1-1 to an ORC memory; scripts/{lint,sync}-memory-skill-sync enforce content and coverage"
---

Every active coordinator skill has exactly one ORC memory so policies cannot
silently diverge across the two context channels.

Contract:
- Active coordinator skills are flat Markdown files at
  `.claude/skills/<slug>.md`; `README.md` and `.claude/archived_skills/` are not
  active skills, and nested skill directories fail lint.
- Each source memory declares `core_memory: true` and the exact stable
  `core_skill: .claude/skills/<memory-slug>.md` path, and carries a visible
  `> **CORE-MEMORY**` tag. The memory is the source of truth.
- Every mapped memory generates the same flat path. Synchronization derives
  skill frontmatter and a canonical body by removing memory-only frontmatter,
  the visible mirror tag, and full-line HTML comments, then normalizing blank
  lines and trailing whitespace.
- Exactly one source memory must map to every active skill. Missing, duplicate,
  stale, invalid, and orphan mappings fail lint.

Tooling:
- `scripts/lint-memory-skill-sync.rs` is read-only. It checks flat mapping and
  slug consistency, the visible source-memory tag, skill presence, frontmatter
  and canonical-body equality, no nested directories, and exactly one source
  memory for every active skill.
- `scripts/sync-memory-skill.rs --adopt-skill <path>...` creates source
  memories from existing flat coordinator skills. Running it without a mode
  regenerates every mapped skill; `--promote <slug>...` adds mapping metadata
  and creates a mirror, `--demote` removes a mapping and mirror, and `--check`
  performs the same decisions without writing.

The memory store is file-based Markdown at
`~/.claude/projects/-home-newton-work-dev-hermit/memory/`, overridable with
`HERMIT_MEMORY_DIR`. After changing a mapped memory, run sync and lint.
