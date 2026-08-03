# Skills Audit - 2026-08-03

## Executive result

This audit read every active Markdown skill in the coordinator, Hermit, and
Reverie skill directories, both narrow ORC skill files, the ORC registration
code, the coordinator memory-sync scripts, and the current memory mappings.
LiteInst2 has no skill directory. "Active" below means discoverable from an
active skill directory, plus skills registered by the tracked ORC plugin;
archived skills and ordinary non-core memories are not counted.

There are **68 active skills/registrations**: 37 flat coordinator skills, 3 ORC
runtime registrations, 23 Hermit skills, 5 Reverie skills, and 0 LiteInst2
skills. The main findings are:

1. The coordinator memory mirror is mechanically complete today: **37 active
   flat skills, 37 mapped core memories, 37 in sync, 0 linter problems**.
2. That sync is **manual, one-way, and not a freshness guarantee**. No hook,
   workflow, ORC plugin path, or CI job invokes the sync or linter. Newer
   non-core memories already contradict active landing/CI skills.
3. There is **no dev-hermit-to-Hermit skill symlink sharing**. The only
   symlinks are per-repository discovery aliases (`.llms/skills` and
   `.agents/skills`). One active skill's claim that the parent skill directory
   resolves into Hermit is false.
4. Landing policy is materially contradictory. Active coordinator skills say
   `--squash --admin` is an escape and `main` is unprotected, while current
   session memory says the no-bypass ruleset requires a real `merge-gate`,
   `--admin` cannot bypass it, and current merges use rebase/queue mechanics.
5. The Hermit layer carries stale copies of coordinator role and landing
   skills, despite the coordinator skill README saying product repositories
   must not carry them. Several copies disagree on labels, gates, and review
   requirements.

GitHub cannot serve a file below a submodule through a nested dev-hermit blob
URL: even the Hermit, Reverie, and LiteInst2 submodule-entry URLs return 404.
Therefore the layer headings link to the dev-hermit `.gitmodules`
declarations, while each product skill links to the actual Markdown file in
its owning GitHub repository. Those are the valid mobile-review links.

## Direct answers

### Where are the PR-planning skills?

There is **no single general PR-planning skill**. The function is fragmented:

- First-principles solution planning before implementation is in
  [`.claude/skills/research-planning-persona.md`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/research-planning-persona.md).
- Planning deliberately competing draft PRs is in
  [`.orc/plugins/hermit-dev/parallel-speculative-attack.md`](https://github.com/rrnewton/dev-hermit/blob/main/.orc/plugins/hermit-dev/parallel-speculative-attack.md).
- PR review/description/label policy is in
  [`.claude/skills/post-facto-review.md`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/post-facto-review.md).
- Landing and cross-repository sequencing are split between
  [`.claude/skills/hermit-lander.md`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-lander.md) and
  [`.claude/skills/pr-landing-mechanics-merge-gate-uptodate-chase.md`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/pr-landing-mechanics-merge-gate-uptodate-chase.md).
- Finding completed branches that lack PRs is a separate retrospective recipe,
  [`.claude/skills/branch-vs-pr-sweep-mostly-already-prd.md`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/branch-vs-pr-sweep-mostly-already-prd.md).
- Hermit's
  [`.claude/skills/fabler/SKILL.md`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/fabler/SKILL.md)
  provides generic read/plan/execute/review structure, but is not PR-specific.

The gap is the planning step between "a task exists" and "this is the proposed
PR stack, ownership split, dependency order, validation plan, and landing
order." The current files cover research planning and landing, not a normal
end-to-end PR plan.

### Where are the CI-health skills?

> **Resolution (2026-08-03):** the implementation and live-query side of this
> fragmentation is now centralized in [`ci-hub/README.md`](../../ci-hub/README.md).
> The skills below remain trigger/policy or historical context; `ci-hub/` is
> the single home for current code, GitHub refresh, history, health summaries,
> and runner operations.

The primary CI-health role is
[`.claude/skills/hermit-ci.md`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-ci.md).
Focused failure iteration is in Hermit's
[`.claude/skills/ci-debugging.md`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/ci-debugging.md).
Deadline-only parallel local/remote validation is the ORC runtime skill
[`.orc/plugins/hermit-dev/urgent-critical-path-fix-validation.md`](https://github.com/rrnewton/dev-hermit/blob/main/.orc/plugins/hermit-dev/urgent-critical-path-fix-validation.md).

CI state and exceptional mechanics are scattered across
[`.claude/skills/ci-capacity-single-pmu-runner-bottleneck.md`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/ci-capacity-single-pmu-runner-bottleneck.md),
[`.claude/skills/self-hosted-ci-sigsegv-blocks-all-prs.md`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/self-hosted-ci-sigsegv-blocks-all-prs.md),
[`.claude/skills/undraft-does-not-trigger-ci.md`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/undraft-does-not-trigger-ci.md),
[`.claude/skills/validate-sh-cannot-be-green-on-devserver.md`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/validate-sh-cannot-be-green-on-devserver.md), and
[`.claude/skills/validate-sh-rr-compat-counter-conflict.md`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/validate-sh-rr-compat-counter-conflict.md).
The live operational poll itself is registered in
[`.orc/plugins/hermit-dev/index.ts`](https://github.com/rrnewton/dev-hermit/blob/main/.orc/plugins/hermit-dev/index.ts),
not in a CI-health Markdown skill. This is too fragmented, and several snapshot
descriptions are now stale.

## Complete active inventory

### Coordinator file-discovered skills (37)

All are discovered through
[`.llms/skills`](https://github.com/rrnewton/dev-hermit/blob/main/.llms/skills),
which resolves to the real parent [`.claude/skills/`](https://github.com/rrnewton/dev-hermit/tree/main/.claude/skills)
directory.

| Skill | What it is for and when it triggers |
| --- | --- |
| [`backend-reality-reviewer`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/backend-reality-reviewer.md) | Audits backend-completion claims against CLI, `Backend`, `Guest`, Detcore, linkage, and real programs; use on every backend progress/completion claim. |
| [`base-feature-branches-on-frontier`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/base-feature-branches-on-frontier.md) | Historical warning that `frontier` is deleted and all new branches/PRs use `main`; relevant when selecting a base. |
| [`benchmarking`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/benchmarking.md) | Reproducible benchmark artifact and metadata protocol; use when planning, running, reviewing, or publishing benchmarks. |
| [`branch-vs-pr-sweep-mostly-already-prd`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/branch-vs-pr-sweep-mostly-already-prd.md) | Recipe for distinguishing genuinely orphaned work from stale/superseded branches; use for missing-PR sweeps. |
| [`ci-capacity-single-pmu-runner-bottleneck`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/ci-capacity-single-pmu-runner-bottleneck.md) | Dated CI-capacity incident analysis and status-tool note; intended for queued/red fleet diagnosis. |
| [`core-memory-skill-sync-tooling`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/core-memory-skill-sync-tooling.md) | Defines the memory-to-flat-skill mirror contract and manual sync/lint tools; use when changing coordinator skills or memories. |
| [`dbi-no-runtime-tool-selection`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/dbi-no-runtime-tool-selection.md) | Records DBI's compile-time tool selection and limited event dispatch; relevant to DBI Tool/Guest integration work. |
| [`detached-clean-merged-slot-can-be-busy`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/detached-clean-merged-slot-can-be-busy.md) | Warns that clean/detached slots may have live users/processes; use before slot removal. |
| [`git-stash-shared-across-worktrees`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/git-stash-shared-across-worktrees.md) | Prohibits ambiguous stash use because worktrees share `refs/stash`; use before any stash operation. |
| [`good-hermit-binary-for-tests`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/good-hermit-binary-for-tests.md) | Dated advice to prefer one debug binary over known-stale builds; intended when a workload unexpectedly hangs. |
| [`hermit-agents-md-project-scoped-claude-symlink`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-agents-md-project-scoped-claude-symlink.md) | Explains product/coordinator policy scope and the Hermit `CLAUDE.md` link; also contains an obsolete direct-push recipe. |
| [`hermit-ci`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-ci.md) | Fixed-role charter for CI monitoring, diagnosis, and CI configuration; load for `hermit-ci` or CI-health work. |
| [`hermit-coord`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-coord.md) | Fixed-role coordinator charter for task, slot, primary, parent, pin, and closure ownership; load for `hermit-coord`. |
| [`hermit-dbi`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-dbi.md) | Fixed-role DBI/DynamoRIO compatibility and integration charter; load for DBI dispatch/work. |
| [`hermit-e9patch`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-e9patch.md) | Fixed-role e9patch AOT rewriting and coverage charter; load for e9patch dispatch/work. |
| [`hermit-kvm`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-kvm.md) | Fixed-role KVM parity charter measured against ptrace and gVisor; load for KVM dispatch/work. |
| [`hermit-lander`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-lander.md) | Fixed-role PR integration/landing charter; load for `hermit-lander`. |
| [`hermit-linux`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-linux.md) | Linux/QEMU/kernel/snapshot/record-replay/sched_ext charter; load for Linux VM work. |
| [`hermit-liteinst`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-liteinst.md) | Fixed-role LiteInst Guest/probe integration charter; load for LiteInst dispatch/work. |
| [`hermit-opt`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-opt.md) | Performance and cross-backend benchmark charter; load for optimization/benchmark work. |
| [`hermit-sabre`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-sabre.md) | Fixed-role SaBRe Guest/example compatibility charter; load for SaBRe dispatch/work. |
| [`multi-backend-tool-binaries`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/multi-backend-tool-binaries.md) | Dated architecture note for one Tool crate with per-backend binaries and divergent runner APIs; relevant to multi-backend Tool packaging. |
| [`parent-git-size-is-local-modules-not-history`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/parent-git-size-is-local-modules-not-history.md) | Dated explanation of why parent `.git` disk usage is local module metadata; use during repository-size diagnosis. |
| [`parked-slot-reuse-is-racy`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/parked-slot-reuse-is-racy.md) | Warns against claiming an apparently parked slot without assignment; use before slot reuse. |
| [`post-facto-review`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/post-facto-review.md) | Canonical review, label, PR-body, dual-review, and post-facto landing policy; default for product PRs. |
| [`pr-landing-mechanics-merge-gate-uptodate-chase`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/pr-landing-mechanics-merge-gate-uptodate-chase.md) | Dated landing-sweep mechanics for merge-gate and stale branches; intended when landing Hermit/Reverie PRs. |
| [`progress-reports-location-and-skill-symlink`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/progress-reports-location-and-skill-symlink.md) | Claims a canonical report location and cross-layer symlink topology; both claims conflict with current files. |
| [`progress-rubric`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/progress-rubric.md) | Evidence rubric and live cross-mode measurement procedure; use for project progress reports. |
| [`record-requires-sequentialization`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/record-requires-sequentialization.md) | Explains why record/replay requires sequentialized scheduling and why QEMU remains blocked; relevant to R/R or QEMU design. |
| [`research-planning-persona`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/research-planning-persona.md) | Generates diverse, scored first-principles approaches for hard determinism/scheduling problems; use before committing to one hard-problem implementation. |
| [`reverie-branches-hold-pinned-build-deps`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/reverie-branches-hold-pinned-build-deps.md) | Requires reachability checks before deleting Reverie branches that may hold pinned SHAs; use for branch cleanup. |
| [`self-hosted-ci-sigsegv-blocks-all-prs`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/self-hosted-ci-sigsegv-blocks-all-prs.md) | Dated systemic self-hosted SIGSEGV incident and mitigation; intended for red-check attribution. |
| [`syscall-classification-two-lists-and-failclosed-gating`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/syscall-classification-two-lists-and-failclosed-gating.md) | Explains classification/dispatch coupling and strict fail-closed behavior; use for syscall support/classification changes. |
| [`undraft-does-not-trigger-ci`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/undraft-does-not-trigger-ci.md) | Records the workflow event behavior of `gh pr ready`; use when deciding whether undrafting enqueues CI. |
| [`validate-sh-cannot-be-green-on-devserver`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/validate-sh-cannot-be-green-on-devserver.md) | Dated host-baseline validation limitations and focused-evidence workaround; use when local full validation is red. |
| [`validate-sh-rr-compat-counter-conflict`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/validate-sh-rr-compat-counter-conflict.md) | Reconciles the record/replay expected-count conflict and describes a dated gate model; use for relevant rebases. |
| [`worktree-cleanup-is-unsafe-for-agents`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/worktree-cleanup-is-unsafe-for-agents.md) | Conservative worktree cleanup warning; use before any bulk cleanup. |

### Coordinator ORC runtime skills (3)

These are registered by
[`.orc/plugins/hermit-dev/index.ts`](https://github.com/rrnewton/dev-hermit/blob/main/.orc/plugins/hermit-dev/index.ts),
not discovered from parent skill frontmatter.

| Runtime skill | What it is for and when it triggers |
| --- | --- |
| [`hermit-dev` / `AGENTS.md`](https://github.com/rrnewton/dev-hermit/blob/main/AGENTS.md) | The plugin registers and activates the entire canonical coordinator policy for dev-hermit/fork/Reverie-related triggers at startup. |
| [`hermit-parallel-speculative-attack`](https://github.com/rrnewton/dev-hermit/blob/main/.orc/plugins/hermit-dev/parallel-speculative-attack.md) | Coordinator-only, gated 3-4-way competing-PR protocol for an owner deadline or quantified critical-path stall. |
| [`hermit-urgent-critical-path-fix-validation`](https://github.com/rrnewton/dev-hermit/blob/main/.orc/plugins/hermit-dev/urgent-critical-path-fix-validation.md) | Coordinator-only parallel local/CI validation and tight failing-test loop under the same deadline/critical-path gate. |

### [Hermit layer in dev-hermit](https://github.com/rrnewton/dev-hermit/blob/main/.gitmodules#L1-L4) (23)

Hermit exposes its real directory through both
[`hermit/.llms/skills`](https://github.com/rrnewton/hermit/blob/main/.llms/skills)
and [`hermit/.agents/skills`](https://github.com/rrnewton/hermit/blob/main/.agents/skills).

| Skill | What it is for and when it triggers |
| --- | --- |
| [`backend-reality-reviewer`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/backend-reality-reviewer/SKILL.md) | Product copy of backend completion audit, with additional demo/time/review rules; use for backend claims. |
| [`benchmark`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/benchmark.md) | Hermit-specific cgroup, K-core, median, slowdown-decomposition benchmark protocol; use for performance experiments. |
| [`ci-debugging`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/ci-debugging.md) | Tight single-shard reproduction via `ci/run-node.sh`/`validate.sh --only`; use whenever a red lane needs a fix. |
| [`continuous-virtual-time-is-sacred`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/continuous-virtual-time-is-sacred/SKILL.md) | Rejects clock/time/scheduler changes that reduce continuous deterministic virtual time; use for time or backend-parity review. |
| [`deadlock-debugging`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/deadlock-debugging.md) | Log-first workflow for hangs, futex/timed-wait/external-I/O stalls, and no-progress; use when a guest wedges. |
| [`determinism-regression-debugging`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/determinism-regression-debugging/SKILL.md) | Bisect and good-vs-bad log diff for regressions; use only when behavior previously worked. |
| [`fabler`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/fabler/SKILL.md) | Generic read/plan/execute/adversarial-verify workflow; triggers on complex research, architecture, implementation, or audits. |
| [`hermit-ci`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/hermit-ci.md) | Diverged product copy of the fixed CI-health role charter; load for `hermit-ci`/CI work. |
| [`hermit-coord`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/hermit-coord.md) | Diverged product copy of coordinator task/slot/parent/closure policy; load for `hermit-coord`. |
| [`hermit-dbi`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/hermit-dbi.md) | Diverged product copy of the DBI role charter; load for DBI work. |
| [`hermit-debugging`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/hermit-debugging/SKILL.md) | General log-first debugging for nondeterminism, hangs, syscalls, and scheduling; use on unexpected Hermit behavior. |
| [`hermit-kvm`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/hermit-kvm.md) | Diverged product copy of the KVM role charter; load for KVM work. |
| [`hermit-lander`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/hermit-lander.md) | Diverged product landing charter with stale labels/gate wording; load for `hermit-lander`. |
| [`hermit-liteinst`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/hermit-liteinst.md) | Diverged product copy of the LiteInst role charter; load for LiteInst work. |
| [`hermit-opt`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/hermit-opt.md) | Diverged product copy of the performance role charter; load for benchmark/performance work. |
| [`hermit-sabre`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/hermit-sabre.md) | Diverged product copy of the SaBRe role charter; load for SaBRe work. |
| [`human-review-first`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/human-review-first/SKILL.md) | Discoverable but dormant alternative that requires explicit user activation before human-gated landing. |
| [`post-facto-review`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/post-facto-review/SKILL.md) | Diverged product copy of default post-facto landing discipline; use for autonomous product landing. |
| [`presenting-quantitative-data`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/presenting-quantitative-data.md) | Makes ratios, tables, charts, and quantitative claims traceable; use for any numerical presentation. |
| [`progress-rubric`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/progress-rubric/SKILL.md) | Byte-identical regular-file copy of the coordinator progress rubric; use for progress reports. |
| [`repo-cleanliness`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/repo-cleanliness.md) | Keeps product repos free of harness artifacts, binaries, experiments, and nested repos; use before every commit/stage decision. |
| [`test-shrink-optimization`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/test-shrink-optimization/SKILL.md) | Reduces test cost while preserving real coverage; use for slow tests/manifests or occasional-gate decisions. |
| [`ux-tester`](https://github.com/rrnewton/hermit/blob/main/.claude/skills/ux-tester.md) | First-user validation of CLI/report/demo/workflow output quality; triggers broadly whenever validating user-facing behavior. |

### [Reverie layer in dev-hermit](https://github.com/rrnewton/dev-hermit/blob/main/.gitmodules#L5-L8) (5)

Reverie exposes its real directory through both
[`reverie/.llms/skills`](https://github.com/rrnewton/reverie/blob/main/.llms/skills)
and [`reverie/.agents/skills`](https://github.com/rrnewton/reverie/blob/main/.agents/skills).

| Skill | What it is for and when it triggers |
| --- | --- |
| [`adding-a-backend`](https://github.com/rrnewton/reverie/blob/main/.claude/skills/adding-a-backend.md) | Implements/extends `Backend::run<T>`, `Guest<T>`, RPC state, and serviced syscalls; use for backend work. |
| [`repo-cleanliness`](https://github.com/rrnewton/reverie/blob/main/.claude/skills/repo-cleanliness.md) | Byte-identical regular-file copy of Hermit's product-repository hygiene rules; use before every commit/stage decision. |
| [`reverie-architecture`](https://github.com/rrnewton/reverie/blob/main/.claude/skills/reverie-architecture.md) | Tool/GlobalTool/Guest/Backend architecture and crate map; read first for work anywhere in Reverie. |
| [`syscall-interception`](https://github.com/rrnewton/reverie/blob/main/.claude/skills/syscall-interception.md) | Subscription, syscall hook, injection, memory, typed syscall, and backend trap guide; use for syscall handlers. |
| [`testing-tools`](https://github.com/rrnewton/reverie/blob/main/.claude/skills/testing-tools.md) | Build/run/test procedure for Reverie Tools and backends; use before validation or new Tool work. |

### [LiteInst2 layer in dev-hermit](https://github.com/rrnewton/dev-hermit/blob/main/.gitmodules#L9-L12) (0)

The [LiteInst2 repository](https://github.com/rrnewton/liteinst2) has no
`.claude/skills`, `.llms/skills`, or `.agents/skills` path. The coordinator
[`hermit-liteinst`](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-liteinst.md)
role charter does not replace a standalone LiteInst2 implementation/testing
skill. This is an actual layer-coverage gap.

## Audit findings

### Contradictions and wrong current facts

1. **Landing mechanics are unsafe and obsolete.**
   [The active landing-mechanics skill](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/pr-landing-mechanics-merge-gate-uptodate-chase.md)
   says `--squash --admin` bypasses the stale up-to-date requirement and calls
   `main` unprotected. Current local memories
   `$HOME/.claude/projects/-home-newton-work-dev-hermit/memory/hermit-main-ruleset-dual-required-checks.md`
   and `.../merge-gate-pr-runs-stall-idle-gate-runners.md` say the 2026-08-03
   no-bypass ruleset requires `merge-gate`, `--admin` cannot bypass it, and the
   working merge path is rebase/queue-oriented. The older skill can cause an
   agent to issue the wrong merge command and misdiagnose the rejection.

2. **CI gate descriptions disagree with current rules.**
   [The CI role](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-ci.md),
   [the lander role](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-lander.md),
   [the self-hosted incident skill](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/self-hosted-ci-sigsegv-blocks-all-prs.md),
   and [the counter-conflict skill](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/validate-sh-rr-compat-counter-conflict.md)
   encode different generations of "real gate" policy. Some say `main` is
   unprotected and self-hosted red is bypassable; current memory says the
   ruleset itself is no-bypass and only `merge-gate` is required. Historical
   incident attribution has been left in the active trigger surface.

3. **The claimed progress-rubric symlink does not exist.**
   [The location/symlink skill](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/progress-reports-location-and-skill-symlink.md)
   says parent `.llms/skills` resolves into the Hermit submodule. `ls -l` and
   `readlink -f` show it resolves to parent `.claude/skills`. The parent
   [progress rubric](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/progress-rubric.md)
   and Hermit [progress rubric](https://github.com/rrnewton/hermit/blob/main/.claude/skills/progress-rubric/SKILL.md)
   are separate regular files with identical bytes. The location skill says
   reports belong in `docs/progress-reports`, while both rubric copies still
   prescribe `ai_docs/progress-reports`.

4. **Product landing copies use obsolete labels.** Hermit's
   [coordinator copy](https://github.com/rrnewton/hermit/blob/main/.claude/skills/hermit-coord.md)
   refers to `human-review`; Hermit's
   [lander copy](https://github.com/rrnewton/hermit/blob/main/.claude/skills/hermit-lander.md)
   instructs agents to add the obsolete `post-facto-review` label. Parent
   [post-facto policy](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/post-facto-review.md)
   and [canonical policy](https://github.com/rrnewton/dev-hermit/blob/main/AGENTS.md)
   explicitly prohibit both.

5. **Review requirements diverge.** Parent
   [post-facto policy](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/post-facto-review.md)
   requires Claude+Codex exact-head review for every PR carrying
   `post-facto-human-review`; Hermit's
   [post-facto copy](https://github.com/rrnewton/hermit/blob/main/.claude/skills/post-facto-review/SKILL.md)
   limits mandatory dual review to determinism/time/scheduling changes. An
   API-triggered PR can therefore receive different gates depending on launch
   directory.

6. **Direct-main/worktree advice violates current policy.**
   [The Hermit AGENTS/symlink skill](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-agents-md-project-scoped-claude-symlink.md)
   calls direct pushes to Hermit `main` safe and uses raw `git worktree add`.
   [Current coordinator policy](https://github.com/rrnewton/dev-hermit/blob/main/AGENTS.md)
   requires a feature PR and registry-aware slot scripts.

7. **The skill-scope README contradicts the filesystem.**
   [The coordinator skill README](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/README.md)
   says Hermit has only four worker skills and forbids purpose-fixed harness
   role charters in products. Hermit currently has 23 skills, including eight
   duplicated fixed-role charters plus landing/review/coordinator policy.

### Redundancy and unclear boundaries

- **Twelve parent/Hermit concepts overlap.** Eleven are divergent:
  [backend reality](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/backend-reality-reviewer.md),
  [benchmarking](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/benchmarking.md),
  [post-facto review](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/post-facto-review.md),
  and the eight same-named `hermit-ci`, `hermit-coord`, `hermit-dbi`,
  `hermit-kvm`, `hermit-lander`, `hermit-liteinst`, `hermit-opt`, and
  `hermit-sabre` role files. Only
  [progress-rubric](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/progress-rubric.md)
  is byte-identical, and it is still a copy, not a link.
- **Benchmark boundaries are not explicit.** Parent
  [benchmarking](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/benchmarking.md),
  Hermit [benchmark](https://github.com/rrnewton/hermit/blob/main/.claude/skills/benchmark.md),
  Hermit [presenting-quantitative-data](https://github.com/rrnewton/hermit/blob/main/.claude/skills/presenting-quantitative-data.md),
  and parent [hermit-opt](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-opt.md)
  all trigger on benchmark work. They do not state a load order or which rules
  are coordinator artifact policy versus product measurement method.
- **Debugging boundaries are only partly clear.** Hermit's
  [determinism-regression-debugging](https://github.com/rrnewton/hermit/blob/main/.claude/skills/determinism-regression-debugging/SKILL.md)
  clearly distinguishes regression from first bring-up, but
  [deadlock-debugging](https://github.com/rrnewton/hermit/blob/main/.claude/skills/deadlock-debugging.md)
  and [hermit-debugging](https://github.com/rrnewton/hermit/blob/main/.claude/skills/hermit-debugging/SKILL.md)
  both claim hangs/scheduler stalls without precedence.
- **CI health is split between role, procedure, incident snapshots, and ORC
  runtime skill** with no current-state index. This makes a stale incident
  memory as triggerable as the live role charter.
- **Review policy is repeated in role charters.** Repeating label and gate text
  in backend roles guarantees drift. The roles should link to one canonical
  review policy rather than restating it.
- Hermit and Reverie
  [repo-cleanliness](https://github.com/rrnewton/hermit/blob/main/.claude/skills/repo-cleanliness.md)
  files are byte-identical regular copies. This duplication is intentional
  only if each standalone product must carry the policy independently; there is
  no enforcement that they remain equal.

### Bad frontmatter descriptions

- [The CI-capacity description](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/ci-capacity-single-pmu-runner-bottleneck.md)
  says there is one runner and chronic queue starvation, while its own first
  update says the fleet grew to three and that failure mode ended.
- [The self-hosted SIGSEGV description](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/self-hosted-ci-sigsegv-blocks-all-prs.md)
  says all PRs are red, while the body documents a landed mitigation. It is an
  incident title, not a current trigger.
- [The progress-location description](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/progress-reports-location-and-skill-symlink.md)
  asserts a symlink that does not exist.
- [The good-binary description](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/good-hermit-binary-for-tests.md)
  hardcodes an old local artifact/revision and has no invocation phrase. A
  build path cannot be a durable capability claim.
- [The landing-mechanics description](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/pr-landing-mechanics-merge-gate-uptodate-chase.md)
  advertises `--admin escape`, which current rules explicitly reject.
- [The memory-sync description](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/core-memory-skill-sync-tooling.md)
  says "Every active coordinator skill" without limiting that claim to the 37
  flat parent files or saying that synchronization is manual. It excludes the
  three ORC runtime skills.
- Many coordinator snapshot descriptions state a fact but omit "Use when...":
  [DBI tool selection](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/dbi-no-runtime-tool-selection.md),
  [multi-backend binaries](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/multi-backend-tool-binaries.md),
  [parent Git size](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/parent-git-size-is-local-modules-not-history.md),
  [record/sequentialization](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/record-requires-sequentialization.md),
  [Reverie branch pins](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/reverie-branches-hold-pinned-build-deps.md),
  [syscall classification](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/syscall-classification-two-lists-and-failclosed-gating.md), and
  [undraft behavior](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/undraft-does-not-trigger-ci.md).
  This makes triggering depend on keyword coincidence rather than an intended
  task boundary.
- Hermit's [fabler description](https://github.com/rrnewton/hermit/blob/main/.claude/skills/fabler/SKILL.md)
  covers nearly every complex task, and
  [ux-tester](https://github.com/rrnewton/hermit/blob/main/.claude/skills/ux-tester.md)
  says "whenever validating that something works." Both are too broad to
  discriminate reliably without explicit exclusions or precedence.
- The ORC Markdown files have no frontmatter. Their runtime descriptions and
  regex triggers live separately in
  [the TypeScript registration](https://github.com/rrnewton/dev-hermit/blob/main/.orc/plugins/hermit-dev/index.ts),
  so reviewers editing the Markdown cannot see or validate the actual trigger
  contract in the file itself.

### Incompleteness

- LiteInst2 has no product skill for build, testing, architecture, probe safety,
  or repository hygiene.
- There is no ordinary PR-planning skill, only hard-problem research planning,
  speculative fan-out, and landing mechanics.
- CI-health guidance lacks one current, generated gate/run/runner map. The
  active set mixes durable procedure with dated incidents.
- The memory linter checks mapping and byte equality only. It does not check
  age, live GitHub configuration, contradiction with non-core memories,
  contradiction with `AGENTS.md`, broken relative links, or duplicated product
  copies.
- Parent flat skills contain product-shaped relative links such as
  `post-facto-review/SKILL.md` and `progress-rubric/SKILL.md`; those targets do
  not exist in the flat parent directory.
- The ORC registration layer has no check that TypeScript descriptions/triggers
  still match the adjacent Markdown instructions.

## ORC-memory sync audit

### Is there a real 1:1 relationship?

**Yes, but only for the 37 flat parent `.claude/skills/*.md` files.** Running
the read-only linter on 2026-08-03 reported:

```text
active skills: 37  mapped memories: 37  in-sync: 37  problems: 0
RESULT: PASS - every active coordinator skill has one in-sync memory.
```

The source memories are file-based Markdown under
`$HOME/.claude/projects/-home-newton-work-dev-hermit/memory/`. Each mapped
memory declares `metadata.core_memory: true` and an exact
`metadata.core_skill: .claude/skills/<slug>.md`. They are not stored in the
dev-hermit repository.

This 1:1 result does **not** cover the ORC-registered
[`hermit-dev`](https://github.com/rrnewton/dev-hermit/blob/main/AGENTS.md),
[`hermit-parallel-speculative-attack`](https://github.com/rrnewton/dev-hermit/blob/main/.orc/plugins/hermit-dev/parallel-speculative-attack.md), or
[`hermit-urgent-critical-path-fix-validation`](https://github.com/rrnewton/dev-hermit/blob/main/.orc/plugins/hermit-dev/urgent-critical-path-fix-validation.md)
skills, nor any Hermit/Reverie/LiteInst2 product skill.

### Is it current?

**Mechanically yes; semantically no.** The mapped memories and their generated
flat skills have equal normalized content today. But newer non-core memories
already supersede the active landing skill's merge style, branch-protection,
and `--admin` claims. The linter does not compare core memories to the rest of
the memory store or to live GitHub state. "In sync" therefore means "the two
copies agree," not "the guidance is true now."

### How does sync actually happen?

The mechanism exists and is implemented, but it is **manual and one-way**:

1. A human/agent edits a source memory in the unversioned memory directory.
2. [`scripts/sync-memory-skill.rs`](https://github.com/rrnewton/dev-hermit/blob/main/scripts/sync-memory-skill.rs)
   strips memory-only metadata/markers/comments, normalizes the body, and writes
   the mapped flat skill. `--adopt-skill`, `--promote`, and `--demote` change
   membership; `--check` is dry-run.
3. [`scripts/lint-memory-skill-sync.rs`](https://github.com/rrnewton/dev-hermit/blob/main/scripts/lint-memory-skill-sync.rs)
   verifies flat paths, slug/frontmatter/body equality, marker presence,
   uniqueness, missing/orphan mappings, and nested directories.
4. The generated skill must then be committed and pushed normally.

There is **no automatic sync**. Repository search found no caller outside the
two scripts and their documentation: no Git hook, CI workflow, ORC startup
hook, watcher, or pre-push gate. The tracked
[`hermit-dev` ORC plugin](https://github.com/rrnewton/dev-hermit/blob/main/.orc/plugins/hermit-dev/index.ts)
loads `AGENTS.md` and its two adjacent narrow skills directly; it does not read
the memory directory or call either sync script. Calling this an "ORC-memory
sync" overstates the integration: it is a manual file mirror from the Claude
project memory directory into coordinator skills.

### Symlink-shared, duplicated, and diverged

`ls -l`, `readlink -f`, inode checks, and byte comparisons show:

- **Cross-layer symlink-shared skills: none.** No file under parent, Hermit, or
  Reverie `.claude/skills` is a symlink to another layer.
- **Discovery aliases only:** parent
  [`.llms/skills`](https://github.com/rrnewton/dev-hermit/blob/main/.llms/skills)
  points to its own `.claude/skills`; Hermit
  [`.llms/skills`](https://github.com/rrnewton/hermit/blob/main/.llms/skills)
  and [`.agents/skills`](https://github.com/rrnewton/hermit/blob/main/.agents/skills)
  point to Hermit's own `.claude/skills`; Reverie
  [`.llms/skills`](https://github.com/rrnewton/reverie/blob/main/.llms/skills)
  and [`.agents/skills`](https://github.com/rrnewton/reverie/blob/main/.agents/skills)
  point to Reverie's own `.claude/skills`.
- **Byte-identical but duplicated regular files:** parent/Hermit
  [progress-rubric](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/progress-rubric.md)
  and Hermit/Reverie
  [repo-cleanliness](https://github.com/rrnewton/hermit/blob/main/.claude/skills/repo-cleanliness.md).
- **Diverged same-concept parent/Hermit files:**
  [backend-reality-reviewer](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/backend-reality-reviewer.md),
  [benchmarking/benchmark](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/benchmarking.md),
  [post-facto-review](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/post-facto-review.md),
  [hermit-ci](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-ci.md),
  [hermit-coord](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-coord.md),
  [hermit-dbi](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-dbi.md),
  [hermit-kvm](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-kvm.md),
  [hermit-lander](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-lander.md),
  [hermit-liteinst](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-liteinst.md),
  [hermit-opt](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-opt.md), and
  [hermit-sabre](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/hermit-sabre.md).

The owner's recollection that selected Hermit skills were shared into the
coordinator layer by symlink is not reflected in the current filesystem. The
closest case is the byte-identical progress rubric, but it is two independent
regular files.

## Recommended changes

1. **Fix landing truth first.** Replace
   [the landing-mechanics skill](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/pr-landing-mechanics-merge-gate-uptodate-chase.md)
   with a short current ruleset/merge-queue procedure generated from verified
   GitHub configuration. Remove `--admin escape`, `main unprotected`, and old
   squash-sweep claims. Reconcile the CI/lander/counter skills in the same PR.

2. **Make one landing policy canonical.** Keep label/review/PR-body rules in
   [parent post-facto policy](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/post-facto-review.md)
   or, preferably, only in
   [canonical `AGENTS.md`](https://github.com/rrnewton/dev-hermit/blob/main/AGENTS.md).
   Role charters should link to it and stop copying paragraphs. Remove or
   product-scope Hermit's coordinator/lander/role copies, especially the files
   that mention obsolete labels.

3. **Demote dated incidents from active trigger space.** Move
   [CI-capacity](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/ci-capacity-single-pmu-runner-bottleneck.md),
   [self-hosted SIGSEGV](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/self-hosted-ci-sigsegv-blocks-all-prs.md),
   [good binary](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/good-hermit-binary-for-tests.md),
   and other host/date-specific snapshots back to ordinary memories or a dated
   reference directory. Keep one current CI-health skill that links historical
   incidents only when their signature matches.

4. **Repair descriptions as trigger contracts.** Every active description
   should say "Use when..." with positive triggers and exclusions. Correct the
   false runner/symlink/admin claims. Narrow
   [fabler](https://github.com/rrnewton/hermit/blob/main/.claude/skills/fabler/SKILL.md)
   and [ux-tester](https://github.com/rrnewton/hermit/blob/main/.claude/skills/ux-tester.md),
   and explicitly order the benchmark and debugging skill families.

5. **Automate the existing memory check.** Add
   [`scripts/sync-memory-skill.rs --check`](https://github.com/rrnewton/dev-hermit/blob/main/scripts/sync-memory-skill.rs)
   and [the linter](https://github.com/rrnewton/dev-hermit/blob/main/scripts/lint-memory-skill-sync.rs)
   to a repository validation job. Add a policy/version/freshness check against
   `AGENTS.md`, link validation, and a small contradiction denylist for merge
   mode, labels, authoritative gates, and worktree layout. Document plainly
   that the source memory is local/unversioned and the committed skill is the
   reviewable artifact.

6. **Do not use cross-submodule symlinks as the sharing mechanism.** They break
   standalone product checkouts and do not produce browsable nested GitHub
   blobs. For genuinely shared content, keep one versioned canonical source and
   generate checked-in product copies with an equality test. Otherwise assign
   each skill to exactly one layer. Fix or delete
   [the false symlink skill](https://github.com/rrnewton/dev-hermit/blob/main/.claude/skills/progress-reports-location-and-skill-symlink.md)
   and align the report path in both rubric copies.

7. **Add the missing workflow indexes.** Create a normal PR-planning skill that
   emits PR stack, ownership, dependency, validation, review-trigger, and
   landing-order fields. Create a current CI-health index that distinguishes
   live gate facts from incident references and points to
   [ci-debugging](https://github.com/rrnewton/hermit/blob/main/.claude/skills/ci-debugging.md)
   for the inner loop.

8. **Cover LiteInst2 or explicitly declare it skill-less.** Add a small
   LiteInst2 product skill for build/test/probe-safety/repo hygiene, exposed
   through the same per-repository discovery aliases, or document that all
   implementation policy intentionally lives elsewhere.

9. **Single-source ORC trigger metadata.** Put name/description/trigger intent
   beside the two ORC Markdown skills and generate or validate the
   [TypeScript registration](https://github.com/rrnewton/dev-hermit/blob/main/.orc/plugins/hermit-dev/index.ts)
   so instruction edits cannot silently diverge from activation behavior.

## Verification evidence

- `ls -l` and `readlink -f` on every `.llms/skills` / `.agents/skills` alias.
- `find` inventory of all active Markdown files in the three real skill
  directories; LiteInst2 paths checked and absent.
- `cmp` and inode checks for all same-name/same-concept cross-layer files.
- Memory linter: 37 active, 37 mapped, 37 in sync, 0 problems.
- Sync dry-run: all 37 mapped skills would be kept/refreshed.
- Repository search for sync/lint callers: none outside scripts/docs.
- ORC registration and startup path read from
  [`.orc/plugins/hermit-dev/index.ts`](https://github.com/rrnewton/dev-hermit/blob/main/.orc/plugins/hermit-dev/index.ts).
- GitHub mobile links checked through `with-proxy`; all report links resolve.
  Direct product-repository file links are required because nested dev-hermit
  submodule file and entry URLs return 404.
