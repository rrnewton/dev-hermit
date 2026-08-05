# e9patch converge — remaining lift is a Detcore-embedding preload cdylib, NOT a CLI flip

Established 2026-08-05 by direct read at live SHAs: reverie slot
`worktrees/e9patch/reverie` on `feat/e9patch-hybridptrace-lifecycle-owner`
@ `846101499c86145a95670e44de343135d0d61d1e` (clean, committed, NOT pushed);
hermit slot `worktrees/e9patch/hermit` on `feat/e9patch-drop-ptrace-downgrade`
@ `fc0b76ad` (= hermit main tip; NO L3 commit yet).

## What the predecessor finished (verified, not inherited)

- **L1 (reverie, load-bearing): DONE.** `HybridPtrace` lifecycle owner implemented;
  `reverie-preload/src/lifecycle.rs` stub `install()` → `Unsupported` is gone.
- **L2 (reverie): auto-cleared in code.** `install_hybrid_runtime()`
  (reverie-e9patch/src/runtime.rs:261-263) no longer returns `Unsupported`; it calls
  `install_with_controller(&HybridPtrace)`.
- **Guest-side env dispatch fully wired.** `initialize_from_environment()`
  (runtime.rs:340-365) reads `REVERIE_E9PATCH_RUNTIME` (=`hybrid`/`1`) →
  `install_hybrid_runtime()`. `TOOL_ENV` selects a shared BuiltinTool with priority.
- **Foreign-branch reconcile: DONE.** hermit slot is off `codex/e9patch-unrecovered-site-coverage`
  and onto the clean L3 companion branch `feat/e9patch-drop-ptrace-downgrade`.

## The correction: the zero-ptracer path needs a Detcore-embedding preload DSO

The A-class launcher exists and is correct. In `reverie-e9patch/src/backend.rs`
`launch_direct` (:937-966), the env-bootstrap path (`tool_data = None`) uses
`TracerBuilder::<()>::new(command).spawn()` — lifecycle-only reaper, follows+reaps the
tree (exec/clone/fork), NO syscall subscription, un-instrumented syscalls fail closed via
the in-guest SIGSYS handler. That is the genuine zero-ptracer-on-syscall-path launcher.
(The "single-process / not tree-reaped" caveat at :890-892 applies ONLY to the sealed-memfd
sub-path `tool_data = Some`, not to the env-bootstrap `run_direct_with_preload` flow.)

BUT that launcher's contract (backend.rs:453) requires "a tool-specific DSO that embeds the
same concrete `T` and calls `install_tool::<T>` from its constructor." For Detcore-in-guest
that means a cdylib embedding **Detcore** that calls `reverie_e9patch::install_tool::<Detcore>`.

- Sabre reaches in-guest Detcore exactly this way: the hermit-side `detcore-sabre` crate
  (`hermit/detcore-sabre/`, crate-type cdylib) produces `libdetcore_sabre.so`
  (hermit-cli/src/lib.rs:864/878; staged by hermit-install/build.rs).
- **No e9patch equivalent exists.** `grep` across `hermit/` for `libdetcore_e9patch`,
  `detcore_e9patch`, `install_tool::<Detcore>`, `reverie_e9patch::install_tool` → zero hits.
  There is no `detcore-e9patch` crate.
- reverie-e9patch's OWN cdylib (Cargo.toml crate-type `["cdylib","rlib"]`,
  `preload-constructor` default-on) can only host the SHARED BuiltinTools (strace/compat/
  spoofgetpid via `TOOL_ENV`) — it depends only on `reverie-core` and cannot embed Detcore.
  So it gives the demo/testing built-in path, not the real Detcore in-guest path.

Note: liteinst is in the SAME position — hermit only ever calls
`run_host_with_preload::<Detcore>` (lib.rs:1534, `TracerBuilder<Detcore>` = B-class,
Detcore host-side). Its in-guest `run_with_preload` has NO hermit caller and would likewise
need a Detcore-embedding liteinst preload DSO. The Detcore-embedding preload is the shared
missing artifact for BOTH ld-preload backends' zero-ptracer form.

## Remaining increment for e9patch zero-ptracer (the actual lift)

1. **New crate `hermit/detcore-e9patch`** (mirror `detcore-sabre`): cdylib embedding Detcore,
   `.init_array` constructor calling `reverie_e9patch::install_tool::<Detcore>(coordinator)`
   reading `REVERIE_E9PATCH_COORDINATOR` (crate::COORDINATOR_ENV). Feature-gated like sabre.
2. **hermit-install/build.rs**: stage `libdetcore_e9patch.so` beside hermit; add a
   `e9patch_runtime_library_path()` locator + availability reason (mirror liteinst/sabre).
3. **hermit lib.rs Backend::E9patch dispatch** (replace the :975-979 rejection): call
   `reverie_e9patch::E9patchBackend::run_direct_with_preload::<Detcore>(command, config, preload)`
   (+ output/verify variants), setting `REVERIE_E9PATCH_RUNTIME=hybrid` on the guest command.
4. **run.rs L3 (LAST)**: `runtime_backend()` (:1714-1720) returns `E9patch` not `Ptrace`;
   fix the test at :772 (`assert_eq!(ro.runtime_backend(), Backend::Ptrace)`). Keep
   `prepare_e9patch_program` / `e9patch::prepare` preprocessing.
5. **Coordinated build**: hermit consumes reverie via a git-pinned dep; to build against
   unpushed reverie `846101499` use a LOCAL-ONLY `[patch."...rrnewton/reverie.git"]` override
   pointing at the slot reverie checkout (like slot `250-delegate`; MUST NOT be committed).
6. **Integrated validation** (the real gate; floor today is only L0): `hermit run --strict`
   then `--strict --verify` with `--backend=e9patch` on a small guest; prove in-guest dispatch
   AND zero ptracer on the syscall path (no per-syscall PTRACE stop; SIGSYS fail-closed).
   Report backend=e9patch + exact Hermit/Reverie SHAs.

## Landing order (unchanged)

reverie `feat/e9patch-hybridptrace-lifecycle-owner` (L1/L2) lands FIRST via PR to
rrnewton/reverie:main; hermit (new crate + dispatch + L3) lands LAST; parent pins both SHAs
together. Patching cluster lands last per owner ordering.
</content>
</invoke>
