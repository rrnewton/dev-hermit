# crates.io name-reservation ("squat") — STAGED, NOT PUBLISHED

Prepared tonight by `[impl agent, claude-opus-4-8]` for task
`sprint-crates-squat-and-dryrun`. **Nothing has been published to crates.io.**
The owner said "don't publish yet"; this stages everything for a 5-second
morning confirm.

## What is ready

24 minimal placeholder crates (`crates/<name>/`), each: `version = "0.0.1"`,
description, `license = "BSD-3-Clause"`, `repository`/`homepage`, `authors`,
empty `src/lib.rs`. **All 24 pass `cargo publish --dry-run` (0 failures).**

> **2026-07-31 update:** added `liteinst2` (non-optional cross-repo dep of
> `reverie-liteinst`; name confirmed AVAILABLE). See **`DRY-RUN-STATUS.md`** for
> the verified reverie/hermit dry-run analysis (cargo 1.97 workspace mode:
> reverie workspace dry-runs 22/22 clean minus reverie-liteinst; hermit remains
> gated on cross-repo `git =` reverie deps → needs publish + git→version
> rewrite).

To reserve the names:
```bash
export CARGO_REGISTRY_TOKEN=<crates.io token>   # or: cargo login
./PUBLISH.sh                        # dry-run everything again (safe)
./PUBLISH.sh --yes-publish-for-real # IRREVERSIBLE reservation
```
`PUBLISH.sh` defaults to dry-run and refuses real publish without the explicit
flag + a token.

## Name availability (re-confirmed against crates.io, this session)

**AVAILABLE — reserving these 23:**
`reverie-core`, `reverie-utils`, `reverie-syscalls`, `reverie-ptrace`,
`reverie-kvm`, `reverie-liteinst`, `reverie-dbi`, `reverie-dbt`,
`reverie-e9patch`, `reverie-dynamorio`, `reverie-sabre`, `reverie-process`,
`reverie-memory`, `reverie-preload`, `reverie-rpc-transport`, `safeptrace`,
`hermit-run`, `hermetic-infra`, `detcore-model`, `detcore-dbi`,
`hermit-resources`, `hermit-verify`, `test-allocator`.

**TAKEN — cannot reserve; a rename is required before that code can ever
publish (owner decision):**

| workspace crate | current name | status | proposed published name |
|---|---|---|---|
| reverie      | `reverie`      | TAKEN | `reverie-core` |
| reverie-util | `reverie-util` | TAKEN | `reverie-utils` |
| hermit-cli   | `hermit`       | TAKEN | `hermit-run` |
| detcore      | `detcore`      | TAKEN | **NEEDS A NAME** (e.g. `detcore-engine`) |
| common/digest| `digest`       | TAKEN (RustCrypto) | **NEEDS A NAME** (e.g. `hermit-digest`) |
| common/edit-distance | `edit-distance` | TAKEN | **NEEDS A NAME** (e.g. `hermit-edit-distance`) |

## Owner decisions needed (D1–D3)

- **D1** — authorize the real (irreversible) reservation publish? `PUBLISH.sh
  --yes-publish-for-real`.
- **D2** — pick published names for the 3 TAKEN crates that have no chosen
  rename yet: `detcore`, `digest`, `edit-distance`.
- **D3** — confirm the reverie crates publish from the reverie repo first (they
  are the leaf deps of the hermit tree).

## Part (2): `cargo publish --dry-run` clean for the hermit dep tree

**Status: gated on the squat actually happening — cannot be green tonight while
we hold publishing.** Root cause, verified this session:

- `hermit` and `detcore-model` depend on `reverie-*` via **`git = {...}`**
  deps. `cargo publish` rejects git deps: `cargo publish -p hermit/-p
  detcore-model --dry-run` fails with `no matching package named
  reverie-syscalls found`.
- So the hermit tree can only dry-run once every `reverie-*` dep exists **on the
  registry** and each manifest is rewritten `git = {...}` → `version = "…"`.

Once the reverie names are reserved/published, the remaining hermit-side changes
are (to be applied in the hermit repo, coordinated with `autocargo`/Buck sync
and with task `fix-hermit-version-source-of-truth`, which owns `hermit-cli`'s
version):

1. Bump each workspace crate `version = "0.0.0"` → `"0.2.0"`.
2. Add a `description` to all 7 manifests (crates.io requires it; today every
   one warns `manifest has no description`).
3. Rewrite the `reverie-*` `git = {...}` deps to `version = "…"` (registry).
4. Rename the 3 undecided TAKEN crates per D2 and update all referrers.

Then `cargo publish --dry-run --no-verify` resolves (manifest-level), and a full
`cargo publish --dry-run` (verify build) works once **real** reverie content —
not the empty placeholders — is published.

These hermit-manifest edits were deliberately **not** made tonight: they cannot
reach a green dry-run without publishing (which we hold), the manifests are
`autocargo`-generated (need Buck sync), and `hermit-cli/Cargo.toml` is owned by
the concurrently-active `fix-hermit-version-source-of-truth` (agent hermit-235).
