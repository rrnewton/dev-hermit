# e9patch corpus new-family ratchet — round 20

**Date:** 2026-08-01
**PR:** https://github.com/rrnewton/hermit/pull/1325 (draft, stacked on round-19)
**Hermit SHA:** 47dfabf6dafc267da07c48c809639dd88dc2e6e6
**Reverie SHA:** 2112c0045f25f895388257caed43b7b5abb9b50a

## Question

Do seven further non-gated syscall families with no existing corpus guest hold
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
e9loader prologue). Families were chosen to have no existing corpus guest
(audited via `git ls-files '*.c'`).

## Guests

| guest | syscall | printed | why host-independent | kept |
| --- | --- | --- | --- | --- |
| `fchown_memfd` | fchown(93) | `fchown=0` | no-op fchown(fd,-1,-1) success | yes |
| `munlockall_ok` | munlockall(152) | `munlockall=0` | always-succeeds return | yes |
| `setrlimit_nofile` | setrlimit(160) | `setrlimit=0` | no-op rewrite; limits not printed | yes |
| `sched_getaffinity_check` | sched_getaffinity(204) | `affinity=1` | boolean; byte count not printed | yes |
| `sched_rr_get_interval_check` | sched_rr_get_interval(148) | `rrinterval=0` | query return | yes |
| `setpgid_self` | setpgid(109) | `setpgid=0` | process-group set to own pid | yes |
| `poll_timeout_zero` | poll(7) | `poll=0` | non-blocking immediate return | yes |
| `sched_getattr_check` | sched_getattr(275) | `getattr=-38` | **DROPPED**: -ENOSYS under hermit | no |

## Result

`RATCHET e9patch: 159/159 PASS_L2` (152 prior + 7 new). One guest dropped for
no-false-parity (#152): `sched_getattr(275)` returns -ENOSYS (-38) under golden
hermit ptrace. Corpus 152 → 159. Inventory manifest 530 → 537 files
(guest-fixture 185 → 192); `audit-inventory` passes.

## Reproduction

```bash
cd worktrees/e9patch/hermit
export HERMIT_E9TOOL=$PWD/../reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=$PWD/../reverie/third-party/e9patch/e9patch
python3 -u tests/backend-parity/e9patch_corpus.py
```

`src/` holds the seven kept guest sources exactly as committed to the PR.
