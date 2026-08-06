# PR Planning & Execution — Synthesis

> **SUPERSEDED — DO NOT EXECUTE THIS PROCEDURE.** It contains historical `--admin` and stale-gate
> instructions that violate current policy. The canonical workspace procedure is
> [`ai_docs/pr-landing-consolidated-process.md`](../pr-landing-consolidated-process.md); the canonical
> planner contract is `agent-utils/skills/pr-landing-planner/SKILL.md`.

**Purpose.** One place that captures how PRs move from creation to landed on the
`rrnewton/hermit` and `rrnewton/reverie` forks, how `ci-hub/bin/pr-status` feeds
planning, how a multi-agent harness babysits PRs to green, the landing-sprint /
"PR zero" protocol, and the session memories that encode the traps. This is a
coordinator-role document (dispatch, landing, gitlinks); product build/test
rules live in `hermit/AGENTS.md` and `reverie/AGENTS.md`.

Generated for task `synth-pr-planning-report`. Date: 2026-07-28.

> **Historical implementation note (2026-08-03):** runnable commands now enter
> through `ci-hub/bin/pr-status`, which delegates collection and CI
> classification to the pinned `agent-utils/pr-landing-planner`. The detailed
> implementation description below documents the retired predecessor and must
> not be treated as current code or current landing policy. See
> [`ci-hub/README.md`](../../ci-hub/README.md).

---

## 1. PR lifecycle — nominal vs. actual

### Nominal lifecycle (what most repos do)

```
feature branch → draft PR → CI green → human review approves → land → pin gitlink
```

### Actual policy on these forks — LAND ON GREEN, REVIEW IS POST-FACTO

The single most important operational fact, encoded directly in
`ci-hub/bin/pr-status` and in memory
[self-hosted-ci-sigsegv-blocks-all-prs](#9-session-memory-inventory) (#53):

> **Landing is never gated on human review.** Every open PR is *free to land*
> once CI is green. The `post-facto-review` label is a **post-landing** tag, not
> a pre-landing gate.

So the real lifecycle is:

```
feature branch (from origin/main)
   → PR (draft or ready)
   → CI green on the REAL gate
   → squash-merge with --admin   ← lands here, no human-review gate
   → (post-facto) add post-facto-review label / actual review
   → coordinator pins parent gitlink if a submodule SHA should move
```

Consequences:

- `main` on both forks is **unprotected** (memory #53). `gh pr merge --admin`
  works once the real gate is green; a red *self-hosted* check does **not** block
  the merge.
- A green PR that has **not merged** is IMPLEMENTED, not LANDED. Per `AGENTS.md`
  Task Lifecycle, a working agent tags `implemented` (status stays
  `in_progress`) and records `PR url | SHA | validation`; only the **coordinator**
  closes after the merge commit is reachable from `origin/main`. Phantom
  closures (task closed but work never merged) are the recurring failure mode.

### The "real gate" vs. cosmetic aggregators

- **hermit** real required gate: **"Regular tests (GitHub-managed portable)"**
  (the Portable test DAG, no PMU / no CPUID interception). Historically renamed;
  match both the old and new name (memory
  [hermit-ci-gate-renamed-portable](#9-session-memory-inventory), #27). A
  synthetic **"merge-gate"** aggregator exists and **stale-fires** — treat it as
  advisory, not the source of truth (memory #114).
- **reverie** formal gate (per `AGENTS.md`): BOTH **"Regular tests
  (GitHub-hosted)"** AND **"Host-dependent tests (self-hosted)"** green, with a
  synthetic **"Merge Gate"** aggregating them. Operationally the self-hosted job
  is flaky/red and does not block an `--admin` squash-merge (memory #53), but do
  not weaken a hardware-sensitive test to make it green — report the limitation.

> Tension to hold consciously: `hermit/AGENTS.md` states "require both CI jobs
> green" as the *formal* bar; `pr_status.py` + memory #53 describe the
> *operational* reality (main unprotected, `--admin`, self-hosted flake ignored).
> When a task says "land the green PRs," it means the **real portable gate**;
> when it says "verify before landing," honor the formal bar and record any
> relaxation explicitly.

### Draft / ready and CI triggering — the gotchas

- **Undrafting a PR does NOT trigger CI** on either fork (memory
  [undraft-does-not-trigger-ci](#9-session-memory-inventory), #26): hermit
  `ci.yml` has **no** `ready_for_review` trigger. So `gh pr ready N` alone will
  not (re)run the portable tests.
- hermit `merge-gate.yml` **does** list `ready_for_review`, so readying makes the
  stale aggregator re-evaluate against **already-existing** green jobs (this is
  how a stale gate flips green without a rebuild).
- reverie **"Rust"** workflow triggers only on `workflow_dispatch`, `push` to
  `main`, and `pull_request` to `main` — no `ready_for_review`. Attaching
  merge-gate-consumable checks requires an owner **push** (a `synchronize`
  event). Empirically (task `trigger-reverie-ci`, 2026-07-28) `workflow_dispatch`
  runs execute but do **not** attach as PR checks; and close/reopen and API-side
  ref updates did not spawn `pull_request` runs at all during an Actions-capacity
  window. If `pull_request → Rust` stops firing repo-wide, that is an Actions
  infra condition, not a push-method bug.
- **CI capacity**: the Rust/PMU lane is bottlenecked on a **single self-hosted
  PMU runner** (memory [ci-capacity-single-pmu-runner-bottleneck], #101) — that
  lane is chronically never-green under contention; do not confuse capacity
  starvation with a code failure.

---

## 2. `ci-hub/bin/pr-status` — capabilities and planning use

`ci-hub/bin/pr-status` is now the planning front-end for a landing sprint. It
is a thin dev-hermit adapter around the pinned shared planner. Run it read-only
anytime; it mutates no GitHub state. The bullets below describe its retired
parent-only predecessor.

### What it does

- **Queries** `rrnewton/hermit` and `rrnewton/reverie` by default
  (`DEFAULT_REPOS`); override/extend with repeated `--repo OWNER/REPO`.
- Shells out to **`with-proxy gh pr list --state open --limit 200 --json
  number,title,url,isDraft,labels,statusCheckRollup`** (proxy is mandatory; it
  raises if `with-proxy` is missing).
- **Classifies the CI rollup** per PR into `green | red | pending | none`
  (`classify_ci_rollup`):
  - **red** if ANY check conclusion ∈ {`FAILURE`, `TIMED_OUT`, `CANCELLED`,
    `ERROR`, `ACTION_REQUIRED`, `STARTUP_FAILURE`, `STALE`}.
  - **pending** if any check is `PENDING/EXPECTED/QUEUED/IN_PROGRESS/WAITING/
    REQUESTED`, has no conclusion yet, or `status != COMPLETED`.
  - **green** only if checks exist and none are red/pending.
  - **none** if there are no checks at all (e.g., reverie PRs whose `Rust` never
    attached — see §1 gotchas).
- **Groups & renders** every open PR under "Free to land," split only by whether
  the `post-facto-review` label is already applied. Unlabeled PRs get the inline
  `ACTION: add the post-facto-review label, then merge when CI is green`.
- **Summary block**: total open (all "free to land"), with-label, need-label,
  and **CI-failing** count.
- **Threshold warning**: `--warn-threshold` (default **10**) — if the open count
  exceeds it, prints `WARNING: … prioritize CI repair and landing`. This is the
  "you are above PR-zero budget" signal.
- Exit code **2** on any `gh`/JSON error (usable in scripts / CI).

### How to drive planning from the output

1. **`green` + no conflict → land now.** These are the sprint's immediate wins.
2. **`pending` → wait / re-poll**, do not merge; a pending self-hosted lane may
   just be queued behind the single PMU runner.
3. **`red` → triage**: is it a *real* failure, a *stale* aggregator fire, or a
   *flaky/capacity* lane? Only real failures need a code fix (§5).
4. **`none` → attach CI** (owner push / see §1 reverie triggering) before it can
   ever be classified.
5. **CI-failing count + threshold warning** set the sprint's priority: repair CI
   first when the backlog is over threshold.

> `pr_status.py` deliberately does **not** know about mergeability/conflicts or
> `mergeStateStatus`; pair it with `gh pr view N --json
> mergeable,mergeStateStatus` when you need the rebase/conflict picture (§4/§5).

---

## 3. Frontier / speculative rebase strategy

- **Base every branch on `origin/main`.** The old shared `frontier`/integration
  branch was **deleted**; do not branch from it (memory
  [base-feature-branches-on-frontier](#9-session-memory-inventory), #25).
  "MAIN-ONLY."
- **Rebase, do not merge, to catch up.** When a private feature branch no longer
  fast-forwards, rebase it onto current `origin/main` and **re-run affected
  validation** — never paper over divergence with a merge commit (`AGENTS.md`).
- **Convergent duplicates are common** on a saturated frontier: two agents
  independently determinize the same syscall family (e.g. the `ss` sock_diag /
  sabre round-8 convergence, memory #5; batch135 shutdown dup, memory #9).
  **Re-read `origin/main` immediately before pushing/opening a PR** to avoid
  landing a redundant change or a stale-premise PR.
- **Rebase only private branches, only when the task authorizes it**, and provide
  the new SHA + re-validation after. Never rebase/force-push a shared branch or
  `main`.

---

## 4. Multi-agent PR babysitting & dependency chains (A→B→C)

Each mutating agent owns one slot (`worktrees/<slot>/{hermit,reverie,liteinst2}`)
and one feature branch per changed repo. Coordinator babysits the fleet:

**Per-PR babysitting loop:** `rebase onto origin/main → build/validate at the exact
SHA → push feature branch → poll CI → merge when the real gate is green`.

**Dependency chains / coordinated cross-repo changes:**

- When Hermit depends on a Reverie change, **land the lower-level dependency
  first** (reverie), then rebase the consumer (hermit) onto that exact SHA,
  validate the pair, and only then **pin** the parent gitlink(s). Document both
  SHAs and the dependency in each handoff (`AGENTS.md` Cross-Repository Changes).
- For a **stack** A→B→C in one repo: land A, rebase B onto the new `main`
  (B was BEHIND), land B, rebase C, land C. Because **auto-merge is disabled**
  (§5) this is inherently serial.
- **Reverie API changes** must be additive/back-compatible; core-contract changes
  (tool/event model, syscall interception semantics, register/memory contracts)
  need human design discussion before implementation (`AGENTS.md` Reverie API
  Policy). Do not smuggle an abstraction change in as cleanup.

**Branch-ownership invariants that constrain babysitting** (Hard Invariants):

- #2 never let two agents mutate the same file/branch; #5 treat unexpected
  changes as owned by someone else (no reset/clean/stash/overwrite); #10 never
  force-push a shared branch or `main`.
- A "clean/detached" or "merged" slot can still be **BUSY** (memories
  [detached-clean-merged-slot-can-be-busy] #36,
  [parked-slot-reuse-is-racy] #30); git-idle ≠ dead. Confirm liveness (recent
  commit times, checked-out worktrees) before touching another agent's branch —
  e.g. a KVM feature branch checked out live in `worktrees/kvm/reverie` must not
  be pushed to from the coordinator.
- **Never `git stash` in a shared worktree** (memory #31) — the stash stack is
  shared across slots.

---

## 5. Landing-sprint / "PR zero" protocol

Goal: drive open-PR count toward zero by landing every green PR and resolving the
rest, safely and continuously.

### Serial rebase (auto-merge disabled)

Auto-merge is **not** enabled, so the sprint is a serial loop, not a fire-and-
forget batch:

1. `./ci-hub/bin/pr-status` (or `gh pr list … --json
   number,title,headRefName,mergeable,mergeStateStatus,statusCheckRollup`).
2. Land each **green + MERGEABLE** PR:
   `with-proxy gh pr merge N -R rrnewton/<repo> --squash --admin --delete-branch`
   (ready it first with `gh pr ready N` if it is a draft).
3. After each merge, later PRs go **BEHIND**; re-poll — a BEHIND PR often still
   `--admin`-merges (see mergeStateStatus rules below), otherwise the owner
   rebases.
4. Repeat until no green PRs remain; report the exact before/after open counts.

### `mergeStateStatus` decision table

| status | meaning | action |
|---|---|---|
| `CLEAN` | mergeable, checks satisfied | merge |
| `BEHIND` | base advanced | usually still `--admin`-mergeable; else owner rebases |
| `BLOCKED` | a required check not satisfied | `--admin` overrides if the REAL gate is green |
| `UNKNOWN` | mergeability recomputing after a recent push/merge | wait & re-poll |
| `DIRTY` (CONFLICTING) | merge conflict | **owner-only rebase**; coordinator must NOT force-push someone's live branch |

### Trust stale green (acceleration)

The synthetic aggregators (`merge-gate` / `Merge Gate`) **stale-fire**: they can
show failure/stale even when the real underlying jobs are green (memory
[pr-landing-mechanics-merge-gate-uptodate-chase](#9-session-memory-inventory),
#114). Acceleration rule:

- If the **real gate** ("Regular tests (GitHub-managed portable)" for hermit;
  the two real reverie jobs) is green at the exact head SHA, **trust it and
  `--admin` merge** even if the cosmetic aggregator is red/stale. Readying the PR
  (§1) or a `workflow_run`-driven re-eval often flips the aggregator without a
  rebuild.
- If a check is genuinely stale/flaky, prefer **`gh run rerun --failed`** over
  forcing a full new push, to avoid a merge-gate up-to-date chase.
- Distinguish a **capacity** red (single PMU runner, memory #101; self-hosted
  SIGSEGV, memory #53) from a **code** red — only the latter needs a fix.

### Red-PR triage (fix vs. close vs. defer)

- **Real, fixable failure** → route to a mutating agent in a worktree slot;
  fix on the feature branch, push, re-validate. Do **not** weaken
  hardware-sensitive assertions to force green — report the limitation.
- **Obsolete / superseded / stale-premise** → close with an explanation comment
  (the change already landed, target removed, or a convergent-dup already
  merged). Verify it is *actually* superseded before closing; do not close a live
  PR just because it is old.
- **Conflict (DIRTY)** → owner rebases; coordinator does not force-push.
- **Never** create empty "bookkeeping" commits to signal progress; never
  `git add -A` a shared checkout (you would sweep in another agent's uncommitted
  work).

---

## 6. Review-comment handling

- **Role tag prefix is mandatory** on every PR description and comment
  (`dev-hermit/AGENTS.md` PR Comment Convention):
  `[impl agent, MODEL]`, `[adversarial-reviewer agent, MODEL]`,
  `[coordinator, MODEL]`, `[Human]`. Example: `[coordinator, opus-4.8]`.
- Review is **post-facto** here (§1): the `post-facto-review` label is applied
  around/after landing, and the adversarial-reviewer gate confirms the work
  actually exists in the PR (diff matches the report, cited validation is real at
  the handoff SHA) before the coordinator closes the task — it is not a
  pre-landing approval gate.
- **Bot-created issues go on `rrnewton` forks ONLY**, never
  `facebookexperimental/*` (those sync to Meta's internal tracker). Use the
  wrapper `./.orc/plugins/hermit-dev/gh-issue-create`, not raw `gh issue create`.
- **External-facing** report URLs must point at the readable fork
  (`rrnewton/…`), not private upstreams.

---

## 7. Auto-merge disabled → manual `--admin` flow (quick reference)

```bash
# Poll
~/work/dev-hermit/ci-hub/bin/pr-status
~/work/dev-hermit/ci-hub/bin/pr-status --repo rrnewton/hermit --warn-threshold 0

# Per-PR mergeability
with-proxy gh pr view N -R rrnewton/hermit \
  --json number,isDraft,mergeable,mergeStateStatus,statusCheckRollup

# Ready a draft (note: does NOT rerun hermit portable CI; only re-evals the gate)
with-proxy gh pr ready N -R rrnewton/hermit

# Re-run only failed checks (prefer over a fresh push when a lane is stale/flaky)
with-proxy gh run rerun --failed -R rrnewton/hermit <run-id>

# Land (squash + admin override; main is unprotected)
with-proxy gh pr merge N -R rrnewton/hermit --squash --admin --delete-branch

# In Meta environments, use appropriate proxies for accessing the web.
# Prefix networked git/gh with `with-proxy`; never `gh auth switch`/`gh auth login`.
```

---

## 8. Skills / paths / tooling inventory

- **`ci-hub/bin/pr-status`** — open-PR health & land-readiness (this doc, §2).
- **`hermit-dev` ORC plugin** (`.orc/plugins/hermit-dev/`) — coordinator policy
  plugin; reads `dev-hermit/AGENTS.md` at activation; ships
  `gh-issue-create` (fork-only issue wrapper).
- **`hermit-lander`** — dedicated landing agent (babysits PRs to green + lands).
- **Slot tooling** — `scripts/allocate-worktree.rs`, `scripts/release-worktree.rs`
  (registry-aware, single writer of `worktree-state.json` + `ACTIVE.md` managed
  block); `scripts/slot-init.sh` (registry-less fallback).
- **Registries** — `worktrees/ACTIVE.md` (machine-local, ignored),
  `worktrees/ARCHIVED.md` (durable history),
  `ai_docs/transient/2026-07-27-worktree-management-map.md` (authoritative index of every
  place worktree info lives — read before any worktree op).
- **Policy sources** — `dev-hermit/AGENTS.md` (= `CLAUDE.md` symlink) for
  coordinator rules; `hermit/AGENTS.md` / `reverie/AGENTS.md` for product build,
  test selection, and evidence.
- **Parent branch** — the parent harness works on shared `main`; `legacy-lead`
  (+ per-slot `legacy-lead-slotNN`) is this machine's lead pointer, currently an
  ancestor of `main` (fast-forwardable). Parent commits: small, frequent, pushed
  to `origin` (`rrnewton/dev-hermit`); 2 MiB / no-binaries ceiling applies.

---

## 9. Session-memory inventory (referenced #7,21,25,26,28,48,53,62,74–79,83 + PR-critical adds)

Line numbers are into the auto-memory `MEMORY.md` index; slugs are the memory
files under `…/memory/`.

**Directly PR-process critical:**

- **#25** `base-feature-branches-on-frontier` — **MAIN-ONLY**; frontier branch
  deleted; base all work on `origin/main`.
- **#26** `undraft-does-not-trigger-ci` — undrafting does not rerun hermit CI
  (`ci.yml` has no `ready_for_review`); readying only re-evaluates the gate.
- **#53** `self-hosted-ci-sigsegv-blocks-all-prs` — **`main` is unprotected;
  self-hosted red does NOT block `--admin` merges.** The basis for land-on-green.
- **#27** *(add)* `hermit-ci-gate-renamed-portable` — real gate now "Regular
  tests (GitHub-managed portable)"; match old+new names.
- **#114** *(add)* `pr-landing-mechanics-merge-gate-uptodate-chase` — merge-gate
  **stale-fires**; rerun `--failed`; trust real green.
- **#101** *(add)* `ci-capacity-single-pmu-runner-bottleneck` — Rust/PMU lane
  never-green under contention (capacity, not code).
- **#28** `devhost-cpuid-faulting-works-flag-is-for-ci` — `--no-virtualize-cpuid`
  is for the portable CI runner, not a dev-host limitation; keep it in harnesses
  so the portable gate stays green.

**"What actually lands" — determinism results (planning context for which PR
classes are landable / mature):**

- **#7** `kvm-ratchet7/8/9` — landed examples (#781 readv, #788 recvmmsg, #805
  select); ratchet-style incremental KVM PRs.
- **#21** `cpp-programs-determinize` + `real-compute-programs-pass-strict-verify`
  — C++ (thread/mutex/condvar/async/atomic) and real-compute reach L2.
- **#48** `tail-rr-diverges-input-is-output` — a known R/R divergence class
  (input-is-output guard) → expect such PRs to carry caveats, not clean green.
- **#62** `clock-monotonic-already-deterministic` — already handled; a
  "re-fix" PR here would be a stale-premise close.
- **#74** `ptrace-stress-tests-3of4-l2` — 3/4 ptrace stress at L2 (RT-signal
  fails) — landable minus the signal case.
- **#75** `sabre-pipe-eagain-execd-children` (#1035) — backend gap; fix lives in
  pinned reverie/SaBRe, approval-gated (not a quick land).
- **#76** `sabre-rdtscp-not-intercepted` — RDTSCP leaks; pinned-reverie fix,
  approval-gated.
- **#77** `compat-push-ptrace-l2-frontier-mature` — ~50 harder programs pass
  `--strict --verify`; frontier mature; remaining fails filed (#830, #1039).
- **#78** `liteinst-interception-incomplete` (#1047) — liteinst nondeterministic
  under `--verify`; backend gaps route to pinned reverie (approval-gated).
- **#79** `concurrency-stress-determinizes` — mutex/rwlock/barrier/semaphore
  pass; landable class.
- **#83** `bash-scripts-verify-fork-volume` — bash scripts verify at L2 with a
  fork-volume ceiling.

**Read these before touching branches (babysitting safety):**

- `detached-clean-merged-slot-can-be-busy` (#36), `parked-slot-reuse-is-racy`
  (#30), `never-git-stash-shared-worktrees` (#31),
  `worktree-cleanup-is-unsafe-for-agents` (#35).

---

## 10. Recommended ORC workflow for a "PR zero" sprint

A deterministic, safe fan-in loop (coordinator + `hermit-lander` + worktree
agents). Serial where landing is serial, parallel where triage is independent.

1. **Snapshot** — `./ci-hub/bin/pr-status` for both forks; capture exact
   open count, and per-PR `ci_status` + (via `gh pr view`) `mergeStateStatus`.
   Classify: `green+CLEAN` / `green+BEHIND` / `pending` / `red-real` /
   `red-stale-or-capacity` / `none(no-CI)` / `DIRTY(conflict)`.
2. **Land the wins (serial).** For each `green` PR in dependency order (deps
   first): ready if draft → `--squash --admin --delete-branch` → re-poll (later
   PRs shift BEHIND). Trust real-gate green over a stale aggregator.
3. **Attach missing CI.** For `none` (esp. reverie): owner push / `synchronize`;
   do not rely on `workflow_dispatch` (won't attach) or undraft (won't trigger).
4. **Triage red in parallel** (independent, so fan out): real→fix on a feature
   branch in a slot; stale/capacity→`rerun --failed` or wait; obsolete→close with
   an explanation after verifying it is truly superseded.
5. **Resolve conflicts by owner.** DIRTY PRs go back to their owning agent for a
   rebase onto `origin/main`; coordinator never force-pushes a live branch.
6. **Rebase BEHIND, re-validate, re-land.** Loop 2–6 until `pr_status.py` reports
   zero green-unlanded and the backlog is under `--warn-threshold`.
7. **Report** with the communication-precision rules: name the exact PRs, the
   real gate + head SHA, "New this run" vs "Baseline," and the before/after open
   counts — never a bare pass ratio.
8. **Pin gitlinks last.** Only after a submodule commit is on `origin/main` and
   validated, the coordinator moves the parent gitlink and commits it to the
   parent (`main`) — separate from the submodule source commit.

**Adversarial-verify overlay** (for a thorough sprint): after step 2, an
independent reviewer confirms each "landed" PR's merge commit is actually
reachable from `origin/main` (guards against phantom closures) before the task is
closed by the coordinator.

---

### Quick appendix: authoritative facts to not re-derive

- Land on green; review is **post-facto** (`pr_status.py`, memory #53).
- Real hermit gate = **"Regular tests (GitHub-managed portable)"** (memory #27);
  aggregators stale-fire (memory #114).
- Undraft ≠ CI trigger (memory #26); reverie needs a **push** to attach checks.
- **MAIN-ONLY** base (memory #25); rebase not merge to catch up.
- Never `git add -A` / force-push / stash on shared or others' branches
  (Hard Invariants #2/#5/#10; memories #30/#31/#35/#36).
- All networked `git`/`gh` via `with-proxy`; never `gh auth switch`.
