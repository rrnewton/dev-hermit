# Codex skill entrypoints

Stock Codex discovers repository skills here. Each tracked entry is a
whole-package symlink to the canonical package in `.claude/skills/<name>/`, so
Claude, Codex, and `.llms` consumers read the same `SKILL.md` and bundled
resources. Do not replace package links with generated pointer files or with a
link to `SKILL.md` alone.

`pr-landing-planner` is the deliberate temporary exception: Codex must use the
reviewed `agent-utils/skills/pr-landing-planner/SKILL.md` path named by
`AGENTS.md`. The live checker rejects a `.agents` planner link until that
agent-utils package has completed the semantic review recorded by the checker;
absence here is quarantine, not an instruction to skip the mandatory planner.

Run `scripts/check-codex-setup.py` after an intentional skill change. The
checker is read-only and rejects wrong, dangling, escaping, root-level, and
file-only links.
