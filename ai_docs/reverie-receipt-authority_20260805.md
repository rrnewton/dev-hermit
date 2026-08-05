# Exact Reverie dependency authority

Issue: [rrnewton/hermit#1653](https://github.com/rrnewton/hermit/issues/1653), part 2.

Hermit green evidence is valid only while the exact Hermit head pins the current
`rrnewton/reverie:refs/heads/main`. A schema-4 receipt's
`reverie_pin_current: true` does not identify which main tip it observed, so it
cannot remain landing authority after that ref moves.

## Authority

`ci-hub reverie-pin-status --hermit-repo CHECKOUT --sha H [--sha H...] --json`
is the single fresh cross-repository predicate. It resolves Reverie main once,
with a 30-second bound, then for each exact Hermit commit scans every tracked
`Cargo.toml` and `Cargo.lock` from the commit object. Manifest pins count only
from real direct/dev/build/workspace/target/patch/replace dependency tables;
lock pins count only from `[[package]]` sources. Comments, package metadata, and
lock metadata cannot manufacture a dependency. It accepts only one lowercase
40-hex Reverie revision equal to that resolved tip. Missing commits,
missing/mixed/malformed pins, network failure, and tip mismatch fail closed.

Schema-6 Hermit ledger rows carry the result:

```json
{
  "reverie_binding": {
    "repository": "rrnewton/reverie",
    "ref": "refs/heads/main",
    "pinned_sha": "<40-hex>",
    "resolved_sha": "<same 40-hex>"
  }
}
```

The binding is inside the immutable receipt body/digest. Old Hermit rows and
missing/tampered bindings do not qualify. `finalize_receipt.py --scan` can append
a schema-6 clone only after this authority verifies the exact Hermit commit.

All current local-evidence consumers route through the Rust semantic verifier:
`validate-status`, `ledger qualified-rows` (and therefore `pr-status`),
`newest-green`, label publication, immutable receipt verification, and hard/soft
green reconciliation. The newest-green cache also records the resolved Reverie
tip, so a ref move invalidates an otherwise byte-identical cache. Its
`--no-fetch` mode never performs a hidden ref lookup and therefore reports the
dependency frontier UNVERIFIABLE rather than emitting an offline green.

Receipt minting keeps that same authority: Rust selects and hashes the exact
schema-6 row, passes only those canonical bytes to the mechanical Python
publisher, then verifies the returned artifact digest, exact row equality, and
`SHA@started_at@host` identity before it can comment or label the PR. Python no
longer scans the ledger or owns a second predicate. Recomputed-artifact wrong-row
and wrong-host negatives prove that publisher output cannot substitute a
self-consistent proxy for the row Rust selected.

## Green-source independence

GitHub and local validation remain interchangeable green authorities. A hosted
green does not need a local ledger receipt. The dependency predicate is a
separate pre-landing condition: whichever source supplied green, the landing
path must freshly verify the exact Hermit head against live Reverie main.
`ci-hub green-source-decision` is the pure OR combiner for already-dereferenced
local and hosted outcomes: either PASS is sufficient, either genuine FAIL
refuses, and two NO_RESULT inputs do not authorize. It accepts no label, run ID,
comment, or ledger proxy and is not itself a dereferencing authority.

## #38 integration obligation

The safe exact-head lander currently lives on `origin/codex-coord` / parent PR
#38 and is deliberately not imported by this change. Before #38 (or a minimal
extraction of it) can land, it must:

1. invoke `reverie-pin-status` for the exact source head before accepting either
   GitHub or local green;
2. for local hard/soft-green reuse, retain independently named bindings for
   source X, observed target-main base Y, and replay result Z, and reject a
   result whose actual replay base is not Y;
3. resolve one tip for a decision and refresh atomically at the final mutation
   boundary rather than inheriting an observation across merge retries;
4. treat timeout/unavailable/moved/malformed state as refusal; and
5. bracket GitHub-green-but-stale-pin refusal without planting an authorization
   label, check, or merge status.

The extraction must retain current-main landing-lock semantics; it must not
reintroduce #38's older lock implementation. The legacy `land-pr.sh` mutating
path is deliberately fail-closed because GitHub's server-side rebase API has an
expected-head condition but no atomic expected-base condition. It is not a
temporary authorization consumer and must not be described as safe.
