# LiteInst corpus sweep — 2026-07-28

## Question

How much of a plain single-process C corpus determinizes to **L2** (bitwise-identical
repeat run) under `hermit --backend liteinst --strict --verify`, and exactly where does
the LiteInst backend's supported boundary begin to fail?

This is the "bigger-scope" batch alternative to per-syscall ratchet rounds: instead of
one syscall per round, build a 20-program corpus, run it through native + ptrace +
liteinst, and classify every outcome from one reproducible harness.

## Method

- **Corpus:** 20 self-contained C programs in `src/` (`01_*.c` .. `20_*.c`). Programs
  01–16 are single-process / single-thread "normal" programs (arithmetic, heap, file
  I/O, env, libm, clocks, libc rand, argv, recursion, buffered stdio, getrandom, anon
  mmap, gmtime). Programs 17–20 deliberately probe the LiteInst boundary: threads
  (`17_pthread`), `fork` (`18_fork`), a callable guest signal handler (`19_signal`), and
  a timer (`20_alarm`).
- **Harness:** `run_sweep.sh` compiles each program with `cc -O1` (`+ -lm`/`-lpthread`
  where noted), then runs it three ways: native, `hermit --backend ptrace run --strict
  --verify`, and `hermit --backend liteinst run --strict --verify`. It records exit code,
  a sha256 prefix of guest stdout, whether liteinst printed `Determinism verified`, and
  whether liteinst's stdout matches the native stdout.
- **Verdicts** (see the header comment in `run_sweep.sh`):
  - `L2-MATCH` — liteinst verified L2 **and** stdout equals native (or the program is in
    the intentionally-determinized set `05_getpid`, `14_getrandom`, where a native
    mismatch is the correct determinized behavior).
  - `L2-WRONG` — liteinst reported L2 (`rc=0`, `Determinism verified`) **but** stdout
    differs from native for a program that should match: a functionally degraded result
    blessed as deterministic.
  - `REJECT` — liteinst exited nonzero (fail-closed).
  - `HANG` — liteinst exceeded the per-run timeout (45 s).
- **Note on ptrace stdout:** the ptrace backend discards guest stdout under `--verify`
  (headless double-run + log compare), so its `out_sha` column is the empty-string hash
  and only `ptrace_verify` (L2/FAIL) is meaningful. LiteInst is in-process (LD_PRELOAD)
  and passes guest stdout through, so its `li_out_sha` is real.

Provenance (reverie `770ee38`, hermit `a61e9eb7`, host, exact commands) is in
`metadata.json`. Machine-readable results are in `results/results.csv`; per-program
stdout/stderr captures are in `results/logs/` (gitignored — regenerate with the harness).

## Results

16/20 L2-MATCH, 2 L2-WRONG, 1 REJECT, 1 HANG. Table generated from `results/results.csv`
(`column -s, -t results/results.csv`):

```
prog          native_rc  native_out_sha  ptrace_verify  li_rc  li_verify  li_out_sha    li_matches_native  li_verdict
01_hello      0          9eb3ab918ef2    L2             0      L2         9eb3ab918ef2  yes                L2-MATCH
02_arith      0          abc5d15546aa    L2             0      L2         abc5d15546aa  yes                L2-MATCH
03_heap       0          a2636df9a410    L2             0      L2         a2636df9a410  yes                L2-MATCH
04_fileio     0          94a672833858    L2             0      L2         94a672833858  yes                L2-MATCH
05_getpid     0          dbc16ad9c8dd    L2             0      L2         c04b0e643e47  no                 L2-MATCH  (determinized)
06_env        0          358a5d82ed60    L2             0      L2         358a5d82ed60  yes                L2-MATCH
07_strsort    0          2dc2993c2f08    L2             0      L2         2dc2993c2f08  yes                L2-MATCH
08_mathlib    0          ef483224911e    L2             0      L2         ef483224911e  yes                L2-MATCH
09_clock      0          d9f58dabf97e    L2             0      L2         d9f58dabf97e  yes                L2-MATCH
10_rand_libc  0          cf2342510172    L2             0      L2         cf2342510172  yes                L2-MATCH
11_argv       0          798257fffe82    L2             0      L2         798257fffe82  yes                L2-MATCH
12_fib        0          ccde3035e8dc    L2             0      L2         ccde3035e8dc  yes                L2-MATCH
13_bufio      0          a2322a741909    L2             0      L2         a2322a741909  yes                L2-MATCH
14_getrandom  0          c203a02e9e11    L2             0      L2         e70e9a38611e  no                 L2-MATCH  (determinized)
15_mmap       0          2d4dd34871ea    L2             0      L2         2d4dd34871ea  yes                L2-MATCH
16_time_fmt   0          e5eb0db1d68e    L2             0      L2         e5eb0db1d68e  yes                L2-MATCH
17_pthread    0          37fc514a8f8c    L2             0      L2         e710bb44b592  no                 L2-WRONG
18_fork       0          e3b3ca07dbe5    L2             0      L2         672d0957afb4  no                 L2-WRONG
19_signal     0          9b714977e278    L2             1      FAIL       e3b0c44298fc  no                 REJECT
20_alarm      0          a05c785585a4    L2             124    HANG       e3b0c44298fc  no                 HANG
```

## Interpretation

**Single-process C is a mature LiteInst frontier (16/16).** Every non-boundary program
determinized to L2 with byte-identical repeat runs, and where it matters LiteInst
*correctly* determinized non-reproducible sources: `05_getpid` (spoofed PID) and
`14_getrandom` (deterministic randomness) both differ from native by design and still
verify L2. `ptrace_verify` is L2 for all 20, so LiteInst matches the ptrace determinism
baseline exactly on the 16 in-boundary programs.

**The four boundary programs fail in the four documented LiteInst boundary modes**, all
of which are shared with e9patch by construction (both ld-preload backends route
clone/fork through the same `reverie-preload` `PassthroughDispatcher` and share the
`reverie-liteinst` signal/timer policy):

- `17_pthread` (**L2-WRONG**): thread-creating `clone` is rejected (`ENOTSUP`); the
  worker threads never run, so the accumulator stays at its initial value. The degraded
  single-thread result is still bitwise-reproducible, so `--verify` blesses it "L2".
- `18_fork` (**L2-WRONG**): `fork` injection is unsupported; the child never runs and the
  parent observes the wrong count. `tool_host::inject` prints
  `reverie-liteinst: clone/fork injection is unsupported` to stderr, but the run still
  exits 0 and verifies L2.
- `19_signal` (**REJECT**): installing a callable guest signal handler is rejected, so
  the program exits nonzero — fail-closed, the desired behavior.
- `20_alarm` (**HANG**): timer arming delivers no RCB/preemption event, so `alarm(1)` +
  spin never receives `SIGALRM` and the guest spins to the timeout.

### Determinism-integrity concern: L2-WRONG (false-L2 on rejected clone/fork)

`17_pthread` and `18_fork` are the important finding. `--verify` proves run₁ == run₂, not
run == native. When LiteInst (or e9patch) rejects a `clone`/`fork` with an errno and the
guest ignores the errno and continues, it reaches a **wrong but perfectly reproducible**
result, which `--verify` then reports as "Determinism verified" with `rc=0`. A rejected
injection thus silently downgrades from "unsupported → fail-closed" to "unsupported →
wrong answer blessed as deterministic." `18_fork` at least prints a stderr diagnostic;
`17_pthread`'s runtime-dispatch rejection is silent.

This is **not** a LiteInst-specific bug to patch in-lane: it stems from the shared
`reverie-preload` clone/fork policy plus `--verify` semantics, i.e. a core
injection-semantics decision that must be discussed with a human before any behavior
change (per the Reverie API Policy). It is filed as a bot issue on `rrnewton/reverie`
rather than fixed here. The clone/fork rejection itself is already unit-tested in
`reverie-liteinst/tests/strace.rs`
(`unsafe_clone_is_rejected_in_compatibility_and_strace_modes` and the compatibility-fork
tests); the gap is purely that a *guest that ignores the errno* is not detectable by
`--verify`.

## Reproduction

```bash
cd ~/work/dev-hermit
# Build the liteinst-capable hermit + detcore preload (round-5 SHAs in metadata.json):
HTTPS_PROXY=http://fwdproxy:8080 cargo build --manifest-path worktrees/liteinst/hermit/Cargo.toml \
  -p hermit -p detcore-liteinst --release
# Run the sweep (HERMIT defaults to the round-5 release binary):
cd experiments/liteinst_corpus_sweep_20260728
TIMEOUT=45 ./run_sweep.sh
column -s, -t results/results.csv
```

`bin/` and `results/logs/` are gitignored; `run_sweep.sh` regenerates them and rewrites
`results/results.csv`.
