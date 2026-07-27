---
name: multi-backend-tool-binaries
description: "reverie multi-backend tool build infra — one tool crate, per-backend bins; backend run-API divergence"
---

Multi-backend tool binary infrastructure in `reverie/` (task impl-multi-backend-tools, slot140, branch impl-multi-backend-tools-slot140; validated but LEFT UNCOMMITTED because that task's protocol forbade commit/PR without explicit instruction).

Shape: write each Reverie `Tool` ONCE in a lib crate depending only on `reverie`, then a `reverie-multibackend-tools` crate holds thin per-backend adapters + `[[bin]]` targets gated by Cargo **features** (backends can't all be default — see below).
- `reverie-tool-sysctr` (lib): syscall counter. Aggregates **live** (one `send_rpc(IncrMsg(1))` per syscall + tail_inject) NOT via on_exit hooks, because KVM's static-ELF runner does NOT drive on_exit_thread/process. `receive_rpc` also inserts `from.as_raw()` into a BTreeSet for a distinct-process count.
- `reverie-tool-riptrace` (lib): strace-like tracer (models reverie-examples/strace; uses `guest.inject`/`tail_inject`/`memory`, `Displayable::display_with_outputs`, `Pid::colored()`).

Backend run-API divergence (the reason features, not one uniform runner):
- **ptrace** = `reverie_ptrace::TracerBuilder::<T>::new(args.into()).config(cfg).spawn().await` → `(ExitStatus, GlobalState)`. Mature; runs any guest command. Needs `T: Tool + 'static`. Args via `reverie_util::CommonToolArguments::parse()` (guest cmd after `--`).
- **kvm** = `KvmBackend::new(MEM)?; install_static_elf_with_args(&img,&argv,&envp)?; block_on(run_static_elf_with_tool::<T>(cfg, capture))` → `(GlobalState, i32 code, Vec<u8> out, Vec<u8> err)`. Bounded: single fixed-address static ELF (though it ran dynamic /bin/true to exit_group here). Needs /dev/kvm.
- **dbi** = cdylib native-client model, NO runtime tool selection: tool baked into `REVERIE_DBI_CLIENT` (built by reverie-dbi/scripts/build-client.sh); `DbiRunner::from_env()` (needs DYNAMORIO_HOME) only launches drrun+client on a guest `Command`. So a per-tool bin can only (1) link-prove via monomorphizing `reverie_dbi::run_tool_{thread_start,post_exec,thread_exit,syscall}::<T>` and (2) drive DbiRunner. `reverie-dbi` build.rs REQUIRES `scripts/backend-submodule.sh activate dynamorio` + a from-source CMake build (~3.5min) → dbi CANNOT be a default cargo feature.

Cross-backend agreement observed: `reverie-sysctr-{ptrace,kvm} /bin/true` both = 33 syscalls; ptrace fork-tree `sh -c '...'` = 489 across 3 processes.

Gotcha: `#[reverie::tool]`/`#[reverie::global_tool]`/`#[reverie::backend]` are just re-exports of `#[async_trait::async_trait]` (reverie/src/lib.rs) — no special codegen; a shared tool lib needs only the `reverie` dep. Config `()` → pass `()` not `Default::default()` (clippy::unit_arg).

Related: [[reverie-kvm-has-no-tool-guest-adapter]], [[dbi-no-runtime-tool-selection]], [[detcore-over-dbi-blocked-by-executor]], [[reverie-rpc-transport-crate]], [[good-hermit-binary-for-tests]].
