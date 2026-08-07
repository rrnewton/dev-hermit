# gVisor blog reproduction v2 — instrumentation cost vs sequentialization cost

**What this is.** A same-host, same-regime comparison of native Linux, full
gVisor (three platforms), and Hermit (six backends) on a syscall microbenchmark,
split into the two costs that a single "slowdown" number conflates:

- **instrumentation cost** — what it costs to intercept a syscall, measured
  where *nobody* has parallelism to exploit;
- **sequentialization cost** — what it additionally costs to force a
  deterministic thread order, measured as what a runtime *fails to gain* when
  given more cores.

v1 (`experiments/gvisor-systrap-benchmark-repro-20260802`) compared local Hermit
tiers against **blog-published** gVisor numbers from a different host, and its
own README retracts the resulting cross-host ordering. This runs gVisor
**locally, through the Sentry, on the same box, in the same core box** as
everything else.

## 0. How to read every number here

- **Absolute anchor first.** Native costs **145 ns per `getpid`** on this host.
  Every `x native` is against that same-host, same-regime figure — never against
  the blog's host.
- **`ns/syscall` is a slope, not a division.** Each arm pays a fixed startup
  (process exec, Sentry boot, backend attach). At N=100k that startup is most of
  the wall time for the fast arms, so `wall / N` would measure boot, not
  syscalls. Every figure is `(T(300k) − T(100k)) / 200k`, which cancels it, taken
  as the **median over 5 repetitions**.
- **A no-result is never a zero.** A cell that could not run is printed as
  `no-result` with its bound, never as `0` and never omitted.
- **Three significant figures.** `186.743x` is reported as `187x`.

## 1. Instrumentation cost — single-threaded guest, everything in a 1-core box

Guest: `getpid-loop N`, one thread, so **no arm has any parallelism to lose**.
All arms confined to one least-busy core. This is the apples-to-apples
interception cost.

| arm | ns/syscall | × native | reps |
| --- | ---: | ---: | ---: |
| native | **145** | 1.00x | 5 |
| runsc — kvm platform | 940 | 6.48x | 5 |
| hermit — dbi | 1,570 | 10.8x | 5 |
| runsc — systrap platform | 6,235 | 43.0x | 5 |
| runsc — ptrace platform | 9,320 | 64.3x | 5 |
| hermit — e9patch | 39,615 | 273x | 5 |
| hermit — ptrace | 39,695 | 274x | 5 |
| hermit — sabre | 56,660 | 391x | 5 |
| hermit — liteinst | 89,680 | 618x | 5 |
| hermit — kvm | **no-result** | — | 0 |

`hermit — kvm` hung: 2 of 2 runs hit a 300 s bound, after also hanging at a 45 s
probe. It is recorded as an explicit no-result, not as a failure and not as a
zero.

### Sanity checks against known references

| reference | expected | measured here | verdict |
| --- | --- | --- | --- |
| ptrace backend, per syscall | ~40 µs | **39.7 µs** | matches |
| native `getpid` (blog, GCE 4-vCPU) | 239 ns | 145 ns | same order; different CPU |
| optimized systrap (blog) | ~4.25x native | 43.0x native | **10x higher — see §3** |

The ptrace match is the load-bearing one: it is an independent figure from prior
work, and the harness reproduces it without tuning.

### What stands out

- **DBI is the fast Hermit backend by a wide margin** — 1,570 ns, about **25x
  faster than ptrace** and faster than gVisor's systrap platform on this host.
- **e9patch and ptrace are indistinguishable** (39,615 vs 39,695 ns), which is
  the expected result: e9patch is preprocessing *over* the ptrace runtime, not a
  separate backend, so it should not change per-syscall cost.

## 2. Sequentialization cost — 4-thread guest, 1 core vs 4 cores

Guest: `getpid-threads N 4` (added for this experiment), four threads issuing
N/4 `getpid` each, checksum consumed so the loop cannot be optimized away. An
uninstrumented runtime can spread these across cores; a deterministic scheduler
cannot. **The ratio is what determinism costs on top of interception.**

| arm | guest s @ 1 core | guest s @ 4 cores | ratio | reps | reading |
| --- | ---: | ---: | ---: | ---: | --- |
| runsc — systrap | 1.08 | 0.36 | **2.99x** | 5 | genuinely uses the extra cores |
| runsc — ptrace | 2.31 | 0.95 | **2.44x** | 5 | genuinely uses the extra cores |
| runsc — kvm | 0.56 | 0.33 | **1.67x** | 5 | genuinely uses the extra cores |
| hermit — e9patch | 8.68 | 8.41 | **1.03x** | 5 | gains nothing |
| hermit — sabre | 13.0 | 13.3 | **0.98x** | 5 | gains nothing |
| hermit — ptrace | 8.24 | 8.59 | **0.96x** | 5 | gains nothing |
| hermit — dbi | 0.41 | 0.46 | **0.89x** | 5 | gains nothing |
| native | 0.10 | 0.02 | 5.10x | 5 | **noise-dominated** — see below |
| hermit — kvm | — | — | no-result | 0 | 2/2 timeout at 300 s |

`guest s` is wall time with the measured core-box helper cost subtracted
(K=1 0.412 s, K=4 0.413 s, spread 0.029 s over 10 calibration samples). The
helper runs in **both** regimes so its cost is common-mode; subtracting it
recovers the guest time.

**Every Hermit backend sits at 0.89–1.03x: four cores buy Hermit nothing.**
gVisor on the identical guest, host, harness and regime gains 1.67–2.99x, which
is the control proving the guest really does contain exploitable parallelism.
The sequentialization cost is the gap between those two groups, and it is
**separate from and on top of** the instrumentation cost in §1.

**Native's 5.10x is flagged, not reported.** Its parallel guest time (0.02 s) is
below the calibration spread (0.029 s), so the instrument cannot resolve it at
N=200k. Directionally native uses the cores; the magnitude here is not
trustworthy. Measuring it properly needs a much larger N.


## 3. What the data likely means

**Hypotheses are labelled as hypotheses.** Measurements are in §1–2.

**(a) Hermit's sequentialization is total, and that is by design.** ptrace and
sabre gain nothing from four cores. gVisor on the identical guest, host and
harness gains ~2.3x, which proves the parallelism is really there to exploit.
So the ~1.0x is not a property of the guest or a contended box — it is Hermit
serializing threads, which is the mechanism that makes replay deterministic.

**(b) The blog discrepancy is a regime difference, not a contradiction.** We
measure gVisor systrap at 43x native where the blog reports ~4.25x. *Hypothesis:*
gVisor's Sentry is a separate multi-threaded application kernel, so confining the
whole tree to one core makes the Sentry timeshare with the guest. §2 supports
this — systrap does use extra cores when given them. **This report therefore does
not claim to refute the blog's 4.25x**; the two numbers are different quantities
measured in different regimes.

**(c) DBI is cheap to intercept but is sequentialized like the rest.** At three
repetitions DBI appeared to retain parallelism (1.28x) and e9patch more so
(1.50x); at the full five they are 0.89x and 1.03x. **That interim reading was
noise and is retracted here rather than quietly dropped** — it is a good
illustration of why this report medians over five reps on a contended host. DBI
remains notable for §1 alone: ~25x cheaper per syscall than ptrace.

## 4. Limitations

1. **A syscall microbenchmark is not an application.** `getpid` is the cheapest
   possible syscall, so these ratios are an *upper bound* on interception
   overhead. Real workloads amortize it.
2. **Shared, contended host.** 316 logical cores with other tenants; load
   averaged 50–83 during collection. Medians over 5 reps mitigate but do not
   remove this.
3. **The core box is a `sched_setaffinity` fallback**, not a cgroup cpuset — the
   `cpuset` controller is not delegated in this sandbox. The mask does inherit
   across fork+execve, which is what the method requires.
4. **The measuring instrument costs 0.42 s** (mostly the helper's 0.3 s
   `/proc/stat` sampling). It is applied to *both* regimes and subtracted using
   measured calibration rows; any cell whose corrected guest time falls below
   the calibration spread is flagged noise-dominated rather than reported.
5. **`hermit — kvm` is absent**, so no Hermit/gVisor KVM-to-KVM comparison is
   possible here.
6. **No application workloads.** v1 covered redis/ffmpeg/TensorFlow/ABSL; this
   round deliberately spends its budget on the sequential-vs-parallel split at
   the syscall layer. The application matrix is unrefreshed.

## 5. Reproducing

```bash
scripts/bench.sh                      # REPS=5 TMO=300 by default
scripts/analyze.py                    # derives every table above
```

`bench.sh` writes one row per (experiment, regime, arm, N, rep) including
`rc` and a typed note, so a timeout or failure stays visible in the raw data.
`analyze.py` recomputes all figures from `raw/measurements.csv`; nothing in this
file is hand-transcribed.

Provenance, binaries, host, widths and timeouts: `metadata.json`.
