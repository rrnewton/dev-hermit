# core-memory skill mirrors

These files are **generated mirrors** of CORE memories (policy / protocol /
architecture decisions) from the file-based memory store at
`~/.claude/projects/-home-newton-work-dev-hermit/memory/`. They exist so that a
core decision recorded in memory is also visible as a loadable agent skill, and
so drift between the two is mechanically detectable.

**Source of truth is the memory file, never the mirror.** A memory is CORE when
its frontmatter declares `core_memory: true` (with `core_skill:` pointing here);
the memory body also carries a visible `> **CORE-MEMORY**` tag.

## Tooling (in `scripts/`)

- `lint-memory-skill-sync.rs` — read-only check that every core memory has an
  in-sync mirror. Reports `MISSING` / `STALE` / `ORPHAN` / metadata problems and
  exits non-zero on any. Run it in CI or before relying on a mirror.
- `sync-memory-skill.rs` — regenerate mirrors from memories:
  - no args → refresh every mirror,
  - `--promote <slug>…` → mark a memory core + create its mirror,
  - `--demote <slug>…` → unmark + remove the mirror,
  - `--check` → dry-run.

## Do NOT hand-edit a mirror

Edit the memory, then run `scripts/sync-memory-skill.rs`. Hand edits inside the
`<!-- BEGIN/END CORE-MEMORY-MIRROR -->` markers are exactly what the linter flags
as `STALE`.

Override the memory store location with `HERMIT_MEMORY_DIR`.
