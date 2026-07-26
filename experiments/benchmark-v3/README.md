# Benchmark v3

Corrected apples-to-apples syscall benchmark using the literal real counter1
contract across gVisor systrap/KVM and Reverie ptrace/DBI/KVM/SaBRe.

- Method and reproduction: [harness/README.md](harness/README.md)
- Detailed results: [results/REPORT.md](results/REPORT.md)
- Machine-readable slopes: [results/summary.tsv](results/summary.tsv)
- Raw batch timings: [results/raw-samples.tsv](results/raw-samples.tsv)
- Batch medians: [results/medians.tsv](results/medians.tsv)
