# Hermit CI build-profile consumer map (DEBUG vs RELEASE)

Anchors: `HERMIT_BIN="$ROOT_DIR/target/debug/hermit"` (`validate.sh:733`) and
`DEFAULT_STRICT_COMPAT_HERMIT_BIN="$ROOT_DIR/target/release/hermit"`, exposed as the overridable
`STRICT_COMPAT_HERMIT_BIN` (`validate.sh:736-738`). Evidence gathered at hermit SHA
`b384187efd725c504d69281f043d442325d4fcb2`.

| Consumer (job / check / DAG node) | Profile | Evidence (file:line) | Why this profile / why it can't standardize |
|---|---|---|---|
| Bulk unit/integration suite: `test.hermit_unit`, `test.detcore_unit`, `test.detcore_misc`, `test.detcore_parallel`, `test.regular_crates`, `test.hermit_integration`, `test.cli`, `test.arbitrary_binaries`, `test.hermit_modes`, `test.app_strict_verify`, `test.command_strict_verify`, `test.ignored_syscall_regressions`, `doc.doctests`, `lint.clippy` | debug | `ci/dag/portable.json` (built from `build.workspace` = `cargo build --workspace`); `ci-portable.yml:263-295` single `build-debug` job | `cargo test`/`nextest` targets linked from the default dev build; no separate release compile. Debug tree built once (`ci-portable.yml:14-17`) and fanned to every shard. Moving to release = full release workspace compile + loss of dev-profile debug-assertions/overflow-checks (the latter is Cargo default behavior, not explicitly commented in-repo). |
| ptrace run/verify/record-replay smoke + envelope probes: `hermit_run_smoke`, `hermit_verify_smoke`, `hermit_record_replay_smoke`, `_envelope_level`, rr smoke | debug | `validate.sh:1573,1621,1634,1649,3488,3534,3765` (`$HERMIT_BIN`) | Fast-iteration path; default debug binary. |
| `test.envelope_levels` (L1–L4 over true/echo/date) | debug | `ci/dag/portable.json` node cmd hard-codes `HERMIT=target/debug/hermit` | Inlined mirror of `run_portable_envelope_levels`, pinned to debug. |
| E2E manifest buckets (portable **and** privileged), incl. `backend-parity-c` (KVM/DBI/SaBRe via manifest) | debug | `ci/test_harness.sh:15` `HERMIT_BIN=${HERMIT_BIN:-$ROOT_DIR/target/debug/hermit}`; DAG `e2e.manifest_*` nodes | e2e harness defaults to the debug binary; run against the debug prebuilt tree. |
| Privileged KVM/PMU/CPUID smoke (`build.privileged_tests`, `cpuid.faulting`, `pmu.preemption`) | debug | `ci/dag/privileged.json` (`cargo build ... --bin hermit`, no `--release`) | Hardware-capability smoke; built debug in-place. |
| Occasional (non-gating) KVM probes | debug | `ci-privileged.yml:106` `env HERMIT_BIN="$PWD/target/debug/hermit"` | Load-sensitive KVM apps run against debug hermit. |
| `test.strict_compat` (portable DAG lane) | **debug** (overridden) | `ci-dag.yml:27`, `validation-levels.yml:41` set `STRICT_COMPAT_HERMIT_BIN=.../target/debug/hermit`; `ci/dag/README.md:118-123` | Corpus reuses whatever `STRICT_COMPAT_HERMIT_BIN` points at; CI overrides to the already-built debug binary to skip a redundant release compile. Profile-agnostic. |
| `test.dbi_parity` (DynamoRIO parity matrix, CI) | release | `ci/dag/portable.json`: `run_matrix.py --hermit target/release/hermit`; paired `build.dbi_release` (`--release ... -p detcore-dbi`) | Paired with the release DBI runtime. **Not** intrinsically release-only: `Makefile:203` runs the same matrix on `target/debug/hermit`. CI uses release only to match the single release backend build. |
| `test.sabre_examples` (CI) | release | `ci/dag/portable.json`: `HERMIT_SABRE_TEST_BINARY=$PWD/target/release/hermit`; paired `build.sabre_release` | Paired with the release SaBRe plugin (`cargo build --release -p detcore-sabre`). |
| `test.liteinst_strict` (CI) | release | `ci/dag/portable.json`: `HERMIT_LITEINST_TEST_BINARY=$PWD/target/release/hermit`; paired `build.liteinst_runtime_release` | Paired with the release `libreverie_liteinst.so` (`stage-liteinst-runtime.sh release`). |
| `--rr-compat-only` (privileged merge-gate) | release | `validation-levels.yml:141`; builds release at `validate.sh:4244-4245` | Standalone gate defaults `STRICT_COMPAT_HERMIT_BIN` to release (`validate.sh:736`); corpus itself profile-agnostic. |
| Standalone compat gates: `--strict-compat-only`, `--sabre-compat-only`, `--liteinst-compat-only`, `--e9patch-compat-only`, `--qemu-l2-only` (Makefile `validate-*`; manual) | release | `validate.sh:4180-4181,4195-4196,4216-4217,4231-4232,4244-4245,4258-4259`; `Makefile:206,209,212` | Each builds `cargo build --release -p hermit ...` because the default strict-compat binary is release (`validate.sh:736`). Overridable via `STRICT_COMPAT_HERMIT_BIN`. |
| `super` weekly stress suite | release (+ debug workspace) | `validate.sh:4118-4119` (debug workspace, then release hermit) | Builds both; release used for compat/heavy probes. |
| `run_full_backend_gates` / `Makefile validate-kvm`, `validate-dbi` (local) | debug | `validate.sh:1677`; `Makefile:12,199,203` `HERMIT_DEBUG_BIN=target/debug/hermit` | Local backend-parity uses debug — direct proof the parity matrix is not release-locked. |
| Demo P0 gate (`demo-hot-path.yml`) | (external, undetermined) | `demo-hot-path.yml:290` runs `scripts/super-validate.sh --demos-only` in a separate `dev-hermit-demo-suite` checkout not present in this repo | Profile not determinable from the hermit checkout — stated as a limitation. |
| `docs.yml`, `runner-health.yml`, `ci-portable-autoretry.yml`, `merge-gate.yml` | none / n/a | grep of each file | Build no hermit binary (rustdoc deploy; runner health; re-dispatch/label orchestration). |

## The one hard constraint

**The `hermit` binary must be the same profile as the third-party backend `.so` it loads.** That is
why RELEASE exists in CI at all: the DBI/SaBRe/LiteInst runtimes are built and staged **only** in
release (dedicated one-shot `build.*_release` nodes), and their parity/compat tests point `hermit` at
`target/release/hermit` to match. Everything else is CI choosing to build debug **once** for the test
matrix and release **once** for the backend runtimes — not a profile any test intrinsically requires.
`strict_compat`, `rr`, `e9patch`, and `dbi_parity` are demonstrably profile-agnostic (they run debug
in the DAG lane and release standalone via the same overridable variable).

Investigated by a subagent sweep over `validate.sh`, `.github/workflows/*.yml`, `ci/dag/*.json`,
`ci/test_harness.sh`, and `Makefile` (biggrep/search_files is not wired to this local checkout; used
ripgrep/read directly).
