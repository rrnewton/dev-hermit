# demo05 golden capture — harness fixed, golden still disqualified

Full write-up: `ai_docs/demo05-golden-capture-fixed-and-residual-disqualification_20260806.md`.

**Question.** The task premise was "the demo05 golden was captured wrong — harness fix, not product
fix". Is that sufficient?

**Answer.** Half right. The capture *was* wrong in four ways (three named previously; the fourth,
fixed here, is that `make build-hermit` in the capture path would detach and move the primary
checkout and build a different Hermit than the operator pinned). But a correct capture through
`05-qemu-boot.py` still is **not** double-run stable: the qcow2 snapshot the demo calls
"bitwise-reproducible" took **5 distinct SHA-256 values across ~18 controlled runs**. The serial
console was stable in every run.

**Mechanism.** Virtual-time drift, not capture hygiene: `rcbs` (the PMU retired-conditional-branch
count) differs by exactly 1 between runs, propagating into COMMIT times and `clock_gettime` results.
Scale is not the cause — `dd bs=1 count=400000` (1.68 M records, demo05's own timeslice flags) is
IDENTICAL 3/3.

**Consequence.** demo05 is DISQUALIFIED as a golden. The prefix-depth ladder should use
`dd bs=1 count=N` as its heavy rung: tunable, QEMU-free, and reproducing at demo05's scale.

Nothing was relaxed to reach any verdict; only the wall-clock prefix is stripped.

## Reproduction

```sh
PAIRS=4 ./pairs.sh                     # controlled demo05 pairs through the script
python3 analyze.py <infoA> <infoB>     # INFO divergence at 4 normalization levels
HERMIT_BIN=<pinned hermit> RUNS=3 ./rung-selfdet.sh   # rung ladder incl. dd bracketing
```
Scripts assume the paths in `metadata.json`; `pairs.sh`/`rung-selfdet.sh` write under `ignored/`.
