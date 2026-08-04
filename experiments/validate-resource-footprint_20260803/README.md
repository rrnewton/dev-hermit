# validate.sh resource footprint (memory + CPU occupancy)

## Question

What resource footprint does one full `validate.sh` run actually hold? Two
consumers need the answer:

1. **Admission control** (`safe_ci_dag_runner.admission`): the memory a
   validate-box must *reserve* so the runner can admit/queue/refuse honestly
   against live host memory instead of silently overcommitting.
2. **Core allocation** (`safe_ci_dag_runner.coreallocator`): whether validate is
   a ~2-core job (as guessed) or bursts wider, so pinning does not starve it.

## Method

Run `validate.sh` at `level=full` against a **warm** primary Hermit checkout and
sample, every 2 s, the run's own cgroup:

- `memory.current` when the memory controller is delegable, else the sum of RSS
  over `cgroup.procs` (this host's populated sandbox scope does **not** delegate
  the memory controller, so the RSS-sum path was used — it is a close proxy for
  peak working set, not an exact `memory.peak`).
- `cpu.stat` `usage_usec`; the per-window rate gives instantaneous cores, and
  `cpu_seconds / wall` gives the true mean occupancy.

Harness: `measure-validate.sh` in this directory. Raw per-sample data:
`samples.csv` (`t_s,mem_current_bytes,cpu_usage_usec,inst_cores`).

```
OUT=/tmp/validate-measure VALIDATE_TIMEOUT=2400 \
  bash experiments/validate-resource-footprint_20260803/measure-validate.sh \
       "$HOME/work/dev-hermit/hermit"
```

## Results

Single run, warm checkout, `level=full`, exit 0 (5 gates passed):

| metric          | value            | what it is                                        |
|-----------------|------------------|---------------------------------------------------|
| `peak_rss`      | **1.98 GiB** (2 130 870 272 B) | peak working set of the whole run  |
| `mean_cores`    | **2.43**         | `cpu_seconds / wall` — true average occupancy      |
| `peak_cores`    | **25.71**        | max 2 s-window `usage_usec` rate — a transient burst |
| `wall_s`        | 527.7 (~8m48s)   | wall clock                                         |
| `cpu_seconds`   | 1280.1           | total CPU consumed                                 |

## Interpretation

- **Memory is small and bounded: reserve 2 GiB per validate-box.** Peak working
  set is 1.98 GiB; rounding up to **2 GiB** is the admission reservation a
  validate-box should carry. This is what `admission.request(mem)` should be
  handed for a validate step. (`peak_rss` is an RSS-sum proxy, so 2 GiB already
  carries a small conservative margin over the measured 1.98 GiB.)

- **The "~2 cores" guess was right for the MEAN, but a real ~26-core burst
  exists.** `mean_cores` 2.43 confirms validate is *on average* a ~2–3 core job,
  so it does not need a wide static pin. But `peak_cores` 25.71 is a genuine
  transient (the parallel cargo/DAG phase), not noise — pinning validate to a
  tiny fixed core set would serialise that burst and stretch wall time. Because
  CPU oversubscription is harmless (unlike memory, which must be reclaimed),
  admission gates on memory and treats cores as advisory; the core allocator
  hands out distinct cores separately without blocking admission on them.

  A count-vs-rate caution (per the coordinator "qualify the number" rule):
  `peak_cores` is an instantaneous *rate* over one 2 s window, not sustained
  occupancy — do not size a static pin to 26 cores. `mean_cores` is the occupancy
  figure; `peak_cores` bounds the transient.

## Reproduction

`metadata.json` records the exact SHA, host, kernel, and command.
Re-run the harness above against a warm checkout; expect peak_rss within a few
hundred MiB and mean_cores ~2–3 (peak_cores varies with host parallelism).
