# LiteInst preload handshake: the guest dies in its constructor, not after it

**Date:** 2026-08-06 · **Task:** `liteinst_preload_handshake_fails` · **Local only, no egress**
**Hermit:** debug + release, both `g0f891e432a75-dirty` · **reverie:** `025d3780` · **Host:** devbig014
**Status:** committed to the parent, **not pushed** (egress 403)

```
Error: verify LiteInst runtime activation failed for tracee NNNN:
       tracee terminated before the required preload handshake completed (phase Waiting)
```

## What the error actually means (it is a post-hoc check, not the failure site)

`reverie-ptrace/src/task.rs:4586` runs **after the tracee has already exited** and reports that
`phase != Ready`. The message names the symptom at the point of detection; the failure happened
much earlier. The state machine is `PreExec → Waiting → Bootstrap → Ready`
(`task.rs:716-721`), and `Waiting → Bootstrap` is driven by the guest executing a trap with a
magic value in `rax` (`classify_liteinst_trap`, `task.rs:2231-2240`). Stuck at `Waiting` means
**that trap was never accepted** — either never fired, or fired and rejected.

## Proven

1. **The DSO is correctly built. The constructor IS registered.**
   `reverie_liteinst_initialize` is exported (`T` at `0x13c1b0`), and `.init_array` carries an
   `R_X86_64_64` relocation to it. The static hex dump of `.init_array` is all zeros, which is
   *normal* for a PIE — entries are filled by relocations at load time, so reading the raw bytes
   would have produced a false "no constructor" conclusion.

2. **Activation works, and its off-switch is a silent no-op.** Standalone, outside hermit:
   | invocation | result |
   |---|---|
   | `LD_PRELOAD=$DSO /bin/true` | **rc=0, silent** |
   | `LD_PRELOAD=$DSO REVERIE_LITEINST_HOST_RUNTIME=1 /bin/true` | **SIGTRAP, rc=133** |

   The trap is the handshake firing correctly with no tracer to catch it. The quiet case is
   `runtime.rs:404-405`: with no tool env set, `initialize_from_environment()` does
   `None => return Ok(())` — **the constructor runs, does nothing, and reports success.** Worth
   noting on its own: a preload runtime that silently no-ops when unconfigured cannot be
   distinguished from one that is absent.

3. **This is NOT the stale-runtime class.** The debug DSO is from Aug 3; a freshly built release
   DSO (Aug 6 04:43) sits beside the release binary. **Both configurations fail identically.**
   Staleness is ruled out, as is pin drift (already ruled out by the filing agent: both locks
   pin reverie `9470712a`).

4. **The guest dies BEFORE `main`.** Running `/usr/bin/env` as the guest:
   | backend | guest stdout lines |
   |---|---|
   | ptrace | **164** |
   | liteinst | **0** |

   Zero output from a program whose entire job is to print means it never reached `main`.
   Constructors run before `main`, so the guest died in or around the preload constructor. This
   corrects the natural reading of the error message, which suggests the guest ran and exited.

5. **`hermit` does wire the activation env.** `configure_host_command`
   (`reverie-liteinst/src/backend.rs:545-558`) canonicalizes the preload, prepends it to
   `LD_PRELOAD`, and sets `REVERIE_LITEINST_HOST_RUNTIME=1`; `run_host_with_preload` calls it
   (`backend.rs:218`), which is hermit's path (`hermit-cli/src/lib.rs:1553-1556`).

## Leading hypothesis — one probe from proof

Combining 2 and 4: the constructor **does** fire the begin-marker trap under hermit, the tracer
**fails to accept it**, the `SIGTRAP` is therefore not consumed, and it kills the guest before
`main`. `classify_liteinst_trap` returns `None` on rejection (`task.rs:2236`), leaving the phase
at `Waiting` — exactly the observed end state.

Rejection happens in `validate_liteinst_handshake` (`task.rs:2106-2146`), which requires all of:
`frame.version == 4`; `helper_stack_top >= 8` and 16-byte aligned; `trap_rip` equal to the
frame's `begin_rip`/`ready_rip`; and **all seven handshake RIPs inside a mapping whose path
string exactly equals `config.preload`**.

**The exact-path clause is refuted as the cause.** I checked the prediction directly: the
guest's own `/proc/self/maps` shows
`/home/newton/work/dev-hermit/hermit/target/debug/libreverie_liteinst.so`, byte-identical to
`readlink -f` of the path hermit canonicalizes. The DSO lives under `/home`, and hermit isolates
only guest `/tmp` and `/dev`, so the namespace does not rewrite it. That clause would pass.

So the rejection is one of the remaining clauses — most plausibly the frame contents
(`version` / `helper_stack_top`) being read from the wrong address or from a frame the DSO
populated differently than this tracer expects.

**The decisive next probe** is to make the rejection speak: add a temporary log in
`validate_liteinst_handshake` naming which clause returned `None` (and dump `frame.version`,
`helper_stack_top`, `trap_rip` vs `begin_rip`). One run then names the field. Everything above
narrows the search to five clauses in one function; guessing between them without that probe is
how a symptom gets "fixed" while the cause survives.

## Candidates refuted (each cost a probe — recording so they are not re-run)

| candidate | verdict | evidence |
|---|---|---|
| Pin drift between hermit and the runtime manifest | refuted (filing agent) | both locks pin reverie `9470712a` |
| Missing/unresolved DSO dependencies | refuted (filing agent) | `ldd` resolves everything |
| Constructor not registered in `.init_array` | **refuted** | `R_X86_64_64` → `reverie_liteinst_initialize` |
| Stale runtime DSO | **refuted** | fresh Aug-6 DSO fails identically |
| `LD_PRELOAD` / activation env not set by hermit | **refuted** | `backend.rs:545-558`, reached via `:218` |
| Exact-path clause in `validate_liteinst_handshake` | **refuted** | guest maps path == canonicalized `config.preload` |
| Guest runs to completion then fails a check | **refuted** | zero stdout vs 164 lines under ptrace |

## Reproduction

```sh
export LD_LIBRARY_PATH=/home/newton/.local/hermit-deps/lu/usr/lib64   # libunwind-x86_64.so.8
hermit/target/debug/hermit run --backend liteinst -- /bin/true        # phase Waiting, rc=1
# activation works standalone:
DSO=$PWD/hermit/target/debug/libreverie_liteinst.so
LD_PRELOAD=$DSO /bin/true                                   # rc=0, silent no-op
LD_PRELOAD=$DSO REVERIE_LITEINST_HOST_RUNTIME=1 /bin/true    # SIGTRAP rc=133 = handshake fired
```

## Secondary finding, separable and worth its own fix

`initialize_from_environment()` returning `Ok(())` when no tool env is set (`runtime.rs:405`)
means an unconfigured preload is **indistinguishable from an absent one**. Any future
mis-wiring of the activation env will present as this same late, uninformative "phase Waiting"
error rather than a loud failure at constructor time. A one-line diagnostic — log or fail when
the DSO is preloaded but no mode was selected — would have made the present bug self-reporting.

## No fix attempted

The cause is narrowed to five clauses in one function but not yet attributed to one. Patching
the plausible-looking candidates would mean changing product code in reverie that I cannot
validate here (no egress, cannot run validate), against my own evidence that the two most
obvious candidates are refuted. Filing the narrowed cause plus the naming probe is worth more
than a speculative patch.
