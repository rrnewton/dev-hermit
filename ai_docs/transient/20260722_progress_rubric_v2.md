# Hermit Progress Rubric v2

Status date: 2026-07-22 (live snapshot around 13:30 PDT / 20:30 UTC).

This revision replaces every vague qualifier ("present together",
"demonstrated", "audited", "useful") with an explicit **assurance level**, the
**backend** it was measured on, the **`--log` verbosity** in effect, and any
**relaxations** applied. A result is reported only at the strongest level it
actually reaches, and any relaxation that voids a level is named.

## Assurance-level ladder (the only definition of "passing")

Each level is a literal command. A test's grade is the highest level whose exact
command exits 0 for that test, with no voiding relaxation applied.

| Level | Exact command | What it proves | What it does NOT prove |
| --- | --- | --- | --- |
| **L0** | `cargo test <target>` (or `cargo nextest run`) exit 0 | The code compiles and the guest/unit test runs to a passing assertion under whatever mode the harness sets. | Nothing about determinism unless the harness itself invokes a strict Hermit run. |
| **L1** | `hermit run --strict -- <prog>` exit 0 | Single deterministic execution in which any unsupported syscall panics (fail-closed) instead of passing through to the host kernel. | Reproducibility: one run is not compared against a second. |
| **L2** | `hermit run --strict --verify -- <prog>` exit 0 | Bitwise-reproducible observable behavior: two back-to-back strict runs produce identical stdout bytes, stderr bytes, exit status, and identical internal scheduler-step log. **This is the definition of "PASSING" (memory #44).** | Deep memory determinism: heap/stack contents are not hashed by default. |
| **L3** | `hermit run --strict --verify --detlog-heap --detlog-stack -- <prog>` exit 0 | Everything in L2, plus per-run content hashes of heap and stack memory maps written into the deterministic log and compared across the two runs. | Freedom from flakiness under load: a single L3 pass can still be nondeterministic across many trials. |
| **L4** | The L2 or L3 command run 20 times, 20/20 exit 0 (state which of L2/L3) | Stability: the reproducibility guarantee holds under repetition/load, not by luck on one trial. | Anything about backends or modes other than the exact one measured. |

### Relaxations that VOID a level (memory #19)

A run that uses any of the following does **not** count as passing at L1 or
above, regardless of exit code. Report such a run explicitly as "relaxed" and at
**L0 only**:

- omitting `--strict` (default/permissive policy), or the minimally-invasive
  bundle `--no-strict`/`--no-deterministic-io`/`--no-virtualize-*`;
- `--no-sequentialize-threads` (removes deterministic single-CPU thread
  ordering);
- `--no-rcb-time` when the workload's determinism depends on RCB preemption;
- `--verify-allow failure`/`both` used to mask a nonzero guest exit.

### Log-verbosity convention

Unless a row states otherwise, every measurement below was taken at the default
human-facing verbosity **`--log=info`**. The pass/fail signal at **L2/L3 does
not come from `--log` output**: `--verify` compares Hermit's internal
scheduler-step deterministic log (and, at L3, the heap/stack hashes in that
log), which is independent of `-l/--log`. `--log=debug`/`--log=trace` are used
only for human diagnosis and change no verdict. Where a count was produced by a
Cargo/nextest harness rather than a direct `hermit run`, the effective level is
whatever mode that harness sets; those rows say so.

## Executive rubric

Grades below are annotated with the highest assurance level actually reached on
the named backend. "Not measured at Lx" means the command for Lx was never run
green for that item; it is not a claim of failure.

| Frontier | Backend | Highest level reached | Exact status | Blocker to next level |
| --- | --- | --- | --- | --- |
| `hermit run` | ptrace | **L2 on the applicable strict set; not L4** | 69/89 applicable non-ignored integration cases exit 0 under strict policy (77.5%); 20 catalogued strict failures. L2 established per-case by the strict integration harness at `--log=info`. No 20x (L4) stress campaign has been run on the full set. | Close the 20 strict failures; run the L4 20x campaign; current `main` HEAD is not CI-green. |
| Explicit strict / fail-closed | ptrace | **L1 measured; L2 for the passing subset** | 69/89 reach L1 (unsupported syscalls panic). Optimized Detcore subscribes to selected syscalls only; the coverage audit records 291 release subscription entries with no active subscription, so an unsubscribed syscall never traps and cannot trigger the panic path. | First-blocking syscalls: `ioctl` (7), `tgkill` (4), `mkdir` (3), `setitimer` (2), one each `clock_settime`, `getrlimit`, `kill`, `setsockopt`. Subscribe at coverage level, not only on events that reach Detcore. |
| DBI / DynamoRIO | dbi | **L0 only** | Best branch (draft PR #126) reports 81/89 guest-execution cases exit 0 under `cargo test` at whatever mode the harness sets — i.e. L0. Not run at L1+: PR #138 makes `--strict --backend dbi` an explicit error because DBI has no deterministic scheduler, so L1 is currently unreachable for DBI. record/replay cases in that run used ptrace, not DBI. | Land a non-conflicting Hermit DBI dispatcher and a deterministic scheduler; only then is L1 definable for DBI. |
| KVM | kvm | **Below L0** | The KVM backend does not execute the requested ELF; it runs a built-in real-mode hello guest and services one `write(2)` via `vmcall`. PR #117 cross-backend tracking: 1/10 smoke cases. No `hermit run --strict` semantics exist for KVM. | Boot a protected/long-mode ELF entry, implement the guest ABI/lifecycle, connect to Detcore. |
| Record/replay | ptrace | **L0 for generated tests; branch-only L2-class for workloads** | 17 generated record/replay tests exist on `main` at L0 (`cargo test`). Draft audit PR #124 measured 13/16 application workloads byte-identical (an L2-class stdout/stderr/exit comparison, on a branch, not on `main`). No 16/16 result is landed or integrated. | Integrate the Node/JVM/SQLite fixes onto `main`; re-run the 16-workload L2 comparison in CI. |
| `run --verify` | ptrace | **Mechanism for L2/L3; heap/stack are opt-in** | `--verify` compares stdout, stderr, exit status, and the normalized internal scheduler-step log (L2). Heap/stack hashing (L3) requires `--detlog-heap`/`--detlog-stack` and is off by default. | Nothing to promote in the mechanism; L3 must be requested explicitly per run. |
| Debugging (GDB) | ptrace | **L0 (functional), not a determinism level** | GDB RSP replay was exercised for record, replay, breakpoints, backtrace, args/vars, finish, and continue in a local run (functional, not a Lx determinism claim). Live `--gdbserver` transport is namespace-hidden; finish→continue can panic; LLDB/MCP are not landed. | Land the finish fix, make transport reachable, add green LLDB packet tests. |

The strongest product today is the ptrace path at **L2 on its passing set**. DBI
reaches only **L0** and cannot currently be defined at L1 (no deterministic
scheduler). KVM is **below L0** (does not run the requested ELF).

## Exact ref and inventory snapshot

| Ref | SHA | Meaning |
| --- | --- | --- |
| Hermit `main` | `5c5c73f662dfaa15c5e256b1c9c7ce4ff861b6e8` | Current landed product; PR #129 is the tip. |
| Hermit `frontier` | `344200e45423bed3050f0cabf7192b82b95a2a6c` | 66 commits ahead, one main commit behind; merge base `4cf3282`. Red CI; not a release candidate. |
| Reverie `main` | `e3e2c965e24b2a2287bac8b520caf7cd1b020d94` | DBI parity implementation, vendored DynamoRIO, expanded KVM mechanism. CI green. |

The Hermit frontier changes 100 files vs its merge base (42 added, 58 modified;
10,693 insertions, 1,124 deletions). "On the frontier branch" means the code is
merged into that branch; it does **not** imply any assurance level, because
frontier CI is red. Do not read frontier code aggregation as an Lx result.

GitHub inventory at the snapshot: 47 merged Hermit PRs; 70 open (64 draft, 6
non-draft); 17 open PRs carry `human-review`; 26 open Hermit issues. The six
non-draft open PRs were #139 (explicitly do not merge), #134, #107, #106,
stacked #102, and human-review #80.

## Test accounting

Denominators remain separate. Each row states the level the count corresponds
to. A raw `cargo nextest list` count is an **inventory** (enumeration), not any
Lx pass count.

| Ref / mode | Result | Level | Interpretation |
| --- | --- | --- | --- |
| Main, Cargo inventory | 337 test functions in 40 suites; 16 `#[ignore]` | Inventory (no level) | `cargo nextest list`; PR #129 added no test declarations. |
| Main, Hermit crate only | 162 test functions | Inventory | Not all launch a guest. |
| Main, fail-closed applicable set | 69 pass / 20 fail / 11 ignored; 61 mode-N/A | **L1 per passing case; L2 where the harness runs verify** | 69/89 = 77.5% of applicable non-ignored cases; the best strict-policy metric. Measured at `--log=info`. |
| Frontier, Cargo inventory | 602 functions in 43 suites; 0 ignored in inventory | Inventory (no level) | Frontier CI is red; 602 is not a pass count at any level. |
| Frontier, Hermit crate only | 411 functions | Inventory | Increase dominated by rr + integration-stack additions. |
| Frontier rr suite | 214 functions | Inventory | 213 enabled rr program cases + one scratch-dir harness invariant. |
| Record/replay | 17 on main; 22 on frontier | **L0** (`cargo test`) | Generated coverage, not the 16-workload L2 application comparison. |

`cargo test --workspace --no-run` on the frontier produced 43 executables. That
is a build result, not an Lx pass. The live frontier Actions run fails in
Detcore before producing an aggregate pass count.

### validate.sh: exactly what it measures, and at which level

`hermit/validate.sh` is the local gate. Its Hermit smoke invocations share
`HERMIT_RUN_ARGS = run --base-env=minimal --no-virtualize-cpuid
--preemption-timeout=disabled`. **These args do NOT include `--strict`.**
Consequences, stated precisely:

| validate.sh check | Command essence | Level | Notes |
| --- | --- | --- | --- |
| `cargo-nextest available` | `cargo nextest show-config version` | Tooling | Not a test. |
| `Build workspace` | `cargo build --workspace` | Build | Not a test. |
| `Test workspace and integrations` | `cargo nextest run --workspace --exclude detcore --exclude ...flaky` | **L0** | Level is whatever each test's harness sets; the wrapper adds no `--strict`. |
| `Test detcore package` | `cargo test -p detcore` | **L0** | Includes PMU/CPUID-sensitive cases. |
| `Fast concurrency stress suite` | `cargo nextest run -p hermit --test stress_suite -E test(=fast_chaos_matrix)` | **L0** (chaos harness) | Chaos, not `--strict --verify`. |
| `Hermit analyze scenarios` / `Schedule search E2E` | `cargo test -p hermit --test analyze`; `hermit_analyze_e2e.sh` | **L0** (requires PMU) | Analyze search, not a determinism verdict. |
| `rr syscall suite` | `cargo test -p hermit --test rr_suite` | **L0** | rr guest programs under Hermit. |
| `Hermit run smoke test` | `hermit run <relaxed args> -- /bin/echo` | **L0, relaxed** | No `--strict`; just checks stdout equals the marker. |
| `Hermit output determinism` | two relaxed runs of `/bin/echo`, stdout diffed | **Does NOT reach L2 (relaxed)** | No `--strict`, no `--verify`; only stdout of one command compared. Per memory #19 this is L0-relaxed, not a determinism pass. |
| `Hermit verify-mode smoke test` | `hermit run <relaxed args> --verify -- /bin/echo` | **Does NOT reach L2 (relaxed)** | Uses `--verify` but **without `--strict`**, so it is void at L1+ per memory #19. Report as L0-relaxed verify smoke. |

Takeaway: a green `validate.sh` establishes **L0** across the workspace plus two
relaxed smoke runs. It does **not** by itself establish L1, L2, L3, or L4 for
any guest, because it never passes `--strict`.

## Frontier evaluations

### 1. Ptrace `hermit run`

Only Hermit backend on `main`. Reverie owns ptrace/seccomp/process
lifecycle/registers/memory/syscall injection/GDB RSP; Detcore supplies
deterministic policy and scheduling.

Level status (backend = ptrace, `--log=info`):

- **L1:** reached by the 69 passing fail-closed cases (unsupported syscalls
  panic). 20 cases fail at L1; 291 release subscription entries are inactive, so
  unsubscribed syscalls never trap and silently escape the L1 guarantee.
- **L2:** reached by the strict-verify integration cases that run `--verify`;
  the 69/89 count is the current L2/L1 passing set at `--log=info`.
- **L3:** reached only where a test explicitly enables `--detlog-heap
  --detlog-stack`; not the default and not aggregated here.
- **L4:** not established for the full set. No 20/20 stress campaign at L2/L3 has
  been recorded for the whole integration matrix; per-test chaos exists but is
  not the L4 command.

Risks: current `main` HEAD CI was queued and recent self-hosted lanes ended red;
open issues cover vfork, polling, blocking connect, pipeline replay, QEMU, and
toolchain compatibility.

Promotion to L4: green CI in both lanes, enforce strict coverage at subscription
level, then run the 20x L2/L3 campaign on the passing set.

### 2. DBI (`--backend dbi`)

Backend = dbi. Reverie main contains the DynamoRIO prototype (#13), CPUID
rewriting (#15), deterministic metadata/clocks/resource changes (#19), and a
source-built DynamoRIO submodule (#18). Hermit `main` has no backend selector;
the frontier shells out to `drrun` + a native client.

Level status:

- **L0:** draft PR #126 reports 81/89 guest-execution cases exit 0 under
  `cargo test` (full Cargo output 156 pass / 0 fail / 11 ignored). This is L0
  only. The `156/156` headline is not parity: adversarial review found the eight
  DBI xfails returned before executing their bodies. Draft stacked PR #138 makes
  them execute under `catch_unwind` and fail on XPASS, but has no CI result.
  Defensible statement: **81 guest cases reach L0; 8 capability gaps unresolved.**
- **L1+:** currently undefined for DBI. PR #138 makes `--strict --backend dbi`
  an explicit error because DBI lacks Detcore's runnable-thread ownership and
  deterministic scheduler. Until a scheduler exists, DBI cannot be measured at
  L1, L2, L3, or L4.

Promotion: land a non-conflicting Hermit DBI dispatcher and deterministic
scheduler; run the corrected xfail harness in CI; then L1 becomes definable.

### 3. KVM (`--backend kvm`)

Backend = kvm. Reverie main has shared backend API (#11), CPUID filtering (#12),
expanded syscall interception (#8) — real KVM code.

Level status: **below L0.** The Hermit frontier does not execute the requested
ELF; it creates a built-in real-mode guest, stages `hello world`, exits via
`vmcall`, decodes one `write(2)`, and performs the host write. The CLI warns the
supplied program is not executed. PR #117 records KVM at 1/10 smoke cases. Draft
Reverie PR #16 attempts static ELF/protected-mode bootstrap but is conflicting
with failed self-hosted CI. There is no Linux process model, loader, arbitrary
ELF execution, signals, threads, filesystems, or Detcore scheduling, so no Lx
level applies.

Promotion: boot a protected/long-mode ELF entry, implement the guest ABI and
lifecycle, connect the shared `Tool`/`Guest` contract to Detcore, add a real
backend matrix.

### 4. Record/replay

Backend = ptrace. `main` defines 17 generated record/replay tests (heap and
stack pointer workloads included); frontier adds five timeout/curl/hardening
cases for 22 functions.

Level status:

- **L0:** the 17 generated tests on `main` pass under `cargo test`.
- **L2-class, branch-only:** draft audit PR #124 measured 13/16 workloads
  byte-identical (stdout/stderr/exit comparison). Failures: JVM timeout, Node
  `FIOCLEX`, SQLite mmap/event alignment. Fix branches: #128 (SQLite
  recvmsg/SCM_RIGHTS + mmap replay), #130 (JVM futex timeout), #132 (Node
  scheduler/FIONBIO/epoll + a branch-local 16/16 claim). PR #132 has no Actions
  result and is conflicting. **The 16/16 statement is branch evidence, not a
  landed or integrated L2 result.**

Promotion: integrate the fixes onto `main`, re-run the 16-workload L2 comparison
and the 17 generated tests in both CI lanes.

### 5. `run --verify`

Backend = ptrace. `--verify` runs the command twice under identical Hermit
configuration and compares stdout bytes, stderr bytes, exit status, and the
normalized internal scheduler-step log (`logdiff`). This mechanism is what
implements the L2 comparison; with `--strict` it is exactly the L2 command.

Precise scope:

- Heap/stack **memory hashing is L3, opt-in only** via `--detlog-heap` and
  `--detlog-stack`. Plain `--verify` does not enable either, so it must not be
  described as always hashing memory. Heap/stack pointer workloads still print
  pointers, so ordinary L2 output comparison catches address drift in stdout —
  but that is not the same as the L3 map-hash comparison.
- `--verify` checks only observations Hermit captures; it makes no idempotency
  claim about arbitrary external side effects.
- Logs are normalized/stripped before comparison.
- Draft PR #148 exists because the double-run setup overrides the user's
  `--log-file` instead of honoring it (UX, not a level change).

### 6. Debugging (GDB, LLDB, MCP)

Backend = ptrace. This is a functional axis, not an Lx determinism axis; grade
it as works/does-not-work, not L0–L4.

- **Works (functional):** GDB RSP replay for record, replay, breakpoints,
  backtrace, args/variables, finish, and continue, exercised in a local run.
- **Does not work / not landed:** live `hermit run --gdbserver` listens inside
  the guest network namespace and is unreachable from the host (draft #144
  attempts a fix; self-hosted check failed); a resume after `finish` can panic
  when the stub expects `StepOver` but receives `Continue`; LLDB connects but
  does not reach a usable stop (Reverie draft #21 adds packets, stacked on the
  unlanded finish fix); Hermit draft #147 adds GDB/LLDB Python tests but
  self-hosted CI failed; MCP PoC PR #146 was closed, not merged (regular CI
  passed, self-hosted failed) — it is a closed experiment, not product support;
  reverse step/continue is not advertised by the RSP server.

## Main versus frontier

| Capability | Main | Frontier | Level note |
| --- | --- | --- | --- |
| Ptrace | Product backend | Product backend + review-stack changes | Main: L2 on passing set. Frontier: unmeasured (red CI). |
| Strict/fail-closed | 69/89 (L1/L2) | Stale embedded status says 3/89; must be rerun | Frontier count is not current; do not cite. |
| rr | No Cargo rr suite | 213 enabled rr cases + harness invariant | Inventory only. |
| DBI | No selector | Selector + DynamoRIO launch path | Frontier DBI is L0 at best. |
| KVM | No selector | Built-in hello/vmcall demo | Below L0. |
| Record/replay | 17 functions | 22 functions + hardening | L0; 16-workload L2 is branch-only. |
| Cargo inventory | 337 / 40 | 602 / 43 | Inventory, not pass counts. |
| CI | Tip queued | Detcore failure; self-hosted at PMU memory | Neither is green. |

The frontier's `docs/FAIL_CLOSED_STATUS.md` still reports 3/89 while `main` is
at 69/89 — direct evidence that frontier status docs must be regenerated after
rebasing. Raw code aggregation on frontier is not an Lx metric.

## CI and runner status

### Hermit

At the snapshot: current main run `29955251128` (PR #129 merge) queued; the PR
run also queued; frontier PR run `29954326191` had a failed GitHub-hosted job at
"Test Detcore without hardware-dependent integration tests"; its self-hosted job
was still in "PMU parallel memory tests"; recent main runs for #119 and #125
completed failure on the long/self-hosted lane. Two lanes exist: regular
GitHub-hosted tests and trusted self-hosted host/PMU/namespace tests. The single
self-hosted runner is a throughput bottleneck; queued/cancelled results are not
green and must not be reported as passing at any level.

### Reverie

Reverie main at `e3e2c965` is green. Latest main runs for vendored DynamoRIO
(#18), expanded KVM interception (#8), DBI parity implementation (#19), and CI
repair (#17) all completed successfully. Reverie has a cleaner mechanism
baseline than Hermit's current integration state.

## Open issues (26)

| Area | Issues |
| --- | --- |
| rr known gaps | #112 signal-mask handler, #113 priority spinlock, #114 rusage `ru_maxrss`, #115 SIGCHLD interrupt, #116 pending-signal flake |
| Record/replay fidelity | #17 FIOCLEX, #19 pipeline desync/leaked children, #22 ppoll/vectored outputs, #23 recvmsg ancillary FDs, #31 Go/SQLite filesystem events, #94 unused syscall registers |
| Syscall/network/scheduling | #18 blocking connect, #20 sched_yield starvation, #21 PMU wrapper false skip, #24/#26 ppoll design/tracking |
| Toolchain/process lifecycle | #15 CLONE_VFORK hang, #16 rustup proxy EBADF |
| QEMU / VM workloads | #5 TCG performance, #6 guest clock calibration, #9 virtualized uname, #10 vng CLONE_VFORK |
| Tests/docs/config | #11 deployment expectations, #12 strict option mismatch, #13 verify tmp/env, #14 noisy PMU traces |

Issues #12, #13, #17, #94 appear covered by newer code/tests but remain open;
verify against the exact Lx command and close or rewrite.

## Recommended promotion order

1. **Restore a green Hermit `main`** in both CI lanes; do not merge around
   queued/failed self-hosted checks; diagnose the PMU-memory hang.
2. **Regenerate frontier evidence:** bring #129 into frontier, rerun the strict
   integration set (L1/L2), Cargo inventory, rr, record/replay, and backend
   matrices; update stale status docs from generated results only.
3. **Grade DBI honestly at L0:** rebase #126, include #138's corrected xfail
   logic, publish "81 L0 + 8 gaps", and keep `--strict --backend dbi` an error
   until a deterministic scheduler exists.
4. **Keep KVM scoped as research** (below L0) until it executes an actual ELF and
   drives Detcore.
5. **Integrate record/replay fixes** and re-run the 16-workload L2 comparison on
   `main` in both runners.
6. **Stabilize debugging bottom-up:** finish/continue fix, reachable transport,
   green LLDB packet tests, then MCP.
7. **Run L4 campaigns** (20x at L2/L3) on the ptrace passing set once `main` is
   green, and record the exact command per suite.

## Reproduction notes

Level-defining commands (backend defaults to ptrace; add `--backend dbi/kvm` to
grade those):

```bash
# L0
cargo test --workspace                       # or: cargo nextest run --workspace
# L1
target/debug/hermit run --strict -- <prog>
# L2 (= "PASSING", memory #44)
target/debug/hermit run --strict --verify -- <prog>
# L3
target/debug/hermit run --strict --verify --detlog-heap --detlog-stack -- <prog>
# L4: run the L2 or L3 command 20 times, require 20/20 exit 0.
```

Inventory / read-only evidence (not pass counts):

```bash
with-proxy gh pr list -R rrnewton/hermit --state open --limit 300
with-proxy gh issue list -R rrnewton/hermit --state open --limit 300
with-proxy gh run list -R rrnewton/hermit --limit 30
with-proxy gh run list -R rrnewton/reverie --limit 30
with-proxy git ls-remote https://github.com/rrnewton/hermit.git \
  refs/heads/main refs/heads/frontier
cargo nextest list --workspace --message-format json   # inventory only
git rev-list --left-right --count origin/main...origin/frontier
```

Voiding relaxations to reject when reading any run's logs (memory #19): missing
`--strict`, `--no-strict`, `--no-sequentialize-threads`, `--no-deterministic-io`,
`--no-virtualize-*` on a determinism claim, or `--verify-allow failure|both`
masking a nonzero exit. Any such run is L0-relaxed, never L1+. No open-head or
frontier code-aggregation figure is a combined release claim at any level.
