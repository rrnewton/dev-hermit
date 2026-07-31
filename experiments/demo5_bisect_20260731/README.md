# demo5 residual-wedge bisect (2026-07-31)

**Question:** Which commit in hermit `f6c836b1..ae2565be` (35 commits) introduced
the residual demo5 boot wedge that `#1190` did not fix? Classify step-back vs
latent-bug.

**Answer:** The bisect is **invalid** — the "known-good" anchor `f6c836b1`
(parent tag `demo-20260729`) does **not** boot demo5 green in the current
environment; it wedges at QEMU *startup*, earlier than the "broken" `ae2565be`
(HPET wedge). Both window endpoints hang → non-monotone → no culprit commit.
The wedge is a **latent, timing-sensitive hermit `-icount` scheduler starvation
bug** (unproductive-poller keeps run_queue non-empty → step2d never jumps
committed vtime to the pending timer deadline), present across the window and
NOT environmental (bare QEMU boots green). `#1190` wedges at HPET identically to
`ae2565be`.

Full analysis + evidence:
`ai_docs/demo5-residual-wedge-bisect-invalid-latent-scheduler-bug_20260731.md`

## Method

Faithful side-effect-free boot harness (`ignored/boot_test.sh`) replicating the
exact `05-qemu-boot.py` boot command with private per-run + private asset dirs
(no collision with agent 220's shared anchor; no primary rebuild). Each binary
built/reused per commit; bare-QEMU control run without hermit.

- Host: 316-core devbig-class, QEMU 10.1.2, load ~8-26% during boot tests.
- Assets: `ignored/qemu-linux/{bzImage,initramfs.cpio.gz}` (kernel 6.17.13).
- Transient harness, logs, per-commit binaries: `ignored/` (gitignored).

## Reproduce

```bash
# bare-QEMU control (green): exact demo argv, no hermit -> boots to shell
# hermit control per commit:
experiments/demo5_bisect_20260731/ignored/boot_test.sh <hermit-bin> <label> <timeout>
#   exit 0 + "RESULT: BOOT_OK …"  => green
#   "RESULT: HANG …" / "FAIL …"   => wedge (see serial.log line count for wedge point)
```

See `results.csv`.
