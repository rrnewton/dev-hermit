---
name: hermit-lander
description: "Compatibility role pointer for the Hermit landing agent. Load when planning or executing PR landings; it delegates planning to pr-landing-planner and fails closed when no current exact-head executor is deployed."
---

# Hermit lander

This role has no independent landing protocol.

1. Load [pr-landing-planner](pr-landing-planner.md) and follow its canonical agent-utils skill before
   choosing, ordering, or assigning a landing batch.
   Publish each PR-to-agent assignment on the assigned work's TaskGraph task.
   Task notes are durable but pull-based; for a time-sensitive ready-to-land
   handoff, ask the coordinator to relay after writing the note. Do not use
   agent-side `SendMessage` with an ORC fleet name or report an attempted send
   as delivery.
2. Do not invoke `ci-hub/landing/land-pr.sh`; its mutating path is deliberately
   fail-closed because it cannot atomically bind the observed target base. The
   parent receipt bundle and `green-source-decision` are semantic/verifier
   components, not a merge executor. If the live repository has no separately
   deployed exact-head executor, report that disposition and stop before a raw
   merge command.
3. Follow `AGENTS.md` for review, publication, and task-closure policy.

Do not add substantive landing rules here. Update the canonical planner skill or the executable that
enforces the rule, then keep this file as a thin role pointer.
