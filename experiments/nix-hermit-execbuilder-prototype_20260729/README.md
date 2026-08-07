# Hermit-wrapped Nix builder: execBuilder-seam prototype

**Date:** 2026-07-29 **Host:** devbig014.atn7.facebook.com **Task:** `rb-nix-execbuilder-prototype`
(child of `epic-nix-reprobuild`). Follows `ai_docs/nix-reprobuild-ca-store-research_20260729.md`.

## Question

Can wrapping the Nix builder's `exec` under `hermit run` determinize a
non-reproducible Nix build? Prototype the seam and a rebuild-and-compare-hash
harness against the research targets nftables-1.1.6 and bcachefs-tools.

> ## ⚠️ CORRECTION 2026-08-06 — three claims below are refuted; do not act on them
>
> Superseded by
> [`experiments/rb_no_namespace_random_leaks_20260806/`](../rb_no_namespace_random_leaks_20260806/README.md),
> which measured each source individually instead of inferring it from a build
> hash. **The history below is left intact; only this note is added.**
>
> 1. **`--no-namespace` is NOT required.** The mode table below says the default
>    full-namespace mode discards writes to `$out` via the "private mnt ns".
>    That is wrong — a mount namespace isolates the *mount table*, not file
>    contents, and writes outside `/tmp` were measured **visible** under the
>    default mode. What is discarded is `/tmp` alone, because Hermit mounts a
>    private tmpfs there; with `sandbox = false` Nix's build directory lives in
>    `/tmp/nix-build-*`, and *that* is what vanished.
>    **Use `hermit run --tmp=/tmp`**, which keeps every namespace, makes host
>    writes visible, and needs no `setarch -R` (the full-namespace mode pins
>    ASLR itself).
> 2. **The `AT_RANDOM` leak does not exist.** `AT_RANDOM` is byte-identical
>    across 10 runs in *every* mode, including `--no-namespace`. Detcore
>    rewrites it in `handle_post_exec` (`detcore/src/lib.rs`), a post-exec hook
>    that namespaces never gated. It is not why `$RANDOM` varies.
> 3. **The `/proc/sys/kernel/random/uuid` leak does not exist.** Identical
>    across 10 runs in every mode; Hermit intercepts the path by name in
>    `detcore/src/procfs.rs` (`ProcfsKind::RandomUuid`), so it does not depend on
>    procfs being privately mounted. `rb_nix_minimum_hermit_dose_20260730` was
>    right about this and this document was wrong.
>
> **What is real:** under `--no-namespace` only, `getpid()` returns a host PID
> (10 distinct values in 10 runs) because Hermit's PID determinism comes from
> the PID namespace, not from syscall virtualization. bash `$RANDOM` is the
> observable consequence and is **intermittent** (3–5 distinct in 10), which is
> why it was mis-attributed twice. Any mode that keeps the PID namespace fixes
> it. Note the mechanism from `getpid` to `$RANDOM` is established by
> elimination, not from bash's source.

## TL;DR

- **Mechanism works.** Nix runs the whole builder process tree under Hermit via
  a one-line `realBuilder` override; no Nix patch and no daemon required.
- **Determinization proven** on a controlled positive control: a derivation
  non-reproducible through wall-clock time and `/dev/urandom` is
  **NONDETERMINISTIC native** (two distinct NAR hashes) and **byte-identical /
  reproducible under the Hermit wrap** — confirmed by both Nix's own `--check`
  oracle and a self-reference-free canonical-rebuild oracle.
- **nftables-1.1.6 is the wrong local target.** On a single host it is *already
  reproducible* natively; its `nix --check` "mismatch" is entirely a
  self-reference / scratch-path artifact (see below), not a runtime
  nondeterminism, so it is neither caused nor curable by Hermit. Its real
  (Lila-observed) nondeterminism is cross-machine/environmental and does not
  reproduce on one machine.
- **Two residual leaks characterized** under `--no-namespace`: `AT_RANDOM`-seeded
  userspace PRNGs (bash `$RANDOM`) and `/proc/sys/kernel/random/uuid` are not
  virtualized. Time and `/dev/urandom` are.

## Setup

Nix 2.35.1 installed single-user (no daemon, `sandbox = false`) — the host had
no `nix`. Single-user + no sandbox means the builder runs directly in the
client process tree, which is the simplest place to interpose Hermit. nixpkgs
pinned to channel revision `9bc02893134c` (see `metadata.json` for all SHAs).

## The seam

nixpkgs `stdenv.mkDerivation` builds by exec'ing
`realBuilder` (defaults to the stdenv `bash`) with `args =
["-e" source-stdenv.sh default-builder.sh]`; i.e. execBuilder does
`execve(bash, ["bash","-e", <phase scripts>])`. (Note the gotcha: the
user-facing `builder` *attribute* is the phase **script**, not the exec'd
binary — `realBuilder` is the binary.)

`nix/hermit-wrap.nix` keeps the derivation byte-identical except it swaps
`realBuilder` for a tiny store-resident wrapper:

```sh
#!/nix/store/…-bash/bin/bash
exec /usr/bin/setarch x86_64 -R <hermit> run --no-namespace -- <stdenv-bash> "$@"
```

so the *entire* builder tree (unpack → patch → configure → build → install →
fixup) runs under Hermit, while Nix keeps evaluation, dependency ordering,
output registration and `--check` comparison. Applied with
`drv.overrideAttrs (_: { realBuilder = hermitWrap; })`.

Two mode choices were forced empirically (see `logs/` and the task notes):

| Hermit mode | writes to `$out` persist? | ASLR pinned? | usable for Nix? |
|---|---|---|---|
| default (full namespace) | **no** (private mnt ns discards them) | yes | no |
| `--no-namespace` | yes | no (leaks to `$RANDOM`) | **yes** + `setarch -R` |

> **⚠️ 2026-08-06: this table is wrong on both rows — see the correction at the
> top.** Measured: the default mode persists writes *outside* `/tmp` (only
> `/tmp` is discarded, and only because Hermit mounts a private tmpfs there),
> and `--no-namespace` leaks `getpid()`, not ASLR or `AT_RANDOM`. The correct
> row is `--tmp=/tmp`: writes persist, ASLR pinned, no PID leak, no `setarch`.

`--no-namespace` is required so the build output lands in `/nix/store`;
`setarch -R` (ADDR_NO_RANDOMIZE) then pins ASLR at the host level since Hermit
cannot while sharing the host namespace. Because `realBuilder` is part of the
input-addressed derivation, wrapping changes the derivation identity and output
path — so we compare wrapped-vs-wrapped, and separately reason about parity with
the native output.

## Harness

Two oracles (`harness/`):

- `rebuild-compare.sh` — Nix's own oracle: realize, then
  `nix-store --realise --check --keep-failed`; distinct-output exit status is
  **104**; NAR-hash both `$out` and the retained `$out.check`.
- `rebuild-canonical.sh` — a **fair** oracle that avoids `--check`'s
  self-reference false positive: build → hash → `nix-store --delete` → rebuild
  into the *same* canonical `$out` → hash. Self-references are then identical in
  both builds so only genuine nondeterminism remains.

`harness/run-detached.sh` sources the nix profile + fwdproxy env and runs a
labelled case; heavy builds were launched with `nohup setsid` (Hermit
sequentializes every short-lived stdenv/patchelf process, so a wrapped build is
minutes, not seconds).

## Results (`results.csv`)

| label | oracle | verdict | note |
|---|---|---|---|
| `nondet-native` (time+urandom+$RANDOM+uuid) | --check | NONDETERMINISTIC | two NAR hashes |
| `nondettime-native` (time+urandom only) | --check | NONDETERMINISTIC | two NAR hashes |
| **`nondettime-hermit`** (time+urandom only) | --check | **reproducible** | identical NAR hash |
| **`nondettime-hermit-canonical`** | canonical | **reproducible** | identical NAR hash |
| `demofast-hermit` (all four sources) | --check | NONDETERMINISTIC | only `$RANDOM`+`uuid` differ; `date`+`urandom` byte-identical |
| `nftables-native` | --check | NONDETERMINISTIC* | *self-reference artifact only |
| `nftables-native-canonical` | canonical | **reproducible** | already reproducible on-machine |

The **`nondettime-*`** rows are the headline: the Hermit exec-builder wrap turns
a genuinely time/RNG-nondeterministic Nix build into a byte-for-byte
reproducible one. `date` and `/dev/urandom` are byte-identical across wrapped
builds (`demofast-hermit` isolates this: only `$RANDOM` and the procfs UUID
differ).

### nftables-1.1.6: the `--check` self-reference artifact

Native `--check` flagged nftables as nondeterministic, but the **only** differing
file was `lib/libnftables.so.1.1.0`, and the **only** differing bytes (32 of
them) were the 32 characters of an embedded `/nix/store` path hash, scattered
across `movabs` immediates in `.text`:

```
build A differing bytes: 3v5hd3z6a2kkfyj9j7ki4qlqhcqv9jbi   (A's canonical $out hash)
build B differing bytes: 4bhwv8w7mj13sxvy6wm0lrgfidwrvfn9   (the --check scratch-path hash)
```

nftables embeds a **self-reference to its own `$out`**. `nix --check` rebuilds
into a scratch path, so the self-reference necessarily differs — a false
positive unrelated to runtime nondeterminism. The canonical-rebuild oracle
(both builds into the same `$out`) confirms nftables-1.1.6 is **reproducible on
this machine**. Its Lila-reported four-hash nondeterminism is therefore
cross-machine/environmental and cannot be reproduced — or fixed — on a single
host. **Takeaway for the epic: `nix --check`/`--rebuild` is an unreliable
same-machine oracle for self-referential outputs; use the canonical-rebuild
oracle (or compare independent hosts) instead.**

## Limitations / what Hermit does NOT virtualize under `--no-namespace`

> **⚠️ 2026-08-06: both bullets in this section are refuted.** `AT_RANDOM` and
> `/proc/sys/kernel/random/uuid` are both determinized under `--no-namespace`
> (10/10 identical each). The single real leak is `getpid()`. See the correction
> at the top and
> [`rb_no_namespace_random_leaks_20260806/`](../rb_no_namespace_random_leaks_20260806/README.md).
> The closing recommendation — "(a) a Hermit mode that persists `$out` while
> retaining full-namespace determinism" — was the right instinct, and that mode
> already exists: `--tmp=/tmp`.

- `AT_RANDOM`-seeded userspace PRNGs: bash `$RANDOM` differs across wrapped
  builds because glibc/bash derive it from the kernel-supplied `AT_RANDOM`
  auxv bytes, which `setarch -R` does not zero (it disables mmap/stack ASLR
  only). Full-namespace Hermit *does* fix `$RANDOM` (it constructs the process),
  but full-namespace discards `$out`.
- `/proc/sys/kernel/random/uuid`: read straight from the host kernel RNG because
  `--no-namespace` shares host procfs; Hermit intercepts `/dev/urandom` but not
  this procfs path.

Neither leak appears in typical build outputs, but both matter for a
"deterministic-by-construction" builder and argue for either (a) a Hermit mode
that persists `$out` while retaining full-namespace determinism, or (b)
zeroing `AT_RANDOM` and virtualizing the procfs RNG under `--no-namespace`.

## Scaling limitation (finding for the epic)

The synthetic controls build in seconds; a **real package** under the wrap does
not. Wrapping `nftables-1.1.6` and rebuilding under Hermit ran **>23 min with no
completion** on this 316-core host, because Hermit determinizes by
sequentializing execution: nftables' parallel `make -j` collapses to one thread,
and the patchelf-heavy `fixupPhase` runs every short-lived process serially. So
the wrapped-nftables `--check`/canonical rows below were **not driven to
completion** — they are confirmatory only (the determinization criterion is
already met by `nondettime`), and their omission reflects Hermit runtime
performance, not a wrap defect. **Takeaway for the epic: the exec-builder seam
is correct and cheap to apply, but whole-package determinization is gated on
Hermit build-time performance (parallelism-preserving determinism, or
per-derivation caching), not on the Nix integration.**

## Reproduce

```sh
. /home/newton/.nix-profile/etc/profile.d/nix.sh
export http_proxy=http://fwdproxy:8080 https_proxy=http://fwdproxy:8080
cd experiments/nix-hermit-execbuilder-prototype_20260729
# clean determinization demo:
bash harness/rebuild-compare.sh nondettime-native '(import ./nix/nondet-time.nix) {}'
bash harness/rebuild-compare.sh nondettime-hermit '(import ./nix/hermit-wrap.nix {}).wrap ((import ./nix/nondet-time.nix) {})'
# nftables self-reference analysis:
bash harness/rebuild-canonical.sh nftables-native-canonical '(import <nixpkgs> {}).nftables'
```

## Files

- `nix/hermit-wrap.nix` — the execBuilder wrap (realBuilder override).
- `nix/nondet-time.nix` — controlled positive control (time+urandom only).
- `nix/nondet-demo.nix` — full control incl. `$RANDOM`+uuid (characterizes leaks).
- `harness/rebuild-compare.sh` — Nix `--check` oracle.
- `harness/rebuild-canonical.sh` — fair canonical-rebuild oracle.
- `harness/run-detached.sh` — env + detached runner.
- `results.csv`, `logs/` — machine-readable results and per-run logs.
- `metadata.json` — SHAs, host, toolchain, coverage summary.
