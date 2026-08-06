# Backend stats transport — map re-established on current main

**Task:** `unify_backend_stats_transport` (P2)
**Date:** 2026-08-06 · **Bound to:** reverie main **`025d3780`**
**Mode:** local audit. No code change, no egress, no validate.

The 2026-08-04 23:40 note asked for exactly this: *"Coupled inc-5 map is being (re)established on
CURRENT main because 18:58-22:42 design notes predate #328/#339/#348/#362 merges. Do not code
against the stale map."* Here is that map, measured.

## The contract already exists — and is more adopted than the notes say

`reverie/src/backend_stats.rs` (362 lines) defines the unification point: `BackendStatsSource`
(`:64`), `BackendStatsSnapshot` (`:58`), `BackendStatsRequest::collect` (`:49`), plus shared
`CounterSnapshot<K>` (`:226`), `PatchShapeStats` (`:116`) and `PatchShapeCollector` (`:165`).

| Backend | conforms to `BackendStatsSource`? | engine | site |
|---|---|---|---|
| **liteinst** | **yes** | RPC GlobalTool, `stats.sock` | `reverie-liteinst/src/stats.rs:490` |
| **dbi** | **yes** | in-crate source | `reverie-dbi/src/backend_stats.rs:625` |
| **sabre** | **yes** | shmem memfd page | `experimental/reverie-sabre-stats/src/lib.rs:389` |
| **e9patch** | **NO — 0 impls** | counters exist, no transport | see below |
| **kvm** | **NO — no stats surface at all** | — | — |

**The headline correction: DBI now has its own `BackendStatsSource`** (`reverie-dbi/src/backend_stats.rs`,
`DbiBackendStatsSource` at `:607`/`:619`/`:625`). The design-of-record notes describe only two
transports — "Family A (liteinst+e9patch)" RPC and "Family B (SaBRe)" shmem — and predate this. So
the design's central decision (*unify behind one **contract**, not one **wire***) is not a pending
proposal: **it is already realized for 3 of 5 backends, across three genuinely different engines.**
That is the strongest available evidence the contract-not-wire choice was right.

## The two real gaps

**e9patch — counters without a transport.** It produces real data:
`dispatch.rs:68 FALLBACK_TOTAL`, `:72 FALLBACK_BY_NUMBER[TRACKED_SYSCALLS]`,
`:243 FALLBACK_SITES: SiteTable<TRACKED_SITES>`, with live `fetch_add` at `:87`/`:91` and a reader
at `:101`. But `grep -c 'impl BackendStatsSource' reverie-e9patch/src/*.rs` → **0**. The numbers are
collected and then unreachable through the common contract. This is precisely the coupling the notes
identified: making `HostBackend::slowpath_counter()` non-`Option` forces this transport to exist.

**KVM — nothing.** No stats surface. (The `reverie-kvm/src/executor.rs:10519-10545` hits my first
sweep returned are `stat()`-syscall *test* code, not backend stats — a false positive worth
recording so the next audit doesn't re-chase it.) Given KVM's output-only fallback cannot establish
L2 anyway, its absence is defensible, but it should be a **declared** gap rather than a silent one.

## New risk the design notes do not cover: taxonomy drift

The whole point is stats *comparable across backends*. One contract is necessary but not sufficient —
the **categories** must also match, and they have drifted.

- Design of record (18:58 / 19:39 notes) fixes the taxonomy as `LiteinstDispatchPath` at
  `stats.rs:111`: `direct_hook` = FASTPATH; `first_site_seccomp` / `ptrace_installation` /
  `unpatchable_or_other` / `in_guest_sigsys` / `in_guest_nested_sigsys` / `cacheline_straddler` =
  SLOWPATH.
- What is at `reverie-liteinst/src/stats.rs` ~`:111-125` on current main is a **different enum** —
  `LiteinstPatchDecision` with `DirectPun` / `Relocated` / `StraddlerFallback` / `OtherFallback`.

So liteinst's own stats now carry at least two category systems, and the one the design pinned may
have been renamed or superseded. **Before wiring e9patch to the contract, fix which taxonomy is
canonical** — otherwise the unified transport will deliver incomparable categories through a common
interface, which is a worse failure than divergent transports because it *looks* uniform.

That is the same defect shape as this week's recurring finding: a value carried without its
condition.

## Recommendation

1. **Resolve the taxonomy question first** — `LiteinstDispatchPath` vs `LiteinstPatchDecision`. Cheap,
   and it gates comparability. The 19:39 confirmation should be re-issued against whichever survives.
2. **Wire e9patch's existing `FALLBACK_*` counters to `BackendStatsSource`** — the data already
   exists; only the impl is missing. Per the owner's 22:42 lock, surface them through the contract
   and **defer the RPC wire** to `e9patch_hybridptrace_inguest_converge`.
3. **Declare KVM's gap explicitly** rather than leaving it absent.
4. **Do not build a fourth engine.** Three engines already conform; the contract is the unification,
   exactly as locked.

## Why no code was written

- The work is **explicitly coupled**: it lands as ONE PR with `shared_inguest_toolhost_family`
  increment-5, on fresh main **after PR #373 merges** (owner sequencing, 21:54 note). #373's state is
  unverifiable this session — egress refused all session.
- `reverie/` is a **primary checkout**; Hard Invariant 1 forbids feature edits there and no slot is
  assigned to this task.
- Coding now would also code against a taxonomy that this audit shows is unsettled.

## Provenance

| Claim | Status |
|---|---|
| Contract shape + line numbers; 3 conforming implementors; e9patch 0 impls with live `FALLBACK_*`; KVM none; `LiteinstPatchDecision` on main | **measured this session** @ reverie `025d3780` |
| `executor.rs:10519` is `stat()` test code, not backend stats | **verified this session** (false positive) |
| Owner design lock (one contract, defer e9patch wire); #373 sequencing; `LiteinstDispatchPath` taxonomy | inherited from 2026-08-04 notes — **the taxonomy claim is contradicted by main, see above** |
| PR #373 state | inherited; **not verifiable — egress down** |
