# COMMIT-turn prefix depth across nine guests: Y=2 everywhere

**Bottom line.** `reverie#402` has **not landed**, so "confirm the depth holds at the
landed SHA" has no landed SHA to confirm at. What *is* answerable — and was the
half the dispatch emphasised — is whether a depth measured on one guest
generalises. The scheduled population was **9 guest × KVM-backend pairs**:
**8/9 were qualifying measurements**, and all eight had KVM `Y=2`, while `Z`
ranges 5..19. The ninth pair (pipeline) exited `rc=1`; it is a setup failure and
therefore zero qualifying trials, not a negative depth. Within the measured
sample the result is 8/8; coverage against the declared population is 8/9.

And a warning that follows directly from that: **`#402`'s "record 20 → >139" is
not in this metric.** In the COMMIT-turn metric no guest gets past record 2, so
after `#402` lands, anyone re-running the named prefix-parity harness will see
*no movement* and could reasonably conclude the fix did nothing.

## State of the dependency, verified not assumed

```
reverie#402   state=OPEN  mergedAt=null  mergeCommit=null  head=867fce5b…
reverie main  038e993926e45514264d30367b70df9b6ac3b9b8
hermit main   4be8edcd…, and every tracked Cargo manifest pins reverie 038e993 == reverie main
```

So the pin measured here is **definitively pre-`#402`**, and these numbers are
the per-guest **before** baseline — which did not previously exist.

## Two metrics, deliberately not conflated

| | (a) COMMIT-turn depth | (b) completed-syscall depth |
|---|---|---|
| where | `ci-hub/parity/prefix_depth.sh` | `reverie#402` evidence comment |
| counts | `COMMIT turn` records | completed syscalls |
| recorded values | KVM 2/5, 2/6, 2/7 | "record 20 → >139", "through syscall #142" |

Record 20 and record 2 are records of **different kinds**. This experiment
measures (a) only, reports raw `Y/Z`, and re-defines nothing — the metric's
definition is under owner review.

## A harness defect that blocks the stated goal

`ci-hub/parity/prefix_depth.sh:126` hardcodes:

```bash
for be in dbi sabre e9patch; do
```

**KVM is not in the loop.** The named "prefix-parity harness" structurally cannot
measure the backend pair `#402` fixes, which is why the recorded KVM rung numbers
were produced ad hoc — that experiment directory contains no runner. `sweep.sh`
here copies the harness's normalization **verbatim** (same `grep -o 'COMMIT turn
.*'`, same `0x…`→`HEX`, same longest-common-prefix awk) and changes only the
backend list, so its numbers are comparable rather than a parallel definition.

## Method validation before any new number was believed

The three recorded rungs reproduce **exactly**:

| guest | recorded | re-measured |
|---|---|---|
| `true` | ptrace 5/5, kvm 2 | ptrace 5/5, kvm 2 |
| `echo` | ptrace 6/6, kvm 2 | ptrace 6/6, kvm 2 |
| `wcsimple` | ptrace 7/7, kvm 2 | ptrace 7/7, kvm 2 |

Hermit was held **constant** at the recorded baseline's clean `g590fcc9eeb03`
(sha256 `14813a81140c98b9…`) so that *guest* is the only variable. Building a
fresh binary would have introduced a second one; the prior turn on this metric
was reversed precisely by a build confound, where a stale dirty binary had KVM
hanging at `rc=124` and produced a false refutation.

## Results

`Z` = golden ptrace COMMIT count. `Y` = identical leading run. Backend pair
ptrace→KVM, `--base-env minimal`, no relaxations.

**Coverage: 8/9 named guest × KVM-backend pairs measured.** A depth figure is
published only for the eight qualifying pairs. If regeneration yields zero
qualifying pairs, the result is `0/N measured — UNMEASURED`, never the previous
depth carried forward as unchanged.

| guest | Z | KVM Y | KVM emitted | rc | |
|---|---:|---:|---:|---:|---|
| `true` | 5 | **2** | 5 | 0 | recorded rung |
| `echo` | 6 | **2** | 6 | 0 | recorded rung |
| `wcsimple` | 7 | **2** | 7 | 0 | recorded rung |
| `threaded` (pthread_lifecycle, 4 threads) | 19 | **2** | 19 | 0 | **new** |
| `heap_fragment_reuse` | 6 | **2** | 6 | 0 | **new**, pinned heap guest |
| `stack_deep_recursion` | 6 | **2** | 6 | 0 | **new**, pinned stack guest |
| `stdout_bytes` | 7 | **2** | 7 | 0 | **new**, pinned stdout guest |
| `detlog_syscalls` | 6 | **2** | 6 | 0 | **new**, pinned detlog guest |
| `pipeline` (`sh -c 'echo a \| wc -c'`) | 27 | — | **52507** | **1** | **not a clean measurement** |

**Which guests improved: none. Which did not: all of them.** There is nothing to
improve *from* yet — this is the before column.

The five new guests span all four pinned parity dimensions (stdout, detlog, heap,
stack) plus threading, and every one lands on the same `Y=2`.

### The pipeline rung is a failure, not a depth

KVM exits `rc=1` and emits **52507** comparable records against a golden 27 — a
factor of ~1945. That is a distinct pathology and `Y` is not meaningful for it,
so the cell is left blank rather than filled with a `2` that would quietly join
the pattern. The recorded baseline also never had this rung clean (its LiteInst
run exited `rc=1`), so it has now failed on both non-ptrace backends measured.

### Controls — the metric is not inert

| control | Y | expected |
|---|---:|---|
| golden vs itself | 5 | 5 (= Z) |
| perturb record 4 | 4 | 4 |
| perturb record 3 | 3 | 3 |
| perturb record 1 | 1 | 1 |
| truncate to 3 | 3 | 3 |

A deliberately perturbed log **lowers** the depth by exactly the perturbation
offset, in all five cases. So `Y=2` is a measurement, not a constant the harness
would emit regardless of input.

## Interpretation

Two readings, and the evidence distinguishes them:

1. *Depth is guest-dependent and one guest cannot speak for the set* — the
   premise the dispatch carried over from the 6/6-vs-1/38 heap result. **For this
   metric, refuted.** `Y=2` on 8 of 8, across guests that differ in threading,
   heap churn, stack depth, stdout volume and syscall count.
2. *Depth is pinned by a single prologue-level divergence common to every guest.*
   **Consistent with everything measured**, and with the recorded diagnosis that
   KVM's record-2 divergence is virtual-time accounting with byte-identical
   resources on every record.

That the lesson did not transfer is itself the finding: it had to be tested to be
known, and testing it cost nine guests. `Y/Z` per guest remains the right report
shape — `Y` alone is meaningless when `Z` ranges 5..27.

**The consequence for `#402`.** Because every guest is pinned at the prologue in
metric (a), a fix that moves metric (b) from record 20 to >139 will show **zero
movement here**. Whoever re-measures after landing must use the metric the claim
was made in, or they will report a regression that is a metric mismatch. This is
the concrete reason the two ladders must not be quoted interchangeably.

## Still owed, once `#402` lands

Re-run `sweep.sh` at the landed pin and diff against `results.csv`. The honest
phrasing to preserve is **"no divergence observed within records 0..139"**, never
a claim of infinity.

## Reproduction

```bash
BACKENDS=kvm ./sweep.sh \
  "true=/bin/true" "echo=/bin/echo hello" "wcsimple=/usr/bin/wc -c /etc/hostname" \
  "threaded=<dir>/pthread_lifecycle" "heap_fragment_reuse=<dir>/heap_fragment_reuse" \
  "stack_deep_recursion=<dir>/stack_deep_recursion" "stdout_bytes=<dir>/stdout_bytes" \
  "detlog_syscalls=<dir>/detlog_syscalls" "pipeline=<dir>/pipeline.sh"
```

Reference guests build from `ci-hub/parity/guests/*.c`. Guests must **not** live
under `/tmp`: Hermit replaces guest `/tmp` and refuses with an explicit error.
The pipeline rung needs a script file — passing `sh -c 'echo a | wc -c'` through
the runner's unquoted `$cmd` word-splits and silently measures something else.

## Limits

- One backend pair (ptrace→KVM). DBI/SaBRe/e9patch not measured here; the stock
  harness covers those three and not this one.
- `n=1` per guest/backend. Enough to establish `Y=2`, since the controls show the
  metric responds to input, but not a stability claim across repeats.
- Metric (a) only. Nothing here measures or contradicts `#402`'s metric-(b)
  result; it is simply a different ladder.
- Hermit held at `g590fcc9eeb03`, which is behind hermit main `4be8edcd`. Chosen
  for comparability with the recorded rungs; a re-measure at current main is a
  separate, single-variable question.
