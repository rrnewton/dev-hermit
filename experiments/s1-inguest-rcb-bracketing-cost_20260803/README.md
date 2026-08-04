# S1 COST: what does in-guest tool-RCB bracketing cost vs the ptrace tracer?

**One-line result:** In-guest tool-RCB bracketing is **cheap by construction**.
The ~31x instrumentation-trap-round-trip win from the S1(b) microbench
**survives after accounting** under both read primitives — **~20x** with the
`read()` syscall fallback reverie currently uses, **~30x** with `rdpmc` — so the
feasibility of a unified in-guest patching backend is **not gated by branch-count
accounting cost.** `rdpmc` is available and correct on this host and makes
bracketing essentially free.

This closes the *one open question* the determinism-achievability retraction left
behind (see `[[s1-inguest-rcb-preemption-is-cost-not-crux]]` and the landed
design `ai_docs/s1-inguest-bracketing-cost-measurement-design_20260803.md`).

## The question, precisely

An in-guest backend that intercepts a syscall in the guest's own address space
must keep Detcore's branch-count clock exact by **bracketing** every tool
callback: read the retired-conditional-branch counter (event
`AMD_RCB_EVENT = 0x5100d1`, reverie `timer.rs:64`) at trampoline entry (clean
baseline — the unconditional `jmp` that delivered control does not tick the
*conditional*-branch counter), read it again before returning control, and deduct
the delta. The ptrace tracer gets user/kernel counter separation for free because
its tool runs in a **separate** tracer process; an in-guest tool's branches land
on the **guest's** side and must be explicitly subtracted.

The cost of that bracketing is exactly **two counter reads per handler**. This
experiment measures the ns and the RCB self-cost of a single counter read under
each candidate primitive, then projects two reads onto the landed S1(b) baseline.

## Why a standalone C microbench (and no liteinst2 child)

The bracketing cost is a property of `(read-primitive x kernel x PMU)` — it is
independent of how the liteinst hook is wired. A code fact makes the direct route
impossible anyway: **reverie-liteinst cannot reach `PerfCounter`.** `perf` is a
private module (`reverie-ptrace/src/lib.rs:45`; only `is_perf_supported` is
re-exported), and `tool_host.rs:616-638` returns `Unsupported` for
`read_clock`/`set_timer` ("LiteInst does not implement an RCB clock/timer"). So a
standalone C tool that **mirrors reverie's perf attrs exactly** isolates the one
quantity asked for, with the smallest possible blast radius and zero risk to Mode
B (the flagship, left UNTOUCHED).

`rcb_bracket_cost.c` opens a counting RCB counter with reverie's exact attrs
(`perf.rs:200-221`: `PERF_TYPE_RAW`, `exclude_kernel/guest/hv`, `pinned=1`,
per-tid, `cpu=-1`, mmap page) and times four things:

| primitive | what it models | reverie ref |
| --- | --- | --- |
| `read_syscall` | `read()` on the perf fd — the **index!=0 live** path reverie's fast-loop FALLS BACK to | `perf.rs:337-360`, fallback `perf.rs:420-430` |
| `read_rdpmc` | userspace `rdpmc` via the mmap seqlock — the in-guest-native primitive reverie **lacks** | none (absent) |
| `read_offset_stopped` | bare mmap `offset` load — the ptrace **stopped-tracee** read (`index==0`) | `ctr_value_fast_loop` `perf.rs:386-448` |
| `ptrace_reset_dance` | reset + set_period + enable ioctls per stop — ptrace's own per-stop bookkeeping | `timer.rs:651-653`, `perf.rs:293-310` |

`pinned=1` means a deschedule EOFs the read; the tool **detects the EOF and
aborts** rather than emit a corrupt number.

## Method

- Host `devbig014`, AMD EPYC 9D85 (family 0x1A Turin), kernel `6.18.39`, gcc
  11.5.0, `cc -O2`. `perf_event_paranoid=1`; `cap_user_rdpmc=1`, `index=2`,
  `pmc_width=48` at runtime.
- **1-CPU cgroup** (`systemd-run --user --scope -q -p AllowedCPUs=200`), single
  thread ⇒ axis (a) sequentialization = 0, isolating pure accounting cost — same
  isolation the S1(b) trap microbench used.
- **Two-point slope** `ns/read = (median(T(Nb)) - median(T(Na)))/(Nb-Na)` over
  reps=20 (+2 discarded warmups), `Na=1e5`, `Nb=1e6`; removes fixed loop
  overhead.
- **RCB self-cost** measured with a *second* `0x5100d1` counter bracketing a
  primitive loop minus an empty-loop baseline.
- **3 independent cgroup runs**; medians reported. rdpmc validated against the
  syscall read (agree within 4-5 RCB).

## Results (median of 3 runs; see `results.csv`)

| primitive | ns / read | RCB / read |
| --- | ---: | ---: |
| `read_syscall` (reverie's current in-guest fallback) | **239.28** | 6 |
| `read_rdpmc` (in-guest-native; not in reverie) | **13.86** | 3 |
| `read_offset_stopped` (ptrace stopped-tracee read) | 0.68 | 2 |
| `ptrace_reset_dance` (per-stop bookkeeping, 3 ioctls) | 543.81 | — |

## Interpretation — the verdict

Bracketing costs **2 reads per handler**:

- **`read()` fallback:** `2 x 239.28 = 478.6 ns` (+12 RCB). Projected onto the
  S1(b) Mode A upper bound `845.7 ns` → `1324.3 ns`, versus ptrace `26,393.7 ns`
  → **19.9x** win. The 31.2x drops but **SURVIVES** comfortably.
- **`rdpmc`:** `2 x 13.86 = 27.7 ns` (+6 RCB). → `873.4 ns` → **30.2x** win.
  The trap advantage is **essentially INTACT**.

The RCB self-cost is a **small constant** (6 RCB per rdpmc bracket pair, 12 per
read() pair), so it is trivially deducted — confirming the design's
"clean-baseline" premise. ptrace's own per-stop bookkeeping (~544 ns reset dance
+ ~0.68 ns stopped read) is real but already buried inside its 26µs round-trip.

**Conclusion:** the in-guest cost of exact branch accounting does not erase the
in-guest mechanism advantage. Even the pessimistic path reverie ships today
(`read()`) keeps a ~20x lead; adding an `rdpmc` primitive (a data-justified,
additive Reverie follow-up) makes bracketing nearly free. **The unified in-guest
patching backend is not gated by accounting cost.**

## What this does and does NOT establish

**Establishes (measured):** the per-read ns/RCB cost of each candidate primitive,
that `rdpmc` is available and correct here, and that two-read bracketing preserves
the S1(b) win under both primitives.

**Does not establish:** axis (a) sequentialization cost (excluded by design —
backend-independent, single-thread degenerate); rare-case costs (RCB overflow in
record/replay; live single-step-to-exact-count fallback, `timer.rs:800-856`),
which the design doc costs as gaps, not measured; a bare-null-hook figure (845.7
is an upper bound carrying strace I/O). Host-scoped to this AMD Turin box.

## Follow-up (data-justified, separate PR)

Add an additive `rdpmc` read primitive to reverie `perf.rs` for the `index!=0`
live-counter case (the path `ctr_value_fast_loop` currently abandons). Measured
worth it (~14 ns vs ~239 ns) but **not load-bearing** for this verdict, since
`read()` alone preserves ~20x. Touches perf/timer core ⇒ weigh
`post-facto-human-review` even though additive.

## Reproduction

```bash
cc -O2 -o rcb_bracket_cost rcb_bracket_cost.c
# args: Nb Na reps  (defaults 200000 20000 15; runs used 1000000 100000 20)
systemd-run --user --scope -q -p AllowedCPUs=<quiet-cpu> \
  ./rcb_bracket_cost 1000000 100000 20 > run.csv 2> run.stderr
# repeat 3x; take medians. Requires cap_user_rdpmc=1 for the rdpmc row.
```

Raw per-run CSVs and stderrs live in `ignored/` (gitignored per experiment
hygiene). `metadata.json` carries full SHAs, host facts, and the verdict
arithmetic.
