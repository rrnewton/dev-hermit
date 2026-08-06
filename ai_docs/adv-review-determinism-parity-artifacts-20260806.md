# Adversarial review — determinism & backend-parity artifacts

**Task:** `adv-review-determinism-parity-artifacts` (P1, trustworthy-envelope pass #280)
**Date:** 2026-08-06 · **Reviewer:** hermit-design · **Method:** plant-a-violation + positive control per guard
**Bound to:** hermit `f89c69766` · reverie `025d3780` · Local only, no egress, no validate run.

Two of the seven artifacts in scope are **my own work from earlier today**. I have marked both harder
than the rest, and both come out worse than the ones I did not write.

---

## 0. Verdicts

| # | Artifact | Verdict | Denominator | Brackets run |
| --- | --- | --- | --- | --- |
| 1 | backend-parity catalog guard (incl. fchown-DBI gap) | **REAL** — live in CI | 28 cases × 3 backends = **84 cells**; the CI DAG node covers **28** (dbi only) | 4 planted / 1 control / plant deleted clean |
| 2 | kvm-stdout-tty parity | **UNVERIFIABLE HERE** | — | none possible (needs `/dev/kvm` + a run) |
| 3 | kvm-L3-detlog-stack | **STRUCTURALLY IMPOSSIBLE as stated** | — | see §4 |
| 4 | register-file-hashing-verify | **NOT LOCATED** | — | none |
| 5 | no-worse-ratchet | **REAL BUT SOCIAL** — no numeric comparison exists | 84 cells | reasoned from source; see §6 |
| 6 | soft-green-vs-hard-green *(mine)* | **REAL logic, INERT in production** | 585 ledger rows classified; **0 consumers** | 27 both-sided, but see §7 |
| 7 | DBI from_guest exit-RPC fix *(mine)* | **INERT BY ABSENCE** — no code exists | — | §8 |

Score: **1 of 7 is a live, bracketed, enforcing guard.** Two are mine and neither is wired.

---

## 1. Artifact 1 — the backend-parity catalog guard: REAL

`tests/backend-parity/run_matrix.py::validate_catalog` (`:348`) enforces five invariants on the
known-gap catalog. It is called from `main()` at `:904`, **before** the backend loop — so it runs on
every invocation, including the CI one, not only under `--check`.

**Wiring (a guard nobody calls is inert):**
* `ci/dag/portable.json:452` — `run_matrix.py --hermit target/release/hermit --backend dbi --strict --require-backend`
* `validate.sh:2261`, `Makefile:13`, and `ci/manifest-plan/src/main.rs:910` (manifest runner entry)

### Both sides, measured

Positive control — the real catalog, unmodified:

```
$ python3 tests/backend-parity/run_matrix.py --check     ; echo $?
RATCHET ptrace: 28/28 (100.0%)   RATCHET dbi: 26/28 (92.9%)   RATCHET kvm: 27/28 (96.4%)
RATCHET-L2 ptrace: 28/28  dbi: 25/28  kvm: 26/28 [kvm detlog=0 guest-visible=26]
0
```

Planted violations, each in a `/tmp` copy (the primary was never modified):

| Planted violation | Refused? | Message |
| --- | --- | --- |
| gap naming a case that does not exist | yes | `known gap has no case implementation: 'no_such_case_xyz'` |
| gap with an empty reason | yes | `hello_stdout/dbi: known gap needs a reason` |
| gap on `ptrace` (the reference backend) | yes | `invalid known-gap backend: 'ptrace'` |
| L1 gap absent from `L2_GAPS` | yes | `hello_stdout/dbi: an L1 gap must also be an L2 gap` |

**Exit code, measured unpiped:** planted → **2**, control → **0**.

> Trap worth recording: my first measurement read `rc=0` for every planted case because `$?` came after
> a `| tail` — the pipeline's exit code, not python's. That is the same pipe-swallows-rc trap recorded
> earlier in this session's green-inheritance notes. **A guard that appears inert may just be a
> mis-measured exit code**; measure unpiped before concluding.

Plant deleted cleanly: the restored file is byte-identical to the primary (`diff -q` silent) and the
control passes again.

---

## 2. Artifact 1, sub-finding A — the denominator counts cells that never ran

`run_matrix.py:951-963`:

```python
if is_gap and not args.probe_gaps:
    print(f"GAP {backend}/{name}: {gap_reason}")
    results.append({... "result": "GAP", "seconds": "0.000"})
    continue
```

**A known-gap cell is not executed at all by default.** So `RATCHET dbi: 26/28` describes 26 cells that
ran and passed out of 28 *listed* — the 2 gap cells contributed `seconds=0.000` and no execution. This
is the same shape as a green with `executed_tests = 0`: the rate's denominator includes cells that
produced no observation.

The rate is not wrong, but it is easy to read as "92.9% of the corpus was verified", which it is not.
Anyone quoting a parity percentage should say **ran/listed**, e.g. "dbi 26 ran-and-passed of 28 listed,
2 not executed (known gaps)".

---

## 3. Artifact 1, sub-finding B — `--check` prints ratchet rates without running anything

The rates in §1's positive control were produced by `--check`, whose own help says "without running
guests". Those numbers are **gap-list arithmetic** — `28 − len(gaps for that backend)` — not
measurements. They will read identically on a box with no DynamoRIO, no `/dev/kvm`, and no hermit
binary at all.

That is legitimate as a catalog self-check. It becomes an over-claim the moment a `--check` figure is
quoted as a parity result. Recommend the `--check` output label them, e.g. `RATCHET (expected from
catalog, no guests run)`.

**Sub-finding C — XPASS never fails.** Under `--probe-gaps` a gap cell that passes is relabelled
`XPASS` (`:967-969`), but `failures` is only incremented for `not is_gap and status == "FAIL"`
(`:981-982`). So a gap that has been silently fixed is *reported* and never *enforced* — non-strict
xfail. A stale gap entry therefore persists indefinitely, and while it persists it suppresses execution
of that cell (§2), so a later genuine regression in the same cell would not be caught.

---

## 4. Artifact 3 — a KVM L3/detlog claim is structurally impossible in this matrix

`run_matrix.py:54-60`:

```python
L2_RANK    = {"gap": 0, "guest": 1, "detlog": 2}
L2_ALLOWED = {"ptrace": {"detlog"}, "dbi": {"detlog", "gap"}, "kvm": {"guest", "gap"}}
```

KVM is **capped at `guest`** (guest-visible stdout+exit parity) and can never record a `detlog`
witness — the comment says the concurrent verify path cannot emit one. The positive control confirms it
live: `RATCHET-L2 kvm: 26/28 [detlog=0 guest-visible=26]`.

So "kvm-L3-detlog-stack" cannot be substantiated *through this matrix*: L3 presupposes L2, and KVM's L2
here is the weaker guest-visible kind. Either the artifact lives somewhere else entirely, or the claim
needs restating. I could not verify it either way without `/dev/kvm` and a run.

---

## 5. Artifacts 2 and 4 — not assessable here

* **kvm-stdout-tty parity**: the only thing I located is the guest fixture
  `tests/rust/interrogate_tty.rs`. Exercising it needs `/dev/kvm` and a hermit run. **UNVERIFIABLE HERE.**
* **register-file-hashing-verify**: `grep -rln "user_regs_struct\|register.*hash\|regs_hash"` across
  `hermit-verify/src` and `detcore` returned nothing under that description; the nearest file is
  `hermit-verify/src/trace_replay.rs`. **I could not locate the artifact**, so I cannot say whether it
  is real, inert, or misnamed. Flagging rather than guessing.

---

## 6. Artifact 5 — the "no-worse ratchet" is enforced by review, not by comparison

`append_parent_scorecard` **appends** a row to the parent scorecard. It contains no `compare`,
`baseline`, `previous`, `regress`, or `raise` — nothing reads the last recorded rate and fails when
today's is lower.

What *is* enforced: a **newly failing non-gap cell** increments `failures` and, with `--require-backend`
on the DAG node, turns the node red. That is real regression protection for a cell that breaks.

What is **not** enforced: the ratchet *number*. Moving a broken cell into `L1_GAPS`/`L2_GAPS` with a
reason drops the rate (26/28 → 25/28) and CI stays **green**, because gap cells cannot fail. The
catalog guard makes that move *visible* — it demands a real case name and a non-empty reason, and it
lands as a reviewable diff — but the stop is a human noticing a new gap entry, not a mechanical
comparison.

**Verdict: REAL BUT SOCIAL.** Its strength equals the attention of whoever reviews the gap-list diff.
A genuine ratchet would record the rate and refuse a decrease absent an explicit override — the same
shape as the gate floors registry.

---

## 7. Artifact 6 — my own soft-green work: REAL logic, INERT in production

`ci-hub/validate/green_class.py` + 27 both-sided brackets, all passing, and it classifies all 585 live
ledger rows. The logic is real. **It is also called by nothing.**

Measured: `grep -c accepts_green_class ci-hub/validate/qualifying-receipt.json` → **0**. The predicate
was never changed, no consumer imports `green_class`, and no landing path consults it.

So the honest verdict on my own artifact is **INERT**: a bracketed classifier that no decision depends
on cannot fail closed on anything. I documented this in §12 of that artifact, but documenting inertness
does not make it enforcement — if a soft producer landed tomorrow, nothing in this module would stop a
soft row from qualifying, because the landing predicate still has no class clause.

The 27 brackets are also weaker than they look in one specific way: they test `derive_class` and
`classify_delta` **in isolation**, never through a real consumer. The negative bracket I ran for the
*coverage* shape earlier (planted `absent_nodes: 1` → row silently dropped by the real `ci-hub
validate-status`) is the standard this should have met and did not.

---

## 8. Artifact 7 — my own DBI exit-RPC "fix": does not exist as code

`grep -rn "SO_RCVTIMEO\|exit_rpc_timeout" reverie/reverie-dbi/src/` → **no hits**. What I produced was
a source audit and a design; there is no bounded wait, no counter, no test. Listing it among
"determinism artifacts that hold" would be a category error.

**Verdict: INERT BY ABSENCE.** The audit's substantive contributions were negative findings — that the
premise (exit-RPC deadlock) describes a symptom of a poisoned scheduler mutex, and that the
coordinator-side teardown answers already exist on main while the guest-side wait is unbounded. Those
stand as findings; none of them is a guard.

---

## 9. What a reader should take from this

The one guard I could fully bracket is genuinely good: five invariants, four distinct refusals, correct
exit codes, live on the CI path, and it makes every gap a named, justified, reviewable entry. Its
weaknesses are all of one kind — **the gap list is an unexecuted region of the matrix that only
grows unless someone looks**: gap cells do not run (§2), `--check` rates are arithmetic (§3), XPASS is
never enforced (§3C), and the rate is never compared (§6).

Recommended, in order of value:

1. **Make XPASS fail under `--probe-gaps`** and run `--probe-gaps` on a schedule. That converts the gap
   list from a growing liability into a self-cleaning one.
2. **Label `--check` output** as catalog-expected, not measured.
3. **Report parity as ran/listed**, never as a bare percentage.
4. **Compare the scorecard rate** against the last recorded one and refuse a decrease without an
   override — turning §6 from social into mechanical.

---

## 10. Not established

* No hermit run, no `/dev/kvm`, no validate, no network. Artifacts 2, 3, 4 are unassessed for that
  reason and are reported as such rather than assumed sound.
* The 84-cell denominator is `len(case_catalog) × len(BACKENDS)` read from the catalog at
  `f89c69766`; I did not execute the matrix, so I have not observed any cell.
* §6's "no comparison exists" is from reading `append_parent_scorecard` and grepping for comparison
  vocabulary. A comparison implemented in a *parent-side* consumer of `compat-envelope/scorecard.csv`
  would not have been caught by that search.
* Artifact 4 may exist under a name I did not guess; "not located" is not "does not exist".
