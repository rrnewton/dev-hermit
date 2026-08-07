# Re-measured at current main `723d19ad5d10` — nothing moved, and that is the finding

**Task:** `re-measure-the-detlog-matrix-at-current-main` · hermit-w7 (`[impl agent, opus-5]`) ·
**2026-08-07** · local, no egress beyond `git fetch`.

**Measured at hermit `723d19ad5d10d40705ba10040f5a5e8edd9699b5`**, release, not `-dirty`,
sha256 `878d7a33…`, Reverie pin `038e9939`, third-party staged via `-p hermit-install`.
This **was** `origin/main` at measurement time and **still was** at report time — freshly
fetched, **commits-after-measurement = 0**. Prior arm: `0041130ccb0d`, Reverie pin `0ae0c01b`.

## 0. Headline

**42 of 42 cells UNCHANGED.** Every tier, every `Z`, every `E`, every prefix depth and every
coverage figure is identical across the two SHAs. One cell shows a different *sample* of a
defect that was already known to be there, and it is classified `SAMPLING-ARTIFACT`, not
`CHANGED`.

| verdict | cells |
| --- | --- |
| `UNCHANGED` | 41 |
| `SAMPLING-ARTIFACT` | 1 |
| `CHANGED` | **0** |
| total | 42 |

And the matrix is now **n=30 per cell instead of n=3** — 1260 runs, zero nonzero exits, zero
empty streams — which retires the standing caveat that every cell in the prior arm rested on
three runs.

## 1. What was in flight, recorded before measuring

Nine commits separate the arms. I wrote the expectation down first so a moved cell would be
attributable rather than mysterious:

| commit | class |
| --- | --- |
| `590fcc9ee` Bump the Reverie pin to `6144323c` | **substantive** |
| `4be8edcd2` Bump Reverie pin to `038e9939` | **substantive** |
| `e80832238` per-backend guest-argument channel for e2e manifests | test harness |
| `75506005d` fix two `clippy::nonminimal_bool` errors | lint |
| `723d19ad5` ignore safe-ci-dag-runner's profile store | gitignore |
| `11dfe3a3b`, `4da445156`, `0b2475b23`, `a86113e0c` backend-parity scorecard tooling | parent-facing (hermit-w7's landed PR #1832) |

So the single runtime variable is the **Reverie pin, `0ae0c01b` → `6144323c` → `038e9939`**.
That matters more than "9 commits" suggests: every backend in this matrix — ptrace, kvm, dbi,
sabre and the liteinst runtime — is Reverie code. Four of the nine are my own landed
scorecard tooling and touch nothing the guest executes.

**My recorded prediction was that cells would move if and only if the Reverie bump moved them,
and that DBI was most likely to move because the same commits re-carried the DBI budget.
Nothing moved. The prediction's "if" branch did not fire.** Two Reverie pin advances crossed
this matrix without shifting one DETLOG cell.

**PR #1847 has not landed** — verified by ancestry, `077833ad` is not an ancestor of
`723d19ad` — so the LiteInst maps-inode determinization is still absent from main. As
predicted, the LiteInst DETLOG self-nondeterminism persists.

## 2. The matrix at `723d19ad`, n=30

`Z` = ptrace golden DETLOG record count (the denominator for every row of that guest).
Coverage is order-preserving LCS under the `hex` policy. **The `ptrace` column is a
SELF-REFERENCE row** — the golden compared against itself. It is here to show the denominator
and to prove the comparator is non-vacuous. It is **not a parity achievement** and must never
be quoted as one.

| guest | Z | ptrace *(self-ref)* | kvm | dbi | sabre | e9patch | liteinst |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `notsc` | 96 | byte-identical | diverges 87.5 % | **self-nondet** | diverges 8.3 % | **not-exercised** | diverges 49.0 % |
| `detlog_syscalls` | 336 | byte-identical | diverges 86.9 % | **self-nondet** | diverges 32.7 % | **not-exercised** | **self-nondet** |
| `heap_fragment_reuse` | 98 | byte-identical | diverges 87.8 % | **self-nondet** | diverges 19.4 % | **not-exercised** | diverges 52.0 % |
| `stack_deep_recursion` | 80 | byte-identical | diverges 85.0 % | **self-nondet** | diverges 10.0 % | **not-exercised** | diverges 48.8 % |
| `stdout_bytes` | 82 | byte-identical | diverges 85.4 % | **self-nondet** | diverges 11.0 % | **not-exercised** | diverges 48.8 % |
| `bin_true` | 68 | byte-identical | diverges 80.9 % | **self-nondet** | diverges 7.4 % | **not-exercised** | diverges 52.9 % |
| `bin_echo` | 84 | byte-identical | diverges 82.1 % | **self-nondet** | diverges 14.3 % | **not-exercised** | diverges 52.4 % |

Tier tally, summing to the population: `byte-identical` 7 (all self-reference) + `diverges` 20
+ `self-nondeterministic` 8 + `not-exercised` 7 = **42 = 7 × 6**. All 23 per-cell fields are in
`detlog-matrix-723-n30.csv`; every one of the 1260 attempts with its exit code and record count
is in `attempts.tsv`.

e9patch is still `not-exercised` on every cell — same banner, unchanged by the Reverie bump:
`candidate_sites=0; mapped_sites=0; b0_sites=0; artifact_sha256=none`. The other three
engagement witnesses are also unchanged (`engagement.tsv`).

## 3. n=30 sharpens two cells that n=3 could only blur

Self-determinism is now reported as **distinct outcome classes over n runs**, because a
differing-*pair* count cannot tell "one minority class" from "every run is unique", and those
are different defects:

| cell | n=3 (prior arm) | n=30 (here) | what n=30 adds |
| --- | --- | --- | --- |
| `*` × `dbi` (all 7) | 3 classes / 3 | **30 classes / 30** | not "unstable" — **no run ever repeats**. Every single execution produces a unique DETLOG stream. |
| `detlog_syscalls` × `liteinst` | 1 class / 3 → false pass | **2 classes / 30 (21 \| 9)** | the minority class is 9/30, so a 3-run sample misses it about 34 % of the time |
| the other 34 cells | 1 class / 3 | **1 class / 30** | self-determinism no longer merely *un-refuted*; it survived 435 pairwise comparisons each |

The standing caveat from the prior arm — "every cell rests on n=3, and the only cell measured
deeper is the one that failed" — is now discharged for all 42 cells.

## 4. The one non-`UNCHANGED` cell is a sampling artifact, not a change

`detlog_syscalls` × `liteinst` reads:

| arm | n | distinct classes |
| --- | --- | --- |
| `0041130c` | 3 | 1 — *a false pass* |
| `723d19ad` | 3 | 3 |
| `723d19ad` | **30** | **2 (21 \| 9)** |
| `0041130c` | **30** | **2 (17 \| 13)** *(from the companion artifact)* |

At matched n=30 **both arms show the same defect**: 2 outcome classes, differing only in the
32 `clock_gettime(CLOCK_MONOTONIC)` records at a constant 6720 ns = 672 RCB offset. The
apparent "flip" is entirely the 3-run sample landing differently. Reporting it as a change
would have manufactured a finding out of sampling noise; it is labelled `SAMPLING-ARTIFACT` in
`cell-diff-0041130c-vs-723d19ad.csv`.

**This is also why the carried override had to be made conditional.** The scorer previously
applied a hardcoded n=30 result from the prior arm. Left unconditional, it would have stamped
the old arm's `17|13` onto this arm's cell and hidden any real movement — in the one task whose
entire purpose is detecting movement. It now applies only when the collected run count is below
30, so the live deeper measurement always wins.

## 5. Planted divergence, re-run at this SHA

Same control as the prior arm: real guest mutation (`notsc_mut.c`, 9 `getpid()` instead of 8),
against an unmutated-rerun negative control.

| backend | Z | control rerun | planted | verdict |
| --- | --- | --- | --- | --- |
| ptrace | 96 | Y=96/96, cover 96/96 | Y=6/96, cover 70/96 | **DETECTED**, no false positive |
| kvm | 96 | Y=96/96, cover 96/96 | Y=6/96, cover 70/96 | **DETECTED**, no false positive |
| sabre | 79 | Y=79/79, cover 79/79 | Y=20/79, cover 29/79 | **DETECTED**, no false positive |
| liteinst | 832 | Y=832/832, cover 832/832 | Y=6/832, cover 581/832 | **DETECTED**, no false positive |
| e9patch | 96 | Y=96/96, cover 96/96 | Y=6/96, cover 70/96 | DETECTED — but this is the ptrace runtime, not e9patch |
| dbi | 94 | **Y=3/94, cover 4/94** | Y=3/94, cover 4/94 | **NOT-ATTRIBUTABLE** |

Byte-for-byte the same numbers as at `0041130c`. DBI stays the honest row: its control already
fails, so the plant cannot be attributed to the mutation.

## 6. A cross-artifact discrepancy, reconciled

The prior matrix reports `detlog_syscalls` × `liteinst` at **E = 1074**; the companion
deep-dive artifact reports **1072** — same commit, same guest. Not an error in either. The two
were driven from hermit binaries at different filesystem paths, and the LiteInst
`LD_PRELOAD` absolute path sits in the guest environment block:

```
- openat(-100, HEX -> ".../ignored/w7-1847/base/libreverie_liteinst.so", …)     path len 55 -> 1072 records
+ openat(-100, HEX -> ".../worktrees/w7/hermit/target/release/libreverie_liteinst.so", …)  len 70 -> 1074
- read(3, HEX, 2048) = Ok(1479)   /  read(3, HEX, 569)        <- the 2 extra records
```

The longer path lengthens the proc text, which changes the read chunking, which adds two
`read` records. So install-path dependence moves the **denominator**, not merely the values —
a sharper statement than the companion artifact makes. Both arms here were driven from one
fixed path, so the comparison above is controlled.

## 7. Non-inferences, unchanged

- **Nothing here transfers to stack or heap.** No `--detlog-stack`/`--detlog-heap`.
- **The `ptrace` column is a self-reference**, not a parity result.
- **`not-exercised` ≠ green.** e9patch's 7 cells are byte-identical to ptrace because they *are*
  ptrace; it rewrote nothing.
- **A `diverges` percentage is not a grade.** SaBRe's 7.4 % and LiteInst's 52.9 % on `bin_true`
  are different failure shapes — SaBRe emits *fewer* records than the golden (a coverage gap),
  LiteInst emits far more (runtime bring-up inside the guest).

## 8. Reproduction

```bash
../harness/matrix_collect.sh <hermit> <outdir> 30     # 1260 runs, ~4 min
python3 matrix_score.py <outdir> matrix.csv
```

Build with `--features third-party-backends -p detcore-dbi -p detcore-sabre -p hermit-install`,
or dbi/sabre/e9patch refuse with "backend is unavailable" and become `no-result` rows. Drive
every arm from **one fixed binary path** (§6).

## 9. Follow-ups

1. **DBI never repeats a DETLOG stream** — 30 unique classes in 30 runs, on all 7 guests. Its
   whole column is unscoreable until that is fixed, and "30/30 unique" is a much stronger
   statement than the earlier "3/3 pairs differ" to hand whoever owns it.
2. **Two Reverie pin advances moved zero DETLOG cells.** Useful as a negative result: DETLOG
   parity is currently insensitive to Reverie pin motion, so it will not catch a regression
   introduced there.
3. **e9patch still needs a corpus that exercises it** — unchanged, `candidate_sites=0` on all 7.
4. **KVM's residual is still ~4 records per simple guest** after discounting decimal-printed
   addresses; unmoved by the Reverie bump, so it is not a pin-side artifact.
