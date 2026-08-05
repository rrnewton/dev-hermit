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
lock pins count only from `[[package]]` sources. Every semantic Reverie
dependency/package must use that Git source; a path/version/registry dependency
cannot be masked by a decoy current pin. Duplicate lock `rev` parameters,
comments, package metadata, and lock metadata cannot manufacture a dependency. Git
replacement, caller object-locator state, and global/system/parameter/local
configuration redirects are disabled; the live lookup runs outside any repository.
It accepts one lowercase
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
missing/tampered bindings do not qualify. The authority mode of
`finalize_receipt.py` accepts a trusted target, exact SHA and ledger; it selects
that run's newest original row, follows only that row's own absolute `log_file`,
recomputes counts/coverage and the log SHA-256, then append-locks and revalidates
the source-row identity before adding a schema-6 clone. `--log` is diagnostics
only and is refused with a ledger. A superficially complete row cannot skip log
rereading, and the ledger is never rewritten.

All parent-tree local-evidence consumers route through the Rust semantic verifier:
`validate-status`, `ledger qualified-rows` (and therefore `pr-status`),
`newest-green`, label publication, immutable receipt verification, and hard/soft
green reconciliation. This is a parent implementation claim, not proof that an
older Hermit workflow has pinned the tree. The newest-green cache also records the resolved Reverie
tip, so a ref move invalidates an otherwise byte-identical cache. Its
`--no-fetch` mode never performs a hidden ref lookup and therefore reports the
dependency frontier UNVERIFIABLE rather than emitting an offline green.

Receipt minting keeps that same authority. Each completed assessment publishes
an append-only, content-addressed `validation-outcomes/<repo>/<sha>/<digest>.json`
snapshot. A pass additionally publishes its receipt and durable log. Rust
verifies the publisher's snapshot verdict and selected identity before labeling.
The consumer resolves the canonical outcome branch tip once, enumerates every
exact-SHA outcome, unions all carried rows, and delegates failure-over-pass
selection to `validate-status`; a later pass can never erase a genuine published
failure. It then re-derives the selected schema-6 row from the original source
row, exact-commit manifests, and durable log. Planned-manifest defects, unplanned
banner inflation, failed terminals, missing finalizer provenance, and log/count
drift refuse. Comments and labels are routing/cache hints, never discovery or
authority.

Artifact publication retries bounded branch-head races without overwriting an
existing content-addressed path. Existing and consumed logs above the Contents
API's 1 MiB inline limit are dereferenced through their exact Git blob identity;
the mutable download URL is never followed.

The deployable verifier is the full-tree
`ci-hub/validation/verify_receipt_bundle.sh` entrypoint. Its manifest names the
Rust modules, executable symlink, predicate, hosted classifier, finalizer, and publishers; it rejects
a modified bundle, requires an exact pinned dev-hermit commit, disables Git
replacement/object-locator state, and proves the target SHA is a commit object
before delegating. Downloading only `verify_receipt.sh`
is not an authority bundle.

## Green-source independence

The intended policy treats GitHub and local validation as independent,
interchangeable green authorities; a genuine failure from either leg wins. A
hosted green does not need a local ledger receipt. The dependency predicate is a
separate pre-landing condition: whichever source supplied green, the landing
path must freshly verify the exact Hermit head against live Reverie main.
`ci-hub green-source-decision` is the pure OR combiner for already-dereferenced
local and hosted outcomes: either PASS is sufficient, either genuine FAIL
refuses, and two NO_RESULT inputs do not authorize. It accepts no label, run ID,
comment, or ledger proxy and is not itself a dereferencing authority.

This paragraph describes the semantic interface, not current live deployment.
At this parent-layer commit the Hermit workflow still downloads an older single
file and does not call the combiner. Therefore `AGENTS.md`'s current hosted-gate
requirements remain the executable landing policy until the coordinated Hermit
bundle/producer integration lands and a follow-up policy commit records that
deployment. Parent tests must not claim the OR is live merely because the pure
combiner is bracketed.

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
