# multisect build-weight & checkout-strategy experiment

**Question.** For the `multisect` tool (rate-aware Git-range search that runs many
checkout+build+run probes), what is the cheapest probe that still yields **usable
backtraces**, how should probes materialize a commit, and **how many probes can
this machine sustain in parallel** (that last number sets multisect's real
speed)? Measure — do not assume.

- **Host:** AMD EPYC 9D85, 316 logical CPUs, 754 GiB RAM, ~2.4 TiB free; shared
  machine (load varies — measurements labelled idle vs contended).
- **Hermit base:** `baf1a7b7f303` (primary `hermit/` on `main`).
- **Toolchain:** host cargo 1.97.1; builds use the repo-pinned nightly from each
  commit's `rust-toolchain.toml` (see the toolchain-drift note below); no sccache.
- **First customer:** the `detcore_misc` reap-hang search (probe = `cargo test -p
  detcore --test tests_misc`, run in-process; excludes hermit-cli, so
  dbi/sabre/e9patch are already out of the build).
- Raw timing files: `ignored/` (gitignored). This README + `metadata.json` +
  `results.csv` are the durable record.

## Checkout strategy — DECISION: `git worktree` (decided with evidence)

| strategy | per-probe cost | disk | shares .git? | correctness risk | verdict |
|---|---|---|---|---|---|
| `git worktree add --detach` | **0.18s** (idle) | **11 MiB** source | yes | none (own `target/`) | **CHOSEN** |
| full `git clone` | seconds–minutes | duplicates objects (GiBs) | no | none | rejected: slow, heavy |
| `cp -a` / reflink of a checkout | ~0.3s (reflink) | COW then diverges | no | stale `target/` reuse | opt-in warm-seed only |
| lock N fleet slots | allocation latency | slot-sized | n/a | contends with fleet; caps N at slot count | rejected |

**Why worktree wins.** It shares the parent repo's `.git` object store (no object
duplication), materializes a commit in ~0.18s and ~11 MiB of source, and gives
each probe **its own `target/`** so no two concurrent probes share a writable
build dir (parent Hard Invariant 8). It does **not** contend for fleet slots, so
parallelism is bounded by cores/courtesy, not by a slot pool.

**Two correctness/robustness findings that shaped the tool:**

1. **Concurrent `git worktree add` collides.** `git worktree add` takes a
   repo-global lock on the parent's worktree metadata; parallel adds (different
   commits, run concurrently by the engine) race and fail (~1.1s infra error,
   and a half-created worktree that later mis-builds). Fix: serialize **only the
   add** via a pool-global `flock` (short critical section); builds then run
   concurrently in their separate worktrees. Verified: with the lock, a
   `k=1 n=1 j=3` search runs all round-1 probes cleanly (previously 1/3 failed
   infra).
2. **Cold build is the robust default; reflink warm-seed is opt-in.** Seeding a
   worktree's `target/` by reflink from a donor works cross-path but pays a
   ~10 s absolute-path cargo-fingerprint relink tax; it saves ~8× CPU on warm
   rebuilds. Because a shared/seeded `target/` risks stale reuse, the default is
   a clean per-worktree cold build (zero cross-worktree correctness risk); warm
   reps within one commit are naturally warm (see below).

## Build-weight table (the estimator's calibration data)

Targeted minimal build = `cargo test -p detcore --test tests_misc --no-run`
(compiles detcore + detcore-model + reverie + testutils; **excludes hermit-cli**,
so the third-party backends are out with zero extra flags).

| config | cold wall | cold CPU | RSS | `target/` | test binary | backtraces? |
|---|---|---|---|---|---|---|
| naive: full-workspace `target/` | — | — | — | **65 GiB** | — | (blows 200 GB cap at 3 probes) |
| **C1** target-only, default dev `debug=2` | 24.0s | 162 CPU-s | 981 MB | 1.6 GB | 210 MB | full |
| **C3** target-only, `CARGO_INCREMENTAL=0` + `debug=line-tables-only` + `split-debuginfo=unpacked` | **18.1s** | **129 CPU-s** | 812 MB | 910 MB | **68.7 MB** | **file:line (kept)** |

**C3 is the recommended probe build:** strictly cheaper than C1 (25% less wall,
20% less CPU, ~3× smaller binary) while **keeping file:line backtraces**, which
this bug class needs. Going lighter (`debug=0`/strip) loses backtraces — not
worth it.

**Warm rebuild (reps 2..N on the same commit):**

| scenario | wall | CPU |
|---|---|---|
| C3, no source change (true cargo no-op) | **0.4s** | ~0.3 CPU-s |
| C3, touch one detcore file | 9.35s | 16 CPU-s (8× less than cold) |

Because multisect samples reps **serially per commit**, rep 1 pays the cold
build and reps 2..N are warm no-ops (~0.4 s) — so a high rep count (needed to
amplify a rare hang) is cheap after the first build.

## Probe run cost & parallel capacity

| measurement | value |
|---|---|
| full `tests_misc` run | 0.41s / 2 CPU-s / 28 tests |
| single `vfork_parent_resumes_after_child_exec` | ~0.02s healthy |
| calibrated passing test (contended host) | 0.10–0.11s |
| cold build, **contended** host | 58.3s (vs 18.1s idle — load-dependent) |
| 8-wide parallel cold C3 | ~23s each (1.28× solo), ~182 CPU-s each (~60 cores for 8) |
| box CPU cap on this host | `safe-ci.slice` CPUQuota ≈ 90% × 316 ≈ 284 cores aggregate |

**Parallel probes sustained:** 8 concurrent cold C3 builds cost ~60 cores total
on a 316-core box, so the machine is CPU-rich — the practical parallel cap is
**fleet courtesy, not hardware**. `jobs=8` is comfortable; `jobs=16` is feasible.
Wall scales ~1/jobs until cores or courtesy run out; **CPU is unchanged** by
parallelism.

**Disk per probe:** C3 = ~11 MiB source + ~910 MiB `target/` ≈ **~0.92 GiB
cold**; warm reps reuse it. Peak pool during a round ≈ `(k+2)` worktrees ≈
~4.6 GiB at `k=3` — three orders under the 200 GB governance cap. (The naive
full-`target/` probe would be **65 GiB each** and blow the cap at 3 probes; the
C3 + per-worktree-target decision is what makes multisect fit the disk budget.)

## End-to-end validation (real box)

`multisect run --probe detcore_misc --good HEAD~3 --bad HEAD -k1 -n1 -j3`, box =
freshly built box-capable `safe-ci-dag-runner`:
- ETA printed up front (canonical `COST ESTIMATE`), search ran through the real
  cgroup box, probes did real checkout+build+run, verdicts classified, and the
  run ended with the canonical `COST ACTUAL` line (**wall 24s, CPU 396s** —
  CPU≫wall confirms parallel multi-core builds).
- Correct `AMBIGUOUS` (exit 4) when the range has no reproducible regression — no
  false blame.
- Env passthrough into the box **confirmed** (`PROBE_*` reach the child).
- Pool auto-cleaned; 0 leaked worktrees; primary returned to `main`.

## Known limitation: nightly-toolchain drift

Each worktree honors **that commit's** pinned `rust-toolchain.toml`. If a commit
in the range pins a nightly that is no longer installed (or was
stabilized/removed a feature the code still gates on), the build fails and the
probe classifies **WEDGED (build)** — a false blame. The probe writes a
`build-failed-<sha>` marker and the orchestrator **warns** so the operator can
tell a real hang from a compile break. Lever: narrow the interval to
build-clean recent commits, or pre-install the pinned nightly. (This is why the
`build-failed` marker + warning path exists — it turns a silent misclassification
into a visible, actionable signal.)

## Reproduction

```bash
cd ~/work/dev-hermit
# checkout cost
/usr/bin/time -v git -C hermit worktree add --detach /tmp/wt-probe HEAD
# C3 targeted build (from the worktree)
cd /tmp/wt-probe && CARGO_INCREMENTAL=0 CARGO_PROFILE_DEV_DEBUG=line-tables-only \
  CARGO_PROFILE_DEV_SPLIT_DEBUGINFO=unpacked \
  /usr/bin/time -v cargo test -p detcore --test tests_misc --no-run
# calibrate via the tool (measures cold+warm+test on this host)
cd ~/work/dev-hermit && ./multisect/multisect calibrate --probe detcore_misc --good HEAD
git -C hermit worktree remove --force /tmp/wt-probe; git -C hermit worktree prune
```
