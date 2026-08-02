# LiteInst flagship: source-grounded answers to the 6 verification questions

- Date: 2026-08-01
- Author: hermit-243 (assist to hermit-liteinst, who owns
  `liteinst-flagship-blockers-diagnosis`)
- Evidence base: exact corpus-run SHAs — Hermit
  `464cbd9f9bb43d5505c914783819e1d349630283`, Reverie pin
  `aa6f1283aeee3efd174c57f6dd8198310bd307e1` (confirmed identical pin at the
  corpus SHA). Line refs: hermit-cli from the worktree checkout verified against
  `git show 464cbd9f:...`; reverie from the pinned cargo checkout `…/aa6f128/`.
- **Purpose: speed up the diagnosis. hermit-liteinst owns the ranked report;
  this is input, not a replacement.**

## HEADLINE CORRECTION (supersedes an earlier architecture-map claim)

An architecture-map agent reported "the default `--backend liteinst` is off
ptrace." **That is wrong for what `hermit` actually runs.** The agent mapped
reverie's `impl Backend for LiteinstBackend::run` (which *is* the standalone
LD_PRELOAD path, `run_with_preload`), but **hermit-cli never calls that trait
method.** hermit dispatches LiteInst directly to the **ptrace host**:

- `hermit-cli/src/lib.rs:1520-1535` (`run_with_backend_inner`) and `:1628-1649`
  (output variant) → `reverie_liteinst::LiteinstBackend::run_host_with_preload::
  <Detcore>` / `run_host_with_output_and_preload::<Detcore>`.
- Confirmed at the corpus SHA: `git show 464cbd9f:hermit-cli/src/lib.rs` →
  `run_host_with_preload::<Detcore>` at 1523, `run_host_with_output_and_preload`
  at 1632.
- Corroborating runtime log: `hermit-cli/src/bin/hermit/run.rs:1548` prints
  `"[liteinst host hybrid] activation verified …; Detcore Tool active in ptrace
  host"`. Test `liteinst_public_dispatch_runs_ptrace_host_hybrid` (lib.rs:2024).

The standalone `run_with_preload` path is exercised **only** by reverie's own
`reverie-liteinst/tests/hybrid.rs:907`, and with a **`PassthroughGetpid`** test
tool — **never Detcore, never via hermit.** So the "native standalone off-ptrace"
flagship path has **no Detcore corpus coverage at all** today.

## The 6 answers

### 1. Is the ~60% (117/200 L2) real?
**Real determinism, but ptrace-hosted — not the native-standalone value prop.**
The path hermit runs (`run_host_with_preload::<Detcore>`) is, per its own
contract (backend.rs:196-205): *"Ptrace owns the sole Tool and GlobalTool from
exec onward; the preload contributes only dynamic site installation and injected
hot-site traps."* So determinism enforcement is the **real Detcore engine hosted
by `reverie_ptrace::TracerBuilder::<Detcore>`** — the same Detcore code as the
ptrace backend. It is not architecturally tautological. BUT:
- It does **not** demonstrate native standalone in-place patching; LiteInst is
  currently riding the ptrace backend with a preload hot-site assist.
- The per-cell *strength* (trivial vs substantive) is still open — see Q5.
- Honest framing: "117/200 L2 on the **ptrace-host LiteInst hybrid**", not
  "native LiteInst backend."

### 2. LD_PRELOAD vs ptrace-host — which does hermit use?
**Ptrace-host.** `hermit run --backend liteinst` →
`run_host_with_preload::<Detcore>` (TracerBuilder-owned ptrace tracer) at both
lib.rs:1523 and 1632, at the corpus SHA. The LD_PRELOAD standalone path
(`run_with_preload`, reverie `Backend::run`) is **not wired into hermit**.

### 3. Ptrace-fallback path?
In the path hermit uses, **ptrace is not a fallback — it is primary**: the ptrace
tracer owns the Tool/GlobalTool from exec onward; the LD_PRELOAD preload provides
in-guest hot-site traps (markers `HOST_BEGIN/READY/HELPER_RETURN/SYSCALL`,
backend.rs:218-224). The *standalone* path (unused by hermit) has **no ptrace
fallback** — first touch is in-process SIGSYS/seccomp only. (Contrast SaBRe,
which does have a genuine ptrace safety-net for missed syscalls,
`hermit-cli/src/sabre_ptrace.rs`.)

### 4. Common Reverie RPC vs bespoke?
**Common, and real Detcore GlobalState — not bespoke.** The host path hosts the
tool via `reverie_ptrace::TracerBuilder::<Detcore>` (standard reverie tool
hosting); it returns the real `detcore` GlobalState (hermit calls
`global_state.clean_up(...)`, `force_shutdown_with_error()`,
`cancel_internal_scheduler()` — real Detcore methods, lib.rs:1528-1533). The
standalone path uses the common `reverie_rpc_transport::RpcServer` +
`reverie_preload::rpc` (not a private transport). No bespoke determinism engine
in either path (consistent with the repo Backend Definition: one shared Detcore).

### 5. Trivial vs substantive cell breakdown?
**Open — needs per-cell data (hermit-liteinst owns this).** Fast discriminator I
recommend to classify the 117 passers without re-reading each program:
- Run each guest **natively twice and diff** (no hermit). If already
  bitwise-identical natively, its L2 pass is **trivial** (Detcore had no
  nondeterminism to sanitize — passes on any backend, even a no-op).
- For the remainder, confirm the nondeterminism source is actually **routed
  through the tool** on this path (time/PID/random via syscall are; anything via
  vDSO under the ptrace host is handled by reverie-ptrace's standard vDSO/CPUID
  mechanism, so likely covered — worth a spot check to avoid a #1095-style clock
  tautology).
This yields the honest substantive count. I can run the native-2x sweep over the
corpus quickly if hermit-liteinst wants to divide labor (say the word to avoid
duplication).

### 6. Single-proc ceiling — what causes it?
**Explicit and hard in the host path.** backend.rs:200-201: *"This initial
hybrid contract supports one tracee process with one thread; fork, vfork, and
clone fail closed before either side is resumed."* So the ptrace-host path caps
at **single-process, single-thread** — even plain `fork` fails closed here (more
restrictive than the standalone path, where `fork` works via seccomp-filter
inheritance). Any corpus cell that forks/execs/creates a thread is therefore in
the failing 83, not the passing 117. Lifting the ceiling = the hybrid must handle
fork/vfork/clone/exec child bootstrap (the same gap set as HybridPtrace).

## Net for the diagnosis
- Correct the framing everywhere from "native standalone" to "ptrace-host
  hybrid" — that is the measured path.
- The flagship "native standalone off-ptrace" path is **unmeasured with Detcore**
  and gated on: wiring hermit to the standalone path (or a HybridPtrace) AND
  closing fork/clone/exec/vDSO/RCB gaps.
- Biggest single lever for *parity* on the current path is the single-proc /
  single-thread ceiling (Q6); biggest lever for *flagship claim* is running
  Detcore on the standalone path at all.
