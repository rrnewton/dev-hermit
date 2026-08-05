# Research task closure evidence

A research task has no implementation pull request, so forcing it through a
PR-shaped verifier fabricates a relationship. Its publication evidence is a
typed tuple:

```text
repository + durable path + last content commit + target branch + ancestry
```

For the standard parent-repository case, the closure gateway derives and
records:

```text
rrnewton/dev-hermit:<path>@<last-content-commit>;target=main@<fresh-tip>
```

It requires the path to be tracked, present on freshly fetched parent `main`,
and the content commit to be an ancestor of that main. The task note must name
the same path and exact content SHA. Separately, the coordinator reads the
artifact and checks the task's verification condition. Publication and goal
completion are independent claims.

This is the same missing-identity defect as a validation-ledger row containing
a SHA without its repository. Hermit CI is adding the repository field to that
ledger; task evidence must not recreate the omission in another subsystem.

## Existing SHA-only repairs

Seven current tasks name a full SHA but omit the authority needed to interpret
it:

| Task | Binding required |
| --- | --- |
| `drain_1556_soft_landed` | Name the repository and PR replay SHA for the landed item, or publish the drain result as a parent artifact tuple. |
| `every-agentic-command-needs-quickstart` | Bind parent `ci-hub/README.md` to parent commit `b3995f3c...`; identify the internal tg diffs as separate authorities if they remain part of the goal. |
| `fix-pr1180-rustdoc-link` | Name the product repository and implementation PR; a branch/head SHA alone cannot prove a rebase landing. |
| `gvisor-writeup-overhaul-colleague-ready` | Bind the versioned gVisor report path to its parent content commit, rather than the bare `80a6b5f` claim. |
| `pr_359_correct_vendored` | Bind the review result to `rrnewton/reverie#359` at its current exact head, or to a versioned review artifact; the reviewed branch was rewritten. |
| `relocate-tick-hub-config-into-version-control` | Bind the named parent paths to parent content commit `df8234db...`. |
| `retired_agents_leave_detached` | Bind the parent policy/tool paths to their parent content commits and keep the live registry-state check as a separate verification result. |

Four current tasks have no PR URL, durable artifact marker, or full SHA:

| Task | Binding required |
| --- | --- |
| `a-pass-row-must-carry-its-profile-partial-profiles-read-as-green` | Name the Hermit writer PR (currently folded into #1615), its eventual merge replay SHA, and partial/full row controls. |
| `add-dont-break-demos-principle` | Bind both declared delivery surfaces (`rrnewton/dev-hermit#25` and `rrnewton/hermit#1389`) to their merge replay SHAs. |
| `audit-every-merge-gate-requirement-has-a-signer` | Publish the signer census as a versioned parent artifact and bind its content commit. |
| `policy-demo-touching-commits-mandatory-adversarial-review` | Bind parent PR #25 to its merge replay SHA and retain the planted-positive/clean-negative checker evidence. |

Until those fields exist, `UNVERIFIABLE` is the correct state. A guessed PR or
repository would turn missing evidence into false evidence.
