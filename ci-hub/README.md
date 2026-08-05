# dev-hermit CI hub

`ci-hub/` is the single versioned home for dev-hermit CI operations and CI
knowledge. Treat each subdirectory as an object with one responsibility and a
stable public entrypoint; do not add new CI scripts under `scripts/`, `ops/`, or
an experiment directory.

## Public entrypoints

Every fenced shell invocation in this README and `landing/README.md` is extracted
and exercised by the docs-as-tests check. Read-only commands execute; mutating
commands must pass their exact argument parser without performing the action.
`--help` is a purity contract: it must not write, touch tracked-file mtimes,
perform network access, or import heavy optional dependencies. The core workflows
are:

```bash
# Print the command list without network or filesystem writes.
./ci-hub/ci-hub help

# Read the opinionated 5-step agent workflow without touching local state.
./ci-hub/ci-hub quickstart

# Pull fresh open-PR CI state from GitHub and classify it.
./ci-hub/ci-hub fresh

# Summarize current-main plus open-PR health.
./ci-hub/ci-hub health

# Parse the canonical landing command without mutating a PR. Real lands use the
# same entrypoint without CI_HUB_DOCS_PARSE_ONLY; it verifies the final pushed
# head's immutable validation receipt before a head-matched rebase merge.
CI_HUB_DOCS_PARSE_ONLY=1 ./ci-hub/landing/land-pr.sh \
  123 example/feature-branch --foreground

# Inspect or recover polling for obligations that are still open.
./ci-hub/ci-hub obligations
./ci-hub/ci-hub obligations --actionable
./ci-hub/ci-hub inherit-obligations --agent hermit-lander --session "$HOSTNAME:$$"
./ci-hub/ci-hub watch-obligations --once

# Incrementally refresh the local GitHub/local-run history store.
./ci-hub/ci-hub refresh-history

# Query the local commit/CI history store.
./ci-hub/ci-hub history

# Inspect local validate-run history, retained profiles, or runner health.
./ci-hub/ci-hub local-history --since 2026-08-03
./ci-hub/ci-hub validate-worktrees --runs 10
./ci-hub/ci-hub runner-health --all
```

Networked commands use `with-proxy` internally.

Detached validations outlive their launching agents. Stop the unit, not the
watcher: `./ci-hub/ci-hub validate-stop --unit validate-NAME.service` targets one
run, while `./ci-hub/ci-hub validate-stop --all` enumerates every active
`validate-*` user service or scope. The command refuses unrelated unit names,
uses `systemctl --user stop` on each exact unit, and fails if any unit remains
active. A signal-aware `validate.sh` records that operator stop as `no_result`,
never as a product failure.

## Standing receipt reconciliation

A validate receipt is keyed to an exact commit SHA, so every rebase/push/mark-ready
orphans the receipt earned at the old head. Nothing else sweeps for the receipts
that are STILL valid, so earned ~500s evidence sits unread while the drain queues
fresh runs. `reconcile-receipts` is the standing join — run it after every drain
rebase wave:

```bash
./ci-hub/bin/reconcile-receipts          # human table
./ci-hub/bin/reconcile-receipts --json   # machine-readable
```

It joins every distinct clean-full receipt commit (enumerated from
`local-history`) against FRESHLY-FETCHED open-PR heads and classifies each,
always WITH A DENOMINATOR ("valid 1 of 59"):

- `VALID` — head matches, authoritatively `is_clean_full_pass` (via
  `validate-status`, NOT the looser `local-history` prefilter), and clears every
  rebase-base floor. Landable NOW: `apply-local-label` + merge, no new validate.
- `FLOOR-BLOCKED` — matched + certified but the head predates a merge-gate or
  producer floor; it validates green yet landing is refused. Lever is REBASE.
- `NOT-CERTIFIED` — matched an open head but the authoritative certifier refuses
  (a `local-history checks==5` match is not landability proof).
- `ORPHANED` — no current open-PR head equals this commit; the head moved or the
  PR landed. `orphaned/total` is the measured cost of push-rewrites-the-head.

**COORDINATOR post-wave step (standing, once per rebase wave).** There is no
per-wave code hook — a rebase wave is a coordinator-level batch, not a single
script's loop, and the natural home (`rebase_wrapper.py`) owns an overlapping
reconcile/`eligible` mechanism. So the coordinator runs this as the FIRST landing
step after every rebase wave, BEFORE queueing any new ~500s validate:

```bash
./ci-hub/bin/reconcile-receipts            # 1. sweep the moved frontier
# 2. for each VALID row: apply-local-label from the receipt, then land it
#    (ci-hub apply-local-label --pr <N> ; ci-hub/landing/land-pr.sh ...).
# 3. for each FLOOR-BLOCKED row: it needs REBASE, not a new validate.
```

A wave rewrites heads — some receipts revive, some die — so only a re-run after
each wave is current; the query costs seconds against a full run's ~500s. This is
the cheapest work in the drain: consume evidence already earned before spending
new compute.

Landing verification is PR-aware and has machine-stable result codes:

```bash
./ci-hub/ci-hub verify-landing PR --repo OWNER/REPO --source CHECKOUT
./ci-hub/ci-hub verify-landing COMMIT_OID --source CHECKOUT
./ci-hub/ci-hub verify-landing PR --repo OWNER/REPO --source CHECKOUT \
  --item "description" --claimed-oid REPORTED_OID
```

The command freshly fetches the target (default `origin/main`) and prints
`LANDED` with rc 0, `NOT_LANDED` with rc 1, or `UNVERIFIABLE` with rc 2. For a
PR it reads GitHub's `mergeCommit.oid`, the commit created by a rebase merge,
then checks that replay SHA's ancestry. Do not test the pre-merge PR head:
rebase merge rewrites it by design. The API's `MERGED` state is not sufficient
on its own; a non-ancestral replay SHA is `NOT_LANDED`, detecting a later
force-push orphan. A PR without `mergeCommit.oid` is `UNVERIFIABLE`, never an
inferred failure or success. `verify-landed-pr` remains a compatibility alias.

Commit abbreviations are expanded and reported as `full_oid`; never copy an
abbreviated OID into a landing report. Claim-audit mode prints the item, reported
OID, full OID, whether it resolves, whether the PR's `mergeCommit.oid` is present
on the fetched target, and the reported OID's own ancestry rc. Thus a pre-rebase
head can truthfully show `claimed_ancestry_rc=1` while
`change_present_on_main=true`; the landing is identified by the separate full
`merge_commit_oid`.

Task closure is a separate, fail-closed consumer of that verifier:

```bash
./ci-hub/bin/close-task TASK --code PR_OR_FULL_SHA --repo OWNER/REPO --source CHECKOUT
./ci-hub/bin/close-task TASK --artifact ai_docs/path.md
./ci-hub/bin/close-task TASK --run-id GITHUB_RUN_ID --repo OWNER/REPO
```

The gateway records `CLOSURE-VERIFIED` on the task before changing its status.
It closes only a landed code reference, a URL that resolves, a local artifact
tracked on freshly fetched parent `main`, or a GitHub Actions run ID that
resolves. Local artifact evidence is recorded as the typed tuple
`rrnewton/dev-hermit:path@last-content-commit;target=main@tip`; the gateway
verifies that content commit is ancestral to the fetched target. This proves
publication, not that the artifact answers the task's goal, which the
coordinator checks separately. `REFUSED` exits 1 for a reference known not to satisfy the criterion;
`UNVERIFIABLE` exits 2 when no answer can be obtained.
Neither nonzero state calls `tg`. Use `--check-only` to validate evidence without
mutating a live task. The upstream `tg` binary has no project hook, so project
policy requires this gateway and forbids raw terminal-status updates.

## Object map and ownership

| Object | Owns | Does not own |
| --- | --- | --- |
| `bin/` | Stable wrappers and pinned shared-tool materialization. | CI classification, scheduling, or history logic. |
| `directives/` | Versioned owner tooling obligations and fresh target-branch ancestry verdicts. | Implementation, review, or treating quoted instructions as completion. |
| `health/` | Dev-hermit-specific current-main, PR, primary, and agent health adapters plus tick configuration. | Generic cadence or PR-CI classification engines. |
| `history/` | Incremental/idempotent GitHub Actions and local commit/CI knowledge store, ingestion, and queries. | Current-live status presentation. |
| `remediation/` | Mandatory post-land dual verification, exact-SHA local execution, watcher, and remediation recommendation. | CI-history ingestion or automatic source-code reverts. |
| `validate/` | Legacy/local `validate.sh` ledger aggregation and linkage to retained profiles. | The generic DAG runner/profile format. |
| `runners/` | Self-hosted runner image, lifecycle, and host status tooling. | Generic CI scheduling. |
| `landing/` | Shared-file **landing mutex** (`land-lock`) that serializes PR landings touching the shared manifest registries. | The land sequence itself (re-union/push/stamp/merge). |
| `closure/` | Verification and evidence recording required before TaskGraph closure. | Task implementation or coordinator judgement that a goal is complete. |

Runtime data is untracked under `ignored/ci-hub/`; versioned code and schemas
live here. Reproducible experiment producers and their frozen outputs remain
with their experiment, but new reusable CI-history queries belong in
`history/`.

The `history` and `refresh-history` front-door commands fall back to the local
validate-run aggregator until the unified store is present. Once
`history/query.py` and `history/ingest.py` exist, dispatch switches to them
automatically without changing callers.

## Shared agent-utils boundary

The `agent-utils` gitlink is a hard dependency, not a source to copy. The
single `bin/agent-tool` adapter materializes the exact pinned commit and runs:

- `pr-landing-planner`: open-PR CI collection/classification. `health/pr_status.py`
  only combines its JSON for the Hermit and Reverie forks; the retired parent
  classifier is not duplicated here.
- `tick-hub`: cadence, gates, and stable `HEALTH`/`ACTION`/`NOTE`/`ERROR`
  emission. Dev-hermit owns only `health/tick-hub.yaml` and project probes.
- `safe-ci-dag-runner`: validation DAG execution and retained profile
  summaries. `validate/aggregate.py` links local run records to that store; it
  does not reimplement the runner or profile schema.

Before adding code, audit the pinned `agent-utils` APIs. If the capability is
generic, add it there and link/use it here.

## Health meanings

`health` is deliberately fail-loud:

- current-main `red` returns 1; missing/unqueryable data returns 2;
- open-PR health is unhealthy for a real regression or systemic runner outage;
- stale/evaluate-once/flaky reds retain their agent-utils classification and
  are not silently relabeled as product failures;
- pending current-main work is displayed as pending and must not be claimed
  green.
- every unresolved speculative-land obligation makes `health` nonzero. A
  verification failure remains unresolved until an explicit fix-forward or
  revert is recorded with `resolve-obligation`.

The outer ORC workflow calls `bin/health-tick` every five minutes. The pinned
tick engine reads `health/tick-hub.yaml`; dev-hermit probes live beside it. A
detached per-obligation watcher records terminal state continuously, while the
dedicated ORC workflow `hermit-dev-speculative-land-remediation-v1` first
recovers any write-ahead intent interrupted after merge but before arm, then
polls every 15 seconds. A failure records durable remediation state and may send
an advisory wake to the live `hermit-lander` (or coordinator). The wake is
recorded as `sent_unacknowledged`; only a reader's `inherit-obligations` scan
changes it to `acknowledged`. The five-minute tick and every fresh lander's
startup scan recover from a lost wake.

## Speculative-land obligation contract

The canonical `landing/land-pr.sh` entrypoint prepares the speculative-land
obligation before its bounded child can merge; raw `gh pr merge` is not the
protocol. It also dereferences the final pushed head's immutable validation
receipt immediately before a `--match-head-commit` merge. Once GitHub exposes
the merged SHA it completes the prepared intent and calls `arm-land`. If the
wrapper dies in that interval, the restartable ORC workflow recovers the intent
and arms it. The write-ahead intent and initial OPEN event both carry the
versioned repository/workflow policy; an unsupported repository is refused
before merge instead of creating an obligation no verifier can satisfy. Arming
appends that OPEN event to
`ignored/ci-hub/obligations.jsonl`, then concurrently:

1. clones the obligation's registered source repository into
   `ignored/ci-hub/obligations/<id>/hermit`, checks out the exact landed commit
   detached, and runs full `./validate.sh --no-label-pr`;
2. confirms the policy-bound GitHub workflow exists for the same SHA,
   dispatching it only when GitHub `main` still equals that SHA; and
3. launches a detached watcher whose durable log lives beside the local
   validation log.

The supported GitHub bindings are explicit rather than inferred from a global
default:

| Repository | Workflow file | Workflow name |
|---|---|---|
| `rrnewton/hermit` | `ci-portable.yml` | `CI (GitHub-managed portable)` |
| `rrnewton/reverie` | `ci.yml` | `Rust` |

Verification-policy schema v1 freezes this repository/workflow binding only;
it does not snapshot the evaluator truth table or authority composition. A
future change to those semantics therefore requires its own explicit migration
and review rather than silently treating this workflow-policy version as proof.

The event log is append-only and locked. Every transition retains timestamps,
verification scope (`total` here), GitHub run IDs, local log path, and failure
recommendation for the history and green-time consumers. The local verifier is
wrapped by `bin/tool-cost`; its history-derived estimate and atomic actual
wall/CPU JSON are copied into the obligation record. Stable cross-store joins
are `landed_sha → gha-runs.csv:head_sha`, `landed_sha → local-runs.csv:git_sha`,
and `github.run_ids → gha-runs.csv:run_id`.

This is a write-ahead, crash-recoverable protocol, **not an atomic transaction
with GitHub**. A merge can complete before arming; the machine-local intent lets
ORC recover that window when the same workspace returns. Raw merges bypass the
intent, and loss of the workspace/disk loses its machine-local recovery state.

Either exact-SHA green satisfies the obligation immediately; a pending,
running, absent, or cancelled/no-result peer is supplemental and never becomes
an AND gate. A simultaneously known green/red pair is a symmetric
`investigation_required` disagreement and never reaches the remediation
actuator. If an already-started peer later reports red, evaluation reopens the
satisfied record as that same non-actuating disagreement; satisfaction never
waits for it. With no green, an authoritative GitHub red changes the obligation to
`remediation_required` and appends `remediation.state=triggered`: revert is
recommended when the bad land is still the main tip; fix-forward is recommended
after main has advanced. Outstanding work is enumerable from state alone with
`obligations --actionable`. A replacement lander acknowledges inherited work at
startup with `inherit-obligations`; no notification delivery is required. The
obligation remains visible and unhealthy even after acknowledgment, until the
repair lands and is closed with:

```bash
./ci-hub/ci-hub resolve-obligation OBLIGATION_ID --kind fix-forward --ref REPAIR_SHA
# or: --kind revert --ref REVERT_SHA
```

## Why this exists

The [2026-08-03 skills audit](../ai_docs/transient/2026-08-03-skills-audit.md#where-are-the-ci-health-skills)
found CI knowledge fragmented across the coordinator `hermit-ci` charter,
Hermit's debugging workflow, five dated state skills, standalone scripts, and
an ORC-only poll registration. Skills now point here for live commands and
ownership. Skills describe when/how an agent should act; this hub owns the
actual code, current query paths, state schema, and operator entrypoints.
