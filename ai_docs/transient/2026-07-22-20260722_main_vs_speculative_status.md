# Main vs. Speculative Frontier

Status date: 2026-07-22.

This document separates three kinds of evidence:

1. **Landed:** committed to `rrnewton/hermit:main`.
2. **Integrated snapshot:** present in the repository's speculative integration
   branches, but not necessarily validated together.
3. **Open frontier:** the best measured result on an open PR head.

These categories are not interchangeable. In particular, the best rr,
fail-closed, DBI, record/replay, and language-runtime results currently live on
different branches. No tested branch contains all of those maxima.

## Ref Snapshot

| Ref | Commit | Meaning |
| --- | --- | --- |
| `origin/main` | `4c707fc` | Current landed baseline |
| `origin/frontier` | `a0176f6` | Newer 49-PR integration snapshot, created 2026-07-22 07:25 -0700 |
| `origin/speculative` | `96261f6` | Older integration line, created 2026-07-21 17:57 -0700 |

`origin/frontier` is the useful "everything merged" snapshot for this audit.
The branch has 127 commits not reachable from `origin/main`; the three
main-side commits have equivalent PR merges in the frontier but different
squash commit identities. GitHub reports no Actions runs for either
`frontier` or `speculative`, so neither is a green integration claim.

There are 63 open PRs. The tables below list the PRs that alter one of the five
requested metrics. Other open PRs cover syscall policy, CI, diagnostics,
documentation, QEMU, performance, and stress tooling without directly changing
these denominators.

## Executive Comparison

| Metric | Landed on `main` | Integrated `frontier` | Best open-PR evidence |
| --- | --- | --- | --- |
| rr syscall tests | No `rr_suite`; 0/219 tracked in Cargo | 213 stable expected passes enabled; 5 known gaps; 218 mapped | #121: 219/219 tracked, 214 pass + 5 xfail |
| Fail-closed | 3/89 measured against main-family commit `5d3b2a3` (3.4%); tracker not landed | PR #41 enforcement merged, but no aggregate run or percentage | #129: 69/89 applicable tests pass (77.5%), 20 known failures |
| DBI parity | No DBI dependency or backend selector | DBI selector and source-build hello path; no suite percentage | #126: 81/89 real DBI passes (91.0%) + 8 xfails; Cargo reports 156/156 |
| Record/replay | 17 generated Cargo tests; committed application matrix is 6/11 | 22 generated Cargo tests plus timeout/desync hardening; no integrated rerun | #124: 13/16 audited workloads (81%); #127 Node and #128 SQLite fixes are separate |
| Language runtimes | Deep Rust/C; optional smoke probes for Python, Node, JVM, and Go; no dedicated runtime matrix | Python hash plus C/C++/Python OSS workload PRs; no six-runtime entropy matrix | #120: Go, Ruby, Node, JVM, OCaml, CPython entropy probes all strict-deterministic |

Percentages use the owning status document's denominator. They must not be
compared as if they were one common test population.

## Landed Main

### Relevant Landed PRs

The recent `origin/main` log contains these capability groups:

| Area | Landed PRs | Capability |
| --- | --- | --- |
| Signals and synchronization | #45, #46, #52, #53 | Signal scenarios, blocking pipe/eventfd, pthread synchronization, shared futexes |
| Deterministic state | #55, #58, #59, #61, #68, #69 | mmap addresses, notification FDs, randomness, clocks, rusage/sysinfo, procfs |
| Network and replay | #57, #66, #67 | find replay repair, nonblocking connect, epoll |
| Validation and tooling | #7, #60, #63, #76, #86 | Schedule-search CI, runtime validation, benchmarks, analyze tests, bounded validation output |
| Compatibility evidence | #51, #64, #65 | Record/replay matrix, README, arbitrary-binary wave 3 |

### Metric Detail

**rr:** Main does not contain `hermit-cli/tests/rr_suite.rs` or the pinned rr
submodule. This means zero rr cases are tracked by public Cargo CI; it does not
mean every rr program would fail.

**Fail-closed:** The diagnostic flag exists, but main has no status manifest or
ratchet script. PR #119 measured 3 passing and 86 failing applicable tests
against `5d3b2a3`, an ancestor of current main. That is 3/89, or 3.4%.
The audit also found 291 syscall release entries missing from optimized
subscriptions, so even this measurement is a lower bound rather than proof
that every unsupported syscall fails closed.

**DBI:** Main has no `reverie-dbi` dependency and no `Backend::Dbi`
dispatch. DBI parity is therefore not applicable on landed main.

**Record/replay:** Main defines 17 generated integration tests: two scenario
tests plus 15 Rust guest workloads. The committed 2026-07-21 application
matrix has 11 rows, of which six record, replay, and match output. A newer
audit based on main reports 13/16, but that report remains open in PR #124.

**Languages:** Rust and C have broad unit, guest, and integration coverage.
The arbitrary-binary matrix has optional smoke coverage for Python, Node.js,
Java, and Go. The README records Python file/JSON work, Node `console.log`,
and Java version startup. These are narrow compatibility probes, not runtime
determinism suites. Main has no dedicated Go, Ruby, Node, JVM, OCaml, and OSS
CPython entropy matrix.

## Open Feature Branches

### Metric Owners

| PR | Branch | Metric and result |
| --- | --- | --- |
| #81 | `port-rr-test-suite-slot06` | Initial rr harness; integrated into `frontier` |
| #121 | `impl-rr-test-regression-tracking` | All 219 exported rr targets execute: 214 expected passes, 5 asserted xfails |
| #119 | `impl-fail-closed-tracking` | Baseline ratchet: 3/89 pass, 86 known failures |
| #125 | `impl-fail-closed-pread64-slot08` | Adds deterministic `pread64` handling |
| #129 | `impl-fail-closed-batch2-slot08` | Latest fail-closed head: 69/89 pass, 20 fail, 11 ignored, 61 mode-N/A |
| #95, #102, #105 | backend selector stack | Adds selector syntax and wires DBI/KVM prototypes |
| #111 | `pin-reverie-hash` | Pins the experimental Reverie dependency |
| #117 | `impl-backend-parity-tracking-main` | Small ratchet: ptrace 10/10, DBI 7/10, KVM 1/10 |
| #126 | `impl-dbi-parity-79-hermit` | Full DBI survey: 81/89 real passes plus 8 documented xfails |
| #124 | `research-record-replay-status` | Application audit: 13/16 pass; JVM, Node, and SQLite fail |
| #127 | `impl-fix-ioctl-fioclex` | Node 16 `--version` records/replays; `console.log` next reaches `clone3` |
| #128 | `impl-fix-sqlite-replay-mmap` | SQLite record/verify passes; adds SCM_RIGHTS coverage; branch suite 17/17 |
| #120 | `research-language-coverage` | Six-runtime entropy matrix and coverage audit |
| #118 | `impl-test-go-determinism` | Go goroutine completion order: native varies, strict is stable |
| #122 | `test-ruby-determinism` | Ruby thread order varies natively but strict deadlocks; not a pass |
| #123 | `impl-test-ocaml-determinism-slot02` | OCaml 5.3 domains: 19/40 native outputs, one strict output |

### rr Frontier

The original frontier document maps 218 rr programs. It enables 213 stable
passes and lists five gaps; one of those five is a flaky program that passed
once and later hung. It also lacks the `arch_prctl_x86.c` mapping.

PR #121 closes the inventory gap:

- 214 normal expected-pass tests;
- 5 xfails that execute and assert their failure shape;
- 0 omitted tests;
- 219/219 inventory tracking, with a count ratchet.

The five xfails cover rusage, two signal-mask/restart paths, priority-sensitive
spin behavior, and flaky sequential pending-signal delivery.

### Fail-Closed Frontier

PR #129 is the strongest measured branch: 69/89 applicable tests pass, or
77.5%. The remaining 20 failures first stop at `ioctl`, `tgkill`, `mkdir`,
`setitimer`, `clock_settime`, `getrlimit`, `kill`, or `setsockopt`.
Eleven tests are explicitly ignored and 61 tests are mode-N/A.

This remains a coverage ratchet, not complete enforcement: optimized
subscriptions can still let an unsubscribed syscall execute without reaching
the panic path.

### DBI Frontier

PR #117's 7/10 figure is a small cross-backend smoke matrix and should not be
confused with the larger integration inventory. PR #126 supersedes the broad
DBI measurement:

- 81/89 guest-execution cases really pass: 91.0%;
- 8 cases are conditional xfails;
- Cargo reports 156 passed, 0 failed, 11 ignored because Rust libtest has no
  native xfail result;
- the xfails require deterministic thread scheduling, network-event export,
  signal state/timers, or event/backtrace support.

The Hermit PR pins its native implementation in companion Reverie PR #19.

### Record/Replay Frontier

PR #124 audited 16 workloads on a main-family commit:

| Group | Passing | Audited |
| --- | ---: | ---: |
| Basic programs | 3 | 3 |
| Nondeterminism/language runtimes | 4 | 6 |
| OSS applications | 6 | 7 |
| **Total** | **13** | **16** |

The failures were JVM startup timeout, Node `FIOCLEX`, and SQLite mmap event
alignment. PR #127 addresses the Node version probe and PR #128 addresses the
SQLite probe, but they are independent branches. A projected 15/16 union is
not a measured result until those heads are integrated and the matrix is
rerun; JVM remains open.

### Language Frontier

PR #120 adds opt-in entropy-removal tests for six external runtimes:

| Runtime | Normal strict result | First fail-closed gap |
| --- | --- | --- |
| Go 1.26.4 | Pass | `gettid` |
| Ruby 3.0.7 | Pass | `pread64` |
| Node.js 16.20.2 | Pass | `pread64` |
| OpenJDK 26.0.1 | Pass | `pread64` |
| OCaml 4.11.1 | Pass | `pread64` |
| OSS CPython 3.9.25 | Pass | `pread64` |

These tests prove deterministic entropy for the named probe, not full runtime
compatibility. PR #122 is an important counterexample: a multi-threaded Ruby
workload deadlocks under strict scheduling even though the single entropy
probe passes.

Additional open workload PRs are #77 (LULESH), #83 (LevelDB), #85 (Ninja),
#97 (Python stdlib), #98 (bzip2/gzip), #99 (Redis), #101 (SQLite), #104
(OpenMP floating-point reduction), and #107 (Python hash seed). They add useful
focused evidence but include timeouts, exclusions, host-selected toolchains, or
other documented scope limits.

## Integrated Frontier

`origin/frontier` merges these 49 PR heads:

`#7, #8, #25, #27, #33, #35, #41, #42, #47, #48, #50, #54, #62,
#70, #71, #72, #73, #74, #75, #76, #77, #78, #79, #80, #81, #83,
#84, #85, #86, #87, #88, #89, #90, #91, #92, #93, #95, #96, #97,
#98, #99, #100, #101, #102, #103, #104, #105, #106, #107`.

This snapshot contains:

- the initial rr harness, with 213 stable tests enabled;
- fail-closed enforcement from #41, but no measurement ratchet;
- the DBI/KVM selector and a source-built DBI hello path, but no parity survey;
- 22 generated record/replay integration tests, including timeout hardening;
- Python hash, LULESH, LevelDB, Ninja, Python stdlib, compression, Redis,
  SQLite, and OpenMP workload branches.

It does **not** contain the newer metric heads #117-#129. It has no GitHub
Actions run, so its combined behavior is unknown. It is a merge-conflict
resolution artifact and a useful code frontier, not release evidence.

## Older `origin/speculative`

The branch named `origin/speculative` predates `frontier` and is not its
ancestor. It integrates the earlier #25/#27/#29/#30/#32-#39 era:

| Metric | Older speculative status |
| --- | --- |
| rr | No Cargo rr harness |
| Fail-closed | Predates #41 and the tracking work |
| DBI | Predates the backend selector stack |
| Record/replay | One matrix test covering six compiled Rust/C workloads |
| Languages | No dedicated multi-runtime determinism matrix |

Use `origin/frontier`, not this branch, for the newest integration snapshot.

## Claim Boundary

The defensible current summary is:

- **Landed:** strong ptrace determinism coverage and 17 record/replay
  integration tests, but no landed rr, fail-closed ratchet, or DBI backend.
- **Measured on open heads:** rr 214/219 + 5 xfail, fail-closed 69/89, DBI
  81/89 + 8 xfail, record/replay 13/16, and six focused runtime entropy passes.
- **Integrated:** `origin/frontier` contains many earlier PRs but has no
  aggregate CI result and predates the latest measurements.

Do not report the open-head maxima as one combined tested system. The next
credible frontier milestone is to build a fresh integration branch from
current main plus the desired non-overlapping heads, run the rr and
fail-closed ratchets, run the DBI matrix with its native companion revision,
rerun the 16-row record/replay audit, and run the opt-in language matrix.

## Reproduction

The inventory was derived without checking out the refs:

```bash
git log --oneline origin/main
git log --first-parent --oneline origin/main..origin/frontier
git rev-list --left-right --count origin/main...origin/frontier
git show REF:docs/STATUS_FILE.md
git ls-tree -r --name-only REF
with-proxy gh pr list --repo rrnewton/hermit --state open --limit 100
with-proxy gh run list --repo rrnewton/hermit --branch frontier
```

The quantitative owners are `docs/rr-test-suite.md` on #121,
`docs/FAIL_CLOSED_STATUS.md` on #129, `docs/DBI_TEST_STATUS.md` on #126,
`docs/RECORD_REPLAY_STATUS.md` on #124, and
`ai_docs/language-coverage.md` on #120.
