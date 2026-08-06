# Codex skill entrypoints

Stock Codex discovers repository skills here. Each tracked entry is a
whole-package symlink to the canonical package in `.claude/skills/<name>/`, so
Claude, Codex, and `.llms` consumers read the same `SKILL.md` and bundled
resources. Do not replace package links with generated pointer files or with a
link to `SKILL.md` alone.

`pr-landing-planner` is the deliberate external-package exception. The checker
accepts only the fixed `.claude/skills/pr-landing-planner` link to
`agent-utils/skills/pr-landing-planner` and rejects a duplicate `.agents`
entry. Codex uses the registered agent-utils package named by `AGENTS.md`;
absence here is intentional, not an instruction to skip the mandatory planner.

Run `scripts/check-codex-setup.py` after an intentional skill change. The
checker is read-only and rejects wrong, dangling, escaping, root-level, and
file-only links.
