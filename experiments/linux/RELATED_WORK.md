# Related Work: Deterministic Whole-System Linux Execution

_Prepared 2026-07-27 for task `research-related-work-linux-determ`._

This document surveys prior art in deterministic execution, record/replay, and
whole-system reproducibility, and situates Hermit's deterministic QEMU/Linux
boot against it. Sources are cited inline; every external claim links to a
primary or authoritative page. Our own result is reported with exact backend,
determinism level, kernel, and relaxations per the Hermit Communication
Precision rules — no unqualified "it works" claims.

## 1. Our Result (stated precisely)

Hermit runs an **unmodified Linux guest kernel plus userspace as a QEMU/TCG
process under the Hermit ptrace backend**, imposing deterministic thread
scheduling, virtual time, and I/O so the *entire virtual machine* executes
reproducibly. Determinism is imposed on QEMU's own host-level execution; the
guest OS is reproducible by construction, not by replaying a captured log.

Execution context (from `experiments/linux-vm-roadmap_20260726/` and
`hermit/docs/QEMU_BOOT.md`):

- **Backend:** ptrace (default, best-tested).
- **Emulator:** QEMU `10.1.0`, `-accel tcg,thread=single -smp 1`,
  `-icount shift=0,sleep=off` (a single instruction-derived virtual clock
  unifies guest TSC and device timers, avoiding PIT-calibration / TSC-watchdog
  / no-clocksource boot failures).
- **Guest kernel:** Linux `6.13.2` (bzImage). Host kernel is
  `6.17.13-0_fbk0_crackerjackhost` — the host and guest kernels are different;
  the "6.17.13" figure is the *host* that produced these runs, not the guest.

Best determinism evidence, bound to commits rather than a branch name:

| Workload | Level | Evidence | Notes |
| --- | --- | --- | --- |
| Cold boot to initramfs marker | **L2** (`--strict --verify`, ptrace) | Hermit `fe97efd`, 2026-07-24 | Bitwise-identical repeat run. |
| `scx_rlfifo` sched_ext + 4 CPU workers | **L2** (`--strict --verify`, ptrace) | Hermit `0c419bf` | 1,340,266 messages/run, per-run repeatable. |
| Cold boot verify (fail-open control) | verify only, **no `--strict`** | Hermit `54ff993` | `run --verify`: 1,130,696 messages, "no substantive differences found." A determinism-verify result, **not** a strict L2 claim. |

**Current-main caveat (honesty).** On current main (`fb5f2014`, 2026-07-26)
strict QEMU boot is **regressed**: PR #644 made `--strict` fail-closed on any
unsupported syscall, and QEMU issues an unsupported `seccomp(SECCOMP_SET_MODE_
FILTER, TSYNC, NULL)` capability probe at startup, so Detcore stops before
Linux boots. The strongest strict-L2 results above are therefore historical
(2026-07-24 tree) pending restoration, tracked as `p0_restore_qemu_strict`.
The "1M+ messages, zero differences" headline corresponds to the fail-open
`run --verify` control (1.13M messages) and the historical strict sched_ext run
(1.34M messages); it should not be read as a current-main strict L2 claim.

The distinctive property, relative to the prior art below, is **determinize-
forward whole-system execution**: two independent cold boots of the same VM
produce identical event streams *without recording a log first*, and the scope
is an entire guest OS (kernel + userspace) rather than a single application or
container.

## 2. Process-Level Record/Replay

### rr (Mozilla / rr-project)
- Sources: <https://rr-project.org/>,
  <https://github.com/rr-debugger/rr>,
  <https://en.wikipedia.org/wiki/Rr_(debugging)>.
- What it achieves: lightweight record-and-replay of Linux user-space processes
  for reverse debugging. Serializes threads onto a single core and uses
  `ptrace` plus hardware performance counters (retired-conditional-branch
  counts) to make replay deterministic.
- Comparison: rr's *mechanism* is the closest cousin to Hermit's ptrace
  backend — single-core serialization plus PMU/RCB-based preemption. But rr
  **records then replays** one captured execution for debugging; it does not
  make a *fresh* run deterministic and does not run a whole VM. Hermit
  `--strict` determinizes the forward execution itself, so two independent runs
  match (`--verify`), and here that guest is an entire QEMU/Linux system, not a
  single traced process.

### dettrace (Navarro Leija et al., ASPLOS 2020)
- Source: <https://krs85.github.io/dettrace.pdf> ("Reproducible Containers").
- What it achieves: deterministic, reproducible execution of normal Linux
  programs inside a container using `ptrace` to intercept and sanitize sources
  of nondeterminism (time, randomness, scheduling of syscalls, etc.).
- Comparison: dettrace is the direct intellectual predecessor of Hermit's
  Detcore approach. It determinizes *application processes* in a container but
  does not provide full deterministic thread scheduling with PMU preemption,
  and it never ran an entire guest OS. Hermit extends the determinize-forward
  container idea with a serialized deterministic scheduler and `--verify`
  bitwise checking, and applies it to QEMU running a complete Linux kernel.

## 3. Whole-System Record/Replay

### PANDA (MIT Lincoln Laboratory)
- Sources: <https://github.com/panda-re/panda>, <https://panda.re/>,
  <https://www.ll.mit.edu/r-d/projects/panda-platform-architecture-neutral-dynamic-analysis>,
  PyPANDA (NDSS): <https://www.ndss-symposium.org/ndss-paper/auto-draft-152/>.
- What it achieves: a QEMU-based Platform for Architecture-Neutral Dynamic
  Analysis with whole-system record/replay. It records an entire guest
  execution and deterministically replays it for reverse engineering, malware
  analysis, and taint tracking.
- Comparison: PANDA and Hermit both concern *whole-system* determinism over
  QEMU, but from opposite directions. PANDA instruments **inside** QEMU to
  record and replay a captured guest trace. Hermit runs **QEMU itself** as a
  deterministic guest process — determinism is imposed at the host boundary
  (QEMU's syscalls, threads, time), so the full VM is reproducible across
  independent runs with no recorded log. PANDA answers "replay what happened";
  Hermit answers "make every run the same, and diff two of them."

### ReVirt (Dunlap et al., OSDI 2002, U. Michigan)
- Sources:
  <https://www.usenix.org/conference/osdi-02/revirt-enabling-intrusion-analysis-through-virtual-machine-logging-and-replay>,
  <https://cs.nyu.edu/~mwalfish/classes/ut/s13-cs439/ref/dunlap02revirt.pdf>.
- What it achieves: VM-level logging and replay (on UMLinux) for intrusion
  analysis. Logs nondeterministic inputs/events so an entire VM can be replayed
  instruction-for-instruction after a compromise.
- Comparison: ReVirt pioneered whole-VM deterministic replay but is a
  **log-and-replay forensics** tool — it reproduces a recorded incident.
  Hermit is determinize-by-construction: the goal is that fresh boots are
  identical and races surface as divergences under chaos/schedule search, not
  that a past run can be reconstructed.

### SimuBoost (KIT / Karlsruhe, Bellosa group)
- Related context: full-system-simulation acceleration literature, e.g.
  <https://www.sigarch.org/the-return-of-rigorous-full-system-timing-simulation/>.
  (SimuBoost is the KIT project applying deterministic record/replay plus
  periodic checkpointing to parallelize slow full-system simulation.)
- What it achieves: records a workload once in a fast VM with periodic
  checkpoints, then replays checkpoint intervals *in parallel* inside a slow
  functional/timing simulator, using deterministic replay to guarantee each
  interval reproduces the recorded run.
- Comparison: SimuBoost treats determinism as a **means to accelerate
  simulation**. Hermit treats deterministic execution as the **product**
  (race discovery/localization, replay, schedule search, lower-overhead
  backends). Both rely on the same underlying guarantee — an interval replays
  identically — but Hermit does not need a prior recording to make a boot
  reproducible.

### Eidetic systems / Arnold (Devecsery et al., OSDI 2014, U. Michigan)
- Context/survey: "Deterministic Record-and-Replay," CACM 2025,
  <https://dl.acm.org/doi/10.1145/3724381>.
- What it achieves: an *eidetic* system (Arnold) records the entire lineage of
  all machine state over long periods so any past state can be replayed and
  data-provenance queries answered years later.
- Comparison: Arnold is whole-machine record/replay optimized for provenance
  and retrospection at massive time scales. Hermit shares the "reproduce
  execution" foundation but is forward-deterministic and interactive: the value
  is that the *next* run matches, enabling `--verify` and race localization,
  not archival lineage.

## 4. Deterministic OS / Deterministic Multithreading

These systems make concurrent *applications* deterministic; none runs an
unmodified whole guest OS the way Hermit-over-QEMU does.

- **dOS — Deterministic Process Groups** (Bergan et al., OSDI 2010):
  <https://www.usenix.org/conference/osdi10/deterministic-process-groups-dos>.
  An OS abstraction that makes an arbitrary group of processes execute
  deterministically. Kernel-integrated; scope is process groups, not a full
  guest kernel.
- **Determinator — Efficient System-Enforced Deterministic Parallelism**
  (Aviram et al., OSDI 2010):
  <https://web3.arxiv.org/pdf/1005.3450>,
  <https://explore.openaire.eu/search/publication?pid=10.1145%2F2160718.2160742>.
  A from-scratch OS whose parallel model is deterministic by design — requires
  its own OS/programming model rather than running stock Linux.
- **DMP / CoreDet / Kendo** — deterministic multithreading via hardware,
  compiler runtime, and software:
  Kendo <https://projects.csail.mit.edu/kendo/>;
  schedule-memoization line
  <https://llvm.org/pubs/2010-10-OSDI-DeterministicMT.html>;
  survey <https://www.academia.edu/123979074/Deterministic_Execution_for_Multicore_and_Cloud_Computing>.
  These deterministically schedule threads of a *single program*.
- Comparison: all of the above determinize application-level concurrency, often
  requiring recompilation, a special runtime, or a bespoke OS. Hermit
  determinizes an **arbitrary unmodified x86-64 binary** — here QEMU, which
  transitively carries an entire guest kernel and userspace — with no guest
  recompilation and no custom OS, using ptrace + PMU preemption + a serialized
  deterministic scheduler.

## 5. Summary Comparison

| System | Scope | Direction | Needs prior recording? | Guest OS unmodified? |
| --- | --- | --- | --- | --- |
| rr | Process | replay for debug | Yes | n/a (user process) |
| dettrace | Container/process | determinize-forward | No | n/a (user process) |
| PANDA | Whole system (QEMU) | record→replay | Yes | Yes |
| ReVirt | Whole VM | record→replay | Yes | Yes (UMLinux) |
| SimuBoost | Whole system (sim) | record→replay (parallel) | Yes | Yes |
| Arnold/eidetic | Whole machine | record→replay (archival) | Yes | Modified stack |
| dOS / Determinator | Process group / new OS | determinize-forward | No | No (custom OS/model) |
| DMP / CoreDet / Kendo | Multithreaded app | determinize-forward | No | n/a (app) |
| **Hermit + QEMU** | **Whole VM (kernel+userspace)** | **determinize-forward** | **No** | **Yes** |

Hermit occupies a corner that the prior art does not: **forward-deterministic,
whole-system, over an unmodified guest OS, without a prior recording.** The
whole-system record/replay systems (PANDA, ReVirt, SimuBoost, Arnold) reproduce
a *captured* execution; the forward-deterministic systems (dettrace, dOS,
Determinator, DMP/Kendo/CoreDet) target applications, containers, or a bespoke
OS. Hermit combines determinize-forward semantics with whole-system scope by
running the emulator itself as the deterministic guest, which is what makes
`--strict --verify` boot-to-boot bitwise comparison — and, ultimately, chaos
scheduling and schedule search across a full kernel — possible.

## 6. General References

- "Deterministic Record-and-Replay," Communications of the ACM, 2025 —
  <https://dl.acm.org/doi/10.1145/3724381> (umbrella survey).
- Record and replay debugging (overview) —
  <https://en.wikipedia.org/wiki/Record_and_replay_debugging>.
- Hermit determinism levels and QEMU boot procedure —
  `hermit/docs/QEMU_BOOT.md`; evidence and current-main regression analysis —
  `experiments/linux-vm-roadmap_20260726/README.md` and `metadata.json`.
