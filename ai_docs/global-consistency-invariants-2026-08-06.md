# Global consistency invariants — 2026-08-06

Audit time: 2026-08-06 PDT. Repository identities are deliberately separate:

- Hermit `origin/main`: `4c70658e785834737cbe1524f77330c781a6f5ea`
- dev-hermit parent `origin/main`: `bf017055596fce31750dac7ec62e9140adc7f41b`
- Reverie `origin/main`: `dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6`
- LiteInst2 `origin/main`: `8bf704feb06a62e7a05bee3b237d70793e4e2689`
- agent-utils `origin/main`: `78c79a8b2dd557142f9e30301cafef6484c0488f`

All five refs were refreshed through `herdr-run --agent hermit-coord 'with-proxy git fetch ...'` before ancestry checks. The Hermit SHA is never used as the parent base, and the parent SHA is never used as a Hermit base.

## Decision table

| Invariant | Deciding command | Result | Inconsistency / repair |
|---|---|---:|---|
| 1. Every live Hermit branch descends from Hermit main `4c70658e7` | For each active, non-detached Hermit row derived from `worktree-state.json`: `git -C <path> merge-base --is-ancestor 4c70658e785834737cbe1524f77330c781a6f5ea HEAD` | **FAIL: 13/53 pass; 40/53 fail** | The registry was first repaired to 75 rows and zero drift. The 40 failures are concurrently owned feature branches, 7–102 required-main commits absent. Rebasing them here would violate branch ownership. |
| 2. Hermit Reverie references, parent Reverie gitlink, and Reverie primary agree | `scripts/check-reverie-pin.rs --repo worktrees/clone/hermit` plus `git ls-tree HEAD reverie`, `git -C reverie rev-parse HEAD origin/main`, and branch check | **FAIL** | Canonical tool derived 17 tracked `Cargo.toml` + 4 `Cargo.lock`; 10 files contain **46** Reverie revision entries, all at intended `dd3c178…`. The primary was safely fast-forwarded from `025d378…` to `main@dd3c178…`. Parent gitlink remains `04a46b4…`, so it alone disagrees. The explicit submodule-bump rule and the divergent/shared parent prevent an automatic commit. Live `ls-remote` from the jailed checker received proxy 403; the intended tip was independently established by the successful outside-jail fetch. |
| 3. agent-utils parent pin, canonical checkout, and fetched main agree | `scripts/check-agent-utils-pin.rs --no-fetch` after an outside-jail fetch | **FAIL** | Checkout and fetched main agree at `78c79a8…` on branch `main`; parent gitlink is `c83bcee…`, **4 commits behind**. Checker also reports 5 attributed in-flight commits in four feature worktrees and uncommitted canonical-checkout changes. Updating the gitlink without serializing those owners and safely publishing the parent would be unsafe. |
| 4. Parent repository is clean | `git -C /home/newton/work/dev-hermit status --short \| wc -l` | **FAIL: 13 retained paths after publishing this report** | Four submodule pointer changes, three PR drafts, four ci-hub files, and two experiment trees. They are mixed concurrent ownership. This report was published as an isolated one-file commit based on fresh parent main, then its duplicate was removed from the divergent shared checkout. No blanket add was performed; no submodule pointer was casually committed. |
| 5. Every landed claim is backed by fresh-main ancestry | Typed cohort: extract every `CLOSURE-VERIFIED` `resolved=` SHA and run `merge-base --is-ancestor` against freshly fetched mains. Legacy cohort: query every note beginning `LANDED`, resolve its SHA/PR merge identity, and test the relevant main. | **FAIL (legacy archive); authoritative/current cohorts repaired** | All **92 typed closure notes / 90 tasks / 93 exact references pass**. The 11 nonterminal tasks with legacy `LANDED` notes now have landing ancestry: 5 already carried full ancestors, 5 short-SHA notes resolved to ancestors, and the one orphaned parent SHA `254da829…` was rebound to byte-identical replay `d83a34b…` (same blob `1835101…`). Four tasks that recorded PR heads instead of squash/rebase commits were corrected with fresh GitHub `mergeCommit.oid` values. The full historical legacy archive remains non-authoritative: from 244 notes / 227 tasks, 29 exact notes remain without a current-main ancestor and 27 additional notes remain short-only/unverified after the current repairs. They must be replay-mapped or explicitly marked obsolete; an API `MERGED` flag is insufficient. |
| 6. Every coalesced head has a qualifying receipt at that exact SHA | `./ci-hub/ci-hub validate-status <SHA> --json` | **FAIL: 0/5 coalesced heads; 0/1 adjacent rebased head** | `e6eba9b…`, `51fae5e…`, `6c5ce83…`, `bf17adf…`, and `445bbd5…` all return `NOT-VALIDATED`, exit 4, `qualifying_count=0`. The adjacent rebased determinism head `6b1090d…` is also not validated. Full validation is box-exclusive and was not multiplied across heads; exact-head receipts cannot be synthesized or reused after a rebase. |

## Live branches that fail the one-base predicate

The list is derived from the repaired registry, not a remembered inventory. Columns are slot, branch, head, number of commits from required Hermit-main history absent, and task.

```text
226 | feat/verify-verdict-independent-of-exit | 4ecf4c578440d8729e3de8350c9fdd4c27ec4bc1 | 49 | groupa-drain-1221-1255
227b | orc/restamp-validate-prelude | 64ffb514782991b1f079395c1f67f93003598eb2 | 29 | stopping-a-validate-is-not-free-some-stop-paths-write-false-reds
243 | validate/pr-1397-rebase | 76359d835a89d2f0d43ef46342fac1f0b596f3fa | 90 | priority-based-ci-planner-owns-the-batch
250 | codex/skid-gated-retry | b15a29daee9943ed75eae988945052829a93b33a | 102 | reverie-single-runner-spof-add-second
anchorwire | fix/anchor-select-qualifying-baseline | 58082897d51fb42ac885c97276caeca63b9fd4a2 | 7 | wire-inert-phase2-guards-into-consumers
canon | local-rebase-1595 | adf919ff204039fd19e3995c595e1e9c8b8e6bcf | 46 | canonicalize-dont-strip-verify-must-preserve-distinguishability
citimeout | codex/ci-manifest-cpu-timeout | c38c5c9df70ad901f427baa7fba1bafbaf1b8375 | 45 | ci-timeout-audit-cpu-time-manifest-node
cleanbuild | feat/validate-zero-byte-object-preflight | ec5a00402a411469e4a33a80c36d453dfe58340c | 45 | clean-rebuild-after-failure-but-scoped-by-what-can-actually-corrupt
codex-reviewer | codex/product-side-xfail-strict-evidence | c26e70eb089542657ec50897ca80ef7474b514ec | 40 | fix_1200_codex_review
coord | coord/validate-rs-phase2-full-lane | 1d92c5cddd6df25c56c59447d9a4c0315b59d686 | 26 | port_validate_sh_to
coord-drain | rebase/1147-b3 | 6a6f41c9777bf84cf71da4cdd64c8604e15ee29f | 36 | (unattributed registry task)
covnode | codex/validate-emit-coverage-node-land | fc49593ac21c7655e841a3de825ef86692ad117c | 25 | wire_the_coverage_node
dbi-fchown | codex/dbi-fchown-identity | f89c69766371806d3c9b2c3003531df2d59d6118 | 7 | determinize_fchown_under_dbi
detlogframe | fix/dbi-detlog-record-framing | 60a460bfcb6563662119f08bc4148a62ef5537d1 | 7 | detlog-record-framing-standardize-all-backends
detwait4 | codex/validate-stop-truncated | a07dd53b242b71b0616c95bdff1f258d71fd8e87 | 67 | stopping-a-validate-is-not-free-some-stop-paths-write-false-reds
devscope | feat/scope-guest-dev-minimal-set | 9570ef9e585e683627bca368b350bbef7b396c63 | 8 | hermit-default-run-passes-entire-host-dev-through
e9bp | drain/pr-1544 | dbe17791423165bff0fae4f2cf5fa58d8ad9ff89 | 101 | drain-e9patch-backend-5prs
e9patch | feat/e9patch-drop-ptrace-downgrade | 2fea6402fc7f08ef96cff05d54fd6542756c814c | 25 | e9patch_hybridptrace_inguest_converge
envhash | local-rebase-1559 | dfeacd1512f6f5dd97f421d2e7bb22632e18cb81 | 27 | env-var-hash-in-info-log
ghdag | feat/portable-dag-third-party-isolation | 8641dac4545c45aa8c45a53165568ec1b5aa2f6e | 44 | collapse-separate-build-nodes-into-one-fat-cargo-invocation
kvm | codex/validate-emit-coverage-node | 630f44aab7fdf4ee52e572c38ae09818e92271b2 | 25 | register_file_hashing_verify
lander2 | rebase/pr-1147-code | 5f4e24ac6e61f4326cebf90a37407b8012e48f23 | 45 | cpu-quota-leaks-into-cargo-num-jobs-cap-must-be-global
liteinst | verify-hang-forced-repro-and-orphan-reap | dadee2c11d4106fbd85453a5064438e39639bc57 | 60 | hermit_run_verify_hangs
nlockgate | codex/nested-lockfile-freshness-gate | 893991acc90a355887fc922add4c9180d312d160 | 49 | liteinst-runtime-build-cargo-lock-is-stale-and-locked-blocks-it
orc-coord | determinize-fchown-under-dbi-map-root | d53550510d1e7d13e84cc8af9bb90269e90b3f07 | 27 | determinize_fchown_under_dbi
parity | feat/parity-mutation-harness | e4ae0400ab1bd7d0df074cd065bcdaaeea6970b1 | 25 | parity-fixture-family-needs-one-shared-mutation-harness
perf | ci/feature-gate-third-party-off-default-build | fc0b76adc59d0d0b686d8a7d6b8babca7a0a11b1 | 45 | portable-dag-gate-emits-nothing-until-done
pinlint | pin-lint-enforce-validate-and-hooks | 35cfefd6585b754148d0856c8538788d2efc67b8 | 48 | pin-lint-exists-but-nothing-forces-it-no-precommit-hook-not-in-validate
pr1147 | pr-1147-dbi-deadlock-fix | c543253509c871512850732eacda959f27a50787 | 46 | pr-1147-dbi-deadlock
rb1595 | trial-rebase-1595 | 330042b3a511731b76672006e3a10e1c6fd32e20 | 36 | rebase-pr-1595-verify-verdict
sabre | fix/findmnt-transient-user-mounts | b045a8ae879d3fb35c78340927520dc50252becd | 32 | determinize_findmnt_against_transient
scwidth | expose-strict-compat-inner-width | 307a0bfe8381bbc4942e1f38f5d03ac7e770cfa0 | 25 | validate-484s-single-gate-dominates
select | codex/validate-smart-selection-additive | 8f0899bdd94248140b4daa0ff4db5a1863e2a216 | 45 | ci-hub-smart-selection-in-validate
slot70 | rebase-work/1552 | c81d05a89e4679ebda0418c6b79b93a0ca7f3b8c | 26 | fix_1588_involuntary_kill
staging-drain | staging/drain-all | d1bf43bfe26b88228b8e8e9f57d2cee3e901838a | 55 | staging-branch-merge-all-prs-test-once
strictcorpus | feat/e2e-jit-and-thread-corpus | 0cab21576285d05248b1dd561151591acaf223d6 | 7 | expand-strict-corpus-new-e2e
vcache | codex/validate-result-cache-by-tree | a0f3d8e887f7e1e361936751b2179780bfced32f | 48 | validate-result-cache-by-sha
vprod | staging/hermit-membership-slice-1412-1543-1515 | 3082454cd9116258902039350f3272bcca0b8990 | 89 | validate_hermit_prs_producer
vselect | codex/validate-smart-selection-default | 11108a3e94b353344a1c6d66eb61614eb9849d63 | 49 | ci-hub-smart-selection-in-validate
wallmeasure | live-wall-measure-2a01963e | 2a01963e6121ea8aa19821c601a9359aed0955df | 46 | live-wall-measure
```

## Actions taken and withheld

Taken:

1. Refreshed every repository main through the audited outside-jail broker.
2. Repaired worktree registry drift; the final repair reconciled 9 branch cells and ended at 75/75 correct rows, 0 drift.
3. Fast-forwarded the Reverie primary from `025d378…` to `dd3c178…` on `main`; verified upstream paths did not overlap its untracked benchmark artifacts.
4. Added TaskGraph correction notes for PR-head-vs-merge identities on #521, #849, #850, #854 and the #861–#910 chain.
5. Rebound `validate-run-ledger`'s orphaned parent SHA to its byte-identical replay commit.

Withheld:

- No other owner's feature branch was rebased.
- No parent submodule gitlink was committed: the parent is divergent and shared, and pointer bumps require an explicit, separately validated decision.
- No agent-utils WIP was staged, reset, or absorbed.
- No validation green was claimed: every queried exact head returned `NOT-VALIDATED`.
- No blanket `git add` was used, and no merge was attempted.
