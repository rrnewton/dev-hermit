# Port-time timeout audit: the checklist, and the half-ported state it just caught

**Date:** 2026-08-05
**Task:** `timeout-audit-at-port-time-to-real-boxing`
**Status:** implemented locally; committed to the parent, **not pushed** (egress 403)
**Scope:** local analysis + implementation + verification. No egress, no CI run, no product change.

## The headline: the prior handoff's central premise is stale, and the port is half done

The 2026-08-03 handoff established that `ci-portable.yml` — the authoritative Regular-tests
lane — bypassed the runner entirely: `ci/run-node.sh` jq-extracted each node's `.cmd` and
`bash -c`'d it, so the manifest's wall `timeout`, `jobs_flag`, `cpu_timeout` and cgroups were
*all* inert. It listed as an unverified hypothesis that the pinned runner might have gained a
`run --only` selector, and called that "likely the real enabling step for the whole task".

**Both halves of that hypothesis are now confirmed, and the enabling step has already happened.**

- `run --only` exists in the pinned runner (`agent-utils/rs/safe-ci-dag-runner/src/cli.rs:518`;
  hermit pins agent-utils `a6f4232f`).
- `ci/run-node.sh:129` now does
  `exec "$runner" run --dag "$dag" --only "$sel" -j "$jobs" ...`.
  Its own header says the jq+bash shim "made GitHub Actions a SECOND execution engine that
  diverged from the runner: it ignored each node's jobs_flag, timeout, cpu_timeout, and cgroup
  boxing. This rewrite kills that divergence."

So a port **did** occur — and the audit that was supposed to ride along with it did not.

### Routing and boxing are two properties, and they moved separately

This is the distinction the whole gate turns on, and a one-boolean "is it ported?" model
misses it:

| property | meaning | portable lane today |
|---|---|---|
| **ROUTED** | the runner executes the node, so the manifest **wall `timeout`** and `jobs_flag` are honoured | **YES — newly** |
| **BOXED** | a cgroup manager exists, so **`cpu_timeout`** and memory caps are enforced | **NO** |

`run-node.sh` adds `--allow-cgroup-failure` whenever `GITHUB_ACTIONS`/`CI` is set, deliberately
and with a documented rationale (the ephemeral hosted VM is treated as the containment
boundary). Independently, `reexec_in_scope()` short-circuits on the same variables
(`cgroup.rs:264`), and GitHub sets `GITHUB_ACTIONS` on hosted **and** self-hosted runners — so
the self-hosted privileged lane is not boxed either. The second unverified hypothesis in the
handoff is therefore also confirmed: **no CI path boxes today; only local invocation does.**

**Consequence, verified in code rather than inferred:** the CPU-time monitor lives inside
`if let Some(c) = &cg` and reads cgroup `cpu.stat usage_usec`
(`scheduler.rs` ~600-628). No cgroup manager ⇒ the monitor thread is never created ⇒ a declared
`cpu_timeout` is **inert, silently**. That is exactly the "declared but unenforced" state this
work exists to leave, so `UNSET` remains the honest answer for every CI node.

## The checklist

Run as a **step inside the port**, never as a follow-up pass. Ordered, because the first check
gates the rest.

1. **ENFORCEMENT REACHABILITY — which of ROUTED / BOXED did this port actually deliver?**
   Answer it from the running code, not a table. A budget on a path that cannot enforce it is
   inert, and blessing it is worse than leaving it unset. *Routing* makes wall timeouts live;
   only *boxing* makes `cpu_timeout` and memory live.
2. **CPU BUDGET, DERIVED NOT GUESSED.** `cpu_timeout = round(max(cpu_s) × 1.5)`, anchored on the
   distribution **MAX** (the tail is what trips a ceiling, not p95), **≥ 5 samples or `UNSET`**.
   `UNSET` is a correct answer; a plausible invented constant is a hard failure, because it
   reads as derived and nobody re-checks it. Do not reimplement the derivation — consume
   `ci-hub/history/query.py:node_cpu_budgets`, so there is one authority rather than two that
   drift.
3. **FLOOR THE DERIVED VALUE AT 1.** Any node with `max_cpu` under ~0.33 s derives to
   `round(x × 1.5) == 0`, and the scheduler enables the monitor only `if cpu_timeout > 0`.
   Emitting 0 **silently disables** the ceiling while looking like a derived number —
   "declared but unenforced", self-inflicted. Live example: `setup.nextest` derives to 0.
4. **DECLARE MEMORY AND WIDTH.** State the memory limit and core count / `inner_jobs` as
   declared resource demand, or the node's parallelism is unrecorded and its CPU figure is
   uninterpretable.
5. **RE-EXAMINE THE WALL AT THE SAME MOMENT.** Routing makes wall live. Check both directions:
   too generous (fails to catch bloat) and too tight (newly breaks a node that previously ran
   unbounded).
6. **RECORD BEFORE/AFTER** in the ci-hub store so the port's effect is measurable.

## Applying it to what has ported so far

`python3 ci-hub/timeout_audit/port_gate.py gate` — 55 nodes (47 portable + 8 privileged):

| result | count |
|---|---|
| `NOT_ENFORCED` (boxing unreachable ⇒ `cpu_timeout` would be inert) | **55 / 55** |
| wall budgets ≥ 10× `est_duration_s` | **18** |
| nodes with ≥ 5 store samples — derivable the moment they box | **53 / 55** |
| `cpu_timeout` declared today | **0 / 55** |

**0 of 55 declared, and that is currently the correct state** — a declared value would be inert.
The actionable finding is the other number: **53 of 55 nodes are already derivable**, so the
moment boxing is enabled the budgets can be set from measurement immediately. The blocker is
enforcement, not data.

Worst carried-wall offenders, now live via routing and never re-derived:

| node | wall (s) | est (s) | bloat | derived cpu_timeout |
|---|---|---|---|---|
| `e2e.manifest_backend_parity_c` | 600 | 5 | **120×** | 25 |
| `e2e.manifest_c_programs` | 600 | 5 | **120×** | 40 |
| `e2e.manifest_bin_c` | 600 | 5 | **120×** | 18 |
| `e2e.manifest_determinism_stress_c` | 600 | 5 | **120×** | 19 |
| `e2e.manifest_util_c` | 600 | 5 | **120×** | 21 |
| `e2e.manifest_chaos_c` | 600 | 5 | **120×** | 17 |
| `e2e.manifest_shared_futex_c` | 600 | 5 | **120×** | 15 |
| `e2e.manifest_debugger_c` | 600 | 5 | **120×** | 14 |
| `check.portability_paths` | 60 | 2 | 30× | 8 |
| `setup.nextest` | 600 | 30 | 20× | **0 → floored to 1** |

Eight `e2e.manifest_*_c` nodes at a **120×** ratio are the predicted failure mode made concrete:
nobody chose 600 s for a 5-second node; it was never re-examined.

**Tightness check (the new risk routing introduced): no node is at risk.** Comparing each wall
budget against observed `max_wall_s` in the store found **zero** nodes with under 2× headroom,
so making wall live did not newly break anything. Worth stating explicitly — it is the
direction of error that would have caused an outage, and it was not checked before.

## Two bugs the gate's own tests caught

1. **A proxy with no causal link.** The first cut decided "is this path routed?" by testing
   whether the string `safe-ci-dag-runner` appeared in `run-node.sh`. It appears **nine times**
   — mostly in a header comment describing the *retired* jq+bash design. A substring match
   reports "routed" for a file that does not route. Now bound to the actual `exec … run --only`
   line.
2. **A silently-empty join.** Node identity in these manifests is `group` + `.` + `job`; there
   is no `name`/`id` key. Keying on those made **every** store lookup miss, so all 55 nodes
   reported "no samples" and the gate degraded to a uniform `UNSET` that reads as a clean
   result. A test now asserts the join is non-empty against the real store.

Both are the same shape as the failure the gate is built to catch: a check that looks like it
passed because it never actually looked.

## What remains

1. **The load-bearing lever is unchanged: enable boxing on a CI path.** Until then every
   `cpu_timeout` is inert and the audit can only prepare. The deliberate `--allow-cgroup-failure`
   on the ephemeral hosted lane is a defensible policy choice, so the realistic target is the
   **self-hosted privileged lane**, where the box is not a throwaway VM and the CI
   short-circuit is arguably wrong.
2. **`2-level boxed` semantics still unconfirmed with the owner** — the task explicitly says not
   to guess. The gate is written to accept whichever levels exist and does not assume.
3. **The gate is not wired into CI.** It exits non-zero when any audited node is `NOT_ENFORCED`,
   so it is ready to be driven as a port-time check; that wiring is not done here.
4. **Not pushed** — egress 403.
