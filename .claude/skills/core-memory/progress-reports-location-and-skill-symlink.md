---
name: core-memory-progress-reports-location-and-skill-symlink
description: "Progress reports go in docs/progress-reports/ (NOT ai_docs/); the progress-rubric skill is symlinked into the hermit submodule (CORE-MEMORY mirror of memory/progress-reports-location-and-skill-symlink.md)"
---

# CORE-MEMORY: progress-reports-location-and-skill-symlink

<!-- GENERATED MIRROR of core memory `progress-reports-location-and-skill-symlink`. Source of truth is the memory
     file `progress-reports-location-and-skill-symlink.md`. Regenerate: scripts/sync-memory-skill.rs. Verify in
     sync: scripts/lint-memory-skill-sync.rs. Do NOT hand-edit inside the
     markers — edit the memory and re-run sync. -->

<!-- BEGIN CORE-MEMORY-MIRROR (source: progress-reports-location-and-skill-symlink.md) -->
Canonical progress-report location = `docs/progress-reports/vN-YYYY-MM-DD.md`
(where `scripts/progress-report.sh` writes; real reports v3-2026-07-22.md /
v3-2026-07-23.md live there in the hermit submodule). The `progress-rubric`
skill USED to say `ai_docs/progress-reports/` — stale on both counts. Parent
repo's `ai_docs/transient/` held 14 older narrative reports (README calls it the
home for point-in-time snapshots) — a second, informal location.

REPO STRUCTURE GOTCHA (cost real investigation 2026-07-23):
- Parent `.llms/skills` is a SYMLINK → `../.claude/skills`, which resolves INTO
  the hermit submodule: `hermit/.claude/skills/progress-rubric/SKILL.md`. So
  editing the "parent" skill actually dirties the SUBMODULE working tree, and
  the parent repo does NOT track the skill. Persisting a skill edit needs a
  hermit-submodule PR, not a parent commit. `git add` of the skill path fails
  with "beyond a symbolic link".
- Parent `docs/` IS a real parent dir (reports committable to parent devbig-lead).
- Parent main working checkout carries concurrent-agent dirt (PROJECT_VISION.md,
  alignment_reminder_prompt.md, hermit+reverie pin bumps, dirty submodule).
  NEVER `git add -A` here — stage exact paths only (skill rule + [[parked-slot-reuse-is-racy]]).

Delivered v4-2026-07-23.md report in parent devbig-lead (commit f6c66fd). A v3
for today already existed in the submodule (c88bc0f) — check before creating to
avoid dupes.
<!-- END CORE-MEMORY-MIRROR -->
