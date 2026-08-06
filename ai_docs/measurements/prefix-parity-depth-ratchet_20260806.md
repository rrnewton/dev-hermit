# Prefix-parity depth: the first INFO-log ratchet numbers

**Date:** 2026-08-06 · **Task:** `prefix-parity-depth-ratchet-metric` · Local, no egress, no validate slot

## The metric

For a (guest, backend) pair: **how many Detcore `COMMIT` records into the INFO log the backend
stays byte-identical to the ptrace golden log.** `Y/Z`, Z = COMMITs in the golden log.

Why this shape: "does demo05 boot" is a boolean that has read 0 for months, so it cannot show
progress and cannot be ratcheted. Prefix depth is monotonic — every divergence fixed moves the
number up, so partial progress is visible.

**This reads worse than today's published parity, and that is correct.** Published parity is
STDOUT+exit-code only. INFO-log COMMIT depth is strictly stronger: it compares the scheduler's
committed resource sequence, not just what the guest printed.

## Results — every rung, not just the top

`golden_sha=f89c69766371806d3c9b2c3003531df2d59d6118` · `flags: --log=info --backend <be>` ·
`date=2026-08-06` · host devbig014 · single run per cell

| guest | backend | **Y/Z raw** | +pid-norm | +time-norm | note |
|---|---|---|---|---|---|
| `/bin/true` | ptrace | 14/14 | — | — | self-reference |
| `/bin/true` | **dbi** | **0/14** | 2 | 13 | diverges at record 0 |
| `/bin/true` | **sabre** | **2/14** | 2 | 2 | emits only 3 of 14 records |
| `/bin/true` | e9patch | 14/14 | — | — | **vacuous — see caveat** |
| `/bin/echo hello` | ptrace | 43/43 | — | — | self-reference |
| `/bin/echo hello` | **dbi** | **0/43** | 2 | 42 | |
| `/bin/echo hello` | **sabre** | **2/43** | 2 | 2 | emits 32 of 43 |
| `/bin/echo hello` | e9patch | 43/43 | — | — | vacuous |
| `wc -c /etc/hostname` | ptrace | 44/44 | — | — | self-reference |
| `wc -c /etc/hostname` | **dbi** | **0/44** | 2 | 43 | |
| `wc -c /etc/hostname` | **sabre** | **2/44** | 2 | 2 | emits 33 of 44 |
| `wc -c /etc/hostname` | e9patch | 44/44 | — | — | vacuous |
| fork/exec pipeline | ptrace | 16/16 | — | — | self-reference |
| fork/exec pipeline | **dbi** | **0/16** | 2 | 15 | |
| fork/exec pipeline | **sabre** | **2/16** | 2 | 2 | emits 5 of 16 |
| fork/exec pipeline | e9patch | 16/16 | — | — | vacuous |

The `+pid-norm` / `+time-norm` columns are **diagnostic decomposition, not the metric**. The
headline number is the raw column. The extra columns exist because a flat constant across four
different guests is a structural defect, not per-guest behaviour, and the decomposition names it.

## Caveat that voids the only perfect score

**e9patch's 14/14 is not backend parity.** Its own banner:

```
:: Backend: e9patch preprocessing + ptrace runtime; candidate_sites=0; mapped_sites=0
```

It patched **zero sites**, so the runtime *is* the ptrace path. The cell is ptrace compared with
itself. **Do not report e9patch as a parity success.** Its true depth is unmeasured until
`mapped_sites > 0`. This corroborates the previously recorded `e9patch reach=0` finding.

## What the two real numbers mean

**dbi 0/Z is one defect, not a broken backend.** dbi emits the *same count* of records with the
*same resources in the same order* (14/14, 43/43, 44/44, 16/16). It scores 0 because record 0
already carries the **host pid**:

```
golden: COMMIT turn 0, dettid 3       ... DetPid(3)
dbi:    COMMIT turn 0, dettid 3357633 ... DetPid(3357633)
```

That is a determinism bug in its own right — a host pid varies run to run, so dbi cannot be
bitwise-reproducible across runs regardless of parity. Normalising it lifts depth to 2; also
normalising virtual time lifts it to **Z-1**. So dbi is essentially at full sequence parity behind
**three separable defects**: (1) un-determinized pid, (2) virtual-time drift from turn 2 on
(`0.025_125_000s` vs `0.001_619_260s`), (3) one residual — the final Exit record's
`MmId.generation` is `0` under dbi vs `1` under ptrace.

**sabre 2/Z is a reach failure, not a scheduling failure.** It matches the first two records then
jumps straight to `Exit`, emitting 3 of 14. The missing middle is the loader's file-path COMMITs
(`Path(".../libc.so.6"): R`). sabre is not intercepting the dynamic loader's `openat`s — consistent
with the recorded `patched_sites=0 → silent ptrace fallback` behaviour.

## Reproduce

```sh
. /tmp/penv.sh   # or set HERMIT_SABRE_BINARY / HERMIT_SABRE_PLUGIN / HERMIT_E9TOOL
OUT=scratch/prefix2 ci-hub/parity/prefix_depth.sh \
  "bin_true=/bin/true" "echo=/bin/echo hello" \
  "coreutil_wc=/usr/bin/wc -c /etc/hostname" "fork_exec_pipeline=<path>"
```

**Build note.** The shipped `hermit/target/release/hermit` is built **without**
`--features third-party-backends`; dbi and sabre report "support was not included in this build"
while still appearing in `--help` (the enum variants are unconditional). A first pass scored them
`NO-RUN` for that reason — a **build artifact, not a parity datum**. These numbers come from a
coherent offline build with `--features third-party-backends` plus `-p detcore-sabre` in the same
`CARGO_TARGET_DIR`, so the plugin ABI matches the binary. Do not mix the plugin across SHAs.

## Honest limits

- **Single run per cell.** These are parity-vs-golden numbers, not double-run determinism numbers.
  A cell could be identical to golden yet nondeterministic run-to-run.
- **demo05 boot is not measured here.** It is the headline target, not the starting rung.
- **kvm and liteinst are absent** — not measured, not zero.
- Four trivial guests. This is the bottom of the ladder by design.
