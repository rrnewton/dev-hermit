# Non-ptrace backend RB-readiness (#126, recurring)

**Task:** `backend-rb-readiness-assessment-overnight`
**Date:** 2026-08-06 · **Source:** `compat-envelope/scorecard.csv` (618 rows), hermit `b64d893a`
**Mode:** existing measurement data only. **No validate run**, no egress, nothing mutated.

---

## Recommendation

**DBI is the closest backend — but it is not RB-ready, and the blocking gap is evidence, not
capability.** DBI has never been measured on a single one of the seven corpus buckets that resemble
a build. Its headline number comes almost entirely from synthetic parity fixtures.

**The one thing to do before an RB trial: run DBI against `applications` + `language-runtimes`.**
Those are the exec-heavy, multi-process buckets an RB build actually looks like, and DBI's coverage
there is currently zero.

## Envelope by backend — like-for-like

Raw per-backend totals are **not comparable**: the corpora differ by more than 2× (liteinst 220
rows, kvm 200, ptrace 99, dbi 92, sabre 7). Restricting to the **99 ptrace-covered reference cells**
gives a common denominator:

| Backend | shared cells | pass | pass % | parity | parity % | outcome breakdown |
|---|---:|---:|---:|---:|---:|---|
| **dbi** | 32 | 30 | **93.8 %** | 30 | **93.8 %** | 30 pass, **2 gap, 0 fail** |
| liteinst | 47 | 29 | 61.7 % | 27 | 57.4 % | 29 pass, 7 fail, 11 skip |
| kvm | 27 | 19 | 70.4 % | 15 | 55.6 % | 19 pass, 8 fail |
| sabre | 7 | 0 | 0.0 % | 0 | 0.0 % | **7 unavailable — no data at all** |

On cells each backend actually **attempted** (excluding gap/skip/unavailable):
**dbi 30/30 = 100 %** · liteinst 29/36 = 80.6 % · kvm 19/27 = 70.4 % · sabre 0 attempted.

Two things separate DBI qualitatively, not just numerically:

- **Zero failures.** Its two non-passes are declared `gap` — DBI declines cells rather than getting
  them wrong. Every other backend has real `fail` rows (liteinst 7, kvm 8).
- **parity % equals pass %.** Every DBI pass also carries parity. Compare **kvm: 19 pass but only 15
  parity — 4 passes carry no parity at all**, consistent with KVM's documented output-only fallback,
  which per `hermit/AGENTS.md` reports `bitwise_parity: false` and *cannot establish L2*. liteinst
  has 2 such cells. **A KVM pass count overstates its determinism assurance; a DBI pass count does
  not.**

## The disqualifying caveat: DBI's corpus is narrow and unrepresentative

DBI attempts **2 of 9 buckets**:

| DBI bucket | outcomes |
|---|---|
| `backend-parity` | 78 pass, 1 fail, 5 gap |
| `c-programs` | 8 pass |

**Buckets ptrace covers that DBI never attempts:** `applications`, `language-runtimes`,
`system-utils`, `data-handling`, `determinism-stress`, `determinism-stress-c`,
`backend-parity-spst`.

`backend-parity` is a **synthetic fixture** bucket — small targeted syscall probes. So DBI's 93.8 %
is measured overwhelmingly on micro-fixtures, and it looks best in part *because it attempted the
narrowest, easiest set*. This is the denominator trap in its natural habitat: the leader on the
common-cell table is the backend with the least demanding coverage.

The perf proxy carries the same caveat. Median `duration_ms` over passing cells: **dbi 174 ms** ·
ptrace 223 ms · liteinst 1464 ms · kvm 1639 ms. DBI is the only backend faster than ptrace — but
across trivial fixtures, so this is **not** evidence it beats ptrace on a build. It is a reason to
run the measurement, not a substitute for it.

## Why DBI is nonetheless the right candidate for RB

A reproducible build is, mechanically, **many compiler invocations** — `fork`/`exec`-heavy,
multi-process, file-I/O-heavy. That capability, not corpus %, is the gating property:

| Backend | process model (from the BACKENDS.md ground-truth audit, this session) |
|---|---|
| **DBI** | coordinator + one `RpcServer`; guest uses a blocking wire-compatible client and **reconnects after fork** — the only multi-process story |
| liteinst | **single-process, single-thread**; fails closed on fork/thread expansion (Mode B hybrid) |
| e9patch direct | **single-process, single-thread**; `clone/fork/vfork/execve` fail closed `EOPNOTSUPP` |
| e9patch generic | runs **as ptrace** at runtime (`run.rs:1761-1767` maps `E9patch → Ptrace`) — carries no Detcore of its own, so no RB benefit |
| KVM | specialized **static-ELF runner**, not a general Linux guest |
| SaBRe | in-guest, but the audited adapter is **first-poll-only** (`poll_once`, noop waker) so it cannot host async Detcore; 7/7 `unavailable` in the scorecard |

liteinst and e9patch-direct are **structurally excluded** from an RB trial: a build that cannot
`fork` cannot invoke a compiler. That eliminates the two backends with the best per-syscall latency
(liteinst Mode A measured 845.7 ns/syscall vs ptrace 26 393.7 ns — 31.2×) from RB consideration
regardless of their speed.

## Gaps before an RB trial, in order

1. **Measure DBI on `applications` + `language-runtimes`** — currently 0 cells. Without this, any RB
   claim rests on `backend-parity` fixtures. This is the blocking item.
2. **Characterise the 5 `backend-parity` gaps and the 1 `fail`** — 6 declined/failed cells whose
   syscalls may or may not appear in a build. A gap that a compiler never hits is irrelevant; one it
   hits every invocation is fatal. Currently unclassified either way.
3. **Establish a fork/exec-depth result.** "Reconnects after fork" is an architectural claim from
   source; the scorecard contains no multi-process cell proving it at build depth (nested
   `make → cc → ld`).
4. **Re-measure perf on a real workload.** The 174 ms median is fixture-scale. RB's whole premise is
   graduating off ptrace's sequentialization, so the comparison must be on something that actually
   sequentializes.
5. **Do not gate on SaBRe or KVM.** SaBRe has zero usable data (7/7 unavailable). KVM cannot claim
   L2 by construction, so it cannot underwrite a *reproducible* build.

## Provenance

| Number | Source | Status |
|---|---|---|
| 618 rows; per-backend and intersection tables; bucket coverage; median durations | `compat-envelope/scorecard.csv` | **computed this session** |
| 99 ptrace reference cells; DBI 2-of-9 buckets; kvm 4 passes without parity | same, derived | **computed this session** |
| Process models (fork/exec, first-poll-only, e9patch→ptrace mapping) | `ai_docs/backends-md-ground-truth-audit-three-patching-20260805.md` | **verified earlier this session** at reverie `025d3780` / hermit `b64d893a` |
| liteinst 845.7 ns vs ptrace 26 393.7 ns (31.2×) | S1 micro-benchmark, 2026-08-03 | inherited — **not re-measured**; `getpid`, axis-b only |
| Scorecard row provenance (`hermit_sha 9429005c`, `reverie_sha unknown`) | CSV header row | **read this session** — note `reverie_sha` is literally `unknown`, so these cells are **not** bound to a Reverie pin |
