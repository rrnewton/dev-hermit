# Manual CI Mode — lander/planner-driven targeted CI + coalesced local-validate landing

**Owner directive (2026-08-03):** "the lander/planner decides which batches are
next and does targeted CI with low wait time. And agents batching/coalescing PRs
for local-validate + land should supplement that."

**Principle:** stop broadcasting CI at every PR and hoping the queue sorts itself
out. A lander-chosen batch gets targeted CI; everything else waits. Fewer runs,
each one wanted. A local run may mint exact-head evidence, but this skill does
not itself authorize or execute a merge. Until the coordinated Hermit merge-gate
bundle is deployed, the current `AGENTS.md` hosted-gate rule remains binding.

The lander owns this mode. It is a runbook to execute under pressure, not a design
doc. Use `with-proxy` for all networked `git`/`gh`.

---

## 1. Switch IN — trigger (key off the TAIL; hosted start-latency is BIMODAL)

Most hosted jobs start instantly; a tail waits ~2h (baseline n=41: median 0s, **p90
7,233s**, max 7,739s). A median trigger would never fire — key off the tail.

Check with `ci-hub runner-health` (prints, per workflow, `queued=N ... queue age
median X max Y`). **Enter manual mode when EITHER:**

- any GitHub-managed workflow shows **queued ≥ 5**, OR
- any GitHub-managed workflow **max queue age ≥ 60m**.

(Baseline at declaration: `CI (GitHub-managed portable)` queued=6, max 2h03m →
fires. Self-hosted runners were idle 3/4 — the starvation is hosted-concurrency,
not runner capacity, so local-validate on the idle box is the escape hatch.)

## 2. Switch OUT — trigger

Return to normal (auto-armed) CI when the hosted tail has recovered, confirmed on
**two consecutive** `ci-hub runner-health` checks ≥ 15 min apart:

- **every** GitHub-managed workflow `queued ≤ 1`, AND
- **every** GitHub-managed workflow `max queue age < 15m`.

## 3. Batch selection — the lander decides (NEAR-GREEN FIRST)

A PR is **near-green** and batch-eligible only when:

- all `test:*` product checks are **pass** (`gh pr checks <n> -R rrnewton/hermit`),
- its only non-pass checks are the **`merge-gate` aggregator** or **queue-starved
  trailing jobs** (e.g. "Delete prebuilt build artifacts", "Reduce e2e parity
  archives"), and
- **required adversarial review is resolved** (`passed-review-*` labels present,
  no unresolved BLOCK). Green CI does not substitute for review. A near-green PR
  with **no review labels at all** (e.g. #1525, #1542 at declaration) is NOT
  batch-eligible — dispatch the review axis first (review is capacity-independent;
  it needs no hosted queue).

A **real** failing check disqualifies until fixed — e.g. #1495's `Reverie pin is
current` is a genuine stale-pin failure, not gate mechanics; do not batch it, and
local-validate will (correctly) also reject it.

Order the batch: near-green first, then oldest-mergeable. `mergeStateStatus` must be
`MERGEABLE` (not divergence-`DIRTY`). Announce the chosen batch in a task note
before dispatching.

## 4. Evidence path A — LOCAL-VALIDATE (preferred measurement path)

Per `hermit/docs/MERGE_QUEUE.md`: a fully green `./validate.sh` on the exact PR head
produces a counted receipt. After every completed run, invoke
`ci-hub apply-local-label`: PASS publishes a content-addressed receipt/log and
outcome snapshot; every non-PASS publishes a typed deny/no-result outcome.
The merge gate resolves the canonical outcome branch tip, unions every
exact-head snapshot (so any genuine failure wins monotonically), and recomputes
the selected pass from its log. `locally-validated` is only a cache hint.

Per chosen PR, in a lander-owned slot (never a primary checkout):

```bash
# 1. CONCURRENCY GATE FIRST (see §6) — abort if at limit or box not SUITABLE.
# 2. Fresh checkout of the exact PR head:
with-proxy git -C <slot>/hermit fetch origin main
with-proxy git -C <slot>/hermit fetch origin pull/<n>/head
git -C <slot>/hermit switch --detach FETCH_HEAD    # exact PR head
# 3. Full portable validate (default profile — NOT --quick). Auto-labels on green:
( cd <slot>/hermit && PR_NUMBER=<n> ./validate.sh )
# 4. Publish the typed outcome even on red/finalizer refusal; publication failure
#    is NO_RESULT. On green, hand the exact outcome receipt to the gate.
#    Do not invoke a raw merge command from this runbook.
```

If validate is RED, publish the typed outcome, do not label or land, and report it.
`./validate.sh --no-label-pr` when a green run must not touch GitHub.

## 5. Landing path B — TARGETED hosted CI (only when a hosted-only signal is needed)

Local-validate covers portable checks. Use targeted hosted CI only when the batch
genuinely needs a **self-hosted-only** signal (PMU / `/dev/kvm` / privileged).
Re-run ONLY the chosen PR's run — never auto-arm the fleet:

```bash
with-proxy gh pr checks <n> -R rrnewton/hermit          # find the run id
with-proxy gh run rerun <run-id> -R rrnewton/hermit     # single PR only
```

Do not push no-op commits to trigger CI across PRs; that is the fan-out this mode exists to stop.

## 6. Concurrent-local-validate LIMIT (HARD — owner constraint)

Local validate is expensive and concurrent runs contend.

- **LIMIT = 2 concurrent local-validate runs**, fleet-wide.
- **Additionally, dispatch only when `ci-hub load-probe` reports SUITABLE.**

Before dispatching, check who is already running one:

```bash
ci-hub load-probe                 # must be SUITABLE
ci-hub validate-worktrees --json  # count non-terminal runs (fresh last_seen, no pass/fail result)
```

`validate.sh` auto-registers every run into `ignored/ci-hub/worktree-registry.json`;
`validate-worktrees` is the single checkable source. If 2 are already live, or the
box is BUSY, **queue the PR — do not dispatch a third.** Record dispatch/completion
in the task note so concurrent landers see the count.

## 7. Landing discipline (unchanged, always)

This skill stops at evidence production. The legacy
`ci-hub/landing/land-pr.sh` path is fail-closed and is not a fallback; a raw
`gh pr merge` is not a replacement authority. Load `hermit-lander` for the
current execution disposition. Merge only when the task authorizes landing,
required adversarial review is resolved, and the live repository gate has
dereferenced its canonical exact-head authorities. Fetch and ancestry-verify
after any authorized merge.
