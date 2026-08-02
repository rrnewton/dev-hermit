# e9patch corpus new-family ratchet — round 22

**Date:** 2026-08-01
**PR:** https://github.com/rrnewton/hermit/pull/1329 (draft, stacked on round-21)
**Hermit SHA:** 3c7934aa5f824a432f0b87d123e59952c5809ba2
**Reverie SHA:** 2112c0045f25f895388257caed43b7b5abb9b50a

## Question

Do six further non-gated syscall families with no existing corpus guest hold
byte-identical parity under **e9patch binary-rewriting preprocessing** with the
**golden hermit ptrace backend**? e9patch is preprocessing, not a Detcore
backend; ptrace is the golden reference.

## Method

Six freestanding, statically linked, raw-syscall C guests (one in-ELF SYSCALL
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
| `setresuid_noop` | setresuid(117) | `setresuid=0` | (-1,-1,-1) no-op success |
| `setresgid_noop` | setresgid(119) | `setresgid=0` | (-1,-1,-1) no-op success |
| `setreuid_noop` | setreuid(113) | `setreuid=0` | (-1,-1) no-op success |
| `setregid_noop` | setregid(114) | `setregid=0` | (-1,-1) no-op success |
| `accept4_abstract` | accept4(288) | `accept4=1` | boolean; fd number not printed |
| `ioprio_get_check` | ioprio_get(252) | `ioprio=1` | boolean; class/level not printed |

## Result

`RATCHET e9patch: 172/172 PASS_L2` (166 prior + 6 new). All six passed the
golden gate; **none dropped** (no false parity, #152). Corpus 166 → 172.
Inventory manifest 544 → 550 files (guest-fixture 199 → 205);
`audit-inventory` passes.

## Reproduction

```bash
cd worktrees/e9patch/hermit
export HERMIT_E9TOOL=$PWD/../reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=$PWD/../reverie/third-party/e9patch/e9patch
python3 -u tests/backend-parity/e9patch_corpus.py
```

`src/` holds the six kept guest sources exactly as committed to the PR.
