# ci-hub CI shard

The `Dev-hermit operational tooling` workflow owns ci-hub's repository-specific
tests. It deliberately has two evidence layers:

- Deterministic tests replace GitHub, TaskGraph, and agent-utils with controlled
  responders. Every typed subcommand must return under a 15-second wall limit
  and a 5-second CPU limit. The composite health test stalls both live-query
  dependencies and requires explicit `DEGRADED`, `UNAVAILABLE`, and
  `PARTIAL RESULT` output. The land-lock test covers
  acquire/renew/release/status/run against an isolated lock path.
- The live probe queries `rrnewton/dev-hermit` with the pinned agent-utils
  planner. Exit 0 or 1 means GitHub responded with a health state. Exit 2 is
  accepted only with an explicit unavailable/incomplete marker. A timeout,
  crash, missing health section, missing final wall/CPU report, or any other
  exit is a ci-hub failure.

The split prevents GitHub latency from being reported as a ci-hub regression
without allowing a broken or stalled ci-hub process to pass as infrastructure
noise.
