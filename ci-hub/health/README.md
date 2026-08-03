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

## Timing-sensitive load precondition

`./ci-hub/bin/load-probe` takes a one-second host-counter sample and exits
zero only when the box is suitable for timing-sensitive work. The default
policy requires executing CPU at or below 50% and `MemAvailable` at or above
10%. Load average is diagnostic only: it includes uninterruptible sleep and is
never used for the verdict. Override thresholds explicitly when a measurement
has a stricter documented precondition.

The report includes measured executing/idle/iowait CPU, top CPU consumers,
`R/S/D/Z` process states, memory/PSI, and the numeric verdict. In a 3pai PID
namespace, aggregate counters remain host-wide but host PIDs are hidden. The
probe says this loudly, prints visible versus cgroup PID coverage, and ranks
cgroup CPU instead of mislabeling the sandbox-local process list as host-wide.
Exit 1 means measured conditions violate policy; exit 2 means required evidence
was unavailable. It reports its cost directly and avoids the `rust-script`
cache, so this is the canonical 3pai/BpfJailer entrypoint. The typed
`./ci-hub/ci-hub load-probe` alias is also available outside that restriction.

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

The same five-minute tick reconciles TaskGraph state, TaskGraph ownership, and
the live `orc.listAgents()` snapshot. `./ci-hub/ci-hub active-work` reports:

- `ORPHANED`: non-implemented in-progress task owned by a dead/retired agent;
- `STALE`: non-implemented in-progress task with no owner;
- `AWAITING-LAND`: in-progress task tagged `implemented`, counted separately
  from active work and not treated as a warning by itself;
- `OFF-BOOK`: busy live agent with no `current_task`; and
- `MISROUTED`: task owner and the agent's actual `current_task` disagree.

The tick atomically retains its latest agent snapshot under
`ignored/ci-hub/agent-snapshot.json`, so the manual command uses the same ORC
view for at most ten minutes. It fails unknown rather than classifying against
a missing or stale snapshot. Use `--json` for the versioned full report and
`--gate` for tick-hub key/value output.

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
