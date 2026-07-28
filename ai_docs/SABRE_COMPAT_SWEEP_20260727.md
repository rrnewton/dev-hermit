# SaBRe Backend Compatibility Deep-Debug — 2026-07-27

Deep compat debugging of Hermit's **SaBRe** backend (static binary rewriting
driving the shared Detcore tool). Companion to `DBI_COMPAT_SWEEP_20260727.md`;
the headline result is a direct **SaBRe-vs-DBI** comparison on DBI's failure set.

- **Backend:** SaBRe. Guest syscall sites are statically rewritten by the
  `sabre` loader to call into the `libdetcore_sabre.so` plugin, which forwards
  each syscall over `reverie-rpc-transport` (UDS + bincode) to an in-Hermit
  Detcore coordinator (`GlobalState`). A ptrace safety net
  (`hermit-cli/src/sabre_ptrace.rs`) catches any syscall instruction SaBRe
  missed.
- **Hermit worktree:** `worktrees/274/hermit`, branch `codex/dbi-compat-debug`
  (based on `origin/main`; behind 10, no source changes made — investigation
  only). No hermit source changes were warranted (see Conclusion).
- **Command form:** `hermit run --backend sabre --strict -- target/release/<guest>`
  (L1). One guest also L2-verified with `--strict --verify`.
- **Corpus:** the same 37 built `[[bin]]` guests as the DBI sweep
  (`target/release/examples/` is empty; the user-named `hello_world`/`counter1`
  are reverie-examples bins that do not exist in the Hermit workspace, so the
  Hermit guest corpus was substituted, exactly as in the DBI task).

## Backend wiring is complete in this workspace (no env vars needed)

`hermit run --backend sabre -- /bin/echo hello` prints `hello` (rc 0) with **no**
environment setup. Resolution (`hermit-cli/src/lib.rs`):

- `resolve_sabre_binary()` finds the `sabre` loader as a packaged resource
  (`target/install_pkg/rsrcs/sabre`; also present at
  `target/install-build/sabre/sabre`). `HERMIT_SABRE_BINARY` can override.
- `sabre_runtime_library_path()` finds `libdetcore_sabre.so` beside the hermit
  binary (`target/release/libdetcore_sabre.so`, built by the `detcore-sabre`
  crate).

Note: the separate `hermit --backend sabre strace` *diagnostic* path
(`backends.rs::run_sabre_strace`) is different and DOES require reverie-side
artifacts (`HERMIT_SABRE_RUNNER`=reverie-sabre-strace, `HERMIT_SABRE_BINARY`,
`HERMIT_SABRE_PLUGIN`); those are not built here, so that path errors. The real
Detcore-over-SaBRe `run` backend does not use them.

`--log info` on every guest tested shows `patched_sites=0`, i.e. SaBRe's static
rewriting covered **all executed syscall sites** and the ptrace safety net had
to patch nothing.

## Result summary (partial sweep — see Environment caveat)

| Guest | SaBRe | DBI (prior) | Note |
| --- | --- | --- | --- |
| chaos_cas_sequence_bin | OK (L1) | OK | |
| chaos_hello_chaos | OK (L1) | OK | |
| chaos_keyvalue_bin | **OK (L1)** | **HANG** | SaBRe beats DBI |
| nanosleep_threads_nocrash_rust | OK (L1) | OK | |
| network_bind_rs | OK (L1) | OK | |
| network_bind_full_rs | **OK (L1)** | **FAIL (EADDRINUSE)** | SaBRe has container/netns |
| rustbin_bind_connect_race | OK (L1) | OK | |
| rustbin_clock_gettime | **OK (L2)** | OK | bitwise-identical repeat |
| rustbin_tkill | **OK (L1)** | **FAIL (rt_tgsigqueueinfo EPERM)** | SaBRe beats DBI |
| rustbin_exit_group | **HANG (rc124@100s)** | HANG | shared preemption gap |

**L2 evidence (rustbin_clock_gettime):** `hermit run --backend sabre --strict
--verify -- target/release/rustbin_clock_gettime` →
`:: Success: deterministic. Determinism verified.` (rc 0). Relaxations: none.

## SaBRe vs DBI — the discriminating findings

SaBRe passes **three** guests that DBI fails, because SaBRe routes *every*
syscall through the one Detcore coordinator and runs the guest inside Hermit's
container (PID/network namespace), whereas DBI (DynamoRIO) selectively
translates only some syscalls and has no namespace container:

1. **rustbin_tkill** — DBI returned `rt_tgsigqueueinfo(3,3,..) = -1 EPERM`
   because DynamoRIO translated `tkill`/`tgkill` virtual→real tids but passed
   the sigqueue family raw to the kernel, where virtual pid 3 is an unrelated
   host process. Under SaBRe the guest prints `tkill + rt_tgsigqueueinfo +
   rt_sigqueueinfo delivery OK. Test complete.` (rc 0): all three go through
   Detcore consistently and the guest runs in a PID namespace where virtual ==
   real. (Same DBI gap detailed in `DBI_COMPAT_SWEEP_20260727.md` §FAIL(1).)
2. **network_bind_full_rs** — DBI panicked `EADDRINUSE` (no network namespace,
   host ephemeral ports already bound). SaBRe passes: the guest runs in Hermit's
   container with a private port space.
3. **chaos_keyvalue_bin** — DBI HANG; SaBRe OK.

## Shared gap: busy-loop preemption

**rustbin_exit_group HANGs under SaBRe** (rc 124 at 100 s), the same as DBI.
Root cause confirmed in `detcore-sabre/src/lib.rs` (262 lines): the plugin
forwards syscalls (`syscall`, `syscall_with_inject`) and virtualizes VDSO
(`clock_gettime`/`getcpu`/`gettimeofday`/`time`) and `rdtsc`, but provides **no
RCB/PMU logical clock or timer-preemption source**. SaBRe only observes the
guest at syscall interception points, so a thread that busy-loops without
issuing a syscall (`loop { print!("") }` in `exit_group.rs`) never yields and
Detcore's sequentializing scheduler cannot preempt it → deadlock. This is the
same *class* of gap as DBI (no `set_timer` backing per the `adding-a-backend`
skill), realized through a different trap mechanism. Under ptrace an armed PMU
timer preempts these guests and they pass.

## Conclusion & handoff

- The SaBRe backend is **materially more compatible than DBI**: it fixes DBI's
  namespace/id-translation failures (tkill, bind_full) and one HANG
  (keyvalue) because it channels all syscalls through one Detcore coordinator
  inside Hermit's container. Confirmed passing at L1 for 8 guests + tkill, and
  at **L2** for rustbin_clock_gettime.
- The **one shared, non-trivial gap** is preemption of syscall-free busy loops
  (rustbin_exit_group HANG). Fixing it requires an RCB/PMU-driven preemption
  source in the SaBRe path (in `detcore-sabre` / the SaBRe guest), not a
  hermit-CLI or Detcore-core change — Detcore is backend-agnostic and already
  arms the timer under ptrace.
- **No hermit-only (detcore-core) fix is warranted** from this sweep; the
  remaining gap is a SaBRe-backend capability, and the compatibility wins are
  already present on `origin/main`.

## Environment caveat (why this is a partial sweep)

During this investigation the dev host was under a **memory-cgroup OOM
condition** (bash processes OOM-killed; `free` showing ~24 GB free of 754 GB)
and **load average ~140–160**, driven by a **dedicated parallel `sabre` agent**
(slot `sabre`, task `compat-sabre-hermit-strict`, branches
`codex/compat-sabre-hermit-strict` + `codex/sabre-gap-closure`) running the
comprehensive SaBRe sweep across the same corpus plus fork/thread/linker edge
cases. To avoid duplicating that agent's lane and worsening the OOM/load
pressure, this sweep was curtailed to the DBI-comparison-critical subset above
(a background full 36-guest run and a background busy-looper run were both
OOM-killed mid-flight). The exhaustive 37-guest × L1/L2 matrix is that agent's
deliverable; the results here are sufficient for the SaBRe-vs-DBI comparison the
user asked for. Guests not individually rerun here are labeled accordingly.
