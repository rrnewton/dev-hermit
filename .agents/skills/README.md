# Codex skill entrypoints

Stock Codex discovers repository skills here. The generated `SKILL.md` files
carry trigger metadata and route to the canonical coordinator instructions in
`.claude/skills/`; this avoids maintaining two policy bodies.

Run `scripts/check-codex-setup.py --write` after an intentional canonical skill
edit, then run `scripts/check-codex-setup.py`. Do not hand-edit generated
entrypoints.
