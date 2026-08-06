# How a program dies: every backend is self-consistent, but only ptrace is faithful — and ptrace fails on SIGTRAP

**Task:** `exit-abnormal-termination-determinism` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress.

## Answer

**Determinism (run-to-run) mostly holds; fidelity does not.** 15 of 16 termination modes are stable at
N=3 within each backend, but the *wait status* diverges badly across backends:

* **DBI collapses death-by-signal into exit status `1`** in 7 of 8 signal cases. `WIFSIGNALED` would be
  **false** under DBI where it is **true** under ptrace and native.
* **DBI hangs** on `SIGILL` and on `exit()` from a thread.
* **SaBRe hangs** on `SIGFPE`, and reports the **wrong signal** for `SIGILL` (143/SIGTERM vs 132/SIGILL).
* **ptrace — the golden reference — mistranslates `SIGTRAP`** (100 vs native 133).
* **One true nondeterminism:** SaBRe's `exit()`-from-a-thread returns **1 or 7 across 3 runs**.

## The matrix (N=3 per cell; value shown when all 3 agreed)

| mode | native | ptrace | dbi | sabre |
| --- | ---: | ---: | ---: | ---: |
| `exit0` / `exit42` / `exit(256)`→0 / `exit(-1)`→255 / `_exit(9)` / atexit×2 | 0/42/0/255/9/3 | **same** | **same** | **same** |
| `abort` | 134 | 134 | **1** | 134 |
| `segv` | 139 | 139 | **1** | 139 |
| `fpe` | 136 | 136 | **1** | **124 (HANG)** |
| `ill` (`__builtin_trap`) | 132 | 132 | **124 (HANG)** | **143** |
| `raise(SIGTERM)` | 143 | 143 | **1** | 143 |
| `raise(SIGKILL)` | 137 | 137 | **1** | 137 |
| `raise(SIGTRAP)` | 133 | **100** | **1** | 133 |
| stack overflow | 139 | 139 | **1** | 139 |
| `exit()` from a thread | 7 | 7 | **124 (HANG)** | **1 or 7 — UNSTABLE** |
| create+join then exit | 5 | 5 | 5 | 5 |

`124` is my 40 s `timeout`'s own exit code, i.e. the run never terminated.

### Normal exits are clean — including the subtle ones

All six normal-exit modes agree everywhere, including the truncation cases (`exit(256)` → 0,
`exit(-1)` → 255) and two `atexit` handlers. So the exit *path* is right; it is the *abnormal* path
that isn't.

### DBI: signalled death becomes a normal exit

Seven modes that die by signal natively (134/139/136/143/137/133/139) all report **`1`** under DBI.
A guest's parent doing `waitpid` + `WIFSIGNALED`/`WTERMSIG` — a shell, a test harness, a supervisor —
sees "exited normally with status 1" instead of "killed by SIGSEGV". That is a guest-observable
behavioural divergence, not a cosmetic one.

### ptrace's own SIGTRAP gap is worth its own line

`raise(SIGTRAP)` is 133 natively and **100 under ptrace**. SIGTRAP is the ptrace mechanism's own
signal, so a guest raising it collides with the tracer. **This is the reference backend being
unfaithful**, which matters disproportionately: every other backend is ratcheted against ptrace, so a
ptrace gap propagates as a false target. (Compare the earlier finding that ptrace is not a clean L3
oracle for multithreaded stack content.)

### The only genuine determinism failure

SaBRe `thread_exit` gives **1 on some runs and 7 on others** at N=3. Everything else in this sweep is
self-consistent — the pervasive problem is fidelity across backends, not instability within one.

## Rigor — "swept" is not "covered"

Full statement in `rigor.txt`. Summary:

* **n = 3** runs per (mode × backend); 16 modes × 3 backends = **48 cells, 144 hermit runs**, plus 16
  native baselines.
* **1 host** (devbig014), one binary, one build, one kernel. No cross-host replication.
* **KVM skipped entirely** — livelocks at guest startup on this host.
* **A negative at N=3 is weak.** It caught SaBRe's instability, but the one-sided 95% upper bound on a
  flake rate after 0/3 clean runs is **~63%**. "Stable at N=3" must not be quoted as "deterministic".
* **Sub-cases not tested**, each a real gap: **WCOREDUMP / core-dump flag** (the task lists it; `$?`
  encodes the signal but not the core bit — **unmeasured**); **DETLOG comparison** (the task asks for
  identical wait-status *and* identical detlog — I asserted **wait-status only**, and detlog parity is
  separately blocked because DBI ignores `--log-file`); process-tree exit ordering; C++ destructor
  ordering; SIGBUS/SIGQUIT/SIGPIPE; `exit()` from inside a signal handler; and behaviour under
  `--verify`, whose `--verify-allow` defaults to `success` and may interact with a signalled guest.

## Provenance (#268)

`worktrees/oci/hermit/target/release/hermit`, built 2026-08-06 04:30, `--features
third-party-backends`. Guest `~/.local/hermit-deps/guests/guest_exit` (`gcc -O1 -pthread`; source
committed as `guest_exit.c`). Flags `--strict --no-virtualize-cpuid --max-timeslice=disabled`.
`LD_LIBRARY_PATH=~/.local/hermit-deps/lu/usr/lib64`. Raw second-half output in `raw-sweep-part2.txt`.
No code changed.
