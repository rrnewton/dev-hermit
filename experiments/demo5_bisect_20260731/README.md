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

## Isolated re-bisect (2026-08-01) — SECOND regression under taskset? STILL NO.

**Question (owner, P0):** With RCB-time flipped back ON (config-revert), find the
commit N+K in `[adbfaca3..2f3689bd(HEAD)]` where re-arming preemption STOPS
repairing demo5 — determines revert-N+K vs land-the-fairness-fix.

**Answer: there is no discrete N+K.** A fully-isolated `git bisect` (private
`CARGO_HOME` 11G reflink to kill cross-agent dynamorio-checkout thrash; every boot
under `taskset -c 288-315`; RCB-ON config held; 2 reps/commit) collapsed to
first-bad **`e3067d69` "Clarify post-facto human review criteria"** — a DOCS-ONLY
commit (`.claude/skills/*.md` + `AGENTS.md`, 0 compiled source). Its release
`hermit` binary is **byte-identical** to its parent `0df976bb`
(both sha256 `0c40b8fe5ef344d8a2b22841a5921eca1574b29db093cb4ea7315f4e102ddfdb`,
56428120 B). During the bisect the SAME binary tested GOOD 2/2 (as `0df976bb`) and
BAD 2/2 (as `e3067d69`). Re-running that identical binary 6× under load ~72-78 →
**6/6 booted to shell** (flips the bisect's BAD). Interleaved endpoint A/B at
matched load ~75-83, 4 rounds each: `adbfaca3` (16108ac8) **1 GOOD / 4**, HEAD
`2f3689bd` (cafb17b1) **0 GOOD / 4** — even the "last-good" anchor `adbfaca3`
wedges 3/4 under load; HEAD only marginally worse (small cumulative timing drift,
not one revertable commit). The interim "adbfaca3 6/6 GREEN vs HEAD 0/6 WEDGE
under isolation" reading was a LOW-LOAD window that faked a categorical boundary.

**Decision:** revert is not viable (bisect lands on documentation); config-revert
(re-arm `--max-timeslice`) is necessary but does not reliably repair demo5 under
load → **land the real fix** (`scheduler-vtime-jump-unproductive-pollers` /
fairness overlay, owner-gated trigger #4).

Evidence (gitignored):
`ignored/hermit-231-private/{iso-bisect.log, fliprate.out, ab2.out, bisect_run_iso.sh, launch_iso_bisect.sh, hermit-0df976bb, hermit-e3067d69 (identical), hermit-adbfaca3, hermit-2f3689bd}`

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
