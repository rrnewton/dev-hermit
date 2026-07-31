# REAL crates.io publish order (leaves-first) — owner-gated, HELD

`[impl agent, claude-opus-4-8]`, task `sprint-crates-squat-and-dryrun`.
This is the dependency-ordered command list for publishing the **real** crate
content (not the empty squat placeholders). **Do not run until owner authorizes
(D1) and names are chosen (D2).** Every networked step uses `with-proxy`.

Two paths are available; pick per D1:

- **Squat first (reserve names now, real content later):**
  `./PUBLISH.sh --yes-publish-for-real` — publishes the 24 empty 0.0.1
  placeholders (order inside the script is leaves-first already). Then the real
  content below replaces them at 0.2.0+.
- **Real content directly (skip empty placeholders):** run the ordered list
  below. Preferred if release is near — crates.io discourages pure squatting.

## Step A — external dep (liteinst2 repo)

```bash
cd <liteinst2 checkout>
with-proxy cargo publish            # name: liteinst2 (AVAILABLE)
```

## Step B — reverie repo (apply manifest-patches/reverie-descriptions.patch first)

Leaves-first. `reverie`→`reverie-core` is **already on reverie main** (publishes
as `reverie-core`, imports as `reverie`). Still apply the `reverie-util`→
`reverie-utils` alias (same `package =` mechanism) BEFORE these:

```bash
cd <reverie checkout>              # branch codex/crates-publish-dryrun applied
with-proxy cargo publish -p reverie-memory
with-proxy cargo publish -p reverie-process
with-proxy cargo publish -p reverie-utils          # was reverie-util
with-proxy cargo publish -p safeptrace
with-proxy cargo publish -p reverie-syscalls
with-proxy cargo publish -p reverie-preload
with-proxy cargo publish -p reverie-rpc-transport
with-proxy cargo publish -p reverie-core           # was reverie
with-proxy cargo publish -p reverie-ptrace
with-proxy cargo publish -p reverie-kvm
with-proxy cargo publish -p reverie-dbi
with-proxy cargo publish -p reverie-e9patch
with-proxy cargo publish -p reverie-liteinst       # needs liteinst2 (Step A)
```

Between each publish, allow crates.io indexing (cargo waits automatically since
1.66; if a downstream publish can't find the just-published dep, retry).

## Step C — hermit repo (apply manifest-patches/hermit-versions-descriptions-deps.patch first)

Prereqs before Step C:
- reverie crates (Step B) are live on the registry.
- Renames applied for the 3 undecided TAKEN names (D2): `detcore`, `digest`,
  `edit-distance`, plus `hermit`→`hermit-run`.
- Finish the git→version rewrite in the hermit crates the staging branch did NOT
  cover (staging branch did detcore, detcore-model, detcore-dbi, detcore-sabre):
  also `detcore-liteinst`, `hermit-cli`, `hermit-install`, `liteinst-runtime-build`
  if they carry reverie `git=` deps. (`hermit-cli`'s version is owned by PR #1196
  — coordinate.)
- Update the reverie dep version requirements to the just-published reverie
  version (e.g. `reverie-core = "0.2"`), not `^0.1.0`.

```bash
cd <hermit checkout>              # branch codex/crates-publish-dryrun applied
with-proxy cargo publish -p test-allocator
with-proxy cargo publish -p hermit-resources
with-proxy cargo publish -p <digest-rename>
with-proxy cargo publish -p <edit-distance-rename>
with-proxy cargo publish -p detcore-model
with-proxy cargo publish -p <detcore-rename>
with-proxy cargo publish -p detcore-dbi
with-proxy cargo publish -p detcore-sabre
with-proxy cargo publish -p hermit-verify
with-proxy cargo publish -p hermit-run             # was hermit (hermit-cli)
```

## Reminder: Buck TARGETS are the source of truth

These Cargo.toml files are autocargo-generated. Bump the versions/descriptions in
the Buck `TARGETS` and regenerate, rather than shipping hand-edited manifests, so
the tree does not drift (same discipline as PR #1196). The staging branches here
are the *validation vehicle*, not the merge vehicle.
