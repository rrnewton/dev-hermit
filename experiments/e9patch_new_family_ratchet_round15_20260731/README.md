# e9patch new-family ratchet — round 15 (2026-07-31)

## Question
Does e9patch binary-rewriting **preprocessing** (used with the **ptrace
backend**, not a Detcore backend) leave a new batch of non-gated syscall
families byte-identical to the golden ptrace run at L2 (bitwise-identical
repeat)?

## Families added
memfd filesystem flush (`syncfs`), process-wide memory-ordering barrier
(`membarrier` `MEMBARRIER_CMD_GLOBAL`), the `getcpu` query, the **legacy**
`getdents(78)` directory enumeration (distinct syscall number from round-13's
`getdents64`), the execution-domain persona query (`personality`), an advisory
write-lock set/release via `fcntl` `F_SETLK` (distinct from round-13's `F_GETLK`
query), and inotify watch removal (`inotify_rm_watch`, distinct from round-12's
add-only guest).

## Dropped
`process_vm_readv` from self returns `-1` under the golden hermit ptrace backend
(the self/tracer read is not supported), so keeping it would encode a hermit
limitation as parity. Dropped per the no-false-parity rule (#152); the batch
kept seven of eight.

## Method
Seven freestanding raw-syscall guests (`-nostdlib -static -ffreestanding -O0
-fno-pie -no-pie`), each with a single `sc()` SYSCALL site so e9tool actually
rewrites the main ELF. The harness (`tests/backend-parity/e9patch_corpus.py`)
runs each guest under golden ptrace and under e9patch-preprocessed ptrace and
checks: exit parity, stdout parity, exact pinned `expected_stdout`, golden L2,
e9patch L2, `candidate_sites>0`, `mapped==candidate`, `b0==0`, and a
guest-syscall DETLOG tail-match modulo the fixed 8-syscall e9loader prologue.

Every printed value is host-independent (the syscall return on success = 0, or a
boolean); host-specific outputs (the getcpu cpu/node, the personality persona
value) are read but never printed.

## Result
`RATCHET e9patch: 122/122 PASS_L2` (exit 0). Corpus 115 -> 122. One `-ENOSYS`-
class drop (`process_vm_readv`); seven kept, no false-parity entries. See
`results.csv`.

## Reproduction
```
export HERMIT_E9TOOL=<reverie>/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=<reverie>/third-party/e9patch/e9patch
cd hermit && python3 -u tests/backend-parity/e9patch_corpus.py
```
SHAs, PR link, and host in `metadata.json`.
