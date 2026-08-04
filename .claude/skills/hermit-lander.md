---
name: hermit-lander
description: "Compatibility role pointer for the Hermit landing agent. Load when planning or executing PR landings; it delegates planning rules to pr-landing-planner and execution rules to the tracked ci-hub lander."
---

# Hermit lander

This role has no independent landing protocol.

1. Load [pr-landing-planner](pr-landing-planner.md) and follow its canonical agent-utils skill before
   choosing, ordering, or assigning a landing batch.
2. Execute an approved Hermit landing with `ci-hub/landing/land-pr.sh`; that tracked program owns the
   land lock, exact-head validation predicate, fresh-base handling, merge mode, and ancestry check.
3. Follow `AGENTS.md` for review, publication, and task-closure policy.

Do not add substantive landing rules here. Update the canonical planner skill or the executable that
enforces the rule, then keep this file as a thin role pointer.
