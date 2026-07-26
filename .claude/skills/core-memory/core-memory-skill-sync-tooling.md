---
name: core-memory-core-memory-skill-sync-tooling
description: "CORE memories mirror 1-1 to .claude/skills/core-memory/<slug>.md; scripts/{lint,sync}-memory-skill-sync keep them in sync (CORE-MEMORY mirror of memory/core-memory-skill-sync-tooling.md)"
---

# CORE-MEMORY: core-memory-skill-sync-tooling

<!-- GENERATED MIRROR of core memory `core-memory-skill-sync-tooling`. Source of truth is the memory
     file `core-memory-skill-sync-tooling.md`. Regenerate: scripts/sync-memory-skill.rs. Verify in
     sync: scripts/lint-memory-skill-sync.rs. Do NOT hand-edit inside the
     markers — edit the memory and re-run sync. -->

<!-- BEGIN CORE-MEMORY-MIRROR (source: core-memory-skill-sync-tooling.md) -->
CORE memories (policy / protocol / architecture decisions — not empirical
program-compat findings) are mirrored 1-1 into loadable agent skills so a
decision recorded once in memory is also visible as a skill, with drift made
mechanical instead of silent.

Contract:
- A memory is CORE iff its frontmatter has `core_memory: true` (plus
  `core_skill: .claude/skills/core-memory/<slug>.md`), and its body carries a
  visible `> **CORE-MEMORY**` tag. The **memory file is the source of truth**.
- The mirror skill at `.claude/skills/core-memory/<slug>.md` DUPLICATES the
  memory body (content, not a pointer) between `<!-- BEGIN/END
  CORE-MEMORY-MIRROR -->` markers. `.claude/` is gitignored in the parent, so
  mirrors are machine-local like the memory store.

Tooling (in the tracked `scripts/`):
- `scripts/lint-memory-skill-sync.rs` — read-only; reports MISSING / STALE /
  ORPHAN / metadata problems and exits non-zero on any. Run after editing any
  core memory.
- `scripts/sync-memory-skill.rs` — regenerate mirrors from memories: no args =
  refresh all; `--promote <slug>…` = mark core + create mirror; `--demote` =
  reverse; `--check` = dry-run.

Note: the original task assumed a sqlite memory DB; this project's store is
FILE-BASED markdown (`~/.claude/projects/-home-newton-work-dev-hermit/memory/`),
overridable via `HERMIT_MEMORY_DIR`. See
[[hermit-agents-md-project-scoped-claude-symlink]] and
[[progress-reports-location-and-skill-symlink]] for other skill-location facts.

**Why:** memories and skills drift when a policy changes in one place only; a
mechanical lint catches it. **How to apply:** after editing a core memory, run
`scripts/sync-memory-skill.rs` then `scripts/lint-memory-skill-sync.rs`; to make
a new memory core, `scripts/sync-memory-skill.rs --promote <slug>`.
<!-- END CORE-MEMORY-MIRROR -->
