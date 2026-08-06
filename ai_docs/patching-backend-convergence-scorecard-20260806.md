# Patching-backend convergence (#283): measured status of both gates

**Task:** `patching-backend-inguest-convergence` · hermit-clone (opus-5), 2026-08-06
**Local, read-only, no egress.** Provenance (#268) on every cell: each is a `file:line` or a commit,
read at hermit `b64d893a` / reverie `025d3780` unless stated.

## The two gates are independent, and conflating them is the trap

The owner's gate is *"shared in-guest code, ptracer out of the path"*. Those are **two** conditions:

1. **Shared code** — does the backend call the one shared driver, or carry its own host?
2. **Ptracer out of the syscall path** — does hermit *invoke* it in-guest, or host-side?

A backend can pass (1) and fail (2), which is exactly what one of them does. An audit that scores
only (1) reports it converged.

## Scorecard

| backend | gate 1: shares `drive_tool_syscall` | gate 2: ptracer out of syscall path | both? |
|---|---|---|---|
| **e9patch** | **YES** — `reverie-e9patch/src/tool_host.rs:343` | **NO on landed code**; YES only on unlanded #1638 | **no** |
| **liteinst** | **YES** — `reverie-liteinst/src/tool_host.rs:309` | **NO** — hermit calls the *host-side* entrypoint | **no** |
| **sabre** | **NO** — two private hosts | **NO** — `sabre_ptrace::run` | **no** |

**0 of 3 backends satisfy both gates on landed code.**

### Provenance per cell

- **The shared driver**: `reverie/reverie-preload/src/tool_host.rs:222` `drive_tool_syscall`.
  Callers: `reverie-e9patch/src/tool_host.rs:343`, `reverie-liteinst/src/tool_host.rs:309`. Two of
  three.
- **SaBRe carries two more hosts**: `reverie/experimental/reverie-sabre/src/reverie_adapter.rs`
  (2211 lines) — `ReverieAdapter` (`:77`) and `RemoteReverieAdapter` (`:342`).
- **SaBRe's ptracer**: `hermit/hermit-cli/src/sabre_ptrace.rs` (1843 lines), wired at
  `hermit/hermit-cli/src/lib.rs:1056` (`sabre_ptrace::run`).
- **LiteInst's host-side invocation**: `hermit/hermit-cli/src/lib.rs:1555`
  `LiteinstBackend::run_host_with_preload::<Detcore>` — the **B-class, host-side** entrypoint. Its
  in-guest `run_with_preload` has **no hermit caller**.
- **e9patch**: the E9patch→Ptrace downgrade is still live at
  `hermit/hermit-cli/src/bin/hermit/run.rs:1924`; hermit `2fea6402f` removes it and adds the
  in-guest `detcore-e9patch` host, but is **not an ancestor of hermit main** (checked), and its gate
  — reverie #377 `4a42194` — is **not an ancestor of reverie main** either.

## The finding worth acting on: LiteInst is the false-converged cell

LiteInst **shares the driver and still runs the ptracer in the syscall path**, because hermit calls
`run_host_with_preload` rather than the in-guest entry. Nothing in the reverie-side code is wrong;
the *dispatch* is host-side. So a convergence audit keyed on "does it
use the shared host?" scores LiteInst green while a ptrace round-trip remains on every syscall.

That makes it the **cheapest** of the three to finish — the in-guest path exists and is shared; what
is missing is a hermit dispatch arm and the Detcore-embedding preload DSO, the same artifact
`detcore-e9patch` supplies for e9patch and `detcore-sabre` for SaBRe.

## SaBRe is the expensive one, and the record's key claim is still unproven

Converging SaBRe means deleting `sabre_ptrace.rs` (1843 lines) in favour of the in-process seccomp
gate. The prior analysis argues SaBRe already meets the precondition — `SabreGuest::regs()` builds
its frame from a thread-local, the same shape e9patch uses, so it could call `drive_tool_syscall`
as-is, additively.

**But the load-bearing claim — that the set of syscalls trapped by IP equals the set
`sabre_ptrace.rs` detects — is argued, not demonstrated.** Do not delete the ptracer on the strength
of the document. The two-sided predicate has to run first. I did not run it: it needs builds of two
coupled repos behind a local-only `[patch]` override, which needs a slot I do not have, and a fresh
build here already fails on `unwind-sys`/`libunwind-ptrace`.

Named risks that survive unchanged: the trusted-gate filter allows only two IPs, so the plugin's own
syscalls trap re-entrantly to SIGSYS — **unmeasured**; `SpinMutex` is a precondition, not an
optional cleanup, because the SaBRe hosts use `parking_lot::Mutex` + `HashMap` + `mimalloc` on
dispatch, which is async-signal-unsafe the moment SIGSYS is adopted; and SIGSYS ownership collides
with SaBRe's own sigaction virtualization.

## Ordered plan (each step is gated by the previous, and none is unblocked by more design)

1. **Land the e9patch chain** — reverie #377, then pin bumps, then hermit #1638 **last** (it removes
   the downgrade). Produces the first backend passing both gates and the reference for the others.
2. **LiteInst dispatch** — add the in-guest arm + Detcore-embedding DSO, mirroring
   `detcore-e9patch`. Cheapest remaining gate-2 win; no new abstraction.
3. **SaBRe** — run the two-sided IP-trap predicate; only on a demonstrated match, adopt
   `InProcessSeccomp`, switch to `drive_tool_syscall`, and delete `sabre_ptrace.rs`. `SpinMutex`
   first.

Perf work on any patching backend is gated on step 1 at minimum: a ptrace round-trip in the syscall
path makes a perf number a measurement of ptrace.

## What I did not do, and why

No code change. The design exists and is detailed; what was missing was a **measured status of the
two gates with provenance**, and the observation that they are independent. Re-designing would have
added a fourth opinion; re-implementing needs a slot. Every number above is a `file:line` or a
commit, checked in this session.
