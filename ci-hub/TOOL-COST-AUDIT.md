# Tool cost compliance audit

Audit date: 2026-08-03. Scope: executable operator/developer tools under parent
`scripts/`, `ci-hub/`, `hermit/{ci,scripts}/`, and `reverie/scripts/`. Tests and
small generated artifacts are not separate operator entrypoints.

## Compliant in this change

| Surface | Estimate | Final wall + CPU | Notes |
| --- | --- | --- | --- |
| `ci-hub/ci-hub` commands | yes | yes | Operation-specific; full vs incremental history is distinguished. |
| `ci-hub/bin/main-health` | yes | yes | Scales with explicit/default repository count. |
| `ci-hub/bin/pr-status` | yes | yes | Scales with explicit/default repository count. |
| `ci-hub/bin/health-tick` | yes | yes | One cadence-filtered tick estimate. |
| Any command launched through `ci-hub/bin/tool-cost` | caller-supplied derived estimate | yes | Shared `wait4` child-tree measurement and exit preservation. |

## Assigned concurrently

| Tool | Owner/task | Required completion |
| --- | --- | --- |
| `hermit/validate.sh` | `hermit-227b` / `wire-affected-test-selection-and-measure` | History-derived warm/cold estimate; final wall + CPU on every exit. |
| Multisect tool | `hermit-231b` / `multisect-tool-design` | `(build + run) x repetitions x ceil(log2(commits)) / parallelism` wall estimate, summed CPU estimate, final wall + CPU. |

## Follow-up list

Every row below lacks both required outputs unless noted.

| Priority | Tool(s) | Missing | Concrete estimate basis |
| --- | --- | --- | --- |
| P0 | `scripts/super-validate.sh` | estimate + actual | Selected scope, stress repetitions, historical per-demo/per-gate cost, parallelism. |
| P0 | `hermit/ci/run-dag.sh`, `hermit/ci/run-node.sh`, `hermit/ci/test_harness.sh` | estimate + actual | Selected DAG critical path and summed node baselines; warm/cold artifact state. |
| P0 | `scripts/e2e-union-rebase.sh`, `scripts/e2e-union-resolve.py` | estimate + actual | PR/manifest row count x measured conversion/rebase cost; network retry allowance. |
| P0 | `hermit/scripts/stress-test.sh`, `hermit/scripts/hermit-code-coverage.rs` | estimate + actual | Iterations/program count x measured run/build cost divided by jobs. |
| P1 | `scripts/detached-verify.rs` | estimate + **CPU actual** | It reports wall duration today; add caller estimate and `wait4` CPU. |
| P1 | `scripts/run_experiment.sh`, `scripts/prepare-demo08-assets.sh` | estimate + actual | Repetitions/assets/download bytes and build/run history. |
| P1 | `scripts/install-deps.sh`, `scripts/checkout-optional-submodules.rs`, `scripts/submodules.sh` | estimate + actual | Missing submodule/dependency count, expected download bytes, warm/cold state. |
| P1 | `scripts/allocate-worktree.rs`, `scripts/release-worktree.rs`, `scripts/worktree-gc.sh`, `scripts/slot-init.sh` | estimate + actual | Product count, checkout/cache size, removal count. |
| P1 | `scripts/pr_conflict_graph.py`, `hermit/scripts/pr-dag-health.sh`, `hermit/scripts/pr_status.py` | estimate + actual | Open PR count, pair count, and GitHub API request count. |
| P1 | `hermit/ci/power-to-weight.rs`, `hermit/ci/select-tests.rs` | estimate + actual | Candidate/path/node counts; history scan size. |
| P1 | `hermit/scripts/progress-report.sh`, `hermit/scripts/compat-map.sh`, `hermit/scripts/manifest-to-commands.rs` | estimate + actual | Manifest/test count and invoked probe count. |
| P1 | `hermit/scripts/stage-liteinst-runtime.sh` | estimate + actual | Build profile, cache state, artifact size. |
| P2 | `scripts/primary_checkout.py`, `scripts/doctor.sh`, `scripts/resource_audit.sh`, `scripts/verify-slot-pushed.sh` | estimate + actual | Checkout/slot/process counts and remote queries. |
| P2 | `scripts/lint-memory-skill-sync.rs`, `scripts/sync-memory-skill.rs`, `scripts/check-demo-review.sh`, `scripts/check-parent-gitmodules.sh` | estimate + actual | Skill/file/diff counts. |
| P2 | `ci-hub/bin/agent-tool`, direct `ci-hub/landing/landing-lock.sh`, and direct `ci-hub/runners/*` commands | estimate + actual | Wrapped through the front door where available; direct invocation still lacks the contract. |
| P2 | `reverie/scripts/backend-submodule.sh`, `reverie/scripts/dump-vdso.py` | estimate + actual | Backend/submodule count or input image size. |

Fast syntax-only checkers can legitimately estimate near-zero cost, but they
still need the final line: the convention is valuable only when callers can
depend on it uniformly, including failure and early-exit paths.
