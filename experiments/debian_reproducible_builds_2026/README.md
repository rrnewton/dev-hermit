# ASPLOS'20 Debian reproducible-build evaluation point

Research date: 2026-07-29. This note pins the Debian experiment in
*Reproducible Containers* [1] to its public source and result artifacts.
`metadata.json` records immutable IDs and hashes; `evaluation_counts.csv`
transcribes the recovered result matrix.

## Exact package sets

The final experiment matrices are public in
`upenn-acg/dettrace-experiments` [2]. They reproduce every cardinality in the
paper, and permit the name-level sets to be recovered:

| Set | Count | Local manifest | SHA-256 |
|---|---:|---|---|
| Canonical Wheezy corpus | 17,145 | `asplos20_all_packages.txt` | `8a3ca4ba7f76921f08549437d067f79c41e7792a8f768bc9d70dae87eadd8bf7` |
| Baseline irreproducible | 11,958 | `asplos20_baseline_irreproducible.txt` | `0c03193042caaf8bf9b709ceb9af207967fed83c41b902a05d3112c4e48e5874` |
| **Primary Hermit target:** baseline irreproducible and Dettrace reproducible | **8,688** | `asplos20_dettrace_reproduced_target.txt` | `8ea518b8b4cb49b21821860b107aab09a4a46f0ad897b5a090ac270efbf0df1c` |

The 8,688-name target is the intersection of `BUILDSTATUS=unreproducible` in
the final baseline matrix and `BUILDSTATUS=reproducible` in the final Dettrace
matrix. It is not the repository's older summer-2018 matrix, the six-package
smoke benchmark, or the paper's 407-package comparison against newer Stretch.

Reproduction of the manifests from checkout
`e6fd33c5865fc65d2551c379493e1ae1d7f9c96e`:

```sh
awk -F, 'NR>1 && $10=="unreproducible" {print $4}' \
  data/sosp2019/summary_baseline_standard.csv | sort -u > baseline.txt
awk -F, 'NR>1 && $10=="reproducible" {print $4}' \
  data/sosp2019/summary_dettrace_concurrent_standard.csv | sort -u > dettrace.txt
comm -12 baseline.txt dettrace.txt > target.txt
```

The exact source **versions/checksums** remain unavailable. `buildPackages.py`
logs `apt-cache show` before entering the Wheezy chroot, so the matrices'
`VERSION` values describe the Ubuntu 18.04 outer container (or are blank), not
reliably the Wheezy sources actually built [3]. Source versions must therefore
come from the missing mirror manifest, not the CSV `VERSION` column.

## Evaluation revisions and dates

- Experiments repository:
  `https://github.com/upenn-acg/dettrace-experiments`.
- Source/config tag `ASPLOS2020Submission`: annotated tag object
  `01f257228cde62ce9f6068e62f6371ce6c772e41`, commit
  `1fa20374c1390ebd9440c599d39774e53f3ada41` [3]. Its gitlinks pin Dettrace
  `3c27bce648fecc1495721d30bd35030df6743e3d` and the modified reprotest
  `0386340dc7580539df46430b43d4833d964a7aa1`.
- Final baseline matrix: added at
  `0330527e6d6e68cb34650fe94c7e3e2c49b2cfce`; SHA-256
  `ece5e5d76c3055b00aef82c7a6d5a14505781b4925af4302a0c115ad5f09bf68`.
- Final Dettrace matrix: last corrected at
  `501e8e6b645f8f7ad5168657ea536652a8a3cd44`; SHA-256
  `43aff3231ada47be34b8f01116fe2234e143756fccd837c37d91c52f4261f9ea`.
  Both matrices are present at requested checkout `e6fd33c5...` [2].
- Recorded run directories date the baseline to **2019-04-05 10:01:08** and
  the two Dettrace shards to **2019-04-20 21:09:52** and
  **2019-04-21 10:08:23** [4]. These are experiment dates, not archive
  snapshot timestamps.
- The latest public experiment commits before those directory timestamps are
  `d3072e12f168d490965ec35554e636be974b98b5` for the baseline run and
  `745dca81d0c1d2bf54756fee6ec1142ecdc51e5f` for the first Dettrace shard.
  The latter pins Dettrace `3c27bce...`; the official config tag follows it by
  about an hour and changes only the Slurm argument parser.

The Dettrace annotated tag `ASPLOS2020Submission` (tag object
`4038b0edd5fb5210d6a20bffdfd89f79de58caa3`) independently resolves to the
same `3c27bce...` commit [5]. Use that commit, not either repository's current
`master`.

## Debian archive point: exact known boundary

The suite is Debian 7 **Wheezy**, with a canonical 17,145-name package list
first committed on 2018-04-18 and deduplicated on 2018-04-22. The builds used a
read-only, on-disk mirror mounted from `/datasets/WheezyRepoMirror` and an
external `/datasets/wheezy.tar.gz` base image [3]. Neither object was checked
in, and the repository contains no snapshot URL, mirror creation date,
`Release`/`Sources` hashes, or source tuple manifest.

Therefore the exact archive **snapshot/date cannot be recovered from the
public artifact**. The April 2019 directory dates only bound when the builds
ran. The name list also does not identify a snapshot: official
`snapshot.debian.org` probes for 2018-06-01, 2019-01-01, and 2019-03-01 each
contain 17,175 stanzas in `wheezy/main/source/Sources.gz`, not 17,145. The
artifact may have used a locally retained mirror and its own filtering rule.

An exact source-level replay still requires the authors' archived
`WheezyRepoMirror` metadata or, minimally:

1. archive timestamp(s), suite/components/architecture, and Release/Sources
   hashes;
2. source tuples `(Package, Version, Directory, Files checksums)` for all
   17,145 names; and
3. the `wheezy.tar.gz` hash plus its APT source configuration.

Without these, the exact **name-level** ASPLOS target is pinned, but any source
retrieval must be labelled a Wheezy reconstruction rather than an exact
archive replay.

## Configuration and method

The recovered driver supplies details omitted by the paper:

- Outer image: Ubuntu 18.04; fresh Docker invocation per package; two copied
  minimal Wheezy trees; read-only mirror bind-mounted into both [3].
- Modified reprotest: upstream version 0.7.8 at
  `0386340dc7580539df46430b43d4833d964a7aa1`, whose single patch adds
  `--seed`; the driver fixes it to **`1234567890`** [3,6].
- Variations exactly:
  `+all,-domain_host,-kernel,-fileordering,-umask`. This varies environment,
  build path, ASLR, CPU count, time, groups, home, locales, executable path,
  and timezone while disabling the four named variations [1,3].
- Reprotest options: `--store-dir`, the variations above,
  `--testbed-init 'mount proc ... && mount devpts ...'`, `--no-diffoscope`,
  `--seed=1234567890`, artifact pattern `*.deb`, and chroot backend rooted at
  `/dettrace-experiments/wheezy/` [3].
- Before each pair, build dependencies are installed in the control chroot,
  then `rsync` clones it to the experimental chroot. Source extraction uses
  `faketime '1984-1-1'` [3].
- Baseline command: single-CPU
  `taskset -c 0 dpkg-buildpackage -uc -us -b`. Baseline `.deb`s are unpacked
  with `dpkg-deb -R`, normalized file-by-file using
  `strip-nondeterminism --timestamp 0`, and compared by
  `diffoscope --exclude-directory-metadata` [3].
- Dettrace command:
  `/dettrace/bin/dettrace --print-statistics --currentAsChroot
  --timeoutSeconds 7200 bash -c "cd build && dpkg-buildpackage -uc -us -b"`.
  The entire build, tests, and packaging execute under Dettrace [1,3].
- Timeout nuance: the paper's initial buildability screen used 30 minutes,
  while the launcher at the April 5 final baseline rerun used four hours
  (`--timeout 14400`). The April 20 Dettrace launcher used two hours
  (`--timeout 7200`), matching the paper. Preserve both baseline stages rather
  than collapsing them into one deadline. The host was CloudLab `c220g5`: 2x
  Intel Xeon Silver 4114, 192 GiB RAM, Ubuntu 18.04 LTS, Linux 4.15, with the
  full seccomp-BPF optimization [1,3].

## Exact result partition

The baseline standard matrix contains 15,761 packages: 3,803 reproducible and
11,958 irreproducible. The Dettrace matrix partitions the same names into
12,130 reproducible, 1,582 timeout, 708 `dettracefailed`, and 1,341
`buildfailed`; it contains no Dettrace-irreproducible package.

| Baseline class | DT reproducible | DT buildfailed | DT dettracefailed | DT timeout |
|---|---:|---:|---:|---:|
| Irreproducible (11,958) | **8,688** | 1,260 | 652 | 1,358 |
| Reproducible (3,803) | 3,442 | 81 | 56 | 224 |

This resolves Table 1's seemingly inconsistent unsupported counts. Its top
half aggregates `buildfailed + dettracefailed` as unsupported: 1,912 for the
baseline-irreproducible row and 137 for the reproducible row (2,049 total).
Its bottom half reports only the explicit `dettracefailed` class: 652 + 56 =
708, omitting the separate 1,341 `buildfailed` rows.

## Rebuild infrastructure plan

1. **Recover or declare the archive.** First request the three mirror items
   above. If unavailable, select one explicit snapshot timestamp, hash its
   Release/Sources files and every source tuple, and name it
   `wheezy-reconstruction`; never present it as the lost original mirror.
2. **Immutable local mirror.** Materialize the selected timestamp using
   `snapshot.debian.org`'s `/archive/debian/YYYYMMDDThhmmssZ/` URLs. Wheezy APT
   must use `Acquire::Check-Valid-Until=false` [7]. Serve a content-addressed,
   read-only local mirror and deny network access during builds.
3. **Fidelity lane first.** Recreate the Ubuntu 18.04/Linux 4.15 outer image,
   two cloned Wheezy chroots, exact driver at `1fa2037...`, reprotest
   `0386340...`, seed `1234567890`, and Dettrace `3c27bce...`. Use the paper's
   30-minute initial buildability screen, the final baseline rerun's four-hour
   ceiling, and the Dettrace run's two-hour ceiling. Verify the recovered
   matrices and a sample of recorded output hashes before comparing Hermit.
4. **Hermit differential.** For every name in
   `asplos20_dettrace_reproduced_target.txt`, build the same source tuple twice
   natively, twice under pinned Dettrace, and twice under a pinned Hermit SHA
   using identical fixed variations. Keep original `.deb`s, normalized
   comparisons, exit/signal/timeout class, wall time, logs, and SHA-256.
5. **Scale without shared state.** One fresh writable chroot per package;
   read-only source/mirror inputs; no network. Parallelize between packages,
   never within a paired comparison. Resume from an append-only manifest keyed
   by source tuple, executor SHA, environment hash, and attempt.
6. **Modern scalable lane.** `sbuild`'s unshare backend or `mmdebstrap` [8,9]
   can improve orchestration only after matching a fidelity sample. A modern
   builder result is not, by itself, an exact historical replay.
7. **Bring-up order.** Use the old six-package benchmark only for plumbing;
   validate with the paper's named examples (Blender, core TeX/LaTeX, LLVM
   3.0/Clang), then a stratified 100-package pilot, then all 8,688 targets.

## Runnable reconstruction bring-up

`rebuild.sh` implements the first runnable lane. It deliberately calls this a
**Wheezy reconstruction**, not an exact replay of the lost paper mirror. It
uses snapshot `20190301T000000Z`; `reconstruction.json` pins the Release,
Sources, builder-image, Hermit, and pilot-source hashes. All 8,688 target names
resolve in this snapshot. The small plumbing pilot is `hello` source `2.8-2`.

The network is available only while fetching indices, bootstrapping the rootfs,
and installing package build dependencies. The native and Hermit build steps
run with external networking disabled. Every heavy command is automatically
routed through `scripts/detached-verify.rs`.

```sh
cd experiments/debian_reproducible_builds_2026
with-proxy ./rebuild.sh fetch-metadata
with-proxy ./rebuild.sh bootstrap
with-proxy ./rebuild.sh prepare hello
./rebuild.sh native hello
HERMIT_BIN=/path/to/pr1160-hermit/target/release/hermit \
  ./rebuild.sh hermit hello
```

The Hermit command uses the default ptrace backend with `--strict --verify`,
default logging, and no determinism relaxations. It exposes the prepared
Wheezy directories at the same guest-visible paths for both runs. A successful
result is L2 evidence for that package/build tuple, not a claim about packages
that have not run.

On Meta hosts where rootless Podman cannot reach fwdproxy's IPv6 address from
its network namespace, expose a loopback-only bridge in a second terminal and
pass it to the setup steps:

```sh
ncat -6 -l ::1 18081 --keep-open --sh-exec 'ncat fwdproxy 8080'
with-proxy env 'DRB_CONTAINER_PROXY=http://[::1]:18081' ./rebuild.sh bootstrap
with-proxy env 'DRB_CONTAINER_PROXY=http://[::1]:18081' ./rebuild.sh prepare hello
```

The full 8,688-package sweep must use the separate parallel experiment runner:
one prepared writable root per package, paired builds never concurrent with
each other, and an append-only result manifest. Until that dependency lands,
this harness intentionally performs one named package at a time.

### Dettrace oracle on newer kernels

The pinned ASPLOS'20 Dettrace source builds unmodified in Ubuntu 18.04, but its
startup check assumes the host vDSO exports exactly four global functions. A
modern kernel exports seven and is rejected before tracing. For an oracle run
on such a host, apply `dettrace-modern-vdso.patch` to commit `3c27bce...`.
The patch filters the discovered map to Dettrace's same four existing target
functions; it does not alter their replacement bytes or the scheduler. Keep the
unmodified-launch failure and adapted-binary hash with the results so this host
compatibility exception remains explicit.

## Sources

1. O. S. Navarro Leija et al., *Reproducible Containers*, ASPLOS 2020,
   [DOI](https://doi.org/10.1145/3373376.3378519),
   [author PDF](https://krs85.github.io/dettrace.pdf). PDF SHA-256:
   `6346dfa0e5ea193fb975d5330da80d030f2a7641d95c7063069c8f8d7d875539`.
2. [Final result matrices at `e6fd33c5`](https://github.com/upenn-acg/dettrace-experiments/tree/e6fd33c5865fc65d2551c379493e1ae1d7f9c96e/data/sosp2019).
3. [Pinned experiment source/config](https://github.com/upenn-acg/dettrace-experiments/tree/1fa20374c1390ebd9440c599d39774e53f3ada41).
4. [Result-data provenance and run directory dates](https://github.com/upenn-acg/dettrace-experiments/blob/e6fd33c5865fc65d2551c379493e1ae1d7f9c96e/data/sosp2019/README.md).
5. [Dettrace ASPLOS commit](https://github.com/dettrace/dettrace/tree/3c27bce648fecc1495721d30bd35030df6743e3d).
6. [Modified reprotest 0.7.8 with `--seed`](https://github.com/upenn-acg/reprotest/tree/0386340dc7580539df46430b43d4833d964a7aa1).
7. [Debian Snapshot usage and timestamp semantics](https://snapshot.debian.org/).
8. [Debian `sbuild` manual](https://manpages.debian.org/bookworm/sbuild/sbuild.1.en.html).
9. [Debian `mmdebstrap` manual](https://manpages.debian.org/bookworm/mmdebstrap/mmdebstrap.1.en.html).
