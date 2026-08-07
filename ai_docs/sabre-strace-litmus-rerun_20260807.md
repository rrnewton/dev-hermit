# SaBRe strace litmus rerun — 2026-08-07

Task: `sabre-strace-litmus-inconclusive-rerun`

## Result

SaBRe is **ptracer-present** at the exact tested Hermit head
`590fcc9eeb0339c5cf23f72b84394a63333e88ff`. The qualifying strace-wrapped
run observed one establishing ptrace call and that call returned `EPERM`:

```text
ptrace(PTRACE_TRACEME) = -1 EPERM (Operation not permitted)
```

The earlier inconclusive cell remains unmeasured; neither of the two setup
failures encountered during this rerun is counted as a negative.

## Provenance

- Hermit repository: `rrnewton/hermit`
- Tested Hermit detached HEAD and freshly fetched `origin/main` before and
  after the measurement: `590fcc9eeb0339c5cf23f72b84394a63333e88ff`
- Hermit version output: `hermit 0.2.0 (2026-08-07, g590fcc9eeb03)`
- Pinned Reverie: `6144323c5dab8b521278fce206f8774360c2b05f`
- Harness: `ci-hub/litmus/strace_attach_litmus.sh`, SHA-256
  `67155cb1c60ae29cdfdfa85a7528eba2447d3ba2f4a225a0537362db56211a38`,
  last changed in dev-hermit commit
  `d53ffbede050497b7e53c3609af23afc288bc960`
- `strace --version`: `strace -- version 6.12`
- Kernel: `6.18.39-0_fbk0_hardened_0_ga43d5727b443`
- `/proc/sys/kernel/yama/ptrace_scope`: unavailable on this host. The verdict
  therefore uses wrap mode, where strace demonstrably already owns the
  tracee, rather than interpreting a bare attach `EPERM`.

Tested artifact SHA-256 values:

| Artifact | SHA-256 |
| --- | --- |
| `hermit` | `2dde1969f1b7adcb027854f756d0f283f4202eeee9ccbd59d534075d5fba6226` |
| `libdetcore_sabre.so` | `0ce15965cb00b8f2603e8832ec7b6aa15d5ee70e2d163c04bf7e585b51978ff4` |
| SaBRe loader | `f595e94cb5b276b683dd808d117e1cc6b1e1eb11c8b94eb9891f6683d7d411d6` |

## Live-premise source check

One of one source snapshots checked (1/1) still puts a ptracer in the SaBRe
path:

- `hermit-cli/src/lib.rs:1056-1064` enters `sabre_ptrace::run`.
- `hermit-cli/src/sabre_ptrace.rs:1015-1036` calls `ptrace::traceme()` in the
  child `pre_exec` hook.
- `hermit-cli/src/sabre_ptrace.rs:284-302` supervises the root tracee and
  resumes it with `PTRACE_SYSCALL`.

This source check predicted contention but was not used as the runtime
verdict.

## Commands actually run

The clean exact-head build passed both requested build commands (2/2):

```bash
CARGO_NET_OFFLINE=true cargo build --release --locked -p hermit --features sabre -p detcore-sabre
CARGO_NET_OFFLINE=true HERMIT_INSTALL_FORCE_RESTAGE=sabre-litmus-590fcc9e cargo build --release --locked -p hermit-install
```

Every runtime command ran under its own `setsid` process group. No broad kill
command was used.

Matched untraced control:

```bash
setsid env TMPDIR=/home/newton/work/dev-hermit/scratch/sl timeout 120 /home/newton/work/dev-hermit/scratch/sabre-litmus-590fcc9e/bin/hermit run --backend sabre -- /bin/true
```

Outcome: exit 0. Across qualifying untraced controls, 2/2 exited 0; the
matched short-`TMPDIR` control was 1/1.

SaBRe strace bracket:

```bash
setsid env TMPDIR=/home/newton/work/dev-hermit/scratch/sl HERMIT=/home/newton/work/dev-hermit/scratch/sabre-litmus-590fcc9e/bin/hermit TIMEOUT=120 ci-hub/litmus/strace_attach_litmus.sh --backend sabre --mode wrap --keep-log -- /bin/true
```

Qualifying outcome (1/1 runs):

```text
PTRACE_ESTABLISHING_CALLS=1
PTRACE_ESTABLISHING_EPERM=1
HERMIT_RC=1
VERDICT=REFUSED-ALREADY-TRACED
```

Thus, 1/1 establishing calls returned `EPERM`, errno 1. The kept local trace
recorded the exact syscall at `strace.log:246`.

Ptrace-backend sensitivity control, using the same harness and environment
except for `--backend ptrace`: 1/1 runs produced
`PTRACE_ESTABLISHING_CALLS=1`, `PTRACE_ESTABLISHING_EPERM=1`,
`HERMIT_RC=1`, and `VERDICT=REFUSED-ALREADY-TRACED`. Its exact syscall was
also `PTRACE_TRACEME = -1 EPERM`. This control contributes no SaBRe ratchet
cell; it only shows that the bracket detects known tracer contention.

## Excluded setup attempts

Two of two setup failures (2/2) are classified **unmeasured**, contributing
zero qualifying runtime trials and zero directional results:

1. Artifacts initially staged below `/tmp` became unavailable after Hermit
   remounted `/tmp`; Hermit reported that the configured SaBRe loader was not
   executable. The backend never started.
2. The first workspace-visible wrapped retry used a long `TMPDIR`. SaBRe
   failed before ptrace because its coordinator Unix-socket path exceeded the
   path-length limit. The harness emitted `VERDICT=ERROR`, `HERMIT_RC=1`, and
   no establishing ptrace call. Its zero-match count also exposed the
   harness's `0` plus fallback-`0` formatting bug; that output is not a
   negative measurement.

Moving the hash-identical artifacts to a namespace-visible path and using a
short `TMPDIR` corrected those setup conditions before the qualifying runs.

## Ratchet update

Only the SaBRe cell was measured here. Starting from the previously recorded
four measured cells (`ptracer-free=1/4`, `ptracer-present=3/4`) and adding the
qualifying SaBRe result gives:

| Classification | Measured denominator | All-backend denominator |
| --- | ---: | ---: |
| Ptracer-free | 1/5 | 1/6 |
| Ptracer-present | 4/5 | 4/6 |
| Unmeasured | — | 1/6 (KVM) |

Measured coverage is therefore 5/6 total backends. This rerun makes no claim
about KVM and does not reinterpret either setup failure as a negative.
