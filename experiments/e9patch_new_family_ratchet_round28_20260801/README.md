# e9patch preprocessing parity ratchet — round 28

## Question

Which further legacy/variant Linux syscalls with no existing corpus guest hold
at e9patch-preprocessing L2 parity against the golden ptrace backend?

## Method

e9patch is binary-rewriting **preprocessing** for the ptrace backend (e9tool
rewrites the guest ELF's `SYSCALL` sites ahead of time; Detcore then runs the
rewritten image under ptrace). The corpus is freestanding, statically linked,
raw-`syscall` x86-64 guests whose `SYSCALL` sites live in the main ELF so the
rewrite path is actually exercised (`candidate_sites > 0`).

Round 28 targets legacy/variant syscall numbers with no existing guest, each
distinct from a covered newer counterpart. Every candidate was native-tested,
then validated under golden hermit ptrace (`run --strict`) BEFORE inclusion — no
guest ships that fails golden or native (no false parity, hermit issue #152).

## New guests (7, zero drops)

| guest | syscall | distinct from | prints |
| --- | --- | --- | --- |
| `epoll_create_legacy` | `epoll_create(213)` | `epoll_create1(291)` | `epollcreate=1` |
| `eventfd_legacy` | `eventfd(284)` | `eventfd2(290)` | `eventfdlegacy=5` |
| `inotify_init_legacy` | `inotify_init(253)` | `inotify_init1(294)` | `inotifyinit=1` |
| `signalfd_legacy` | `signalfd(282)` | `signalfd4(289)` | `signalfdlegacy=1` |
| `mbind_default` | `mbind(237)` MPOL_DEFAULT | get/set_mempolicy | `mbind=0` |
| `ioprio_set_self` | `ioprio_set(251)` | `ioprio_get(252)` | `ioprioset=0` |
| `epoll_pwait2_timeout_zero` | `epoll_pwait2(441)` | `epoll_pwait(281)` | `epollpwait2=0` |

Every printed value is host-independent (boolean valid-fd, a fixed round-tripped
counter, or the syscall return on success). None changes CPU scheduling, virtual
time, or randomness.

## Results

```
python3 tests/backend-parity/e9patch_corpus.py --require-backend
  => RATCHET e9patch: 198/198 PASS_L2
```

Each new guest: `candidate_sites=1 mapped_sites=1 b0_sites=0 prologue=8
tail_match=yes` — golden guest-syscall DETLOG identical to the e9patch sequence
modulo the deterministic 8-syscall e9loader prologue; golden ptrace and e9patch
both L2 (`--strict --verify`).

```
ci/test_harness.sh audit-inventory  => exit 0
  guest-fixture 224 -> 231, manifest-test 196 unchanged, files 569 -> 576
```

## Reproduction

```bash
cd hermit
export HERMIT_E9TOOL=<repo>/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=<repo>/reverie/third-party/e9patch/e9patch
# hermit must be built --features e9patch
python3 tests/backend-parity/e9patch_corpus.py --require-backend
```

## Interpretation

The supported non-gated new-family field is thinning (confirmed unsupported
under hermit across prior rounds: sched_getattr, io_setup, openat2, SysV IPC,
keyctl, userfaultfd, several prctl ops). Round 28 mined the legacy/variant vein
— older syscall numbers whose newer flag-carrying counterparts were already
covered — and landed 7 with zero drops. This vein (legacy fd creators,
NUMA-policy range calls, I/O-priority, timespec poll variants) still has residue
but is finite; future rounds will likely see the drop rate climb again as the
remaining uncovered syscalls skew toward privileged, namespace, time, or
scheduling families that are gated or unsupported.
