# Strict compatibility envelope coordination — 2026-08-06

## Decision

The fleet is not at strict cross-backend parity. The current committed
full-corpus CSV establishes only stdout-hash compatibility and same-backend
determinism over an old 200-cell population. It does not establish equality of
INFO logs, stack detlogs, or heap detlogs. Those percentages are useful for
ordering work, but none is a four-signal green.

Work should proceed in this order:

1. Make the evidence authority honest: nonzero record counts, INFO + stack +
   heap comparison, a qualifying positive, and a deliberately divergent
   negative that is refused.
2. Close absolute-zero small buckets before the broad C-program tail.
3. Close small semantic-parity buckets where determinism already passes.
4. Continue the bulk C-program ratchets after the first three steps yield
   attributable greens.

## Evidence boundary

The committed `compat-envelope/fullcorpus-scorecard.csv` contains 200 unique
verify cells per backend and was last changed by parent commit
`d83a34b3c3e619a9778ad8a130c35afaf0116acc`. Its run identifies Hermit
`82a8e853357584a3a567fd80812e015572a607c7` and Reverie
`a4f33d69a56ed4233a53b218c39d93807ffc8cd0`. Ptrace passes 179 cells; the
remaining ptrace cells are 20 divergences and one timeout. The current README
defines a newer 235-cell population, so this CSV is a historical baseline, not
a fresh result for current main.

`stdout parity` below means piped guest stdout SHA-256 equality with ptrace.
`determinism` means the backend's two runs agree. Neither is full parity.

| Lane | Stdout parity | Determinism | Highest-value next bucket |
| --- | ---: | ---: | --- |
| DBI | 136/179 (76.0%) | 155/179 (86.6%) | language-runtimes 1/6 parity, 3/6 det; then system-utils 2/6, 5/6 |
| KVM | 112/179 (62.6%) | 129/179 (72.1%) | determinism-stress-c 3/9 parity and det |
| SaBRe | 141/179 (78.8%) | 164/179 (91.6%) | language-runtimes 1/6 parity, 4/6 det; then system-utils 2/6, 6/6 |
| LiteInst | 108/179 (60.3%) | 118/179 (65.9%) | determinism-stress-c 0/9 and language-runtimes 0/6 |
| e9patch shared corpus | 172/179 (96.1%) | 178/179 (99.4%) | language-runtimes 3/6; system-utils 5/6; one C determinism failure |

The separate e9patch preprocessing-invariance population is 227/227 stdout
parity and 227/227 deterministic. That result says rewriting preserves this
observable under ptrace. It does not prove that e9patch produces in-guest INFO,
stack, and heap evidence.

## Structural blockers found in this sweep

- **KVM cannot currently prove the requested fact.** The KVM run path selects
  `kvm_output_only` by backend identity and sets `compare_logs` false. Its
  verify result is output + exit agreement by construction. Removing or
  replacing that bypass is a prerequisite to any KVM four-signal percentage.
- **DBI can exit zero with no comparable evidence.** The new strict-parity
  harness produced box rc 0 for `true` and `echo`, but zero DBI log messages.
  INFO, stack, and heap are therefore NO-RESULT, not green.
- **The shared scorecard authority is stdout-only.** Every five-backend
  percentage above is an upper bound on the required fact.
- **The regression CSV is malformed.** A DBI `file_metadata` reason contains
  unquoted newlines, splitting one record across physical lines 542–544.
  `render-scorecard.rs` warns and skips the fragments, so malformed evidence
  can silently change counts.
- **The population is stale.** The measured CSV has 200 cells while the current
  documented definition has 235. No percentage should be described as current
  main until a fresh bounded run records the exact Hermit/Reverie pair.

## Durable routing

| Finding / priority | TaskGraph destination |
| --- | --- |
| DBI zero-message evidence and language/system buckets | `ratchet-dbi-strict-parity`; honesty prerequisite `impl-dbi-verify-honest` |
| KVM backend-identity log bypass and 3/9 stress bucket | `ratchet-kvm-strict-parity`; `kvm-detlog-heap-stack-parity` |
| SaBRe language/system semantic parity | `ratchet-sabre-strict-parity` |
| LiteInst 0/9 stress and 0/6 runtime buckets | `ratchet-liteinst-strict-parity` |
| e9patch shared-corpus tail and in-guest evidence | `ratchet-e9patch-strict-parity` |
| One verifier over INFO + stack + heap for every consumer | `cross-backend-detlog-parity-sweep` |
| Quoted CSV production and fail-closed parsing | `scorecard_csv_producer_must` |

Each destination received a TaskGraph note with the exact counts and the rule
that zero records are NO-RESULT. No new concurrent full validate was started;
this sweep used existing measurement data and source inspection.

## Reproduction

```sh
./compat-envelope/render-scorecard.rs \
  --csv compat-envelope/fullcorpus-scorecard.csv \
  --backends dbi,kvm,sabre,liteinst,e9patch --observable stdout --all

./compat-envelope/render-scorecard.rs \
  --csv compat-envelope/e9patch-scorecard.csv \
  --backends e9patch --observable stdout --all
```

The next coordination tick should require: exact source SHAs, the explicit
population, nonzero selected/executed/evidence counts, and the four individual
observable verdicts. A rising stdout percentage alone is not progress against
the strict-parity north star.
