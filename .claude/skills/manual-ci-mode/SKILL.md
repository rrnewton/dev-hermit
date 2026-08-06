---
name: manual-ci-mode
description: "Hermit exact-head local validation path within the owner-authorized local-or-hosted landing policy."
---

# Exact-head local validation mode

This skill operates the local half of Hermit's owner-authorized exact-head OR
policy. A full local receipt dereferenced by `ci-hub validate-status` and the
registered hosted result dereferenced by `ci-hub hosted-status` are
interchangeable positives. Missing hosted evidence does not block a local green;
a genuine product red from either authority does block.

**Deployment transition.** Until
[`hermit-merge-gate-authority-deployment`](../../../ci-hub/landing/README.md#deployment-obligation-hermit-merge-gate-authority-deployment)
lands in Hermit, its required merge-gate still requires portable+privileged and
pins the older verifier. Obey that gate and do not report portable-only hosted
authority as deployed end to end.

Use `ci-hub/ci-hub quickstart` for the live command sequence. Admit the run
through ci-hub and launch its full profile by the tracked `systemd-run --user`
producer in `AGENTS.md`, from the assigned slot. Require a clean tree, anchored
40-hex commit, full profile and selection, nonzero counted coverage, zero
failures, and a durable log/ledger row. After any rebase or head change, the old
receipt is inapplicable and validation must run again.

Before landing, require all of the following:

1. The task authorizes landing and required adversarial review is resolved at
   the exact current head.
2. `ci-hub validate-status --sha <40-hex-head>` accepts that head's receipt (or
   the canonical lander observes a hosted green through `ci-hub hosted-status`).
3. `ci-hub/landing/land-pr.sh` owns fresh-base preparation, the serialized land
   lock, merge mode, and post-merge ancestry check.

`locally-validated` is only a cache derived from the receipt. Never type it by
hand or treat its presence, a command exit, a comment, or a copied status as
authority. Do not use raw worktree operations, direct `./validate.sh` from an
agent sandbox, `--admin`, or a primary checkout. The author shepherds a new PR
through this protocol; a dedicated lander is reserved for backlog recovery.
