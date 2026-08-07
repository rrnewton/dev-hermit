# Test selection: reachability vs relevance — design

**Task:** `test-selection-relevance-vs-reachability-design` · **Agent:** hermit-cc (opus-5) · **2026-08-07**
**Status:** DESIGN ONLY. No code changed. Every number below is measured, with its denominator.

---

## 1. Premise check — the conclusion holds, the stated cause does not

The task's premise is that **cargo-metadata dependency closure** is the problem: it answers "could
this conceivably affect that test?", and since hermit-with-all-backends depends on essentially all
code, the honest closure is "run everything".

**Measured, that is not the live mechanism.** `hermit/ci/select-tests.rs` (invoked from
`validate.sh:4432`) performs no dependency closure at runtime. It reads a declarative map,
`hermit/ci/test-footprints.json`: 20 footprints / 59 path globs / 10 `force_full` globs /
14 `ci_irrelevant` globs. That map *is* the relevance layer the task proposes adding — it already
exists, and it is generated, not hand-written (`ci/manifest-plan/src/bin/generate-test-footprints.rs`).

**But the conclusion — selection does not usefully narrow — is confirmed, and more strongly than the
task claims.** Over the **last 40 consecutive hermit commits**, each diffed against its own parent
and run through the live selector:

| outcome | count | share |
|---|---|---|
| `full` | 28 | **70.0 %** |
| `selective` | 12 | 30.0 % |

Selected nodes against a universe of **47 nodes / 80 cells**: min 32, **median 47**, max 47.

> **Median narrowing across all 40 commits: 0.0 %.**

Even restricting to the 12 `selective` commits, the selected counts are
`[32, 33, 33, 44, 44, 44, 44, 46, 46, 46, 46, 46]` — **median 44/47 = 94 %** of the universe, best
case 32/47 = 68 % (a 32 % narrowing). So the selector's *best observed* result still runs two-thirds
of everything, and its typical result runs all of it.

### 1.1 Why it goes full — and it is not closure breadth

Top `force_full` triggers across the same 40 commits (denominator = 40):

| fires | share | path |
|---:|---:|---|
| 14 | 35.0 % | `ci/test_harness.sh` |
| 8 | 20.0 % | `ci/configure-build-jobs.sh` |
| 8 | 20.0 % | `validate.sh` |
| 6 | 15.0 % | `ci/dag/portable.json` |
| 6 | 15.0 % | `.github/workflows/ci-portable.yml` |
| 5 | 12.5 % | `ci/dag/privileged.json` |
| 4 | 10.0 % | `ci/run-with-reverie-dbi-budget.sh`, `ci/run-dag.sh`, `ci/run-node.sh`, `ci/expected-e2e-plan.json` |

Every one is **CI machinery**, caught by two coarse globs: `ci/**` and `validate.sh`. Not one is
product code, and not one is a closure-breadth effect.

The residual 94 % in `selective` mode has a different cause again: the reason string is
`e2e: all backends (core/CLI/fixture change)`. A core or CLI change legitimately fans out to every
backend cell, so selective mode narrows the *node* set barely at all.

**So there are two independent causes, and the task's framing addresses neither:**

- **(a) fail-safe coarseness** — 70 % of commits, driven by `ci/**` and `validate.sh`.
- **(b) backend fan-out** — the residual, driven by core/CLI changes reaching all backends.

---

## 2. The constraint that must survive: self-reference

`ci/**` is not merely lazy. Checked explicitly:

```
ci/select-tests.rs        force_full via ['ci/**']
ci/test-footprints.json   force_full via ['ci/**']
ci/dag/portable.json      force_full via ['ci/**']
ci/test_harness.sh        force_full via ['ci/**']
validate.sh               force_full via ['validate.sh']
```

The selector and its own policy map live inside the glob that forces full. **That is correct and
must not be relaxed:** a selector cannot be trusted to scope a change to itself, and a map that
could exempt its own edits could silently exempt anything. Any design that narrows `ci/**` must keep
this subset forcing full.

This is the distinction the current map lacks — it has one `ci/**` glob doing three different jobs.

---

## 3. Design

### 3.1 Split `force_full` by *why*, not by directory

Three classes, only one of which is a narrowing candidate:

| class | members | disposition |
|---|---|---|
| **SELF-REFERENTIAL** | `ci/select-tests.rs`, `ci/test-footprints.json`, `ci/dag/*.json` | **Always full.** Non-negotiable (§2). |
| **UNIVERSAL** | `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml`, `.cargo/**` | **Always full.** Genuinely reaches everything. |
| **RUNNER MECHANICS** | `ci/test_harness.sh`, `ci/run-node.sh`, `ci/run-dag.sh`, `ci/configure-build-jobs.sh`, `ci/expected-e2e-plan.json` | **Give each a footprint.** |

The third class is the whole opportunity: `ci/test_harness.sh` alone accounts for **14/40 = 35 %** of
all full runs, and it drives the e2e manifest nodes — it does not affect `test.detcore_unit`,
`test.regular_crates`, or the Rust unit lanes. Today it forces all 47.

**A footprint is not an exemption.** These files get mapped to the nodes they actually drive, by the
same generated mechanism as product code — never a hand-written "skip this".

### 3.2 The outer bound must be a *check*, not just a generator

The task's layer 1 (mechanical reachability as a sound outer bound) is the piece that genuinely does
not exist. `generate-test-footprints.rs` *produces* the map, but nothing *verifies* at selection time
that the selected set is a superset of what cargo reachability implies. Without that, a stale or
under-generated map silently under-selects — and under-coverage is exactly the observed historical
failure of the hand map (11 footprints / 19 globs where cargo-derived truth was 20 / 56; today's map
is 20 / 59, i.e. it has since been regenerated, which is evidence the drift is real and recurring).

Proposal: `select-tests.rs --verify-bound`, which recomputes cargo reachability for the changed
paths and asserts `selected ⊇ (reachable ∩ nodes)`. **Fails closed** — a violation forces full and
reports the escaping nodes. Run it in CI on the map itself, so regenerating produces no diff and the
bound is enforced rather than assumed.

This preserves the task's rule that narrowing never exceeds the mechanical bound, by making the
bound checkable instead of notional.

### 3.3 Relevance is generated and diffed, never asserted

Keep `test-footprints.json` a **checked-in build artifact** with a CI check that regeneration is a
no-op. That makes relevance auditable: a reviewer sees the map change in the diff, and a stale map
fails the check rather than quietly narrowing.

### 3.4 Measure the miss rate — now actually implementable

The task's validation loop was blocked on data. It no longer is. The sibling task
`ci-hub-incremental-vs-total-tracking` landed `ci-hub/validate/totality.py`, which identifies
**provably TOTAL** runs from observed node execution (37 such runs in the current 654-row ledger).
That supplies the control arm:

> For each TOTAL run, recompute what selection *would* have chosen for that commit, and compare
> against which nodes actually failed. A **miss** is a node that failed in the total run and that
> selection would have skipped.

Report with denominators, e.g. *"over N total runs, selection would have skipped M of K observed
failures; median detection latency X commits."* That converts "is our narrowing too aggressive?" from
an argument into a number. Note the honest limit: with 37 total runs in ~654, the current sample is
small, and the miss-rate estimate should carry its own confidence caveat rather than being quoted as
a rate.

### 3.5 Cadence derived, not picked

Full-run cost is measured (~180 s warm floor, set by the `strict_compat` 175 s monolith under the
`hermit_guest:1` cap). The chain-depth counter and `anchored` flag from `totality.py` give the other
half: current depth is **69 runs since the last provably-total run**, anchored at `fc49593a`
(2026-08-05T07:28:29Z). A max-depth knob should be set from the measured miss rate and that cost —
not chosen — and depth must be reported with the `anchored` flag, since an unanchored chain is a
lower bound, not a measurement.

---

## 4. What this design deliberately does not do

- **Does not relax the fail-safe.** Unknown paths still resolve to FULL; skip still requires
  positive proof-of-inert; narrowing operates only on the known-reachable set.
- **Does not exempt `ci/**` wholesale.** The self-referential subset keeps forcing full (§2).
- **Does not touch backend fan-out (cause (b)).** A core/CLI change reaching all backends may be
  *correct*; deciding whether the all-backends e2e expansion is over-broad needs per-backend failure
  correlation from §3.4, and asserting it now would be the same unmeasured guess this design exists
  to replace.

## 5. Expected effect, stated as a bound rather than a promise

Addressing §3.1 alone removes the trigger behind 14/40 = 35 % of full runs, and the runner-mechanics
class collectively accounts for 30/40 trigger-fires (paths can co-fire, so that is fires not
commits). It cannot be converted into a predicted narrowing percentage without knowing how many of
those commits *also* touch a universal or self-referential path — which §3.4's harness would measure
directly. Stating a headline "X % faster" here would be exactly the asserted-not-measured claim the
task is trying to eliminate.

## 6. Reproduction

```bash
cd ~/work/dev-hermit/hermit
# decision + reasons for one commit
git diff --name-only <sha>~1 <sha> | xargs ./ci/select-tests.rs --files --format json
# the 40-commit distribution and force_full trigger table
#   (loop the above over `git log --format=%H -40`, tally .decision / .node_count / .reasons)
python3 -c "import json;d=json.load(open('ci/test-footprints.json'));print(len(d['footprints']),len(d['force_full']))"
```

Universe: 47 nodes / 80 cells (`./ci/select-tests.rs --files ci/dag/portable.json --format json`).
