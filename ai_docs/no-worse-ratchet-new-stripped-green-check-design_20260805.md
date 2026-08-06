# NO-WORSE RATCHET — design of the check that fails a newly-added strip

**Task:** `no-worse-ratchet-during-sprint-no-new-stripped-greens` (P0, `quality/no-worse-ratchet`)
**Author:** hermit-clone (opus-5), 2026-08-05 · **Local design/analysis only**: no validate-run, no
egress, no product file modified. **No green claimed** — nothing built or run under test.
**Ties:** `verify-tightening-high-confidence-compat-scorecard` (25-site audit),
`canonicalize-dont-strip-verify-must-preserve-distinguishability` (#1595), soft-vs-hard-green.
**Deliberately out of scope:** the full repair (`green-reset-after-landing-sprint-…`, backlogged).

---

## 0. The enabling fact: #1595 has LANDED

`ComparisonSpec` is on hermit `main` (`hermit-cli/src/bin/hermit/verify.rs:130-260`), carrying
`{strictness, compare_logs, strip_lines, canonicalize_addresses, full_trace, exact_remainder,
stripped_prefixes, canonicalizations, ignore_lines, skip_commit, skip_detlog}` plus a 10-clause
`is_bitwise_parity()`. The task named this as the mechanism that makes the ratchet checkable rather
than remembered — **it now exists, so the ratchet is buildable today.**

The design insight that follows: **`stripped_prefixes` and `canonicalizations` are already a
machine-readable, versioned strip inventory that ships with every verdict.** No new manifest format
needs inventing. A newly-added strip is necessarily one of:

- **(a)** a new token in `stripped_prefixes` / `canonicalizations`;
- **(b)** a flip of one of the six boolean fields, or a clause dropped from `is_bitwise_parity()`;
- **(c)** an erasure that registers **no token at all** — the dangerous, undeclared case.

(a) and (b) are cheap static checks. (c) is the one that needs a behavioural test, and it is not
hypothetical: the audit's A9/A10 (`filter_deterministic` line drops) and A6 (the greedy `/tmp/.*"`
regex, which erases flags/result/size beyond its documented reach) are exactly class (c).

---

## 1. The invariant, stated semantically first

Everything below is an approximation of one property. Write it down before the mechanisms so it is
clear what each check is a proxy *for*:

> **NEW-STRIP(c) vs baseline B** — there exist two guest log traces `L1`, `L2` such that
> `verdict_B(L1, L2) = UNEQUAL` and `verdict_c(L1, L2) = EQUAL`.

A commit that makes the comparator call two previously-distinguishable runs identical has widened
the hole, **however it did so** — new regex, new filter, new default, deleted test, relaxed
predicate. Checks that key on source text are proxies; the check that keys on this property is not.

Corollary, and the reason the ratchet is worth building: rule (1) of the task ("no new claim may
rest on a stripped comparison") cannot be complied with by intent, because `Stripped` is the default
at the `--verify` call sites. The verdict must carry the fact. It now can.

---

## 2. Baseline B — concrete and available today

| component | pinned value |
|---|---|
| comparator source | hermit `b64d893ae9ea6404472eae9cb86102d91ec642ef` |
| strip-site inventory | the 25 sites in `ai_docs/verify-strip-site-audit-20260805.md` (A1-A13, B1-B5, C1-C2, D1-D4, E1) with their verdicts |
| scorecard snapshot | `compat-envelope/scorecard.csv` @ anchor `82a8e853…`, the set of existing `run_id` values |
| policy constants | `PARITY_STRIPPED_PREFIXES`, `PARITY_CANONICALIZATIONS`, the 10-clause predicate |

All four exist now; none needs to be produced first. **B deliberately locks in the bad defaults**
(B1 `Stripped` default, D1 stdout-SHA parity, C1 memory coverage off). That is the point: the
ratchet is *no-worse*, not *good*. Rule (2) — tolerate existing debt — is implemented as
grandfathering against B, not as an exception anyone has to remember.

---

## 3. The check: four limbs

### R1 — Policy lock (static, milliseconds)

A checked-in `ci/comparison-policy.lock` recording, in a diffable form: both versioned token arrays,
the required value of each of the 10 `ComparisonSpec` fields for parity, and the **clause count** of
`is_bitwise_parity()`. The checker re-derives these from source and diffs against the lock.

Catches: a token added or removed (a); a boolean default flipped, or a clause quietly deleted from
the acceptance predicate (b). That last one is the highest-leverage attack — weakening the *rule*
rather than the *comparison* — and it is a one-line diff that no behavioural test would notice,
because the comparison itself is unchanged.

### R2 — Distinguishability corpus (behavioural, seconds) — **the primary limb**

A locked corpus of log pairs, each `{id, dimension, left, right, expect}`, run against the live
comparator under the parity policy. Every dimension the parity definition names gets at least one
`expect: Unequal` pair, and every legitimate normalization gets an `expect: Equal` control:

| expect | dimension | rationale |
|---|---|---|
| Unequal | virtual-time timestamp differs | the exact thing `RE1`-style numeric stripping hides |
| Unequal | syscall argument / result / size / flag differs | A6's greedy regex erases these today |
| Unequal | count differs (same values, different multiplicity) | |
| Unequal | message present in one trace only | A8 selection-strip class |
| Unequal | message order differs | |
| Unequal | address **aliasing** differs (`1,1` vs `1,2`) | canonicalization must preserve identity |
| Equal | wall-clock prefix differs | A1, legitimate |
| Equal | host addresses renumbered but aliasing preserved | A7, legitimate |

**Fail conditions:** any `Unequal` pair compares `Equal` → a new strip, detected *semantically*
regardless of introduction route. Also fail if the corpus **count decreases** — deleting a pair is
the same attack as adding a strip, and a check that doesn't guard its own denominator is a proxy.

The seed already exists: `cargo test -p detcore --lib logdiff::` is 22/22 with exactly this shape
(wall-clock-prefix EQUAL, address-only EQUAL, alloc-order UNEQUAL, aliasing UNEQUAL, vtime UNEQUAL).
R2 is that suite promoted to a locked, counted, fail-on-shrink corpus.

### R3 — Claim-site ratchet (data, milliseconds) — implements rules (1) and (3)

Add two required columns to the scorecard: `comparison_mode` and `bitwise_parity`. Today's header
has `deterministic` and `parity` with **no mode recorded anywhere** — the Proxy Binding failure in
its purest form: the value does not carry its condition.

Check: for every row whose `run_id` is **not** in the baseline snapshot, if `deterministic=1` or
`parity=1` then `comparison_mode` must be non-empty and must name a mode; and per rule (3), a *new
cell* additionally requires `bitwise_parity=true`. Rows in the baseline set are exempt.

This is what makes rule (2) affordable: **no re-audit of the existing 621 rows is required.** The
grandfather set is a list of `run_id`s, computed once.

### R4 — Non-bitwise constructor allow-list (static, milliseconds)

An allow-list, with a count, of the call sites permitted to construct a non-parity `ComparisonSpec`
(`Stripped`, or `compare_logs=false`). Today that legitimately includes the `--verify` defaults at
`run.rs`/`record_start.rs` (B1) and the KVM output-only fallback (B2). A **new** such site fails.

This bounds the growth of B1/B2 without requiring the sprint to fix them — precisely the no-worse
semantic.

---

## 4. The defeat vector, and the only mitigation that works

**Every baseline-diff check is defeatable by editing the baseline in the same commit.** A PR that
adds a strip *and* updates `comparison-policy.lock` *and* deletes the corpus pair that would have
caught it passes a naive gate cleanly. This is not a hypothetical: it is the same shape as the rule
the Merge Gate already enforces — `ci-hub/validation/verify_receipt.sh` must execute from an
immutable parent commit, **never from the PR under test**.

Mitigations, in order of importance:

1. **Run the checker and read the lock from the merge base / an immutable parent commit, not the PR
   head.** Without this, R1-R4 are advisory.
2. **Lock and corpus edits must be their own commit**, tagged `mechanism:comparison-policy`, so
   `ci-hub pr-status` surfaces the overlap and a coordinator sees a policy change as a policy change
   rather than as a line in a feature diff.
3. **Print the delta.** The gate must emit `tokens +1 / corpus 22→21 / allow-list +1` in its output.
   A silent baseline bump reads as a pass; a printed one is reviewable. No silent caps.

---

## 5. Bracketing the check itself (both directions, per repo policy)

A ratchet nobody has seen fire is a claim, not a control.

- **Negative:** plant a strip — add a regex erasing syscall results — and confirm R2 fails, naming
  the specific failing pair id. State the count of pairs that flipped.
- **Positive:** plant a *legitimate* normalization (a new wall-clock prefix form) with its token
  declared in the lock and a matching `Equal` control pair, and confirm the gate **passes**. This is
  the half that proves the check is neither inert nor so broad it blocks honest work.
- **Predicate-weakening:** delete one clause from `is_bitwise_parity()` and confirm R1 fails.
- **Do not** plant an authorization artifact to test the gate. Exercise it with an inert fixture or
  an isolated repo, never a real label or a real merge path.

---

## 6. Honest limitations

- **R2's coverage is not its count.** It catches strips only on dimensions the corpus covers, so its
  strength is coverage, not the number of pairs. R1/R4 are the completeness backstop: a new
  normalization construct in the comparator that no corpus pair exercises should fail with
  *"uncovered construct"*, not pass because no existing pair noticed.
- **The FullTrace gap (A9/A10) is pre-existing and must be recorded as baseline debt, not
  normalized away.** `filter_deterministic` is not called under `FullTrace`, so `--verify-strict`
  re-exposes host-timing-dependent poll-retry lines. Silently reusing `filter_deterministic` under
  `FullTrace` to make the corpus green would itself be a new strip — the exact thing this ratchet
  exists to refuse. It needs a producer-side fix or a declared, token-carrying exclusion.
- **A6 (greedy `/tmp/.*"`) is a strip that IS declared but is wider than its token claims.** Token
  presence is not token accuracy. R2 is what closes that gap, since a pair differing only in a flag
  after a `/tmp` path will compare EQUAL today.
- **`Fstat` without its output struct** (`detcore/src/lib.rs:773`, FIXME T136880615) is invisible at
  every strictness. No comparator-side ratchet can see it; only a producer change can. Record it in
  B as known-uncovered rather than letting the ratchet imply coverage it does not have.

---

## 7. Recommended sequencing (cheapest real protection first)

1. **R3** — two scorecard columns + the grandfather list. Highest value per hour: it directly
   implements rules (1) and (3), needs no comparator work, and stops the debt the reset must undo
   from deepening.
2. **R1** — the policy lock. Small, and it guards the acceptance predicate, which nothing else does.
3. **R2** — promote the existing 22 `logdiff::` tests into the locked, counted corpus; add the
   missing dimensions.
4. **R4** — allow-list, once R1's lock format exists (it can share the file).

R3 alone satisfies the task's stated rule. R1+R2 are what make it a ratchet rather than a
convention.

## Evidence index

- `ComparisonSpec` + `is_bitwise_parity()`: `hermit-cli/src/bin/hermit/verify.rs:126-275` (on `main`)
- Strip inventory (25 sites, verdicts, denominators): `ai_docs/verify-strip-site-audit-20260805.md`
- Scorecard header (no mode column): `compat-envelope/scorecard.csv:1`, 620 data rows
- Seed corpus: `cargo test -p detcore --lib logdiff::` (22 tests)
- Immutable-parent-commit precedent: Merge Gate / `ci-hub/validation/verify_receipt.sh` rule
