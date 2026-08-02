# e9patch corpus ratchet — round 32 (inert fd/timer/sleep probes)

## Question

Round 32 of the standing e9patch corpus ratchet. Can freestanding raw-syscall
x86-64 guests for a direct `memfd_create` fd probe, an unarmed
`getitimer(ITIMER_REAL)` query, and a 1ms relative `clock_nanosleep` on
`CLOCK_MONOTONIC` reach L2 parity across the golden ptrace backend and the
e9patch-rewritten ptrace path? And which zero-copy / cross-address-space
data-movement syscalls does golden hermit not yet support?

## Method

Each candidate is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) printing only
host-independent values. Each was native-tested, then golden-hermit-ptrace
L2-tested (`--strict --verify`, "Determinism verified"), then e9patch L2-tested
(candidate_sites>0, mapped==candidate, no SIGILL fallback `b0==0`, DETLOG
tail-match with the deterministic 8-syscall e9loader prologue removed). A
candidate is KEPT only if native, golden, and e9 all pass and agree; any guest
failing native OR golden is DROPPED (no false parity, hermit issue #152).

**Environment note.** The fleet PMU was heavily contended during vetting by ~31
concurrent kvm/sabre `--strict --verify` processes — many of them leaked/wedged
supervisors alive over a day (`Sl`, 0% CPU) plus ~6 livelocked spinners (`Rl`,
131% CPU, 1.5 days CPU time). Under that load, L2 verify runs intermittently
wedged (PMU-skid). Vetting therefore ran each hermit invocation with a short
timeout and killed the whole process group (`killpg`, SIGKILL) on a wedge, then
retried, to catch clean PMU windows. Non-verify `--strict` runs were unaffected.

## Kept (3)

| guest | syscall | assertion | stdout |
|-------|---------|-----------|--------|
| memfd_create_check | memfd_create(319) | valid fd → boolean 1 | `memfd=1` |
| getitimer_real | getitimer(36) | unarmed ITIMER_REAL → 0 | `getitimer=0` |
| clock_nanosleep_relative | clock_nanosleep(230) | 1ms relative CLOCK_MONOTONIC → 0 | `clocknanosleep=0` |

`memfd_create_check` prints a boolean valid-fd because the fd number itself is
host-dependent. `getitimer_real` reads an unarmed interval timer (zeroed
itimerval, return 0). `clock_nanosleep_relative` sleeps on a virtualized clock
so its wakeup/return are deterministic — the same class as the already-landed
`nanosleep` guest.

## Dropped (5) — hermit limitations

| guest | syscall | golden | native | reason |
|-------|---------|--------|--------|--------|
| splice_pipe | splice(275) | -38 (-ENOSYS) | 5 | hermit does not support splice |
| tee_pipe | tee(276) | -38 (-ENOSYS) | 5 | hermit does not support tee |
| vmsplice_pipe | vmsplice(278) | -38 (-ENOSYS) | 5 | hermit does not support vmsplice |
| copy_file_range_memfd | copy_file_range(326) | -38 (-ENOSYS) | 5 | hermit does not support copy_file_range |
| process_vm_readv_self | process_vm_readv(310) | -1 (-EPERM) | 5 | ptrace supervision blocks self-target process_vm_readv |

Golden and e9patch agreed on the divergent value for all five (so this is not an
e9-specific regression), but asserting `-ENOSYS`/`-EPERM` as expected output
would encode a hermit gap as faithful Linux behavior. These five are a durable
record of hermit's current zero-copy / cross-address-space data-movement gaps.

## Results

- golden ptrace: 3/3 L2verified=1; native==golden and expected stdout matched.
- e9patch: 3/3 PASS_L2 exit=0, sites c/1 m/1 b0/0, prologue=0, tail_match=yes.
- audit-inventory: exit 0 (595 files, 250 guest fixtures).
- corpus size: 214 → 217.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
