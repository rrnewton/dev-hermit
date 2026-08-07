# Hermit for reproducible builds — overnight findings, 2026-08-06/07

**Status: FINAL for the overnight sprint**, frozen 2026-08-07 06:00 PDT. Every claim cites the
directory and commit that produced it. The headline Debian counts were re-derived from raw
`results.csv` at freeze (see §3/Debian) rather than carried from the lane's summary.
Any remaining **`HOLE`** is a deliberate blank — not yet published, and not estimated.

Tags: **[M]** measured here · **[C]** carried, measured by another agent, not re-derived ·
**[I]** inferred · **[NR]** designed, not run. Do not promote **[C]**/**[I]** to a headline
without re-deriving.

Team: `claude-coord-176`. Host: `devbig176`, 176-core AMD EPYC 9D64.

## 0. Executive summary

**The strongest result.** On a reconstructed Debian Wheezy corpus, building each package **twice
from two different absolute root paths**:

> **52 of 52 packages that diverge natively across two build roots are byte-identical under
> `hermit run --strict --no-rcb-time`** — out of 58 attempted, with **all 58 native controls
> firing**, so no "identical" cell is vacuous. Each package is its own control.

Across the whole set there is **exactly one unexplained Hermit-side failure** (`ack-grep`); every
other non-verdict is package-side, harness-side, or explained.

**Two models worth more than the count.**

1. **Path-varying nondeterminism has two mechanisms, and Hermit addresses exactly one.**
   *Path-embedded* — the path lands in the output bytes — Hermit does **not** fix, and correctly
   so: nothing is nondeterministic from the guest's view. *Path-triggered* — the path perturbs
   allocation, `readdir` order, or timing — Hermit **does** fix. Both were reproduced in one corpus
   with opposite predicted outcomes. **The practical consequence is a triage step nobody was
   doing: classify a candidate by mechanism before reaching for Hermit.**
2. **A named Detcore defect class.** *A handler that lets the guest execute an indefinitely-blocking
   syscall while holding the scheduler turn deadlocks whenever the only task that can unblock it is
   queued behind.* Four instances found in one night with **four different proximate causes**; two
   fixed, one specified-and-declined, one an open reachability risk to the fix itself.

**Three Hermit bugs fixed** (#1851 `chown` family, #1864 `epoll_pwait`, plus the `openat` fd-typing
error that made `read`-on-pipe misroute). Six PRs landed and ancestry-proven; four more are green
and blocked on a fleet-wide CI outage, not on our work.

**The honest negatives, which are results and not failures.**

- **Nix real nixpkgs: N=0** of 13 measurable. Not a determinization failure — a compatibility one.
  With two of three blockers fixed, `lensfun` now clears cmake configure, build **and** install and
  hangs in `fixupPhase`. **The remaining distance is one bug, not a category.**
- **buck2: 0 of 8** fleet-flagged targets reproduced their nondeterminism locally, over 3,287
  executed actions per round. This is the **expected** outcome, not a null one: a same-host A/B
  cannot reproduce cross-environment nondeterminism by construction. It narrows what the fleet's
  flags can be — and if they are path- or host-sourced, **Hermit is not the right tool** and a
  hermetic-root change is.
- **The GHC `-C0` flagship result does not replicate.** Recorded as a non-replication with
  attribution unknown. It stopped being true and nothing noticed for nine days, because no
  re-runnable harness existed. That is the argument for capture, made by example.

**The infrastructure result, found at the freeze.** The P0 demo gate — the merge gate for the
entire fleet — went red on every head including `main` at 06:31Z and stayed red for five hours,
reporting `no ASAN UAF found in seeds 0-63 for the generated fixture`. **That statement was false
in two independent ways, and the gate's green had never been earned.** `calibrate_crash_seed()`
returns early whenever a cached `.crash-seed` exists, and that file lives *inside the cached
directory* — so every green run restored an answer instead of computing one, and the live
calibration path was unexercised until a cache eviction made every runner take it at the same
instant. Measured here: the cached seed **47 does not crash a freshly built fixture** (converts
cleanly, 8s). Separately, `rc` was captured and never read, so **"no ASAN string in the output" was
reported as "this seed did not crash" when it equally meant "this seed never executed"** — and CI's
own timing refutes its message, 64 seeds inside 37.4s against a 30s per-seed timeout. Fixed and
landed (`6c7c0997`), bracketed with three planted negatives and two positives; measurements in
`demo08-crash-seed-calibration_20260807`. **A P0 merge gate whose green depends on a 7-day-LRU
cache entry is the defect behind the defect.**

**The methodological through-line.** Nearly every significant finding tonight came from refusing to
let a status word stand in for the thing it claimed: `UNIONED` that preserved nothing, a scorecard
describing a build that no longer exists, a hosted green that went stale when a pin moved beneath
it, `output_hash` that was `sha256("")`, `BUILD FAILED` that meant out-of-memory, `du` that meant
nothing on a reflinking filesystem, an empty `/proc` file on a kernel without `CONFIG_PROC_CHILDREN`
read as evidence of absence. **Several of those were ours, including two by the coordinator, and
they are recorded here rather than quietly fixed.** §5 lists what a reader must not conclude.

## 1. The corrections: three refuted claims, one shape

Three load-bearing claims in `nix-hermit-execbuilder-prototype_20260729` were refuted, all the
same way: *a build-output observation attributed to a cause nobody measured.*

Source: `rb_no_namespace_random_leaks_20260806` (parent `76117cd954a88df48d94de8cde8bb9c7859a63c8`).
Five randomness probes + two write probes, four modes, N=10, `native` as positive control. **[M]**

| source | `native` | `hermit run` | `--tmp=/tmp` | `--no-namespace` |
|---|---|---|---|---|
| `at_random` | 10/10 | 1 | **1** | 1 |
| `gettimeofday` | 10/10 | 1 | **1** | 1 |
| `procfs_uuid` | 10/10 | 1 | **1** | 1 † |
| `getpid` | 10/10 | 1 | **1** | **10 — LEAK** |
| `bash_random` | 10/10 | 1 | **1** | **3–5 — LEAK** |
| `write_visible_tmp` | visible | **DISCARDED** | **visible** | visible |
| `write_visible_out` | visible | visible | **visible** | visible |

> **† CONTESTED — two agents measured this cell differently and it is unresolved.**
> `rb_no_namespace_random_leaks_20260806` measured `/proc/sys/kernel/random/uuid` as **determinized
> under `--no-namespace`** (1 distinct value, N=10), probing the source directly under
> `hermit run`. `nix_hermit_repro_hostnix_20260806` measured it as **not virtualized at all**
> (6 distinct values in 6 canonical rebuilds), probing it from inside a real nix builder process
> tree. Both are careful measurements with controls; they differ in *context*, not obviously in
> rigour, and the plausible reconciliation is that the read reaches procfs by a different path
> from inside a builder. **Nobody has run the discriminating experiment**, so neither number is
> promoted here. Note this does not affect the recommended wrap — `--tmp=/tmp --no-rcb-time`
> determinizes the UUID in both agents' measurements — but it does mean the `--no-namespace`
> column of this table is not fully settled.
>
> Consequence for the aggregate: the nix lane's *"`--no-namespace` scores 10 distinct in 10"* is an
> **aggregate over a four-source probe**, and means "at least one source leaks", not "determinizes
> nothing".

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

**N=0 reproduced is still the honest headline for real nixpkgs — but the remaining distance is one
bug, not a category.** Two Hermit compatibility bugs were found *and fixed* tonight, and a third
is isolated:

| bug | fix | effect |
|---|---|---|
| **#1849** fake uid 0 makes GNU `tar` fail restoring ownership, killing `unpackPhase` for every tarball package | **#1851** — move `chown`/`fchown`/`fchownat`/`lchown` to `Determinized` no-op success | stock `which` and `hello` clear `unpackPhase` unaided |
| **#1850** cmake configure hangs forever at zero CPU | **#1864** — route NULL-sigmask `epoll_pwait` through the existing non-blockable/timeoutable path | reproducer **`rc=124` at 90 s → `rc=0` at 2 s** |
| `fixupPhase` hang, different entry point | **not fixed** | `lensfun` now clears configure, build *and* install, then hangs here |

**#1850's root cause is not what it was first reported to be, and the correction is instructive.**
The original diagnosis — *"pipe readiness/EOF not delivered"* — was **refuted** by a six-probe
syscall differential (with a must-hang control): Hermit matches native exactly on read-EOF,
`EPOLLHUP`, `POLLHUP`, buffered data, and data+HUP. A symptom had been generalised into a mechanism
without testing the mechanism.

Worse, **the supporting evidence never existed.** "No children at all" had been read from
`/proc/<pid>/task/<tid>/children` — but **this kernel is built without `CONFIG_PROC_CHILDREN`**, so
that file is empty on every process. An absence produced by a missing kernel option was recorded as
an observation. The children were alive the whole time. *(Same shape as every other trap this
sprint: a proxy that returns nothing, read as evidence of nothing.)*

The real cause came from Hermit's own scheduler log rather than inference: the log ends at
`COMMIT turn 198, dettid 3` injecting `epoll_pwait(3,…,-1,NULL,8)` with `queue len 2`, while dettid
5 waited with an inbound `openat`. **`handle_epoll_pwait` performed a blocking wait while holding
the scheduler turn**, and the only task that could satisfy it was queued behind. glibc implements
`epoll_wait(2)` as `epoll_pwait`, so real programs never reached `handle_epoll_wait` — which has
always been correct. `epoll_pwait` was also the **only** poll/epoll member lacking
`NonblockableSyscall`/`TimeoutableSyscall`, while `Ppoll` has had both all along. Non-NULL sigmask
is deliberately untouched, since polling cannot reproduce the atomic mask swap.

The `fixupPhase` hang was **re-tested rather than assumed** to be the same bug: `find … -print0` is
alive and ptrace-stopped at `openat`, never resumed, while bash blocks reading its pipe — zero CPU,
resampled 20 s apart. `handle_read` already routes `FdType::Pipe` through the turn-yielding path, so
it is a different entry point. Two hypotheses are recorded on the issue as *next steps, not
findings*, with the exact `--log=debug` check that settles it.

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

> **52 of 52 packages: native two-root DIVERGES, hermit two-root IDENTICAL** — out of **58
> attempted**, with all 58 native controls firing. **[M]**
> Wrap: `hermit run --strict --no-rcb-time`. Every "hermit identical" cell has a native control
> that diverged, so none is vacuous.

**Re-derived at freeze from the raw 324-row `results.csv`, not carried from the lane's summary.**
Final counts, keyed on `artifact_sha256` by `root`:

| arm | identical | divergent |
|---|---|---|
| native control (`native-n1` vs `native-n2`) | 0 | **58 — every control fires** |
| `hermit --strict` (rcb-time on) | 46 | **6** (`figlet`, `grep`, `indent`, `nano`, `time`, `wdiff`) |
| `hermit --strict --no-rcb-time` | **52** | **0** |

Vacuity check: **0** packages are "hermit identical" without a diverging native control. Six of the
58 have no Hermit rows at all and are excluded from the 52 denominator, not counted as passes:
`ack-grep`, `bsdmainutils`, `flex`, `groff`, `lftp`, `splint`.

**The `--no-rcb-time` dose is load-bearing for 6 of 52 packages (11.5%), and that is a measured
number rather than an inferred one.** It is the difference between the two Hermit rows above, on
the same packages in the same corpus.

> **A near-miss worth recording, because it is this report's own thesis turned on the author.**
> `results.csv` also carries a `hermit_sha256` column. Computing the headline from it yields a
> tidy **58/58** — and it is meaningless: that column holds one single value across all 324 rows,
> because it identifies the *Hermit binary*, not a per-package artifact. Comparing it across two
> roots compares a constant to itself and is true by construction. The coordinator computed 58/58
> first and caught it only on a distinctness check (`distinct hermit hashes: 1`). **A column whose
> name contains `sha256` is not thereby a measurement of the thing you are measuring.** Run the
> distinctness check before quoting any ratio derived from a hash column.

**Failure shape across everything attempted: exactly ONE unexplained Hermit-side failure.**
`ack-grep` builds natively but fails under Hermit with `make[1]: /work/build/0: Command not found`
— an `ExtUtils::MakeMaker` Makefile expanded a command variable to the literal `0`. Undiagnosed,
and importantly **not** explained by the id-mapping asymmetry that accounts for `bsdmainutils`, so
it is a genuine Hermit/Perl interaction lead rather than a known-benign difference. Everything else
that failed is package-side (`socat` does not build in this Wheezy reconstruction at all) or
harness-side (`less`, `bc` hit a resumability gap).

Six attempts yielded no verdict and are named so the denominator is auditable: `socat` does not
build in this Wheezy reconstruction at all; `less` and `bc` hit a harness resumability gap;
`bsdmainutils` builds natively but fails under Hermit — **and that one was checked before being
called a bug. It is not one.** `chown root:tty` returns `EINVAL` because Hermit's container maps
exactly **one** GID (`gid_map: 0 100 1`) while the native podman container maps 65537. That is
correct user-namespace behaviour. Minimal probe: `chown(f,0,0)` and `chown(f,0,-1)` succeed under
Hermit, `chown(f,0,5)` and `chown(f,-1,5)` do not. **The two executors therefore differ in
privilege environment.** Every comparison here is *within* one executor so no verdict is affected,
but **no cross-executor claim is supported either.**

**An earlier figure of 13/13 in this report was inflated by a defect in the lane's own
`summarize.sh`**, which counted every arm against the native denominator, silently crediting a
package still building in one arm to another. The lane found and fixed it; each arm now reports its
own completed denominator and the table is regenerated from `results.csv`. The 52/52 above is the
corrected figure.

**The 29/29 replaced an earlier 7/13, and that correction is itself a result.** Both arms are
retained in `results.csv`:

| arm | result |
|---|---|
| `hermit run --strict` | 7 / 13 |
| `hermit run --strict --no-rcb-time` | **all measured packages identical** |

All six apparent failures flipped and none of the passes changed — the asymmetry signature of a
*measurement artifact*, not six product gaps closed by one flag. This host's PMU fails validation
(`AmdSpecLockMapShouldBeDisabled`) and Hermit derives virtual time from RCB counts, so without
`--no-rcb-time` **Hermit's own clock was the nondeterminism being measured.**

### The two mechanisms, both reproduced in this corpus

The buck2 lane predicted that none of these packages could be embedding its build root path, or
Hermit could not have made them identical. **Confirmed — 0 of 28 checked embed the host root path**
(`check_path_embedding.sh`, bracketed: the same grep finds `hostname`'s payload string and misses
an absent control).

But the reason is sharper than "these packages don't embed paths". Several **do** — `groff` bakes in
`/work/build/src/libs/libgroff/errarg.cpp`, `hdparm` bakes in `/work/build`. They embed the **guest**
path, and the two-root design holds that constant: the build runs *inside* the rootfs, so the
guest-visible build dir is `/work/build` in every root and the build never sees the host path.
**The design isolates mechanism 2 by construction and excludes mechanism 1.**

So the honest reading is: *Hermit eliminated every path-**triggered** divergence in the set; the
main arms say nothing about path-**embedded** divergence.* The lane then closed that gap rather than
leaving it asserted, with `guestpath_arm.sh` varying the **guest** path:

| package | native | hermit `--no-rcb-time` |
|---|---|---|
| `hostname`, `tree`, `zip` | DIFFERS | **DIFFERS** |

with a same-long-path-twice control coming out **IDENTICAL**, proving the difference is caused by
the path rather than residual nondeterminism. **Both mechanisms reproduced in one corpus with the
predicted opposite outcomes: the two-mechanism model is confirmed.** Hermit is deterministic at
either path and correctly declines to mask a truthful path difference.

> **Loose end, recorded not explained:** `hostname`'s mechanism-1 delta is **19 bytes, entirely in
> the ELF build-id**. Every mtime and the tar listing are identical and the path string is absent
> from the stripped artifact — `dh_strip` removes the visible path, but a path-dependent
> fingerprint survives in the build-id.

Separately, the earlier pilot: two independent fresh-root builds of `hello_2.8-2` produced a
bitwise-identical 68,896-byte `.deb` (`55306cc9…`, `cmp` = 0) at L1. Unblocked by Reverie #287 —
the minimized regression went from a 120 s timeout after 432,359 repeated interceptions to passing
in 0.01 s. **[C]**

**Denominator discipline: 52/52 controlled wins of 58 attempted, and 52/8,688 against the paper's target set.** The default arm (no `--no-rcb-time`) is 46/52 and remains labelled confounded.
The harness records **no** per-package durations, deliberately: any duration printed by a build
under Hermit is virtual time, so **no overhead or slowdown factor may be derived from this
experiment.** This
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
| Inverted join: flagged set ∩ known-buildable frame | 74 targets | **[C]** | **0 flagged targets** in that tree — corroborates the control arm |
| **Flagged-target arm** | **8 targets, 3,287 executed actions/round** | **[C]** | **0 reproduced** — see interpretation below |

> **RETRACTION (published on main, `47ebae27`).** An earlier version of this section — and of the
> experiment directory — reported the flagged arm as *"blocked by buildability: flagged targets do
> not build on a generic devserver."* **That was wrong and is withdrawn.** Three flagged batches
> reported `BUILD FAILED`, and buildability was inferred from the exit status. The actual root
> cause, read later from the message body, was:
>
> ```
> Buck2 daemon was killed by an OOM killer due to high memory pressure.
> ```
>
> Each `--isolation-dir` starts *and keeps* its own buck2 daemon; seven were created and none
> killed, on a box shared with three other agents' Rust builds. `BUILD FAILED` was a **proxy** for
> "cannot build here", and the root-cause line says something entirely different. It surfaced as an
> `h2 protocol error ... broken pipe`, which reads nothing like memory pressure.
>
> This is the **third** proxy-error this one experiment has documented, after comparing action
> *result* digests (2022) and the isolation-dir-substring false positive earlier the same night.
> The correct status of the flagged arm is **NOT MEASURED**.

**Do not report "buck2 is reproducible."** The clean arms are the *control* population; the arm
that would test flagged targets did not run.

Two caveats that change what the telemetry means: it is **user builds only** (Sandcastle/CI,
service accounts and cancelled builds filtered out), and **RE's action-cache "paranoid" mode pins
an action to its first result**, suppressing observable divergence for most remotely-executed
actions — so the residual signal concentrates in *locally* executed actions. That cuts against the
naive reading of "run RE actions twice". fbcode build-stamping also makes many binaries
nondeterministic by design, so 41,701 flagged targets is emphatically not 41,701 bugs.

> **Of 8 fbcode targets the fleet flagged on 2026-08-05, 0 reproduced their nondeterminism in a
> controlled two-round local rebuild over 3,287 executed actions per round.** Executor mix
> `Local=3287, Cache=0, RE=0`. The flagged action class for these targets is the final `rustc link`
> step, and those actions demonstrably executed in both rounds — this is **not** "the flagged action
> never ran". Sampling rule: flagged, `rustc`-only, non-GPU/ASIC/torch → largest coherent cluster
> (30 unittests in one Rust-port tree) → first 8 by sorted name. Arbitrary but reproducible.

**This is probably not a null result, and the reasoning matters more than the zero.** A same-host
A/B **cannot** reproduce cross-environment nondeterminism by construction: the telemetry observes
differing output digests across *developers, machines, paths, times, and toolchain states*, and two
local rounds hold every one of those constant. So 0/8 is the **expected** outcome if these flags
encode cross-environment nondeterminism, and would have been surprising only if they encoded
intra-host nondeterminism (uninitialized memory, hash iteration order, parallelism races).

It does not say the fleet is wrong. It **narrows what the flags can be** — and that cuts against
the naive framing of the buck2 ask:

| | |
|---|---|
| Hermit determinizes **within** a run — time, PIDs, scheduling, randomness | right tool for intra-host nondeterminism |
| Hermit does **not** normalize hostname or absolute build paths across machines | if these flags are path/host-sourced, **Hermit is not the fix** — a hermetic-root or path-normalization change is |

No Hermit leg was run, deliberately: nothing reproduced locally for it to fix, and manufacturing a
divergence would prove nothing about these targets. Against the Debian lane's shape
(13/13 → 13/13) this is **8 flagged → 0 reproduced → 0 available for Hermit** — a different shape
because the upstream step produced no candidates, not because Hermit underperformed.

> **OPEN CROSS-TRACK QUESTION, unresolved.** The Debian two-root experiment varies precisely one
> environment axis — the absolute build root path — and Hermit makes 13/13 byte-identical. The
> buck2 reading above says Hermit does not normalize absolute build paths. Both cannot be simply
> true as stated. Plausible reconciliation: in the Debian case the root path *triggers* divergence
> in sources Hermit does virtualize (timestamps, randomness, readdir order) rather than being
> embedded in the output. **Nobody has run the discriminating experiment**, and it is the single
> cheapest high-value follow-up on the board.

The retraction: two earlier `BUILD FAILED` results were **resource exhaustion, not target
unbuildability** — first seven concurrent buck2 daemons, then host memory pressure (146 kill signals
across 13 third-party rustc/link actions with four lanes sharing the box). `-j 4` made it pass, and
round 1 then built all 8 flagged targets in 3.5 minutes. Neither failure was ever about the targets.

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

## 3b. A named defect class, found three times by three different routes

The single most transferable product finding of the sprint:

> **A Detcore handler that lets the guest execute an indefinitely-blocking syscall *while holding
> the scheduler turn* deadlocks whenever the only task that can unblock it is queued behind.**

Three instances turned up in one night, each with a **different proximate cause** — which is
precisely why an audit is worth more than three point fixes:

| # | syscall | why it blocked | status |
|---|---|---|---|
| 1 | `epoll_pwait` | **no nonblocking path existed.** glibc implements `epoll_wait(2)` as `epoll_pwait`, so real programs never reached the (correct) `handle_epoll_wait` | fixed — **#1864**, reproducer `rc=124` at 90 s → `rc=0` at 2 s |
| 2 | `read` on a pipe | **the path existed and was not taken.** `openat` typed fds by *pathname string*, so `/dev/fd/63` — bash process substitution, actually a pipe — was classified `FdType::Regular` | fixed — patch on **#1850** |
| 3 | `openat` on a **FIFO** (write side) | **no nonblocking path exists**; blocks in-kernel at `wait_for_partner`. Convertible in principle but needs `BlockedPool` modelling, not a flag | **OPEN, design specified — this is what keeps Nix at N=0** |
| 4 | `epoll_pwait2` | byte-for-byte the pre-#1864 shape; **recent glibc may route `epoll_wait` through it**, bypassing the #1864 fix entirely | **OPEN — reachability risk to #1864** |

**Instance 2 is the same proxy trap as everything else in this report:** a pathname is a *proxy* for
a file's type; the authority is `S_IFMT`. `Pipe` appeared **zero times** in 30 MB of pre-fix debug
log. Detcore already classifies correctly on the SaBRe fallback path — `openat` had simply never
been brought into line. Verified in isolation with #1864 reverted: the reproducer advances from
`shrinking RPATHs` to `checking for references`.

**Instance 3 is now settled: measured, then deliberately NOT implemented.** The blocking side was
decoded from the scheduler log rather than assumed (`arg2: 577` = `O_WRONLY|O_CREAT|O_TRUNC` — the
**write** side). Real FIFO semantics were then measured (`harness/fifo-open-semantics.c`):

| probe | result |
|---|---|
| `O_WRONLY\|O_NONBLOCK`, no reader | **ENXIO** — a precise "no reader yet" |
| `O_WRONLY\|O_NONBLOCK`, reader present | succeeds |
| `O_RDONLY\|O_NONBLOCK`, no writer → `read()` | **returns 0 — spurious EOF** |

So the write side is convertible and *is* the side that deadlocks; **the read side is not** — a guest
that should wait for a writer would instead see end-of-input, and Linux offers no non-consuming
"does this FIFO have a writer?" probe. Even the convertible half needs new machinery, for two
reasons read from source: the turn-yielding framework is **fd-keyed** and `open` has no fd yet
(`ioaction_based_on_fd_status` starts with `get_fd(...).unwrap_or_else(|| panic!(…))`, and `get_fd`
has no `Openat` arm — `open`/`openat` *create* descriptors), and every would-block predicate matches
`EAGAIN`/`EWOULDBLOCK` while FIFO write-open signals **`ENXIO`**. That is `BlockedPool` modelling,
not a flag flip, so the lane stopped at a design writeup on #1850 with both sides specified.

> **⚠ Reachability risk in #1864 itself, not yet ruled out.** `epoll_pwait2` is byte-for-byte the
> pre-#1864 shape and marked `(MAYHANG)`, and **its own doc comment says "Detcore treats it exactly
> like `epoll_pwait`" — which #1864 makes false.** The same comment records that *recent glibc
> routes `epoll_wait`/`epoll_pwait` through `epoll_pwait2`*. On such a glibc/kernel pairing **#1864
> would be bypassed and the deadlock would return.** This host traces plain `epoll_pwait`, so the
> fix does engage here — but the fix's reach is a property of the host's glibc, and that has not
> been characterised. Anyone deploying #1864 elsewhere must check which entry point their glibc
> uses.

**Audit criterion, recorded for the follow-up:** every syscall that can block indefinitely must
either yield the scheduler turn or be modeled in the `BlockedPool`. `(MAYHANG)` comments mark many
of them; the gap is the unmarked ones **plus** those routed correctly only when upstream metadata
happens to be right — which is how instance 2 hid.

**Instance 3's exact stopping point is recorded so nobody re-derives it:** stopped task dtid 107 in
`openat` at `wait_for_partner`; held turn 2261 with `queue len 3`; queued partner dtid 109;
settling check is to confirm turn 2262 never commits. The reproducer is ~1 second of build
(`nondet-demo` with `fixupPhase` enabled), not 25 minutes.

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

**Parent-repo changes landed tonight:**

| change | commit | why |
|---|---|---|
| `scripts/prepare-demo08-assets.sh` — bind the crash seed to its fixture; stop reporting "no UAF" for runs that never ran | `6c7c0997` | the fleet-wide P0 above (#1877) |
| `experiments/demo08-crash-seed-calibration_20260807` — per-seed measurements behind that fix | `4eb80d7c` | durable evidence |
| `scripts/prepare-demo08-assets.sh` — probe cgroup boxing; degrade loudly to an unboxed calibration where it is unavailable | `be0d7ab9` | what the first fix then revealed |

**The first fix paid for itself within one CI run, which is the point of the whole exercise.** With
the diagnostic in place the gate stopped blaming the fixture and reported
`never executed the guest: 0 of 64 seeds ... last rc=3` — and `rc=3` is `hermit-box-run`'s
documented fail-closed status for "cgroup-v2 / systemd `--user` scope unavailable", which is the
normal condition on a GitHub-managed runner. All 64 seeds had completed in **0.8 seconds**. The
search was never entering the space it was searching. Five hours of a fleet-wide P0 reduced to one
legible line the moment the check was made to carry its own evidence. That run also confirmed the
fixture-identity binding was not hypothetical: CI's fixture hashes to `752202aee2d6` where this box
builds `c81d933d90c4`, so a crash seed genuinely does not transfer between the two.

Both verified as ancestors of freshly fetched `rrnewton/dev-hermit:main`, not asserted from a push
result.

**Landing status at freeze:** #1843, #1678, #1851 and #1864 are green on their own merits and were
blocked solely by the demo-gate outage above; #1851 and #1864 additionally await
`adversarial-review-codex1..4`. The gate was re-run on #1843 and #1678 against the fix. **No PR
caused the outage** — `main` itself was red, and the five green demo-gate runs that followed
`4be8edcd` (06:04–06:29Z) exonerate the only `main` commit in the window.

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
9. **`du` is not a disk measurement on this filesystem.** Run roots are `cp -a --reflink=auto`
   copies on btrfs, so `du` counts every reflinked copy in full: a directory reading **94 GB by
   `du`** measured **4.09 GiB total / 0.23 GiB exclusive** under `btrfs filesystem du -s`. The
   coordinator raised a false disk alarm against the wrong lane on a `du` number and withdrew it;
   the real consumers are the five Rust `target/` trees at ~71 GiB exclusive. `worktree-gc.sh`'s
   own header documents this ~3.9x overstatement and was not consulted. **The authority is
   `btrfs filesystem du -s` and its Exclusive column.**
10. **Timing numbers printed *by a wrapped build* are virtual, not wall.** nixpkgs' stdenv times
   phases with bash's `$SECONDS`, which under the wrap reads Hermit's **virtual** clock. Measured:
   `sleep 1` under Hermit prints `SECONDS=3`. So every *"completed in N minutes"* line in a wrapped
   nix build log is virtual time and must not be compared against a native wall-clock figure — a
   "170x slowdown" reported earlier tonight was exactly this units error, and was retracted. Only
   host-measured timings (harness `wall_s`, the 8.4x spawn-cost figure, zero-CPU hang evidence)
   are wall-clock.

## 6. What the next engineer should do first

1. **Fix the two nixpkgs compatibility bugs** — the cmake configure hang and fake-uid-0 breaking
   `tar` in `unpackPhase`. These, not determinization, are why real nixpkgs is N=0.
2. **Extend Debian beyond 58.** The design, controls and harness are done and the set is now
   52/52 measured of 58 attempted, so this is pure throughput on a convenience-biased selection — a larger, less biased
   sample is the obvious next increment. Also re-run the default (`--strict`, no `--no-rcb-time`)
   arm on a **working-PMU host** to confirm directly that the 6 default-arm divergences are a PMU confounder rather
   than inferring it from the flip pattern.
3. **Bisect the GHC `-C0` non-replication.** The harness now exists, so it is cheap.
4. **Vary ONE environment axis at a time** in the buck2 A/B — different absolute path, different
   user, shifted clock — instead of holding everything constant. That identifies which axis a
   flagged action is sensitive to, and only then does Hermit have a defined job. Also resolve the
   open cross-track question above. Superseded: retry the flagged arm serially — one isolation dir for the whole session, killed
   between phases, one target at a time. The failure was daemon memory pressure from
   concurrency, not target size; the harness now carries a `trap ... kill`.
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
`demo08-crash-seed-calibration_20260807` ·
`rb_ghc_rts_ticker_determinized_20260730` ·
`rb_ghc_j1_determinism_20260730` ·
`rb_drb_haskell_ghc_concurrency_20260729` ·
`rb_nix_minimum_hermit_dose_20260730` ·
`nix-hermit-execbuilder-prototype_20260729` (annotated: 3 refuted claims).
