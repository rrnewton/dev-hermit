# Which layer was that number measured at? — provenance audit of every perf figure steering work

**Task:** `microbench-ceilings-must-be-confirmed-on-the-real-path-before-driving-work` (P1)
**Date:** 2026-08-06 · **Author:** hermit-design · Local audit. No benchmark run, no build, no egress.

The triggering result: a microbench predicted `uds_full 12.33µs → uds_lean 4.40µs`; the real-path A/B
measured **66.60µs → 67.05µs with fully overlapping IQRs — no effect**. This audit does the three things
the task asks: enumerate the figures with their layer, say whether that layer's share of the real path
is known, and address the decomposition.

**Two findings came out of it that were not in the task:** the same transfer error is live one program
larger (§3), and the decomposition the task asks for is already half-done in a figure nobody connected
to it (§4).

---

## 1. The inventory — every figure, and the layer it was measured at

| # | Figure | Layer | Share of the real path known? |
| --- | --- | --- | --- |
| 1 | `uds_full 12.33` / `uds_lean 4.40` / `futex 2.38` µs/hop same-core (cross-core 7.58/4.09/3.28) | standalone C 2-thread ping-pong, **not hermit** | **YES, now — ~3%.** Refuted on the real path |
| 2 | `~2.4µs` context-switch "residual" | same microbench | **NO.** ~4% if the same ratio holds — never confirmed |
| 3 | `d(uds_full→uds_lean) = 7.93µs` = "~80% of removable cost" | same microbench | **NO.** "Removable" is microbench-scoped; the real A/B says removable ≈ 0 |
| 4 | `~62µs/call`, **35% user / 65% sys** at site-703 | **real** hermit det-mode (`sched_yield` loop) | absolutes self-stated load-inflated; **only the ratio is robust** |
| 5 | `~13.6µs` "independently-observed floor" | **idle-gated flagship** figure | different host condition — #4 is 4.5× it *because loaded* |
| 6 | S1 Mode A `845.7ns` vs ptrace `26393.7ns` = **31.2×** | standalone C microbench, 1-CPU cgroup | **NO** — and see §3 |
| 7 | `read()` 239.28ns · `rdpmc` 13.86ns · ptrace reset-dance 543.81ns → projected **19.9× / 30.2×** | same microbench, **projected onto #6** | **NO** — a projection onto a microbench baseline |
| 8 | LiteInst patch fastpath `0.58µs/call` | perf-lane attribution | partially — its own note says "`--strict` cost is Detcore RPC, not the patch" |
| 9 | ptrace `~31µs/getpid`; **49.4% kernel** in `native_queued_spin_lock_slowpath` → tasklist_lock, userspace self-time only **3-4%** | **real** hermit, `perf record -g`, 351k samples | real path; self-stated inflated by other tenants on the 316-core box |
| 10 | **66.60 → 67.05µs** A/B, 8 interleaved reps, only `libreverie_liteinst.so` differing | **real path** | this *is* the real path — but see §2 |

**Seven of ten are microbench-layer or condition-qualified.** Exactly one figure (#1) has had its share
of the real path established, and it was established by *refutation*.

---

## 2. The one real-path number is a defect signature, not a baseline

Per the owner's note: on **patching** backends ~67µs is *evidence that ptrace is still in the path* — an
architectural defect — **not a budget to decompose**. That note applies directly to figure #10, because
that A/B varied only `libreverie_liteinst.so`, i.e. it was measured on a patching backend.

So the number that refuted the microbench is itself a number that should not exist where it was taken.
Two consequences:

* **The refutation stands.** "The transport saving did not transfer" is true regardless of *why* the
  baseline is 67µs — an unchanged 66.60→67.05 with overlapping IQRs is a null result either way.
* **The decomposition must not be done there.** Item (3) of the audit — "decompose the ~67µs into
  Detcore scheduler vs ptrace host vs tokio reactor vs context switch" — cannot be executed against the
  measurement that motivated it, because on a patching backend the correct response is to remove ptrace
  from the path, not to apportion it. **Decomposition applies to KVM/ptrace only.**

That distinction is easy to lose: the same 67µs is a *budget* on ptrace and a *bug* on LiteInst.

---

## 3. The same transfer error is live one program larger — S1's 31.2×

This is the finding the audit surfaced that the task did not name.

Figures #6/#7 (the S1 in-guest-vs-ptrace result driving the unified in-guest patching backend program)
are microbench-layer, and their own record states the exclusion plainly:

> *"can in-guest interception beat a ptrace round-trip on the ONE cost axis in-guest can win — **(b)
> instrumentation trap round-trip** — while **(a) sequentialization** (park+RPC to global scheduler,
> backend-independent) is held constant? **Chose getpid because it needs no scheduling decision ⇒ axis
> (a) = 0**."*

Holding axis (a) at zero is *correct experimental design* for the question asked. But axis (a) is the
coordinator RPC — figure #4's ~62µs — which the real-path A/B shows dominates the hop. So **31.2× is a
speedup on a term that was deliberately set to zero in the measurement and is the largest term in
production.**

That is structurally the identical error as `12.33 → 4.40`: a real, well-measured number at one layer,
quoted without its share of the layer above. And #7 compounds it by *projecting* (19.9× / 30.2×) onto
that same baseline.

**This does not refute S1.** The trap-round-trip win is real on its axis, and the unified in-guest
backend has architectural justification independent of latency (removing ptrace from the path is the
point — §2). What it refutes is **quoting 31.2× as an expected end-to-end speedup**. Before that number
sizes any further work it needs the same real-path A/B that killed the transport ceiling — on a
scheduling-bearing workload, not `getpid`.

---

## 4. The decomposition is already half-done, in figure #9

Audit item (3) asks for the real hop split into Detcore scheduler / ptrace host / tokio reactor /
context switch. For **ptrace** — the backend where the note says decomposition is legitimate — figure #9
already supplies most of it, from `perf record -g` on real hermit (351k samples):

| Component | Share |
| --- | --- |
| kernel `native_queued_spin_lock_slowpath` → `queued_read_lock_slowpath` (**tasklist_lock**) | **~49.4%** |
| — under `ptrace_stop`/`ptrace_notify` (incl. seccomp `__seccomp_filter`) | 20.6% |
| — under `ptrace_check_attach` (feeds `safeptrace::getregs`) | 19.4% |
| — under `__do_wait` | 9.4% |
| **all userspace self-time** (`__memmove_avx512` 1.16%, `WaitFuture::poll` 0.64%, vdso 0.40%, tokio park **0.39%**, …) | **~3-4%** |

Read against the audit's four buckets: **"ptrace host" ≈ half the hop; "tokio reactor" ≈ 0.39%;
"Detcore scheduler" userspace ≈ low single digits.** Nobody connected this profile to the ceiling
question, yet it answers most of it — and it answers it in the direction that matters: the userspace
optimisation lane is **exhausted**, and the levers are structural (fewer ptrace stops, or a
determinization-model change), not micro-optimisation.

It also independently corroborates §3: if userspace self-time is 3-4%, then a lever that removes
userspace work — which is what the transport trim was — cannot produce a large end-to-end win. The
transport refutation was predictable from a profile that already existed.

**Caveat carried with it:** figure #9's absolutes are self-stated as inflated by other tenants
hammering `tasklist_lock` on a shared 316-core box. The *shares* are the usable part; the ~31µs/getpid
is not a quiet-host number.

---

## 5. What this means for the levers currently in flight

| Lever | Sized by | Verdict |
| --- | --- | --- |
| Lever B (shared-mem ring) | #1 | already rejected; now doubly — transport is ~3% |
| Cheap guest-side trim (PR #369) | #1, #3 | real cleanup, **not** a latency win. Do not sell it as one |
| affinity → SCX → BPF-backend ladder | #2 | **unsized.** ~2.4µs is a transport-layer number; its real-path share is unmeasured. Measure before investing |
| Unified in-guest patching backend | #6, #7 | architecturally justified (§2); **31.2× is not an end-to-end claim** (§3) |
| Further ptrace userspace micro-opt | #9 | **exhausted** — 3-4% of the hop total |

---

## 6. The rule, stated so it can be applied mechanically

A performance figure may size a lever only if it carries **three** things:

1. **The layer it was measured at** (transport microbench / in-guest trap / real det-mode hop / profile share).
2. **That layer's share of the real path** — measured, not assumed. Absent this, the figure can rank
   alternatives *within* its layer and nothing more.
3. **The host condition** — idle-gated vs loaded, pinned vs shared. Figures #4, #5 and #9 differ by
   4.5× on this axis alone, and #5 vs #4 is the same quantity under two conditions.

"4.40µs" fails (2). "31.2×" fails (2) and additionally excluded the dominant term by construction.
"~2.4µs" fails (2). This is the same defect the ledger work keeps finding under a different name: **a
value that does not carry its conditions is a proxy**, and proxies steer work into the smallest term
because the smallest term is the easiest one to model.

---

## 7. Not established

* **No benchmark was run, nothing was built, no host was measured.** Every figure above is quoted from
  its recorded provenance (task notes, memory records, experiment artifacts); I re-derived none of them.
* **§3's claim that axis (a) dominates on a scheduling-bearing workload** rests on figure #4 (~62µs
  coordinator RPC) being the relevant term for such a workload. That is consistent with #4's own
  "floor is workload-shaped … bites yield-heavy/multi-thread-handoff workloads", but I did not measure
  an S1-style A/B on a yielding workload — which is precisely the experiment §3 says is owed.
* **§4's mapping of profile buckets onto the audit's four categories is my reading** of the symbol
  attribution, not a re-run of `perf record`. "tokio reactor ≈ 0.39%" is one symbol (`tokio
  current_thread park`), which may understate reactor cost spread across other frames.
* **Figure #8 (0.58µs LiteInst fastpath) I could not place precisely** — its layer is recorded as
  perf-lane attribution without a stated harness, so it is listed but not relied on.
* The inventory is scoped to figures I could find in the task graph and memory. A number steering work
  that lives only in a PR comment or a chat message would not appear here.
