# Hermit backend maturity model

Status: definitive testable model as of 2026-07-28.

This document defines maturity for Hermit execution backends. A level is an
evidence claim, not an implementation milestone or an estimate. Levels are
cumulative: a backend holds the highest level for which it passes every gate
at one identified Hermit and Reverie revision.

The central distinction is:

- **backend-local determinism:** two executions through one backend agree;
- **backend parity:** the guest-visible result also agrees with the ptrace
  reference.

A backend can be deterministic and still implement a different clock, random
stream, schedule, or output policy. Backend-local `--verify` success therefore
does not establish parity.

## Level summary

| Level | Testable gate |
| --- | --- |
| **B0: builds** | The backend crate exists and compiles in a clean release build. |
| **B1: intercepts** | A guest launches and the backend demonstrably intercepts a documented syscall subset. |
| **B1.5: Reverie tools** | Shared Reverie counter and trace tools execute through the backend's `Guest`/`Tool` contract. |
| **B2: Detcore entry gate** | `hermit run --backend BACKEND --strict --verify` drives `Detcore<BackendGuest>` on a nontrivial workload. |
| **B2.1: examples parity** | Every executable in `hermit/examples/` verifies locally and matches ptrace guest output. |
| **B2.2: C corpus** | All 183 manifest-listed C tests pass `--strict --verify`. |
| **B2.3: shell and Rust corpus** | B2.2 remains green and all manifest-listed shell and Rust tests pass. |
| **B2.4: full compatibility envelope** | The post-CI-overhaul-v2 compatibility manifest passes with no missing or skipped expected rows. |
| **B3: majority parity** | At least 50% of the frozen ptrace corpus is guest-observable parity-clean. |
| **B4: complete parity** | 100% of that corpus is bitwise parity-clean across every supported backend. |
| **B4+: leading workloads** | Named frontier workloads, such as a Linux boot under QEMU, pass in addition to B4. |

## Gate definitions

### B0: crate exists and builds

Required evidence:

1. The backend has a versioned crate and is selected by the workspace build.
2. `cargo build --release` succeeds from a clean checkout.
3. The report records the exact repository SHA and toolchain.

B0 makes no runtime, interception, determinism, or compatibility claim.

### B1: partial guest implementation

Required evidence:

1. A real guest ELF launches through the backend rather than a mock protocol.
2. At least one syscall reaches a backend callback, with an observed argument
   and return value.
3. A test proves an inspect, suppress, replace, or inject action. Merely
   starting the guest process is insufficient.
4. Missed instructions, library calls, child processes, and fallback paths are
   reported explicitly.

B1 permits a limited syscall surface and incomplete process lifecycle.

### B1.5: simple Reverie tools

This level validates the Reverie `Guest`/`Tool` boundary before Detcore is
introduced. Required evidence is a fixed guest matrix for:

- `counter1`, proving local tool callbacks;
- `counter2`, proving global aggregation and process/thread accounting;
- `strace`, or a documented trace adapter exercising the same callback and
  memory/register contracts.

Each tool must exit zero and produce a stable, explained result on repeated
runs. Backend-specific launch glue is allowed, but a hard-coded output or a
runner that bypasses the Tool contract is not. Process-tree omissions count as
documented B1.5 limitations and block any stronger process-tree claim.

### B2: Hermit strict/Detcore entry gate

B2 is the base integration gate for the B2 subdivisions. Required evidence:

1. The public CLI selects the backend without private environment setup.
2. The path actually instantiates or connects the backend guest to Detcore;
   logs must identify the active backend/tool.
3. A nontrivial workload exits zero under `--strict --verify` with no
   determinism relaxation other than a separately reported hardware
   capability accommodation.
4. Any ptrace safety net, uninstrumented child, or backend-specific verifier is
   disclosed. Silent fallback to the ptrace backend fails the gate.

Passing B2 says that Detcore can drive the backend. It does not say that the
backend covers the Hermit corpus or agrees with ptrace.

### B2.1: all Hermit examples, with ptrace parity

**The examples cross-backend scorecard is the B2.1 acceptance test.** At the
2026-07-28 snapshot the executable denominator is:

```text
date.sh
devrand.sh
race.sh
rand.py
timed-progress-bar.py
```

For every executable example, every backend must:

1. exit zero under `hermit run --backend BACKEND --strict --verify -- ...`;
2. pass its backend-local verifier;
3. exit identically to a ptrace strict single run;
4. produce byte-identical guest stdout and guest stderr after removing only
   launcher diagnostics; and
5. produce identical declared artifacts or externally visible side effects.

Adding an executable to `examples/` expands the denominator. Reports must name
the tree SHA and list the denominator rather than carrying forward a stale
count. CLI presentation differences, such as one verifier suppressing guest
stdout, must be measured with auxiliary strict single runs; they cannot be
mistaken for guest-output parity.

### B2.2: complete C corpus

The gate is all **183 C entries** in the manifest generated by CI overhaul v2.
Every row must run through the selected backend with `--strict --verify`, meet
its declared exit/output expectations, and finish within its declared timeout.

The report must record the manifest SHA and resolved entry count. A missing
binary, unsupported host, timeout, unexpected skip, or backend fallback is a
failure, not an exclusion. An intentional unsupported case must remain visible
as a failing row until the model is deliberately revised.

### B2.3: complete shell and Rust corpus

B2.3 includes B2.2 and adds every shell and Rust entry in the versioned e2e
manifests. Build nodes must complete before the per-manifest run nodes. Every
manifest bucket must report its selected, passed, failed, timed-out, and skipped
counts so an empty or undiscovered bucket cannot appear green.

### B2.4: full compatibility envelope

B2.4 is the complete post-CI-overhaul-v2 envelope: C, shell, Rust, application,
runtime, lifecycle, filesystem, networking, signal, threading, and other
declared compatibility rows. The denominator is the resolved manifest set at a
named SHA, not a hand-maintained historical number.

Promotion requires all expected-pass rows to pass. It also requires the
harness to prove that the selected count equals the expected count. Optional
hardware lanes may be reported separately, but their absence cannot be used to
claim the corresponding capability.

### B3: at least 50% ptrace-corpus parity

B3 changes the question from backend-local compatibility to cross-backend
equivalence. Freeze the full ptrace reference corpus at one SHA and compare,
for every selected row:

- exit status and signal disposition;
- guest stdout and stderr;
- declared files, hashes, and protocol results;
- deterministic time, random, PID/TID, and scheduling observations; and
- any test-specific semantic result.

At least 50% of all reference rows must match ptrace. The denominator includes
unsupported and timed-out rows. B3 also requires B2.4, so a small favorable
subset cannot manufacture a majority claim.

### B4: 100% parity across all backends

Every supported backend must match the frozen ptrace reference on every corpus
row using the B3 comparison. No backend-specific expected output, silent
fallback, normalization of semantic values, or determinism relaxation is
allowed. Harmless launcher diagnostics may be separated from guest stderr, but
the separation rule must be uniform and versioned.

B4 is both an individual backend claim (that backend is 100% ptrace-parity
clean) and a release claim (all supported backends are B4 at the same revision).

### B4+: leading workloads

B4+ records frontier demonstrations after B4, for example deterministic Linux
boot and a declared userspace workload under QEMU. Each B4+ claim names its
workload, command, guest image digest, backend, output oracle, timeout, and
hardware requirements. A showcase does not compensate for a lower corpus
level and must never be used to skip B2-B4 gates.

## Evidence rules

Every promotion report must contain:

- exact Hermit, Reverie, and lower-level dependency SHAs;
- exact commands, backend selector, strict/verify flags, and relaxations;
- host kernel, architecture, PMU/KVM availability, and timeout policy;
- the resolved test list and denominator;
- per-row exit status and machine-readable output/artifact hashes;
- proof that the requested backend was active and did not silently fall back;
- separate backend-local verification and ptrace-parity totals; and
- retained raw logs or a durable experiment path.

Queued, skipped, stale, cancelled, or missing checks are not passes. Evidence
from different SHAs cannot be combined into a higher level without rerunning
the lower gates at the promotion SHA.

## Current backend levels

These are the highest levels proven by the latest common scorecard, not claims
about unmeasured newer commits.

**Measurement snapshot:** Hermit
`adbfaca337c7b404c772573b327a4e739212f89d`, Reverie
`f93bad17213609c85429613802ff367a2dd1f801`, recorded by parent commit
`4d6cadba3ade7fe6ff318d508c4d8398b0317de7` on 2026-07-28.

| Backend | Current proven level | Evidence and next failing gate |
| --- | --- | --- |
| **ptrace** | **B2.1** | The scorecard passes 5/5 examples locally and defines the ptrace single-run reference. No same-SHA result proves all 183 C manifest rows, so B2.2 is not claimed. |
| **KVM** | **B2 base** | It passes 5/5 backend-local example verification but matches ptrace guest output on only 2/5 (`rand.py`, `timed-progress-bar.py`). It therefore fails B2.1. The B1.5 audit also found child syscalls bypassing Tool callbacks. |
| **DBI** | **B2 base** | It passes 4/5 backend-local verification; `race.sh` changes schedule between its two runs. It matches ptrace output on 2/5 examples and therefore fails B2.1 before later corpus gates are considered. |
| **SaBRe** | **B2 base** | It passes 5/5 backend-local verification but matches ptrace output on only 1/5 (`timed-progress-bar.py`). Different time, random, and race outputs fail B2.1. |

No backend has a current evidence-backed B2.2, B2.3, B2.4, B3, B4, or B4+
claim under this model. KVM's 2/5, DBI's 2/5, and SaBRe's 1/5 example parity
rates are diagnostics for B2.1; they are not B3 measurements because the B3
denominator is the full frozen ptrace corpus.

## Evidence index

- [`transient/2026-07-28-examples-cross-backend-scorecard.md`](transient/2026-07-28-examples-cross-backend-scorecard.md), parent commit
  `4d6cadba3ade7fe6ff318d508c4d8398b0317de7`: authoritative B2.1 run.
- [`transient/2026-07-27-backend-b15-audit.md`](transient/2026-07-27-backend-b15-audit.md), parent commit
  `10fd339`: Reverie tool and process-tree evidence.
- [`transient/2026-07-27-backend-architecture-report.md`](transient/2026-07-27-backend-architecture-report.md), parent commit
  `ca217bf`: backend/RPC architecture and command evidence.
- [`../experiments/multibackend_compat_20260728/README.md`](../experiments/multibackend_compat_20260728/README.md), parent commit
  `207d611`: harder-program L2 gaps outside the examples scorecard.
- [`DBI_COMPAT_SWEEP_20260727.md`](DBI_COMPAT_SWEEP_20260727.md): 37-guest DBI diagnostic sweep.
- [`SABRE_COMPAT_SWEEP_20260727.md`](SABRE_COMPAT_SWEEP_20260727.md): SaBRe diagnostic sweep and preemption gap.

## Promotion checklist

1. Pin the Hermit and Reverie SHAs and resolve the manifest denominator.
2. Run all gates through the backend selector with fail-closed strict mode.
3. Verify backend-local repeatability.
4. Compare guest-visible results with the ptrace baseline.
5. Publish raw per-row results and hashes.
6. Assign only the highest cumulative gate that passed completely.
