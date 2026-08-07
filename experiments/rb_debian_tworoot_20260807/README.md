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

Derived from `results.csv` by `./summarize.sh` (computed from the manifest, not
transcribed):

| package | version | native two-root | hermit | hermit `--no-rcb-time` |
|---|---|---|---|---|
| bzip2 | 1.0.6-4 | DIVERGES | IDENTICAL | **IDENTICAL** |
| dos2unix | 6.0-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| ed | 1.6-2 | DIVERGES | IDENTICAL | **IDENTICAL** |
| figlet | 2.2.5-2 | DIVERGES | DIVERGES | **IDENTICAL** |
| grep | 2.12-2 | DIVERGES | DIVERGES | **IDENTICAL** |
| hostname | 3.11 | DIVERGES | IDENTICAL | **IDENTICAL** |
| indent | 2.2.11-2 | DIVERGES | DIVERGES | **IDENTICAL** |
| nano | 2.2.6-1 | DIVERGES | DIVERGES | **IDENTICAL** |
| time | 1.7-24 | DIVERGES | DIVERGES | **IDENTICAL** |
| tree | 1.6.0-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| units | 1.88-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| wdiff | 1.1.2-1 | DIVERGES | DIVERGES | **IDENTICAL** |
| zip | 3.0-6 | DIVERGES | IDENTICAL | **IDENTICAL** |

**13 of 13 packages diverge natively across two roots. 13 of 13 are byte-identical under `hermit run --strict --no-rcb-time`.**

The control fired on every package, so not one of the 13 wins is vacuous: each
is a package that provably *was* root-sensitive and provably stopped being so.

### The two hermit arms, and why the first one is confounded

The `hermit` column is the arm I ran first, without `--no-rcb-time`. It shows
7/13, and **that number is an artifact of this host, not a property of Hermit.**

This machine's PMU fails validation — every run logs
`PMU validation failed; RCB timers may be unreliable
error=AmdSpecLockMapShouldBeDisabled`. Hermit's virtual time is derived from
retired-conditional-branch counts, so on a host where those counts are
unreliable, Hermit's own clock becomes a nondeterminism source. `--no-rcb-time`
takes virtual time off the RCB counter and is **required** here.

Adding it moves all six apparent failures — `figlet`, `grep`, `indent`, `nano`,
`time`, `wdiff` — to IDENTICAL, and changes none of the seven that already
passed. That is the signature of a measurement artifact, not of six unrelated
product gaps suddenly being fixed by a flag.

**Retraction.** An earlier version of this file described those six as cases
where "Hermit's determinization is incomplete" and floated hermit#1850 (pipe
readiness/EOF not delivered) as a possible cause of the `figlet` divergence.
Both statements were wrong and are withdrawn. `figlet` builds successfully and
byte-identically under Hermit on this host once virtual time is off the broken
PMU; there is no hang and nothing here supports a link to #1850.

The lesson is worth more than the retracted claim: **an unreliable PMU makes
Hermit look nondeterministic in a way that is easy to mistake for a product
bug.** Anything measuring Hermit determinism on this class of host must pass
`--no-rcb-time` or its results are confounded. Both arms are kept in
`results.csv` precisely so that comparison is auditable.

`results.csv` is an append-only manifest keyed by `(package, source_version,
root, executor, artifact_sha256, hermit_sha256, utc)`, so every cell above
dereferences to per-root artifact hashes and the exact binary that produced them.

## Scope and honesty about the denominator

This is **13 packages**, selected as small, fast-building members of the
8,688-name ASPLOS'20 target set (the intersection of baseline-irreproducible and
Dettrace-reproducible). It is **not** a sample of that set and must not be
reported as a percentage of it — 13 of 8,688 is a rounding error, and the
selection was convenience-biased toward small packages.

What it *is*: a working, controlled, one-command experiment with a real internal
control, on a rootfs and pipeline that did not run at all at the start of the
session. The bottleneck is per-package `prepare` (apt build-deps plus source
download through a single serialized proxy bridge, roughly 1-3 minutes each),
not build time — native builds take seconds, Hermit builds tens of seconds.

**On those durations:** they are host wall-clock, observed from outside the
guest, and they are approximate — this harness does not record per-package
timings, and `results.csv` deliberately contains none. That is on purpose. A
duration printed *by* a build running under Hermit is **virtual** time, not
wall: Hermit virtualizes the clock, so a stdenv-style `$SECONDS` or a `time`
line emitted from inside the guest reads Hermit's clock and is not a
measurement of how long anything took on this machine (`sleep 1` inside the
guest can print `SECONDS=3`). No overhead or slowdown factor should be derived
from anything in this experiment; if one is needed, measure it on the host and
say so explicitly.

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
# On a host whose PMU fails validation, add --no-rcb-time to the hermit builds
# in tworoot.sh (build_hermit); without it Hermit's own clock is a
# nondeterminism source and the measurement is confounded.

./summarize.sh          # regenerate the verdict table from results.csv
```

`tworoot.sh` prepares a package if it is not already prepared, so the second
command is sufficient from a bootstrapped rootfs. Packages may be run in
parallel by launching several `tworoot.sh` invocations; they share the manifest
append-only and use disjoint run roots.

## Notes on the Hermit invocation

`hermit run --strict --no-rcb-time --base-env=minimal --network=local`, with the build root
bind-mounted at `/tmp/drb-root` and `/proc` + `/dev` bound into it, then
`chroot`. The bind is load-bearing: Hermit gives the guest a private `/tmp`
tmpfs, so build output written under `/tmp/drb-root` would otherwise be
discarded; the explicit `--bind` maps it back to the real host root. Neither
`--no-namespace` nor `setarch -R` is used — full namespaces are kept.
`--no-rcb-time` is required on this host (see the two-arm discussion above);
`--max-timeslice=disabled` is used in the corrected arm as a free speedup and
does not affect the artifacts.

## Files

- `tworoot.sh` — the experiment: prepare, four builds in four roots, compare.
- `summarize.sh` — derives the verdict table and totals from `results.csv`.
- `results.csv` — append-only artifact manifest, four rows per package.
- `metadata.json` — SHAs, host, toolchain, method, scope.
