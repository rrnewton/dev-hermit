# Nix reproducible builds, CA-store status, and a Hermit builder seam

**Date:** 2026-07-29

**Task:** `rb-nix-reprobuild-research`

**Method:** Web/source review only. This host has no `nix` executable, so no
local Nix build result is claimed.

## Findings

The shortest path is to reuse Nix's comparison machinery and Lila's package
queue, not create another rebuild database. Nix already has the two-build
primitive, a distinct nondeterminism exit status, retained mismatched outputs,
and hooks for hashing and diagnosis. Lila already turns those primitives into a
distributed Nix rebuild service.

Content addressing is complementary, not a reproducibility mechanism. It names
an output by its content after the build; it does not force two builds to emit
the same content. In fact, Nix's current `ca-derivations` feature is still
experimental, its stabilization milestone is open, and an open bug reports
that `nix-build --check` fails to detect a deliberately nondeterministic CA
derivation. The first Hermit experiment should therefore stay
input-addressed, use Nix/Lila to compare NAR hashes, and introduce CA only after
the wrapped build is stable.

The clean scalable Hermit hook is Nix 2.35's experimental
`external-builders` interface. It hands a helper the real builder, arguments,
environment, inputs, outputs, store directory, and build directories as JSON.
A Hermit adapter can execute that builder under `hermit run --strict` while Nix
retains evaluation, dependency ordering, output registration, checking, and
hooks. For a smaller proof of concept, override one derivation's `builder` with
a store-resident wrapper that invokes Hermit and then the original builder.

## What Nix already compares

- Current `nix build --rebuild` is explicitly documented as rebuilding an
  already built package and comparing it with the existing store paths.
  [Nix 2.35 `nix build` manual](https://nix.dev/manual/nix/latest/command-ref/new-cli/nix3-build#opt-rebuild)
- The legacy equivalent is
  `nix-build '<nixpkgs>' -A PACKAGE --check --keep-failed`. A check-mode
  binary mismatch has dedicated exit status **104**, distinct from a normal
  build failure or fixed-output hash mismatch.
  [Nix `nix-build` manual](https://nix.dev/manual/nix/latest/command-ref/nix-build.html#special-exit-codes-for-build-failure)
- The official NixOS reproducibility page recommends both forms, retaining the
  failed rebuild and using diffoscope. It warns that two matching builds are
  useful evidence, not proof. Fixed-output derivations require its documented
  four-build sequence because the declared hash changes build behavior.
  [NixOS Reproducible Builds](https://reproducible.nixos.org/)
- Nix's `diff-hook` runs only after Nix has established that outputs differ; it
  receives the previous output, rebuilt output, and derivation path. It is not
  part of equality determination. `post-build-hook` runs after locally built
  outputs but not substituted paths. Both are daemon configuration on a
  multi-user installation.
  [Nix configuration: `diff-hook`](https://nix.dev/manual/nix/latest/command-ref/conf-file#conf-diff-hook),
  [`post-build-hook`](https://nix.dev/manual/nix/latest/command-ref/conf-file#conf-post-build-hook)

This gives the desired division of labor: Hermit controls the builder process
tree; Nix performs the independent rebuild and byte/NAR comparison; Lila
selects work and stores attestations; diffoscope explains failures. Running
Hermit's own `--verify` inside every Nix `--rebuild` would turn two builds into
four. Start with one strict Hermit execution per Nix build and let Nix perform
the independent comparison. Use Hermit `--verify` separately when scheduler-log
equivalence, rather than output reproducibility, is the question.

## Existing infrastructure to piggyback

### Current service: Lila

Lila is the active hash-collection service behind
[reproducibility.nixos.social](https://reproducibility.nixos.social/). Its
architecture is already the required control plane:

1. a Nix post-build hook publishes signed NAR hashes;
2. a server aggregates hashes by derivation/output;
3. a `rebuilder` asks the server for candidates, runs an ordinary build, then
   runs `nix build DRV^OUTPUT --rebuild --no-link`.

The NixOS module additionally enables Nix's `diff-hook`; on a mismatch it
hashes `REBUILD_PATH` and publishes that second result. The rebuilder keeps
`MAX_CORES` workers busy and records failures rather than retrying them.

Sources at Lila commit `46799edecd524b9eb114e767b0e0c4dbdde2c442`:

- [README and operating model](https://github.com/nix-community/lila/blob/46799edecd524b9eb114e767b0e0c4dbdde2c442/README.md)
- [`rebuilder.rs`](https://github.com/nix-community/lila/blob/46799edecd524b9eb114e767b0e0c4dbdde2c442/utils/src/bin/rebuilder.rs)
- [post-build hash hook](https://github.com/nix-community/lila/blob/46799edecd524b9eb114e767b0e0c4dbdde2c442/utils/src/bin/build-hook.rs)
- [mismatch hash hook](https://github.com/nix-community/lila/blob/46799edecd524b9eb114e767b0e0c4dbdde2c442/utils/src/bin/diff-hook.rs)
- [NixOS hook configuration](https://github.com/nix-community/lila/blob/46799edecd524b9eb114e767b0e0c4dbdde2c442/utils/nixos/module.nix)

The public JSON API exposes jobsets, evaluation output paths, per-output
attestations, and token-protected rebuild suggestions. It is enough to import a
candidate list without scraping HTML:

```text
GET /api/jobsets
GET /api/jobsets/1/evaluations
GET /api/evaluations/24
GET /api/attestations/by-output/<store-basename>
```

The latest displayed minimal-ISO runtime evaluation (2026-01-18, route revision
`e4bae1bd10c9`) contains 751 outputs: 749 reproducible and two with multiple
NAR hashes:

- `nftables-1.1.6`: four distinct reported hashes;
- `bcachefs-tools-1.34.0`: two distinct reported hashes.

Evidence: [evaluation](https://reproducibility.nixos.social/evaluations/1/e4bae1bd10c9),
[nftables attestations](https://reproducibility.nixos.social/api/attestations/by-output/64dwyz1hp1bckdip5mlfavqf1ds9cz46-nftables-1.1.6),
[bcachefs attestations](https://reproducibility.nixos.social/api/attestations/by-output/pvh5hx8lwjsfb74cdfrp5533k9r8zmiv-bcachefs-tools-1.34.0).
Lila also has minimal/graphical runtime and build-closure jobsets, a Haskell
jobset, and an AArch64 graphical runtime jobset.

### Legacy r13y report generator

The older `nix-reproducible-builds-report`, called r13y, remains useful as a
reference implementation. Its worker realizes each `.drv`, reruns
`nix-store --realise --check --keep-failed`, detects the retained `.check`
directory, exports both outputs as NARs, hashes them into a local CAS, and
generates diffoscope reports. It can enumerate a runtime closure or the wider
build closure and separates timeouts for retry. That is the behavior worth
retaining; its coordinator endpoint (`compute.r13y.com`) and report generator
have been superseded by Lila for new integration.

Source at `bfb642b6477c7dc342b1ad8652750735c7ac7ad5`:
[check loop](https://codeberg.org/raboof/nix-reproducible-builds-report/src/commit/bfb642b6477c7dc342b1ad8652750735c7ac7ad5/src/check/mod.rs),
[protocol](https://codeberg.org/raboof/nix-reproducible-builds-report/src/commit/bfb642b6477c7dc342b1ad8652750735c7ac7ad5/src/messages.rs).
Direct access to `r13y.com` returned HTTP 403 from this host; no design should
depend on that legacy domain.

## CA `/nix/store`: state of the art and limits

[RFC 0062](https://github.com/NixOS/rfcs/blob/master/rfcs/0062-content-addressed-paths.md)
changes when the output name becomes known. An input-addressed output path is a
function of the derivation; a CA derivation builds into scratch output paths,
hashes/moves the result afterward, and records a **realisation** mapping the
derivation-output identity to the resulting store path. This enables early
cutoff when distinct resolved derivations produce content already present.

CA paths are self-authenticating, but the derivation-to-output realisation must
still be signed. RFC 0062 also documents the central nondeterminism hazard: two
builders can produce different CA paths for the same derivation, causing
incompatible duplicate dependencies (its “two-glibc” example). A store avoids
that by accepting at most one realisation per derivation output, which makes
the first accepted result authoritative; it does not make that result
reproducible.

As of Nix 2.35:

- `ca-derivations` remains an experimental feature.
  [manual](https://nix.dev/manual/nix/latest/development/experimental-features#ca-derivations)
- The stabilization milestone is open with 57 closed and 28 open items at
  research time.
  [Nix milestone 35](https://github.com/NixOS/nix/milestone/35)
- Open issue [Nix #5336](https://github.com/NixOS/nix/issues/5336) provides a
  `date +%N` reproducer where `nix-build --check` correctly fails in
  input-addressed mode but incorrectly succeeds with `__contentAddressed =
  true`.

Therefore CA should not be the oracle for the Hermit pilot. Compare NAR hashes
through Lila or an independent CAS first. CA can follow once repeated Hermit
builds are stable, where it can deduplicate equal results and provide early
cutoff.

## Concrete Hermit hook point

Wrapping the `nix build` client is insufficient on daemon installations: the
actual builder is launched by `nix-daemon`, outside the client's process tree.
Post-build and diff hooks are also too late; they observe outputs after the
nondeterministic execution.

The current no-patch seam is Nix's `external-builders` experimental feature.
For a matching `system`, Nix invokes a configured helper with a JSON document
containing `builder`, `args`, `env`, `inputPaths`, `outputs`, `storeDir`,
`tmpDir`, and `tmpDirInSandbox`. The helper is explicitly a builder/sandbox
provider. A `hermit-nix-builder` adapter should:

1. read that JSON and preserve the exact environment and argv;
2. make the input store paths read-only and the declared output/build paths
   writable in Hermit's namespace;
3. execute `hermit run --strict -- <builder> <args...>`;
4. return the builder status and leave output registration/comparison to Nix.

The interface is documented in
[Nix configuration](https://nix.dev/manual/nix/latest/command-ref/conf-file#conf-external-builders)
and selected in current Nix source before the normal local builder:
[`derivation-building-goal.cc`](https://github.com/NixOS/nix/blob/6eb73313e44ce05ff2a24ab212c6583d676df924/src/libstore/build/derivation-building-goal.cc#L350-L358).
Its implementation writes the JSON and executes the helper as the build user;
recursive Nix is explicitly unsupported:
[`external-derivation-builder.cc`](https://github.com/NixOS/nix/blob/6eb73313e44ce05ff2a24ab212c6583d676df924/src/libstore/unix/build/external-derivation-builder.cc).
The feature and its stabilization milestone are still experimental, so this is
a prototype seam rather than a production-stable contract.

For a one-package smoke test, changing the derivation's builder is less work:
make a Nix-store wrapper whose argv is the original builder plus its original
arguments, and have it `exec hermit run --strict --no-namespace -- "$@"`.
`--no-namespace` is necessary when the wrapper runs *inside* Nix's already
constructed chroot/namespaces; Nix still supplies filesystem isolation. This
changes the input-addressed derivation identity, so compare two wrapped builds
to one another and separately diff a wrapped output against the upstream
unwrapped output for semantic parity.

If neither experimental interface is acceptable, the narrow source patch is
`DerivationBuilderImpl::execBuilder`. Current Nix has already entered the
chroot, changed to the build directory, selected the build user, and assembled
the final argv/environment before the single `execve(drv.builder, ...)` call.
Replace only that exec with Hermit plus the original command; do not patch the
evaluator or store registration.
[Pinned source](https://github.com/NixOS/nix/blob/6eb73313e44ce05ff2a24ab212c6583d676df924/src/libstore/unix/build/derivation-builder.cc#L957-L1090).

## How to enumerate targets

Use three feeds, in this order:

1. **Live Lila failures:** enumerate enabled jobsets, latest evaluations, and
   outputs whose attestations contain more than one distinct `output_hash`.
   Start with `nftables-1.1.6` and `bcachefs-tools-1.34.0`; expand from runtime
   jobsets to build-closure jobsets.
2. **Nixpkgs issue backlog:** query
   `repo:NixOS/nixpkgs is:issue is:open label:"6.topic: reproducible builds"`.
   It contained 82 open issues at research time. The official issue template
   mandates the same `--check`/`--rebuild --keep-failed` and diffoscope flow.
   [Live query](https://github.com/NixOS/nixpkgs/issues?q=is%3Aissue+is%3Aopen+label%3A%226.topic%3A+reproducible+builds%22),
   [issue template](https://github.com/NixOS/nixpkgs/blob/master/.github/ISSUE_TEMPLATE/09_unreproducible_package.yml)
3. **Pinned closure sweep:** evaluate one nixpkgs revision to a `.drv` list,
   first for an ISO/runtime closure and then its build closure. Lila uses an
   uploaded SBOM and schedules candidates; legacy r13y demonstrates the local
   equivalent. For each derivation, run the normal build followed by the check
   build, classify exit 104 as output nondeterminism, retain mismatches, hash
   exported NARs, and attach diffoscope output. Retry timeouts separately from
   reproducibility failures.

The actionable prototype is therefore: import Lila's two current failures,
wrap only their actual derivation builders under strict Hermit, run the
existing Lila/Nix rebuild comparison unchanged, and measure whether the set of
distinct NAR hashes collapses to one. Do not enable CA mode for that test; use
it only after the independent hash oracle is green.
