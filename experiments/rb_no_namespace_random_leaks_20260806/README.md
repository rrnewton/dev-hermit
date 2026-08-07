# Which randomness sources actually leak under `hermit run --no-namespace` — and why `--no-namespace` was never needed

**Date:** 2026-08-06 **Host:** devbig176 **Agent:** claude-coord-176

## Headline

`hermit run --tmp=/tmp` — an **existing flag** — determinizes every source
measured here *and* lets guest writes land on the host filesystem. The
reproducible-builds Nix seam does not need `--no-namespace`, does not need
`setarch -R`, and does not need a new Hermit mode. Nothing had to be fixed in
Hermit; the prototype was using the wrong flag for the right reason.

## Question

`experiments/nix-hermit-execbuilder-prototype_20260729` reports that the Nix
`execBuilder` seam must use `hermit run --no-namespace`, because the default
full-namespace mode discards writes to `$out`. It then reports two residual
nondeterminism leaks in that mode:

1. **`AT_RANDOM`-seeded userspace PRNGs**, claimed to be why bash `$RANDOM`
   varies ("glibc/bash derive it from the kernel-supplied `AT_RANDOM` auxv
   bytes, which `setarch -R` does not zero").
2. **`/proc/sys/kernel/random/uuid`**, claimed "read straight from the host
   kernel RNG because `--no-namespace` shares host procfs".

`experiments/rb_nix_minimum_hermit_dose_20260730` contradicts (2): its probe
reads that exact path under `--no-namespace` and reports the build
**reproducible**. It also gives a *different* mechanism for `$RANDOM` — "bash
seeds it from the real host `getpid()`".

Which leaks are real, what is the mechanism, and is `--no-namespace` actually
required? All three prior claims are attributions inferred from a build hash,
not measurements of the individual source.

## Method

Measure each candidate **individually** rather than inferring it from a build
hash. Five randomness probes plus two write-visibility probes, four modes, N=10
runs per randomness cell. The recorded quantity is the **number of distinct
values across the 10 runs**: `distinct == 1` is determinized, `> 1` is a leak.

| probe | what it reads |
| --- | --- |
| `at_random` | the 16 bytes at `getauxval(AT_RANDOM)` |
| `gettimeofday` | `gettimeofday()` seconds + microseconds |
| `getpid` | `getpid()` |
| `bash_random` | three successive `$RANDOM` draws (bash 5.1.8) |
| `procfs_uuid` | one read of `/proc/sys/kernel/random/uuid` |
| `write_visible_tmp` | does a file written under host `/tmp` survive the run? |
| `write_visible_out` | does a file written **outside** `/tmp` survive the run? |

The two write probes are what make a mode *usable* at all: Nix puts its build
directory under host `/tmp` when `sandbox = false`, and `$out` lives outside
`/tmp` in `/nix/store`. A mode solves the problem only if it determinizes **and**
both writes survive.

**`native` is the positive control.** Every randomness probe must vary natively,
or the probe has no power and a `determinized` verdict elsewhere is vacuous. All
five vary 10/10 natively, so every verdict below is load-bearing.

The two shell probes use bash builtins only, matching the Nix build sandbox
(which clears `PATH`).

## Results (`results.csv`, N=10)

Distinct values / 10 runs, then the two write verdicts:

| source | `native` | `hermit run` | **`hermit run --tmp=/tmp`** | `hermit run --no-namespace` |
| --- | --- | --- | --- | --- |
| `at_random` | 10 — varies | 1 — determinized | **1 — determinized** | 1 — determinized |
| `gettimeofday` | 10 — varies | 1 — determinized | **1 — determinized** | 1 — determinized |
| `procfs_uuid` | 10 — varies | 1 — determinized | **1 — determinized** | 1 — determinized |
| `getpid` | 10 — varies | 1 — determinized | **1 — determinized** | **10 — LEAK** |
| `bash_random` | 10 — varies | 1 — determinized | **1 — determinized** | **5 — LEAK** |
| `write_visible_tmp` | visible | **DISCARDED** | **visible** | visible |
| `write_visible_out` | visible | visible | **visible** | visible |

`--tmp=/tmp` is the only mode with no `LEAK` and no `DISCARDED`.

Whole-process-tree check, 4 consecutive runs of a bash script that spawns three
subshells and reads its own stack base:

```
--tmp=/tmp      self=3      child1=5      child2=7      child3=9      stack=7ffffffdb000   (x4, byte-identical)
--no-namespace  self=929883 child1=929885 child2=929888 child3=929890  stack=7ffffffdb000
                self=929901 child1=929903 child2=929906 child3=929908
                self=929920 child1=929935 child2=929944 child3=929946
                self=929957 child1=929959 child2=929961 child3=929963
```

Under `--no-namespace` even the *spacing* between child PIDs varies
(`+2,+3,+2` vs `+15,+9,+2`), so this is not a fixed offset that a build could
normalize away.

## Findings

### 1. `--no-namespace` was never required. The obstacle was Hermit's private `/tmp`.

The prototype's mode table says the default full-namespace mode does not persist
writes to `$out`, attributing it to "private mnt ns discards them". That
mechanism is wrong: a mount namespace isolates the *mount table*, not file
contents. `write_visible_out` is **visible** under the default mode — writes
outside `/tmp` always persisted.

What the default mode discards is `/tmp` alone, because Hermit mounts a private
tmpfs there. With `sandbox = false`, Nix builds in `/tmp/nix-build-*`, so the
**build directory** vanished — which is what was observed and then misread as
"`$out` does not persist". `--tmp=/tmp` exposes host `/tmp` and the whole problem
disappears, with every namespace retained.

`setarch -R` also becomes unnecessary: it was added because `--no-namespace`
cannot pin ASLR, and the full-namespace mode pins it itself.

### 2. The `AT_RANDOM` premise is refuted — it is already determinized

`AT_RANDOM` is byte-identical across all 10 `--no-namespace` runs
(`a2cd18d300537a5cb083dc48dbfa0ef2`), and identical to the default-namespace
value. Detcore overwrites it in `handle_post_exec` (`detcore/src/lib.rs`) — a
post-exec hook, not a namespace effect, so it fires in every mode:

```
DETLOG [post_exec, dtid 3] init auxv AT_RANDOM value to [162, 205, 24, …]
```

`AT_RANDOM` is therefore **not** why bash `$RANDOM` varies, and there was never
anything to fix there.

### 3. The procfs-UUID premise is refuted — the contradiction resolves for `rb_nix_minimum_hermit_dose`

`/proc/sys/kernel/random/uuid` is identical across all 10 `--no-namespace` runs.
Hermit does not rely on procfs being privately mounted; it intercepts the path in
its own procfs layer, `detcore/src/procfs.rs`:

```rust
"/proc/sys/kernel/random/uuid" => ProcfsKind::RandomUuid,
```

`rb_nix_minimum_hermit_dose_20260730` was right and the execbuilder prototype was
not. The difference is **not** rootless-podman versus host: this run reproduces
the podman result on the bare host, so the container was never the reason. One
bug, not two.

### 4. The only real leak is `getpid()`

10 distinct values in 10 runs under `--no-namespace`; 1 under every mode that
keeps a PID namespace. Hermit's PID determinism comes **entirely from the PID
namespace**, not from syscall virtualization — `getpid` is not intercepted at
all. It is classified pass-thru in `detcore/src/syscall_classification.rs`, under
a comment that states its own precondition:

> These existing and triaged passthroughs are conditionally repeatable under
> Hermit's **fixed-container**, stable-filesystem, and serialization assumptions.

`--no-namespace` removes the fixed container and silently voids that precondition
— for `getpid` and for the rest of the identity family in the same list
(`getppid`, `getpgid`, `getpgrp`, `getsid`, `gettid`, `getcwd`).

Hermit's own `run --help` already documents this correctly, in PID terms rather
than `AT_RANDOM` terms:

> Host process, filesystem, and network state are shared, reducing determinism.
> Schedule and preemption replay require stable namespace PIDs and are not
> supported.

### 5. `bash $RANDOM` is the observable consequence, and it is intermittent

Under `--no-namespace`: 3 distinct values in one 10-run block, 5 in another. Under
every PID-namespace mode: 1 in 10. **Intermittent, not per-run** — a build
sampling `$RANDOM` a few times looks reproducible most of the time and then is
not, which is the worst failure shape for a build system, and the reason this
was mis-attributed twice.

**Honest limitation on mechanism.** That `getpid` is the only varying input to
bash's seed is established **by elimination** — time and `AT_RANDOM` measure
identical, `getpid` measures varying, and every mode that fixes `getpid` fixes
`$RANDOM`. It is **not** established from bash's source: a reimplementation of
bash 5.1's documented `seedrand`/`intrand32` (`tv_sec ^ tv_usec ^ getpid()`,
Park–Miller) failed to reproduce the observed triples from the recorded PIDs, so
the exact seed path in this RHEL bash 5.1.8 build is unconfirmed. Both prior
experiments asserted a bash internal; neither verified it, and neither does this
one. **The leak is measured; the bash internal is inferred.**

## Recommendation

Change the Nix `execBuilder` wrapper from

```sh
exec /usr/bin/setarch x86_64 -R <hermit> run --no-namespace -- <stdenv-bash> "$@"
```

to

```sh
exec <hermit> run --tmp=/tmp -- <stdenv-bash> "$@"
```

This keeps the PID, user, UTS and mount namespaces, so PIDs are **real and
deterministic** rather than fabricated, and Hermit's deterministic `/proc` stays
mounted.

### Why not a new "drop only the mount namespace" mode

That was the design under consideration before this measurement, and it is now
unnecessary — and it would have been worse. Dropping the mount namespace while
keeping the PID namespace leaves the guest reading the **host** procfs, which is
bound to the host PID namespace: `/proc/self` would resolve to the guest's host
PID, and `/proc/<vpid>` for a small in-namespace PID would silently address an
unrelated **host** process. Hermit's procfs layer sanitizes `/proc/self/...`
content but does not remap numeric `/proc/<pid>/...` paths. Retaining the mount
namespace means that question never arises.

Detcore-side `getpid` virtualization is also the wrong seam and is not needed:
without a PID namespace a virtual PID names no real process, so `kill`, `tgkill`,
`waitpid`, `sched_*`, `prlimit`, `/proc/<pid>/…` and pidfiles would all need
translation, and any gap signals an unrelated host process.

### Residual limitations of the recommendation

- `--tmp=/tmp` **shares host `/tmp`**. That is deliberate here (Nix's build
  directory must be visible) but it re-admits host state into the guest: two
  concurrent builds, or leftover files from a previous run, are shared inputs.
  Hermit determinizes execution; it does not make a mutable shared filesystem
  deterministic.
- This is `reproducible-output-under-shared-/tmp`, not a strict-sandbox result.
  Isolation of everything except `/tmp` is retained, which is strictly more than
  `--no-namespace` offered.
- Not measured here: whether a real nixpkgs derivation builds green under this
  flag. That is the Nix agent's seam, and the scaling limitation the prototype
  recorded (Hermit sequentializes `make -j`) is unaffected by this change.

## Reproduce

```sh
cd experiments/rb_no_namespace_random_leaks_20260806/harness
cc -O2 -o probes probes.c
N=10 HERMIT_BIN=/path/to/hermit ./run-matrix.sh    # writes ../results.csv
```

Needs a Hermit build and permission to create user+PID namespaces; ~4 min at
N=10. `native` must show `LEAK` on all five randomness rows; if it does not, the
probes are inert on that host and the other rows mean nothing.

## Files

- `harness/probes.c` — `AT_RANDOM`, `gettimeofday`, `getpid`.
- `harness/shell-probes.sh` — bash `$RANDOM`, procfs UUID (builtins only).
- `harness/write-probe.sh` — writes under host `/tmp` and outside it.
- `harness/run-matrix.sh` — the 4-mode × N-run sweep; writes `results.csv`.
- `results.csv` — machine-readable verdicts.
- `metadata.json` — SHAs, host, toolchain, N, superseded claims.
