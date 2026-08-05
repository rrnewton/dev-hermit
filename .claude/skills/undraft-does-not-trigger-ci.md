---
name: undraft-does-not-trigger-ci
description: "Historical workflow-trigger observation: do not assume ready-for-review starts or avoids CI; inspect the exact current workflow and use ci-hub for landing state."
---

# Undrafting is not a validation event

An older Hermit workflow omitted `ready_for_review`, so undrafting did not start
CI. Workflow triggers can change. Inspect the workflow at current main before
making a queue claim, and never use draft status or a trigger assumption as
validation evidence.

Hermit landing uses the exact-head local receipt accepted by
`ci-hub validate-status`; GitHub runs are supplemental. Undrafting neither
creates nor invalidates that receipt, while any head change does.
