---
name: pr-landing-mechanics-merge-gate-uptodate-chase
description: "Deprecated compatibility alias for historical PR-landing mechanics. Load pr-landing-planner instead; do not use the July 2026 admin/stale-gate instructions."
---

# Historical landing-mechanics alias

The July 2026 instructions formerly stored here are obsolete. They described a stale-gate refire
loop and standalone `--admin --squash` escape that conflict with the current landing contract.

Load [pr-landing-planner](../pr-landing-planner/SKILL.md) for planning. For Hermit execution, use
`ci-hub/bin/safe-exact-head-land` through the
[hermit-lander](../hermit-lander/SKILL.md) role under `AGENTS.md`.
`ci-hub/landing/land-pr.sh` remains executable through an unresolved legacy
caller, but is not authority and must not be used as a fallback. This file
remains only so old references resolve to the canonical
skills instead of silently loading historical rules.
