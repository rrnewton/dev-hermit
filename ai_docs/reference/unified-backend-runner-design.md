# Unified multi-backend example-tool runner — design & handoff

Author: [impl agent, opus-4.8] (task `impl-unified-backend-runner`), 2026-07-26.
Investigated on `~/work/dev-hermit/reverie` @ main `74d090e` / `origin/main`
`0fdf5fda`; `/dev/kvm` present; host devbig.

## Status: BLOCKED on file-ownership overlap — no code changes made

This task ("add `--backend {ptrace,kvm,dbi,sabre}` to the example tools so the
*same binary* runs on any backend; verify `counter2 --backend kvm /bin/echo
hello`") **cannot be implemented right now without violating Hard Invariant #2**
(never let two agents mutate the same file/branch). The exact files it must edit
are currently **dirty and owned by a live concurrent slot**. This document is
the ready-to-apply design so the work can be completed in one clean pass once the
blocker clears. No PR was opened and no shared file was touched.

### The overlap (measured 2026-07-26)

Four orchestrator tasks target `reverie/reverie-examples/` at once:

| Task | Owner/slot | Scope | State |
|---|---|---|---|
| `impl-kvm-example-tools` | hermit-kvm / slot297 | KVM runner + shared example lib | **in_progress, uncommitted WIP, PR #123** |
| `impl-dbi-example-tools` | hermit-184 / slot310 | DBI observation harness (reverie-dbi) | in_progress |
| `impl-sabre-example-tools` | hermit-sabre / slot283 | SaBRe/riptrace adapters | in_progress |
| `impl-unified-backend-runner` | (this task) | `--backend` flag unifying all four | blocked (this doc) |

`slot297` already has uncommitted changes to **`counter1.rs`, `counter2.rs`,
`chaos.rs`, `noop.rs`, `strace/{main,tool,config,global_state}.rs`,
`strace_minimal.rs`, `Cargo.toml`**, plus new `lib.rs` and `kvm.rs` and a
`tests/` dir. These are precisely the files the unified runner must modify.
This task's own verify (`counter2 --backend kvm ...`) is impossible without
editing `counter2.rs` + `Cargo.toml` + `lib.rs`, all owned by slot297.
Therefore this task should be **sequenced after #123 lands**, then built on top
of slot297's foundation — not run in parallel.

### What slot297 (#123) already built — reuse it, don't duplicate

- `reverie-examples/lib.rs`: a `pub` library re-exporting each tool module
  (`counter1`, `counter2`, `chaos`, `noop`, `strace`, `strace_minimal`) via
  `#[path = ...] pub mod`. This is the shared-tool-library refactor the unified
  runner needs. The per-tool source files were edited to make their `Tool`
  types + state accessors (`num_syscalls()`, `inner`, etc.) `pub`.
- `reverie-examples/kvm.rs`: a working generic KVM runner. Its `execute::<T>()`
  does `KvmBackend::new(mem) -> install_static_elf_with_context(image, argv,
  envp, cwd) -> run_static_elf_with_tool::<T>(config, capture)`, driven by a
  hand-rolled `block_on`. **UX delta vs this task:** slot297 uses a *subcommand*
  CLI (`kvm_examples counter2 -- /bin/echo hello`), whereas this task specifies a
  *flag* on each tool binary (`counter2 --backend kvm /bin/echo hello`). The
  runner logic is identical; only the CLI surface differs.

## Verified backend reality (drives the design)

Only **ptrace** and **kvm** can be dispatched at runtime from a single
in-process tool binary. **dbi** and **sabre** fundamentally cannot:

- **ptrace** — `reverie_ptrace::TracerBuilder::<T>::new(cmd).spawn().await?`
  then `.wait().await? -> (ExitStatus, T::GlobalState)`. Generic over `T: Tool`.
- **kvm** — `KvmBackend::run_static_elf_with_tool::<T>(config, capture_output)
  -> (T::GlobalState, i32, stdout, stderr)`. Generic over `T: Tool`. Loads
  static **and** dynamic-PIE ELF (the loader follows `PT_INTERP` and maps
  `ld.so`; `elf.rs:226`), so `/bin/echo` (dynamic PIE) does load. With
  `capture_output=false`, guest writes go straight to the real fds
  (`executor.rs:776 host_write`) and the returned buffers are empty — natural
  passthrough for a CLI tool.
- **dbi** — the `reverie::Tool` is **compiled into a DynamoRIO client `.so`** at
  build time and launched out-of-process via `drrun` (`DbiRunner`); there is no
  runtime tool selection (memory `dbi-no-runtime-tool-selection`). A single
  example binary cannot host it behind a flag. Real Detcore-over-DBI lives in
  hermit's `detcore-dbi` + `hermit --backend dbi`.
- **sabre** — three separately built artifacts (runner + pinned loader +
  plugin `.so`) launched via `HERMIT_SABRE_*` env vars; hermit does not even
  link `reverie-sabre` (see `ai_docs/transient/sabre-backend-assessment.md`).
  Not a runtime flag in a standalone tool.

So the honest unified runner supports **ptrace + kvm** as real in-process
dispatch, and returns a clear, actionable error for **dbi + sabre** pointing to
the out-of-process harnesses.

## Recommended implementation (apply ON TOP of merged #123)

1. **`reverie-util/commandline.rs`** (this crate is UNTOUCHED by slot297 — the
   one genuinely-disjoint file): add
   ```rust
   #[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum, Default)]
   pub enum ExampleBackend { #[default] Ptrace, Kvm, Dbi, Sabre }
   ```
   and a field on `CommonToolArguments`:
   ```rust
   #[clap(long = "backend", value_enum, default_value_t = ExampleBackend::Ptrace)]
   pub backend: ExampleBackend,
   ```
   Default `Ptrace` keeps every existing tool's behavior byte-identical. Keep
   `reverie-util` free of backend-crate deps — the enum is just data.
2. **`reverie-examples/lib.rs`** (slot297's new file): add `pub mod runner;`.
3. **`reverie-examples/runner.rs`** (new): a single generic dispatcher, reusing
   slot297's `kvm.rs` logic:
   ```rust
   pub async fn run_tool<T: Tool>(
       args: CommonToolArguments,
       config: <T::GlobalState as GlobalTool>::Config,
   ) -> anyhow::Result<(ExitStatus, T::GlobalState)>
   ```
   - `Ptrace`: `TracerBuilder::<T>::new(args.into()).spawn().await?.wait().await`.
   - `Kvm`: read `args.program` bytes; argv = program + `program_args`; envp from
     the `Command` built by `args.into()` (honors `--no-host-envs`/`-e`); cwd =
     `current_dir`; `install_static_elf_with_context` + `run_static_elf_with_tool
     ::<T>(config, /*capture_output=*/ false)`; map `code -> ExitStatus::Exited`.
   - `Dbi` / `Sabre`: `anyhow::bail!` with a message explaining these are
     out-of-process backends (DBI = tool baked into a DynamoRIO client `.so`;
     SaBRe = external loader+plugin via env) and pointing to `hermit --backend
     {dbi,sabre}` / the reverie-dbi + reverie-sabre harnesses. Fail loud, never
     silently fall back to ptrace.
4. **Each tool `main`** (counter1/counter2/noop/strace_minimal — the ones whose
   `GlobalState::Config: Default`): replace the hardcoded `TracerBuilder` block
   with `reverie_examples::runner::run_tool::<T>(args, Default::default()).await?`
   then keep the existing summary print. `strace`/`chaos` carry extra CLI + a
   non-`()` config; wire them once the simple ones are proven (their config is
   already threaded through slot297's `kvm.rs`).
5. Delete or keep slot297's subcommand `kvm.rs` binary as an internal test bin —
   coordinate with hermit-kvm. The flag-based path supersedes its CLI.

### Verify (task acceptance)
```sh
cargo build -p reverie-examples
target/debug/counter2 --backend kvm /bin/echo hello    # task's stated check
target/debug/counter2 --backend ptrace /bin/echo hello # regression: default path
target/debug/counter2 /bin/echo hello                  # default == ptrace, unchanged
target/debug/counter2 --backend dbi  /bin/echo hello   # clear "use hermit --backend dbi" error
```
Add a small integration test under `reverie-examples/tests/` asserting
ptrace/kvm parity of the syscall count for `/bin/true`, and that dbi/sabre error
cleanly. Gate the kvm leg on `/dev/kvm` availability (see
`reverie-kvm/tests/strace.rs:kvm_available`).

## Reproduction of the blocker finding
```sh
cd ~/work/dev-hermit
git -C worktrees/slot297/reverie status --short   # shows dirty reverie-examples/*
tg impl-kvm-example-tools -v                       # in_progress, owner hermit-kvm, PR #123
```
