# Portable validation DAG: work versus critical path

Date: 2026-08-05

## Verdict

The 484-second warm portable gate is **not primarily an outer-scheduler width
problem**. The manifest at the measured commit is dominated by serialization:

- its dependency-only critical path is a four-node chain ending in the large
  `test.strict_compat` join;
- independently, `resource_caps.hermit_guest = 1` forces sixteen guest nodes to
  run one at a time.

At the measured SHA, total declared work divided by historical width 2 is 2,680
seconds, while the single-lane `hermit_guest` work is already 2,640 seconds.
Thus the manifest's own model says width 2 is only 40 declared seconds above an
unavoidable resource-serialization bound; at width 4 and above that bound
dominates. Increasing outer width can shorten cold/build-heavy fan-out, but it
cannot shorten the strict-compat join or the `hermit_guest` serialized lane.

The comparable warm results already recorded on this task, 484 seconds at
`-j2` versus 426.2 seconds at `-j16`, are consistent with that shape: an 8x
width increase bought only about 12% wall time. They are not a same-SHA A/B and
are used only as corroboration, not as the source of the static verdict.

This is therefore a **serial-latency problem: a short dependency chain of long
nodes, reinforced by an intentional single-lane guest resource**. It is not a
missing-parallelism-width problem.

## Scope and evidence boundary

This is local static analysis of two locally available Hermit snapshots:

- measured snapshot `9ebe1608303c66bfaa4b9c7d0521a30d9519c182`,
  where the owner recorded the 484-second run; and
- current local `main` snapshot
  `b64d893ae9ea6404472eae9cb86102d91ec642ef`, used to check whether the
  conclusion survives DAG drift.

It reads:

- `ci/dag/portable.json`
- `ci/dag/privileged.json`
- `ci/dag/README.md`
- the scheduler-width plumbing in `validate.sh`

No validation command, DAG node, fetch, or other network operation was run.
The 484-second and 426.2-second observations above are pre-existing results in
the TaskGraph record, not new measurements from this analysis. They were taken
on different revisions and are not presented as a controlled A/B.

The manifests' `hint.est_duration_s` values are explicitly documented as
hand-estimated scheduling hints, not benchmarks. Consequently, their sum and
critical path describe the declared cost shape but must not be presented as a
prediction of the measured 484-second wall time.

## Computed graph metrics

For each node `v`, this analysis uses:

```text
work(v) = hint.est_duration_s(v)
critical_path(v) = work(v) + max(critical_path(dep))
total_work = sum(work(v))
```

The maximum `critical_path(v)` over all nodes is the dependency-only weighted
critical path. Resource-cap lower bounds are calculated separately because a
scarce-resource edge is enforced by the scheduler but is not represented in
the node's `deps` array.

| Snapshot / manifest | Nodes | Edges | Total work | Dependency critical path | Work / path |
| --- | ---: | ---: | ---: | ---: | ---: |
| measured `9ebe1608` / portable | 47 | 79 | 5,360 s | 1,265 s | 4.24 |
| measured `9ebe1608` / privileged | 7 | 10 | 255 s | 170 s | 1.50 |
| current `b64d893a` / portable | 47 | 77 | 4,898 s | 1,060 s | 4.62 |
| current `b64d893a` / privileged | 8 | 10 | 285 s | 195 s | 1.46 |

At the measured SHA, portable's dependency critical path is:

| Node | Declared duration | Cumulative |
| --- | ---: | ---: |
| `e2e.metadata` | 5 s | 5 s |
| `build.workspace` | 360 s | 365 s |
| `lint.clippy` | 300 s | 665 s |
| `test.strict_compat` | 600 s | 1,265 s |

`test.strict_compat` alone accounts for 600/1,265, or 47.4%, of that path. It
is an eight-input join and cannot begin until the slowest required predecessor
finishes. Current local main preserves the same four-node path while reducing
its declared build cost: 30 + 130 + 300 + 600 = 1,060 seconds. The conclusion
therefore survives the intervening DAG edits.

Current privileged's path is included as a control:

```text
e2e.metadata (30) -> build.manifest_guests (75)
                  -> e2e.manifest_applications (90) = 195 s
```

It is small in both work and path length, matching the observed fact that the
portable lane—not the privileged lane—dominates full validation wall time.

## Resource serialization is stronger than global width

Both the measured and current portable manifests declare the same scarce-resource
caps and work totals:

| Resource | Cap | Nodes using it | Declared work using it | Makespan lower bound |
| --- | ---: | ---: | ---: | ---: |
| `hermit_guest` | 1 | 16 | 2,640 s | 2,640 s |
| `manifest_guest` | 4 | 13 | 640 s | 160 s |

The `hermit_guest` work must run serially regardless of outer `-j`. Under the
measured snapshot's declared hint model, the portable makespan lower bounds are
therefore:

```text
dependency critical path       = 1,265 s
hermit_guest work / cap         = 2,640 s
manifest_guest work / cap       =   160 s
total work / outer width 2      = 2,680 s
total work / outer width 4      = 1,340 s
total work / outer width 16     =   335 s
```

At historical width 2, the global-width work bound is only 40 seconds, or 1.5%,
above the `hermit_guest` serialization bound. By width 4, the resource bound is
almost twice the global-width bound; at width 16, global width is plainly not
limiting. On current local main, `total_work / 2 = 2,449` is already below the
unchanged 2,640-second resource bound.

These absolute seconds do not match warm reality because the hints are not
measurements. The ordering of the bounds is still useful: adding outer lanes
does not remove either the join chain or the resource serialization.

## Why the warm gate was 484 seconds

The DAG has broad fan-out after its build barriers, so many short or warm-cache
nodes overlap. Wall time then collapses toward the longest serialized region:

1. `e2e.metadata` gates the shared builds.
2. `build.workspace` gates most tests and documentation.
3. `lint.clippy` is the slowest declared predecessor of `test.strict_compat`.
4. `test.strict_compat` is a large composite node, is tagged
   `latency-bound`, consumes the single `hermit_guest` slot, and runs only after
   all eight declared predecessors complete.
5. Other guest tests also consume that same one-wide resource, while manifest
   buckets are capped at four.

The current `validate.sh` default is host-adaptive and capped at 16; on the
316-core host it resolves to 16. The prior comparable warm observations show
that moving from 2 to 16 lanes reduced 484 seconds to 426.2 seconds, only 57.8
seconds. Because those observations are not same-SHA, they corroborate but do
not independently prove the static finding.

The exact number 484 cannot be reconstructed even from the matching historical
manifest hints: its declared critical path is 1,265 seconds, greater than the
entire observed wall. Doing so would require the original same-SHA step-profile
CSV; the DAG README explicitly requires measured profiles before treating
durations as benchmarks. This task forbids running validate, so no replacement
measurement was manufactured.

## Consequence

The relevant optimization targets are inside or immediately before the serial
region:

1. split or safely parallelize the strict-compatibility matrix;
2. remove only proven-nonessential join dependencies, while preserving the
   target/cache mutation safety for which the barrier exists;
3. shorten the longest manifest buckets, especially language runtimes; and
4. relax `hermit_guest: 1` only after proving concurrent guest executions do
   not share mutable state or determinism-sensitive resources.

Raising global `CI_DAG_JOBS` further is not the primary fix.

## Reproduction of the static calculation

The calculation was performed with `jq` recursion over the manifest's node
weights and dependencies. A compact equivalent is:

```bash
git -C hermit show 9ebe1608:ci/dag/portable.json | jq '
  (.steps
   | map({key:(.group + "." + .job),
          value:{duration:(.hint.est_duration_s // 0), deps:(.deps // [])}})
   | from_entries) as $nodes
  | def cp($id):
      $nodes[$id] as $n
      | $n.duration + ([$n.deps[] | cp(.)] | max // 0);
  {
    nodes: ($nodes | length),
    edges: ([$nodes[] | .deps | length] | add),
    total_work_s: ([$nodes[] | .duration] | add),
    critical_path_s: ([$nodes | keys[] as $id | cp($id)] | max)
  }
'
```
