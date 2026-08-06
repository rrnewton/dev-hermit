---
name: ci-capacity-single-pmu-runner-bottleneck
description: "Historical PMU-runner capacity context. Use only to recognize the old incident; query live capacity and exact-head validation through ci-hub."
---

# Historical PMU capacity incident

In July 2026, Hermit's host-dependent jobs queued behind scarce PMU runners and
later hit a separate package-provisioning failure. Those runner counts, queue
depths, workflow names, and failure classifications are historical observations,
not current CI truth.

Run `ci-hub/ci-hub quickstart`, then use its current runner-health and PR-status
entrypoints. Verify the running mechanism and exact head; do not infer current
capacity from this note. PMU/KVM work may still require scarce host facilities,
so derive safe concurrency from live capacity before dispatch.

Hermit landing accepts either owner-authorized exact-head authority:
`ci-hub validate-status` for a clean, counted local receipt or `ci-hub
hosted-status` for the versioned hosted job set. A `locally-validated` label is
only a cache and never authority by itself. Missing/partial evidence is no-result;
any genuine product failure from either authority blocks.

**Deployment transition.** This parent rule is not yet live end to end. Until
[`hermit-merge-gate-authority-deployment`](../../../ci-hub/landing/README.md#deployment-obligation-hermit-merge-gate-authority-deployment)
lands in Hermit, its required merge-gate still requires portable+privileged and
pins the older verifier. Obey that gate and do not report portable-only hosted
authority as deployed.
