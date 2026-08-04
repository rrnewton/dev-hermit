# Implemented-task closure audit (2026-08-04)

This report classifies the immutable 106-task `IN_PROGRESS` + `implemented`
snapshot recorded by `verify-implemented-tasks-landed-for-closure`. It does not
equate the `implemented` tag with landing or goal completion.

## Method and current drift

For every implementation PR, the audit queried GitHub only for
`mergeCommit.oid`, freshly fetched the named repository's `main`, and required:

```text
git merge-base --is-ancestor <mergeCommit.oid> origin/main
```

The API `MERGED` state and the pre-rebase PR head were not accepted as landing
evidence. The immutable snapshot partitions as 38 landed, 46 not landed, and 22
unverifiable (38 + 46 + 22 = 106).

The live TaskGraph population has since moved to 104 `IN_PROGRESS` +
`implemented` tasks (61 ownerless, 43 owned). That live population is not folded
into the immutable snapshot. Rerun the audit before using it for a later mass
closure.

## Landed and goal met (3/106)

These are the only task closures supported by both landing ancestry and the
separate goal-completion audit:

- `hermit-container-runtime-prototype` — Hermit PR #1179, replay
  `c68f150aa702314c7a13c2e097c40e54ea56ea3f`, ancestry rc=0.
- `make-all-backends-default-build` — Hermit PR #1433, replay
  `88d3d019f7de967071122fd0ebb18b1b16e9354d`, ancestry rc=0.
- `test-shrink-optimization-skill` — Hermit PR #1416, replay
  `9e37635de13f16916ab627b0372976dc28377ddb`, ancestry rc=0.

## Landed with residual (33/106)

Six tasks have an explicit verification condition that is not met:

- `fix-execd-sibling-admission-quiescence`
- `fix-kvm-detpid-mismatch`
- `fix-parallel-make-determinism`
- `hb-impl-anchor-model`
- `liteinst-straddler-wait-calibration`
- `sabre-detlog-forwarder`

Twenty-seven landed tasks define no `Verify` field, so landing alone cannot
establish goal completion:

- `audit-fabricated-numbers-presented-as-data`
- `cargo-publish-metadata-cleanup-0p2`
- `crate-versions-0.2.0-floor`
- `dbi_preemption_via_safe`
- `demo5-fix-qemu-icount-idlewarp`
- `derive-test-footprints-from-cargo-metadata`
- `e2e_coreutils_shuf_permutation`
- `e2e_data_handling_jq`
- `e2e_lua_5_4`
- `e2e_m4_macro_processor`
- `e2e_mktemp_name_determinism`
- `e2e_openssl_ed25519_keygen`
- `e2e_openssl_enc_determinism`
- `e2e_perl_random_determinism`
- `e2e_proc_random_uuid_2`
- `e2e_ruby_random_determinism`
- `e2e_rust_hashmap_iteration`
- `e2e_sort_random_determinism`
- `e2e_system_utils_openssl`
- `e2e_test_c_stl`
- `e2e_uuidgen_random_determinism`
- `e9patch-ratchet-round2-nongated`
- `fix-ci-manifest-guests-timeout-headroom`
- `fix_kvm_detlog_verify`
- `hermit-features-cfg-review`
- `hermit-oci-dropin-design`
- `kvm_example_parity_milestone`

## Landed but still active (2/106)

Do not close these tasks. The current TaskGraph rows still have live owners:

- `factor-thirdparty-backends-into-separate-packages` — `BACKLOG`, owner
  `hermit-247`.
- `sabre-timed-progress-bar-verify-determinism` — `IN_PROGRESS`, owner
  `hermit-sabre`.

## Not landed (46/106)

These remain drain work, not bookkeeping:

- `add-dont-break-demos-principle`
- `add-long-running-multibackend-perf-tests`
- `add-preemption-counts-to-run-summary`
- `backend-abstraction-lint-covers-half-the-backends`
- `backend-prefix-match-and-cli-cleanup`
- `backend-short-flag-b`
- `build-debug-episode-cli-and-migrate`
- `ci-cancellation-masking-let-red-land`
- `ci-hub-smart-selection-in-validate`
- `ci-validate-timing-history-query`
- `command-strict-verify-10pct-flaky`
- `common_lazy_backend_stats`
- `dbi-ci-timeout-investigation`
- `dbi-m4-multiprocess`
- `dbt-corpus-round-nongated-3`
- `demo5-fix-pmu-skid-reverie-robustness`
- `determinize-ghc-rts-ticker`
- `document_compatibility_ratchet_provenance`
- `e2e_language_runtimes_tcl`
- `e2e_python_hashseed_determinism`
- `e2e_sqlite_database_engine`
- `e2e_test_coreutils_shuf`
- `e2e-python-script-determinism`
- `e9patch_ptw_promote_agreed_subset`
- `e9patch-corpus-round-3`
- `fix-load-dependent-scheduling-vtime`
- `fix-pr1180-rustdoc-link`
- `hermit-02-release-readiness-assessment`
- `kvm-corpus-round-nongated`
- `liteinst-instrumentation-stats`
- `liteinst-perf-attribution-fastpath`
- `load-bearing-shorthand-must-be-defined`
- `logdiff-unsafe-strip-lines-rename`
- `make_plugin_detcore_build`
- `make_stale_hermit_dir`
- `matrix-tsv-relocate-to-parent`
- `parallel-experiment-runner`
- `policy-demo-touching-commits-mandatory-adversarial-review`
- `pr_359_correct_vendored`
- `pr_359_key_dynamorio`
- `relocate-generated-output-to-hermit-ignored`
- `reverie_345_correct_so`
- `sabre_non_gated_parity`
- `sabre-env-caching-fix-reverie-3abfe7a`
- `select-tests-separate-config-from-machinery`
- `split-matrix-asymmetric-tests-from-code`

## Unverifiable (22/106)

These have no supported implementation-PR replay SHA or have a non-GitHub
deliverable. They must not be silently classified as not landed or closed:

- `agent-to-agent-sendmessage-fails-fleetwide`
- `audit-every-merge-gate-requirement-has-a-signer`
- `benchmark-writeup-and-skill`
- `ci-hub-first-bad-query-from-local-history`
- `ci-hub-newest-green-main`
- `dbt-ratchet-round2-nongated`
- `demo5-rigorous-rootcause`
- `every-agentic-command-needs-quickstart`
- `fix-commit-is-destructive-rule-misfire`
- `gvisor-systrap-benchmark-repro`
- `gvisor-writeup-overhaul-colleague-ready`
- `landing-skills-redundancy-and-discoverability`
- `nightly-demo-sweep-ci`
- `rb-drb-modern-frontier-research`
- `relocate-tick-hub-config-into-version-control`
- `reserve-crate-names-hermit-run-hermetic-infra`
- `retired_agents_leave_detached`
- `scheduler-vtime-jump-unproductive-pollers`
- `sprint-fbsource-mini-release-import`
- `study-min-vtime-scheduler-alternatives`
- `super-validate-audit`
- `tick-hub-usage-audit`

## Closure rule

Only the three `Landed and goal met` tasks are closure candidates from this
snapshot. The 33 residual tasks and two live-owner tasks remain open. The 46
not-landed tasks remain drain work. The 22 unverifiable tasks require note repair
or task-specific artifact verification.
