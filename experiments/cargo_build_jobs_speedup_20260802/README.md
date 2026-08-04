# Curve 1 — `cargo build` (release, third-party-backends) wall & memory vs `CARGO_BUILD_JOBS`

Umbrella task: `headline-inner-step-scaling-curves-cargo-and-strict-compat` (CURVE 1).
Synthesis added 2026-08-04 by hermit-inner-scaling (opus-4.8) from the raw sweep
captured 2026-08-02. **No new build was run for this write-up** (box was contended by
`hermit-238b`'s cargo-lock-contention storm; a competing `cargo build` sweep would have
corrupted both datasets). This README rescues an orphaned, README-less experiment and
corrects a data-validity defect in `results.csv`.

## Question (owner)

> "`CARGO_BUILD_JOBS=8` is very stupid... find out how much parallel scaling our cargo
> build can actually benefit from." — where does the release/third-party-backends build
> (the exact `build.dbi_release` node) flatten, and is 8 defensible?

## Method

Raw sweep: `run-sweep.sh` did CLEAN builds (throwaway `CARGO_TARGET_DIR` per point) of

```
CARGO_BUILD_JOBS=$j THIRD_PARTY_BUILD_JOBS=$j \
  cargo build --release --locked -p hermit --features third-party-backends \
    -p detcore-dbi -p hermit-install
```

which is **byte-identical to the current `ci/dag/portable.json` `build.dbi_release`
node cmd** (portable.json:54). Wall = wall-clock; `peak_rss_mb` = 2-second-sampled
sum of RSS over the whole build process group (cargo+rustc+cc1+cmake/make).

## VALIDITY CORRECTION — read before trusting `results.csv`

**`results.csv` rows with `exit_code == 101` are FAILED builds, not measurements.**
Their walls are failure-fast times, not build times (e.g. `full316,128 → wall=42s` is a
*fast crash*, not a fast build). Every 101 here is a **BpfJailer sandbox block**, NOT an
OOM and NOT a wall:

```
Enforcer: FS, Reason: FILE_OPEN   Role: 3pai
Fatal error: can't create .../gelf_getshdr.c.o: Operation not permitted
/usr/bin/ar: .../eblcorenote.c.o: Operation not permitted
/usr/include/wchar.h: fatal error: .../stddef.h: Operation not permitted
```

The DynamoRIO C/C++ build (`reverie-dbi/build.rs` → cmake → gcc/ar) hits the 3pai
filesystem enforcer and dies in `build.rs:339`. This is the known env-block class
(`bpfjailer-blocks-reflink-cp`, `validate-env-sandbox-block-classification`): the
DynamoRIO native build **cannot run under the agent sandbox** and must go through the
`systemd-run --user` producer path. The failures are non-deterministic w.r.t. `j` —
they hit whichever native compile happened to touch a blocked FS op — so **no `exit==101`
row carries any wall or memory signal.**

Invalid (discard): `full316 j128, j316`; `cpuset4 j8, j64`.

## RESULTS — valid rows only (`exit_code == 0`)

### Wall (full box, clean release build) — this is the headline curve

| CARGO_BUILD_JOBS | wall (s) | speedup vs j8 | marginal gain vs prev |
|---:|---:|---:|---:|
| 8  | 175 | 1.00× | — |
| 16 | 132 | 1.33× | 1.33× |
| 32 | 108 | 1.62× | 1.22× |
| 64 | 104 | 1.68× | 1.04× |

4-core anchor (`taskset -c 0-3`): j4 = 641s, j16 = 641s (flat — core-bound at 4 cores,
extra jobs cannot help). So the build genuinely uses width; the full-box curve is real
parallel scaling, not cache/lock serialization.

### THE KNEE = j32

By the safe-ci-dag runner's own per-step marginal-gain rule (advance while gain ≥ 1.15×,
`estimates.py:_SPEEDUP_MIN_MARGINAL_GAIN`): j8→j16 (1.33×) advance, j16→j32 (1.22×)
advance, **j32→j64 (1.04×) STOP**. Past j32 the wall barely moves (108→104s = 3.7%).
Absolute wall floor ≈ **104s at j64**; the *knee* (last worthwhile width) is **j32 at 108s**.

### Memory — the actual binding constraint

`results.csv` `peak_rss_mb` is **2-second ps-sampled → an undercount** of the true peak
(a polled aggregate is not a cgroup-recorded peak). The authoritative memory number is the
node's own **cgroup-RECORDED** derivation (portable.json:53, task
`memory-caps-must-scale-with-job-count`): per-worker ≈ **522 MiB/proc, LINEAR in jobs**,
cgroup peak **@j8 = 6.8 GiB**. Linear model `peak(j) ≈ 2.72 GiB + 0.51·j GiB`:

| jobs | cgroup-model peak | ps-sampled (undercount, this sweep) |
|---:|---:|---:|
| 8  | ~6.8 GiB  | 4.4 GB |
| 16 | ~10.9 GiB | 5.9 GB |
| 32 | ~19.0 GiB | 10.9 GB |
| 64 | ~35.3 GiB | 16.3 GB |

The ps-sampled column corroborates the *linear shape* but understates magnitude; use the
cgroup model for any cap decision.

## Interpretation — is `CARGO_BUILD_JOBS=8` "stupid"?

**No — 8 is memory-cap-derived, not arbitrary.** The `build.dbi_release` node has
`hard_mem_max_bytes = 8.5 GiB`. At 522 MiB/proc: j8 = 6.8 GiB (22.5% headroom, PASSed
47/47 full-validate runs), j12 = only 1.2% headroom, j16 ≈ 10.9 GiB = **OOM** against the
cap. 8 is the largest job count that fits 8.5 GiB with headroom. It OOMed every full
validate *before* being pinned because the old cmd used `${THIRD_PARTY_BUILD_JOBS:-$(nproc)}`
= up to 32/nproc jobs against the same cap.

**The real lever is the memory cap, and the knee past which more cap buys nothing is j32:**

| operating point | cap needed (peak×1.25) | wall | vs current |
|---|---:|---:|---:|
| j8 (current)      | 8.5 GiB  | 175s | 1.00× |
| j16               | ~14 GiB  | 132s | 1.33× |
| **j32 (the knee)**| ~24 GiB  | 108s | **1.62×** |
| j64               | ~44 GiB  | 104s | 1.68× (NOT worth +20 GiB for 4s) |

**Recommendation for the owner:** keep `CARGO_BUILD_JOBS=8` *iff* the node stays at an
~8.5 GiB cap. To make this build faster, raise `hard_mem_max` and `CARGO_BUILD_JOBS`
together and **stop at j32** — ~2.8× the memory cap (8.5→24 GiB) buys 1.62× the build
speed (175→108s). Going past j32 to j64 costs ~20 GiB more for a 4-second gain. Jobs and
cap must move together (`dag-mem-caps-pinned-jobs-fix`); bumping jobs alone re-introduces
the OOM class.

## Remaining gaps (need a QUIET box + the systemd-run producer path)

1. **Clean j128/j316 walls** — the Aug-2 attempts sandbox-blocked. LOW value: the wall
   curve already flattened at j32; the debug `-p hermit` curve
   (`per-step-j-model-already-exists-memory-is-the-gap`) likewise regressed past j64
   (j128 34.9 → j316 37.0). j128/j316 would only confirm flat-to-regressing.
2. **cgroup-recorded memory at j16/j32/j64** — this sweep has only ps-sampled undercounts;
   the 522 MiB/proc linear model supplies the estimates above. MEDIUM value (firms up the
   cap-per-jobs table). Run via `systemd-run --user` under `safe-ci.slice` so `memory.peak`
   populates.
3. **Low anchors j1/j2 full-box** — only the 4-core j4=641s anchor exists.

Any of these is NEW measurement: run only on a genuinely quiet box, coordinated with
`hermit-231b` (curves 1+2 owner) and `hermit-238b` (cache-contention), and through the
`systemd-run --user` producer path so the DynamoRIO C build escapes the 3pai FS enforcer.

## Provenance / reproduction

- Build cmd: identical to `ci/dag/portable.json` `build.dbi_release` (portable.json:54).
- Host: devbig014 (AMD EPYC 9D85, 158 phys × 2 SMT = 316 threads).
- Hermit SHA of the Aug-2 checkout is **unrecorded in the original sweep** — a durability
  defect of the raw capture; the cmd is byte-stable so the shape is representative, but a
  re-run for headline numbers should stamp the exact SHA.
- Reproduce: `run-sweep.sh full316 all 1,2,8,16,32` on a quiet box, launched via
  `systemd-run --user` (NOT bare from an agent shell — the DynamoRIO C build sandbox-blocks).
- Raw: `build-*.log`, `results.csv` (apply the validity correction above), `run-sweep.sh`.
