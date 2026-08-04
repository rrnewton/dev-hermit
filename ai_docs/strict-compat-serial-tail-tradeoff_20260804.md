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
