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

## Design: six builds, one variable, an internal control

Prepare a source package once, then build it **six** times from that same
prepared tree, in **six different root paths**, across three arms:

```
   native  in root N1        hermit  in root A        hermit --no-rcb-time in root A'
   native  in root N2        hermit  in root B        hermit --no-rcb-time in root B'
```

and compare each pair. The only thing varied within a pair is the path the build
runs in. (The third arm exists because this host's PMU is broken; see below. The
first two arms are the original design.)

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

| package | version | native | hermit | hermit `--no-rcb-time` |
|---|---|---|---|---|
| ack-grep | 1.96-2 | DIVERGES | pending | pending |
| bridge-utils | 1.5-6 | DIVERGES | IDENTICAL | **IDENTICAL** |
| bsdiff | 4.3-14 | DIVERGES | IDENTICAL | **IDENTICAL** |
| bsdmainutils | 9.0.3 | DIVERGES | pending | pending |
| bzip2 | 1.0.6-4 | DIVERGES | IDENTICAL | **IDENTICAL** |
| cabextract | 1.4-3 | DIVERGES | IDENTICAL | **IDENTICAL** |
| cflow | 1:1.4+dfsg1-2 | DIVERGES | IDENTICAL | **IDENTICAL** |
| cgdb | 0.6.6-2 | DIVERGES | IDENTICAL | **IDENTICAL** |
| cmatrix | 1.2a-4 | DIVERGES | IDENTICAL | **IDENTICAL** |
| cscope | 15.7a-3.6 | DIVERGES | IDENTICAL | **IDENTICAL** |
| dialog | 1.1-20120215-2 | DIVERGES | IDENTICAL | **IDENTICAL** |
| dos2unix | 6.0-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| ed | 1.6-2 | DIVERGES | IDENTICAL | **IDENTICAL** |
| ethtool | 1:3.4.2-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| figlet | 2.2.5-2 | DIVERGES | DIVERGES | **IDENTICAL** |
| file | 5.11-2+deb7u8 | DIVERGES | IDENTICAL | **IDENTICAL** |
| flex | 2.5.35-10.1 | DIVERGES | pending | pending |
| gperf | 3.0.3-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| grep | 2.12-2 | DIVERGES | DIVERGES | **IDENTICAL** |
| groff | 1.21-9 | DIVERGES | pending | pending |
| hdparm | 9.39-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| hostname | 3.11 | DIVERGES | IDENTICAL | **IDENTICAL** |
| httping | 1.5.3-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| indent | 2.2.11-2 | DIVERGES | DIVERGES | **IDENTICAL** |
| lftp | 4.3.6-1+deb7u2 | DIVERGES | pending | pending |
| ltrace | 0.5.3-2.1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| lzop | 1.03-3 | DIVERGES | IDENTICAL | **IDENTICAL** |
| moreutils | 0.47 | DIVERGES | IDENTICAL | **IDENTICAL** |
| mtools | 4.0.17-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| nano | 2.2.6-1 | DIVERGES | DIVERGES | **IDENTICAL** |
| ncompress | 4.2.4.4-5 | DIVERGES | pending | pending |
| netcat-openbsd | 1.105-7 | DIVERGES | IDENTICAL | **IDENTICAL** |
| ngrep | 1.45.ds2-12 | DIVERGES | IDENTICAL | **IDENTICAL** |
| numactl | 2.0.8~rc4-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| pax | 1:20120606-2 | DIVERGES | IDENTICAL | **IDENTICAL** |
| pbzip2 | 1.1.8-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| pmount | 0.9.23-2 | DIVERGES | IDENTICAL | **IDENTICAL** |
| psmisc | 22.19-1+deb7u1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| pv | 1.2.0-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| sdparm | 1.07-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| sipcalc | 1.1.5-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| strace | 4.5.20-2.3 | DIVERGES | IDENTICAL | **IDENTICAL** |
| stress | 1.0.1-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| sysfsutils | 2.1.0+repack-2 | DIVERGES | IDENTICAL | **IDENTICAL** |
| sysstat | 10.0.5-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| time | 1.7-24 | DIVERGES | DIVERGES | **IDENTICAL** |
| tofrodos | 1.7.9.debian.1-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| toilet | 0.3-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| tree | 1.6.0-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| tty-clock | 1.1-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| uncrustify | 0.59-2 | DIVERGES | pending | pending |
| units | 1.88-1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| vlan | 1.9-3 | DIVERGES | IDENTICAL | **IDENTICAL** |
| wdiff | 1.1.2-1 | DIVERGES | DIVERGES | **IDENTICAL** |
| whois | 5.1.1~deb7u1 | DIVERGES | IDENTICAL | **IDENTICAL** |
| xdelta | 1.1.3-9 | DIVERGES | pending | pending |
| zip | 3.0-6 | DIVERGES | IDENTICAL | **IDENTICAL** |

**57/57 packages diverge natively across two build roots. 49/49 measured in the corrected arm are byte-identical under `hermit run --strict --no-rcb-time`.**

*(Table and totals generated by `./regen_readme.py` from `results.csv`;
regenerate after any new run.)*

The control fired on **every** package, so not one of the wins is vacuous: each
is a package that provably *was* root-sensitive and provably stopped being so. Rows marked `pending` were still building when measurement stopped; they are
excluded from every total, and `summarize.sh` counts each arm against its own
completed denominator rather than crediting one arm's progress to another.

### The two hermit arms, and why the first one is confounded

The `hermit` column is the arm I ran first, without `--no-rcb-time`. It shows a
shortfall against the corrected arm, and **that shortfall is an artifact of this
host, not a property of Hermit.**

This machine's PMU fails validation — every run logs
`PMU validation failed; RCB timers may be unreliable
error=AmdSpecLockMapShouldBeDisabled`. Hermit's virtual time is derived from
retired-conditional-branch counts, so on a host where those counts are
unreliable, Hermit's own clock becomes a nondeterminism source. `--no-rcb-time`
takes virtual time off the RCB counter and is **required** here.

Adding it moves **every** apparent failure — `figlet`, `grep`, `indent`, `nano`,
`time`, `wdiff` — to IDENTICAL, and changes **none** of the packages that
already passed. All six flips go one way; not one pass regresses. That is the
signature of a measurement artifact, not of six unrelated product gaps suddenly
being fixed by a flag.

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

## Which mechanism is this measuring? (both, and they behave differently)

Varying a build root can cause divergence two ways, and they have opposite
expected outcomes under Hermit:

1. **path-embedded** — the path lands in the output bytes. Hermit should *not*
   fix this, and it would be wrong to: the guest asked where it was and got a
   truthful answer that genuinely differs per root.
2. **path-triggered** — the root change perturbs timing, entropy, allocation or
   iteration order, which then leaks into the output. Hermit *should* fix this.

The buck2 lane predicted that the headline above is entirely mechanism 2, which
implies **no package in the set embeds its build root in the compared output**.
Checked, and the prediction holds — but for a stronger reason than luck.

### The prediction, checked

`./check_path_embedding.sh` unpacks both *native* `.deb` payloads for every
package and greps for the two host root paths and for the differing component:

```
for tag in n1 n2; do ar x <root>/work/*.deb; tar xf data.tar.*; done
grep -rlaF -- "<host root path>" .
grep -rlaF -- "native-n1" . ; grep -rlaF -- "native-n2" .
```

**Result: 0 of 28 packages embed the host build root path** (`path_embedding_check.txt`).
The check is not inert: on `hostname`'s payload the same grep finds `hostname`
and correctly fails to find an absent control string.

The check also reports which build directory each package *does* record, and
that is the informative part: `groff` embeds
`/work/build/src/libs/libgroff/errarg.cpp` and `hdparm` embeds `/work/build`.
So these packages **do** bake their build path into shipped artifacts — they
simply bake in the *guest* path, which this design holds constant across roots.
The result is therefore not "these packages happen not to embed paths"; it is
"the path they embed is the one that does not vary here".

### Why it could not have gone the other way here

The deeper reason is structural, and it bounds what this experiment measures.
The build always runs *inside* the rootfs — `podman --rootfs`, or Hermit plus
`chroot` — so the **guest-visible build directory is `/work/build` in all four
roots**, verified from `etc/drb-source-dir` in each. Only the *host* path
differs, and the build never sees it.

So this experiment **holds the guest-visible path constant and varies the host
root**: it isolates mechanism 2 by construction, and mechanism 1 is excluded
rather than merely absent. The correct reading of the headline is therefore
sharper than "Hermit made N packages reproducible":

> **Hermit eliminated every path-*triggered* divergence in the set. The experiment says nothing about path-*embedded* divergence.**

### Closing that gap: varying the guest-visible path

`./guestpath_arm.sh` builds the same source at two guest paths of different
length (`/work/build` vs
`/work/build-with-a-substantially-longer-directory-name`):

| package | native | hermit `--no-rcb-time` |
|---|---|---|
| hostname 3.11 | DIFFERS | DIFFERS |
| tree 1.6.0-1 | DIFFERS | DIFFERS |
| zip 3.0-6 | DIFFERS | DIFFERS |

**Control** (`./samepath_control.sh`, same long path built twice under
Hermit): `hostname`, `tree`, `zip` all **IDENTICAL**. So the short-vs-long
difference is caused by the path itself, not by residual run-to-run
nondeterminism — Hermit is deterministic at either path, and legitimately
reports a different artifact for a different path.

Both mechanisms are therefore reproduced in the same corpus, with the predicted
opposite outcomes. The two-mechanism model is **confirmed**.

### One loose end worth recording

For `hostname` the mechanism-1 difference is **19 bytes, all in the ELF
build-id** (`d977c152…` vs `5db02718…`); everything else in the package —
including the `tar` listing and every mtime — is identical. The path string is
*not* present in the stripped artifact, in either variant. So Debian's
`dh_strip` removes the visible build path while a path-dependent fingerprint
survives in the build-id. The exact propagation channel into that hash is not
localized here; it is recorded as an observation, not an explanation.

## Scope and honesty about the denominator

This is a convenience-biased selection, not a sample, drawn from as small, fast-building members of the
the 8,688-name ASPLOS'20 target set (the intersection of baseline-irreproducible
and Dettrace-reproducible). It **must not** be reported as a percentage of that
set — the count here is a rounding error against 8,688, and packages were chosen
for being small and fast to build, which plausibly correlates with being easy to
determinize. A defensible percentage needs a randomized draw; see followups.

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

## Attempted but not measured, and why

Not every package selected produced a verdict. Recording the taxonomy so the
denominator is auditable rather than quietly shrinking. Note the shape: of the
packages that failed, exactly one (`ack-grep`) is an unexplained Hermit-side
failure; the rest are package-side, harness-side, or explained.

| package | outcome | cause |
|---|---|---|
| `socat` | native build failed | `dh_auto_build: make -j1 returned exit code 2` — does not build in this Wheezy reconstruction at all. Excluded; nothing to do with Hermit. |
| `bsdmainutils` | native OK, **Hermit build failed** | `chown: changing ownership of .../usr/bin/bsd-write: Invalid argument` — see the id-mapping note below. |
| `less`, `bc` | prepare refused | A partially prepared root from an earlier aborted run exists and `rebuild.sh prepare` preserves it rather than overwrite. `rebuild.sh resume-prepare` is the intended path; not run here. Harness resumability gap, not a package or Hermit problem. |
| `ack-grep` | native OK, **Hermit build failed** | `make[1]: /work/build/0: Command not found` — a Perl `ExtUtils::MakeMaker` Makefile expanded a command variable to the literal `0`, so the build ran `/work/build/0`. Undiagnosed; unlike `bsdmainutils` this one is **not** explained by the id-mapping asymmetry, so it is a genuine lead for a Hermit/Perl interaction rather than a known-benign difference. |

### The two executors do not have the same privilege environment

Worth stating plainly, because it explains `bsdmainutils` and bounds what may be
concluded across arms. Guest id maps, read from `/proc/self/gid_map`:

```
hermit guest :  0  100  1                      <- exactly one GID mapped
podman native:  0  100  1
                1  1879113728  65536           <- 65536 more
```

Hermit's container maps a single id (`map_root()`); the native podman container
maps a 65537-id range. So `chown(file, 0, 5)` — what `bsdmainutils` does for
`root:tty` — returns `EINVAL` under Hermit because GID 5 is unmapped, and
succeeds natively. **This is correct user-namespace behavior, not a Hermit
defect**, and it was checked directly with a minimal probe:
`chown(f,0,0)` and `chown(f,0,-1)` succeed under Hermit while `chown(f,0,5)` and
`chown(f,-1,5)` return `EINVAL`.

The consequence for this experiment is bounded: **every comparison here is
within one executor** (native n1-vs-n2, hermit a-vs-b), never across them, so
the asymmetry cannot affect a verdict. What it does mean is that a package can
be buildable in one arm and not the other, and that no cross-executor claim
(e.g. "Hermit builds are equivalent to native builds") is supported by this
data.

## Reproduction

```bash
# once: metadata + wheezy reconstruction rootfs (needs the bridge above)
cd ../debian_reproducible_builds_2026
DRB_CONTAINER_PROXY="http://[::1]:13129" with-proxy ./rebuild.sh fetch-metadata
DRB_CONTAINER_PROXY="http://[::1]:13129" with-proxy ./rebuild.sh bootstrap

# per package set: prepare (if needed) + six builds across three arms + compare
cd ../rb_debian_tworoot_20260807
DRB_CONTAINER_PROXY="http://[::1]:13129" ./tworoot.sh \
  --hermit-bin /path/to/hermit/target/release/hermit  hostname tree ed units
# tworoot.sh runs the corrected --no-rcb-time arm itself. On a host whose PMU
# fails validation that arm is the authoritative one; the plain arm is kept only
# so the confound stays auditable.
# KEEP_ROOTS=1 retains the run roots (check_path_embedding.sh needs them).

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

## Disk cost, and a measurement trap worth knowing

Run roots are `cp -a --reflink=auto` copies on **btrfs**, so they share extents
with the prepared root until a build writes. That makes `du` badly misleading
here: it reports **logical** size, counting each reflinked copy in full.

At peak this experiment's `ignored/` measured **94 GB by `du`** while its actual
physical footprint was a small fraction of that. After reclaiming every
fully-measured package:

```
$ btrfs filesystem du -s --gbytes rb_debian_tworoot_20260807/ignored
     Total   Exclusive  Set shared  Filename
   4.09GiB     0.23GiB     0.73GiB  rb_debian_tworoot_20260807/ignored
```

**Total 4.09 GiB, exclusive 0.23 GiB.** Read the *Exclusive* column; `du` and
the *Total* column both include shared extents that freeing this directory would
not return. Anyone attributing free-space decline on a shared btrfs host needs
`btrfs filesystem du -s`, not `du -sh`, or they will chase the wrong lane.

The durable cost of the pipeline is the prepared-root cache in
`debian_reproducible_builds_2026/ignored` (20.4 GiB total, **10.04 GiB
exclusive**) — 35 unpacked Wheezy roots with build-deps installed. That is worth
keeping: it is what makes re-running a package cheap.

`tworoot.sh` now reclaims each package's six run roots inside the per-package
loop, as soon as its hashes are in `results.csv`, so peak footprint is bounded by
packages in flight rather than by queue length. `KEEP_ROOTS=1` retains them,
which `check_path_embedding.sh` needs since it reads the native payloads.

## Files

- `tworoot.sh` — the experiment: prepare, then six builds across three arms in
  six roots, compare each pair, and reclaim the roots.
- `summarize.sh` — derives the verdict table and each arm's own denominator from
  `results.csv`. **Regenerate the table above with this after any new run.**
- `check_path_embedding.sh` — mechanism-1 grep over both native payloads.
- `guestpath_arm.sh` — mechanism-1 probe: varies the guest-visible build path.
- `samepath_control.sh` — its control: same long guest path built twice.
- `results.csv` — append-only artifact manifest, six rows per fully measured
  package, keyed by `(package, source_version, root, executor, artifact_sha256,
  hermit_sha256, utc)`.
- `mechanism_probe.csv` — guest-path arm and control verdicts.
- `mechanism_probe_hashes.csv` — their artifact hashes, captured before those
  roots were reclaimed.
- `path_embedding_check.txt` — raw output of `check_path_embedding.sh`.
- `regen_readme.py` — regenerates the table and headline above from
  `results.csv`, so the prose cannot drift from the manifest.
- `metadata.json` — SHAs, host, toolchain, method, scope, caveats.
