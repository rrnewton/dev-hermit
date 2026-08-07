# Which randomness sources actually leak under `hermit run --no-namespace`

**Date:** 2026-08-06 **Host:** devbig176 **Agent:** claude-coord-176

## Question

`experiments/nix-hermit-execbuilder-prototype_20260729` reports two residual
nondeterminism leaks that block reproducible Nix builds under
`hermit run --no-namespace` — the mode the Nix `execBuilder` seam requires so
build output lands in the real `/nix/store`:

1. **`AT_RANDOM`-seeded userspace PRNGs**, claimed to be why bash `$RANDOM`
   varies ("glibc/bash derive it from the kernel-supplied `AT_RANDOM` auxv
   bytes, which `setarch -R` does not zero").
2. **`/proc/sys/kernel/random/uuid`**, claimed "read straight from the host
   kernel RNG because `--no-namespace` shares host procfs".

`experiments/rb_nix_minimum_hermit_dose_20260730` contradicts (2): its probe
reads that exact procfs path under `--no-namespace` and reports the build
**reproducible**. It also gives a *different* mechanism for `$RANDOM` — "bash
seeds it from the real host `getpid()`".

So: which leaks are real, and what is the actual mechanism? Both prior claims
are second-hand attributions, not measurements of the individual source.

## Method

Measure each candidate source **individually** rather than inferring it from a
build hash. Five probes, three modes, N=10 runs each; the recorded quantity is
the **number of distinct values observed across the 10 runs**. `distinct == 1`
is determinized; `distinct > 1` is a leak.

Probes (`harness/probes.c`, `harness/shell-probes.sh`):

| probe | what it reads |
| --- | --- |
| `at_random` | the 16 bytes at `getauxval(AT_RANDOM)` |
| `gettimeofday` | `gettimeofday()` seconds + microseconds |
| `getpid` | `getpid()` |
| `bash_random` | three successive `$RANDOM` draws from bash 5.1.8 |
| `procfs_uuid` | one read of `/proc/sys/kernel/random/uuid` |

Modes: `native` (no hermit), `hermit run` (default namespaces),
`hermit run --no-namespace`.

**`native` is the positive control.** Every probe must vary natively, otherwise
the probe has no power to detect a leak and a `determinized` verdict elsewhere
would be vacuous. All five vary 10/10 natively, so all five verdicts below are
load-bearing.

The two shell probes deliberately use bash builtins only, matching the Nix build
sandbox (which clears `PATH`).

## Results (`results.csv`, N=10, distinct values / 10 runs)

| source | native | `hermit run` | `hermit run --no-namespace` |
| --- | --- | --- | --- |
| `at_random` | 10 — varies | 1 — determinized | **1 — determinized** |
| `gettimeofday` | 10 — varies | 1 — determinized | **1 — determinized** |
| `procfs_uuid` | 10 — varies | 1 — determinized | **1 — determinized** |
| `getpid` | 10 — varies | 1 — determinized | **10 — LEAK** |
| `bash_random` | 10 — varies | 1 — determinized | **3 — LEAK** |

## Findings

### 1. The `AT_RANDOM` premise is refuted — it is already determinized

`AT_RANDOM` is byte-identical across all 10 `--no-namespace` runs
(`a2cd18d300537a5cb083dc48dbfa0ef2`), and identical to the value the default
namespace mode produces. Detcore overwrites it in `handle_post_exec`
(`detcore/src/lib.rs`), which is a post-exec hook, not a namespace effect, so it
fires in both modes:

```
DETLOG [post_exec, dtid 3] init auxv AT_RANDOM value to [162, 205, 24, …]
```

`AT_RANDOM` is therefore **not** why bash `$RANDOM` varies, and there is nothing
to fix here.

### 2. The procfs-UUID premise is refuted — the contradiction resolves for `rb_nix_minimum_hermit_dose`

`/proc/sys/kernel/random/uuid` is identical across all 10 `--no-namespace` runs.
Hermit does not depend on procfs being privately mounted: it intercepts the path
in its own procfs layer, `detcore/src/procfs.rs`:

```rust
"/proc/sys/kernel/random/uuid" => ProcfsKind::RandomUuid,
```

The `rb_nix_minimum_hermit_dose_20260730` observation was correct and the
execbuilder prototype's was not. The difference is **not** rootless-podman
versus host — this run reproduces the podman result on the bare host, so the
container was never the reason.

### 3. The real leak is `getpid()`, and it is the only primitive one

`getpid` returns a host PID under `--no-namespace`: 10 distinct values in 10
runs. Under the default namespace it is a stable small PID. Hermit's PID
determinism comes **entirely from the PID namespace**, not from syscall
virtualization — `getpid` is not intercepted at all. It is classified pass-thru
in `detcore/src/syscall_classification.rs`, under a comment that states its own
precondition:

> These existing and triaged passthroughs are conditionally repeatable under
> Hermit's **fixed-container**, stable-filesystem, and serialization assumptions.

`--no-namespace` removes the fixed container and therefore silently voids that
precondition — for `getpid` and for the rest of the identity family in the same
list (`getppid`, `getpgid`, `getpgrp`, `getsid`, `gettid`, `getcwd`).

Hermit's own `run --help` already documents this correctly, in PID terms rather
than `AT_RANDOM` terms:

> Host process, filesystem, and network state are shared, reducing determinism.
> Schedule and preemption replay require stable namespace PIDs and are not
> supported.

### 4. `bash $RANDOM` is the observable consequence, and it is intermittent

3 distinct values in 10 `--no-namespace` runs; 1 in 10 under the default
namespace. **Intermittent, not per-run** — 6 of the first 10 runs returned the
canonical triple. Anything sampling `$RANDOM` a handful of times will look
reproducible most of the time and then not be, which is the worst failure shape
for a build system.

**Honest limitation on mechanism.** That `getpid` is the only varying input to
bash's seed is established by elimination — time and `AT_RANDOM` are measured
identical, `getpid` is measured to vary, and the mode that fixes `getpid` fixes
`$RANDOM`. It is **not** established by reading bash's source: a
reimplementation of bash 5.1's documented `seedrand`/`intrand32`
(`tv_sec ^ tv_usec ^ getpid()`, Park–Miller) failed to reproduce the observed
triples from the recorded PIDs (`harness/`-adjacent scratch, not kept), so the
exact seed path in this RHEL bash 5.1.8 build is unconfirmed. Both prior
experiments asserted a bash-internal mechanism; neither verified it, and neither
does this one. The *leak* is measured; the *bash internal* is inferred.

## Interpretation: where the fix belongs

Not at `AT_RANDOM` (already determinized) and not at the procfs UUID (already
intercepted). The question is how `--no-namespace` can keep deterministic PIDs.

**Virtualizing `getpid` inside Detcore is the wrong seam.** Without a PID
namespace a virtual PID names no real process, so every PID-consuming
syscall would need translation — `kill`, `tgkill`, `waitpid`, `sched_*`,
`prlimit`, `/proc/<pid>/…`, pidfiles written for other tools. Any gap sends a
signal to an unrelated host process; on a shared multi-tenant box that is an
active hazard, not just a correctness bug. It is also a core syscall-model
change, i.e. a `post-facto-human-review` trigger.

**Selective namespace retention is the right seam.** `--no-namespace` is
all-or-nothing today (`Container::new()` with no `unshare`), but the Nix seam
only needs the **mount** namespace dropped, so writes land in the real
`/nix/store`. The USER + PID namespaces can stay, and then PIDs are *real* and
deterministic rather than fabricated. Reverie already exposes exactly this
granularity — `Container::unshare(Namespace)` takes a bitflag
(`reverie-process/src/container.rs`), and hermit's default path already calls
`unshare(Namespace::USER | Namespace::PID)`. This is also option (a) the
execbuilder prototype itself asked for: "a Hermit mode that persists `$out`
while retaining full-namespace determinism".

**Known open issue with that seam, not yet resolved here.** The default mount
namespace also does `.mount(Mount::proc())` (`hermit-cli/src/bin/hermit/run.rs`),
which is what binds procfs to the new PID namespace. A PID namespace *without* a
mount namespace leaves the guest reading the host procfs, where its own entries
appear under host PIDs and `/proc/self` resolves against the host PID namespace.
Hermit's procfs interception layer may absorb this, but that must be measured
before the mode is offered. Nesting the namespaces externally is not a
workaround: `unshare --user --map-root-user --pid --fork -- hermit run
--no-namespace` aborts, because hermit's own `unshare(CLONE_NEWUSER |
CLONE_NEWPID)` is denied inside an already-mapped user namespace.

## Reproduce

```sh
cd experiments/rb_no_namespace_random_leaks_20260806/harness
cc -O2 -o probes probes.c
N=10 HERMIT_BIN=/path/to/hermit ./run-matrix.sh    # writes ../results.csv
```

Needs a hermit build and permission to create user+PID namespaces. Takes ~2 min
at N=10. `native` must show `LEAK` on all five rows; if it does not, the probes
are inert on that host and the other rows mean nothing.

## Files

- `harness/probes.c` — `AT_RANDOM`, `gettimeofday`, `getpid`.
- `harness/shell-probes.sh` — bash `$RANDOM`, procfs UUID (builtins only).
- `harness/run-matrix.sh` — the 3-mode × N-run sweep; writes `results.csv`.
- `results.csv` — machine-readable verdicts.
- `metadata.json` — SHAs, host, toolchain, N.
