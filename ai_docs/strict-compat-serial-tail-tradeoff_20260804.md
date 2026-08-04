# strict_compat serial tail: in-node parallelism vs DAG-node split — the tradeoff

**Task:** `strict-compat-is-the-serial-tail-47pc-of-critical-path`
**Author:** coordinator (opus-4.8), 2026-08-04
**Status:** decision-ready tradeoff analysis. No code changed. STATES the tradeoff per the
task's instruction ("do not just pick").

## The observable consequence, in plain language

`strict_compat` is the single fattest node in the portable CI DAG (`est_duration_s: 600`, the
next is `workspace` at 360). Making it stop dominating is worth doing, but the two obvious fixes
are **not** interchangeable, and the reason is a global resource cap most framings miss. One fix
is cheap and local with a bounded speedup; the other is a global-blast-radius change that alone
is inert. Below is what each actually costs and buys, grounded in the code.

## What strict_compat actually is (measured from source, not inferred)

- DAG node: `hermit/ci/dag/portable.json`, `job: strict_compat`,
  `cmd: ./validate.sh --portable-strict-compat-only --no-label-pr --verbose`,
  `timeout: 1800`, `hint.est_duration_s: 600`, `hint.hard_mem_max_bytes: 6 GiB`,
  `hint.rss_baseline_bytes: 3 GiB`, `hint.resources: {hermit_guest: 1}`.
- Inside `validate.sh`, that profile runs `run_compatibility_corpus`, a **straight-line serial
  bash loop** of ~234 `strict_compatibility_probe` / `functional_compatibility_probe` call sites
  (~180 effective after conditionals). Verified: the corpus region (validate.sh ~2354–2760) has
  **no `&`, no `wait`, no `xargs -P`, no `-j`** — every probe is a foreground
  `timeout $STRICT_COMPAT_TIMEOUT hermit run --strict --verify -- <util>` with `status=$?`.
- Contrast `run_super_probe` (validate.sh ~1888) which DOES batch with `SUPER_JOBS`
  (`(host_cpus*3+1)/2`). So the machinery for in-node parallelism already exists in this file —
  the compat corpus simply does not use it.

**Why inner `-j` / `--test-threads` / a wider graph never touched it (two independent layers):**

1. **Internally serial, and not a cargo target.** The corpus is a hand-rolled loop of `hermit`
   process invocations, not `cargo test`/`nextest`. `-j` and `--test-threads` are cargo/nextest
   knobs — structurally inapplicable to a bash for-loop. So the flat j-sweep
   (`j2=2680 · j4=1340 · j>=5=1265 FLAT`) past j=5 is "waiting on this one serial node," exactly
   as the task says.

2. **Globally serialized even if split.** `portable.json` sets
   `resource_caps: {hermit_guest: 1, manifest_guest: 4}`. **Twenty** nodes declare
   `hint.resources.hermit_guest: 1` (strict_compat, hermit_unit, hermit_integration, cli,
   dbi_parity, app_strict_verify, command_strict_verify, envelope_levels, detcore_misc,
   detcore_parallel, arbitrary_binaries, hermit_modes, liteinst_strict, sabre_examples,
   ignored_syscall_regressions, applications_e2e, …). The cap of 1 means **at most one
   hermit-running node executes at a time across the entire DAG.** These 20 nodes are already a
   serial spine; strict_compat is its largest single segment.

That second fact is the crux of the tradeoff and is easy to miss.

## Option (a): parallelise the per-utility probes IN-NODE

Background K probes at once inside `run_compatibility_corpus`, reusing the existing
`run_super_probe` batching pattern; each utility still records its own row via
`record_compatibility_result`.

- **Cheap and local.** One node, one cgroup, no DAG-shape change, no change to the global
  `hermit_guest` cap. Smallest blast radius.
- **Outer scheduler unaffected.** The node keeps holding exactly one `hermit_guest` token, so the
  serial spine's accounting is undisturbed.
- **BUT it silently breaks the node's declared footprint.** `hard_mem_max_bytes: 6 GiB` /
  `rss_baseline_bytes: 3 GiB` were measured at serial (1 concurrent guest). Running K concurrent
  `hermit --strict --verify` guests is ~K× peak memory — the classic "bare `hard_mem_max_bytes`
  measured at an unstated parallelism" defect (CLAUDE.md Proxy Binding; cf.
  `[[dag-mem-caps-pinned-jobs-fix]]`). Option (a) is only correct if it **re-declares the node
  footprint as `{jobs: K, bytes: K × recorded-per-guest-peak}`** — carry the condition with the
  value. Skipping that re-declaration reintroduces the OOM class that blocked all landing before
  the pinned-jobs fix.
- **Speedup is bounded by the node's own core budget**, not by the box. The node runs inside the
  runner's cgroup (CPUQuota/cpuset, `[[safe-ci-dag-runner-boxing-cpuquota-not-cpuset]]`); K
  concurrent guests contend for that same slice, so realistic gain is ≤ the cores allocated to
  this one node (order ~12), and less in practice because each `--strict --verify` run is
  record+replay and itself multi-threaded. It shrinks the fat node but does **not** de-serialize
  the 20-node hermit spine.
- **Attribution:** already have per-utility rows in the summary TSV, so per-utility PASS/FAIL is
  preserved. What (a) does NOT give: per-utility independent timeouts, retries, or scheduler
  visibility.

## Option (b): split into separate DAG nodes

Emit ~180 per-utility nodes (or a handful of category shards) so the outer scheduler spreads them
and each has its own log/timeout/attribution.

- **Alone it is INERT.** Each shard runs a hermit guest, so each must declare `hermit_guest: 1`;
  with `resource_caps.hermit_guest: 1` the shards **run one at a time anyway** — the split buys
  nothing until you also raise the cap.
- **Raising the cap is a global change with the whole hermit spine as blast radius.** It doesn't
  just parallelise strict_compat's shards; it lets all 20 hermit_guest nodes run concurrently.
  Peak memory becomes cap × (3–6 GiB per hermit node) and peak cores scale likewise — a much
  larger resource envelope than touching strict_compat alone, and it interacts with the
  admission/mem-cap accounting fleet-wide.
- **The cap value is a global width constant** — precisely the thing the task warns against. It
  MUST be derived from the runner's core/mem budget (see "deriving width" below), never set to a
  literal.
- **What (b) buys that (a) cannot:** true outer-scheduler load-balancing (shards interleave with
  every other ready node, not just among themselves), per-utility node-level timeouts/retries, and
  per-utility failure attribution as first-class scheduler state — which we lack today. It also
  attacks the *spine*, not just the one fat node.
- **Costs:** ~180× node/process/cgroup setup overhead; cold `hermit` startup paid per utility
  instead of amortised; larger DAG to maintain; and the global-cap change must be validated against
  peak memory across the whole spine, not just strict_compat.

## Deriving width WITHOUT a per-node constant (applies to both options)

The task's caution is real: `CARGO_BUILD_JOBS` already leaks from the CPU quota as
`NUM_JOBS=284` because the cap lives on some DAG commands and not others (validate.sh ~479–489).
Reading `nproc` inside the node re-creates exactly that bug. Instead:

- **Take the core budget from the runner**, i.e. the node's own cgroup allocation
  (`cpuset.cpus.effective`, or the CPUQuota-derived core count the safe-ci-dag-runner already
  computes and could export to the step as an env var). This is the number of cores this node is
  actually allowed, not the box's `nproc`.
- **Bound K by memory too:** `K = min(cores_from_runner, mem_budget / recorded_per_guest_peak)`,
  where `recorded_per_guest_peak` is a *recorded* (not sampled) peak RSS of one
  `hermit --strict --verify` guest, obtained the same way `[[dag-mem-caps-pinned-jobs-fix]]`
  obtained its cap. Then the node's declared footprint is `{jobs: K, bytes: K × per-guest-peak}`.
- For option (b), the "width" is the global `hermit_guest` cap; derive it identically from the
  box budget the runner owns, and re-verify the whole hermit spine's peak memory at that cap.

## Recommendation shape (decision left to owner/coordinator)

- If the goal is **"shrink the biggest node cheaply, this week"** → **(a)**, with the footprint
  re-declared as `{jobs: K, bytes: …}` and K derived from the runner. Bounded gain (~node core
  budget), zero DAG blast radius.
- If the goal is **"de-serialize the hermit spine and gain per-utility attribution"** → **(b)**,
  but understand it is a *global* change (raise + derive `hermit_guest` cap, re-validate spine
  memory), not a local one, and it is inert without the cap change.

The two are also **composable**: do (a) now for the cheap win, and treat (b) (the cap +
per-utility nodes) as the larger follow-up that attacks the spine — the memory `[[portable-ci-admission-limited-derived-ceiling-17]]`
already names "shrink backend-build + strict-compat" as the residual busy-wall lever.

## VERIFY checklist for whichever change is built (from the task)

- Re-measure the critical path after the change; state strict_compat's new % contribution.
- Re-run the j-sweep; show where it now goes flat (it should move past j=5 for option (a), or
  depend on the new cap for option (b)).
- Per-utility failures remain attributable (a parallelised sweep reporting one aggregate
  pass/fail is a regression — the per-utility TSV rows must survive).
- For (a): confirm no OOM at K concurrent guests, and that the node's declared
  `{jobs, bytes}` footprint matches the recorded peak.
- For (b): confirm the raised `hermit_guest` cap does not OOM the spine at its new concurrency.

---

# MEASURED ADDENDUM 2026-08-04 (hermit-perf, opus-4.8) — the tradeoff is now measured, not just stated

**How measured.** Ran the real release binary `hermit/target/release/hermit` (built 2026-08-03,
primary on `main`) with the exact portable-strict-compat probe invocation
`run --strict --verify --no-virtualize-cpuid --max-timeslice=disabled -- <util>`, on devbig014
(unloaded, warm cache). Per-probe wall + user+sys CPU via `/usr/bin/time -f '%e %U %S %M %P'`.
Effective cores = (user+sys)/wall. N=22 distinct probes. NOT a full-corpus wall run (that needs
the boxed systemd-run producer path, not a bare 600s agent run) — this measures the *composition*.

## Finding 1 (decisive): every probe runs at ~1.0-1.25 effective cores, uniformly

| probe | wall | cores | maxRSS |
|-------|------|-------|--------|
| true/echo/pwd/cat/wc/head/base64/base32/id/seq (trivial x~200 in corpus) | 0.03-0.05s | 0.75-1.3 | 13-16MB |
| openssl / ruby / python3 | 0.06-0.41s | 1.0-1.1 | 12-16MB |
| node | 0.98s | 1.17 | 34MB |
| java -version | 1.85s | 1.21 | 41MB |
| git --version | 2.22s | 1.20 | 32MB |
| rustc/make/cmake --version | 0.05-0.16s | 1.1-1.2 | 15-28MB |
| **javac (compile H.java)** | **18.92s** | **1.23** | **380MB** |

hermit `--strict --verify` determinizes the guest to ~serial execution, so **every probe uses ~1.2
cores no matter how parallel the native workload is** (javac has JIT threads; still 1.23 cores under
hermit). This REFUTES the "sweep is running at ~12 cores" framing in the task title: the node uses
**~1.2 of its ~12-core budget, one probe at a time — ~10 cores sit idle for the node's entire
duration.** That idle headroom is exactly what option (a) reclaims.

## Finding 2: time is pole-dominated, not evenly spread

`javac` (18.9s) is a single tall pole; `java`/`git` ~2s; `node` ~1s; the ~200 remaining probes are
<=0.1s each (=~10-20s total). A serial batch of ~9 heavy + 21 trivial probes exceeded 120s wall,
so the real `gcc/g++/make/cmake/rustc` *compile* probes (each spawning `cc1`/`as`/`ld` as separate
hermit-traced processes) are the seconds-each heavy middle. Attacking a handful of heavy probes,
not the 200 trivial ones, is where any speedup comes from.

## Finding 3: memory per concurrent guest is bounded and small

Per-guest RSS: 13-45MB typical, **380MB worst (javac)**. So K concurrent guests add K x (<=0.4GB),
NOT the multi-GB OOM class the pre-measurement note feared. At the node's 6GiB hard cap: K is
mem-bounded at ~15 (6GiB/0.4GB), core-bounded at ~10 (12 cores / 1.2). **The binding cap is cores.**

## The measured ceiling (analytic model, parameterised by the above)

    parallel_wall ~= max( longest_single_probe , total_cpu_seconds / K )
    K = min( floor(effective_cores / 1.2) , floor(mem_budget_bytes / 400MB) )

On a ~12-core validate slot: K~=10. With the DAG's declared 600s and pole=19s:
`wall ~= max(19, 600/10) = ~60s` => **~10x**, strict_compat 600s -> ~60s.
On ubuntu-latest (4 core): K~=3 => smaller win but smaller idle headroom too.

**Projected critical-path re-measurement (VERIFY item 1, PROJECTION — no change landed):**
CP 1265s, strict_compat 600->~60-100s => **CP ~= 745-805s**, strict_compat share **47% -> ~8-13%**.

**Projected j-sweep (VERIFY item 2):** option (a) is INTRA-node, invisible to the OUTER `-j`
scheduler, so the outer j-sweep flat-point (j>=5) does NOT move — only the CP FLOOR drops. Moving
the outer flat-point rightward requires option (b) + a `hermit_guest` cap bump (a global width
constant, the risky change). This corrects the artifact's earlier guess that (a) "should move past
j=5" — it will not; (a) lowers the floor, (b)+cap raises the outer width.

## The width caution is real and located (owner's explicit warning)

`validate.sh:440 host_cpus = getconf _NPROCESSORS_ONLN || nproc` reads the FULL machine (316 on
devbig), NOT the runner cgroup; nothing in validate.sh reads `cpuset.cpus.effective` or `cpu.max`.
`validate.sh:466 SUPER_JOBS = (host_cpus*3+1)/2` = **474 on devbig** — the existing in-node parallel
primitive ALREADY carries the nproc-leak (same class as CARGO_BUILD_JOBS -> NUM_JOBS=284). **Reusing
SUPER_JOBS as-is for the corpus would spawn ~474 concurrent guests and oversubscribe catastrophically.**
Any option (a) implementation must first derive K from the runner cgroup (cpuset.cpus.effective and
cpu.max quota/period) AND the mem budget per the formula above — do not reuse SUPER_JOBS unfixed.

## Attribution (VERIFY item 3): preserved by the existing primitive

`run_super_probe` (validate.sh:1893-1923) already dispatches K probes concurrently, each to its own
log file, and the corpus already emits per-utility TSV rows. Option (a) built on that primitive keeps
per-utility attribution. Option (b) additionally gives each utility its own DAG-node timeout/retry.
Today javac's 19s + 380MB is invisible inside the 600s aggregate — both options surface it; this is
concrete evidence for the task's note that (b)'s attribution benefit may matter as much as scheduling.

## Bottom line (owner picks the shape; both are now measured)

- **(a) in-node parallel**, K derived from runner cgroup + mem: measured ceiling **~10x on a 12-core
  slot** (600->~60s), cheap, zero DAG blast radius, memory bounded (<=0.4GB/guest), attribution kept.
  Does NOT de-serialize the 16-node hermit_guest spine and does NOT move the outer j-sweep.
- **(b) split into DAG nodes**: inert without a global `hermit_guest` cap bump (blast radius: all 16
  hermit nodes; the cap is itself a width constant to derive, not hardcode); buys true outer
  load-balancing + per-utility node timeouts/attribution; attacks the spine.
- **Composable**: (a) now for the cheap ~10x on the fat node; (b) as the spine follow-up. The pole
  (javac) argues for isolating heavy probes either way.

Correction to prior note: the hermit_guest spine is **16 nodes** (measured from ci/dag/portable.json),
not 20.
