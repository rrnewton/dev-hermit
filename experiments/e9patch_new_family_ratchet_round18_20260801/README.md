# e9patch new-family ratchet — round 18 (2026-08-01)

## Question

Does **e9patch preprocessing + the ptrace backend** preserve byte-identical L2
parity for seven syscalls that had **no existing guest at all** in the corpus?

This round is different from rounds 1–17: rather than adding flag/path variants
of already-covered families, it audits the existing 137-guest corpus (which
already covers `umask`, `access`, `chdir`, `msync`, `fstat`, `statx`,
`prctl`-name, and `uname`) and targets syscalls entirely absent from it:
`sched_getparam(143)`, `sched_get_priority_min(147)`, a dedicated `ftruncate(77)`,
`sync_file_range(277)`, the `AF_UNIX`/`SOCK_SEQPACKET` socketpair,
`pidfd_open(434)`, and `sendmmsg(307)`.

## Method

Seven freestanding (`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`)
raw-syscall guests, each ending in `exit_group`, each with a single in-ELF
SYSCALL site (`candidate_sites=1`). Driven by
`tests/backend-parity/e9patch_corpus.py`, which runs each guest under golden
hermit ptrace and under a hermit built `--features e9patch` with `e9tool`
preprocessing, then compares exit code, stdout, and detlog (tail-match modulo
the fixed 8-syscall e9loader prologue). `src/` holds copies of the kept guests.

Reproduce:

    export HERMIT_E9TOOL=.../worktrees/e9patch/reverie/third-party/e9patch/e9tool
    export HERMIT_E9PATCH_BACKEND=.../worktrees/e9patch/reverie/third-party/e9patch/e9patch
    python3 -u tests/backend-parity/e9patch_corpus.py

## Results

`RATCHET e9patch: 144/144 PASS_L2`. All seven kept guests pass at L2 (see
`results.csv`).

One candidate was **dropped** under the no-false-parity rule (#152):
`prctl PR_SET_NO_NEW_PRIVS/PR_GET_NO_NEW_PRIVS` — `PR_SET_NO_NEW_PRIVS` returns
`-ENOSYS` (-38) under golden hermit ptrace, so it would encode a hermit
limitation rather than parity. The batch kept seven of eight.

## Interpretation

e9patch preprocessing leaves the `sched_getparam`/`sched_get_priority_min`/
`ftruncate`/`sync_file_range`/`SOCK_SEQPACKET`-socketpair/`pidfd_open`/`sendmmsg`
families byte-identical to golden ptrace. Every printed value is host-independent
by construction (syscall return, a fixed scheduler constant, a boolean, a fixed
message count, or fixed round-tripped text); the `pidfd_open` fd number is read
but never printed. The two `sched_*` guests are pure queries that read but never
change scheduling, so none of these is a DetCore/Reverie trigger.
