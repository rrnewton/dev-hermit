---
name: core-memory-skill-sync-tooling
description: "Audit the optional local Claude-memory mirrors for coordinator skills. Use only when maintaining that compatibility bridge; preview every change and never let unversioned memory overwrite newer AGENTS.md or versioned skill policy."
---

# Local memory compatibility bridge

The versioned coordinator skills are the portable repository surface. Some
flat `.claude/skills/*.md` files also have one-to-one mirrors in an
operator-local Claude memory directory. That local store is not available to
stock Codex, is not versioned with the repository, and is not an authority over
`AGENTS.md`.

`scripts/sync-memory-skill.rs` has no memory-to-repository mode. Its default
refuses to act, `--check` explains the authority boundary, and explicit
`--adopt-skill .claude/skills/<slug>.md` may export a reviewed repository skill
to the optional local store. The read-only linter gates repository skill
structure; local-memory absence or drift is advisory. To recover useful prose
from local memory, review it and apply an explicit repository patch.

Stock Codex discovery lives under `.agents/skills/<name>/SKILL.md` and is
validated by `scripts/check-codex-setup.py`. After an intentional versioned
skill edit, regenerate those adapters with `scripts/check-codex-setup.py
--write` and run the checker again.
