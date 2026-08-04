# Implemented-Task Five-State Classification

The filed snapshot contained 138 tasks whose status was `IN_PROGRESS` and whose
tags included `implemented`. The live set fell to 137 when the audit was claimed
and 136 by 2026-08-04T10:27:49Z because two tasks closed during the audit. Both
departing rows were independently closure-verified as `LANDED`, so the original
138-row denominator can be reconstructed without inference.

| State | Tasks | Meaning |
| --- | ---: | --- |
| LANDED | 79 | A recorded merge replay, direct-main commit, or tracked research artifact is ancestry-confirmed on main. |
| IN-FLIGHT | 40 | A recorded implementation PR or internal diff is open. |
| STRANDED | 9 | The recorded implementation PR is closed unmerged and its exact head is absent from main. |
| NOT-SUBMITTED | 0 | A remotely reachable implementation branch exists with no PR. |
| UNVERIFIABLE | 10 | The task has no ancestry-testable implementation reference. |
| **Total** | **138** | |

The complete filed-snapshot table is
[`2026-08-04-implemented-task-five-state-classification-filed-138.csv`](2026-08-04-implemented-task-five-state-classification-filed-138.csv).
The live 136-row table captured before final publication remains
[`2026-08-04-implemented-task-five-state-classification.csv`](2026-08-04-implemented-task-five-state-classification.csv).

The two rows closed during the audit were:

- `obligation_revert_path_lone`: `LANDED`, parent commit
  `5c573184e62935948f3a31ee3b39b3fbef7a639f`, closure-gateway verified.
- `strict-compat-is-the-serial-tail-47pc-of-critical-path`: `LANDED`, research
  artifact `ai_docs/strict-compat-serial-tail-tradeoff_20260804.md` durably
  committed at parent `79c9ab3b7f11bd0b1365a6d6bf3c416fa68b7d04` and closure-gateway verified.

## Method

Fresh main refs, all PR metadata, and remote branch refs were fetched for
`dev-hermit`, `hermit`, `reverie`, `agent-utils`, and `liteinst2`. The main tips
used were:

- dev-hermit `6cc8911c6d126b3be6fc5589308cbdd3ca29be51`
- hermit `ca719dfad4ae4d4b097f10461c017315753d549c`
- reverie `6adcc98d75657af4c8b6b6e3b592f26d05e34003`
- agent-utils `a5dac3b9c8fa736c98f9561e0a757e74207d4cc6`
- liteinst2 `8bffae9da68e0636ec4b6dc473a0fd29ac589d20`

For rebase-merged PRs, landing uses `mergeCommit.oid`, never the obsolete PR
head. A task with a currently open implementation PR is `IN-FLIGHT` even if its
notes also contain older landed work. Evidence subjects mentioned by an audit
are not treated as that audit's implementation. No validation status was used
to infer reachability. In particular, Reverie has no ledger receipt writer
until PR #364 lands, so `ci-hub validate-status` was not read as a pass or a
failure here.

## Stranded

All five unique heads below were fetched from their closed PR refs and returned
`git merge-base --is-ancestor <head> origin/main` rc=1.

| Task | Closed PR | Exact unreachable head |
| --- | --- | --- |
| `add-long-running-multibackend-perf-tests` | hermit#1444 | `e4586cc5ffb380a17fe1434bccfc67e84df4ca68` |
| `backend-prefix-match-and-cli-cleanup` | hermit#1444 | `e4586cc5ffb380a17fe1434bccfc67e84df4ca68` |
| `backend-short-flag-b` | hermit#1444 | `e4586cc5ffb380a17fe1434bccfc67e84df4ca68` |
| `logdiff-unsafe-strip-lines-rename` | hermit#1444 | `e4586cc5ffb380a17fe1434bccfc67e84df4ca68` |
| `add-preemption-counts-to-run-summary` | hermit#1341 | `297401c0a503215459f90edb3244358f6328ee5c` |
| `ci-validate-timing-history-query` | hermit#1210 | `66480061ea4f71323ea0f9b6d924edbbc063f693` |
| `fold_edit_distance_into` | hermit#1582 | `b0b648bc68da6a8da483867c7a2d0121fbf2aad7` |
| `make_plugin_detcore_build` | hermit#1564 | `7c7838fb3516ac0e584911c5bf73a7de8e39b7a5` |
| `make_stale_hermit_dir` | hermit#1564 | `7c7838fb3516ac0e584911c5bf73a7de8e39b7a5` |

## Unverifiable

These tasks have results or claims but no implementation commit, PR, internal
diff, or durable tracked artifact that answers the ancestry question:

- `audit-every-merge-gate-requirement-has-a-signer`
- `cpu-affinity-has-no-allocator-boxed-runs-are-not-isolated`
- `fix-pr1180-rustdoc-link`
- `goal-completion-check-for-landed-pr-tasks`
- `rb-drb-modern-frontier-research`
- `reserve-crate-names-hermit-run-hermetic-infra`
- `super-validate-audit`
- `sweep-for-conservative-constants-blocking-capacity`
- `tg_implemented_tag_landmine`
- `verify-implemented-tasks-landed-for-closure`

## Live Corrections

The second candidate named in the task is no longer not-submitted. Reverie
commit `bea22ccc96d6e59586c8fb928c5719785501a54e` was rebased to PR #365 head
`77b37173f387b17c1605df19a877087a4722e7e8`, then landed as
`6adcc98d75657af4c8b6b6e3b592f26d05e34003`, which is ancestry-confirmed on
Reverie main.

The allocator worked example in the task premise was also corrected:
`ad3803fd` is Hermit #1568's timeout autoretry guard. The actual allocator
source is agent-utils branch `codex/dag-runner-core-allocator` at
`22a401fe3b3dfe6cf20e984a4ae50b3435088182`. Its recovery task is not tagged
`implemented`, so it is not part of this 136-task denominator; hermit-220 owns
its composition on PR #15.
