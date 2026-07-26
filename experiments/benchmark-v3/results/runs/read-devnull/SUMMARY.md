# Marginal syscall cost

Criterion linear-regression estimates. Units are nanoseconds per additional syscall; intervals are 95% confidence intervals.

Full Criterion HTML: `/tmp/criterion-counter1-v3-read-20260726/report/index.html`.

Raw measured batches are in `raw-samples.tsv`; their batch-average medians are in `medians.tsv`. Regression slope is the primary marginal-cost statistic.

## `read-devnull`

![read-devnull backend comparison](read-devnull.svg)

| Backend | ns/syscall | 95% CI | Statistic |
| --- | ---: | ---: | --- |
| native | 92.483 | 91.533-93.345 | slope |
| gvisor-systrap | 7438.116 | 7354.679-7508.922 | slope |
| gvisor-kvm | 1041.539 | 1032.682-1051.104 | slope |
| reverie-ptrace | 17310.645 | 17142.354-17465.778 | slope |
| reverie-dbi | 1521.385 | 1513.080-1529.048 | slope |
| reverie-kvm | 13183.877 | 13172.636-13193.331 | slope |
| reverie-sabre | 851.538 | 848.284-856.619 | slope |

Generated files: `/home/newton/work/dev-hermit/experiments/benchmark-v3/results/runs/read-devnull`.
