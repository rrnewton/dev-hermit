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

An hourly `unowned_residue` reminder catches the complementary case where a
correct refusal or recycle leaves no active owner and therefore emits no normal
failure. `residue_sweep.py` combines the canonical orphan-task detector, exact
declined-action note markers, and a two-signal held-slot predicate (dead
registered owner **and** no live process cwd under the slot). The gate only
reports and wakes the coordinator; `--route` records typed TaskGraph
dispositions, while task reassignment and slot lifecycle remain coordinator
operations.

## What this hub structurally cannot see: its own host dying

Everything above runs inside ORC, and `.orc/plugins/hermit-dev/index.ts` is its
outer scheduler. On 2026-08-06 the ORC process and its tmux server died
together, so the scheduler, the tick, and every gate above died in the same
event and reported nothing. A human noticed, after a long silent interval.

Two watchdogs therefore live outside ORC, on independent `systemd --user`
timers. They answer different questions and must not be confused:

- `hermit-health-staleness.timer` asks **"is the tick still firing?"**, purely
  by the mtime of the heartbeat and `.tick-hub/fired-state`. It would read a
  dead ORC as perfectly healthy for as long as those files stay fresh.
- `hermit-orc-liveness.timer` asks **"is ORC itself alive?"**, by cross-checking
  active session rows in `~/.orc/index.db` against `/proc`
  (`scripts/orc-liveness-watchdog.rs`, unit sources under `scripts/systemd/`).
  It runs every two minutes and shares no code, state file, or timer with the
  tick path.

The liveness watchdog trusts neither of the two available proxies.
`sessions.status = 'active'` is a label ORC wrote, not a fact about now — a
SIGKILL leaves it reading `active` forever, which is exactly the case that must
be caught — so it is treated as a claim to dereference. And "the PID exists" is
a proxy for "ORC is running", because PIDs recycle, so the check reads
`/proc/<pid>/cmdline` and compares argv[0]'s basename; a live PID belonging to a
different program is reported as `DEAD_PID_RECYCLED`, never folded into `LIVE`
and never folded into `DEAD_STALE_PID`.

A missing or unreadable database reports `UNKNOWN_*` and alerts rather than
degrading to silence: absence of evidence is not evidence of health. Alerting is
durable-log-first and never claims a delivery it did not make, since the alarm
case is by definition "ORC is not running" and pushing it through an ORC tmux
pane would be self-defeating. Auto-restart is off by default — relaunching Orc
is owner-only while the running process injects a Codex sandbox flag that
post-1.0 Codex rejects, so restarting would faithfully reproduce a coordinator
that cannot spawn.

Read the verdict without running anything:

```bash
cat ~/.local/state/hermit-orc-liveness.status      # latest verdict, one line
tail ~/.local/state/hermit-orc-liveness.jsonl      # durable per-check records
./scripts/orc-liveness-watchdog.rs --dry-run       # check now, write nothing
rust-script --test scripts/orc-liveness-watchdog.rs
```

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
