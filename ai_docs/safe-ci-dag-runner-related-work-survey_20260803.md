# Related Work: Resource-Aware CI Execution and Untrusted-Compute Containment

_Survey for task `safe-ci-dag-runner-related-work-survey`, 2026-08-03._
_Grounded in the runner source at dev-hermit `agent-utils/{rs,py}/safe(_)ci(_)dag(_)runner/` and the boxing proposal `ai_docs/enshrine-box-all-untrusted-compute-proposal.md`._

We are about to invest heavily in `safe-ci-dag-runner` — library mode, cgroup
boxing on by default, and using it as the sole execution substrate for CI /
validation / benchmark compute — having **never surveyed the prior art on the
CI-execution side**. We have surveyed deterministic Linux boot, virtual time
(Kendo/dettrace/DThreads/dOS/Determinator), and gVisor systrap; nothing on
resource-aware build/test scheduling or untrusted-compute boxing. This document
fixes that gap and answers one blunt question up front: **where does a mature
tool already do what we are building, so "extend the library as needed" does not
quietly become "reimplement nsjail, worse"?**

**The spine of this survey is a feature checklist (§2): rows = every capability we
have built or discussed, columns = our honest status + the closest prior art +
a STEAL / DIVERGE / GENUINELY-NOVEL verdict.** It is deliberately not a tool tour;
it answers, per capability, "how does a mature system already do this, and should
we copy it or diverge?" §0 states our baseline; §1 profiles each surveyed system
once (referenced from the table); §3 is the blunt bounded finding.

We survey two families of prior art:

- **Family A — resource-aware build/test DAG schedulers**: Bazel, Buck2, BuildXL,
  Pants, cargo-nextest, Nix, Please, Taskcluster. How do they separate scheduler
  WIDTH from resource QUOTA from CORE-BUDGET, express per-node demand, and profile?
- **Family B — containment for untrusted compute**: nsjail, systemd-run `--scope`,
  raw cgroup-v2, bubblewrap, Firejail, gVisor. How do they enforce, KILL CLEANLY
  on breach, and ATTRIBUTE a breach?

Our own status vocabulary in the table is exact: **IMPLEMENTED** (exists and is
actually enabled in a real workflow/manifest/cron), **BUILT-UNUSED** (works but
nothing in production engages it), **PARTIAL**, **PLANNED** (design/branch only,
not on `main`), **ABSENT**. The BUILT-UNUSED/PLANNED distinction is load-bearing:
several of our headline features are coded but dark.

---

## 0. What we actually built (the baseline the prior art is measured against)

Before comparing, state our real requirements precisely — from the source, not
the brief — because the comparison is only useful against the concrete model.

### 0.1 The three concurrency/quota gates conflated as "-j 2"

The `-j 2` confusion — "we thought we had no scheduler when we had one
configured to two lanes" — is really a conflation of **three independent gates**,
all live in `scheduler.rs`:

1. **Outer scheduler WIDTH** — `jobs` (`-j` / `CI_DAG_JOBS`). The greedy
   longest-processing-time (LPT) ready-set loop launches steps until
   `running.len() >= jobs`. This is the single number people saw and mistook for
   the whole story.
2. **Named-resource QUOTA** — each `Step` declares `hint.resources` DEMAND (a
   `BTreeMap<String,i64>`, e.g. `{"hermit_guest":1}`); `DagConfig.resource_caps`
   is the per-resource CAP. `res_free()` refuses to launch a step whose demand
   would push summed concurrent demand over the cap. This is a **second,
   independent admission gate** — `hermit_guest:1` serializes all hermit-guest
   steps *regardless* of how wide `-j` is (intentional PMU-contention safety in
   the shared CI DAG).
3. **CPA core-budget** — `core_budget` (`P`): the summed inner-parallelism
   (`preferred_inner_jobs`) of concurrently running steps must not exceed `P`
   (`cores_free()`), with a "run alone if wider than the whole budget" escape so
   it never deadlocks.

Plus two finer knobs: **inner width** (`preferred_inner_jobs` → the step's own
`-j` fan-out, appended to its command) and **memory-aware sizing**
(`step_mem_cap_bytes` = `hard_mem_max_bytes`, else `rss_baseline_bytes *
mem_cap_factor`, floored) which becomes the cgroup `memory.max`.

So WIDTH (gates 1, 3, inner) and QUOTA (gate 2, memory) are genuinely distinct
axes in our model — the design lesson we want to check against prior art is
whether the mature tools keep them distinct or also collapse them.

### 0.2 The containment mechanism (`cgroup.rs`)

Two-level cgroup-v2 boxing, default-ON in the `run` CLI:

- **Outer:** re-exec inside a transient `systemd-run --user --scope` with
  `Delegate=yes`, `MemorySwapMax=0`, optional `CPUQuota`, under a shared
  `safe-ci.slice` whose aggregate `CPUQuota` (~90% of cores) bounds the SUM
  across concurrent runs.
- **Inner:** carve one child cgroup per step; the step's bash leader self-moves
  (`echo $$ > cgroup.procs`) BEFORE forking, so every descendant inherits the
  cgroup at fork.
- **Enforce:** per-step `memory.max` + `memory.swap.max=0` (OOM-kill at cap),
  `cpu.max` (rate).
- **Clean kill:** `cgroup.kill` = atomic SIGKILL of the whole subtree, catching
  `setsid`/double-fork escapees a process-group kill misses; `killpg` follows as
  belt-and-suspenders; a normal-exit backstop reaps leftover step cgroups; a
  SIGINT/SIGTERM handler tears the whole outer scope down.
- **Attribution:** `memory.events` `oom_kill` count → `step_failure_reason`
  precedence OOM > timeout > pids-guard > signal > exit, surfacing
  `OOM-KILLED (hit inner MemoryMax; N oom_kill event(s))`; plus per-step
  `memory.peak`, `cpu.stat`, `cpu.pressure` (PSI avg10/avg60), `cgroup.threads`
  as profiling rows.
- **No silent failure:** every degraded cgroupfs write emits a visible `warn`;
  on a host without cgroup-v2 + a systemd `--user` scope the runner refuses to
  run advisory-only (exit 3) unless `--allow-cgroup-failure`.

**Known gap (from our own audit):** we enforce a WALL timeout + `cpu.max` RATE,
but there is **no per-step CPU-TIME budget** (total CPU-seconds). Two other gaps:
the GitHub portable lane bypasses the runner via raw `bash` (`ci/run-node.sh`),
and the Python library entry point defaults to `NoopCgroups`.

---

## 1. The surveyed systems (one profile each)

Cited by the feature table in §2. Primary sources in-line.

### Family A — resource-aware build/test DAG schedulers

- **BuildXL** (Microsoft) — *the strongest overall match.* Implements all three
  gates as distinct concepts: `/maxProc` = WIDTH; per-pip **`weight`** = CORE-BUDGET
  ("total weight of concurrent processes must be < process slots"; `weight ≥
  maxProc` ⇒ run alone; can be **dynamic from historic CPU** via
  `/UseHistoricalCpuUsageInfo`); and `acquireSemaphores?: SemaphoreInfo[]` where
  `SemaphoreInfo = {name, limit, incrementBy}` — a **named** resource, per-pip
  **variable demand** (`incrementBy`), per-name **cap** (`limit`), with
  `Contract.Requires(value ≤ limit)` — exactly our gate-2. `acquireMutexes` =
  the limit-1 sugar. RAM-based throttling + historic-runtime critical-path
  prioritization; filesystem sandbox w/ timeouts. (Pip-Weight.md,
  Transformer.Execute.dsc, ProcessSemaphoreInfo.cs, Scheduler-Prioritization.md.)
- **Bazel** — *strongest for boxing + profiling.* `--jobs` (WIDTH) is orthogonal
  to `--local_resources name=value` pools: `--local_cpu_resources` (core budget),
  `--local_ram_resources`, and `--local_extra_resources` = **arbitrary named
  pools** (our `hermit_guest:1`). Per-action demand via Starlark `resource_set`
  callback `(os,num_inputs)->{cpu,memory,local_test}` or test **tags**
  (`cpu:3`, `resources:<name>:<n>`). Per-action cgroup boxing:
  `--experimental_sandbox_limits`, `--experimental_sandbox_memory_limit_mb`,
  `--experimental_sandbox_enforce_resources_regexp` (turns a declared request into
  an enforced cgroup limit + OOM-kill), `--experimental_cgroup_parent`. **JSON
  trace profile** (`--profile`): per-action `dur`, an in-flight `action count`
  row, a CPU-usage counter row — `jq`-queryable. Affected-test selection is exact
  (it owns the build graph). Flaky handling = `--runs_per_test` /
  `--flaky_test_attempts` (retry-to-tolerate).
- **cargo-nextest** — *closest same-ecosystem analogue.* Cleanly separates all
  three: `--test-threads` (WIDTH) vs per-test `threads-required = N|"num-cpus"`
  (CORE-BUDGET weight vs the global budget) vs `[test-groups] name={max-threads}`
  + `filter`/`test-group` (a **named cap**, `max-threads=1` = mutex). A test
  consumes within *both* global and group limits. Slow-timeout + retries; no
  cgroup metering; "resource" unit is always threads (no arbitrary named cap like
  BuildXL).
- **Buck2** — WIDTH `--num-threads`; hybrid local/remote executor. Named quota =
  `LocalResourceInfo` (**tests only**): a setup command emits N concrete
  instances as a "pool of homogeneous local resources"; the scheduler leases one
  and injects it as an env var (`IDB_COMPANION=…`). No documented global CPU/RAM
  or inner-`-j` budget for local build actions (leans on RE Platform properties +
  `resource_units`). `buck2 log what-ran` / critical-path; Starlark-only profiler;
  per-action local cgroup boxing not in public docs.
- **Pants v2** — global `process_execution_local_parallelism` (≈ combined
  width/core budget); per-process `ProcessConcurrency {exactly|range|exclusive}`
  with `{pants_concurrency}` **templated into argv** and range-processes
  preemptible. No user-facing named quota beyond cores.
- **Nix** — only two *global* knobs: `max-jobs` (WIDTH) and `cores`
  (`NIX_BUILD_CORES` per-derivation hint); explicitly **no summed core budget**
  ("max consumed cores = max-jobs × cores", oversubscription is the user's
  problem). Distributed builds route by `requiredSystemFeatures` vs a machine's
  `supportedFeatures`/`mandatoryFeatures` = **capability matching, not a quota**.
- **Please** — global `NumThreads` only; no per-target demand/quota/budget.
  Weakest.
- **Taskcluster** — cluster-scale: per-worker `capacity` (WIDTH) distinct from
  autoscaled pool min/max ceilings; resource-fit by **routing to a sized worker
  pool** (`taskQueueId`), not a scheduler quota. Scopes = authorization, not
  resource quota. Strong per-task VM/container isolation.

### Family B — containment for untrusted compute

- **raw cgroup-v2** — *validates our mechanism.* `cgroup.kill` = atomic subtree
  SIGKILL, migration-proof (catches `setsid`/double-fork escapees a killpg
  misses) — exactly our choice. Full attribution fields we already read
  (`memory.events` oom_kill, `memory.peak`, `cpu.stat`, `cpu.pressure` PSI,
  `cgroup.threads`). **No native CPU-time kill** (use RLIMIT_CPU); `memory.oom.group=1`
  and `cpu.stat` nr_throttled/throttled_usec are worth adding.
- **systemd-run `--scope`** — our exact outer mechanism: transient scope,
  `Delegate=yes`, cgroup-v2 directives; `RuntimeMaxSec` = **wall** (no CPU-seconds);
  `Result=oom-kill` + `MemoryPeak` = clean attribution.
- **nsjail** — *the "are we reimplementing this, worse?" benchmark.* One
  cgroup-per-exec enforcer: writes cgroup files directly
  (`cgroup_mem_max`→memory.max, etc.), CLONE_NEWPID kill (not `cgroup.kill`),
  **`rlimit_cpu` = RLIMIT_CPU CPU-seconds = exactly our missing gap, for free** —
  but **ZERO cgroup-counter attribution** (no memory.peak/events/cpu.stat), and it
  *is* a real sandbox (namespaces/seccomp/mount/net) which we are **not**.
- **Firejail** — rlimits (`--rlimit-cpu` CPU-seconds kill, `--timeout` wall);
  **`--cgroup=` was REMOVED**; weak attribution; SUID liability. Steal the
  wall-timeout + CPU-seconds-kill ergonomics only.
- **bubblewrap** — namespace/FS sandbox, **NO cgroup resource limits** (delegates
  to caller); strong PID-1 **reaper** clean-kill; `--die-with-parent`. Steal the
  PID-namespace-init reaper as a clean-kill backstop.
- **gVisor** — syscall-interception app-kernel (a *different axis*); delegates
  resource limits to host cgroups (literally our approach); memfd/shmem
  accounting trap (don't key attribution on `anon`); high per-step overhead =
  wrong tool for short CI steps.

---

## 2. Feature checklist (the spine)

Rows = our capabilities. **Us** = honest status (§ vocabulary). **Closest prior
art** names the system(s) and how they do it. **Verdict**: STEAL (copy it),
DIVERGE (do it differently, with reason), or NOVEL (no adequate prior art). Our
status evidence is from a file:line audit of the pinned tree.

### 2.1 Resource control / boxing

| Our feature | Us | Closest prior art (how) | Verdict |
|---|---|---|---|
| cgroup-v2 boxing default-on in `run` | **PARTIAL** — default-on in the CLI (`cgroup.rs`), but the **required** portable gate bypasses the runner via raw `bash` (`ci/run-node.sh`); only the non-required privileged lane actually boxes | Bazel per-action cgroup (`--experimental_sandbox_limits`, `--cgroup_parent`); systemd-run `Delegate=yes` scope (our exact outer); nsjail writes cgroup files directly | **DIVERGE** — mechanism is industry-standard (systemd/cgroup-v2); our gap is *deployment* (wire the required lane), not design |
| opt-out default limits "1 core / 1 GB / 10 s" | **ABSENT as stated** — real defaults are 0.90 CPU fraction / 8 GiB mem floor / 600 s step timeout; opt-out = `--allow-cgroup-failure` | Bazel/systemd ship *no* implicit tiny cap; nsjail requires explicit limits | **DIVERGE** — a tiny default cap is our own policy idea; no prior art argues for it. Decide it deliberately, don't inherit |
| `cpu_timeout` — per-step CPU-seconds budget | **PLANNED** — not on `main`; only branches `codex/cpu-time-timeout` & `origin/ci/cpu-time-rlimit-timeout`; **0/54** nodes set it | **nsjail `rlimit_cpu`** & **Firejail `--rlimit-cpu`** = RLIMIT_CPU CPU-seconds kill, for free; systemd `RuntimeMaxSec` is wall-only | **STEAL** — set `RLIMIT_CPU` via `prlimit`/`setrlimit` in the bash leader (the Rust branch already does this); the cleanest, most portable path |
| wall timeouts (per-step + global) | **PARTIAL** — per-step on all 54 nodes (runner-native); **no** runner-native whole-run timeout (only OS `timeout`/workflow `timeout-minutes`) | Firejail `--timeout`; systemd `RuntimeMaxSec`; every CI platform | **STEAL** — add a runner-native global deadline; trivial and standard |
| advisory memory sizing (`rss_baseline × mem_cap_factor`) | **IMPLEMENTED** (`sizing.rs`; factor 1.25, floor 8 GiB) — but advisory-only on raw-bash/Noop paths | Bazel `resource_set` memory demand; BuildXL RAM throttling | **STEAL/keep** — align with Bazel's per-action memory-demand model |
| `--max-mem` = sizes WIDTH, not per-step MemoryMax | **IMPLEMENTED** (`sizing.rs jobs_for_budget`) — picks largest outer `-j` whose worst-case RAM fits | Bazel `--local_ram_resources` bounds concurrency by summed demand (same idea, per-pool) | **STEAL/converge** — same intuition as Bazel's RAM pool; our whole-run framing is a reasonable variant |
| clean-kill-on-breach (`cgroup.kill` + killpg) | **IMPLEMENTED** (`cgroup.rs`) — but fires only when boxed (privileged lane) | **raw cgroup-v2 `cgroup.kill`** (our choice, migration-proof); bubblewrap PID-1 reaper; nsjail CLONE_NEWPID | **STEAL (add)** — add bubblewrap-style PID-namespace init reaper as a second backstop |
| structured breach/attribution records | **IMPLEMENTED** (`cgroup.rs`: oom_kill, memory.peak, cpu.stat, cpu.pressure PSI, cgroup.threads) — boxed lane only | systemd `Result=oom-kill`+`MemoryPeak`; raw cgroup fields; **nsjail = ZERO attribution**; gVisor memfd/shmem trap | **NOVEL** (integration) — per-step attribution *rows folded into the DAG profile* is beyond any surveyed containment tool; **STEAL** `memory.oom.group=1` + `cpu.stat nr_throttled` as extra fields |

### 2.2 Scheduling

| Our feature | Us | Closest prior art (how) | Verdict |
|---|---|---|---|
| `-j` / `CI_DAG_JOBS` outer WIDTH | **IMPLEMENTED** (`cli.rs`; `-j 2` in privileged lane) | Everyone: Bazel `--jobs`, nextest `--test-threads`, Buck2 `--num-threads`, BuildXL `/maxProc` | **STEAL/standard** — keep WIDTH explicitly named and separate (the `-j 2` confusion was ours) |
| named-resource QUOTA (`hint.resources` vs `resource_caps`) | **IMPLEMENTED** (`scheduler.rs`; `hermit_guest:1`, `manifest_guest:4`; 29/47 nodes) | **BuildXL `SemaphoreInfo{name,incrementBy,limit}`** (near-exact); Bazel `--local_extra_resources`; nextest `test-groups`; Buck2 `LocalResourceInfo` (named-instance pool w/ env handle) | **STEAL** — adopt BuildXL's `{name,incrementBy,limit}` schema verbatim (we hard-code demand=1; `incrementBy` gives variable demand); add Buck2's **handle injection** for addressable resources (a `/dev/kvm` slot, a socket) |
| CPA core-budget `P` (Σ inner-jobs ≤ P) | **BUILT-UNUSED** (`scheduler.rs`; `core_budget` defaults `None`; no caller sets `--planner cpa`) | **BuildXL `weight`** (static or **historic-CPU-derived**); Pants `ProcessConcurrency`; Bazel `cpu` pool; nextest `threads-required` | **STEAL** — turn it on, and derive weight from **historic CPU** like BuildXL instead of hand-tuning |
| inner width (`preferred_inner_jobs` appended to cmd) | **BUILT-UNUSED in the Hermit DAG** (mechanism in `model.*`; **0/54** nodes set it) | Pants `{pants_concurrency}` argv templating; Bazel `resource_set(num_inputs)` | **STEAL** — Pants' argv-templating + range-picking is the exact ergonomics to copy when we enable it |
| ~54-node DAG manifest | **IMPLEMENTED** (`ci/dag/portable.json` 47 + `privileged.json` 7) | Bazel/Buck2 derive the DAG from build files; ours is hand-authored | **DIVERGE** — a hand-authored CI DAG is fine for a fixed pipeline; don't over-engineer toward a full build graph |
| critical-path / theoretical-max | **IMPLEMENTED** (`estimates.rs` makespan `max(T_cp, area/P)`; `viz.rs`) — computed, not gating | **BuildXL** historic-runtime **critical-path prioritization**; Buck2 `log critical-path` | **STEAL** — use it to *prioritize* ready steps (order by expected downstream), not just to display |
| widening-vs-workers distinction | **IMPLEMENTED** (docs + `scheduler.rs step_width`) | nextest `threads-required` vs `--test-threads`; Pants concurrency vs parallelism | **STEAL/validated** — the exact split nextest/BuildXL also make; keep it |
| PMU exclusivity | **IMPLEMENTED** (`flock` + `hermit_guest:1` cap) | Nix `requiredSystemFeatures` (`benchmark`) capability routing; Bazel `exclusive` tag | **NOVEL/DIVERGE** — PMU-contention determinism is Hermit-specific; the *mechanism* (a cap of 1 + Bazel-style `exclusive` tag) is standard |
| GitHub-Actions fan-out | **IMPLEMENTED** (`ci-portable.yml` matrix shards + `portable-shards.json` + fail-closed coverage) | Taskcluster autoscaled pools; Bazel/Buck2 remote execution farms | **DIVERGE** — we shard one DAG across ephemeral GH runners rather than a persistent RE farm; correct for our infra |
| leaf / sub-DAG semantics | **IMPLEMENTED** (`run_dag_boxed`, `run_dag_boxed_ordered`; node-subset exec) | Bazel/Buck2 target patterns / RE partitioning | **STEAL/standard** |

### 2.3 Observability

| Our feature | Us | Closest prior art (how) | Verdict |
|---|---|---|---|
| per-node profiling history (cross-run) | **PARTIAL** — store + sync backends built (`perflog.rs`, `sync.rs`), but CI writes ephemeral `$RUNNER_TEMP` and sets no `--profile-sync` ⇒ no persistence in GH CI (persists locally) | BuildXL historic per-pip runtime store (drives prioritization); Bazel JSON trace (per-run) | **STEAL** — persist it (BuildXL proves the payoff: feed history back into weight/critical-path) |
| cost estimate + actual | **IMPLEMENTED** (`ci-hub/lib/tool_cost.py`; `estimates.rs`) | Bazel JSON trace `dur`; BuildXL historic runtime | **STEAL/keep** |
| live progress renderer | **PARTIAL** — line-based status stream + static ASCII/DOT (`viz.rs`); no in-place ANSI redraw in the pinned tree | **Buck2 superconsole**; Bazel `--curses` | **STEAL** — Buck2's superconsole is the reference for the in-place renderer we've designed |
| queue depth / time-in-queue / time-since-last-green | **IMPLEMENTED** (`ci-hub/health/*.py`, tick-wired) | Taskcluster queue metrics; GitHub Actions insights | **NOVEL** (for a build-DAG tool) — build tools don't own queue health; ours is a CI-platform concern we track |
| performance ratchet | **PARTIAL** — power-to-weight ranking (`power-to-weight.rs`), no enforcing gate | Bazel/Buck2 have none; benchmark CI (e.g. Criterion+bencher) is the closest external | **NOVEL** — an enforcing perf-ratchet on CI-node cost is not in the surveyed set |
| green-time % | **PARTIAL** — green/red/pending counts + staleness (`github_main_health.py`); % derivable, not first-class | CI dashboards (Taskcluster); DORA-style metrics | **DIVERGE/keep** — standard reliability metric, just formalize it |

### 2.4 Test selection & correctness

| Our feature | Us | Closest prior art (how) | Verdict |
|---|---|---|---|
| fail-open test selection | **IMPLEMENTED** (`select-tests.rs`; any doubt ⇒ full; wired into required lane; kill-switch) | Bazel/Buck2 select affected tests **exactly** from the build graph | **DIVERGE (honest)** — Bazel/Buck2 do this *precisely* for free because they own the dep graph; we **heuristically reconstruct** footprints and **fail open** because Hermit's cargo tests aren't in a build graph. Ours is a pragmatic approximation, strictly weaker than a true graph — worth stating plainly |
| cargo-derived footprints | **IMPLEMENTED** (`test-footprints.json`) | Bazel `query rdeps` (precise) | **DIVERGE** — same as above; a footprint map is the best available without a build graph |
| incremental-vs-total (skip/selective/full) | **IMPLEMENTED** (`select-tests.rs` trinary) | Bazel test caching (skips unaffected automatically) | **DIVERGE** |
| commit-anchoring (`--base origin/main`) | **IMPLEMENTED** | Standard `git diff base…HEAD` in CI selection everywhere | **STEAL/standard** |

### 2.5 Flakiness

| Our feature | Us | Closest prior art (how) | Verdict |
|---|---|---|---|
| matched-load probing | **IMPLEMENTED** (`ci-hub/stress/matched-burst.sh` → multisect `matched.sh`; nightly) | **None** surveyed co-schedule subjects under matched instantaneous load; Bazel `--runs_per_test` just repeats in isolation | **NOVEL** |
| validity calibrator | **IMPLEMENTED** (`matched-burst.sh`; a wave counts only if a known-flaky binary flakes) | **None** — no surveyed tool proves its probe was powerful enough before trusting a clean result | **NOVEL** — the strongest genuinely-novel item in the survey |
| trinary flaky-is-red | **IMPLEMENTED** (any hang ⇒ RED) | **Opposite** of Bazel `--flaky_test_attempts` / nextest retries, which **tolerate** flakiness (pass-on-retry) | **NOVEL/DIVERGE** — deliberately inverts the industry default; the inversion is the point (determinism of outcome) |
| nightly stress cron | **IMPLEMENTED** (crontab `30 4 * * *`, parent host) | Bazel CI `--runs_per_test` nightly jobs; general soak testing | **DIVERGE** — must run on the loaded parent host (an idle runner false-greens); the *load-dependence* is the novel constraint |
| multisect (git-range flake bisection) | **IMPLEMENTED** (`multisect/` standalone) | `git bisect` (single-shot, no rate model); Bazel has none | **NOVEL** — rate-based (trinary) bisection of a probabilistic flake |

### 2.6 Architecture

| Our feature | Us | Closest prior art (how) | Verdict |
|---|---|---|---|
| Rust + Python cross-differential (byte-identical) | **IMPLEMENTED** (`cross/differential.py`; CI-run parity of list/ascii/dot/json + profile schema) | **None** — surveyed tools are single-implementation | **NOVEL** — idiosyncratic to our dual-impl need; unusual and defensible |
| library mode | **IMPLEMENTED** (`run_dag`, `Step`, `DagConfig`; defaults NoopCgroups = unboxed) | Bazel/Buck2 daemons w/ APIs; BuildXL SDK | **STEAL (caution)** — library mode is standard, but our **unboxed-by-default library entry** contradicts "box all untrusted compute" (see proposal); make boxed the default |
| shared types | **IMPLEMENTED** (parallel `model.rs`/`model.py`, kept identical by the harness) | Protobuf/Starlark single-source schemas (Bazel/Buck2) | **DIVERGE** — a single schema source (protobuf) would be cleaner than two hand-kept types; consider it |
| land-lock mutex | **IMPLEMENTED** (`ci-hub/landing/landing-lock.sh` flock+lease FIFO) | GitHub merge queue, Bors, **Zuul** gate pipeline | **STEAL** — look at Zuul/Bors before extending; serialized landing is well-trodden |
| speculative-land obligations | **IMPLEMENTED** (`ci-hub.rs` typed obligation store + verifier polling) | **Zuul speculative execution** (speculative merges + dependent pipelines) is the canonical prior art | **STEAL** — Zuul is the reference design here and was **not** in the surveyed set; recommend a dedicated look before investing further |

Cross-cutting BUILT-UNUSED/PLANNED honesty note: our three headline scheduling
gates are **not all live** — named-quota is real, but **CPA core-budget and
inner-width are coded-but-dark** (no manifest node engages them) and
**`cpu_timeout` is unmerged**. The `enshrine-box-all-untrusted-compute-proposal`
is effectively the owner's own writeup of exactly these gaps (raw-bash bypass,
cpu-time budget, library NoopCgroups default).

---

## 3. Bounded honest finding

**Does a mature tool already do what we are building? For most of the resource /
scheduling substrate — yes, and we should copy rather than invent.**

1. **The three-gate scheduler is BuildXL, almost exactly.** `/maxProc` (width) +
   `weight` (core-budget, static or historic-CPU) + `SemaphoreInfo{name,
   incrementBy, limit}` (named quota) is a near-superset of our design. **Steal
   the semaphore schema verbatim**, adopt Bazel's `name=value` pool spelling, and
   take nextest's "consume within both global and group limits" composition rule.
   We are *not* reimplementing this worse — but we should stop hand-rolling and
   converge on their vocabulary. Our genuine additions are the *integration*
   (attribution rows folded into the DAG profile) and the Hermit-specific PMU
   determinism cap.

2. **Per-step CPU-time budget is nsjail/Firejail's `rlimit_cpu`, for free.** Our
   `cpu_timeout` gap is solved by `RLIMIT_CPU` in the leader — no new mechanism
   needed. The Rust branch already does the portable thing; land it.

3. **Boxing is not the hard part; deployment is.** cgroup-v2 + systemd `--scope`
   *is* the right, standard mechanism (Bazel and systemd agree). Our real problem
   is that the **required** lane bypasses the runner (raw bash) and the **library
   default is unboxed** — a wiring/policy gap, not a missing capability.

4. **BLUNT: what we built is NOT a sandbox.** It is a resource-governor +
   attribution layer. It has **no namespace, seccomp, mount, or network
   isolation** — untrusted code under it still reaches the host FS, network,
   `/proc`, and syscalls. nsjail and bubblewrap *are* sandboxes and we are not
   competing with them. Decide honestly which we need: if the compute is merely
   *our own, possibly-buggy* CI steps, resource-governance + attribution is the
   right scope and we should keep extending it (adding RLIMIT_CPU, oom.group,
   reaper) — **not** reimplement nsjail. If any step runs genuinely untrusted
   code, adopt **nsjail or bubblewrap per-leaf** for the isolation and keep our
   layer for the budget/attribution on top. "Extend the library" is the right
   call for gates 1–3, profiling, and CPU-time; it is the *wrong* call for real
   isolation — there, adopt, don't rebuild.

5. **Genuinely novel, keep and lead with:** matched-load probing, the validity
   calibrator, trinary flaky-is-red, and rate-based multisect. No surveyed
   system co-schedules under matched load, proves probe power before trusting a
   clean result, or treats flakiness as red-by-default (Bazel/nextest do the
   opposite — retry-to-tolerate). The dual Rust/Python byte-identical harness is
   also unusual. These are our defensible originality; the scheduler/boxing
   substrate is not.

6. **Look before investing further (not in the surveyed set):** **Zuul** for
   speculative-land/obligations and **Bors/GitHub merge queue** for the land-lock
   — serialized + speculative landing is well-trodden and worth a dedicated read
   before we extend our own.
