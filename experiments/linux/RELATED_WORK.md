# Related Work: Deterministic Whole-System Linux Execution

_Updated 2026-07-27 for task `impl-related-work-cleanup`._

This survey separates two properties that are often grouped under the word
"deterministic":

1. **A-priori determinism.** Given the same program, inputs, seed, and system
   configuration, each of N independent executions follows the same behavior.
   The machines do not exchange a recording. Hermit, dettrace, Dthreads, dOS,
   Determinator, DMP, CoreDet, and Kendo target this property at different
   scopes.
2. **Deterministic replay.** The first execution is allowed to be
   nondeterministic. The system records the choices or external events that
   occurred, and later executions reproduce that particular trace. rr, PANDA,
   ReVirt, SimuBoost, and Arnold target this property.

The distinction matters operationally. A replay system can reproduce a boot
only after receiving that boot's snapshot or event log. An a-priori
deterministic system aims to make fresh executions agree without sharing such
an artifact.

**Literature finding.** In the primary sources reviewed here, we found no
earlier published demonstration of an unmodified Linux kernel cold-booting on
the first execution with the same execution across independent machines,
without a prior recording. Whole-system systems such as PANDA and ReVirt
replay a captured boot; a-priori systems before Hermit target applications,
process groups, containers, or a purpose-built OS. This is a bounded literature
finding, not a claim that no unpublished or differently framed experiment has
ever done it.

## 1. Hermit + QEMU (2022-present; whole-VM result in 2026)

Hermit runs an **unmodified Linux guest kernel and userspace inside QEMU/TCG**
while determinizing QEMU's host-level process. Hermit controls thread
scheduling, time, randomness, and supported I/O at QEMU's Linux interface;
QEMU's fixed instruction-count clock (`-icount shift=0,sleep=off`) supplies one
coherent guest clock. The guest boot is a fresh forward execution, not a replay
of a previous VM log.

The maintained configuration is:

- **Backend:** Hermit's ptrace backend with serialized scheduling.
- **Emulator:** QEMU 10.1.0, `-accel tcg,thread=single -smp 1`, fixed icount.
- **Guest:** Linux 6.17.13 bzImage plus a minimal initramfs.
- **Oracle:** the guest prints `SHARED_FUTEX_QEMU_KERNEL_OK` and powers off.

Evidence is bound to exact revisions:

| Workload | Assurance | Evidence |
| --- | --- | --- |
| Cold boot to the initramfs marker | L2: `run --strict --verify`, ptrace, no scheduling relaxation | Hermit `fe97efd`, 2026-07-24 |
| Strict cold boot to marker and poweroff | L1: `run --strict`, ptrace, no scheduling relaxation | Hermit `dd60278f`, 166.486 s |

Boot and verification scripts are maintained at `experiments/linux/strict_l2_test.sh` and `hermit/docs/QEMU_BOOT.md`.

`--strict` and `--verify` establish different facts. `--strict` makes the
forward run fail closed outside Hermit's supported deterministic boundary.
`--verify` executes the workload twice and compares its event streams. The L2
result therefore demonstrates two independent cold boots with no recording;
it is not record/replay.

A longer, accessible explanation of Hermit's design and goals can be found in the 2022 blog post, ["Hermit: Deterministic Linux for Controlled Testing and Software Bug-finding"](https://developers.facebook.com/blog/post/2022/11/22/hermit-deterministic-linux-testing/).
It states the a-priori goal directly: a network-free program should execute
identically irrespective of time and place. Hermit grew from the dettrace
prototype described in the ASPLOS 2020 paper
["Reproducible Containers"](https://doi.org/10.1145/3373376.3378519).

The existing QEMU evidence used independent executions on the evidence host.
A literal N-host experiment using identical Hermit, QEMU, kernel, initramfs,
and configuration artifacts would be the strongest direct validation of the
cross-machine part of the a-priori contract.

## 2. A-Priori Deterministic Execution

### dettrace / Reproducible Containers (2020)

- Sources: [ASPLOS 2020 paper](https://doi.org/10.1145/3373376.3378519),
  [author PDF](https://krs85.github.io/dettrace.pdf).
- **Class:** a-priori determinism; no recording is required.
- **Scope:** normal Linux applications in a container. dettrace uses `ptrace`
  to intercept and normalize time, randomness, filesystem observations, and
  syscall ordering.
- **Boundary:** application/container processes, not a guest kernel. It does
  not provide Hermit's PMU-driven preemption of CPU-bound threads.
- **Relation to Hermit:** direct predecessor. Hermit retains the userspace
  determinization model, adds a deterministic scheduler and verification, and
  applies the model to QEMU as an arbitrary Linux process.

### dOS: Deterministic Process Groups (2010)

- Source: [OSDI 2010](https://www.usenix.org/conference/osdi10/deterministic-process-groups-dos).
- **Class:** a-priori determinism.
- **Scope:** an OS abstraction for deterministic groups of communicating
  processes. It requires kernel support and does not determinize a stock guest
  Linux kernel as a whole.

### Determinator (2010)

- Source: ["Efficient System-Enforced Deterministic
  Parallelism"](https://www.usenix.org/legacy/events/osdi10/tech/full_papers/Aviram.pdf).
- **Class:** a-priori determinism.
- **Scope:** a purpose-built operating system and parallel programming model.
  Determinator makes its own applications deterministic; it is not a method
  for running an unmodified Linux kernel deterministically.

### Dthreads (2011)

- Source: [SOSP 2011](https://doi.org/10.1145/2043556.2043587).
- **Class:** a-priori determinism.
- **Scope:** a deterministic replacement for the pthreads runtime. It isolates
  threads and merges state at synchronization boundaries, targeting
  multithreaded applications rather than an operating-system boot.

### DMP (2009), CoreDet (2010), and Kendo (2009)

- Sources: [DMP](https://doi.org/10.1145/1508244.1508255),
  [CoreDet](https://doi.org/10.1145/1736020.1736029),
  [Kendo](https://doi.org/10.1145/1508244.1508256).
- **Class:** a-priori deterministic multithreading.
- **Scope:** DMP explores deterministic shared-memory multiprocessing;
  CoreDet combines compiler and runtime support; Kendo determinizes lock-based
  programs in software. Their unit of control is a parallel application, not
  an unmodified guest OS and its devices.

## 3. Deterministic Replay

### rr (2014-present)

- Sources: <https://rr-project.org/>, <https://github.com/rr-debugger/rr>.
- **Class:** deterministic replay.
- **Scope:** Linux userspace processes for reverse debugging. rr serializes
  threads on one core and combines `ptrace`, syscall recording, and retired
  conditional branch counters to return to recorded asynchronous events.
- **Distinction:** rr does not constrain two fresh executions to choose the
  same schedule or inputs. It records one execution and makes replay match that
  execution. Hermit's ptrace/PMU mechanics are related, but its forward run is
  determinized before `--verify` compares independent executions.

### PANDA (2013-present)

- Sources: <https://panda.re/>, <https://github.com/panda-re/panda>,
  [PANDA manual: Record/Replay
  Details](https://github.com/panda-re/panda/blob/dev/panda/docs/manual.md#recordreplay-details),
  [MIT Lincoln Laboratory overview](https://www.ll.mit.edu/r-d/projects/panda-platform-architecture-neutral-dynamic-analysis).
- **Class:** deterministic whole-system replay and dynamic analysis.
- **Scope:** PANDA is built into QEMU, so plugins can observe all guest code and
  data. Its current documentation identifies whole-system replay for x86,
  x86-64, and ARM, plus OS introspection, taint analysis, time-travel
  debugging, and an architecture-neutral callback/plugin framework.
- **Recording contract:** `begin_record` produces two required artifacts:
  `<name>-rr-snp`, the VM snapshot at the beginning of the recording, and
  `<name>-rr-nondet.log`, the log of nondeterministic changes crossing the
  CPU/RAM boundary (for example DMA, interrupts, and input instructions).
  `panda-system-$arch -replay <name>` consumes both artifacts. The official
  README emphasizes compact, shareable logs and repeatable analysis.
- **Portability boundary:** PANDA avoids trace-format changes, but its README
  guarantees replay only between PANDA builds with the same address length.
  That is trace portability, not a guarantee that an unrecorded run on another
  host independently makes the same choices.
- **Distinction:** PANDA can record and replay an entire Linux execution,
  including a boot. The first run remains the event source. Hermit instead
  determinizes QEMU's host interface so a fresh boot is the comparison unit.

### ReVirt (2002)

- Source: [OSDI 2002](https://www.usenix.org/conference/osdi-02/revirt-enabling-intrusion-analysis-through-virtual-machine-logging-and-replay).
- **Class:** whole-VM deterministic replay.
- **Scope:** logs nondeterministic events in a UMLinux virtual machine for
  instruction-level intrusion analysis. It reconstructs a recorded incident;
  it does not prescribe the outcome of a fresh boot.

### SimuBoost (2019)

- Source: [KIT dissertation, "SimuBoost: Scalable Parallelization of Functional
  System Simulation"](https://publikationen.bibliothek.kit.edu/1000097700).
- **Class:** checkpointed deterministic replay.
- **Scope:** runs a workload first in a fast hardware-assisted VM, creates
  periodic checkpoints, then replays intervals in parallel in a slower
  functional simulator. Heterogeneous deterministic replay ensures each
  interval reproduces the recorded predecessor state. A Linux build is an
  evaluation workload, not an a-priori deterministic Linux boot claim.

### Arnold / Eidetic Systems (2014)

- Source: [OSDI 2014](https://www.usenix.org/conference/osdi14/technical-sessions/presentation/devecsery).
- **Class:** long-horizon whole-system record/replay.
- **Scope:** records machine-state lineage so past states can be reconstructed
  and queried. Its objective is retrospective provenance, not making the next
  independent run choose the same execution.

## 4. Comparison

| System | Years | Scope | Class | Prior recording? | Fresh unmodified Linux boot? |
| --- | --- | --- | --- | --- | --- |
| Hermit + QEMU | 2022-present; VM result 2026 | Whole VM | A-priori determinism | No | **Demonstrated without replay** |
| dettrace | 2020 | Process/container | A-priori determinism | No | No |
| dOS | 2010 | Process group | A-priori determinism | No | No |
| Determinator | 2010 | Purpose-built OS | A-priori determinism | No | No; custom OS |
| Dthreads | 2011 | Multithreaded app | A-priori determinism | No | No |
| DMP / CoreDet / Kendo | 2009-2010 | Multithreaded app | A-priori determinism | No | No |
| rr | 2014-present | Process | Deterministic replay | Yes | No |
| PANDA | 2013-present | Whole system / QEMU | Deterministic replay | Yes: snapshot + nondeterminism log | Replay only |
| ReVirt | 2002 | Whole VM | Deterministic replay | Yes | Replay only |
| SimuBoost | 2019 | Whole-system simulation | Deterministic replay | Yes: checkpoints + trace | Replay only |
| Arnold | 2014 | Whole machine | Deterministic replay | Yes | Replay only |

The empty quadrant in the reviewed literature is **a-priori determinism at
whole-system scope over an unmodified guest OS**. Hermit reaches that quadrant
by treating QEMU as the determinized process. PANDA and ReVirt provide deeper
whole-system replay facilities, but their reproducibility is conditional on a
recorded execution. Dthreads, dOS, Determinator, DMP, CoreDet, Kendo, and
dettrace avoid a prior recording, but stop at an application, process group,
container, or custom OS boundary.

## 5. Answer to the Linux-Boot Question

**Has another published system deterministically booted an unmodified Linux
kernel on the first run, reproducibly across N independent machines, without
sharing a recording?** Not in the primary literature reviewed for this survey.

That conclusion has three precise limits:

1. PANDA, ReVirt, SimuBoost, and related whole-system tools can reproduce Linux
   executions, but only from a recording or checkpoint lineage.
2. Earlier a-priori systems can make concurrent programs reproducible, but do
   not claim this whole-system, unmodified-Linux scope.
3. Hermit's current evidence establishes independent no-recording cold boots;
   publishing an N-host run with content-addressed Hermit, QEMU, kernel, and
   initramfs inputs would make the cross-machine comparison explicit.

## 6. General References

- Meta, ["Hermit: Deterministic Linux for Controlled Testing and Software
  Bug-finding" (2022)](https://developers.facebook.com/blog/post/2022/11/22/hermit-deterministic-linux-testing/).
- Navarro Leija et al., ["Reproducible Containers" (ASPLOS
  2020)](https://doi.org/10.1145/3373376.3378519).
- ["Deterministic Record-and-Replay" (Communications of the ACM,
  2025)](https://dl.acm.org/doi/10.1145/3724381).
- Hermit QEMU procedure and evidence: `hermit/docs/QEMU_BOOT.md`,
  `experiments/linux/README.md`, and `experiments/linux/strict_l2_test.sh`.
