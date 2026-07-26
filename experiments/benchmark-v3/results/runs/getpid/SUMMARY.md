# Marginal syscall cost

Criterion linear-regression estimates. Units are nanoseconds per additional syscall; intervals are 95% confidence intervals.

Full Criterion HTML: `/tmp/criterion-counter1-v3-getpid-20260726/report/index.html`.

Raw measured batches are in `raw-samples.tsv`; their batch-average medians are in `medians.tsv`. Regression slope is the primary marginal-cost statistic.

## `getpid`

![getpid backend comparison](getpid.svg)

| Backend | ns/syscall | 95% CI | Statistic |
| --- | ---: | ---: | --- |
| native | 69.554 | 68.464-71.214 | slope |
| gvisor-systrap | 7305.646 | 7132.485-7508.248 | slope |
| gvisor-kvm | 911.084 | 904.959-916.668 | slope |
| reverie-ptrace | 16945.545 | 16880.530-17013.757 | slope |
| reverie-dbi | 1001.195 | 998.147-1004.378 | slope |
| reverie-kvm | 9965.556 | 9943.981-9996.991 | slope |
| reverie-sabre | 614.420 | 609.579-618.688 | slope |

Generated files: `/home/newton/work/dev-hermit/experiments/benchmark-v3/results/runs/getpid`.
