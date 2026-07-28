# SaBRe backend: EAGAIN leaks to execve'd pipeline children

**Date:** 2026-07-28
**Task:** `compat-sabre-ratchet-round3` (P1: SaBRe compat ratchet round 3)
**Backend under test:** `sabre` (SaBRe static binary rewriting + ptrace
fallback supervisor). Compared against `ptrace` (default, best-tested).
**Hermit:** `worktrees/275/hermit` `target/release/hermit`, slot HEAD
`15293c34` (feature branch `codex/qemu-userspace-program-l2`).
**Mode:** `hermit run --strict` (and `--strict --verify` for L2).

## Question

The SaBRe ratchet asks: does the sabre backend pass more real programs at L2,
and what SaBRe-specific guest issues remain? Establish an L2 pass/fail matrix
and root-cause any genuine sabre-only failure.

## Result summary

### Example set — 4/4 L2-PASS under sabre (`--strict --verify`)

| Program | sabre L2 | Notes |
|---|---|---|
| `date.sh` | PASS | |
| `race.sh` | PASS | two bash a/b loops, 200 each |
| `rand.py` | PASS | **must use `/usr/bin/python3`** (see fbpython note) |
| `timed-progress-bar.py` | PASS | datetime-based |
| `devrand.sh` | excluded | blocks on `/dev/random` by design |

Broad utilities also L2-PASS under sabre: `echo`, `cat /etc/hostname`,
`seq 100`, `sort`, `date`, a Python compute loop.

**Not a sabre bug — fbpython.** `rand.py` via `/usr/local/bin/python3`
SIGSEGVs (patched_sites=0, scheduler 0 turns — the plugin never engages),
because that path is `fbpython`, the Meta Python wrapper. Upstream
`/usr/bin/python3 -c 'print(2+2)'` runs fine under sabre (133 scheduler
turns, output `4`, exit 0). Use `/usr/bin/python3` for Python examples.

**Not a sabre bug — `ls -l /`.** Flaky under `--verify` on **both** sabre and
ptrace (DETLOG identical, stdout differs): the known NSS/`/proc` output
nondeterminism, not sabre-specific.

### Genuine sabre-specific bug: EAGAIN leaks to execve'd pipeline children

Multi-process pipelines whose **reader can outrun a slow/descheduled writer**
FAIL under sabre but PASS under ptrace:

```
hermit run --backend sabre  --strict -- /bin/sh -c 'head -c 1000 /dev/urandom | wc -c'
  -> wc prints 0, then: /usr/bin/wc: 'standard input': Resource temporarily unavailable ; rc=1
hermit run --backend ptrace --strict -- /bin/sh -c 'head -c 1000 /dev/urandom | wc -c'
  -> 1000 ; rc=0
```

Also fails: `{ sleep 0.05; echo x; } | wc -c`, `cat /dev/urandom | head -c 1000 | wc -c`
(gnulib `safe_read`/`safe_write` treat EAGAIN as fatal).
PASSES: `echo hi | wc -c`, `seq 100 | wc -l` — the writer completes before the
reader reads, so no empty-pipe read happens.

## Root cause

Detcore forces container-internal **pipe fds physically `O_NONBLOCK`** so its
sequentialized scheduler can use the nonblockize-and-retry strategy
(`detcore/src/syscalls/helpers.rs` `retry_nonblocking_syscall`,
`detcore/src/fd.rs` physical vs logical nonblocking). It keeps the flag out of
the **guest's logical view** (`fd.rs::set_logical_nonblocking`) so the guest
still sees a blocking fd. This masking only holds while the guest's
`read`/`write`/`fcntl` are **Detcore-mediated**.

Under the sabre backend, an **execve'd child process in a pipeline** has the
plugin (`libdetcore_sabre.so`) loaded, **but its libc syscall sites are not
SaBRe-rewritten**. Its `read`/`write`/`fcntl` therefore execute as **raw**
syscall instructions, which reach the ptrace fallback supervisor
(`hermit-cli/src/sabre_ptrace.rs`). The fallback's `mapping_is_trusted`
blanket-**trusts every `.so` mapping** (an anti-recursion measure: patching a
libc site would recurse into the plugin's own RPC, which also lives in libc).
So the child's raw libc read/write **run natively against the real,
physically-`O_NONBLOCK` pipe fd** and return EAGAIN, which is never routed
through Detcore's retry loop.

### Proof (`reader2.c`, run as exec'd child vs. root guest)

| Context (sabre) | plugin loaded | guest `fcntl(F_GETFL)` O_NONBLOCK | `read()` |
|---|---|---|---|
| exec'd pipeline child (`sh -c '… | reader2'`) | 1 | **1 (leaked)** | **-1 (EAGAIN)** |
| root guest (`hermit run … reader2 < …`) | 1 | 0 (masked) | 6/7 (ok) |

Trace corroboration: the root guest's libc reads land on the plugin trampoline
`0x555555561c99` (→ detcore); the exec'd child's 539 libc reads are `raw=true`
at `0x7ffff7cff550` (→ fallback, trusted `.so`, native). `--log trace` shows
**zero** `NonblockableSyscall:` routing decisions for the child's pipe IO and
zero `"redirecting raw syscall"` fallback patches — i.e. Detcore never sees
those reads/writes.

## Why there is no safe local fix (yet)

- **Narrowing the fallback's `.so` trust** to route the child's libc reads
  through Detcore recurses into the plugin's own libc-based RPC (the documented
  reason the trust is blanket). rip alone cannot distinguish a guest libc read
  from the plugin's infra read — both are in libc.so.
- **The correct fix is SaBRe re-instrumenting execve'd children's libc** so
  their syscalls are mediated like the root guest's — that lives in
  reverie-sabre / the SaBRe loader (a pinned, read-only dependency), so it
  needs design sign-off, a reverie branch, and a parent pin bump.
- **Making Detcore not leave pipes physically nonblocking under sabre** would
  deadlock the sequentialized scheduler (the whole reason for the strategy).

This is a genuine, precisely-localized frontier bug for the sabre backend, in
the same "needs architectural change + approval" bucket as the RT-signal
delivery bug (reverie#207). It is **not** landed here.

## Reproduction

```bash
cd ~/work/dev-hermit/experiments/sabre_pipe_eagain_20260728
gcc -O0 reader2.c -o /var/tmp/reader2      # NOT /tmp (hermit isolates guest /tmp)
HERMIT=~/work/dev-hermit/worktrees/275/hermit/target/release/hermit

# Reliable failure (sabre) vs pass (ptrace):
$HERMIT run --backend sabre  --strict -- /bin/sh -c 'head -c 1000 /dev/urandom | wc -c'   # rc=1, EAGAIN
$HERMIT run --backend ptrace --strict -- /bin/sh -c 'head -c 1000 /dev/urandom | wc -c'   # rc=0, 1000

# Instrumented proof of the flag leak:
$HERMIT run --backend sabre  --strict -- /bin/sh -c "{ sleep 0.05; echo hello; } | /var/tmp/reader2"  # O_NONBLOCK=1, read=-1
$HERMIT run --backend ptrace --strict -- /bin/sh -c "{ sleep 0.05; echo hello; } | /var/tmp/reader2"  # O_NONBLOCK=0, read=6
```

## Files

- `reader.c`, `reader2.c` — readers that report `fcntl(F_GETFL)` O_NONBLOCK,
  (and for `reader2`) whether the plugin/sabre loader are mapped, then a
  blocking `read(0,…)`.
- `slowpipe.c` — pipe + fork; child spins then writes. Single-image (no
  execve); PASSES under sabre (root/forked reader is Detcore-mediated).
- `sleeppipe.c` — pipe + fork; child `nanosleep`s then writes. Also single
  image; PASSES under sabre. (Static build SIGABRTs under sabre — a separate
  static-binary limitation, not this bug.)

The `slowpipe`/`sleeppipe` single-image cases passing while the exec'd-child
pipelines fail is the discriminator that localizes the bug to **execve'd**
children, not pipes in general.
