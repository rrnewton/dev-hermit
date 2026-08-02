# e9patch new-family ratchet — round 17 (2026-08-01)

## Question

Does **e9patch preprocessing + the ptrace backend** preserve byte-identical L2
parity for eight further non-gated syscall families not covered in rounds 1–16?

Families this round: three more `fcntl` ops (F_SETFD/F_GETFD `FD_CLOEXEC`
round-trip, `F_GETOWN`, `F_GETPIPE_SZ`), two working `ioctl`s (`FIONREAD`,
`FIONBIO`, beyond round-4's `TCGETS`/`ENOTTY`), the semaphore `eventfd`
flag path (`EFD_SEMAPHORE`, distinct from round-6's plain eventfd), the
`AF_UNIX`/`SOCK_DGRAM` socketpair (distinct from round-7's STREAM pair), and an
abstract-namespace `bind`.

## Method

Eight freestanding (`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`)
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

`RATCHET e9patch: 137/137 PASS_L2`. All eight new guests pass at L2:
guest-visible bitwise-identical output and detlog tail-match. See `results.csv`.

## Interpretation

e9patch preprocessing leaves the `fcntl`/`ioctl`/`eventfd2`/`socketpair`/`bind`
families byte-identical to golden ptrace. Every printed value is
host-independent by construction (syscall return, a boolean, a fixed count, or
fixed round-tripped text); host-specific values (pipe capacity, owner pid, fd
numbers) are read but never printed. None of the families touches scheduling,
time, or randomness. No guest was dropped this round — all eight passed golden
hermit ptrace on the first attempt, so there is no false-parity (#152) removal.
