# Patching-backend reach matrix: do the new guests exercise anything?

## Question

New inline-syscall guests were added because typical guests gave e9patch
`candidate_sites=0`, which made its "parity" a measurement of plain ptrace.
**"We added guests" is not "the backends now do work."** For each new guest ×
each patching backend, what does *that backend's own* reach counter say?

## The matrix — empty cells left visible

| guest | shape | **e9patch** `mapped_sites` | **sabre** site counter | **liteinst** |
|---|---|---:|---|---|
| libc baseline *(contrast)* | dynamic, libc | **0** — NOT-EXERCISED | *none exposed* | unmeasurable here |
| `inline_syscall_sites` | inline asm in main ELF | **5** — EXERCISED | *none exposed* | unmeasurable here |
| `static_nolibc_syscall_sites` | `-static -nostdlib`, no PLT | **6** — EXERCISED | **SIGSEGV** | unmeasurable here |
| `mixed_inline_and_libc_syscalls` | inline + libc interleaved | **1** — EXERCISED | *none exposed* | unmeasurable here |

## Three findings, and only the first is the good news

**1. e9patch is genuinely exercised now.** 0 → 5/6/1. The vacuity that motivated
the guests is closed *for e9patch*, and the contrast row proves the counter
discriminates rather than always reporting nonzero.

**2. SaBRe exposes NO site counter at all, so its cells are unfalsifiable.** Its
evidence schema (`HERMIT_SABRE_PATH_EVIDENCE`) carries exactly
`guest_rpc_observed`, `ptrace_fallback_sites`, `trusted_shared_object_sites`,
`trusted_shared_objects` — and **both site counters read 0 for every guest,
including the ones e9patch demonstrably rewrites.** `guest_rpc_observed` is
`true` for all three dynamic guests *and* for the plain-libc contrast guest, so
it does not discriminate exercised from not-exercised and cannot serve as the
reach signal. **There is currently no way to tell a real SaBRe measurement from
a silent ptrace fallback**, which is the same defect class the e9patch work
started from, one backend over. This matches the standing finding that
`patched_sites=0` yields a silent ptrace fallback with rc=0.

**3. The high-value static guest CRASHES SaBRe.** `-static -nostdlib` →
**rc=139 (SIGSEGV)**, reproduced twice. The one shape the task called high-value
for all three backends is the shape SaBRe cannot run at all. That is a real
SaBRe defect surfaced by the new corpus, not a guest bug: the same binary runs
correctly native, under ptrace, and under e9patch.

**liteinst is UNMEASURABLE on this host, not zero-reach.** Every guest —
*including the plain-libc contrast* — fails at
`verify LiteInst runtime activation failed … tracee terminated before the
required preload handshake completed (phase Waiting)`. Because the contrast
guest fails identically, this is an environment/build gap, not a statement about
guest shape. Recording it as "0 reach" would be wrong.

## Verified both ways

A nonzero cell scores as a real measurement **and** a zero cell reads
NOT-EXERCISED rather than parity: the libc contrast row is carried through the
whole matrix precisely so the e9patch column has a demonstrated zero next to its
nonzeros. Without that row, "5" would be a number with no scale.

## Reproduction

```
export HERMIT_E9TOOL=<repo>/target/install_pkg/rsrcs/e9tool
export HERMIT_INSTALL_DIR=<repo>/target/install_pkg
hermit run --backend e9patch --strict -- <guest>        # grep candidate_sites/mapped_sites
HERMIT_SABRE_PATH_EVIDENCE=ev.jsonl \
  hermit run --backend sabre --strict -- <guest>        # read ev.jsonl
hermit run --backend liteinst --strict -- <guest>
```

Guests are from hermit PR #1730. **They must live outside `/tmp`**: hermit
replaces guest `/tmp` with an isolated directory, and a guest under host `/tmp`
never runs — which silently measures 0 sites and looks like a reach failure.

## What this does not establish

e9patch reach is measured; it is not shown that a rewritten site changes any
*observable*, only that the rewriter mapped it. Whether patched execution then
agrees with ptrace is the parity question this unblocks, not one it answers.
SaBRe and liteinst columns are blocked on a counter and on the host respectively.
