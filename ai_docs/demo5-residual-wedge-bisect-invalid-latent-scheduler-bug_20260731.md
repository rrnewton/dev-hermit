# demo5 residual wedge: bisect is INVALID — latent `-icount` scheduler starvation, not a window regression

**Date:** 2026-07-31
**Angle:** differential bisect-to-culprit (distinct from agent 220's good-vs-broken trace-diff)
**Task:** DEMO5 SPECULATIVE ATTACK (owner-authorized). Range under test: hermit
`f6c836b1..ae2565be` (35 linear commits; `#1190` merge `61d8df39` in-history).
**Author:** impl agent, opus-4.8

## TL;DR

The assigned bisect — "bisect from known-good `demo-20260729` (hermit `f6c836b1`)
to the culprit commit for the residual demo5 wedge" — **cannot be performed,
because the known-good anchor does not boot demo5 green in the current
environment.** A monotone `git bisect` requires a GOOD endpoint; here **both**
endpoints of the window hang, at *different* points, so the landscape is
non-monotone and there is no single culprit commit.

The residual wedge is a **latent, timing-sensitive hermit scheduler bug**
(`--no-rcb-time -icount` vCPU starvation by an unproductive poller), present
across the whole window. It is **not** a step-backward introduced by any commit
in `f6c836b1..ae2565be`, and it is **not** environmental — bare QEMU boots green.
`#1190`'s clock fix does **not** clear it.

## Method

Faithful, side-effect-free boot harness replicating the *exact* demo5 command
(`05-qemu-boot.py` lines 128-157 + `build_qemu_command` argv):

```
hermit run --strict --no-rcb-time --target-timeslice 100000 --max-timeslice disabled -- \
  python3.12 demos/lib/qemu_controller.py boot --qemu qemu-system-x86_64 \
  --qmp-socket … --serial-log … --disk … --kernel bzImage --initrd initramfs.cpio.gz \
  --snapshot-name hermit-boot --timeout {300|600}
```

Private per-run work dir + private asset dir (symlinks to the shared read-only
`bzImage`/`initramfs.cpio.gz`) so it never touches agent 220's shared
`boot-anchor`/`hermit-boot.qcow2` and never rebuilds the primary. Harness +
raw logs: `experiments/demo5_bisect_20260731/ignored/` (gitignored).
Host: devbig-class, 316 cores, QEMU 10.1.2, load ~8-26% during the boot tests.

## Results

| Binary / config | Position | Boot outcome | Wedge point | Evidence |
| --- | --- | --- | --- | --- |
| **bare QEMU 10.1.2**, exact demo argv (`-icount shift=0,sleep=off`), **no hermit** | — | **GREEN** | none — reaches interactive shell | 380 serial lines, `rtc: 2022-01-01T00:00:07Z`, past `hpet0`, `Interactive busybox shell` |
| hermit **`f6c836b1`** | window START = parent tag `demo-20260729` "last known green demo cut" | **HANG** | **QEMU startup** | 0 serial lines, no `qmp.sock`; dtid 9 exec's QEMU then `brk(NULL)` and issues **no further syscalls**; controller `wait_for_socket` times out → SIGKILL tid 9. Reproducible 2/2 (`--timeout 300` and `600`; identical signature; 915s virtual burned at t600) |
| hermit **`61d8df39`** (`#1190`) | mid-window | **HANG** | **HPET** | 250 serial lines frozen at `hpet0: … 100.000000 MHz counter` `[0.724]`; 223,008 `SleepUntil(LogicalTime(0))` commits |
| hermit **`ae2565be`** | window END (220's 0/3 broken) | **HANG** | **HPET** | 250 serial lines frozen at `hpet0` `[0.724]`; info-log tail: `COMMIT turn 636946, dettid 17 … {SleepUntil(LogicalTime(0)): W}, on previously committed 1_767_227_900.362_250_000s` (committed vtime frozen) |

## Why the bisect is invalid

`git bisect` finds the first commit where GOOD→BAD. It requires a green anchor.
- The designated anchor `f6c836b1` is **not** green here — it wedges at QEMU
  *startup*, i.e. **earlier/worse** than the "broken" `ae2565be` (which reaches
  HPET). The landscape is non-monotone: `startup-wedge (f6c836b1)` →
  `HPET-wedge (#1190, ae2565be)`. There is no GOOD end and no single BAD
  transition, so no culprit commit exists to bisect to.
- The parent tag message ("Last known green demo cut before 2026-07-31 fix")
  reflects a green cut in a *different runtime condition*, not reproducible on
  this host today. Bare QEMU booting green rules out a QEMU-version/asset
  regression, so the non-reproducibility is inside hermit's deterministic
  scheduler and is **timing/load sensitive** (consistent with the prior
  load-sensitivity observations).

## Classification: latent foundation bug, NOT a step-back

- **Root class:** hermit's `--no-rcb-time -icount` scheduler starves the QEMU
  vCPU thread whenever an unproductive poller keeps the run_queue non-empty
  (`SleepUntil(LogicalTime(0))` yields), so `scheduler` step2d never jumps
  committed virtual time to the pending `-icount` timer deadline. The vCPU is
  never handed enough virtual time to advance past the busy-poll (QEMU main-loop
  poll at startup; guest HPET calibration later). This is the
  `scheduler-vtime-jump-past-unproductive-pollers` /
  `demo5-wedge-clock-skew-past-deadline-poller` class (agents 220/227; memory
  notes) — confirmed here by 223k–636k `SleepUntil(LogicalTime(0))` commits with
  frozen `committed_time`.
- **Window commits moved the wedge point, they did not introduce it.** Between
  `f6c836b1` and `#1190` something improved QEMU-*startup* scheduling
  (startup-wedge → HPET-wedge), a partial improvement; the HPET wedge then
  persists identically at `#1190` and `ae2565be`.
- **`#1190` is neither cause nor fix.** `61d8df39` wedges at HPET exactly like
  `ae2565be`; the clock-domain fix did not clear the starvation wedge.
- **Environment excluded as the wedge cause:** bare QEMU (same version, assets,
  argv) boots to a shell.

## Convergence with agent 220 (distinct angle, same root)

220 runs the good-vs-broken full-log trace-diff. My bisect-to-culprit angle
converges on the *same* root (unproductive-poller vtime starvation) and adds
three independent facts 220's angle does not surface: (1) the "known-good" tag
is not green here → **no culprit commit exists**; (2) **bare-QEMU green** isolates
the wedge strictly to hermit; (3) **`#1190` wedges at HPET** → not the fix.

## Recommendation

No revert/bisect fix is possible — there is no green hermit anchor in the window
to revert *to*. The real fix is the foundation scheduler change: make step2d
jump committed virtual time to the earliest pending `-icount`/timer deadline when
only unproductive pollers remain runnable (or admit the vCPU's timer deadline as
a productive event). That is a **core DetCore scheduling change (post-facto
trigger #4)** and must be **owner-designed** — do not freelance a speculative
revert. The task's "speculative competing-fix PR" option is therefore N/A for a
bisect-revert; any speculative fix would itself be the owner-gated scheduler
change.
