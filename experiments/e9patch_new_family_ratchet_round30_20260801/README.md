# e9patch corpus ratchet — round 30 (2026-08-01)

## Question

Round-30 of the standing e9patch backend-parity corpus ratchet. Can freestanding
raw-syscall x86-64 guests exercising a process-config query
(`prctl PR_GET_TIMERSLACK`), process CPU-tick accounting (`times`), and the
`getxattr`/`lgetxattr`/`fgetxattr` nonexistent-user-xattr error path reach L2
parity across **both** the golden ptrace backend and the e9patch-rewritten
ptrace path?

## Method

Eight candidate guests were written as freestanding, statically-linked,
raw-syscall programs (`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`).
Each was native-compiled and run, then run under golden hermit-ptrace at
`--strict` and `--strict --verify` (L2), then under the e9patch-rewritten
ptrace path at L2 through the corpus runner's full contract (exit + stdout
parity, golden L2, e9 L2, `candidate_sites>0 && mapped==candidate`, `b0==0`,
guest-syscall DETLOG tail-match). Any candidate returning a hermit-limitation
errno (`-ENOSYS`, or `-EOPNOTSUPP` where native succeeded) is DROPPED — no false
parity (hermit issue #152).

## Results

Corpus: **203 → 208** guests (+5 kept, 3 dropped, drop rate 3/8).

| guest | syscall | expected stdout | golden | e9patch |
|-------|---------|-----------------|--------|---------|
| `prctl_timerslack` | `prctl PR_GET_TIMERSLACK` | `timerslack=1` | L2 | PASS_L2 c/1 m/1 b0/0 |
| `times_check` | `times` (100) | parity-only (None) | L2 | PASS_L2 c/1 m/1 b0/0 |
| `getxattr_devnull` | `getxattr` (191) | `getxattr=1` | L2 | PASS_L2 c/1 m/1 b0/0 |
| `lgetxattr_devnull` | `lgetxattr` (192) | `lgetxattr=1` | L2 | PASS_L2 c/1 m/1 b0/0 |
| `fgetxattr_devnull` | `fgetxattr` (193) | `fgetxattr=1` | L2 | PASS_L2 c/1 m/1 b0/0 |

**Dropped** (encode a hermit limitation under golden hermit-ptrace, so no true
parity contract exists):
- `prctl PR_GET_FP_MODE` (46) → `-ENOSYS` (-38). Native returned `-EINVAL`
  (-22, the correct x86-64 rejection); hermit returns `-ENOSYS`.
- `name_to_handle_at` (303) → `-EOPNOTSUPP` (-95). Native succeeded.
- `seccomp` (317) → `-EOPNOTSUPP` (-95). Native succeeded.

All kept e9 runs: `prologue=8 tail_match=yes` — the candidate SYSCALL site was
found and mapped with no SIGILL fallback, and the golden guest-syscall DETLOG
sequence is an exact suffix of the e9 sequence (removed prefix = the
deterministic 8-syscall e9loader prologue).

## Interpretation

The config-query, process-time-accounting, and xattr error-path surfaces behave
deterministically under both backends. `times_check` is parity-only because its
return is host/timing dependent; all other kept guests print host- and
filesystem-independent values (booleans). The get/lget/fgetxattr guests assert a
faithful, portable Linux semantic (reading a nonexistent user xattr fails)
rather than any hermit-specific value. The 3/8 drop rate confirms continued
thinning of the reachable, hermit-supported non-gated syscall field.

## Reproduction

```bash
cd ~/work/dev-hermit/worktrees/e9patch/hermit
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
python3 -c 'import sys; sys.path.insert(0,"tests/backend-parity"); import e9patch_corpus as c; c.main()' \
  prctl_timerslack times_check getxattr_devnull lgetxattr_devnull fgetxattr_devnull
```

PR: https://github.com/rrnewton/hermit/pull/1368 (stacked on #1364).
