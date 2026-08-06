# A measured rung ladder from 5K to 529K records — the three-order-of-magnitude hole is closed

**Task:** `bracket-the-rung-gap-between-1-7k-and-1-5m-records` · hermit-det4
(`[impl agent, opus-5]`) · **2026-08-06** · local, no egress.
**Anchor:** hermit `4c70658e785834737cbe1524f77330c781a6f5ea`, reverie pin `dd3c178`,
`hermit 0.2.0 (2026-08-06, g4c70658e7858)` release, ptrace, `--strict`, relaxations none,
`PYTHONDONTWRITEBYTECODE=1`.

## The qualified ladder

Every row is measured. A rung is **QUALIFIED** only when three separate runs produce a
byte-identical full INFO log (`DETLOG` + scheduler `COMMIT turn`, wall-clock prefix stripped and
nothing else). An unqualified rung is not a rung.

| rung | records | band | golden, n=3 |
| --- | --- | --- | --- |
| `gcc -O0 -o out tiny.c` *(hermetic — see §3)* | **5 413** | ~5K | **QUALIFIED** |
| `tar cf /dev/null /usr/include` | **12 011** | **~10K** | **QUALIFIED** |
| `sh` loop, 100× `/bin/true` | **18 040** | ~20K | **QUALIFIED** |
| `grep -rl include /usr/include \| wc -l` | **30 738** | ~30K | **QUALIFIED** |
| `sh` loop, 500× `/bin/true` | **88 528** | **~100K** | **QUALIFIED** |
| `sh` loop, 2000× `/bin/true` | **352 862** | ~350K | **QUALIFIED** |
| `sh` loop, 3000× `/bin/true` | **529 084** | **~500K** | **QUALIFIED** |
| demo05 (QEMU boot) | ~1 500 000 | ~1.5M | unresolved (see `ptrace-golden-self-determinism-per-rung_20260806.md`) |

All three requested bands — ~10K, ~100K, ~500K — are hit **and** qualified. The remaining hole is
529K → 1.5M, a factor of ~3, down from three orders of magnitude.

## Also sized, not qualified

Measured but not run at n=3; listed so nobody re-derives them.

| candidate | records | note |
| --- | --- | --- |
| `perl -e 'print 1'` | 585 | cheapest interpreter start measured |
| `sha256sum` on a 4 MB file | 652 | **compute-heavy, syscall-light — see §2** |
| `python3 -c 'print(1)'` | 2 514 | the previous "TOOL-ERROR" rung, now measured properly (§3) |
| `python3 -c 'import json; …'` | 2 760 | one import adds ~250 records |
| `gcc -O2 -c linux/kvm.h` | 2 081 | |
| `find /usr/include -type f \| wc -l` | 5 636 | |
| `sort big.txt \| wc -l` | 7 770 | |
| `tar cf /dev/null /usr/share/doc` | 35 718 | |

## 2. The lever is syscalls, not work

`python3 -c 'print(1)'` and a version of the same script running a 10 000-iteration arithmetic loop
both produce **2 514 records — identical**. `sha256sum` over a 4 MB file produces **652**, fewer
than `perl` starting up and doing nothing.

**Detcore record count tracks syscall volume, not CPU work.** That is why every "make it bigger"
attempt in the previous session landed in the same low band: they were making the guest *compute*
more. The ladder above climbs by `fork`/`exec` count and by file-system traversal, which is the
only thing that moves the number. Anyone extending this ladder toward 1.5M should reach for more
processes or more path lookups, not a heavier algorithm.

The scaling is close to linear in fork count: 100 → 18 040, 500 → 88 528, 2000 → 352 862,
3000 → 529 084 (≈176 records per `fork`+`exec` of `/bin/true`). So ~8 500 forks would land on
demo05's 1.5M, though wall time is superlinear — fork-2000 took 18.9 s and fork-3000 took 87.8 s,
so a 1.5M-record fork rung is likely several minutes and worth measuring before adopting.

## 3. Two rungs that failed for unpinned inputs, and the fix

Neither was fixed by weakening the comparison. Both were fixed by making the rung hermetic, which
is the only legitimate repair.

**`gcc-tiny-c` — FAIL at 277/5413, then IDENTICAL at 5413/5413.** The divergence:

```
readlink("/home/newton/det4-q/tiny.out", …) = Err(ENOENT)   ← run 1
readlink("/home/newton/det4-q/tiny.out", …) = Err(EINVAL)   ← runs 2 and 3
```

gcc's **output file survives between runs**, so run 1 observes it absent and later runs observe it
present-but-not-a-symlink. Record counts differed too (5413 vs 5417). Deleting the output before
each run makes the rung reproduce exactly. This is the same class as the demo05 `.pyc` finding: the
guest was reading state left behind by its own previous run.

**`python3 -c 'print(1)'` — previously reported TOOL-ERROR, now 2 514 records.** That verdict was
correct and worth having: the earlier attempt passed the program through `sh -c`, which ate the
parentheses. Passing guests as an **argv array** rather than a shell string fixes it. Had
TOOL-ERROR been collapsed into FAIL, this would have been recorded as a nondeterministic rung and
someone would have gone looking for a determinism bug that does not exist.

## 4. Method

Three-valued throughout: **IDENTICAL / FAIL / TOOL-ERROR**. A run that crashes, times out, or emits
zero records is TOOL-ERROR and is never reported as FAIL. It has now caught three false results
across this line of work.

Only the real wall-clock prefix is stripped. Virtual time, RCB counts, syscall arguments, results,
sizes and flags are compared verbatim. No field dropped, no time blunted.

Guests are passed as argv arrays. Where a shell is genuinely required (pipelines, loops) it is
named explicitly as `/bin/sh -c`, so the shell is part of the rung rather than an accident of
quoting.

## 5. What remains

* **529K → 1.5M is still open.** A ~8 500-fork rung should land there by the scaling above, but
  needs measuring, and its wall time may make it impractical as a routine rung.
* Eight sized-but-unqualified candidates (§1) — each needs an n=3 pass before use.
* demo05 remains unresolved, per the companion artifact; nothing here changes that.
* Every number here is one host, one binary, n=3. The record counts are stable to the record
  across runs, but they are counts for *this* corpus on *this* filesystem — `tar`/`grep`/`find`
  rungs traverse `/usr/include` and `/usr/lib64`, so they will differ on a differently-populated
  host and are not portable constants.

## Reproduction

```
HERMIT_BIN=ignored/det4-parity/hermit/target/release/hermit ./ignored/det4-rung-sizing.sh
HERMIT_BIN=ignored/det4-parity/hermit/target/release/hermit ./ignored/det4-rung-qualify.sh
```
Raw: `ignored/det4-rung-sizes.tsv`, `ignored/det4-rung-qualify.tsv`.
