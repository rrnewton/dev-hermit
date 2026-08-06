# anchor_select → `validate.sh` wiring: the hole measured, the patch written, the plant run — **needs a hermit slot to apply**

**Task:** `wire-inert-phase2-guards-into-consumers` (hermit-submodule half) · hermit-clone, 2026-08-05
**Local only, no egress.** The parent-side half is done and landed in the working tree
(`ai_docs/wiring-inert-phase2-guards-consumers-and-plant-evidence-20260805.md`).
**Nothing in `hermit/` was modified** — see §5.

## 1. The hole, measured rather than asserted

`hermit/validate.sh:4319 resolve_selective_baseline` infers the green baseline from the ledger with:

```bash
jq -r --arg slot "$VALIDATION_SLOT" '
    select(.result == "pass" and .commit != "unknown" and .slot == $slot)
    | .commit' "$VALIDATION_LEDGER_FILE" | tail -n 1
```

**Two fields checked.** It does not check `commit_anchored`, `tree_dirty`, `profile`,
`selection_mode` (the 1-hop clause), `failures`, `executed_tests`, or per-node `coverage` — the
seven other clauses of the shared qualifying-receipt predicate. A receipt that **passed while
executing zero tests** is therefore eligible as the green anchor.

Planted one such receipt (real hermit ancestor `cf1fe1ba`, `result: "pass"`, `executed_tests: 0`,
everything else valid) and ran both deciders on the same row:

| decider | verdict |
|---|---|
| the current `jq` filter, verbatim from `validate.sh` | emits `cf1fe1ba…` — **ACCEPTED as the anchor** |
| `anchor_select.row_qualifies` (shared predicate) | `(False, 'executed_tests==0')` — **REFUSED** |
| same row, `executed_tests: 942` (positive control) | `(True, 'qualifies')` — accepted |

That is the divergence the wiring closes, demonstrated on a real input rather than argued.
`tail -n 1` also orders by **file position**, not event time — noted, out of scope here.

## 2. The patch

`scratch/anchor-wire/resolve_selective_baseline.patched.sh` — ready to apply. It replaces only the
**automatic ledger inference**, routing it through the one shared verifier, and follows the guarded
parent-helper idiom already used in this file for `failure_evidence.py` (`:1412`) and
`nonzero_result.py` (`:1488`):

```bash
local anchor_helper="$DEV_HERMIT_PARENT/ci-hub/validate/anchor_select.py"
if [[ -n $DEV_HERMIT_PARENT && -r $anchor_helper ]] \
    && command -v python3 >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
    if anchor_json=$(python3 "$anchor_helper" --target HEAD --include-dirty \
        --ledger "$VALIDATION_LEDGER_FILE" --json 2>/dev/null); then
        sha=$(jq -r '.anchor.sha // empty' <<<"$anchor_json" 2>/dev/null)
    else
        sha=""
    fi
fi
```

`anchor_select`'s contract is exit 0 = inherit, **every non-zero exit means run the FULL lane**, so
no failure mode of the tool can yield a smaller test set.

## 3. Plant evidence — the bash glue, bracketed both directions

Stubbed verifier so the exit-code contract is tested independently of the predicate (which is tested
directly in §1). Run inside the hermit checkout because the function uses bare `git` — **read-only
git operations only**.

```
GLUE BRACKETS. POSITIVE CONTROL FIRST:
  exit 0 + anchor.sha  => INHERIT            -> cf1fe1babce00d037d381b2c3d1a7bcf244b8b06
  exit 4 NO-ANCHOR     => refuse             -> <empty: FULL LANE>
  exit 3 RE-ANCHOR-NOW => refuse             -> <empty: FULL LANE>
  exit 2 REFUSED       => refuse             -> <empty: FULL LANE>
  exit 5 ERROR         => refuse             -> <empty: FULL LANE>
  exit 0 but no anchor => refuse             -> <empty: FULL LANE>
  exit 0, sha not in repo => refuse          -> <empty: FULL LANE>

OVERRIDES + PARENT-ABSENT:
  explicit --selective-baseline honoured     -> cf1fe1babce00d037d381b2c3d1a7bcf244b8b06
  no parent (bare checkout) => full lane     -> <empty: FULL LANE>
```

The positive control earned its place twice: an earlier revision of this harness emitted `<empty>`
for **every** case — a `%r` Python format spec in a bash `printf` meant the stub was never written,
so the suite "passed" vacuously. A second revision failed for a different reason (the harness ran
outside the git repo, so the trailing `git cat-file -e` guard rejected even valid SHAs). Both were
visible only because a case was required to SUCCEED.

## 4. Two decisions the owner should confirm, not inherit from me

- **Explicit operator overrides stay unqualified.** `--selective-baseline` and
  `HERMIT_LAST_GREEN_SHA` still bypass the predicate. I kept them deliberately — a human naming a
  baseline is an instruction, not an inference — but this is a **remaining bypass** and should be a
  conscious choice, not an oversight. Qualifying them (or at least logging when an override skips
  the predicate) is a one-line follow-up.
- **Parent-absent ⇒ full lane.** A bare hermit checkout has no `ci-hub/`, so selection is disabled
  and the full suite runs: slower, never weaker. **Checked: this costs nothing today** — no hosted
  workflow uses the selective lane (`grep -rn 'selective|shallow-select|SELECTIVE'
  hermit/.github/workflows/*.yml` → no hits), so it is local-only.

## 5. Why this is not applied — I need a slot

`hermit/` is a **primary checkout**, currently clean and on `main`. Applying this would be feature
development in a primary, which:

1. violates **Hard Invariant 1** ("never do feature development in a primary checkout") and the
   Primary Checkout Invariant ("must ALWAYS be on latest main");
2. cannot produce the required artifact — **Hard Invariant 9** requires Hermit product work to land
   through a feature PR against `rrnewton/hermit:main`, which needs a branch in a worktree;
3. would leave the shared primary dirty, blocking every other agent's integration operations and
   failing the closeout guard.

**Request: one hermit slot** (`scripts/allocate-worktree.rs --agent hermit-clone --task
wire-inert-phase2-guards-into-consumers --product hermit`). With it the remaining work is small and
fully specified: apply §2, add a bash regression exercising the §3 cases, run the focused validate
lane, push the branch, open the PR. Egress is also down, so the PR itself waits regardless.

## Reproduction

```
# the hole (both deciders, same row)
jq -r --arg slot testslot 'select(.result=="pass" and .commit!="unknown" and .slot==$slot)|.commit' \
   scratch/anchor-wire/planted.jsonl
python3 -c "import sys,json;sys.path.insert(0,'ci-hub/validate');import anchor_select as A;\
print(A.row_qualifies(json.load(open('scratch/anchor-wire/planted.jsonl')),\
json.load(open('ci-hub/validate/qualifying-receipt.json'))))"
# the glue
bash scratch/anchor-wire/harness.sh
```
