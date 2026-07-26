# Backend-specific tool audit & consolidation plan

Author: [impl agent, opus-4.8] (task `impl-cleanup-backend-hacky-tools`), 2026-07-26.
Audited against `rrnewton/reverie` **origin/main `5dda5d7`** (primary checkout was
behind; fetched read-only). No code changes made — see "Why not executed now".

## Task goal
"No backend-specific tool hacks. Port valuable tools to `reverie-examples/`
(standard `Tool`/`Guest` API, working across ALL backends); delete the rest;
make `reverie-examples/` the single source of truth." Verify: "No backend-specific
tool implementations remain outside `reverie-examples/`."

## Why this is NOT executed now (blocked + premature)

1. **Every consolidation target is under an open, unmerged PR** — deleting/
   refactoring them now conflicts with and would clobber in-flight work
   (Hard Invariant #2 at the PR level):
   - **#126** (OPEN) modifies `reverie-kvm/src/tools.rs` + `reverie-kvm/tests/strace.rs`
     — the KVM StraceTool this task would consolidate.
   - **#123** (OPEN) modifies `reverie-dbi/src/tools.rs` — the DBI tools this task
     would delete.
   - **#127** (OPEN) is *adding* new backend-specific tool hosts in
     `reverie-liteinst` (`src/bin/rpc_tool_guest.rs`, `src/bin/trap_count_guest.rs`,
     `src/tool_host.rs`, `tests/strace.rs`) — i.e. the tree is actively growing
     more backend-specific tools, the opposite of this task's end-state.
   - **#128** (OPEN) touches the SaBRe path.
2. **The backend "duplicates" ARE each backend's only test coverage.** The
   reverie-kvm tools are consumed by `reverie-kvm/tests/{counter,strace,static_elf}.rs`.
   Deleting them without a replacement deletes real coverage.
3. **The end-state structurally depends on a blocked task.** "reverie-examples
   tools work across ALL backends via the standard API" requires the unified
   multi-backend runner — task `impl-unified-backend-runner`, which is BLOCKED
   (see `ai_docs/reference/unified-backend-runner-design.md`). The example tools
   are ptrace-only `[[bin]]`s today; nothing lets their `Tool` types run on
   kvm/dbi/sabre without either (a) that runner or (b) a shared tool library the
   backends import. Neither exists on main yet.

Therefore the correct action is this audit + a sequenced plan; execute the
consolidation only after #126/#123/#127 land and the shared-tool foundation
exists. No competing PR was opened; no shared file was touched.

## Full inventory (every `Tool`/`GlobalTool` impl outside `reverie-examples`)

### A. Genuine backend duplicates — consolidation targets (ALL currently blocked)
| Location | Tools | Duplicates | Used by | Blocker |
|---|---|---|---|---|
| `reverie-kvm/src/tools.rs` | StraceTool, CounterTool, HierarchicalCounterTool (+ StraceLog/SyscallCounter/HierarchicalCounter globals) | strace_minimal, counter1, counter2 | reverie-kvm tests `{strace,counter,static_elf}.rs` | **open PR #126** |
| `reverie-dbi/src/tools.rs` | SyscallCounterTool, SharedSyscallCounterTool, StraceTool | counter1, strace | *nothing on main* (`#[cfg(feature="prototype-runtime")]`, unconsumed) | **open PR #123** |
| `experimental/riptrace/tool/src/lib.rs` | Riptrace | strace | riptrace bins | SaBRe area (#128) |
| `experimental/reverie-sabre/src/tool.rs` | sabre tool surface | strace-like | sabre plugins | SaBRe area (#128) |
| `reverie-liteinst/src/bin/strace.rs` (+ #127's `rpc_tool_guest`, `trap_count_guest`, `tool_host`) | strace / counter guests | strace, counter | liteinst tests | **open PR #127 (actively adding)** |

### B. Legitimate test fixtures — KEEP (not duplicates; test backend machinery)
- `reverie-kvm/tests/vmcall.rs`: PassthroughTool, RecordingTool (+RecordingGlobal) — exercise the `vmcall` transport + `run_with_tool`.
- `reverie-kvm/tests/static_elf.rs`: PostExecTool, FailingPostExecTool — post-exec lifecycle.
- `reverie-e9patch/tests/backend.rs`: EventCounter, EmulateGetpid, InjectGetpid, CountRead — e9patch backend behavior.
- `reverie-rpc-transport/tests/round_trip.rs`: Counter — UDS+bincode RPC round trip.
- `reverie-dbi/tests/cross_process_counter.rs` + `reverie-dbi/src/counter.rs` (SyscallCounterGlobal) — the #121 cross-process GlobalState RPC demo/test (backend-specific by nature, not a plain duplicate).
- `reverie/tests/*.rs`: the whole reverie integration suite (TestTool/LocalState/…) — core fixtures, not tools.

### C. Canonical / keep as-is
- `reverie-examples/*` — the canonical tools (but ptrace-only bins; need lib extraction to be reusable).
- `reverie/src/tool.rs` — the trait definitions and `impl … for ()`.
- `reverie-dbi/src/lib.rs` PrototypeTool — DBI's own adapter Tool, consumed by lib.rs (backend glue, not a duplicate).

## Sequenced consolidation plan (execute later)

**Prereq 0:** land open PRs #126 (kvm strace), #123 (dbi tools), #127 (liteinst).
Do not fight them. Re-audit after they merge.

**Prereq 1 (the enabling refactor, shared with `impl-unified-backend-runner`):**
extract the canonical `Tool` impls into ONE backend-agnostic library that depends
only on `reverie` (the counter/strace/noop `Tool` structs use only `reverie` +
serde; the `reverie-ptrace` dep lives solely in the bin `main()`s). Two viable
shapes — pick ONE with the coordinator to avoid a third competing structure:
  - (a) add `lib.rs` to `reverie-examples` exposing `pub mod {counter1,counter2,
    strace_minimal,strace,noop,chaos}` (this is what un-landed slot297 PR-#123-marker
    work prototyped; its tree is in `worktrees/slot297/reverie`), or
  - (b) a dedicated `reverie-example-tools` crate (the `multi-backend-tool-binaries`
    memory / slot140 approach: `reverie`-only lib, per-backend thin adapters).

**Step 2:** rewire the backend test suites to import the canonical `Tool` types:
  - `reverie-kvm/tests/{counter,strace,static_elf}.rs` → import canonical
    Counter/Strace from the shared lib; then delete `reverie-kvm/src/tools.rs`
    duplicates (keep the tests/ fixtures in group B).
  - `reverie-dbi` → same for its counter/strace; `reverie-dbi/src/tools.rs` is
    already dead on main (feature-gated + unconsumed), so after #123 lands it can
    likely be deleted outright or folded into the shared lib.
  - `reverie-liteinst` (post-#127) → point its tool-host bins at the shared lib.

**Step 3:** keep all group-B fixtures and group-C canonical/glue. Verify each
backend's test suite is green importing from the single lib; confirm no duplicate
`Tool` logic remains outside `reverie-examples`/the shared lib.

**Acceptance (task verify):** one shared-lib home for counter/strace/noop; backend
crates contain only real backend glue + genuine test fixtures; all suites green.

## Reproduction
```sh
cd ~/work/dev-hermit
with-proxy git -C reverie fetch origin main
git -C reverie grep -n "impl Tool for\|impl GlobalTool for" origin/main -- '*.rs' | grep -v reverie-examples
with-proxy gh pr list -R rrnewton/reverie --state open   # #126/#123/#127/#128 touch the targets
```
