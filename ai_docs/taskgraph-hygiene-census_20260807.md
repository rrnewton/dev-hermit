# TaskGraph hygiene census — full nonterminal enumeration

**Task:** `taskgraph-hygiene-full-open-census` · **Agent:** hermit-w7 (opus-5)
**Snapshot:** `2026-08-07T03:29:12Z` · **Denominator: 431** nonterminal tasks

## Method and its verification

Enumerated with **stable cursor pagination by `local_id`** (`WHERE local_id > :cursor ORDER BY local_id LIMIT 100`), never `LIMIT K` from the head, so no row can be hidden behind a truncation boundary.

| check | result |
|---|---|
| aggregate over the three nonterminal statuses | 431 |
| total minus CLOSED (independent path) | 431 |
| rows exported by the cursor walk | 431 |
| distinct `local_id` | 431 |
| duplicate rows | 0 |
| malformed rows (wrong field count) | 0 |
| categories sum to denominator | True |
| every task classified exactly once | True |

**The denominator is volatile and that is itself a finding.** Measured across this session: 596 → 605 → **431** nonterminal, with IN_PROGRESS collapsing 199 → 25 in roughly 25 minutes as a closure sweep ran. Any census is a snapshot; this one is bound to the instant above.

## Category counts

Mutually exclusive, precedence-ordered (first match wins), so the columns add up.

| category | count | share |
|---|---:|---:|
| active | 14 | 3.2% |
| ready | 231 | 53.6% |
| blocked | 80 | 18.6% |
| implemented-awaiting-land | 81 | 18.8% |
| owner-decision | 10 | 2.3% |
| stale-premise | 5 | 1.2% |
| subsumed/duplicate | 8 | 1.9% |
| orphaned | 2 | 0.5% |
| **total** | **431** | 100% |

Precedence: `malformed` → `implemented-awaiting-land` → `stale-premise` → `subsumed/duplicate` → `owner-decision` → `blocked` → `active` → `orphaned`/`obsolete` → `ready`. A task with an open blocker *and* an `implemented` tag counts as implemented-awaiting-land, not blocked.

## P0 and P1 inventory

### P0 — 145 tasks

| category | count |
|---|---:|
| blocked | 45 |
| ready | 39 |
| implemented-awaiting-land | 38 |
| active | 13 |
| owner-decision | 4 |
| subsumed/duplicate | 4 |
| stale-premise | 2 |

**Actionable now (52):**

- `ci_main_red_groundtruth` [ready] i=95 — ci-main-red-groundtruth-and-dag-refactor
- `scorecard-carry-full-provenance` [ready] i=95 — Scorecard rows carry the full comparison provenance
- `determinism_stress_order_violation` [active] i=92 — determinism-stress/order-violation fails at distinct=1 and pins newest-green 27 commits behind 
- `pr_drain_hermit_coord` [ready] i=85 — pr-drain-hermit-coord: claim #1558 #1570 #1534 #1547 #1299 (validate@head via systemd-run, --re
- `hermit_reverie_pin_bump` [ready] i=80 — hermit reverie-pin bump to 55f6876a (post #369+#221)
- `refresh_hermit_and_parent` [ready] i=55 — Refresh Hermit and parent pins to current Reverie main
- `agent-utils-own-ci-configured-and-green` [ready] i=50 — P0: agent-utils must have its OWN CI configured and GREEN — it's the enforcement mechanism behi
- `capture-immediate-pre-tightening-scorecard-baseline` [active] i=50 — Capture an immediate pre-tightening compatibility scorecard baseline on current main
- `demo5-fix-revert-config-flip-0591104` [ready] i=50 — P0: revert the 0591104 demo5 config flip (re-arm --max-timeslice in demos/05-qemu-boot.py) → re
- `determinism_stress_order_violation_3` [ready] i=50 — determinism-stress-order-violation-chaos-red-on-main
- `every-agent-shepherds-its-own-pr-to-landing` [ready] i=50 — P0 `process/shepherd-own-pr`: STALENESS is the protocol breach, not count — and every agent she
- `fanout-land-green-free-to-land` [ready] i=50 — P0: land the ~51 GREEN free-to-land PRs NOW (low-hanging fruit — 0 landed in 40min while agents
- `green-reset-after-landing-sprint-recompute-envelope-on-bitwise` [ready] i=50 — P0 (AFTER LANDING SPRINT — owner 2026-08-04): RESET WHAT GREEN MEANS. Recompute the ENTIRE comp
- `guard-heavy-fbsource-buck-runs-from-hostwide-io-stall` [active] i=50 — Guard heavy fbsource Buck runs from recreating the host-wide Btrfs I/O stall
- `health-timer-alarm-must-bind-latest-invocation` [active] i=50 — Bind health-timer liveness alarm to the latest durable invocation
- `impl-dbi-debug-simple-coreutils` [ready] i=50 — P0: DBI debug — why do simple coreutils (ls, cat, echo, head) fail under DBI?
- `land-clusters-as-they-ripen-not-all-or-nothing` [ready] i=50 — OWNER PRINCIPLE: LAND A CLUSTER THE MOMENT IT IS HEALTHY, then form the next — do NOT hold heal
- `land-e9patch-chain-to-remove-ptrace-downgrade` [ready] i=50 — The #283 ptracer-out-of-path gate is UNMET and blocked on ONE landing chain: reverie #377 -> pi
- `landing-throughput-ceiling-serialized-validate` [ready] i=50 — P0: LANDING is the bottleneck, not implementation — 70 implemented-unlanded tasks against a ~6-
- `ledger-records-reds-without-distinguishing-flake-from-defect` [ready] i=50 — P0 `ci/false-red-durability`: the validate ledger records a FAILED with no way to distinguish D
- `maintain-dev-hermit-main-freshness-during-release` [active] i=50 — Keep dev-hermit main fetched, pushed, and ancestry-verified throughout the 0.3 sprint
- `measure-red-pr-flip-rate-on-new-main` [ready] i=50 — P0: measure how many of the 172 CI-failing PRs flip GREEN when rebased onto the new main (which
- `memory-caps-cover-ten-nodes-not-the-class` [ready] i=50 — P0 `boxing/oom-class`: #1583 fixed memory caps on TEN pinned-job nodes — `test.hermit_integrati
- `merge-gate-trusts-label-presence-not-the-ledger` [ready] i=50 — P0 `fake-green/merge-gate-label-leg`: merge-gate.yml:392-393 — the SOLE required check on main 
- `milestone-hermit-02-stable-and-done` [ready] i=50 — MILESTONE GATE: hermit 0.2 is stable and DONE — release actually cut, past the CI/PR backlog
- `owner-ask-lands-same-turn-or-it-is-lost` [ready] i=50 — P0 (OWNER) `process/consolidate-gains`: owner asks get LOST between filing and landing. 20 owne
- `owner-decision-zero-ptracer-requires-reverie-core-abstraction-changes` [ready] i=50 — OWNER DECISION `patching/zero-ptracer-scope`: the path to no-ptrace is NOT a pure additive exte
- `per-node-private-cargo-target-dirs-to-kill-lock-contention` [ready] i=50 — SUPERSEDED by `dag/one-fat-build` — private target dirs work around a lock that should not exis
- `permission-sweep-misses-busy-agents-sabre-lost-65-minutes` [ready] i=50 — P0 `orc/permission-sweep`: an agent sat on an approval prompt for 65 MINUTES because the sweep 
- `phase1-hermit-standalone-build` [ready] i=50 — P2 (gated): hermit/ builds standalone (make/cargo build on lone checkout) + remove optional sub
- …and 22 more

### P1 — 149 tasks

| category | count |
|---|---:|
| ready | 81 |
| implemented-awaiting-land | 34 |
| blocked | 21 |
| owner-decision | 5 |
| subsumed/duplicate | 4 |
| stale-premise | 3 |
| active | 1 |

**Actionable now (82):**

- `owner_decision_clear_the` [ready] i=90 — ANSWERED BY OWNER RULE: rebase the 23 pre-anchor passers — 'never test any PR without rebasing 
- `p1_resume_qemu_linux` [ready] i=90 — P1: Resume QEMU Linux R/R at zero-read and mprotect divergences
- `p1_land_reproducible_strict` [ready] i=85 — P1: Land reproducible strict-L2 sched_ext QEMU coverage
- `rf-prctl-dumpable-virtualize` [ready] i=85 — Fix-forward: PR_SET_DUMPABLE=0 physical passthrough breaks ptrace timer creation (perf_event_op
- `rf-procfs-shared-access-mediation` [ready] i=85 — Fix: procfs/sysfs ProcfsFile mediation is bypassable (pread64/readv/preadv/lseek + relative/ali
- `rf-backend-bypass-rr-dbi` [ready] i=82 — Fix: reclassified-Determinized syscalls bypass fixed handlers via DBI copied-children and recor
- `p1_retest_qemu_snapshot` [ready] i=80 — P1: Retest QEMU snapshot resume under strict after the seccomp fix
- `rf-procfs-semantic-coherence` [ready] i=80 — Fix: procfs virtual files are semantically incoherent (host topology preserved w/ zeroed counte
- `p1_add_a_durable` [ready] i=75 — P1: Add a durable strict-L2 Linux userspace workload fixture
- `rf-prctl-timerslack-virtualize` [ready] i=75 — Fix: PR_SET_TIMERSLACK physically passed through -- alters real wake latency/timeout ordering; 
- `scorecard_csv_producer_must` [ready] i=75 — Scorecard CSV producer must quote multiline reason fields
- `rf-getsockopt-optlen-overlap` [ready] i=72 — Fix: getsockopt TCP_INFO canonicalization corrupts returned optlen when optval/optlen overlap (
- `rf-netlink-autobind-determinism` [ready] i=72 — Fix: Netlink autobind (implicit sendto/sendmsg) bypasses ID rewriting; port collision vs INET e
- `rf-ns-inode-identity-aliases` [ready] i=72 — Fix: namespace inode identity still observable via readlinkat/O_PATH/procfd aliases (#877)
- `rf-socket-timestamp-ioctl` [ready] i=72 — Fix: SIOCGSTAMP* handler advances time per-query and ignores clock mode (#912)
- `a_plain_git_reset` [ready] i=70 — A plain 'git reset HEAD~1' on shared parent main silently deletes concurrent agents' commits, a
- `hosted_ubuntu_latest_lane` [ready] i=70 — Hosted ubuntu-latest lane is the single point of failure for ALL landing - owner decision neede
- `liteinst_detlog_heap_and` [active] i=70 — liteinst --detlog-heap and --detlog-stack hashes are nondeterministic RUN-TO-RUN at an identica
- `p1_correct_pmu_overshoot` [ready] i=70 — P1: Correct PMU overshoot telemetry and map QEMU chaos timeslices
- `replace_reverie_ptrace_provenance` [ready] i=70 — Replace Reverie ptrace provenance error-string fallback with typed classification
- `rf-ioprio-semantics` [ready] i=70 — Fix: ioprio_get ignores who (no ESRCH) and ioprio_set is a no-op that cannot round-trip (#881)
- `rf-thread-self-open-time-binding` [ready] i=70 — Fix: /proc/thread-self/{stat,status} rewrites reader TID, not opener TID (fd passed across thre
- `rf-softirqs-ci-and-determinism` [ready] i=68 — Fix: /proc/softirqs -- hosted CI needs absent /usr/bin/lsirq; digit-width determinism (9->10) &
- `rf-file-mutation-readback` [ready] i=65 — Fix-forward: file_mutation fixture overclaims write parity -- never reads file content back (#9
- `rf-key-users-todo-breadcrumbs` [ready] i=60 — Fix: /proc/key-users -- bind TODO-HUMAN-REVIEW(PR-TBD) breadcrumbs to PR-951 + aliases/bypass/C
- `liteinst_flip_cli_to` [ready] i=55 — liteinst-flip-cli-to-inguest-toolhost
- `agent-utils-rust-script-by-default` [ready] i=50 — P1: move agent-utils toward rust-script by DEFAULT so a stale compiled binary cannot silently w
- `append-only-investigation-history-coordinator` [ready] i=50 — P1: coordinator-managed APPEND-ONLY machine-readable investigation history — serialize incremen
- `arch-cross-process-child-tool-admission` [ready] i=50 — P1: Cross-process forked-child Tool/scheduler admission — keystone for race.sh parity across AL
- `ci-hub-local-ci-history-store` [ready] i=50 — P1: ci-hub local store — ingest full GitHub Actions history incrementally + idempotently into a
- …and 52 more

## Review candidates — keyword-flagged, NOT confirmed

Read-only census: **nothing was closed or demoted.**

**These are NOT closure evidence, and I verified that before publishing them as such.**
The `stale-premise` and `subsumed/duplicate` categories are keyword matches over note
text, and spot-checking two of them showed both were false positives:

  actually reads *"The malformed **notes** are superseded by corrected plain-text notes."*
  That is about the notes, not the task. This task is also **release-0.3 protected**, so
  proposing it for closure is precisely the harm the task's constraint guards against.
- `drain_dbi_backend_parity` matched on premise language that turns out to describe a
  *partially* refuted premise about three specific PRs, not a dead task.

So treat every row below as **"a human should read this note"**, not as a closure
recommendation. A note is one agent's unverified belief; a keyword inside it is weaker
still. Protected `release-0.3` tasks and anything IN_PROGRESS are excluded from the lists
below even when flagged.

### stale-premise — 5 listed

- `epic-backend-supremacy` [BACKLOG P1] — EPIC: Backend supremacy — a non-ptrace backend substantially beats ptrace on bui
  - evidence: a note records the premise as stale/refuted; notes=5, open blockers=0, last modified 2026-08-06
- `staging-hermit-33-free-merges` [BACKLOG P0] — P0 `drain/staging-hermit`: build hermit staging from the 33 FREE merges first — 
  - evidence: a note records the premise as stale/refuted; notes=27, open blockers=0, last modified 2026-08-06
- `validate-dag-runner-p0-umbrella` [BACKLOG P0] — P0 (OWNER-DECLARED): validate fully on safe-ci-dag-runner, parallelism maximized
  - evidence: a note records the premise as stale/refuted; notes=13, open blockers=0, last modified 2026-08-06
- `validate_sh_retry_classifier` [BACKLOG P1] — validate.sh retry classifier: tail|sed|grep -q SIGPIPE/pipefail false-negative m
  - evidence: a note records the premise as stale/refuted; notes=3, open blockers=0, last modified 2026-08-06
- `vision-sabre-hybrid-backend` [BACKLOG P1] — VISION: SaBRe/BI hybrid backend — in-guest syscall hooking + ptrace fallback
  - evidence: a note records the premise as stale/refuted; notes=1, open blockers=0, last modified 2026-08-07

### subsumed/duplicate — 7 listed (1 excluded as release-0.3-protected or IN_PROGRESS)

- `demo5-fix-vtime-skew-poller-livelock` [BACKLOG P1] — P1 (OWNER-GATED): scheduler vtime-jump for the vtime-skew poller-livelock (demo5
  - evidence: a note says duplicate-of / superseded-by / subsumed-by; notes=1, open blockers=0, last modified 2026-08-01
- `drain_dbi_backend_parity` [OPEN P0] — drain-dbi-backend-parity-identity-family
  - evidence: a note says duplicate-of / superseded-by / subsumed-by; notes=5, open blockers=0, last modified 2026-08-07
- `e9patch-corpus-salvage-not-wholesale-graft` [BACKLOG P1] — P1: salvage the SUBSTANTIVE e9patch corpus tests (roundtrips/state/errno/identit
  - evidence: a note says duplicate-of / superseded-by / subsumed-by; notes=1, open blockers=0, last modified 2026-08-03
- `full-validate-green-proof-with-1520-1521` [BACKLOG P0] — P0: produce a READABLE end-to-end GREEN validate log with #1520 + #1521 applied 
  - evidence: a note says duplicate-of / superseded-by / subsumed-by; notes=8, open blockers=1, last modified 2026-08-06
- `mass-parallel-rebase-stale-base` [BACKLOG P0] — P0: mass-parallel rebase of stale-base red PRs — split into small batches, shard
  - evidence: a note says duplicate-of / superseded-by / subsumed-by; notes=65, open blockers=0, last modified 2026-08-03
- `nightly_stress_red_triage` [OPEN P1] — nightly-stress-red-triage
  - evidence: a note says duplicate-of / superseded-by / subsumed-by; notes=7, open blockers=1, last modified 2026-08-07
- `validate_4_open_hermit` [BACKLOG P1] — Validate 4 open hermit PRs SOLO via producer path
  - evidence: a note says duplicate-of / superseded-by / subsumed-by; notes=20, open blockers=0, last modified 2026-08-06

### orphaned — 2 listed

- `dev_hermit_parent` [OPEN P2] — Dev Hermit Parent
  - evidence: no notes, blocks nothing, untouched 17d; notes=0, open blockers=0, last modified 2026-07-21
- `reverie_backends` [OPEN P2] — Reverie Backends
  - evidence: no notes, blocks nothing, untouched 17d; notes=0, open blockers=0, last modified 2026-07-21

## Reprioritization candidates

**81 implemented-awaiting-land.** These are the largest block of non-actionable work; each is complete but unlanded, so they inflate the open count without being schedulable. They clear by landing, not by planning.

**80 blocked** by at least one non-closed blocker. Top blockers by how many nonterminal tasks they gate:

| blocker | gates |
|---|---:|
| `certify-post-tightening-scorecard-postcard` | 20 |
| `rebase-and-relevance-recheck-inflight-work` | 9 |
| `ci-hub-local-ci-history-store` | 7 |
| `milestone-hermit-02-stable-and-done` | 6 |
| `resync-upper-team-main-and-agent-utils` | 5 |
| `release-0.3-acceptance-contract` | 4 |
| `milestone-m10-multi-process` | 4 |
| `measure-post-tightening-compat-envelope-drop` | 4 |
| `land-scorecard-tier-correction` | 4 |
| `verify-team-machine-ledger-end-to-end` | 3 |

## Protected, per the task's own constraint

`release-0.3` work: **20** nonterminal tasks, left untouched and not proposed for closure.

- `coordinate-release-0.3-overnight-critical-path` [IN_PROGRESS P0 subsumed/duplicate] — Coordinate the Hermit 0.3 release critical path and milestone reporting
- `release-0.3-acceptance-contract` [IN_PROGRESS P0 active] — Define executable Hermit 0.3 release acceptance gates
- `release-0.3-backend-rename-split` [OPEN P0 blocked] — Complete DBI→DBT rename and release backend packaging split
- `release-0.3-docs-help-cleanup` [OPEN P0 blocked] — Polish Hermit README, docs, help output, and repository cleanliness
- `release-0.3-fbsource-hermit` [OPEN P0 blocked] — Create second fbsource import diff: Hermit 0.3
- `release-0.3-fbsource-parity` [OPEN P0 blocked] — Compare fbsource Buck-built Hermit with external Cargo-built 0.3
- `release-0.3-fbsource-rehearsal-hermit-diff` [OPEN P0 blocked] — Create the fbsource Hermit rehearsal import diff stacked on Reverie
- `release-0.3-fbsource-rehearsal-parity` [OPEN P0 blocked] — Compare rehearsal fbsource Buck-built Hermit with external Cargo-built source pa
- `release-0.3-fbsource-rehearsal-reverie-diff` [OPEN P0 blocked] — Create the fbsource Reverie rehearsal import diff
- `release-0.3-fbsource-rehearsal-runbook` [OPEN P0 blocked] — Publish a repeatable runbook from the successful fbsource import rehearsal
- `release-0.3-fbsource-rehearsal-source` [OPEN P0 blocked] — Select and freeze a reasonably-clean main source pair for fbsource import rehear
- `release-0.3-fbsource-reverie` [OPEN P0 blocked] — Create first fbsource import diff: Reverie first-party code

## Caveat that limits one axis

`tasks.owner` is **not usable** as a signal: `tg claim` does not reliably populate it. Measured at an earlier snapshot, 170 of 192 IN_PROGRESS tasks (88.5%) had no owner, and two of this agent's own three claimed tasks read empty while the third read `orc`. No category above keys on owner.

