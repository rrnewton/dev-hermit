# coordinator-rpc guest-side hop trim — real-path A/B (2026-08-04)

## Question
Does the guest-side syscall trim in reverie PR #369 (cache pid/tid via a
`pthread_atfork` flag instead of `getpid+gettid` every hop; collapse the
`1B + 3B` header framing into a single 4-byte read) measurably reduce the
det-mode coordinator-RPC hop latency on the **real** reverie/Detcore path?

The transport-layer ceiling microbench
(`experiments/coordinator-rpc-leverb-ceiling_20260804/`) predicted the hop
could move `uds_full 12.33us -> uds_lean 4.40us` (same-core). This experiment
tests whether that ceiling transfers to the real hop.

## Method (controlled .so swap)
Identical hermit binary; ONLY `libreverie_liteinst.so` differs, selected via
`HERMIT_LITEINST_RUNTIME`:
- BEFORE = pinned base reverie `04a46b43` release .so
- AFTER  = branch `975c9fa8` (PR #369) release .so
Rejected `base-artifacts/base-preload.so` as BEFORE — different shape / e9patch
symbols would confound.

Probe: `yield_loop` (`sched_yield -> end_timeslice_with_sched_yield ->
resource_request` = 1 coordinator hop/iter; anchor `hermit/detcore/src/lib.rs:703`).
Per-rep marginal slope over N in {20000, 120000} subtracts process startup.
BEFORE/AFTER interleaved within each rep (same load window). K=8 de-contended
(K=2 gives pure noise — coordinator/reactor/ptrace-host threads eat
load-dependent CPU under 2-core oversub and swamp the few-us signal).
8 interleaved reps. 316-core box (devbig014), loadavg 20–42 during the batch,
concurrent drain/validate.

`measure_v3.py` is the harness; `src/yield_loop.c` the probe;
`median-anchors-v3.json` the raw medians/IQRs.

## Results (us/hop, median [p25–p75] over 8 interleaved reps)
| metric | BEFORE | AFTER | Δ p50 | reduction |
|--------|--------|-------|-------|-----------|
| wall   | 66.60 [66.20–69.80] | 67.05 [66.70–68.10] | +0.45 | **-0.7%** |
| cpu    | 66.55 [65.20–69.90] | 67.05 [66.70–68.10] | +0.45 | -0.8% |
| sys    | 44.25 [43.20–46.20] | 44.85 [43.70–45.30] | -0.35 | -1.4% |
| user   | 22.30 [21.80–23.70] | 22.80 [22.70–23.10] | -0.45 | -2.2% |

IQRs fully overlap. A true 3.5us (cross) or 7.9us (same-core) saving would push
AFTER below BEFORE p25 — NOT observed.

## Interpretation — PERF PREMISE REFUTED on the real path
The real det-mode hop is ~67 us/hop, and BEFORE ≈ AFTER within noise. The
C-microbench `12.33 -> 4.40us` is a **transport-layer ceiling that does not
transfer**: the real hop's ~55us+ floor is Detcore scheduler logic + ptrace host
+ tokio reactor — none of which the standalone transport microbench models.
PR #369 removes `getpid + gettid + 1 read` (confirmed by source diff), but those
are a small, cross-core-cheap fraction of ~67us, so the saving (<~2us) is buried
and indistinguishable from zero. Corroborates existing findings
(`detcore-coordinator-rpc-codegen-inert-kernel-bound`,
`coordinator-roundtrip-reduction-scope`,
`coordinator-rpc-leverb-ceiling-rejects-transport-rewrite`): the coordinator RPC
is kernel/scheduler-bound. Only lever A (owner-gated in-guest quiescence, which
removes the context switch) can move the real hop; lever B (transport rewrite)
is already rejected; and the guest-side syscall trim is now ALSO shown inert on
the real path.

## Disposition
PR #369 is behavior-preserving, passes all gates (Regular + Host-dependent +
merge-gate green), and removes real syscalls — defensible as **LOW-RISK CLEANUP,
NOT a measurable coordinator-RPC latency win**. Do not sell as a perf win.

## Caveats
- strace syscall-count/hop BLOCKED: nested ptrace conflicts with liteinst
  host-hybrid ("failed to open pidfd for LiteInst tracee -110 ETIMEDOUT").
  In-guest `CLOCK_MONOTONIC` unusable (Detcore virtualizes guest clock). The
  `-getpid/-gettid/-1read` reduction is confirmed by SOURCE DIFF only, not
  independently counted on the real path.
- The `delta.p75 = 48.4` entries in the JSON are an artifact of independent
  per-quantile subtraction across reps, not a real tail regression; the
  paired-within-rep signal is the p50/p25 columns.

## Reproduction
```
python3 measure_v3.py          # requires the two .so paths in median-anchors-v3.json
```
Rebuild AFTER .so: `cargo build -p reverie-liteinst --release` on branch
`perf/coordinator-rpc-guest-side-trim` (975c9fa8). BEFORE .so: same crate at base
`04a46b43`.
