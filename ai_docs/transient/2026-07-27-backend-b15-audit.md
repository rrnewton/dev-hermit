# Reverie Backend B1.5 Audit Consolidation

Date: 2026-07-27

## Scope and evidence level

This report consolidates the 2026-07-27 Reverie example-tool audits for
ptrace, KVM, DBI, SaBRe, LiteInst, and e9patch. It covers tool execution,
`counter2` counts and launcher-level timings, local-to-global RPC, and sharing
of the ptrace implementation.

These are Reverie L0 compatibility and performance observations. They are not
Hermit L1/L2 determinism results. Timings from different audit cohorts use
different guests, build modes, sample counts, and host controls; compare only
rows within the same cohort.

### Source reports

| Scope | Evidence source | Tested revision |
| --- | --- | --- |
| Ptrace and KVM | `/tmp/backend-audit-kvm-ptrace.md` | Reverie `a08d12a7575a74eacf15f1655347b0b15b417d0b`; observed `origin/main` `0779f2305909ec08c5ba7e8d48ebcb7bd751c040` |
| DBI | `/tmp/backend-audit-dbi.md` | Reverie `178e306c0baaac4417758dea795c5667e7d07c2d`, branch `codex/reverie-dbi-b15-rpc` |
| SaBRe and e9patch | `/tmp/backend-audit-sabre-e9patch.md` | Reverie `b6009ddf84e6282d62d30d2166601906d85eb548`; SaBRe `34065e7d`; e9patch `6c2c03c1` |
| LiteInst | `/tmp/backend-audit-liteinst.md` | Reverie `5766696d166d05c2532c7f8493410d2304d67b77`, branch `codex/reverie-liteinst-b15-tool-communication` |

A scan of `worktrees/` found no additional standalone audit report. The repeated
`ai_docs/transient/kvm-backend-results.md` files are a historical 2026-07-22
snapshot that predates the current KVM ELF/tool path and are not used as
current evidence.

## Executive findings

1. Ptrace is the only backend that ran all nine general example binaries.
2. LiteInst and e9patch ran all seven production `ToolKind` tools, but neither
   exposes the ptrace-specific `debug` or `strace_minimal` binaries.
3. KVM exposes four of nine tools. Its root guest runs the real Tool path, but
   child syscalls bypass Tool callbacks; the process-tree `counter2` result is
   therefore incomplete.
4. DBI has working adaptations for seven behaviors, including the exact
   counter tools, but lacks full `strace` and `debug`. Its UDS RPC transport is
   real and tested, yet the production launcher does not start the server or
   set the socket environment, so real process trees still use independent
   process-local globals.
5. SaBRe exposes four tool families. Its normal fallback is in-guest `SIGILL`,
   not ptrace. Exact `counter2` lacks process-exit publication; the separate
   coordinator-specific counter does aggregate one process.
6. E9patch is a hybrid. Rewritten root-ELF syscall sites use injected `int3`
   frames, while `reverie-ptrace` remains attached for lifecycle, libraries,
   signals, timers, and all zero-patched-site traffic.
7. The backend counters were self-consistent in repeated controls, but are not
   generally equal across backends because observation windows and child
   coverage differ. Raw `strace` output-line counts are scheduling-sensitive
   and must not be treated as syscall totals.

## Tool compatibility matrix

Legend:

- **PASS**: the production/shared Tool or its normal example runner executed.
- **ADAPTED**: a backend-specific adaptation executed the corresponding
  behavior; it is not the same launcher or complete feature surface.
- **UNSUPPORTED**: no selector/runner exists; this is not a runtime failure.

| Example tool | Ptrace | KVM | DBI | SaBRe | LiteInst | e9patch |
| --- | --- | --- | --- | --- | --- | --- |
| `chaos` | PASS | UNSUPPORTED | ADAPTED | UNSUPPORTED | PASS | PASS |
| `chrome_trace` | PASS | UNSUPPORTED | ADAPTED | UNSUPPORTED | PASS | PASS |
| `chunky_print` | PASS | UNSUPPORTED | ADAPTED | UNSUPPORTED | PASS | PASS |
| `counter1` | PASS | PASS | PASS exact + adapted | PASS exact + adapted | PASS | PASS |
| `counter2` | PASS | PASS | PASS exact + adapted | PASS exact + adapted | PASS | PASS |
| `debug` | PASS with automated GDB | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| `noop` | PASS | PASS | ADAPTED | ADAPTED | PASS | PASS |
| `strace` | PASS | PASS | UNSUPPORTED (minimal adaptation only) | ADAPTED | PASS | PASS |
| `strace_minimal` | PASS | UNSUPPORTED | ADAPTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |

Important qualifications:

- The KVM audit reports four supported runners: `counter1`, `counter2`,
  `noop`, and `strace`. The other five fail at CLI parsing with exit 2.
- DBI dispatches `HERMIT_DBI_*` modes inside the DynamoRIO native client. Full
  strace filtering/configuration and a GDB server are not ported. The umbrella
  DBI E2E script remained red at an independent backtrace probe, so the cells
  above are direct runs, not a full-suite pass.
- SaBRe's exact counter selectors import production counter source, while its
  `strace`, `noop`, and coordinator counters are backend-specific adapters.
- E9patch ran the exact seven production `ToolKind` implementations. On
  `/bin/echo`, zero root-ELF sites were recoverable, so ptrace supplied all
  events. A one-site direct-syscall ELF separately proved the injected-trap
  path for all seven tools.
- LiteInst's conservative chaos smoke proved selection, configuration, and
  callback execution but deliberately disabled intervention. Three stronger
  chaos intervention tests timed out under high-volume diagnostics.

## Control: self-consistency verification

The KVM/ptrace audit used this deterministic process-tree workload:

```sh
/bin/sh -c 'for i in 1 2 3 4; do /bin/true; done; printf "workload\n"'
```

The requested native control was run twice without changing the command:

```sh
strace -f -e trace=all /bin/sh -c \
  'for i in 1 2 3 4; do /bin/true; done; printf "workload\n"' 2>&1 | wc -l
```

The raw line totals were **451** and **446**. This control is not
self-consistent as a line count. `strace` output lines are not equivalent to
system calls: a blocked call can appear on one line or be split into entry and
`<... resumed>` records depending on scheduling. The pipeline also counts
signals, process-exit markers, and the workload's stdout.

A follow-up pair wrote the trace stream separately with `strace -o` and
classified each record. The trace files contained 452 and 448 lines, but both
had exactly **404 syscall-entry records**, the same frequency for every syscall
name, five process-exit records, and four signal records. Only the number of
resumed records differed (39 versus 35). The native workload was therefore
self-consistent at the syscall-entry level even though the requested raw
`wc -l` proxy was not.

The Reverie ptrace control was also run twice:

```sh
target/debug/counter2 --no-host-envs -- /bin/sh -c \
  'for i in 1 2 3 4; do /bin/true; done; printf "workload\n"'
```

| Control | Run 1 | Run 2 | Self-consistent? |
| --- | --- | --- | --- |
| Native `strace ... 2>&1 \| wc -l` | 451 output lines | 446 output lines | No; scheduling-sensitive formatting |
| Native syscall-entry records (`strace -o`, diagnostic pair) | 404 | 404 | Yes; syscall-name frequencies also match |
| Reverie ptrace `counter2` | 325 calls, 5 processes, 5 threads | 325 calls, 5 processes, 5 threads | Yes |

Each ptrace run reported 157 root calls and 42 calls from each of four child
processes. Together with the source audit's three identical KVM results of 160
calls, one process, and one thread, these controls rule out workload variation
as the explanation for the ptrace/KVM delta. The cross-backend difference is
an observation-boundary difference: current KVM Tool callbacks cover only the
root, whereas ptrace covers the process tree. Native `strace`'s 404 entry
records are not directly comparable to either `counter2` total because
`strace` and Reverie count at different interception boundaries.

## Counter2 counts

Stable counts do not imply equal coverage. The deltas below identify actual
interception boundaries rather than nondeterminism in the tool.

| Cohort and guest | Ptrace | Backend result | Interpretation |
| --- | --- | --- | --- |
| KVM audit, `/bin/echo hello` | 40 calls | KVM 39 | Stable one-call boundary difference. |
| KVM audit, `/bin/sh` plus four `/bin/true` children | 325 calls, 5 processes, 5 threads | KVM 160 calls, 1 process, 1 thread | KVM child syscalls bypass Tool callbacks, so only the root is represented. |
| DBI, `/bin/true` | 33 calls | DBI 32 | DBI starts after launcher-side `execve`; both see the same post-exec stream. |
| DBI, `/bin/bash -c '/bin/true & wait'` | One summary: 258 calls, 2 processes, 2 threads | Two summaries: 32 and 211 calls, each 1 process/1 thread | Production DBI does not share one global across the real fork/exec tree and misses pre-exec child events. |
| SaBRe/e9patch, `/bin/echo audit-counter2` | 114 | e9patch 114; SaBRe exact 88 thread-local; SaBRe custom 86 global | E9patch had zero rewritten sites and consumed the same ptrace stream. SaBRe has a different loader/window; exact process-exit RPC is absent. |
| LiteInst, 256 direct `getpid` guest | 332 in 5/5 runs | LiteInst 283 in 5/5 runs | LiteInst starts in an `LD_PRELOAD` constructor and misses 49 loader/startup, vDSO, and exec-boundary calls. |

## Counter2 benchmark results

### Cohort A: ptrace versus KVM

The ptrace/KVM source report records 100 debug-build hyperfine runs of the
deterministic shell/four-child workload:

| Path | Mean | Standard deviation | Min | Max |
| --- | ---: | ---: | ---: | ---: |
| Ptrace | 58.093 ms | 9.453 ms | 49.422 ms | 112.740 ms |
| KVM | 3,534.535 ms | 540.226 ms | 2,893.965 ms | 5,362.522 ms |

KVM was 60.843x the ptrace mean for this debug-build workload. Both paths
exited 0 in all 100 runs, but they did not observe the same process tree.

### Cohort B: DBI startup

One shell loop ran 100 sequential `/bin/true` executions per path:

| Path | Wall time for 100 | Approximate wall/run |
| --- | ---: | ---: |
| Native | 0.08 s | 0.8 ms |
| Ptrace counter2 | 3.77 s | 37.7 ms |
| DBI counter2 | 4.70 s | 47.0 ms |

DBI was 1.25x ptrace (24.7% slower) for this startup-dominated workload.

### Cohort C: SaBRe and e9patch

Ten end-to-end samples of `/bin/echo audit-counter2`, with two warmups:

| Path | Mean | Median | Min | Max |
| --- | ---: | ---: | ---: | ---: |
| Native | 5.769 ms | 5.886 ms | 5.363 ms | 6.236 ms |
| Ptrace exact | 34.479 ms | 34.645 ms | 32.178 ms | 36.084 ms |
| SaBRe exact | 69.480 ms | 69.395 ms | 67.746 ms | 71.139 ms |
| SaBRe custom | 70.363 ms | 70.411 ms | 68.790 ms | 71.849 ms |
| E9patch exact | 20.391 ms | 20.355 ms | 19.162 ms | 21.488 ms |

For a one-site direct-syscall ELF, e9patch used its injected-trap event path:

| Path | Mean | Median | Min | Max |
| --- | ---: | ---: | ---: | ---: |
| Native | 5.376 ms | 5.331 ms | 4.797 ms | 6.130 ms |
| Ptrace exact | 35.752 ms | 33.396 ms | 30.841 ms | 54.802 ms |
| SaBRe exact | 85.860 ms | 70.558 ms | 67.049 ms | 159.024 ms |
| E9patch exact | 19.427 ms | 19.327 ms | 16.581 ms | 24.034 ms |

These figures include launcher/rewriter setup. SaBRe had two large outliers in
the direct-syscall cohort, so its median is more representative.

### Cohort D: LiteInst

Thirty CPU-0-pinned release runs of the 256-direct-`getpid` guest:

| Path | Mean | Standard deviation | Median | Range |
| --- | ---: | ---: | ---: | ---: |
| Native | 2.8688 ms | 1.8069 ms | 2.3862 ms | 2.11-11.15 ms |
| LiteInst counter2 | 6.9535 ms | 0.4581 ms | 6.8034 ms | 6.46-8.20 ms |
| Ptrace counter2 | 97.6860 ms | 9.4912 ms | 96.1693 ms | 93.47-147.32 ms |

Ptrace was 14.05x LiteInst by mean wall time. The native command was below
hyperfine's 5 ms precision warning, and LiteInst/ptrace observed 283/332 calls,
so no per-event cost is inferred.

## Local-to-global RPC architecture

All backends target Reverie's `Tool`, `GlobalTool`, `GlobalRPC`, and `Guest`
contracts. The transport and lifecycle coverage differ substantially.

| Backend | Local-to-global mechanism | Process-tree status |
| --- | --- | --- |
| Ptrace | The tracer owns Tool state and one host global; callbacks and lifecycle remain in the attached supervisor. No guest UDS bridge is needed. | Complete enough for the audited `counter2` process tree: one 258-call, 2-process, 2-thread summary. |
| KVM | `KvmGuest::send_rpc` directly calls the host `GlobalTool::receive_rpc`; no socket is needed inside the host KVM runtime. | Root Tool RPC works and lifecycle RPC round trips are tested. Forked child syscalls currently execute in the KVM personality without per-child Tool callbacks, producing the 160/1/1 result. |
| DBI | `DbiGuest` either calls a process-local fallback or uses a synchronous per-thread UDS client. Frames are big-endian length + bincode request/response. PID change after fork drops the inherited connection. `reverie-rpc-transport::RpcServer` owns one shared `Arc<G>`. | Transport and fork reconnect pass a focused Rust integration test. The production `drrun` launcher does not start the server or set `HERMIT_DBI_RPC_SOCKET`, and copied children issue no Tool RPC before exec. Real process trees remain split. |
| SaBRe | The host starts `RpcServer`; the injected adapter uses a blocking client per guest thread and receives config on connect. | Coordinator-specific counters use the shared global. The exact production `counter2` path does not publish process-exit RPC, so the audit only obtained a thread-local total. |
| LiteInst | The coordinator starts `RpcServer` and passes a sealed-memfd bootstrap plus `LD_PRELOAD`. The guest uses the wire-compatible synchronous coordinator client; post-seccomp I/O goes through a trusted syscall gate. | Active and demonstrated for the audited root process. Pre-constructor syscalls, static binaries, exec rebootstrap, and full clone/thread lifecycle remain outside coverage. |
| E9patch | The active implementation delegates Tool/Guest/global ownership to the attached ptrace controller. Declared preload/RPC dependencies are future foundations and are not on the current event path. | Inherits ptrace lifecycle for root, libraries, signals, and timers. Rewritten sites still report through ptrace via an injected trap frame. |

### Shared transport

DBI, SaBRe, and LiteInst converge on `reverie-rpc-transport::RpcServer` and
typed `GlobalTool::receive_rpc` dispatch. Their clients and bootstrap differ:

- DBI: environment socket path, per-thread synchronous connection, PID-aware
  reconnect; not wired by the production launcher.
- SaBRe: injected adapter with blocking per-thread client.
- LiteInst: sealed-memfd configuration plus preload client and trusted syscall
  gate; wired by the production launcher.

KVM instead invokes the host global directly. E9patch and ptrace keep the
global in the attached tracer process.

## Ptrace sharing and fallback analysis

| Backend | Uses `reverie-ptrace` / `safeptrace` on active path? | Actual event and lifecycle path |
| --- | --- | --- |
| Ptrace | Yes, directly | `TracerBuilder<T>` and `safeptrace` own tracing, memory, registers, lifecycle, and events. |
| KVM | No | KVM VM exits plus a host syscall personality; direct host Tool/global calls. |
| DBI | No | DynamoRIO native client dispatches into Rust DBI adaptations. |
| SaBRe | No | Rewriter/loader plus injected adapter. Short rewrite sites fall back to in-guest UD0/UD2 and a `SIGILL` handler, not ptrace. |
| LiteInst | No | `LD_PRELOAD`, seccomp `SECCOMP_RET_TRAP`, a `SIGSYS` bootstrap trap, then `liteinst2` hot patches. The documented `HybridPtrace` lifecycle is not active. |
| E9patch | Yes, directly and transitively through `safeptrace` | Root-ELF sites may be rewritten, but ptrace is always the lifecycle and Guest controller and becomes the event source for libraries, non-ELFs, and zero-site inputs. |

Only e9patch literally shares the ptrace backend implementation. SaBRe's
"fallback" is an in-process illegal-instruction path. LiteInst's first-hit
trap is SIGSYS/seccomp, after which successful sites use direct patched hooks.
Neither is ptrace-of-last-resort. DBI and KVM are independent transports.

## Backend-specific gaps

### KVM

- Five of nine example binaries lack a KVM runner.
- Root Tool/RPC execution is real, but child syscalls bypass per-child Tool
  callbacks and lifecycle aggregation.
- The current benchmark report is bound to tested Reverie SHA
  `a08d12a7575a74eacf15f1655347b0b15b417d0b`.

### DBI

- Production launch does not enable the implemented shared UDS global.
- Copied children use native fallbacks until exec and issue no Rust Tool RPC.
- Full strace and debug are absent.
- The umbrella E2E script fails before examples at a backtrace probe.
- Every direct launch warns that the DynamoRIO distribution is incomplete.

### SaBRe

- Three of seven production selectors plus debug/strace-minimal are absent.
- Exact counter2 lacks process-exit publication.
- The loader and interception boundary produce different counts from ptrace.

### LiteInst

- No debug or strace-minimal selector.
- No pre-constructor, static-binary, vDSO, exec-rebootstrap, or complete
  clone/thread lifecycle coverage.
- Strong chaos intervention tests need logging/performance recalibration.

### E9patch

- Current implementation is not an independent no-ptrace backend.
- Shared-library and lifecycle coverage remain ptrace-owned.
- The preload/RPC design described in dependencies is not active.

## Conclusion

The audits demonstrate broad root-process Tool compatibility, but they do not
support a uniform B1.5 claim across all backends. Ptrace remains the complete
reference. LiteInst has the broadest non-ptrace selector coverage and a wired
coordinator RPC, with known startup/lifecycle gaps. E9patch supports all seven
production tools but remains a ptrace hybrid. DBI has a credible transport
implementation that is not wired into production process-tree execution.
SaBRe has working adapted tools and coordinator RPC with narrower coverage.
KVM executes real root Tools and direct host RPC, but its child lifecycle is
the dominant correctness gap.

Future benchmark work should use one release-built guest, one identical
counter2 implementation, CPU affinity, a common sample count, and a workload
long enough to separate fixed launcher cost from steady-state hook cost.
