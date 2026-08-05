# HANDOFF @2026-08-05T04:14:36Z (hermit-coord)

**HANDOFF-FINAL: READ THIS FILE — complete and current. (Recreated after a shared-index
commit race deleted the first copy; see [[parent-shared-index-commit-race]].)**

Two workstreams: (1) THE PORT (validate.sh -> Rust, owner P1); (2) root-cleanliness tick.
Plus resolved fast-fail-reds investigation.

---

## WORKSTREAM 1 — THE PORT (owner P1: validate.sh -> Rust on safe-ci-dag-runner AS A LIBRARY, delete the shell script; review gate = SUBSUMPTION over ~36 gates x all profiles). `validate-harness-detection-refuse-bare-in-dev-hermit` is dependency-wired BEHIND this port.

### STATE: discovery COMPLETE, no port code written. Design/decision open.

### ESTABLISHED (source-verified):
- **validate.sh** = `hermit/validate.sh` @ main-then `fc0b76adc`, **4522 lines, 38 `run_check` sites** (~36 live; some dead).
- **#1586** (`codex/validate-rust-script-thin-wrapper`, `scripts/validate.rs`, 774 lines) is a thin Phase-1 wrapper, NOT the port: runs ONE DAG lane via `run_dag_boxed_ordered`. 4 open adversarial blockers, unlanded.
- **Library** = `agent-utils/rs/safe-ci-dag-runner/`. API: `run_dag_boxed_ordered(cfg,jobs,keep_going,verbosity,cgroups,order:Option<Vec<String>>,core_budget:Option<i64>) -> RunResult` at `src/scheduler.rs:881`. Types `src/model.rs`: DagConfig(297) Step(85) StepOutcome(355) RunResult(448). DAG via `io::dag_from_json` (`src/io.rs:272`, STRICT parse). Live-by-default `emit()`=println! `scheduler.rs:140` (START 478/PASS 824/FAIL 829). No `--allow-cgroup-failure` => exit 3 fail-closed if cgroup unestablished (the "bare validate.sh exits 3 in ~9s in sandbox" cause). cgroup::reexec_in_scope re-execs under `systemd-run --user --scope`.
- **DAG manifests are JSON, NOT TOML**: `hermit/ci/dag/{portable,privileged}.json` (~44 / 8 nodes). TRAP: nodes carry a `manifest:{lane,category}` field the model doesn't parse (ignored at step level; confirm before reusing strict parser).
- **Leaf gates largely already IN the DAG.** Missing port surface: (a) level/multi-lane composition (full=portable+privileged, super=20x stress), (b) all compat-only/focused modes (--strict/portable-strict/rr/sabre/e9patch/liteinst-compat-only, --privileged-only, --qemu-l2-only, --envelope, --only <lane> <nodes>, --selective/--since-green), (c) lifecycle glue: TREE-keyed cache-hit skip, dirty-tree hard gate, counted/full-coverage ledger, PR receipt/label minting.

### Full gate x profile matrix in resumable subagent `aa4f87473ecfefb27` (the subsumption checklist). Highlights/TRAPS:
- `full` records 6 gates (preamble x2: Initialize submodules + Reverie pin consistency; portable manifest+lane; privileged manifest+lane).
- TRAP ASYMMETRY: strict-compat-only runs `run_strict_compatibility_envelope` BARE (outside run_check, no ledger gate, `exit $?`); sabre/e9patch/rr run INSIDE run_check. Preserve.
- DEAD (don't port): `run_full_backend_gates`(L2008, "Real backend compatibility matrix" L2023, no call site), start_check/wait_for_background_checks, check_copyright_headers, run_portable_envelope_levels, run_privileged_envelope_record_replay, run_hermit_targets_serial. `backend_selector_supported` used L2412/2419 but UNDEFINED — verify origin (else super KVM/DBI probes silently skip via 127).
- LANDMINE constants (rebase hotspots): STRICT_COMPAT_TOTAL=191(L920), RR_COMPAT_EXPECTED=139(L929, must==#RR_COMPAT_PASSING_LABELS or parse-time exit 2), SABRE_COMPAT_EXPECTED=207/SABRE_COMPAT_TOTAL=212(L933/936, 212 also in gate NAME), E9PATCH_COMPAT_TOTAL=155(L937), VALIDATION_GATES_EXPECTED_JSON(L643 hardcoded 5 — decoupled from real ~6, cache-soundness landmine).
- Ledger `append_validation_ledger`(L1212) schema_version:4; counts from `nonzero_result.py --ledger-fields` (needs DEV_HERMIT_PARENT); pass iff exit==0 && failures==0. EXIT trap `cleanup`(L1433) publishes `locally-validated` only if pass&&full&&anchored&&!dirty via `ci-hub apply-local-label`.

### LEDGER/RECEIPT CONTRACT the port MUST preserve:
- **NEW LANDING PREDICATE (owner):** pass AND profile=full AND checks=6 AND executed_tests plausible (~700+). Check count alone INSUFFICIENT.
- **executed_tests is THE discriminator.** Real full runs ~760-783. tests<=1/null => NO-RESULT, NOT FAILED. Port must emit true libtest executed_tests, never node counts under that name (#1586 blocker #1).
- #1586 writes schema:3, executed_nodes/skipped_nodes, full_coverage:false, NO executed_tests/log_file (fail-closed). schema-5 counted receipt minted separately by `finalize_receipt.py --scan` from durable LOG. Phase-2 landmine: adding log_file to validate.rs rows would let `_is_countless_clean_full_pass` launder a DAG-lane log into a receipt — make scan skip producer=="validate.rs" first. See [[validate-rs-failclosed-node-counts-and-log_file-phase2-landmine]], [[validate-rs-1586-live-by-default-blocked-on-ledger-counts]].

### OPEN DECISION (surface to owner): extend #1586 vs fresh full-port; home = hermit `scripts/validate.rs` vs new agent-utils lib. Owner: "AS A LIBRARY", may work on the main hermit checkout owned, but `~/work/dev-hermit/hermit` must stay on main — announce any branch switch in the task note. agent-utils changes go direct-to-main serialized (NOT a casual pin bump).

### NEXT: decide branch/home with owner, then port the ORCHESTRATION LAYER (level/mode dispatch + lifecycle glue) on top of the existing DAG engine, driving the subsumption matrix (subagent aa4f87...) as checklist.

---

## WORKSTREAM 2 — ROOT CLEANLINESS TICK: RECONCILED THIS SESSION.
- On entry: parent DIVERGED 9<->1. HEAD f1d9354 was a **patch-id duplicate** of origin `c9d26d3` (already landed). ~30 M files (bulk mtime 2026-08-04 19:04:43) were **STALE-TREE inversions**: working tree MISSING origin content (primary_checkout.py missing 91 lines / check_pins; Makefile missing install-hooks; ledger.json missing 15 lines). Committing them would REVERT merged PRs (documented main-break) — see [[root-dirtiness-often-reset-mixed-stale-tree-inversion]].
- ACTION: tagged recovery refs `recovery/root-stale-tree-20260805` (stash-create 6fbba26, tracked WT changes) + `recovery/root-committed-dup-f1d9354`, then `git reset --hard origin/main` -> HEAD e71a3c0, 0<->0. Nothing lost (all recoverable).
- HAZARD HIT: while reconciling, ANOTHER agent committed `5ca88b6` (SIGSYS spec, +326) and `git reset --hard` deleted my staged-but-uncommitted handoffs from disk. [[parent-shared-index-commit-race]]. Recreated this file. TRAP: hermit pin-drift PRE-COMMIT HOOK blocks parent-only ai_docs commits when hermit primary is on an in-flight branch — use `HERMIT_PIN_DRIFT_OVERRIDE=1 git commit` for parent-only changes.
- DELIBERATELY NOT COMMITTED: `compat-envelope/{absolute-oracles.csv,test-render-scorecard.sh}` (another workstream's product-tooling dir, not owner's ai_docs/experiments safe-set, possibly half-finished); `pr_sweep.{err,out}` (transient command-output capture — belongs gitignored). Submodule bumps REPORT-ONLY: hermit +fc0b76ad (demo-20260804 branch), reverie +8688189 (feat/inguest-toolhost-counter-seam). NOTE both hermit & reverie primaries are OFF main — Primary Checkout Invariant concern, separate op, likely other agents' in-flight state; not acted on.

---

## WORKSTREAM 3 — FAST-FAIL REDS (RESOLVED by owner):
- Five short 6chk reds (245/92/106/160/35s) are NO-RESULTS (executed_tests 1/null), from 11-15 concurrent validates livelocking pre-execution. Discriminator = executed_tests (~700+ real).
- GENUINE (do not sweep): `71bc3856` PR#1622 fail 6chk 403s tests=765 (real SaBRe-examples timeout). Gates PR#1147. Its land-1622 checkout is hermit-dbi's — DO NOT TOUCH.
- `54b4d4e5` 245s tests=430 = partial, needs a look. `a96d9f5c pass 6chk 452s` proved machinery+main healthy. HALT: single designated producer until concurrency genuinely enforced.

## IN FLIGHT / dangling:
- Idle worktree `ignored/validate-producer/1549/hermit` (detached b3df5441, clean, NEVER launched) — release or reuse.
- Resumable discovery subagents: `aa4f87473ecfefb27` (gatexprofile matrix), `a85628e15f017f8a4` (validate.rs + dag-runner API).
- Recovery refs `recovery/root-stale-tree-20260805`, `recovery/root-committed-dup-f1d9354` can be deleted once confidence is high the stale tree held nothing wanted.
