# Prefix-parity depth round 3: latest measured Y/Z by pair

**Task:** `prefix-parity-depth-round3-report-current-YZ-per-pair`

**Consolidated:** 2026-08-07T05:40:32Z

## Outcome

Two measurement rounds produced **10/24 measured cells** in a fixed matrix of
4 guests x 6 backend labels. Another **8/24 cells** reached a backend setup
refusal but produced no qualifying candidate trace, and **6/24 cells** were not
run. Setup refusals are unmeasured; they are never reported as `0/Z`.

These are the **latest measured values**, not a fresh-current-main claim.
Round 2 measured Hermit
`75506005d873a76f62be00b1d82696188651047a`; live Hermit main at consolidation
was `590fcc9eeb0339c5cf23f72b84394a63333e88ff`. The measured-to-live interval
contains **5/5 later commits**, including product-code and Reverie-pin changes,
so a future round must remeasure rather than inherit these values.

## Fixed definition

For each guest, `Z` is the number of ordered INFO-log lines beginning at
`COMMIT turn` in the ptrace golden. `Y` is the longest raw byte-identical prefix
of the candidate and golden COMMIT streams after removing only the logging
prefix by extracting from `COMMIT turn` onward. Paths, values, addresses, and
virtual time are not normalized.

This is a COMMIT-only prefix metric. It is not the older full-record
`DETLOG + COMMIT` metric and is not an L1/L2 strict-verification result.

## Measurement scope

| Round | Guests | Hermit | Trial shape | Host |
| --- | --- | --- | --- | --- |
| 1 | `/bin/true`, `/bin/echo hi` | `f89c69766371806d3c9b2c3003531df2d59d6118` | one captured ptrace golden per guest, compared to itself; one LiteInst candidate per guest; one DBI setup attempt per guest | not recorded in the predecessor evidence |
| 2 | `wc -c /etc/hostname`, fork/exec pipeline | `75506005d873a76f62be00b1d82696188651047a` | ptrace `2/2` qualifying captures per guest; each available candidate `1/1` qualifying trace per guest | `devbig014` class, AMD EPYC 9D85, kernel `6.18.39-0_fbk0_hardened_0_ga43d5727b443` |

Round 1 does not establish ptrace self-determinism: its `Z/Z` rows are
self-comparisons of one captured golden, not independent replicates. Round 2
does establish ptrace repeatability for its two guests with `2/2` matching
captures each.

## Every measured guest x backend pair

`Qualifying/attempted` counts candidate traces usable for the prefix metric. A
candidate can qualify for the metric and still fail guest execution after
emitting its trace, as the two round-2 pipeline candidates did.

| Round | Guest | Backend | Qualifying/attempted | Y/Z | Candidate records | Execution | First divergent DetCore commit |
| ---: | --- | --- | ---: | ---: | ---: | --- | --- |
| 1 | `/bin/true` | ptrace | `1/1` captured golden | **5/5** | 5 | self-reference; independent repeat not recorded | none; full `5/5` prefix |
| 1 | `/bin/true` | LiteInst | `1/1` | **2/5** | 31 | completion status not recorded | zero-based record 2, `COMMIT turn 2`: injected LiteInst DSO path replaces `/etc/ld.so.cache` |
| 1 | `/bin/echo hi` | ptrace | `1/1` captured golden | **6/6** | 6 | self-reference; independent repeat not recorded | none; full `6/6` prefix |
| 1 | `/bin/echo hi` | LiteInst | `1/1` | **2/6** | 32 | completion status not recorded | zero-based record 2, `COMMIT turn 2`: injected LiteInst DSO path replaces `/etc/ld.so.cache` |
| 2 | `wc -c /etc/hostname` | ptrace | `2/2` | **7/7** | 7 per capture | rc 0; both stdout values match | none; full `7/7` prefix |
| 2 | `wc -c /etc/hostname` | LiteInst | `1/1` | **2/7** | 33 | rc 0; stdout matches | zero-based record 2, `COMMIT turn 2`: injected LiteInst DSO path replaces `/etc/ld.so.cache` |
| 2 | `wc -c /etc/hostname` | KVM | `1/1` | **2/7** | 7 | rc 0; stdout matches | zero-based record 2, `COMMIT turn 2`: same path, different virtual time |
| 2 | fork/exec pipeline | ptrace | `2/2` | **30/30** | 30 per capture | rc 0; both stdout values are `2` | none; full `30/30` prefix |
| 2 | fork/exec pipeline | LiteInst | `1/1` | **2/30** | 37 | rc 1; empty stdout; cleanup `ENOTSUPP` | zero-based record 2, `COMMIT turn 2`: injected LiteInst DSO path replaces `/etc/ld.so.cache` |
| 2 | fork/exec pipeline | KVM | `1/1` | **2/30** | 5,836 | rc 1; stdout `0`, expected `2`; `wc` saw `EAGAIN` | zero-based record 2, `COMMIT turn 2`: same path, different virtual time |

No measured candidate has `Y=0`. The explicit `0/Z` data points below are
deliberate metric mutations with known denominators, not setup failures.

## Cells without a qualifying parity trial

Every row retains the known ptrace denominator. A blank numerator means no
candidate stream existed to compare.

| Round | Guest | Backend | Known Z | Qualifying/attempted | Typed result |
| ---: | --- | --- | ---: | ---: | --- |
| 1 | `/bin/true` | DBI | 5 | **0/1** | `UNMEASURED`: support not included in the build |
| 1 | `/bin/true` | SaBRe | 5 | no invocation | `NOT RUN`: backend not offered by the binary |
| 1 | `/bin/true` | KVM | 5 | no invocation | `NOT RUN`: backend not offered by the binary |
| 1 | `/bin/true` | e9patch | 5 | no invocation | `NOT RUN`: backend not offered by the binary |
| 1 | `/bin/echo hi` | DBI | 6 | **0/1** | `UNMEASURED`: support not included in the build |
| 1 | `/bin/echo hi` | SaBRe | 6 | no invocation | `NOT RUN`: backend not offered by the binary |
| 1 | `/bin/echo hi` | KVM | 6 | no invocation | `NOT RUN`: backend not offered by the binary |
| 1 | `/bin/echo hi` | e9patch | 6 | no invocation | `NOT RUN`: backend not offered by the binary |
| 2 | `wc -c /etc/hostname` | DBI | 7 | **0/1** | `UNMEASURED`: support not included in the build |
| 2 | `wc -c /etc/hostname` | SaBRe | 7 | **0/1** | `UNMEASURED`: support not included in the build |
| 2 | `wc -c /etc/hostname` | e9patch | 7 | **0/1** | `UNMEASURED`: support not included in the build |
| 2 | fork/exec pipeline | DBI | 30 | **0/1** | `UNMEASURED`: support not included in the build |
| 2 | fork/exec pipeline | SaBRe | 30 | **0/1** | `UNMEASURED`: support not included in the build |
| 2 | fork/exec pipeline | e9patch | 30 | **0/1** | `UNMEASURED`: support not included in the build |

The accounting is therefore **10/24 measured**, **8/24 setup-refused**, and
**6/24 not run**. The three classes sum to **24/24 cells**.

## First divergence excerpts

All four LiteInst candidates first differ at zero-based record index 2. Round 2
captured the representative path difference exactly:

```text
ptrace:   COMMIT turn 2, dettid 3 using resources {Path("/etc/ld.so.cache"): R}, ...
LiteInst: COMMIT turn 2, dettid 3 using resources {Path(".../libreverie_liteinst.so"): R}, ...
```

Both KVM candidates also first differ at index 2, but retain the same path and
change virtual time:

```text
coreutil ptrace: ... /etc/ld.so.cache ... 1_767_225_600.001_334_260s
coreutil KVM:    ... /etc/ld.so.cache ... 1_767_225_600.001_298_250s
pipeline ptrace: ... /etc/ld.so.cache ... 1_767_225_600.001_333_910s
pipeline KVM:    ... /etc/ld.so.cache ... 1_767_225_600.001_298_250s
```

The four ptrace self-reference pairs have no divergent commit. The 14
unmeasured/not-run cells have no first divergence because no qualifying
candidate comparison exists.

## Both-direction metric controls

The predecessor rounds bracketed the metric in both directions:

| Round/control guest | Positive self-control | Measured baseline | Perturb record 1 | Perturb record 0 |
| --- | ---: | ---: | ---: | ---: |
| round 1, `/bin/true` | **5/5** | **2/5** | **1/5** | **0/5** |
| round 2, coreutil | **7/7** | **2/7** | **1/7** | **0/7** |

The positive controls prove full depth is reachable. The planted changes lower
the numerator, including a real `0/5` and `0/7` with known denominators. No
setup refusal participates in this negative-control accounting.

## Interpretation and limitations

- All **6/6 measured non-ptrace candidate pairs** stop at `Y=2`, independent of
  guest weight. The current metric is pinned in backend startup: LiteInst first
  exposes its injected DSO path; KVM first changes deterministic virtual time.
- The fork/exec pipeline adds an execution result: both available candidate
  backends fail `1/1` executions even though their COMMIT prefix is measurable.
- Round 1 lacks a recorded host identity and an independent ptrace replicate.
- Each non-ptrace measured cell has only `1/1` candidate trace. This is parity
  against a golden, not backend double-run determinism.
- Raw logs were ephemeral and are not committed. Round 2 preserves its full
  commands, hashes, outputs, and typed CSV in
  `experiments/prefix-parity-depth-round2_20260807/`.
- Because live Hermit main advanced after round 2, none of these rows may be
  promoted to a current-main score without a new measurement round.

## Source records

- TaskGraph task `prefix-parity-depth-harness-trivial-guests`, note dated
  2026-08-07T05:03Z.
- `experiments/prefix-parity-depth-round2_20260807/` at parent content commit
  `454409ac8e53b298666c5a72d14b45571b36fb88`.
- `results.csv` in this directory is the complete typed 24-cell consolidation;
  `controls.csv` records the two mutation brackets.
