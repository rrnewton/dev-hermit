---
name: hermit-lander
description: "Compatibility role pointer for the Hermit landing agent. Load whenever planning, assigning, recovering, or executing a Hermit PR landing; it delegates planning to pr-landing-planner and execution to the safe exact-head ci-hub lander."
---

# Hermit lander

This role has no independent landing protocol.

1. On startup or replacement, run `ci-hub/ci-hub inherit-obligations --agent
   <registered-agent>` and inspect every inherited remediation before accepting
   new queue work. Then load
   [pr-landing-planner](../pr-landing-planner/SKILL.md) and follow its canonical agent-utils skill before
   choosing, ordering, or assigning a landing batch.
   Publish each PR-to-agent assignment on the assigned work's TaskGraph task.
   Task notes are durable but pull-based; for a time-sensitive ready-to-land
   handoff, ask the coordinator to relay after writing the note. Do not use
   agent-side `SendMessage` with an ORC fleet name or report an attempted send
   as delivery.
2. Execute a task-authorized Hermit landing only through
   `ci-hub/bin/safe-exact-head-land --repo rrnewton/hermit --pr <PR>
   --expected-head <40-hex-X> --actor <registered-agent> --json`. This is the
   live no-rewrite executor: it owns the land lock, exact-head validation,
   durable intent/recovery, fsynced exact-operation mutation barrier,
   synchronous REST merge guarded by `sha=X`, replay-tree proof, and
   exact-landed-SHA obligation handoff. The barrier clears only after the
   handoff is durably armed. If it refuses or reports
   pending, preserve and resume that attempt; never bypass it with a raw
   `gh pr merge`, branch rewrite, or another landing script.
3. Do not invoke `ci-hub/landing/land-pr.sh` as landing authority or fallback.
   It remains an executable file, and `parallel-prevalidate.sh` still defaults
   to it; removing that active caller is an unresolved fleet-wide migration
   blocker. This normative prohibition does not imply mechanical disablement.
4. Follow `AGENTS.md` for review, publication, and task-closure policy.

Do not add substantive landing rules here. Update the canonical planner skill or the executable that
enforces the rule, then keep this file as a thin role pointer.
