---
name: hermit-lander
description: "Compatibility role pointer for the Hermit landing agent. Load when planning or executing PR landings; it delegates planning rules to pr-landing-planner and execution rules to the tracked ci-hub lander."
---

# Hermit lander

This role has no independent landing protocol. The workspace's single end-to-end procedure is
[`ai_docs/pr-landing-consolidated-process.md`](../../ai_docs/pr-landing-consolidated-process.md).

1. Load [pr-landing-planner](pr-landing-planner/SKILL.md) and follow its canonical agent-utils skill before
   choosing, ordering, or assigning a landing batch.
   Publish each PR-to-agent assignment on the assigned work's TaskGraph task.
   Task notes are durable but pull-based; for a time-sensitive ready-to-land
   handoff, ask the coordinator to relay after writing the note. Do not use
   agent-side `SendMessage` with an ORC fleet name or report an attempted send
   as delivery.
2. Execute an approved Hermit landing with `ci-hub/landing/land-pr.sh`; that tracked program owns the
   land lock, exact-head validation predicate, fresh-base handling, merge mode, and ancestry check.
3. Follow `AGENTS.md` for review, publication, and task-closure policy.

Do not add substantive landing rules here. Update the owning authority named by the consolidated
process, then keep this file as a thin role pointer.
