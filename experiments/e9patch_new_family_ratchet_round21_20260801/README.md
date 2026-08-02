# e9patch corpus new-family ratchet — round 21

**Date:** 2026-08-01
**PR:** https://github.com/rrnewton/hermit/pull/1327 (draft, stacked on round-20)
**Hermit SHA:** db1cc45cb76e37df00ff6bd2d1b48b7e4509207a
**Reverie SHA:** 2112c0045f25f895388257caed43b7b5abb9b50a

## Question

Do seven further non-gated syscall families with no existing corpus guest hold
byte-identical parity under **e9patch binary-rewriting preprocessing** with the
**golden hermit ptrace backend**? e9patch is preprocessing, not a Detcore
backend; ptrace is the golden reference.

## Method

Seven freestanding, statically linked, raw-syscall C guests (one in-ELF SYSCALL
site each via a shared `sc()` helper, terminated by `exit_group`), compiled
`gcc -nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`. Each was
native-tested (rc=0) then run through `tests/backend-parity/e9patch_corpus.py`,
which executes every guest under the golden hermit ptrace backend at L2
(`--strict --verify`) and again under e9patch preprocessing, comparing
guest-visible output byte-for-byte (detlog tail must match modulo the 8-syscall
e9loader prologue). Families were chosen to have no existing corpus guest.

## Guests

| guest | syscall | printed | why host-independent |
| --- | --- | --- | --- |
| `munlock_page` | munlock(11) | `munlock=0` | success return after mlock |
| `connect_abstract` | connect(42) | `connect=0` | connect to listening abstract socket |
| `recvmmsg_socketpair` | recvmmsg(299) | `recvmmsg=hi` | fixed round-tripped payload |
| `preadv2_memfd` | preadv2(327) | `preadv2=hi` | fixed round-tripped payload |
| `pwritev2_memfd` | pwritev2(328) | `pwritev2=hi` | fixed round-tripped payload |
| `prctl_cap_ambient` | prctl(157) PR_CAP_AMBIENT | `capambient=0` | boolean, not in ambient set |
| `pidfd_getfd_self` | pidfd_getfd(438) | `pidfdgetfd=1` | boolean; fd number not printed |

## Result

`RATCHET e9patch: 166/166 PASS_L2` (159 prior + 7 new). All seven passed the
golden gate; **none dropped** (no false parity, #152). Corpus 159 → 166.
Inventory manifest 537 → 544 files (guest-fixture 192 → 199);
`audit-inventory` passes.

## Reproduction

```bash
cd worktrees/e9patch/hermit
export HERMIT_E9TOOL=$PWD/../reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=$PWD/../reverie/third-party/e9patch/e9patch
python3 -u tests/backend-parity/e9patch_corpus.py
```

`src/` holds the seven kept guest sources exactly as committed to the PR.
