# CI timeout headroom + load-relative timeout options (analysis + recommendation)

- **Task:** `timeout-headroom-and-load-relative` (P1, CI robustness, agent hermit-ci)
- **Date:** 2026-08-01
- **Principle to encode (owner):** load must *slow* tests, never *flake* them.
  Short of OOMD (memory), a wall-timeout must not fire from CPU contention
  alone. Status-124 under load is a flaw, not a verdict.
- **Status:** ANALYSIS + OPTIONS + DECISION. Option A **shipped** as hermit draft
  PR #1428 (static widening, zero-risk). Durable fix recommendation revised after
  the owner's `RLIMIT_CPU`/`prlimit` steer: **C-rlimit now supersedes B** as the
  preferred load-invariant mechanism (see §4). C-rlimit itself is pending owner
  confirm; the parent-repo half of A is pending parent-file commit authorization.

---

## 1. Where every timeout lives (surface inventory)

| Surface | File | Trigger | Granularity | Kind |
| --- | --- | --- | --- | --- |
| Gate (phase) timeout | `hermit/validate.sh` (`GATE_TIMEOUT_SECONDS`) | wall, `SECONDS`-poll process-tree kill | whole phase | 600 / 1500 (qemu) / 1800 (sabre) / 3600 (privileged) |
| Strict-compat per-command | `hermit/validate.sh` `STRICT_COMPAT_TIMEOUT=60` | `timeout(1)` wall | one guest cmd | 60s |
| rr-compat phase | `hermit/validate.sh` `RR_COMPAT_PHASE_TIMEOUT_SECONDS=60` | wall poll | phase | 60s |
| Smoke | `hermit/validate.sh` `HERMIT_SMOKE_TIMEOUT=30s` | `timeout(1)` wall | one cmd | 30s |
| Backend-parity per-case | `hermit/tests/backend-parity/run_matrix.py` | `communicate(timeout=30)` wall | one guest case | **30s** (reference run 2s) |
| DAG per-step | `hermit/ci/dag/{portable,privileged}.json` | wall, `start.elapsed()` in runner | one node | per-step (table §2) |
| Expansion per-cell | `compat-envelope/expansion-dag.rs` | wall `timeout` in emitted DAG step | one cell | `ptrace_base × geomean × 1.5` headroom, **20s floor** |
| Full-corpus collect | `compat-envelope/collect-fullcorpus.sh` | `timeout(1)` wall | one cell | `TMO_RUN=90`, **`TMO_VERIFY=120`** |

All eight surfaces are **wall-clock**. None is load-aware. The DAG runner CI
actually uses (`agent-utils/rs/bin/safe-ci-dag-runner`, v0.11) enforces the
per-step timeout at a single site — `scheduler.rs:357`:
`if start.elapsed().as_secs() as i64 >= step.timeout { kill_group(pid) }`.

---

## 2. Headroom table (timeout ÷ est_duration_s), tightest first

Ratio = configured `timeout` divided by the config's own `est_duration_s` hint.
A ratio near 1 means a small slowdown trips status-124.

### privileged.json — THE DANGER ZONE (default_step_timeout=120)

| step | timeout | est_s | ratio |
| --- | --- | --- | --- |
| Prepare CI-enabled privileged manifest | 90 | 75 | **1.2×** |
| manifest bucket: applications | 120 | 90 | **1.3×** |
| Build Hermit + focused test binaries | 120 | 60 | **2.0×** |
| CPUID-faulting smoke | 40 | 15 | 2.7× |
| PMU RCB-overflow smoke | 30 | 5 | 6.0× |
| multi-mode E2E metadata | 60 | 5 | 12× |
| manifest bucket: backend-parity | 120 | 5 | 24× |

### portable.json (default_step_timeout=600) — tightest handful

| step | timeout | est_s | ratio |
| --- | --- | --- | --- |
| Portable strict compatibility envelope | 1800 | 600 | **3.0×** |
| Build workspace (cargo build) | 1200 | 360 | 3.3× |
| manifest bucket: determinism-strict | 600 | 150 | 4.0× |
| Clippy | 1200 | 300 | 4.0× |
| Portable Hermit integration targets | 1200 | 300 | 4.0× |
| Test regular workspace crates (nextest) | 900 | 200 | 4.5× |
| …remaining 39 steps | | | 5.0×–120× |

**Read:** portable has a comfortable floor (3.0×). privileged has three cells at
1.2–2.0×, which flake the moment a hosted 2-core runner is contended (the OOM
incident host). The absurdly-loose 120× manifest-bucket cells (est 5s, timeout
600s) are harmless but show the estimates are uneven.

### Other per-cell surfaces (no est hint; headroom = judgment)

- **run_matrix.py = 30s flat wall per case.** L2 (`--verify`) runs the guest
  twice; a debug-build guest under contention can exceed 30s → status-124. This
  is the tightest per-case knob in the tree and is invisible to the headroom
  hints because it is hard-coded.
- **expansion-dag = 1.5× geomean, 20s floor.** 1.5× wall headroom is *below*
  even the privileged danger-zone ratio; this is the primary source of the
  "phase-2 geomean per-cell budget" status-124s the owner flagged.
- **collect-fullcorpus TMO_VERIFY=120s.** The script's own comment
  (`collect-fullcorpus.sh:203`) already names the flaw: "A 120s timeout under a
  24-way parallel storm is a load/scheduling artifact, not a determinism
  verdict." It already mitigates with a **one-shot serial retry** of timed-out
  cells (PAR=1 repair pass) — the only existing load-flake defense anywhere.

---

## 3. Options A / B / C — implementability × effectiveness

### Option A — widen the too-tight timeouts (static)

- **What:** bump the demonstrated flake-prone knobs: privileged 1.2–1.3× cells
  → ≥3×; expansion `--headroom` default 1.5 → 2.5–3.0; run_matrix per-case
  30 → 90; collect-fullcorpus `TMO_VERIFY` 120 → 300.
- **Implementability:** trivial — JSON + two constants. Zero new code, zero new
  failure modes. Landable today.
- **Effectiveness:** removes *today's* flakes; does **not adapt** — a heavier
  storm still flakes, and every real hang now wastes proportionally more wall
  before detection. Does not encode the principle.
- **Risk:** none beyond slower hang-detection.

### Option B — loadavg-scaled wall timeout

- **What:** `effective = base × max(1, loadavg / capacity)` where `loadavg` is
  `/proc/loadavg` (1-min) and `capacity` reflects expected concurrency. Apply at
  the single runner site (`scheduler.rs:357`) plus a shared shell helper for
  `validate.sh` / `collect-fullcorpus.sh`, and in `run_matrix.py`.
- **Implementability:** clean. `/proc/loadavg` + `nproc` are one dependency-free
  read; the runner has exactly one enforcement site to wrap. ~30 lines + a
  shell helper. No cgroup, no perms.
- **Effectiveness:** high — directly implements "load slows, doesn't flake": a
  swamped box gets proportionally more wall-time. Backstop is implicit (factor
  is bounded by real load, not infinite).
- **Caveats to design around:**
  1. **Self-load double-count.** In a `-jN` run, loadavg ≈ N from our *own*
     parallel steps; the `est_duration_s` hints were already measured under that
     parallel load. Naively scaling by `loadavg/nproc` inflates even with zero
     external contention. Fix: divide by `max(nproc, jobs)` (expected
     concurrency), or scale only the *excess* over baseline occupancy.
  2. **loadavg lag.** 1-min average reacts slowly to a burst; sample at
     kill-decision time (not just step start) and take the max seen, so a
     late-arriving storm still extends the budget.
  3. loadavg counts uninterruptible-D (IO) tasks — mild over-inflation, which is
     *safe* (errs toward not-killing).

### Option C — CPU-time budget + generous wall backstop

- **What (owner's first-choice principle):** primary trigger = per-cell CPU-time
  (load-invariant by construction — same CPU-seconds regardless of wall);
  secondary = generous wall backstop for no-CPU hangs (a deadlocked job burns
  ~0 CPU and would never trip a CPU-time budget).

There are two ways to get the CPU-time trigger. The distinction turns out to be
decisive, so they are graded separately.

#### C-accounting — poll cgroup/`/proc` and compare to a budget (original read)

- **Implementability in the runner CI uses: POOR.** The Rust
  `safe-ci-dag-runner` v0.11 does **no per-step cgroup boxing** and **no perf
  logging** — both are explicitly Python-only in this build
  (`scheduler.rs:19-22`, `lib.rs:10-11`, `cli.rs:14`; `--cgroups` prints
  "runs steps UNBOXED"). So there is no per-cell CPU accounting to *read*. To add
  it you must pick one of:
  - **(a) per-node cgroup v2 + `cpu.stat usage_usec`** — clean number, but
    requires cgroup delegation/mkdir under `/sys/fs/cgroup`. This is the *exact*
    fragile path behind the recent hosted-runner failure; CI already runs with
    `--allow-cgroup-failure`. Making the *timeout* depend on cgroup would make
    the timeout itself fail where cgroups are unavailable. Unacceptable coupling.
  - **(b) walk `/proc/<pid>/task/*/stat` utime+stime over the process tree** —
    dependency-free but racy: the tree (bash→hermit→guest→forks) changes every
    poll, short-lived children are missed, and it needs a recursive PID-tree
    walk each 20ms tick. Fiddly and imprecise.
  - **(c) `getrusage(RUSAGE_CHILDREN)`** — aggregates *all* reaped children of
    the runner, not per-node; useless at `-j>1` where cells run concurrently.

This is the variant the "defer C" conclusion below was originally about.

#### C-rlimit — kernel-enforced `RLIMIT_CPU`, no accounting to read (owner's steer)

- **Key reframe:** you do not have to *measure* CPU-seconds at all. Set
  `RLIMIT_CPU` on the cell and the **kernel** delivers `SIGXCPU` at the soft
  limit and `SIGKILL` at the hard limit once the process has burned N CPU
  seconds (utime+stime). No polling, no cgroup, no `/proc` walk. This is
  off-the-shelf and dependency-free:
  - shell: `ulimit -St <soft> -Ht <hard>` (bash builtin) or
    `prlimit --cpu=<soft>:<hard> -- <cmd>` (util-linux, present in CI);
  - Rust: `libc::setrlimit(RLIMIT_CPU, ...)` inside a
    `CommandExt::pre_exec` closure at the single spawn site.
- **Load-invariance:** exact, not approximate. CPU-seconds are the same physical
  quantity regardless of wall time or contention — this is precisely the "load
  slows, never flakes" property, enforced by the scheduler itself.
- **Implementability in the runner CI uses: GOOD.** The runner already launches
  each step as `bash -c step.cmd`, so the zero-Rust path is to prepend
  `ulimit -St <cpu> -Ht <cpu+grace>; ` when a step carries a `cpu_budget_s`
  field — no cgroup, no perms, no unsafe. The shell surfaces
  (`validate.sh`, `collect-fullcorpus.sh`) wrap the inner command the same way;
  `run_matrix.py` sets it via `resource.setrlimit` in a `preexec_fn` (or a
  `prlimit --cpu` prefix). The existing wall `timeout`/`communicate(timeout=…)`
  stays as the generous no-CPU-hang backstop — it is already present at every
  surface, so C-rlimit *adds* the CPU trigger without removing anything.
- **Exit-code mapping:** `SIGXCPU` is signal 24 → exit `128+24 = 152`. Map 152 to
  a distinct "cpu-budget-exceeded" outcome, analogous to wall `124`/kill `137`,
  so a CPU-budget kill is not misread as a product failure.
- **The one real limitation — per-PROCESS, not per-tree.** `RLIMIT_CPU` accounts
  each process's own CPU time; the *value* is inherited across `fork`, but each
  child starts a fresh count. So it catches the single dominant-CPU process
  cleanly, but a cell that fans CPU across many short children (e.g. `make -jN`
  of genuinely parallel children each under the limit) can evade it. For Hermit's
  serialize-guest-onto-one-CPU model the supervisor is the natural chokepoint, so
  the dominant-process proxy is a good fit for most cells; the wall backstop
  covers the multi-proc-fan case, and an exact multi-process aggregate (cgroup
  `cpu.stat`) is only needed if that case proves real.
- **Budget units:** budgets must be expressed in CPU-seconds. `est_duration_s` is
  wall; for CPU-bound single-thread cells CPU ≈ wall, and for latency/IO-bound
  cells CPU ≪ wall (so a CPU budget derived from the wall estimate is naturally
  generous → won't flake). Conservative rollout: set `cpu_budget_s` = current
  wall `est_duration_s` (safe, since CPU ≤ wall), then tighten from measured
  `getrusage`/`/usr/bin/time -v` on clean runs.

---

## 4. Recommendation

**Ship A now (done — PR #1428). Durable fix = C-rlimit, which now supersedes B.
B is the fallback; C-accounting stays deferred.**

The `RLIMIT_CPU` steer overturns the earlier "B over C" call. That call rested
on "the CPU-time trigger is not cleanly implementable in the unboxed Rust
runner" — true for **C-accounting** (you would have to *read* cgroup/`/proc`),
but false for **C-rlimit**, where the kernel enforces the budget via `ulimit -t`
/ `prlimit --cpu` with zero accounting code and zero cgroups. Given that, the
ranking is:

1. **A (shipped, PR #1428):** static widening of the proven flake-prone knobs —
   privileged.json defaults/build/cpuid/manifest to ≥4× headroom, `run_matrix.py`
   per-case 30 → 90s (env-overridable). Kills the current status-124s with zero
   new code. Parent-repo half (`expansion-dag` `--headroom` 1.5 → 3.0,
   `collect-fullcorpus` `TMO_VERIFY` 120 → 300) still pending parent-file commit
   authorization.

2. **C-rlimit (the durable load-invariant fix — now preferred over B):** add a
   per-step `cpu_budget_s`; enforce it with `ulimit -St/-Ht` (or `prlimit
   --cpu`) at each surface, and keep the existing wall `timeout` as the generous
   no-CPU-hang backstop. This is off-the-shelf, needs no cgroups/perms, is
   *exactly* load-invariant (not an approximation), and pairs naturally with the
   wall backstop already present everywhere. Map exit 152 → cpu-budget outcome.
   Start budgets = current wall estimates (safe), then tighten from measured CPU
   time.

3. **B (loadavg-scaled wall) — fallback, not first choice.** Keep it ready if
   C-rlimit's per-process accounting proves insufficient for multi-process cells,
   or as a stopgap where setting a CPU budget per cell is awkward. B approximates
   load-invariance (laggy 1-min loadavg, self-load double-count) where C-rlimit
   achieves it exactly, so C-rlimit is both simpler *and* better for the primary
   trigger.

4. **C-accounting — still deferred.** Only needed for an exact multi-process CPU
   aggregate, which requires per-node cgroup boxing in the Rust runner (or a racy
   `/proc`-tree walk). Revisit only if the multi-proc-fan evasion case is shown
   to matter in practice.

**Why C-rlimit over B (revised):** both keep a wall backstop; the difference is
the primary trigger. C-rlimit's is a physical CPU-second budget the kernel
enforces — genuinely load-invariant, no sampling, no loadavg math. B's is a
scaled wall clock — an approximation with lag and self-load caveats. C-rlimit is
now the cleaner path in the exact code path CI runs, so it becomes the durable
recommendation and B steps back to fallback.

### Suggested split into follow-on tasks
- `ci-timeout-widen-tight-cells` (Option A) — **shipped as hermit PR #1428**;
  parent-repo half pending parent-file authorization.
- `ci-timeout-cpu-budget-rlimit` (Option C-rlimit) — per-step `cpu_budget_s` via
  `ulimit -t`/`prlimit --cpu` + wall backstop across the runner, `validate.sh`,
  `collect-fullcorpus.sh`, and `run_matrix.py`; exit-152 mapping. Now the
  preferred durable fix (pending owner confirm).
- `ci-timeout-load-relative-runner` (Option B) — loadavg-scaled wall; fallback if
  C-rlimit's per-process accounting is insufficient.
- `ci-timeout-cpu-budget-cgroup` (Option C-accounting) — exact multi-process
  aggregate; blocked on Rust-runner cgroup boxing.
