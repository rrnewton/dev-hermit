# SaBRe backend compatibility assessment

Status: point-in-time assessment on 2026-07-25.

This document assesses the code and live behavior on these exact public main
commits:

- Hermit: 94764e34a13309c21b8b55f3d52966de70a03a03
- Reverie: 16abb69f94237d2ebe2db4da229515ac07d1c05c
- SaBRe loader: 34065e7ddae6f1c90db7e0bf5c22a9aa89f9d605

Hermit main advanced to a46dd910fbcec0467780a15cc294815fd7382808 during
the assessment. That commit adds only KVM performance experiment files, so all
SaBRe-relevant source remains identical; the live evidence stays bound to 94764e34.

The assessment distinguishes syscall interception, functional compatibility,
and deterministic execution. Passing the first two does not establish the
third.

## Executive assessment

SaBRe currently works as an experimental, in-process syscall interception
runtime for many dynamically linked Linux x86-64 programs. It rewrites raw
SYSCALL instructions in selected ELF text sections, loads a plugin into the
guest, and can forward intercepted syscalls to the shared Reverie StraceTool.
This is more capable than libc interposition.

It is not a Detcore backend. There is no SaBRe implementation of the Reverie
Backend trait, no Detcore-over-SaBRe instantiation, and no Hermit Cargo
dependency on reverie-sabre. Hermit launches three separately built artifacts
through environment variables. The shared-tool adapter polls each async handler
once; any suspension other than the special tail_inject path fails or is
dropped. Detcore requires real suspension for scheduling, blocking syscalls,
RPC, timers, signals, and lifecycle coordination.

Hermit accepts --strict --verify with --backend sabre, but --strict does not
activate Detcore. --verify merely runs a successful guest twice and compares
exit status, stdout, and stderr. The command reports this limitation itself.
The strongest justified claim is L0 functional compatibility, not L1 or L2
determinism.

Under the repository backend-reality rubric, SaBRe is at most B1: a compiling
partial Guest/tool adapter. Its CLI spelling and broad compatibility do not
make it a real Detcore backend.

## Live evidence

| Command or check | Result |
| --- | --- |
| with-proxy cargo build --release -p hermit --bin hermit | PASS on Hermit 94764e34 |
| with-proxy cargo build --release -p reverie-sabre -p reverie-sabre-strace | PASS on Reverie 16abb69f |
| cmake configure/build of pinned SaBRe | PASS |
| hermit --backend sabre strace -- /bin/echo hello | PASS, exit 0, prints hello and raw syscall trace |
| hermit run --backend sabre --strict --verify -- /bin/echo hello | PASS, exit 0, two equal runs; explicitly says no Detcore determinization |
| hermit run --backend sabre --strict --verify -- /bin/false | Exit 1 after run 1; nonzero exits are not compared |
| with-proxy cargo test -p reverie-sabre | PASS, 41 passed, 0 failed |
| SaBRe/ptrace conformance run.sh all | PASS, 4/4 legs: thread_lifecycle and signal_forwarding on both runtimes |
| Static x86-64 probe | FAIL: pinned loader asserts at loader.c:351; Hermit reports exit 1 |

The current SaBRe validation corpus executed 159 rows:

- 156 PASS
- 3 FAIL: top, kill -0 1, and pgrep
- Overall validate.sh exit: 1

The three failures are process identity and process-introspection cases. The
SaBRe launch returns before Hermit's namespace/container path, so the guest
does not receive the same PID/proc view as the ptrace backend. kill -0 1
observed EPERM; top and pgrep did not find the expected guest shell identity.

There is also a separate validation bug: validate.sh still names and requires a
151-row SaBRe corpus. It runs all 159 current rows and then fails because
159 != 151. The observed program result is therefore 156/159, while the
blocking ratchet itself is red because its denominator is stale. Full session
log: /tmp/hermit-validate.oZIUUa.log.

The earlier 147/147 result recorded in Reverie's ASSESSMENT.md was real for its
older Hermit SHA, but it is not the current denominator and must not replace the
159-row measurement above.

## Current execution model

The live path is:

    hermit CLI
      -> reverie-sabre-strace host runner
      -> pinned SaBRe ELF loader
      -> libreverie_sabre_strace_plugin.so in the guest
      -> ReverieAdapter<StraceTool>
      -> Tool::handle_syscall_event
      -> Guest::inject / raw host syscall

Hermit discovers the runner, loader, and plugin through:

- HERMIT_SABRE_RUNNER
- HERMIT_SABRE_BINARY
- HERMIT_SABRE_PLUGIN

The Hermit process does not link reverie-sabre. Backend selection is a CLI
launch adapter, not Reverie Backend dispatch.

## What works

| Capability | Current state |
| --- | --- |
| Raw syscall interception | Works for SYSCALL instructions that the loader discovers and rewrites. It is not limited to libc wrappers. |
| Shared Reverie tool forwarding | Verified with StraceTool. Syscall, thread-start, and thread-exit callbacks are forwarded. |
| Immediate syscall execution | Guest::inject can issue ordinary syscalls synchronously. tail_inject has a special result handoff. |
| Guest memory | Direct LocalMemory access works because the plugin shares the guest address space. |
| Typed thread state | The adapter allocates T::ThreadState per observed native TID. |
| Dynamic programs | Broad current compatibility: 156/159 selected rows passed, including shells, language runtimes, toolchains, text utilities, and filesystem utilities. |
| Basic fork/exec lifecycle | execve re-enters the loader; fork/wait and shebang workloads have passed prior focused tests. |
| Runtime thread lifecycle | The native SaBRe conformance test passed 128 pthread create/return/join cycles. |
| Partial signals | The native runtime's signal_forwarding conformance leg passed. |
| Native SaBRe extensions | The separate synchronous API has RDTSC, selected VDSO, function-detour, and lifecycle hooks. These are not a shared Detcore event surface. |
| Exit propagation | The runner propagates guest status. The generic Hermit verify path, however, stops after the first nonzero run. |

## What does not work

### Async Reverie handlers cannot suspend

ReverieAdapter uses a no-op waker and polls each future exactly once.

- A pending syscall handler returns EIO unless tail_inject already stored a
  result.
- A pending handle_thread_start future is logged and dropped.
- A pending on_exit_thread future is logged and dropped.
- The future is not retained, scheduled, or resumed.

This is the central Detcore blocker. Detcore's first syscall action is an async
pre-handler hook. Its normal path awaits register reads, scheduler/global-state
operations, injected syscalls, blocking I/O, futex/poll/epoll waits, signal
coordination, and timeslice handoff. A first-poll adapter cannot preserve those
semantics.

### The Guest surface is only a compatibility subset

SabreGuest currently provides:

- pid and tid, but ppid is always None
- LocalMemory
- typed thread state
- a 4096-byte local scratch arena presented as Stack
- ordinary synchronous injection and the tail_inject special case

Important gaps are explicit:

- regs returns an all-zero register structure
- set_timer, set_timer_precise, and read_clock return ENOSYS
- direct injection of clone, clone3, fork, vfork, exit, and exit_group returns
  ENOSYS; separate SaBRe runtime wrappers handle only the original calls
- no PMU/RCB timer, CPUID event, backend-neutral RDTSC event, subscription,
  post-exec contract, or controllable signal-delivery event
- no remote isolation between guest memory and tool state

These gaps prevent correct Detcore preemption, register inspection, process
control, virtual time, and signal scheduling even if basic future suspension
were added.

### Hermit bypasses normal containment and determinization

Backend::Sabre returns from RunOpts before Hermit's with_container and
run_in_container paths. The SaBRe runner therefore executes directly in the
host namespaces rather than the normal PID, mount, network, UTS, and temporary
filesystem setup.

Consequences include:

- --strict config is validated but not applied
- process identity/proc behavior differs, as the top, kill, and pgrep failures
  demonstrate
- normal Hermit isolation claims do not apply
- host-visible side effects can occur during both verification runs
- many run options can be accepted even though run_sabre receives only the
  program, arguments, verify flag, and log level

The two-run comparison is useful smoke coverage, but it is not a sandbox,
record/replay, deterministic scheduling, or semantic syscall virtualization.

### Interception is broad but not fail closed

The pinned loader scans ELF .text sections and rewrites instructions it can
relocate. The current source returns without patching when a usable .text
section is absent. A comment claims missed calls will be caught by seccomp, but
the pinned source contains no seccomp installation.

Uncovered or unsupported cases include:

- static executables
- stripped or unusual ELF layouts without the expected sections
- arbitrary dynamically loaded objects outside the recognized scan path
- JIT or anonymous executable mappings
- raw instructions created after loader scanning
- calls made intentionally inside the loader/plugin recursion guard
- execveat
- non-x86-64 guests in the Reverie adapter

A missed syscall can execute natively without a fail-closed notification. This
alone prevents a general determinism claim.

### Runtime semantics remain partial

The native runtime is substantial, but its signals are not kernel-exact.
Handler masks, SA_NODEFER, SA_RESETHAND, alternate stacks, complete ucontext,
realtime ordering/payload, and synchronous fault mediation are incomplete.
Shared tools can observe only the subset translated by the adapter and cannot
suppress, replace, defer, or retarget signal delivery through a backend-neutral
contract.

Global RPC is blocking, reserves guest fd 100, and shares the guest's process
resources. Plugin formatting and state can allocate in the guest. The design is
not a production isolation boundary.

## Tools that can work now

The safe target is a tool whose handlers are immediately ready and whose
correctness does not depend on stopping other guest threads.

| Tool shape | Feasibility |
| --- | --- |
| Syscall logger such as StraceTool | Verified |
| Per-thread or atomic syscall counter | Straightforward, but no shared SaBRe CounterTool is currently shipped |
| Synchronous allow/deny policy | Feasible for rewritten syscalls if it returns immediately |
| Synchronous argument/result rewriting | Feasible when LocalMemory and one direct syscall are sufficient |
| tail_inject wrapper | Supported by the adapter's special pending-result path |
| Simple local-state profiler | Feasible for intercepted syscall boundaries |
| Small deterministic-value shim | Technically feasible for selected time/random/identity calls, but does not establish Hermit determinism because coverage, scheduling, signals, and containment remain open |
| Detcore or another scheduler/blocking tool | Not feasible with the current first-poll adapter |

The separate native reverie_sabre::Tool API can support additional synchronous
SaBRe-specific hooks. Such a tool is not portable across Reverie backends.

## Improvement roadmap

### P0: Make the existing compatibility claim accurate

1. Update the SaBRe gate denominator from 151 to the current selected corpus and
   record an evidence-based floor from measured results.
2. Diagnose or classify top, kill, and pgrep rather than hiding them behind the
   stale-count failure.
3. Rename output and documentation so --strict --verify cannot be mistaken for
   L2. Prefer an explicit compatibility mode, or reject --strict for SaBRe.
4. Compare nonzero exit statuses in both runs instead of stopping after run 1.
5. Add tests that prove SaBRe never appears in the real-Detcore backend table.

Exit criterion: one reproducible matrix with a stable denominator, exact
failures, and no determinism wording.

### P1: Contain and harden the useful synchronous backend

1. Route the SaBRe runner through Hermit's container/namespace setup, including
   artifact bind mounts and PID/proc semantics.
2. Reject every Hermit option that the launch adapter does not honor.
3. Add a shared CounterTool and tests for immediate-ready handlers, tail_inject,
   pending-handler EIO, pending lifecycle drops, fork, exec, and signals.
4. Provide one supported build/provenance command for the optional GPL loader
   and BSD Reverie runner/plugin.
5. Fail before guest execution for static or otherwise unsupported ELF inputs.

Exit criterion: tracing/counting runs inside the same containment envelope as
ptrace, and the complete compatibility corpus has only explicitly classified
gaps.

### P2: Prove or reject an async suspension architecture

Replace poll_once with a resumable executor design. A credible spike must:

1. retain a pending future and wake it later
2. park only the calling guest thread without blocking unrelated tool progress
3. let a host-side coordinator schedule and wake guest threads
4. survive fork and loader-mediated exec without inherited locks or stale
   wakers
5. support cancellation and process exit without dropping lifecycle futures
6. demonstrate a pending RPC, a blocking syscall, and one scheduler handoff

A dedicated host/controller executor plus blocking guest callback RPC is the
most plausible direction. If the spike cannot avoid deadlock and reentrancy
hazards, SaBRe should remain a synchronous-tool backend and Detcore integration
should stop here.

Exit criterion: a shared test Tool intentionally returns Pending, is later
woken, resumes exactly once, and completes across two guest threads plus fork.

### P3: Complete the Guest and event contract

After P2 succeeds:

1. expose the real syscall trampoline register frame
2. implement stable stack and parent/thread identity
3. implement timer, precise timer, and logical clock hooks
4. add PMU/RCB preemption or an explicitly weaker scheduling profile
5. virtualize CPUID and close RDTSC/RDTSCP coverage
6. add controllable signal delivery and exact lifecycle/post-exec events
7. support process-control injection safely
8. install a fail-closed syscall backstop and cover DSOs/JIT mappings, or reject
   them before execution
9. decide whether static binaries are supported by loader work or rejected as a
   permanent product boundary

Exit criterion: backend-neutral Guest conformance passes without zero-register
or ENOSYS compatibility stubs.

### P4: Integrate Detcore as a real backend

1. Add an actual SaBRe Backend implementation and direct, versioned Hermit
   linkage.
2. Construct Detcore with the SaBRe Guest/global-state path rather than
   StraceTool.
3. Route all CLI execution through the normal backend and containment
   machinery.
4. Run echo, true, and cat with real Detcore logs and --strict --verify.
5. Expand to the same fixed program denominator used by ptrace, KVM, and DBI.

Exit criterion: the backend-reality audit reaches B2 only after a real
Detcore/SaBRe path runs arbitrary requested programs. B3 requires at least 50
percent of the ptrace strict corpus. B4 requires full corpus parity.

### P5: Deterministic scheduling and production hardening

Only after P4:

1. add child-start barriers and deterministic run tokens
2. coordinate physical signals through a deterministic broker
3. implement PMU skid correction and exact preemption stress tests
4. validate multithreaded shared-memory workloads, blocking I/O, clone/exec,
   signals, and pure userspace loops
5. isolate or integrity-protect in-guest tool/RPC state
6. run long stress and cross-host CPU-model tests

This is a multi-quarter parity project, not a small adapter patch.

## Reproduction

The exact current-main measurement used these task-local slots:

    cd $HOME/work/dev-hermit/worktrees_reverie/slot111
    with-proxy scripts/backend-submodule.sh activate sabre
    cmake -S third-party/sabre -B target/sabre-assessment
    cmake --build target/sabre-assessment -j2
    with-proxy cargo build --release -p reverie-sabre -p reverie-sabre-strace
    with-proxy cargo test -p reverie-sabre
    with-proxy cargo build -p riptrace -p riptrace-tool
    env SABRE_BINARY=target/sabre-assessment/sabre timeout 180 experimental/reverie-sabre/conformance/run.sh all

    cd $HOME/work/dev-hermit/worktrees/slot129
    with-proxy cargo build --release -p hermit --bin hermit
    env HERMIT_SABRE_RUNNER=$HOME/work/dev-hermit/worktrees_reverie/slot111/target/release/reverie-sabre-strace \
        HERMIT_SABRE_BINARY=$HOME/work/dev-hermit/worktrees_reverie/slot111/target/sabre-assessment/sabre \
        HERMIT_SABRE_PLUGIN=$HOME/work/dev-hermit/worktrees_reverie/slot111/target/release/libreverie_sabre_strace_plugin.so \
        VALIDATE_GATE_TIMEOUT_SECONDS=1800 \
        with-proxy ./validate.sh --sabre-compat-only

The task-local build directories and /tmp log are not durable artifacts. The
three repository SHAs and commands above are the reproducible evidence.

## Recommended product position

Keep SaBRe. It is useful today for low-overhead syscall tracing, compatibility
experiments, and simple synchronous tools over dynamically linked x86-64
programs.

Do not count it as a real Hermit deterministic backend, do not include it in
the ptrace/KVM/DBI Detcore compatibility table, and do not label its successful
two-run comparison L2. Present it as an experimental SaBRe compatibility
runtime until P2 and P3 prove that Detcore's async and Guest contracts can be
implemented without weakening their semantics.
