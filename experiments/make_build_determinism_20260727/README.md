# make-driven builds under hermit --strict --verify

**Date:** 2026-07-27 · **Task:** "Continue compat expansion" (option 2, build system) · **Agent:** hermit-274
**Hermit:** `6044c2d39ace4bd84f32598a9fde692fcea67b3f` · release binary (built 2026-07-27 09:56) · **Backend:** ptrace
**Tools:** GNU make 4.x (/usr/bin/make), gcc (/usr/bin/gcc), coreutils

## Question

Does a real `make`-driven build — which forks+execs a recipe shell per rule, i.e. a
genuine multi-process build — produce **deterministic** output under
`hermit run --strict --verify`? And does that extend to driving a real C compiler?

## Method

Two self-contained workloads, each generating all sources + the `Makefile` inside
the guest's **private /tmp** (pristine per run; see redis experiment for the
private-/tmp gotcha):

- `make_test.sh` — **compiler-free** build: coreutils recipes (`seq`/`awk`/`sort`/
  `uniq`/`cat`/`wc`/`md5sum`) with a pattern rule + a 3-way dependency + an
  aggregate. Isolates the make **driver's** multi-process determinism.
- `make_gcc_test.sh` — `make` driving a real **gcc** compile+link of a 2-object
  C program (`main.o` + `util.o` -> `prog`), then runs `prog`.

Run under `--strict` (L1) and `--strict --verify` (L2); L4 = 5x --verify;
artifact determinism = md5 of the build output across independent `--strict` runs.

## Results

| Workload | L1 | L2 verify | L4 (5x verify) | artifact md5 across independent runs |
| --- | --- | --- | --- | --- |
| coreutils `make` | PASS | **PASS** ("no substantive differences") | **5/5** | `combined.dat` = `8e530518ccaa5466d022ffc575e3721a` (identical, = native) |
| `make`+gcc | PASS | PASS (intermittent) | **~4/5** (flaky) | `prog` = `f47859351db029871524057bfecd4f64` (identical across 3 runs) |

### coreutils make — deterministic, L2 → effectively L4

A multi-process, forking `make` build is **fully deterministic** under
`--strict --verify`: 5/5 verify, and the artifact is byte-identical to the native
build across independent invocations. The make driver's fork+exec of per-rule
recipe shells is serialized deterministically by hermit's scheduler.

### make+gcc — deterministic *output*, flaky *execution*

The **compiled binary is byte-identical** (`prog` md5 identical across 3
independent `--strict` runs), consistent with "gcc output is byte-deterministic"
(memory `gcc-nondet-is-fs-state`). But `--strict --verify` is **intermittent**
(observed 4/5 and lower): gcc's own execution has nondeterminism under --verify —
vfork scheduling (memory `build-tools-fail-verify-vfork-scheduling`) plus an
observed runaway `mincore` heap-scanning path (gcc GC), syscall counts in the
hundreds of millions on the diverging run. **The nondeterminism is gcc's, not
make's** — the coreutils make build with the identical driver is 5/5.

## Takeaway

`make` as a build **driver** determinizes cleanly under hermit `--strict --verify`
(multi-process fork/exec fully covered). The frontier boundary is the **compiler**:
gcc emits deterministic bytes but executes nondeterministically under --verify.
A deterministic-compiler build would need a compiler that avoids gcc's vfork/GC
paths (e.g. tcc — single execve/0 forks; not installed here). No hermit code
change was made or required for the coreutils result.

## Reproduction

```bash
cd ~/work/dev-hermit/worktrees/274/hermit
SC=~/work/dev-hermit/experiments/make_build_determinism_20260727/make_test.sh
./target/release/hermit --log warn run --strict --verify -- /bin/sh "$SC"
#  => ":: Success: deterministic. Determinism verified."
```

## Files
- `make_test.sh` — compiler-free coreutils make workload.
- `make_gcc_test.sh` — make driving a real gcc compile.
- `coreutils.Makefile` — the coreutils build's Makefile (also embedded in the script).
- `coreutils_artifact.txt` — deterministic build report (guest stdout).
- `metadata.json` — SHAs, host, results.
