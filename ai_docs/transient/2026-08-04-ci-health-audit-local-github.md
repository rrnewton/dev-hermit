# CI Health Audit: Local and GitHub

Status: durable closure artifact for `ci-health-audit-local-github`.

This document anchors the audit performed on 2026-07-31, with a follow-up on
2026-08-01 and a correction verified on 2026-08-04. Historical measurements
are preserved as measured; later repository state is not back-projected into
them.

## Correction: PR #1210 did not land

An original task note said **"PIN-BUMP REMEDIATION LANDED (draft PR)"** for
Hermit PR #1210. That sentence was false.

Fresh GitHub and ancestry verification on 2026-08-04 established:

- `rrnewton/hermit#1210` is **OPEN**;
- `mergedAt` is null and `mergeCommit` is null;
- cited commit `8ac7ddd9ebbf3603c23275187f9b3a4f540e977e` is not an ancestor of fresh
  Hermit `origin/main`;
- current PR head `66480061ea4f71323ea0f9b6d924edbbc063f693` is also not an ancestor of
  `origin/main`.

PR #1210 must not be reused as landing evidence. Later main contains newer
agent-utils pins and runner changes, but that does not make the original PR
landing claim true.

## Scope and method

The audit examined recent GitHub Actions runs for Hermit and Reverie, local
Hermit hardware capability and validation, workflow timeout headroom, Merge
Gate signal quality, and the gap between Hermit's pinned safe-CI runner and
agent-utils main.

The primary GitHub sample contained 33 Hermit portable runs, the most recent
25 Hermit runs used for regression triage, and 15 Reverie runs. Local evidence
included a privileged Hermit validation on `devbig014` and source inspection
of runner versions and workflow entrypoints.

## Findings on 2026-07-31

### GitHub CI was broadly healthy

- Reverie Rust lane: about 7.0-7.6 minutes, green in the sampled runs.
- Hermit privileged lane: about 2.3-2.6 minutes, green.
- No genuine test-regression pattern was found in the sampled 25 Hermit and 15
  Reverie runs.

### Portable CI was the long tail

For the 33-run Hermit portable sample:

- median: 34.2 minutes
- mean: 39.1 minutes
- minimum: 17.6 minutes
- maximum: 120.3 minutes
- configured timeout: 180 minutes

The maximum consumed 67% of the timeout. Portable CI was the critical path;
the privileged lane was short enough to overlap it.

The DAG's then-recorded estimates were 86 minutes serial / 21 minutes critical
path for portable work and 255 seconds serial / 170 seconds critical path for
privileged work. Those were model estimates, not replacements for observed
wall time.

### Merge Gate produced expected red noise

Merge Gate emitted short failure placeholders while waiting for CI and refire.
This behavior was expected by the contemporary protocol but polluted generic
run-list and PR-health views. A reader unaware of the refire convention could
mistake the placeholders for product failures.

### Hermit pinned an old runner

Hermit pinned safe-ci-dag-runner v0.2.0 while agent-utils main was on the
v0.11.x line. The old runner lacked the profile store, graph annotations,
summary queries, and per-step timing history needed to attribute the portable
long tail. This was a real capability gap.

The audit proposed a compatibility-checked pin update and persistent profile
storage. The proposed remediation PR #1210 did not land, as corrected above.

### Local hardware support was present

The local machine exposed PMU/retired-branch counting, CPUID faulting, and
`/dev/kvm`. A privileged-only validation completed 7/7 nodes in 131.3 seconds.
No local hardware capability blocker was identified.

### Workspace homeostasis was degraded

At one observation point, the workspace reported about 913 GB apparent
worktree usage against the then-current 200 GB advisory, 25 languishing slots,
and 42 physical slot directories against a soft target of 12. These were
point-in-time governance measurements, not proof of physical disk pressure.

## Follow-up on 2026-08-01

The v0.11 migration investigation identified additional integration hazards:

1. The Python entrypoint imported YAML eagerly and crashed on hosts without
   PyYAML, even for help/basic invocations.
2. The newer runner required cgroup boxing unless explicitly allowed to
   degrade; Actions jobs did not have the expected delegated user scope.
3. In unboxed mode, `--max-mem` was advisory rather than enforced. With no
   profile history, relying on it could over-schedule a small runner.
4. A hosted run was killed after about 51 minutes under that unboxed/no-history
   configuration.

The operational lesson was to pin explicit outer concurrency for unboxed
lanes rather than infer safety from `--max-mem`. The observed configuration at
that time used `-j 2` for portable and privileged CI and host-derived width for
local validation.

These findings describe the audited revisions. They do not assert that every
workaround or follow-up subsequently landed.

## Recurring audit contract

The proposed recurring CI-health check covered:

1. PR/main status with real failures separated from placeholder/no-result
   states.
2. Portable wall-time trend against its configured timeout.
3. Reverie hosted test health.
4. Privileged/PMU lane health and queue depth.
5. Agent-utils pin freshness versus its upstream main.
6. Profile-store availability and growth.
7. Nightly toolchain drift.

The task established the checklist and one-shot audit. It did not, by itself,
prove that an automated 30-minute cadence was deployed and firing.

## Evidence limitations

The detailed report originally lived only in TaskGraph notes. Raw GitHub API
responses and local timing logs were not versioned with the task. This artifact
therefore preserves the reported sample sizes, measurements, methodology, and
known limitations, while the 2026-08-04 correction is independently checkable
from PR state and Git ancestry.

