# Benchmark adversarial review, round 1

Date: 2026-07-28

Reviewed implementation: `f028ff977742c36c4b8dc3721908d982a9930155`

Reviewer role: `[adversarial-reviewer agent, gpt-5.6-sol]`

Verdict: **REJECT**

## Major findings

1. Result directories were not owned by one run. Reusing `--output` and then
   failing could leave an old `environment.json` claiming `complete=true`
   beside newly truncated partial result files. A direct harness probe
   reproduced the mixed run.
2. Artifact and source provenance was mutable. Every invocation overwrote one
   global `target/benchmark-pod-image/pod.bin`, so an older result's recorded
   path later named different bytes. Concurrent runs could race. Source state
   was sampled between build phases, and build flags plus the final harness
   binary digest were not recorded.
3. The post-run predicate validated row count and variant presence rather than
   the exact matrix. It accepted a forged run ID, sample 999, wrong operation
   denominator, duplicate/missing sample rows, and a bogus rate. CSV validation
   was only a line count. The harness also checked only aggregate sharded totals,
   the final IPC response, collision-prone aggregate allocator values, and no
   expected value for `checked_get`.

## Minor findings

- Fork-phase waits used a fixed 30-second deadline unrelated to `--timeout`.
- Some samples reused warmed state while process/presence samples recreated it;
  the documentation called all samples independent.
- Logically sharded atomics and fine locks retained false sharing.
- Metadata omitted `Mems_allowed_list`, NUMA topology, and worker placement.
- Release integration ran benchmark smoke only on the default toolchain even
  though the private harness is outside workspace MSRV checks.

## Reproduced evidence

- Rust 1.85 smoke passed with 22 rows.
- A reduced two-sample/four-worker run passed with 44 rows.
- Current and Rust 1.85 strict Clippy, shellcheck, `bash -n`, and rustfmt passed.
- Untampered result rows passed independent matrix and CSV/JSON comparisons.
- Package integration correctly excluded private benchmark paths.

Green workload runs did not compensate for mutable provenance or a validator
which accepted a forged result set.
