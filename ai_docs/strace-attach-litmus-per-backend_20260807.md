# The strace-attach litmus, run per backend

**Measurement only.** No perf work, no product change, nothing optimized.

`ptrace` permits exactly one tracer per process. So if `strace` can attach to
hermit and hermit still runs its guest, no ptracer is in that guest's path. This
is positive and **unfakeable in the direction that matters**: a source audit, a
"we removed the ptrace calls" claim, or a clean-looking trace would all also be
satisfied by a silent fallback. This one would not — a fallback to ptrace makes
the run fail, loudly, because the tracer slot is already taken.

- hermit `e808322385d41015a77eb27f898ffb1f7c6cd220` (fresh `origin/main`), **release** binary
- `strace 6.12`, host devbig014
- Backends staged via `HERMIT_INSTALL_FORCE_RESTAGE` + release build, so `sabre`
  and `liteinst` are genuinely available rather than skipped

## The command

```bash
strace -f -o <log> ./target/release/hermit --log=off run \
    --backend <B> --strict --base-env=minimal -- /bin/echo LITMUS
```

`/bin/echo LITMUS` rather than `/bin/true` deliberately: an exit code alone does
not distinguish "the guest ran" from "hermit exited cleanly having run nothing".
The guest must produce an **observable effect** for the run to count as a pass.

## Results — 6 of 6 backends tested, denominator complete

| backend | family | litmus | hermit rc | guest ran | mechanism of failure |
|---|---|---|---|---|---|
| `ptrace` | ptrace-family | **FAIL** | 1 | no | `a child PTRACE_TRACEME probe was denied` |
| `e9patch` | ptrace-family | **FAIL** | 1 | no | *byte-identical* TRACEME denial |
| `sabre` | ptrace-family | **FAIL** | 1 | no | `failed to spawn ptraced SaBRe guest` → EPERM |
| `liteinst` | patching | **FAIL** | 1 | no | `failed to open pidfd for LiteInst tracee: ETIMEDOUT` |
| `dbi` | JIT | **PASS** | 0 | **LITMUS** | — (`Detcore Tool active`) |
| `kvm` | VM | **PASS** | 0 | **LITMUS** | — |

**All three patching backends named in the gate — sabre, e9patch, liteinst — fail
the litmus. None of them is ready for perf work.**

## Why each failure is what it says it is

**e9patch** produces a TRACEME denial byte-identical to plain `ptrace`'s, including
the same 129-line strace log length. That is not a coincidence of wording:
`Backend::E9patch` shares its match arm with `Ptrace` in
`hermit-cli/src/bin/hermit/run.rs` and never branches to a runner of its own.

**sabre** names the mechanism in its own error text — *"failed to spawn **ptraced**
SaBRe guest"*. And the source agrees rather than merely allowing the reading;
`run.rs:3033` says: *"The ptrace-family backends (ptrace, e9patch, sabre) already
force…"*. That is the codebase's own classification of sabre, not an inference
from a message.

**liteinst** is the one that needed care, because `ETIMEDOUT` is also what
strace-induced slowness would look like. It is not slowness:

```
under strace  run 1  rc=1  ETIMEDOUT
under strace  run 2  rc=1  ETIMEDOUT
under strace  run 3  rc=1  ETIMEDOUT
no strace     run 1  rc=0  LITMUS
no strace     run 2  rc=0  LITMUS
```

3/3 deterministic failure with strace attached, 2/2 clean success without. The
verdict is solid. The *mechanism* is stated more weakly than the other three:
LiteInst fails opening a pidfd for what it calls its "tracee", which is consistent
with a tracer conflict but is not an explicit TRACEME denial, so it is recorded as
strace-induced and deterministic rather than as a proven ptrace round trip.

## Why the two passes are real and not silent fallbacks

The failure mode this litmus exists to exclude is a backend that "passes" by
quietly doing nothing. Both passes were checked for an observable guest effect,
and both printed `LITMUS` through the pipe under strace.

One correction worth recording, because it nearly became a false caveat: I first
compared `execve` counts in the strace logs — DBI 84, KVM 1 — and suspected KVM of
no-op'ing. That was the wrong instrument. DBI's 84 execves are `cmake`/`ld` from a
client rebuild, not the guest; the guest's exec is not visible as
`execve("/bin/echo")` in either log. The observable-effect test settles it and
refutes the suspicion.

What the KVM pass does **not** establish: that KVM's Detcore path engaged. No
engagement witness appeared at `-l info`. The claim supported here is narrow and
exact — *KVM ran the guest with no ptracer in the path* — not that KVM achieved
determinism parity.

## Reproduction

```bash
cd <hermit checkout at e808322>
HERMIT_INSTALL_FORCE_RESTAGE=litmus cargo build --release --locked \
  -p hermit --features third-party-backends -p detcore-dbi -p detcore-sabre -p hermit-install
export HERMIT_E9TOOL=<path to e9tool>
for bk in ptrace e9patch sabre liteinst dbi kvm; do
  strace -f -o /tmp/$bk.strace ./target/release/hermit --log=off run \
      --backend $bk --strict --base-env=minimal -- /bin/echo LITMUS
done
```

## Limitations

One host, one guest (`/bin/echo`). `e9tool` came from a transient build tree, so
that binary is not durably pinned. The litmus tests whether a ptracer is in the
path; it says nothing about whether a backend is otherwise correct, and a PASS is
not a determinism claim.
