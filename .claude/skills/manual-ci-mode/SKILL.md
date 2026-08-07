---
name: manual-ci-mode
description: "Hermit exact-head local validation path within the complementary named-coverage landing policy."
---

# Exact-head local validation mode

This skill operates the local half of Hermit's owner-authorized exact-head
coverage policy. A full local receipt dereferenced by `ci-hub validate-status`
binds the portable+privileged local coverage set. The registered hosted result
dereferenced by `ci-hub hosted-status` binds hosted-portable coverage. These are
complementary, not interchangeable: Hermit landing requires both named sets.
Missing evidence from either side is a no-result that blocks landing, and a
genuine product red from either authority also blocks.

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
2. `ci-hub validate-status --sha <40-hex-head>` accepts that head's full local
   receipt and the canonical lander observes the registered hosted-portable
   green through `ci-hub hosted-status`.
3. `ci-hub/landing/land-pr.sh` owns fresh-base preparation, the serialized land
   lock, merge mode, and post-merge ancestry check.

`locally-validated` is only a cache derived from the receipt. Never type it by
hand or treat its presence, a command exit, a comment, or a copied status as
authority. Do not use raw worktree operations, direct `./validate.sh` from an
agent sandbox, `--admin`, or a primary checkout. The author shepherds a new PR
through this protocol; a dedicated lander is reserved for backlog recovery.

## Parent repository evidence is a different authority

`rrnewton/dev-hermit` has no `validate.sh` producer, so it cannot honestly mint
a Hermit full-profile local receipt. Do not append synthetic parent rows to the
Hermit ledger and do not treat that absence as a bypass. A parent commit's
qualifying authority is instead:

```bash
./ci-hub/ci-hub hosted-status --repo rrnewton/dev-hermit --sha <exact-40-hex-head>
```

The registered parent policy requires 4/4 exact-head GitHub jobs: both
`Dev-hermit operational tooling` shards, the `Portability` path-policy job, and
the `Demo review gate` job. Missing, cancelled, partial, stale, or malformed
evidence is `NO_RESULT`; any genuine red remains red. A task-specific positive
and negative mutation fixture must be wired into one of those deterministic
shards when it introduces a new authority. A PR-body claim, local command exit,
label, or an unregistered workflow is not parent landing evidence.
