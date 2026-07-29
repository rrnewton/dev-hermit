# Demo07 option 3: external drgn task_struct read succeeds

Task: `demo07-drgn-deterministic-kernel`
Date: 2026-07-29
Agent: hermit-220
Tested Hermit: `f06e576493596429a21489c406cb7398f56bb189` (detached slot SHA)

## Result

Option 3 works end to end. A separate same-UID process can read the physical
RAM of QEMU while Hermit already ptrace-traces QEMU, without a second ptrace
attach. drgn 0.2.0 consumed that external physical-memory callback, the live
guest's VMCOREINFO, and the exact BuildID-matched fbk 6.18.39 vmlinux. It read
`init_task` and walked the live `task_struct.tasks` list.

This makes option 2 (ephemeral Hermit fork plus `dump-guest-memory`) unnecessary
for the demo07 read-only kernel-introspection goal. Option 1 (live gdbstub/QMP)
remains inferior because it requires QEMU execution or special virtual-time
compensation.

## Quiescent, zero-guest-execution observation

The research probe waits for QEMU state `t` (ptrace-stop), reads QEMU's actual
`TracerPid`, resolves that tracer thread's `Tgid`, and sends `SIGSTOP` to that
exact Hermit tracer thread group. It proceeds only if:

- the tracer TGID is state `T`; and
- QEMU remains state `t` before and after introspection.

Because the stopped tracer is the only process that can resume its tracee,
QEMU executes no guest instructions during the observation interval. Both
authoritative runs also had `serial_bytes_delta=0`.

An earlier repeat exposed an important race: stopping the outer Hermit launcher
PID is insufficient because the real tracer can have a different TGID. In that
attempt QEMU became state `R`. The final probe intentionally treats that as a
failed quiescence condition; only the exact-TracerPid runs below are evidence.

## Authoritative repeat evidence

Logs:

- `ignored/demo07-drgn_20260728/probe_drgn_tasks.exact-tracer.log`
- `ignored/demo07-drgn_20260728/probe_drgn_tasks.exact-tracer.repeat.log`

Both runs reported:

```text
snapshot: ... qemu=... state=t tracer_tid=... tracer_tgid=... tracer_state=T ram=512MiB
init_task: pid=0 comm='swapper/0'
tasks (pid comm):
      1 busybox
      2 kthreadd
      3 pool_workqueue_
      4 kworker/R-rcu_g
      5 kworker/R-sync_
      6 kworker/R-kvfre
      7 kworker/R-slub_
      8 kworker/R-netns
      9 kworker/0:0
     10 kworker/0:0H
     11 kworker/0:1
     12 kworker/u4:0
     13 kworker/R-mm_pe
     14 kworker/u4:1
     15 ksoftirqd/0
     16 rcu_sched
RESULT: drgn task_struct read succeeded; tasks_shown=16; physical_reads=1742 bytes=3341514; qemu_state=t; serial_bytes_delta=0
```

Each run scoped teardown to its own process group. A post-run process check
found no surviving QEMU or probe process.

## Memory and drgn path

The 512 MiB q35 guest uses one anonymous, contiguous QEMU RAM mmap. For this
configuration guest physical address zero corresponds to the mmap base, so:

```text
QEMU host virtual address = RAM mmap base + guest physical address
```

The callback registered the range `[0, 512 MiB)` with
`Program.add_memory_segment(..., physical=True)` and fulfilled reads with
`pread()` on `/proc/<qemu-pid>/mem`. It then called
`set_linux_kernel_custom(vmcoreinfo, True)`; drgn performed kernel virtual to
physical translation and resolved types/symbols from vmlinux. drgn never used
QEMU's PID as its process target and never issued `PTRACE_ATTACH`, QMP, gdbstub,
or an in-guest command.

The host had no installed QEMU at resume time. The run therefore used a
workspace-local extraction of `qemu-system-x86-core
10.1.2-1.4.hs+fb.el9.x86_64`, downloaded with `with-proxy dnf download`; no
system package was installed.

## Exact inputs

```text
Hermit SHA: f06e576493596429a21489c406cb7398f56bb189
Hermit binary SHA-256: ba3cc254bb1aa559be6bdb548454e3d0b4b00feda77a8213ff0353156946f472
QEMU binary SHA-256: 4f45fad875a6e34e62cf3b91fcc7df217cfb7301fb1e62af6e8102c40a96a387
vmlinux SHA-256: 64f6e10aae09585b415454a3ed915ecaa3e1687d1ba6fa58c8e1c08a1120a2eb
bzImage SHA-256: a9aa7ab24737c0bc210d58463efdffedce98261c31ce3cc09e746e726bab534b
initramfs SHA-256: c9c929ef185c702685c6481571f7086d45db5afa1d4a0e75465da5ba4a21a48a
kernel/vmlinux BuildID: c8c92f7347503db25b1d63ba2c7872b813b840f7
```

The QEMU boot ran with the Hermit ptrace backend, `--strict`, default logging,
and the existing research-harness relaxation `--no-rcb-time`; QEMU used
`-icount shift=0,sleep=off`. This is a focused introspection proof, not an
L0-L4 whole-product completion claim.

## Reproduction

Research probe:
`ignored/demo07-drgn_20260728/probe_drgn_tasks.py`.

```bash
cd "$(git rev-parse --show-toplevel)"
env FB_PAR_UNPACK_BASEDIR="$PWD/ignored/demo07-drgn_20260728/drgn-par" \
  HERMIT_RELEASE="/absolute/path/to/hermit-at-f06e576/target/release/hermit" \
  drgn -q -p $$ ignored/demo07-drgn_20260728/probe_drgn_tasks.py
```

`-p $$` only supplies the packaged drgn CLI with an initial harmless process
target before the script constructs its independent custom kernel `Program`.
The script does not attach drgn to QEMU.
