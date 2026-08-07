# The compat-integrity chain — one index

Every figure below is either **re-measured by me at `origin/main` `15380090`** and
labelled MEASURED, or **carried from task notes** and labelled CARRIED, or listed
under ASSUMED. Nothing is asserted without one of those three labels.

## The verdict, up front

**The floor of 0 qualified greens was a MEASUREMENT-CONTRACT verdict, not a
backend-quality verdict.** It said nothing about whether the backends agree; it
said the scorecard could not express whether they agree. Every layer below looked
like the bottom until it was tested.

## FIRST: the headline figure in this task is already out of date

The task was written against "0 of 2,284 rows … canonical 0". **MEASURED at
`origin/main` `15380090`, that is no longer true.**

| | as the task states it | MEASURED now |
|---|---:|---:|
| population | 2,284 | **2,290** |
| `verify_compare` blank | 1,926 | 1,926 |
| `verify_compare` = `stripped` | 346 | 346 |
| `verify_compare` = `syscall-count-across-reps` | 12 | 12 |
| **`verify_compare` = `canonical`** | **0** | **6** |

**The floor has been broken.** `ddfd448 "compat-envelope: earn the first 6
qualified greens — ptrace, short guests, full tier"` landed six rows carrying
`tier=bitwise`, `bitwise_parity=1`, `comparison_tier=full-stdout-info-stack-heap`,
and real compared-message counts:

```
ptrace-short-full-tier/heapy                            compared 123|123
ptrace-short-full-tier/name_to_handle_at_eopnotsupp     compared 150|150
ptrace-short-full-tier/name_to_handle_directory_...     compared 150|150
ptrace-short-full-tier/name_to_handle_empty_path_...    compared 159|159
ptrace-short-full-tier/name_to_handle_regular_...       compared 150|150
ptrace-short-full-tier/print_memaddrs                   compared 149|149
```

All six are `backend=ptrace`, `mode=verify`. So the correct current statement is
**6 of 2,290**, not 0 of 2,284 — and the chain below is now *history plus one live
residue*, not an open indictment.

## The chain, layer by layer

**1. Greens did not state what they measured.** Resolved by requiring a tier
declaration. CARRIED from task notes; the contract is visible in the scorecard
header, which now carries `tier`, `comparison_tier`, `parity_comparator`,
`parity_tier` (MEASURED — all four present in all four live scorecards).

**2. The `tier` column was added to the contract but not the data.** CARRIED as
"empty in 100% of rows". MEASURED now: `tier` is blank on **1,926 of 2,290
(84.1%)**, with `stripped-uncounted` 346, `counter` 12, `bitwise` 6. So the layer
was real and is now partially filled; 100% is a historical figure, not a current one.

**3. No row used a bitwise-capable comparator.** MEASURED: was 0, is now 6 (see
above).

**4. A working canonical comparator EXISTS and nothing live selects it.**
MEASURED by me, first-hand, and this is the layer that still stands for the
*harness* path:
- `BitwiseInfoV1` is implemented at hermit `origin/main` in
  `hermit-cli/src/bin/hermit/verify.rs`; the CLI flag is real
  (`run.rs:336 verify_strict: bool`, consumed at `run.rs:608`).
- `hermit/ci/test_harness.sh` at `origin/main` passes `--verify` **4 times**
  (lines 1565, 1569, 1629, 1634) and `--verify-strict` / `--verify-json`
  **0 times**.
- It was never removed: `git log --all -S '--verify-strict' -- ci/test_harness.sh`
  is **empty**; the only `compat-envelope/` hits are prose, and `7eb631d`'s own
  metadata says *"the collector … never passes `--verify-strict`"*.
- One cell, both ways, same guest/backend/binary, only the flag differing:
  plain `--verify` → `bitwise_parity=false`, 84/84 compared; `--verify-strict` →
  **`bitwise_parity=true`, 107/107 compared.** Stripped discards 21% of the stream.
- Full artifact: `ai_docs/why-no-bitwise-comparator-was-wired_20260807.md`
  (landed `13f5578`).

## The parallel finding: which comparison fields have a live producer

MEASURED across all 2,290 rows at `origin/main` `15380090`:

| field | non-blank | blank | column absent |
|---|---:|---:|---:|
| `verify_compare` | 364 | 1,926 | 0 |
| `bitwise_parity` | **6** | 2,284 | 0 |
| `compared_log_messages` | **6** | 2,284 | 0 |
| `stdout_parity` | **0** | 2,278 | 12 |
| `tool_count_parity` | 0 | 12 | 2,278 |
| `parity_comparator` | **0** | 2,290 | 0 |
| `parity_tier` | **0** | 2,290 | 0 |

Two corrections to the task's framing, both MEASURED:

- `bitwise_parity` is **no longer a hardcoded blank** — `ddfd448` gave it six real
  values. The hardcode at `compat-envelope/collect-envelope.rs:434`
  (`let bitwise_parity = "";`) still exists for *that* collector, so the six rows
  came from a different producer.
- **`stdout_parity` is now non-blank on zero rows**, so the claim that "stdout is
  the only real producer" describes a producer that currently emits nothing into
  the live data. `parity_comparator` and `parity_tier` are non-blank on zero rows
  in every scorecard.

## Resolved along the way

- **The ptrace-control vs ledger mismatch was DIFFERENT GUESTS, neither wrong.**
  CARRIED from task notes. I did not re-derive this and have no artifact path for it.
- **Guest identity + witness enforcement landed** at parent `ac943078`
  *"ci-hub/parity: pin a reference guest per dimension and gate emission on it"*
  (2026-08-06). MEASURED: `ac943078` is an ancestor of `origin/main`.
  **Note, because it matters operationally:** this is the same commit carrying the
  literal home path at `ci-hub/parity/measure-dimensions.sh:54` that has main red
  on Portability — filed as `parent_portability_is_red` [P1].

## RETRACTIONS — recorded, not quietly dropped

A withdrawn figure that stays in circulation is worse than one never published.
Both of these were published in task notes before being withdrawn.

**R1. "11 of 13 certification failure" — RETRACTED.** The measurement came from a
checkout **287 commits stale**. Authoritative main is **13/13**. CARRIED; I did
not re-run the certification. Anyone who saw 11/13 should discard it.

**R2. "165 of 171 parity rows" — RETRACTED.** The figure did not survive tracing:
**zero of the rows map to scorecard cells.** CARRIED; I did not re-derive it. It
should not be cited as parity evidence in any form.

**R3. My own, from this session — "the KVM engagement witness is fake."**
I negative-tested `execution_us` by denying `/dev/kvm` via
`systemd-run --user --scope -p DevicePolicy=strict`; the witness still appeared,
which looked like proof. **The denial never bound** — a direct probe shows
`/dev/kvm` stayed openable, because cgroup-v2 device control is eBPF-based and a
user scope cannot apply it. The control was inert and the conclusion is withdrawn.
Recorded in `ai_docs/engagement-witness-per-backend_20260807.md` (`63c287b`).

**R4. My own — "plain `--verify` reports `bitwise_parity: false`" as a
measurement.** That first run used `--log=off`, and `--verify` requires
`--log=info`. The JSON's own `verdict` field read `no_result`: the guest ran, the
comparison did not. Re-run correctly it is a genuine `false` over 84 compared
messages — but the first reading was a no-result wearing a verdict's clothes.

## ASSUMED — the unverified residue

This is the section to read first if you are deciding what to do next. Each item
is something the chain *depends on* that I did not establish.

1. **That the six new qualified greens are correct, not merely present.** MEASURED
   that they exist with real counts; **NOT verified** that their comparisons are
   sound. Note all six have an **empty `ref_output_hash`** — they pass
   `check_cell_comparison.py` only because `bitwise_parity` is not in its
   `VERDICT_COLUMNS`. Whether a bitwise verdict should require a reference is an
   open contract question, not a settled one.
2. **That `ptrace-short-full-tier/*` is a representative population.** All six are
   ptrace, all six are "short guests", chosen by the commit that created them. No
   backend other than ptrace has a qualified green. Six of 2,290 is 0.26%.
3. **R1 and R2 above** — I carried both retractions from notes without re-deriving
   either. If the 13/13 figure is itself stale, nothing here would catch it.
4. **That the harness is the only selection path.** I verified `ci/test_harness.sh`
   and `collect-envelope.rs`. The six canonical rows came from *somewhere else*,
   so at least one other producer exists that I did not trace.
5. **Whether `stdout_parity` emitting zero non-blank values is intended.** It has a
   producer but no live values. That could be correct post-migration or a
   regression; I did not determine which.
6. **The cost of selecting the strict comparator at scale.** Measured only as a
   bound on one cell: plain `--verify` finished in seconds, `--verify-strict`
   exceeded 900s and completed under 2700s. Whether that is tolerable across 2,290
   cells is unknown and is the likeliest practical blocker.

## Source artifacts

| artifact | commit |
|---|---|
| `ai_docs/why-no-bitwise-comparator-was-wired_20260807.md` | `13f5578` |
| `ai_docs/engagement-witness-per-backend_20260807.md` | `63c287b` |
| `ai_docs/strace-attach-litmus-per-backend_20260807.md` | `fb1d1de` |
| `ai_docs/e9patch-detlog-routes-via-the-ptrace-host_20260807.md` | `9614483` |
| first six qualified greens | `ddfd448` |
| guest identity + witness enforcement | `ac943078` |
| verdict-without-reference refusal | `9712dce` |

## Limitations

Measured at one instant on a fast-moving tree — `origin/main` advanced from
`e808322` to `15380090` during the session, and the central figure changed under
me while this task sat. Re-measure before citing any number here.
