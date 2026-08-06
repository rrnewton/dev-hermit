# demo05: the capture really was wrong — and fixing it is still not enough to qualify the golden

**Task:** `fix-the-ptrace-golden-self-determinism-at-the-failing-rungs` · hermit-w2
(`[impl agent, opus-5]`) · **2026-08-06** · local, no egress.
**Anchor binary:** `ignored/det4-parity/hermit/target/release/hermit`, self-reported
`hermit 0.2.0 (2026-08-06, g4c70658e7858)` — **clean**, no `-dirty` marker; its checkout is clean at
`4c70658e785834737cbe1524f77330c781a6f5ea`.

This continues `double-run-determinism-of-the-ptrace-reference-per-rung` (same author) and closes
the three items `ptrace-golden-self-determinism-per-rung_20260806.md` left open.

## The headline

The task's corrected premise — *"the demo05 golden was captured wrong; this is a harness fix, not a
product fix"* — is **half right, and the half that is wrong is the half that matters.**

* **Right:** the capture *was* wrong, in **four** distinct ways. det4 named three; I found and
  **fixed** a fourth.
* **Wrong:** capturing correctly, through `05-qemu-boot.py` itself, **still does not produce a
  double-run-stable demo05 golden.** Across ~18 controlled runs of the identical command with a
  pinned binary, the snapshot the demo advertises as "bitwise-reproducible" took **five distinct
  SHA-256 values**.

So **demo05 is DISQUALIFIED as a golden**, and it is disqualified after the harness is fixed, not
before. The ratchet should not wait for it — see §5 for the rung that replaces it.

## 1. The fourth capture defect — found and fixed

`05-qemu-boot.py` preflighted with `make build-hermit`. That depends on `init-hermit` →
`checkout-all`, which runs `git submodule update --init --recursive`, and the Makefile warns in its
own comment that this **"DETACHES attached primaries."** The parent gitlink for `hermit` is
`b4e94ce4`; the primary's HEAD is `f89c69766`. So the documented way to capture a demo05 golden
would have **moved the coordinator-owned primary checkout to a different commit and rebuilt**, then
captured the golden against a binary nobody selected.

Today it does not silently do that — it fails, because the primary is dirty with other agents'
work. That failure is the only reason the mis-capture has not already happened, and it is also why
the prescribed capture could not be run at all until this fix.

**Fix (in `demos/05-qemu-boot.py`):** when `HERMIT_RELEASE` names an existing executable, skip the
build entirely and print the binary's baked-in version instead:

```
Pinned Hermit (skipping build-hermit): …/det4-parity/hermit/target/release/hermit
  [hermit 0.2.0 (2026-08-06, g4c70658e7858)]
```

The version string carries a `-dirty` marker, so it attests source state as well as SHA. Unpinned
callers keep the old build path unchanged. A golden capture must pin its binary, never rebuild it.

## 2. What the correct capture actually buys

Captured through the script (fresh private run dir, `PYTHONDONTWRITEBYTECODE=1`, fixed `cwd=ROOT`,
run-dir path folded to `<run-dir>`, binary pinned), det4's three inputs are genuinely gone. No
divergence in this work was attributable to the `.pyc` cache, the run directory, or the cwd.

**The serial console is rock solid.** Every single run — all ~18 — produced serial SHA-256
`5d24dc06c1cb82f1…`, and no run ever emitted a `WARN: serial output SHA-256 differs`. Whatever is
unstable, it is not the guest's observable boot behaviour.

## 3. What it does not buy — the disqualifying evidence

**qcow2 snapshot SHA-256, the demo's primary determinism witness — 5 distinct values in ~18 runs:**

| value (first 16) | runs | condition |
| --- | ---: | --- |
| `4b32c64fdce29da5` | 5 | pairs with run-1 artifacts present |
| `6e109fd2b7ae4850` | 5 | pairs with run-1 artifacts present |
| `2374d6e8d7b09331` | 5 | fully-reset pre-state |
| `f63d002e15747579` | 1 | fully-reset pre-state |
| `a95dec29cfe4dd8b` | 2 | first pair of the session |

Two things to read carefully here, because I got the first one wrong before checking further:

* Pre-state **matters**: in 4 consecutive pairs, run 1 and run 2 disagreed **4/4**, and run 2 always
  executes with artifacts run 1 published (`boot-anchor/`, `hermit-boot.qcow2`, `run-history/`)
  that run 1 did not have. The demo compares two runs that did **not** face the same directory state.
* Pre-state is **not the whole story**. Four runs from a fully-reset pre-state all gave
  `2374d6e8`, which looked decisive — and then a fifth and sixth under the *same* procedure gave
  `2374d6e8` and `f63d002e`. **Five samples agreeing did not survive a sixth.** Equalising the
  pre-state raises the odds; it does not make the snapshot reproducible.

**Hermit INFO log:** never byte-identical, and the magnitude is itself unstable — **20,601 differing
lines (1.03%)** in one controlled pair, **484,438 (24.26%)** in another.

## 4. The mechanism: virtual-time drift from a hardware branch counter

The syscall sequence is identical; the *clock* is not.

At the first divergence of the 1.03% pair, commits 126 → 127 span a timeslice containing **exactly
one syscall**, whose logged result is identical in both runs — yet committed virtual time differs
by **60 ns**. The syscall contribution is equal, so the delta comes from the RCB-derived component.

Directly visible at `rdtsc` interception:

```
DetTime { syscalls: 2981, syscall_nanos: Some(30170750), rcbs: 34252077, … }   run A
DetTime { syscalls: 2981, syscall_nanos: Some(30170750), rcbs: 34252078, … }   run B
```

Of the 22 `inbound rdtsc` lines in the log, **14 differ, every one with an `rcbs` delta of exactly
−1**. `rcbs` is the PMU retired-conditional-branch count. Everything else is downstream: of 20,601
differing lines, 12,436 are COMMIT times and 8,149 are `clock_gettime` results handed back to the
guest, all carrying the propagated offset. Only **13 discrete offset-change events** occur in
191,640 commits, and the offset returns to zero for ~179,000 of them — the entire Linux boot is
bit-identical in the middle.

**This is not a capture input.** No amount of pinning env, cwd, run dir, or bytecode cache changes
what the hardware branch counter reports.

## 5. Scale is not the discriminator — and this is the actionable part

The prior artifact's open question was the unbracketed gap between ~1,715 records and demo05's
~1.5 M. `dd bs=1` gives one read + one write per block, so `count=` brackets it by construction.
All rungs n=3, full INFO log, only the wallclock prefix stripped:

| rung | records | verdict |
| --- | ---: | --- |
| `python3 -c 'print(1)'` | 2,756 | **IDENTICAL** |
| `dd … count=1000` | 4,849 | **IDENTICAL** |
| `dd … count=10000` | 40,850 | **IDENTICAL** |
| `dd … count=100000` | 400,859 | **IDENTICAL** |
| `dd … count=400000` | 1,600,889 | **IDENTICAL** |
| `dd … count=400000` **with demo05's exact `--target-timeslice 100000 --max-timeslice 2000000000`** | 1,681,726 | **IDENTICAL** |
| demo05 (QEMU boot) | ~1.5 M | **DISQUALIFIED** |

A **1.68 M-record golden reproduces bit-for-bit, three times, under demo05's own timeslice flags** —
larger than demo05 itself. So the instability is neither scale nor the timeslice configuration. It
is specific to the QEMU workload, which is the one that reads `rdtsc` and therefore samples the
branch counter.

**Recommendation for the prefix-depth ladder (#315): use `dd bs=1 count=N` as the heavy rung.** It
is tunable to any record count, it reproduces at demo05's scale, and it needs no QEMU assets. The
ratchet does not have to block on demo05.

## 6. `python-startup` — det4's TOOL-ERROR resolved

Not a golden failure and not a hermit problem: the old runner wrapped every rung in
`/bin/sh -c "$cmd"`, so `/usr/bin/python3 -c print(1)` reached `sh` as unquoted `print(1)` and `sh`
died on the paren. Running the rung as **direct argv, no shell** gives a real verdict:
**IDENTICAL, 2,756 records, n=3.**

## 7. Two relaxations in the comparator that buy nothing

`demo_common.hermit_log_diff` normalizes three things. Measured on the 1.03% pair:

| normalization | differing lines after |
| --- | ---: |
| L1 wallclock prefix only | 20,601 |
| L2 + `FileContents(<host inode>)` | **20,601** |
| L3 + `0x7f…` guest addresses | **20,601** |

**The inode mask and the address mask each remove exactly zero lines.** They are inert here, and the
in-code justification for the address mask — that separate invocations inherit different host
environments and so shift the stack base — cannot apply to this pair at all: both runs were launched
from the same shell with identical environment. Two relaxations of the compare, carrying a rationale
the controlled pair disproves, buying nothing. I have **not** removed them (they may still earn
their keep across genuinely different environments, which I did not test), but they should not be
trusted as evidence that the log "matches after normalization."

## 8. What was NOT done to reach any verdict here

No time blunted, no field dropped, no comparison coarsened. The only thing stripped anywhere is the
real wall-clock prefix. Where a rung would only reproduce by weakening the compare, the output is
DISQUALIFIED — which is exactly what demo05 gets.

## 9. Limitations

* The `rcbs ±1` observation is from 14 lines in one pair. I did not run a PMU-level experiment to
  characterise the counter's variability, or test whether it is load-dependent.
* I did not determine *which* element of run 1's published pre-state (`boot-anchor/`,
  `hermit-boot.qcow2`, `run-history/`) reaches the guest, only that equalising all of them changes
  the outcome distribution. A targeted bisect is the obvious next step.
* Six identical-pre-state runs is a small sample for a bimodal-looking effect; the 5-then-1 split is
  reported as observed, not modelled.
* The unpinned (`make build-hermit`) path of §1's fix was deliberately not exercised, because doing
  so would move the primary checkout.
* Whether the demo's *own* pass/fail policy should treat a differing qcow2 as fatal is a product
  decision I did not make; today it prints `PARTIAL` and exits 1.

## Reproduction

```sh
# controlled pairs through the script (the prescribed capture)
PAIRS=4 ./ignored/w2-demo05/pairs.sh
# INFO-log divergence at four normalization levels
python3 ignored/w2-demo05/analyze.py <infoA> <infoB>
# the rung ladder, including the python fix and the dd bracketing
HERMIT_BIN=ignored/det4-parity/hermit/target/release/hermit RUNS=3 ./ignored/w2-rungs/rung-selfdet.sh
```
