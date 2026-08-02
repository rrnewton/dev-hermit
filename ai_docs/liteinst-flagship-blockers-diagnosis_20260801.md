# LiteInst flagship blockers: source and corpus diagnosis

Date: 2026-08-01
Owner task: `liteinst-flagship-blockers-diagnosis`

## Bottom line

The scorecard's **60% parity / 66% determinism** cells are arithmetically real,
but they are percentages of the **179 ptrace-L2-green rows**, not of all 200
manifest rows:

| metric | scorecard-table denominator | full-200 denominator |
|---|---:|---:|
| parity | 108/179 = **60.3%** | 108/200 = **54.0%** |
| L2 determinism | 118/179 = **65.9%** | 118/200 = **59.0%** |

A row-by-row join to the exact ptrace CSV confirms that all 108 parity
numerators and all 118 determinism numerators are in the 179 ptrace-green set;
zero numerator rows come from ptrace-red cells or empty-output coincidences.

The exact run is `liteinst-fullcorpus-scorecard` at Hermit
`82a8e853357584a3a567fd80812e015572a607c7` and Reverie
`a4f33d69a56ed4233a53b218c39d93807ffc8cd0`. It attempted all 200 rows; it
did not turn topology failures into passes. The 60/66 rendering deliberately
uses ptrace's 179 green rows as the cross-backend reference denominator. The
same report separately and correctly prints `det 118/200 (59%), parity 108`.

The measured Hermit backend is **not the standalone in-guest Tool path**.
It is the **ptrace-host hybrid**:

```text
hermit --backend liteinst
  -> LiteinstBackend::run_host_with_preload::<Detcore>
  -> reverie_ptrace::TracerBuilder::<Detcore>
       owns Tool + GlobalTool in the host
  -> LD_PRELOAD DSO in the tracee
       discovers/patches sites and emits traps back to ptrace
```

So both halves of the earlier shorthand need correction:

- **LD_PRELOAD is active**, not parked: the tracee loads the LiteInst patching
  runtime.
- The **in-guest local Detcore Tool path is parked/unwired in Hermit**: the
  scorecard's Detcore Tool and GlobalTool live in the ptrace host.

The fastest route to a broadly working flagship is a **LiteInst-over-hybrid**:
keep a minimal ptrace supervisor for pre-main, exec/clone lifecycle, vDSO, and
unpatchable-site fallback, but run the common Reverie Tool locally at patched
sites and use the existing common global-state RPC transport. That is distinct
from today's host hybrid, where every patched syscall still traps to the
host-owned Tool.

## 1. Scorecard provenance and denominator

The recorded runner is
[`sweep-liteinst.sh`](../experiments/ptrace_fullcorpus_scorecard_20260801/sweep-liteinst.sh).
For each of the 200 manifest cells it runs:

1. `hermit run --backend liteinst --strict ...` for exit/stdout parity against
   the companion ptrace reference;
2. `hermit run --backend liteinst --strict --verify ...` for L2 DETLOG
   determinism;
3. portable cells with `--no-virtualize-cpuid --max-timeslice=disabled`;
4. a 120-second verify timeout, with failures/timeouts counted as non-passes.

The raw result and exact SHAs are in
[`scorecard-liteinst.csv`](../experiments/ptrace_fullcorpus_scorecard_20260801/scorecard-liteinst.csv).
The two denominator presentations are explicit in
[`REPORT.md`](../compat-envelope/REPORT.md): the table says it renders each
backend as a fraction of the ptrace-green count (179), while the headline row
says `118/200` and parity `108`.

A later topology-gated corroboration at Hermit `464cbd9f` / Reverie `aa6f1283`
reported 107/200 parity and 117/200 L2, a one-cell difference caused by a
ptrace-reference timeout on `lsm-get-self-attr-enosys`. The open, unlanded
Hermit PR #1397 would repair a different one-cell `arch_prctl` failure. These
runs must not be combined into a fictitious landed numerator.

**Verdict:** 60/66 is not fabricated, but saying “60%/66% of the full corpus”
is wrong. The honest full-200 headline for that exact run is **54%/59%**.

## 2. What actually executes

At the exact Hermit scorecard SHA,
`hermit-cli/src/lib.rs:1520-1526` explicitly calls
`LiteinstBackend::run_host_with_preload::<Detcore>`; the captured-output path
does the same at `:1628-1635`. `hermit-cli/src/bin/hermit/run.rs:1545-1549`
prints “Detcore Tool active in ptrace host.”

At the exact Reverie pin, `reverie-liteinst/src/backend.rs:196-229` documents
the contract and implements it with `TracerBuilder::<T>`:

- ptrace owns the sole Tool and GlobalTool from exec onward;
- the preload contributes dynamic site installation and hot-site traps;
- the slice supports one tracee process with one thread.

There is a second, real path in the same file:

- `Backend::run` at `:389-400` calls `run_with_preload`;
- `launch<T>` at `:466-559` starts a plain child with `LD_PRELOAD` and a
  coordinator socket;
- `runtime.rs:1469`'s `installed_syscall_hook` constructs an in-guest event;
- `runtime.rs:1666-1668` routes it to `tool_host::dispatch`;
- `tool_host.rs:105-135` constructs the local Tool.

Hermit bypasses that trait method. Reverie tests exercise it with test tools,
but the scored Hermit Detcore corpus does not.

This is the precise reconciliation with the earlier “ptrace-host + preload
parked” report: **the DSO/patcher is live; local Tool ownership is parked.**

## 3. How ptrace fallback works

Ptrace is primary in the scored backend, not merely an emergency attachment.
There are two site-level routes:

1. **Patch succeeds.** The first seccomp stop calls the stopped-tracee installer
   (`reverie-ptrace/src/task.rs:3475-3515`). The DSO installs a liteinst2 hook
   (`runtime.rs:1076-1151`). Later hits enter `host_syscall_hook`
   (`runtime.rs:1451-1466`), which emits the validated host marker. Ptrace
   recognizes it (`task.rs:2204-2276`) and calls `handle_injected_syscall`,
   which dispatches to the host Tool (`task.rs:2005-2082`). Thus the patch
   avoids executing the original syscall instruction, but it does **not** avoid
   a ptrace stop or move Tool execution into the guest.
2. **Patch fails or the site cannot be claimed.** The DSO records
   `SITE_FALLBACK` and returns `EOPNOTSUPP` from the installer
   (`runtime.rs:1103-1151`). The current syscall is still serviced exactly once
   by the host Tool. The attempted-site set prevents repeated installer calls,
   and later executions remain on the ordinary seccomp/ptrace host-Tool path
   (`task.rs:3618-3686`).

Mapping mutation invalidates attempted-site provenance so a new mapping
generation can be reconsidered. Process/thread creation is not a site fallback:
the current host hybrid deliberately returns `ENOTSUPP` on every new task
(`task.rs:3823-3873`).

The desired patch-site policy remains:

1. direct pun -> patch;
2. no direct pun, upstream relocation found -> relocate and patch;
3. straddler with no pun/upstream site -> do not use the expensive guarded
   split protocol; leave it on ptrace;
4. every other unsafe/unknown case -> documented ptrace fallback.

LiteInst2 draft PR #16 documents this tree. The Rust port does not yet implement
the PLDI'17 upstream scan, so branches 2 and 3 are not yet fully distinguished
in production. Instrumentation statistics are needed before assigning a
frequency to the straddler branch.

## 4. Global state and RPC

The scored ptrace-host path uses **neither a common socket RPC transport nor a
bespoke socket protocol**. It does not need cross-process RPC:

- `TracerBuilder::spawn` initializes GlobalTool in the host and stores it in an
  `Arc` (`reverie-ptrace/src/tracer.rs:2161-2178`);
- `TracedTask` implements `GlobalRPC` by directly calling
  `gs_ref.receive_rpc` in the same address space
  (`reverie-ptrace/src/task.rs:5383-5417`).

The parked native/in-guest path **does use the common library**, not a private
protocol:

- host: `reverie_rpc_transport::RpcServer` in `backend.rs:493-501`;
- guest: `CoordinatorRpc` wraps the shared `reverie_preload::rpc::CoordinatorClient`
  in `rpc.rs:67-115`;
- local Tool: `tool_host.rs:105-135`.

Therefore a LiteInst-over-hybrid implementation should reuse this common
transport for GlobalTool state rather than add another RPC stack.

## 5. Are the passing cells substantive?

“Trivial” is subjective, so the 118 exact L2 passers were classified into
three auditable source/intent bins. The parity counts use the same raw CSV.

| passing-cell class | L2 | parity | definition |
|---|---:|---:|---|
| expected-error/refusal | **47** | **47** | ENOSYS, EPERM, EOPNOTSUPP, invalid-argument, or equivalent refusal is the asserted result |
| lightweight leaf/smoke | **16** | **14** | one-shot identity/CPU/time/meminfo/sysinfo/uname/debuggee observations with no substantial state machine |
| stateful substantive | **55** | **47** | successful multi-step state mutation, delivery, data movement, or protocol behavior |
| total | **118** | **108** | |

On the broad definition that includes both negative probes and leaf queries,
**63/118 (53.4%) are lightweight/trivial and 55/118 (46.6%) are substantive**.
On the strictest definition, the irreducibly trivial floor is the 47
expected-error cells.

The 55 substantive cells break down as:

- 23 network/socket/IPC protocol cells;
- 11 file-descriptor, file, procfs, and PTY state cells;
- 8 timer/signal-delivery cells;
- 7 mapping/executable-memory/register-ABI cells;
- 6 scheduler/resource/process-local state cells.

Examples include mmap stress, executable mmap, SCM_RIGHTS+mmap, timer delivery,
signal delivery, file I/O+metadata, proc fd state, netlink autobind, TCP_INFO,
socket cookies/timestamps, scheduler policy mutation, and Unix autobind. The
headline is therefore not *only* `/bin/true`-style smoke, but roughly half of
its passes are cheap negative or leaf probes.

## 6. Single-process/single-thread ceiling

The topology-gated audit first ran ptrace strict with a machine-readable
execution summary. Among rows whose reference completed it observed 50 cells
outside SP/ST:

- **26 multiprocess**;
- **24 single-process multithreaded**.

That empirical 50 is a lower bound because 22 rows were static or had a failed
ptrace reference. Source inspection of those rows finds 12 additional workloads
that inherently create a process or thread:

- unclassified (10): `robust-futex-test`, `ipc-determinism`,
  `liteinst-advanced`, `nanosleep-threads-simple`, `signal-determinism`,
  `thread-sync-determinism`, `determinism-stress-c/thread-contention`,
  `determinism-stress/process-chains`,
  `determinism-stress/thread-contention`, and `pmu-skid`;
- static (2): `racewrite-nostdlib` and `qemu-net-init`.

No topology-bearing cell appears among the 118 passes. Therefore:

- **62/200 inherently require multiprocess and/or multithread support**;
- the honest topology-only SP/ST ceiling is at most **138/200 = 69%**;
- the current 118 L2 passes are **85.5% of that topology-reachable ceiling**;
- only **20 SP/ST-reachable rows** remain before topology becomes the absolute
  wall.

The looser `150/200` number obtained from only the 50 successfully classified
rows is not the real ceiling; it ignores topology visible in the sources of
static/reference-failed rows.

The 20 remaining SP/ST-reachable non-passes are:

- 4 static SP/ST fixtures that cannot load an LD_PRELOAD DSO;
- 5 SP/ST rows whose ptrace reference failed, so LiteInst cannot honestly be
  charged or credited;
- 11 measured dynamic SP/ST failures: seven post-start exec/interpreter cases,
  one GS-base preservation bug (open PR #1397), two time/timestamp/uptime gaps,
  and one fd-close/lifecycle gap.

## 7. Ranked blockers

### 1. Process/thread lifecycle: largest coverage unlock (62 cells)

The host hybrid's patch-helper stack, bootstrap phase, attempted-site set,
active-hook provenance, tracee identity, cleanup, and signal ownership are
process-global. `handle_new_task` records the child and then deliberately fails
closed. Supporting fork/vfork/clone/threads requires per-process runtime state,
per-thread patch/trap frames, inherited-hook accounting, Detcore lifecycle
callbacks, and correct parent/child stop ownership.

This is the largest corpus lever and is mandatory before B3 can represent broad
program behavior rather than an SP/ST slice.

### 2. Move Tool execution into the guest, with a minimal supervisor: flagship unlock

The trampoline-to-local-Tool machinery is not missing; it exists in
`installed_syscall_hook -> tool_host::dispatch`. What is missing is a Hermit
Detcore launch path that can use it safely for arbitrary workloads. Today's
patched path still traps to ptrace on every syscall, so it does not deliver the
flagship architecture or expected hot-path benefit.

The recommended fast route is the LiteInst-over-hybrid design:

- local Tool dispatch at successfully patched sites;
- existing common RPC transport for GlobalTool state;
- ptrace supervisor only for loader/pre-main coverage, exec/clone lifecycle,
  vDSO discovery, and unpatchable-site slow paths;
- backend-neutral lifecycle primitives shared with SaBRe, with thin
  instrumentation adapters rather than copied SaBRe code.

This is faster and safer than first trying to make pure constructor-only
LD_PRELOAD own every lifecycle edge.

### 3. Exec, static ELF, and pre-constructor coverage (at least 11 SP/ST cells)

Pure LD_PRELOAD cannot instrument a static image and cannot see loader syscalls
before its constructor. The native runtime explicitly rejects `execve` and
`execveat` (`runtime.rs:1648-1658`). Interpreter and utility fixtures enter
through a shell and then exec Python, gawk, OpenSSL, or another target, so they
are single-process yet still fail. The hybrid supervisor can rebootstrap the
runtime at exec and handle pre-main gaps.

### 4. vDSO, RCB timer/preemption, and residual semantics

The native guest adapter explicitly returns Unsupported from `set_timer`,
`set_timer_precise`, and `read_clock` (`tool_host.rs:616-637`). A pure preload
path also needs vDSO interception and careful signal coexistence. These are
required for clock/time parity and for deterministic preemption, even after
process lifecycle works.

### 5. Patch-site completeness and observability

Implement the PLDI'17 upstream relocation scan, keep the safe ptrace bailout
for straddlers and all unknown cases, and land the instrumentation stats. This
improves hot-site coverage and quantifies slow-path frequency, but it is not the
reason 62 topology cells fail and is not a substitute for lifecycle work.

### 6. Repair the five SP/ST reference failures

These cells currently lack a valid ptrace oracle. Fixing their fixtures or
reference behavior is necessary for honest credit, but it is scorecard hygiene,
not a LiteInst architecture fix.

## 8. Pure native versus SaBRe-style hybrid

| path | advantages | blockers/risk | recommendation |
|---|---|---|---|
| pure constructor-only LD_PRELOAD | no ptrace hot path; existing local Tool and common RPC code | misses pre-constructor loader activity and static ELF; exec rebootstrap, vDSO, signal ownership, clone/thread state, and RCB timers all need new lifecycle machinery | valuable end-state/optimization, not fastest broad backend |
| LiteInst-over-hybrid | reuses liteinst2 patcher/local callback/common RPC; ptrace supplies lifecycle and safe per-site fallback | must clearly separate supervisor slow path from local Tool fast path and factor lifecycle code without duplicating SaBRe | **fastest route to a working flagship** |
| current ptrace-host hybrid | already real and correct for much of SP/ST corpus | Tool remains in host; every hot-site callback traps; all new tasks fail closed | retain as correctness baseline, not flagship end-state |

The LD_PRELOAD trampoline-to-in-guest-callback strategy is therefore **not
itself blocked**. It is implemented but not selected by Hermit for Detcore. The
blocker is safely combining that local fast path with lifecycle/global-state
semantics broad enough for real workloads. A minimal hybrid closes that gap
sooner than a pure-native rewrite.

`-fpatchable-function-entry` is also not the blocker. It only helps code built
with that contract and does not solve arbitrary DSOs, loader/pre-main syscalls,
exec/clone, vDSO, or static binaries. Runtime discovery/relocation plus a defined
ptrace fallback remains necessary.

## Evidence inventory

- raw 200-cell scorecard:
  `experiments/ptrace_fullcorpus_scorecard_20260801/scorecard-liteinst.csv`
- scorecard runner and rendered denominator:
  `experiments/ptrace_fullcorpus_scorecard_20260801/sweep-liteinst.sh`,
  `compat-envelope/REPORT.md`
- topology-gated corroboration:
  `experiments/liteinst_fullcorpus_scorecard_20260801/results/results.csv`,
  `experiments/liteinst_fullcorpus_scorecard_20260801/RESULTS.md`
- Hermit scored call path: commit `82a8e853`,
  `hermit-cli/src/lib.rs:1520-1535,1628-1649`
- Reverie scored runtime: commit `a4f33d69`,
  `reverie-liteinst/src/backend.rs`, `runtime.rs`, `tool_host.rs`, `rpc.rs`,
  and `reverie-ptrace/src/{tracer,task}.rs`
- unlanded one-cell GS fix: rrnewton/hermit PR #1397,
  `3cf7e1ca74583afaf79c2be5204f767d2a133edf`
- patch-site decision tree: rrnewton/liteinst2 draft PR #16,
  `60126577b9aef9e57fcd15476008f6e601a45126`
