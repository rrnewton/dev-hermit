# orc-coord-014 — first cross-team TaskGraph -> GitHub surfacing sweep

**Date:** 2026-08-06 · **Team:** `orc-coord-014` · **TaskGraph task:** `surface-substantial-taskgraph-work-to-github-now`

Purpose: make substantial, large-effort, long-standing dev-hermit TaskGraph work visible on the shared GitHub
coordination surface so the two other active agent teams can claim and land it. Product code was not modified.

## Scope of the sweep

Queried the **full** taskgraph via `tg sql` (not a truncated ready view): **OPEN=40, BACKLOG=322 (362 candidates)**,
IN_PROGRESS=151, CLOSED=4054. Dedup corpus: **rrnewton/hermit 367 issues** (359 open, ~250 auto-generated
`Syscall classification: X` stubs) and **rrnewton/reverie 18 issues** (17 open), titles **and bodies** fetched locally.

### Destination constraint (structural, not a judgement call)

`./.orc/plugins/hermit-dev/gh-issue-create` allowlists **only** `rrnewton/hermit` and `rrnewton/reverie`; it hard-rejects
anything else. Harness-local work — ci-hub, validate/drain/landing tactics, ORC/fleet, worktree registry, agent-utils,
PR-specific validate/land tasks — therefore has **no approved GitHub destination** and was excluded by construction.
The existing backlog task `extend-ghissue-wrapper-liteinst2` tracks the `rrnewton/liteinst2` gap.

### Dedup method — and a failed method worth recording

`gh search issues --repo A --repo B <query>` **silently ignored the query string**: eleven different queries all
returned the identical newest-8 list. Concluding 'no duplicate' from that would have been a proxy with no binding to
the claim. Dedup was redone against the locally-fetched title+body corpus, where the match is observable.

## A. Migrated — new GitHub issue created, local description reduced to a pointer

| TaskGraph ID | P | GitHub | Title |
|---|---|---|---|
| `unified-in-guest-patching-backend` | P0 | [rrnewton/hermit#1783](https://github.com/rrnewton/hermit/issues/1783) | Architecture gate: unify sabre/e9patch/liteinst onto ONE in-guest Detcore subscriber with ZERO ptracer on the syscall path |
| `arch-cross-process-child-tool-admission` | P1 | [rrnewton/hermit#1784](https://github.com/rrnewton/hermit/issues/1784) | Cross-process forked-child Tool/scheduler admission — keystone for race.sh parity across ALL non-ptrace backends |
| `liteinst-flagship-acceleration` | P1 | [rrnewton/hermit#1785](https://github.com/rrnewton/hermit/issues/1785) | LiteInst flagship path: in-guest Tool dispatch + multiproc + B3/B4 corpus ratchet (pure-Rust, first-party, no third-party deps) |
| `liteinst-multiproc-and-inguest-flagship` | P1 | [rrnewton/hermit#1785](https://github.com/rrnewton/hermit/issues/1785) | LiteInst flagship path: in-guest Tool dispatch + multiproc + B3/B4 corpus ratchet (pure-Rust, first-party, no third-party deps) |
| `epic-backend-supremacy` | P1 | [rrnewton/hermit#1786](https://github.com/rrnewton/hermit/issues/1786) | EPIC: backend supremacy — a non-ptrace backend substantially beats ptrace on real build jobs |
| `research-arm-backend` | P3 | [rrnewton/hermit#1787](https://github.com/rrnewton/hermit/issues/1787) | Research: ARM/AArch64 backend support — survey prior art and propose the most feasible path |
| `scx-scheduler-for-hermit-threads` | P2 | [rrnewton/hermit#1788](https://github.com/rrnewton/hermit/issues/1788) | Dedicated SCX scheduler for hermit threads: make the guest-yield -> coordinator-run handoff immediate and same-core |
| `bpf-scx-native-hermit-backend` | P3 | [rrnewton/hermit#1789](https://github.com/rrnewton/hermit/issues/1789) | Roadmap: a BPF+SCX hermit backend that PLACES guest threads rather than requesting placement |
| `goal-qemu-linux-under-hermit` | P0 | [rrnewton/hermit#1790](https://github.com/rrnewton/hermit/issues/1790) | MILESTONE: run a full Linux VM under QEMU deterministically with Hermit (--strict + sched_ext + --verify) |
| `p1_resume_qemu_linux` | P1 | [rrnewton/hermit#1791](https://github.com/rrnewton/hermit/issues/1791) | QEMU Linux record/replay: resume at the zero-length read and mprotect length divergences |
| `rf-procfs-semantic-coherence` | P1 | [rrnewton/hermit#1792](https://github.com/rrnewton/hermit/issues/1792) | procfs virtual files are semantically incoherent: host topology with zeroed counters, fail-open parsers, paired-file inconsistency, over-zeroing |
| `rf-backend-bypass-rr-dbi` | P1 | [rrnewton/hermit#1793](https://github.com/rrnewton/hermit/issues/1793) | Reclassified-Determinized syscalls bypass fixed handlers via DBI copied children and record/replay subscriptions (host syscall executes) |
| `epic-drb-replay-asplos20` | P1 | [rrnewton/hermit#1794](https://github.com/rrnewton/hermit/issues/1794) | EPIC: reproduce the ASPLOS'20 Debian Reproducible Builds case study under Hermit (dettrace -> hermit) |
| `epic-drb-modern-frontier` | P1 | [rrnewton/hermit#1795](https://github.com/rrnewton/hermit/issues/1795) | EPIC: close the MODERN Debian Reproducible Builds frontier to 100% with Hermit |
| `epic-nix-reprobuild` | P2 | [rrnewton/hermit#1796](https://github.com/rrnewton/hermit/issues/1796) | EPIC: deterministic-by-construction Nix builder with Hermit hooked into the build step |
| `realistic-deterministic-syscall-timing-model` | P3 | [rrnewton/hermit#1797](https://github.com/rrnewton/hermit/issues/1797) | Long-term: replace the fixed NANOS_PER_SCHED tick with deterministically-seeded draws from empirically-profiled syscall/context-switch cost distributions |
| `hermit-strict-parallel-build-perf` | P1 | [rrnewton/hermit#1798](https://github.com/rrnewton/hermit/issues/1798) | Determinism vs parallelism: --strict sequentialization does not scale to real parallel package builds (nftables >23min on a 316-core host) |
| `research-parallelism-plan` | P2 | [rrnewton/hermit#1798](https://github.com/rrnewton/hermit/issues/1798) | Determinism vs parallelism: --strict sequentialization does not scale to real parallel package builds (nftables >23min on a 316-core host) |
| `fix-load-dependent-scheduling-vtime` | P1 | [rrnewton/hermit#1799](https://github.com/rrnewton/hermit/issues/1799) | --strict makes a LOAD-DEPENDENT number of scheduling decisions for timer/wall-clock-driven workloads, so --verify breaks under host load |
| `logical-child-reap-model` | P1 | [rrnewton/hermit#1800](https://github.com/rrnewton/hermit/issues/1800) | Design decision: logical-child-reap model for deterministic wait4/waitid under blocking servers (redis/HTTP starvation) |
| `interactive-tty-shell-support` | P2 | [rrnewton/hermit#1801](https://github.com/rrnewton/hermit/issues/1801) | Interactive deterministic shell with TTY support (hermit run -it): fix the unkillable `run bash` hang, PTY/signal forwarding, deterministic stdin replay |
| `vision-reverse-debugging` | P3 | [rrnewton/hermit#1802](https://github.com/rrnewton/hermit/issues/1802) | VISION: replay-based reverse stepping / time-travel debugging (reverse-step and reverse-continue in GDB/LLDB) |
| `impl-semantic-versioning` | P1 | [rrnewton/hermit#1803](https://github.com/rrnewton/hermit/issues/1803) | Adopt consistent semantic versioning across ALL hermit and reverie crates |
| `hermit-run-initial-crates-release` | P2 | [rrnewton/hermit#1804](https://github.com/rrnewton/hermit/issues/1804) | Tight, stable initial hermit-run crates.io release (a real 0.1.0, unbundled-backend model) |
| `epic-test-powerweight` | P1 | [rrnewton/hermit#1805](https://github.com/rrnewton/hermit/issues/1805) | EPIC: coverage-guided test power-to-weight optimization across the whole suite, slowest-first |
| `vision-nightly-stress-testing` | P3 | [rrnewton/hermit#1806](https://github.com/rrnewton/hermit/issues/1806) | VISION: nightly stress testing — random seeds + super-validate + regression tracking + auto-filed bugs |
| `owner-decision-zero-ptracer-requires-reverie-core-abstraction-changes` | P0 | [rrnewton/reverie#392](https://github.com/rrnewton/reverie/issues/392) | OWNER DECISION: the path to zero-ptracer is not additive — it changes Reverie core-abstraction categories 1-3 |
| `vision-reverie-backend-repo-split` | P0 | [rrnewton/reverie#393](https://github.com/rrnewton/reverie/issues/393) | VISION: split Reverie backends by dependency weight and remove optional submodules so hermit builds standalone |

**28 TaskGraph tasks -> 26 new issues** (two pairs were consolidated onto one issue each:
`liteinst-flagship-acceleration` + `liteinst-multiproc-and-inguest-flagship` -> hermit#1785;
`hermit-strict-parallel-build-perf` + `research-parallelism-plan` -> hermit#1798).

New issues: **rrnewton/hermit #1783-#1806** (24) and **rrnewton/reverie #392-#393** (2).
All carry labels `orc-coord` + `help wanted` and reproduce the original description, tags, graph edges, and
accumulated task notes verbatim, under an explicit provenance banner stating the content was NOT re-verified
against current `main` during migration.

## B. Reused — pre-existing GitHub issue already covered the scope; NO duplicate created

For these the local description was **prepended** with a pointer and **kept**, because the GitHub issue does not
contain the local text verbatim — replacing it would have destroyed local detail rather than relocating it.
An `[orc-coord-014]` comment naming the covered TaskGraph IDs was posted on each target issue.

| TaskGraph ID | P | GitHub | Why reused |
|---|---|---|---|
| `milestone-code-coverage-measurement` | P2 | [rrnewton/hermit#1557](https://github.com/rrnewton/hermit/issues/1557) | same two measurement axes + CI admission gate |
| `quantify-test-coverage-value-syscall-flags-and-rust-loc` | P1 | [rrnewton/hermit#1557](https://github.com/rrnewton/hermit/issues/1557) | same two measurement axes + CI admission gate |
| `rf-procfs-shared-access-mediation` | P1 | [rrnewton/hermit#973](https://github.com/rrnewton/hermit/issues/973) | exact restatement of #973 sections 1-3 |
| `research-backend-maturity-audit` | P1 | [rrnewton/hermit#1087](https://github.com/rrnewton/hermit/issues/1087) | downstream application of the B0-B4 maturity model |
| `vision-backend-parity` | P0 | [rrnewton/hermit#1087](https://github.com/rrnewton/hermit/issues/1087) | downstream application of the B0-B4 maturity model |
| `impl-backend-wip-banners` | P1 | [rrnewton/hermit#1087](https://github.com/rrnewton/hermit/issues/1087) | downstream application of the B0-B4 maturity model |
| `impl-backend-maturity-warnings` | P2 | [rrnewton/hermit#1087](https://github.com/rrnewton/hermit/issues/1087) | downstream application of the B0-B4 maturity model |
| `impl-backend-maturity-gh-issues` | P1 | [rrnewton/hermit#1087](https://github.com/rrnewton/hermit/issues/1087) | downstream application of the B0-B4 maturity model |
| `impl-dbi-wip-warning` | P1 | [rrnewton/hermit#1087](https://github.com/rrnewton/hermit/issues/1087) | downstream application of the B0-B4 maturity model |
| `vision-kvm-backend` | P0 | [rrnewton/hermit#198](https://github.com/rrnewton/hermit/issues/198) | maps onto #198 'What a real KVM backend needs' |
| `impl-kvm-final-push` | P1 | [rrnewton/hermit#198](https://github.com/rrnewton/hermit/issues/198) | maps onto #198 'What a real KVM backend needs' |
| `hb-impl-config-json-yaml-dsl` | P1 | [rrnewton/hermit#1146](https://github.com/rrnewton/hermit/issues/1146) | implementation sub-scope of the happens-before RFC |
| `hb-impl-candidate-introspection` | P1 | [rrnewton/hermit#1146](https://github.com/rrnewton/hermit/issues/1146) | implementation sub-scope of the happens-before RFC |
| `vision-strict-compat-envelope-to-100` | P1 | [rrnewton/hermit#1745](https://github.com/rrnewton/hermit/issues/1745) | depends on the verifier/parity standard #1745 defines |
| `impl-dbi-verify-honest` | P1 | [rrnewton/hermit#1745](https://github.com/rrnewton/hermit/issues/1745) | depends on the verifier/parity standard #1745 defines |
| `impl-dbi-global-state-fix` | P0 | [rrnewton/reverie#118](https://github.com/rrnewton/reverie/issues/118) | implementation of the GlobalState/RPC gap #118 documents |
| `impl-dbi-cross-process-rpc` | P0 | [rrnewton/reverie#118](https://github.com/rrnewton/reverie/issues/118) | implementation of the GlobalState/RPC gap #118 documents |
| `impl-dbi-implement-uds-ipc` | P0 | [rrnewton/reverie#118](https://github.com/rrnewton/reverie/issues/118) | implementation of the GlobalState/RPC gap #118 documents |

**18 TaskGraph tasks -> 7 existing issues.** Zero duplicate issues created.

## C. Deliberately NOT surfaced — stale premise

| TaskGraph ID | Why not filed |
|---|---|
| `vision-sabre-hybrid-backend` | Milestones M1-M5 describe a SaBRe backend that now exists (`hermit-cli/src/sabre_ptrace.rs`); the 2026-08-04 zero-ptracer decision records SaBRe as already CONFORMING. Filing verbatim would publish a stale ask. Live SaBRe scope is covered by hermit#1783 / reverie#392. |
| `impl-e9patch-real-backend` | Asks to 'create reverie-e9patch' and fill 'currently no-op' trampolines; its own notes record reverie#103 landed the backend, since advanced to HybridPtrace (reverie#377 / hermit#1638). Live e9patch scope is covered by hermit#1783 and hermit#1715. |

Both calls rest on the tasks' own notes and the decision/frontier records, **not** on fresh measurement at current
`main`; both are annotated UNVERIFIED in their TaskGraph notes. Each task got an explanatory note; neither was closed.

## Excluded categories (and why)

- **Harness-local** (ci-hub, validate, drain/staging/landing, ORC/fleet, worktree registry, agent-utils, parent gitlinks): no approved GitHub destination — see the wrapper allowlist above.

- **Tactical/ephemeral** PR-lifecycle tasks (`validate PR #N at head`, `land <PR list>`, per-PR review-fix stubs): real work, but not durable claimable scope for another team.

- **Trivial stubs**: the four `e2e_*` determinism-test tasks carry impact 3 and empty descriptions; nothing to migrate.

- **~250 `Syscall classification: X` GitHub stubs** already exist for the syscall-coverage family; no TaskGraph task needed re-filing there.

## Reversibility

- `tg` snapshot `orc-coord-014-pre-github-sweep` taken before any description was rewritten (`tg restore`).
- All 46 original descriptions (51,041 chars) backed up verbatim to
  `ai_docs/orc-coord-014-tg-description-backup-20260806.json` (uncommitted; the parent is shared and dirty with other agents' work).

## Follow-ups this sweep did not do

- `impl-backend-maturity-gh-issues` asks for per-backend Backend Maturity Tracker issues. Not created: the tracker set should be derived from a real audit (`research-backend-maturity-audit`) against the #1087 gates, not from estimates.
- No local task was closed. Closure remains coordinator-only through the verified gateway.
