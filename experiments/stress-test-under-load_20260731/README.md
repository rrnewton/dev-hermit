# Load-independence guardrail (stress-test-under-load-harness)

**Question.** Hermit's promise is that its decisions — the schedule it picks, the
`--verify` verdict, and the exact output bytes — are a pure function of the guest,
*independent of host load*. If concurrency pressure on the host ever changes any
of those, that is a load-induced determinism failure and is **P0**. This harness
is the standing guardrail that tries to provoke exactly that.

**Key idea: the load *is* the test.** There is **no synthetic load** (no
`stress-ng`, no busy spinners). We run the Hermit determinism tests *themselves*
×N concurrently through a bounded worker pool. The parallel contention between the
reps is the load. Then we compare, across identical reps, the guest output hash,
the `--verify` verdict, and the schedule fingerprint. Any cross-rep divergence of
an otherwise-deterministic test = load-induced decision change = **P0**.

## The three-layer stack

```
safe-ci-dag-runner  (SINGLETON DAG: outer cgroup MEM cap + per-step profiling)
  └─ bounded worker pool of parallel `hermit run --strict --verify` reps  (harness.py)
       └─ cross-rep output-hash / verify / schedule-fingerprint divergence  == P0
```

1. **Safety + profiling — `guarded_run.py`.** A pool of hundreds of concurrent
   `hermit` processes can OOM the host. Before any rep runs, `guarded_run.py`
   re-execs itself into a transient `systemd-run --user --scope` with
   `Delegate=yes -p MemorySwapMax=0 -p MemoryMax=<cap>`, then **verifies the cap
   actually reached cgroup v2** (`verify_scope_limits`). This is No-Silent-Failure:
   if the scope or cap can't be established/verified, it **refuses to run** (exit 3)
   rather than run advisory-only. The whole descendant tree (setsid escapees
   included) is reaped on exit. The run is wrapped in a **singleton DAG** so
   safe-ci-dag-runner supplies per-step + whole-run resource CSVs (the profiling
   output) and outer-scope peak-mem / OOM accounting. The cap deliberately sets
   **no CPU quota** — CPU oversubscription is the load we want.

2. **Bounded worker pool — `harness.py`.** A pool sized between `Nprocs` and
   `2×Nprocs` pulls rep jobs from a queue; the pool bound *is* the concurrency cap.
   Two modes:
   - `reps` — fixed N reps × each test (Phase 1: validate the engine + hash-diff).
   - `timed` — a time-bounded **fair hot loop**: with constant CPU oversubscription
     (pool > Nprocs), tests take turns roughly fairly, so a fast test runs many
     hundreds of times while a slow one runs ~10× in the same window. Per-test run
     counts + any divergence are tracked. **The 1-hour torture run is this mode.**

3. **Per-rep oracle (2 hermit invocations).** For each rep:
   - `hermit run --strict -- CMD` → `sha256(guest stdout)` = **output_hash**.
   - `hermit run --strict --verify -- CMD` → stderr `"Determinism verified"` =
     **verify_pass**, plus the `Logs contain A | B <label>` count lines =
     **schedule fingerprint** (count_fp).

## Severity model (`harness._classify`)

| verdict | meaning |
|---|---|
| `GREEN` | all reps agree: 1 output hash, verify passes, 1 count fingerprint |
| `P0_OUTPUT_DIVERGENCE` | >1 distinct output hash across reps — load changed the bytes |
| `P0_VERIFY_FLIP` | `--verify` passes in some reps, fails in others |
| `P0_SCHEDULE_FP_DIVERGENCE` | >1 distinct schedule fingerprint across reps |
| `PREEXISTING_VERIFY_FAIL` | verify fails *consistently* — a real bug, but not load-induced (not P0 by this harness) |
| `INCONCLUSIVE` | too few reps scored (e.g. all timed out) |

Timeouts are recorded as a **perf note, never a P0 on their own** — a slow rep
under load is expected; only a *changed decision* is P0.

## Workload suite

Frozen deterministic Hermit examples (from `hermit/examples/`): `date` (bash),
`devrand` (bash), `rand` (python3), `progressbar` (python3 timed-progress-bar),
`pipeline` (`echo … | gzip | gunzip | sha256sum`). `race.sh` is **excluded**
(intentionally chaotic — not a determinism oracle).

## Reproduction

```bash
cd experiments/stress-test-under-load_20260731
./run.sh smoke      # reps  mode: 5 tests ×2, pool 8, 8G cap  — engine sanity (~3 min)
./run.sh validate   # reps  mode: pool=Nprocs, reps=10, 64G cap  — Phase 1 at scale
./run.sh torture    # timed mode: 60 min oversubscribed pool, 64G cap  — Phase 2 (HELD)
```

Classifier self-test (no hermit, no load — pure unit check of the P0 detector):

```bash
python3 test_classify.py    # 26 asserts over harness._classify / _count_fp; exit 0 = OK
```

Env overrides: `HERMIT_ROOT HERMIT_BIN MEM_CAP POOL REPS MINUTES PER_RUN_TIMEOUT OVERSUB`.
All output → `results/<profile>_<ts>.log` (issue #113: no stream flood). Profiling
CSVs → `results/perf_<profile>_<ts>/`. Per-rep records →
`results/reps_<mode>_<ts>.jsonl`; roll-up → `results/summary_<mode>_<ts>.json`.
Exit code: `0` GREEN, `2` P0, `3` refused (no cap) / preexisting-fail.

## Smoke result (2026-07-31, this host)

Profile `smoke` (reps ×2, pool 8, 8 GiB cap), hermit `1ece0654`, GREEN (rc=0):

| test | verdict | output hash (all reps) | notes |
|---|---|---|---|
| date | GREEN | `142e113ed20a5539` | 1 count-fp, verify 2/2 |
| devrand | GREEN | `f5edcf77a8645391` | 1 count-fp, verify 2/2 |
| rand | GREEN | `1c3d662e35b48901` | 1 count-fp, verify 2/2 (~80 s/rep) |
| pipeline | GREEN | `6df2960214d3120d` | 1 count-fp, verify 2/2 |
| progressbar | INCONCLUSIVE | — | both reps hit the 120 s wall-clock timeout; correctly **not** a P0 |

Safety/profiling confirmed active: outer cgroup `memory.max=8589934592 (bound)`,
`memory.swap.max=0 (disabled)`, peak `memory.peak=362 MB`, `oom_kill=0`; ambient
host load during the run 72.9–90.4 (real contention). This validates the full
stack (cap enforcement, cross-rep hash diff, schedule-fp diff, timeout handling)
end to end.

## Phase 1 result — `validate` at scale (2026-07-31, reps ×10, pool=316, 64G cap)

hermit `1ece0654`, reverie `aa6f1283`, `PER_RUN_TIMEOUT=300`. GREEN (rc=0), 303 s,
50/50 reps. The harness's own self-load drove ambient load **56 → 238** (avg 130)
during the run; every test stayed byte-identical:

| test | verdict | reps scored | distinct output hashes | count-fps | verify |
|---|---|---|---|---|---|
| date | GREEN | 10/10 | 1 (`bd10d6e1de07fa5a`) | 1 | 10/10 |
| devrand | GREEN | 10/10 | 1 (`f5edcf77a8645391`) | 1 | 10/10 |
| rand | GREEN | 10/10 | 1 (`1c3d662e35b48901`) | 1 | 10/10 |
| progressbar | GREEN | 10/10 | 1 (`d8778ce336753d09`) | 1 | 10/10 |
| pipeline | GREEN | 10/10 | 1 (`6df2960214d3120d`) | 1 | 10/10 |

Cap held: `memory.peak=2.1 GB` / 64 GB, `oom_kill=0`. With a 300 s budget
`progressbar` now scores 10/10 (the 120 s smoke budget was the only reason it was
INCONCLUSIVE earlier — a perf/tuning artifact, never a divergence).

## Phase 2 result — `timed` fair hot-loop mechanism smoke (3 min, auto-oversubscribed)

`MINUTES=3 PER_RUN_TIMEOUT=120`, pool auto = 474 (1.5×nproc). GREEN (rc=0). The
oversubscribed pool (each rep = 2 invocations + verify double-run ⇒ ≈3–6×
effective) drove ambient load to a **peak of 1247 (avg 948)** on the 316-core host:

| test | verdict | runs | scored | timeouts | distinct output hashes |
|---|---|---|---|---|---|
| date | GREEN | 176 | 142 | 34 | 1 (`48118bb9f7129b89`) |
| devrand | GREEN | 175 | 140 | 35 | 1 (`f5edcf77a8645391`) |
| rand | INCONCLUSIVE | 175 | 0 | 175 | — |
| progressbar | INCONCLUSIVE | 175 | 0 | 175 | — |
| pipeline | GREEN | 175 | 79 | 96 | 1 (`6df2960214d3120d`) |

- **Fairness confirmed:** 876 total runs, ~175 per test (the `min(started)` picker
  balanced a fast test and a slow test to near-equal *run counts* in the window;
  under a longer window a fast test would out-run a slow one in *completions*).
- **Load-independence held across a ~20× load swing (56 → 1247):** every test with
  ≥2 scored reps produced exactly one output hash and one schedule fingerprint. No P0.
- **Timeouts correctly never false-P0:** at load ≈948, `rand` (~80 s baseline) and
  `progressbar` blew past the 120 s budget on all reps ⇒ INCONCLUSIVE, not P0.
- Cap held: `memory.peak=15.9 GB` / 64 GB, `oom_kill=0`.

## Tuning for the real 1-hour torture (now baked into `torture` defaults)

The Phase-2 findings are applied as the `torture` profile defaults (all still
env-overridable):

1. **Per-run timeout vs oversubscription.** At full oversubscription the heavy
   tests need a much larger budget to score. `torture` now defaults to
   `OVERSUB=1.25` (pool still > Nprocs, machine stays swamped) and
   `PER_RUN_TIMEOUT=600` so `rand`/`progressbar` also yield scored reps instead of
   the 175/175 timeouts seen at `1.5×` + `180 s`. Fast tests carry the divergence
   signal regardless; a consistent timeout is a perf signal, not a bug.
2. **Drain tail.** In `timed` mode the deadline bounds *starting* new reps; in-flight
   reps drain to completion/timeout, so a 3-min window took 666 s wall under extreme
   oversubscription. `torture`'s `--step-timeout` is therefore
   `minutes*60 + PER_RUN_TIMEOUT + 600` (= 4800 s for the 60-min run: window + one
   full per-run drain + margin).

## Observation — `date.sh` nanosecond drifts across invocations (not load-induced)

`examples/date.sh` prints a fixed virtual epoch under `--strict`
(`2025-12-31_16:00:00_<ns>`); consecutive runs in a tight burst are byte-identical,
but the **nanosecond** field drifts across separate hermit invocations minutes
apart (`…_041535730` vs `…_041534390`, ≈1.3 µs), yielding a different output hash
per harness run (smoke `142e…`, validate `bd10…`, timed `48118b…`). This is **not**
caught as P0 because it is stable *within* each run (all reps of one invocation
agree) — the guardrail's invariant is cross-rep-within-run identity, which held.
It is a genuine clock-domain determinism curiosity (cf. #1095 / PMU-skid /
GuestClock) worth a separate, owner-gated investigation; the torture run over many
hours and load regimes is the natural place to see whether it ever shifts
*within* a single window (which would be a real P0).

## Status: BUILT + SMOKE + PHASE-1 + PHASE-2 VALIDATED, real torture HELD

All three engine phases pass GREEN with the cgroup cap always verified active and
zero OOM. The `torture` profile is armed correct-by-default with the Phase-2
tuning above, so the real run is a one-liner. Per the owner sequence, the
**1-hour** torture run is **held** until (a) the demo5 fix is confirmed green +
landed and (b) `main` is green. The owner signals "go" to launch `./run.sh torture`.
