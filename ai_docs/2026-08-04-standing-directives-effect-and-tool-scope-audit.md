# Standing Directives: Effect And Tool-Scope Audit

Date: 2026-08-04

## Executive Result

This report separates two questions that had previously been conflated:

1. Did an implementation reach the named main branch?
2. Does the resulting mechanism cover what it claims to verify or enforce?

Publication state, denominator 10:

| State | Count |
| --- | ---: |
| LANDED | 6/10 |
| IN-FLIGHT | 3/10 |
| STRANDED | 0/10 |
| NOT-STARTED | 1/10 |
| UNVERIFIABLE (`rc=2`, outside the four states) | 0/10 |

Effective mechanism coverage:

| Coverage | Count |
| --- | ---: |
| MECHANISM-COMPLETE | 3/10 |
| MECHANISM-PARTIAL | 6/10 |
| No mechanism yet | 1/10 |

Therefore 7/10 directives are not fully in effect. Their actionable
dispositions are:

| Disposition | Count | Directives |
| --- | ---: | --- |
| IN-FLIGHT | 3 | Stateful IRQ-aware core allocator; load-immune timeout management; merge-gate definition authority |
| NOT-STARTED remainder | 3 | Automatic green-time log production; Hermit consumption of the `--cgroups` removal; mechanical agent-utils PR serialization |
| CORRECTLY GATED | 1 | DBI-to-DBT rename/install factoring |
| STRANDED | 0 | None |

## Snapshot And Method

Freshly fetched branches used for the final snapshot:

| Repository | Named target | SHA |
| --- | --- | --- |
| `rrnewton/dev-hermit` | `origin/main` | `781a07a752b1ae8773f5fee33bdba0c0ad90cb9f` |
| `rrnewton/hermit` | `origin/main` | `397fc8463b208e51445e52089de35a8c0efd22d8` |
| `rrnewton/reverie` | `origin/main` | `6adcc98d75657af4c8b6b6e3b592f26d05e34003` |
| `rrnewton/agent-utils` | `origin/main` | `60403dddb145a88784c14004220c721930fd87c5` |

Landing verification rules:

- Direct pushes use the full claimed SHA against the freshly fetched, named
  target branch.
- Pull requests use GitHub `mergeCommit.oid`, not the pre-merge PR head. The
  replay/squash SHA must be an ancestor of the freshly fetched target.
- GitHub `MERGED` alone is insufficient because later history replacement can
  orphan a previously merged commit.
- `rc=2` means UNVERIFIABLE and is reported separately. It is never folded
  into NOT-STARTED, NOT-LANDED, or STRANDED.
- An open PR is positively IN-FLIGHT; the absence of `mergeCommit.oid` on an
  open PR is expected and is not an unverifiable landing claim.

Tool-scope verification rules:

- Trace producers, consumers, and enforcement call paths.
- Derive growing file/member sets rather than trusting a hardcoded list.
- Plant a violation outside the previously observed happy path and require the
  tool to fail.
- A LANDED artifact may still be MECHANISM-PARTIAL.

## Directive Census

| Directive | Publication state | Landing evidence | Effective coverage | Action |
| --- | --- | --- | --- | --- |
| Green-time percentage metric and log | LANDED | Parent `469f439`, `a5abff7`, both on `origin/main` | PARTIAL | Automatic recurring log producer is NOT-STARTED |
| Newest green main | LANDED | Parent `0c166eec`, on `origin/main` | COMPLETE | None |
| Remove deprecated `--cgroups` | LANDED artifact | Agent-utils `dfefbdb8`, on `origin/main`; parent pins it | PARTIAL | Hermit consumer pin/docs remainder is NOT-STARTED |
| Agent-utils changes go directly to main | LANDED | Agent-utils `60403dd`, direct push, zero associated PRs, exact-SHA CI green | PARTIAL | Direct path works; mechanical at-most-one-PR enforcement is NOT-STARTED |
| Rebase-aware landing verifier | LANDED | Dev-hermit PR #31 `mergeCommit.oid=b8d8d647`; follow-up `4f018407`, both on `origin/main` | COMPLETE | None |
| Reverie pin checker covers tracked lockfiles | LANDED | Hermit PR #1581 `mergeCommit.oid=397fc846`, verified `rc=0` on `origin/main` | COMPLETE for tracked Cargo metadata | None |
| Merge-gate definition authority | IN-FLIGHT | Hermit #1578 head `fe1a03f7`; #1579 head `4beaedf9`; neither is on main | PARTIAL | Land evidence binding first, then rebase and revalidate the versioned gate |
| Stateful IRQ-aware core allocator | IN-FLIGHT | Branch `22a401fe` and agent-utils PR #15 head `1c7c8556`, neither on main | PARTIAL candidates | Finish composed implementation; do not leave in PR backlog |
| Load-immune CI timeout management | IN-FLIGHT | Several derivation/evidence commits landed; core fallback command absent | PARTIAL | Finish queue cancel, bounded admission, local validate, and evidence path |
| DBI-to-DBT rename/install factoring | NOT-STARTED | No implementation artifact | None | CORRECTLY GATED on fewer than 10 Hermit PRs; current count 75 |

## Per-Directive Findings

### Green-Time Percentage Metric

Publication is LANDED. Parent commits `469f439` and `a5abff7` are ancestors of
fresh `origin/main`. The live query returned a four-state, explicitly scoped
result for `rrnewton/hermit`; at the final snapshot the portable-workflow
green percentage was 0.78%, and the current state was `no_result` rather than
fabricated green.

Coverage is PARTIAL. `ci-hub/history/query.py` contains the append-log
capability, but no repository workflow, timer integration, status-log path, or
root `ci-hub` front door automatically invokes it. The owner asked for a metric
logged over time. Measurement and manual append exist; the recurring producer
is NOT-STARTED.

### Newest Green Main

Publication is LANDED at parent `0c166eec`. The live command completed with
`rc=0` and reported:

- latest full/full green SHA `e8a0d8d3`;
- current branch tip `f2f925dc` at the time of the control;
- 25 commits after the green point;
- 23 commits with no validation record.

This is MECHANISM-COMPLETE for its stated local-ledger scope. It names the
profile and selection strength, reports evidence gaps, and freshness-checks the
branch tip even on a cache hit.

### Deprecated `--cgroups` Removal

The implementation artifact is LANDED directly on agent-utils main at
`dfefbdb8`; current parent main pins exactly that commit. Both agent-utils
engines now hard-error when the retired flag is supplied.

The directive remains PARTIAL in its primary consumer. Hermit main pins older
agent-utils `ec4ddf07`; `dfefbdb8` is not an ancestor of that pin. The pinned
Python and Rust parsers still accept `--cgroups` as a deprecated no-op. Hermit
also retains stale instructions at:

- `ci/run-dag.sh:19`
- `ci/dag/README.md:170`

The remaining Hermit pin and documentation update has no implementation
artifact, so that remainder is NOT-STARTED. Agent-utils PR #9 was closed as
superseded by the direct-main commit and is not landing evidence.

This is the canonical quoted-but-not-tracked failure: the requested removal was
repeated accurately as a fact, but repetition created neither an owner nor a
wakeup. The later direct-main commit fixed agent-utils; it did not make the
cross-repository directive complete.

### Agent-Utils Direct-To-Main Policy

The path is now observed, not merely documented. Repository-local
`agent-utils/AGENTS.md` landed directly on agent-utils main at
`60403dddb145a88784c14004220c721930fd87c5`. A fresh fetch returned ancestry
`rc=0`, and the GitHub commit-to-PR query returned an empty list: the change
landed without a PR.

The exact-SHA main workflow, run `30904083970`, completed green in 2m10s. It ran
the embedded-doc check, rustfmt, both builds, repository checks, Python and Rust
tests, differential mypy, and the Python/Rust behavioral differential. The
local pre-push contract also passed: 266 Python tests, 68 Rust tests including
boxing and CPU-time smokes, and 378 differential checks across 41 fixtures.

The repository-local policy is visible before an agent publishes work. It
requires serialized writers, a fresh fetch, the full test contract, an explicit
fast-forward `HEAD:refs/heads/main` refspec, and post-push ancestry. It limits
PR exceptions to genuinely high-risk pre-main review or atomic coordination
with an in-flight consumer.

The Hermit boundary is explicit and independently enforced. Agent-utils
ruleset `20313492` permits direct fast-forward pushes while forbidding deletion,
non-fast-forward updates, and nonlinear history. Hermit ruleset `20244443`
requires a pull request plus `merge-gate`; ruleset `20307165` forbids history
rewrites. Direct-to-main applies to agent-utils and parent-only dev-hermit
tooling, never to Hermit product changes.

Coverage is still PARTIAL because publication serialization remains discipline,
not mechanism. GitHub permits agent-utils PRs, and after closing stale,
superseded PR #9, three distinct draft PRs remain: #3, #8, and #15. They contain
live functionality and were not discarded. No check currently enforces the
policy's at-most-one exceptional PR rule or requires a reason for the exception.
That enforcement remainder is NOT-STARTED.

### Rebase-Aware Landing Verifier

Publication is LANDED. Dev-hermit PR #31 has
`mergeCommit.oid=b8d8d647cd00cff3fca72a32517bfe4afe84a8a7`, which is ancestral to
fresh parent main. Follow-up `4f018407` is also ancestral.

The mechanism is COMPLETE for its stated PR/full-SHA inputs:

- live rebase-merged Hermit #1219 resolved replay SHA `0f891e43` and returned
  LANDED `rc=0`, even though its pre-rebase head is not ancestral;
- live open Hermit #1365 returned UNVERIFIABLE `rc=2` because it has no
  `mergeCommit.oid`;
- the tool freshly fetches the named target before testing ancestry.

The prior PR-head ancestry primitive is invalid and is not used by this report.

### Reverie Pin Checker And Tracked Lockfiles

This changed state while the report was being prepared. Hermit PR #1581 moved
from open to merged. The corrected verifier resolved
`mergeCommit.oid=397fc8463b208e51445e52089de35a8c0efd22d8` and returned LANDED
`rc=0` against fresh Hermit `origin/main`.

Current main derives every tracked `Cargo.toml` and `Cargo.lock` with
`git ls-files`. The checker includes a negative regression in which a tracked
`runtime/Cargo.lock` retains a stale Reverie revision and the checker exits 1.
The observed main source contains both lockfile extraction and the fail-closed
assertion.

The mechanism is COMPLETE for its stated scope: tracked Cargo dependency
metadata, including tracked vendored paths. Its explicit exclusions are
non-Cargo tracked files, generated/untracked files, and nested submodule
contents. A full/short-pin search performed during the implementation found no
live Reverie pin outside the covered Cargo metadata set.

### Merge-Gate Definition Authority

Publication is IN-FLIGHT. Current Hermit main still accepts a branch-local
`workflow_dispatch` job named `merge-gate`, and ruleset `20244443` consumes that
name without authenticating the workflow definition. Existing run
`30868091777` proves the live failure: open PR #1547's stale portable-only YAML
emitted `merge-gate/success` at its exact head even though current main requires
portable plus privileged CI.

Fresh inspection of all 75 open PR heads found 57 with weaker portable-only
gate definitions. Only 56 predate `bfb0a9ef` by ancestry; PR #1543 contains that
commit but retained the stale gate blob, so ancestry is not a complete scope
check. Current main also accepts bare `locally-validated` label presence without
ledger or durable exact-head evidence.

PR #1578 implements evidence binding and has passed planted negative and
positive controls, but remains open. PR #1579 implements a versioned context
and registered blob, but its current head predates #1578 integration and its
guard remains branch-owned YAML. The practical sequence is #1578 first, then a
fresh #1579 rebase and repeat validation. The stronger complete architecture is
a trusted main-defined producer, such as a GitHub App/controller that creates a
check on the PR head. GitHub native required workflows are unavailable for this
user-owned repository; the live configuration attempt returned HTTP 422.

The mechanism is PARTIAL both on main and in the current candidate. Full
evidence, exposure counts, and tradeoffs are recorded in
`ai_docs/2026-08-04-merge-gate-branch-yaml-authority-audit.md`.

### Stateful IRQ-Aware Core Allocator

Publication is IN-FLIGHT. Neither the old stateful branch `22a401fe` nor
agent-utils PR #15 head `1c7c8556` is ancestral to agent-utils main.

No complete candidate exists yet:

- PR #15 provides per-run cpuset/affinity but no cross-launch lease registry or
  IRQ-aware allocation.
- `22a401fe` provides flocked leases and dead-holder reclaim, but its cumulative
  `/proc/interrupts` threshold can classify every core as contaminated.
- the required replacement must use actual `/proc/irq/*/smp_affinity_list`
  target sets and preserve cross-engine parity.

The work is actively owned, so it is IN-FLIGHT rather than STRANDED. Because
agent-utils tooling is direct-to-main and single-threaded by owner policy, the
open PR/branch must not become a second backlog.

### Load-Immune CI Timeout Management

Publication is IN-FLIGHT. Parent commits `66b373f`, `136043b`, `f4b86d4`,
`a98209d`, and `48b0ec4` are ancestral and provide kill-immune CPU-budget
derivation, a stated hosted multiplier, busy-box evidence, and bounded
exact-head gate polling.

The requested end-to-end behavior is absent. Parent main has no `ci-timeout`
command, `ci-local-fallback` marker, cancellation-reason record, or bounded
local-validate admission queue. Hermit autoretry guard `ad3803fd` is not
ancestral to Hermit main. The actual owner directive remains:

1. detect a job queued longer than the threshold without conflating runtime;
2. prevent cancellation from triggering an autoretry loop;
3. cancel with an explicit reason;
4. admit a bounded local full validate;
5. bind the result to the exact head and durable evidence.

Partial components are landed, but the mechanism as claimed is not in effect.

### DBI-To-DBT Rename And Install Factoring

Publication is NOT-STARTED and this is intentional. No implementation artifact
exists. The owner gate is fewer than 10 open Hermit PRs; the measured final
count is 75. This item is CORRECTLY GATED, not abandoned or stranded.

## Route Policy Finding

Owner tooling changes are expected to land directly on the appropriate main
after their repository tests pass. A tool sitting in a routine PR queue is not
in effect and has the same operational result as an unimplemented request.

Agent-utils `60403dd` is the positive control: one validated commit, pushed
straight to main, ancestry-confirmed, with exact-SHA main CI green and no PR.
It proves the route is available. The three remaining draft PRs prove the
single-thread/exception policy is not yet mechanically enforced.

Some changes in this audit predate that explicit rule and landed through PRs.
They are classified by the resulting merge commit, not retroactively called
violations. Current agent-utils work must follow the direct-main,
single-threaded policy rather than growing a parallel PR backlog.

## Final Answer

The original question, "did it land?", is insufficient. Six of ten
directives have landed implementation artifacts, but only three of ten have
complete effective mechanisms. The remaining seven are actionable without
guessing:

- three actively IN-FLIGHT;
- three missing-scope remainders NOT-STARTED;
- one CORRECTLY GATED;
- zero STRANDED;
- zero UNVERIFIABLE landing claims.

The repeatable sweep must preserve both ledgers. Otherwise a landed but
scope-incomplete verifier will be reported green and reproduce the exact
failure this audit is intended to catch.
