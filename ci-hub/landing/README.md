# Landing mutex (`ci-hub land-lock`)

A small, deterministic **shared-file landing mutex** that serializes PR landings
which mutate the same shared manifest registries. Discoverable, not folklore.

## Why it exists

Every backend-parity / e2e-manifest PR mutates the **same two** shared files:

- `hermit/tests/e2e/manifests/backend-parity-c.toml`
- `hermit/tests/e2e/manifests/inventory/test-files.json`

When several landers push + merge concurrently, **each land moves `origin/main`
and DIRTYs every other in-flight PR**, so the pack "serializes" the hard way —
by collision-and-retry, burning the single self-hosted `[gate]` runner and
converging slowly (the documented "mass-parallel drain won't self-heal;
SERIALIZE" failure mode). This mutex turns that scrum into an orderly queue:
**exactly one land is in flight at a time.**

## Contract

Every lander MUST hold the lock around its entire land sequence:

```
acquire  ->  re-union onto fresh origin/main  ->  push  ->  stamp
         ->  merge --rebase  ->  ancestry-verify  ->  release
```

Acquire **before** fetching fresh main (so your re-union sees the final state of
the previous land); release **only after** `git merge-base --is-ancestor <sha>
origin/main` confirms your commit actually landed.

## Design (small + deterministic)

- Typed Rust command variants in `ci-hub/ci-hub.rs` own acquire, renew,
  release, status, and run. `landing-lock.sh` is an exec-only compatibility
  path for landers and heartbeats that started before the Rust cutover.
- An advisory **`flock`** on the guard file makes each check-and-set atomic
  across old and new processes.
- The held state is a **lease with an expiry**, not a held fd — so acquire in one
  shell and release in another Just Work, and **(a) a dead holder cannot wedge
  the pack**. Supervised `run` leases record host, boot ID, PID, and process
  start time in a sidecar. A waiter can reclaim immediately when that exact
  process is proven gone; legacy/manual leases remain protected until their
  lease lapses (`--hold` seconds, default 900).
- **(b)** The lockfile records holder **agent + PR + host + timestamps** for
  debuggability; `status` prints them.
- **(c)** Waiters enqueue in a **FIFO**, so ordering is deterministic and each
  waiter sees who is ahead of it; `release` frees the lock immediately and names
  the next agent, which then acquires on its next short (3s) poll rather than
  polling blindly.

Runtime state (all machine-local, gitignored):

| file | role |
| --- | --- |
| `~/work/dev-hermit/.landing-lock`        | holder metadata — the lock |
| `~/work/dev-hermit/.landing-lock.owner`  | supervised owner identity; does not alter the legacy holder format |
| `~/work/dev-hermit/.landing-lock.guard`  | `flock` target (impl detail) |
| `~/work/dev-hermit/.landing-lock.queue`  | FIFO waiter list |

## Usage

```bash
cd ~/work/dev-hermit

# Inspect
ci-hub/ci-hub land-lock status

# Canonical land: detached by default; prints the durable log path and PID
ci-hub/landing/land-pr.sh 1533 codex/my-branch

# Manual acquire / release around your land sequence
ci-hub/ci-hub land-lock acquire --agent hermit-ci --pr 1533   # blocks until yours
#   ... re-union -> push -> stamp -> merge --rebase -> ancestry-verify ...
ci-hub/ci-hub land-lock release --agent hermit-ci

# Crash-safe wrapper (RECOMMENDED): acquire, run, always release, with a
# background heartbeat that renews the lease so a long land keeps the lock, and
# a HARD --child-deadline that kills a wedged land subtree and releases the lock.
ci-hub/ci-hub land-lock run --agent hermit-ci --pr 1533 \
  --child-deadline 2160 -- ./my-land-sequence.sh
```

### Subcommands

| command | purpose |
| --- | --- |
| `acquire --agent NAME --pr N [--wait S] [--hold S]` | block until acquired (FIFO); reclaims a lapsed lease |
| `renew --agent NAME [--hold S]` | heartbeat — extend your lease during a long land |
| `release --agent NAME` | free the lock (owner only); signals the next waiter |
| `status` | print holder metadata, process liveness, seconds left, and the FIFO queue |
| `reclaim-dead` | release only when the supervised owner process is proven gone |
| `run --agent NAME --pr N [--child-deadline S] [...] -- CMD...` | acquire → run CMD (auto-heartbeat, hard child-deadline) → always release |

Defaults: `--wait 1800` (give up after 30 min), `--hold 900` (lease lapses after
15 min so a dead holder self-clears), `--child-deadline 2160` (kill a wedged
land subtree after 36 min). Poll interval 3s.

Exit codes: `0` ok · `1` wait-timeout · `2` usage · `3` not-owner / internal ·
`124` child-deadline breach (land subtree killed, lock released).

### `--child-deadline`: no unbounded wait may hold the queue

`run`'s heartbeat renews the lease **for as long as the child lives** — so a land
script that hangs at a failing/UNKNOWN gate would renew forever and **wedge the
FIFO permanently** (the observed ~2040-minute head-of-line starvation). The fix:
`run` supervises the child against `--child-deadline`; on breach it SIGTERMs the
whole child process group (then SIGKILL after a short grace), **releases the
lock**, prints a loud `ABANDON PR #N` line, and exits `124`. The PR is left open
for retry. A zero deadline is rejected: an unbounded wait is unboxed compute and
every wait here is bounded. `land-pr.sh`'s surviving outer supervisor also posts
a durable PR comment when the killed inner process cannot do so itself.

**Never hand-roll a renewer.** A bare `acquire` plus an external `renewer.sh`
loop that outlives a dead agent defeats the lease-lapse safety net and is exactly
what produced the zombie-held lock. Always land under `land-lock run` (directly,
or via `land-pr.sh`, which self-wraps) so the lease is bound to a bounded child.

### Abnormal termination and evidence-based recovery

`run` removes the lock on every path it can observe: child success, nonzero
exit, launch failure, and hard deadline. SIGKILL and machine loss cannot run
cleanup code, so supervised leases also persist an owner sidecar. `status`
reports `owner_process=alive`, `dead:...`, or `unknown:...`; a proven-dead live
lease is shown as `ORPHANED (reclaimable)`. The next FIFO acquire reclaims it
automatically, or an operator can run `land-lock reclaim-dead`. Reclamation is
refused while the process is alive or its identity cannot be verified. This
preserves the rule that one lander never force-releases another lander's lock.

The holder file format is byte-compatible with pre-sidecar landers. A legacy
bare `acquire` has no process evidence and therefore remains lease-only: it is
never declared dead merely because a PID was not recorded.

## Shared landers (`land-pr.sh`, `union-rebase.sh`)

The land sequence itself lives here too, not only in `scratch/`:

| script | role |
| --- | --- |
| `ci-hub/landing/land-pr.sh <PR> <BRANCH> [--union]` | detached-by-default full single-PR lander: self-wraps in `land-lock run --child-deadline`, requires exact-head ledger evidence before and after rebase, derives `locally-validated` only through `apply-local-label`, polls merge-gate, dereferences the final head's immutable validation receipt, performs a head-matched rebase merge, then verifies ancestry |
| `ci-hub/landing/union-rebase.sh <hermit-wt> <BRANCH> [--push]` | authoritative additive union-rebase of the shared manifest registries (`*.toml` by `[[test]]` id, `test-files.json` by path, `matrix.tsv` by row); the derived `ci/expected-e2e-plan.json` is regenerated, never hand-unioned |

`land-pr.sh` bakes in the three race-tolerance fixes so a transient CI state
never wedges a land:

1. **Race-tolerant exact-head gate poll** — query the Actions workflow runs for
   the rebased head and evaluate the latest `pull_request` or
   `workflow_dispatch` run. The dispatch event matters: the `workflow_run`
   controller runs on `main`, and its dispatched PR-head success can be absent
   from `statusCheckRollup`. Ride through transient
   `FAILURE`/`IN_PROGRESS`/`QUEUED` states to `SUCCESS`; bounded by
   `--gate-deadline` (default 1080s), then ABANDON with the last event, run ID,
   URL, and state.
2. **The merge command is the mergeability arbiter** — do not gate on
   `mergeStateStatus` (it sticks at `UNKNOWN`); attempt `gh pr merge --rebase` in
   a bounded retry loop, which forces GitHub to recompute mergeability.
3. **Treat the label only as a cache** — exact-head ledger evidence is required
   before and after rebase. Only `apply-local-label` may materialize the label;
   it requires a nonzero executed-test count, hashes the referenced log, and
   publishes the selected ledger row as an immutable receipt on
   `rrnewton/dev-hermit:validation-receipts` before commenting or labeling. A
   genuine gate failure is never overwritten by re-stamping metadata.
4. **Dereference the final authorization** — immediately before merge, fetch the
   current PR comments and pass the final pushed head to the parent-pinned
   `ci-hub/validation/verify_receipt.sh`. Missing, forged, stale, tampered,
   zero-executed, or incomplete receipts refuse the landing before any merge
   call. The merge itself uses `--match-head-commit` so a concurrent push cannot
   inherit that authorization.

Every terminal bail emits a visible ABANDON signal — stderr **and** a role-tagged
PR comment — so an abandoned PR never silently languishes (the #244 pattern).
Before taking the lock, the shared lander persists a post-land intent. Only after
the merged SHA is ancestry-confirmed does it arm concurrent exact-SHA local and
GitHub verification; ORC recovery closes the merge-before-arm crash window.

```bash
cd ~/work/dev-hermit
ci-hub/landing/land-pr.sh 1470 codex/backend-parity-contract --union
# DETACHED LAND: pid=... log=.../land-pr1470-<UTC timestamp>-<pid>.log
```

The default launcher uses `nohup` plus a new session and returns immediately,
before lock acquisition. The printed timestamped log is the durable observation
surface across the agent shell's 120-second cap and agent recycling. Use
`--foreground` only for short diagnostics; it does not change any deadline.

### Deadline basis and scope

The gate default is derived from successful `demo-hot-path.yml` pull-request
runs created from `2026-08-03T23:00:00Z` through measurement on 2026-08-04:
`n=11`, min `372s`, median `586s`, p90 `646s`, p95/p99/max `864s` (nearest-rank
percentiles). The `1080s` gate deadline is `ceil(864 * 1.25)`. This is an
operational baseline for the current runner, not a claim that every future gate
finishes within it; refresh the dated sample when the runner or workflow changes.

Every wait in one landing attempt has an explicit scope:

| wait | bound | scope |
| --- | ---: | --- |
| lock acquisition | 1800s | FIFO wait before this landing owns the lock |
| lock lease | 900s, renewed every 300s | dead-holder safety; not a land duration |
| merge-gate | 1080s by default | exact-head Actions runs; caller may override |
| whole child | 2160s by default | entire lock-held subtree; `land-pr.sh` derives twice an overridden gate deadline unless explicitly set |
| merge retry | 12 attempts, 15s sleeps | at most 180s of explicit retry sleep |
| gate poll | 15s interval | included in the gate deadline |
| label / ready settling | 4s each | fixed sleeps before polling |
| termination grace | 5s | SIGTERM-to-SIGKILL interval after child timeout |

Individual `git` and `gh` calls do not have separate per-call timers. They are
inside the whole-child process-group deadline, so a stalled call cannot hold the
landing lock beyond that ceiling. The child deadline must be greater than the
gate deadline; zero is rejected rather than meaning unbounded.

## Verifying your land (before you release)

Release only after the commit is on `origin/main`:

```bash
with-proxy gh pr view PR_NUMBER -R rrnewton/hermit --json state,mergeCommit \
  -q '{state:.state, sha:.mergeCommit.oid}'          # want state=MERGED
with-proxy gh api \
  "repos/rrnewton/hermit/compare/$(with-proxy gh pr view PR_NUMBER -R rrnewton/hermit --json mergeCommit -q .mergeCommit.oid)...main" \
  --jq 'select(.status == "ahead" or .status == "identical") | "LANDED"'
```

**Do not poll `mergeStateStatus` to decide you've landed.** If auto-merge is
armed (REBASE), the PR merges the instant merge-gate goes green, and
`mergeStateStatus` then reads `UNKNOWN` for a merged PR — check `state == MERGED`
and `mergeCommit`, not the merge-state. Also note the merged SHA is the
**rebased** commit (a fresh 40-hex), not your pre-merge branch head.

---

# Rebase wrapper + soft-green query (`rebase_wrapper.py`)

`ci-hub/landing/rebase_wrapper.py` is ci-hub's **own** rebase wrapper. It owns the
whole span **rebase → push → validate-at-pushed-head → record** (not just the
rebase): it records `revision X rebased on main Y -> Z`, derives a **soft-green
confidence level**, and binds the **receipt at the pushed head Z**, so a lander
can decide whether to land on the prior or wait for a tip validate.

**Soft-green is a level, not a boolean.** Zero textual conflicts is a
high-confidence *prior*, not a proof (git conflict detection is line-based;
semantic dependency is not — a caller X added while Y changed the callee's
contract has no conflict and still breaks). So:

- `soft-green(zero-conflict)` — earned mechanically; land on the prior, **verify
  the tip post-facto, fix forward**. The two halves are one system.
- `soft-green(resolver-judged)` — a conflict was resolved and the resolving agent
  judged it low-risk enough to keep the soft green. The **risk judgement is a
  required field**: a conflicted rebase with no `--risk-judgement` + `--rationale`
  is **REFUSED (exit 2)**, never defaulted to green.

**Landability carries the base Y.** A clean rebase onto a base **below a gate
floor** yields an unlandable Z even with zero conflicts. So `landable` includes
"the base clears every floor in `validate/rebase-base-floors.json`" (delegated to
`gate_floors.py`, re-checked live at query time so a newly-added floor demotes a
stale record).

**Landability carries the receipt at the *pushed* head Z.** THE PUSH REWRITES THE
HEAD, so a receipt earned on the pre-push SHA dies with it — and the merge gate
checks the pushed Z, for which no receipt then exists. That exact gap cost an
afternoon (2026-08-04): 15 heads rebased, pushed, marked ready; **none could
merge** (`qualifying_count: 0`) because every receipt was bound to a SHA the push
had discarded. No agent was wrong — the missing step lived in the gap between the
rebase front (ends at push) and the lander (assumes a receipt). So `receipt_at_Z`
is a **first-class record field** (a `null` there is *visible*; a missing receipt
inside an assumption is not), and `eligible` **re-derives it live** by
dereferencing the one receipt authority `ci-hub validate-status --sha Z`. A
receipt appearing after recording promotes Z with **no re-record**; one revoked
demotes it. Right after `--push` the receipt is normally `null` (validate at Z has
not run yet) — the head is recorded `NOT-LANDABLE` and flips eligible only once
validate at Z completes.

**Full landability:** `landable` = soft-green **AND** base clears every live floor
**AND** a clean full-validation receipt is bound live to the pushed head Z. A push
with **no subsequent receipt is REFUSED as landable, never silently queued**.

**Record schema** (`ignored/rebase-records.jsonl`, append-only, latest-per-Z):
`{ schema_version, recorded_utc, source_rev:X, base:Y, result:Z, conflicts:[]|
[files], resolver, risk_judgement, rationale, soft_green, base_clears_floor,
base_unmet_floors, receipt_at_Z: null|{sha:Z, verdict:VALIDATED, profile,
selection_mode, finished_at, slot, host, qualifying_count}, landable,
landable_reason }`.

## Consumer contract (the lander QUERIES; it does not read notes)

```
ci-hub/landing/rebase_wrapper.py eligible                 # list landable heads
ci-hub/landing/rebase_wrapper.py eligible --result <Z>    # exit 0 eligible / 2 not
```

A query has no mailbox to miss — this closes the producer-posted-to-the-wrong-task
gap (12 verified heads were invisible that day because a producer posted to its
own task). By default `eligible` re-checks **both** the base floor and the receipt
at Z **live**; `--no-recheck-floor` / `--no-recheck-receipt` trust the frozen
record snapshot (offline). Records live beside the validate ledger at
`ignored/rebase-records.jsonl` (append-only, latest-per-Z wins). Producer paths:

```
# mechanical: ci-hub owns the rebase, auto-soft-greens the zero-conflict case
rebase_wrapper.py rebase --source <X> --onto newest-green [--push]

# resolver: after resolving conflicts, record with the mandatory judgement
rebase_wrapper.py record --source <X> --base <Y> --result <Z> \
    --conflicts <files> --resolver <agent> \
    --risk-judgement retained-soft-green|needs-full-validate --rationale "..."
```

See memory `ci-hub-ledger-cannot-record-soft-vs-hard-green` (the validate ledger
cannot record soft-vs-hard green — this store carries the provenance it can't).

## Notes

- `run` is preferred over bare `acquire`/`release`: it releases on every observed
  child termination, and evidence-based recovery clears a lease if the
  supervisor itself is killed. Its heartbeat prevents a genuinely long (but
  live) land from having its lease reclaimed out from under it.
- The lease is a **safety net**, not a schedule: keep `--hold` comfortably above
  your real land time, and prefer `run` so releases happen promptly.
- Disjoint footprints don't strictly need the lock, but taking it is cheap and
  keeps the single `[gate]` runner from being contended — when in doubt, hold it.
