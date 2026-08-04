# Cargo lock contention in the portable DAG

Status: measurement in progress. This experiment compares the ten Cargo nodes
that emitted package-cache/build-directory lock waits in PR #1592's retained
full-profile log. Shared and per-node-target arms start from identical Btrfs
snapshots, share an experiment-private Cargo home, and trace lock syscall wall
time. This excludes unrelated fleet Cargo activity and the later
`test.strict_compat` OOM from the attribution.

Exact Hermit head: `8078d089e3c8c968e9f416b215f027a342b847ac`.
