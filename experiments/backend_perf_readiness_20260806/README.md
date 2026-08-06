# Backend perf-readiness: is the ptracer out of the syscall hot path?

**Date:** 2026-08-06 · **Task:** `backend-perf-readiness-post-architecture` (#283 gate)
**Local only, no egress** · **Hermit:** release `g0f891e432a75-dirty` · **Host:** devbig014 (316 cores)
**Status:** committed to the parent, **not pushed** (egress 403)

## Readiness table

The #283 gate: *a patching backend is not perf-ready while a ptrace round-trip is in the syscall
path.* Per-backend verdict, measured:

| backend | intercepts 200/200 | guest `TracerPid` | µs / guest syscall | **gate** |
|---|---|---|---|---|
| `ptrace` | ✓ | 1 | **34.13** | FAIL *(reference — by definition)* |
| `e9patch` | ✓ | 1 | **34.31** | **FAIL** — indistinguishable from ptrace |
| `sabre` | ✓ | 1 | **39.88** | **FAIL** — 17% *worse* than ptrace |
| `dbi` | ✓ | 1 | **1.43** | **PASS** — 24× cheaper, at equal coverage |
| `liteinst` | — | — | — | **BLOCKED** — cannot activate (preload handshake) |
| `kvm` | — | — | — | **UNMEASURABLE** — hangs >400 s on a trivial guest |

**Only `dbi` passes.** Per the task's instruction, overheads are not decomposed for the backends
that fail.

## Why "is a ptracer attached" is the wrong question

The obvious probe is whether the guest is ptrace-attached. I ran it — a fixture reading its own
`/proc/self/status` — and it **does not discriminate**:

```
native    TracerPid: 0
ptrace    TracerPid: 1
sabre     TracerPid: 1
e9patch   TracerPid: 1
dbi       TracerPid: 1     <- the backend that PASSES
```

Every backend attaches a ptracer (PID 1 in hermit's PID namespace). Attachment is used for
process lifecycle and setup regardless of architecture. **Attachment ≠ a round-trip per
syscall**, and #283 is about the latter. So the gate has to be decided on the *cost signature*,
not on presence.

## The discriminator: marginal cost per guest syscall

A ptrace round-trip costs tens of microseconds; an in-guest handler costs well under one. That
is one to two orders of magnitude — big enough to read off directly.

Fixture: a C program issuing exactly N `getppid()` calls (cheap, non-elidable, one syscall per
iteration). Marginal cost = (T(50 000) − T(2 000)) / 48 000, median of 3 runs each, so
process startup cancels out and only the per-syscall term survives.

```
BACKEND    N=2k(med)  N=50k(med)   us/syscall
ptrace        0.0873      1.7257        34.13
e9patch       0.0842      1.7309        34.31
sabre         0.2132      2.1276        39.88
dbi           0.0657      0.1341         1.43
```

`e9patch` landing within 0.5% of `ptrace` is not a coincidence: its own CLI description is
"preprocess the main ELF with e9patch, **then use the ptrace runtime**". The measurement agrees
with the stated architecture. `liteinst` is likewise described as "the **ptrace-hosted** LiteInst
hybrid", though it cannot currently run to confirm it.

## The control that makes the `dbi` result mean something

A low per-syscall cost has two possible causes: the backend is efficient, or **it is not
intercepting anything**. That trap is real — a companion measurement this week found SaBRe
determinizing 4 of 49 syscalls while `--verify` reported success. So low cost was not credited
until coverage was verified equal:

```
ptrace    getppid=200/200   total_logged=250
e9patch   getppid=200/200   total_logged=250
dbi       getppid=200/200   total_logged=250
sabre     getppid=200/200   total_logged=244
```

All four intercept every one of the 200 syscalls. Coverage is equal, so the 24× spread is
purely architectural: **`dbi` handles syscalls in-guest via DynamoRIO's JIT, with the ptracer
attached but off the per-syscall path.**

One measurement artifact worth recording, because it nearly became a false finding: an earlier
pass reported `e9patch getppid=0/200`, which would have read as "intercepts nothing". The log
file had been written to `--log-file=/tmp/...`, and **hermit isolates guest `/tmp`**, so the
file never existed. Zero was the absence of a log, not the absence of interception. Re-run with
a non-`/tmp` path, e9patch intercepts 200/200 like the others.

## The two backends that could not be assessed

**`kvm` — unmeasurable, and this contradicts the task's prior.** The task notes "KVM may already
qualify". On this host it does not run at all: `hermit run --backend kvm` on a guest that only
reads `/proc/self/status` was killed at a **400 s timeout** having produced no output. It cannot
be credited with passing a gate it cannot be measured against. This matches the known
`kvm --strict` hang on this box, so it is plausibly host-specific rather than a product
regression — but it must be measured on a host where KVM runs before KVM is called perf-ready.

**`liteinst` — blocked upstream.** `verify LiteInst runtime activation failed … (phase Waiting)`.
Root-caused separately today: the guest dies in its preload constructor, narrowed to five
clauses in `validate_liteinst_handshake`. Until that clears, liteinst has no perf-readiness
number.

## Consequences

1. **`dbi` is the only perf-optimization candidate today.** It is the one backend where the
   ptracer is demonstrably off the per-syscall path at equal coverage.
2. **The in-guest convergence has not yet moved `sabre` or `e9patch` across the gate.** Both
   still pay a full supervisor round-trip per syscall. For `sabre` the round-trip is the
   coordinator RPC rather than ptrace itself — the same disqualifier in cost terms, and worth
   noting because "we removed ptrace" would not by itself constitute passing.
3. **State the gate as a cost predicate, not an attachment predicate.** `TracerPid != 0` holds
   for the passing backend too. A readiness check keyed on attachment would have failed `dbi`
   and told nobody anything.

## Reproduction

```sh
export LD_LIBRARY_PATH=/home/newton/.local/hermit-deps/lu/usr/lib64
H=hermit/target/release/hermit ; G=scratch/perfready/nsys      # N getppid() calls
# marginal cost (median of 3):
for be in ptrace dbi sabre e9patch; do
  for n in 2000 50000; do /usr/bin/time -f "$be $n %e" $H run --backend $be -- $G $n >/dev/null; done
done
# coverage control (log-file must NOT be under /tmp -- guest /tmp is isolated):
$H --log=info --log-file=$PWD/scratch/perfready/$be.log run --backend $be -- $G 200
```

## Limits

- One syscall shape (`getppid`, no arguments, no memory access). A backend could be cheap here
  and expensive on syscalls needing argument marshalling; the gate question is round-trip
  presence, which this isolates cleanly, but the *magnitude* is not a general overhead figure.
- Single host. `kvm`'s failure in particular is likely host-specific.
- Release build; the debug build's absolute numbers differ, though the ordering held in spot
  checks.
