# LiteInst: the exact gaps blocking zero-ptracer (report only)

**Date:** 2026-08-07 · **Task:** `ratchet-liteinst-parity-blocked-report-only` · **Author:** hermit-w1

Report only. No source changed, no CLI flag flipped, no compat cell claimed.

## Provenance

Citations are against `origin/main` of each repo, **not** the primary checkouts — both primaries were
behind and the relevant files differ:

| repo | citation commit | primary at the time |
| --- | --- | --- |
| hermit | `75506005d` | `f89c69766` (13 behind; `run.rs` differs) |
| reverie | `6144323c5` | `dd3c178ea` (2 behind; `lifecycle.rs` differs by +128) |

Litmus binary: `hermit/target/release/hermit`, self-reporting `0.2.0 (2026-08-06, gf89c69766371-dirty)`.

**Stated limitation:** that binary is built from hermit `f89c69766` with a **dirty** tree, so the runtime
observations bind to `f89c69766`+dirt, not to `75506005d`. Source citations and runtime observations are
therefore at two different commits. Nothing below depends on a line that changed between them, but the
gap was not closed by rebuilding.

## Verdict

LiteInst **fails** the zero-ptracer gate. Not partially — on the shipped hermit CLI path the Detcore Tool
runs in the ptrace host, and the runtime says so unprompted on every run:

```
hermit: [liteinst host hybrid] activation verified (traps=1, hooks=31); Detcore Tool active in ptrace host
```

That is a runtime self-report, not an inference from source.

## Litmus — stated exactly as observed

The acceptance test is framed as "strace attaching to hermit itself — ptrace permits one tracer, so a
successful attach is positive proof no ptracer is present." Measured three ways. The first two are
reported because the naive reading yields a **false positive**, which matters more than the pass/fail.

### L-A — `strace -qq -e trace=ptrace hermit run --backend=liteinst -- /bin/true` (no `-f`)

**Attach succeeds**, guest runs, clean exit. But hermit itself issued **9,430 ptrace calls** against a
single tracee pid, none failing:

| request | count |
| --- | --- |
| `PTRACE_GETREGSET` | 4971 |
| `PTRACE_SETREGSET` | 1597 |
| `PTRACE_PEEKDATA` | 857 |
| `PTRACE_CONT` | 734 |
| `PTRACE_SYSCALL` | 571 |
| `PTRACE_SINGLESTEP` | 449 |
| `PTRACE_GETSIGINFO` | 199 |
| `PTRACE_POKEDATA` | 50 |
| `PTRACE_SETOPTIONS` | 1 |
| `PTRACE_GETEVENTMSG` | 1 |

So "strace attached to hermit successfully" is **not** evidence of zero ptracer. hermit is the *tracer*,
and tracing a tracer is legal — the proxy does not bind to the claim. 571 `PTRACE_SYSCALL` stops for
`/bin/true` is a per-syscall ptracer on the hot path, observed directly.

9,430 is a **lower bound**: without `-f`, strace follows only the initial thread. A `--backend=ptrace`
control under the same command reported 0 ptrace calls — which means "its ptrace traffic runs on a tokio
worker thread strace never followed", not "ptrace uses no ptrace". The two counts are not comparable and
are not compared here.

### L-B — `strace -f …` (follows threads *and* the guest child) — the reading that discriminates

**FAILS.**

```
1600304 ptrace(PTRACE_TRACEME) = -1 EPERM (Operation not permitted)
Error: failed to open pidfd for LiteInst tracee 1600304: -110 ETIMEDOUT (Connection timed out)
```

ptrace permits one tracer; strace took the guest, so hermit's required `PTRACE_TRACEME` was refused and
the backend could not start. **LiteInst requires being the guest's tracer.**

*Non-vacuous:* the same test on `--backend=ptrace` also produces `TRACEME EPERM` — it fires on the
known-ptracer reference. `--backend=sabre` and `--backend=dbi` returned "not included in this build", so
they yielded no data and no claim is made about them.

*Asymmetry:* ptrace fails its availability **preflight** with a clean typed message; liteinst has no
equivalent preflight and dies later with an ETIMEDOUT pidfd error.

### L-C — guest-side attach (`hermit run --backend=liteinst -- strace -f /bin/true`)

Does **not reach** the ptrace question. It dies earlier:

```
reject LiteInst post-start exec failed for tracee 3: the required preload runtime
cannot be preserved across exec (phase Ready)
```

Attributed by bisecting the guest: a fork+exec guest (`sh -c /bin/true`) fails identically, while a
no-fork guest (`sh -c 'exit 3'`) succeeds. The blocker is the exec-rebootstrap gap, not ptrace. **The
guest-side litmus is unrunnable on LiteInst until exec rebootstrap exists.**

## What forces a ptracer into the syscall path (B-class)

**B1 — the CLI dispatch itself.** `hermit-cli/src/lib.rs:1552-1567` routes `Backend::Liteinst` to
`reverie_liteinst::LiteinstBackend::run_host_with_preload::<Detcore>` (`:1555`); the output variant at
`:1662-1666` does the same. That entry point is `TracerBuilder::<T>::new(command)` with `T=Detcore` at
`reverie-liteinst/src/backend.rs:219`, whose own doc at `:199-202` states *"Ptrace owns the sole Tool and
GlobalTool from exec onward"*. Per the in-tree discriminator this is the literal B-class violation. The
user-visible CLI help says it too: `liteinst: Use the ptrace-hosted LiteInst hybrid with one Detcore
Tool` (`hermit-cli/src/lib.rs:601`).

*Requires:* flip the dispatch to the in-guest `run_with_preload` (`backend.rs:362`), which already uses
the A-class lifecycle-only reaper `TracerBuilder::<()>::new(command)` at `backend.rs:762`. **Necessary
but not sufficient** — every gap below currently makes that path fail closed.

**B2 — the in-guest path has no hermit caller.** `git grep run_with_preload` over hermit `origin/main`
returns nothing. The in-guest entry points are dead code from hermit's perspective.

**B3 — dispatch counters live in the ptrace crate.** `reverie-ptrace/src/liteinst_stats.rs:117`
(`record_ptrace_installation`) and `:129` (`record_direct_hook`) — the host counts per-site dispatch,
which is only possible because the host observes it.

## In-guest gaps

Authoritative in-tree statement: `reverie-liteinst/AGENTS.md:92-103` ("Supported Boundary"), restated at
`reverie-liteinst/src/backend.rs:354-361`. Both agree and both are current.

### G1 — RCB clock is a constant zero
`reverie-liteinst/src/tool_host.rs:755-760`; `read_clock` returns `Ok(0)`, commented *"LiteInst has no
sample yet, so zero is the honest deterministic lower bound."*

*Requires:* a real retired-conditional-branch source readable in-guest — a self-monitoring `perf_event`
fd opened per thread and read via RDPMC or `read(2)` from the guest, plus thread-create/exec re-arm and
save/restore across the SIGSYS boundary so the count is not charged to the runtime's own instructions.

### G2 — timer arming delivers nothing
`tool_host.rs:744-748` (`set_timer`) and `:750-753` (`set_timer_precise`) both return `Ok(())` having done
nothing; the comment concedes *"a CPU-bound thread cannot yet be preempted between syscalls."* The only
scheduling boundary is a syscall, so a compute-bound guest is unpreemptable and any schedule depending on
timer preemption is unreachable.

*Requires:* PMU interrupt delivery to the guest (`perf_event` with a period, signal-driven delivery to the
owning thread), skid accounted for, arriving as a Reverie timer event with no host round trip.

### G3 — CPUID / RDTSC / RDTSCP / RDRAND / RDSEED are absent, not stubbed
A case-insensitive grep for all five across `reverie-liteinst/src/*.rs` returns **zero** hits.
`AGENTS.md:99-101` lists them as "not routed to the in-guest Tool as Reverie events." The existing
determinization is ptrace-only: CPUID faulting is armed at `reverie-ptrace/src/task.rs:1468` via
`ARCH_SET_CPUID(0)`, a mechanism whose trap (SIGSEGV) is serviced by the ptracer. The fixture
`tests/fixtures/hybrid_cpuid_policy.c` exercises `ARCH_GET/SET_CPUID` *policy*, not determinized CPUID
values — it is not coverage of this gap.

*Requires:* keep `ARCH_SET_CPUID(0)` to make CPUID fault, then service SIGSEGV in-guest and emulate the
leaf from Detcore state. RDTSC/RDTSCP need `CR4.TSD` (a prctl the guest cannot set for itself today) or
instruction rewriting. **RDRAND/RDSEED cannot fault at all** and must be rewritten at patch time — making
them a LiteInst rewriting-engine problem, not a Tool-event problem.

### G4 — clone3 / vfork / thread-clone / execve / execveat fail closed
`tool_host.rs:519-542` (`injected_syscall_guard`): all return `Errno::EOPNOTSUPP` (`:536`). Only
single-threaded plain fork is allowed (`is_plain_fork`, `:510-516`). Correct fail-closed behaviour, and
the direct cause of litmus L-C.

*Requires:* for thread clone, per-thread runtime state and a shared coordinator connection established in
the new thread before it can trap; for exec, re-bootstrapping the preload across address-space
replacement (the sealed-memfd bootstrap at `AGENTS.md:84-85` survives exec only if re-established) with
tool-state handover; for vfork, either resolving the shared-address-space hazard or a documented
downgrade to fork.

### G5 — vDSO is not intercepted
A grep for `vdso` across `reverie-liteinst/src/` returns exactly one hit, a comment in a test fixture
(`src/bin/lifecycle_guest.rs:115`, *"The fixture only proves that ptrace syscallized the vDSO call"*).
`AGENTS.md:97` lists vDSO interception as not implemented.

*Requires:* patch or replace the vDSO mapping so `clock_gettime`/`gettimeofday`/`time`/`getcpu` route to
the in-guest Tool. **Under the host-hybrid these are currently syscallized by ptrace, so this gap is
masked by the very ptracer the gate wants removed — removing the ptracer exposes it.**

### G6 — guest callable signal handlers restricted
`tool_host.rs:526-533` rejects unsupported `sigaction`, non-null `sigaltstack`, and non-null
`rt_sigprocmask` with `EPERM` (`:538`). `AGENTS.md:98` lists guest callable signal handlers as not
implemented.

*Requires:* nested-signal-safe reentry of the SIGSYS dispatcher and an alt-stack discipline that does not
collide with the guest's own.

### G7 — unpatchable-site fallback: duplicated, not missing
`AGENTS.md:97` and `backend.rs:361` both list it unsupported. **Nuance, correcting a coarser reading:**
LiteInst *does* have a substantial in-guest SIGSYS dispatcher in production code
(`reverie-liteinst/src/runtime.rs`, SIGSYS handling throughout; straddler handling in
`src/straddler.rs`). `runtime.rs:989-994` states LiteInst *"hosts its own SIGSYS dispatcher rather than
the shared `PassthroughDispatcher`"* while reusing the same reviewed `ForkHook`/`is_fork_like` seam as
e9patch. The machinery is **duplicated rather than shared**. Path classes `in_guest_sigsys` /
`in_guest_nested_sigsys` exist in `reverie-liteinst/src/stats.rs:111` but have no counterpart in the
host-hybrid's class list at `reverie-ptrace/src/liteinst_stats.rs:225` (7 classes vs 5) — those in-guest
classes are unreachable on the shipped path.

*Requires:* hoisting onto the shared `reverie-preload` seam, not writing a new fallback.

## Observability gap that blocks measuring this gate

The fastpath/slowpath ratio (`direct_hook` vs `ptrace_installation`) is the number that would quantify how
much of the syscall path is genuinely in-guest, and it is **unreachable from the hermit CLI**. Both
LiteInst dispatch sites return early — `hermit-cli/src/lib.rs:1566` and `:1678` — *before*
`backend_stats::request()` at `:1570` and `:1686`. The `_and_stats` variants (`backend.rs:240`, `:312`)
have no hermit caller.

Confirmed empirically: `hermit run --backend=liteinst --summary -- /bin/true` prints the activation line
and **no** instrumentation-stats line. Any future claim of "mostly in-guest" cannot currently be measured
through hermit.

## Prior anchors that had moved (re-derived; do not reuse the old ones)

| what | previously cited | current |
| --- | --- | --- |
| `read_clock` / `set_timer` | `tool_host.rs:887-903` | file is now 854 lines total; `:744-748`, `:750-753`, `:755-760` |
| CLI dispatch | `lib.rs:1531-1546` | `:1552-1567` (and `:1662-1666`) |
| ptrace counters | `liteinst_stats.rs:113-129` | `:117` and `:129` |

## Scope

Read-only throughout. No source file modified, no branch created, no commit in either product repo. No CLI
flag flipped in any config or source. No compat cell claimed and no perf number quoted as an improvement.
The only executions were the litmus runs above against an already-built binary; both primaries were left
untouched, each still on `main`.
