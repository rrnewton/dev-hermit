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
acquire  ->  bind exact PR head + counted receipt  ->  durable intent
         ->  fsynced mutation barrier  ->  synchronous sha-guarded rebase merge
         ->  replay/tree proof  ->  exact-landed-SHA obligation handoff
         ->  clear mutation barrier  ->  release
```

Acquire **before** fetching fresh main so every proof sees the final state of the
previous land. Release only after the safe executor has freshly proven the
landed replay and durably handed off its exact merge commit.

## Design (small + deterministic)

- Typed Rust command variants in `ci-hub/ci-hub.rs` own acquire, renew,
  release, status, and run. `landing-lock.sh` is an exec-only compatibility
  path for landers and heartbeats that started before the Rust cutover.
- An advisory **`flock`** on the guard file makes each check-and-set atomic
  across old and new processes.
- The held state is a **lease with an expiry**, not a held fd — so acquire in one
  shell and release in another Just Work, and **(a) a dead holder cannot wedge
  the pack**. Supervised `run` leases record host, boot ID, PID, and process
  start time in a sidecar. It also persists the child leader identity and
  process group. A waiter can reclaim only when the owner is proven gone, that
  group is empty, and no mutation barrier is armed; legacy/manual leases remain
  protected until their lease lapses (`--hold` seconds, default 900).
- Canonical `run` starts an isolated exact-parent pidfd watchdog before its
  first guard attempt. Acknowledged monotonic phase bounds cover acquisition,
  startup, the persisted child deadline plus cleanup graces, and final release;
  SIGSTOP/deadlock can therefore break a flock by killing only the exact
  supervisor. Generic diagnostic/legacy subcommands do not inherit this claim.
- **(b)** The lockfile records holder **agent + optional repository + operation
  + pending mutation + PR + host + timestamps** for debuggability; `status`
  prints them. Supervised exact-head renewal preserves every binding.
- **(c)** Waiters enqueue in a **FIFO**, so ordering is deterministic and each
  waiter sees who is ahead of it; `release` frees the lock immediately and names
  the next agent, which then acquires on its next short (3s) poll rather than
  polling blindly.

Runtime state (all machine-local, gitignored):

| file | role |
| --- | --- |
| `~/work/dev-hermit/.landing-lock`        | holder metadata — the lock |
| `~/work/dev-hermit/.landing-lock.owner`  | supervised owner identity; does not alter the legacy holder format |
| `~/work/dev-hermit/.landing-lock.domain` | supervised child leader/process-group, sibling watchdog identity, and deadline |
| `~/work/dev-hermit/.landing-lock.guard`  | `flock` target (impl detail) |
| `~/work/dev-hermit/.landing-lock.queue`  | FIFO waiter list |
| `~/work/dev-hermit/.landing-lock.cleanup-required` | fsynced armed/published/residual process-domain authority; blocks ordinary acquisition and reclaim |
| `~/work/dev-hermit/.landing-lock.cleanup-required.tmp-*` | atomic-replacement scratch, machine-local and ignored |

`validate-lock` uses the identical cleanup-authority suffixes beside
`.validate-lock`; all four cleanup files are root-anchored in `.gitignore`.

## Usage

```bash
cd ~/work/dev-hermit

# Inspect
ci-hub/ci-hub land-lock status

# Canonical exact-head lander syntax appears below this executable block

# Lock diagnostics only; production Hermit lands use safe-exact-head-land
ci-hub/ci-hub land-lock acquire --agent hermit-ci --pr 1533   # blocks until yours
#   ... diagnostic operation only ...
ci-hub/ci-hub land-lock release --agent hermit-ci

# Crash-contained wrapper (RECOMMENDED): acquire, run, and release only after
# proving the payload domain empty and the exact mutation barrier cleared. A
# background heartbeat renews the lease; a HARD --child-deadline kills a
# wedged subtree, then releases on complete cleanup proof or retains quarantine.
ci-hub/ci-hub land-lock run --agent hermit-ci --repo rrnewton/hermit --pr 1533 \
  --operation 40_HEX_HEAD --child-deadline 2160 -- ./my-land-sequence.sh
```

The real landing command is deliberately a non-executable documentation block:

```text
ci-hub/bin/safe-exact-head-land --repo rrnewton/hermit --pr PR \
  --expected-head 40_HEX_HEAD --actor REGISTERED_AGENT --json
```

### Subcommands

| command | purpose |
| --- | --- |
| `acquire --agent NAME --pr N [--wait S] [--hold S]` | block until acquired (FIFO); reclaims a lapsed lease |
| `renew --agent NAME [--hold S]` | heartbeat — extend your lease during a long land |
| `release --agent NAME` | free an unarmed lock (owner only); an armed mutation refuses manual release |
| `status` | print holder metadata, process liveness, seconds left, and the FIFO queue |
| `reclaim-dead` | release only when owner death and cleanup `Recoverable`/`None` are proven and no mutation is pending |
| `assert-child --agent NAME --repo REPO --pr N --operation X --child-pid PID` | internal verifier only: dereference the exact live holder, operation, process domain, and bounded kernel ancestry; never operator-granted authorization |
| `arm-mutation` / `bind-mutation-call` / `clear-mutation` with the exact assertion tuple | internal safe-executor boundary; bind `X` to exact attempt plus call-count/last-call high-water before invocation, or clear only that attempt |
| `run --agent NAME [--repo REPO] --pr N [--operation X] [--child-deadline S] [...] -- CMD...` | exact binding → gated spawn → auto-heartbeat → cleanup census → release only when cleanup is empty and no mutation remains; otherwise quarantine/retain |

Defaults: `--wait 1800` (give up after 30 min), `--hold 900` (lease lapses after
15 min so a dead holder self-clears), `--child-deadline 2160` (kill a wedged
land subtree after 36 min). Poll interval 3s.

Exit codes: `0` ok · `1` wait-timeout · `2` usage · `3` not-owner / internal ·
`124` child-deadline breach with the land subtree proven gone and the lock
released · `125` heartbeat failure with the subtree likewise proven gone and
released. An incomplete cleanup proof returns an error and retains a
`QUARANTINED` lock; a pending mutation retains exact-operation recovery.

### `--child-deadline`: no unbounded wait may hold the queue

`run`'s heartbeat renews the lease **for as long as the child lives** — so a land
script that hangs at a failing/UNKNOWN gate would renew forever and **wedge the
FIFO permanently** (the observed ~2040-minute head-of-line starvation). The fix:
`run` supervises the child against `--child-deadline`; on breach it SIGTERMs the
whole child process group (then SIGKILL after a short grace), and performs a
complete descendant census. Only a proven-empty payload domain **releases the
lock**, prints a loud `ABANDON PR #N` line, and exits `124`; an incomplete or
nonempty census retains a `QUARANTINED` lock and returns an error. The PR is left
open for retry. A zero deadline is rejected: an unbounded wait is unboxed
compute and every wait here is bounded. The canonical safe exact-head executor
reports the bounded refusal/pending result for recovery.

`safe-exact-head-land` always supplies `--repo rrnewton/hermit` and strips
generic lock, landing-store, obligation-store, and docs-parse overrides
before entering the lock. Its hidden inner flag carries no authority. The inner
process must pass `assert-child`, which binds exact agent/repository/PR/operation and
holder host to the live supervisor's boot ID/PID/start time, then proves the
selected Python child and assertion verifier lie on the same bounded kernel
process chain and in the recorded process group. A legacy/manual,
repository-less, or operation-less lease cannot authorize it.

**Never hand-roll a renewer.** A bare `acquire` plus an external `renewer.sh`
loop that outlives a dead agent defeats the lease-lapse safety net and is exactly
what produced the zombie-held lock. Always land under `land-lock run` (directly,
or via `safe-exact-head-land`, which self-wraps) so the lease is bound to a
bounded child.

### Abnormal termination and evidence-based recovery

Before spawn, `run` fsyncs an `armed` cleanup authority. The gated child cannot
exec the payload until an atomic replacement publishes its exact PID/start-time
identity and process group. Before any descendant census, `run` persists
`census-pending`, disables heartbeat renewal, and joins the heartbeat; only then
may it freeze descendants and publish a complete `residual` census. Normal exit,
nonzero exit, and hard deadline clear the authority only after both the process
group and residual census prove the domain empty.

SIGKILL and machine loss cannot finish that census. `status` therefore reports
the lease as `QUARANTINED`, not merely `ORPHANED`, and every ordinary acquire,
renew, release, and dead-owner reclaim refuses. While recorded identities are
live, even explicit recovery is refused. A complete residual record becomes
recoverable only after every exact PID/start-ticks identity and its group are
absent; `reclaim-dead` must additionally prove the supervisor owner dead. A
same-boot `published`/`census-pending` record with no final census remains
unrecoverable even after its leader disappears, because an escaped descendant
may be unrecorded. A host reboot (different boot ID) is the stronger proof that
such a process domain is gone. This preserves the rule that one lander never
force-releases another lander's payload.

The owner sidecar still reports `owner_process=alive`, `dead:...`, or
`unknown:...`. A proven-dead supervised lease with **no** cleanup authority is
shown as `ORPHANED (reclaimable)` for backward-compatible/manual cases.

Before the synchronous GitHub REST mutation, the safe child fsyncs
`pending_mutation=X` with the exact durable attempt and advances a fsynced
`{call_count,last_call_id}` high-water before each invocation. Nonzero exit or
supervisor death retains it after the process-domain cleanup completes. Only
the exact same agent/repository/PR/operation may adopt that state; manual
`release`, generic `reclaim-dead`, a different operation, and a nominally
successful child that did not clear the barrier all refuse. The safe executor
clears the barrier only after replay verification and durable exact-landed-SHA
obligation arming.

The holder file format is byte-compatible with pre-sidecar landers. A legacy
bare `acquire` has no process evidence and therefore remains lease-only: it is
never declared dead merely because a PID was not recorded.

## Exact-head executor and active legacy migration gap

Hermit's live land sequence and the retained helper history live here:

| script | role |
| --- | --- |
| `ci-hub/bin/safe-exact-head-land --repo rrnewton/hermit --pr <PR> --expected-head <X> --actor <agent> --json` | **Canonical Hermit lander:** no branch rewrite; binds the counted exact-head receipt, fsynced intent and mutation barrier, synchronous REST `sha=X` merge, actual replay/tree proof, recovery, and exact-landed-SHA obligation handoff. |
| `ci-hub/landing/union-rebase.sh <hermit-wt> <BRANCH> [--push]` | authoritative additive union-rebase of the shared manifest registries (`*.toml` by `[[test]]` id, `test-files.json` by path, `matrix.tsv` by row); the derived `ci/expected-e2e-plan.json` is regenerated, never hand-unioned |
| `ci-hub/landing/land-pr.sh` | **Active legacy executable, not canonical authority:** still mode 755 and still the default `LAND_CMD` in `parallel-prevalidate.sh`; removing that caller is an unresolved fleet-wide migration blocker outside this change. |

The safe executor's complete proof and recovery contract is in
[`SAFE_EXACT_HEAD_LANDING.md`](SAFE_EXACT_HEAD_LANDING.md). If it refuses or
returns pending, preserve and resume that attempt; never bypass it with raw
`gh pr merge`, a branch rewrite, or the legacy script.

The repository has not yet mechanically disabled the legacy caller. Do not
interpret “not canonical authority” as “not executable”: it can still mutate
GitHub today, which is precisely why its remaining caller must be migrated.

### Historical legacy behavior (non-authoritative)

The policy-prohibited legacy `land-pr.sh` attempted the following race-tolerance measures.
This material explains old logs and tests; it is not a procedure to run:

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

The legacy launcher used `nohup` plus a new session and emitted an ABANDON
comment on terminal bailout. Those behaviors are historical observations, not
authorization to invoke it.

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
| whole child | 2160s by default | entire lock-held subtree; the quarantined legacy script derived twice an overridden gate deadline unless explicitly set |
| merge retry | 12 attempts, 15s sleeps | at most 180s of explicit retry sleep |
| gate poll | 15s interval | included in the gate deadline |
| label / ready settling | 4s each | fixed sleeps before polling |
| termination grace | 5s | SIGTERM-to-SIGKILL interval after child timeout |

Individual `git` and `gh` calls do not have separate per-call timers. They are
inside the whole-child process-group deadline, so a stalled call cannot hold the
landing lock beyond that ceiling. The child deadline must be greater than the
gate deadline; zero is rejected rather than meaning unbounded.

## Observing a completed land (not release authority)

These read-only queries are useful diagnostics after the safe executor returns
`LANDED_AND_ARMED`:

```bash
with-proxy gh pr view PR_NUMBER -R rrnewton/hermit --json state,mergeCommit \
  -q '{state:.state, sha:.mergeCommit.oid}'          # want state=MERGED
with-proxy gh api \
  "repos/rrnewton/hermit/compare/$(with-proxy gh pr view PR_NUMBER -R rrnewton/hermit --json mergeCommit -q .mergeCommit.oid)...main" \
  --jq 'select(.status == "ahead" or .status == "identical") | "LANDED"'
```

They do not authorize `land-lock release`. PR state plus main ancestry omits the
replay-tree proof, exact actual-base receipt when required, durable remediation
arm, and the pending-mutation barrier. Only the supervised safe executor clears
that barrier and releases after its complete proof.

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

## Canonical store, reconciliation, and cross-host provenance

**One store per host, never per-checkout.** The store path resolves from
`CI_HUB_REBASE_STORE` (override outright — a fleet pins one shared path), else
`ignored/rebase-records.jsonl` under `parent_root()`, which honours
`DEV_HERMIT_PARENT` and only *then* falls back to the module location. It is
**never** anchored to `__file__`: a scratch/worktree-slot copy of the wrapper has
a different `__file__`, so a `__file__`-derived path would send the producer's
records to a store the lander never reads — the same producer-wrote-own /
consumer-read-other gap `eligible` exists to kill, merely relocated onto the
filesystem. With the env set once on a host, every copy converges on one store.

**`eligible` list mode reconciles against the LIVE open-PR population** (`gh pr
list`, default `rrnewton/hermit`; `--no-reconcile` for store-only) so *invisible
!= nothing-pending*. Every open pushed PR is accounted for exactly once and lands
in a bucket: `eligible`, `pending-no-receipt` (soft-green + base OK, awaiting
validate@Z), **`receipt-unknown`** (the receipt authority was unreachable —
VISIBLE and non-landable, **never** collapsed to absent, so a validate-status
failure can no longer make an eligible head vanish silently), `unaccounted` (open
PR with no record — pushed by a path that never called the wrapper), or
`disqualified`. Recorded heads matching no open PR are reported `recorded_not_open`
(superseded / force-pushed away — the chronic push-rewrites-the-head orphan case).

**Cross-host durability (opt-in).** A machine-local JSONL cannot carry the one
non-derivable datum (the soft-green level + resolver judgement + rationale) to
another host. `record`/`rebase --publish-provenance` publishes exactly that datum,
content-addressed and keyed by Z, to the shared `validation-receipts` branch;
`eligible --durable-provenance` recovers an otherwise-`unaccounted` open-PR head by
dereferencing it — then holds it to the **same live floor + receipt gates** as a
local record (durability supplies the datum, never landability). Network stays off
the default hot path.

## Notes

- `run` is preferred over bare `acquire`/`release`: it owns the heartbeat,
  persisted process domain, whole-group cleanup, and retained-mutation recovery.
  Supervisor death never clears a live domain or armed operation merely because
  the owner PID disappeared.
- The lease is a **safety net**, not a schedule: keep `--hold` comfortably above
  your real land time, and prefer `run` so releases happen promptly.
- Disjoint footprints don't strictly need the lock, but taking it is cheap and
  keeps the single `[gate]` runner from being contended — when in doubt, hold it.

---

# Landing preflight (`ci-hub/landing/preflight.py`)

**Run this before trusting any green.** Each check is a defect that actually
fired on 2026-08-04; the rules were previously retyped by hand into agent
dispatches, and a rule that gets retyped is a rule that decays.

```bash
# the three checks
python3 ci-hub/landing/preflight.py --sha '<handed-sha>' --pr '<n>'          # 1: SHA still head?
python3 ci-hub/landing/preflight.py --log '<validate.log>'                 # 2: nonzero executed tests?
python3 ci-hub/landing/preflight.py --landed-pr '<n>' --checkout hermit    # 3: landed by ancestry?
# the two standing traps
python3 ci-hub/landing/preflight.py --diff-of worktrees/'<slot>'/hermit    # 4: reverie patch override?
#   5: byte-identical branch -- check_no_byte_identical_branch(), library use
```

Exit **0** only when every requested check PASSes. **`UNKNOWN` blocks**: an
unanswerable check must never launder "I could not tell" into "it is fine".
Add `--no-network` to run offline (unresolvable checks then report UNKNOWN, so
the gate correctly refuses rather than passing).

1. **The SHA you were handed is a cache; the branch is the source.** Four handed
   SHAs went stale in one night.
2. **A green must carry a nonzero executed test count.** `--features` gating
   yields build-ok / target-ran / zero-executed / SUCCESS. Absent and empty logs
   are refused too.
3. **Landing is verified by `mergeCommit.oid` ancestry on a freshly-fetched
   remote** — never the PR head (always false after a rebase replay: that read
   79 unlanded when 46 had landed), never the `MERGED` flag alone (a later
   force-push orphans the replay SHA).

Negative tests proving each check refuses its real bad case:
`python3 -m pytest ci-hub/landing/test_preflight.py -q`
