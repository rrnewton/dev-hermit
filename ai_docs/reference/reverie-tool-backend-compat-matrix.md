# Reverie example-tool × backend compatibility matrix

Author: [impl agent, opus-4.8] (task `impl-tool-compat-matrix`), 2026-07-26.
Measured on `~/work/dev-hermit/reverie` @ main `74d090e`; `/dev/kvm` present;
development host.

## TL;DR — the crucial architectural finding

**The 9 `reverie-examples` tools are ptrace-only binaries.** Each is a
`[[bin]]` that hardcodes `reverie_ptrace::TracerBuilder::<Tool>` (e.g.
`counter1.rs:77`); none reference `reverie_kvm`/`reverie_dbi`/`reverie_sabre`.
So the *same binary* cannot run on another backend — the backends are **not**
drop-in `TracerBuilder` replacements at the example-tool level.

Instead, each non-ptrace backend re-implements a **subset** of tools against the
shared `reverie::Tool` trait, run through its own harness:
- **KVM**: `runtime.rs:400 run_with_tool<T,E>` (generic) → `StraceTool` wired.
- **DBI**: `reverie-dbi/src/tools.rs` → `StraceTool` + `SyscallCounterTool`,
  plus `PrototypeTool` (lib.rs:463) and the real `Detcore<DbiGuest>` (in hermit).
- **SaBRe**: `experimental/reverie-sabre-strace` (a `StraceTool` via
  `reverie_sabre::ReverieAdapter`) + `experimental/riptrace`.

So "the matrix" is really: **ptrace has the full example-tool suite; the other
backends each cover a small subset via their own Tool implementations.**

## Matrix (measured)

| Tool | ptrace | KVM | DBI | SaBRe |
|---|---|---|---|---|
| noop | ✅ PASS | — not ported | — not ported | — not ported |
| counter1 | ✅ PASS (114 syscalls) | — not ported | ~ src exists (`SyscallCounterTool`), build blocked* | — not ported |
| counter2 | ✅ PASS (114, 1 proc) | — not ported | ~ (as above) | — not ported |
| strace | ✅ PASS (full args) | ✅ PASS (names+order; **args zeroed** = partial) | ~ src exists, build blocked* | ✅ PASS (full args) |
| strace_minimal | ✅ PASS | — (KVM StraceTool ≈ this) | — | — (sabre-strace ≈ this) |
| chaos | ✅ PASS | — not ported | — not ported | — not ported |
| chunky_print | ✅ PASS | — not ported | — not ported | — not ported |
| chrome_trace | ✅ PASS | — not ported | — not ported | — not ported |
| debug | ⚠ blocks for gdb (rc=124 timeout — **expected**, waits for a debugger attach) | — not ported | — not ported | — not ported |

Legend: ✅ ran, correct output · ~ tool exists in source but not runnable in
this checkout · — not implemented for that backend · ⚠ expected blocking.

\* **DBI build blocked (environmental, not a tool defect):** `cargo test -p
reverie-dbi` fails at `reverie-dbi/build.rs:97` — DynamoRIO submodule is at
`cca42665…` but the build asserts `929840ad…`. The DBI tools (`StraceTool`,
`SyscallCounterTool`) and a test harness (`tools.rs:262`) exist in source and
could not be exercised here. Separately, the **real `Detcore<DbiGuest>`** path
(hermit `detcore-dbi`) runs `echo` at L2 (established in prior tasks).

## Evidence (exact commands + results)

- **ptrace baseline** — `cargo build -p reverie-examples` (exit 0), then
  `target/debug/<tool> -- /bin/echo hi` for each: noop/counter1/counter2/strace/
  strace_minimal/chaos/chunky_print/chrome_trace all rc=0 with guest `hi` and
  correct tool output (`counter1`: "Total system calls in process tree: 114";
  `strace`: `execve(... "/bin/echo" ...)`). `debug` rc=124 (timeout) — it starts
  a gdbserver and waits for an attach; blocking is by design, not a failure.
- **KVM** — `cargo test -p reverie-kvm --test strace -- --nocapture`: **2/2 pass**
  (`strace_tool_records_write_syscall_name`,
  `strace_tool_records_multiple_syscall_names_in_order`). Records syscall
  names + order via `run_with_tool` + `StraceTool`, but `SyscallArgs` are mostly
  zeroed → **partial** strace (names/order only). `run_with_tool<T,E>` is generic,
  so other Tools are *feasible* on KVM but only `StraceTool` is wired.
- **DBI** — `cargo test -p reverie-dbi`: **build error** (DynamoRIO submodule
  revision mismatch, above). Not run.
- **SaBRe** — `cargo build -p reverie-sabre-strace` (exit 0), then
  `target/debug/reverie-sabre-strace --sabre /tmp/sabre-upstream/build/sabre
  --plugin target/debug/libreverie_sabre_strace_plugin.so -- /bin/echo hello`:
  **rc=0**, full syscall trace with **real decoded args**
  (`Munmap{arg0:…,arg1:65536,…}`, `Madvise{…}`, `ExitGroup{…}`) — higher fidelity
  than KVM's zeroed-args strace. (SaBRe is an experimental M2 syscall-tracing
  backend, not a deterministic Detcore backend; fail-open; needs an external
  SaBRe loader binary, found under /tmp from prior builds.)

## Interpretation (for the "prove the interface with trivial tools first" goal)

- **ptrace** is the only backend with the complete `reverie::Tool` example suite;
  it is the reference for tool-interface correctness (8/9 clean, debug blocks by
  design).
- **strace is the common denominator** proven on 3 of 4 backends: ptrace (full),
  SaBRe (full args), KVM (partial — names/order, args zeroed). Fixing KVM's
  `StraceTool` to decode real args is the obvious next KVM tool-interface task.
- **KVM's `run_with_tool<T>` is generic**, so porting `counter`/`noop` to KVM is
  low-risk interface work that would broaden this matrix.
- **DBI's tools exist but are unbuildable in this checkout** due to a DynamoRIO
  submodule pin mismatch — an environment fix (align `third-party/dynamorio` to
  `929840ad`) is the prerequisite to measuring the DBI column.
- **SaBRe** already drives a shared `reverie::Tool` through `ReverieAdapter` with
  full-arg fidelity, but is experimental/fail-open — not a determinism backend.

## Reproduction
```sh
cd ~/work/dev-hermit/reverie
cargo build -p reverie-examples
for t in noop counter1 counter2 strace strace_minimal chaos chunky_print chrome_trace; do
  target/debug/$t -- /bin/echo hi; done          # ptrace baseline (debug blocks)
cargo test -p reverie-kvm --test strace -- --nocapture   # KVM StraceTool
cargo test -p reverie-dbi                                  # DBI (currently build-blocked)
cargo build -p reverie-sabre-strace && \
  target/debug/reverie-sabre-strace --sabre <sabre> \
    --plugin target/debug/libreverie_sabre_strace_plugin.so -- /bin/echo hello
```
