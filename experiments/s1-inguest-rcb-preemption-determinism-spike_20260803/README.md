# S1 crux spike: can an in-guest RCB-preemption interrupt land the guest deterministically?

**One-line verdict:** **NO — provably the DBI wall again, on this host.** An
in-guest PMU-overflow interrupt (the guest fielding its *own* retired-branch
overflow signal, with no external ptrace tracer) does **not** land at a
deterministic instruction across identical runs: over 20 byte-identical runs the
preemption landed at **9 distinct RIPs** with a **retired-branch skid of 15–90**
past the target. The only hardware facility that would remove that skid — PEBS
precise sampling (`precise_ip > 0`) — is **architecturally unavailable on this
AMD Zen host**. The software correction ptrace-Detcore uses to survive skid
(fire-early, then single-step to the exact count) requires the guest to
single-step *itself* from its own signal handler = the in-process re-entrancy
wall that already killed DBI clean-call preemption.

## Why this is the whole S1 question

S1 (the in-guest patching-backend unification gate) reduced to **one crux**: full
Detcore cannot run on liteinst Mode A because it needs deterministic RCB
preemption, and the open question was whether an *in-guest* backend can deliver
that **at all**, or whether it is provably the same wall DBI hit. The instrument
half of the crux was already closed by hermit-250: PMU exhaustion **fails hard,
it does not silently multiplex** (`reverie-ptrace/src/perf.rs:210` `pinned=1` +
panic guards at 350-354 / 440-446), so deterministic counts are *preserved* under
contention — the crux was not answered NO-by-construction, and the spike was
worth running. This spike answers the remaining half: **deterministic landing.**

## Two cost/mechanism facts this depends on

- **Counter accuracy is fine** — the count *read* at delivery is exact
  (`ctr_at_sig` is a clean readable value each run). The problem is not counting.
- **Delivery timing is not** — the *interrupt arrives* a variable number of
  branches after the counter overflows. That skid is the nondeterminism.

## Method

- **Host:** `devbig014` (short label), AMD EPYC 9D85 (Zen), kernel
  `6.18.39-0_fbk0_hardened_0_ga43d5727b443`, `perf_event_paranoid=1`,
  `cpu/caps/max_precise=0`.
- **Instrument established first** (the failure mode is building a spike around a
  denied instrument): at `paranoid=1`, own-process perf is permitted
  (`perf_event_open(pid=0, cpu=-1)` succeeds), so the spike stays own-process and
  needs no privilege and no `pmu-serial` flock. `precise.c` confirms
  `precise_ip=0` opens but `precise_ip=1/2/3` fail (`ENOENT`, then
  `EOPNOTSUPP`) — no PEBS here.
- **Guest analogue** (`spike.c`): a single process, ASLR disabled
  (`ADDR_NO_RANDOMIZE` + re-exec), pinned to CPU 3, runs a fixed deterministic
  loop (`5,000,000` iterations, one conditional branch each). A pinned
  (`pinned=1`, matching `perf.rs:210`) `PERF_COUNT_HW_BRANCH_INSTRUCTIONS`
  counter with `sample_period = 1,000,000` and `precise_ip=0` (forced by the
  host) delivers `SIGRTMIN` to the process on overflow via
  `F_SETSIG`+`O_ASYNC`+`F_SETOWN_EX`. The handler captures, from its own
  `ucontext`, the **RIP at delivery**, the **exact counter value**, and the
  **loop iteration** reached.
- **Determinism test:** run the identical binary/workload/target 20 times and
  compare landing point (`results.csv`). Determinism ⇒ same RIP + same
  iteration + same count every run.

## Results (`results.csv`, 20 runs)

| Quantity at preemption | Range over 20 identical runs | Verdict |
| --- | --- | --- |
| Retired-branch count (`ctr_at_sig`) | 1,000,015 – 1,000,090 (**skid 15–90**) | non-constant |
| Loop iteration (`iter_at_sig`) | 400,001 – 400,031 | non-constant |
| **RIP at delivery** | **9 distinct addresses** (0x401314–0x401364) | **nondeterministic** |
| Counter *readable* at delivery | exact integer every run | accurate (not the problem) |

The preemption lands **somewhere different every run**. This is inherent to
interrupt-based (non-precise) counter-overflow delivery: the NMI/interrupt is
raised after overflow and takes a variable, pipeline-dependent number of
instructions to be delivered.

## Interpretation — why this is the DBI wall, not a tuning problem

1. **Non-precise delivery is nondeterministic by nature** (measured: skid 15–90).
   No amount of margin tuning makes an interrupt land at a fixed RIP.
2. **The precise fix is unavailable on AMD here.** PEBS (`precise_ip>0`) records
   the exact overflow RIP with zero skid, and is what a determinism engine would
   need for in-guest precise landing. It fails to open on this Zen host
   (`EOPNOTSUPP`); AMD's precise facility is IBS, a *statistical sampling* model,
   not a per-event precise-RIP capture. `reverie`'s own `set_timer_precise` path
   therefore cannot run on this host at all.
3. **The software escape = the re-entrancy dead-end.** ptrace-Detcore does not
   rely on zero skid: it arms the timer *early* (`target − margin`), takes the
   stop, then **single-steps** the remaining branches to hit the exact RCB count
   (the skid-margin machinery; see `demo5-fix-pmu-skid`). For an *in-guest*
   backend, that correction means the guest single-steps **itself** — a
   SIGTRAP-per-instruction loop driven from its own signal handler, mutating its
   own execution state mid-flight. That is exactly the in-process re-entrancy
   wall documented for DBI clean-call preemption
   (`dbi-preemption-in-process-reentrancy-blocker`): the preemption machinery
   cannot run inside the very context it must preempt.

## Scope and honest limits (what would change the answer)

- **This is a host-scoped NO with a mechanism, plus a fundamental leg.** The
  *skid nondeterminism* (leg 1) is fundamental to non-precise overflow on any
  hardware. The *precise-unavailable* result (leg 2) is specific to AMD/no-PEBS
  hosts. On an **Intel host with PEBS**, `precise_ip>0` could give a
  **deterministic landing RIP** without single-stepping — that leg must be
  re-tested there before claiming a universal NO.
- **Even a precise-landing YES would leave leg 3 open.** Deterministic *landing*
  is necessary but not sufficient: the in-guest handler must still park + RPC to
  the global scheduler (axis (a)) from that RIP, and whether that is safe from an
  arbitrary in-guest signal context is the re-entrancy question this spike did
  **not** test (it only measured landing determinism).
- Single-threaded only; measures landing determinism, not multi-thread or the
  Mode A build gaps (clock/timer/scheduler are `Unsupported` stubs — see the S1
  micro-benchmark experiment).

## Bottom line for unification

On this host, **S1 does not clear its crux**: in-guest deterministic RCB
preemption is not achievable via the available mechanism, and the correction that
would rescue it is the known DBI in-process re-entrancy wall. This is a complete,
valuable result: the in-guest unification case is **blocked**, not merely
unproven, unless (a) run on an Intel/PEBS host where precise landing is possible
**and** (b) the in-guest safe-yield/re-entrancy sub-problem is separately solved.
**Mode B remains untouched.**

## Reproduction

```bash
cc -O0 -o precise precise.c && ./precise          # confirm precise_ip>0 fails (no PEBS)
cc -O0 -o kprobe  kprobe.c  && ./kprobe           # K=5 usable pinned counters/core
cc -O0 -fno-pie -no-pie -o spike spike.c
for i in $(seq 1 20); do ./spike 5000000 1000000; done   # 20 identical runs; compare landing
```
