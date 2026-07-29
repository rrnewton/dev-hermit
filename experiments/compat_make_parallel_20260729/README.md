# Parallel make compatibility under Hermit

Date: 2026-07-29

Task: `compat-deep-app-make-parallel`

## Result

The small five-object `make -j4` build passes at L1 on the ptrace backend and
its visible stdout/stderr is bitwise-identical across two independent strict
runs. It does **not** pass L2: two independent `hermit run --strict --verify`
invocations both reported a deterministic-log scheduling divergence.

Backend: ptrace. Log level: default. Relaxations: none. This is evidence for
this exact workload and backend, not a whole-project determinism claim.

## Inputs

- Hermit: `291679b9ec7cb37a147589d40e5f174c4b40f9f9`
- Debug binary SHA-256:
  `88ae2b2abe5b2508af2bbaad60ed457809de71b5cb8f2d954a55bd1081038484`
- Host: Linux `6.18.39-0_fbk0_hardened_0_ga43d5727b443`, x86-64
- GNU Make 4.3
- GCC 11.5.0 20240719 (Red Hat 11.5.0-14)
- Fixture SHA-256:
  `d1c0e46e32edc2f179cb10520cbbf1fc2071d15cb36ad8288ba3188a0002ef74`

The fixture recreates and removes one fixed work directory on every execution,
launches four compile recipes in parallel, compiles `main.c`, links without a
build ID, executes the result, and prints its SHA-256.

## Reproduce

From a current Hermit checkout with a matching debug build:

```bash
fixture="$(git rev-parse --show-toplevel)/experiments/compat_make_parallel_20260729/fixture.sh"
hermit=target/debug/hermit
work=/tmp/hermit-compat-make-parallel-l2

timeout 300 "$hermit" run --strict --verify -- "$fixture" "$work"
timeout 300 "$hermit" run --strict --verify -- "$fixture" "$work"
```

The two L1 comparison runs were:

```bash
timeout 300 "$hermit" run --strict -- "$fixture" "$work" >strict1.log 2>&1
timeout 300 "$hermit" run --strict -- "$fixture" "$work" >strict2.log 2>&1
cmp strict1.log strict2.log
sha256sum strict1.log strict2.log
```

Both strict logs had SHA-256
`59a8ccd3cc4708cfe6009d0babd4d1ec6a96157d649474f6c09eb7318c41b184`.
The linked program in both printed SHA-256
`1dbc8b22ebb98a8f30f217c3563b7edc4e506269c5313de9dcb55a7112f3db41`.

## L2 divergence

Both required verify invocations first diverged at scheduler turn 680,
immediately after compiler-child `vfork() = 37`:

- run 1 committed make's `BlockedExternalContinue` path for dtid 21, which was
  polling the jobserver/child state with `pselect6` and `wait4(WNOHANG)`;
- run 2 committed compiler-child dtid 33's inbound `SIGCHLD` path, followed by
  `wait4(37)` and jobserver descriptor cleanup.

The first invocation retained `/tmp/run1_log_UYKLW` and
`/tmp/run2_log_b6wwm`; the second retained `/tmp/run1_log_mUowC` and
`/tmp/run2_log_mhbaf`. Those host-local logs are transient. The normalized
first-divergence evidence and exact counts are preserved in `results.txt`.

The clock-skew warning in visible make output is identical across strict runs
and is not the L2 split: verify first diverges in Detcore scheduler resource
selection after `vfork`, before any visible output difference is reported.
