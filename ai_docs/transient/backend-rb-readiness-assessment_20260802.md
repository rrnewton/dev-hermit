# Backend Reproducible-Build Readiness Assessment (recurring #126/#127)

**Date:** 2026-08-02
**Author:** hermit-243 (impl/research agent, opus-4.8)
**Task:** `backend-rb-readiness-assessment-overnight`
**Type:** Research/assessment only — NO code inflow.

## Question

Which of the leading **non-ptrace** backends (KVM, DBI, and the patching
candidates SaBRe / LiteInst / e9patch) is closest to being ready to try on a
**simple reproducible build** — the "RB perf-graduation path off ptrace"? For
each: current B-level, corpus %, key gaps to the next level, and RB-readiness.

## Method & evidence base

This is a synthesis of source-grounded, committed evidence measured over
2026-08-01/02; **no fresh full-corpus sweep was run**, deliberately — the
compat-envelope collector must not run against the shared primary checkout on a
loaded host (documented load-artifact incident: a denominator run under
loadavg ~850 produced spurious `status 124` timeouts). The load-bearing numbers
are re-bound here to the currently committed artifacts:

- **Cross-backend L2 determinism (unified 200-cell corpus, apples-to-apples)** —
  `compat-envelope/fullcorpus-scorecard.csv` (1200 rows, measured at hermit
  `82a8e853`, reverie `a4f33d69`, real `/dev/kvm`, portable-lane flags
  `--strict --verify --no-virtualize-cpuid --max-timeslice=disabled`). Re-tallied
  live for this report.
- **Cross-backend contract parity** — `hermit/tests/backend-parity/matrix.tsv`
  (23–24 hand-authored triple-pass contracts; the authoritative parity source).
- **B-levels** — `ai_docs/backend-maturity-architecture-report_20260801.md` +
  `hermit/docs/SABRE_COMPATIBILITY.md` (source-grounded).
- **RB workload facts** — `experiments/rb_nix_minimum_hermit_dose_20260730/` and
  `experiments/nix-hermit-container-approach_20260730/`.

## What a "simple reproducible build" actually requires

A build (make → cc → cpp/cc1/as/ld, or a serial `nix -j1 --cores 1`) is not a
single static ELF. Ranked by how load-bearing each capability is for RB:

1. **fork + `execve` chains** (mandatory). A build *is* a process tree of
   re-exec'd external tools. A backend that cannot fork-then-exec cannot run a
   build at all.
2. **Reproducible file I/O + entropy determinization** (the deliverable). The
   built artifact must be byte-identical across runs; any host leak
   (hostname, real pid, wall-clock, TSC, PRNG) that reaches the output breaks RB.
3. **Net wall-clock win vs ptrace** (the *point*). Graduating off ptrace is a
   performance move; a backend that runs the build but no faster than ptrace
   buys nothing for RB.
4. **Multi-thread / `-jN`** (nice-to-have; "simple" = serial `-j1` first). Needs
   timer preemption of non-yielding threads — deferrable for a first trial.

## Cross-backend scorecard

L2-det and parity are the committed 200-cell corpus figures (`82a8e853`).
"fork→exec" is the RB-critical process-model verdict from the parity matrix +
source. "Perf vs ptrace" is the runtime-overhead posture.

| Backend | B-level | L2 det (200) | parity (200) | backend-parity matrix | fork→exec build tree | Perf vs ptrace | RB-ready? |
|---|---|---|---|---|---|---|---|
| **ptrace** | **B4** (reference) | 179/200 (89.5%) | — (self) | 23/23 | ✅ full | 1× (incumbent, ~33µs/syscall, kernel-bound) | **incumbent** — runs real nix builds reproducibly today |
| **DBI** (DynamoRIO) | **B3** | 156/200 (78%) | 137/200 (69%) | 22/23 L1, 21/23 L2 | ✅ **proven at parity + L2** (m4; `multiprocess_fork_exec` contract) | **~16.5× faster/syscall**; **~11× slower** on branch-bound compute; ~40ms startup | **CLOSEST — recommended first RB trial** |
| **SaBRe** | **B3** | 164/200 (82%) | 142/200 (71%) | n/a (loader backend) | ⚠️ broadest non-ptrace process model; build-path fork→exec not explicitly blessed | no measured perf-leader data | **strong second candidate** |
| **KVM** | **B2** | 130/200 (65%) | 112/200 (61%) | 22/23 L1, 21/23 L2 | ❌ self-`/proc/self/exe` not virtualized; subprocess-spawn cells fail; python3 blocked | (flagship for full-VM, not RB) | **not near-term** |
| **LiteInst** | **B2** hybrid | 118/200 (59%) | 108/200 (54%) | n/a | ❌ **exec DENIED** (owner-gated); MT threads fail-closed; flat-fork only; runs in a ptrace host | n/a | **disqualified for builds** |
| **e9patch** | (preprocessing) | 179/200 (89.5%) | 173/200 (87%) | n/a | ✅ (tracks ptrace) | **runtime IS ptrace → ZERO runtime speedup** | **highest compat, but NOT a perf path** |

## Per-backend detail

### DBI — closest to RB-ready
- **Process model is the differentiator.** DBI is the *only* non-ptrace backend
  with **proven fork → execve → reap at ptrace parity and L2 byte-identical**
  (`hermit --backend dbi run --strict` on m4; locked in by the
  `multiprocess_fork_exec` cross-backend contract, matrix 23/24). This is exactly
  the build process tree.
- **Perf leader on the axis that matters for graduation:** ~16.5× faster per
  intercepted syscall (2.0µs vs ptrace 33µs), ~40ms DynamoRIO startup tax,
  crossover at ~1–2k syscalls. **Caveat (honest):** DBI is **~11× slower than
  ptrace on syscall-free compute** (structural code-cache dispatch, not fixable
  at the C counter). A compile is *both* syscall-heavy (I/O, spawn) and
  compute-heavy (parsing/codegen), so the net wall-clock on a real build is
  **not a guaranteed win** — it must be *measured*, not assumed.
- **RB-relevant leak to close first:** the **uname nodename host-FQDN leak** —
  DBI reports `devbig014` vs ptrace `hermetic-container.local`
  (`detcore/src/syscalls/misc.rs:592`, nodename rewrite gated on
  `!has_uts_namespace`; DBI reports `has_uts_namespace=true` but never sets the
  UTS hostname). If a build embeds the hostname (common in `config.h`/build
  stamps), this breaks byte-identical output. Fix = reverie-dbi UTS-namespace
  hostname inheritance (do **not** unconditionally force the default hostname).
- **Other gaps (deferrable for a serial trial):** DynamoRIO startup stall on
  native pthread startup (`pthread_lifecycle` gap → threaded builds risk a
  stall; start `-j1`); no timer preemption of non-yielding threads (hangs on
  busy-wait threads — serial builds don't need it); no network-namespace
  isolation.
- **B3→next gap = corpus breadth + the pthread-startup stall**, not the execution
  model.

### SaBRe — strong alternative
- **Highest L2 determinism among true runtime backends** (164/200), B3,
  source-grounded 131/194 = 67.5% strict-verify. Real loader backend
  (`libdetcore_sabre.so` + coordinator RPC; confirmed *not* a ptrace fallback).
  Described as the **broadest non-ptrace process model**.
- **Gap for RB:** the specific fork→exec build path is not blessed by a dedicated
  parity contract the way DBI's is, and there is **no perf-leader measurement**
  for SaBRe. Requires `--no-virtualize-cpuid --max-timeslice=disabled`.
- **Verdict:** worth a *parallel* RB trial; on determinism it edges DBI, but DBI
  has the proven build-shaped process model and the perf data.

### KVM — not the near-term RB path
- B2, det 130/200, parity 112/184. The build-critical capability — **exec of
  external tools** — is KVM's weak spot: `/proc/self/exe` is not virtualized
  (self-re-exec lands in the hermit supervisor binary), and subprocess-spawn
  cells (bash-loop, py/perl-io-subprocess, process-chains) fail. python3 is
  blocked behind an ordered chain (vfork barrier + CLONE_THREAD-bypasses-Detcore;
  partially fixed but unlanded).
- KVM is the flagship for `goal-qemu-linux-under-hermit` (full-VM), **not** the
  near-term userspace-build perf path.

### LiteInst — disqualified for builds
- B2 hybrid, det 118/200. **exec is denied (owner-gated)** and **MT thread-clone
  fails closed (ENOTSUPP)**; only flat `fork` works, and Detcore still runs in a
  **ptrace host**. A build is an exec chain, so LiteInst cannot run one today.
  The dominant unlock (MT threads, ~70% of its gap) plus exec are both
  owner-gated (flagship #1466 / vDSO-clock + bootstrap-FD blockers).

### e9patch — highest compat, but not a perf-graduation path
- **Critical:** `hermit run --backend e9patch` maps its **runtime to ptrace**
  (`runtime_backend()`: `E9patch → Ptrace`; the reverie-e9patch SIGSYS/hybrid
  runtime is dead code on the hermit path). All e9patch cost is one-time AOT
  e9tool rewriting; **at runtime it literally is ptrace, so it delivers ZERO
  speedup over ptrace.** Its near-perfect compat (183/184 = 99.46% on the
  ptrace-green denominator; only `rcx-canonicalization` inherent) is real but
  **irrelevant to the "off-ptrace perf" goal.** It is an AOT preprocessing stage,
  not a backend for RB perf.

## The RB workload itself (already partly proven)

- ptrace **already runs a serial nix build reproducibly today** — minimum dose =
  plain `hermit run --no-namespace` inside rootless podman (`nixos/nix:2.3.16`,
  hostlibs loader + `seccomp=unconfined`). `--strict` is optional for
  reproducibility (only adds fail-closed). Surprise: `--no-sequentialize-threads`
  is a ~5× *pessimization* for serial builds (forces the RCB/PMU path).
- **Two known workload blockers** (backend-independent): modern nix (2.35) needs
  `pidfd_send_signal` (nr 424), currently detcore-Unsupported → fail-closed;
  extending to a real `cc`-built package is **egress-blocked offline** (need a
  pre-seeded `/nix` store or an IPv4 mirror). A first DBI trial should therefore
  use pre-pidfd nix (2.3.16) or a self-contained / pre-seeded derivation.

## Recommendation

1. **Try DBI first on a simple *serial* reproducible build.** It is the only
   non-ptrace backend with a proven build-shaped process model (fork→exec→reap at
   parity + L2) *and* the syscall-perf lead that motivates leaving ptrace.
   Concrete trial: `hermit run --backend dbi --no-namespace` on the existing
   pre-pidfd rootless-podman nix recipe (`-j1 --cores 1`), witness = sha256 of
   the built output across N=3 fresh `/nix/store` runs, **and** record net
   wall-clock vs the ptrace baseline (do not assume the syscall win survives the
   ~11× compute penalty on a compile-heavy build).
2. **Close the uname nodename leak before trusting DBI RB output** (reverie-dbi
   UTS-hostname inheritance). It is the one identified determinism leak that a
   real build is likely to hit.
3. **Run SaBRe as a parallel candidate** — highest det among runtime backends,
   broadest process model; bless a fork→exec build contract for it and gather
   perf numbers to compare head-to-head with DBI.
4. **Do not pursue KVM, LiteInst, or e9patch for near-term RB.** KVM's exec gaps,
   LiteInst's missing exec/threads (owner-gated), and e9patch's ptrace-at-runtime
   design each disqualify them from the *perf-graduation-off-ptrace* framing,
   regardless of their compat scores.

**Bottom line:** DBI is the closest to RB-ready; the recommended next step is a
measured serial-build trial (artifact-hash reproducibility + net wall-clock),
gated on closing the uname nodename leak, with SaBRe evaluated in parallel.

## Evidence SHAs

- Unified 200-cell corpus: hermit `82a8e853`, reverie `a4f33d69`
  (`compat-envelope/fullcorpus-scorecard.csv`).
- Backend-parity matrix + maturity report: hermit main `0da50ed8`, reverie main
  `a4f33d6` (`ai_docs/backend-maturity-architecture-report_20260801.md`).
- SaBRe source-grounded compat: hermit `3bc2ab61`
  (`hermit/docs/SABRE_COMPATIBILITY.md`, 131/194 = 67.5%).
- RB minimum-dose experiment: `experiments/rb_nix_minimum_hermit_dose_20260730/`.
- DBI perf baseline: `experiments/dbi_perf_leader_baseline_20260801/`.
- Assessment written at parent main `505fa39`.
