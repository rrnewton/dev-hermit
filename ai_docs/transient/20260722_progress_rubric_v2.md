# Hermit Progress Rubric v2

Status date: 2026-07-22 (live snapshot around 13:30 PDT / 20:30 UTC).

This rubric reports results in **three top-level sections, in priority order**:

1. **[FRONTIER RESULTS](#1-frontier-results-primary)** — the leading-edge
   `frontier` branch. This is the primary subject and the bulk of the document.
2. **[MAIN RESULTS](#2-main-results-how-far-behind)** — the landed `main`
   branch: where the only trustworthy *measured* passes live today, and how far
   behind frontier it is in capability.
3. **[TENTATIVE RESULTS](#3-tentative-results-open-prs--untrustworthy)** —
   numbers that exist only in open/draft PRs. **These are untrustworthy** and
   must never be cited as achievements.

Every number below states **which branch** it was observed on. A grade names
three things or it is not a grade: the **assurance level**, the **branch**, and
the **backend**.

> ## ⚠️ Frontier CI is RED — this is unacceptable and gates everything
>
> The `frontier` branch does not have green CI in either lane (GitHub-hosted
> Detcore job fails; self-hosted lane hangs in PMU memory tests). **A leading-edge
> branch with red CI cannot report a single measured assurance level.** Almost
> everything in the FRONTIER RESULTS section is therefore *code-on-branch*
> evidence (the code exists), not a measured pass. Restoring green frontier CI is
> priority #1; until then, no frontier number above L0 is real.

## Definitions used by all three sections

### The three types of evidence (apply this to every claim)

Every number in this document is exactly one of three kinds. Confusing them is
the error this rubric exists to prevent, so each is labeled with its type **and**
its branch.

1. **Inventory** — an enumeration or raw count (`cargo nextest list`, "602 test
   functions", "100 files changed"). Proves code *exists* on a branch. Proves
   **nothing** about behavior and is never an Lx pass.
2. **Measured pass** — a specific command exited 0 at a stated **Lx** on a stated
   **branch** and **backend**. The only evidence type that earns a grade.
3. **Code-on-branch (aggregation)** — a feature's code is merged into a named
   branch. Proves *presence*, not correctness. Because frontier CI is red, nearly
   all frontier evidence is this type; because open-PR code is unmerged and
   unvalidated, all TENTATIVE evidence is this type too.

### Assurance-level ladder (the only definition of "passing")

Branch- and backend-agnostic. Each level is a literal command; a test's grade is
the highest level whose exact command exits 0 for that test on the branch/backend
being reported, with no voiding relaxation applied.

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

### Exact refs

| Ref | SHA | Role |
| --- | --- | --- |
| Hermit `frontier` | `344200e45423bed3050f0cabf7192b82b95a2a6c` | **Primary subject (Section 1).** 66 commits ahead of merge base, one `main` commit behind; merge base `4cf3282`. **CI red.** |
| Hermit `main` | `5c5c73f662dfaa15c5e256b1c9c7ce4ff861b6e8` | Landed baseline (Section 2). PR #129 is the tip; holds the only measured passes. |
| Reverie `main` | `e3e2c965e24b2a2287bac8b520caf7cd1b020d94` | Mechanism dependency. DBI parity impl, vendored DynamoRIO, expanded KVM mechanism. CI green. |

---

## 1. FRONTIER RESULTS (primary)

**Branch: `frontier` @ `344200e`. CI: RED in both lanes (see callout above).**

Because frontier CI is red, this entire section is **code-on-branch (type 3)
evidence** except where a row explicitly says "measured". Frontier changes 100
files vs its merge base (42 added, 58 modified; 10,693 insertions, 1,124
deletions) — **inventory**, not a grade. "On frontier" means the code is merged
into that branch; it does **not** imply any assurance level.

### 1a. Frontier capability grades

| Capability | Backend | Frontier grade | Evidence type (frontier) | Basis / blocker |
| --- | --- | --- | --- | --- |
| `hermit run` (core) | ptrace | **Unmeasured (red CI)** | Code-on-branch | Product backend + review-stack changes are merged, but frontier CI never reaches an aggregate pass count. No Lx can be reported for frontier ptrace. The measured L2 for this backend exists only on `main` (Section 2). |
| Strict / fail-closed | ptrace | **Not currently measured on frontier** | Code-on-branch (+ stale doc) | Frontier's embedded `docs/FAIL_CLOSED_STATUS.md` says **3/89** — stale and wrong; do not cite. Must be re-measured after rebasing #129. `main`'s 69/89 (Section 2) is the number that is real today. 291 release subscription entries are inactive, so unsubscribed syscalls never trap and silently escape the L1 guarantee. |
| DBI / DynamoRIO | dbi | **Code present; L0 only (via open PR — see §3)** | Code-on-branch | The DBI selector + `drrun`/native-client launch path are on frontier. The 81/89 L0 figure comes from open draft PR #126 and is **tentative (Section 3)**. `--strict --backend dbi` is (per draft #138) an explicit error; L1+ is undefined for DBI on any branch. |
| KVM | kvm | **Below L0** | Code-on-branch | Frontier does not execute the requested ELF; it runs a built-in real-mode hello guest and services one `write(2)` via `vmcall`. No `--strict` semantics exist. |
| Record/replay | ptrace | **22 generated functions at L0 (harness)** | Inventory + L0 harness | Frontier has 22 generated rr functions (the 17 on `main` + 5 timeout/curl/hardening). They are L0 by harness, but frontier's aggregate CI is red so they are not confirmed green in CI. The 16-workload L2-class application result is **tentative (Section 3)**. |
| `run --verify` | ptrace | **Mechanism present (L2 tool; L3 opt-in)** | Mechanism | `--verify` compares stdout, stderr, exit status, and the normalized internal scheduler-step log (L2). Heap/stack hashing (L3) requires `--detlog-heap`/`--detlog-stack` and is off by default. Same mechanism as on `main`. |
| Debugging (GDB) | ptrace | **Functional (local run), not an Lx level** | Code-on-branch + local functional | GDB RSP replay exercised on frontier for record, replay, breakpoints, backtrace, args/vars, finish, and continue in a *local* run. Live `--gdbserver` transport is namespace-hidden; finish→continue can panic; LLDB/MCP not landed (drafts are tentative, §3). |

**Frontier takeaway:** frontier *contains* the most capability (DBI, KVM, rr,
debugging) but has the *least measured assurance* — because its CI is red. Nothing
on frontier is graded above L0 by measurement.

### 1b. Frontier inventory (counts only — no level)

| Frontier metric | Count | Type |
| --- | --- | --- |
| Cargo inventory | 602 functions in 43 suites; 0 ignored in inventory | Inventory |
| Hermit crate only | 411 functions | Inventory |
| rr suite | 214 functions (213 enabled rr cases + 1 scratch-dir harness invariant) | Inventory |
| record/replay | 22 functions | Inventory (L0 by harness) |
| `cargo test --workspace --no-run` | 43 executables built | Build result (inventory) |

None of these are pass counts. The live frontier Actions run fails in Detcore
before producing any aggregate pass count.

### 1c. Per-backend frontier detail

**Ptrace `hermit run` (frontier).** Only Hermit backend that also exists on
`main`. Reverie owns ptrace/seccomp/lifecycle/registers/memory/syscall
injection/GDB RSP; Detcore supplies deterministic policy and scheduling. Frontier
layers the review-stack changes on top. **Aggregate: unmeasured** (red CI). Open
issues touch vfork, polling, blocking connect, pipeline replay, QEMU, toolchain.
Promotion: green frontier CI → rerun strict integration to establish L1/L2 *on
frontier* (not inherited from `main`) → enforce strict coverage at subscription
level → 20x L2/L3 for L4.

**DBI (`--backend dbi`, frontier-only).** Selector + DynamoRIO launch path are
merged; `main` has no selector. Measured L0 figures come from open PR #126 →
Section 3. L1+ undefined (no deterministic scheduler).

**KVM (`--backend kvm`, frontier-only).** Below L0: built-in real-mode guest,
`hello world`, `vmcall`, one decoded `write(2)`, host write; the CLI warns the
supplied program is not executed. No Linux process model, loader, arbitrary ELF
execution, signals, threads, filesystems, or Detcore scheduling. Promotion: boot
a protected/long-mode ELF entry, implement the guest ABI/lifecycle, connect
`Tool`/`Guest` to Detcore.

**Record/replay (frontier).** 22 generated functions at L0 (harness); heap/stack
pointer workloads included. The application-workload L2 comparison is tentative
(§3).

**`run --verify` (frontier).** Heap/stack memory hashing is **L3, opt-in only**;
plain `--verify` does not hash memory. `--verify` checks only observations Hermit
captures — no idempotency claim about arbitrary external side effects. Logs are
normalized/stripped before comparison.

**Debugging (frontier).** Functional axis, not Lx. Works (local): GDB RSP replay
for record/replay/breakpoints/backtrace/args/vars/finish/continue. Does not work
/ not landed: gdbserver namespace-hidden, finish→continue panic, LLDB no usable
stop, MCP not product — drafts are tentative (§3).

---

## 2. MAIN RESULTS (how far behind)

**Branch: `main` @ `5c5c73f`.** This is the small follow-on: `main` is the landed
baseline and, paradoxically, holds the **only trustworthy measured passes** today
because frontier CI is red. In *capability* `main` is ~66 commits and every new
backend behind frontier; in *assurance* it is one measured L2 ahead.

### 2a. Main measured passes (type 2 — the real numbers)

| Main metric | Result | Type & level | Interpretation |
| --- | --- | --- | --- |
| Fail-closed applicable set | 69 pass / 20 fail / 11 ignored; 61 mode-N/A | **L1 per passing case; L2 where the harness runs verify** | 69/89 = 77.5% of applicable non-ignored cases (ptrace, `--log=info`). The best strict-policy metric on **any** branch. First-blocking syscalls: `ioctl` (7), `tgkill` (4), `mkdir` (3), `setitimer` (2), one each `clock_settime`, `getrlimit`, `kill`, `setsockopt`. |
| Record/replay generated | 17 functions | **L0** (`cargo test`) | Generated coverage on `main`. |
| Cargo inventory | 337 functions in 40 suites; 16 `#[ignore]` | Inventory | `cargo nextest list`; PR #129 added no test declarations. |
| Hermit crate only | 162 functions | Inventory | Not all launch a guest. |

No L4 (20x) campaign has been run on `main` either. L3 only where a test
explicitly enables `--detlog-heap --detlog-stack`.

### 2b. How far behind is `main`?

| Capability | `main` | `frontier` | Gap |
| --- | --- | --- | --- |
| Ptrace `hermit run` | **L2 measured** on 69/89 (type 2). | Product backend + review stack; **unmeasured** (red CI, type 3). | Frontier has more code, no measured grade; `main` holds the only real L2. |
| Strict / fail-closed | 69/89 at **L1/L2** measured (type 2). | Code present; embedded doc stale at 3/89 (type 3). | **Do not cite the frontier 3/89**; rerun after rebase. |
| rr suite | None. | 213 enabled rr cases + invariant (inventory). | Frontier addition. |
| DBI | No selector. | Selector + DynamoRIO launch path; L0 via open PR (§3). | Frontier addition; L0 at best. |
| KVM | No selector. | Built-in hello/vmcall demo; below L0. | Frontier addition; not functional. |
| Record/replay | 17 functions at **L0** (type 2). | 22 at L0 + hardening; 16-workload L2 branch-only (§3). | Frontier adds coverage; the L2 result is landed on neither branch. |
| Debugging | Minimal. | GDB functional (local); LLDB/MCP not landed. | Frontier addition; functional, not a grade. |
| Cargo inventory | 337 / 40 suites. | 602 / 43 suites. | +265 functions of code-on-branch — inventory, not passes. |
| CI | Tip queued; recent self-hosted red. | Detcore failure + self-hosted PMU hang. | **Neither branch is green.** |

Net: `main` is far behind in capability but ahead in trustworthy assurance,
purely because frontier CI is red.

---

## 3. TENTATIVE RESULTS (open PRs — untrustworthy)

> **Everything in this section comes from open/draft PRs with no green CI. It is
> branch-only, unvalidated, type-3 evidence. Do not cite any figure here as an
> achievement or an Lx pass.** Numbers still state their source branch/PR.

GitHub inventory at the snapshot (inventory only): 47 merged Hermit PRs; 70 open
(64 draft, 6 non-draft); 17 open PRs carry `human-review`; 26 open issues. The six
non-draft open PRs: #139 (explicitly do not merge), #134, #107, #106, stacked
#102, human-review #80.

### 3a. DBI (draft PRs #126, #138)

- Draft PR #126 reports **81/89** guest-execution cases exit 0 under `cargo test`
  (full Cargo output 156 pass / 0 fail / 11 ignored) — an **L0** claim, tentative.
- The **`156/156` headline is not parity**: adversarial review found the eight DBI
  xfails returned *before executing their bodies*. Draft stacked PR #138 makes
  them execute under `catch_unwind` and fail on XPASS — but has **no CI result**.
- Defensible-if-landed statement: "81 guest cases would reach L0; 8 capability
  gaps unresolved." Not landed → tentative.
- PR #138 also makes `--strict --backend dbi` an explicit error (no deterministic
  scheduler), so L1+ is undefined for DBI.

### 3b. KVM (tracking PR #117; Reverie draft #16)

- PR #117 cross-backend tracking records KVM at **1/10** smoke cases — inventory
  of a demo, not an Lx pass.
- Draft Reverie PR #16 attempts static ELF / protected-mode bootstrap but is
  **conflicting with failed self-hosted CI**. Untrustworthy.

### 3c. Record/replay audit (draft PRs #124, #128, #130, #132)

- Draft audit PR #124 measured **13/16** application workloads byte-identical
  (an L2-class stdout/stderr/exit comparison) — **branch-only, on neither `main`
  nor frontier tip.** Failures: JVM timeout, Node `FIOCLEX`, SQLite mmap/event
  alignment.
- Fix branches: #128 (SQLite recvmsg/SCM_RIGHTS + mmap replay), #130 (JVM futex
  timeout), #132 (Node scheduler/FIONBIO/epoll + a **branch-local 16/16 claim**).
- **PR #132 has no Actions result and is conflicting.** The 16/16 statement is
  branch evidence, not a landed or integrated L2 result on any tracked branch.

### 3d. Debugging drafts (Hermit #144, #147, #146; Reverie #21)

- Draft #144 attempts to fix `--gdbserver` reachability (namespace-hidden);
  self-hosted check failed.
- Draft #147 adds GDB/LLDB Python tests; self-hosted CI failed.
- MCP PoC PR #146 was **closed, not merged** (regular CI passed, self-hosted
  failed) — a closed experiment, not product support.
- Reverie draft #21 adds LLDB packets, stacked on the unlanded finish fix; LLDB
  connects but does not reach a usable stop. Reverse step/continue is not
  advertised by the RSP server.

### 3e. verify UX (draft PR #148)

- Draft PR #148 exists because the double-run setup overrides the user's
  `--log-file` instead of honoring it. UX only — not a level change, not a result.

---

## Appendices

### A. validate.sh: what it measures, on which branch, at which level

`hermit/validate.sh` is the local gate; it runs against the **checked-out
branch** (frontier when working the review stack, `main` otherwise). Its Hermit
smoke invocations share `HERMIT_RUN_ARGS = run --base-env=minimal
--no-virtualize-cpuid --preemption-timeout=disabled`. **These args do NOT include
`--strict`**, so every level below is branch-independent.

| validate.sh check | Command essence | Level | Notes |
| --- | --- | --- | --- |
| `cargo-nextest available` | `cargo nextest show-config version` | Tooling | Not a test. |
| `Build workspace` | `cargo build --workspace` | Build | Not a test. |
| `Test workspace and integrations` | `cargo nextest run --workspace --exclude detcore --exclude ...flaky` | **L0** | Level is whatever each test's harness sets; no `--strict` added. |
| `Test detcore package` | `cargo test -p detcore` | **L0** | Includes PMU/CPUID-sensitive cases. |
| `Fast concurrency stress suite` | `cargo nextest run -p hermit --test stress_suite -E test(=fast_chaos_matrix)` | **L0** (chaos harness) | Chaos, not `--strict --verify`. |
| `Hermit analyze scenarios` / `Schedule search E2E` | `cargo test -p hermit --test analyze`; `hermit_analyze_e2e.sh` | **L0** (requires PMU) | Analyze search, not a determinism verdict. |
| `rr syscall suite` | `cargo test -p hermit --test rr_suite` | **L0** | rr guest programs under Hermit. |
| `Hermit run smoke test` | `hermit run <relaxed args> -- /bin/echo` | **L0, relaxed** | No `--strict`; just checks stdout equals the marker. |
| `Hermit output determinism` | two relaxed runs of `/bin/echo`, stdout diffed | **Does NOT reach L2 (relaxed)** | No `--strict`, no `--verify`; L0-relaxed per memory #19. |
| `Hermit verify-mode smoke test` | `hermit run <relaxed args> --verify -- /bin/echo` | **Does NOT reach L2 (relaxed)** | `--verify` but **no `--strict`**; void at L1+ per memory #19. |

Takeaway: a green `validate.sh` establishes **L0** across the workspace plus two
relaxed smoke runs, on whichever branch it ran. It does **not** establish L1–L4
for any guest on any branch, because it never passes `--strict`.

### B. CI and runner status

**Hermit.** **Frontier PR run `29954326191` is red**: failed GitHub-hosted job at
"Test Detcore without hardware-dependent integration tests"; self-hosted job stuck
in "PMU parallel memory tests". On `main`: current run `29955251128` (PR #129
merge) queued; PR run also queued; recent main runs for #119 and #125 completed
failure on the long/self-hosted lane. Two lanes: regular GitHub-hosted, and
trusted self-hosted host/PMU/namespace. The single self-hosted runner is a
throughput bottleneck; queued/cancelled results are not green and must not be
reported as passing at any level.

**Reverie.** Reverie main at `e3e2c965` is **green**. Latest main runs for
vendored DynamoRIO (#18), expanded KVM interception (#8), DBI parity impl (#19),
and CI repair (#17) all succeeded. Reverie's *mechanism* baseline is sound even
though Hermit frontier's *integration* CI is red.

### C. Open issues (26)

| Area | Issues |
| --- | --- |
| rr known gaps | #112 signal-mask handler, #113 priority spinlock, #114 rusage `ru_maxrss`, #115 SIGCHLD interrupt, #116 pending-signal flake |
| Record/replay fidelity | #17 FIOCLEX, #19 pipeline desync/leaked children, #22 ppoll/vectored outputs, #23 recvmsg ancillary FDs, #31 Go/SQLite filesystem events, #94 unused syscall registers |
| Syscall/network/scheduling | #18 blocking connect, #20 sched_yield starvation, #21 PMU wrapper false skip, #24/#26 ppoll design/tracking |
| Toolchain/process lifecycle | #15 CLONE_VFORK hang, #16 rustup proxy EBADF |
| QEMU / VM workloads | #5 TCG performance, #6 guest clock calibration, #9 virtualized uname, #10 vng CLONE_VFORK |
| Tests/docs/config | #11 deployment expectations, #12 strict option mismatch, #13 verify tmp/env, #14 noisy PMU traces |

Issues #12, #13, #17, #94 appear covered by newer code/tests on frontier but
remain open; verify against the exact Lx command on frontier and close or rewrite.

### D. Recommended promotion order

1. **Get frontier CI green in both lanes** — the gate on every frontier grade.
   Diagnose the Detcore failure and the PMU-memory hang; do not merge around
   queued/failed self-hosted checks. (Restoring green `main` is a prerequisite so
   #129 can land into frontier.)
2. **Regenerate frontier evidence from green runs:** rerun the strict integration
   set to establish L1/L2 *on frontier* (not inherited from `main`), plus Cargo
   inventory, rr, record/replay, and backend matrices; update stale status docs
   from generated results only.
3. **Grade DBI honestly at L0 on frontier:** rebase #126, include #138's
   corrected xfail logic, publish "81 L0 + 8 gaps", keep `--strict --backend dbi`
   an error until a deterministic scheduler exists.
4. **Keep KVM scoped as research** (below L0) until it executes an actual ELF and
   drives Detcore.
5. **Integrate record/replay fixes** onto frontier and re-run the 16-workload L2
   comparison in both runners.
6. **Stabilize debugging bottom-up:** finish/continue fix, reachable transport,
   green LLDB packet tests, then MCP.
7. **Run L4 campaigns** (20x at L2/L3) on the ptrace passing set once frontier is
   green, recording the exact command per suite.

### E. Reproduction notes

Level-defining commands (backend defaults to ptrace; add `--backend dbi/kvm` to
grade those; run on the branch you are grading and say which):

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

Inventory / read-only evidence (type 1 — not pass counts):

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
