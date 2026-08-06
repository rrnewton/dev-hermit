# File-I/O determinism residue: readv/writev short-vector and sendfile-to-socket

**Task:** `file-io-determinism-residue` · **Agent:** hermit-det3 (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress.

## Question

The parent file-io sweep (`file-io-offset-determinism`) closed with an honest scoping statement:
it never exercised **readv/writev short-vector** behaviour or **sendfile with a socket
destination**, and ran n=2 on one host with no repetition. Those are exactly where short-count
divergence hides. This experiment covers them.

Why the parent's cells could not have found it: its `readvwritev` mode ran against a **regular
file**, where a vectored read is short only at EOF — so the split point is a function of file size
and can never be host- or timing-dependent. Its `sendfile` mode was **file→file**, which transfers
everything. Neither shape can produce a short count whose position varies.

## Answer, in one line each

1. **sendfile to a socket is not short — it is REFUSED.** hermit returns `ENOSYS`, deliberately.
   The task's question is therefore *unreachable*, and the real risk is somewhere else.
2. **A blocking `write()`/`writev()` to a pipe or stream socket returns a SHORT COUNT that Linux
   would never return.** Deterministic, but wrong — and for `writev` the shortfall lands *inside*
   an iovec element, tearing the vector.
3. **Everything that hermit actually executes is self- and cross-backend deterministic** —
   13 of 14 modes, 5/5 runs, zero detlog differences. The one failure is a **hang**, not a
   nondeterminism.

## 1. sendfile → socket is refused, silently

All three socket-destination shapes (blocking AF_UNIX socketpair, nonblocking AF_UNIX with a
drainer, loopback TCP with a drainer) report `sent=0 calls=0 err=38`. From the detlog:

```
finish syscall #51: socketpair(1, 1, 0, 0x7fffffffc750) = Ok(0)
finish syscall #61: sendfile(3, 5, 0x7fffffffbb90, 1048576) = Err(Errno(ENOSYS))
```

The socket is fine; `sendfile` itself is refused. **hermit exits 0.** Natively the same guest
transfers the full 1 MiB.

**Root cause, read from source** — `detcore/src/syscalls/files.rs:840-844`:

```rust
if !matches!(in_type, FdType::Regular | FdType::Memfd)
    || !matches!(out_type, FdType::Regular | FdType::Memfd) { return Err(Errno::ENOSYS.into()); }
```

Deliberate, and documented at `files.rs:797-808`: *"Socket and pipe destinations can block and need
the nonblocking scheduler path; return ENOSYS for those endpoint types so libc/application
fallbacks use Detcore's existing read/write handlers instead."*

**The design is defensible; the silence is not.** The mitigation rests on a fallback the product
cannot verify exists. glibc does **not** provide one — `sendfile()` is a thin syscall wrapper, and
the guest receives `-1/ENOSYS` straight through (measured). So the fallback must live in the
*application*. When it doesn't — and the `sendfile-sock-*` guests here deliberately don't, standing
in for any program that treats `sendfile` as always-available — **the guest transfers zero bytes and
hermit exits 0 under `--strict`, with no diagnostic.** A static file server under hermit would serve
empty responses and be scored a clean deterministic run.

The `sendfile-sock-fallback` mode brackets the other half: a guest that *does* implement the
ENOSYS fallback completes correctly and deterministically. So the intended path works — the gap is
that nothing tells a guest it is on the unintended one.

## 2. Blocking write/writev returns a short count Linux would not

Native vs hermit, same guest, same host:

| mode | endpoint / call | native (3/3) | hermit ptrace (2/2) |
| --- | --- | --- | --- |
| `pipe-write-short` | blocking pipe, `write` 256 KiB | 262144 (full) | **65536** — pipe capacity |
| `pipe-writev-block` | blocking pipe, `writev` 3×64 KiB | 196608 (full) | 196608 (full) — **correct** |
| `sock-write-short` | blocking AF_UNIX, `write` 64 KiB | 65536 (full) | **32640** |
| `sock-writev-block` | blocking AF_UNIX, `writev` 3×64 KiB | 196608 (full) | **32640** |

`sock-writev-block` prints `elem=0+32640`: the shortfall lands **inside iovec element 0** —
element 0 partially written, elements 1 and 2 untouched, and nothing tells the guest which. That is
the short-vector observable the parent sweep could not produce.

**This is not a nondeterminism.** The short value is stable (2/2 under hermit, 3/3 full natively).
It is a *deterministic wrong value* — which is why a check phrased as "assert identical return
lengths" scores it clean. Consequence: any guest that does not loop on a partial write silently
loses data while hermit exits 0. The first fallback loop written for this experiment did exactly
that and lost 526336 of 1048576 bytes before it was fixed.

**Root cause, and the product already states it** — `detcore/src/syscalls/helpers.rs:191`,
`execute_blocking_pipe_writev`:

> *"Complete a logically blocking pipe writev after Hermit has made the pipe physically
> nonblocking. **A positive short write is an implementation artifact here: without O_NONBLOCK,
> Linux blocks until the full vector is written** unless a signal or error interrupts it."*

The fix exists and is correct. It is wired at **one** call site — `files.rs:1071`:

```rust
if physically_nonblocking && fd_type == FdType::Pipe && !logically_nonblocking {
    self.execute_blocking_pipe_writev(guest, call).await
} else if physically_nonblocking && matches!(fd_type, Socket | Pipe | Eventfd) {
    self.execute_nonblockable_fd_syscall(guest, call).await   // <-- returns the partial count
```

The generic path bottoms out in `retry_nonblocking_syscall_helper` (`helpers.rs:1121`), which
retries only while `syscall_would_have_blocked(res)` — i.e. only on `EAGAIN`. A positive partial
count is not "would have blocked", so it is handed to the guest as-is.

**Coverage of the existing fix — 1 of 4 cells:**

| | pipe | socket |
| --- | --- | --- |
| `writev` | **COMPLETED** (`files.rs:1071`) | SHORT |
| `write` | SHORT | SHORT |

`handle_write`'s own `deterministic_io` branch (`files.rs:916-950`) contains a correct completion
loop, but it is the `else` arm — physically-nonblocking pipes and sockets never reach it.

**This unifies with finding 1.** The `sendfile` ENOSYS refusal is justified in-code as *"socket and
pipe destinations can block and need the nonblocking scheduler path"* — the **same missing
capability**. `sendfile` responds by refusing; `write`/`writev` respond by returning a value Linux
would never return. One gap, two different unsafe answers.

## 3. Determinism results

`--strict --verify` ×3 and `--strict --detlog-stack --detlog-heap` ×5 per (mode × backend), across
ptrace and e9patch. Full table in `results.csv` / `cross-backend.csv`.

**13 of 14 modes are clean on every axis:** 3/3 `--verify` "Determinism verified"; 5/5 runs rc=0;
one distinct stdout; **zero** content-mode detlog differences run-to-run; **zero** structural
differences ptrace vs e9patch. No mode emitted a `FileContents` record, so the known raw-host-inode
defect (`detlog_embeds_raw_host`) does not touch these results.

**The exception is `readv-nonblock-short`, and it fails by HANGING, not by diverging** — rc=124
(timeout) on both backends, 0/3 verify. See §4.

### Anti-vacuity — measured before trusting any green

10 native runs per mode (`native-vacuity.csv`). Only **4 of 14** modes vary natively:

| mode | distinct native stdout / 10 | verdict |
| --- | --- | --- |
| `sendfile-sock-unix` | 10 | non-vacuous — but see below |
| `readv-nonblock-short` | 9 | non-vacuous — hangs under hermit |
| `readv-pipe-short` | 7 | **non-vacuous, genuine determinism win** |
| `writev-drain-short` | 6 | non-vacuous — but see below |
| other 10 modes | 1 | **vacuous as determinism evidence** |

For the 10 natively-stable modes, "identical under hermit twice" is weak evidence and is **not**
counted as a determinism win here. They remain useful as *divergence* cells — `pipe-write-short`
and `sock-writev-block` are natively stable yet produce a *different* stable value under hermit,
which is finding 2.

**Two of the four non-vacuous greens are not wins, and saying so is the point:**

- `sendfile-sock-unix` varies natively 10/10 and is perfectly stable under hermit — because hermit
  **refuses the syscall** and the guest does nothing. Determinism by non-execution. Counting this
  as a win would be the clearest possible example of a vacuous green.
- `writev-drain-short` is stable under hermit but transfers **65536 bytes where native transfers
  786432** — it hits the EAGAIN cap (§4). Deterministic and semantically divergent.

That leaves **`readv-pipe-short` as the one unambiguous win**: natively 7/10 distinct split
patterns, byte-identical under hermit on both backends. Hermit genuinely determinizes a
host-dependent vectored-read split point.

## 4. A nonblocking poller starves its blocking peer (unattributed)

`readv-nonblock-short` times out under hermit. From the log: the reader issues **201 consecutive
`readv` EAGAINs before the writer thread gets a single turn**; the reader then exhausts its retry
budget, and `pthread_join` blocks forever on a writer stuck in a full pipe. Natively the reader gets
data after 0–8 EAGAINs and the guest completes. `writev-drain-short` shows the same shape without
the terminal deadlock: 201 EAGAINs, drainer never runs, 65536 bytes moved instead of 786432.

This is **consistent with** the known foundation class
`scheduler-vtime-jump-unproductive-pollers` — whose prior witnesses are all QEMU- or build-scale,
so a ~40-line file-I/O reproducer would be useful. **It is not confirmed to be that bug.** The
discriminator work (frozen `committed_time` + `SleepUntil(0)` storm vs. a reap/wait4 cause) was not
done. Treat as unattributed.

## Rigor — "swept" is not "covered"

- **n:** 3 `--verify` + 5 detlog runs per (mode × backend) = 218 of a planned 224 hermit runs
  (`readv-nonblock-short` contributes 2 timeouts per backend instead of 8 runs), plus 140 native
  runs. Better than the parent's n=2, still not stress-hardened.
- **hosts: 1** (devbig014), one binary, one build, one kernel. **No cross-host replication.** The
  box was shared with ~15 concurrent agents throughout, which inflates native variance (helping the
  non-vacuity claim) and could mask a load-sensitive hermit-side effect (hurting the greens).
- **backends: 2 of 6**, and they are **not independent** — e9patch is binary-rewriting
  preprocessing whose *runtime is ptrace*. So "cross-backend parity" here is close to a
  single-scheduler sample and proves less than a 2-backend table appears to. DBI and SaBRe are not
  in this build; LiteInst's preload runtime is unavailable; KVM hangs at guest startup.
- **Sub-cases NOT exercised**, each a real gap rather than a rounding of scope: `O_DIRECT`;
  `io_uring`; NFS/overlay filesystems; `sendfile` with a *pipe* destination (only socket and file
  were run); `splice`/`vmsplice`/`copy_file_range`; `eventfd` as a short-write endpoint (the
  `handle_write` guard names it alongside Socket/Pipe, so it is likely a fourth broken cell —
  untested); short writes interrupted by a **signal**, which is the one case where Linux *does*
  legally return short and which therefore decides whether a fix must distinguish them; and
  `--verify-strict`/`bitwise_parity`, so **no L2 claim is made here** — the `--verify` runs used the
  default lossy `Stripped` comparator.
- **Assurance level: L1** (`--strict` completes deterministically) plus cross-run content-mode
  detlog equality. **Not L2.**

## Reproduction

```bash
# build the guest
gcc -O1 -g -o shortvec shortvec.c -lpthread

# native anti-vacuity (must run FIRST -- a green on a natively-stable mode is vacuous)
for m in <modes>; do for i in $(seq 1 10); do ./shortvec "$m"; done; done

# the hermit matrix (env-pinned; see run-cell.sh for why env -i is load-bearing)
HERMIT_BIN=<hermit> MODES="<modes>" ./sweep.sh
python3 analyze.py
```

`run-cell.sh` pins the environment with `env -i` and a fixed variable set. This is **not** hygiene:
the kernel writes `envp` into the guest's initial stack, so any host variable differing between two
runs changes `--detlog-stack` and manufactures a false divergence. Measured in the parent sweep:
unpinned = 3/3 distinct stack hashes, pinned = 2/2 identical.

## Follow-ups this raises (not filed as tasks by this experiment)

1. Wire the existing completion helper into the three uncovered cells (`write`/pipe,
   `write`/socket, `writev`/socket), and check `eventfd`.
2. Decide whether `sendfile`-to-socket should keep failing silently. A warning, or a `--strict`
   fail-close, would convert a silent zero-byte transfer into a visible one.
3. Confirm or refute the poller-starvation attribution with the documented discriminator.
