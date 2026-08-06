# Livelock vs contended wait — a classifier for the timeout bucket

**Task:** `livelock-reads-as-slow-test-on-every-wall-clock-instrument` (P1)
**Date:** 2026-08-06 · **Author:** hermit-design · Local only, no egress, no validate run.
**Code:** `ci-hub/validate/livelock_class.py` · **Brackets:** `ci-hub/validate/test_livelock_class.py` (21/21)

The full design rationale lives in the module docstring, which travels with the code. This artifact
records what the real data changed about the design, and what is **not** wired.

---

## 1. The measured data refuted the obvious rule

The task states the signature as *"wall == CPU at the budget (a full core burned)"*. Surveyed against
the local step-profile corpus (8 files, 96 rows, 68 with usable wall time), that rule is **not safe on
its own**:

```
cores_burned = (user_s + sys_s) / elapsed_s
    min 0.012 · p50 0.919 · p90 6.649 · max 127.262
```

* **`≈ 1.0` is ordinary.** The median *completed* step already sits at 0.919 cores. A rule keyed on the
  ratio alone flags healthy single-threaded work.
* **A multi-threaded livelock is not `≈ 1.0`.** N spinning threads give `≈ N`; "wall == CPU" describes
  only the single-threaded case.

So the ratio is a measure of **cores burned**, not a livelock test, and `timed_out` is a **mandatory
conjunct**:

| condition | verdict |
| --- | --- |
| `timed_out ∧ cores_burned ≥ 0.90` | **LIVELOCK** |
| `timed_out ∧ cores_burned < 0.90` | **CONTENDED_WAIT** |
| `timed_out ∧ no CPU data` | **UNKNOWN_NO_CPU** |
| `¬timed_out` | NOT_APPLICABLE |

## 2. The threshold is a policy choice inside a measured gap

| case | wall | cpu | cores |
| --- | --- | --- | --- |
| confirmed livelock — `test.detcore_misc` @85626e18 (100% CPU in `futex_`, ptrace-stopped `vfork` child) | 600.013 | 599.986 | **0.99995** |
| killed-but-blocked — `test.rr_suite_contract`, local corpus | 300.191 | 100.9 | **0.336** |
| CPU excluded from parent getrusage — `detcore_misc` @3d5b42ce | 601 | 7.47 | **0.012** |

`0.90` sits inside the 0.336 → 0.99995 gap. It is a **judgement, not a derivation**, so every verdict
carries the threshold it used and the report labels the basis. `test_the_real_incidents_stay_separated_across_the_whole_gap`
asserts both real incidents classify correctly at 0.40/0.60/0.75/0.90/0.98 — so a nudge cannot silently
misclassify them, and if that test ever fails it means the gap closed and the threshold needs
re-deriving rather than adjusting.

## 3. `UNKNOWN_NO_CPU` is a first-class verdict, not a fallback

The classification **cannot be applied retroactively to the validate ledger**: per-gate rows carry only
`real_seconds`, and the top-level run CPU is not a proxy — the run whose child was measured live at
100% CPU for 601s recorded **7.47 CPU-seconds total**, because the spinning child's CPU is excluded
from the orchestrator's `getrusage`.

So a killed gate with no CPU data returns UNKNOWN. Either default would manufacture a verdict the data
does not support, and "assume contended ⇒ re-dispatch" is specifically the behaviour that spends a
second full budget re-running a confirmed livelock.

Note the trap this encodes: feeding the *ledger's* top-level CPU into this classifier would have
classified the confirmed livelock as CONTENDED (0.012 cores). The input must be the **per-node** step
profile, not the parent's accounting.

## 4. Live result

```
$ livelock_class.py --profiles $(local step-profile corpus)
rows=96 killed=1 threshold=0.9 cores
  CONTENDED_WAIT 1 · NOT_APPLICABLE 95
  test.rr_suite_contract  CONTENDED_WAIT  cores=0.336  wall=300.191
exit 0
```

The one killed row in the local corpus is a genuine blocked wait — correctly *not* flagged. The corpus
contains no livelock to find, so this is a negative control, not a detection.

## 5. What is NOT wired — stated because I flagged others for exactly this

`livelock_class` has a gate-level adapter (`classify_gate`, `gate_is_wall_kill`,
`index_profiles_by_step`) that joins a ledger gate row to its step profile, and it is bracketed. **No
production consumer calls it yet.**

The junction is `ci-hub/validate/flake_class.py:211` and `:385`:

```python
if record.get("result") in ("fail", "timeout") and is_env_fault(record): -> env fault
```

I deliberately did not edit that module:

* It is a **two-engine parity** module (Python mirrored by Rust); changing its verdicts risks the
  parity the codebase enforces elsewhere, and I cannot run the cross-engine panel here.
* The honest change there is **not** a tweak to `is_env_fault`. The prior reclassification established
  that all six environmental signatures are banner/coredump/OOM/exit-code discriminated and fire on a
  *specific fault*, so a livelock cannot hide in them. The livelock-confusable bucket is the
  **wall-clock-kill** set (exit 124/137/143 — 25 instances: lsof ×7, SaBRe ×10, liteinst_strict ×3,
  detcore_misc ×2, build ×3), which the catalogue never called environmental. So the wiring is a **new
  bucket** for wall-kills, not a modification of the environmental one.

Until that lands, this is a classifier with a tested adapter and no caller — inert in production, and
I am naming it rather than implying coverage.

## 6. Not established

* **No validate run, no build, no network.** The classifier is pure; the live run reads existing CSVs.
* **The corpus is small and local**: 8 step-profile files, 96 rows, exactly **1** killed row. The
  livelock coordinate is taken from a task note recording the runner's own profile, not from a row I
  read. So the LIVELOCK side of the bracket is a *recorded* measurement, not one I reproduced.
* **The 25-instance wall-kill inventory in §5 is quoted** from the prior reclassification note, not
  re-counted here.
* **`classify_gate`'s name-matching is a guess about shape**: it strips a `lane:` prefix because
  validate.rs lane-prefixes gate tags. Whether ledger gate names and step-profile step names line up in
  practice was not verified against a real pair — the local corpus has no killed gate with a matching
  profile row.
