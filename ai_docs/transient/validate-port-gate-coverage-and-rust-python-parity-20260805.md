# validate.sh → Rust port: gate-coverage table + Rust-vs-Python parity (2026-08-05, hermit-coord)

Evidence base: `hermit/validate.sh` @ `fc0b76adc` (4522 lines), `hermit/ci/dag/{portable,privileged}.json`,
`hermit/ci/run-dag.sh`, `agent-utils/common/bin/engine-resolver`, `agent-utils/rs/safe-ci-dag-runner/`,
and PR #1586 `scripts/validate.rs` (Phase-1 library wrapper). This is the SUBSUMPTION checklist the owner
mandated: a port that silently drops any row below is a fake green.

## A. How validate.sh composes gates (the real structure)

Main dispatch (validate.sh L4514-4517 + compat/level branches):

| Profile / mode | Suite fn | Gates it records |
|---|---|---|
| (preamble, ALWAYS) | main | `initialize_repository_submodules` (L4368) + `validate_reverie_pin_consistency` (L4371) |
| `quick` | run_quick_suite L4226 | 8 DIRECT-command gates (NOT DAG): Build workspace; Portable E2E metadata; Portable ptrace E2E verification; Detcore core unit tests; Hermit run smoke; Hermit output determinism; Hermit verify-mode smoke; Hermit record/replay smoke |
| `portable-only` | run_portable_only_suite L4022 | manifest gate + `portable CI DAG lane` (2) |
| `full` | run_full_suite L4238 | portable manifest + portable DAG lane + privileged manifest + privileged DAG lane (4) + 2 preamble = **6 gates** |
| `super` | run_super_suite L4342 | stress gates L4346-4362 (build workspace+release, super_stress_suite, hermit_modes default_, stress_suite ×2, chaos_buck_, leveldb, sqlite) |
| `--selective`/`--since-green` | run_selective_suite L4082 | manifest + `portable CI DAG (selective subset)` via RUN_DAG_FILE_OVERRIDE; FAIL-SAFE to full portable on any doubt |
| `--strict`/`--portable-strict-compat-only` | run_strict_compatibility_envelope L3714 | **BARE, outside run_check** — `exit $?`, no ledger gate/summary (ASYMMETRY) |
| `--sabre-compat-only` | run_sabre_compatibility_envelope L3726 | SaBRe compatibility ratchet (212 programs), INSIDE run_check |
| `--e9patch-compat-only` | run_e9patch_compatibility_envelope L3771 | e9patch ratchet (155), INSIDE run_check |
| `--rr-compat-only` | run_rr_compatibility_envelope L3794 | rr ratchet (139), INSIDE run_check |
| `--liteinst-compat-only` | (liteinst branch) | liteinst ratchet |
| `--qemu-l2-only` / `--envelope` | envelope levels | measurement, not a pass/fail gate |

**KEY:** the landing-relevant `full` profile is ENTIRELY DAG-driven. `run_ci_manifest_lane <lane>` (L4013) =
`./ci/test_harness.sh validate` (manifest gate) + `./ci/run-dag.sh <lane>` (the DAG lane). The DAG lane
internally runs **portable=45 steps, privileged=7 steps** (`ci/dag/*.json`, keys group/job/desc/cmd/timeout/hint).

## B. What `./ci/run-dag.sh` actually is (the parity crux)

`ci/run-dag.sh` (121 lines) is a THIN wrapper: resolves a `safe-ci-dag-runner` via the engine-resolver, then
`exec "$runner" run --dag "$dag" "$@"` (L121). It does NOT itself run the DAG. The engine-resolver
(`agent-utils/common/bin/engine-resolver`) chooses the engine:

- **`ENGINE="${SAFE_CI_DAG_RUNNER_ENGINE:-python}"` (L39) — DEFAULT IS PYTHON.**
- Python: `exec python3 py/bin/safe-ci-dag-runner` → `safe_ci_dag_runner/__main__.py`.
- Rust: opt-in ONLY via `SAFE_CI_DAG_RUNNER_ENGINE=rust`; requires a built `rs/bin/safe-ci-dag-runner`
  (`./setup rs`); **NEVER silently falls back to Python** (refuses, exits) and logs which engine won on
  every invocation. Header rationale: guards against "a Rust runner missing an enforcement guard" silently
  running — i.e. they do not yet trust Rust to have full boxing/timeout enforcement parity.

`./ci/test_harness.sh validate` (manifest gate) = `cargo run -p hermit-manifest-plan -- --format harness-json`
+ jq inventory checks (test_harness.sh L129/L211/L1204).

## C. Rust-vs-Python parity verdict

**The Rust DAG runner has NOT reached parity-as-default.** Concretely:
1. **Default engine is Python.** Every ordinary `validate.sh` full run today executes the Python engine
   (`safe_ci_dag_runner/__main__.py`). Rust is gated behind `SAFE_CI_DAG_RUNNER_ENGINE=rust` + a manual build.
2. **No differential/parity test** compares Rust vs Python output on the same DAG. The Rust crate's tests
   (`rs/safe-ci-dag-runner/tests/`: boxing_smoke, core_box_smoke, cpu_timeout_smoke, default_cap_smoke,
   unsafe_no_cgroups_smoke) verify the RUST engine's own enforcement guards in isolation — none asserts
   "Rust result == Python result" on a shared lane. Parity is asserted structurally, not measured.
3. **Enforcement-guard parity is the stated risk.** The resolver defaults to Python precisely to avoid a
   Rust engine that lacks a guard (cgroup boxing / cpu-wall timeout / admission) running silently. Until a
   guard-for-guard audit + a differential run on portable+privileged exists, Rust≠trusted-equivalent.

**What the Rust engine DOES have (library, source-verified):** `run_dag_boxed_ordered` (scheduler.rs:881)
runs a DAG boxed (systemd-run --user --scope cgroup), live-by-default emit() (▶/✓/✗), fail-closed exit 3 if
cgroup unestablished. So the leaf-execution + boxing + liveness half is present; the unproven half is
DifferenceVsPython on real lanes + the enforcement-guard-for-guard audit.

## D. Port coverage map — what #1586 (Rust library) covers vs the remaining surface

| validate.sh element | Ported in #1586 validate.rs? | Gap |
|---|---|---|
| DAG lane execution (one lane) | YES (`run_dag_boxed_ordered`) | — |
| Boxing / live progress / fail-closed cgroup | YES (library) | — |
| Multi-lane composition (full = portable+privileged) | NO | runs ONE lane per process |
| Preamble: submodule init + Reverie pin consistency | NO | not invoked |
| Manifest gate (`test_harness.sh validate`) | NO | still shells out |
| quick suite (8 direct gates) | NO | not ported |
| compat-only modes (strict/sabre/e9patch/rr/liteinst) | NO | not ported; strict has bare-exit asymmetry |
| super stress suite | NO | not ported |
| --selective / --since-green subset | NO | not ported |
| TREE-keyed result cache-hit skip | NO | not ported |
| dirty-tree hard gate | NO | not ported |
| Ledger schema-4 counted (executed_tests ~700+) | NO — writes fail-closed schema-3 node counts | deliberate; see landmine |
| locally-validated label mint | NO | not ported |

**Landmine (do not regress):** #1586 writes schema:3 executed_nodes/skipped_nodes, NO executed_tests/log_file,
so no consumer mistakes a DAG-lane run for a full-TEST pass. The port's counted ledger MUST emit true libtest
executed_tests (~760-783 for full), never node counts under that name, and if it adds `log_file`, first make
`finalize_receipt.py --scan` skip `producer=="validate.rs"` (else a DAG-lane log launders into a schema-5 receipt).

## E. Strategic consequence + recommended next step

Porting validate.sh to the safe-ci-dag-runner LIBRARY commits the DAG execution to the RUST engine in-process
(#1586 calls the crate directly, bypassing run-dag.sh AND the engine-resolver). Therefore the SUBSUMPTION review
must ALSO discharge the Rust≡Python enforcement-parity debt the resolver currently hedges by defaulting to
Python. Two workstreams, in order:

1. **Parity gate (prerequisite):** add a differential harness — run portable+privileged under both engines
   (`SAFE_CI_DAG_RUNNER_ENGINE=python` vs `=rust`) on the same commit, assert identical pass/fail set + per-node
   outcomes + enforced boxing/timeout. Land that as the evidence the Rust engine is trustworthy as default.
2. **Orchestration port:** on top of the library, implement (in order of landing value) full multi-lane
   composition → preamble → manifest gate → counted schema-4 ledger + label + cache/dirty-gate → compat modes
   → super/selective. Drive each row of section A/D to green with a test that the gate fires and is counted.

Home/branch: the primary `hermit` checkout is currently 16 behind origin/main AND shows the bare-repo anomaly
(`git status` → "must be run in a work tree"), so it is NOT a safe port surface as-is. Recommend: extend PR
#1586's branch (it already has the library wrapper + fail-closed ledger) rather than start fresh on the stale
primary; reconcile the primary to main separately.
