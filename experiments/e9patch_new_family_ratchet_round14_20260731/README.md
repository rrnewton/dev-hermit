# e9patch new-family ratchet — round 14 (2026-07-31)

## Question
Does e9patch binary-rewriting **preprocessing** (used with the **ptrace
backend**, not a Detcore backend) leave a new batch of non-gated syscall
families byte-identical to the golden ptrace run at L2 (bitwise-identical
repeat)?

## Families added
A lone `AF_UNIX`/`SOCK_DGRAM` `socket(2)`, socket half-close (`shutdown`
`SHUT_RDWR`), robust-futex-list register/query (`set_robust_list`,
`get_robust_list`, registration only — no futex contended), `madvise`
`MADV_WILLNEED`, shared anonymous `mmap` (`MAP_SHARED` flag path), `prctl`
`PR_GET_KEEPCAPS`, and `arch_prctl` `ARCH_GET_FS`.

## Method
Eight freestanding raw-syscall guests (`-nostdlib -static -ffreestanding -O0
-fno-pie -no-pie`), each with a single `sc()` SYSCALL site so e9tool actually
rewrites the main ELF. The harness (`tests/backend-parity/e9patch_corpus.py`)
runs each guest under golden ptrace and under e9patch-preprocessed ptrace and
checks: exit parity, stdout parity, exact pinned `expected_stdout`, golden L2,
e9patch L2, `candidate_sites>0`, `mapped==candidate`, `b0==0`, and a
guest-syscall DETLOG tail-match modulo the fixed 8-syscall e9loader prologue.

Every printed value is host-independent (the lowest free fd = 3, a default flag,
or the syscall return on success = 0); host-specific addresses (the FS base and
robust-list head pointer) are read but never printed.

## Result
`RATCHET e9patch: 115/115 PASS_L2` (exit 0). Corpus 107 -> 115. No `-ENOSYS`
drop; the batch stayed at eight (no false-parity entries). See `results.csv`.

## Reproduction
```
export HERMIT_E9TOOL=<reverie>/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=<reverie>/third-party/e9patch/e9patch
cd hermit && python3 -u tests/backend-parity/e9patch_corpus.py
```
SHAs, PR link, and host in `metadata.json`.
