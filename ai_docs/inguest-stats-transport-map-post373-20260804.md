# In-guest stats-transport map (reverie main @ 8688189a, post-#373)

Author: impl agent, opus-4.8, 2026-08-04 ~23:55Z. Tasks: `shared_inguest_toolhost_family` (inc-5) +
`unify_backend_stats_transport` (coupled).

**Why this doc exists (durability fix):** the predecessor's 23:40Z "IN-FLIGHT CODE-SEARCH" note warned
that the search result returns ONLY to the spawning context and does NOT auto-write — so the map was LOST
across recycling. This file re-establishes it on fresh main and PERSISTS it. Every claim is file:line at
this checkout so a third party can open the file and check it. Re-run basis: fresh
`git -C reverie rev-parse origin/main` = `8688189a87f11447a88d6f0e298a756c5f853cb0`; slot
`worktrees/inguest/reverie` clean at that SHA on branch `feat/inguest-toolhost-counter-seam`.

Companion design docs (do not duplicate): `in-guest-rcb-accounting-zero-ptracer-design-20260804.md`
(RCB dance + zero-ptracer acceptance gate), `shared-inguest-toolhost-build-spec-20260804.md` (inc-5 spec),
`patching-backends-ptrace-on-syscall-path-audit-20260804.md`,
`patching-backends-zero-ptracer-three-bucket-classification-20260804.md`.

---

## 1. Shared driver — `reverie-preload/src/tool_host.rs`

Public items: `drive_ready` (:86), `enum SyscallOutcome` (:102), `drive_syscall` (:140),
`enum DrivenSyscall` (:189), `drive_tool_syscall<T,G>(tool,guest,syscall,number,tail)` (:222),
`struct TailResult` (:284, methods set_result :293 / set_exit :299 / set_fork_child :308).
Private: `classify_outcome -> Option<DrivenSyscall>` (:249, None=restart), `enum TailAction` (:119).

**Seam status — the inc-5 hole, confirmed:** NO `HostBackend` and NO `HostSyscallEvent` trait exists yet.
NO slowpath/fallthrough counter hook exists (neither `Option<..>` nor non-`Option`). The module doc
(:44–54) names them as future work and states the counter accessor "must be **non-`Option`** so a
converging backend cannot silently drop per-path counts — that invariant is a hard requirement." So the
non-Option counter seam is documented-but-unbuilt. This is exactly what inc-5 must add.

## 2. LiteInst pipeline — `reverie-liteinst/src/stats.rs` + exit site

- Taxonomy `enum LiteinstDispatchPath` (:47–63), 7 variants: FirstSiteSeccomp, PtraceInstallation,
  InGuestSigsys, InGuestNestedSigsys, CachelineStraddlerFallback, UnpatchableOrOtherFallback, DirectHook.
- Producer: `GuestStatsHooks` (:277) `.submit` (:310–329) → `BlockingRpcClient::<LiteinstStatsGlobal>::connect`
  → `LiteinstProcessStats{paths:[u64;IN_GUEST_PATH_COUNT], sites}` (:388–392).
- GlobalTool: `LiteinstStatsGlobal` (:395–398), `#[reverie::global_tool] impl GlobalTool` (:406–421),
  `receive_rpc` (:412–420). Aggregation `into_source` (:423–482) → `CounterSnapshot<LiteinstDispatchPath>`.
- `impl BackendStatsSource for LiteinstBackendStatsSource` (:490–496).
- Exit path: `runtime::submit_process_stats` (`runtime.rs`:953–975, `.submit` at :974), called from
  `tool_host.rs` `finish_tool_exit` (:435–439).
- Verdict: the transport PLUMBING (BackendStatsSource / CounterSnapshot / PatchShape*) is SHARED in
  `reverie/src/backend_stats.rs`; the INSTANCES (LiteinstDispatchPath / RPC GlobalTool / exit-submit) are
  liteinst-specific. This is the "unify behind ONE contract, not one wire" surface.

## 3. e9patch — `reverie-e9patch/src/dispatch.rs` + crate

- Only C-ABI observability atomics: `FALLBACK_TOTAL` (:68), `FALLBACK_BY_NUMBER[512]` (:64/:72),
  `FALLBACK_SITES[256]` (:112/:243); getters :100/:262/:271/:279; recorders :85/:250; reset :302.
  Dispatcher records a fallback only when `event.source()==SignalTrap` (:361–364) then delegates to
  passthrough. C-ABI exports in `lib.rs` (:292/:304/:323/:337).
- **NO `BackendStatsSource` impl and NO exit-time stats submission anywhere in the e9patch crate**
  (dispatch.rs/runtime.rs/backend.rs/lib.rs read in full).
- In-guest path GATED OFF: `install_hybrid_runtime` returns `io::ErrorKind::Unsupported`
  (`runtime.rs`:259–262; doc :251–254 "ptrace performs all event handling").
- Consequence for inc-5: making the seam counter non-Option FORCES e9patch to surface its REAL FALLBACK_*
  via the same contract; the RPC exit WIRE is unvalidatable E2E now (in-guest gated Unsupported) → wire
  DEFERS to `e9patch_hybridptrace_inguest_converge` (owner decision, 22:42 note). Counter surface != wire.

## 4. Shared contract — `reverie/src/backend_stats.rs`

`trait BackendStatsSource{type Snapshot; fn backend_stats(&self)->Self::Snapshot}` (:64–70);
`trait BackendStatsSnapshot: Display{const BACKEND_NAME}` (:58–61); `CounterSnapshot<K>` (:226),
`PatchShapeStats` (:116), `PatchShapeCollector` (:165). Impls: FakeSource (:275, test),
LiteinstBackendStatsSource (`reverie-liteinst/src/stats.rs`:490),
DbiBackendStatsSource (`reverie-dbi/src/backend_stats.rs`:625, out-of-proc fixed wire-record aggregator),
SabreStats (`experimental/reverie-sabre-stats/src/lib.rs`:389).

## 5. SaBRe — `experimental/reverie-sabre-stats/src/lib.rs`  ⚠ CORRECTION TO DESIGN-OF-RECORD

- Transport = shared-memory sealed page (memfd+mmap), NOT RPC: `SabreStats::create` (:227–257,
  memfd_create :232 / ftruncate :242 / F_ADD_SEALS :250–251), `SharedStats::map` (:179–197),
  `from_inherited_fd` (:303–317). `impl BackendStatsSource for SabreStats` (:389–421).
- **CORRECTION:** SaBRe conforms to the shared `BackendStatsSource` / `CounterSnapshot` PLUMBING, but its
  TAXONOMY does NOT match `LiteinstDispatchPath`. SaBRe has its own `enum SabrePatchRoute` (:40–47) and
  `enum SabreSlowPath` (:62–83, SIGILL-marker / ptrace-centric variants). The 18:58–22:42 notes' repeated
  claim that "SaBRe conforms to the SAME taxonomy" is REFUTED: it conforms to the same CONTRACT, not the
  same fast/slow path enum. Implication: "one contract" is right; "one shared taxonomy across all backends"
  is NOT what exists — each backend keeps its own path enum, unified only at CounterSnapshot<K>.

## 6. Vacuity guard `direct_hook==N+1 && ptrace_installation==0` — CONFIRMED ABSENT

Not present in stats.rs, runtime.rs, backend_stats.rs, e9patch/dispatch.rs, or liteinst tests/hybrid.rs.
Tests assert raw counts (e.g. `dispatch_path_counts()==[..]`) but no such compound guard. Predecessor's
22:32 "guard does not exist, spec sec5 'keep it' is wrong" is CONFIRMED. (Bounded negative: no whole-tree
content-grep tool was available in-slot — biggrep not onboarded — so this is grounded in the files where
the guard would live, all opened.)

## 7. Architecture-gate cross-check (owner priority: ptracer OUT first)

- **e9patch = 100% ptrace on the syscall path TODAY** — the owner's cited defect. `install_hybrid_runtime`
  → Unsupported (`runtime.rs`:259–262); production `Backend::run` drives via `spawn_tracer::<T>` →
  `TracerBuilder::<Detcore>` (backend.rs), banners `event_source=ptrace`. Removal is owned by
  `e9patch_hybridptrace_inguest_converge` (NOT these stats tasks), now UNBLOCKED by #373.
- **liteinst = hybrid** — hot calls in-guest, but first-site install (`traps=1`) + straddler/mapping-churn/
  unpatchable fallbacks + CPUID/RDTSC/clone3/vfork/exec/timer(RCB=0) still route to the ptrace host
  (per `reverie-liteinst/CLAUDE.md` Supported Boundary).
- **Fail-closed = in-guest SIGSYS, NOT ptrace (GOOD, matches owner requirement):** reverie-preload owns
  seccomp filter (`seccomp.rs`:78, `SECCOMP_RET_TRAP` :38) + SIGSYS handler (`signal.rs`:42, alt-stack :68);
  dispatcher distinguishes `SyscallEventSource::{SignalTrap,DirectInstrumentation}` (`dispatch.rs`:25–30).
  LiteInst's un-instrumented escape fails closed with `-EOPNOTSUPP` (`runtime.rs`:861–905), not a ptrace trap.

## 8. Sequencing implication (per owner "ptracer OUT first, measure AFTER")

The inc-5 counter-seam / stats-transport is MEASUREMENT-adjacent → it is the "measure AFTER" half and
should NOT jump ahead of the ptracer-out work. The genuine architecture-gate blocker (e9patch 100% ptrace)
is `e9patch_hybridptrace_inguest_converge`. The in-guest RCB accounting (companion design doc) is part of
ptracer-OUT/determinism (not perf measurement) and is unblocked now (C1 satisfied), gated only on the rdpmc
visibility change (C2, reverie #363). Both coupled stats tasks stay `in_progress`; no code written yet.
