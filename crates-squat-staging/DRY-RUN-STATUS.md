# `cargo publish --dry-run` status for the hermit dep tree — verified 2026-07-31

Update by `[impl agent, claude-opus-4-8]` for task `sprint-crates-squat-and-dryrun`.
Toolchain: **cargo 1.97.1**. All networked commands used `with-proxy`.
**Nothing published to crates.io.** Manifest edits are committed on staging
feature branches (not for direct merge) and captured as patches in
`manifest-patches/`.

## Main moved (2026-07-31 refresh)

Both staging branches were refreshed onto current `main`:
- **hermit** main `ae2565be` now has **hermit-cli 0.2.0** (PR #1196 LANDED —
  single-source `--version`) and the **third-party-backends cargo feature gate**.
- **reverie** main `aa6f1283` now has the **reverie→reverie-core** rename (the
  package publishes as `reverie-core`, `[lib] name = "reverie"` alias; backend
  split). `reverie-util`→`reverie-utils` is **not** yet on main; still needs the
  same `package =` alias at publish time.

## Staging feature branches (in slot 231, refreshed onto current main)

| repo | branch | SHA | base | change |
|---|---|---|---|---|
| reverie | `codex/crates-publish-dryrun` | `275edf4032b0206cddf42b2921402118503c970d` | reverie main `aa6f1283` | add `description` to 10 publishable crates (reverie-core conflict resolved) |
| hermit  | `codex/crates-publish-dryrun` | `11403089e90f2358702fc6bc0f6218d39a209e79` | hermit main `ae2565be` | bump every crate 0.0.0→0.2.0 (pkg + internal dep reqs), add descriptions to 11 publishable crates |

The refreshed hermit branch **no longer bakes in** the reverie git→version
rewrite (main's reverie deps are already dual `{version, git, rev}`; the
real-publish rewrite is an owner step in `REAL-PUBLISH-ORDER.md`, since it
can't dry-run clean pre-publish and now needs the `reverie-core` alias).

Patches: `manifest-patches/reverie-descriptions.patch`,
`manifest-patches/hermit-versions-descriptions-deps.patch`.

## Dry-run results (VERIFIED this session)

### Reverie — CLEAN (re-verified at `275edf4`)
```
cd reverie   # (with reverie-descriptions.patch applied)
cargo publish --dry-run --no-verify --allow-dirty \
  -p reverie-memory -p reverie-process -p reverie-util -p safeptrace \
  -p reverie-syscalls -p reverie-preload -p reverie-rpc-transport \
  -p reverie-core -p reverie-ptrace -p reverie-kvm -p reverie-dbi -p reverie-e9patch
```
→ **12 crates packaged, 0 errors, 0 warnings.** (Note `-p reverie-core`, the
post-rename package name.) Descriptions cleared the
`no description` warnings; internal `{version,path}` deps are co-resolved by the
multi-`-p` (subset workspace-publish) form. `reverie-liteinst` is intentionally
excluded (blocked on external `liteinst2`, see below).

### Hermit leaves — CLEAN (re-verified at `11403089`)
```
cd hermit    # (versions+descriptions applied; reverie deps unchanged = main's
             #  dual {version,git,rev} form)
cargo publish --dry-run --no-verify --allow-dirty \
  -p test-allocator -p hermit-resources -p digest -p edit-distance
```
→ **4 crates packaged, 0 errors** (one warning: `digest@0.2.0 already exists` —
the RustCrypto name collision; dry-run does not block on it, but a real publish
needs the rename).

### Hermit reverie-dependent crates — BLOCKED on reverie-registry (expected)
`cargo publish -p detcore-model --dry-run` on current main:
```
error: failed to prepare local package for uploading
Caused by:
  no matching package named `reverie-syscalls` found
  location searched: crates.io index
```
This is the whole story of the remaining block: cargo resolves the entire
workspace before packaging **any** member; on publish it drops the `git` source
from reverie's dual `{version, git, rev}` deps and looks up `reverie-syscalls`
(and the other reverie crates) on the **crates.io index**, where they don't yet
exist. Everything else (path-deps, versions, descriptions) is resolved. So the
hermit dep tree dry-runs clean the moment the reverie crates are published — no
further hermit manifest work beyond the reverie dep rewrite + the 3 renames.

> The gate is identical whichever dep form you use: `git=` deps are rejected by
> `cargo publish`; `version=`-only deps make the workspace unresolvable until
> reverie is on a registry. Both point to the same fact: **reverie must be
> published first.**

## Can the full hermit tree be proven clean WITHOUT publishing? No. (probed 2026-07-31)

Asked whether a local stand-in registry could make the reverie-dependent hermit
crates dry-run clean while still holding the crates.io publish. It cannot —
this is a hard cargo invariant, not a missing workaround:

- A **directory/vendored source replacement** of `crates-io` is rejected outright:
  `error: crates-io is replaced with non-remote-registry source dir …; include
  --registry crates-io to use crates.io`. Passing `--registry crates-io` then
  resolves deps against **real** crates.io, where reverie is still absent.
- A **separate local registry** (reverie deps `{version, registry="local"}`)
  fails a different way: `cargo publish` to crates.io forbids depending on a
  crate from any other registry — crates.io deps must resolve on crates.io.

So the only faithful way to a clean full-tree `cargo publish --dry-run` is to
publish the reverie crates to crates.io first (the owner-held step). Everything
that *can* be validated without that publish already is (reverie 12/12 clean;
hermit 4 leaves clean).

## The 2 reverie blockers

1. **`liteinst2`** — non-optional `git=` dep of `reverie-liteinst`
   (`rrnewton/liteinst2` rev b21b248…). `reverie-liteinst` can't publish until
   `liteinst2` is on crates.io. Name is **AVAILABLE**; staged as the 24th
   placeholder (`crates/liteinst2/`, dry-run clean) and first in `PUBLISH.sh`.
2. **descriptions** — added on the reverie staging branch (patch above).

## TAKEN names needing a rename before REAL publish (owner D2)

`detcore`, `digest` (RustCrypto), `edit-distance`, plus the already-decided
`hermit`→`hermit-run`, `reverie`→`reverie-core`, `reverie-util`→`reverie-utils`.
Dry-run does NOT check name availability, so these dry-run clean today; the real
publish fails until renamed.

## Owner morning actions

1. **D1** authorize reservation publish → `./PUBLISH.sh --yes-publish-for-real`
   (24 placeholder names incl. liteinst2). crates.io discourages pure squatting;
   prefer publishing real 0.x content near release.
2. **D2** pick published names for `detcore`, `digest`, `edit-distance`.
3. **D3** publish the real reverie crates (apply `reverie-descriptions.patch`,
   publish liteinst2 + the reverie crates leaves-first per
   `REAL-PUBLISH-ORDER.md`), then apply `hermit-versions-descriptions-deps.patch`,
   do the reverie git→version→registry dep rewrite (strip `git`+`rev`, point at
   published `reverie-core`/`reverie-utils` via `package =` alias) + the 3
   renames, and run `cargo publish --dry-run` for the hermit tree — now fully
   clean.

Already landed since staging began (no longer owner TODO):
- **PR #1196** — hermit-cli is 0.2.0 on main via a single `CARGO_PKG_VERSION`
  source (+ build date/SHA). The staging branch leaves hermit-cli's package
  version alone and only bumps its internal dep reqs.
- **reverie→reverie-core** rename is on reverie main (publishes as
  `reverie-core`, imports as `reverie` via `[lib] name`). `reverie-util`→
  `reverie-utils` still needs the same alias at publish time.

Also required at real-publish time: bump the internal Buck TARGETS versions to
match (these Cargo.toml files are autocargo-generated), same discipline as
PR #1196's documented internal `hermit-cli/BUCK` autocargo bump.
