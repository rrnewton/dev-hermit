# Reproducible Nix builds under Hermit: the exec-builder seam on host nix

**Question.** Can `hermit run` around a Nix derivation's builder make a
non-reproducible build byte-identical — and what is the *minimum* Hermit
flag-set that does it on this host? Then: does it work on **real nixpkgs
packages**, and how does a nixpkgs consumer opt in *only* the packages that
need it?

## TL;DR

1. **The seam works and is one line**, but the mode in every prior write-up was
   wrong. `--tmp=/tmp`, not `--no-namespace`; no `setarch -R`. On the four-source
   probe `--no-namespace` scores **10 distinct in 10 — identical to no Hermit at
   all**, while `--tmp=/tmp` scores 2.
2. **Minimum dose on this host: `run --tmp=/tmp --no-rcb-time`.** `--no-rcb-time`
   is *required* because this AMD host's PMU fails validation and Hermit's
   default logical clock is driven by PMU-read RCB counts. At N=20:
   `nondet-time` 20 distinct native → **1 under Hermit**; `nondet-seconds`
   16 → **1**.
3. **One real nondeterministic nixpkgs package found: `lensfun-0.3.4`**
   (`date +%s` baked into `$out`). K=17 attempted, 13 measurable, M=1, **N=0
   reproduced** — because **cmake configure hangs under Hermit** (R3b), a
   compatibility bug with a 90-second reproducer, not a determinization failure.
4. **Two new Hermit bugs, both blocking real nixpkgs work:** the fake uid 0 that
   breaks `tar` in `unpackPhase` for every tarball-sourced package (R3a), and the
   cmake `epoll_wait` hang (R3b).
5. **CA store: crisp negative.** `nix --check` does **not** detect nondeterminism
   in a `__contentAddressed` derivation on nix 2.30.2 (nix#5336 reproduces), so
   CA cannot be the oracle. Hermit *does* collapse a CA derivation onto one
   content-addressed path.
6. **Ergonomics: `passthru.needsHermit` + a one-overlay `hermitizeIfNeeded`**,
   verified with a two-sided gate — the marked package is wrapped, the unmarked
   package's derivation is byte-identical to stock.


> **READ THIS FIRST — `/nix` ON THIS HOST IS EPHEMERAL.**
> Every number below was measured against a **host** nix install
> (`~/.nix-profile`, nix 2.30.2, single-user, no daemon, `sandbox = false`).
> Chef reverts `/nix` on Meta devservers, so that store **will be deleted**.
> Run [`./bootstrap.sh check`](bootstrap.sh) before believing anything here
> still runs; `./bootstrap.sh host` recreates the same install pinned to the
> same nix version and nixpkgs revision. A rootless-podman fallback exists and
> is assessed below (it provisions a store but cannot fetch nixpkgs through
> fwdproxy today).

---

## Provenance

| | |
|---|---|
| Run date (UTC) | 2026-08-07 (local 2026-08-06 evening PDT) |
| Host | `devbig176`, AMD EPYC 9D64 88-Core, 176 logical CPUs, kernel `6.16.1-0_fbk5_hardened_rc1_0_gffabe313d1ba` |
| Hermit | `1fadc03779f2a246a9b5af5d4a93533511c837df`, clean tree, `target/release/hermit`, built in slot `worktrees/nix-repro176` |
| Nix | 2.30.2, single-user, **no daemon**, `sandbox = false`, `experimental-features = nix-command flakes` |
| nixpkgs | `cab778239e705082fe97bb4990e0d24c50924c04` (25.11pre839900), channel `nixpkgs-unstable` |
| Egress | `cache.nixos.org` **is** reachable through `http://fwdproxy:8080` (HTTP 200). Build *inputs* were substituted; the *target* of every measurement was forced to build locally with `--option substitute false`. |
| Host load | 35-50 (shared box, other agent fleets active) |

Exact SHAs, per-script `sha256`, and tool versions: [`metadata.json`](metadata.json).
Raw rows: [`results.csv`](results.csv) (one row per configuration),
[`runs.csv`](runs.csv) (one row per individual build).

---

## Methods

### The seam

nixpkgs `stdenv.mkDerivation` builds by exec'ing `realBuilder` (the *binary*;
the user-facing `builder` attribute is the phase *script*). Replacing
`realBuilder` with a tiny store-resident wrapper puts Hermit around the entire
builder process tree — unpack, patch, configure, build, install, fixup — while
Nix keeps evaluation, dependency ordering, output registration and comparison.
No patch to nix and no patch to nixpkgs. [`nix/hermit-wrap.nix`](nix/hermit-wrap.nix):

```sh
#!/nix/store/…-bash
exec <hermit> run --tmp=/tmp --no-rcb-time --max-timeslice disabled -- <original-builder> "$@"
```

Two changes from the 20260729 prototype, both load-bearing, both explained
under *Corrections* below: the mode is **`--tmp=/tmp`**, not `--no-namespace`,
and **`setarch -R` is gone**. One robustness change: the original builder is
read off the already-evaluated derivation (`drv.drvAttrs.builder`) instead of
being hard-coded to `stdenv.shell`.

Because `realBuilder` is part of the input-addressed derivation, wrapping
changes the derivation identity and the output path. We therefore compare
wrapped-vs-wrapped and native-vs-native, never wrapped-vs-native.

### The oracle — canonical rebuild, not `nix --check`

[`harness/canonical-nrep.sh`](harness/canonical-nrep.sh) builds a derivation
**N times into the same canonical `$out`** (build → NAR-hash → `nix-store
--delete` → rebuild) and counts distinct hashes.

`nix --check` is **not** usable here. With `sandbox = false` nix's check-mode
rebuild goes to a *redirected* output path, so any output embedding a
self-reference to its own `$out` differs by exactly that store-path hash — a
false positive unrelated to runtime nondeterminism. That is the nftables-1.1.6
finding from the 20260729 prototype, and it is why every verdict here comes
from the canonical oracle.

Two additional oracle details that mattered:

- A derivation with several outputs (`out`, `dev`, `doc`, …) must have **all**
  outputs deleted in one call, or nix refuses on account of a live referrer.
  The witness is the `+`-joined NAR hash of every output.
- Serial throughout: `--option max-jobs 1 --option cores 1`, for both arms.

### Independent variable

For every comparison the only thing that changes is `realBuilder` (and, in the
dose sweep, the Hermit flags inside it). Where a package needed a tweak to run
under the wrap at all (`TAR_OPTIONS`, see *Seam blockers*), the tweak was
applied to **both** arms.

---

## Evaluation — what each probe actually does

| probe | what it does | nondeterminism class |
|---|---|---|
| `nondet-time` | writes `date -u +%s.%N` (**nanoseconds**) and 32 bytes of `/dev/urandom` into `$out` | wall-clock time + kernel RNG |
| `nondet-seconds` | writes `date +%s` (**whole seconds**) into `$out` — a faithful surrogate for what `lensfun` does | wall-clock time at second resolution |
| `nondet-demo` / `nondet-demo-fast` | all four sources: nanosecond clock, `/dev/urandom`, `$RANDOM$RANDOM`, `/proc/sys/kernel/random/uuid`. The `-fast` variant adds `dontFixup = true` (it produces no ELF, so fixup measures nothing but costs minutes under the wrap) | + `AT_RANDOM`-seeded userspace PRNG + procfs RNG |
| `urandom-temp-names` | seeds three scratch filenames from `/dev/urandom`, then bakes the resulting directory listing into `$out/manifest.txt` | kernel RNG, shaped like a real build's temp-name pattern |
| **`lensfun-0.3.4`** | **a real nixpkgs package.** Its `prePatch` ends with `date +%s > data/db/timestamp.txt`, and cmake installs that file to `$out/share/lensfun/version_1/timestamp.txt` | wall-clock time at second resolution, baked into a real package's output |
| `hello`, `which`, `bc`, `figlet`, `tree` | ordinary small autotools packages, already reproducible | none — used as **seam-buildability** probes |
| 8 small Haskell packages | Lila reports two distinct NAR hashes for each | used to test whether cross-machine ≠ on-machine |

---

## Results

### R1. Minimum dose (the study) — N=10 per dose, three derivations

Native is the positive control in every block. `distinct` is the number of
distinct NAR hashes across N canonical rebuilds; `1` means byte-identical.

**`nondet-time`** (time + urandom), N=10:

| dose | distinct/10 | verdict | wall s (per build) |
|---|---|---|---|
| native (control) | **10** | NONDETERMINISTIC | 0-1 |
| `run --tmp=/tmp` | 3 | NONDETERMINISTIC | 2-59 |
| **`run --tmp=/tmp --no-rcb-time`** | **1** | **reproducible** | 2-4 |
| `run --tmp=/tmp --no-rcb-time --max-timeslice disabled` | 1 | reproducible | **1** |
| `… --strict` | 1 | reproducible | 1 |
| `run --no-namespace` + `setarch -R` (superseded) | 4 | NONDETERMINISTIC | — |
| `run --no-namespace --no-rcb-time --max-timeslice disabled` + `setarch -R` (superseded) | **2** | **NONDETERMINISTIC** | — |

**`urandom-temp-names`** (kernel RNG only), N=10: native **10 distinct**; every
`--tmp=/tmp` dose including plain `run --tmp=/tmp` → **1 distinct, reproducible**.

**`nondet-demo-fast`** (all four sources), N=10 — the decisive mode comparison:

| dose | distinct/10 |
|---|---|
| native (control) | 10 |
| `run --no-namespace` + `setarch -R` (superseded) | **10 — no determinization at all** |
| `run --no-namespace --no-rcb-time --max-timeslice disabled` + `setarch -R` | **10 — no determinization at all** |
| `run --tmp=/tmp` | 4 |
| `run --tmp=/tmp --no-rcb-time` | **2** |
| `run --tmp=/tmp --no-rcb-time --max-timeslice disabled` | **2** |
| `… --strict` | 2 |
| `… --sequentialize-threads` | 2 |
| `… --target-timeslice 1000000000` | 2 |

`--no-namespace` scores **10/10 — exactly as bad as native** on this probe,
because `$RANDOM` and the procfs UUID leak straight through it. That is the
single sharpest argument for the mode correction, independent of the
buildability argument in R3.

**Summary of results at N=20** (dose `run --tmp=/tmp --no-rcb-time --max-timeslice disabled`):

| probe | native distinct/20 | hermit distinct/20 | verdict |
|---|---|---|---|
| `nondet-time` (nanosecond clock + urandom) | 20 | **1** | **reproducible** |
| `nondet-seconds` (second clock — the `lensfun` class) | 16 | **1** | **reproducible** |
| `nondet-demo-fast` (all four sources) | 20 | 2 | NONDETERMINISTIC (clock only) |

#### The residual: a discrete virtual-clock quantum

The `nondet-demo-fast` residual is **entirely** the clock. Six wrapped builds,
content diffed directly:

```
urandom=2972bb044d96df2871ba034c95de2770   IDENTICAL 6/6
bashrandom=148378                          IDENTICAL 6/6   <- AT_RANDOM class, fixed
uuid=038939f6-223b-42f7-bcca-00dccaab37d6  IDENTICAL 6/6   <- procfs RNG,  fixed
date=1767228154.422875000                  1 of 6
date=1767228154.672875000                  5 of 6
```

The two clock values differ by exactly **250 ms**. With `--no-rcb-time` the
logical clock advances a fixed increment per scheduler check-in, so a build
whose syscall path varies by one check-in lands one quantum apart. Four extra
doses (`--strict`, `--sequentialize-threads`, `--target-timeslice`,
`--max-timeslice disabled`) all leave it at 2/10, so it is not a preemption
artifact.

**Practical consequence, and why it matters for real packages:** a build that
bakes *nanoseconds* can still differ; a build that bakes *whole seconds*
cannot. `nondet-seconds` — which does exactly what `lensfun` does — is
**1 distinct in 20**. So the real-package class we identified is inside the
seam's competence even though the clock is not perfectly deterministic.

**Minimum dose on this host = `run --tmp=/tmp --no-rcb-time`.**
`--max-timeslice disabled` is not needed for correctness but is a **~3x speedup**
(1 s vs 3 s per build) and removes the 59 s outlier seen with PMU preemption on.
`--strict` is free.

#### Why `--no-rcb-time` is required here — mechanism, not correlation

This host's PMU is unusable by Hermit. Every run logs:

```
ERROR reverie_ptrace::perf: PMU validation failed; RCB timers may be unreliable
      error=AmdSpecLockMapShouldBeDisabled
```

Hermit's default logical clock advances with **retired-conditional-branch (RCB)
counts read from that PMU**. With an unreliable counter the virtual clock
jitters, and the jitter is visible in the output at nanosecond resolution.
Directly observed, five wrapped builds of `nondet-time`:

```
date=1767225615.542100300   <- 4 of 5 builds
date=1767225615.542075450   <- 1 of 5 builds
urandom=2972bb…b700         <- IDENTICAL in all 5
```

Only the sub-microsecond digits of the clock move; `/dev/urandom` is perfectly
virtualized. `--no-rcb-time` makes logical time advance by a fixed increment per
scheduler check-in instead, and the jitter disappears (1 distinct in 20).

This **supersedes** the minimum dose reported by
`experiments/rb_nix_minimum_hermit_dose_20260730` (plain `--no-namespace`),
which was measured on a host whose PMU worked. The honest statement is
host-conditional: *on a PMU-degraded host, `--no-rcb-time` is required for a
build that reads a high-resolution clock.*

### R2. Real nixpkgs packages — N reproduced / M nondeterministic / K attempted

**K = 17 real nixpkgs packages attempted** (8 Haskell, 8 C/autotools/cmake, plus
`lensfun`). Full per-package rows in [`results.csv`](results.csv).

| package | native canonical verdict | class | hermit arm |
|---|---|---|---|
| **`lensfun-0.3.4`** | **NONDETERMINISTIC (3/3 distinct)** | wall-clock: `date +%s` → `$out/share/lensfun/version_1/timestamp.txt` | see *status* below |
| `haskellPackages.code-page` | reproducible | — (Lila multi-hash is cross-machine only) | n/a |
| `haskellPackages.unliftio-core` | reproducible | — | n/a |
| `haskellPackages.haskell-lexer` | reproducible | — | n/a |
| `haskellPackages.nanospec` | reproducible | — | n/a |
| `haskellPackages.logging-facade` | reproducible | — | n/a |
| `haskellPackages.utf8-string` | reproducible | — | n/a |
| `haskellPackages.os-string` | reproducible | — | n/a |
| `haskellPackages.call-stack` | **not measurable** — external referrers block canonical delete | — | n/a |
| `hello`, `which`, `bc`, `figlet`, `tree` | reproducible | — | seam-buildability probes; `figlet` **built end to end under the wrap** |
| `patchelf`, `libarchive` | **not measurable** — external referrers | — | n/a |
| `xz` | not an attribute in this nixpkgs | — | — |

**So: K = 17 real nixpkgs packages attempted, 13 measurable,
M = 1 on-machine nondeterministic, N = 0 reproduced under the wrap.**
The 1/13 base rate is consistent with Lila's 749/751 reproducible; the
N = 0 is explained by R3b, not by the seam.

`lensfun` was found by grepping nixpkgs for `date` invocations that do **not**
reference `SOURCE_DATE_EPOCH`. That grep is the reusable method: of ~59 files
matching a build-time `date`, all but a handful correctly derive it from
`SOURCE_DATE_EPOCH`; `lensfun` does not. Confirmed by diffing two native
builds — the only differing bytes are the epoch seconds in `timestamp.txt`
(`1786070995` vs `1786071182`).

**Status of the `lensfun` hermit arm: BLOCKED, and the blocker is a Hermit bug,
not slowness.** `lensfun` is a cmake package, and **cmake's configure step hangs
under `hermit run`** (R3b). `N` for real packages is therefore **0 of 1**.

What we can say instead, and it is not nothing: the `lensfun` nondeterminism
class — a whole-second `date` baked into `$out` — is reproduced by
`nondet-seconds`, which goes from **16 distinct in 20 natively** to **1 in 20
under the wrap**. The mechanism is demonstrated; the specific package is blocked
behind a fixable compatibility bug.

### R3. Seam blockers found

Two, both new, both actionable, and together they explain why no prior write-up
had ever driven a real nixpkgs package through this seam.

#### R3a. Hermit reports uid 0 it cannot back

This is the most actionable Hermit finding of the night and it is **not** in any
prior write-up.

Detcore answers `getuid`/`geteuid`/`getgid`/`getegid` with a constant `0` in
**both** namespace modes (`detcore/src/syscall_classification.rs`: "fixed
virtual-root identity"). GNU tar therefore believes it is root and tries to
restore each archive member's recorded ownership. What happens next depends on
the mode:

| mode | `id -u` | `uid_map` | `chown 0:0` | `chown 1000:1000` |
|---|---|---|---|---|
| `--no-namespace` | 0 | *(no user namespace)* | **EPERM** | **EPERM** |
| `--tmp=/tmp` | 0 | `0 <caller-uid> 1` (one uid) | **OK** | **EINVAL** |

Measured consequence: under `--no-namespace`, **4/4** tarball-sourced packages
(`hello`, `which`, `bc`, `figlet`) fail in `unpackPhase` with
`tar: … Cannot change ownership to uid 1000, gid 1000: Operation not permitted`.
Under `--tmp=/tmp` they fail the same way with `Invalid argument`, because the
user namespace maps exactly one uid. Since nixpkgs unpacks upstream tarballs
that record foreign uids, **this breaks essentially every tarball-sourced
package** — the seam was never generally applicable, and nobody had noticed
because only synthetic `dontUnpack` probes had been tried.

Workaround used here, applied to **both** arms so the comparison stays fair
(`nix/real-candidates.nix`): `TAR_OPTIONS = "--no-same-owner --no-same-permissions"`.
It is a no-op natively (tar only restores ownership when euid==0). With it,
`which` gets past `unpackPhase` into `configurePhase` under the wrap.

Suggested Hermit fixes, in increasing order of invasiveness: (a) map a wider
uid range in the user namespace; (b) virtualize `chown`/`fchown` to a
no-op-success when the guest believes it is root; (c) do not virtualize uid to
0 when sharing the host filesystem.

#### R3b. `cmake` configure HANGS under `hermit run` — lost pipe-EOF in epoll

This is the blocker that stopped the real-package arm, and it is a hang, not
slowness. Minimal reproducer: [`harness/cmake-hang-repro.sh`](harness/cmake-hang-repro.sh),
a three-line `CMakeLists.txt` and one `main.c`:

```
native: rc=0   wall=1s
hermit: rc=124 wall=90s     (124 == timed out)
```

`hermit` produced **no output at all** before the timeout. The two in-flight
`lensfun` builds hung at the same place and were still there after 28 minutes.

Root cause, read off the live process rather than inferred:

```
/proc/<cmake-pid>/syscall  ->  281 0x3 0x7fffffff2880 0x400 0xffffffff …
                               ^^^ epoll_wait   epfd=3   maxevents=1024  timeout=-1
/proc/<cmake-pid>/wchan    ->  do_epoll_wait
/proc/<cmake-pid>/fd/3     ->  anon_inode:[eventpoll]
/proc/<cmake-pid>/fd/4,12  ->  pipe:[…]        (pipes to a child that has exited)
ps TIME                    ->  00:00:00 after 28 minutes of wall clock
```

cmake spawns a compiler-probe child, watches its pipes with `epoll_wait` and an
**infinite** timeout, and never receives the readable/EOF event after the child
exits. Zero accumulated CPU confirms a lost wakeup rather than a slow one.
Reproduces under **both** `--tmp=/tmp` and `--no-namespace`, so it is
independent of the mode correction.

Impact: every cmake-based nixpkgs package is out of reach of this seam until it
is fixed — including the one real nondeterministic package we found. Autotools
packages are unaffected (see R4: `figlet` reached `fixupPhase` under the wrap).

### R4. Cost, and what does get through

The seam is one line and correct. What makes a real package expensive is that
Hermit determinizes by **sequentializing**, and a configure step is thousands of
short-lived processes.

`harness/spawn-cost.sh`, 200 sequential `/bin/true` execs, 3 reps
([`logs/spawn-cost.csv`](logs/spawn-cost.csv)):

| mode | per-process |
|---|---|
| native | 0.76-0.80 ms |
| hermit `--tmp=/tmp --no-rcb-time --max-timeslice disabled` | 6.0-6.7 ms |

**~8.4x on a trivial exec** — but the observed slowdown on real build phases is
far larger, because real child processes are not `/bin/true`:

- `which`: `updateAutotoolsGnuConfigScriptsPhase` took **2 min 49 s** under the
  wrap (sub-second natively) — roughly 170x.
- `hello`, `which`, `bc`: still inside `configurePhase` after ~25 min under the
  wrap; an autotools `configure` is thousands of short-lived probe processes.

This reproduces and quantifies the 20260729 prototype's scaling finding
(nftables >23 min, no completion, on a 316-core box) and it is unaffected by the
`--tmp=/tmp` correction. **Whole-package determinization is gated on Hermit
build-time performance, not on the Nix integration.** Note this is a *separate*
issue from the cmake hang in R3b: the autotools packages are slow but making
progress and accumulating CPU; cmake accumulates none.

**What does get through:** with the `TAR_OPTIONS` workaround, `figlet-2.2.5` —
a real nixpkgs autotools package — unpacked, patched, configured, compiled and
installed under the wrap, reaching `fixupPhase`. That is the first real nixpkgs
package this seam has been shown to drive end to end past `unpackPhase`. It was
already reproducible natively, so it is a **buildability** result, not a
determinization win.

### R5. Ergonomic opt-in — enabling Hermit for only the builds that need it

[`nix/hermit-overlay-demo.nix`](nix/hermit-overlay-demo.nix) splits the decision
in two, which is what makes it upstreamable:

1. **Declaration**, beside the package (ideally in nixpkgs):
   `passthru.needsHermit = true;` — inert, changes no derivation, changes no
   output hash.
2. **Enforcement**, one overlay on the consumer side: `hermitizeIfNeeded`,
   which wraps *iff* the package declared the flag.

[`harness/ergonomics-check.sh`](harness/ergonomics-check.sh) is a two-sided gate
— a positive-only gate would pass an overlay that wrapped everything, and a
negative-only gate would pass one that wrapped nothing:

```
POSITIVE  lensfun declared needsHermit
          -> drv 01pd7dfb… (stock)  becomes  svwcazgh… (wrapped)      PASS
          -> realBuilder = /nix/store/…-hermit-exec-builder            PASS
NEGATIVE  hello did not declare
          -> drv 5g60vyp4… IDENTICAL to stock, no hermit in the .drv   PASS
ESCAPE    consumer-side unconditional hermitize of hello -> ywll4l8z…  PASS
rc=0
```

The negative case matters most: nothing else in the closure is rebuilt, so a
consumer can opt in one package without perturbing the rest of nixpkgs.
Cross-check: the wrapped derivation the overlay produces (`svwcazgh…`) is
byte-identical to the one the measurement harness built, so the demo and the
measurement are the same object.

**What this does NOT prove:** it is verified at *evaluation* level. It proves
the overlay selects correctly and produces the right derivation; it does not
prove the wrapped build of `lensfun` completes (it did not — R4).

### R6. Content-addressable store — a clean NEGATIVE for the oracle question

`harness/ca-probe.sh`, nix 2.30.2, `ca-derivations` enabled per-invocation
(the shared `~/.config/nix/nix.conf` was not modified):

| question | result |
|---|---|
| Q1 input-addressed `--check` on a `date`-nondeterministic derivation | exit **104** — nix detects it |
| Q2 the **same** nondeterminism as a `__contentAddressed = true` derivation | exit **0** — nix does **NOT** detect it |
| Q3 CA + hermit: three canonical rebuilds | **one** store path, `2xg2m2hc…`, identical content each time |
| Q3 control: CA **native**, delete+rebuild | **different** path each genuine rebuild (`54dzzf6i…`, `m1f38dww…`) — so deletion really does force a rebuild and Q3 is not caching |

**Conclusion: CA mode cannot serve as the reproducibility oracle.**
[NixOS/nix#5336](https://github.com/NixOS/nix/issues/5336) reproduces on 2.30.2:
`--check` silently succeeds on a nondeterministic CA derivation. Content
addressing names an output by its content *after* the build; it does not force
two builds to agree, and its own checker will not tell you they disagreed.

The Q3 positive is still worth having: **Hermit collapses a CA derivation onto a
single content-addressed store path across rebuilds**, which is exactly the
"same content ⇒ same path" property CA promises — but you must verify it with an
external oracle (the canonical rebuild), not with `nix --check`.

### R7. Cross-machine ≠ on-machine, re-confirmed

[`harness/lila-multihash.py`](harness/lila-multihash.py) pulled Lila's
(reproducibility.nixos.social) minimal-ISO **build closure**, evaluation 26:
**113 of 3359 outputs have more than one reported NAR hash**
([`lila-multihash-eval26.tsv`](lila-multihash-eval26.tsv)); 106 of the 113 are
Haskell packages.

Six of those Haskell packages were measurable here, and **all six are
reproducible on this machine** (3/3 identical). Lila's multi-hash set is
therefore mostly *cross-machine/environmental* — the same trap as nftables. It
is a useful **candidate** filter and a useless **verdict**.

---

## Corrections to prior work

Two claims inherited from
`experiments/nix-hermit-execbuilder-prototype_20260729` were refuted during this
run. Both have the same shape: a build-output observation attributed to a cause
nobody measured.

1. **"`--no-namespace` is required because the default private mount namespace
   discards writes to `$out`."** False. A mount namespace isolates the mount
   table, not file contents. Hermit replaces `/tmp` with a private tmpfs, and
   with `sandbox = false` nix builds in `/tmp/nix-build-*` — so it was the
   *build directory* that vanished, not `$out`. Refuted by
   `experiments/rb_no_namespace_random_leaks_20260806` (parent `76117cd9`) and
   independently confirmed here end to end on real nix derivations: with
   `--tmp=/tmp` the output lands in the real `/nix/store` and the build
   reproduces.
2. **"`setarch -R` is needed to pin ASLR."** Only true under `--no-namespace`.
   The full namespace pins it itself; `useSetarch = false` is the default now.

And `--no-namespace` is not merely unnecessary, it is *worse* on both axes
measured here: it leaks (2 distinct in 10 on `nondet-time` even with
`--no-rcb-time`, versus 1 in 20 for `--tmp=/tmp`), and it makes `chown` fail
outright so no tarball-sourced package can unpack.

Carried forward as **inferred, not proven**: the `getpid → bash $RANDOM`
mechanism reported by the sibling experiment is established *by elimination*, not
from bash's source — a reimplementation of bash 5.1's `seedrand`/`intrand32` did
not reproduce the observed triples. Cite it as "measured leak, inferred
mechanism".

---

## Limitations — read before quoting any number

- **`/nix` is ephemeral on this host.** Chef reverts it. See the banner.
- **N = 0 real nixpkgs packages were shown reproducible under the wrap.** One
  (`lensfun`) is confirmed nondeterministic and is the right target; its wrapped
  build did not finish (R4). Do not read R1 as a real-package result.
- **The determinism label is `reproducible-output-under-shared-/tmp`.**
  `--tmp=/tmp` shares the host `/tmp`, which re-admits host state as a build
  input: a concurrent build or a leftover from a previous run is visible. This
  is strictly *more* isolation than `--no-namespace` gave (all other namespaces,
  including the user namespace, are retained), not less — but it is not a
  hermetic sandbox, and with `sandbox = false` the isolation that remains is
  Hermit's, not nix's.
- **Host-conditional dose.** `--no-rcb-time` is required *because this host's
  PMU is broken*. On a host with a working PMU the plain dose may suffice
  (`rb_nix_minimum_hermit_dose_20260730` measured exactly that). Re-run the
  sweep on any new host rather than copying the dose.
- **Shared, loaded box.** Load 35-50 on 176 cores throughout, with other agent
  fleets building. Wall-clock numbers (including the 59 s outlier and the
  spawn-cost figures) carry that noise. The reproducibility verdicts do not
  depend on timing.
- **Two packages' verdicts are unobtainable with this oracle**
  (`call-stack`, `os-string`, `patchelf`, `libarchive`): other store paths
  already depend on them, so `nix-store --delete` refuses and the canonical
  rebuild cannot run. Recorded as `error / delete-failed`, not as a verdict.
- **The podman fallback cannot fetch nixpkgs today.** `nixos/nix:2.3.16` builds
  self-contained derivations offline, but `nix-channel --update` fails through
  fwdproxy (`curl error 56`) even with `--network=host` and proxy env set. The
  image's nix 2.3.16 does not negotiate the CONNECT tunnel.
- Sample sizes are stated per row. `n<2` is reported as `INCONCLUSIVE`, never
  as reproducible.

---

## Reproduction

```sh
cd experiments/nix_hermit_repro_hostnix_20260806
./bootstrap.sh check          # is there a working nix + hermit + egress?
./bootstrap.sh host           # recreate the host nix install if chef ate it
N=10 ./run.sh                 # the whole experiment
```

Individual pieces:

```sh
# minimum-dose study on one derivation
bash harness/dose-sweep.sh '(import ./nix/nondet-time.nix) {}' 10 nondet-time

# one configuration, N repetitions
HERMIT_ARGS="run --tmp=/tmp --no-rcb-time" \
  bash harness/canonical-nrep.sh mylabel hermit '(import <nixpkgs> {}).lensfun' 3

# triage a new real package: native first, hermit only if it is nondeterministic
bash harness/screen-batch.sh candidates-real.tsv 3 2

# the two-sided ergonomics gate, and the CA assessment
bash harness/ergonomics-check.sh
bash harness/ca-probe.sh

# refresh the cross-machine candidate list from Lila
python3 harness/lila-multihash.py --evaluation 26 > lila-multihash-eval26.tsv
```

Override `HERMIT=` to test a different hermit binary; override `HERMIT_ARGS=`
and `HERMIT_USE_SETARCH=` for a different dose. `MIN_FREE_GIB` (default 60)
stops the harness before it fills the disk.

---

## Files

| path | what |
|---|---|
| `bootstrap.sh` | recreate the nix install after chef wipes `/nix`; assess the podman fallback |
| `run.sh` | end-to-end reproduction |
| `harness/env.sh` | single source of environment, hermit path, and the measured default dose |
| `harness/canonical-nrep.sh` | the N-repetition canonical-rebuild oracle |
| `harness/dose-sweep.sh` | minimum-dose sweep over namespace/clock flag sets |
| `harness/screen-batch.sh` | two-step real-package triage (native, then hermit) |
| `harness/spawn-cost.sh` | per-process cost of the seam |
| `harness/ca-probe.sh` | `ca-derivations` assessment |
| `harness/ergonomics-check.sh` | two-sided gate on the opt-in overlay |
| `harness/lila-multihash.py` | pull the cross-machine candidate list from Lila |
| `harness/collect-results.py` | normalize every emitted row into `results.csv` |
| `nix/hermit-wrap.nix` | the seam: `hermitize`, `hermitizeIfNeeded`, `overlayFor` |
| `nix/hermit-overlay-demo.nix` | the `passthru.needsHermit` opt-in design |
| `nix/nondet-time.nix`, `nix/nondet-demo.nix` | controlled probes |
| `nix/real-candidates.nix` | real packages + the documented `TAR_OPTIONS` workaround |
| `nix/ca-nondet.nix` | input-addressed / content-addressed pair |
| `candidates-*.tsv` | the package lists that were screened |
| `results.csv`, `runs.csv`, `lila-multihash-eval26.tsv`, `logs/` | data |
