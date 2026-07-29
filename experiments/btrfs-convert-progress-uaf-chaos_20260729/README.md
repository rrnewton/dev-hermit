# btrfs-convert progress-thread use-after-free under hermit chaos

**Agent:** hermit-227 · **Date:** 2026-07-29 · **Task:** `btrfs-schedule-dependent-userspace-bug`

## Question

Can hermit's chaos scheduler deterministically surface a *schedule-dependent*
**userspace** btrfs-progs use-after-free that native (blind) execution almost
never hits — and replay it bit-for-bit from a recorded seed?

## Target bug

`btrfs-convert` runs a background *progress* subthread (`common/task-utils.c`,
`convert/main.c:print_copied_inodes`) that repeatedly dereferences a shared
`struct task_info *info` while the main thread copies inodes. Historically
(fixed upstream by commit **73e211a7**) the teardown path had a
use-after-free:

- `task_start()` called `pthread_detach()` on the subthread, and
- `task_stop()` did **not** `pthread_join()` it,

so `task_deinit()` could `free(info)` while the detached subthread was still
reading `*info`. Whether the crash occurs depends entirely on the thread
interleaving at teardown, which is why it is rare in the wild and essentially
invisible to blind/native execution.

The **fix** (this experiment's differential, see
`fix-73e211a7-buggy-vs-fixed.diff`) is exactly the historical one: do not
detach, and `pthread_join()` the subthread before `task_deinit()` frees `info`.

## Method

Two `btrfs-convert` binaries were built from btrfs-progs **v7.1**, compiled
with AddressSanitizer so the latent UAF becomes an observable `abort()`:

```
make EXTRA_CFLAGS='-fsanitize=address -fno-omit-frame-pointer -g -O1 -D_FORTIFY_SOURCE=0' \
     EXTRA_LDFLAGS='-fsanitize=address' btrfs-convert
```

- **buggy**: reintroduces the pre-73e211a7 detach + no-join teardown.
- **fixed**: the 73e211a7 teardown (no-detach + join).

Both variants share an identical *observability harness* (documented in
`demos/08-btrfs-convert-uaf.md`): the historical wall-clock `CLOCK_MONOTONIC`
timerfd that paces the progress thread **never fires under hermit**, because
hermit virtualizes `CLOCK_MONOTONIC` to logical (RCB) time, which barely
advances during this I/O-bound, branch-light conversion. The timer-paced UAF is
therefore *dormant* under hermit. The harness replaces the timerfd with a pipe:
the subthread parks cheaply on a blocking `read()` during `copy_inodes()`, and
`task_stop()` writes a single "final tick" byte to wake it for its last loop
iteration — the iteration that races `free(info)`. This adaptation is applied
**identically to both variants**; the only behavioral difference between them
remains the detach/join, i.e. the real bug.

Each run converts a fresh reflink copy of a small populated ext4 image
(`pop-tiny.img`, ~100 files). Command:

```
hermit run --chaos --sched-seed <S> --no-virtualize-cpuid -- <variant>/btrfs-convert <image>
```

(`--no-virtualize-cpuid` because CPUID faulting is unavailable on this host.)

## Results

See `results.csv`. Headline:

| Execution                | Runs | UAF crashes | Notes |
|--------------------------|------|-------------|-------|
| **native** buggy         | 40   | **0**       | bug dormant under blind execution |
| native fixed             | 20   | 0           | — |
| **hermit chaos** buggy, seeds 0–31 | 32 | **2** (seeds **15**, **19**) | schedule-dependent |
| hermit chaos fixed, seeds 0–31     | 32 | **0**       | fix closes the window |

Two seeds (5, 30) are pathologically slow chaos *schedules* for this workload
and hit the 70 s per-run wall-clock timeout in **both** variants; they are a
hermit-chaos artifact, not the bug, and are recorded as `timeout` in the CSV.

**The crash** (`asan-report-seed15.txt`) is the textbook 73e211a7 UAF:

```
ERROR: AddressSanitizer: heap-use-after-free ... thread T1
  #0 task_period_wait common/task-utils.c:154
  #1 print_copied_inodes convert/main.c:170
freed by thread T0 here:
  #0 free
  #1 task_deinit common/task-utils.c
  #2 do_convert convert/main.c
previously allocated by thread T0 here:
  #1 task_init common/task-utils.c
```

i.e. the progress subthread (T1) reads `info` after the main thread (T0) freed
it in `task_deinit`.

**Determinism.** Buggy seed 15 was replayed 3× with the *identical* command
(same image path). The guest ASAN report is **byte-identical** every time —
same faulting heap address (`0x606000000330`), same PC (`0x52793b`), same stack,
same shadow bytes. The only diff between runs is hermit's own host-side
`reverie_ptrace::lifecycle` log lines, which carry wall-clock timestamps and
vary in ordering; the guest execution itself is deterministic.

Determinism is per-input: the faulting heap *address* depends on `argv` (the
image path length shifts the initial heap layout), so the same seed with a
different image filename yields a legitimately different — but still per-input
deterministic — address (e.g. the sweep, which uses a distinct filename per run,
reports `0x…210` rather than `0x…330`). The PC, frames, and SUMMARY line are
invariant across all of them. `demos/08-btrfs-convert-uaf.sh` therefore replays
with the exact same image path to demonstrate byte-identical output.

**Why chaos beats blind fuzzing here:** native execution never hit the UAF in
40 runs, because the teardown window is tiny on real hardware. Hermit's chaos
scheduler explores distinct interleavings per seed and *lands the main thread's
`free` before the subthread's post-wake dereference* on specific seeds, then
reproduces that exact schedule deterministically from the seed.

## Reproduction

The binaries and images live under the ignored working directory
`ignored/demo08-btrfs/` (not committed — btrfs-progs build trees and disk
images are large/binary). To rebuild and reproduce:

1. Build btrfs-progs v7.1 twice with the ASAN flags above, applying the
   buggy vs. fixed teardown from `fix-73e211a7-buggy-vs-fixed.diff` plus the
   shared observability harness described in `demos/08-btrfs-convert-uaf.md`.
2. Build a populated ext4 image:
   `mkfs.ext4 -F -q -b 4096 -N 200 -d <some-dir> pop-tiny.img` (256 MiB).
3. Sweep seeds:
   `for s in $(seq 0 31); do cp --reflink=auto pop-tiny.img run.img; \
     hermit run --chaos --sched-seed $s --no-virtualize-cpuid -- buggy/btrfs-convert run.img; \
     echo "seed $s rc=$?"; done`
4. Confirm the fixed variant never aborts across the same seeds, and that a
   crashing seed (e.g. 15) reproduces bit-for-bit on replay.

See `demos/08-btrfs-convert-uaf.sh` for a scripted version of this sweep and
`demos/08-btrfs-convert-uaf.md` for the full write-up, including the
observability adaptation and its justification.
