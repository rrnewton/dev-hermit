# Breach table: portable CI DAG vs the 1-core / 1-GiB / 10-s-CPU default cap

**Task:** `enable-cgroups-and-cpu-timeouts-across-dag-nodes` (P0). Produce, by
*measurement* (never fabrication), which portable-DAG nodes exceed the proposed
undeclared-node default cap **1 core / 1 GiB / 10 s CPU**, and by how much, so
per-node declarations can be derived as
`cpu_timeout = round(max(cpu_s) * 1.5)` when `n >= 5` samples, else `UNSET`.
**`UNSET` is a valid, correct answer; a fabricated constant is a hard failure.**

## Method

- Hermit `main` @ `1cea8a6fbaa7a1fd9c08e105eaad7590edd841d1`.
- Runner: `safe-ci-dag-runner 0.11.0` (agent-utils `main` @ `1c0e9c3`), boxing
  ACTIVE (two-level cgroup-v2 `systemd --user` scope; per-step `cpu.stat` +
  `peak_bytes` + wall). Verified this build applies **no** default per-step cap
  (`sizing.rs:16` returns `None` for undeclared nodes), so observed peaks are
  TRUE, not cap-truncated.
- 5 sequential boxed runs: `ci/run-dag.sh portable -j 8 --perf-dir <run> --keep-going`.
  Driver: `ignored/breach-table-231b/run-breach-measure.sh`; raw per-run logs +
  `step_profiles_*.csv` under `ignored/breach-table-231b/run-0{1..5}/`.
- Aggregation: `ignored/breach-table-231b/aggregate-breach.py`. `cpu_s = user_s +
  sys_s`. **Only VALID samples count** (`ok` true, `oom_kills == 0`, not
  `timed_out`); OOM-killed / timed-out / failed rows are excluded from the
  derivation.
- Host: `AMD EPYC 9D85` (158-core), agent role `3pai_audit`.

## Environment caveats (why many nodes are UNSET here)

This agent environment is hostile to a clean full-DAG sweep. These are
measurement-substrate artifacts, **not** properties of the nodes:

1. **BpfJailer (`3pai_audit`) blocks `cmake` FILE_OPEN** on the DynamoRIO cold
   build, so any node that cold-builds all crates (`build.dbi_release`,
   `test.hermit_integration`) fails cold. DBI is a non-default cargo feature, so
   the default `build.workspace` + test/e2e nodes are unaffected.
2. **Cold-build OOM thundering herd:** run 1 cold-compiles `detcore`/`reverie` in
   parallel across `build`/`doc`/`clippy` → per-node `MemoryMax` OOM-kills
   `cc1plus`/`clippy-driver` (`doc.rustdoc`, `lint.clippy` fail cold, pass warm).
3. **DAG selection / heavy-node scheduling:** the `e2e.manifest_*` buckets ran
   inconsistently across runs (only `determinism_stress` and `language_runtimes`
   produced real work; the rest are empty under `--ci-only --allow-empty`). The
   heavy buckets got `n < 5` valid samples → `UNSET`.
4. **`test.rr_suite_contract`** hit the 300 s wall-clock gate → `TIMEOUT` →
   excluded.

**The robust source for the still-`UNSET` nodes is CI perflog history**
(GitHub-hosted portable-DAG runs: no BpfJailer, warm-vs-cold spread, many
samples), accumulated into a ci-hub store and consumed by PR #1547's derivation
pipeline. This local run bootstraps and cross-validates that.

## Result — three buckets

### A. Fits under the 10 s CPU default → NO per-node declaration needed
(derived `round(max(cpu_s)*1.5) < 10`; the aggressive default already covers them)

| node | n | max cpu_s | max mem | max cores |
|------|---|-----------|---------|-----------|
| check.backend_abstraction | 5 | 0.0 | 0.01G | 0.42 |
| setup.nextest | 5 | 0.0 | 0.01G | 0.45 |
| check.script_sigpipe | 5 | 0.1 | 0.04G | 0.91 |
| lint.rustfmt | 5 | 1.0 | 0.04G | 0.95 |
| build.flaky_harnesses | 5 | 0.8 | 0.14G | 0.76 |
| check.portability_paths | 5 | 2.2 | 0.10G | 1.06 |

### B. Needs an explicit `cpu_timeout` (derived >= 10 s, n >= 5) — MEASURED DECLARATIONS

| node | n | max cpu_s | max mem | max cores | max wall | breaches | **cpu_timeout** |
|------|---|-----------|---------|-----------|----------|----------|-----------------|
| build.manifest_guests | 5 | 13.8 | 0.18G | 1.04 | 71.9 | CPU,CORES | **21** |
| doc.doctests | 5 | 14.0 | 1.23G | 0.98 | 14.2 | MEM,CPU | **21** |
| e2e.metadata | 5 | 21.2 | 0.42G | 1.63 | 13.0 | CPU,CORES | **32** |
| test.regular_crates | 5 | 52.5 | 2.11G | 1.10 | 272.7 | MEM,CPU,CORES | **79** |
| test.detcore_unit | 5 | 85.2 | 1.86G | 0.28 | 299.2 | MEM,CPU | **128** |
| build.workspace | 5 | 918.7 | 8.00G | 16.32 | 56.3 | MEM,CPU,CORES | **1378** |

Caveat: `build.workspace` / `build.manifest_guests` max is anchored on the single
COLD sample (runs 2–5 are cache-warm ~0); the `×1.5` margin is thin for a slower
or more-loaded cold build. Conservative-max is the intended posture, but re-derive
from CI history before locking these two.

### C. UNSET — TRUE breachers that need clean sampling (do NOT flip default yet)

| node | n | why UNSET / observed (contaminated) |
|------|---|-------------------------------------|
| build.dbi_release | 4 | BpfJailer cmake block cold; warm: cpu 892.5s, 8GiB, 14.1 cores |
| build.sabre_release | 3 | cold OOM/fail; warm: cpu 189.8s, 1.63GiB, 4.2 cores |
| build.liteinst_runtime_release | 3 | cold OOM/fail; warm: cpu 237.4s, 2.9GiB, 9.6 cores |
| doc.rustdoc | 4 | OOM cold; warm: cpu 34.9s, 5GiB, 2.9 cores |
| lint.clippy | 4 | OOM cold; warm: cpu 41.4s, 1.27GiB |
| e2e.manifest_determinism_stress | 2 | selection: cpu 16.7s, wall 302.9s (cpu << wall, scheduling-bound) |
| e2e.manifest_language_runtimes | 1 | selection: cpu 43.4s, wall 82.6s |
| test.hermit_integration | 0 | BpfJailer + OOM every run (all samples invalid) |
| test.rr_suite_contract | 0 | 300 s wall-clock TIMEOUT every run |
| e2e.manifest_{applications,backend_parity_c,bin_c,c_programs,chaos_c,data_handling,debugger_c,determinism_stress_c,shared_futex_c,system_utils,util_c} | 0 | empty under `--ci-only` here / not sampled |

## Interpretation

- Only **6 nodes** need an explicit `cpu_timeout` above the 10 s default from the
  clean data (bucket B), and **6 nodes** fit comfortably under it (bucket A).
- The bucket-C set contains the biggest real breachers (release builds at
  4–16 cores / 2–8 GiB, DBI/SaBRe/LiteInst, and the heavy e2e buckets). They are
  precisely why the migration order is **land declarations FIRST, flip the default
  LAST** — flipping the 1-core/1-GiB/10-s default now would kill these nodes.
- Memory: several nodes exceed 1 GiB (`build.workspace` 8 GiB, `doc.rustdoc` 5 GiB,
  release builds 1.6–2.9 GiB, `test.*` 1.8–2.1 GiB). The 1 GiB default is a real
  forcing function for builds — they must declare higher `hard_mem_max_bytes`.
- Effective cores: builds run 4–16 cores; the 1-core default is a forcing function
  that build nodes must explicitly override.

See `metadata.json` for exact SHAs/commands and `breach-table.json` for the raw
per-node aggregate.
