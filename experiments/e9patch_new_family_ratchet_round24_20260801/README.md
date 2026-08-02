# e9patch corpus new-family ratchet — round 24

**Date:** 2026-08-01
**PR:** https://github.com/rrnewton/hermit/pull/1335 (draft, stacked on round-23 #1333)
**Hermit SHA:** f5a67c3eb5784e79e86e70f17a2a0ffc16d825ea
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
native-tested (rc=0) then run through `tests/backend-parity/e9patch_corpus.py`,
which executes every guest under the golden hermit ptrace backend at L2
(`--strict --verify`) and again under e9patch preprocessing, comparing
guest-visible output byte-for-byte (detlog tail must match modulo the 8-syscall
e9loader prologue). Families were chosen to have no existing corpus guest.

## Guests (kept)

| guest | syscall | printed | why host-independent |
| --- | --- | --- | --- |
| `mlockall_all` | mlockall(151) | `mlockall=0` | MCL_CURRENT + munlockall success |
| `faccessat2_devnull` | faccessat2(439) | `faccessat2=0` | /dev/null world-readable, success |
| `pidfd_send_signal_self` | pidfd_send_signal(424) | `pidfdsignal=0` | signal 0 permission check, no delivery |

## Dropped (no false parity, #152)

| guest | syscall | golden result |
| --- | --- | --- |
| `openat2_devnull` | openat2(437) | failure — unsupported under hermit |
| `shmget_rmid` | shmget(29) | failure — no usable System V IPC namespace |
| `semget_rmid` | semget(64) | failure — no usable System V IPC namespace |
| `msgget_rmid` | msgget(68) | failure — no usable System V IPC namespace |

## Result

`RATCHET e9patch: 181/181 PASS_L2` (178 prior + 3 new). Kept 3 of 7. Corpus
178 -> 181. Inventory manifest 556 -> 559 files (guest-fixture 211 -> 214);
`audit-inventory` passes.

## Reproduction

```bash
cd worktrees/e9patch/hermit
export HERMIT_E9TOOL=$PWD/../reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=$PWD/../reverie/third-party/e9patch/e9patch
python3 -u tests/backend-parity/e9patch_corpus.py
```

`src/` holds the three kept guest sources exactly as committed to the PR.
