# Operational tick hub

Dev-hermit uses the pinned `agent-utils/tick-hub` engine for coordinator
health polling. Project policy stays in this repository:

- `ops/tick-hub.yaml` defines checks, cadences, gates, and warning actions.
- `ops/tick-hub-state.yaml` is the versioned runtime policy.
- `scripts/operational_health.py` implements the project-specific probes.
- `scripts/run-tick-hub` materializes the exact `agent-utils` gitlink on
  demand, provisions the pinned Python runtime dependency into the ignored
  `.tick-hub/venv` when needed, then invokes the real tick-hub CLI.
- `.orc/plugins/hermit-dev/index.ts` is the outer five-minute scheduler and
  wakeup delivery adapter.

The hub checks GitHub current-main health and open-PR red counts every 15
minutes. It checks the live ORC agent snapshot every five minutes. An agent is
reported stuck when ORC marks it broken, or when an active agent has no
activity for at least 60 minutes. Green ticks stay quiet; an `ACTION:` or
`ERROR:` wakes the coordinator with a `HARD WARNING`.

The only mutable hub data is `.tick-hub/fired-state`, which is gitignored.
The probes are read-only; `--flush` only advances that cadence state.

Run a dry tick from the repository root:

```bash
HERMIT_AGENT_SNAPSHOT_JSON='[]' ./scripts/run-tick-hub --no-header
```

Run and persist due times:

```bash
HERMIT_AGENT_SNAPSHOT_JSON='[]' ./scripts/run-tick-hub --flush --no-header
```

`agent-utils` uses `update = none`, so ordinary recursive submodule checkout
does not fetch it. The runner checks out the exact pinned commit on first use
and fails rather than overwriting a dirty checkout.
