# AGENTS.md Policy Rationale (read on demand)

Companion to the parent `AGENTS.md`/`CLAUDE.md`. That file carries the **executable predicates**;
this file carries the **rationale, worked examples, and reference tables** an agent reads only when it
needs the *why* behind a rule. Splitting them keeps the spawn-time policy file under its 40,000-char
limit (every agent pays that file at spawn; this one is read on demand). When a predicate in `AGENTS.md`
points here, this is the long form.

---

## Proxy Binding Review Axis — full rationale

**Proxy Binding** is the mandatory adversarial-review axis: **what binds this check to the fact it claims,
and can I observe that binding rather than infer it?** Authenticating **who** emitted evidence proves
origin, not causation. A predicate such as `marker-present && mismatch-present` merely ANDs two independent
facts. Causal binding requires evidence that can exist only when the claimed condition caused the reported
outcome, such as a typed first-cause result.

**One verifier per authority, called by every consumer.** An **evidence authority** is the source whose
contents can make a load-bearing claim true; a label, comment, status, or copied field is only a cache or
reference to that source. Give each authority one semantic verifier that dereferences the source, validates
the qualified value, and is called by every gate, labeler, lander, status view, and closure path that acts on
the claim. Do not collapse different authorities behind one generic-looking semantic check: a validation
receipt and a Git commit require different proofs. They may share transport/digest primitives, not truth
conditions. A verifier is not deployed merely because it exists. Mark an authority covered only after (1) a
counted qualifying positive passes, (2) a well-shaped but nonexistent/tampered/otherwise nonqualifying
negative is refused, and (3) a call-site audit shows that every consumer invokes the verifier rather than
reimplementing or bypassing it.

### Load-Bearing Authority Registry

This registry covers authorities that currently decide validation, review, landing, dependency currency, or
workflow-policy outcomes. Add a row before introducing another load-bearing authority or consumer; update a
row only from exact code and tests, never from a PR description.

| Authority and qualified value | Canonical dereferencing verifier | Coverage and remaining hole |
| --- | --- | --- |
| **Local validation ledger:** exact Hermit SHA plus clean/full/counted local ledger row and satisfied per-node coverage. | `ci-hub validate-status` / `ci-hub/lib/validate_status.rs`; prevalidation, history, Rust label/receipt selection, and immutable-receipt paths call this verifier. | **PARTIAL.** The legacy lander is disabled and no current safe lander consumes the result. `validate.sh` still applies `locally-validated` directly from its own exit path. Labels are not ledger evidence; the Rust label path must first call the ledger verifier. |
| **Published validation outcomes/receipt:** one canonical branch-tip set of append-only content-addressed exact-SHA outcome snapshots; a pass additionally binds its receipt, durable log, source row/finalizer provenance, counts, exact target tree, and Reverie conditions. | Producer: Rust `apply-local-label` publishes every typed assessment and re-verifies the returned snapshot; PASS publishes log then receipt then outcome, retrying concurrent append races without overwrite. Consumer: exact-tree `verify_receipt_bundle.sh` first loads its required-path manifest from the expected bundle commit, then enumerates every outcome at one resolved tip and unions their rows through `validate-status` (genuine failure is monotonic). That canonical selector admits a Hermit pass only after the finalizer recomputes it from its unique source row, exact-commit manifests, and durable log; large logs use exact Git blobs. Comments/labels are routing/cache only. | **PARTIAL.** Brackets cover producer→consumer, later failure over older pass, self-asserted finalized rows, arbitrary-log/count claims, finalizer source identity, bundle-owned required paths, required manifests, failed terminals, unplanned banners, stdin large-log transport, publication races, content tamper/absence, guarded predicate binding, zero-gate identical-tree cache refusal, PR-head label-race cleanup, moved pin, Git replacement/global/local-config redirects, and the executable symlink. The current Hermit workflow must still pin and call the complete parent bundle and publish every completion path before this becomes the live local leg. No active safe lander yet atomically binds source, replay base, and result. |
| **Historical Git provenance:** the claimed commit exists in the named repository and the measurement/artifact is bound to that commit, not merely written beside a 40-hex string. | **None.** The minimum identity primitive is a fresh fetch plus `git cat-file -e <sha>^{commit}` in the intended repository; measurement causation additionally needs a commit-bound artifact/receipt. | **MISSING.** Hermit #1546 validates provenance fields and full-SHA syntax without dereferencing the object or a measurement artifact. Commit existence alone proves identity, not that the measurement came from it. |
| **Adversarial/human review:** reviewer lane, verdict, PR number, and exact reviewed head in a durable receipt produced by that lane. | **None.** `scripts/core-review-protocol-lint.sh` checks cache labels only. | **MISSING.** Push-time label invalidation narrows staleness but does not prove who reviewed what; `passed-review-*`, numbered review labels, and `human-approved` are assertions until a lane-specific exact-head receipt is dereferenced. |
| **Landing authorization:** a durable coordinator/owner decision names the exact repository, PR, source head X, observed target base Y, replay result Z, and final dependency frontier allowed to land, with no unresolved hold. | **None active.** The legacy `land-pr.sh` mutating path is fail-closed before any checkout/network mutation because server-side replay cannot atomically condition on Y. | **MISSING.** `safe-exact-head-land` must combine a durable coordinator decision with canonical local/hosted validation, exact X/Y/Z identity, the current landing lock, and an atomic final-boundary dependency check. Agent assignment, a ready label, or invoking a legacy script is not authorization. |
| **Landed PR identity and task closure:** GitHub's `mergeCommit.oid` is reachable from a freshly fetched target branch. | `ci-hub verify-landing`; `ci-hub/closure/verified_close.py` calls it. | **PARTIAL.** Dead code below the legacy lander's early refusal reimplements the proof, and `ci-hub/remediation/land_and_arm.py` can arm from `state == MERGED` plus a shaped `mergeCommit.oid` without proving ancestry. The safe lander and remediation must call `verify-landing`; never substitute PR-head ancestry, `MERGED`, or `mergeStateStatus`. |
| **Hosted CI result:** an authoritative workflow/job run for the exact head, with terminal success distinct from failure and `NO_RESULT`. | Exact-head GitHub API run/job lookup plus the canonical result classifier in `ci-hub/check_outcome.py`. | **PARTIAL.** Remediation, health, and history paths call the classifier, but no active safe lander yet combines it with local validation. Other consumers still hand-roll lookup/classification; `hermit/scripts/pr_status.py` maintains separate conclusion sets and #1593 adds another implementation. Cancelled/skipped/missing/queued/stale are `NO_RESULT`, not red or green. |
| **Workflow-policy version:** the required context was emitted by the trusted current workflow definition, not an older PR-branch YAML with weaker rules. | Hermit #1579's versioned context/blob check and ruleset reconciler. | **MISSING on main.** Until the versioned gate lands and every required-context consumer switches, stale branch YAML can emit a current-looking green. |
| **Live dependency currency:** the exact Hermit commit's real Cargo dependency entries and Cargo.lock `[[package]]` sources identify `rrnewton/reverie`, every manifest rev and lock query/precise fragment is the same lowercase full SHA, and that SHA equals one freshly resolved canonical main ref. | Parent authority: `ci-hub reverie-pin-status` / `ci-hub/lib/reverie_pin.rs`; receipt consumers share `ci-hub/lib/qualifying_receipt.rs`. It disables Git replacement/object-locator and inherited config redirects, sets a discovery ceiling even when caller-controlled TMPDIR is inside a worktree, resolves live state outside any repository, proves the target is a commit, rejects every semantic path/version/registry Reverie dependency even beside a decoy Git pin, and structurally parses real dependency/package tables. | **PARTIAL.** Parent brackets cover semantic positives plus replacement/tree-object, global and TMPDIR-local config redirects, path-plus-decoy, duplicate/mismatched query, short SHA, forged repo, metadata/comment spoof, malformed/mixed pin, moved tip, and cache invalidation negatives. The current Hermit hosted workflow still uses a separate checker until the coordinated exact-tree bundle integration lands; no safe lander yet binds X/Y/Z plus a final-boundary ref refresh. |
| **Workspace ownership:** `worktree-state.json`, the managed `ACTIVE.md` row, registered worktrees, and actual checked-out branches/detached SHAs agree. | `scripts/check-worktree-registry.rs`; its fixture accepts 2/2 correct rows and refuses planted content drift while preserving a 1/1 correct control. | **PARTIAL.** `allocate-worktree.rs --check-only` calls it, but normal allocation, `release-worktree.rs`, and `worktree-gc.sh` do not all run the verifier before acting. They must call it rather than trusting or independently parsing one surface. |
| **Mechanism overlap:** every open PR changing the same load-bearing mechanism is in the coordinator's exact-head review set, including mechanisms an author failed to tag. | **None for semantic completeness.** `ci-hub pr-status` groups declared `mechanism:<slug>` labels; it cannot prove that the declarations cover the code's actual mechanisms or that two intentions are compatible. | **MISSING; human semantic authority required.** The label grouper is a discovery aid, not a verifier. Record coordinator review against the exact PR/head set, and retain file/symbol review for untagged overlap; do not describe label absence as proof of independence. |
| **Merge conflicts and final mergeability:** fetched PR/base objects are the objects analyzed, pairwise conflicts come from real Git merges, and the final mutation is conditioned on the same target base. | Planning: `scripts/pr_conflict_graph.py` fetches base/head refs, checks API-head equality, and runs `git merge-tree`. No active landing verifier currently provides an atomic expected-base mutation. | **PARTIAL: planning covered, landing missing.** `mergeable` and `mergeStateStatus` remain hints only. `safe-exact-head-land` must retain the real merge-tree planning path and must not promote a preflight or server-side `gh pr merge --rebase` into proof that the observed base stayed fixed. |

The generative cure is: **carry the condition with the value.** A value measured under conditions it does
not record is a proxy, whether that value is a string, flag, status, hash, or number. Store `{ jobs: 32,
bytes: N }`, not a bare memory cap `N`; bind green to an exact-SHA run with a nonzero executed-test count;
bind landing to `mergeCommit.oid` ancestry on freshly fetched main, not a PR head or `MERGED` flag. A bare
value and a qualified value often read identically as facts, so inspection cannot reveal that the
qualification is missing. Reviewers must ask what conditions made the value true, whether those conditions
travel with it, and whether they are still current at the decision point.

For test and validation results, **a green must carry what it verified** in one result record: exact SHA,
profile, discovered count, selected count, executed count, filtered/skipped count, failure count, and the
declared per-node coverage obligations. A full green requires the full profile, nonzero execution, satisfied
coverage obligations, and zero failures. A bare `filtered == 0` predicate is not completeness: legitimate
suites can filter tests (693 in one measured full run), while an incomplete discovery set can report zero
filtered. A partial-profile `PASS` row is not a full green, and `test result: ok` with zero executed tests is a
no-result, not success. Keep these qualifications together at the ledger-write point so no downstream reader
can pair a bare `PASS` with inferred coverage.

Verification must bracket guarded behavior from both sides. **Negative:** plant the violating case and
confirm **refusal** (proves the mechanism is not permissive). **Positive:** plant the genuine qualifying
case and confirm it **fires** (proves the mechanism is not inert). Neither alone is verification: a guard
that refuses everything passes every negative test. State the counts on both sides. PR #1468 is the model:
9 cells / 18 executions remained eligible with zero fallback and zero trusted-native sites, while the
`random-device` negative was rejected with 66 trusted-native sites.

Do **not** plant an artifact that is itself an authorization. Hand-adding a merge/review/validation label,
dispatching a workflow that can auto-merge, or arming another live gate tests by creating the hazard. Exercise
the consumer with an inert fixture, a dry-run/read-only mode, or an isolated test repository; the negative
control must be incapable of authorizing the action whose refusal it tests.

A check fails when it keys on a correlated proxy without an observable identity, causal, coverage, or
provenance link to the claimed condition. Reviewers name the claimed fact, the observed evidence, the
conditions under which it was measured, and the binding between them; passing tests do not supply a missing
binding. The current twelve worked examples are:

1. A `locally-validated` label with no exact-head ledger record: the label is a cache, not the source of truth.
2. A merge gate that authorizes on bare label presence without reading the ledger.
3. `workflow_dispatch` running the PR branch's older YAML, allowing a weaker historical gate to emit the same green.
4. `is-ancestor <PR head>` encoding a merge-commit model under rebase-merge, where the PR head is never ancestral to replayed main (it undercounted landings by 33); use `mergeCommit.oid` after a fresh fetch.
5. A pin checker walking `Cargo.toml` but not tracked `Cargo.lock`, reporting consistency over an incomplete file set.
6. A green result with no executed count: success is not bound to any work having run.
7. `filtered == 0` used as completeness although 693 filters are legitimate and an incomplete discovery set can also report zero.
8. A `parity%` derived from piped-stdout SHA-256 but presented as full INFO + detlog-stack + detlog-heap parity.
9. `ACTIVE.md` naming a branch the slot does not hold, while reconciliation passes by comparing row counts rather than row contents.
10. `--cgroups` accepted by a CLI but producing no cgroup behavior or typed acknowledgement.
11. A cancelled run classified as red: a no-result is rendered as a result.
12. Dispatch boilerplate listing `commit` as destructive, causing agents to withhold the durable handoff the protocol requires.

Earlier marker-substring, error-string, partial-backend, rendered-SIGPIPE, and unqualified-memory-cap cases are
the same class. In each case ask: **what binds this signal to the fact it claims, and can I observe that
binding rather than infer it?**

### Mechanical enforcement is deliberately split by layer

- **Source/config lint:** reject representations whose missing qualification is syntactically observable.
  Of the twelve examples above, only **3/12** are source/config-lintable without pretending to understand
  runtime semantics: #2 can forbid a label-presence authorization branch, #4 can forbid PR-head ancestry at
  the typed landed-identity boundary, and #12 can reject `commit` in the destructive-operation list of the
  dispatch template that owns it. The existing Rust error-string proxy lint covers **0/12** of this new
  catalogue; it correctly covers an earlier syntactic instance and must not claim more. These checks prove
  only that a known bad representation is absent, not that the replacement binding is truthful or sufficient.
- **Runtime/result checks:** require one ledger record carrying run ID, exact SHA, durable log, profile,
  discovered/selected/executed/filtered counts, failures, and declared coverage obligations; mechanically
  reject full green unless the profile is full, execution is nonzero, coverage obligations are satisfied,
  and failures are zero. This layer catches #1, #3, #6, #9, #10, and #11 with ledger/provenance/content/
  behavioral/classifier checks. Require `mergeCommit.oid` ancestry after a fresh fetch behind landed. A
  planted stale `Cargo.lock` fixture can regression-test the known half of #5, but it cannot prove that every
  future relevant file is in the checker's universe. These are evidence validators and contract tests, not
  source lint.
- **Semantic review:** determine whether a marker is causally bound, a file/backend/gate registry is
  complete (#5), coverage obligations actually define the intended suite (#7), and a parity artifact covers
  the full claimed trace (#8). It must also establish workflow/registry freshness, behavioral currency, and
  causal validity even where a mechanical detector exists. Perfect counts over an incomplete discovery set
  remain a proxy. No general lint can infer these facts. Do not stretch a syntactic lint to claim coverage of
  them; a lint claiming all twelve would itself fail Proxy Binding.

---

## Establish What You Have Before Acting On It — full rationale

A **coordinator** rule for how an observation becomes filed work or a reported conclusion. Both failure modes
below are the same mistake — acting on a claim or a quantity before establishing what it actually is.
Verifying first costs minutes; acting on the wrong thing costs the implementation and the rollback.

**A note is unverified until the coordinator checks it.** A note is one agent's point-in-time belief, not
established fact. Do not launder a note into a task premise by rewriting "X appears to be Y" into "X is Y,
fix it." When a premise originates from a note (or any second-hand observation) rather than the
coordinator's own direct verification, the task description must **attribute the premise to its source**,
**mark it UNVERIFIED** in those words, and **make "verify the premise" the explicit first step, with
"premise refuted" a valid, valuable outcome** — the refutation is a deliverable. A correctly-hedged
observation ("code-inferred, unmeasured") becomes wrong only when the coordinator drops the hedge. (Full
list: note `task-premises-from-notes-must-be-marked-unverified`.)

**A number is unqualified until the coordinator states what it measures.** A number can be arithmetically
correct yet measure the wrong thing; the trap is reaching for the first available quantity, usually a
**proxy**. Before acting, establish **what it measures** (the quantity the decision needs, or a proxy?), its
**unit** (a count is not a rate; an aggregate is not a per-unit; a load average is not a utilisation; a
source tree is not a shipping artifact), and its **denominator/comparison base**. When a ratio looks
surprising, interrogate the denominator before filing work against the numerator.

---

## Verify A Mechanism By The Running Thing, Not Its Config — full rationale

**Do not verify that a mechanism governs a process by reading its configuration, its flag, or its exit code.
Find the running thing and ask what is actually holding it.** A flag can be a deprecated no-op; an exit code
can come from a different wrapper; both mislead, sometimes in opposite directions, and two layers of
inference can be wrong at once. The direct observation settles in one command what the inference got wrong.

Canonical instance (cgroup boxing): take a live PID and walk the cgroup tree to find which scope contains
it — `find /sys/fs/cgroup/... -name cgroup.procs` and grep for the PID, then print the containing path. A
result like `safe.slice/safe-ci.slice/safe-ci-<n>.scope/step-test.<node>/cgroup.procs` holding the PID
proves per-DAG-node boxing is real and active — where reading a (deprecated) flag and an (unrelated) exit
code had claimed the opposite. The same move generalises: check which commit a validate record is keyed to,
which workflow file a dispatch actually ran, and whether a required label has a signer that exists.

---

## Record Every Measurement Immediately — full rationale

**Before you measure, check whether the number already exists** — search `experiments/`, task notes, and the
ci-hub history store first; re-deriving a known quantity wastes fleet time and risks measuring it differently
than the value on record.

**Any measurement you take goes into a task note immediately — even one taken incidentally.** Numbers
measured in passing don't feel like deliverables, so they die in a pane at the next recycle; that loss is
expensive (a passing max-`cc1plus`-RSS reading was the whole explanation for an OOM blocking every ready PR,
but lived only in a pane). If you measured it, write it down with its units, its context, and **how you
obtained it — sampled versus recorded matters enormously**: a polled aggregate and a cgroup-recorded peak
are different numbers, and mislabelling a sampled aggregate as a peak has refuted true findings (polling
misses spikes by construction). Bind the number per *Establish What You Have*: what it measures, its unit,
its denominator.

---

## Running validate — systemd-run producer path, full rationale

**Pre-anchor preflight.** A Hermit PR head that does **not** descend from hermit `bfb0a9ef` (commit-anchoring,
2026-08-03 18:43 UTC) runs its **own older** `validate.sh`, which emits the anchor fields
(`commit_anchored`/`selection_mode`) **NULL**; the consumer `is_clean_full_pass`
(`ci-hub/lib/validate_status.rs:100-121`) is **fail-closed** on them, so the ~17-minute run is a **guaranteed
rejection even when fully green**. The producer travels with the branch, so this can never be fixed by
anything on main — the pre-anchor head must be **rebased**, not re-validated. `#1591` is anchored (rc=0);
`#1558` is pre-anchor and was caught in a drain claim by this check. Re-derive the live pre-anchor set with
the `is-ancestor` loop; do not trust a stale list. **HOLD mass-rebase** until the version-aware counts
consumer (`reject_missing_or_filtered`) lands — see task
`prs-predating-commit-anchoring-can-never-produce-a-qualifying-receipt`.

**Why systemd-run.** An agent sandbox CANNOT run `validate.sh` directly: BpfJailer denies a process
**creating its own** cgroup, so the wrapper exits 3 in ~9s having executed nothing. The tell is `CPU/wall
1.0x` on a many-core box — the run never boxed, never ran. Concluding "the PR is broken" from that exit is a
misdiagnosis (it cost hours). The working path launches validate as a transient user unit — the process ASKS
systemd for a scope instead of creating one itself, so it is **still boxed** (the boxing principle holds,
this is not a bypass), and it runs **detached with a durable log** that outlives agent recycling — the
difference between a green *claim* and green *evidence*. Both legs of GitHub-free landing require a validate
record, and until this path nothing could produce one. Let `apply-local-label` add the label FROM the ledger
record — never by hand. Every completed assessment publishes an append-only, content-addressed outcome;
a pass also publishes its exact counted row and log. The gate discovers all exact-SHA outcomes from one
canonical branch tip and makes genuine failure monotonic; comments and labels are not evidence. Derive the safe concurrency against total cores before fanning out records; do not guess (contended
runs are a recurring bug class). The Hermit Merge Gate must execute the full
`ci-hub/validation/verify_receipt_bundle.sh` from an exact pinned parent commit, never from the pull request under test — a PR-controlled verifier can
authorize itself even when the workflow YAML is separately pinned.

## Canonical Layout — full directory tree

Predicate in AGENTS.md: every normal worktree path is `worktrees/<slot>/{hermit,reverie,liteinst2}` where
`<slot>` is a named agent (`kvm`, `dbi`, `sabre`, `e9patch`, `liteinst`, `ci`, `coord`, `lander`, `opt` —
`hermit-` prefix stripped) or a generic `slotNN`. Nested layout v3: one slot per agent, exactly one mutating
owner. Old flat layout (`worktrees/slotNN` + sibling `worktrees_reverie/slotNN`) and primary-nested
`hermit/.worktrees/…` scratch trees are deprecated — never create either.

```text
~/work/dev-hermit/
|-- AGENTS.md ; CLAUDE.md -> AGENTS.md
|-- .orc/plugins/hermit-dev/       # coordinator policy plugin
|-- .gitmodules
|-- hermit/ reverie/ liteinst2/    # primaries; coordinator only
|-- agent-utils/                    # optional shared tooling; exact pin, on demand
|-- worktree-state.json             # machine-local slot->owner map (gitignored)
|-- worktrees/
|   |-- ACTIVE.md                   # human notes + script-managed slot table (gitignored)
|   |-- ARCHIVED.md                 # append-only completed-slot history (durable)
|   |-- kvm/{hermit,reverie,liteinst2}/  # named-agent slot; dbi/ slot01/ ... up to 12 active + 5 parked
|-- ai_docs/                        # durable research + handoffs
|-- experiments/                    # durable reproducible evidence
`-- scratch/                        # ignored transient material
```

Physical worktrees, build output, `worktree-state.json`, and `ACTIVE.md` are machine-local and gitignored;
`ARCHIVED.md` is durable. `ai_docs/transient/2026-07-27-worktree-management-map.md` is the authoritative index
of every place worktree information lives — read it before any worktree operation.

## Vocabulary — full glossary

- **Parent**: `~/work/dev-hermit/`, the harness repo holding gitlinks and workspace state.
- **Primary checkout**: `~/work/dev-hermit/{hermit,reverie,liteinst2}/`. Coordinator-owned; integration, pinning, inspection, cache donation.
- **Submodule**: a repo the parent records as an exact gitlink SHA — a commit, not a branch, not uncommitted contents.
- **Slot**: one opaque paired workspace under `worktrees/`. **Active worktree**: assigned to live work and in `ACTIVE.md`. **Parked slot**: clean, detached, retained for cache reuse, omitted from `ACTIVE.md`. **Legacy slot**: pre-policy non-canonical worktree in `ACTIVE.md`; may finish its task but must be removed, not reused.
- **Product worktree**: one nested submodule checkout inside a slot, e.g. `slot02/hermit`.
- **Feature branch**: task-specific branch in one product worktree; slot names are unrelated to branch names.
- **Hermit base**: current `rrnewton/hermit:main`, unless a task names another reviewed base. **Hermit upstream**: `facebookexperimental/hermit`, public reference — not the default landing target.
- **Shared slot**: active slot used by multiple research-only agents, or mutating agents with explicitly disjoint file ownership in `ACTIVE.md`. No two agents may edit the same file concurrently.
- **Handoff SHA**: the exact commit tested and offered for integration. A branch name alone is not evidence.
- **3pai agent sandbox**: agent execution environment identified at runtime by `META_3PAI_*` vars and the `3pai_sandbox.slice` cgroup; file/network policy enforced by BpfJailer.

## Product Vision — full statement

`goal-hermit-v2`: a robust deterministic execution engine whose `run`/`record` modes support arbitrary
real-world binaries, whose chaos mode exposes concurrency races, whose schedule search localizes races to
events and stack traces, whose production backend avoids ptrace overhead, and whose non-communicating processes
execute in parallel. `goal-qemu-linux-under-hermit`: run a complete Linux VM as a userspace QEMU process under
Hermit so deterministic execution, record/replay, chaos scheduling, and schedule search expose and localize
kernel races across the full stack. Prioritize correctness, faithful replay, race discovery/localization,
lower overhead, backend maturity, and QEMU/Linux viability. Do not close either long-range goal without its
required human verification.

## Process-Kill Safety — war story

Hard Invariant 15 exists because a broad `pkill` once killed other agents' hermit runs mid-task, and `pkill -f
"find / -path"` recurred later because a freshly recycled agent did not carry the verbal correction — the rule
does not survive recycling unless it lives in AGENTS.md. Up to eighteen agents share this box and its binary
paths (`hermit`, `cargo`, `python3`, …), so any name/pattern/`-f`-substring/user/`ps|grep|kill` match kills
siblings' live work.

## Task Lifecycle — phantom-closure rationale

Phantom closures (a task marked done while its work never landed) are a recurring failure mode; completion
splits into an implementation step the worker performs and a closure step only the coordinator performs, with
an adversarial review gate between. Ordinary agents have no reliable fleet-wide peer-message channel: the
agent-side `SendMessage` registry is scoped to same-session subagents and cannot resolve fixed/numeric ORC
fleet names (`hermit-lander`, `hermit-247`), so never claim another fleet agent was notified merely because a
message was attempted. TaskGraph notes are pull-based — they preserve a handoff across recycling but do not
wake a recipient and are not delivery acknowledgement.

## Coordinator Checklist (coordinator-only preflight)

Terse preflights recapping rules stated in full in AGENTS.md; each references a section there.

- **Before dispatch:** reconcile `ACTIVE.md` + both Git worktree lists + physical slot children; check parent/primaries/candidate slot for unexpected changes; confirm ≤ 12 active worktrees and ≤ 15 agents; record exclusive ownership or every sharing agent + disjoint path; confirm base SHA + publication target per repo, verifying a handed SHA against `ci-hub newest-green`; register the slot before work; apply *Establish what you have* when a premise came from a note/number.
- **Before Hermit publication or landing:** re-read concurrent local state, remote `main`, and the exact PR head; verify the handoff SHA, diff, evidence, cleanliness; push/open only when authorized; require both hosted and self-hosted checks green at the exact head SHA; merge only when authorized; record the resulting `main` SHA + CI.
- **Before parent pinning:** submodule commits clean, reviewed, tested, fetchable; inspect `git diff --submodule=log` before staging; stage only intended gitlinks + parent-owned files; validate a coordinated Hermit/Reverie pair when both move; commit parent changes to `main` only when the task authorizes it.
- **Before closeout:** each changed repo has a clean committed feature branch; record exact SHAs + validation in the task and `ARCHIVED.md`; detach slot children at their parent-pinned gitlinks; remove/update the slot row; keep ≤ 5 parked; leave unrelated concurrent work exactly as found.
- **Before closing a task (coordinator only):** `in_progress` + `implemented` with a PR link/artifact path; adversarial reviewer verified the work exists; invoke `./ci-hub/bin/close-task` with that code/artifact/run reference. Proceed only on rc 0; never let a working agent close its own task; treat `--status resolved` as a raw close that bypasses the gate.

## Action-time procedures (consulted when performing the action; invariants stay in AGENTS.md)

### Submodule pinning — A/B protocol, procedure, initialization

- **A/B protocol** (in `ci-hub/history/SUBMODULE-BUMPS.md`): clean green parent A → advance one gitlink to fetched `origin/main` → B changing only that gitlink → verify B → append to the ci-hub history store. Never bury a gitlink advance in an unrelated commit. Determinism changes need a powered repeated probe (one run is insufficient). Use `make single-submodule-bump ARGS='plan ...'` before execution.
- **Procedure:** confirm each submodule clean and on the intended SHA; inspect `git diff --submodule=log -- hermit reverie`; stage only pointers intentionally moved (`git add hermit reverie` records only checked-out commits, not uncommitted files); re-inspect `--cached`. If both move together, validate the exact pair and update both gitlinks in one commit with old/new SHAs + compatibility evidence. Confirm referenced commits are fetchable from authorized remotes before sharing the parent commit.
- **Initialization** reproduces recorded commits: `git submodule update --init --checkout -- hermit` and the Reverie form with `-c submodule.reverie.update=checkout` (override only when init is intended and `.gitmodules` marks it `update = none`). Do not recursively init optional/heavy nested submodules without a task that needs them.

### Existing Hermit PR checkout — never validate against the PR's historical Reverie pin

Checkout and preparation are one operation: `scripts/checkout-hermit-pr-latest-reverie.sh --repo
worktrees/<slot>/hermit [--push] <pr>`. It fetches current Hermit+Reverie main, checks out the PR without
rewriting history, merges current Hermit main, runs `check-reverie-pin.rs --update-to-latest` to update every
tracked pin site, commits the pin update, and runs full validation at that commit. `--push` publishes the
validated candidate with a non-force push. A stale pin is a hard validation failure; do not substitute raw `gh
pr checkout` + validation.

### Slot closing / parking / reclaiming

Close a slot only after intended work is committed and handed off. Record each child HEAD, feature branches,
exact SHAs, validation, and integration disposition in `ARCHIVED.md`; detach each child at its parent-pinned
gitlink so `git -C $slot status --short` is empty; remove the slot's row from `ACTIVE.md`. Keep feature
branches until reachable from a pushed branch or merged target. Reclaim a parked slot with `git worktree remove
--force` then `git worktree prune` (`--force` is needed because the parent holds initialized submodules; it does
not authorize discarding changes). To reuse a parked slot, repeat the clean-start audit and branch from the
current base — never reset a parked child to make it current.

### Landing Authorization — lander startup obligation discovery

On startup or replacement, `hermit-lander` must discover durable inherited remediation before taking new queue
work (wake messages are advisory, lost during recycling): `ci-hub/ci-hub inherit-obligations --agent
hermit-lander --session "..."`. This acknowledges discovery, not completion; every obligation stays open in
`ci-hub health` until its fix-forward or revert SHA is recorded with `ci-hub resolve-obligation`.

### Worktree Registry — conflicts to resolve before assigning a slot

Reconcile the registry with all Git worktree registries (`git worktree list --porcelain` for parent and each
product) and the filesystem; the parent list owns canonical nested slots, product lists should normally contain
only the primary checkout. Resolve before assigning: an unregistered physical checkout; a registered worktree
whose directory is missing; a live slot absent from `ACTIVE.md`; a row for a parked/missing slot; duplicate
rows; a branch checked out by more than one worktree; any non-canonical path. Never silently delete a stale
path — record what owns it and preserve uncommitted work first.

### Clean Start — submodule status flag legend

Submodule status flags (`git submodule status`): leading space = matches gitlink; `+` = HEAD differs (not
automatically an error — integration may be in flight; attribute before acting, do not erase with a submodule
update unless the reset is intended); `-` = not initialized; `U` = merge conflict.

## Communication Precision — full report-writing rules

Governs coordinator headlines, cross-task aggregation, and user-facing progress reports. Reports must be
specific enough that another engineer can act without re-deriving the scope.

- **Never headline a bare pass ratio.** `10/10 pass` is not a headline. Name the program category, the exact programs (or link a table), the Hermit mode and backend, and why that batch was selected.
- **Separate new results from baseline.** Label every rollup `New this run`, `Baseline reconfirmed`, `Regression`, or `Not rerun`, and state the commit/PR that changed between compared runs.
- **Classify programs before totaling** (system utilities, text-processing, interpreters/runtimes, compilers/build tools, databases, network programs, interactive applications, virtualization/emulators); mixed batches need subtotals.
- **Name execution context** (native baseline, ptrace, DBI, KVM; strict run / strict verify / record-replay / relaxed) and why it answers the batch question.
- **Name the tool** — never "the Tool"; say `StraceTool`, `Detcore`, `CounterTool`, etc.
- **Give the exact command line**, **say where** (`main`, `PR #N`, or exact branch/SHA), and **qualify the result** (determinism level `L0`/`L1`/`L2`, pass count `18/20`, exact programs/tests covered). "It works" is not a result. Bind evidence to commits, not branch names.
