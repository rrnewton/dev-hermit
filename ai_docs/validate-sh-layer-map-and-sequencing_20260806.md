# `validate.sh` layer map, and the smallest safe first step

**Task:** `test-architecture-epic` · hermit-det4 (`[impl agent, opus-5]`) · **2026-08-06**
**Scope:** MAP ONLY. **No code was moved.** Read-only analysis of `hermit/validate.sh` at
hermit main `4c70658e785834737cbe1524f77330c781a6f5ea`.

## 0. Two numbers to fix before anything else

**The file is 4854 lines, not 3,893.** The epic's figure is stale. Worse, the growth is recent
and fast:

| commit | date | lines |
| --- | --- | --- |
| `bab07cb9e` | 2026-08-04 21:32 | 4551 |
| `98573d146` | 2026-08-04 23:53 | 4654 |
| `b7f9c7131` | 2026-08-05 01:28 | 4687 |
| `806b67665` | 2026-08-05 08:58 | 4706 |
| `f21b22edd` | 2026-08-05 08:58 | 4860 |
| `4b9202c23` | 2026-08-06 08:03 | **4854** |

**+303 lines in 36 hours; +961 since the epic was filed.** Any sequencing that takes weeks is
racing a file that grows faster than it shrinks. That argues for a first step whose *whole point*
is to make one category of growth land somewhere else.

Structure: **95 functions / 3402 lines in function bodies / 1452 lines top-level.**

## 1. What actually executes — measured, not grepped

The epic and its predecessor both got burned by grepping for names. So: ground truth from a real
full run's emitted gates and from the run ledger.

**A real `full` run emits exactly SEVEN gates** (`ignored/validate/validate-hermit-det4-cd8a43ab7030-1786026082.log`):

```
✅ Reverie dependency pin equals latest main
✅ Initialize repository submodules
✅ Reverie pin consistency
✅ Centralized test manifest and inventory
❌ portable CI DAG lane          <- safe-ci-dag-runner does the real work
✅ Centralized test manifest and inventory
✅ privileged CI DAG lane        <- safe-ci-dag-runner does the real work
```

`run_full_suite` itself is **9 lines**: two `run_ci_manifest_lane` calls and one `run_check`.

**Profile distribution over 611 recorded runs** (`ignored/validate-run-ledger.jsonl`):

| profile | runs | share |
| --- | --- | --- |
| `full` | 385 | 63 % |
| `portable-strict-compat-only` | 179 | **29 %** |
| `portable-only` | 24 | 4 % |
| `only-portable` | 18 | 3 % |
| `quick` | 2 | <1 % |
| `envelope-only`, `selective`, `rr-compat-only` | 1 each | <1 % |
| **`super`** | **0** | **0 % locally — but see below** |

Two consequences that should change the plan:

* **Symptom 1 of the epic is already resolved for 63 % of runs.** Orchestration for
  `full`/`portable`/`privileged` is `run_ci_manifest_lane` → `ci/run-dag.sh` →
  `safe-ci-dag-runner`. This confirms the 2026-08-04 correction. Do not re-litigate it.
* **The 908-line compatibility corpus is NOT cold code.** It serves
  `portable-strict-compat-only`, the **second-most-used profile at 29 %**. Any instinct to
  "delete the part `full` doesn't touch" would delete the second-busiest path.
* **`super` is not dead either, and the ledger alone would have said it was.** Zero of 611 *local*
  runs use it — but `.github/workflows/validation-levels.yml:208` runs
  `./validate.sh super --no-label-pr`. The ledger records this box; CI is a second caller the
  ledger cannot see. **Do not delete `super` on ledger evidence.** I nearly recommended exactly
  that, and checking the workflows before writing it down is the only reason this document does
  not.

## 2. The layer map

Line ranges are brace-matched, at `4c70658e7`.

| Layer | Where it belongs | Principal regions in `validate.sh` | ~lines |
| --- | --- | --- | --- |
| **W. Wrapper** — profile/arg parsing, env setup, traps, summary, ledger/receipt emission | **stays bash** (this is the ≤100-line target) | `validation_slot_name` 37-118 · `select_validation_level` 119-298 · arg parsing ~300-460 · `cleanup` 1630-1700 · `interrupted` 1702-1787 · `append_validation_ledger` 1366-1551 · `print_summary` | ~1000 |
| **O. Orchestration** | `safe-ci-dag-runner` — **already there for full/portable/privileged** | `run_ci_manifest_lane`, `run_full_suite` 4549-4557, `run_portable_only_suite` | ~120 |
| **D. Test definition** (declarative data expressed as bash arrays) | **shared TOML manifest** | ten `declare -[aA]r` tables, lines **1034-1230**: `RR_COMPAT_PASSING_LABELS` (139 entries, 1146-1171) · `RR_COMPAT_KNOWN_FAILURES` 1136 · `COMPAT_SUMMARY_KNOWN_FAILURES` 1090 · `PORTABLE_STRICT_DIAGNOSTIC_FAILURES` 1102 · `PORTABLE_STRICT_SUPER_ONLY` 1107 · `COMPAT_SUMMARY_CATEGORIES` 1115 · `HERMIT_RUN_ARGS` 1185 · `ENVELOPE_PROBES` 1206 | **137** |
| **X. Test execution** (bash constructing hermit command lines) | **typed harness reading the manifest** | `run_compatibility_corpus` **3092-3999 (908)** · `strict_compatibility_probe` 2902-3026 (125) · `rr_compatibility_probe` 2765-2848 (84) · `run_sabre_compatibility_command` 2863-2901 · `super_probe_command` 2544-2580 · `run_super_probe` 2582-2637 · `run_envelope` 4128-4196 | ~1400 |
| **V. Determinism checking** (R/R stdout/stderr comparison in bash) | **`hermit --verify`** | the comparison + known-failure adjudication inside `rr_compatibility_probe` and the R/R tail of `run_compatibility_corpus` (~3850-3900), driven by `RR_COMPAT_EXPECTED`/`RR_COMPAT_TOTAL` | ~250 |
| **S. Super-suite** | leave for now — 0 local runs but **CI-invoked** (`validation-levels.yml:208`) | `run_super_suite` 4657-4854 · `run_super_diagnostic_suite` 4586-4656 · `run_super_stress_suite` 2639-2676 | ~310 |

Layers X and V are the mass, and they are entangled: the corpus function both *runs* cases and
*adjudicates* them. Separating them is the expensive part, which is exactly why it should not be
first.

## 3. Proposed order

The constraint that dominates everything: **validate is the only green signal we have.** So the
ordering is by blast radius on the `full` path, ascending — not by size of prize.

1. **Extract layer D (declarative tables) to the manifest.** Data only, no logic change. Details
   in §4.
2. **Leave layer S alone for now.** `super` looked like free deletion at zero local runs, but
   `validation-levels.yml:208` invokes it, so removing or gating it breaks a workflow. It is off
   the hot path and low-value to move early; revisit it after step 3, when its R/R adjudication
   has already moved into the product and what remains is thinner.
3. **Move layer V into `hermit --verify`.** Every bash-side R/R comparison that has no product
   equivalent is a **user-facing product gap** — that is the epic's own framing and it is right.
   Do this *before* X: once the product adjudicates, the harness in X only has to run cases and
   report, which makes X a much smaller job.
4. **Move layer X to a typed harness** reading the manifest produced in step 1. This is the 908-line
   prize, and it is last because it is only tractable after 1 and 3.
5. **Reduce the wrapper.** Whatever remains of W after 1-4 is the ≤100-line target.

Steps 3 and 4 are the ones that need the honest-ratchet guarantee: the R/R known failures for
`g++`, `ar`, `strip`, `gprof`, `gcov` are **real product gaps** at
`hermit-cli/src/replayer/mod.rs:776` and must survive the move unchanged.

## 4. The smallest safe first step, concretely

**Extract `RR_COMPAT_PASSING_LABELS` (139 entries, lines 1146-1171) into the shared TOML manifest,
with `validate.sh` reading it back.** Nothing else in the same change.

Why this one:

* **It is data, not behaviour.** No control flow moves. The failure mode of a data move is a
  count mismatch, which is loud and immediate — unlike a logic move, whose failure mode is a
  silently weakened check.
* **It comes with its own verifier, already in the file.** Line 1174 is a **top-level** guard:
  `if ((${#RR_COMPAT_PASSING_LABELS[@]} != RR_COMPAT_EXPECTED))` against
  `readonly RR_COMPAT_EXPECTED=139` (line 1072). Because it is top-level it runs on **every
  profile including `full`**, so a botched extraction fails the very next validate rather than
  hiding until someone runs the compat profile. That is a rare gift: the check that proves the
  refactor is already written and already on the hot path.
* **It pays a second debt.** This exact counter is a recurring rebase-conflict hotspot — the array
  and its expected count are two facts that must be edited together in one 4854-line file. Moving
  the array to the manifest and *deriving* the count removes the class.
* **It is the established direction.** `tests/e2e/manifests/backend-parity-c.toml` already exists
  and cases have been migrating into it, so this is continuation, not invention.
* **Bounded blast radius, measurable.** It touches the 29 % compat profile's data and the
  top-level guard; it cannot touch the seven `full` gates, because none of them reads the table.

**Acceptance for step 1, both directions:** with the table in the manifest, a `full` run still
emits the same seven gates and passes; and *deliberately* removing one entry from the manifest must
make the top-level guard fire with the existing message. Plant that negative before claiming the
step — an extraction that cannot fail is an extraction that is not being checked.

## 5. What I did not establish

* **No static call graph is offered as evidence.** I built one and discarded it: bash defeats it
  (`trap 'cleanup' EXIT`, names inside strings, dispatch via `case`), and my first two attempts
  produced obviously-wrong answers — "18 lines top-level" and "every profile reaches everything".
  The execution claims here come from an emitted gate list and the run ledger instead. Anyone
  extending this map should do the same.
* **Line attributions for layers V and X are approximate at the boundary.** The corpus function
  interleaves running and adjudicating; I did not draw an exact line inside it, and doing so is
  part of step 3, not of the map.
* **I did not measure the parallelism or timeout claims** (1.58×, `cpu_timeout` unreachable). They
  are inherited from the epic and its predecessor tasks, not re-derived here.
* **The ledger is local-only, and that nearly produced a wrong recommendation.** `super` shows
  0 of 611 runs there, which I first read as "unexercised, safe to delete". It is invoked by
  `validation-levels.yml:208`. Treat the ledger as evidence about *this box*, never about total
  reachability; the workflows are a separate caller set and must be grepped explicitly.
* **`scripts/super-validate.sh` is a DIFFERENT script** from `validate.sh`'s `super` profile.
  `demo-hot-path.yml` and `merge-gate.yml` reference the former; only `validation-levels.yml`
  reaches the latter. Conflating them would misattribute callers.
