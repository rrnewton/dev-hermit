# gVisor "instruction interpreter" vs. Hermit's PMU-skid preemption — and DynamoRIO/DBI for skid-free precise positioning

*Research note, 2026-07-30. Author: impl/research agent (opus-4.8). Task:
`research-gvisor-interpret-vs-pmuskid`.*

Source SHAs (all permalinks below pin these):

| Repo | Remote | SHA |
| --- | --- | --- |
| gVisor | `google/gvisor` | `3f1f8f20c8d376259749446d16766b457d9c982c` |
| Reverie | `rrnewton/reverie` | `4cee948e35ae5561f44f21be3a6e1bbc653058c0` |
| Hermit | `rrnewton/hermit` | `0321a015f19d26c9e40f933fd1662225b23a9c61` |
| DynamoRIO | `DynamoRIO/dynamorio` (submodule) | `929840ad9190e5086775e8debc0f0b79b4208d59` |

gVisor source browsed locally at `ignored/gvisor` (gitignored per large-artifact policy).

---

## TL;DR / recommendation

1. **Does gVisor interpret an x86 instruction *sequence* before returning to
   native execution? NO — refuted from source.** gVisor's entire platform layer
   contains exactly **one** instruction emulated in software: **`CPUID`**, a
   single instruction, after which it resumes native execution. There is no
   multi-instruction decoder, no basic-block interpreter, and no "emulate N
   instructions then switch back" mode on any platform (ptrace, KVM, slimvm,
   systrap). Guest code always runs natively. Adin's recollection is almost
   certainly a conflation of one of three real-but-different gVisor mechanisms
   (single-instruction CPUID emulation, systrap's one-time *binary rewriting* of
   syscall sites, or the host kernel KVM's own MMIO emulation — which gVisor
   explicitly tries to avoid). **There is therefore no gVisor technique to port.**

2. **Don't write a from-scratch x86 interpreter.** If the goal is to run the
   "last stretch" to an exact preemption point under something that counts and
   stops exactly, we already own a mature, correct x86 translator that does this:
   **DynamoRIO — the DBI backend.** It already counts every branch **inline with
   zero skid** and can transfer control at the exact target boundary. A
   hand-rolled interpreter would reimplement, less correctly, what DynamoRIO
   already provides.

3. **Recommended direction: mature DBI branch-count precise preemption as the
   skid-free path** — it eliminates PMU skid *and* the PMU hardware dependency
   for that backend, and is structurally *more* precise than ptrace's
   PMU-overflow-then-single-step. The remaining work is not "counting" (done) but
   **safe delivery of the stop** for guests that never hit a syscall, plus moving
   the scheduler turn off the guest's shared libc. This aligns with
   `goal-hermit-v2`'s "production backend avoids ptrace overhead."

4. **Important scoping caveat (a category distinction that matters):** DBI is
   **not** a drop-in replacement for the *ptrace* backend's single-step
   correction phase. ptrace runs the guest natively, out-of-process; you cannot
   "drop into DynamoRIO for the last 100 branches" without changing the execution
   vehicle — that change *is* the DBI backend. So skid-free precise preemption
   arrives by the DBI backend maturing, not by grafting interpretation onto
   ptrace. On ptrace itself, single-stepping tuned by `skid_margin` remains the
   pragmatic mechanism.

---

## Q1 — Does gVisor interpret an x86 instruction sequence? (VERDICT: NO)

**Per-platform interception mechanism (guest always runs natively):**

- **ptrace platform — native run + hardware syscall-trap.** Normal execution uses
  `PTRACE_SYSEMU` ("start running until the next system call"), i.e. the guest runs
  natively until the kernel traps it at a syscall boundary
  ([`subprocess.go:559-565`](https://github.com/google/gvisor/blob/3f1f8f20c8d376259749446d16766b457d9c982c/pkg/sentry/platform/ptrace/subprocess.go#L559-L565)).
  Single-step (`PTRACE_SYSEMU_SINGLESTEP`) is used **only** to faithfully forward
  the *guest application's own* `PTRACE_SINGLESTEP`, gated on the guest's trap flag
  `(regs.Eflags & arch.X86TrapFlag) != 0`
  ([`subprocess_amd64.go:79-81`](https://github.com/google/gvisor/blob/3f1f8f20c8d376259749446d16766b457d9c982c/pkg/sentry/platform/ptrace/subprocess_amd64.go#L79-L81)).
  gVisor never single-steps for its *own* execution, and never counts instructions.

- **KVM / slimvm platforms — hardware VM fault + native re-execute.** The bluepill
  fault handler dispatches on the hardware `exitReason` and resumes the guest
  natively; it does **not** decode the faulting RIP
  ([`bluepill_unsafe.go:230-254`](https://github.com/google/gvisor/blob/3f1f8f20c8d376259749446d16766b457d9c982c/pkg/sentry/platform/kvm/bluepill_unsafe.go#L230-L254)).
  slimvm's `SwitchToUser` switches on the exception vector (Syscall / PageFault /
  #DB / InvalidOpcode / #GP→CPUID) and reacts to the *fault type*, not to a decoded
  instruction stream
  ([`machine_amd64.go:267-323`](https://github.com/google/gvisor/blob/3f1f8f20c8d376259749446d16766b457d9c982c/pkg/sentry/platform/slimvm/machine_amd64.go#L267-L323)).
  The word "emulation" in the KVM path refers to the *host Linux kernel's* MMIO
  emulation, which gVisor explicitly does not want: "We would actually prefer that
  no emulation occur"
  ([`bluepill_unsafe.go:240-244`](https://github.com/google/gvisor/blob/3f1f8f20c8d376259749446d16766b457d9c982c/pkg/sentry/platform/kvm/bluepill_unsafe.go#L240-L244)).

- **systrap platform — seccomp trampoline + one-time binary rewriting.** systrap
  patches an individual guest `syscall` instruction into a `jmp *addr`
  (`jmpInst = {0xff,0x24,0x25,...}`) pointing at a trap table
  ([`usertrap_amd64.go:42-49`](https://github.com/google/gvisor/blob/3f1f8f20c8d376259749446d16766b457d9c982c/pkg/sentry/platform/systrap/usertrap/usertrap_amd64.go#L42-L49)).
  This is one-time *binary rewriting* of syscall sites for speed; surrounding code
  still runs natively. It is not interpretation of a sequence.

**The only software instruction emulation anywhere — CPUID, a single instruction.**
`TryCPUIDEmulate` copies exactly `len(arch.CPUIDInstruction)` bytes at guest RIP,
byte-compares to the CPUID opcode, and bails if it does not match
(`if !bytes.Equal(inst, arch.CPUIDInstruction[:]) { return false }`); on a match it
writes RAX/RBX/RCX/RDX and advances **one** instruction (`s.Regs.Rip += uint64(len(inst))`)
([`cpuid_amd64.go:45-71`](https://github.com/google/gvisor/blob/3f1f8f20c8d376259749446d16766b457d9c982c/pkg/sentry/platform/cpuid_amd64.go#L45-L71)).
It is invoked from every platform after a CPUID-faulting `#GP`/SIGSEGV (e.g.
[`kvm/context.go:114-121`](https://github.com/google/gvisor/blob/3f1f8f20c8d376259749446d16766b457d9c982c/pkg/sentry/platform/kvm/context.go#L114-L121)).
There is no loop, no decoder for other opcodes, and no "emulate several
instructions then resume" — which refutes the alleged optimization.

**No instruction/branch counting exists in gVisor's platform layer at all.** The
`perf`/`retired`/`instruction`/`interpreter` grep hits are unrelated: clock
sampling (`pkg/sentry/time/sampler.go`), TCP, and the **seccomp-BPF** interpreter
(`pkg/bpf/interpreter.go`, `package bpf` — not x86).

---

## Q2 — Hermit's PMU-skid problem and the single-step correction phase

Hermit reaches an **exact** deterministic preemption point (a target count of
retired conditional branches, RCB) on the ptrace backend in **two phases**: a
coarse PMU-overflow phase that deliberately **undershoots**, then a **precise
single-stepping phase**. The module header says so directly: *"Due to PMU skid,
precise timer events must be driven to completion via single stepping. This means
the PMI is scheduled early..."*
([`timer.rs:15-19`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-ptrace/src/timer.rs#L15-L19)).

- **Counter.** A raw PMU event for retired conditional/taken branches, per-CPU,
  modeled on rr's table: AMD `0x5100d1`, Intel `0x5101c4`, aarch64 `BR_RETIRED`
  ([`timer.rs:64,126-138`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-ptrace/src/timer.rs#L64-L138)),
  programmed via `perf_event_open` with `PERF_TYPE_RAW`, `exclude_kernel`,
  `pinned`, and overflow signal delivery
  ([`perf.rs:200-253`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-ptrace/src/perf.rs#L200-L253),
  [`perf.rs:326-335`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-ptrace/src/perf.rs#L326-L335)).

- **What the "skid" is.** The PMU overflow interrupt (PMI) is *imprecise*: it is
  delivered some number of RCBs **after** the branch that crossed the sample
  period. Reverie therefore arms the overflow at `ticks - skid_margin` so the
  late-arriving PMI still lands **at or before** the true target
  ([`timer.rs:634-655`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-ptrace/src/timer.rs#L634-L655)).
  `skid_margin` is "the experimentally determined maximum number of RCBs an
  overflow interrupt is delivered after the originating RCB"
  ([`timer.rs:166-175`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-ptrace/src/timer.rs#L166-L175)).

- **Precise phase = `PTRACE_SINGLESTEP`, one stop per instruction.** After the
  PMI, `attempt_single_step` walks the guest one instruction at a time
  (`ptrace::step`, [`safeptrace/src/lib.rs:760-763`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/safeptrace/src/lib.rs#L760-L763)),
  re-reading the RCB clock each step, until it lands exactly on the target RCB
  (and optionally exact instruction offset)
  ([`timer.rs:800-856`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-ptrace/src/timer.rs#L800-L856)).
  It first asserts the coarse phase did **not** overshoot ("Consider increasing
  skid margin for this CPU")
  ([`timer.rs:809-815`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-ptrace/src/timer.rs#L809-L815)).
  Detcore arms this via `set_timer_precise`
  ([`hermit/detcore/src/lib.rs:630-638`](https://github.com/rrnewton/hermit/blob/0321a015f19d26c9e40f933fd1662225b23a9c61/detcore/src/lib.rs#L630-L638))
  and independently guards overshoot with `report_rcb_overshoot`
  ([`detcore/src/lib.rs:218-236`](https://github.com/rrnewton/hermit/blob/0321a015f19d26c9e40f933fd1662225b23a9c61/detcore/src/lib.rs#L218-L236)).

- **Why it hurts (quantified from source).** Each single step is a full round trip:
  `PTRACE_SINGLESTEP` → one instruction → `SIGTRAP` → `waitpid` → RCB clock read →
  repeat. The step budget per preemption is `max_single_step_count = skid_margin + 5`
  ([`timer.rs:177-182`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-ptrace/src/timer.rs#L177-L182)):
  Intel **100–125**, aarch64 **1000**, AMD Zen default **10 000**, Turin EPYC **1000**
  (p99 skid 384 RCBs, per the comment)
  ([`timer.rs:126-146`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-ptrace/src/timer.rs#L126-L146)).
  These count *branches*; the number of *instructions* stepped is larger, since a
  full RCB of straight-line code is stepped between branches. The tradeoff is
  explicit: too-small margin → nondeterministic late delivery → panic; too-large →
  "degrade performance from excessive single stepping"
  ([`timer.rs:166-172`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-ptrace/src/timer.rs#L166-L172)).

- **No alternative to single-stepping is implemented.** Hardware breakpoints are
  explicitly listed as intentionally omitted
  ([`perf.rs:9-17`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-ptrace/src/perf.rs#L9-L17));
  PEBS `precise_ip` lowers *sample* skid but explicitly **not** *notification* skid
  ([`perf.rs:175-188`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-ptrace/src/perf.rs#L175-L188)).
  The single-step loop is the sole precise-landing mechanism. This is the same
  design Mozilla **rr** uses (count with the PMU, then single-step to the exact
  branch); the skid-margin table is modeled on rr's `PerfCounters.cc`. So it is a
  known-hard problem with a known, imperfect answer — not a Hermit-specific bug.

**Where interpretation *could* help, in principle:** the slow/fragile part is the
single-step correction. If the "last `skid_margin` branches" ran under a mechanism
that counts branches exactly and can stop exactly, there would be **no skid and no
per-instruction ptrace round trips**. That is precisely what a binary translator
gives you — see Q3.

---

## Q3 — DynamoRIO / DBI for skid-free precise positioning

### It already counts every branch inline, with zero skid

The DBI native client registers a per-instruction instrumentation pass and inserts
an **inline** counter update at each branch as it copies basic blocks into the code
cache:

- Branch predicate `is_counted_branch = cbr || ubr || call || return`
  ([`native/client.c:663-665`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-dbi/native/client.c#L663-L665)).
- Inline increment into a global atomic `branch_count` via
  `drx_insert_counter_update(... &branch_count, 1, DRX_COUNTER_64BIT | DRX_COUNTER_LOCK)`
  ([`native/client.c:721-727`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-dbi/native/client.c#L721-L727),
  counter declared at [`client.c:219`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-dbi/native/client.c#L219)).

This is a pure software count updated at each branch — **no PMU, no interrupt, no
skid**. Caveat: it counts `cbr+ubr+call+return`, a *superset* of the PMU RCB
(conditional branches only); restrict `is_counted_branch` to `instr_is_cbr` for
exact RCB parity. Either variant is a deterministic function of the instruction
stream. (Note: `reverie-dbi/src/counter.rs` is a *syscall* histogram tool, not the
branch counter — the branch counting lives in `native/client.c`.)

### Stopping at an exact count — not built, but the primitives exist

The tool-facing timer hooks are currently **stubs returning `ENOSYS`**:
`set_timer` / `set_timer_precise` / `read_clock`
([`reverie-dbi/src/lib.rs:370-392`](https://github.com/rrnewton/reverie/blob/4cee948e35ae5561f44f21be3a6e1bbc653058c0/reverie-dbi/src/lib.rs#L370-L392),
`TODO-STUB(#31)`: *"A working timer needs a retired-conditional-branch threshold
trap installed in the native DynamoRIO client; the branch counter is currently only
sampled at syscall boundaries, never armed."*).

Building exact-stop is a modest increment on existing DynamoRIO facilities: an
inline (or clean-call) compare of `branch_count` against a target, then
`dr_redirect_execution` to transfer control at the target branch/block. Because the
compare sits at the *same instrumented boundary* as the increment, the transfer
lands **exactly** on the target — structurally **more precise than
PMU-overflow-then-single-step**, with zero skid.

### Current DBI preemption state, and the re-entrancy dead-end

- Preemption is **disabled by construction** in the DBI backend today:
  `load_dbi_config` sets `max_timeslice = None; sequentialize_threads = true;` and
  drives the global scheduler externally on a branch count, re-entering the
  scheduler only **at syscalls**
  ([`hermit/detcore-dbi/src/lib.rs:249-265`](https://github.com/rrnewton/hermit/blob/0321a015f19d26c9e40f933fd1662225b23a9c61/detcore-dbi/src/lib.rs#L249-L265)).
  Consequence: **29/36 PASS_L2**, but **6 HANG** — pure busy-wait guests that never
  hit a syscall starve siblings (the very case PMU-RCB timeslicing solves on
  ptrace). See the standing parent-repo design note
  `ai_docs/dbi-branch-count-preemption-design_20260730.md` and the
  `dbi-l2-corpus-baseline` memory.

- **The documented dead-end is about *running the scheduler turn in-process*, not
  about positioning.** The DBI Detcore lib runs **inside the guest's address
  space**, so injecting a full scheduler turn from a clean call re-enters
  non-reentrant libc shared with the guest (glibc lazy-PLT resolver →
  `undefined symbol: getcwd`; `malloc`/heap re-entrancy → guest dies) — the
  async-signal-safety hazard of doing allocating work in a signal handler; the
  ptrace backend is immune only because Detcore runs **out-of-process**. Crucially,
  the design note's own isolation proof (a) shows *"A clean call that runs only the
  branch-count check **without** the yield is fully transparent — a normally-passing
  guest still PASS_L2 ... The fault is entirely in *running the scheduler turn*, not
  in the instrumentation."* So:
  - **(a) Positioning** (count branches, compare, stop/redirect): libc-free, proven
    transparent — the blocker does **not** kill it.
  - **(b) Running the scheduler turn in-process at that point**: re-enters shared
    libc — the blocker kills this.

### Bottom line for Q3

DBI **is** a viable vehicle for skid-free precise positioning: the exact inline
branch counter exists today, the stop/redirect and safe-suspension primitives exist
(`dr_redirect_execution`, `dr_suspend_all_other_threads_ex`), and positioning is
empirically transparent. What remains:

1. **Threshold + read (small):** compare `branch_count` to a target and expose it;
   wire the stubbed `set_timer`/`read_clock`; optionally restrict to `cbr` for RCB
   parity.
2. **Safe delivery of the stop (the real work):** for a pure busy-wait there is no
   syscall boundary, so transfer must be DR-mediated (`dr_suspend_all_other_threads_ex`
   or fragment flush + controlled re-entry), with follow-on work on a DR-private
   stack/allocator — not the guest's.
3. **Who runs the scheduler (separate decision):** either make the in-process turn
   allocation-free / guest-libc-free at the stop point, or (cleaner) run the
   scheduler **out-of-process** over the existing `reverie-rpc-transport`, removing
   the shared-libc hazard entirely.

---

## Options compared

| Option | Skid? | Precise? | Effort | Verdict |
| --- | --- | --- | --- | --- |
| **A. DynamoRIO/DBI branch-count positioning** | none (software count) | exact branch/block, structurally exact | moderate — counter done; needs threshold + safe-point delivery + out-of-proc turn | **Recommended** |
| **B. gVisor "interpret a sequence" technique** | — | — | — | **Not available** — gVisor has no such mode (Q1) |
| **C. From-scratch x86 interpreter for the final phase** | none | exact | very high — a correct full x86 interpreter | **Not recommended** — reinvents DynamoRIO, less correctly |
| ptrace PMU + single-step (status quo) | undershoot by `skid_margin`, corrected by single-step | exact, but slow correction | shipped | keep for the ptrace backend |

---

## Recommendation

1. **Close Adin's gVisor lead as refuted.** There is no gVisor instruction-sequence
   interpreter to port (Q1). What he likely recalled is CPUID single-instruction
   emulation, systrap syscall-site binary rewriting, or host-KVM MMIO emulation.

2. **Do not build a from-scratch x86 interpreter.** DynamoRIO already is a mature,
   correct x86 translator that counts branches inline with zero skid and can stop
   exactly. Reuse it.

3. **Invest in DBI branch-count precise preemption as the strategic skid-free
   path.** It removes PMU skid *and* the PMU-hardware dependency (a real portability
   pain: CI/VM hosts without accessible counters), and is structurally more precise
   than PMU+single-step. Scope: wire the stubbed `set_timer`/`read_clock` over the
   existing inline counter (cbr-restricted for RCB parity), then solve the two hard
   parts — safe-point delivery for syscall-free busy-waits (DR thread suspension)
   and moving the scheduler turn out-of-process — which are already scoped in the
   parent-repo note `ai_docs/dbi-branch-count-preemption-design_20260730.md`. This
   is the natural fix for the 6 current DBI HANGs, not a separate effort.

4. **Do not attempt to graft interpretation onto the ptrace backend's single-step
   phase.** ptrace runs the guest natively out-of-process; "run the last N branches
   under a translator" *is* the DBI backend, not a ptrace add-on. On ptrace, keep
   single-stepping tuned by `skid_margin`. Skid-free precise preemption is delivered
   by the DBI backend maturing — which also advances `goal-hermit-v2`'s
   "production backend avoids ptrace overhead."

**One-line answer to the task's key question:** *Yes — reuse DynamoRIO (the DBI
backend) for skid-free precise preemption positioning; gVisor offers no such
technique, and a from-scratch interpreter is unnecessary.*

---

## Source map (for follow-up)

- gVisor interception & CPUID-only emulation: `pkg/sentry/platform/{ptrace,kvm,slimvm,systrap}/…`, `pkg/sentry/platform/cpuid_amd64.go` @ `google/gvisor` `3f1f8f20`.
- Hermit/Reverie PMU-RCB + single-step: `reverie-ptrace/src/{timer.rs,perf.rs,task.rs,tracer.rs}`, `safeptrace/src/lib.rs`, `hermit/detcore/src/lib.rs` @ the SHAs above.
- DBI: `reverie/reverie-dbi/{src/lib.rs,native/client.c}`, `hermit/detcore-dbi/src/lib.rs`, DynamoRIO `ext/drx/drx.h`, `core/lib/dr_ir_utils.h`, `core/lib/dr_tools.h`.
- Prior art: standing design note `ai_docs/dbi-branch-count-preemption-design_20260730.md`; memory `dbi-preemption-in-process-reentrancy-blocker`, `dbi-l2-corpus-baseline`, `min-vtime-scheduler-study`.
</content>
</invoke>
