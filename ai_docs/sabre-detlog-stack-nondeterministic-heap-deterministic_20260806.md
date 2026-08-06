# SaBRe detlog: the STACK is nondeterministic, the HEAP is not

**Date:** 2026-08-06 · **Task:** `sabre-detlog-heap-stack-parity` · **Local only, no egress**
**Hermit:** `0.2.0 (2026-08-04, g0f891e432a75-dirty)`, debug build (release lacks the `sabre` feature)
**Status:** committed to the parent, **not pushed** (egress 403)

## Headline

`hermit run --backend sabre --detlog-stack` produces a **different hash sequence on every run**
of the same guest. ptrace, run the same way, is bit-stable. This is a determinism bug in the
SaBRe backend, not a parity difference — and `--verify` passes anyway, because it compares
stdout.

The heap is the opposite: **deterministic under both backends.** So the two axes named in this
task have genuinely different verdicts and should not be reported together.

| axis | ptrace | sabre | verdict |
|---|---|---|---|
| `--detlog-stack` hashes across runs | 3/3 **identical** | 3/3 **all different** | **DETERMINISM BUG (sabre)** |
| `--detlog-heap` hashes across runs | 2/2 identical | 2/2 identical | deterministic both |
| stack extent | `0x7ffffffdc000-0x7ffffffff000` (0x23000) | `0x7ffffffdb000-0x7ffffffff000` (0x24000) | parity gap: **one page larger** |
| heap base | `0x405000` | `0x555555571000` | parity gap: different load base |
| heap record count | 7 | 126 | parity gap: **18× sampling** |
| stack record count | 53 / 58 | 111 / 128 | parity gap: ~2.2× sampling |

## The determinism bug

Fixture: dynamically linked C, `main()` does `openat`/`read`/`close`/`write`.

```
ptrace run1 hashseq_md5=440adca94699   n=53
ptrace run2 hashseq_md5=440adca94699   n=53      <- bit-stable
ptrace run3 hashseq_md5=440adca94699   n=53

sabre  run1 hashseq_md5=333e313269ca   n=111
sabre  run2 hashseq_md5=2c1c5782c9ea   n=111     <- three distinct sequences
sabre  run3 hashseq_md5=9adefea7ef6d   n=111
```

The range and record count are stable; only the **contents** move. Diffing two runs
record-by-record: **111 of 111 differ, starting at record #1, zero identical.** So a single
varying region of the stack is being hashed on every sample — one poisoned input, 100% of the
hashes, every run.

`--verify` reports "Determinism verified" for sabre regardless, because the guest's stdout
(`hi`) is stable. This is the same blindness measured in the companion task: the verify/parity
instrument does not look at the detlog.

## Attribution: narrowed, not closed — three candidates REFUTED

Recording the refutations, because each one costs a probe to rule out:

1. **`AT_RANDOM` — REFUTED.** The 16 auxv bytes are the prime suspect (they live on the stack
   and are the canonical startup entropy). They are **identical across all runs and both
   backends**: `[162, 205, 24, 211, 0, 83, 122, 92, 176, 131, 220, 72, 219, 250, 14, 242]`.
   Correctly determinized; not the cause.
2. **Staged-program path with the PID — REFUTED for this fixture.**
   `stage_sabre_program_in` writes `/dev/shm/hermit-sabre-program-{std::process::id()}`
   (`hermit-cli/src/lib.rs:832`) and that path reaches the guest's **argv** via
   `command.prepend_args(...)`. argv sits on the initial stack and is **not** covered by any
   scrubbing. But staging is gated on `sabre_program_needs_neutral_name`, which only fires for
   filenames starting with `ld` (`lib.rs:816-820`), and my fixtures do not. **Latent bug
   nonetheless — see below.**
3. **The coordinator socket env var — REFUTED as a sufficient explanation.**
   `command.env(SABRE_RPC_SOCKET_ENV, &socket_path)` (`lib.rs:1039`) injects a path with a
   random `tempfile` suffix (observed: `hermit-sabre-rpc-YydPIr`, `-m5TL9z`, per run) into the
   guest environment. But `reverie-sabre`'s `take_private_env` **scrubs it in place** —
   `ptr::write_bytes(entry.cast::<u8>(), 0, bytes.len())` (`paths.rs:77`), with a dedicated
   test. The suffix is fixed-length, so a successful scrub yields constant bytes. If this were
   the only source, late records would converge; **all 111 differ**, so it cannot be the whole
   story.

**Remaining candidate and the decisive next probe:** something else on the stack varies for the
whole run. The probe that settles it is a byte-level diff of the guest stack between two sabre
runs (dump `[stack]` at a fixed syscall index under each run and `cmp` them) — that names the
offset, and the offset names the field. I did not have a stack-dump facility to hand and would
rather leave this attributed-to-a-region than guess a cause.

## Latent bug worth filing separately

`hermit-sabre-program-{PID}` in argv is a **real** nondeterminism source for any guest whose
filename begins with `ld` — precisely the dynamic-loader cases the neutral-name workaround
exists to serve. It did not fire for my fixtures, so it is untested here, but it is
nondeterministic by construction: the PID differs every run and the string lands unscrubbed on
the guest stack. There is already a `TODO-HUMAN-REVIEW(PR-845)` on that workaround.

## The heap result, stated precisely

Heap hashing is **deterministic under both backends** — ptrace 7 records stable across runs,
sabre 122 records stable across runs. The differences are parity, not determinism:

- **Base address**: ptrace `0x405000` vs sabre `0x555555571000` (standard PIE base). The hash is
  over contents, so this need not break content parity, but it means the two backends are not
  describing the same address space.
- **Sampling cadence**: 7 vs 126 records for identical guest work. Both grow in the same
  `0x20000` steps to the same `+0x71000` total, so the *allocation behaviour* agrees; SaBRe just
  samples ~18× more often.

So "detlog-heap parity" is currently a **cadence and addressing** problem, and is tractable.
"detlog-stack parity" is blocked behind a determinism bug and cannot be assessed until that is
fixed — you cannot compare a hash against ptrace when it does not agree with itself.

## Reproduction

```sh
export LD_LIBRARY_PATH=/home/newton/.local/hermit-deps/lu/usr/lib64   # libunwind-x86_64.so.8
H=hermit/target/debug/hermit                                         # release has no sabre
G=<fixture NOT under /tmp>                                           # guest /tmp is isolated
for i in 1 2 3; do
  $H --log=info --log-file=r$i.log run --backend sabre --detlog-stack -- $G >/dev/null 2>r$i.err
  cat r$i.log r$i.err | grep -o 'DETLOG \[memory\].*' | grep -oE '\->[0-9a-f]+' | md5sum
done   # sabre: three different digests; same loop with --backend ptrace: one digest
```

## Recommended order

1. **Settle the attribution** with the stack-byte diff above. One probe.
2. **Fix the stack nondeterminism.** Until then SaBRe has no meaningful detlog-stack parity
   number, and any ratchet against it would be measuring noise.
3. **Then** address heap cadence/addressing, which is comparatively mechanical.
4. **Separately**, fix or gate `hermit-sabre-program-{PID}` before any `ld*` guest is used for
   parity measurement.
