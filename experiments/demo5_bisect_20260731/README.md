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

## Post-breakage double-bisect (2026-08-01) — SECOND regression? NO.

**Question (owner):** After the parent config flip `0591104` (2026-07-28T15:02:41Z,
`--max-timeslice 2000000000`→`disabled` + `--no-rcb-time`) that removed all timer
preemption, does re-arming RCB (config-revert) give reliably-green demo5 at HEAD,
or is there a SECOND, deeper regression at some hermit commit N+K on top of the
config flip?

**Answer:** **No second regression exists; the config-revert is necessary but
insufficient; the residual wedge is load-sensitive.** A `git bisect` over hermit
`adbfaca3`(good)..`0ca0dec2`(bad) with the RCB-ON config held collapsed to
first-bad `e8c2f704` — but that commit's PARENT is exactly the good anchor
`adbfaca3`, and its only diff is `tests/e2e/manifests/manifest-harness.rs` (a
CI-DAG test harness NOT compiled into the `hermit` binary). **Proof the bisect is
invalid:** the release `hermit` binaries at `adbfaca3` and `e8c2f704` are
BYTE-IDENTICAL (both sha256 `566b61b3f629b6b4696fff09982c98f3d7953d287408ca5131b0f6236e11fae2`)
yet were classified GREEN vs HARD_WEDGE. That same byte-identical binary run 5×
serially under host load ~57-96 wedged **5/5** (all frozen at the identical
`[0.724403]` hpet0), while its lone GREEN occurred in a low-load window. Green vs
wedge is set by HOST LOAD, not by any hermit commit → no discrete regressor to
bisect. Root cause remains the latent foundation bug
`scheduler-vtime-jump-unproductive-pollers` (owner-gated, post-facto trigger #4).

Evidence (gitignored raw logs):
`ignored/hermit-231-private/{verdicts.csv,bisect-run2.log,flaky-rate.log,boot_classify.sh,bisect_run.sh,flaky_rate.sh}`

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
