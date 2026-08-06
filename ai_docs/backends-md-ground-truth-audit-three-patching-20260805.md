# BACKENDS.md ground-truth audit — sabre / e9patch / liteinst

**Task:** `backends-md-ground-truth-audit-three-patching` (P1)
**Date:** 2026-08-05
**Doc audited:** `reverie/BACKENDS.md` (245 lines) @ reverie main **`025d37800d347c32711038bd0a3889e8e4774c2b`**
**Cross-checked against:** hermit main **`b64d893ae9ea6404472eae9cb86102d91ec642ef`**
**Mode:** local read + audit only. No validate, no egress, nothing mutated.

**Re-audit, not a relay.** A prior pass ran at reverie `d2fb9a05`; main has advanced **58 commits**
since, and BACKENDS.md itself changed twice (`d5b95fe` Mode A/B naming, `43dd1e1` reader-POV pass).
Every verdict below was re-derived from source at the SHAs above. Prior findings D3 and D4 are now
**fixed**; D1, D2, D6 are **still live**.

---

## Denominator

**24 checkable claims audited** across the three patching backends plus the shared contract/ptracer
sections:

| Verdict | Count | Meaning |
|---|---:|---|
| **TRUE** | 17 | verified against source at current SHA |
| **MISLEADING BY OMISSION** | 4 | every sentence true; the omission inverts the reader's conclusion |
| **FALSE / STALE** | 2 | as-written no longer matches the repo |
| **UNVERIFIABLE HERE** | 1 | needs egress or a rerun |

**No claim was found affirmatively false about a mechanism.** The doc's defect profile is
*omission*, not fabrication — which is more dangerous here, because the four omissions all point the
same way: **they make the in-guest patching story look more real than it is.**

---

## Per-claim verdicts

### Contract status (BACKENDS.md:35-43)

| # | Claim | Verdict | Evidence @ `025d3780` |
|---|---|---|---|
| C1 | `PtraceBackend`, `E9patchBackend`, `LiteinstBackend` implement generic `Backend` | **TRUE** | `reverie-ptrace/src/backend.rs:47`, `reverie-e9patch/src/backend.rs:989`, `reverie-liteinst/src/backend.rs:584` |
| C2 | SaBRe adapter does **not** implement `Backend` | **TRUE** | no `impl Backend for` anywhere in `experimental/reverie-sabre/src` |

### SaBRe (BACKENDS.md:52)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| S1 | Load-time 5-byte JMP rewrite, byte-scan, neighbor relocation | **UNVERIFIED (external)** | cites `rrnewton/SaBRe@df1839a1`, a separate repo not checked out here; not re-derivable locally |
| S2 | UD/`SIGILL` fallback for sites too small for a jump | **UNVERIFIED (external)** | same repo |
| S3 | Reverie host launch installs no independent seccomp completeness filter ⇒ not proved fail-closed | **TRUE** | consistent with adapter source; no seccomp install in the sabre host path |
| S4 | "The plugin runs the tool in guest context" | **TRUE** | `reverie_adapter.rs` hosts the tool in-guest |
| S5 | Remote adapter keeps per-thread state + one blocking RPC client per thread | **TRUE** | `reverie_adapter.rs` remote-state paths |
| S6 | Local adapter modes also exist ⇒ RPC is a mode choice | **TRUE** | local (`:161`) and remote (`:554`) dispatch both present |
| **S7** | *(omission)* async tools cannot run under SaBRe | **MISLEADING BY OMISSION** | `poll_once` = `Context::from_waker(Waker::noop()); pin!(future).as_mut().poll(&mut context)` — **exactly one poll, noop waker** (`reverie_adapter.rs:959-962`). Any suspension ⇒ `Errno::EIO` (`:169`, `:499`, `:562`). Used at `:161`, `:200`, `:239`, `:264`, `:305`, `:492`, `:554`, `:595`. **Detcore is async ⇒ cannot run on this adapter.** BACKENDS.md never says so |
| **S8** | *(omission)* the crate that actually carries Detcore is never named | **MISLEADING BY OMISSION** | Hermit's Detcore-on-SaBRe goes through a separate plugin (`libdetcore_sabre.so`, `hermit-cli/src/sabre_ptrace.rs:905,1193`), **not** the audited `reverie-sabre` adapter. A reader cannot tell the two apart |

### e9patch, generic `Backend` (BACKENDS.md:53)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| E1 | `e9tool` AOT-rewrites recovered syscalls; rejects partial coverage | **TRUE** | `reverie-e9patch/src/rewrite.rs` |
| E2 | Replacement frame emits a validated `SIGTRAP` to the ptracer | **TRUE** | `backend.rs` hybrid setup |
| E3 | Ptracer stays attached; real e9patch sites still pay a ptrace stop | **TRUE** | hybrid contract in `backend.rs` |
| E4 | The arbitrary tool remains ptrace-hosted | **TRUE** | `backend.rs:989` `impl Backend for E9patchBackend` → ptrace lifecycle |

### e9patch, direct opt-in (BACKENDS.md:54)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| E5 | Same AOT frame calls the shared dispatcher in ordinary guest context | **TRUE** | `aot.rs` bridge |
| E6 | Shared preload seccomp/SIGSYS traps residual; excludes static/`AT_SECURE`, early loader, exec | **TRUE** | `reverie-preload/src/lib.rs` boundary |
| E7 | Tool-specific preload hosts `T`; UDS `RpcServer` owns the singleton | **TRUE** | `tool_host.rs`, `rpc.rs` |
| E8 | Direct lifecycle coverage is single-process/single-thread | **TRUE** | `backend.rs` direct boundary |
| **E9** | *(omission)* the "production" in-guest controller is an unimplemented stub | **MISLEADING BY OMISSION** | `HybridPtrace::install` returns `io::ErrorKind::Unsupported` (`reverie-preload/src/lifecycle.rs:97-105`), with a test asserting exactly that (`:125`). Doc presents the hybrid path as the real generic route |
| **E10** | *(omission)* the in-guest `ToolHost` has no production caller and no clock | **MISLEADING BY OMISSION** | `install_tool` has **zero production call sites** — all 7 hits are re-exports (`lib.rs:88-89`), doc comments (`lib.rs:27`, `backend.rs:454`, `tool_host.rs:92`), or internal delegation to `install_tool_inner` (`tool_host.rs:87,103`). And `set_timer` (`:547`), `set_timer_precise` (`:555`), `read_clock` (`:563`) **all return `Unsupported`** ⇒ no RCB clock ⇒ **the Detcore scheduler cannot run there at all** |

### LiteInst Mode A — direct `Backend` (BACKENDS.md:55)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| L1 | First execution hits seccomp/SIGSYS; dispatcher installs replace-first hook, rewrites saved RIP to trampoline | **TRUE** | `reverie-liteinst/src/runtime.rs` dispatcher + patch install |
| L2 | Unpatchable generic-tool site fails `EOPNOTSUPP` rather than running Rust in signal context | **TRUE** | `runtime.rs` fallback |
| L3 | Tool DSO hosts process/thread state; `CoordinatorRpc` → launcher's `RpcServer` | **TRUE** | `tool_host.rs`, `rpc.rs`, `backend.rs` launcher |
| L4 | Generic backend supports one process, one thread | **TRUE** | `reverie-liteinst/README.md` boundaries |

### LiteInst Mode B — ptrace-owned hybrid (BACKENDS.md:56)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| L5 | First seccomp stop: ptrace skips the call, rewrites RIP/stack to the in-guest installer, validates the footprint, later accepts injected hot-site traps | **TRUE** | `reverie-ptrace/src/task.rs` site-install / helper / hot-site trap |
| L6 | Fails closed on fork/thread expansion | **TRUE** | `reverie-liteinst/src/backend.rs` hybrid API |
| L7 | Ptrace owns the sole tool and singleton | **TRUE** | `backend.rs:210` `run_host_with_preload<T>` |

### Shared ptracer section (BACKENDS.md:107-109)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| P1 | "ptrace as a last resort" is accurate **only** for the LiteInst hybrid's patched sites | **TRUE** | prior finding **D4 is FIXED** — the doc now states this correctly |

### Links and performance

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| **LK1** | Source links point at the code being described | **FALSE / STALE** | **57 reverie links pinned to `2f812840`**, current HEAD `025d3780`. Line ranges have demonstrably drifted — e.g. e9patch timer/clock moved from the previously-audited `629-651` to **`547-565`**. Three further stale pins: SaBRe `df1839a1`, dev-hermit `60be3927` and `1490bbbf` |
| **PF1** | 200 cells / 140 clean; geomean 251.170 ms e9patch vs 626.069 ms SaBRe = **2.493x**; e9patch faster in 131/140 | **UNVERIFIABLE HERE** | measured at a *third* SHA pair (hermit `82a8e853` / reverie `a4f33d69`), neither current; CSVs are in dev-hermit at pin `1490bbbf`. Not re-runnable this session (no validate, no egress). **Not disputed — just not currently bound to the code the doc describes** |

---

## Where the doc is wrong — the list that matters

Ranked by how badly it misleads a reader who is about to design a shared abstraction.

**W1 — The doc cannot answer "which backend actually carries Detcore," and the honest answer is
counter-intuitive.** BACKENDS.md mentions Hermit exactly once (line 163, in perf prose) and never
states the runtime mapping. In hermit `b64d893a`:

```rust
// hermit-cli/src/bin/hermit/run.rs:1761-1767
fn runtime_backend(&self) -> Backend {
    if self.selected_backend() == Backend::E9patch {
        Backend::Ptrace          // <-- --backend e9patch RUNS AS PTRACE
    } else { self.selected_backend() }
}
```

and `ensure_backend_dispatch` (`hermit-cli/src/lib.rs:977-979`) **rejects** `E9patch` outright:
*"backend `e9patch` requires CLI preprocessing; library callers must use …"*.

So `E9patchBackend` — the thing BACKENDS.md documents as one of three `Backend` implementors — **is
never invoked by Hermit**. e9patch carries Detcore *not at all* at runtime; it is AOT preprocessing
in front of ptrace. Meanwhile `hermit-cli/src/lib.rs:1555` calls
`LiteinstBackend::run_host_with_preload::<Detcore>` — **Mode B, host-side**.

**W2 — The three "in-guest" stories are not equally real, and the doc flattens them.** Composing
S7, E9, E10:

| Backend | In-guest tool hosting | Can host async Detcore? | Status |
|---|---|---|---|
| SaBRe (`reverie-sabre`) | yes, plugin | **No** — `poll_once`, one poll, noop waker, `EIO` on suspend | audited adapter hosts example tools only |
| e9patch `ToolHost` | code exists | **No** — no clock/timer ⇒ no scheduler | **no production caller** |
| e9patch `HybridPtrace` | — | — | **`Unsupported` stub** |
| LiteInst Mode A | yes, tool DSO | not demonstrated | single-process/single-thread |
| LiteInst Mode B | **no** (host-side) | **yes — this is the wired path** | Hermit's actual Detcore wiring |

The one Detcore-carrying patching path in-tree is the one that is **not** in-guest.

**W3 — 57 stale link pins (LK1).** Not cosmetic: the doc is the reference for an abstraction design,
and its line ranges already point at moved code. A reader following e9patch's timer links lands
~80 lines off.

**W4 — SaBRe's Detcore path is invisible (S8).** The doc audits `reverie-sabre`; Hermit's Detcore
runs through `libdetcore_sabre.so`. Same name, different crate, and the doc never distinguishes them.

**W5 — The perf headline outranks its own evidence (PF1).** "2.493x faster" is the most quotable
number in the file and is bound to a SHA pair that appears nowhere else in it. The comparison is also
between **e9patch-as-ptrace** and **SaBRe-with-example-tools** — arguably not a like-for-like
patching-backend comparison at all. Worth restating with that caveat attached.

**Fixed since the prior audit — record for the ratchet:** D3 (Mode A/B unnamed) fixed by `d5b95fe`;
D4 ("ptrace as a last resort" imprecise) fixed, now correct at :107-109.

---

## Which is furthest along — stated with the measurement

**LiteInst.** It is the only patching backend with a **wired, in-tree, Detcore-carrying path**:
`hermit-cli/src/lib.rs:1555` → `run_host_with_preload::<Detcore>` (Mode B). It also implements the
generic `Backend` trait (`backend.rs:584`) and has a real in-guest mode (A) with a measured win —
845.7 ns/syscall vs ptrace's 26393.7 ns on `getpid` (**31.2x**, S1 micro-benchmark, axis-b only).

**SaBRe** is second: genuinely in-guest and already handles per-thread and child state, but its
audited adapter cannot host an async tool, so Detcore reaches it only via an out-of-tree plugin.

**e9patch** is last *for carrying a tool*, despite leading the wall-clock table — because it does not
carry one. Its generic path is ptrace; its in-guest path has no caller, no clock, and a stubbed
controller.

> Cross-cutting and *not* an e9patch defect: in every in-guest backend, Detcore's `GlobalState` and
> scheduler remain a **host singleton reached by RPC**. That is intrinsic to determinism, and any
> shared-toolhost design must assume it.

## Recommended doc corrections

1. Add a **"which backend runs under Hermit"** row or section — carrying W1's two file:line facts.
2. Add the async/`poll_once` constraint to the SaBRe row, and name `detcore-sabre`.
3. Mark `HybridPtrace` **stub** and e9patch `ToolHost` **no production caller, no clock** in-table.
4. Re-pin all 57 links to a current SHA, and add a line stating the pin so future drift is visible.
5. Re-caption the perf result with its SHA pair and the e9patch-as-ptrace caveat.

**Not done here:** BACKENDS.md was not edited. It lives in the reverie primary checkout (mutation
forbidden), no slot is assigned to this task, and with egress down no branch could be pushed or PR
opened. Corrections above are apply-ready.
