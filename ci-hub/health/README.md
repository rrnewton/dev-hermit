# CI health object

Dev-hermit uses the pinned `agent-utils/tick-hub` engine for coordinator
health polling. Project policy stays in this repository:

- `ci-hub/health/tick-hub.yaml` defines checks, cadences, gates, and warning actions.
- `ci-hub/health/tick-hub-state.yaml` is the versioned runtime policy.
- `ci-hub/health/operational_health.py` implements project-specific probes.
- `ci-hub/bin/health-tick` uses `ci-hub/bin/agent-tool` to materialize the exact
  `agent-utils` gitlink on
  demand, provisions the pinned Python runtime dependency into the ignored
  `.tick-hub/venv` when needed, then invokes the real tick-hub CLI.
- `.orc/plugins/hermit-dev/index.ts` is the outer five-minute scheduler and
  wakeup delivery adapter.

The hub checks GitHub current-main health and open-PR red counts every 15
minutes. Every five minutes it checks the live ORC agent snapshot and runs the
same gentle primary-refresh routine as `make checkout-fresh`. Clean product
checkouts fast-forward to `main`; a globally consistent Hermit/Reverie/LiteInst2
gitlink snapshot is committed and pushed to parent `main`. A dirty checkout,
detached or stale branch, Hermit/Reverie pin mismatch, or parent-main race is
preserved and hard-warned instead. An agent is reported stuck when ORC marks it
broken, or when an active agent has no activity for at least 60 minutes. Green
ticks stay quiet; an `ACTION:` or `ERROR:` wakes the coordinator with a
`HARD WARNING`.

Hub cadence data lives in the gitignored `.tick-hub/fired-state`. Health and PR
probes are read-only. The primary-snapshot reminder is intentionally mutating,
but only through clean fast-forwards and a path-limited parent gitlink commit;
it has no reset, clean, force-checkout, or force-push path.

Run a dry tick from the repository root:

```bash
HERMIT_AGENT_SNAPSHOT_JSON='[]' ./ci-hub/bin/health-tick --no-header
```

Run and persist due times:

```bash
HERMIT_AGENT_SNAPSHOT_JSON='[]' ./ci-hub/bin/health-tick --flush --no-header
```

`agent-utils` uses `update = none`, so ordinary recursive submodule checkout
does not fetch it. The runner checks out the exact pinned commit on first use
and fails rather than overwriting a dirty checkout.
