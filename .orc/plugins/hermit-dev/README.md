# hermit-dev ORC plugin

This version-controlled project plugin loads the canonical `dev-hermit`
coordinator policies and owns the thin ORC adapter for operational polling.
The policy text is not duplicated: startup reads the workspace `AGENTS.md` and
registers it as the `hermit-dev` skill.

## Responsibilities

- Register and activate the canonical `AGENTS.md` policy.
- Register the two narrow coordinator skills stored beside this file.
- Expose `await orc.hermit-dev.activate()` and
  `orc.hermit-dev.status()`.
- Register the fork-safe `gh-issue-create` wrapper.
- Run the real `agent-utils/tick-hub` operational poll every five minutes.
  The versioned config is `ci-hub/health/tick-hub.yaml`; the plugin only supplies the
  live `orc.listAgents()` snapshot, invokes `ci-hub/bin/health-tick`, and sends a
  `HARD WARNING` wakeup when the hub emits an action/error or fails.
- Recover any write-ahead land intent interrupted between merge and arm, poll
  obligations every fifteen seconds, and send an advisory wake when an exact-SHA
  verifier fails. A completed send is recorded as `sent_unacknowledged`; a
  lander's startup `inherit-obligations` scan records acknowledgment. Durable
  state, the five-minute tick, and fresh-reader discovery own correctness, so a
  wake lost during recycling costs latency rather than losing the obligation.
- Through that tick, gently fast-forward clean product primaries and publish a
  coherent parent gitlink snapshot; dirty or inconsistent state is preserved
  and surfaced as a hard warning.
- Through that tick, reconcile containers created by
  `scripts/agent-podman.rs`. Only a labelled `agent`-lifetime container whose
  exact creator invocation is gone is gracefully stopped and removed.
  Task-retained, owner-unknown, and legacy containers are surfaced without
  deletion; see `ci-hub/containers/README.md`.
- Kill the obsolete `hermit-dev-pr-health` reminder workflow at startup. The
  replacement workflow has the distinct ID
  `hermit-dev-operational-health-v1`, so an old durable source cannot be
  silently retained.

## Loading

Start ORC from the dev-hermit workspace so it loads the tracked project config
at `.orc/config.js`. That config imports the versioned module directly:

```js
import "./plugins/hermit-dev/index.ts";
```

The direct import is intentional: the installed ORC build finds the project
config but does not add project plugin names to `loadPlugin()`'s registry in
the session runtime. A relative module import stays inside the project module
root and is covered by an isolated headless boot test.

`~/.orc/config.js` may contain a comment referring to this project config plus
genuine per-user settings such as the selected model. It must not contain a
plugin copy, workflow, polling, or policy logic. Do not copy or symlink this
module into `~/.orc/plugins/`: a copy drifts from version control, while a
symlink is rejected by ORC's module-root sandbox.

The historical `install.sh` now checks this invariant and explains how to
remove a stale home copy; it no longer installs one.

## Verify

From the dev-hermit root:

```bash
HERMIT_AGENT_SNAPSHOT_JSON='[]' ./ci-hub/bin/health-tick --no-header
```

A live session's `orc.hermit-dev.status()` must report the five-minute tick
interval and `ci-hub/health/tick-hub.yaml` config. `orc.listWorkflows()` must show the
sleeping `hermit-dev-operational-health-v1` and
`hermit-dev-speculative-land-remediation-v1` workflows and must not show
`hermit-dev-pr-health`.
