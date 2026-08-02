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
- `vdso_patch` is invoked **ungated** in `reverie-ptrace/src/task.rs:1757`
  (`vdso::vdso_patch(self).await.expect(...)`, contrast the CPUID patch at
  1775 gated on `subscriptions.has_cpuid()`), so it *should* run for the
  in-guest `()` supervisor too.
- Yet the vDSO still leaks host time under liteinst while it is determinized
  under the full-`T` ptrace supervisor. So the redirect is **not landing** on
  the guest's live `[vdso]` in the in-guest launch path
  (`reverie-liteinst/src/backend.rs` in-guest `None` arm =
  `TracerBuilder::<()>::new(command).spawn()`).

The remaining unknown — **why** the ungated `vdso_patch` write is ineffective
under the `()` supervisor (not reached for this task, silently no-ops, is
reverted, or the redirected syscall is not trapped in-guest) — requires hands-on
runtime inspection (trace in `vdso_patch`, or dump the running guest's `[vdso]`
bytes). That is a bounded corrective fix reusing the existing `vdso_patch`
primitive + the existing in-guest seccomp trap, **not** a new Tool/Guest/Backend
abstraction. If the eventual fix turns out to change syscall-interception
semantics, it becomes owner-gated; the diagnosis so far points at a
plumbing/ordering bug, not a semantic change.

## Disposition

This single vDSO clock leak is the **sole** red cell on PR #1466's authoritative
gate (`Regular tests (GitHub-managed portable)`); every other flagship test
(fork L2, thread-clone fail-closed, python entropy/random, preload-inert) is
green. Fixing it turns the gate green and unblocks the entire flagship stack from
landing. It is reported and preserved here; the fix is being pinned via runtime
inspection before any reverie-crate edit.

## Reproduction

```bash
cd ~/work/dev-hermit
HB=worktrees/liteinst/hermit/target/debug/hermit   # build: cargo build -p hermit
cc -O2 -g -Wall -Wextra -Werror -o /tmp/clock_probe \
   experiments/liteinst_inguest_vdso_clock_leak_20260802/src/clock_probe.c

$HB --log=error run --backend liteinst --strict -- /tmp/clock_probe   # vdso=host raw=1767225600
$HB --log=error run --backend ptrace   --strict -- /tmp/clock_probe   # vdso=1767225600 raw=1767225600
```
