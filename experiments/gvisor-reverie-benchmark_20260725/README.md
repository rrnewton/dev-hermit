# gVisor vs Reverie syscall-interception benchmark (v2, steady-state)

**Question.** What is the *steady-state* per-syscall interception overhead of
each execution backend — gVisor (systrap, KVM) versus Reverie (ptrace, DBI,
KVM, SaBRe) — measured on programs that run for ≥3 s natively and exercise many
syscalls, so the result reflects trap/handle latency and not process startup?

This supersedes the v1 benchmark, which used trivially short programs (`echo`,
`seq` at ~2 ms) and therefore measured startup cost, not interception cost.

## Method

- Host: AMD EPYC 9D85 (316 logical CPUs), kernel 6.17.13, pinned to **CPU 112**.
- Sampling: **2 warmups + 9 measured** runs per cell; report the **median**.
- Backend order is rotated each round to spread transient interference.
- Guest env fixed: `LC_ALL=C LANG=C RUST_LOG=off`, stdout discarded.
- Runner: `run_benchmarks.rs` (std-only rust-script). Analysis: `analyze.rs`
  (std-only; deterministic SplitMix64 bootstrap, 10 000 replicates).
- **Load caveat:** the host was under extreme contention (load ≈ 300–400)
  throughout. Medians are **directional**, not publication-grade. Raw per-sample
  data is retained in `real-raw.tsv` / `marginal-raw.tsv` for re-analysis.

Backends (all seven exercise the *same guest binary*):

| Label | What runs |
| --- | --- |
| native | guest run directly |
| gvisor-systrap | `runsc --platform=systrap` |
| gvisor-kvm | `runsc --platform=kvm` |
| reverie-ptrace | `counter2` (Reverie ptrace counter tool) |
| reverie-dbi | `drrun -c libreverie_dbi_client.so` (DynamoRIO, PrototypeTool) |
| reverie-kvm | `reverie-kvm-counter` |
| reverie-sabre | `riptrace --sabre sabre --plugin libriptrace_plugin.so` |

Provenance (SHAs, binary hashes, toolchain) is in `metadata.json`.

### Workloads (all ≥3 s native except sqlite, see caveat)

| Workload | Command | Syscall count | Native median |
| --- | --- | --- | --- |
| getpid-3s | C fixture, 40 000 000 × `getpid()` | 40 000 000 (exact) | 4.139 s |
| find-usr | `find /usr -type f` (exit 1: 3 unreadable dirs) | ~3 019 178 (ptrace-observed) | 7.610 s |
| dd-byte-io | `dd if=/dev/zero of=/dev/null bs=1 count=15000000` | 30 000 000 (exact) | 3.346 s |
| tar-doc | `tar cf /dev/null` with `/usr/share/doc` ×120 | ~1 963 837 (ptrace-observed) | 3.140 s |
| sqlite-100k | `sqlite3 :memory:` 100K insert + index + 900 scans | ~167 (ptrace-observed) | 2.997 s |

`getpid-3s` and `dd-byte-io` have exact operation counts. For `find`/`tar`/
`sqlite` the common denominator is the median ptrace-observed syscall count
(backends observe slightly different totals; find varies by only 3 syscalls out
of 3.02 M ≈ 0.0001 %).

## Headline result — raw per-syscall trap overhead

The purest measure is `getpid-3s` (40 M syscalls, ~zero guest work per call).
Amortized per-syscall overhead = `(median_backend − median_native) / count`:

| Backend | median (s) | slowdown | **µs / syscall** |
| --- | --- | --- | --- |
| native | 4.139 | 1.0× | 0 (baseline) |
| **reverie-sabre** | 43.6 | 10.5× | **0.99** |
| **gvisor-kvm** | 44.7 | 10.8× | **1.01** |
| **reverie-dbi** | 61.1 | 14.8× | **1.42** |
| gvisor-systrap | 327.5 | 79.1× | 8.08 |
| reverie-kvm | 1176.5 | 284.2× | 29.31 |
| **reverie-ptrace** | 1624.7 | 392.5× | **40.51** |

**Takeaway:** the default Reverie/Hermit backend, **ptrace, has by far the
highest per-syscall cost (~40 µs)** — about **40× more expensive than SaBRe,
gVisor-KVM, and DBI (~1 µs)**. gVisor's **systrap (~8 µs)** sits in the middle.
gVisor-**KVM (~1 µs)** is competitive with the fastest Reverie interposition
paths. reverie-kvm (~29 µs) is close to ptrace, not to gvisor-kvm — the two
"KVM" backends are architecturally different and perform very differently.

## Marginal getpid slope (ns per syscall) with 95 % bootstrap CI

Fitted `wall_ns = intercept + slope·N` over N ∈ {1K, 10K, 100K, 1M}; slope is
the marginal cost per additional syscall. Per-N medians are shown so
nonlinearity is visible; CI is percentile bootstrap over the 9 raw samples/N.

| Backend | slope ns/call | 95 % CI | intercept ns | implied µs/call @1M |
| --- | --- | --- | --- | --- |
| native | 69.9 | 67.7 – 76.3 | 834 463 | 0.071 |
| reverie-sabre | 737 | 658 – 863 | 64 122 154 | 0.801 |
| reverie-dbi | 1 228 | 984 – 5 046 | 211 857 163 | 1.408 |
| gvisor-kvm | 3 389 | 2 252 – 9 072 | 287 313 783 | 3.656 |
| gvisor-systrap | 7 104 | 6 566 – 20 532 | 575 451 162 | 7.593 |
| reverie-kvm | 25 959 | 25 224 – 26 369 | 227 937 635 | 26.157 |
| reverie-ptrace | 32 302 | 31 453 – 33 375 | 24 774 785 | 32.326 |

The marginal slope for ptrace/kvm/sabre/native (linear, tight CI) agrees with
the `getpid-3s` amortized numbers above. systrap / gvisor-kvm / reverie-dbi have
**wide CIs** because their small-N (1K) points are startup-dominated and noisy;
their true asymptotic marginal cost is best read from the "µs/call @1M" column
(gvisor-kvm ≈ 3.7 µs, systrap ≈ 7.6 µs, dbi ≈ 1.4 µs). The per-N `µs/call`
convergence table is in the analysis output and `marginal-report.tsv`.

## Real-workload matrix (per-syscall overhead, µs/call)

Full medians in `real-summary.tsv`; overhead + slowdown in `real-report.tsv`.
`—` = backend cannot run this workload (see exclusions).

| Backend | getpid-3s | find-usr | dd-byte-io | tar-doc | sqlite-100k |
| --- | --- | --- | --- | --- | --- |
| native (median s) | 4.139 | 7.610 | 3.346 | 3.140 | 2.997 |
| reverie-sabre | 0.99 | 1.85 | 1.27 | 1.40 | (compute) |
| gvisor-kvm | 1.01 | 17.21 | 1.05 | 39.58 | (compute) |
| reverie-dbi | 1.42 | 3.30 | 1.52 | 6.19 | (compute) |
| gvisor-systrap | 8.08 | 10.16 | 5.55 | 14.51 | (compute) |
| reverie-kvm | 29.31 | — | — | — | (compute) |
| reverie-ptrace | 40.51 | 41.53 | 31.13 | 31.66 | (compute) |

Consistent cross-workload story: **ptrace ~30–42 µs/syscall everywhere**;
**SaBRe ~1–2 µs** is the most consistent low-overhead interposer; **DBI
~1.4–6 µs**; **gVisor-systrap ~6–15 µs**. gVisor-KVM is excellent on
syscall-light-per-trap loops (getpid/dd ~1 µs) but degrades sharply on
filesystem-metadata-heavy work (find 17 µs, tar 40 µs), where its sentry must
service richer VFS operations.

## Caveats and honest limitations

- **sqlite-100k is compute-bound, not syscall-bound** (~167 syscalls for 100K
  in-memory inserts). Its per-syscall numbers are meaningless; instead it shows
  *whole-program* cost. Here DBI is the outlier: **21× slowdown** (63 s) because
  DynamoRIO instruments every basic block — its cost is per-*branch*, not
  per-*syscall*. All syscall-interposition backends (ptrace, gvisor-*, sabre,
  reverie-kvm) stay within ~1.0–1.4× of native on sqlite. This is a useful
  contrast: DBI trades cheap syscalls for expensive computation.
- **sqlite-100k native median = 2.997 s**, marginally (≈3 ms, 0.1 %) below the
  3 s target (samples 2.969–3.110 s straddle 3 s). Reported, not hidden. The
  other four workloads are comfortably ≥3 s native.
- **reverie-kvm** cannot run find/dd/tar (ENOSYS opening `/usr`, `/dev/zero`,
  `/dev/null` — it fails after ~50–80 syscalls). Those cells are excluded from
  all medians, not counted as fast "wins". reverie-kvm runs only getpid + sqlite.
- **One gVisor-systrap `find` sample was killed** (hung 38 min in futex_wait vs
  the normal 30–42 s) during the original run (status 143). A replacement sample
  was run on CPU 112 (44.556 s, exit 1, all 3 expected permission errors) and
  appended; the corrected 9-sample median is unchanged at 38.281 s (robust).
- Extreme host load inflates all absolute times and widens variance; treat
  ratios between backends as more reliable than absolute microseconds.

## Reproduce

```bash
cd hermit/target/gvisor-benchmark-v2       # gitignored scratch with fixtures
source bench.env                            # 8 backend binary paths
# real matrix (needs MICRO_ITERATIONS=40000000 DD_COUNT=15000000 TAR_REPEATS=120):
taskset -c 112 env MICRO_ITERATIONS=40000000 DD_COUNT=15000000 TAR_REPEATS=120 \
  BENCH_ROOT=$PWD rust-script run_benchmarks.rs real <out-dir>
# marginal series:
taskset -c 112 env BENCH_ROOT=$PWD rust-script run_benchmarks.rs marginal <out-dir>
# analysis:
BENCH_ROOT=$PWD rust-script analyze.rs
```

Files in this directory: `real-raw.tsv`, `real-summary.tsv`, `real-report.tsv`,
`marginal-raw.tsv`, `marginal-report.tsv`, `run_benchmarks.rs`, `analyze.rs`,
`bench.env`, `metadata.json`, `systrap-find-replacement.stderr`.
