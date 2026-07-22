# Chaos Mode Bug-Finding Effectiveness

Date: 2026-07-21

## Executive Summary

Chaos mode is useful, but it is not uniformly more effective than the random
scheduler by itself.

- Across the five deliberately faulty scheduling-point categories, random-only
  exposed 61.2% of four-thread runs and 67.8% of sixteen-thread runs.
  Chaos plus random exposed 56.8% and 64.6%, respectively.
- Chaos plus random did improve the sparse producer/consumer race: 34% versus
  28% at four threads, and 8% versus 5% at sixteen threads.
- The best strategy depends on the race. At sixteen threads, sticky-random 0.5
  was best for the condition-variable oracle (53%), while random-only was
  better for publication ordering (95%) and chaos plus random was slightly
  better for producer/consumer (8%).
- A sticky probability of 0.9 usually reduced discovery because it kept one
  runnable thread active too long. The four-thread producer/consumer race first
  appeared only on iteration 71 and the sixteen-thread case was not exposed.
- PMU preemption is essential for the CAS handoff race. It was never exposed
  with preemption disabled. A one-million-RCB mean exposed 8/100 seeds and a
  ten-million-RCB mean exposed 5/100.
- Imprecise timer search preserved the ten-million-RCB discovery rate and first
  failing seed while reducing 100-seed runtime from 47 seconds to 4 seconds.
- Hermit's serialized guest execution cannot explore weak-memory outcomes. The
  store-buffer test failed natively but never under any Hermit scheduler mode.

A ten-seed smoke tier is adequate for common races but unreliable for sparse
ones. At an 8% exposure rate it misses the bug about 43% of the time; at 5% it
misses about 60% of the time. Keep at least 100 fixed seeds for sparse races.

## Inputs And Environment

| Item | Value |
| --- | --- |
| Hermit checkout | /home/newton/work/dev-hermit/hermit |
| Branch and HEAD | feature/clone-vfork at 592d5c6ccbced0d1240b6562ff87652cb706f142 |
| Stress guest | tests/stress/concurrency.rs |
| Stress guest SHA-256 | bb301f3a848f7ffa166bd82708c24362ae0cc42212dfeb2446e0bbb960516fa9 |
| Stress harness SHA-256 | f20a20c68a8091c60f352097db30255d1613aa92fee0e8c85c491b936361eba9 |
| Measured Hermit binary SHA-256 | 304cc10de64c872dd7851078e3e3e3022e2643394c6e5e37a464ad28a3e4dc84 |
| Host | Linux 6.13.2, x86_64 |
| CPU | AMD EPYC 9D85, 158 cores / 316 logical CPUs |
| PMU | perf stat -e branches:u /bin/true succeeded |
| CPUID faulting | Unavailable; all runs used --no-virtualize-cpuid |

The stress files were concurrent untracked work in the primary checkout.
Research did not modify Hermit source. Hashes above identify the exact inputs.

## Method

The parameterized guest contains five intended scheduling bugs, two correct
synchronization controls, and one weak-memory litmus:

| Category | Intended outcome |
| --- | --- |
| atomic-lost-update | Expose a non-atomic load/yield/store update |
| publish-ordering | Observe a publication flag before its data |
| producer-consumer | Observe done before queued work |
| missing-barrier | Observe peers before their readiness stores |
| condvar-lost-wakeup | Enter the vulnerable notification window |
| mutex-correctness | Never fail |
| rwlock-fairness | Never fail under its bounded oracle |
| store-buffer | May fail natively; cannot fail with serialized guest threads |

For every category, 100 iterations were run at 4 and 16 threads under:

1. Native Linux scheduling.
2. Hermit random scheduling without chaos.
3. Chaos with the default round-robin heuristic.
4. Chaos plus the random heuristic.
5. Chaos plus sticky-random with probabilities 0.5 and 0.9.

Hermit scheduling-point runs disabled PMU preemption. Seeds were 0 through 99.
One non-chaos deterministic round-robin control was also run per category and
thread count. A result counted as exposure only when the guest returned status
1 and printed exposed=true. Exit status alone was not treated as a bug.

Each run had a one-second timeout followed by SIGKILL after 0.2 seconds. A cell
was censored after five timeouts. This prevented Hermit processes that retain
SIGTERM from becoming new orphaned processes. The completed matrix contained
9,244 runs: 9,223 valid outcomes, 21 timeouts, and zero harness errors.

The CAS test separately ran 100 seeds for each PMU configuration. Those 600
runs had no timeouts or harness errors.

## Scheduling-Point Results

Each entry is exposed/valid runs followed by the one-based first detection
iteration. Native first iterations are not reproducible seeds; Hermit
iterations map to seed = iteration - 1.

| Category | Threads | Native | Random only | Chaos + random | Sticky 0.5 | Sticky 0.9 |
| --- | ---: | --- | --- | --- | --- | --- |
| atomic-lost-update | 4 | 75/100; first 1 | 99/100; first 1 | 96/100; first 1 | 68/100; first 1 | 17/100; first 8 |
| publish-ordering | 4 | 94/100; first 1 | 80/100; first 1 | 74/100; first 1 | 30/100; first 3 | 10/100; first 10 |
| producer-consumer | 4 | 40/100; first 4 | 28/100; first 1 | 34/100; first 3 | 13/100; first 10 | 2/100; first 71 |
| missing-barrier | 4 | 73/100; first 1 | 67/100; first 2 | 53/100; first 1 | 75/100; first 2 | 67/100; first 2 |
| condvar-lost-wakeup | 4 | 2/100; first 57 | 32/100; first 1 | 27/100; first 1 | 28/100; first 1 | 11/100; first 8 |
| atomic-lost-update | 16 | 99/100; first 1 | 100/100; first 1 | 100/100; first 1 | 100/100; first 1 | 72/100; first 1 |
| publish-ordering | 16 | 100/100; first 1 | 95/100; first 2 | 92/100; first 1 | 42/100; first 1 | 10/100; first 12 |
| producer-consumer | 16 | 2/100; first 26 | 5/100; first 4 | 8/100; first 11 | 3/100; first 17 | 0/99; none |
| missing-barrier | 16 | 98/100; first 1 | 97/100; first 1 | 95/100; first 1 | 88/100; first 1 | 98/100; first 1 |
| condvar-lost-wakeup | 16 | 63/100; first 1 | 42/100; first 1 | 28/100; first 15 | 53/100; first 1 | 36/100; first 3 |

Aggregate rates weight each of the five faulty categories equally:

| Threads | Native | Random only | Chaos + random | Sticky 0.5 | Sticky 0.9 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 56.8% | 61.2% | 56.8% | 42.8% | 21.4% |
| 16 | 72.4% | 67.8% | 64.6% | 57.2% | 43.3% |

The deterministic round-robin control exposed atomic update, publication,
producer/consumer, and missing-barrier on its first run at both thread counts.
It did not expose the condition-variable oracle. These guests deliberately use
barriers and yields, so they are good scheduler probes but do not demonstrate
that chaos uniquely discovers their bugs.

### Round-Robin Chaos Liveness

Chaos with the default heuristic had high exposure among runs that completed,
but it was not a usable search configuration for two yield-heavy categories:

| Category | Threads | Runs attempted | Exposed | Timeouts |
| --- | ---: | ---: | ---: | ---: |
| publish-ordering | 4 | 8 | 1 | 5 |
| producer-consumer | 4 | 8 | 1 | 5 |
| publish-ordering | 16 | 6 | 0 | 5 |
| producer-consumer | 16 | 6 | 0 | 5 |

The cells were censored after five timeouts. This is consistent with the
sched_yield starvation problem already tracked by upstream issue 81. Adding
the random scheduler avoided these timeouts.

### Correctness Controls And Weak Memory

Mutex correctness and bounded RwLock fairness produced zero failures in every
100-run native and Hermit configuration at both thread counts.

The store-buffer litmus produced 1/100 native failures at sixteen threads,
first on iteration 54. It produced 0/100 in every Hermit configuration at both
thread counts. This confirms that scheduler exploration is not a substitute
for weak-memory exploration when guest threads are serialized.

## PMU Preemption Results

The CAS handoff race requires a preemption inside a computation region rather
than at an explicit syscall or yield.

| Configuration | Exposed | First iteration | First seed | Runtime |
| --- | ---: | ---: | ---: | ---: |
| Preemption disabled | 0/100 | none | none | 3s |
| Imprecise, 1M RCB mean | 8/100 | 9 | 8 | 4s |
| Imprecise, 10M RCB mean | 5/100 | 17 | 16 | 4s |
| Imprecise, 80M RCB mean | 0/100 | none | none | 4s |
| Precise, 10M RCB mean | 5/100 | 17 | 16 | 47s |
| Precise, 80M RCB mean | 0/100 | none | none | 10s |

The ten-million-RCB imprecise and precise runs found exactly the same five
seeds, including first seed 16. Imprecise search was about 12 times faster.
The one-million-RCB setting found more failures, while 80 million was too
coarse for this guest. Precise one-million-RCB runs were not repeated because
the preceding stress audit found them impractically slow.

## Iteration Budgets

Observed rates imply these practical budgets:

- Common scheduling bugs generally appeared within one to three seeds.
- Chaos plus random found the sixteen-thread producer/consumer bug at 8%;
  about 36 independent seeds are needed for a 95% chance of at least one hit.
- The 5% ten-million-RCB CAS rate needs about 59 seeds for a 95% chance.
- The 8% one-million-RCB CAS rate needs about 36 seeds for a 95% chance.
- A ten-seed tier remains useful as a smoke test, but it is not a reliable
  regression gate for sparse races.
- The existing 100-seed slow tier is justified for producer/consumer,
  condition-variable, and PMU handoff searches.

## Recommendations

1. Keep random-only as an explicit baseline. Current chaos plus random does not
   provide a general exposure-rate improvement for yield-heavy tests.
2. Select strategies per bug family instead of declaring one global winner.
   Use random for publication, chaos plus random for sparse producer/consumer,
   and include sticky 0.5 for condition-variable coverage.
3. Avoid sticky 0.9 as a default. It suppresses the cross-thread progress most
   of these tests need.
4. Use imprecise PMU timers for discovery, record the failing preemptions, and
   use precise replay for diagnosis.
5. Retain at least 100 fixed seeds for sparse tests and report the first failing
   seed so failures remain reproducible.
6. Treat timeout rate as a first-class result. A search strategy that exposes
   many bugs but regularly starves the guest is not effective.
7. Document the serialized-thread weak-memory limitation in any chaos-mode
   effectiveness claim.

## Reproduction

The scheduling-point command shape was:

    timeout -k 0.2s 1s target/debug/hermit run \
      --base-env=minimal --preemption-timeout=disabled \
      --no-virtualize-cpuid --seed=SEED \
      --chaos --sched-heuristic=random \
      target/chaos-effectiveness-concurrency CATEGORY THREADS

The random-only baseline omitted --chaos. Sticky runs selected
--sched-heuristic=stickyrandom and set --sched-sticky-random-param to 0.5 or
0.9.

The PMU command shape was:

    timeout -k 0.2s 5s target/debug/hermit run \
      --base-env=minimal --chaos --no-virtualize-cpuid --seed=SEED \
      --imprecise-timers --preemption-timeout=10000000 \
      --record-preemptions-to=target/chaos-effectiveness-preemptions.json \
      target/chaos-effectiveness-cas

All seed ranges were inclusive 0 through 99.

## Limitations

- Results cover one host, one Hermit binary, two thread counts, and synthetic
  guests with explicit scheduling points.
- The primary checkout contained concurrent untracked stress assets. Input
  hashes, rather than a repository commit, identify those files.
- Native runs are nondeterministic and their iteration numbers are not seeds.
- Exposure rates are empirical frequencies over fixed deterministic seeds, not
  confidence intervals over all possible schedules.
- Round-robin chaos cells with five timeouts were censored and must not be
  compared by raw percentage to complete 100-run cells.
- Existing orphaned condvar probes from an earlier task were observed but not
  modified or terminated.
