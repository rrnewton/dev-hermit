# e9patch corpus new-family ratchet — round 25

**Date:** 2026-08-01
**PR:** https://github.com/rrnewton/hermit/pull/1338 (draft, stacked on round-24 #1335)
**Hermit SHA:** 3fb7141d109345ece566d9e8f1df4e0dc734a5de
**Reverie SHA:** 2112c0045f25f895388257caed43b7b5abb9b50a

## Question

Do further non-gated syscall families with no existing corpus guest hold
byte-identical parity under **e9patch binary-rewriting preprocessing** with the
**golden hermit ptrace backend**? e9patch is preprocessing, not a Detcore
backend; ptrace is the golden reference.

## Method

Freestanding, statically linked, raw-syscall C guests (one in-ELF SYSCALL site
each via a shared `sc()` helper, terminated by `exit_group`), compiled
`gcc -nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`. Each was
native-tested (rc=0) then run through `tests/backend-parity/e9patch_corpus.py`
under the golden hermit ptrace backend at L2 (`--strict --verify`) and again
under e9patch preprocessing, comparing guest-visible output byte-for-byte
(detlog tail must match modulo the 8-syscall e9loader prologue).

## Guests (kept)

| guest | syscall | printed | why host-independent |
| --- | --- | --- | --- |
| `capset_noop` | capset(126) | `capset=0` | capget then capset identical sets, permitted no-op |
| `epoll_wait_timeout_zero` | epoll_wait(232) | `epollwait=0` | timeout 0 empty set, 0 ready |

## Dropped (no false parity, #152)

| guest | syscall | golden result |
| --- | --- | --- |
| `keyctl_keyring_id` | keyctl(250) | failure — no usable kernel keyring under hermit |

## Result

`RATCHET e9patch: 183/183 PASS_L2` (181 prior + 2 new). Kept 2 of 3. Corpus
181 -> 183. Inventory manifest 559 -> 561 files (guest-fixture 214 -> 216);
`audit-inventory` passes.

## Field-thinning note

The drop rate is climbing across rounds (r22 0/6, r23 1/7, r24 4/7, r25 1/3):
the readily-reachable supported non-gated syscall families are nearly exhausted.
Confirmed hermit-unsupported this session and not to be re-added: sched_getattr
(275), io_setup (206), openat2 (437), System V IPC shmget/semget/msgget
(29/64/68), keyctl (250). memfd `user.*` xattr write/get/remove fails natively
(tmpfs anon inode -> -EACCES/-ENODATA), so it cannot ship as a host-independent
success guest.

## Reproduction

```bash
cd worktrees/e9patch/hermit
export HERMIT_E9TOOL=$PWD/../reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=$PWD/../reverie/third-party/e9patch/e9patch
python3 -u tests/backend-parity/e9patch_corpus.py
```

`src/` holds the two kept guest sources exactly as committed to the PR.
