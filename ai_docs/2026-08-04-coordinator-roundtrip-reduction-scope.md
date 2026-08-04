# Coordinator round-trip reduction — scope (source-read only)

**Task:** `p1_perf_coordinator_roundtrip` (hermit-perf lane). Research/scope-only.
**Method:** SOURCE-READ ONLY. No build, no benchmarks, no box load, nothing pushed.
**Pins read:** hermit `c369be3f`, reverie `04a46b43`.
**Date:** 2026-08-04 (coordinator/opus-4.8).

Follow-on to the CLOSED sibling `detcore_strict_coordinator_rpc`
(`experiments/coordinator-rpc-codegen-sensitivity_20260804/`, memory
`detcore-coordinator-rpc-codegen-inert-kernel-bound`), which proved **codegen is
INERT** for the deterministic-mode floor (floor is ~65% kernel / ~35% user sys
time, so in-guest handler codegen cannot reach it). This task reframes: **the
deterministic-mode floor is the COORDINATOR PROTOCOL, not the backend.**
LiteInst's ns-scale in-guest fastpath sits *under* a ~13.6µs (load-inflated:
observed ~62µs on the 316-core shared box) round-trip, so the backend does not
gate det-mode perf on scheduling-heavy workloads.

---

## THE COUNT (state before proposing anything)

- **Logical layer: exactly 1 blocking request/response round-trip per SCHEDULING
  syscall.** Ordinary/PassThrough syscalls (e.g. `getpid`) incur **ZERO**
  round-trips (this is the vacuity trap from the sibling — a coordinator-side
  `getpid` probe is not an RPC).
- **Gated on** `cfg.sequentialize_threads`.
- **Fires at exactly 3 call sites** (`hermit/detcore/src/lib.rs`):
  - **`lib.rs:703`** — timeslice-end / `sched_yield` / yield / priority
    change-point. **THE hot site** — the 62µs round-trip.
  - **`lib.rs:1126`** — inbound signal delivery. One hop per delivered signal;
    **cold path** (signals rare in compute/syscall workloads).
  - **`lib.rs:1524`** — HappensBeforeCheckpoint. Fires **ONLY** when
    `config.happens_before` carries syscall-count anchors (race-reproduction
    runs). **ZERO** round-trips in ordinary deterministic execution; not a
    general-perf path.
- `resource_release_all` (`tool_global.rs:2152`) is **NON-blocking** (no wait).

Path: `resource_request` (`tool_global.rs:2119`) → `send_and_update_time`
(`:2066`) → `guest.send_rpc((time, mm, request)).await` (`:2076`).

## PER-HOP KERNEL COST (guest side, ~7 syscalls, common non-fork path)

Chain: detcore `send_rpc` → `LiteinstGuest::send_rpc`
(`reverie/reverie-liteinst/src/tool_host.rs:632`) → `CoordinatorRpc::send_rpc`
(`reverie-liteinst/src/rpc.rs:113`) → `BlockingRpcClient::try_send_rpc`
(`reverie/reverie-rpc-transport/src/blocking_client.rs:87`).

1. `getpid` (`rpc.rs:114`) — fork detection (compares `connection.pid != pid`)
2. `gettid` (`rpc.rs:115`) — reconnect-after-fork trigger
3. `write(4B header)` (`blocking_client.rs:123`)
4. `write(payload)` (`:124`)
5. `read(1B probe)` (`:132`) — clean-EOF detection
6. `read(3B rest of header)` (`:140`)
7. `read(payload)` (`:147`) — **BLOCKS here until coordinator replies = the
   context switch / wakeup**

**Transport = length-prefixed bincode frame over a blocking `UnixStream`
(UDS)**, talking to a **tokio-epoll** coordinator.

> **CORRECTION to the sibling artifact / old strace:** the `pidfd_open` +
> `clock_nanosleep` + `openat` syscalls in the old strace are
> **COORDINATOR-side** (deterministic virtual-time + guest-thread management),
> **NOT** the guest send path. The guest path is plain UDS `read`/`write`.

## BUSY-POLL: REFUTED

The coordinator serve loop is **tokio async, not a spin**: `RpcServer::serve`
(`reverie/reverie-rpc-transport/src/server.rs:132`) `accept().await` → spawn one
task per connection → `serve_connection_inner` loops on
`read_message(&mut stream).await` (`:214`). Async read ⇒ epoll-backed ⇒ the
coordinator **BLOCKS in `epoll_wait`** when no request is pending. Corroborated
by the flagship strace's `+2 epoll_wait per intercepted syscall` (that
`epoll_wait` IS the tokio reactor). LiteInst drives it via `serve_rpc_until`
(`reverie-liteinst/src/backend.rs:602`).

⇒ **Both sides BLOCK.** The measured **CPU/wall ~102% is NOT busy-poll**; it is a
serialized **2-thread ping-pong**: A runs, wakes B, A blocks, B runs, wakes A —
the two essentially never run concurrently, so wall ≈ sum of alternating CPU
bursts + context-switch/scheduler sys-time, with near-zero TRUE idle because each
handoff immediately makes the peer runnable. The 65% sys is that context-switch +
UDS + epoll machinery.

---

## REDUCIBILITY — three levers, source-grounded

### Lever (a) — reduce the COUNT (in-guest quiescence fast-path). HIGHEST CEILING.

Site 703's coordinator-side answer (`recv_request_resources`,
`tool_global.rs:1050` → scheduler run_queue `scheduler.rs`) is
**deterministically "resume the same thread"** when the run_queue has exactly ONE
runnable thread **AND** no timed/futex/io waiter is due at the current logical
time (occupancy discriminants: `scheduler.rs` `is_empty:253`,
blocked/futex/timed pools `:255-269`).

⇒ **ELIDABLE CASE = fully quiescent except this thread** (narrower than "single
runnable": must also have NO pending timed/futex/io waiter, because those become
runnable at a specific logical time the guest doesn't locally know). For a
**single-threaded guest, every site-703 yield is a VACUOUS round-trip** to a
coordinator that can only answer "keep running you." An in-guest fast path could
skip it if the guest observes "I am the only runnable thread AND no waiter is
pending" via a coordinator-maintained shared-memory runnable-count / quiescence
flag (updated only when another thread becomes runnable or a waiter is armed).

- **Removes the hop itself** — the only lever that does.
- **Covers pure single-threaded compute/syscall/yield workloads** — exactly the
  shape that produced the 62µs (the `sched_yield` probe is single-threaded,
  explicitly yielding every iteration = worst-case vacuous round-trips), plus
  uncontended stretches of multi-threaded guests.
- **RISK: core-DetCore-scheduling change ⇒ `post-facto-human-review` TRIGGER 4
  (highest bar).**

### Lever (b) — reduce PER-HOP kernel cost (shared-mem ring + futex). SAFE INCREMENTAL.

Replace the ~7 guest syscalls + tokio-reactor epoll round-trip with ~1
`futex_wait` / ~1 `futex_wake`. Also trims the guest-side fat independently:
- `getpid`+`gettid` every hop (`rpc.rs:114-115`) are pure fork-detection — cache
  pid / detect fork via a cheaper signal ⇒ −2 syscalls/hop.
- 3-read framing (1B probe + 3B + body) could be 1 `recv` ⇒ −2 syscalls/hop.
- Best-case guest-side ~7 → ~3 syscalls without touching the transport.

- **Bounded win: still one wakeup + one context switch per hop.** Does NOT
  eliminate the hop.
- **RISK: transport change, lower review bar than (a).**

### Lever (c) — avoid the WAKEUP (coordinator spins a shared-mem mailbox). DEAD as stated.

Premised on "the coordinator already spins" — **REFUTED above**: it blocks on
epoll. Making the coordinator busy-poll a shared-mem mailbox would cost a
dedicated core (bad on a shared box). Eliminating the wakeup requires lever (a)
instead.

---

## FLOOR IS WORKLOAD-SHAPED (reframe)

The "floor dominates ALL backends" claim is workload-shaped. Site 703 fires on
yields / timeslice-preemptions / multi-thread handoffs. Under **LiteInst
specifically the RCB clock is fixed at 0 and timer preemption does not deliver**
(`reverie-liteinst` CLAUDE.md), so a single-threaded **non-yielding** compute
loop barely round-trips; the floor bites **yield-heavy or
multi-thread-handoff** workloads. **State the workload when citing the floor.**

## Caveats

- **Absolute 62µs is load-inflated** (316-core shared box). Only the ~35/65
  user:sys ratio is robust (inherited from the CLOSED sibling).
- The hypotheses about lever (a)/(b) magnitudes are **NOT proven** — no
  measurement was run. Any build should gate a bounded measurement, and must not
  fan out during box-capped hours (validate cap 6).

## Recommendation

If the lane pursues reduction: **lever (b)** is the safe incremental (transport
change, bounded win); **lever (a)** is the high-ceiling bet (removes the hop for
single-threaded / quiescent workloads) **but gate on the trigger-4 human
review**. **Lever (c) is dead.**

## Key file:line map (saves rediscovery)

- `resource_request` `hermit/detcore/src/tool_global.rs:2119` →
  `send_and_update_time` `:2066` → `guest.send_rpc` `:2076` → `receive_rpc`
  `:607` / `RequestResources` `:705` / `recv_request_resources` `:1050`
- Call sites `hermit/detcore/src/lib.rs:703` / `:1126` / `:1524`
- Guest send `reverie/reverie-liteinst/src/tool_host.rs:632` →
  `reverie/reverie-liteinst/src/rpc.rs:113` (CoordinatorRpc) →
  `reverie/reverie-rpc-transport/src/blocking_client.rs:87` (try_send_rpc),
  framing `:118`/`:129`
- Coordinator serve `reverie/reverie-rpc-transport/src/server.rs:132` / `:214`;
  liteinst drive `reverie/reverie-liteinst/src/backend.rs:602` `serve_rpc_until`
- Scheduler occupancy `hermit/detcore/src/scheduler.rs` `is_empty:253`, pools
  `:255-269`
