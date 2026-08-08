# e9patch preprocessing + ptrace vs golden plain-ptrace corpus parity

**Date:** 2026-07-31
**Task:** `e9patch-compat-ratchet-post-demo5`
**Host:** devbig014, kernel 6.18.39, GCC 11.5.0, e9tool 1.0.1

## Question

What is the current e9patch-preprocessing corpus parity against the golden
ptrace backend, at what assurance level, and where is the honest ceiling?
e9patch is binary-rewriting **preprocessing for the ptrace backend**, not a
standalone Detcore backend: e9tool rewrites the guest ELF ahead of time to
pre-trap its `SYSCALL` sites, then Detcore runs the rewritten image under ptrace.

## Method

e9tool rewrites only the **main** executable, so dynamically linked libc guests
expose zero in-ELF `SYSCALL` sites (`candidate_sites=0`) and e9patch is a no-op
on them (empirically confirmed on `/bin/echo`, `/bin/true`, `/bin/cat`:
`candidate_sites=0; mapped_sites=0; artifact_sha256=none`). The corpus is
therefore **freestanding, statically linked, raw-`syscall`** guests
(`-nostdlib -static -ffreestanding`, `exit_group`) so `candidate_sites > 0` and
the rewrite path is actually exercised.

`src/gen_corpus.sh` emits 12 guests; `src/sweep.sh` compares golden plain-ptrace
against e9patch preprocessing + ptrace on exit status, stdout, golden L2, e9patch
L2, and the e9patch patch metrics (candidate/mapped/b0). `src/detlog_parity.sh`
adds the finer guest-syscall DETLOG check (see below).

- golden plain : `hermit run --strict -- <guest>`
- golden L2    : `hermit run --strict --verify -- <guest>`
- e9patch plain: `hermit --backend e9patch run --strict -- <guest>`
- e9patch L2   : `hermit --backend e9patch run --strict --verify -- <guest>`
- detlog       : `hermit --log=info run --strict [...] -- <guest>` (plain; the
  guest-syscall subsequence is the ordered `inbound syscall:` lines with
  timestamps and addresses normalized)

stdout is captured from the **plain** run because `--verify` diverts guest stdout
into a temp file for its own log comparison.

## Results

`results/results.csv` — **12/12 PASS_L2**. Every guest: exit parity, stdout
parity, golden L2 verified, e9patch L2 verified, `mapped_sites == candidate_sites`
(full direct-AOT coverage), `b0_sites == 0` (no SIGILL signal fallback).

`results/detlog_parity.csv` — **12/12 TAIL_MATCH**. The golden guest-syscall
DETLOG sequence is exactly the suffix of the e9patch sequence. The removed
prefix is the deterministic **e9loader prologue** (`results/observed_e9loader_prologue.txt`):

```
readlink(/proc/self/exe) → open(self) → arch_prctl(GET_FS) →
mmap(RW anon TLS) → arch_prctl(SET_FS) → N × mmap(RX trampoline) → close
```

The prologue is 8 syscalls for single-region guests and 10 for `multi_site`
(3 patch sites → 4 trampoline `mmap`s instead of 2); it scales deterministically
with patch-site count. The guest's own syscalls follow byte-identically.

## Interpretation

The achievable and enforced e9patch-preprocessing parity is:

1. guest-visible parity (exit status + stdout) with golden ptrace,
2. L2 internal determinism (bitwise repeat) on both backends,
3. guest-syscall DETLOG identity **modulo the deterministic e9loader prologue**
   (tail-match), and
4. full direct-AOT coverage with no signal fallback (`mapped==candidate`, `b0=0`).

**Byte-identical DETLOG identity to plain ptrace is impossible by construction**
because the e9loader prologue prepends ~8 deterministic startup syscalls. That is
an architectural property of AOT rewriting, not a fixable defect; this artifact
makes **no** strict-detlog-identity claim (#152).

On this freestanding corpus the guest-visible + L2 + tail-match bar is
**saturated** — there is no failing batch to fix at that layer. The B-level
(#57): `b0_sites=0` and `mapped==candidate` on all guests is full direct-AOT
patch coverage with zero signal-fallback sites.

## Provenance

- Hermit: `0ca0dec256fd484e238b475a031a5c2d482eeba8` (origin/main), built
  `--features e9patch`; run from branch `codex/e9patch-ptrace-corpus-parity-ratchet`.
- Reverie: `2112c0045f25f895388257caed43b7b5abb9b50a`
  (`third-party/e9patch/{e9tool,e9patch}` vendored, e9tool 1.0.1).

## Reproduction

```bash
bash src/gen_corpus.sh src
HB=<hermit --features e9patch> \
E9DIR=<reverie>/third-party/e9patch \
  bash src/sweep.sh src results
E9DIR=<...> bash src/detlog_parity.sh src results
```

The same corpus and checks are institutionalized in the Hermit repo at
`tests/backend-parity/e9patch_corpus/` + `tests/backend-parity/e9patch_corpus.py`.
