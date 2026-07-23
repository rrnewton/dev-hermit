# KVM M3 — Detcore-over-KVM integration: status (BLOCKED, evidence-based)

Status: point-in-time integration assessment. Author: agent `hermit-061`
(task `kvm-m3-detcore-integration`). Grounded in code read on 2026-07-23.

**Bottom line: M3 as specified — `hermit run --backend kvm --strict -- /bin/echo`
running deterministically under Detcore, with `--verify` passing — is NOT
achievable on the current bare KVM prototype, and no amount of hermit/Detcore
wiring changes that.** The blocker is the missing Linux-ABI/ELF-execution
substrate (the Sentry-sized prerequisite already flagged in M1 and M2), plus a
runtime-model mismatch. This document records the three concrete, code-cited
blockers and the real path forward. It deliberately does **not** ship a fake or
misleading "KVM is now a real backend" integration.

Prior milestones (context):
- M1 (`kvm-guest-trait-audit.md`): reverie-kvm implemented *none* of `Guest`;
  KVM needs a Linux ABI before `Guest` is meaningful.
- M2 (reverie PR #25, `impl-kvm-minimal-guest`): built a minimal `KvmGuest` +
  `KvmBackend::run_tool` and proved a trivial `GlobalState=()` syscall-counter
  tool runs over the real-mode `vmcall` demo. Explicitly noted KVM was still far
  from Detcore-ready.

---

## Blocker 1 — There is no Linux process to instrument (the ELF wall)

`hermit-cli/src/bin/hermit/backends.rs::run_kvm` does not execute the target
program. Its own message says so:

> `hermit: [kvm backend] {program:?} is not executed as an ELF; the reverie-kvm
> prototype runs a built-in hello-world guest that issues write(2) via vmcall.`

The prototype creates a single **real-mode** vCPU, writes a `vmcall; hlt`
program plus a fixed syscall frame, and runs it. It has **no ELF loader, no
Linux address space, no process/thread, no Linux syscall ABI**. Therefore
`hermit run --backend kvm -- /bin/echo` cannot run `/bin/echo` under KVM under
*any* tool. The `--verify` determinism criterion presupposes running the real
program, which is impossible here.

This is not a wiring gap; it is the fundamental substrate gap. Per
`ai_docs/kvm_backend_design.md`, giving KVM a Linux personality (gVisor's Sentry,
or a guest kernel) is a ~6–9 engineer-month MVP.

## Blocker 2 — Detcore needs a tokio runtime + a concurrent scheduler; `run_tool` cannot provide it

Detcore is not a leaf tool; its `GlobalState` runs a scheduler loop as a
background task. `detcore/src/tool_global.rs::init_global_state`:

```rust
async fn init_global_state(cfg: &Config) -> GlobalState {
    let sched = Arc::new(Mutex::new(Scheduler::new(cfg)));
    let global_time = Arc::new(Mutex::new(GlobalTime::new(cfg)));
    let handle = if cfg.sequentialize_threads {
        Some(tokio::spawn(sched_loop(sched.clone(), global_time.clone())))  // <-- tokio
    } else { None };
    ...
}
```

`--strict` sets `sequentialize_threads`, so Detcore spawns `sched_loop` on a
tokio runtime and its `handle_syscall_event` *awaits scheduler turns serviced by
that concurrently-running loop*. The M2 `KvmBackend::run_tool` drives the tool
with a minimal, single-threaded `block_on` (std `thread::park`) and **no tokio
runtime**. Consequences:

- `tokio::spawn` inside `init_global_state` panics ("there is no reactor
  running") with no tokio runtime present.
- Even inside a tokio runtime, a synchronous `block_on` on the vCPU thread cannot
  also run the scheduler loop concurrently, so Detcore's first
  `resource_request` await would deadlock.

The production ptrace backend hosts Detcore on a tokio `LocalSet` with the
scheduler task live; `run_tool` is not that. Driving Detcore needs a real
async run loop (tokio `LocalSet`) integrated with the KVM vCPU stepping — a
substantial run-loop design, not a call-site change.

## Blocker 3 — Cross-repo: hermit does not see the M2 API

`run_tool`/`KvmGuest` exist only on my unmerged reverie branch
`impl-kvm-minimal-guest` (reverie PR #25, base `frontier`). Hermit pins a
different reverie revision and `hermit-cli` contains no `run_tool`/`KvmGuest`
references. Wiring hermit to the new API first requires landing reverie PR #25
and re-pinning hermit's reverie dependency (a cross-repo step). This is
surmountable, but it is gated behind Blockers 1–2 being worthwhile.

---

## What *is* established (so the blocker is correctly located)

M2 proved the `Guest`/`GlobalRPC` **plumbing layer** works on `KvmGuest`: a real
`reverie::Tool` observes an intercepted syscall, reads/writes real guest memory,
and `inject` actually executes the syscall (verified end-to-end via a pipe).
So the obstacle to Detcore is **not** the `Guest` trait surface — it is:
(a) the absence of a Linux process/ABI to instrument, and (b) Detcore's
tokio-scheduler runtime model. Both are above the `Guest` layer.

## Real path to a KVM Detcore backend (revised roadmap)

The correct next step is **not** "wire Detcore." It is, in order:

1. **Linux-ABI substrate (the gate).** Add ELF loading + a Linux syscall
   personality behind KVM — the gVisor-Sentry bridge from
   `ai_docs/kvm_backend_design.md` (recommended), or a guest kernel. Until this
   exists there is no process to run `/bin/echo`, so M3's verify is undefined.
2. **A real async KVM run loop.** Replace `run_tool`'s `block_on` with a tokio
   `LocalSet`-based driver that steps the vCPU and services Detcore's scheduler
   concurrently (mirroring `reverie-ptrace`'s `TracerBuilder`/`Tracer`).
3. **Thread/process model + virtual PIDs, real vCPU-register wiring into
   `Guest::regs`, real `stack`/`tail_inject`, timers (PMU/RCB), CPUID** — the M1
   audit's remaining rows.
4. **Then** host `Detcore` via `TracerBuilder::<Detcore>`-equivalent on KVM,
   returning `(ExitStatus, GlobalState)`, and only then is
   `hermit run --backend kvm --strict` meaningful. Land reverie PR #25 + re-pin
   hermit as part of this.

### Recommendation

Reframe the KVM track around Blocker 1 (the Sentry/Linux-ABI substrate) as the
next milestone; "run Detcore over KVM" is downstream of it, not adjacent to M2.
M3 (Detcore integration) should remain **open/blocked** pending that substrate,
not marked complete. `hermit run --backend kvm` remains a real-mode `vmcall`
demonstration, honestly labeled as such in `run_kvm`.
