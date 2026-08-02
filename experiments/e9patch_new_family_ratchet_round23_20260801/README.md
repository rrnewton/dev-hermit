# e9patch corpus new-family ratchet — round 23

**Date:** 2026-08-01
**PR:** https://github.com/rrnewton/hermit/pull/1333 (draft, stacked on round-22 #1329)
**Hermit SHA:** 1cf614b007f8ea5a3e0a8ca0e4b3df67c8c123f1
**Reverie SHA:** 2112c0045f25f895388257caed43b7b5abb9b50a

## Question

Do six further non-gated syscall families with no existing corpus guest hold
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

## Guests

| guest | syscall | printed | why host-independent |
| --- | --- | --- | --- |
| `setpriority_self` | setpriority(141) | `setpriority=0` | PRIO_PROCESS no-op success |
| `getpriority_self` | getpriority(140) | `getpriority=1` | boolean; 20-nice value not printed |
| `get_mempolicy_default` | get_mempolicy(239) | `getmempolicy=0` | query success |
| `set_mempolicy_default` | set_mempolicy(238) | `setmempolicy=0` | MPOL_DEFAULT no-op success |
| `modify_ldt_read` | modify_ldt(154) | `modifyldt=0` | 0 bytes, no LDT entries; buffer not printed |
| `rt_sigqueueinfo_self` | rt_sigqueueinfo(129) | `sigqueueinfo=0` | signal 0 permission check, no delivery |

## Result

`RATCHET e9patch: 178/178 PASS_L2` (172 prior + 6 new). **1 dropped**
(no false parity, #152): `io_setup(206)`/`io_destroy(207)` — `io_setup`
returns `-ENOSYS` (-38) under golden hermit, so the asynchronous-IO family has
no guest. Corpus 172 -> 178. Inventory manifest 550 -> 556 files (guest-fixture
205 -> 211); `audit-inventory` passes.

## Reproduction

```bash
cd worktrees/e9patch/hermit
export HERMIT_E9TOOL=$PWD/../reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=$PWD/../reverie/third-party/e9patch/e9patch
python3 -u tests/backend-parity/e9patch_corpus.py
```

`src/` holds the six kept guest sources exactly as committed to the PR.
