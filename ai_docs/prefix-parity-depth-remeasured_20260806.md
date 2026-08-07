# Prefix-parity depth, re-measured after the SaBRe loader-openat fix

**Task:** `re-measure-prefix-parity-depth-after-loader-openat-fix` · hermit-det4
(`[impl agent, opus-5]`) · **2026-08-06** · local, no egress.

## Anchor

| | |
| --- | --- |
| hermit | `4c70658e785834737cbe1524f77330c781a6f5ea` (main) |
| binary | built by me: `hermit 0.2.0 (2026-08-06, g4c70658e7858)`, release, `--features third-party-backends` |
| reverie pin | `dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6` |
| SaBRe | staged from **this** tree (`cargo build --release --locked -p detcore-dbi -p detcore-sabre -p hermit-install`), so `libdetcore_sabre.so` carries **this** detcore, not an older pin |
| host | devbig014 |

Staging mattered: the only pre-existing SaBRe-capable binary on this box (`g52d56e5c`) pins
reverie `79517704`, two pins behind. Measuring with its `libdetcore_sabre.so` would have measured
the **old** detcore and could not have shown the fix either way.

## Definition

**Prefix-parity depth `Y/Z`** — `Y` = number of leading deterministic records in the INFO log that
are identical to the ptrace golden; `Z` = total such records in the golden. A record is a `DETLOG`
line or a scheduler `COMMIT turn` line. Only the real wall-clock prefix is stripped; syscall
values, counts, flags and virtual time are compared verbatim. **Full INFO log, not stdout.**

## Precondition: the ptrace golden must be self-deterministic

Enforced per rung, double-run, before any backend is compared.

| rung | golden self-check | Z (golden records) |
| --- | --- | --- |
| `/bin/true` | **PASS** | 118 |
| `/bin/echo hi` | **PASS** | 309 |
| `/bin/cat /etc/hostname` | **PASS** | 326 |
| `/bin/wc -c /etc/hostname` | **PASS** | 316 |
| `sh -c 'echo a \| wc -c'` (fork/exec pipeline) | **PASS** | 603 |
| **demo05 (QEMU Linux boot)** | **FAIL — self-depth 4507 / 1 431 103 (0.31 %)** | n/a |

## Results — strict definition

| rung | sabre | dbi |
| --- | --- | --- |
| `/bin/true` | **0 / 118** | **3 / 118** |
| `/bin/echo hi` | **0 / 309** | **3 / 309** |
| `/bin/cat /etc/hostname` | **0 / 326** | **3 / 326** |
| `/bin/wc -c /etc/hostname` | **0 / 316** | **3 / 316** |
| fork/exec pipeline | **0 / 603** | **3 / 603** |
| demo05 | **NOT MEASURABLE** | **NOT MEASURABLE** |

demo05 is not measurable *for backend parity* because its golden fails the precondition. Reporting
a backend depth against a reference that is not self-identical would attribute to the backend a
divergence no backend fix can close. (The sabre demo05 run was still progressing at report time —
serial growing 17 869 → 24 929 bytes, so slow rather than wedged — but its depth would be
meaningless regardless.)

## The sabre 0 is log plumbing, not the guest — and the loader fix did work

The strict `0/Z` is real and is reported as the headline, but it is **not** caused by the guest
diverging at the first scheduling decision. The first differing record is:

```
- ptrace   INFO detcore::scheduler::runqueue: DETLOG SCHEDRAND: seeding scheduler runqueue with seed 0
+ sabre    INFO detcore:                      DETLOG USER RAND: seeding PRNG for root thread with seed 0
```

Two artifacts at once: **sabre omits the `SCHEDRAND` record entirely**, and it emits every record
under a flat `detcore` tracing target where ptrace emits the module path
(`detcore::scheduler::runqueue`, `detcore::tool_local`). Both are properties of how sabre's DETLOG
reaches the sink, not of what the guest did.

**Second measurement, definition tightened, stated separately so it cannot be confused with the
headline:** strip the tracing-target prefix and the depth becomes **6 / 118** on `/bin/true`, with
the first *substantive* divergence:

```
- ptrace   inbound syscall: brk(NULL) = ?
+ sabre    inbound syscall: clock_gettime(CLOCK_MONOTONIC, 0x7fffffffb4d0) = ?
```

**The dynamic loader's openats are gone from the head of the trace.** The first divergence has
moved to an extra/earlier `clock_gettime(CLOCK_MONOTONIC)` in sabre's startup.

### The same depth in the metric's own units, and the commit to unblock next

The metric is defined in **detcore commits**, so stated that way for `/bin/true`, sabre,
target-normalized:

| unit | depth |
| --- | --- |
| records (DETLOG + COMMIT) | **6 / 118** |
| **detcore COMMIT turns** | **2 / 14** |

* **last COMMIT turn still identical:**
  `COMMIT turn 1, dettid 3 using resources {MemAddrSpace(DetPid(3)): RW}`
* **first diverging commit — this is the next thing to unblock:**
  `COMMIT turn 2, dettid 3 using resources {Path(".../libunwind/lib/glibc-hwcaps/x86-64-v2/…")}`

So the divergence sits at **`COMMIT turn 2`**, the commit that acquires the loader's search-path
`Path` resource — reached one syscall early on sabre, because sabre issues
`clock_gettime(CLOCK_MONOTONIC)` where ptrace issues `brk(NULL)`. The openat *results* no longer
differ; what differs is the syscall that precedes the loader's path acquisition. That is the
next rung of the owner's loop.

One reproducibility note on that commit record: the `Path(...)` it names is under the
`LD_LIBRARY_PATH` this measurement used, so the golden's loader records embed the caller's
environment. Both arms ran with the identical environment, so it is a shared constant here and not
a source of the divergence — but a golden captured under a different `LD_LIBRARY_PATH` is not
comparable to this one, which matters for anyone re-running it.

**So the ratchet cannot move for sabre until the log plumbing is equalised**, no matter how many
real divergences get fixed: the strict metric will keep reading 0 while record 0 differs for a
formatting reason. Equalising the sabre sink (emit the module target; emit `SCHEDRAND`) is a
prerequisite for this metric to be able to show sabre progress at all.

**dbi's 3/Z** is the already-known P0: record 3 is
`COMMIT turn 0, dettid <raw host TID>` (`dbi-determinize-detlog-thread-id`). It is the same value
at every rung because it is the first `COMMIT` record, which is always the fourth record.

## Old vs new

**There is no prior number to compare against.** I searched the task notes and `ai_docs/` /
`experiments/` for a previously recorded prefix-parity depth with a denominator and found none.
The honest side-by-side is therefore:

| | old | new |
| --- | --- | --- |
| sabre, `/bin/true` | *never measured with a denominator* | 0 / 118 strict · 6 / 118 target-normalized |
| dbi, `/bin/true` | *never measured with a denominator* | 3 / 118 |
| demo05 golden self-depth | *never measured* | 4507 / 1 431 103 |

This is the first measurement of the metric with a stated `Z`. It is not a regression against
anything; there was nothing to regress from.

## Why the metric is worth the trouble — demonstrated on the headline target

Earlier today I measured five consecutive demo05 ptrace boots whose **serial output was
byte-identical across all five** (26 209 bytes each, exit 0, 47.9–48.3 s). Two runs of the same
configuration diverge in the INFO log at **record 4507 of 1 431 103**, on a
`read(3, …)` returning a different length (24 509 vs 25 896 bytes).

**Stdout-only parity reports demo05 as 5/5 perfect. Full-INFO parity reports that the reference is
not even self-deterministic.** That is precisely the weakness the metric exists to remove, and it
is the strongest single argument in this document for the change of measure.

## Instrument errors caught (both mine, both would have produced false findings)

1. **The harness preferred a non-empty `--log-file` over stderr.** SaBRe writes 4 records to the
   log-file and ~90 to stderr, so that rule would have measured sabre against a truncated stream.
   Fixed to prefer the **richer** stream. This is a real bug in the harness shipped as PR #1709 and
   should be fixed there too.
2. **Copying the harness out of `scripts/` broke its relative `#[path]` prelude**, so it failed to
   compile — and my driver, which treated "no `STREAMS AGREE`" as non-self-determinism, reported
   *"the ptrace golden is not self-deterministic at every rung."* That is a dramatic false finding
   and it survived for one run. The driver now distinguishes **TOOL-ERROR** (no verdict emitted)
   from **FAIL** (a real `FIRST DIVERGENCE`), and never reports the former as the latter.

## Reproduction

```
HERMIT_BIN=ignored/det4-parity/hermit/target/release/hermit ./ignored/det4-parity-depth.sh
```

Raw results: `ignored/det4-parity-depth.tsv`. Harness:
`ignored/det4-detlogdiff/hermit/scripts/xbdiff-richer-stream.rs` (PR #1709's
`cross-backend-detlog-diff.rs` plus the stream-selection fix).

## Limits

* Five low rungs plus demo05. No `--detlog-heap` / `--detlog-stack` (L3) depth was measured.
* One host, one run per cell for the backend comparisons; the golden self-check is the only
  repeated measurement.
* kvm, liteinst and e9patch were not measured.
* The target-normalized 6/118 was computed only for `/bin/true`; the other rungs have the strict
  number only.
