# Green-Inheritance Anchor Selection — design, implementation, and measured decay

**Task:** `green-inheritance-test-selection-anchored-on-full-main-validates` (P0, OWNER)
**Date:** 2026-08-05 · **Author:** hermit-design · **Status:** logic implemented + bracketed locally; no validate run (box-wide egress 403, host-zero directive)
**Implementation:** `ci-hub/validate/anchor_select.py` · **Brackets:** `ci-hub/validate/test_anchor_select.py` (25/25 pass)
**Evidence base:** hermit `b64d893ae9ea6404472eae9cb86102d91ec642ef`; ledger `ignored/validate-run-ledger.jsonl` @ 585 rows, 2026-08-05

---

## 0. What this document decides

An **incremental** (selective) validate run does not test the whole suite. It tests only what the
change can affect and **inherits** the rest of its green from an earlier run — the **anchor**. Four
questions had to be answered before that inheritance is trustworthy:

| Question | Answer |
| --- | --- |
| Which commit may be the anchor? | The **nearest** ancestor of the target carrying a **qualifying full-profile** receipt that clears every gate floor. |
| Can an incremental anchor on an incremental? | **No — one hop only.** Enforced structurally by `selection_mode == "full"` in the shared predicate. |
| What diff drives the selection? | `TIP vs ANCHOR`, **two-dot**, never `tip vs tip-1` and never three-dot. |
| When must the anchor be refreshed? | **Derived, not picked:** the instant the selector's decision over the cumulative window is `full`, because the saving is then exactly zero. Measured: that happens at **d = 1** on current main. |

The headline consequence for scheduling is at the end of §5: **on current main, 1-hop selection saves
nothing at any distance**, and three defects in the existing wiring (§7) would each have let an
incremental run inherit green from something that was never validated.

---

## 1. The three rules and the evidence for each

### Rule 1 — ONE HOP ONLY

> *"We could start to CHAIN an incremental hard green on another incremental run, but I DON'T TRUST
> THE INFRA ENOUGH FOR THAT YET. Better to do incremental runs based ONLY ON PREVIOUS FULL RUNS for
> now — 1 HOP."* — owner, 2026-08-04

Chaining **compounds** the risk that the footprint map is wrong. Each hop multiplies the chance that
a test which should have run was skipped, and after N hops nothing in the chain was ever fully
validated: the green becomes a claim about a claim. One hop bounds the error to a **single selection
decision against a known-full baseline**.

**This rule is already enforced by construction, and that is worth stating precisely because it is
the property most likely to be lost silently:**

- Producer: `hermit/validate.sh` sets `VALIDATION_SELECTION_MODE=selective` for every `--selective`
  / `--since-green` / `--shallow-select` run (validate.sh:529–540) and writes it into the receipt
  (validate.sh:1510).
- Predicate: `ci-hub/validate/qualifying-receipt.json` requires `selection_mode: "full"`.
- Consumers: `ci-hub/lib/validate_status.rs:151` (`is_clean_full_coverage`) and `:225`
  (`is_clean_full_pass` → `qualifying_receipt::row_qualifies`).

So a selective receipt is not merely a *weaker* anchor — it is **not an anchor at all**, and there is
no second hop available to take. `anchor_select.py` emits `hop: 1` in the record so the property is
**observed in the artifact**, not assumed by the reader, and
`test_anchor_select.py::test_selective_receipt_refused` pins it so a future relaxation of
`selection_mode` breaks a test instead of silently enabling N-hop chaining.

### Rule 2 — the anchor predicate is the shared qualifying-receipt predicate, never `result == "pass"`

> *"THE LEDGER HOLDS 92 `portable-strict-compat-only` RECEIPTS THAT SAY `result=pass` WITH 2 CHECKS —
> keying on `result` would anchor on those and manufacture green for code never validated anywhere in
> the chain."* — owner

Measured on the live ledger (585 rows, 2026-08-05; `ignored/anchor-bracket.py`):

| Filter | Rows | Distinct commits |
| --- | ---: | ---: |
| `result == "pass"` and `commit != "unknown"` (what `validate.sh` actually uses today) | **346** | — |
| …of which **qualify** under `qualifying-receipt.json` | **107** | 105 |
| …**disqualified** | **239 (69%)** | — |

Disqualification reasons: `profile != full` **148** (of which `portable-strict-compat-only` 164 rows
appear in the pass population overall), `commit_anchored != true` **44**, pre-count receipt cannot
prove nonzero execution **39**, count-capable coverage unsatisfied **8**.

The predicate is **loaded, not restated**, in `anchor_select.py` — it reads
`qualifying-receipt.json`, the same file the Rust and jq consumers read, so tightening the floor
remains one edit.

### Rule 3 — the anchor must be an **ancestor** of the target (new finding)

`select-tests.rs:636` computes the local delta as:

```rust
git diff --name-only {baseline}...HEAD      // THREE dots
```

Three-dot diff means *"changes on HEAD's side since the merge-base"*. For a **non-ancestor** baseline
that **silently relocates the effective anchor to `merge-base(baseline, HEAD)`** — a commit that
carries **no receipt at all**. The run would then inherit green from a commit nothing ever validated,
and nothing in the output would say so.

This is not hypothetical. Of the **105 distinct qualifying commits** in the ledger, only **11 are
ancestors of hermit HEAD**; **86 are not** (PR branches and other lines), and 8 are not present
locally. A selector that picks by recency alone lands on a non-ancestor most of the time.

`anchor_select.py` therefore requires `git merge-base --is-ancestor <anchor> <target>` **before**
accepting a candidate, and then diffs with **two dots**. Two-dot is deliberate: against a verified
ancestor it is identical to three-dot, but it cannot succeed against a non-ancestor by quietly
relocating — if ancestry is ever lost it returns the honest **larger** set (fail-safe direction),
which `test_two_dot_diff_against_non_ancestor_is_wider_not_narrower` pins.

---

## 2. The algorithm

Input: `target` (commit/ref), the ledger, the shared predicate, the floor registry, the selector.

1. **Resolve** `target` to a 40-hex commit. Unresolvable ⇒ `REFUSED`.
2. **Qualify** every ledger row through `qualifying_receipt.json`
   (`commit_anchored ∧ ¬tree_dirty ∧ profile=full ∧ selection_mode=full ∧ result=pass ∧
   failures≤0 ∧ executed_tests>0 ∧ (schema≥5 ⇒ per-node coverage satisfied)`). Keep the newest
   qualifying receipt per commit, so the record cites the receipt actually relied on. Count and
   report every refusal reason — a refusal must be auditable, not a bare `False`.
3. **Bound by ancestry.** Drop candidates absent from the local object store; drop candidates that
   are not ancestors of `target`. Compute `first-parent` distance for the rest.
4. **Apply the gate-schema floor** by delegating to `ci-hub/validate/gate_floors.py --head <sha>
   --no-fetch` (exit 0 = clears all floors). Floor logic is not reimplemented here.
5. **Pick the NEAREST** surviving ancestor (tie-break: newer receipt). *Nearest, not newest-by-time*:
   the selection diff is TIP-vs-ANCHOR, so the nearest qualifying ancestor minimises the diff and
   therefore maximises the saving. On main's first-parent line nearest == newest; for a PR head they
   differ, and nearest is the one that is correct **and** cheaper.
   No anchor survives ⇒ `NO-ANCHOR` (exit 4, run FULL).
6. **Diff** `git diff --name-only <anchor>..<target>` (two-dot), plus staged/unstaged/untracked when
   `--include-dirty` (the local `validate.sh` case, where the tree is ahead of the commit).
7. **Select** by piping that file list to `hermit/ci/select-tests.rs --files - --format json`.
8. **Verdict + obligation** (§3). The re-anchor cause is read from the **selector's own `reasons`**,
   never from a mirrored copy of the force_full policy — see §6 for why that distinction was not
   theoretical.

### Fail-safe direction

Every non-`0` exit means **run the full lane**. There is no path — tool crash, timeout, unreadable
ledger, selector error, missing anchor, git failure — on which a failure of this tool produces a
*smaller* test set. The only decision that runs fewer tests than a change might need is
`INHERIT-CLEAN` (selector `skip`), and that requires positive proof that *every* changed file is
inert; §7-C is the one place that proof is currently wrong.

---

## 3. Verdicts, exit codes, and the receipt obligation

| Verdict | Exit | Meaning | Action |
| --- | ---: | --- | --- |
| `INHERIT-SELECTIVE` | 0 | anchor found; selector returned a subset | run the subset; inherit the rest |
| `INHERIT-CLEAN` | 0 | anchor found; every changed path provably inert | inherit wholesale, run nothing |
| `RE-ANCHOR-NOW` | 3 | anchor found but selected set **is** the full set | run FULL; that run becomes the new anchor |
| `NO-ANCHOR` | 4 | no qualifying full-green ancestor | run FULL |
| `REFUSED` | 2 | bad input | run FULL |
| `ERROR` | 5 | selector/git failed | run FULL |

An inherited green is only auditable if it **carries the conditions it inherited under**. On an
`INHERIT-*` verdict the tool emits the record the consuming receipt must embed:

```json
"inherited_green": {
  "hop": 1,
  "anchor_sha": "<40-hex>",
  "anchor_receipt_finished_at": "<iso8601>",
  "anchor_profile": "full",
  "anchor_selection_mode": "full",
  "distance_commits": 19,
  "diff_files": 123,
  "selector_decision": "selective",
  "selected_nodes": 44,
  "universe_nodes": 47
}
```

A later reader can re-derive the whole decision from this: which receipt was trusted, over what
distance, what the selector concluded, and how much of the universe actually ran. A bare
`"inherited": true` flag would be exactly the kind of unbound proxy this design exists to remove.

---

## 4. Where it plugs in

| Layer | File / symbol | Change |
| --- | --- | --- |
| **Anchor authority** | `ci-hub/validate/anchor_select.py` (new) | the one verifier; every consumer calls it |
| Shared predicate | `ci-hub/validate/qualifying-receipt.json` | **unchanged** — loaded, not copied |
| Floor registry | `ci-hub/validate/gate_floors.py --head --no-fetch` | **unchanged** — delegated |
| Branch-wide green scan | `ci-hub/lib/history_queries.rs:173` `newest_green` | complementary: newest green *on a branch*. Anchor selection needs newest green **that is an ancestor of an arbitrary target** and **nearest**, which `newest_green` does not express. Eventual home is `ci-hub newest-green --ancestor-of <sha> --nearest`, sharing this algorithm. |
| **Local producer** | `hermit/validate.sh:4319` `resolve_selective_baseline` | **replace the body** with a call to `anchor_select.py --target HEAD --include-dirty --json`; consume `anchor.sha`; on any non-zero exit run the full lane (that branch already exists at `run_selective_suite`:4376) |
| Selector | `hermit/ci/select-tests.rs:363` `select`, `:635` `local_changed_files` | unchanged for the ci-hub path (files are piped in). If `--baseline` keeps its three-dot diff, add an ancestry assertion there too. |
| DAG execution | `hermit/validate.sh:4355` `build_selected_portable_dag` → `RUN_DAG_FILE_OVERRIDE` → `ci/run-dag.sh` | unchanged — it already consumes a node list |
| Hosted CI | `.github/workflows/ci-portable.yml` | **still unwired** (`ci/test-selection.md`: "no workflow yet gates its matrix on it"). The under-selection defect in §7-C is therefore **latent, not live** — it cannot mint a fake green today, and would the moment wiring lands. |

The plug-in point is deliberately **one function body**. `resolve_selective_baseline` is the only
place that picks the anchor, so replacing it is what makes "one verifier per authority" true rather
than aspirational.

---

## 5. When to refresh the anchor — derived, not picked

The owner's constraint: *derive the refresh trigger from the measured saving, or it becomes another
underived constant.* Two signals, and only one of them exists today.

### Hard trigger (exact, available now)

> **Re-anchor the instant the selector's decision over the cumulative window is `full`.**

This is derived from the quantity that matters, not from a commit count: `decision == full` means the
selected set *is* the full set, so the saving is exactly **zero** and an incremental run costs a full
one while providing weaker evidence. It is also **monotonic**: `force_full` is a union over the
window, so once any commit since the anchor touches a force_full-class path, every greater distance
also forces full. The decay is a **cliff, not a curve**.

### Measured cliff, fresh at the live anchor

Anchor `d53550510d1e` → target `b64d893ae9ea`, 19 first-parent commits, universe 47 nodes / 80 cells /
11 shards (`anchor_select.py --decay-curve`):

| d | diff files | decision | nodes | cells | cause (selector's own reason) |
| ---: | ---: | --- | ---: | ---: | --- |
| 1 | 32 | full | 47/47 | 80/80 | `.github/workflows/ci-portable.yml → force_full` |
| 5 | 47 | full | 47/47 | 80/80 | same |
| 10 | 122 | full | 47/47 | 80/80 | same |
| 19 | 123 | full | 47/47 | 80/80 | same |

Every distance d = 1…19 forces full. **Realized 1-hop saving on current main: zero.**

This independently replicates the earlier measurement (task note 2026-08-04 16:58) at a *different*
anchor and a *different* cause — that window was anchor `e8a0d8d3`, 48 commits, cause
`ci/run-node.sh`. Two windows, two unrelated force_full paths, same cliff at d = 1. The contrast that
makes the owner's point quantitative: over the earlier 48-commit window, **per-commit** (tip vs
parent) selection gave full 34 / selective 13 / skip 1 — i.e. ~29% of commits would save something
when tested against their own parent, and **0%** save anything when tested against a full-run anchor.
TIP-vs-ANCHOR is a categorically bigger diff than TIP-vs-PARENT, and that is the whole cost of the
1-hop rule.

### Soft trigger (correct, blocked)

`selected_wall / full_wall ≥ θ` is the signal that matches the cost model — re-anchoring pays one
full run, amortised over the K selective runs it then serves, so keeping the anchor is worth it while
`selected_wall < full_wall − full_wall_median/K`. Measured over the **n = 107 qualifying full-green
receipts** that carry a wall time: median **500 s**, mean 539 s, p25 401 s, p75 683 s.

**It cannot be computed today: no per-node wall durations are recorded anywhere.** `ci/test-selection.md`
states power-to-weight "has no duration data yet" and uses explicitly unmeasured ordinal weights; the
ledger records whole-run `real_seconds` only. The node/cell fractions this tool reports are an
**optimistic proxy** — they weight every node equally while full-run wall is dominated by a few heavy
e2e cells, so a change selecting one heavy cell reads as `node_fraction ≈ 0.1` while actually costing
~0.6 of the wall. The tool therefore **reports the fractions and refuses to gate on them**, emitting
`wall_fraction: null` with its blocker named.

**Shared unblocker:** emit per-node duration into the receipt. It is the same missing input that
blocks power-to-weight ranking, so one change unblocks both.

---

## 6. A proxy that was wrong on its first live run

The first draft named the re-anchor cause by matching changed paths against a **mirrored copy** of
hermit's `force_full` globs. On the first live invocation it reported
`.github/workflows/ci-portable-autoretry.yml` as the cause — a path the selector does **not** force
(only three workflow files are in `force_full`; the rest fall through `.github/**` to
`ci_irrelevant`). The mirrored list was a proxy for the selector's decision, it disagreed with the
decision it was explaining, and it did so immediately.

The copy is deleted. The cause is now read from the selector's own emitted `reasons`, so the reported
cause and the decision it explains come from the same evaluation. The live run now correctly names
`.github/workflows/ci-portable.yml → force_full`. This is recorded here rather than quietly fixed
because it is a compact instance of the failure mode the whole design targets: *a second copy of a
rule is a proxy for the rule.*

---

## 7. Defects found in the existing wiring

### A. `resolve_selective_baseline` picks the anchor with a bare `result == "pass"` (LIVE, load-bearing)

`hermit/validate.sh:4319`, ledger fallback at :4333–4341:

```bash
sha=$(jq -r --arg slot "$VALIDATION_SLOT" '
    select(.result == "pass" and .commit != "unknown" and .slot == $slot)
    | .commit' "$VALIDATION_LEDGER_FILE" | tail -n 1)
```

This is the **only** place the anchor is chosen, and it checks **none** of: `profile`,
`selection_mode`, `commit_anchored`, `tree_dirty`, `executed_tests`, coverage, gate floor, or
ancestry. It also takes `tail -n 1` — last *line appended*, not newest by `finished_at`.

Consequences, measured: **239 of 346** rows it accepts do not qualify; 148 are non-`full` profiles
(compat-only runs with as few as 2 checks); 44 were never commit-anchored (a dirty-tree run — green
claimed for a tree that never existed as a commit); and nothing restricts the pick to an ancestor, so
combined with the three-dot diff at `select-tests.rs:636` the effective anchor can silently become an
unvalidated merge-base.

**The same file already contains the rigorous query.** `cache_lookup_record` (validate.sh:621), which
guards the *result-cache* authority, checks `commit_anchored`, `tree_dirty`, `selection_mode == "full"`,
`executed_tests > 0`, and `gates_run >= gates_expected`. Two queries over the same ledger, in the same
script: the cache one is rigorous, the safety-critical anchor one is bare. This is precisely the
"one verifier per authority, called by every consumer" violation — and note the cache query is
**tree-keyed**, so it cannot be reused as-is; the anchor needs its own commit-keyed verifier, which is
what `anchor_select.py` is.

*Calibration:* the row `tail -n 1` currently returns happens to be a qualifying full receipt
(`fc49593ac21c`, profile=full, selection_mode=full). The defect is **latent-by-luck**, not
currently firing. It has no binding that keeps it that way.

**Fix:** replace the body of `resolve_selective_baseline` with a call to `anchor_select.py`.

### B. `--shallow-select` pins the baseline to `HEAD~1` with no receipt requirement (LIVE)

`hermit/validate.sh:4322–4327`:

```bash
if ((SHALLOW_SELECT == 1)); then
    sha=$(git rev-parse --verify HEAD~1 2>/dev/null) || return 0
```

`HEAD~1` is **not an anchor** — it is whatever commit happens to precede this one, with no requirement
that it was ever validated at all, let alone fully. This is exactly the construction the 1-hop rule
forbids, in its strongest form: not "chained onto an incremental" but "chained onto nothing".

**Fix (either is acceptable):** require `HEAD~1` to itself carry a qualifying full receipt — at which
point `--shallow-select` is just the ordinary anchor path that happened to find an anchor at
distance 1 — or remove the flag. Do not leave a mode whose baseline is unconditioned.

### C. Six of nine workflow files classify as `ci_irrelevant` ⇒ `skip` (LATENT, re-confirmed at HEAD)

`hermit/ci/test-footprints-policy.json` lists three workflow files in `force_full` (:21–23) and
`.github/**` in `ci_irrelevant` (:39). The repository has **nine** workflow files. The other six
match `.github/**` and are classified inert, and since the `ci_irrelevant` branch
(`select-tests.rs:414`) leaves `all_inert = true`
for a `ci_irrelevant` hit, a change touching **only** such a file yields `Decision::Skip` — **zero
tests**.

Re-confirmed live at `b64d893ae9ea` by direct invocation:

| changed file | decision | nodes |
| --- | --- | ---: |
| `.github/workflows/ci-dag.yml` | **skip** | 0 |
| `.github/workflows/validation-levels.yml` | **skip** | 0 |
| `.github/workflows/ci-portable.yml` (control, in `force_full`) | full | 47 |
| `detcore/src/scheduler.rs` (control) | selective | 46 |
| `README.md` (control, genuinely inert) | skip | 0 |

`ci/test-selection.md:78` asserts "the three real workflow files are force_full", which contradicts
the nine-file reality; a newly added workflow silently inherits `skip` classification.

**Fix:** replace the three named entries in `force_full` with `.github/workflows/**`, then regenerate
`ci/test-footprints.json` (the generator copies `force_full`/`ci_irrelevant` verbatim). `.github/**`
may stay in `ci_irrelevant` — `force_full` is evaluated first (`select-tests.rs:390` before `:414`),
so `dependabot.yml` and issue templates correctly remain inert. Add a `self_test` case
(`select-tests.rs:960`) asserting a workflow change ⇒ `full`, and correct the doc sentence.

*Calibration:* **latent, not live.** Selection is not wired to hosted CI, so this cannot mint a fake
green today. It becomes exploitable the moment wiring lands, which is why it blocks that wiring rather
than the current state.

---

## 8. What is **not** established

- **No end-to-end mutation bracket was run.** The safety crux — plant a breaking change in file F,
  confirm every test that *should* fail is *selected* and *does* fail — requires a slot and a real
  build (~528 s median). Egress is down and a host-zero directive is in force; no validate was
  launched. Selection-level coverage was verified statically and by direct selector invocation only.
  The residual risk is a `ci_irrelevant` or over-broad-glob file with a **non-`.rs` runtime consumer**
  (a shell harness or guest program reading a doc), which has not been swept.
- **No wall measurement of a selective run.** The ledger holds exactly one `profile=selective` row in
  585 (a **fail** at 237 s, pre-schema: its `selection_mode` is absent), so the selected-vs-full
  **wall** comparison — the third of the owner's three numbers — has no clean data point at all. The other two (distance, selected-vs-full counts) are measured above. This is
  the same blocker as the soft re-anchor trigger.
- **Fixes A, B, C are specified, not landed.** They touch `hermit/`, which needs a slot and a PR;
  egress is down.

---

## 9. Reproduction

```bash
# anchor + selection + decay for the current HEAD (read-only, no validate, no network)
python3 ci-hub/validate/anchor_select.py --target HEAD --json

# the decay curve: one point per distance from the anchor (deliverable 4)
python3 ci-hub/validate/anchor_select.py --decay-curve
python3 ci-hub/validate/anchor_select.py --decay-curve --anchor d53550510d1e

# brackets: 25 tests, throwaway ledger + throwaway git repo, no side effects
python3 ci-hub/validate/test_anchor_select.py
```

The refusal counts behind §1 Rule 2 come out of `--json` as `refusal_reasons`,
`candidates_non_ancestor`, `candidates_not_present_locally`, and `candidates_below_floor`. The raw
`result == "pass"` population count (346) came from a one-off census left at
`ignored/anchor-bracket.py` — machine-local and not durable; the durable tools are the two
`ci-hub/validate/` files.

Live output at `b64d893ae9ea` (2026-08-05):

```
verdict: RE-ANCHOR-NOW
ledger:  585 rows, 105 qualifying receipts, 6 eligible anchors
         (non-ancestor 86, absent 8, below-floor 5)
anchor:  d53550510d1e at first-parent distance 19 (hop=1)
         profile=full selection_mode=full executed=786
diff:    123 files (TIP vs ANCHOR, two-dot)
select:  decision=full nodes=47/47 cells=80/80
cause:   .github/workflows/ci-portable.yml → force_full
action:  run FULL now: the selected set IS the full set, so the anchor buys nothing
```

---

## 10. Recommended order of work

1. **Fix C** (`.github/workflows/**` → `force_full`) — it is the only defect that can produce a
   *smaller-than-correct* test set, and it blocks hosted-CI wiring.
2. **Fix A** (`resolve_selective_baseline` → `anchor_select.py`) — closes the anchor authority.
3. **Fix B** (`--shallow-select`) — remove or condition it.
4. **Per-node duration emission** in the receipt — unblocks both the wall-based re-anchor trigger and
   power-to-weight.
5. **The end-to-end mutation bracket**, once a slot and a quiet host are available.
6. **Only then** wire hosted CI to the selector.

**Engineering call for the owner, stated plainly:** 1-hop test selection yields *zero* saving on
CI-machinery-heavy windows, which is what recent main is — two independently measured windows both
hit the cliff at d = 1. Its value is confined to pure-product windows before the first force_full
commit. The mechanism is worth completing for correctness (the anchor authority is load-bearing for
the clean-rebase soft-inherit work too), but it should not be scheduled as a throughput win on
current evidence. The zero-diff clean-rebase case
(`soft-inherited-validation-across-clean-rebase`, hermit-243) is the sibling that *does* save, and its
soft-green state schema should be shared with this one, not forked.
