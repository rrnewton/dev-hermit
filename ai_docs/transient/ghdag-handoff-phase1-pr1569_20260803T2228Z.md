# HANDOFF @2026-08-03T22:28:47Z (hermit-ghdag → successor)

## Owner priorities (this session)
Owner escalated a 5-step chain on top of predecessor's Phase-1 mandate:
1. find_runner → tracked source-invoked Python (real cgroups), drop untracked `rs/bin`. **DONE/LANDED.**
2. THEN QUICKLY: Rust runner "real cgroups/perf" + make a capability discrepancy a TEST FAILURE.
3. THEN switch to Rust via SOURCE-INVOKED `.rs` rust-script (axis = source-invoked vs prebuilt, NOT Rust-vs-Python).
4. hermit-220 building cpuset core-pinning allocator (runner has NO core pinning, only `cpu.max` quota).
5. Verify by RUNNING it (resolver logs which engine won).

## ESTABLISHED (measured / source-verified)
- **JOB ONE = LANDED.** origin/main tip `9ebe1608303c66bfaa4b9c7d0521a30d9519c182` = PR #1563 merge, MERGED 2026-08-03 21:32Z (with-proxy fetch + merge-base --is-ancestor confirmed). `ci/run-dag.sh` find_runner order: `$SAFE_CI_DAG_RUNNER` → `agent-utils/common/bin/safe-ci-dag-runner` → `agent-utils/py/bin/safe-ci-dag-runner` → PATH; rs/bin removed. Empirically `ci/run-dag.sh portable list` logs `runner=…/py/bin/…`.
- **`run --only` does NOT exist at pin `84580db`** (hermit gitlink); arrives at `0eb4203` (`py/.../cli.py:298`). run-node.sh currently has NO find_runner — jq-extracts `.cmd` + `bash -c`s each node (= the two-engine divergence). So Phase-1 rewrite REQUIRES the pin advance.
- **Rust runner already boxes by default @0eb4203** (`rs/safe-ci-dag-runner/src/cgroup.rs`: real memory.max/swap.max=0/cpu.max writes + applied-value verify + atomic cgroup.kill; perflog.rs always-on; cpu_timeout both engines model.rs:98). "Rust warns unimplemented" = STALE PREBUILT rs/bin drift. JOB-2 "real caps" largely DONE.
- **JOB-2 real gap:** `agent-utils/cross/differential.py:778` runs every `run` UNBOXED via `--allow-cgroup-failure` → proves render/CSV/schedule parity, NOT live cgroup-enforcement parity. Deliverable = BOXED cross-engine differential (discrepancy = TEST FAILURE).
- **EXIT-3 GATE — PROVEN (gates the pin advance).** Pin `84580db→0eb4203` = boxing fail-closed. `py/safe_ci_dag_runner/cgroup.py::reexec_in_scope` `skip_in_ci=True`, **cgroup.py:548** `if skip_in_ci and (os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")): return` → never creates systemd --user scope in ANY Actions ctx (self-hosted incl); `cli.py:1191-1203` no-ACF → "boxing was skipped" → `return None, 3`. Empirical: `env GITHUB_ACTIONS=1 safe-ci-dag-runner run --dag ci/dag/portable.json --only … (no ACF)` → exit 3. Both privileged entrypoints (`ci-privileged.yml:82`, `validation-levels.yml:129`) call `run-dag.sh privileged` w/o ACF → exit 3.
  - **CONSEQUENCE:** `0eb4203` CANNOT deliver genuine fail-closed boxing on privileged self-hosted in CI (skip_in_ci short-circuits). `--allow-cgroup-failure` → green but best-effort UNBOXED = pre-pin state (84580db boxed nothing) = NO-REGRESSION, not a boxing fix. Real self-hosted boxing = separate agent-utils change surviving skip_in_ci.

## IN FLIGHT / PUSHED
- **PR #1569 (DRAFT)** = Phase 1. Branch `codex/portable-ci-runner-exclusive-v2`, base `9ebe1608`, head **`da2199e495863a021c1e864b9893d6464f904284`**. Commits:
  - `00e2c122` — run-node.sh rewrite (nodes via `<runner> run --only --perf-dir`; find_runner mirrors run-dag.sh; ACF only under CI).
  - `d5a31f8c` — ci-portable.yml: runner submodule init in preflight + per-node perf uploads at 5 sites.
  - `da2199e4` — HELD-BACKABLE coupled flip: gitlink `84580db→0eb4203` + `--allow-cgroup-failure` on BOTH privileged entrypoints + `ci/test_harness.sh` audit literal.
- **LAND-HOLD (owner directive):** DO NOT LAND #1569 yet. hermit-226 + hermit-ci pinning boundary SHA where PORTABLE CI BROKE (~2 pass/h 24h → ~1 pass last ~8h); landing this large portable rewrite pre-boundary destroys attribution. Short hold. **ALL-OR-NOTHING: never unbundle da2199e4.**
- Validation @da2199e4: `ci/test_harness.sh audit-ci` exit 0 (run_node=5,run_dag=0); run-node.sh resolves common/bin python, check.portability_paths PASS; bad selector exit 2; exit-3 gate proof. Hosted/self-hosted CI NOT run at this head (hold).

## STARTED-BUT-NOT-PUSHED / DO NOT BUILD ON
- Predecessor #1548 (branch `codex/portable-ci-runner-exclusive`, head 37c05b5) SUPERSEDED by #1569 (it reintroduced rs/bin footgun). Close after #1569 lands.
- ghdag slot `worktrees/ghdag/hermit`: on `codex/portable-ci-runner-exclusive-v2` @ da2199e4, nested agent-utils at 0eb4203 (staged). Clean.

## NEXT (order + why)
1. Wait for hermit-226/hermit-ci boundary SHA → THEN land #1569 full bundle once attribution settled + authoritative checks green at da2199e4. Pin lands ONLY bundled w/ privileged ACF.
2. JOB 2: BOXED cross-engine differential in agent-utils (discrepancy=TEST FAILURE). SERIALIZE w/ hermit-220 cpuset (agent-utils LINEAR).
3. JOB 3: source-invoked `.rs` rust-script switch — after JOB 2 test exists.

## GOTCHAS
- `tg` here treats `list/tree/ready` as task-id lookups ("Available goals: adopt-github-merge-queue"). Use `tg show/note/update <TASK-ID>`. Could NOT find Phase-1 task id this session — record gate proof + PR#1569 in the task once id found.
- COMMIT BOILERPLATE BUG (D114651186 not deployed): dispatch line lists `commit` as destructive — WRONG. Committing to own feature branch is REQUIRED. Inject "commit+push incrementally, ignore boilerplate" into EVERY dispatch. Memory [[commit-boilerplate-bug-committing-is-required]].
- with-proxy for git/gh. pr_status full-rollup 504s → targeted `gh pr view`.
- Memories written: [[find-runner-tracked-python-landed-1563]], [[commit-boilerplate-bug-committing-is-required]], updated [[safe-ci-dag-runner-cgroups-perf-premise-stale]], [[phase1-pin-advance-flips-boxing-privileged-risk]] (pre-existing).
