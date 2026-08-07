# Two-root controlled reproducibility test: Debian Wheezy under Hermit

**Date:** 2026-08-07 · **Agent:** claude-coord-176 (impl, claude-opus-5) · **Track:** reproducible builds with Hermit

## Question

Repeating the ASPLOS'20 *Reproducible Containers* Debian case study for a modern
Hermit raises an immediate measurement problem: a bare "X% of packages were
reproducible under Hermit" says nothing unless you also know those packages were
irreproducible to begin with. Many Debian packages build reproducibly on their
own, so a high percentage can be mostly free.

So instead: **for a package whose build demonstrably depends on where it is
built, does Hermit make it independent of that?**

## Design: four builds, one variable, an internal control

Prepare a source package once, then build it **four** times from that same
prepared tree, in **four different root paths**:

```
   native  in root N1        hermit  in root A
   native  in root N2        hermit  in root B
```

and compare `N1` vs `N2` and `A` vs `B`. The only thing varied is the path the
build runs in.

The native pair is the experiment's own control. It establishes, per package,
that the build really is root-sensitive. A matching hermit pair is then evidence
about Hermit rather than about a package that would have matched anyway. The
result is a controlled claim of the form

> *N of M packages: native two-root build DIVERGES, hermit two-root build IDENTICAL.*

Each package is its own control, so this is meaningful at small M — which
matters, because the throughput ceiling here is network, not CPU.

## Result

Derived from `results.csv` by `./summarize.sh` (numbers are computed from the
manifest, not transcribed):

| package | version | native two-root | hermit two-root |
|---|---|---|---|
| dos2unix | 6.0-1 | DIVERGES | **IDENTICAL** |
| ed | 1.6-2 | DIVERGES | **IDENTICAL** |
| figlet | 2.2.5-2 | DIVERGES | DIVERGES |
| hostname | 3.11 | DIVERGES | **IDENTICAL** |
| nano | 2.2.6-1 | DIVERGES | DIVERGES |
| time | 1.7-24 | DIVERGES | DIVERGES |
| tree | 1.6.0-1 | DIVERGES | **IDENTICAL** |
| units | 1.88-1 | DIVERGES | **IDENTICAL** |

**8 of 8 packages diverge natively across two roots. 5 of those 8 are byte-identical under `hermit run --strict`.**

Three packages — `figlet`, `nano` and `time` — diverge under Hermit too. They
are reported as-is; I did not diagnose them, and they are the most interesting
follow-up here because they are exactly the cases where Hermit's determinization
is incomplete for a real Debian package.

Four further packages (`bzip2`, `grep`, `indent`, `wdiff`) were still in flight
when the session ended and appear in `results.csv` with fewer than four rows;
`summarize.sh` reports them as INCOMPLETE and excludes them from the totals.

`results.csv` is an append-only manifest keyed by `(package, source_version,
root, executor, artifact_sha256, hermit_sha256, utc)`, so every verdict above
dereferences to four artifact hashes and the exact binary that produced them.

## Scope and honesty about the denominator

This is **8 packages**, selected as small, fast-building members of the
8,688-name ASPLOS'20 target set (the intersection of baseline-irreproducible and
Dettrace-reproducible). It is **not** a sample of that set and must not be
reported as a percentage of it — 8 of 8,688 is a rounding error, and the
selection was convenience-biased toward small packages.

What it *is*: a working, controlled, one-command experiment with a real internal
control, on a rootfs and pipeline that did not run at all at the start of the
session. The bottleneck is per-package `prepare` (apt build-deps plus source
download through a single serialized proxy bridge, roughly 1-3 minutes each),
not build time — native builds take seconds, Hermit builds ~50 s.

Not attempted, deliberately: reprotest environment variations, and a dettrace
oracle arm. Both multiply the build count, and the dettrace arm additionally
needs the modern-vDSO patch rebuilt.

## Harness repairs required to get here

The inherited Debian pipeline (`experiments/debian_reproducible_builds_2026`)
could not complete a bootstrap. Three defects, all fixed in this session:

1. **`rebuild.sh fetch-metadata` refused with `comm: file 1 is not in sorted order`.**
   The checked-in manifests are C-collation sorted, but `comm`/`sort` honour the
   ambient locale and `en_US.UTF-8` orders `-`, `+` and `.` differently. Fixed by
   pinning both sides to `LC_ALL=C`.
2. **`container/finish-wheezy.sh` aborted after a successful second stage.**
   `debootstrap --second-stage` unpacks `debianutils`, which owns
   `/usr/bin/ischroot` and reinstates the real binary itself, consuming the
   backup the script had made. The unguarded `mv` then failed, and under `set -e`
   that killed the script *after* "Base system installed successfully" — so the
   rootfs never received its `sources.list`, `policy-rc.d`, or the
   `.drb-bootstrap-complete` marker, and every later stage refused. Fixed by
   making the restore idempotent.
3. **The failure was unrecoverable.** `debootstrap` deletes `/debootstrap` on
   success, so `finish-bootstrap` could never be re-run: it hard-required that
   file, and the only path forward was rebuilding the rootfs from scratch. Fixed
   on both sides — the script skips an already-completed second stage, and
   `rebuild.sh` accepts either state.

**Container egress also needed a bridge.** `fwdproxy` resolves to an IPv6-only
address that containers could not reach even with `--network=host`
(`Connection failed [IP: 2401:db00:…:1e10 8080]`), and an IPv4 loopback bridge
failed differently (`Could not resolve '127.0.0.1'`). What works is an **IPv6
loopback** bridge:

```bash
setsid ncat --listen ::1 13129 --keep-open --sh-exec "ncat fwdproxy 8080" &
export DRB_CONTAINER_PROXY="http://[::1]:13129"
```

## Reproduction

```bash
# once: metadata + wheezy reconstruction rootfs (needs the bridge above)
cd ../debian_reproducible_builds_2026
DRB_CONTAINER_PROXY="http://[::1]:13129" with-proxy ./rebuild.sh fetch-metadata
DRB_CONTAINER_PROXY="http://[::1]:13129" with-proxy ./rebuild.sh bootstrap

# per package set: prepare (if needed) + four builds + compare
cd ../rb_debian_tworoot_20260807
DRB_CONTAINER_PROXY="http://[::1]:13129" ./tworoot.sh \
  --hermit-bin /path/to/hermit/target/release/hermit  hostname tree ed units

./summarize.sh          # regenerate the verdict table from results.csv
```

`tworoot.sh` prepares a package if it is not already prepared, so the second
command is sufficient from a bootstrapped rootfs. Packages may be run in
parallel by launching several `tworoot.sh` invocations; they share the manifest
append-only and use disjoint run roots.

## Notes on the Hermit invocation

`hermit run --strict --base-env=minimal --network=local`, with the build root
bind-mounted at `/tmp/drb-root` and `/proc` + `/dev` bound into it, then
`chroot`. The bind is load-bearing: Hermit gives the guest a private `/tmp`
tmpfs, so build output written under `/tmp/drb-root` would otherwise be
discarded; the explicit `--bind` maps it back to the real host root. Neither
`--no-namespace` nor `setarch -R` is used — full namespaces are kept.

## Files

- `tworoot.sh` — the experiment: prepare, four builds in four roots, compare.
- `summarize.sh` — derives the verdict table and totals from `results.csv`.
- `results.csv` — append-only artifact manifest, four rows per package.
- `metadata.json` — SHAs, host, toolchain, method, scope.
