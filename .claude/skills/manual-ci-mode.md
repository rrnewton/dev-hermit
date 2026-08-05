---
name: manual-ci-mode
description: "Current GitHub-independent Hermit landing protocol: ci-hub-admitted full local validation, exact-head counted receipts, and semantic verification before landing."
---

# Exact-head local validation mode

Hermit landing does not wait for delayed GitHub workflows. The authority is the
full local validation receipt for the exact current PR head, dereferenced by
`ci-hub validate-status`. GitHub is supplemental signal: a queued or missing
workflow does not block, while a genuine product failure it reveals still does.

Use `ci-hub/ci-hub quickstart` for the live command sequence. Admit the run
through ci-hub and launch its full profile by the tracked `systemd-run --user`
producer in `AGENTS.md`, from the assigned slot. Require a clean tree, anchored
40-hex commit, full profile and selection, nonzero counted coverage, zero
failures, and a durable log/ledger row. After any rebase or head change, the old
receipt is inapplicable and validation must run again.

Before landing, require all of the following:

1. The task authorizes landing and required adversarial review is resolved at
   the exact current head.
2. `ci-hub validate-status --sha <40-hex-head>` accepts that head's receipt.
3. `ci-hub/landing/land-pr.sh` owns fresh-base preparation, the serialized land
   lock, merge mode, and post-merge ancestry check.

`locally-validated` is only a cache derived from the receipt. Never type it by
hand or treat its presence, a command exit, a comment, or a copied status as
authority. Do not use raw worktree operations, direct `./validate.sh` from an
agent sandbox, `--admin`, or a primary checkout. The author shepherds a new PR
through this protocol; a dedicated lander is reserved for backlog recovery.
