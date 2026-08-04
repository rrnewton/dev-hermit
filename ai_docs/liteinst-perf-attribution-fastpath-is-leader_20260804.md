# LiteInst perf attribution: the patch fastpath is the LEADER; the "14.5x" was the retired legacy path

**Date:** 2026-08-04 · **Lane:** hermit-perf · **Task:** `liteinst-perf-attribution-fastpath` (P0)
**Question (owner):** LiteInst looked ~14.5x slower than a ptrace tracer. A patching
backend losing to ptrace by 14.5x is not a tuning gap — something must be broken
(patch fastpath not firing → every syscall falls through to the slow ptrace-plus
path). Attribute the time before optimising anything.

## TL;DR — the premise is REFUTED for the current backend, and CONFIRMED only for the retired one

Measured with the **same plain tool** as every other backend (landed, correctness-gated
counter2 shootout, [reverie#331](https://github.com/rrnewton/reverie/pull/331)
merge `a9f25aa7`, single host devbig014), **LiteInst's in-guest patch fastpath is
the FASTEST backend** — not 14.5x slow:

| Rank | Backend | Geomean slowdown vs native | Paired marginal ns/syscall |
| ---: | --- | ---: | ---: |
| 1 | **LiteInst** | **1.032x** | **583** |
| 2 | e9patch | 1.036x | 734 |
| 3 | SaBRe | 1.056x | 996 |
| 4 | KVM (reverie) | 1.597x | 14,501 |
| 5 | ptrace | 1.687x | 17,101 |
| 6 | DBI | 5.433x | 1,362* |

\* DBI's per-syscall cost is low (1.36µs), but its geomean is dominated by the
DynamoRIO code-stream instrumentation CPU floor (5.38x native CPU-heavy), not by
counter2 dispatch.

So at **~0.58µs/syscall**, the LiteInst patch fastpath is **~29x faster than
ptrace** and **~12x faster than gVisor systrap (~7µs)** — the owner's hope that a
patching backend leads like systrap is **already realized**. There is no broken
fastpath in the current in-guest backend.

**The "14.5x slower than ptrace" number was the LEGACY HOST HYBRID**, a genuinely
broken path that has since been replaced by the in-guest backend.

## Three distinct LiteInst numbers — conflating them is the whole confusion

| # | Configuration | per-syscall | Status |
| ---: | --- | ---: | --- |
| 1 | **Legacy host hybrid** (SIGTRAP→ptrace host validates marker + parses `/proc/<pid>/maps` + runs Tool) | **>450µs** (>45s / 100k, >11x ptrace) | **BROKEN, retired.** This is the owner's "14.5x." |
| 2 | **In-guest patch + Detcore `--strict`** (plain-tool patch fastpath fires, but every syscall round-trips to the Detcore coordinator) | **~14.2µs** (1.417s / 100k; 2.84x *faster* than ptrace) | patch fires; pays the full determinism RPC |
| 3 | **In-guest patch + plain counter2** (patch mechanism alone, no determinism) | **~0.58µs** (leader) | **the fastpath's true cost** |

The legacy hybrid was not a patch fastpath at all: its trampoline raised `SIGTRAP`
on **every** syscall and fell through to the ptrace host — i.e. literally
"ptrace-plus-overhead," exactly the owner's hypothesized failure mode. `perf record`
put its samples in the `int3`/ptrace-stop path and in
`reverie_ptrace::task::guest_maps` (the `/proc/<pid>/maps` parse in
`classify_liteinst_trap`), and it executed **7.24B instructions** for 10k calls vs
ptrace's own **1.48B** — ~5x *more* work than the tracer it was supposed to beat.

The current in-guest path installs the patch **once** (`traps=1, hooks=32`) and
then dispatches in-process — which is why #3 is 0.58µs.

## Where the `--strict` time goes (attribution of #2 vs #3)

The gap between #3 (0.58µs bare) and #2 (~14.2µs under `--strict`) is **~13.6µs of
Detcore coordinator RPC — a determinism cost at the coordinator, NOT a patch defect.**
`strace -f -c` on the in-guest `--strict` path shows a load-independent structural
slope of **+3 `sendto`, +5 `recvfrom`, +2 `epoll_wait` per intercepted syscall**
(the coordinator round-trip). ptrace and KVM pay their *interception* cost
(17.1µs / 14.5µs) which would then *also* stack the coordinator cost; LiteInst pays
only the coordinator cost on top of a near-free (0.58µs) interception.

**Consequence for the owner's optimisation question:** the lever for faster
*deterministic* execution is not the patch mechanism (already the leader) — it is
eliminating the per-syscall coordinator RPC via an in-process / shared-memory
Detcore fastpath (in-guest RCB read via `rdpmc`, in-process scheduling decision).
See `inguest-rcb-read-needs-rdpmc-not-ptrace-mmap-fastpath` and
`s1-inguest-rcb-preemption-is-cost-not-crux`.

## The KVM 1.01µs figure — RESOLVED (keep it separate)

The predecessor's unresolved "KVM 1.01µs" is **gvisor-kvm** from the idle-gated
Criterion `benchmark-v3` run (devbig030, 2026-07-26): gVisor's own **KVM platform**
running a counter1 *semantic equivalent* (one Go `atomic.Uint64` increment after
each `platform.Context.Switch`), measured at **0.91–1.08µs/syscall**. It is **not a
mockup** — it is the real gVisor KVM platform — but it is a *minimal-tool
platform-switch cost*, not gVisor's full sentry syscall emulation, and it is
**anomalously faster than gvisor-systrap (~7µs) in the same run**, which inverts
gVisor's own published systrap<KVM ordering. Treat it as unexplained and keep it
out of any table beside real backend numbers. It is a completely different quantity
from **reverie-kvm** (Hermit's KVM backend), measured at **9.97–13.18µs** in the
same run and **14.5µs** in counter2.

Reference frame reconciliation on the idle-gated benchmark-v3 host: gvisor-systrap
**~7.1–7.4µs** (matches the ~8µs reference), ptrace **16.8–17.3µs**, reverie-kvm
**10–13µs**, sabre **0.61–0.86µs**, dbi **1.0–1.5µs** — all cross-check the
counter2 ranking.

## Method status and caveats (non-negotiable per the owner)

- **The mandated 1-CPU sequentializing box is NOT ready.**
  `runner-cpu-affinity-single-core-runs` (= hermit-220) is Phase 2, blocked by
  `rust-runner-parity-catchup-and-real-crosscheck`, 0/1 complete. Per the owner's
  directive I am saying so rather than producing a blended number.
- **Sequentialization is not a confound in any number above.** All fixtures
  (counter2 workloads, `syscall_heavy`, the getpid loops) are **single-threaded**,
  so Hermit's thread-sequentialization penalty is ≈0 by construction. That penalty
  is a *separate axis* that only appears on multithreaded workloads and must be
  measured with the 1-CPU box once it lands — it does **not** contaminate the
  per-syscall instrumentation-overhead attribution here.
- **Load caveat.** The counter2 shootout ran under high background load; its
  *absolute* ns are host/load-sensitive. The **ranking** and **order of magnitude**
  are robust because each backend is normalised to native in the same run and the
  paired slope subtracts native work + the low-syscall CPU floor. benchmark-v3
  (idle-gated Criterion) corroborates the order independently.
- These are Reverie-level backend measurements (plain counter tools); no Hermit
  determinism level is claimed for #3. #2 is `--strict` (Detcore, L2 `--verify`
  37/37 pass on 1,000 calls).

## Relationship to PR #1443 (out of the perf lane)

The in-guest flagship implementation lives in Hermit PR #1443 / Reverie PR #330,
which an adversarial review FAILED on **determinism/landing** grounds (vDSO host-clock
leak under `--strict`, disabled timeslice preemption, non-building pinned tree). Those
are flagship-lifecycle blockers owned by the liteinst/flagship lane. **This perf
attribution is independent of whether #1443 lands** — the counter2 leader result (#3)
is on landed Reverie main (`a9f25aa7`) and stands on its own.

## Sources (all verified directly, integrity-checked)

- `experiments/backend-perf-attribution-20260802/` — counter2 shootout (landed
  reverie#331 `a9f25aa7`, framework `36ce950a`); `sha256sums.txt` verified OK. The
  single-run cross-backend table above (rank, ns/call).
- `experiments/benchmark-v3/results/REPORT.md` — idle-gated Criterion corroboration
  (devbig030); source of the gvisor-kvm "1.01µs" resolution and the frontier tiers.
- `ai_docs/kvm-perf-attribution-startup-not-vmexit_20260802.md` — reverie-KVM is
  startup/teardown-bound, ~1.3x ptrace steady-state; corroborates counter2 KVM.
- Hermit PR #1443 `benchmarks/liteinst-perf-attribution-2026-08-02.md` — the #1/#2
  legacy-vs-in-guest `--strict` numbers, `perf record`/`perf stat`/`strace` slope,
  and the load caveat (load avg 60–86).
