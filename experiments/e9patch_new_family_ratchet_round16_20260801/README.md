# e9patch new-family ratchet — round 16 (2026-08-01)

## Question
Does e9patch binary-rewriting **preprocessing** (used with the **ptrace
backend**, not a Detcore backend) leave a new batch of non-gated syscall
families byte-identical to the golden ptrace run at L2 (bitwise-identical
repeat)?

## Families added
Randomness (`getrandom`, printing the byte count filled — not the random
bytes), the `gettid` thread-id query, the **legacy** `getrlimit(97)`
resource-limit query (distinct from round-8's `prlimit64`), clear-child-tid
registration (`set_tid_address`), two scheduler **queries** that read but never
change scheduling (`sched_get_priority_max(SCHED_OTHER)` and
`sched_getscheduler(0)`, both the fixed constant 0 for the default policy), and
the no-argument `sync(2)` (distinct from round-15's fd-scoped `syncfs`).

## Dropped
`prctl` `PR_GET_CHILD_SUBREAPER` returns `-ENOSYS` (-38) under the golden hermit
ptrace backend, so keeping it would encode a hermit limitation as parity.
Dropped per the no-false-parity rule (#152); the batch kept seven of eight.

## Method
Seven freestanding raw-syscall guests (`-nostdlib -static -ffreestanding -O0
-fno-pie -no-pie`), each with a single `sc()` SYSCALL site so e9tool actually
rewrites the main ELF. The harness (`tests/backend-parity/e9patch_corpus.py`)
runs each guest under golden ptrace and under e9patch-preprocessed ptrace and
checks: exit parity, stdout parity, exact pinned `expected_stdout`, golden L2,
e9patch L2, `candidate_sites>0`, `mapped==candidate`, `b0==0`, and a
guest-syscall DETLOG tail-match modulo the fixed 8-syscall e9loader prologue.

Every printed value is host-independent (the syscall return on success = 0, a
boolean, a fixed byte count, or a fixed scheduler constant); the random bytes,
tid values, and rlimit fields are read but never printed. The scheduler guests
only query policy and range — they do not alter scheduling.

## Result
`RATCHET e9patch: 129/129 PASS_L2` (exit 0). Corpus 122 -> 129. One `-ENOSYS`
drop (`prctl PR_GET_CHILD_SUBREAPER`); seven kept, no false-parity entries. See
`results.csv`.

## Reproduction
```
export HERMIT_E9TOOL=<reverie>/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=<reverie>/third-party/e9patch/e9patch
cd hermit && python3 -u tests/backend-parity/e9patch_corpus.py
```
SHAs, PR link, and host in `metadata.json`.
