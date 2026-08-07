# Prefix-parity depth round 2: coreutil and fork/exec

**Task:** `prefix-parity-depth-round2-coreutils-and-fork-exec`

**Measured:** 2026-08-07 UTC on Hermit `75506005d873a76f62be00b1d82696188651047a`

**Reverie pin:** `0ae0c01b5e4c9fbf85c97adc66c2740f280727df`

## Question

After the `/bin/true` and `/bin/echo` rung, how many leading DetCore `COMMIT`
records remain identical to the ptrace golden for a small coreutil and a
fork/exec pipeline on every backend available in the exact-head build?

## Method

The exact current `rrnewton/hermit:main` source was built with `cargo build
--locked -p hermit --bin hermit`.  The matching constructor-enabled LiteInst
DSO was built with `scripts/stage-liteinst-runtime.sh`.  SHA-256 identities are
in `metadata.json`.

Each cell used this shape, with no strict/verify assurance claim:

```text
timeout 240 hermit --log=info --log-file=PATH --backend BACKEND \
  run --base-env=minimal --tmp=/tmp -- GUEST ARGS...
```

The two guests were:

```text
/bin/wc -c /etc/hostname
/bin/sh -c '/bin/echo a | /bin/wc -c'
```

`Z` is the number of ordered lines in the ptrace golden beginning at `COMMIT
turn`. `Y` is the longest raw identical prefix after removing only the logging
prefix by extracting from `COMMIT turn` onward. Values, paths, addresses and
virtual time were not normalized. Ptrace ran twice per guest; both COMMIT
streams and stdout matched before candidates were scored.

## Results

| guest | backend | Y/Z | emitted | rc | execution result |
| --- | --- | ---: | ---: | ---: | --- |
| `wc -c /etc/hostname` | ptrace | 7/7 | 7 | 0 | golden; replicate identical |
| `wc -c /etc/hostname` | LiteInst | 2/7 | 33 | 0 | stdout matched |
| `wc -c /etc/hostname` | KVM | 2/7 | 7 | 0 | stdout matched |
| fork/exec pipeline | ptrace | 30/30 | 30 | 0 | golden; replicate identical |
| fork/exec pipeline | LiteInst | 2/30 | 37 | 1 | empty stdout; cleanup `ENOTSUPP` |
| fork/exec pipeline | KVM | 2/30 | 5,836 | 1 | stdout `0` not `2`; `wc` got `EAGAIN` |

DBI, SaBRe and e9patch preprocessing were unavailable in this build. They are
recorded in `results.csv` with each guest's `Z`; none is misreported as `0/Z`.

## First divergent DetCore commit

All four measured candidate pairs first diverged at zero-based record index 2,
so every candidate numerator is `Y=2`.

Coreutil, ptrace versus LiteInst:

```text
ptrace:   COMMIT turn 2, dettid 3 using resources {Path("/etc/ld.so.cache"): R}, on previously committed 1_767_225_600.001_334_260s
LiteInst: COMMIT turn 2, dettid 3 using resources {Path("/tmp/prefix-round2.3d0Rd4/target/debug/libreverie_liteinst.so"): R}, on previously committed 1_767_225_600.001_335_090s
```

Coreutil, ptrace versus KVM:

```text
ptrace: COMMIT turn 2, dettid 3 using resources {Path("/etc/ld.so.cache"): R}, on previously committed 1_767_225_600.001_334_260s
KVM:    COMMIT turn 2, dettid 3 using resources {Path("/etc/ld.so.cache"): R}, on previously committed 1_767_225_600.001_298_250s
```

Pipeline, ptrace versus LiteInst:

```text
ptrace:   COMMIT turn 2, dettid 3 using resources {Path("/etc/ld.so.cache"): R}, on previously committed 1_767_225_600.001_333_910s
LiteInst: COMMIT turn 2, dettid 3 using resources {Path("/tmp/prefix-round2.3d0Rd4/target/debug/libreverie_liteinst.so"): R}, on previously committed 1_767_225_600.001_335_110s
```

Pipeline, ptrace versus KVM:

```text
ptrace: COMMIT turn 2, dettid 3 using resources {Path("/etc/ld.so.cache"): R}, on previously committed 1_767_225_600.001_333_910s
KVM:    COMMIT turn 2, dettid 3 using resources {Path("/etc/ld.so.cache"): R}, on previously committed 1_767_225_600.001_298_250s
```

LiteInst therefore first differs because its injected DSO becomes the committed
path resource. KVM first differs only in virtual time at the same path resource.
The heavier pipeline does not deepen the prefix and independently exposes real
backend execution failures.

## Metric bracket

The coreutil cell supplies both directions:

```text
positive ptrace self-control       7/7
measured LiteInst baseline         2/7
perturb candidate record index 1   1/7
perturb candidate record index 0   0/7
```

The `0/7` control states its denominator explicitly. This proves the metric can
award full depth and can detect a deliberately planted regression rather than
being inert or saturated.

## Reproduction and limitations

Run the command shape above twice for ptrace and once for each candidate. Extract
records with:

```sh
sed -n 's/^.*\(COMMIT turn .*\)$/\1/p' LOG
```

Compare the resulting ordered lines without further normalization. To reproduce
the negative controls, append a token to extracted candidate line 2 and then
line 1 before recomputing the longest common prefix.

This was a debug build, one candidate run per cell, and a COMMIT-only metric.
It is not Hermit L1/L2 validation. Raw logs and binaries were intentionally not
committed. `/etc/hostname` contained 27 bytes on the measurement host.
