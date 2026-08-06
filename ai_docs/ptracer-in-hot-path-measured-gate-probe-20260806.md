# The #283 gate: three instruments tried, three failed — and the one that works

**Task:** `verify-ptracer-out-of-path` · hermit-clone (opus-5), 2026-08-06
**Local, no egress.** hermit `b64d893a`, release binary built 2026-08-03, shared devbig box.

> **CORRECTION, 2026-08-06.** An earlier version of this document reported
> `native 0.102 / ptrace 10.126 / e9patch 10.126 / sabre 10.001` µs-per-syscall and concluded "the
> ptracer is in the hot path, 99× native". **Those numbers are retracted.** They were produced by
> `clock_gettime` *inside the guest*, and under `--strict` Detcore **virtualizes the clock** — so they
> are deterministic virtual time, not elapsed cost. They have been REPLACED below with external
> wall-clock measurements, which give the same qualitative verdict on a valid instrument.

## The retraction, and the tell

Repeated runs of the same probe:

```
ptrace    10.036  10.036  10.036
e9patch   10.036  10.036  10.036
sabre     10.001  10.001  10.001
native     0.096   0.093          <- the only one that varies
```

A real timing measurement does not repeat to three decimal places. A virtualized clock does. The
native control varies because it is not under Detcore.

So the "e9patch identical to ptrace to 3 d.p." agreement I offered as corroboration is **an artefact
of the virtual clock**, not evidence about the hot path. Also retracted on the same grounds: the 99×
figure, the "~10 µs hop on this box" claim, and my correction of the task's "~67 µs" figure.

The trapped-vs-untrapped table (`getpid` 4×, `write` 98×, `sched_yield` 2972×) came from the same
instrument, so the **multipliers are virtual-time ratios, not costs**. The qualitative split it shows
— some syscalls reach the tool, some do not — is probably real, but is not asserted here.

## Three instruments, three failure modes

1. **strace cannot measure a ptracer.** Counting `ptrace` calls gave "1" for every backend;
   `ptrace(PTRACE_TRACEME) = -1 EPERM` — strace already holds the tracer slot, so the run under
   measurement is not the run of interest.
2. **`getpid` measures nothing.** Detcore traps only a subset of syscalls via seccomp-BPF; an
   untrapped syscall reports any backend as fast.
3. **In-guest timing measures virtual time.** The subject of this document.

**You cannot time a deterministic-execution engine from inside the guest.** That is the durable
lesson: the engine's job is to make the clock a function of the program, so the clock cannot also be
a measure of the engine.

## The instrument that works — and the answer

External wall clock around the whole invocation, differenced across two workload sizes (N=2 000 vs
N=12 000) so fixed startup cancels:

```
marginal_µs = (T(N₂) − T(N₁)) / (N₂ − N₁)
```

Three independent pairs per backend, guest = `churn3` (in-image syscall site, `mapped_sites=1`):

| backend | marginal µs/syscall (3 pairs) | vs native |
|---|---|---|
| native | ~0.09 (in-guest clock valid — not under Detcore) | 1× |
| ptrace | **34.4, 33.9, 31.4** | ~370× |
| **e9patch** | **33.9, 34.3, 33.4** | ~375× — **indistinguishable from ptrace** |
| sabre | **156.7, 159.6, 155.5** | ~1740× — **~4.7× worse than ptrace** |

**The #283 gate is NOT met.** e9patch pays the full round-trip *even though it genuinely instrumented
the site* (`mapped_sites=1`) — which is the `run.rs:1924` downgrade shown empirically: the rewriting
happens, the ptrace hop remains. Spread is ±1.5 µs, so the separations are far outside noise.

**New finding: sabre is ~4.7× more expensive per syscall than plain ptrace** (~157 µs vs ~33 µs),
consistent with `sabre_ptrace.rs` taking the stop *and* the SaBRe machinery layered on top. Anyone
treating SaBRe as a lighter-weight path than ptrace should see this number first.

The hop here is ~33 µs against the task's "~67 µs" — same order, different box/binary; both should
carry their own provenance rather than one inheriting the other.

## What survives, because it does not depend on timing

**The reachability wall is now precisely characterised — and it is *not* "e9patch instruments
nothing".** e9patch rewrites the **main ELF only**. A probe whose syscall is issued from libc yields
`candidate_sites=0; mapped_sites=0`. Building `scratch/ptpath/churn3.c` with the syscall emitted as
**inline asm**, so the instruction lands in the probe's own `.text` (`objdump`: 1 syscall insn in the
main ELF), e9patch reports:

```
candidate_sites=1; mapped_sites=1; b0_sites=0
```

**It did instrument the site.** So the earlier "instruments nothing" result was a property of the
**corpus**, not of the backend. The two are now separated: reachability is a guest-*linkage*
property, and it is fixable by choosing guests with in-image syscall sites rather than by changing
e9patch.

**The static findings stand** (no timing involved): the `E9patch→Ptrace` downgrade is live at
`hermit-cli/src/bin/hermit/run.rs:1924`; hermit #1638 (`2fea6402f`) removes it and is **not** an
ancestor of hermit main; reverie #377 (`4a42194`) is **not** an ancestor of reverie main.

**TracerPid**, read by the guest from `/proc/self/status`: native `0`, every runnable backend `1`;
liteinst fails to start. The caveat stands — a **lifecycle-only** tracer legitimately keeps TracerPid
non-zero, so this is an **upper bound, not the gate**. Reading it as the gate would produce a false
fail on #1638.

## The reusable gate probe

```bash
gcc -O2 -o churn3 scratch/ptpath/churn3.c     # inline-asm syscall => in-image site
./churn3 20000                                 # native baseline (~0.09 us/syscall)
# marginal cost, EXTERNAL clock, two points so startup cancels:
for N in 2000 12000; do /usr/bin/time -f %e hermit --backend <b> run --strict -- $PWD/churn3 $N; done
```

`churn3` is the right guest precisely because its syscall is in-image and therefore
e9patch-reachable (`mapped_sites=1`); a libc-issued syscall would measure an uninstrumented backend.
**Never time from inside the guest** — Detcore virtualizes the clock.

**After the e9patch chain lands (reverie #377 → pin bumps → hermit #1638), e9patch must fall well
below ptrace's ~33 µs on this probe.** Staying at parity means the landing did not achieve the gate,
whatever the PR claims.

## Limits

- 3 pairs per backend, one host, one binary, two workload sizes assuming linearity. Enough for a
  ~370× architecture verdict; **not** a perf publication.
- ptrace and e9patch differ by less than the run-to-run spread, so the honest claim is
  **"indistinguishable"**, never "e9patch is slower".
- **liteinst not measurable** — fails to start ("preload handshake … phase Waiting").
- **dbi not measured** on this probe.

---

# Addendum: the first NON-VACUOUS e9patch parity cell, and the gap it exposes

With `churn3` (in-image syscall site ⇒ `mapped_sites=1`) an e9patch parity comparison is meaningful
for the first time — previously every cell compared ptrace with itself.

**Double-run self-verify (`--strict --verify`)** — all three pass:
`ptrace`, `e9patch` (**with `mapped_sites=1`**), `sabre` → *"Success: deterministic."*

**Cross-backend vs ptrace, same guest:**

| channel | result |
|---|---|
| stdout | **IDENTICAL** (45 bytes) — including the virtual-time-derived timing line |
| DETLOG | **DIFFERS** — ptrace 1120 lines vs e9patch 1132; 52 lines differ *after* address+bignum normalization |

## Root cause: e9patch's own loader syscalls are attributed to the guest

Syscalls present under e9patch and absent under ptrace:

```
readlink("/proc/self/exe")      open("<the guest binary>", 0)
mmap(..., PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED, fd=3, off=24576)   ×2
close(3)                        arch_prctl
```

That is **e9patch's rewritten-image loader**, executing through the *instrumented* path. The tell is
in the log itself: the `readlink` argument address is `0x20e9e9d61` — e9patch's own signature
mapping range — and it lands at `finish syscall #51`, i.e. during startup, before the workload loop.

Exact cost to parity:

```
ptrace   inbound=558  finish=557
e9patch  inbound=564  finish=563        <- exactly 6 extra syscalls attributed to the guest
```

Those 6 shift **every subsequent `finish syscall #N` index**, which is why 52 lines differ while the
guest's own behaviour is identical (stdout matches byte for byte).

**Consequence: e9patch cannot reach bitwise DETLOG parity with ptrace until its loader syscalls stop
being attributed to the guest.** This is a backend-attribution defect, not guest nondeterminism —
and it is invisible on any corpus where e9patch instruments nothing.

**Fix direction:** the mechanism already exists. `reverie-preload/src/seccomp.rs::for_trusted_gate`
keys on `SECCOMP_DATA_IP`, allowing designated gate IPs through untraced. e9patch's loader must
issue its syscalls through that gate (or complete before the tool is installed), exactly as the
preload host keeps its own syscalls out of the guest's event stream.

---

# Addendum 2: the instrument is now validated by a zero control

A negative result is worthless if the instrument cannot register a positive. Prior to this, nothing
showed the external-clock method *could* detect the absence of a per-syscall cost.

**Zero control** — identical probe, loop body replaced by pure arithmetic, **no syscall at all**
(`objdump`: 0 syscall instructions on the loop path), ptrace backend, three pairs:

```
marginal = 1.120 , -0.330 , 0.010  µs/iteration
```

Centred on zero, and **negative once** — i.e. pure noise. So:

1. **The instrument is valid**: it reports ~0 when there is no syscall to pay for.
2. **The noise floor is ≈ ±1 µs**, so the ~34 µs measured for a real syscall is ~30× the floor.
3. Therefore the ~34 µs **is** genuine per-syscall cost, not an instrument artefact or a fixed
   overhead that the differencing failed to cancel.

## A second retracted inference of mine

I had classified `getpid` as *untrapped* (from the virtual-time table) and used it as a would-be
positive control. Measured externally it costs **35.06 / 42.40 µs — the same as `write`**. So
`getpid` is trapped too, which is unsurprising in hindsight: Detcore must intercept it to virtualize
the pid. **The trapped/untrapped split was an artefact of the retracted virtual-time instrument, and
I carried it forward into a control it could not support.** The no-syscall loop is the control that
actually works, because it needs no assumption about which syscalls trap.

## Final verdict on the gate

| | marginal µs/syscall | instrument |
|---|---|---|
| zero control (no syscall) | **≈0 (±1)** | validates the method |
| native | ~0.09 | in-guest clock valid (not under Detcore) |
| ptrace | ~34 | external, 3 pairs |
| **e9patch** | **~34 — indistinguishable** | external, 3 pairs, `mapped_sites=1` |
| sabre | ~157 | external, 3 pairs |

**No landed patching backend removes the ptrace round-trip, and the measurement saying so is now
backed by a control proving it could have detected the opposite.** e9patch pays the full hop even
with the site genuinely rewritten — the `run.rs:1924` downgrade, shown empirically.

This is as far as the question can be taken locally. The gate changes only when the e9patch chain
lands (reverie #377 → pin bumps → hermit #1638); re-running this probe before then will reproduce
the same numbers.
