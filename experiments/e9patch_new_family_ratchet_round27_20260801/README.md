# e9patch new-family ratchet, round-27 (2026-08-01)

## Question

Do six syscall families with no existing corpus guest hold byte-identical
parity under e9patch **preprocessing** with the golden hermit **ptrace
backend** (e9patch is binary-rewriting preprocessing, not a backend)? The
families: legacy `accept(43)`, and the non-blocking readiness multiplexers
`select(23)`, `pselect6(270)`, `ppoll(271)`, `epoll_pwait(281)` (each with a
zero timeout), plus `getrusage(98)`.

## Method

Freestanding, statically linked, raw-`syscall` x86-64 guests (one in-ELF
`SYSCALL` site via a shared `sc()` helper; every guest ends in
`exit_group(231)`). Each guest is native-tested, then run through
`tests/backend-parity/e9patch_corpus.py`, which builds hermit `--features
e9patch`, preprocesses the guest with e9tool, and compares guest-visible
output and the detlog tail (modulo the 8-syscall e9loader prologue) against the
golden ptrace run at `--strict --verify` (L2). A guest that fails under golden
ptrace is dropped per no-false-parity (#152).

The four readiness multiplexers are all invoked with a **zero timeout** so they
return immediately without blocking or registering a timed waiter; `accept`
returns an already-queued abstract-socket connection; `getrusage` reads
accounting fields into a buffer that is never printed. Each guest prints only a
host-independent value (the syscall return 0 or a boolean valid-fd 1).

## Results

191/191 PASS_L2 (185 prior + 6 new). Kept all 6 candidates, zero drops:

- `accept_abstract` (43) -> `accept=1`, PASS_L2
- `select_timeout_zero` (23) -> `select=0`, PASS_L2
- `pselect6_timeout_zero` (270) -> `pselect=0`, PASS_L2
- `ppoll_timeout_zero` (271) -> `ppoll=0`, PASS_L2
- `epoll_pwait_timeout_zero` (281) -> `epollpwait=0`, PASS_L2
- `getrusage_self` (98) -> `getrusage=0`, PASS_L2

## Interpretation

All six are routine backend-parity coverage: they touch neither randomness nor
CPU scheduling, and the zero-timeout readiness polls register no timed waiter,
so they meet no `post-facto-human-review` trigger. `accept` is distinct from
round-22's `accept4`; `epoll_pwait` is the sigmask-carrying variant of the
round-25 `epoll_wait` guest; `select`/`pselect6`/`ppoll` are the readiness
multiplexers analogous to the round-20 `poll_timeout_zero`.

This is the first zero-drop round since round-22: the readiness-multiplexer
family is a rich vein of distinct, well-supported non-blocking syscalls,
temporarily reversing the field-thinning trend of rounds 23-26.

## Reproduction

```
cd ~/work/dev-hermit/worktrees/e9patch/hermit
git checkout codex/e9patch-corpus-round27-families   # @ 809b3e167bc919cc03bfda8193833b9b35873acb
export HERMIT_E9TOOL=$PWD/../reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=$PWD/../reverie/third-party/e9patch/e9patch
python3 tests/backend-parity/e9patch_corpus.py
bash ci/test_harness.sh audit-inventory
```
