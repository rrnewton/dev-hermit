# e9patch corpus new-family ratchet — round 19

**Date:** 2026-08-01
**PR:** https://github.com/rrnewton/hermit/pull/1322 (draft, stacked on round-18)
**Hermit SHA:** 988d22837dd9d1354f47a5b13855de07e571364c
**Reverie SHA:** 2112c0045f25f895388257caed43b7b5abb9b50a

## Question

Do eight further non-gated syscall families with no existing corpus guest hold
byte-identical parity under **e9patch binary-rewriting preprocessing** with the
**golden hermit ptrace backend**? e9patch is preprocessing, not a Detcore
backend; ptrace is the golden reference.

## Method

Eight freestanding, statically linked, raw-syscall C guests (one in-ELF SYSCALL
site each via a shared `sc()` helper, terminated by `exit_group`), compiled
`gcc -nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`. Each was
native-tested (rc=0) then run through
`tests/backend-parity/e9patch_corpus.py`, which executes every guest under the
golden hermit ptrace backend at L2 (`--strict --verify`) and again under
e9patch preprocessing, comparing guest-visible output byte-for-byte (detlog tail
must match modulo the 8-syscall e9loader prologue).

Families were chosen to have **no existing corpus guest** (audited via
`git ls-files '*.c'`), avoiding both exact-name collisions and family
duplicates.

## Guests

| guest | syscall | printed value | why host-independent |
| --- | --- | --- | --- |
| `listen_abstract` | `listen(50)` | `listen=0` | success return; abstract-bound stream socket |
| `recvmsg_socketpair` | `recvmsg(47)` | `recvmsg=hi` | fixed round-tripped payload |
| `recvfrom_socketpair` | `recvfrom(45)` | `recvfrom=hi` | fixed round-tripped payload |
| `arch_prctl_getgs` | `arch_prctl(158)` GET_GS | `getgs=0` | GS base read into local, never printed |
| `prctl_pdeathsig` | `prctl(157)` GET_PDEATHSIG | `pdeathsig=0` | success return |
| `kill_self_sig0` | `kill(62)` sig 0 | `kill=0` | liveness probe, no signal delivered |
| `tgkill_self_sig0` | `tgkill(234)` sig 0 | `tgkill=0` | liveness probe, no signal delivered |
| `flistxattr_memfd` | `flistxattr(196)` | `flistxattr=0` | size query; list read but never printed |

## Result

`RATCHET e9patch: 152/152 PASS_L2` (144 prior + 8 new). All eight passed the
golden gate; **none dropped** (no false parity, #152). Corpus 144 → 152.
Inventory manifest 522 → 530 files (guest-fixture 177 → 185);
`audit-inventory` passes.

## Reproduction

```bash
cd worktrees/e9patch/hermit
export HERMIT_E9TOOL=$PWD/../reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=$PWD/../reverie/third-party/e9patch/e9patch
python3 -u tests/backend-parity/e9patch_corpus.py
```

`src/` holds the eight kept guest sources exactly as committed to the PR.
