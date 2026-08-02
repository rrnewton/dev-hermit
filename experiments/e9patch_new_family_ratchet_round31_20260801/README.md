# e9patch corpus ratchet — round 31 (new syscall families)

## Question

Round 31 of the standing e9patch corpus ratchet. Can six additional raw-syscall
freestanding x86-64 guests reach L2 parity across the golden ptrace backend and
the e9patch-rewritten ptrace path, extending coverage into the xattr-remove
family, an unarmed timerfd fd probe, a clock-resolution query, and a
non-blocking signal poll?

## Method

Each candidate guest is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) that prints only
host-independent values. Each was:

1. compiled and native-tested (expected exit + stdout),
2. golden-hermit-ptrace-tested at L2 (`--strict --verify`, witness
   "Determinism verified"), then
3. e9patch-tested at L2 (candidate_sites>0, mapped==candidate, no SIGILL
   fallback b0==0, guest-syscall DETLOG tail-match with the deterministic
   8-syscall e9loader prologue removed).

A candidate is KEPT only if native, golden, and e9 all pass and agree. Any
guest failing native OR golden is DROPPED (no false parity, hermit issue #152).

## Kept (6)

| guest | syscall | assertion | stdout |
|-------|---------|-----------|--------|
| removexattr_devnull | removexattr(197) | error on nonexistent xattr → boolean 1 | `removexattr=1` |
| lremovexattr_devnull | lremovexattr(198) | error on nonexistent xattr → boolean 1 | `lremovexattr=1` |
| fremovexattr_devnull | fremovexattr(199) | error on nonexistent xattr → boolean 1 | `fremovexattr=1` |
| timerfd_create_check | timerfd_create(283) | valid fd → boolean 1 | `timerfd=1` |
| clock_getres_monotonic | clock_getres(229) | resolution query returns 0 | `clockres=0` |
| rt_sigtimedwait_empty | rt_sigtimedwait(128) | empty set + {0,0} → -EAGAIN | `sigtimedwait=-11` |

The xattr-remove guests reuse the round-30/31 error-path boolean pattern:
collapse a host/filesystem-varying errno into a stable host-independent
`(r<0?1:0)` assertion. `timerfd_create_check` never arms the fd (no
`timerfd_settime`), so it registers no timed waiter and stays a pure
fd-allocation probe. `rt_sigtimedwait_empty` uses a {0,0} timeout so it returns
immediately without blocking or registering a timed waiter.

## Dropped (1)

| guest | syscall | reason |
|-------|---------|--------|
| kcmp_self_file | kcmp(312) | native returns 0 (fds equal); golden hermit-ptrace returns -1. pid virtualization breaks the kernel's pid-based comparison — a hermit limitation, not e9-specific. |

## Results

- golden ptrace: 6/6 L2verified=1; native==golden and expected stdout matched.
- e9patch: 6/6 PASS_L2 exit=0, sites c/1 m/1 b0/0, prologue=8, tail_match=yes.
- audit-inventory: exit 0.
- corpus size: 208 → 214.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
