# Ancestry-verified state of the implemented pile
Date: 2026-08-06 · Agent: hermit-verify · Task: `ancestry-verify-the-implemented-pile`
## Method and its bases
Population: **298** tasks with `status=IN_PROGRESS` and tag `implemented`.
Test: `git merge-base --is-ancestor <sha> origin/main` in the owning repo, against a
**freshly fetched** `origin/main`. PR references resolve to `mergeCommit.oid`, never the head.

| repo | origin/main at test time | clone |
|---|---|---|
| hermit | `4c70658e785834737cbe1524f77330c781a6f5ea` | complete |
| reverie | `dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6` | complete |
| dev-hermit (parent) | `50664a46fa1fa613f143d426fc3d1b6384d0c1e6` | **SHALLOW** |
| agent-utils | `16c88e9d7f522a4e8224ecae5a2e6b6cbe19730a` | complete |

**PR window:** `gh pr list --state all --limit 2000` for hermit (1365 PRs, range 1-1728) and
`--limit 900` for reverie (371, range 1-389). Neither hit its limit, so neither is truncated.
At `--limit 900` hermit returned exactly 900 with a floor of #797, which would have hidden
three referenced PRs (#356, #369, #373) — the truncation artifact, reproduced.

**Anti-vacuity:** the ancestry test was bracketed both ways — tip-of-main and main~50 return
ANCESTOR, an open PR head returns NOT-ANCESTOR. It discriminates.

## The three buckets

| bucket | count |
|---|---|
| **LANDED** | **163** |
| **NOT-LANDED** (the real drain queue) | **34** |
| **UNKNOWN-no-sha** | **101** |
| total | 298 |

LANDED by repo: {'hermit': 69, 'dev-hermit': 66, 'reverie': 23, 'agent-utils': 5}

## NOT-LANDED — this is the actual drain queue

- `audit-implemented-tags-vs-real-commits` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `backend-rb-readiness-assessment-overnight` — 3 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `fix-env-sensitivity-perturbing-stack-addresses` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `fix-verify-strict-compare-info-only` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `fix_pr_1147_fail` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `fix_pr_1147_failed` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `fix_pr_1147_nonleader` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `fixture-enumeration-order-identity` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `fixture-fork-exec-wait-ordering-identity` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `fixture-mmap-layout-pointer-order` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `fixture-randomness-source-identity` — 2 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `fixture-shared-memory-mmap-coherency` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `fixture-signal-mask-inheritance` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `fixture-socket-epoll-ordering-identity` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `fixture-stat-metadata-identity` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `fixture-timer-family-identity` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `four-prs-carry-unbacked-locally-validated-labels` — 8 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `gh-unreachable-direct-route-via-herdr-run` — 3 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `green-time-sparse-timeline-model` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `identify-injected-regions-per-patching-backend` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `mutation-test-the-fixtures-can-they-fail` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `p1_fix_dbi_from` — 2 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `pids_axis_real_cgroup` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `pin-check-ls-remote-must-route-via-herdr-run` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `ptracer-removal-1-liteinst-counters` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `randomness-fixture-add-vdso-and-verify-cpuid-case` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `reverie-dbi-build-rs-dynamorio-panic-blocks-unrelated-prs` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `reverie_344_route_new` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `sandbox-failure-classifier-misses-ebadf` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `seven-fixtures-emit-boolean-not-value-structurally-blind` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `step_worker_thread_exception` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `validate-run-admission-fetch-must-route-via-herdr-run` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `validate_ledger_qualified_rows` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main
- `validate_service_env_drops` — 1 candidate commit(s) exist locally, none is an ancestor of its origin/main

## LANDED — 163 tasks, safe to close on this evidence

| task | repo | ancestor commit | provenance |
|---|---|---|---|
| `extend-safe-ci-dag-runner-add-the-missing-features` | agent-utils | `570e78655e4c` | note SHA direct in agent-utils |
| `fix-parent-gitlinks-reverie-agentutils` | agent-utils | `3491df43e4ab` | note SHA direct in agent-utils |
| `herdr-path-smoke-matrix` | agent-utils | `3491df43e4ab` | note SHA direct in agent-utils |
| `herdr-relay-allowlist-cannot-cover-wrapped-invocations` | agent-utils | `2450511418e2` | note SHA direct in agent-utils |
| `oom-kill-lands-on-innocent-neighbour` | agent-utils | `570e78655e4c` | note SHA direct in agent-utils |
| `adv-review-boxing-artifacts` | dev-hermit | `8f15ea4b151b` | note SHA direct in dev-hermit |
| `adv-review-landing-ledger-artifacts` | dev-hermit | `5645376a7ae6` | note SHA direct in dev-hermit |
| `backend-perf-readiness-post-architecture` | dev-hermit | `892a5088684c` | note SHA direct in dev-hermit |
| `bpfjailer-blocks-cgroup-boxing-agent-validates-cannot-run` | dev-hermit | `8117b39c08a1` | note SHA direct in dev-hermit |
| `can-github-merge-queue-satisfy-our-local-validation-without-losing-the-local-loop` | dev-hermit | `d64a2d32689a` | note SHA direct in dev-hermit |
| `cancelled-scheduled-run-is-silent` | dev-hermit | `9fbb14fc02a0` | note SHA direct in dev-hermit |
| `cap-concurrent-validates-at-6-measured-knee` | dev-hermit | `aa12eda07231` | note SHA direct in dev-hermit |
| `ci-hub-owned-validate-panes-outside-the-agent-jail` | dev-hermit | `0ff921e501b2` | note SHA direct in dev-hermit |
| `close-top-gap-cells-toward-100` | dev-hermit | `b9c69768a835` | note SHA direct in dev-hermit |
| `cmake-trusts-mtime-not-content-so-a-truncated-artifact-is-permanent` | dev-hermit | `407874b391e3` | note SHA direct in dev-hermit |
| `compat-scorecard-refresh-and-drive` | dev-hermit | `71d159900a98` | note SHA direct in dev-hermit |
| `compat-scorecard-refresh-post-wave` | dev-hermit | `bb00eaa5d4c3` | note SHA direct in dev-hermit |
| `consolidate-fixtures-scattered-across-four-branches` | dev-hermit | `03fc0f9e38bd` | note SHA direct in dev-hermit |
| `crates-io-also-403-blocks-rebuild` | dev-hermit | `0d57f5c4a802` | note SHA direct in dev-hermit |
| `dag-test-steps-should-call-the-hermit-binary-not-cargo` | dev-hermit | `19feac38110a` | note SHA direct in dev-hermit |
| `dbi-determinize-detlog-thread-id` | dev-hermit | `5220310b835e` | note SHA direct in dev-hermit |
| `dbi-detlog-heap-stack-parity` | dev-hermit | `3559c1922462` | note SHA direct in dev-hermit |
| `dbi-route-detlog-to-log-file` | dev-hermit | `5220310b835e` | note SHA direct in dev-hermit |
| `dbi-standardize-detlog-record-framing` | dev-hermit | `5220310b835e` | note SHA direct in dev-hermit |
| `derive-cargo-build-parallelism-from-speedup-curve-not-a-picked-number` | dev-hermit | `b318927e4a83` | note SHA direct in dev-hermit |
| `env-auxv-argv-determinism` | dev-hermit | `e23c8413f3ce` | note SHA direct in dev-hermit |
| `execute-landing-cascade-via-herdr-run` | dev-hermit | `0d57f5c4a802` | note SHA direct in dev-hermit |
| `exit-abnormal-termination-determinism` | dev-hermit | `57017d3d6d88` | note SHA direct in dev-hermit |
| `expand-strict-corpus-new-e2e` | dev-hermit | `a7c79b7f4fde` | note SHA direct in dev-hermit |
| `fix-ledger-gate-counts-partial-accept` | dev-hermit | `7b94becb79ab` | note SHA direct in dev-hermit |
| `flaky-failure-attribution-capability` | dev-hermit | `f43398c3501f` | note SHA direct in dev-hermit |
| `ledger-storage-and-offmachine-backup-options` | dev-hermit | `66b50c909cf0` | note SHA direct in dev-hermit |
| `liteinst-backend-feature-capability-gate` | dev-hermit | `5220310b835e` | note SHA direct in dev-hermit |
| `liteinst-build-then-stage-runtime` | dev-hermit | `5220310b835e` | note SHA direct in dev-hermit |
| `liteinst-expansion-runner-experiment` | dev-hermit | `4b0dbc7cded0` | note SHA direct in dev-hermit |
| `liteinst-runtime-rebuilt-with-hermit` | dev-hermit | `5220310b835e` | note SHA direct in dev-hermit |
| `liteinst_preload_handshake_fails` | dev-hermit | `770c10db975b` | note SHA direct in dev-hermit |
| `mmap-address-space-layout-determinism` | dev-hermit | `d606952330bd` | note SHA direct in dev-hermit |
| `never-test-a-pr-without-rebasing-first` | dev-hermit | `0844c42edb12` | note SHA direct in dev-hermit |
| `orc_hermit_msg_push` | dev-hermit | `21467741288a` | note SHA direct in dev-hermit |
| `orphaned-hermit-processes-spin-forever-at-99-cpu-nothing-reaps-them` | dev-hermit | `36949cde4acc` | note SHA direct in dev-hermit |
| `p1_fleet_primary_hermit` | dev-hermit | `ab637c8a23d6` | note SHA direct in dev-hermit |
| `parity-depth-is-stdout-only-not-full-detlog` | dev-hermit | `8117b39c08a1` | note SHA direct in dev-hermit |
| `patching-backends-remove-ptracer-from-syscall-path` | dev-hermit | `27b892ad094f` | note SHA direct in dev-hermit |
| `per-platform-cpu-timeout-multipliers` | dev-hermit | `a726b8f55be7` | note SHA direct in dev-hermit |
| `pr-planning-process-consolidation-drain-as-testcase` | dev-hermit | `61d144f346bc` | note SHA direct in dev-hermit |
| `proc-sys-read-determinism` | dev-hermit | `03374fc659ef` | note SHA direct in dev-hermit |
| `publish-orphaned-local-main-commits` | dev-hermit | `e2ae9b9b732b` | note SHA direct in dev-hermit |
| `queue-depth-1-on-dev-hermit-main-too` | dev-hermit | `19feac38110a` | note SHA direct in dev-hermit |
| `randomness-source-determinism-verify` | dev-hermit | `f31c6000cb45` | note SHA direct in dev-hermit |
| `ratchet-sabre-strict-parity` | dev-hermit | `5220310b835e` | note SHA direct in dev-hermit |
| `readdir-ordering-determinism` | dev-hermit | `5f5050a56dbc` | note SHA direct in dev-hermit |
| `register-five-new-guards-in-mutation-suite` | dev-hermit | `a2e534a1ac96` | note SHA direct in dev-hermit |
| `register_file_hashing_verify` | dev-hermit | `761183fdff6c` | note SHA direct in dev-hermit |
| `remove-deprecated-cgroups-flag-direct-to-main-now` | dev-hermit | `4fe4cff0d0ec` | note SHA direct in dev-hermit |
| `report-the-drain-as-two-pools-cleanup-backlog-vs-steady-state-flow` | dev-hermit | `276f1a241b50` | note SHA direct in dev-hermit |
| `retire_or_publish_agent` | dev-hermit | `3af43cdfbab1` | note SHA direct in dev-hermit |
| `rusage-resource-accounting-determinism` | dev-hermit | `170eeac08002` | note SHA direct in dev-hermit |
| `sabre-close-remaining-cells` | dev-hermit | `8e6bcf39e94d` | note SHA direct in dev-hermit |
| `sabre-detlog-heap-stack-parity` | dev-hermit | `acef8d2c5bda` | note SHA direct in dev-hermit |
| `sabre-expand-reach-beyond-main-elf` | dev-hermit | `3229970f3411` | note SHA direct in dev-hermit |
| `scorecard-determinism-requires-double-run` | dev-hermit | `7080d680f0f8` | note SHA direct in dev-hermit |
| `scorecard-parity-claims-verify-backed` | dev-hermit | `1fd8443388bc` | note SHA direct in dev-hermit |
| `stack3-scorecard-integrity-parent-only` | dev-hermit | `027d7f026207` | note SHA direct in dev-hermit |
| `strict-verify-holes-audit` | dev-hermit | `8dddca884c04` | note SHA direct in dev-hermit |
| `sweep-for-work-stranded-uncommitted-by-the-commit-is-destructive-rule` | dev-hermit | `6d12f300dbb1` | note SHA direct in dev-hermit |
| `tasks-filed-without-a-verify-field-cannot-be-goal-checked` | dev-hermit | `a4182f985ce3` | note SHA direct in dev-hermit |
| `timeout-audit-at-port-time-to-real-boxing` | dev-hermit | `37cfbcb96def` | note SHA direct in dev-hermit |
| `vacuous-test-audit-hermit-staging-candidates` | dev-hermit | `6e5bb82ea405` | note SHA direct in dev-hermit |
| `wip-limit-open-prs-tracked-against-active-agent-count` | dev-hermit | `3923eb6ee3df` | note SHA direct in dev-hermit |
| `wire-cpu-timeout-enforcement-inert` | dev-hermit | `427aa76f04db` | note SHA direct in dev-hermit |
| `add-long-running-multibackend-perf-tests` | hermit | `630f44aab7fd` | note SHA direct in hermit |
| `audit_detlog_record_framing` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `backend-prefix-match-and-cli-cleanup` | hermit | `630f44aab7fd` | note SHA direct in hermit |
| `backend-short-flag-b` | hermit | `630f44aab7fd` | note SHA direct in hermit |
| `backend_parity_contract_fixture` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `claude-md-over-size-limit-instructions-not-loading` | hermit | `46e3fa2338a3` | mergeCommit of bare PR #1580->hermit |
| `coalesce-staged-work-into-topic-prs` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `compat_timeout_policy_evidence` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `cross-backend-detlog-diff-harness` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `cut-demo-tag-20260804-verified` | hermit | `3e4367ec206c` | note SHA direct in hermit |
| `dbi-log-file-stack1` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `dbi_log_file_is` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `demo-presentation-cycle` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `derive-all-dag-memory-caps-as-a-set-not-reactively` | hermit | `b4e94ce4455d` | mergeCommit of bare PR #1599->hermit |
| `determinize_fchown_under_dbi` | hermit | `f89c69766371` | note SHA direct in hermit |
| `detinode-newtype-make-invalid-unrepresentable` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `detlog-record-framing-standardize-all-backends` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `detlog_embeds_raw_host` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `drain-blocked-no-green-exists-at-or-above-the-gate-floor` | hermit | `2a01963e6121` | note SHA direct in hermit |
| `e9patch-corpus-power-to-weight-selection` | hermit | `b64d893ae9ea` | note SHA direct in hermit |
| `env-var-hash-in-info-log` | hermit | `065980ea661f` | note SHA direct in hermit |
| `expand-e2e-corpus-top-of-funnel-ptrace` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `file-io-determinism-residue` | hermit | `f89c69766371` | note SHA direct in hermit |
| `fix-concurrency-nondeterminism-found` | hermit | `9b642f6d3e1b` | mergeCommit of bare PR #1595->hermit |
| `fix_pr_1147_detpid` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `fixture-clock-family-full-coverage` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `fixture-env-auxv-startup-surface` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `fixture-file-io-short-reads-identity` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `fixture-io-uring-and-epoll-edge-level` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `fixture-pid-tid-virtualization-identity` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `fixture-proc-sys-read-identity` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `fixture-signal-waitstatus-identity` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `fixture-truth-table-landed-running-canfail` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `globally-consistent-state-invariants` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `goal-repro-btrfs-f6a6c280` | hermit | `0c096177d71f` | mergeCommit of bare PR #1151->hermit |
| `golden-logs-for-prefix-depth-ratchet` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `green-inheritance-test-selection-anchored-on-full-main-validates` | hermit | `b4e94ce4455d` | note SHA direct in hermit |
| `help-lists-backends-the-build-cannot-run` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `herdr-run-utility-agent-utils` | hermit | `2c54dfb5dc9f` | note SHA direct in hermit |
| `hermit-default-run-passes-entire-host-dev-through` | hermit | `b64d893ae9ea` | note SHA direct in hermit |
| `ioctl-tty-determinism` | hermit | `630f44aab7fd` | note SHA = head of hermit#1632, its mergeCommit |
| `livelock-reads-as-slow-test-on-every-wall-clock-instrument` | hermit | `339da3c4b3ce` | mergeCommit of bare PR #1177->hermit |
| `main-queue-depth-1-not-cancel-in-progress` | hermit | `d5fcdbe822bd` | mergeCommit of hermit#1575 |
| `make_unsupported_syscall_panic` | hermit | `b64d893ae9ea` | note SHA direct in hermit |
| `manifest_requires_field_is` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `nightly-stress-tests-not-actually-running` | hermit | `3201d7b473f5` | note SHA direct in hermit |
| `no-worse-ratchet-during-sprint-no-new-stripped-greens` | hermit | `b64d893ae9ea` | note SHA direct in hermit |
| `phase2-hermit-dynamorio-plugin` | hermit | `ae2565be5697` | mergeCommit of bare PR #1207->hermit |
| `pmu-concurrency-cap-and-exhaustion-behaviour` | hermit | `e4fb394e5e66` | mergeCommit of hermit#1565 |
| `port_validate_sh_to` | hermit | `a79333589520` | note SHA = head of hermit#1586, its mergeCommit |
| `prefix-parity-depth-ratchet-metric` | hermit | `f89c69766371` | note SHA direct in hermit |
| `rdrand_is_hidden_by` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `rebase-40-branches-onto-one-base` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `reconcile-branch-population-40of53-vs-9of32` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `refine-closable-list-sha-must-be-the-tasks-own-work` | hermit | `065980ea661f` | note SHA direct in hermit |
| `sabre-intercept-dynamic-loader-openats` | hermit | `f89c69766371` | note SHA direct in hermit |
| `stack2-2-abnormal-termination-fidelity` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `staging-branch-merge-all-prs-test-once` | hermit | `20f21bdbde5f` | mergeCommit of bare PR #1514->hermit |
| `suspected_regression_kvm_livelock` | hermit | `82a8e8533575` | note SHA direct in hermit |
| `systemd-run-user-is-the-validate-producer-path` | hermit | `83d0bf344368` | mergeCommit of bare PR #1571->hermit |
| `third-party-backends-move-downstream-of-the-first-party-build-in-the-dag` | hermit | `b64d893ae9ea` | note SHA direct in hermit |
| `validate-484s-single-gate-dominates` | hermit | `e8a0d8d3be3b` | mergeCommit of hermit#1574 |
| `validate-defaults-to-a-profile-that-cannot-authorize-landing` | hermit | `e11175c9de88` | note SHA direct in hermit |
| `validate-sh-duplicates-product-functionality` | hermit | `b384187efd72` | note SHA direct in hermit |
| `validate-then-land-is-unsound-the-push-rewrites-the-head` | hermit | `1b12bc1a9f2a` | note SHA direct in hermit |
| `vdso_getrandom_is_exported` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `verify-tightening-high-confidence-compat-scorecard` | hermit | `9b642f6d3e1b` | mergeCommit of bare PR #1595->hermit |
| `verify_strict_full_trace` | hermit | `4c70658e7858` | note SHA direct in hermit |
| `wire-inert-phase2-guards-into-consumers` | hermit | `cf1fe1babce0` | note SHA direct in hermit |
| `backends-md-ground-truth-audit-three-patching` | reverie | `d5b95fea4da7` | mergeCommit of reverie#358 |
| `coalesce-and-rebase-onto-fresh-main` | reverie | `025d37800d34` | note SHA direct in reverie |
| `e9patch-corpus-broaden-toward-100` | reverie | `5df245d36c55` | note SHA = head of reverie#309, its mergeCommit |
| `erestartsys-retry-is-a-detcore-correctness-property-not-a-backend-one` | reverie | `8688189a87f1` | mergeCommit of reverie#366 |
| `fix_pr_1443_pin` | reverie | `025d37800d34` | note SHA direct in reverie |
| `fork-exec-process-tree-determinism` | reverie | `9470712afa9b` | note SHA direct in reverie |
| `herdr-run-cargo-for-cold-builds` | reverie | `9470712afa9b` | note SHA direct in reverie |
| `kvm-close-remaining-cells` | reverie | `9470712afa9b` | note SHA direct in reverie |
| `kvm-stdout-tty-winsize-divergence` | reverie | `c1355d175812` | mergeCommit of reverie#332 |
| `microbench-ceilings-must-be-confirmed-on-the-real-path-before-driving-work` | reverie | `718686c8bb7a` | mergeCommit of bare PR #369->reverie |
| `no-hardcoded-wall-timeouts-idiom` | reverie | `e376686edbd1` | mergeCommit of bare PR #356->reverie |
| `produce-closable-landed-list` | reverie | `025d37800d34` | note SHA direct in reverie |
| `prune-stale-local-backup-branches-blocking-pin-check` | reverie | `04a46b43930b` | note SHA direct in reverie |
| `ptracer-removal-3-shared-host-hoist` | reverie | `dd3c178ea955` | note SHA direct in reverie |
| `ptracer-removal-5-e9patch-l1-l2-l3` | reverie | `dd3c178ea955` | note SHA direct in reverie |
| `reverie-gitmodules-still-has-shallow-skipping` | reverie | `025d37800d34` | note SHA direct in reverie |
| `reverie-portable-vs-privileged-split-audit` | reverie | `025d37800d34` | note SHA direct in reverie |
| `reverie_clone_with_stack` | reverie | `79517704b0d1` | note SHA direct in reverie |
| `reverie_host_dependent_dependencies` | reverie | `b50abeba112a` | mergeCommit of reverie#357 |
| `shared_inguest_toolhost_family` | reverie | `9a7c0aa701d0` | mergeCommit of reverie#373 |
| `staged-reverie-pin-bumps-r1-r2-not-always-straight-to-main` | reverie | `04a46b43930b` | note SHA direct in reverie |
| `sysinfo_2_writes_uninitialized` | reverie | `025d37800d34` | note SHA direct in reverie |
| `unify_backend_stats_transport` | reverie | `9a7c0aa701d0` | mergeCommit of bare PR #373->reverie |

## UNKNOWN-no-sha — 101 tasks, NOT foldable into either bucket

- 97 × no PR link and no 40-hex SHA in any note
- 4 × bare "PR #N" reference that matched no PR in either window

These carry no verifiable landing evidence. They are not evidence of *not* landing —
many are research-only tasks whose closure evidence is a durable artifact, not a PR.

- `adv-review-determinism-parity-artifacts`
- `adv-review-process-infra-artifacts`
- `adversarial-review-phase2-tightening-artifacts`
- `adversarial-review-tightening-batch-2`
- `ambiguous-zero-audit-across-signals`
- `anchor-selection-is-a-search-pick-the-cheapest-full-green-anchor`
- `backend-parity-c-cells-do-not-run-and-are-born-ci-false`
- `ci-hub-measure-green-time-percentage`
- `close-gap-cells-round2`
- `close_boxing_coverage_gap`
- `cmake-content-hash-elf-magic-not-size`
- `concurrency-determinism-sweep`
- `convert-parent-commit-sites-then-flip-index-guard-to-block`
- `corpus-harden-flaky-cells`
- `cross-backend-info-log-parity`
- `dbi-close-remaining-cells`
- `define-the-heap-as-guest-allocated-pages-only-code-and-static-excluded`
- `demo-preflight-name-all-missing-prereqs-at-once`
- `demo05-unreachable-needs-qemu-and-kernel-download`
- `design-the-dag-runner-parallelism-surface-three-axes-not-one-j`
- `determinism-dimension-coverage-matrix`
- `detlog-parity-regression-gate`
- `detlog_heap_stack_hash`
- `e9patch-candidate-sites-zero-means-parity-is-meaningless`
- `e9patch-close-remaining-cells`
- `e9patch-detlog-heap-stack-parity`
- `e9patch-inguest-detlog`
- `e9patch_rewrite_injects_9`
- `equalise-env-blocks-across-preload-and-ptrace-arms`
- `expand-corpus-real-apps`
- `file-io-offset-determinism`
- `fix-inert-determinism-parity-guards`
- `fix-inert-process-infra-guards`
- `fix_reverie_330_fork`
- `fixture-inventory-and-gap-map`
- `flag-combination-matrix-coverage`
- `full-rebaseline-hardened-standard`
- `gap-to-100-prioritized-map`
- `gitignore-star-log-silently-excludes-golden-logs`
- `guest-coordinator-same-core-affinity`
- `headline-inner-step-scaling-curves-cargo-and-strict-compat`
- `herdr-run-log-rotation-4-days`
- `herdr-run-tee-not-just-redirect`
- `hermit-wrapper-singleton-dag-mode-for-all-adhoc-runs`
- `hourly_alignment_reminder_relay`
- `index-of-measurements-taken`
- `install_cmake_on_the`
- `kvm-corpus-broaden-toward-100`
- `kvm-detlog-heap-stack-parity`
- `kvm_l3_detlog_stack`
- `l3_stack_content_divergence`
- `landing-preflight-three-checks-before-trusting-any-green`
- `liteinst-close-remaining-cells`
- `liteinst-corpus-broaden-toward-100`
- `liteinst-detlog-heap-stack-parity`
- `measurements-index-with-denominators`
- `memory-cap-anchor-plus-scaling-model-explore-both-sides`
- `mutation-testing-for-guards-a-measurable-score-not-ad-hoc-plants`
- `panic-on-unsupported-syscalls-default`
- `parity-against-ptrace-cannot-detect-a-shared-bug-needs-a-correctness-oracle`
- `parity-checker-mutation-test`
- `partial-views-are-footguns-full-scorecard-must-be-the-default`
- `patch_site_inventory_positive`
- `patching-backend-inguest-convergence`
- `patching-shared-inguest-handler-impl`
- `periodic-residue-sweep-correct-refusals-leave-unowned-work`
- `prepare-stacks-for-landing`
- `producer-posts-to-its-own-task-consumer-reads-a-different-one`
- `prose_audit_reader_pov`
- `ratchet-dbi-strict-parity`
- `ratchet-kvm-strict-parity`
- `ratchet-liteinst-strict-parity`
- `re-measure-after-355-pin-bump-contaminated-figures-list`
- `remove-200gb-disk-cap-monitor-disk-fill-instead`
- `research-oci-integration-next-phases`
- `restate-headline-numbers-with-provenance`
- `revalidate-detlog-parity-normalized`
- `reverie_338_aggregate_kvm`
- `scorecard-double-run-determinism`
- `shared-git-index-race-in-parent-repo`
- `sig-alarm-e9patch-exceeds-wall`
- `signal-delivery-determinism`
- `signal-determinism-residue`
- `single-lane-agents-own-one-linear-fat-pr-and-shepherd-it-to-landing`
- `soft-green-vs-hard-green-is-not-tracked-anywhere-in-ci-hub`
- `soft-inherited-validation-across-clean-rebase`
- `stack2-1-rusage-from-virtual-time`
- `stack2-3-ptrace-sigtrap-mistranslation`
- `stack2-4-mmap-layout-policy-unified`
- `stack4-ci-tooling-mixed`
- `strictness-tightening-program-the-p0-after-the-drain`
- `surface_superseded_fail_count`
- `timer-syscall-determinism`
- `unblock-liteinst-strict-parity-ratchet`
- `validate-must-work-standalone-hermit-checkout`
- `validate-wall-budget-600s-the-median-passes-the-tail-does-not`
- `verify-dynamorio-build-unblocks-detlog-dbi`
- `verify-fixtures-run-in-ci-not-just-locally`
- `verify-oom-group-real-boxed-run`
- `verify-ptracer-out-of-path`
- `wip-invariant-open-prs-bounded-by-active-agent-count`

## Caveats that bound these numbers

- **The parent is a SHALLOW clone.** Shallowness can only cause FALSE NEGATIVES (an object
  absent locally cannot test as an ancestor), never false positives. So the 66 dev-hermit
  LANDED rows are sound, and some NOT-LANDED/UNKNOWN rows may actually be landed.
- **Parent `main` has DIVERGED: 7 ahead, 4 behind `origin/main`.** Anything living only in
  those 7 unpushed commits correctly tests NOT-LANDED — it is not on published main.
- A bare `PR #N` note carries no repo; those were resolved hermit-first, then reverie.
