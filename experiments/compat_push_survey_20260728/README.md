# Compat push: harder-program L2 survey (ptrace backend)

**Date:** 2026-07-28
**Task:** compat push — run harder programs under `--strict --verify`, debug + fix.
**Backend:** ptrace (default). **Mode:** `hermit run --strict --verify` (L2).
**Hermit:** `worktrees/275/hermit` `target/release/hermit`, slot HEAD `15293c34`,
reverie pin `9233c0d0`. **Host:** devbig030, kernel 6.17.13.

## Question

After the debug-batch determinize sweeps declared the syscall frontier
saturated, does the ptrace backend still fail *harder* real programs at L2, and
is any failure a fresh, safe, landable compat fix?

## Result summary

~50 harder programs run under `hermit run --strict --verify`. **Near-all PASS.**
Every genuine failure maps to an already-tracked hard/architectural issue; no
fresh landable syscall/determinize gap was found.

### PASS at L2 (representative)

- **Compression:** `xz`, `zstd`, `bzip2` roundtrips.
- **Crypto/encode:** `openssl dgst -sha256`, `openssl genrsa|rsa`, `sha512sum`,
  `base64` roundtrip, `iconv` utf8<->utf16.
- **Text/data:** `jq`, `gawk` (incl. `|&` coprocess to `sort`), `perl` (hash +
  `fork`/`waitpid`), `sqlite3 :memory:`, `sed -i`, `tar czf|tzf`, `od`, `factor`,
  `expr`, `printf %.4f`, `yes|head`, `sort` of 100k shuffled lines.
- **Concurrency (single image):** `python3` 8-thread mutex counter;
  `asyncio.gather` of 50 coros; `make -j4`; `python3` threaded TCP loopback
  echo; `multiprocessing.Process`+`Queue`; `multiprocessing.Pool(1)`.
- **Host-state probes (correctly virtualized):** `/proc/cpuinfo` cpu count,
  `/proc/meminfo`, `sched_getaffinity`, `os.times`, `getrusage`, `/proc/uptime`,
  `nproc`, `getconf`, `locale -a`, `mktemp`, `os.urandom`, `uuid4`, `dd
  if=/dev/urandom`, `taskset`, `ionice`, `numactl`, `ldd`, `flock`, `stdbuf`,
  `date +%N`, `signalfd` (block+raise+sigwaitinfo), single-process `alarm`+`pause`.

### FAIL — all map to known hard issues (no fresh landable fix)

| Program | Failure | Maps to |
|---|---|---|
| `python3 multiprocessing.Pool(>=2)` | HANG (livelock) | **#830** (added minimal repro) |
| `comm <(seq 3) <(seq 2 4)` | HANG (livelock) | **#830** (added minimal repro) |
| `git init/commit` in a fresh repo | HANG | #830 family + Meta-git-wrapper |
| `timeout 5 echo hi` | nondeterministic + `ivar.rs:91` panic | **#1039** (filed) |

### Not bugs (test artifacts / environment)

- `ssh-keygen -f /var/tmp/k` "divergence": the `--verify` re-run finds the key
  file the first run wrote (`newfstatat` ENOENT vs Ok, st_size=419) — the known
  persistent-workdir-across-verify artifact, not a hermit bug.
- `os.eventfd` "fail": Python 3.10+ API; `/usr/bin/python3` is 3.9, native
  `AttributeError` too.
- `strace echo`: nested ptrace under hermit; expected.

## Two localized failures (both already tracked)

### 1. Multi-process pipeline `InternalIOPolling` livelock — #830

`multiprocessing.Pool(2)` is a ~3-line deterministic reproducer of #830 (rsync).
Signature: only the main task (dtid 3) is runnable (`queue len 1`), busy-polling
`wait4(WNOHANG)` on `{InternalIOPolling: W}`, `poll_attempt` growing without
bound (532640+), while worker processes blocked on the pool's inqueue semaphore
are never scheduled to progress. `Pool(1)` passes; the >=2-worker semaphore
contention is the trigger. `comm <()` is the same shape with two pipe inputs.
Minimal reproducers added as a comment to #830.

### 2. `timeout(1)` armed SIGALRM races child under `--verify` — #1039 (filed)

`timeout 5 echo hi` arms a 5 s `setitimer`/`alarm` then `wait4`s the child. The
ordering of "child completes" vs. "5 s virtual timer fires" is not anchored to
deterministic virtual time, so verify run 1 sees `wait4=Ok(5)` (exit 0) and run
2 sees `kill(5,15)=ESRCH` then `exit_group(124)`. Intermittently panics at
`detcore/src/ivar.rs:91` ("Ivar multiple put") in the concurrent signal path.
Single-process `alarm`+`pause` is L2, so the trigger is the armed-timer-vs-child
race. Full DETLOG diff in #1039.

## Disposition

No fresh, safe, landable compat fix this round: the ptrace L2 frontier is mature
and the remaining failures are deep scheduler/timer determinism issues (#830 and
#1039), the same architectural bucket as reverie#207 and the record-mode
multi-process hangs. Contributed value this round: minimal `mp.Pool(2)`/`comm`
reproducers on #830 and a new focused issue #1039 for the `timeout` timer race +
ivar panic. Per hermit AGENTS.md ("report the exact limitation"; agents do not
close tasks), the task stays `in_progress` with this evidence.

## Reproduction

```bash
cd ~/work/dev-hermit/worktrees/275/hermit && cargo build --release
H=./target/release/hermit
$H run --strict --verify -- timeout 5 echo hi                 # nondet / ivar panic (#1039)
$H run -- /usr/bin/python3 -c 'import multiprocessing as mp
def f(x): return x+1
with mp.Pool(2) as p: print(p.apply(f,(5,)))'                 # HANG (#830); Pool(1) OK
$H run -- bash -c 'comm <(seq 3) <(seq 2 4)'                  # HANG (#830)
```
