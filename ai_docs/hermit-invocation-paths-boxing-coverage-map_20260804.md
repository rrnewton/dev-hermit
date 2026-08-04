# Hermit invocation-path → boxing-coverage map (2026-08-04)

**Task:** `close_boxing_coverage_gap`. **Author:** hermit-coord (co-coordinator), with a
parallel code-search audit reconciled in.
**Evidence bound to:** hermit `8f656b4d8f0318a26994bd96b12af8b06bd36ebc`; agent-utils
`dfefbdb8fd0ce449d5689f6183aca1e28b3f177f`; parent
`7a3d7917b2979f4e50c1ece2f42874a210bccecd`. **Host:** devbig014.

## The question (owner's framing)

The CPU-timeout/reaper wrapper is real and correct, and it is attached to **exactly one**
invocation path. The gap is not the mechanism — it is *coverage*. So: **enumerate every way a
`hermit` process can start, and mark which ones reach the wrapper.** "The gap is always a path
added later."

## What the leak actually is (corrected 2026-08-04)

The recurring leak is **NOT "orphaned hermit processes."** Re-chased with
`cat /proc/<pid>/cgroup` + `cmdline` + start-time + run-state:

- Every process actually burning CPU is a **shell** (`bash`/`/bin/sh`), **not** the `hermit`
  binary. They are the **guest workloads** of hermit integration tests.
- The spinner is the **supervisee (guest)**; the thing that vanished-without-reaping was hermit
  (the supervisor). Correct name: **orphaned hermit-test guest processes**.
- Snapshot: **2 orphaned scopes / 17 processes**, only **7 CPU-spinning @99.5%**, 10 sleeping
  (paired parent shells + idle DBI fixtures in `nanosleep`). Report **N scopes AND M processes** —
  scopes understate (one scope holds many leaked procs; systemd frees a scope only when EMPTY).
- Anomaly: a bounded `for _ in {1..100000}` bash loop had **4d+ of CPU** → not progressing → the
  guest is **wedged/corrupted** after its ptrace/DBI instrumentation died mid-run, not a benign
  native loop.

The scope-owning agent PIDs were **dead**; the scope (`3pai_sandbox.slice/run-p<AGENT>.scope`)
enforces nothing: no `cpu.max`, `memory.max=max`, `pids.max=max`, no reaper.

## Source of the leaked guests (file:line) — resolves the open question

All three leaked cmdlines are in **`hermit/hermit-cli/tests/cli.rs`** (Rust CLI integration tests,
run by `cargo test`) at hermit `8f656b4`:

| Leaked guest cmdline | Source |
|---|---|
| `set -euo pipefail; output=$(/bin/sh -c 'printf guest-stderr >&2' 2>&1); test …` | `hermit-cli/tests/cli.rs:782` |
| `{ printf "%4096s" x; for _ in {1..100000}; do :; done; printf "%1371s" y; } \| wc -c` | `hermit-cli/tests/cli.rs:1006` |
| `target/tmp/dbi-wait/dbi_wait_lifecycle`, `target/tmp/dbi-execveat/dbi_execveat_unsupported` | `hermit-cli/tests/cli.rs:141-174` (built into `CARGO_TARGET_TMPDIR`) |

(The parallel audit could not pin these to an e2e manifest cell within its read budget; direct `rg`
settles it — they are **Rust CLI tests**, launched with no containment: `fn hermit(args)`
`cli.rs:35-38` is `Command::new(env!("CARGO_BIN_EXE_hermit")).…output()`; spawned children
`cli.rs:43-48` use `.spawn()` with **no process group, no `kill_on_drop`, no cgroup, no timeout**.)

## The ONLY boxing mechanism

`safe-ci-dag-runner run` **re-execs itself** into a `systemd-run --user --scope` delegated cgroup —
Python `agent-utils/py/safe_ci_dag_runner/cgroup.py` `reexec_in_scope()` L525-602 (anti-recursion
guard `SAFE_CI_IN_SCOPE` L553; `os.execvp` L598); Rust
`agent-utils/rs/safe-ci-dag-runner/src/cgroup.rs` `reexec_in_scope()` L190-262. It **asks systemd**
for the scope rather than creating its own, which is why it works from inside the BpfJailer sandbox.
The `cpu_timeout` reaper (`scheduler.py` `_monitor()` L422-457: 1 Hz `cpu.stat usage_usec` →
`cgroup.kill` on breach; wall backstop) is **inert without the re-exec** — it reads a per-step
cgroup that exists only because the runner boxed itself. **A `hermit` process is COVERED only if
some ancestor went through that re-exec.** Arbitrary-command entry: `run --dag -` (stdin);
fail-closed with **exit 3** if boxing is required but unavailable.

## Invocation-path enumeration

### COVERED — reaches the wrapper (with two large caveats)

- **CI validation DAG lanes.** `validate.sh` manifest lanes call `run_ci_manifest_lane` (audited to
  execute exactly one DAG, `hermit/ci/test_harness.sh:373-376`) → `ci/run-dag.sh <lane>`
  (`exec "$runner" run --dag …` `run-dag.sh:118`) / `ci/run-node.sh` (`:126`) → the re-exec above.

- **CAVEAT 1 — validate.sh is NOT "fully covered."** Its **plain** gates (`cargo build`/`test`/
  `fmt`/`clippy`) are governed only by a **process-tree wall timeout** — `run_timed_command`
  (`validate.sh:1166-1233`) + `kill_process_tree` (`:880-890`, invoked `:1198/:1204`), **no cgroup,
  CPU/memory of the subtree uncapped**. Only the manifest/DAG lanes are boxed.

- **CAVEAT 2 — even the DAG path is UNBOXED on CI.** `ci/run-node.sh:120-123` adds
  `--allow-cgroup-failure` when `$GITHUB_ACTIONS`/`$CI` is set, so hosted and self-hosted runners
  deliberately run best-effort **unboxed**. Boxing holds off-CI only via the producer path
  (`systemd-run --user … validate.sh`), which obtains the scope.

### GAP — bypasses boxing (no cgroup; at most a GNU `timeout` wall wrapper)

1. **Rust CLI integration tests (the observed leak).** `cargo test -p hermit-cli` — ~121 files under
   `hermit/hermit-cli/tests/`. **The leak source is `cli.rs:782/1006/141-174` (above).** Others:
   `tests/common/nondeterminism.rs:132-164` (`run_command` has **no timeout at all**);
   `tests/zero_copy_pipe_fallback.rs:53-88` (GNU `timeout` wall only). Boxed only if `cargo test`
   itself is a DAG node.
2. **e2e shell harness `ci/test_harness.sh`.** `direct`/`direct-argv` guest cells (`:852-861`) launch
   via `run_capture` (`:895-896`) under GNU `timeout` (`:714-715`) only — no cgroup, no subtree reap.
   Same guest cmdline *shapes* as the leak; the launch is `HERMIT_BIN … -- bash -c '<string>'`.
3. **determinism-stress shell libs.** `hermit/tests/e2e/lib/determinism-stress/common.sh:7,66,90-91`
   — `timeout … "$hermit_bin" …`, GNU `timeout` only.
4. **Ad-hoc manual / explicit-path** — `./target/debug/hermit`, `./target/release/hermit`, a
   worktree hermit typed in an agent pane → agent session scope, **no wrapper at all**. A PATH-shim
   named `hermit` would **miss all explicit-path forms**.
5. **CI workflow direct-harness steps** — `ci-portable.yml` e2e/SaBRe steps (`:644-657`, `:740-761`)
   and `ci-privileged.yml` occasional-KVM step (`:101-114`) call `test_harness.sh` **directly**
   (Mechanism A), GNU `timeout` only.
6. **Any future runner/bench/script that fork+execs hermit outside `ci/run-dag.sh`** — the
   "path added later." Coverage is by convention, so each new entry point re-opens the gap.

**Nested / record-replay hermit** inherits the coverage class of its *outermost* launcher; the
anti-recursion guard (`SAFE_CI_IN_SCOPE`) prevents a second re-exec, and the outer scope (if any)
already contains the whole subtree.

## Coverage fix (fix coverage, not mechanism; no second reaper)

Route the uncovered **tree roots** through the existing DAG runner as a 1-node boxed step (the
predecessor's `box <cmd>` glue, part-1 verified): shlex-quote the command into a 1-node DAG with a
default `cpu_timeout`, pipe to `safe-ci-dag-runner run --dag -`, propagate exit. Then the **whole
process tree** — `cargo test`/`test_harness.sh` → hermit → guest — lives in one boxed scope; on
timeout or teardown `cgroup.kill` reaps the guest subtree too. This closes gap-paths **1, 2, 3, 5**
(the dominant ones): agents run `cargo test`, the e2e harness, and manual hermit **under `box`**.

**Key insight for any fix:** the leak is the **guest subtree one layer below hermit**, which
survives independent of the `hermit` process. A fix that only wraps the `hermit` binary (a PATH
shim) is doubly insufficient — it misses explicit-path invocations *and* it does not help the
`cargo test`/`test_harness.sh` cases, whose entry point is `cargo`/`bash`, not `hermit`. **Coverage
must wrap the tree root, not the hermit binary.**

**Irreducible residue (owner decision, do NOT build silently):** a human/agent typing
`./target/release/hermit run …` directly (gap-path 4), and the **`--allow-cgroup-failure` CI
carve-out** (CAVEAT 2), are not closed by an opt-in `box`. Fully-automatic closure needs one of:
(a) a CPUQuota + reaper on `3pai_sandbox.slice` / the per-agent run scope — **owner has forbidden
this** (it is a *second reaper*); (b) a hermit-product self-watchdog that caps/reaps its own runaway
guest; or (c) discipline (route through `box`) + removing the CI carve-out where hosts allow. Flag
(a)/(b) as an explicit decision; do not implement unasked.

## Do NOT

- Do **not** kill the 17 orphans — owner has not authorized. The PID list (task note) is evidence.
- Do **not** build a second reaper. The wrapper is correct; only its coverage is short.
