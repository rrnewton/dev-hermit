---
name: undraft-does-not-trigger-ci
description: "Historical workflow-trigger observation: do not assume ready-for-review starts or avoids CI; inspect the exact current workflow and use ci-hub for landing state."
---

# Undrafting is not a validation event

An older Hermit workflow omitted `ready_for_review`, so undrafting did not start
CI. Workflow triggers can change. Inspect the workflow at current main before
making a queue claim, and never use draft status or a trigger assumption as
validation evidence.

Hermit landing uses the owner-authorized exact-head OR authority: the local
receipt accepted by `ci-hub validate-status` or the versioned hosted job set
accepted by `ci-hub hosted-status`. Undrafting creates neither authority, while
any head change invalidates evidence bound to the old head.

Until
[`hermit-merge-gate-authority-deployment`](../../../ci-hub/landing/README.md#deployment-obligation-hermit-merge-gate-authority-deployment)
lands in Hermit, its required merge-gate still requires portable+privileged and
pins the older verifier. Obey that gate; the portable-only rule is not deployed
end to end yet.
