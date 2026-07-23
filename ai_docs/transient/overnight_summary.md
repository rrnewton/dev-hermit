# Overnight Summary — Hermit Sprint (night of 2026-07-21 → morning 2026-07-22)

**Prepared:** 2026-07-22, for morning check-in.
**Scope:** Autonomous multi-agent sprint (~108 agent slots) on `rrnewton/hermit`,
`rrnewton/reverie`, and `rrnewton/dev-hermit`. This is a briefing synthesized from the
completed-task notes; every headline claim is traceable to a specific task (`tg <id> -v`).

---

## 0. TL;DR — read this first

- **Hermit's ptrace backend is in great shape.** 30 of 35 real programs run bit-for-bit
  deterministically under `--strict --verify`; record/replay is rock-solid (24/24 integration
  tests, 100/100 stress round-trips).
- **One thing needs your attention immediately:** `origin/main` @ `9c6b11d` is in a **broken
  intermediate state for `--backend dbi`** — PR #213 removed the fail-closed gate but the
  dispatch code isn't on main, so `hermit run --backend dbi` now errors with *"no dispatch
  implementation"*. **The fix is PR #215 (open, needs landing).** See §3 and §7.
- **Several finished fixes are sitting in draft/open PRs** waiting to land: #214 (openssl
  scheduler panic), #215 (DBI dispatch), #216 (JVM record/replay), #217 (this doc's sibling
  WHATS_WORKING.md), #218 (KVM wiring). See §1.
- **Two real, unresolved reliability defects** remain on ptrace: `python3 --strict --verify`
  is **flaky** (intermittent run-to-run divergence), and `git --version` **hangs** under
  `--strict` (CLONE_VFORK). See §6.

---

## 1. Pull requests

### Landed to `main` overnight
Current `origin/main` HEAD: **`9c6b11d`**. Notable commits merged during the sprint:

| PR / commit | What |
|-------------|------|
| #212 (`597d142`) | Bump reverie pin to main tip `6981ac0` (working DBI rev, ≥ 69f47d9) |
| #213 (`9c6b11d`) | Ungate `--backend dbi` when DynamoRIO SDK present — **gate removal only; see §3 caveat** |
| #210 (`15fb99f`) | End-to-end record/replay regression for real `sqlite3` binary |
| #209 (`fb2668a`) | Determinize `statfs`/`fstatfs` (canonicalize host-varying fields) |
| #88 (`f2cec1e`) | Harden record/replay failure handling |
| #83 (`c359e4b`) | Focused LevelDB `--strict` integration test |
| #206 (`dd284a3`) | Canonicalize `rcx`/`r11` on syscall return (defense-in-depth) |
| #207/#208 | Missing `--strict` syscalls: getuid/getcwd/statfs/timer_create/membarrier (landed earlier, squashed `208f574`) |
| `47f4261` | JVM record/replay: futex-timeout rebasing + statfs/flock (landed pre-sprint) |

**PR queue cleanup:** the backlog was cleared — 2 PRs merged (#209, #210), **57 conflicting
PRs closed** (all conflicted with the advanced main; work largely already landed via
#207/#208/#204 or superseded). 35 key-change PRs were labeled `human-review` +
`post-facto-review` before closing. All closes are reversible (reopen-if-needed comments left).

### Open PRs right now (6)

| PR | State | Labels | What | Action |
|----|-------|--------|------|--------|
| **#215** | open | human-review, post-facto-review | **DBI dispatch (`run_dbi`)** — unbreaks `--backend dbi` on main | **LAND (P0)** |
| #214 | draft | — | openssl SIGALRM scheduler-panic fix (`timed_waiters.rs`) | Review → land |
| #216 | draft | human-review, post-facto-review | JVM record/replay: mask futex flags + add `java` RR gate | Review → land |
| #217 | open | — | `WHATS_WORKING.md` (hands-on capability doc) | Review → land |
| #218 | draft | human-review | Wire hermit-cli → reverie-kvm (reaches backend, fail-closed exec) | Review → land |
| #211 | draft | — | `validate`: auto-apply `locally-validated` label on green run | Review → land |

Reverie side: **reverie PR #37** — minimal strace Tool running over the KVM `run_with_tool`
adapter (L0 green). reverie PR #35 (DBI child coordination) and #36 (rcx/r11) referenced.

---

## 2. Application compatibility matrix (`--strict --verify`, ptrace backend)

Aggregated from the testing tasks, all on `main` @ `15fb99f`, backend ptrace, no relaxations.
**35 distinct programs tested; 30 PASS bit-for-bit.** (Full copy-pasteable matrix lives in
`hermit/WHATS_WORKING.md`, PR #217.)

| Category | Result | Notes |
|----------|--------|-------|
| Single-threaded / coreutils / interpreters | **19/20 PASS** | true, echo, date, env, cat, ls, wc, head, sort, uniq, grep, sed, awk, tr, cut, seq, sha256sum, base64, gzip, tar, make, perl, sqlite3. Only fail: `openssl speed` (bug, now fixed in #214) |
| Filesystem | **4/5 PASS** | cat/ls/sha256sum/diff(regular) pass; `diff <(…) <(…)` fails on `/dev/fd/N` isolation (not a determinism bug) |
| Network | **4/4 PASS** | curl; python bind/getsockname (port virtualized → deterministic 32768); python TCP echo; **threaded** TCP echo |
| Multithreaded | **4/5 PASS at L2** | C pthreads, Go goroutines, OpenMP, Rust threads all bit-identical; python `threading` diverges on MT time (see §6) |
| Servers | **1/1 PASS** | **Redis** full workflow (server + SET/GET/SHUTDOWN) bit-for-bit deterministic — headline win |

**Reliability stress (20 apps × 5 runs):** 18/20 apps are 5/5 stable. The 2 exceptions —
`python3` (flaky, 3/5 then 0/6) and `git --version` (hangs, 0/5) — are the headline defects
in §6. **Takeaway: a one-shot "app passes" does not always hold under repetition.**

---

## 3. DBI backend (DynamoRIO)

**Functional parity achieved, but main is currently mis-wired.**

- **8/8 programs run correctly under `--backend dbi`** with byte-identical stdout and matching
  exit codes vs ptrace (echo, true, false, ls, perl, python3, sqlite3, sort). Verified with a
  real SDK at `/home/newton/dynamorio/build` + the native reverie-dbi client.
- **The execution path** (`run_dbi` in `backends.rs` + `run.rs` `Backend::Dbi` arm) shells to
  `drrun -disable_rseq -c $HERMIT_DBI_CLIENT -- <program> <args>`. Self-contained, no new crate
  deps. **This lives in PR #215, which is NOT yet landed.**
- **⚠️ CRITICAL: main @ `9c6b11d` is broken for DBI.** PR #213 removed the availability gate
  but the dispatch code never landed. So on real main, `hermit run --backend dbi` passes
  `ensure_available()` (with an SDK) and then hits `ensure_backend_dispatch()` →
  *"backend `dbi` has no Hermit dispatch implementation"*. It advertises DBI as available but
  errors on dispatch. **Landing #215 fixes this.**

**Honest caveats on DBI:** this is **functional-correctness parity, not determinism.** DBI
does not drive the Detcore scheduler; reverie-dbi branch-count telemetry varies run-to-run, so
DBI is *not* bit-deterministic. `--strict` is accepted but a **no-op** on the DBI path
(`run_dbi` ignores `DetConfig`). No L1/L2 claim for DBI.

---

## 4. KVM backend

**Progressed from "raw transport prototype" to "real Tool/Guest host at L0" — but still no
real Linux execution.**

- **reverie-kvm now has a Tool/Guest adapter** (`run_with_tool` + `KvmGuest: Guest<T>`, commit
  `f05752c`, on reverie main `6981ac0`). This corrects the earlier "zero reverie dependency"
  understanding. A real `reverie::Tool` can now be driven over KVM.
- **reverie PR #37** adds a working minimal **strace tool** over KVM (`reverie-kvm/tests/strace.rs`):
  `handle_syscall_event` decodes syscalls and records them; test green.
- **hermit-cli is now wired** (PR #218, draft): adds the `reverie-kvm` dep (hermit-cli only —
  detcore stays on abstract `reverie`), `run_kvm()` constructs a `KvmBackend` and **reaches KVM
  code**, then returns an honest error. `hermit run --backend kvm -- echo hello` exits 1 with:
  *"the KVM backend cannot run `echo`: … does not yet implement the Linux execution personality
  (ELF loader, virtual memory, guest-kernel ABI); see #198."*
- **The gap (tracked as #198):** real guest execution needs an ELF loader, protected/long mode,
  virtual memory, a guest-kernel ABI/Sentry bridge, `SyscallExecutor` wiring, and
  scheduling/timers. The current `SyscallExecutor` is a hermetic stand-in. Also note the
  hypercall return register is truncated to 32 bits (needs the frame return slot for full i64).

---

## 5. Record / replay

**Rock-solid on ptrace.**

- **Integration suite: 24/24 passing** (`cargo test -p hermit --test record_replay` →
  `24 passed; 0 failed; 0 ignored`, verified this sprint). Covers real curl/sqlite3 binaries,
  forked shell commands, directory walks, record-timeout behavior, and 15 Rust guest workloads.
- **Stress: 100/100** — 10 stock workloads (echo, ls, perl, python3, git, sqlite3, sort, tar,
  curl, bash) × 10 record→replay round-trips, zero divergences.
- **JVM (`java -version`) record/replay fixed** (PR #216, open): root cause was
  `parse_futex_timeout` comparing the **raw** futex op word against `FUTEX_WAIT_BITSET`, so
  glibc's `FUTEX_WAIT_BITSET|FUTEX_PRIVATE_FLAG` was misclassified as a relative timeout. Fix
  masks with `FUTEX_CMD_MASK`. Adds `java` as a CI record/replay gate. Pre-fix the JVM hung
  >240s; post-fix record 6s / replay 7s, replay matches.
- Node.js `ioctl(FIOCLEX)` panic and SQLite `Mmap` replay panic were addressed as part of the
  earlier record/replay hardening.

> **Note on the "14/14" target:** the sprint plan referenced "14/14". The *integration test
> suite* is larger than that and fully green at **24/24**; the JVM fix pushes the *real-binary
> record/replay workload matrix* to its complete set. Numbers above are what was actually
> measured this sprint.

> **Correction to prior notes:** an earlier note claimed `hermit replay` was broken post-#88
> (execve envp corruption). That regression is **resolved** on current main — verified round
> trips on echo/date and the 24/24 suite.

---

## 6. Root causes found

### 6a. vfork/clone+exec scheduling nondeterminism — blocks gcc/rustc (`research-vfork-scheduling-nondet`)
- **Root cause (smoking gun):** under `--strict`, a `vfork` parent is descheduled via a
  `BlockingExternalIO` resource whose scheduler path is **deliberately racy**
  (`scheduler.rs:1711-1740`: "*this INTENTIONALLY RACES … leans on an assumption of
  NON-INTERFERENCE*"). That non-interference assumption holds for ordinary background I/O but is
  **violated by process creation**: the vfork child **self-registers** into the run queue at a
  real-time-dependent point relative to concurrently-committed turns, so the committed turn
  order flips run-to-run → DETLOG diverges → `--verify` fails. Empirically, first divergence is
  exactly at `vfork()` (one run commits a polling turn, the other seeds the child).
- **Proposed fix:** model kernel vfork semantics deterministically — (a) *eagerly* have the
  parent tell the scheduler to expect a pending child, (b) keep the parent blocked until the
  child execs/exits (a deterministic event, not a racing poll), (c) schedule the child
  first. Minimal viable = (a)+(c). No child-first ordering knob exists today.

### 6b. openssl scheduler panic — FIXED (`impl-fix-openssl-scheduler-panic`, PR #214)
- **Root cause:** `pop_if_before` didn't drop the fired alarm's `alarm_times` entry, so a
  re-arm after fire hit an "internal invariant broken, entry missing" panic
  (`timed_waiters.rs:91`) → non-unwinding → SIGSEGV, triggered by `openssl speed`'s
  SIGALRM/`setitimer` loop.
- **Fix (PR #214):** `pop_if_before` drops the fired entry; `clear_old_alarm` tolerates an
  already-fired/missing entry. Unit tests (fire-then-rearm/cancel/replace) + minimal C repro
  pass; the SIGSEGV is gone (openssl is just slow under strict now).

### 6c. Clock / temp-file determinism — two findings, mild tension to reconcile
- **`research-clock-determinization`:** `CLOCK_MONOTONIC` is **already deterministic** under
  `--strict` (virtualized via detcore virtual time; vDSO patched to trap). A simple C clock
  program is byte-identical across runs and passes L2. **The gcc `--verify` blocker is NOT the
  clock** — it's an unsupported `unlink` syscall (fail-closed panic) plus other fs calls.
- **`impl-determinize-tempfile-names`:** for *gcc specifically*, glibc's `__gen_tempname`
  (mkstemp) seeds temp suffixes from `CLOCK_MONOTONIC` nanoseconds, and `--verify`'s **second**
  run carries a small deterministic **+30ns/read skew** vs the first, which glibc amplifies into
  different temp filenames → syscall-trace (not output) divergence. Proposed fix: make
  `--verify`'s two runs clock-identical (reset run2's virtual-clock epoch/accounting in
  `setup_double_run`), optionally quantize guest-visible clocks.
- **Reconciliation for the human:** both agree single-shot runs are reproducible and gcc's
  *output* is byte-identical; they differ on whether the residual `--verify` skew matters. Net:
  gcc needs (1) the missing fs syscalls (`unlink` first) **and** (2) the `--verify` double-run
  clock-identity fix. Worth a focused follow-up that treats these together.

### 6d. Multithreaded virtual-time divergence — NOT reproducible now (`impl-fix-mt-virtual-time-divergence`)
- **Mechanism:** a time read returns `epoch + Σ(all threads' RCB-derived work)`; under heavy MT
  load a thread's RCB tally at a *preemption* point (not a syscall boundary) can vary run-to-run,
  perturbing the observed sub-second time.
- **Outcome:** deliberately **no code change**. The divergence would **not reproduce** on
  current main across 33+ attempts (incl. the old reverie pin, 8× CPU load, 10× concurrent
  verifies). It's **intermittent / load-sensitive**, not an always-on bug. Shipping a
  speculative virtual-time change would risk regressing 30+ passing apps. **Recommendation: get
  a reliable repro (likely needs the heavily-loaded multi-agent env) before touching
  `time.rs`;** the real target is RCB-attribution determinism, not the guest clock shape.

---

## 7. Remaining issues (prioritized)

1. **[P0] main is broken for `--backend dbi`.** `9c6b11d` advertises DBI available but errors on
   dispatch. **Land PR #215.**
2. **[P1] `python3 --strict --verify` is flaky** — intermittent run-to-run divergence (3/5,
   then 0/6). Headline reliability defect; no root cause pinned yet. Related to the MT
   virtual-time class (§6d). Needs a reliable repro.
3. **[P1] `git --version` hangs under `--strict`** (0/5, also hangs without `--verify`).
   Consistent with the known CLONE_VFORK strict-mode hangs (§6a). **Always wrap runs in
   `timeout`.**
4. **[P1] gcc/rustc fail `--strict --verify`** — needs the vfork deterministic model (§6a) +
   missing fs syscalls (`unlink`…) + the `--verify` clock-identity fix (§6c).
5. **[P2] Finished fixes still unlanded:** #214 (openssl), #216 (JVM), #218 (KVM wiring), #211
   (validate label), #217 (docs). Review and land.
6. **DBI is not deterministic** (functional parity only; `--strict` is a no-op there) and **KVM
   cannot execute real Linux programs** yet (#198). Both are known architectural gaps, not
   regressions.

---

## 8. Performance (ptrace overhead, `--strict`, `impl-benchmark-hermit-overhead`)

Two regimes (min-of-N wall-clock, noisy 316-core host under load):
- **Startup/short tasks** (echo, git, bash, curl, sqlite3, perl, tar, ls): ~15–32 ms fixed
  floor (fork/exec + ptrace-attach + detcore init) → 4–12× ratios dominated by fixed cost.
- **Compute/syscall-heavy**: true steady-state overhead — **python3 ≈ 116×**, **sort ≈ 172×**
  (syscall interception + precise-preemption instruction accounting on tight loops).

Gotchas confirmed: hermit isolates guest `/tmp` (put inputs outside `/tmp`); `/usr/local/bin`
python3/git are Meta wrappers that hang/slow under strict — use stock `/usr/bin` binaries.

---

## 9. Recommended next steps (morning)

1. **Land PR #215** to unbreak `--backend dbi` on main (P0, 5-minute win).
2. **Review + land the finished fixes:** #214 (openssl), #216 (JVM record/replay), then #211,
   #217, #218. All are labeled for human review where they touch syscalls/backends/API.
3. **Chase the two ptrace reliability defects with real repros:** python3 `--verify` flakiness
   and the MT virtual-time divergence — both point at RCB-attribution determinism under load,
   likely only reproducible in the heavily-loaded multi-agent environment.
4. **Implement the deterministic vfork model** (§6a) — highest-leverage single fix; unblocks
   gcc/rustc/git and removes the CLONE_VFORK hang class.
5. **For gcc `--verify`:** open a focused task combining the missing fs syscalls (`unlink`…) with
   the `--verify` double-run clock-identity fix (§6c).
6. **Decide DBI/KVM investment:** DBI needs Detcore-scheduler integration to become
   deterministic (cross-repo executor work); KVM needs a Linux execution personality (#198).
   Both are large; prioritize per product goals.

---

*Sources: task notes for `research-vfork-scheduling-nondet`, `research-clock-determinization`,
`impl-fix-openssl-scheduler-panic`, `impl-fix-mt-virtual-time-divergence`,
`impl-determinize-tempfile-names`, `impl-dbi-expand-compat`, `impl-recreate-dbi-ungate`,
`impl-wire-kvm-hermit-cli`, `impl-kvm-strace-tool`, `impl-kvm-simple-tools`,
`impl-commit-jvm-rr-fix`, `impl-rr-stress-test`, `impl-test-determinism-stress`,
`impl-benchmark-hermit-overhead`, `impl-merge-all-open-prs-main`, `impl-comprehensive-app-matrix`,
`impl-create-whats-working-doc`. Read any with `tg <id> -v`.*
