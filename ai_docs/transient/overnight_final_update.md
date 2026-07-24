# Overnight Final Update — Hermit Determinism Sprint

**Date:** 2026-07-23 (morning handoff)
**Baseline:** `origin/main` progressed `15fb99f → db09337` overnight.
**Method:** Unless noted, all results are **L2** = `hermit run --strict --verify` →
`:: Success: deterministic. Determinism verified.` (exit 0), ptrace backend,
default log, no relaxations. Guest binaries: stock `/usr/bin/*` preferred over
Meta wrappers throughout.

---

## TL;DR

- **Determinism is solid for the vast majority of single-threaded, single-process
  workloads.** ~80+ distinct programs across 22 categories pass L2 on current main.
- **Final 100-run stress test: 90/100 pass (10% flake), and every failure is
  concentrated in the two multithreaded runtimes that read wall-clock time**
  (fbpython, node). The 8 single/light-threaded apps were a perfect 80/80.
- **One real fix landed as a draft PR:** Java `LogicalTime` overflow (#219 → PR
  **#222**), verified `java -version` now runs (was hanging).
- **vfork/gcc investigation concluded:** the priority tweak does **not** fix gcc;
  root cause is mid-compilation multi-process interleaving. Preserved as reference
  PR **#221** (WIP), not for landing as a gcc fix.
- **Remaining gaps are a small, well-characterized set** (below), dominated by
  multithreaded/multi-process virtual-time & scheduler-ordering determinism.

---

## 1. Comprehensive determinism matrix (~80+ programs, 22 categories)

All **PASS L2** unless flagged. Aggregated from ~25 overnight testing tasks.

| Category | Result |
|---|---|
| **Coreutils / text** | true, echo, cat, ls, date, env, wc, head, sort, uniq, cut, seq, tr, tail, grep, sed, awk — PASS |
| **Regex** | grep -oE, python re.findall, perl match — 3/3 PASS |
| **FP / math** | python math, bc -l (pi@50), perl atan2, awk sin/cos/exp — 4/4 PASS (numerically correct + bitwise) |
| **Shell scripts** | cmd-subst+pipe, here-doc, EXIT trap, functions+args — 4/4 PASS |
| **Process trees / IPC pipes** | fork/exec chains + pipe cascades + builtin loops — 3/3 PASS |
| **Subprocess** | python fork/exec + pipe/dup2/poll/waitpid — 2/2 PASS |
| **Signals / timers** | alarm/SIGALRM, SIGUSR1, sigprocmask; nanosleep, sleep, setitimer, timer_create/settime, timerfd — all PASS (#214 fixed a SIGALRM re-arm panic) |
| **Filesystem / links** | ln -s, readlink, realpath, stat/lstat, hardlink (shared inode, nlink=2) — PASS. Inodes virtualized to small deterministic values |
| **Compression** | gzip round-trip PASS; bzip2 round-trip PASS. **tar czf FAIL** (multi-proc + NSS getpwuid socket → interleaving) |
| **Archives** | zip, cpio, ar — PASS L2 **and byte-identical** across runs (native zip/ar differ; hermit determinizes them) |
| **Database** | sqlite3 (incl. `random()`, `datetime('now')` → virtual epoch, all deterministic); redis full SET/GET/SHUTDOWN — PASS |
| **Git** | **stock /usr/bin/git**: init+config+add+commit+log, branch/checkout/3-way-merge/diff — PASS, **commit hashes deterministic** (build-reproducibility win). Meta git 2.53 = slow/timeout (use stock) |
| **Network** | curl; python TCP loopback (incl. threaded); ephemeral port virtualized to 32768 — 4/4 PASS |
| **Python stdlib** | json, os.getpid/getuid (virtualized), sys.version, tempfile (random name deterministic), pathlib — 5/5 PASS (stock python3.9) |
| **Multithreaded** | C pthread, Go goroutines, OpenMP, Rust std::thread — PASS. **python threading FAIL@L2** (MT wall-clock divergence). Thread *scheduling* determinism is solid |
| **Language runtimes** | node, lua, perl, stock python3 — PASS. **java** (fixed via PR #222), **php** FAIL (hang), ruby N/A (host install broken) |
| **Record/Replay** | echo/ls/cat/grep/sort/wc, pthread+pipe, stock-python multiprocessing — byte-identical. find (replay panic), gcc/make -j4 (record timeout) |
| **DBI backend** | echo, C-hello, python3, curl, redis run under `--backend dbi`. Syscall-bound ~64× *faster* than ptrace; CPU-bound 1.5–2.5× slower. **Caveat: DBI is interception-only, `--strict` is a no-op there (partial determinism).** PR #215 ungates dispatch |

### Key determinism wins (host nondeterminism correctly sanitized)
sqlite3 `random()`/`datetime`, **git commit hashes**, python `tempfile` random names,
socket ephemeral ports, virtualized pid/uid/inodes, virtual epoch time, zip/ar
archive bytes — all reproducible run-to-run where they vary natively.

---

## 2. Final stress test (v2) — 100 runs, flake detection

10 apps × 10 runs, `--strict --verify` (origin/main db09337). Detail: `.`=pass `F`=fail.

| app | pass | fail | pattern |
|---|---|---|---|
| echo hello | 10 | 0 | `..........` |
| /usr/bin/git init | 10 | 0 | `..........` |
| **python3 (=fbpython)** | **1** | **9** | `FFFF.FFFFF` |
| sqlite3 SELECT | 10 | 0 | `..........` |
| **node -e** | **9** | **1** | `...F......` |
| lua -e | 10 | 0 | `..........` |
| curl --version | 10 | 0 | `..........` |
| bash -c echo hi | 10 | 0 | `..........` |
| sort (piped) | 10 | 0 | `..........` |
| grep pattern | 10 | 0 | `..........` |

**Totals: 90 PASS / 10 FAIL / 0 timeout — 10% overall flake.**

- **8/10 apps rock-solid (80/80, zero flakes).**
- Both failing apps are **multithreaded runtimes reading wall-clock time from worker
  threads**: fbpython (heavy MT startup + folly telemetry) and node (V8 background
  threads). Same root cause class as the MT-time gap below — **not** syscall coverage,
  and unrelated to the timer/archive/symlink paths (all clean).
- Stock `/usr/bin/python3.9` is deterministic; the PATH `python3`=fbpython is the outlier.

---

## 3. Fixes & PRs from this batch

| PR | Status | Summary |
|---|---|---|
| **#222** | **draft, verified** | **Java `LogicalTime` overflow fix (#219).** `detcore-model/src/time.rs` `Add<Duration>` now saturates both the u128→u64 nanos cast and the add. `java -version` goes from **hang → exit 0 in 0.9s** (openjdk 1.8.0_492). L0: `cargo test -p detcore-model` 17 pass (incl. new regression test), build/fmt/clippy clean. JVM `--strict --verify` tracked separately |
| **#221** | draft, WIP/reference | **vfork child-priority investigation.** child priority 1000→999 (POSIX vfork intent). **Does NOT fix gcc** — root cause is mid-compilation multi-process interleaving among the gcc driver + cc1/as/ld, not initial fork ordering. Regression-neutral (5/5 fork/exec apps still L2). Kept for reference, not for landing as a gcc fix |
| #215 | rebased, mergeable | Ungate `--backend dbi` dispatch |
| #214 | landed | Fix scheduler panic on SIGALRM re-arm after alarm fires |
| #207 | landed (208f574) | getcwd/getuid/getgid/getegid/statfs/fstatfs/timer/membarrier handlers — unblocked python stdlib + stock-git workflows under --strict |

---

## 4. Genuine open gaps (real, current)

1. **Multithreaded wall-clock time** — python threading / fbpython / node fail L2 via
   `gettimeofday`/`clock_gettime` sub-second divergence on worker threads.
   **Intermittent/load-sensitive** (could not repro in 33+ runs on a light host).
   Structural cause identified: the guest clock is a *global sum across all threads*,
   and clone-time inherited work is double-counted (child's DetTime not rebased at
   `lib.rs:909`), making the summed clock RCB-jitter-sensitive. Fix direction =
   RCB-attribution determinism (scheduler/PMU) and/or per-thread clock — **not** clock
   reshaping (the ~36ms gap defeats quantization). Needs a reliable heavy-load repro first.
2. **Multi-process + NSS socket lookups** — `tar czf` (getpwuid/getgrgid → nscd socket)
   nondeterministic syscall interleaving; scheduler-ordering gap, reproduced across 3 tasks.
   Same class as the gcc driver→cc1/as/ld interleaving from the vfork investigation.
3. **Record/replay of heavy/parallel workloads** — find (replay panic), gcc/make -j4
   (record timeout/jobserver), plus `hermit replay` execve-envp corruption post-#88.
4. **php** hangs under hermit (likely preemption single-step stall). **openssl speed**
   SIGALRM panic — may be fixed by #214, needs re-test.

## 5. Non-issues (documented so they aren't mis-filed as bugs)
`diff <(procsub)` (=/dev/fd isolation); ruby (host install broken, fails w/o hermit);
fbpython & Meta-git (heavyweight wrappers — use stock `/usr/bin/python3` and
`/usr/bin/git`); nginx -t (needs root); netcat (native `nc -l` hang); env determinism
depends on passed-in host env (expected).

---

## 6. Recommended next steps
1. **Review & land PR #222** (Java overflow) — clean, verified, self-contained win.
2. **Scheduler/virtual-time investigation** for the MT-time + multi-process interleaving
   gaps (gaps 1 & 2 share root cause). This is the highest-leverage remaining work and
   would unblock python-threading, tar, and gcc/rustc together. Needs a reliable
   heavy-load repro harness.
3. **Decide on PR #221** — keep as reference or close; it is not a gcc fix.
4. **Re-test openssl speed** now that #214 landed; investigate **php** hang.

---
*Aggregation only — no code changes in this task. Sources: impl-comprehensive-final-matrix,
impl-test-determinism-stress-v2, impl-attempt-vfork-priority-fix, impl-fix-java-overflow,
impl-push-vfork-branch (plus the ~25 per-category determinism tasks they aggregate).*
