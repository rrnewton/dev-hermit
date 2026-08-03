# S1 COST measurement design: tool-RCB bracketing vs the ptrace tracer

**Task:** `unified-patching-backend-constructor-feasibility` (S1). **Author:**
hermit-e9patch (opus-4.8), 2026-08-03. **Status:** DESIGN ONLY — not yet
executed; execution needs a liteinst slot + release build (see §6). This is the
"one open question" left after the determinism-achievability framing was
retracted (see `## CORRECTION` in
`unified-patching-backend-constructor-feasibility_20260803.md` and memory
`[[s1-inguest-rcb-preemption-is-cost-not-crux]]`). **Grounded in
`reverie-ptrace/src/{timer.rs,perf.rs}` and `task.rs`** at reverie primary
`d2fb9a05`; every mechanism below carries a `file:line`. **No blind spike** — the
measurement is specified against the shipping code, not first principles.

## The question, stated precisely

Determinism is solved and shipping (timer.rs skid-margin + single-step; mozilla
rr). The remaining in-guest question is **cost**: *how many RCBs, and how many
wall-nanoseconds, does an in-guest backend spend on branch-count accounting per
event, versus what the ptrace tracer spends* — per preemption-positioning case.
The in-guest accounting mechanism is the owner's **tool-RCB bracketing**: read
the RCB counter at trampoline entry (clean baseline — the unconditional `jmp`
that delivered control does not increment the retired-*conditional*-branch
counter, event `AMD_RCB_EVENT = 0x5100d1`, timer.rs:64), read it again before
returning control to the guest, and deduct the difference so guest branch
accounting stays exact.

Units matter (coordinator "establish what you have" rule): the deliverable is
**two numbers per case** — (i) RCBs the accounting itself consumes (measured with
the same 0x5100d1 event — the cost of bracketing literally *is* a branch count),
and (ii) wall-ns per event. A ratio without both is a proxy.

## The load-bearing finding: the ptrace fast-read does NOT transfer in-guest

This is the crux the measurement exists to resolve, and it is a code fact, not a
hypothesis.

- **ptrace reads a STOPPED tracee.** `Timer::read_clock` (timer.rs:305) →
  `TimerImpl::read_clock` (timer.rs:705) → `PerfCounter::ctr_value_fast`
  (perf.rs:365). The clock counter is opened with `fast_reads(true)`
  (timer.rs:615), so `ctr_value_fast` takes the mmap path
  (`ctr_value_fast_loop`, perf.rs:386-448): a **seqlock-protected shared-memory
  load of `page.offset`** — *no syscall, no `rdpmc`*. This works because the
  tracer reads the tracee while it is **ptrace-stopped**, so the counter is not
  currently scheduled on a core and `page.index == 0` (perf.rs:418-420).
- **In-guest reads a LIVE counter on its own core.** A tool bracketing its own
  handler is the running thread; its RCB counter is actively scheduled ⇒
  `page.index != 0`. reverie's fast loop **explicitly bails to the `read()`
  syscall** in that case (perf.rs:420-430, comment: rdpmc would only be valid on
  the same core and is racy on an active PMU — a constraint that is *false* for
  the in-guest reader, which IS the same-core running thread). So as written,
  in-guest bracketing would pay a **`read()` syscall per bracket read**
  (perf.rs:337-360), i.e. two syscalls per handler entry — which could rival or
  exceed the very trap round-trip savings that motivate in-guest (~31× on axis
  (b), `[[s1-liteinst-mode-a-inguest-trap-win-but-detcore-blocked]]`).
- **The in-guest-native primitive is `rdpmc`,** which reverie does **not**
  implement anywhere in perf.rs/timer.rs (no `rdpmc`, no `cap_user_rdpmc`; the
  only inline asm is the `do_branches` test helper, perf.rs:583). `rdpmc` is a
  userspace register read (~20-40 cycles) and is valid *precisely* in the case
  ptrace avoided: reading your own currently-scheduled counter on the core you
  run on.

**Consequence for "cheap by construction."** The owner's claim is right *if the
read is `rdpmc`* and wrong if it falls to `read()`. The measurement's primary job
is to quantify both regimes so the design decision (implement in-guest `rdpmc`
vs. reuse the syscall fallback) is made on data.

## The cases to measure (grounded per-case cost model)

Bracketing rides the existing per-syscall dance, so its incremental cost is
scoped to what already happens per event:

1. **Syscall preemption — PRIMARY / COMMON.** Per handler entry the in-guest
   cost is: 2× (read RCB now) + the deduct arithmetic + the existing re-arm.
   Baseline ptrace cost for the analogous stop: the reset dance
   `request_event`/`observe_event`/`finalize_requests` (timer.rs:328/337/387;
   bodies 634-670 / 672-674 / 709-725) which does `reset`→`set_period`→`enable`
   ioctls (perf.rs:293-310) plus `read_clock` at arm time (timer.rs:656) and a
   conditional `tgkill` (timer.rs:709-725). **Measure:** RCBs and ns for the two
   bracket reads under (a) `read()` fallback and (b) `rdpmc`, vs. the ptrace
   per-stop bookkeeping. This is where the verdict lives — the common case.
2. **RCB overflow during RECORD — RARE.** RECORD may ignore skid (record where
   you landed). In-guest cost = fielding its own PMI (signal delivery) + 2 bracket
   reads; **no single-step**. Compare to ptrace taking the overflow as an
   out-of-process stop.
3. **RCB overflow during REPLAY — RARE.** scx-sim technique: breakpoint at the
   known target RIP + count branches to disambiguate the dynamic instance. Cost =
   breakpoint install/handle + branch count, a positioning technique, not live
   stepping.
4. **Live single-step-to-exact-count — RAREST fallback.** The one genuinely
   different case: ptrace drives `attempt_single_step` (timer.rs:800-856) →
   `single_step_with_clock` (timer.rs:547-571) out-of-process, bounded by
   `max_single_step_count = skid_margin + 5` (timer.rs:180-182; = 1005 here).
   In-guest there is no external tracer to drive SIGTRAP-per-instruction, so this
   is an **architecture/cost** question (who steps whom) for a rare fallback —
   NOT a determinism question. The measurement records this as a design gap to
   cost separately, not as a blocker.

## Metric definition

- **Event:** `AMD_RCB_EVENT = 0x5100d1` as `Event::Raw` → `PERF_TYPE_RAW`
  (timer.rs:64,185-192; perf.rs:97,103). **NOT** generic
  `PERF_COUNT_HW_BRANCH_INSTRUCTIONS` — that was the original spike's error; it
  counts all branches, not the retired *conditional* branches Detcore's clock
  uses.
- **Counter attr:** mirror reverie exactly — `exclude_kernel`/`exclude_guest`/
  `exclude_hv`, `pinned=1`, per-tid, `cpu=-1` (perf.rs:200-213). `pinned=1` means
  a deschedule EOFs the read and panics (perf.rs:353) — so the box must be quiet;
  use the standard load probe before measuring (`[[ci-hub-load-probe-use-over-load-average]]`).
- **Two sub-numbers per case:** RCBs consumed by the accounting code (self-count
  via a second 0x5100d1 counter, or by bracketing the bracket) and wall-ns
  (median of n≥10, K=1 cgroup, two-point slope to subtract fixed overhead — same
  method as the S1(b) microbench, `experiments/s1-liteinst-inguest-trap-microbench_20260803`).

## What to build to measure it (bounded; Mode B UNTOUCHED; no scheduler needed)

The measurement does **not** require the in-guest Detcore scheduler and does not
touch Mode B (the flagship). It is a focused microbench in a warm-patched Mode A
in-guest hook (the null/`CounterTool` path already exists: signature
`calls=N traps=1 hooks=N`):

1. In the warm hook, add a **bracket harness**: read RCB at entry, read RCB
   before return, accumulate the delta over N invocations. Provide **two
   read-primitive implementations** behind a flag: (a) reverie's `ctr_value`
   `read()` path (perf.rs:337) — the current index!=0 fallback; (b) a new `rdpmc`
   read (the in-guest-native primitive reverie lacks). Report per-handler RCBs and
   ns for each.
2. Measure the **ptrace tracer baseline** the same way: per seccomp-stop, the
   bookkeeping RCBs/ns (reset dance + `read_clock`), for the identical syscall.
3. **Release build only** — in debug, `ctr_value_fast` runs the syscall read
   anyway for a `debug_assert_eq!` (perf.rs:371), which would poison the (b)
   `rdpmc` number.
4. 1-CPU cgroup (axis (a) = 0; single-thread) so the delta is purely accounting
   cost, exactly as the S1(b) datum isolated the trap mechanism.

## Honest limits

- This measures **accounting/bracketing overhead**, not axis (a)
  sequentialization (park + RPC to the global scheduler singleton). (a) is
  backend-independent and degenerate on single-thread Mode A
  (`[[s1-liteinst-mode-a-inguest-trap-win-but-detcore-blocked]]`); it is deferred
  until a multi-thread build exists and cannot change the accounting verdict.
- Case 4 (live single-step fallback) is costed as a design gap, not measured — no
  in-guest driver exists yet.
- Host-scoped to this AMD Turin box (family 0x1A model 0x11, skid-margin 1000,
  `rdpmc` availability to be confirmed via `cpu/caps` / `cap_user_rdpmc`).

## Bottom line for the coordinator

The design is ready; execution needs a **liteinst slot + release build**
(coordinator-authorized allocation). The single most important number it will
produce: **is an in-guest RCB read a `read()` syscall or an `rdpmc`, and does that
choice keep bracketing cheap enough that the ~31× trap-mechanism win survives
after accounting?** That, not determinism, is what gates the in-guest perf case.
