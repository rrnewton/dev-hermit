# cpu_timeout derivation pipeline — run of 2026-08-03

## Question

The DAG manifests (`hermit/ci/dag/portable.json`, `privileged.json`) give every
node a **wall** `timeout` and memory hints, but **zero** nodes carry a
`cpu_timeout`. Wall timeouts catch *stuck-and-idle*; they do **not** catch a node
that *burns CPU* while wedged (today's reap-spin pinned a core but never tripped
its wall bound). We want a per-node CPU-time budget — but the standing constraint
is: **do not hand-write 54 guesses.** Derive from measurement, leave a node
UNSET where data is insufficient and say so, and build the derivation as a
*reusable pipeline* so it does not rot as nodes are added.

## Method

`hermit/ci/dag/derive-cpu-timeouts.rs` (rust-script) is the pipeline. It:

1. reads the **node universe** directly from the manifests (serde_json), so the
   set of nodes is never hand-maintained — new nodes appear automatically;
2. ingests per-node CPU-time **samples** from CSV (`--samples`, repeatable),
   auto-detecting the column shape (`cpu.usage_usec`, `cpu.user_usec` +
   `system_usec`, `cpu_s`, or `user_s`+`sys_s`) and filtering to successful runs;
3. per node with `n >= --min-samples` (default 5), sets
   `cpu_timeout = round(max(observed cpu_s) * --headroom)` (default headroom 1.5,
   `--floor` 0), anchored on the **distribution max**, not the median — a CPU
   budget must clear the worst legitimate run or it flakes;
4. for every node **without** enough samples, emits it as **UNSET** with the
   reason, and never invents a value;
5. `--apply` writes the derived values back as a single `"cpu_timeout": <n>,`
   line per node (idempotent text edit; `--self-test` proves round-trip +
   idempotency).

### Reproduce

```sh
cd hermit   # the checkout whose ci/dag manifests you are deriving for
./ci/dag/derive-cpu-timeouts.rs \
  --samples <ambient-samples.csv> \
  --samples <controlled-load-samples.csv> \
  --step e2e/metadata \          # single-node study CSV: force the step key
  --format human                 # or json; add --apply to write manifests
./ci/dag/derive-cpu-timeouts.rs --self-test   # pure-function round-trip checks
```

The only sample data that exists today is the 35-sample `e2e/metadata` study in
[`../cpu-time-timeout-manifest-node_20260803/`](../cpu-time-timeout-manifest-node_20260803/)
(20 ambient + 15 under controlled load). Those two CSVs are the `--samples`
inputs above; `derivation-report.txt` and `derivation-output.json` here are this
run's captured output.

## Results

**Universe: 50 unique `group/job` keys** across the two manifests (the earlier
"54 nodes" figure counts the cross-manifest duplicates; deduped by key it is 50).

| outcome | count | nodes |
| --- | --- | --- |
| **SET** (justified by measurement) | **1** | `e2e/metadata` → `cpu_timeout: 18` |
| **UNSET** (honestly left without one) | **49** | every other node — `no CPU-time samples` |

`e2e/metadata`: n=35, max observed CPU 12.24 s → round(12.24 × 1.5) = **18 s**.
The wall `timeout` (60 s portable / 20 s privileged) is retained as the
idle-hang backstop; 18 s CPU is the burn-a-core backstop.

The full 49-node UNSET list is in `derivation-report.txt` /
`derivation-output.json`. **This is the honest before/after: 0 → 1 justified
`cpu_timeout`, with 49 nodes deliberately left unset for lack of data.**

### Why only one node has data

Per-node CPU-seconds come from the runner's cgroup `cpu.stat` (`usage_usec`),
which is populated **only under `--cgroups`**. This host's cgroup v2 scope
delegates only `io memory pids` — **no `cpu`/`cpuset` controller** — so the
runner cannot generate per-step `cpu.usage_usec` here, and there is no per-step
`getrusage` fallback (the perflog captures only whole-run `getrusage`). So real
per-node CPU data for the other 49 nodes is genuinely unavailable today; that is
exactly the "leave it unset and say so" case, not an omission.

## Update: opt-out default makes this pipeline the derive step (2026-08-03)

The owner then inverted the runner's resource-limit default, which *supersedes*
the "how do we get data for 49 nodes" problem above:

- Boxing becomes **opt-out**: cgroups **ON by default**, escape hatch
  `--unsafe-no-cgroups` (deliberate friction, use logged/reviewable).
- An **undeclared** node gets a deliberately **small default cap**: 1 core,
  1 GB memory, **10 s CPU time**.
- That tight default is a **forcing function**: run the full DAG once and every
  node that breaches its default reports its real requirement. The **breach
  table** is the empirical measurement; there is no guessing.

This pipeline is exactly the **derive step** in the owner's migration order:
*(1) flip default → (2) run DAG, collect breach table → (3) derive each
declaration from its measured breach → (4) land declarations first → (5) land
the flip.* Feed the breach table in as `--samples` and this tool emits each
breaching node's `round(max_cpu × 1.5)` declaration; non-breaching nodes stay
UNSET because the 10 s default already suffices for them. So the two lasting
outputs — the human breach message and the machine breach record — become this
tool's input, and the UNSET list stops meaning "unknown" and starts meaning
"fits under the default." `e2e/metadata` (max 12.24 s CPU) would breach the 10 s
default, which independently confirms it needs the derived 18 s declaration.

The runner change (opt-out flip, small default, the breach message + structured
ci-hub record, both Rust and Python) is tracked under task
`cgroups-opt-out-with-small-default-cap`; this hermit-side pipeline is the
consumer that turns its breach table into manifest declarations.

## Landing chain (why the values do not enforce yet)

`cpu_timeout` is **inert** against the currently pinned runner and only becomes
enforcing after a specific cross-repo chain completes:

1. **Enforcement is unmerged.** `cpu_timeout` is enforced only by the Python
   runner's 1 Hz cgroup monitor (`scheduler.py`: `if cpu_used_s >=
   step.cpu_timeout: reap`), which lives **only on branch
   `codex/cpu-time-timeout`** (agent-utils PR #5, DRAFT; Python-only, Rust not
   done). SOURCE-VERIFIED: agent-utils `main` (`1c0e9c3`, v0.11.x) has **no**
   `cpu_timeout` in `py/scheduler.py` or `rs/src` at all, and `--cgroups` there
   is still opt-in (`cli.py:370`). A separate `origin/ci/cpu-time-rlimit-timeout`
   (PR #4) enforces the CPU cap via **`RLIMIT_CPU`/`prlimit`**, which works
   **without** the cgroup `cpu` controller (host-portable). None of this is in
   the version hermit pins.
2. **The pin is 9 minors behind.** `hermit/ci/dag/README.md` pins
   `rrnewton/agent-utils` **v0.2.0** (`84580db`). Reaching hermit CI requires a
   pin bump **v0.2.0 → v0.11.x**.
3. **`--cgroups` must be wired on the invocation sites.** No workflow passes
   `--cgroups`; without it the monitor never runs and `cpu_stats` is `None`. That
   wiring is task `impl-ci-dag-mem-limits` (owner hermit-ci), coordinated as a
   clean split: hermit-ci owns `--cgroups` + `--max-mem`/`MemoryMax`; this task
   owns only the `cpu_timeout` derivation pipeline + values.
4. **Byte-safe until then.** The pinned **v0.2.0** runner parses both modified
   manifests without error (unknown `cpu_timeout` key ignored; verified: 47
   portable / 7 privileged steps parse). So landing the value now breaks
   nothing and pre-stages the data for when the chain completes.

Once `--cgroups` runs, each such run drops per-node `cpu.usage_usec` into
`--perf-dir` CSVs; re-running this pipeline over them fills in the 49 UNSET
nodes with measured values automatically. **That is the anti-rot property:** the
node set comes from the manifests and the values come from the last real run, so
neither is hand-maintained.

## Files

- `derivation-report.txt` — human report of this run (1 SET, 49 UNSET).
- `derivation-output.json` — machine-readable per-node result.
- `metadata.json` — SHAs, command, host, inputs.
