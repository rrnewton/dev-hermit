# Manual CI Mode — lander/planner-driven targeted CI + coalesced local-validate landing

**Owner directive (2026-08-03):** "the lander/planner decides which batches are
next and does targeted CI with low wait time. And agents batching/coalescing PRs
for local-validate + land should supplement that."

**Principle:** stop broadcasting CI at every PR and hoping the queue sorts itself
out. A lander-chosen batch gets targeted CI; everything else waits. Fewer runs,
each one wanted. **The default landing path in this mode does NOT touch the hosted
queue at all — it lands on local-validate-green via the merge-gate either/or leg.**

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

## 4. Landing path A — LOCAL-VALIDATE (PREFERRED; bypasses hosted queue)

Per `hermit/docs/MERGE_QUEUE.md`: a fully green `./validate.sh` on the exact PR head
auto-creates and applies `locally-validated`; `merge-gate` passes on that label
without any ci-portable run. Label is stripped on head change.

Per chosen PR, in a lander-owned slot (never a primary checkout):

```bash
# 1. CONCURRENCY GATE FIRST (see §6) — abort if at limit or box not SUITABLE.
# 2. Fresh checkout of the exact PR head:
with-proxy git -C <slot>/hermit fetch origin main
with-proxy git -C <slot>/hermit fetch origin pull/<n>/head
git -C <slot>/hermit switch --detach FETCH_HEAD    # exact PR head
# 3. Full portable validate (default profile — NOT --quick). Auto-labels on green:
( cd <slot>/hermit && PR_NUMBER=<n> ./validate.sh )
# 4. On green (label applied), land via merge queue:
with-proxy gh pr merge <n> --repo rrnewton/hermit --auto --merge
```

If validate is RED it is a real failure — do not label, do not land; report it.
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

`--rebase`/merge-queue, **NEVER `--admin`** for autonomous work. Fetch fresh before
every land. **Ancestry-verify** after merge — an API `MERGED` can be orphaned;
confirm the merge commit is reachable from `origin/main`. Merge only when the task
authorizes landing and required adversarial review is resolved.
