# Implemented-task closure audit refresh (2026-08-04)

This report refreshes the closure census after the live `IN_PROGRESS` +
`implemented` population reached 115 tasks. It does not equate implementation,
landing, and goal completion.

## Method

The semantic join ran first. Across 92 currently open Hermit and Reverie pull
requests, 67 have a dedicated confirmed owner, 24 are mentioned only by a
rollup, and one is unmentioned (`rrnewton/hermit#1617`). The closure population
is narrower than the join tool's 182 nonclosed implemented tasks: this report
uses exactly the 115 rows whose status is `IN_PROGRESS` and whose tags include
`implemented`.

For each of those 115 tasks, the audit selected the latest task note that both
claimed implementation and named a GitHub pull request. This produced an
explicit implementation handoff for 77 tasks; 38 had no such binding and stay
unverifiable. Pull request metadata was queried across Hermit, Reverie, SaBRe,
LiteInst2, dev-hermit, and agent-utils. A merged API state was not landing
evidence. For every merged handoff, the audit freshly fetched the named
repository's `main` and required:

```text
git merge-base --is-ancestor <mergeCommit.oid> origin/main
```

All 41 replay SHAs belonging to 38 all-merged tasks returned rc=0. No pull
request head SHA and no API `MERGED` flag was accepted as the final proof.

The immutable partition is:

| State | Tasks |
| --- | ---: |
| Landed and goal met | 3 |
| Landed with residual | 27 |
| Landed but assigned to a live owner | 8 |
| Not landed | 39 |
| Unverifiable | 38 |
| **Total** | **115** |

## Landed and goal met (3/115)

These are the only closure candidates. Their task-level verification condition
was checked separately from landing:

- `hermit-container-runtime-prototype` - Hermit PR #1179 replay
  `c68f150aa702314c7a13c2e097c40e54ea56ea3f`.
- `make-all-backends-default-build` - Hermit PR #1433 replay
  `88d3d019f7de967071122fd0ebb18b1b16e9354d`.
- `test-shrink-optimization-skill` - Hermit PR #1416 replay
  `9e37635de13f16916ab627b0372976dc28377ddb`.

## Landed with residual (27/115)

Four tasks have an explicit verification condition that is not established by
their landing evidence:

- `fix-kvm-detpid-mismatch`
- `fix-parallel-make-determinism`
- `hb-impl-anchor-model`
- `sabre-detlog-forwarder`

The other 23 landed tasks define no structured `Verify` field, so landing alone
cannot prove their full goal:

- `demo5-fix-qemu-icount-idlewarp`
- `e2e_coreutils_shuf_permutation`
- `e2e_data_handling_jq`
- `e2e_lua_5_4`
- `e2e_m4_macro_processor`
- `e2e_mktemp_name_determinism`
- `e2e_openssl_ed25519_keygen`
- `e2e_openssl_enc_determinism`
- `e2e_perl_random_determinism`
- `e2e_proc_random_uuid_2`
- `e2e_python_hashseed_determinism`
- `e2e_ruby_random_determinism`
- `e2e_rust_hashmap_iteration`
- `e2e_sort_random_determinism`
- `e2e_sqlite_database_engine`
- `e2e_system_utils_openssl`
- `e2e_test_c_stl`
- `e2e_uuidgen_random_determinism`
- `fix_kvm_detlog_verify`
- `hermit-features-cfg-review`
- `hermit-oci-dropin-design`
- `kvm-compat-ratchet-post-demo5`
- `kvm_example_parity_milestone`

## Landed but assigned to a live owner (8/115)

The agent snapshot was 50 seconds old. These task rows name live agents, so the
audit conservatively leaves them untouched even though their implementation
pull requests landed:

- `accept_fail_closed_efault` - `hermit-sabre`
- `cargo-publish-metadata-cleanup-0p2` - `hermit-coord`
- `fix-1571-portable-dag-manifest-gate-failure` - `hermit-ptw`
- `fix-ci-manifest-guests-timeout-headroom` - `hermit-ghdag`
- `flip-merge-tree-default-on-for-planning` - `hermit-lander`
- `resolve_hermit_digest_crate` - `hermit-coord`
- `sabre-timed-progress-bar-verify-determinism` - `hermit-sabre`
- `safe_ci_dag_runner` - `hermit-sabre`

## Not landed (39/115)

Twenty-eight tasks have at least one open implementation pull request. Eleven
have a closed-unmerged handoff (some alongside a merged prerequisite). All stay
open as drain or recovery work:

- `add-long-running-multibackend-perf-tests`
- `add-preemption-counts-to-run-summary`
- `backend-prefix-match-and-cli-cleanup`
- `backend-short-flag-b`
- `backend_parity_shared_ledger`
- `build-debug-episode-cli-and-migrate`
- `canonicalize-dont-strip-verify-must-preserve-distinguishability`
- `clean-rebuild-after-failure-but-scoped-by-what-can-actually-corrupt`
- `coalesce-patching-backend-prs-into-one`
- `common_lazy_backend_stats`
- `cpu-affinity-has-no-allocator-boxed-runs-are-not-isolated`
- `dbi-m4-multiprocess`
- `dbi_preemption_via_safe`
- `dbt-corpus-round-nongated-3`
- `determinize-ghc-rts-ticker`
- `document_compatibility_ratchet_provenance`
- `e2e-python-script-determinism`
- `e2e_language_runtimes_tcl`
- `e2e_test_coreutils_shuf`
- `fix-load-dependent-scheduling-vtime`
- `fix_1200_codex_review`
- `fix_1213_timerfd_poll`
- `fix_1576_detcore_overshoot`
- `fix_1588_involuntary_kill`
- `hermit_run_verify_hangs`
- `kvm-corpus-round-nongated`
- `logdiff-unsafe-strip-lines-rename`
- `matrix-tsv-relocate-to-parent`
- `pr_359_key_dynamorio`
- `product_side_xfail_strict`
- `record_start_verify_run`
- `register_file_hashing_verify`
- `relocate-generated-output-to-hermit-ignored`
- `reverie-validate-into-ci-hub-history`
- `reverie_337_fix_liteinst`
- `select-tests-separate-config-from-machinery`
- `split-matrix-asymmetric-tests-from-code`
- `stopping-a-validate-is-not-free-some-stop-paths-write-false-reds`
- `validate-must-refuse-to-run-unharnessed-inside-dev-hermit`

## Unverifiable (38/115)

These tasks have no task note that simultaneously claims implementation and
names its implementation pull request. They may be direct-main tooling,
research artifacts, or deficient handoffs. The audit does not fabricate a
not-landed result:

- `a-pass-row-must-carry-its-profile-partial-profiles-read-as-green`
- `add-dont-break-demos-principle`
- `audit-every-merge-gate-requirement-has-a-signer`
- `benchmark-writeup-and-skill`
- `cargo-lock-contention-serializes-the-dag-regardless-of-width`
- `cgroups-opt-out-with-small-default-cap`
- `dbi-ci-timeout-investigation`
- `dbt-ratchet-round2-nongated`
- `drain_1556_soft_landed`
- `every-agentic-command-needs-quickstart`
- `fix-execd-sibling-admission-quiescence`
- `fix-pr1180-rustdoc-link`
- `fix_reverie_359_e9patch`
- `force-skid-witness-injection`
- `gvisor-systrap-benchmark-repro`
- `gvisor-writeup-overhaul-colleague-ready`
- `is-portable-ci-usable-at-all-evidence`
- `job_cpu_at_kill`
- `kvm_only_pipe2_o`
- `landing-skills-redundancy-and-discoverability`
- `liteinst-lane-restaffed-ratchet-toward-ptrace-envelope`
- `narrow-fix-for-vfork-reap-livelock-without-rollback-regression`
- `nightly-demo-sweep-ci`
- `pipelined-rebase-front-for-the-23-head-drain`
- `policy-demo-touching-commits-mandatory-adversarial-review`
- `pr_359_correct_vendored`
- `prs-predating-commit-anchoring-can-never-produce-a-qualifying-receipt`
- `rb-drb-modern-frontier-research`
- `relocate-tick-hub-config-into-version-control`
- `retired_agents_leave_detached`
- `reverie-pin-is-one-fact-in-twenty-places`
- `safe-ci-dag-runner-library-mode`
- `scheduler-vtime-jump-unproductive-pollers`
- `sprint-fbsource-mini-release-import`
- `study-min-vtime-scheduler-alternatives`
- `super-validate-audit`
- `tg_implemented_tag_landmine`
- `tick-hub-usage-audit`

## Closure rule

Only the three goal-met tasks are supported closure candidates. The 27 residual
tasks and eight live-owner tasks remain open. The 39 not-landed tasks remain
drain/recovery work. The 38 unverifiable tasks require a direct-main SHA,
artifact proof, or a repaired implementation handoff before any closure.
