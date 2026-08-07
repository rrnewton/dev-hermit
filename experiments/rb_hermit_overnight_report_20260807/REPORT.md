# Hermit for reproducible builds — overnight findings, 2026-08-06/07

**Status: LIVING DOCUMENT.** Every claim cites the directory and commit that produced it.
**`HOLE`** = not yet published; deliberately empty rather than estimated.

Tags: **[M]** measured here · **[C]** carried, measured by another agent, not re-derived ·
**[I]** inferred · **[NR]** designed, not run. Do not promote **[C]**/**[I]** to a headline
without re-deriving.

Team: `claude-coord-176`. Host: `devbig176`, 176-core AMD EPYC 9D64.

## 1. The corrections: three refuted claims, one shape

Three load-bearing claims in `nix-hermit-execbuilder-prototype_20260729` were refuted, all the
same way: *a build-output observation attributed to a cause nobody measured.*

Source: `rb_no_namespace_random_leaks_20260806` (parent `76117cd954a88df48d94de8cde8bb9c7859a63c8`).
Five randomness probes + two write probes, four modes, N=10, `native` as positive control. **[M]**

| source | `native` | `hermit run` | `--tmp=/tmp` | `--no-namespace` |
|---|---|---|---|---|
| `at_random` | 10/10 | 1 | **1** | 1 |
| `gettimeofday` | 10/10 | 1 | **1** | 1 |
| `procfs_uuid` | 10/10 | 1 | **1** | 1 |
| `getpid` | 10/10 | 1 | **1** | **10 — LEAK** |
| `bash_random` | 10/10 | 1 | **1** | **3–5 — LEAK** |
| `write_visible_tmp` | visible | **DISCARDED** | **visible** | visible |
| `write_visible_out` | visible | visible | **visible** | visible |

1. *"`--no-namespace` is required; the private mount ns discards `$out`."* **Refuted.** A mount
   namespace isolates the mount *table*, not file contents. Only `/tmp` is discarded — Hermit's
   private tmpfs — and with `sandbox = false` Nix builds in `/tmp/nix-build-*`, so the **build
   directory** vanished and was misread as "`$out` doesn't persist".
2. *"`AT_RANDOM` isn't virtualized."* **Refuted.** Identical in every mode; Detcore rewrites it in
   `handle_post_exec`, a post-exec hook namespaces never gated.
3. *"`/proc/sys/kernel/random/uuid` reads the host RNG."* **Refuted.** Intercepted by path
   (`ProcfsKind::RandomUuid`). Resolves the standing contradiction in favour of
   `rb_nix_minimum_hermit_dose_20260730`, and reproduces on the bare host — **rootless podman was
   never the reason.**

**Actionable line:** the wrapper should be
`exec <hermit> run --tmp=/tmp --no-rcb-time -- <stdenv-bash> "$@"` — not `--no-namespace`, and no
`setarch -R` (full-namespace mode pins ASLR itself). `--no-rcb-time` is required on this host per
the nix lane's N=20 finding: the PMU fails validation (`AmdSpecLockMapShouldBeDisabled`) and
Hermit's default logical clock is RCB-driven. **[M] + [C]**

The one real leak is `getpid()`: Hermit's PID determinism comes from the PID namespace, not
syscall virtualization, and `--no-namespace` silently voids the documented "fixed-container"
precondition for the whole identity family. `bash $RANDOM` is the consequence and is
**intermittent** (3/10 then 5/10) — which is why it was mis-attributed twice.

> **Honesty note.** That `getpid` is the *only* varying seed input is **[I]**, by elimination. A
> reimplementation of bash 5.1's `seedrand`/`intrand32` did **not** reproduce the observed
> triples. The leak is measured; the bash internal is inferred.

Stale claims are annotated in place in the prototype README (same commit).

## 2. The architectural boundary

Source: `rb_nix_namespace_refusal_20260807` (parent `fab2a5d15a410c82ee27f00de7752f773e159164`). **[M]**

**You cannot run nix itself under Hermit and ask it to build — in any mode.** Detcore
deterministically refuses `unshare`, `setns`, `mount`, `umount2`, `mount_setattr`, `move_mount`,
`open_tree`, `fsopen`, `fsmount`, `fsconfig`, `fspick` at a fixed `-EPERM`; the in-source comment
names "user-namespace unshare" as keeping the pinned container bitwise-identical under `--verify`
and record/replay. nix 2.3 unshares a mount namespace before every builder.
**A collision between two correct designs, not a defect on either side.**

| probe | native | `hermit run` | `--image` |
|---|---|---|---|
| `unshare --user` | OK | **EPERM** | **EPERM** |
| `unshare --mount` | OK | **EPERM** | **EPERM** |
| `nix-build`, chroot store | — | — | `writing to file: EPERM` |
| `nix-build`, default store | — | — | `setting up a private mount namespace: EPERM` |

Killed before reporting: **not** image mode's `chroot(2)` (default mode does no chroot and still
EPERMs); **not** the diverted store (default store fails one step *earlier*). Native
`unshare --user` succeeds, so not host policy.

| approach | works? |
|---|---|
| Hermit wraps the **builder process** the build system `execve`s | **YES** — build system does its namespace work outside Hermit |
| Hermit wraps **the build system itself** | **NO** |

`--image` is "deterministic file inputs for a guest", **not** "a nix daemon under Hermit".
Separately, the chroot-store `chown … Invalid argument` is a uid-map miss (`nixbld` is gid 30000,
outside the single-id map); `--option build-users-group ""` skips it and `nix-instantiate` then
succeeds. **[M]**

## 3. Per-track results

### Nix

Dirs: `rb_no_namespace_random_leaks_20260806`, `rb_nix_minimum_hermit_dose_20260730`,
`nix_hermit_repro_hostnix_20260806`, `rb_nix_namespace_refusal_20260807`

| result | denominator | tag |
|---|---|---|
| `--tmp=/tmp` determinizes all 5 probed sources, host writes visible | 5 sources × N=10 | **[M]** |
| Minimum dose **`--tmp=/tmp --no-rcb-time`**; `nondet-time` 20 distinct native → **1**; `nondet-seconds` 16 → **1** | N=20 | **[C]** |
| **Real nixpkgs: K=17 attempted, 13 measurable, M=1 nondeterministic found (`lensfun-0.3.4`), N=0 reproduced** | 17 packages | **[C]** |
| nix builds cannot run with nix itself under Hermit | 2 store configs, 3 probes | **[M]** |
| **CA store is not a usable oracle** — `nix --check` does not detect nondeterminism in `__contentAddressed` on nix 2.30.2 (nix#5336 reproduces) | 1 derivation | **[C]** |

**N=0 reproduced is the honest headline for real nixpkgs.** The blocker is compatibility, not
determinization: **cmake configure hangs under Hermit** (90 s reproducer), and **fake uid 0 breaks
`tar` in `unpackPhase`** for every tarball-sourced package. Both are Hermit bugs found tonight and
both block real nixpkgs work.

> ⚠️ **`/nix` on this host is chef-ephemeral** — Meta devservers revert it. Run `bootstrap.sh check`
> before trusting anything in that dir.

### Debian / ASPLOS'20

Dirs: `debian_reproducible_builds_2026`, `rb_debian_tworoot_20260807`

Reference target set, recovered from public `upenn-acg/dettrace-experiments` matrices **[C]**:
17,145 canonical Wheezy corpus → 11,958 baseline irreproducible → **8,688 primary Hermit target**
(baseline irreproducible ∧ Dettrace reproducible).

The strongest new result of the night, and the best-designed: a **two-root controlled test** —
build four times from one prepared tree in four different root paths, `native N1 vs N2` as the
per-package control, `hermit A vs B` as the arm. Each package is its own control, so it is
meaningful at small M.

> **5 of 13 packages: native two-root DIVERGES, hermit two-root IDENTICAL** — `dos2unix`, `ed`,
> `hostname`, `tree`, `units`. **[C]**
> All 13 were root-sensitive natively (the control fired every time). 3 diverged under Hermit too
> (`figlet`, `nano`, `time`), reported undiagnosed rather than dropped; 5 incomplete (`bzip2`,
> `grep`, `indent`, `wdiff`, `zip`).

Separately, the earlier pilot: two independent fresh-root builds of `hello_2.8-2` produced a
bitwise-identical 68,896-byte `.deb` (`55306cc9…`, `cmp` = 0) at L1. Unblocked by Reverie #287 —
the minimized regression went from a 120 s timeout after 432,359 repeated interceptions to passing
in 0.01 s. **[C]**

**Denominator discipline: 5/13 controlled wins, and 1/8,688 against the paper's target set.** This
is a reconstruction at Debian Snapshot `20190301T000000Z`, not a replay of the paper's
(unavailable) mirror.

### buck2

Dir: `buck2-action-bitwise-determinism_20260806`. The directory labels itself
**"bootstrap / demo, not a study"**; that label is kept here.

| measurement | denominator | tag | result |
|---|---|---|---|
| Same-isolation-dir double build | 337 executed actions, 1 target | **[C]** | **0 divergent**; executed count nonzero, so not a cache artifact |
| Batched control arm | 5,548 actions, 74 targets | **[C]** | **0 divergent** |
| Hermit determinizes a nondeterministic tar (Sarah Clark's 2022 test, unmodified) | 1 script, 2 runs | **[C]** | native NONDETERMINISTIC → **DETERMINIZED** |
| Fleet telemetry, `ds=2026-08-05`, **user builds only** | — | **[C]** | 85,674 flagged rows / 41,701 targets |
| Flagged-target arm | 5 targets | **[NR]** | **BLOCKED** — GPU/ASIC/MTIA toolchains don't build on a generic devserver |

**Do not report "buck2 is reproducible."** The clean arms are the *control* population; the arm
that would test flagged targets did not run.

Two caveats that change what the telemetry means: it is **user builds only** (Sandcastle/CI,
service accounts and cancelled builds filtered out), and **RE's action-cache "paranoid" mode pins
an action to its first result**, suppressing observable divergence for most remotely-executed
actions — so the residual signal concentrates in *locally* executed actions. That cuts against the
naive reading of "run RE actions twice". fbcode build-stamping also makes many binaries
nondeterministic by design, so 41,701 flagged targets is emphatically not 41,701 bugs.

Methodological carry: a **two-isolation-dir** double build is **unsound** — it produced a false
positive whose only difference was the isolation-dir substring embedded in `buck-out` paths.
Sound method: one isolation dir, `buck2 clean` between rounds, batched, assert executed-action
count > 0.

Prior art located: `fbcode/hermetic_infra/reproducible_builds/` (Sarah Clark, Summer 2022,
13 landed diffs). 2022 headline: ~146 irreproducible actions of ~75,000 across 19 fbcode targets.
Two traps recorded there and re-confirmed: action *result* digests are nondeterministic by
construction, and RE action digests expire in ~1.5 days.

### GHC / Haskell

The 2024 Reproducible Builds summit lists *"GHC produces nondeterministic output when concurrency
is enabled"* as an **unsolved frontier class**. **[C]**

Stock parallel `ghc --make -j8 -O0` (ticker ON, no `+RTS -C0`): **3 distinct hashes → 1 hash,
3/3 reproducible**; `-N1/-N2/-N4` each 3/3 within itself; `-j1` reproducible under strict, relaxed
and native. 46-module package, 3 runs per arm. Mechanism: the threaded RTS ticker is a periodic
`CLOCK_MONOTONIC` timerfd driving green-thread preemption; virtualizing timerfd against Detcore's
clock (Hermit #1169) determinizes it — **closed by determinizing the mechanism, not disabling it.**
Caveat: hashes differ *between* `-N` values, so the guarantee is "reproducible for a fixed `-N`".

> **Non-replication, attribution unknown.** The 2026-07-29 flagship configuration
> `hermit --strict -j8 +RTS -C0` was recorded REPRODUCIBLE (3/3 identical `e6037a6c…`) and
> measured **NON-REPRODUCIBLE (3/3 distinct)** on 2026-08-07 by the newly captured harness.
> Candidates: a Hermit change since `32f004cd`, host dependence (no working PMU or CPUID faulting
> here), or an original 3-run result that was not robust. **Do not cite the `-C0` result as
> current until bisected.** Bisecting is now cheap because the harness exists — which is the
> argument for capture, made by example: the headline stopped being true and nothing noticed for
> nine days.

## 4. Product changes landed tonight (verified against GitHub, not taken from status)

| change | merge commit | unblocked |
|---|---|---|
| reverie **#396** — traced-tree root distinct from synthetic `getppid` | `0ae0c01b` | the KVM outage below |
| hermit **#1840** — pin → `0ae0c01b` + DBI budget calibration carry | `0041130c` | carries #396; cleared a pin-lint wall blocking **every** open PR |
| hermit **#1705** — purge incomplete build artifacts; README → reverie fork | `a8951eff` | local validation hygiene |

**KVM 0/200 → 139/200 (also 131/177 measurable) is [C].** #1840's own body says so: *"reported as
its measurement, not re-derived by me."* What was personally verified is the `/bin/true`
before/after: `timeout 45 hermit run --strict --backend kvm -- /bin/true` → Terminated rc=143
before, rc=0 after. **The published `fullcorpus-scorecard.csv` does not show this outage** — it was
collected at `82a8e8533575`, before the cause; every KVM number in it describes a build that no
longer exists.

**Open, not delivered:** #1843 (`--image` `/dev`), #1833 (per-backend guest-args channel),
#1828 (SaBRe fail-closed), #1813 (exec-clock continuity), #1811 (clippy), SaBRe #16.

## 5. Limitations — what a reader must not conclude

1. **Single host**, `devbig176`, 176-core **AMD**. Run-to-run stability on one machine is *not*
   cross-machine reproducibility — the property Debian actually cares about. The two-root design
   is the closest proxy and varies only the path.
2. **No CPUID faulting** (`ARCH_SET_CPUID` → ENODEV; PMU reports `AmdSpecLockMapShouldBeDisabled`).
   A full local `validate.sh` profile **cannot** go green here. This is also why `--no-rcb-time`
   is needed.
3. **ptrace backend almost everywhere.**
4. **`--tmp=/tmp` shares host `/tmp`** — deliberate, but it re-admits host state as a build input.
   Honest label: `reproducible-output-under-shared-/tmp`.
5. **`--verify` is the default lossy Stripped comparator. No L2 claim anywhere in this report.**
6. **Hermit sequentializes `make -j`** — `nftables` under the wrap: >23 min, no completion. This
   is the real cost gate on whole-package determinization.
7. **Small N**: 10 / 3 / 2 / 1 depending on arm. `bash_random` at 3/10 then 5/10 shows N=10 can
   miss a sub-10%-per-run regime.
8. **`/nix` is chef-ephemeral** on this host.

## 6. What the next engineer should do first

1. **Fix the two nixpkgs compatibility bugs** — the cmake configure hang and fake-uid-0 breaking
   `tar` in `unpackPhase`. These, not determinization, are why real nixpkgs is N=0.
2. **Move Debian off 5/13** — finish the 5 incomplete packages and diagnose the 3 that diverged
   under Hermit. The design and controls are done; this is throughput.
3. **Bisect the GHC `-C0` non-replication.** The harness now exists, so it is cheap.
4. **Run the buck2 flagged-target arm** somewhere those toolchains build, or invert the join onto
   a known-buildable frame.
5. **Do not chase cross-machine reproducibility with these harnesses** — they measure one host and
   would silently report success.

**Open owner decisions:** the **`unshare` policy question** (relax the documented fail-closed
refusal / virtualize the namespace family as a new determinization strategy / patch the build
system — all core-abstraction calls); whether `--image` should carry `/dev/tty`; whether the compat
scorecard should be regenerated given its known-stale KVM column.

## Appendix: evidence index

`rb_no_namespace_random_leaks_20260806` @ `76117cd9…` ·
`rb_nix_namespace_refusal_20260807` @ `fab2a5d1…` ·
`nix_hermit_repro_hostnix_20260806` ·
`rb_debian_tworoot_20260807` ·
`debian_reproducible_builds_2026` ·
`buck2-action-bitwise-determinism_20260806` ·
`rb_ghc_captured_reproduction_20260807` ·
`rb_ghc_rts_ticker_determinized_20260730` ·
`rb_ghc_j1_determinism_20260730` ·
`rb_drb_haskell_ghc_concurrency_20260729` ·
`rb_nix_minimum_hermit_dose_20260730` ·
`nix-hermit-execbuilder-prototype_20260729` (annotated: 3 refuted claims).
