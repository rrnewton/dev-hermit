---
name: validate-orchestrator-discipline
description: "Coordinator behavior around validate: never claim green without a completed durable log; run detached (agents recycle); when RED don't stall on protocol, tight-loop the failing cell; interrogate surprising ratios; never present a fabricated number as a measurement; verify repeatedly-stated architectural directives are actually in effect"
---

Coordinator/orchestrator discipline for running `validate` and reporting its
results. Every rule below exists because the coordinator VIOLATED it on
2026-08-03; they bind coordinator behavior, not just the tool. The TOOL's own
invariants are the Hermit-level skill, proposed at
`ai_docs/validate-invariants-hermit-skill-proposal.md` (to be placed by the owner
under `hermit/.llms/skills/`). Verified 2026-08-03 against `worktrees/226v/hermit`,
the tool ENFORCES: HEAD-SHA recording into a JSONL run ledger (there is NO
dirty-tree gate and NO `--run-on-dirty-tree` flag); fail-open-to-FULL
affected-test selection (`!= 'false'` job gating — default is selective only in
portable CI, but FULL inside `validate.sh` itself); wall-clock process-tree gate
timeouts; flaky-is-RED trinary stress classification; history-derived cost
estimates always-printed on every exit path (EXIT trap); honest count ratchets
(`RR_COMPAT_EXPECTED=139` etc. guarded against the actual array size); and a
four-layer architecture (`validate.sh` → `ci/run-dag.sh` → `safe-ci-dag-runner`
→ shared TOML manifests, determinism via `hermit --verify`, not bash). It does
NOT enforce: any CPU-time / `RLIMIT_CPU` budget (all timeouts are wall-clock),
any performance ratchet (profiling CSVs are observability-only), or a dirty-tree
refusal. **There is NO symlink sharing between `dev-hermit/.claude/skills` and
`hermit`'s skill dirs** — this cross-reference is an EXPLICIT LINK, not
inheritance; read both.

1. **Never claim green without a completed, durable log.** "Green" requires a
   validation run that actually FINISHED and left a durable, timestamped log you
   can point to (per-node wall+CPU, the validated SHA, pass/fail per cell). A
   still-running run, a killed run, a run whose log you cannot produce, or a
   remembered outcome is NOT green. State the log path and the SHA, or do not say
   green.

2. **Run validate detached, because agents recycle.** A full validate outlives an
   agent's context window; if it runs inline, recycling loses it and its result.
   Launch it detached (`nohup … > /tmp/…-<sha>.log 2>&1 &` or a background task)
   writing a durable timestamped log, and hand off the log path + SHA so the next
   agent can read the outcome. Never gate a turn on a foreground full run.

3. **When main is already RED, do not stall on protocol — tight-loop the failing
   cell.** State-dependent gating: when GREEN, protect it with full gates; when
   ALREADY RED, a fix cannot make it worse, so ship the fix immediately and
   iterate on the specific failing node (`validate.sh --only <node>` /
   `ci/run-node.sh`), NOT the whole 40-50 min DAG. Slow validate is post-hoc
   CONFIRMATION, never the inner loop. Do not block a red-clearing fix on
   land-lock ceremony, adversarial-review latency, or a full re-run first.

4. **Interrogate a surprising ratio; never just route it.** An unexpected
   pass/fail ratio, a suspiciously round number, or a result that contradicts a
   baseline is a signal to DIG (which cell, which SHA, is it flaky, is the
   selector fail-open masking skips), not a workload to hand to another agent or
   summarize away. Flaky (any result strictly between 0% and 100%) is RED, not a
   footnote.

5. **Never present a fabricated number as a measurement.** An estimate, a guess,
   a "should be ~N", or a hardcoded range is not a measurement and must never be
   reported as one. Derive costs/counts from history or an actual run, label
   estimates as estimates, and print ACTUAL wall+CPU on completion including
   failure paths. A number with no run behind it is a lie.

6. **Verify a repeatedly-stated architectural directive is actually in effect.**
   When the owner has said something "should be so" more than once (e.g. cgroups
   on, CPU-time timeouts set, the runner orchestrating), do not assume it is
   true — grep the call sites and read the config, because directives drift from
   reality (e.g. `--cgroups` was accepted-but-inert for months before it was
   removed — it now hard-errors with exit 2 in both engines, and cgroup-v2 boxing
   is ON by default; ALL DAG nodes lack `cpu_timeout`).
   Report the delta between the stated directive and the verified state.

See [[validate-sh-rr-compat-counter-conflict]] and
[[pr-landing-mechanics-merge-gate-uptodate-chase]] for adjacent mechanics, and
[[safe-ci-dag-runner-cgroup-default-and-cpu-timeout-branches]] for the current
runner boxing/timeout state.
