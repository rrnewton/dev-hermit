# LiteInst backend B2.1 root-cause analysis and increment roadmap

Status: research deliverable, 2026-07-28. Task `ratchet-liteinst-backend`.
Agent: hermit-liteinst. **No code landed** — every fix routes to
approval-gated cross-repo `reverie-liteinst` (see §6).

## 1. Measurement snapshot (authoritative baseline)

| Item | Value |
| --- | --- |
| Hermit SHA | `9482e344b7a458284637cb9a0230f81c75f5c68f` (branch `codex/ratchet-liteinst-b21`, based on `origin/main`) |
| Reverie pin (`detcore-liteinst/Cargo.toml:21`) | `aecbbacee525d58dfc485e7c56d43f498bba6a31` |
| Build | `cargo build --release` (full workspace; rebuilds `libdetcore_liteinst.so`) |
| Host | Meta devserver, PMU available |
| B2.1 denominator (`examples/`) | `date.sh devrand.sh race.sh rand.py timed-progress-bar.py` |

`origin/main` is 1 commit ahead (`340b72b3`, an unrelated Redis e2e test);
it touches no liteinst/reverie/signal/Cargo path, so this baseline is
authoritative for the LiteInst backend.

## 2. Headline finding — the B2.1 scorecard is interpreter-sensitive

The documented "LiteInst 2/5 local, 1/5 parity" baseline
(`backend-maturity-model.md`) is reproducible **only with a profiler-free
Python**. The measured result depends on which `python3` resolves:

- **Default `python3` → `/usr/local/fbcode/platform010/bin/fbpython` /
  `/usr/local/bin/python3` = Python 3.12.13+meta.** meta-python installs a
  **SIGPROF profiler handler** at startup. Under LiteInst this
  `rt_sigaction(SIGPROF, <real handler>)` returns **-1/EPERM**, meta-python's
  C++ `CHECK` fails (`Check failed: sigaction(...) sigprof (enable)`), and the
  process aborts (SIGABRT, exit 134). Result: **0/5 local** (both python
  examples crash; the three sh examples fail on exec/fork).
- **Profiler-free `/usr/bin/python3.9` (3.9.25)** installs no SIGPROF handler
  and reproduces the documented **2/5 local, 1/5 parity exactly.**

Actionable model change: **B2.1 example reports must pin the Python
interpreter** (record `readlink -f $(command -v python3)` + `--version`).
An interpreter swap silently moves the score by 2 examples.

## 3. Per-example before/after (profiler-free python3.9, LiteInst vs ptrace)

| Example | ptrace | LiteInst local (`--strict --verify`) | LiteInst→ptrace parity | Gap / root cause |
| --- | --- | --- | --- | --- |
| `date.sh` | PASS | FAIL | — | `exec /usr/bin/date` → guest **execve ENOTSUP** (no exec support) |
| `devrand.sh` | PASS | FAIL | — | `exec hexdump /dev/urandom` → guest **execve ENOTSUP** |
| `race.sh` | PASS | FAIL | — | bash forks subshells → guest **fork/clone ENOTSUP** |
| `rand.py` | PASS | **PASS (local)** | **FAIL (parity)** | getrandom interception-completeness desync of stateful `Pcg64Mcg`: ptrace stream `c448fd56…` ≠ liteinst `5fa4bb5c…` |
| `timed-progress-bar.py` | PASS | **PASS (local)** | **YES** (`d6fb4e6f…`) | matches ptrace |

**Before → target:** local 2/5 → 5/5; parity 1/5 → 5/5. No increment is
landable in-tree (§6).

With default meta-python 3.12, `rand.py` and `timed-progress-bar.py`
additionally regress from PASS to SIGABRT — see §4.

## 4. Root cause: guest signal-handler installs return EPERM(-1)

Exact site: `reverie-liteinst/src/runtime.rs`
(checkout `~/.cargo/git/checkouts/reverie-2fc770f7a9c80803/aecbbac/`).

- `forward_nested_tool_syscall` (line ~703). `unsupported_signal_state`
  (lines ~717-735) = `rt_sigaction, rt_sigprocmask, sigaltstack, rt_sigsuspend,
  pselect6, ppoll, epoll_pwait, epoll_pwait2, io_pgetevents`. Line ~738-739:
  `} else if unsupported_signal_state { event.result = -i64::from(libc::EPERM); }`.
- `signal_action_supported` (line ~666) returns false when `rt_sigaction`
  installs a non-`SIG_DFL`/non-`SIG_IGN` handler (or touches `SIGSYS`, the
  backend's own trap signal).

Confirmed liteinst-specific with a minimal C reproducer (`sigaction(SIGPROF,
handler)`): **ptrace = 0, sabre = 0, liteinst = -1**. detcore's own logic is
fine — `detcore/src/syscalls/signal.rs:handle_rt_sigaction` no-ops
`PERF_EVENT_SIGNAL` and injects otherwise; ptrace exercises the same detcore
and returns 0. The -1 is produced entirely inside the liteinst inject/forward
path, before detcore's decision matters for this path.

**SaBRe is the fix precedent:** SaBRe already no-ops guest signal-handler
installs to **success (0)** rather than rejecting, which is why SaBRe does not
crash meta-python here (SaBRe still fails `date/devrand/rand/race` on
time/random/schedule divergence, but it does not SIGABRT).

## 5. Gap taxonomy (all four B2.1 gaps)

1. **execve ENOTSUP** (`date.sh`, `devrand.sh`) — LiteInst supports one
   process image; guest `execve`/`execveat` → `ENOTSUP`
   (`unsupported_process`, runtime.rs ~704-716, `event.result =
   -ENOTSUP`). Architectural: single in-process patched image.
2. **fork/clone ENOTSUP** (`race.sh`) — same `unsupported_process` set
   (`clone/clone3/fork/vfork`). No multi-process/thread lifecycle.
3. **getrandom parity desync** (`rand.py`) — LiteInst local-deterministic but
   its intercepted syscall set differs from ptrace, so detcore's stateful
   `thread_prng` (`Pcg64Mcg`, position-dependent) advances to a different
   state than ptrace. Distinct from `/dev/urandom` (position-independent
   `canonical_random_device_byte`, PR-1096) which is already parity-clean.
   This is an **interception-completeness** gap, not a value-determinism bug.
4. **guest signal-handler EPERM** (meta-python 3.12 SIGPROF; §4) — the only
   gap that causes a *hard crash* on a common operation; the most impactful
   to fix, and the smallest change.

## 6. Why nothing is landable in-tree (routing)

`detcore-liteinst/src/lib.rs` is a thin `.init_array` LD_PRELOAD shim; the
backend semantics live in `reverie-liteinst` (consumed as a pinned git rev in
~8 `Cargo.toml` files). Every gap above is in `reverie-liteinst/src/runtime.rs`
inject/forward policy — a cross-repo change requiring:

1. a PR to `rrnewton/reverie`, then a pin bump + full rebuild + revalidation; and
2. **user approval** — per the parent **Reverie API Policy**, changes to
   "syscall interception/injection semantics" and signal handling are
   approval-gated ("discuss with the user BEFORE implementation"). Flipping
   EPERM→success for signal-handler installs is exactly such a semantic change,
   and it partially contradicts the hermit safety invariant of "reject
   unsupported injection paths rather than partly emulating them" — so it MUST
   be discussed, not smuggled in as cleanup.

This matches the standing observation (MEMORY): *every LiteInst backend gap
routes to pinned reverie, approval-gated.*

## 7. Increment roadmap (approval-ready, ordered by value/effort)

1. **[Recommended first] Signal-handler no-op-to-success** for non-`SIGSYS`
   guest `rt_sigaction` handler installs in `reverie-liteinst` (match SaBRe).
   Small, precedented, unblocks meta-python 3.12 from hard-crashing → restores
   default-interpreter `rand.py`/`timed-progress-bar.py` to their python3.9
   behavior. Record intent, never deliver (preserves determinism / no async
   signal delivery). **Approval-gated.**
2. **getrandom interception-completeness** — enumerate the ptrace-intercepted
   syscall set vs LiteInst's and close the delta so `thread_prng` advances
   identically → `rand.py` parity. Backend-scope, `reverie-liteinst`.
   **Approval-gated.**
3. **execve support** (`date.sh`, `devrand.sh`) — requires re-patching the new
   image post-exec (or a HybridPtrace fallback that owns lifecycle). Large;
   coordinate with hermit-e9patch on the shared
   `reverie_preload::lifecycle::HybridPtrace` controller (still an Unsupported
   skeleton) so both backends benefit.
4. **fork/clone support** (`race.sh`) — multi-process lifecycle + RPC
   aggregation across children. Largest; also HybridPtrace-adjacent.

## 8. Coordination with hermit-e9patch

Per e9patch coord notes on the task (reverie PRs #240/#241): e9patch consumes
the **shared** `reverie-preload` seam (`dispatch::{SyscallDispatcher,
SyscallEvent, PassthroughDispatcher}`, `lifecycle::{LifecycleController,
InProcessSeccomp, HybridPtrace, RuntimeConfig}`, `install()`). **The EPERM in
§4 is in `reverie-liteinst/src/runtime.rs`, NOT in the shared
`reverie-preload` — so fix #1 is liteinst-only and does not touch e9patch's
contract.** However: e9patch's `PassthroughDispatcher::apply_guards` has its
own `sigaltstack/rt_sigprocmask/…` guard policy; if we later unify signal
handling into the shared seam, ping hermit-e9patch first (they hold
`PassthroughDispatcher::new()/apply_guards`, the `SyscallDispatcher` trait, and
`install()` as a shared contract). The HybridPtrace controller needed for
increments #3/#4 lives in the shared crate — whoever needs it first
co-designs it there.

## 9. Reproduction

```bash
cd ~/work/dev-hermit/worktrees/liteinst/hermit    # @ 9482e344
cargo build --release
PROF=/usr/bin/python3.9                            # profiler-free; NOT default python3
for ex in date.sh devrand.sh race.sh rand.py timed-progress-bar.py; do
  ./target/release/hermit run --backend liteinst --strict --verify -- examples/$ex
  ./target/release/hermit run --backend ptrace   --strict --verify -- examples/$ex
done
# Signal-handler probe (ptrace=0, sabre=0, liteinst=-1):
#   C program: sigaction(SIGPROF, <handler>, NULL); print rc
```
