# Hermit Overnight Testing Summary — 2026-07-23

Aggregated from the overnight fleet of ~25+ testing/validation tasks run against
`rrnewton/hermit` `main`. Branch progressed **15fb99f → db09337** during the run;
outcomes below are on current `main` unless a landed fix is called out.

Primary sources: `impl-comprehensive-final-matrix`, `impl-test-java-determinism`,
`impl-test-java-programs`, `impl-test-deterministic-builds` (plus the ~20 category
tasks they aggregate).

Terminology: **L2** = `hermit run --strict --verify`, two runs bitwise-identical
(the determinism bar used throughout). Backend = ptrace unless noted.

---

## TL;DR

- **The core value proposition holds: hermit produces reproducible builds.** Two
  independent `hermit run --strict -- gcc/clang -o out src.c` yield **bit-identical
  binaries** — proven decisively by a `__DATE__/__TIME__` program that differs
  natively every build but is byte-identical under hermit (clock virtualized).
- **The vast majority of single-threaded, single-process workloads are
  deterministic at L2** on current `main`: coreutils, text/regex, FP math, shell
  scripts, process/pipe trees, subprocess, signals, timers, filesystem/symlink,
  sqlite3, redis, stock git (incl. deterministic commit hashes), python stdlib,
  node, lua, perl, curl, sockets (with port virtualization).
- **Key landed fixes this cycle:** identity/fs syscall cascade (#207:
  getcwd/getuid/geteuid/getgid/getegid/statfs + timers + membarrier), a SIGALRM
  re-arm scheduler panic (#214), DBI `--backend dbi` ungate (#213), reverie pin
  bumped to a working DBI rev (#212).
- **Java `#219` overflow fix validated** (branch `impl-java-overflow-fix-v2`):
  `saturating_add` in `LogicalTime` — java now runs and is deterministic for
  startup, Hello World, threaded compute, and file I/O.
- **Remaining gaps** are a small, well-characterized set (below), several of
  which are *test-methodology artifacts* rather than engine defects.

---

## Category matrix (L2 unless noted)

| Category | Result | Notes |
|---|---|---|
| Coreutils / text | PASS | true, echo, cat, ls, date, env, wc, head, sort, uniq, cut, seq, tr, sha256sum, base64, tail, grep, sed, awk |
| Regex | PASS | grep -oE, python re, perl — 3/3 |
| FP / math | PASS | python math, bc -l, perl atan2, awk trig — 4/4 (fbpython launcher nondet, out of scope) |
| Shell scripts | PASS | cmd-subst+pipe, here-doc, EXIT trap, functions+args — 4/4 |
| Process trees / pipes | PASS | fork/exec chains + pipe cascades + `while read` builtin loops — 3/3 |
| Subprocess | PASS | python `subprocess` fork/exec + pipe/dup2/poll/waitpid — 2/2 |
| Signals / timers | PASS | alarm/SIGALRM, self-signal, sigprocmask, nanosleep/sleep/setitimer/timer_create/timerfd (#214 fixed a SIGALRM re-arm panic) |
| Filesystem / symlink | PASS | symlink+readlink, realpath, stat/lstat, hardlink nlink, pathlib; inodes virtualized to small deterministic values |
| Compression | PARTIAL | gzip ✅, bzip2 ✅; **tar czf FAIL** (multi-proc + NSS getpwuid socket lookup → nondeterministic interleaving) |
| Database | PASS | sqlite3 `random()` & `datetime('now')` deterministic; redis full SET/GET/SHUTDOWN |
| Git | PASS (stock) | stock /usr/bin/git 2.52 init/commit/merge/diff L2; **commit hashes deterministic** (reproducibility win). Meta git 2.53 (`git`) times out under --verify (telemetry threads) — use stock git |
| Network | PASS | curl, python TCP loopback (single + threaded), bind+getsockname (port virtualized to 32768) — 4/4 |
| Python stdlib | PASS | json, os.getpid/getuid (virtualized), sys.version, tempfile random name (deterministic), pathlib — 5/5 (stock python3.9) |
| Multithreaded | MOSTLY | C pthread ✅, Go goroutines ✅, OpenMP ✅, Rust std::thread ✅; **python threading FAIL@L2** (multithreaded wall-clock/virtual-time divergence, intermittent). Thread *scheduling* determinism itself is solid |
| Language runtimes | MOSTLY | node ✅, lua ✅, perl ✅, stock python3 ✅; ruby N/A (host broken); **php FAIL** (hang/preemption stall); **java** now ✅ with #219 fix |
| Deterministic builds | **PASS** | gcc & clang bit-identical binaries across independent runs (see below) |
| Record/replay | PARTIAL | coreutils + pthread/pipe + python multiprocessing replay byte-identical; find/gcc/make -j record have known gaps |
| DBI backend | RUNS | echo/C-hello/python3/curl/redis run under `--backend dbi`; syscall-bound ~64× faster, CPU-bound 1.5–2.5× slower; **interception-only — does NOT drive Detcore, so `--strict` is a no-op there** |

---

## Deterministic builds — the headline use case (task: impl-test-deterministic-builds)

Confirmed on `main @ db09337`, two independent `--strict` runs, byte-compared:

| compiler | test | native | hermit (2 independent runs) |
|---|---|---|---|
| gcc 11 | hello.c → binary | identical | **IDENTICAL** (sha256 508f2b57…) |
| clang | hello.c → binary | — | **IDENTICAL** (sha256 4e298afe…) |
| gcc 11 | `__DATE__/__TIME__` embed | **DIFFERENT** (bakes wall-clock) | **IDENTICAL** (sha256 55f52fc2…) |

The `__TIME__` case is the decisive proof: natively the compiler bakes the
wall-clock compile time into the binary (two builds differ); under hermit the
clock is virtualized so it resolves identically every run.

**Important reconciliation:** `gcc` *fails* `--strict --verify`, but that measures
internal syscall-trace determinism (multi-process cc1/as/ld interleaving + FS
state), **not** the build artifact. The artifact identity holds. "gcc fails
--verify" and "gcc builds are reproducible" are both true and not contradictory.

---

## Java — #219 overflow fix validated (tasks: impl-test-java-determinism, impl-test-java-programs)

Branch `impl-java-overflow-fix-v2` (slot94 @ 0a838fd) changes
`detcore-model/src/time.rs` to use `saturating_add` in the three `LogicalTime`
`Add` impls, replacing the unchecked `+` that panicked at `time.rs:216`
(`attempt to add with overflow`). This resolves **GH #219**.

| program | run (--strict) | L2 | output |
|---|---|---|---|
| `java -version` | PASS exit0 (was: overflow panic) | PASS (5/5) | OpenJDK 1.8.0_492 (Temurin) |
| Hello World | PASS | PASS | "Hello, hermit!" |
| HelloThreads (4 threads, compute) | PASS | PASS (4/4) | deterministic |
| FileIO (createTempFile/write/read) | PASS | PASS | deterministic, len=14 |

The fix is what makes java run at all (unpatched java hangs/panics via the
`LogicalTime` overflow). 4-thread Java is *stably* deterministic — unlike
python/node threading — because those threads do pure compute and don't read
wall-clock time, avoiding the MT virtual-time gap.

> Caveat: only exercises startup/Hello/threaded-compute/file-IO. Heavy Java
> workloads that read wall-clock time under many threads could still hit the
> multithreaded virtual-time gap (below). PR #223 carries this fix; its local
> `validate.sh` currently fails on unrelated/environmental tests (cc-link, PMU,
> a stale `git ExpectedFail`) plus one LogicalTime-adjacent futex-timeout test
> that needs a main-baseline comparison before attribution.

---

## Known gaps / follow-ups (characterized)

1. **Multithreaded wall-clock virtual-time divergence** — python/node threads
   that read `gettimeofday`/`clock_gettime` can diverge at sub-second granularity
   under L2. Intermittent/load-sensitive. Thread *scheduling* determinism is
   solid; this is virtual-time-under-MT specifically.
2. **tar czf nondeterministic** — multi-process + NSS `getpwuid` socket lookup →
   nondeterministic syscall interleaving (a scheduler-ordering gap).
3. **`--verify` on filesystem-mutating workloads (make/gcc/tar)** — `--verify`
   re-runs the guest twice in the *same persistent* working dir, so run 1's output
   leaks into run 2 (e.g. `readlink → ENOENT` vs `EINVAL`, "up to date"), producing
   *false* nondeterminism. make/gcc are deterministic from clean state. Only guest
   `/tmp` is per-run isolated. **Recommendation:** `--verify` should reset/overlay
   the workdir between runs.
4. **php** — hangs/timeout under strict and plain (likely preemption single-step
   stall).
5. **Meta git (2.53)** — telemetry threads make `--verify` time out; stock git is
   fine and gives deterministic commit hashes.
6. **record/replay** — find replay panics; gcc/`make -j` record timeout
   (parallel-proc/jobserver limitation).
7. **DBI backend** — interception-only; does not drive Detcore, so `--strict` is a
   no-op and determinism is only partial there.

---

## Determinism wins (host nondeterminism correctly sanitized)

`sqlite3 random()` / `datetime('now')`, **git commit hashes**, python `tempfile`
random names, socket ephemeral ports, virtualized pid/uid/inodes, virtual epoch
time — all reproducible run-to-run where they vary natively. These are the
capabilities that make reproducible builds and record/replay debugging possible.

---

*Generated by task `impl-write-overnight-summary-doc` from fleet task notes.
"PASS" = observed L2 determinism + correct output in the cited task; not a
guarantee across all inputs.*

---

# Final batch (appended 2026-07-23, task impl-update-overnight-summary-final)

Second wave of tasks: CLI-mode coverage, more app determinism, record/replay
fixes, and — most importantly — the **root cause of the build-tool `--verify`
failures**. All on `main @ db09337`, ptrace backend, unless noted.

## Root cause: build-tool `--verify` failures = vfork scheduling nondeterminism

The single most important overnight finding. gcc / rustc / bash **FAIL
`--strict --verify`**, but *not* because of clocks, PRNG, or missing syscalls:

- **They diverge at `vfork()`.** A vforked parent is kernel-blocked, so the child
  must self-register (`lib.rs:950`), which races Detcore's intentional
  turn-race in `BlockingExternalIO` (`scheduler.rs:1727`). A normal `clone`
  registers the child synchronously → deterministic; `vfork` does not.
- Single-process tools (`make`, `echo`, `tcc`) **PASS L2** — no vfork, no race.
- Proposed fix: eager child-expect + deterministic parent-block woken by child
  exec/exit, child scheduled first.

This reconciles every earlier "gcc/tar/make nondeterministic" observation:
1. build **artifacts** are still bit-identical across independent runs
   (reproducible builds hold — see the deterministic-builds section above);
2. `--verify` failures are the vfork scheduling race **plus** the
   test-methodology artifact below — not a clock/PRNG defect.

**`--verify` persistent-workdir artifact:** `--verify` re-runs the guest twice in
the *same* working dir; for filesystem-mutating workloads run 1's output leaks
into run 2 (`readlink → ENOENT` vs `EINVAL`, "up to date"), causing *false*
nondeterminism. Only guest `/tmp` is per-run isolated. make/gcc are deterministic
from clean state. Recommendation: `--verify` should reset/overlay the workdir.

**tcc is the deterministic-C witness:** TinyCC is 1 execve / 0 forks, so it dodges
gcc's vfork nondeterminism — byte-identical output, **L2-verifies** (once the
output-path existence is consistent). Use tcc when a determinism-clean C compiler
is needed.

## CLI modes & isolation (this batch)

| Feature | Result |
|---|---|
| stdin passthrough | PASS — `echo … \| hermit -- cat`/`wc -c` correct + L2 deterministic |
| large output | PASS — `seq 100000` (~588KB) & `find /usr \| head -100` L2 (getdents order + SIGPIPE teardown deterministic) |
| `/tmp` isolation | CONFIRMED bidirectional — host `/tmp` files invisible to guest; guest `/tmp` writes don't leak; per-run private tmpfs |
| env isolation | **Partial/inconsistent** — `getpid`→3, `getuid`→0(root), `hostname`→hermetic-container.local are virtualized, but the **environment is passed through raw** (only `ASAN/LSAN_OPTIONS` added). `$HOSTNAME`/`$USER`/`$HOME` **leak the real host** and contradict the syscalls. Use `--base-env=minimal` to scrub |
| `--chaos` | Deterministic *per seed* (reproducible), not run-to-run random |
| `--seed` | No effect under plain `--strict` (deterministic scheduler is seed-independent); only changes scheduling **with `--chaos`**; same seed always reproducible |
| `hermit analyze` | Chaos-mode root-cause bisector; needs a *discriminating* target (default nonzero-exit) + baseline; on trivially-passing programs correctly reports "no matching run". Gotcha: guest flags like `-c` must be escaped `-- -c` |

## More app determinism (this batch)

| Workload | Result |
|---|---|
| process trees / pipe cascades | PASS L2 (`for\|sort\|head`, `seq\|sort\|tail`, subshell\|`while read`) |
| python subprocess (run/Popen+communicate) | PASS L2 |
| Go (hello, 4 goroutines, mpsc channels) | PASS L2 (static binaries; scheduler + futex deterministic) |
| Rust (hello, 4 threads, mpsc) | PASS L2 (dynamic glibc; full rseq/getcwd surface) |
| complex SQLite (aggregates, BEGIN/COMMIT) | PASS L2 |
| signals (SIGUSR1 self-signal handler) | PASS L2 |
| env / locale | PASS L2 (5/5, 4/4); **id** rare intermittent DETLOG flake (~1/10; stdout stable) |

## Record/replay & platform fixes landed/confirmed on main

- **`--strict` syscall cascade LANDED** (PR #207, commit 208f574):
  getuid/geteuid/getgid/getegid/getcwd/statfs/fstatfs = passthrough; timer_create
  family = emulated (PosixTimers); membarrier = no-op. python3/git/sqlite3 pass L2.
- **replay envp corruption RESOLVED** @15fb99f (post-#88 break fixed);
  `record start`/`replay` suite 24/24 green.
- **FIOCLEX record/replay panic** already fixed (PR #127) — don't re-PR.
- **JVM record/replay** fix already on main (47f4261); net-new delta was a futex
  `FUTEX_CMD_MASK` fix (PR #216).
- **CLOCK_MONOTONIC** already deterministic (virtualized epoch + vDSO patched);
  the gcc `--verify` blocker is vfork/unsupported-syscall, **not** the clock.
- **Meta git wrapper** (`git` = 2.53, 720 futex/39 clone3 + telemetry) ~73s under
  `--strict`, `--verify` times out; stock `/usr/bin/git` is 0.24s and L2-clean —
  use stock git in tests.
- **`ls -la`** intermittently diverges under `--verify` at `poll()` on an AF_UNIX
  NSS/nscd socket (owner/group name lookup); pure file ops are stable. (Same NSS
  class as the `tar`/`id` flakes.)

## Performance

`hermit --strict` overhead: ~18–30ms fixed startup tax (3.5–4.4× on trivial
commands), ~87× on CPU-bound work (precise single-stepping). DBI backend:
syscall-bound ~64× *faster* than ptrace, CPU-bound 1.5–2.5× *slower* (but
interception-only — no Detcore, so `--strict` is a no-op there). Caveat: bare
`timeout` leaks orphaned hermit processes — use `--kill-after`.

## Net assessment

Determinism engine is **solid for single-threaded / single-process** workloads
across a very broad app surface. The two remaining engine-level gaps are both
scheduling races, now root-caused: **vfork** (build tools) and **multithreaded
wall-clock virtual-time** (python/node threads reading the clock). NSS/nscd
socket lookups (`tar`, `ls -la`, `id`) are a related intermittent poll-ordering
class. Everything else (clocks, PRNG, identity syscalls, getdents order, signals,
pipes/IPC, record/replay of common tools) is deterministic on current main.
