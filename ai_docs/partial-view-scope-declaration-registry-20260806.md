# A tool must state what it did not check — mechanism, detector, and the five instances

**Task:** `partial-views-are-footguns-full-scorecard-must-be-the-default` · hermit-clone (opus-5), 2026-08-06
**Local, no egress.** Delivered: `ci-hub/validate/scope_declaration.py` +
`ci-hub/validate/tests/test_scope_declaration.py` (18 tests). 385 passed across
`ci-hub/validate` + `ci-hub/landing`.

## The rule, made executable

> If an answer is SCOPED, the SCOPE IS PART OF THE ANSWER.
> "Consistent" is not "correct". "Green" is not "tested". "Enforced" is not "enforced everywhere".

A verdict that omits its scope is not a *weaker* answer — it is a **different answer to a question
the reader did not ask**, and the reader cannot tell. That is why the canonical instance
(`scorecard-full-manifest-denominator`: 131-of-194 rendered as a fraction of 28) looked fine.

Three pieces:

- **`Scope`** — what was examined, what was **not**, and the denominator: `examined` of `total`
  *plus where `total` came from*. The scorecard bug was not a miscount; it was confidently counting
  the **wrong population**, so an unsourced total is treated as a defect in itself.
- **`ScopedVerdict.render()`** — the omissions print **with** the verdict, never as a footnote. A
  scoped OK that can be copied without its scope is the whole failure mode.
- **`audit_scope()`** — the detector, because "remember to state your scope" is exactly the kind of
  rule that decays.

## Plant a partial view → it is flagged

```
PLANT  28 of 194, declaring nothing        -> undeclared-partial-view
PLANT  partial with an UNSOURCED total     -> unsourced-denominator
PLANT  numerator without a denominator     -> half-a-denominator
PLANT  tool that never says what it checks -> no-scope-declared
PLANT  "all backends enforce it" while naming 3 blind spots -> overclaiming-summary
```

**Positive controls, which are what make it usable:** a full sourced view is clean, and — the one
that matters — **a partial view that DECLARES its omissions is clean**. Partial is fine. Partial and
silent is the defect. A modestly-worded summary that names its blind spots is likewise not penalised,
or the detector would punish exactly the behaviour it is trying to produce.

## The negative test the task names

`consistent_but_broken_is_not_ok()`. Modelled on instance 1: `check-reverie-pin.rs` reports *"a bump
is OPTIONAL, not required"* while `detcore_misc` **livelocks** at that pin — a green checkmark
explaining why the drain needn't move. The tool is not wrong; the **reading** is, and only because
the scope was invisible.

```python
assert consistent_but_broken_is_not_ok(pin) is False   # an OK with a material blind spot
assert "DOES NOT CHECK" in pin.render()                # and it cannot be quoted past
assert "LIVELOCK" in pin.render()
```

Paired with a positive control (an OK with nothing unexamined **does** read as OK), so the predicate
does not simply brand every OK unsafe.

## The five instances — 3 declared, 2 blocked

The registry is itself a denominator; it would be absurd to fix "state your denominator" without
stating this one. `python3 ci-hub/validate/scope_declaration.py` prints it and **exits 1 while any
instance is undeclared** — a ratchet, not a status page.

| # | instance | status |
|---|---|---|
| 2 | green with zero executed tests | **DECLARED** — absent/empty/zero-count logs all refused |
| 5 | `is-ancestor <PR head>` | **DECLARED** — the head is structurally never tested; a spy test pins it |
| 3 | `locally-validated` with no backing record | **DECLARED (new here)** — see below |
| 1 | `check-reverie-pin.rs` | **UNDECLARED** — hermit submodule, needs a slot |
| 4 | backend-abstraction lint (3 of 6) | **UNDECLARED** — hermit submodule, needs a slot |

**Instance 3, closed this session.** A label is a *cache* of a fact, never the fact.
`label_is_backed()` gives three separate refusals rather than one boolean, because *"no label"*,
*"label with no record"*, and *"label with a record for a **different** head"* are different
situations — and the third looks most like success. Testing the **consumer** with a fixture is also
the safe pattern: planting a real `locally-validated` label on a cold PR can satisfy merge-gate and
auto-merge, so the authorisation is never planted.

**Instances 1 and 4 are blocked on a hermit slot, not on analysis.** Their exact declaration text is
already in the registry, so the slot work is mechanical:

- `check-reverie-pin.rs` — *answers: whether the pinned commit is an ANCESTOR of the target.*
  *DOES NOT CHECK: runtime behaviour at that pin (detcore_misc LIVELOCKS there).*
- backend-abstraction lint — *answers: the commandment on the backends it enumerates.*
  *DOES NOT CHECK: the backends it does not enumerate (3 of 6).*

## Honest limits

- **2 of 5 remain undeclared**, and the registry exits 1 to say so rather than reporting partial
  success — which would be this very footgun.
- **Instance 3's predicate is offline-only.** It refuses the bad shapes correctly, but *fetching* live
  label state still needs egress; nothing here has been run against the four real PRs.
- **The detector is opt-in.** `audit_scope()` flags a `ScopedVerdict` handed to it; it does not crawl
  the codebase looking for tools that emit unscoped verdicts. Every tool that has not adopted `Scope`
  is invisible to it — which is, precisely, an undeclared partial view in the detector itself. Stated
  here rather than discovered later.
- The full-scorecard-as-default ask is **already satisfied** by the closed
  `scorecard-full-manifest-denominator` work (2026-08-01); this task was the general rule, and I did
  not re-open that fix.

## Files

`ci-hub/validate/scope_declaration.py` (new) · `ci-hub/validate/tests/test_scope_declaration.py`
(new, 18 tests). Uncommitted — egress down.
