# Coordinator-RPC lever-B ceiling microbench

**Task:** `perf_coordinator_roundtrip_reduction` (lever B: shared-mem ring + futex).
**Motivating scope:** `ai_docs/2026-08-04-coordinator-roundtrip-reduction-scope.md`.
**Verdict up front: the transport rewrite (lever B) is NOT worth it. The cheap
guest-side trim captures ~80% of the removable cost with no transport change.**

## Question

The det-mode coordinator RPC hop is 1 blocking round-trip per scheduling syscall,
~7 guest-side syscalls (getpid+gettid fork-detect, framed write×2, framed read×3
with the last read blocking = the wakeup). Lever B proposes replacing the
blocking-UnixStream + tokio-epoll transport with a shared-mem word + futex.
**Before writing any transport,** measure the ceiling: how much can it remove,
and how much of that needs the rewrite vs a cheap guest-side edit?

Key structural fact (from the scope): **every comparand pays the same two context
switches** — the cross-thread hop. Lever B does **not** remove the ctx switch
(only lever A, the owner-gated in-guest quiescence fast-path, does). So the
measured deltas bound only the removable transport/framing/reactor overhead
stacked on top of that shared, irreducible cost.

## Method

Standalone 2-thread ping-pong (`src/rpc_ceiling_bench.c`), K=1, 200k iters, 20k
warmup, 64B payload, per-round-trip `CLOCK_MONOTONIC`, medians + IQR. Three
comparands, each run same-core-pinned (both threads on CPU0 — the serialized
handoff that matches the det-mode 2-thread ping-pong) and cross-core (CPU0/CPU1):

| mode       | models                                                                 | ~syscalls |
|------------|------------------------------------------------------------------------|-----------|
| `uds_full` | getpid+gettid + framed write×(1B+3B+payload) + read×(1B+3B+payload)     | ~8*       |
| `uds_lean` | 1 write + 1 read, no getpid/gettid, no probe framing                    | 2         |
| `futex`    | shared-mem word + `SYS_futex` WAIT/WAKE                                 | ~2        |

\* harness fidelity caveat: `uds_full` splits the header write (1B+3B) so it does
3 writes vs the real guest's 2 (one `write(4B)`); ~1 extra syscall, a few hundred
ns. Does not change the conclusion.

## Conditions (state with every number)

- **1-CPU semantics:** threads pinned to a single core (`same`) or two cores (`cross`).
- **Box:** devbig014, 316 cores, kernel 6.18.39; gcc 11.5.0.
- **Concurrent load at measurement:** loadavg 1m 53.2 → 47.0 (drain validates
  carrying the box). Single K=1 bench process, no fan-out.
- **Absolute ns are load-inflated**; the **inter-mode deltas at the same pin**
  are same-run/same-load and are the robust signal.

## Results (µs/hop, p50; IQR = p25–p75)

| mode / pin        | p50 µs | IQR µs        |
|-------------------|-------:|---------------|
| uds_full  / same  | 12.33  | 11.18–14.85   |
| uds_lean  / same  |  4.40  |  4.24–5.52    |
| futex     / same  |  2.38  |  2.21–2.51    |
| uds_full  / cross |  7.58  |  6.43–8.19    |
| uds_lean  / cross |  4.09  |  3.70–4.88    |
| futex     / cross |  3.28  |  3.03–3.51    |

`uds_full/same = 12.33µs` lands right on top of the independently-observed
~13.6µs det-mode floor — cross-check that the harness measures the right thing.

## Decomposition (the decision)

Same-core (matches det-mode serialized handoff):

- **d(uds_full → uds_lean) = 7.93 µs** — cheap guest-side trim (drop
  getpid+gettid fork-detect, collapse the 3-read framing to one recv). **NO
  transport rewrite.** ~80% of the total removable cost.
- **d(uds_lean → futex) = 2.01 µs** — the transport-swap-**specific** saving.
  What a full shared-mem+futex rewrite actually buys, on top of the cheap trim.
- **d(uds_full → futex) = 9.94 µs** — full lever-B ceiling.

Cross-core: d(full→lean) = 3.50 µs, **d(lean→futex) = 0.81 µs**, d(full→futex) =
4.31 µs. The rewrite-specific saving is even smaller cross-core.

The `futex/same = 2.38µs` is the floor of a 2-thread ping-pong on one core — the
**irreducible cross-thread hop cost lever B cannot remove** (2 context switches +
futex overhead). Lever B can only shave transport/framing overhead *above* it.

## Verdict & recommendation

**Do NOT build the shared-mem+futex transport.** The decision rule (README of the
prep harness): if `d(uds_lean → futex)` is small, take the cheap guest-side trim
instead. It is small — **0.8–2.0 µs**, versus **3.5–7.9 µs** for the trim that
needs no rewrite, and both sit on a ~2.4 µs irreducible ctx-switch floor.

**Actionable win surfaced by the data — cheap guest-side trim (small reverie PR,
no core-abstraction change):**
1. Cache pid/tid instead of `getpid`+`gettid` every hop for fork detection
   (`reverie/reverie-liteinst/src/rpc.rs:114-115`).
2. Collapse the 3-read header/payload framing (1B probe + 3B + body) into a
   single `recv` (`reverie/reverie-rpc-transport/src/blocking_client.rs:129-147`).

This captures the majority of the removable per-hop cost. The residual ~2.4 µs is
the context switch itself — removable only by **lever A** (in-guest quiescence
fast-path), which is owner-gated (post-facto-human-review trigger 4).

## Reproduction

```
cd experiments/coordinator-rpc-leverb-ceiling_20260804/src
cc -O2 -pthread -o rpc_ceiling_bench rpc_ceiling_bench.c
./rpc_ceiling_bench 200000 20000 64 | tee ../results.csv
```
Do not run under a fleet validate pause / host-wide-zero window. Deltas are
robust to load; absolutes are not.
