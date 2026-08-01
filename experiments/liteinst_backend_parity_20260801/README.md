# LiteInst backend-parity scorecard measurement

This experiment measures LiteInst over every applicable row of Hermit's
checked-in `tests/backend-parity/matrix.tsv` at an exact landed Hermit SHA.
The scope is deliberately limited to dynamic ELF guests that stay within one
process and one thread.

The 23-row matrix has a 20-row SP/ST denominator. The exclusions are
`pthread_lifecycle`, `process_wait_accounting`, and `process_wait_lifecycle`.
`random_sources` is measured with the matrix's existing `--root-only` contract.
The exclusions are not failures and are not included in this bucket's ptrace
denominator.

For every included test, the runner:

1. runs ptrace and LiteInst three times with `--strict`, requiring stable guest
   exit status and byte-identical stdout;
2. compares the LiteInst observation bit-for-bit with ptrace for parity; and
3. runs each backend with `--strict --verify`, requiring the full
   `Determinism verified` DETLOG witness for determinism.

The generated `scorecard-rows.csv` uses the canonical compatibility-envelope
schema and the isolated `backend-parity-spst` bucket. This prevents future
full-matrix ptrace rows from silently enlarging LiteInst's explicitly scoped
denominator.

Reproduce from the parent workspace after staging the current-main LiteInst
runtime beside the release Hermit binary:

```bash
cd worktrees/liteinst/hermit
./validate.sh --liteinst-compat-only
cd ../../..
experiments/liteinst_backend_parity_20260801/run.py \
  --repo worktrees/liteinst/hermit \
  --hermit worktrees/liteinst/hermit/target/release/hermit \
  --output-dir experiments/liteinst_backend_parity_20260801/results
```

Results and exact provenance are recorded in `results/metadata.json` and
`results/results.csv` after the live run.
