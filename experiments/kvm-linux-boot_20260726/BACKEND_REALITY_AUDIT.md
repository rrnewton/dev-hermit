# Backend Reality Audit: KVM Linux Boot Branch

Score: **B0 under the strict backend-reality gate**.

The root QEMU thread does instantiate and execute the real shared Detcore tool,
but concurrent QEMU workers and selected root syscalls bypass Detcore. The
audit rule makes complete Detcore integration a hard prerequisite, so a Linux
boot and matching guest output do not raise this branch above B0.

| Check | Evidence |
|---|---|
| `--backend kvm` flag on main | Yes; the flag predates this branch. |
| `impl Backend` in `reverie-kvm` | No literal trait implementation; the integration uses `KvmBackend::run_static_elf_with_tool`. |
| `Detcore<KvmGuest>` path | Yes for the root: `hermit-cli/src/lib.rs:798` -> `run_kvm` -> `KvmBackend::run_static_elf_with_tool::<Detcore>` at line 878 -> tool construction in `reverie-kvm/src/runtime.rs:535`. |
| Root syscall interception | Subscribed non-process calls invoke `tool.handle_syscall_event` in `runtime.rs`; results return through the syscall frame. |
| Bypass | `runtime.rs:659-706` routes process calls, futex, ppoll, and readv to `ElfExecutor`; worker VMs execute all calls through `ElfExecutor` outside Detcore. |
| CLI linkage | Yes; Hermit links and constructs `reverie_kvm::KvmBackend`. |
| Arbitrary programs | `/bin/echo hello`, `/bin/true`, and `/bin/cat /dev/null` each exited 0 under `--backend kvm run --strict --verify`; comparison was output/status only. |
| Linux userspace | Reproducibly assembled BusyBox image completed two boots with matching captured output/status and exit 0. |
| Code on main | No. Source is uncommitted in task worktrees. |

## Gap To A Full Backend

1. Route every guest thread through a real `KvmGuest<Detcore>` lifecycle.
2. Integrate KVM worker scheduling with Detcore instead of host-thread races.
3. Remove root syscall bypasses by implementing the required KVM injection
   contract or an equivalent Tool-visible adapter.
4. Restore complete deterministic-log comparison for KVM `--verify`.
5. Implement Linux thread-group file-table, signal, and `exit_group` semantics.
6. Land coordinated Reverie and Hermit commits, then reproduce the commands on
   current main before changing the B0 score or closing the milestone.
