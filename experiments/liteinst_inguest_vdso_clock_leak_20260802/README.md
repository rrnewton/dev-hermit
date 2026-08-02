# LiteInst in-guest vDSO clock leak — the sole authoritative-gate blocker

## Question

The in-guest LiteInst flagship stack (Hermit #1429 → #1443 → pin PR #1466)
lands everything *except* one authoritative-gate test:
`liteinst_strict_verify_virtual_identity_and_time`
(`hermit-cli/tests/liteinst_advanced.rs:148`). It panics with
`guest startup consumed an implausible amount of virtual time: 1785656129` —
`date -u +%s` under `--backend liteinst --strict --verify` returns the **host
wall-clock** (~`1.785e9`) instead of the deterministic epoch
(`1_767_225_600`).

Is this an owner-gated *structural* limitation of the in-guest model (the prior
memory claim), or a bounded, in-lane determinism bug?

**Answer: a bounded determinism bug.** The in-guest Tool determinizes the *raw*
`clock_gettime` syscall correctly; only the glibc **vDSO fast path** leaks host
time. `reverie-ptrace::vdso::vdso_patch` — which rewrites the vDSO time symbols
into real `syscall` stubs so the seccomp trap can reach them — is effective under
a full-Tool ptrace supervisor but **not effective under the in-guest
`TracerBuilder::<()>` lifecycle supervisor**. This *refutes* the earlier
"vDSO time is structural / owner-gated" note.

## Method — the decisive probe

`src/clock_probe.c` reads the clock two ways in the same process:

```c
clock_gettime(CLOCK_REALTIME, &vd);              /* libc -> __vdso_clock_gettime fast path */
syscall(SYS_clock_gettime, CLOCK_REALTIME, &rw); /* raw syscall, bypasses the vDSO */
printf("vdso=%ld raw=%ld\n", (long)vd.tv_sec, (long)rw.tv_sec);
```

Run native, under `--backend liteinst --strict`, and under the golden
`--backend ptrace --strict` on the flagship Hermit
(`codex/liteinst-flagship-hermit-pin`, reverie pin `456b628`).

## Results

See `results.csv`.

| backend  | vdso tv_sec  | raw tv_sec   | interpretation                                   |
|----------|--------------|--------------|--------------------------------------------------|
| native   | 1785660391   | 1785660391   | host wall-clock (both)                            |
| liteinst | 1785660392   | **1767225600** | **vDSO leaks host time; raw syscall = epoch**  |
| ptrace   | 1767225600   | 1767225600   | both determinized to epoch (golden reference)    |

The decisive contrast is the single liteinst row: `raw != vdso`. The in-guest
per-syscall determinization path provably works (raw → epoch); only the vDSO
fast path escapes it.

## Interpretation (root cause, source-confirmed)

- glibc's `clock_gettime` calls `__vdso_clock_gettime`, which reads the kernel
  `vvar` page in **pure userspace — no syscall** — so seccomp cannot trap it.
- `reverie-ptrace/src/vdso.rs:247 vdso_patch` exists precisely to fix this: it
  mprotects `[vdso]` RWX, overwrites each time symbol with a
  `mov <nr>,%eax; syscall; ret` stub, and restores R+X. The redirected symbol
  then makes a real syscall at a non-gate IP, which the in-guest preload seccomp
  filter (`reverie-preload/src/seccomp.rs::for_trusted_gate`, traps by IP)
  would catch and route to the in-guest Tool for determinization — exactly as it
  already does for libc's raw `clock_gettime`.
### RESOLVED by runtime instrumentation (2026-08-02) — Hypothesis (a): `vdso_patch` is never reached

A `VDSO_TRACE` `eprintln!` build (scratch reverie checkout at `456b628`) shows
the ptrace backend emits the full patch trace (ENTER → found `[vdso]` → 5 stub
writes → DONE) while the **LiteInst backend emits zero `VDSO_TRACE` lines**. The
reason is now source-confirmed:

- At the pinned `456b628`, `reverie-liteinst/src/backend.rs::launch<T>()`
  (line 549) launches the guest via a **plain `child_command.spawn()`**
  (line 620) — a `std::process::Command::spawn()` with **no `TracerBuilder` and
  no ptrace attach**, in *both* the `Some(tool_data)` and `None` arms. The
  `TracerBuilder`-based `run_host_with_preload*` paths (lines 208–330) exist but
  `launch()` does not use them.
- `vdso_patch`'s only call site is `reverie-ptrace/src/task.rs:1757` via
  `tracee_preinit` (`tracer.rs:1731 postspawn`), which only runs under a ptrace
  supervisor. With no supervisor, `vdso_patch` never executes for the LiteInst
  backend.
- LiteInst's only in-guest interceptor is `reverie-preload`'s `InProcessSeccomp`
  (`lifecycle.rs:73-79`): a SIGSYS handler + `SeccompFilter::for_trusted_gate`
  that traps **syscall instructions** at non-gate IPs. The glibc vDSO
  `clock_gettime` fast path issues **no syscall instruction** (it reads the
  kernel `vvar` page directly), so it is never trapped → host time leaks. The
  raw `syscall(SYS_clock_gettime)` *is* a syscall instruction → trapped → SIGSYS
  → `LiteinstDispatcher::dispatch` → determinized. That is exactly the observed
  `vdso=host raw=epoch` split.

**Correction to this note's own earlier framing:** the premise that the in-guest
arm uses `TracerBuilder::<()>` (and therefore a bounded "why is the ungated
`vdso_patch` ineffective" wiring bug) was WRONG — that describes the *later*
commit `8c9aad1` / reverie PR #337, not the pinned `456b628`. At `456b628` there
is no supervisor at all, so there is no uncalled `vdso_patch` to wire up.

## Disposition — OWNER-GATED (not a contained fix)

`reverie-preload/src/lifecycle.rs:16-21` documents the gap directly: the
in-process seccomp "cannot cover the ~40 loader/startup syscalls before the
constructor, **vDSO fast paths**, or `exec`." The documented remedy —
`HybridPtrace` LifecycleController (`lifecycle.rs:82-104`) — is an intentional
non-functional skeleton (`install()` returns `ErrorKind::Unsupported`) that a
future task must build. Two remedy paths exist and **both are owner-gated** under
the Reverie API Policy because both change the LiteInst
syscall-interception / lifecycle mechanism:

1. **HybridPtrace lifecycle controller** (smallest correct change): a thin ptrace
   launcher that stops the guest at the post-exec preinit stop, calls the
   *existing, proven* `reverie_ptrace::vdso::vdso_patch` on the guest `[vdso]`,
   then hands the hot path to the in-process SIGSYS trap. Reuses `vdso_patch`
   verbatim but introduces a new backend lifecycle controller / interception
   mechanism. (Reverie PR #337's `TracerBuilder::<()>` lifecycle-supervisor slice
   is this same class of change — it *would* reach `vdso_patch` — but #337 also
   carries a separate `backend.rs` LD_PRELOAD regression and is itself the
   lifecycle change, so it is not a drop-in either.)
2. **In-guest-only vDSO neutralization** (self-patch the vDSO in the preload
   constructor): not reliably contained — the in-handler dispatch fails closed
   with `EOPNOTSUPP` for any unhookable site (`runtime.rs:1722-1723`, no
   trap-and-emulate), and the only fully-safe variant depends on glibc-version
   vDSO fallback behavior. New in-guest interception machinery → owner-gated.

This single vDSO clock leak is the **sole** red cell on PR #1466's authoritative
gate (`Regular tests (GitHub-managed portable)`); every other flagship test
(fork L2, thread-clone fail-closed, python entropy/random, preload-inert) is
green. But turning it green requires an owner-reviewed lifecycle-controller task,
not an autonomous in-lane fix. It is reported as an owner-gated blocker; the
shipped flagship increment (flat plain-fork L2, PR #1466) is unaffected.

## Reproduction

```bash
cd ~/work/dev-hermit
HB=worktrees/liteinst/hermit/target/debug/hermit   # build: cargo build -p hermit
cc -O2 -g -Wall -Wextra -Werror -o /tmp/clock_probe \
   experiments/liteinst_inguest_vdso_clock_leak_20260802/src/clock_probe.c

$HB --log=error run --backend liteinst --strict -- /tmp/clock_probe   # vdso=host raw=1767225600
$HB --log=error run --backend ptrace   --strict -- /tmp/clock_probe   # vdso=1767225600 raw=1767225600
```
