# Stranded Implemented-Task Dispositions

This follows the 2026-08-04 implemented-task ancestry sweep. Its nine stranded
tasks collapse to five closed-unmerged Hermit PR heads. Fresh source and PR
state were checked against Hermit main `ca719dfad4ae4d4b097f10461c017315753d549c`.

## Summary

| Disposition | Tasks |
| --- | ---: |
| Superseded, objective already present | 1 |
| Superseded with named salvage | 2 |
| Worth focused replay | 6 |
| Genuinely dead | 0 |
| **Total** | **9** |

## Per-Task Decisions

### `ci-validate-timing-history-query`: SUPERSEDED

Do not reopen Hermit #1210 (`66480061`). Hermit main now pins agent-utils
`ec4ddf07689fae6baf9e55c76b8247487ea9705f` through Hermit commit
`74a5b6b5`; that pin descends from #1210's requested `1c0e9c3c` and contains
the default profile store plus `summary build/merge/plan/stats`. Current
`ci/run-dag.sh` deliberately uses the tracked source resolver rather than
#1210's prebuilt-Rust-runner wiring. Carry nothing from the old Hermit branch:
its functionality arrived through a newer architecture, and its `-j 2`
hosted-runner workaround is an underived constant now audited separately.

### `make_plugin_detcore_build`: SUPERSEDED WITH SALVAGE

Do not reopen Hermit #1564 (`7c7838fb`) wholesale. It has extensive conflicts
with current crate deletion/factoring, locks, manifests, and backend dispatch.
The superseding architecture surfaces are live Reverie #359 (vendored payload
packaging) and the named Hermit package/install tasks
`factor-thirdparty-backends-into-separate-packages` and
`dbi-to-dbt-rename-and-install-path-factoring`.

Carry forward only commit `1c860845`'s invariant and tests: the helper identity
must cover the locked resolved non-dev dependency graph, reachable local source,
features/cfg, compiler flags/wrappers, target/profile, and `rustc -vV`; a
dependency-resolution mutation must change the identity. Do not carry the old
Reverie pin, obsolete `detcore-dbi` layout, or #1564's complete plugin/dispatch
tree.

### `make_stale_hermit_dir`: SUPERSEDED WITH SALVAGE

Use the same superseding surfaces as above; do not reopen #1564. Carry only
`0b904f49`'s selected-root repair contract and real-Cargo regression: a helper
found under `HERMIT_DIR/bin` must be replaced through `cargo install --root`
for that exact root, then rediscovered as the new version. Do not carry the old
helper packages, pin set, or full plugin protocol wholesale.

### `fold_edit_distance_into`: WORTH REPLAYING

Replay the focused one-commit Hermit #1582 (`b0b648bc`) rather than reopening
its stale PR. Current main still contains the standalone `common/edit-distance`
package and both consumers still depend on it. A synthetic merge of #1582 onto
fresh main completed without conflict. The original reason still binds: remove
the unrelated crates.io name dependency while preserving the byte-for-byte
algorithms and 24 tests. Re-run generated footprint and package checks at the
new head.

### `add-preemption-counts-to-run-summary`: WORTH FOCUSED REPLAY

Do not reopen bundled Hermit #1341. Current main contains none of
`syscall_boundary_preemptions`, `branch_preemptions`, or the displayed summary,
and live skid PR #1576 does not touch the five accounting files. Replay only
commit `297401c0`'s five-file observational accounting change. Exclude #1341's
nonfatal over-skid mode, old Reverie pin, manifest edits, and lock churn. The
old whole PR conflicts broadly with current manifests and crate layout.

### `backend-prefix-match-and-cli-cleanup`: WORTH FOCUSED REPLAY

Reimplement as a small CLI-only change, not by reopening #1444. Current main
still has exact-value `--backend`, retains the post-subcommand compatibility
path, and has no unique-prefix parser. Preserve exhaustive help and explicit
zero/multiple-match errors. Exclude all performance tests, artifact relocation,
generated inventory, and compatibility-count changes from #1444.

### `backend-short-flag-b`: WORTH FOCUSED REPLAY

Land with the focused backend CLI change above. Current main declares only
`#[clap(long)]` for the global backend option, so `-b` is still absent. Do not
reopen the seven-commit #1444 branch.

### `logdiff-unsafe-strip-lines-rename`: WORTH FOCUSED REPLAY

Land as a small CLI/docs safety change. Current main still publicly exposes
`--strip-lines` with a mild description, and project skills teach that spelling.
Make `--unsafe-strip-lines` visible with the determinism warning and retain the
old name only as a hidden compatibility alias. Exclude #1444's unrelated test
and output-relocation changes.

### `add-long-running-multibackend-perf-tests`: WORTH TEST-ONLY REPLAY

Do not reopen #1444. Current main has no `performance.toml` and no files under
`tests/e2e/performance/`; the old branch has 30 measured workloads. Their
manifest cells are all `ci = false`, so they add manual measurement coverage
without adding validate wall time. Re-submit only the performance sources,
manifest, explicit inventory disposition, and current symmetry checks. Preserve
the measured backend exclusions; exclude every CLI and repository-wide artifact
relocation from #1444.

## Other Candidate

`bea22ccc96d6e59586c8fb928c5719785501a54e` is no longer stranded or
not-submitted. It was rebased to Reverie PR #365 head `77b37173`, then landed as
`6adcc98d75657af4c8b6b6e3b592f26d05e34003`; fresh main ancestry returned rc 0.

No source branch was modified while making these dispositions.
