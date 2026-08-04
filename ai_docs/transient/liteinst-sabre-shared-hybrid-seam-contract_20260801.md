# Shared hybrid seam: LiteInst + SaBRe DRY contract (pre-impl design)

- Date: 2026-08-01
- Status: **pre-implementation design discussion** (Reverie API Policy gate for
  interception/lifecycle changes; post-facto-human-review trigger #2). Owner
  authorized co-developing the shared seam now, HybridPtrace gated OFF, draft PR
  marked parked-WIP. First thin-adapter *target* deferred to hermit-liteinst's
  ranked diagnosis.
- Authors/owners: hermit-243 (LiteInst native-path lane), hermit-sabre
  (SaBRe/shared-primitives), hermit-liteinst (diagnosis).
- Base: Reverie pin `aa6f1283aeee3efd174c57f6dd8198310bd307e1`; Hermit `origin/main`.
- Companion: `ai_docs/liteinst-native-path-decision-and-shared-hybrid-design_20260801.md`
  (architecture reality + gap list).

## Purpose

Factor the ptrace-hybrid lifecycle machinery **once** so LiteInst and SaBRe share
it, with only the instrumentation layer differing. This is needed for the
flagship coverage gaps (pre-main/exec/clone/vDSO) regardless of which gap is
closed first, so it is built now; the specific first gap the LiteInst adapter
closes waits on the diagnosis.

## What is genuinely shared vs. backend-specific (grounded)

Shared (factor once):
- **Ptrace lifecycle/supervisor**: spawn + `PTRACE_TRACEME`, wait for exec-stop,
  install seccomp **pre-main**, `PTRACE_EVENT_{EXEC,CLONE,VFORK,FORK}` handling,
  syscall-stop slow-path decode, trusted-mapping check, child bootstrap, readiness
  + physical-exit supervision. (SaBRe today: `hermit-cli/src/sabre_ptrace.rs` +
  `lib.rs::run_sabre`/`shutdown_sabre_rpc`.)
- **In-guest hybrid guest-runtime**: in-guest Tool dispatch, fork/exec-safe
  per-thread connection state, global-state UDS/RPC over the existing
  `reverie-rpc-transport` (do NOT add a new transport). (SaBRe today:
  `experimental/reverie-sabre/{callbacks.rs,rpc.rs,internal.rs}` +
  `detcore-sabre`; LiteInst today: `reverie-liteinst/{runtime.rs,tool_host.rs,rpc.rs}`.)

Backend-specific (thin adapters, NOT shared):
- **Instrumentation layer**: liteinst2 punning patcher + trampoline vs SaBRe ELF
  rewriter/loader. Marker decode, trusted mappings, handler-entry RIP.
- **Guest register capability** — the decisive divergence: liteinst2 `HookContext`
  **allows** RIP/RSP/RFLAGS edits (`tool_host.rs:472-532`); SaBRe `set_regs`
  **rejects** RIP/RSP/flags with `EOPNOTSUPP` (`reverie_adapter.rs:1171`) because
  it resumes through an internal scratch trampoline. The shared runtime must
  abstract "can the adapter rewrite control flow?" and never assume set-RIP.

## Proposed module boundary (refines hermit-sabre's proposal)

1. **Hermit-side backend-neutral hybrid supervisor** (new module in `hermit-cli`,
   e.g. `hybrid_supervisor.rs`): owns ptrace slow-path/event lifecycle,
   parametrized by an `InstrumentationAdapter`.

   ```rust
   /// Backend-specific knowledge the neutral ptrace supervisor needs.
   /// Async-signal-safety notes apply to any handler-context method.
   pub trait InstrumentationAdapter {
       /// Bytes that mark an instrumented/trusted syscall site (e.g. SaBRe 0f ff).
       fn syscall_marker(&self) -> &'static [u8];
       /// Is this mapping trusted (instrumentation, plugin, vDSO, *.so)?
       fn mapping_is_trusted(&self, m: &MappingInfo) -> bool;
       /// Where to redirect RIP for a slow-path-caught syscall, if this backend
       /// rewrites control flow; None => backend cannot set RIP (SaBRe).
       fn handler_entry_rip(&self, regs: &Regs) -> Option<u64>;
       /// Post-exec / post-clone re-establishment hook for the in-guest runtime.
       fn on_lifecycle_event(&self, ev: LifecycleEvent) -> io::Result<()>;
   }
   ```

2. **Reverie-side shared hybrid guest-runtime** (new module under
   `reverie-preload`, consumed by both `reverie-liteinst` and `reverie-sabre`):
   in-guest Tool dispatch + fork/exec-safe per-thread connection + UDS/RPC. The
   existing `reverie-preload::{dispatch,lifecycle,trap}` seam is the anchor;
   `HybridPtrace` (`lifecycle.rs:95`, currently `Unsupported`) becomes the
   LiteInst/standalone lifecycle controller that pairs with the Hermit supervisor.

3. **Thin adapters**: `reverie-liteinst` implements `InstrumentationAdapter` over
   liteinst2 (RIP-rewrite capable); `reverie-sabre` implements it over its ELF
   rewriter (RIP-rewrite incapable). Each owns only patcher/callback ABI.

## Proposed disjoint file ownership (Hard Invariant #2)

To avoid concurrent edits to the same file, proposed split (for hermit-sabre
confirmation before anyone edits shared files):

| Path | Owner | Notes |
| --- | --- | --- |
| `hermit-cli/src/hybrid_supervisor.rs` (new, shared) | **hermit-sabre lands first** | neutral supervisor + `InstrumentationAdapter` trait |
| `reverie-preload/src/hybrid_runtime.rs` (new, shared) | **hermit-sabre lands first** | shared in-guest runtime primitives |
| `reverie-preload/src/lifecycle.rs` (`HybridPtrace`) | **hermit-243** | LiteInst/standalone controller, gated OFF |
| `reverie-liteinst/*` (adapter) | **hermit-243** | LiteInst `InstrumentationAdapter` impl |
| `hermit-cli/src/sabre_ptrace.rs`, `experimental/reverie-sabre/*`, `detcore-sabre/*` | **hermit-sabre** | SaBRe adapter migration |

Land order (hermit-sabre's request, adopted): shared primitives (rows 1-2) land
first behind the trait; then LiteInst and SaBRe migrate in separate thin-adapter
commits. hermit-243 co-develops the trait contract now and owns the LiteInst
adapter + `HybridPtrace` controller once row 1's trait is agreed.

## Gating

- `HybridPtrace` selected only via explicit config/env opt-in; default remains
  `InProcessSeccomp`. The authoritative L2 matrix is untouched until a gap is
  deliberately closed and validated.
- Draft PR(s) marked parked-WIP-to-resume.

## Open handshake items (blocking shared-file edits)

1. hermit-sabre confirm/adjust the module boundary + the `InstrumentationAdapter`
   trait signature above.
2. hermit-sabre confirm the disjoint file-ownership table + who physically lands
   rows 1-2, and reserve those paths in `ACTIVE.md`.
3. hermit-liteinst ranked diagnosis → which gap the first LiteInst thin-adapter
   commit closes (multiproc/exec vs vDSO vs RCB/PMU).
