# Same-core affinity: the lever has no target where optimising is legitimate

**Task:** `guest-coordinator-same-core-affinity` (P1, OWNER) · **Date:** 2026-08-06 · **Author:** hermit-design
**Bound to:** reverie `025d3780` · hermit `f89c69766` · **Source audit only.** No benchmark, no build, no egress.

The task asks to pin the guest thread and host coordinator to the same core and measure the delta. I
did not implement it, and the reason is not "no slot" — it is that **the hop this lever targets does
not exist on the backends where optimising is legitimate**, and on the backends where it does exist it
is already measured as a regression.

Three independent facts, each sufficient on its own. Below each is what would change it.

---

## 1. On ptrace and KVM there is no coordinator to pin (source-verified)

`reverie-ptrace/src/task.rs:5599`:

```rust
async fn send_rpc(&self, args: G::Request) -> G::Response {
    // In debugging mode we round-trip through a serialized representation
    // to make sure it works.
    let deserial = if cfg!(debug_assertions) { … bincode … };
    …
}
```

`send_rpc` calls the `GlobalState` **directly, in the same address space**. The bincode round-trip is a
`debug_assertions`-only self-check, not a transport. Corroborated by the dependency graph:

```
reverie-liteinst/Cargo.toml:35:  reverie-rpc-transport = { … }
reverie-ptrace/Cargo.toml:       (absent)
reverie-kvm/Cargo.toml:          (absent)
```

**So "guest thread ↔ host coordinator" names a hop that exists only on the out-of-process backends** —
liteinst, and e9patch/DBI when a coordinator socket is configured. On ptrace and KVM there is no
coordinator process, no UDS, and no context switch between tool and coordinator to make cheaper.

*What would change this:* nothing short of moving ptrace/KVM to an out-of-process GlobalState, which
would be adding the cost this lever is meant to remove.

---

## 2. Where the hop does exist, same-core is a measured regression today

Already recorded on this task (2026-08-04, from `experiments/coordinator-rpc-leverb-ceiling_20260804/`),
p50 µs/hop:

| transport | same-core | cross-core | same − cross |
| --- | ---: | ---: | ---: |
| `uds_full` (today, ~8 syscalls/hop) | **12.33** | 7.58 | **+4.75 → 1.63× SLOWER** |
| `uds_lean` (post-trim, ~4) | 4.40 | 4.09 | +0.31 → ~8% slower |
| `futex` (~2 — lever B, **rejected**) | 2.38 | 3.28 | −0.90 → 1.38× faster |

The sign flips `+ → ~0 → −` as syscalls-per-hop fall 8 → 4 → 2. The mechanism is not mysterious: many
serialized syscalls **punish** co-residency, because on one core the peer cannot run until you yield;
a cheap hop **rewards** locality. The clean same-core win only appears at the ~2-syscall transport that
was already rejected.

So the owner's premise — *"guest yields ⇒ host runs on the same core immediately, cheaper switches"* —
is right about the mechanism and wrong about the sign at today's hop cost.

*What would change this:* the guest-side hop trim (`perf_coordinator_rpc_guest`) landing **and** the
post-trim syscall count falling nearer 2 than 4. At `uds_lean` same-core is still marginally slower, so
the trim is **necessary but likely not sufficient**.

---

## 3. Even a win here is ~3% of the real hop — and it is on the path the owner says not to optimise

Two rulings already on record collide precisely here:

* The transport layer is **~3% of the real hop**. Established by refutation: the microbench predicted
  `12.33 → 4.40µs`; the real-path A/B measured **66.60 → 67.05µs, IQRs fully overlapping, no effect**.
* On **patching** backends ~67µs is *evidence ptrace is still in the path* — an architectural defect,
  **not a budget to optimise**.

The out-of-process backends from §1 (liteinst, e9patch) **are** the patching backends. So the only
place this lever has a target is the place where the correct response is to remove ptrace from the
path, not to shave the switch.

---

## 4. What I did NOT answer, and why it does not change the disposition

The task's item (1) — *"are guest and coordinator co-resident today? Sample actual CPU assignment
during a det-mode run, do not assume."* — I answered **structurally, not empirically**:

* ptrace/KVM: the question is ill-posed. Tool and coordinator are the same thread in one address space.
* liteinst: they are genuinely separate processes, so placement is a real question — and it is
  **unmeasured**. Answering it needs a det-mode liteinst run under representative load.

I did not run it. Under any outcome the disposition is the same: if they land cross-core today, forcing
same-core regresses per §2; if they already land same-core, the lever is a no-op. And the measurement
itself is heavy concurrent load on a shared box, which standing directives say to avoid.

*What would change this:* it becomes worth measuring **after** the hop trim lands, at which point the
question is "what is the post-trim syscall count, and does same-vs-cross flip at it" — not "pin them".

---

## 5. The constructive redirect: on ptrace, the expensive switch is a different one

The owner's underlying goal — make the context switch cheaper — is sound. It is the *target* that is
misnamed for ptrace.

On ptrace there is no coordinator switch (§1), but there **is** an expensive switch: **tracee ↔ tracer**,
the ptrace stop. The existing profile (real hermit, `perf record -g`, 351k samples) puts **~49.4% of the
hop in kernel `tasklist_lock` contention** under `ptrace_stop`/`ptrace_notify` (20.6%),
`ptrace_check_attach` (19.4%) and `__do_wait` (9.4%), with all userspace self-time at ~3-4%.

So if co-residency is to be tried on ptrace, the pair to pin is **tracee and tracer**, not guest and
coordinator. That is a different experiment with its own unmeasured sign — and §2's mechanism warns it
could go either way, because a ptrace stop is also a serialized handoff. **It is worth one measurement;
it is not worth an implementation first.**

---

## 6. Disposition

**Do not implement same-core affinity now.** Sequence it strictly after the guest-side hop trim, and
re-scope it from *"pin guest and coordinator to the same core"* to:

> *measure the real det-mode placement and the forced same-vs-cross delta at the post-trim syscall
> count, on liteinst; and separately measure tracee↔tracer co-residency on ptrace.*

That re-scope is what the earlier ordering-gate note concluded from the bench data; this document adds
the two reasons the bench could not see — **§1 (no target on ptrace/KVM at all)** and **§3 (the only
target is on the path the owner has ruled a defect rather than a budget)**.

---

## 7. Provenance, as required

| Number | Where from | Conditions |
| --- | --- | --- |
| same/cross table (§2) | `experiments/coordinator-rpc-leverb-ceiling_20260804/` | standalone C 2-thread ping-pong, K=1, 200k iters, medians+IQR, devbig014 316-core, loadavg ~50 with concurrent drain validates. **Absolutes load-inflated; deltas at same pin robust.** Transport layer, not hermit. |
| 66.60 → 67.05µs (§3) | real-path A/B | 8 interleaved reps, identical hermit binary, only `libreverie_liteinst.so` differing. Real path — but a patching backend, see §3. |
| ~49.4% / 3-4% (§5) | `perf record -g` on real hermit | 351k samples, `syscall_loop 1000000`, release, devbig014. Shares robust; **absolute ~31µs/getpid inflated by other tenants on the shared box.** |
| ~2.4µs residual (task premise) | same microbench as §2 | transport layer; **real-path share unmeasured** |

**I generated no new numbers.** Every figure above is quoted with the conditions under which it was
taken, which is the discipline this lane adopted after the ceiling that did not transfer.

---

## 8. Not established

* **No benchmark, no build, no run, no host measured.** §1 is a source read; §2, §3 and §5 are quoted.
* **Real det-mode placement on liteinst is genuinely unknown** (§4). I argue it does not change the
  disposition; I did not measure it.
* **§5's tracee↔tracer suggestion is unmeasured in both magnitude and sign.** I am proposing an
  experiment, not predicting its result — and §2's mechanism is a reason to expect it could regress.
* The `debug_assertions` bincode round-trip in §1 means ptrace's in-process RPC *does* serialize in
  debug builds. Any placement measurement taken on a debug binary would not represent release
  behaviour.
* I did not verify whether DBI-with-coordinator-socket behaves like liteinst here; the dependency check
  covered ptrace, KVM and liteinst only.
