# LiteInst full-manifest scorecard sweep

This live sweep covers all 200 current e2e manifest tests that declare a
ptrace `verify` cell. It first runs the ptrace reference with `--strict` and a
machine-readable execution summary. LiteInst is then measured only when the
reference proves that the workload stayed within one process and one thread
and, for compiled C fixtures, the guest is dynamically linked.

Every one of the 200 rows is emitted in canonical `scorecard.csv` schema:

- in-scope rows contain measured LiteInst parity and full DETLOG determinism;
- multiprocess, multithreaded, static, build-failed, or unclassifiable rows are
  explicit `skip` cells with a reason;
- skipped rows remain in the full 200-cell denominator and therefore cannot
  inflate the reported percentages.

Parity is the same guest-visible contract used by the KVM full-corpus sweep:
exit success plus byte-identical stdout versus ptrace. Determinism requires
`hermit run --backend liteinst --strict --verify` to exit successfully and
emit the `Determinism verified` witness. This does not claim stderr, heap/stack
L3, multiprocess, multithread, or static-ELF coverage.

Reproduce from the parent workspace with a clean current-main LiteInst slot:

```bash
cd worktrees/liteinst/hermit
./validate.sh --liteinst-compat-only  # stages release Hermit + LiteInst runtime
cd ../../..
experiments/liteinst_fullcorpus_scorecard_20260801/run.py \
  --repo worktrees/liteinst/hermit \
  --hermit worktrees/liteinst/hermit/target/release/hermit \
  --output-dir experiments/liteinst_fullcorpus_scorecard_20260801/results \
  --parallel 8
```

Raw stdout/stderr and topology summaries stay in the slot's ignored `target/`
tree. Only compact CSV/JSON evidence belongs in this parent experiment.
