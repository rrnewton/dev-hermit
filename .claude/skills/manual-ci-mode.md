---
name: manual-ci-mode
description: "Compatibility pointer for the former manual-CI and coalesced-landing runbook. Load pr-landing-planner for current backlog planning and evidence rules."
---

# Historical manual-CI alias

The queue thresholds and per-PR landing commands formerly stored here were a
dated operating snapshot and are not a second landing protocol.

Load [pr-landing-planner](pr-landing-planner/SKILL.md) for serial-versus-coalesced
batch planning, soft-versus-hard green, validation evidence, merge serialization,
and landing proof. For Hermit execution, use `ci-hub/landing/land-pr.sh` under
`AGENTS.md`.

Do not add substantive landing rules here. Update the canonical agent-utils
skill or the executable that enforces the rule.
