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

Hermit landing does not wait for GitHub. It requires `ci-hub validate-status` to
accept a clean, counted, full-profile receipt for the exact current head. A
`locally-validated` label is only a cache and never authority by itself. Any
genuine product failure observed locally or by supplemental GitHub checks still
blocks.
