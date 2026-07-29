# demo07 option 3 — KEY BLOCKER resolved + drgn bootstrap de-risked

Task: `demo07-drgn-deterministic-kernel` (hermit-275, 2026-07-28). Research only,
no source changes. Working artifacts (gitignored):
`ignored/demo07-drgn_20260728/` (`probe_extern_read.py`, `probe_vmcoreinfo.py`,
`initramfs-idle*`).

## The KEY BLOCKER

> Can hermit read its already-ptraced, stopped QEMU child's memory WITHOUT a
> second ptrace attach?

**Answer: YES — two independent paths, both proven.**

### Path A — external same-uid read (empirically proven)

`yama` LSM is OFF on this host (`/proc/sys/kernel/yama/ptrace_scope` does not
exist). Under yama-off + same-uid, `__ptrace_may_access` grants a *non-tracer*
process read access to `/proc/<pid>/mem` and `process_vm_readv(2)` without any
`PTRACE_ATTACH` (a second attach would EPERM anyway, since hermit already traces
QEMU).

Proven live (`probe_extern_read.py`): booted fbk `6.18.39` QEMU under
`hermit run --strict` (icount profile, `-smp 1 -m 512M`). From a **separate
process** (`pid 3892855`, NOT the tracer; `qemu TracerPid` = a hermit thread):

- guest RAM host mapping: single **contiguous 512 MiB anonymous rw mmap** at
  host-va `0x7fffc8600000-0x7fffe8600000` (q35, 512M < PCI hole ⇒ one RAMBlock
  `pc.ram`, no split).
- read that region via `/proc/<qemu>/mem`, scanned 512 MiB in **0.4 s**, and
  found the live guest banner
  `Linux version 6.18.39-0_fbk0_vm2_0_ga43d5727b443 …` at guest-phys
  `0xa800570` (168.00 MiB).

**guest-phys → host-va is linear for this config:** `host_va = mmap_base +
guest_phys` (mmap_base = `0x7fffc8600000`, guest_phys 0 = mmap_base). Holds
because the whole 512 MiB is below the PCI hole → one contiguous RAMBlock. For
≥ ~2 GiB guests QEMU splits `pc.ram` at the 0xC0000000/4 GiB PCI hole and the
map needs two segments; the demo's 512 MiB avoids that.

### Path B — hermit's own in-process capability (code-grounded)

reverie-ptrace already reads its stopped child's memory (hermit IS the tracer),
via the `MemoryAccess` trait:

- bulk reads → `process_vm_readv(2)`; small (≤8 B) reads → `PTRACE_PEEKDATA`
  (`reverie/safeptrace/src/memory.rs:62-144`). Writes symmetric
  (`process_vm_writev` / `POKEDATA`; POKEDATA is the only path that can write
  write-protected pages — `reverie-ptrace/src/task.rs:893-911`).
- gated at the type level on `Stopped` (`safeptrace/src/lib.rs:441-471`); a
  fresh `Stopped(pid)` is built per access (`task.rs:2201-2206`), stateless,
  **no `/proc/pid/mem` fd** is ever opened by reverie.
- `Guest::memory()` returns `Stopped` (`task.rs:2607-2628`), so an in-hermit
  Tool could read guest-phys RAM during any ptrace-stop.

So the drgn physical-read callback can be served either **out-of-band**
(external `/proc/<qemu>/mem`, Path A — zero hermit changes, zero virtual-time
cost) or **in-process** (a hermit tool via `guest.memory()`, Path B). Path A is
the natural fit for option 3's "read-only, external, no Heisenberg" goal: QEMU
is stopped/quiescent under hermit's ptrace control, the read never advances
virtual time or perturbs Detcore.

Consistency note: for a coherent `task_struct` snapshot, read while QEMU is in a
ptrace-stop (hermit holds it there between turns). Static data (banner,
VMCOREINFO) is safe to read anytime; live mutable structs want the stop.

## drgn bootstrap — de-risked (`probe_vmcoreinfo.py`)

`CONFIG_PROC_KCORE` and `CONFIG_FW_CFG_SYSFS` are UNSET ⇒ drgn's QMP/`-device
vmcoreinfo` auto-discovery and in-guest `/proc/kcore` are both unavailable. So
`set_linux_kernel_custom()` with a manual physical-read callback + manual
VMCOREINFO is required. All the bootstrap inputs are **recoverable from physical
RAM** (found the VMCOREINFO ELF-note payload at guest-phys `0x1b9a000`, 108
lines):

```
OSRELEASE=6.18.39-0_fbk0_vm2_0_ga43d5727b443
BUILD-ID=c8c92f7347503db25b1d63ba2c7872b813b840f7   # == our vmlinux BuildID
PAGESIZE=4096
KERNELOFFSET=2d000000                               # KASLR slide (CONFIG_RANDOMIZE_BASE=y)
SYMBOL(swapper_pg_dir)=ffffffffb0a30000             # top-level PGD for VA->phys walk
SYMBOL(_stext)=ffffffffae000000
SYMBOL(init_uts_ns)=ffffffffb12c0410
NUMBER(phys_base)=-620756992
```

The vmlinux debuginfo (`ignored/qemu-linux-618-vm2/extracted/lib/modules/
6.18.39-0_fbk0_vm2_0_ga43d5727b443/vmlinux-…`, 472,748,856 B, has
`.debug_info`/`.BTF`/`.symtab`) BuildID **exactly matches** the guest
`BUILD-ID` — same kernel, so drgn types/offsets will be correct.

> Caveat: `extracted/boot/vmlinux-…` is a **dangling** symlink to an absolute
> `/lib/modules/…` path that doesn't exist here. Point drgn at the real
> `extracted/lib/modules/…/vmlinux-…` file.

## task_struct read prototype — remaining steps

The read primitive (Path A), the guest-phys→host map (linear), and the drgn
bootstrap inputs (VMCOREINFO) are all in hand. Remaining, for the next window:

1. Wrap `/proc/<qemu>/mem` phys reads as a drgn `set_linux_kernel_custom()`
   read-callback (linear phys→host-va + `pread`).
2. Feed drgn the VMCOREINFO string above + the matched vmlinux; let drgn use
   `swapper_pg_dir` + `KERNELOFFSET` to walk kernel VAs → phys.
   (Alternatively hand-walk the x86-64 4-level page tables from
   `swapper_pg_dir` phys to translate a kernel VA, if drgn's custom mode still
   wants a virtual-read callback.)
3. Read `init_task` and iterate `task_struct.tasks` to dump the process list —
   the concrete "task_struct read" demo, done from OUTSIDE, read-only, at zero
   virtual-time cost.

## Reproduce

```
cd ~/work/dev-hermit
python3 ignored/demo07-drgn_20260728/probe_extern_read.py   # Path A + RAM map + banner
python3 ignored/demo07-drgn_20260728/probe_vmcoreinfo.py    # drgn bootstrap inputs
```

Both boot QEMU-under-hermit in their own session/pgid and SCOPED-kill only that
group at teardown (never a broad pkill — see incident history). ~60 s each.
