# The ptrace golden's self-determinism, per rung — and a disqualification I am withdrawing

**Task:** `fix-the-ptrace-golden-self-determinism-at-the-failing-rungs` · hermit-det4
(`[impl agent, opus-5]`) · **2026-08-06** · local, no egress.
**Anchor:** hermit `4c70658e785834737cbe1524f77330c781a6f5ea`, reverie pin `dd3c178`, binary
`hermit 0.2.0 (2026-08-06, g4c70658e7858)` release, ptrace, `--strict`, relaxations none.

## 0. The premise this task was filed under is wrong, and so was part of mine

The task states the golden "is not self-deterministic **at every rung**". It is not. My own
measurement, which the task cites, found the opposite: **9 of 10 rungs reproduce, one was
inconclusive.** And the one I originally reported as a hard FAIL — demo05 — I am **withdrawing**,
because every divergence I have found there so far turned out to be a variable in *my harness*,
not in the reference.

## 1. The disqualification list — the deliverable

| rung | runs | verdict | Z (records) |
| --- | --- | --- | --- |
| `/bin/true` | 3 | **QUALIFIED — identical** | 538 |
| `/bin/echo hi` | 3 | **QUALIFIED — identical** | 729 |
| `/bin/cat /etc/hostname` | 3 | **QUALIFIED — identical** | 746 |
| `/bin/wc -c /etc/hostname` | 3 | **QUALIFIED — identical** | 736 |
| `sh -c 'echo a \| wc -c'` | 3 | **QUALIFIED — identical** | 1301 |
| `sh -c` 200-iteration loop | 3 | **QUALIFIED — identical** | 934 |
| `ls -R /usr/include \| wc -l` | 3 | **QUALIFIED — identical** | 1715 |
| `ls -la /usr/lib64 \| wc -l` | 3 | **QUALIFIED — identical** | 1715 |
| `cat /usr/include/linux/kvm.h` | 3 | **QUALIFIED — identical** | 746 |
| `python3 -c print(1)` | 3 | **TOOL-ERROR** — my `sh -c` ate the parens; not a golden verdict | — |
| **demo05 (QEMU boot)** | 2+2+2 | **UNRESOLVED — see §3. Not qualified, and NOT disqualified.** | ~1.5 M |

**Nothing is currently disqualified.** That is a different statement from "everything is fine":
demo05 is unproven in *both* directions, and unproven is not the same as failing.

Counts here are larger than in the previous artifact because these rungs are wrapped in
`/bin/sh -c`, which adds the shell's own trace. Each rung is compared only against itself.

## 2. Method, and what was *not* done to get these greens

Three runs per rung, full INFO log (`DETLOG` + scheduler `COMMIT turn`), all pairs compared.
**The only thing removed is the real wall-clock prefix** — the one field with no deterministic
content. Virtual time, RCB counts, syscall arguments, results, sizes and flags are compared
verbatim. No field was dropped, no time was blunted, no comparison was coarsened to produce a
green. Had a rung needed that, the correct output was DISQUALIFIED, and it would have said so.

Verdicts are three-valued and stay that way: **IDENTICAL / FAIL / TOOL-ERROR**. A run that
crashes, times out, or emits no records is TOOL-ERROR and is never reported as FAIL. That
separation is not decoration — it is what caught a false "the golden fails at every rung" earlier
today, and it caught `python-startup` here.

## 3. demo05: three "golden failures", three harness bugs

Each time I fixed a variable, the divergence moved — which is the signature of a measurement
artifact, not of a nondeterministic reference.

**Attempt 1 — self-depth 4507 / 1 431 103, record counts differing (1 431 103 vs 1 429 295).**
Root cause: the guest reads
`demos/lib/__pycache__/demo_common.cpython-39.pyc`, and the read length is sized from `st_size`:

```
openat(… "…/demos/lib/__pycache__/demo_common.cpython-39.pyc") = Ok(3)
fstat(3) → lseek(3,0,SEEK_CUR) ×2 → fstat(3)
read(3, 0x5555556ce8a0, 24509)   ← run A
read(3, 0x55555564a050, 25896)   ← run B
```

The `.pyc` is a **shared, concurrently-written cache**. `demos/05-qemu-boot.py:168-173` already
knows this and sets `PYTHONDONTWRITEBYTECODE=1` with a comment saying exactly why. **My harness
invoked `qemu_controller.py` directly and therefore never set it.** The reference was not at
fault; my invocation was.

**Attempt 2 — with `PYTHONDONTWRITEBYTECODE=1`: record counts became EXACTLY equal
(1 497 180 = 1 497 180)**, and the divergence moved *earlier*, to 1489, on an `execve`. Cause: the
two runs used different working directories (`…/det4-d5-qA/` vs `…/det4-d5-qB/`), and those paths
appear inside the guest's `execve` argv. My harness again — the arms were not given identical
arguments.

**Attempt 3 — identical cwd, identical argv, `PYTHONDONTWRITEBYTECODE=1`:** run 1 produced
1 498 854 records; run 2 produced 85 977 with a **0-byte serial log**, i.e. it never booted.
That is **TOOL-ERROR**, not a golden FAIL, and it is reported as such. Most likely the second
iteration collided with state the first left in the shared run directory.

**Net: demo05's golden is UNPROVEN.** The published claim "demo05's golden self-diverges at
4507/1 431 103" is **retracted** — the number was real, its attribution to the reference was not.

## 4. What this means for the ratchet

* The prefix-depth numbers at the low rungs **stand**. They were measured against goldens that
  reproduce, and this run re-confirms those goldens at n=3 rather than n=2.
* demo05 is **not** established as a blocked rung. Anyone planning work on the premise that the
  reference is broken at demo05 should wait for a controlled re-run.
* The real lesson is narrower and more useful than "the golden is nondeterministic": **the demo05
  harness has at least two guest-visible inputs that must be pinned — the Python bytecode cache
  and the run directory (which reaches the guest through argv).** Both are properties of how the
  demo is invoked, and both are already handled correctly by `05-qemu-boot.py`. A golden for
  demo05 must be captured *through that script*, not by calling the controller directly.

## 5. What remains

* A controlled demo05 pair, run through `05-qemu-boot.py` itself rather than a hand-built
  invocation, with a fresh run directory per iteration. That is the outstanding measurement.
* The boundary between "reproduces" (≤1715 records here) and demo05 (~1.5 M) is still unbracketed:
  nothing between those two scales was tested, because the intermediate rungs I added all landed
  in the same low band.
* `python3 -c 'print(1)'` needs re-running with correct quoting; it is a genuinely interesting
  rung (heavy loader, many mmaps) and is currently unmeasured, not passing.

## Reproduction

```
HERMIT_BIN=ignored/det4-parity/hermit/target/release/hermit RUNS=3 ./ignored/det4-golden-selfdet.sh
```
Results: `ignored/det4-golden-selfdet.tsv`. demo05 attempts:
`ignored/det4-d5-selfdet.sh`, `ignored/det4-d5-controlled.sh`.
