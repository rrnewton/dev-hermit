# Per-backend fixture execution matrix

**Question.** For each backend-parity-c fixture and each backend, does the fixture actually
EXECUTE (with a nonzero check count), or is it GATED-OFF (`ci=false`), NOT-WIRED (backend never
mentioned), DECLINED (named and disabled with a reason), or unable to run here?

**Answer.** Of 2550 (fixture, mode, backend) cells, **exactly 1 is selected to run in CI**:
`personality-domain`, mode `verify`, backend `ptrace`. e9patch appears in zero cells — neither
enabled nor declined. The 23 cells that name a non-ptrace backend as enabled are all
simultaneously `ci=false` and target a backend not built here, so none has ever been observed.

Full analysis, including the empty-cell inventory and two corrections to my own intermediate
numbers: `ai_docs/per-backend-fixture-execution-matrix-20260806.md`.

## Files
- `results.csv` — the matrix, one row per (fixture, mode, backend) with its cell class and, for
  ptrace/verify, the actual observed run.
- `ptrace-verify-actual-runs.tsv` — raw sweep output: fixture, state, exit code, `ok=` count.
- `metadata.json` — SHAs, host, kernel, compile flags, measured backend availability.

## Reproduction
```bash
cd hermit    # f706d3dc3
gcc -O1 -D_GNU_SOURCE -pthread -o /tmp/f tests/backend-parity/fixtures/<name>.c
hermit run --strict --verify --base-env=minimal -- /tmp/f      # record rc and ok=N
hermit run --backend=<b> --base-env=minimal -- <hello>         # backend availability
```
Runtime env: `LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib`
(the `ignored/lu-parity` tree ships only the static libunwind and fails at runtime).
