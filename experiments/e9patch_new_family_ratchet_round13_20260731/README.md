# e9patch new-family ratchet — round 13 (2026-07-31)

## Question
Does e9patch binary-rewriting **preprocessing** (used with the **ptrace
backend**, not a Detcore backend) leave a new batch of non-gated syscall
families byte-identical to the golden ptrace run at L2 (bitwise-identical
repeat)?

## Families added
Path-based `statfs` on `/`, the legacy `pipe(2)` syscall, directory enumeration
(`getdents64`, reduced to a non-empty boolean), `signalfd4` registration (no
signal delivered or read), `close_range` fd teardown, socket-option get/set on
an `AF_UNIX` socketpair (`getsockopt SO_TYPE`, `setsockopt SO_REUSEADDR`), and
`fcntl F_GETLK` record-lock querying on a memfd.

## Method
Eight freestanding raw-syscall guests (`-nostdlib -static -ffreestanding -O0
-fno-pie -no-pie`), each with a single `sc()` SYSCALL site so e9tool actually
rewrites the main ELF. The harness (`tests/backend-parity/e9patch_corpus.py`)
runs each guest under golden ptrace and under e9patch-preprocessed ptrace and
checks: exit parity, stdout parity, exact pinned `expected_stdout`, golden L2,
e9patch L2, `candidate_sites>0`, `mapped==candidate`, `b0==0`, and a
guest-syscall DETLOG tail-match modulo the fixed 8-syscall e9loader prologue.

Every printed value is host-independent (a family constant, the lowest free
pipe fd = 3, a boolean, or the syscall return on success = 0), so the pinned
stdout is portable.

## Result
`RATCHET e9patch: 107/107 PASS_L2` (exit 0). Corpus 99 -> 107. No `-ENOSYS`
drop; the batch stayed at eight (no false-parity entries). See `results.csv`.

## Reproduction
```
export HERMIT_E9TOOL=<reverie>/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=<reverie>/third-party/e9patch/e9patch
cd hermit && python3 -u tests/backend-parity/e9patch_corpus.py
```
SHAs, PR link, and host in `metadata.json`.
