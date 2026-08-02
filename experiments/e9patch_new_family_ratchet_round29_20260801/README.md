# e9patch corpus ratchet — round 29 (2026-08-01)

## Question

Round-29 of the standing e9patch backend-parity corpus ratchet. Can freestanding
raw-syscall x86-64 guests exercising the scheduling / credential / process-query
syscall surface reach L2 parity across **both** the golden ptrace backend and the
e9patch-rewritten ptrace path, in the non-time / non-gated lane?

## Method

Seven candidate guests were written as freestanding, statically-linked,
raw-syscall programs (`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`),
each issuing a single fixed syscall and printing only host-independent output
(the syscall's success return, or a "query succeeded" boolean).

Each candidate was: (1) native-compiled and run; (2) run under golden
hermit-ptrace at `--strict` then `--strict --verify` (L2); (3) run under the
e9patch-rewritten ptrace path at L2. Any candidate failing golden or native, or
returning `-ENOSYS`, is DROPPED (no false parity — hermit issue #152). Kept
guests are wired into the `CORPUS` dict of
`tests/backend-parity/e9patch_corpus.py` and registered in the inventory
manifest.

## Results

Corpus: **198 → 203** guests (+5 kept, 2 dropped, drop rate 5/7).

| guest | syscall | expected stdout | golden | e9patch |
|-------|---------|-----------------|--------|---------|
| `sched_yield_noop` | `sched_yield` (24) | `yield=0` | L2 | PASS_L2 c/1 m/1 b0/0 |
| `prctl_capbset_read` | `prctl` `PR_CAPBSET_READ` | `capbset=1` | L2 | PASS_L2 c/1 m/1 b0/0 |
| `prctl_thp_disable` | `prctl` `PR_GET_THP_DISABLE` | `thpdisable=1` | L2 | PASS_L2 c/1 m/1 b0/0 |
| `setfsuid_noop` | `setfsuid` (122) | `setfsuid=1` | L2 | PASS_L2 c/1 m/1 b0/0 |
| `setfsgid_noop` | `setfsgid` (123) | `setfsgid=1` | L2 | PASS_L2 c/1 m/1 b0/0 |

**Dropped** (returned `-ENOSYS` / -38 under golden hermit-ptrace, so no true
parity contract exists): `prctl PR_GET_NO_NEW_PRIVS` (39), `rseq` (334).

All kept e9 runs: `prologue=8 tail_match=yes` — the candidate SYSCALL site was
found and mapped with no SIGILL fallback, and the golden guest-syscall DETLOG
sequence is an exact suffix of the e9 sequence (removed prefix = the
deterministic 8-syscall e9loader prologue).

## Interpretation

The scheduling/credential/query surface behaves deterministically under both
backends, extending parity coverage without touching any time, PID, randomness,
filesystem, or namespace channel. The 5/7 drop rate confirms the remaining
un-probed non-gated syscall field continues to thin.

## Reproduction

```bash
cd ~/work/dev-hermit/worktrees/e9patch/hermit
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
python3 -c 'import sys; sys.path.insert(0,"tests/backend-parity"); import e9patch_corpus as c; c.main()' \
  sched_yield_noop prctl_capbset_read prctl_thp_disable setfsuid_noop setfsgid_noop
```

PR: https://github.com/rrnewton/hermit/pull/1364 (stacked on #1361).
