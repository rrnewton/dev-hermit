# Starter nondeterministic-nixpkgs list (Hermit exec-builder targets)

Purpose: seed candidates for the `epic-nix-reprobuild` Hermit-determinization
work. Each entry notes the nondeterminism *class* and whether it is expected to
be **on-machine reproducible** (single-host `--check`/canonical rebuild will
show a diff → Hermit can attempt to fix it) or **cross-machine only**
(single-host rebuild is already reproducible → Hermit cannot demonstrate a fix
on one host; needs two hosts, or is out of scope for the runtime wrap).

This is a triage list, not a claim that Hermit fixes each one. The oracle for
"can Hermit even engage here on one host" is the **canonical-rebuild oracle**
(`harness/rebuild-canonical.sh`), NOT `nix --check` — see the nftables finding
in `README.md` for why `--check` gives false positives on self-referential
outputs.

## Classes of build-time nondeterminism vs Hermit `--no-namespace` coverage

| class | example source | Hermit `--no-namespace` covers? |
|---|---|---|
| wall-clock timestamps | `date`, `__DATE__`/`__TIME__`, mtime baked into archives | **yes** (virtualized) |
| `/dev/urandom` reads | temp-name seeds, salts | **yes** (virtualized) |
| `AT_RANDOM` userspace PRNG | bash `$RANDOM`, glibc `rand()` w/o srand | **no** (setarch -R doesn't zero AT_RANDOM) |
| `/proc/sys/kernel/random/uuid` | uuid temp dirs | **no** (shared host procfs) |
| filesystem readdir order | `*` glob / `find` ordering into archives/linker | **no** by wrap alone (Hermit doesn't reorder host getdents) |
| thread/parallelism race | `make -jN` output interleave, ar/linker ordering | partial (Hermit schedules threads deterministically *within* a proc; cross-proc make needs full determinism) |
| cross-machine env | CPU features, nproc, hostname, kernel/toolchain | **no** (environmental; not a single-host runtime issue) |

## Candidate packages

Prioritize **on-machine-reproducible-once-broken** candidates that exercise the
classes Hermit *does* cover, since those are where the wrap can show a win.

| package | version seen | nondeterminism class (reported) | on-machine? | why a good/poor Hermit target |
|---|---|---|---|---|
| `nftables` | 1.1.6 | Lila 4-hash | **cross-machine only** (verified here) | Poor: on-machine reproducible; `--check` diff is pure self-reference artifact. Keep as a *negative control*. |
| `bcachefs-tools` | 1.34.0 (research) / 1.38.8 (this pin) | reported non-repro | unverified (drifted) | Re-pin nixpkgs to get 1.34.0 before testing; version drift invalidates comparison. |
| controlled `nondet-time` (this repo) | — | timestamps + urandom | **on-machine** | **Positive control — Hermit fixes it (proven).** Use as the regression witness. |
| packages baking `date`/`__DATE__` into output | many (docs, firmware blobs, `--version` strings) | timestamps | **on-machine** | Good: exactly the class Hermit virtualizes. Grep nixpkgs for `SOURCE_DATE_EPOCH`-noncompliant builders. |
| packages seeding temp names from `/dev/urandom` | test-suite scratch dirs, some codegen | urandom | **on-machine** | Good: Hermit virtualizes urandom. |
| Perl/Python packages with hash-seed-order output | `PERL_HASH_SEED`, `PYTHONHASHSEED` consumers | AT_RANDOM-derived hash seed | **on-machine** | Mixed: interpreter hash randomization is AT_RANDOM-seeded → Hermit `--no-namespace` does NOT fix (would need AT_RANDOM zeroing or full-namespace). Good stress case for the coverage gap. |
| Go modules without `-trimpath`/`-buildid=` | various | build-id / path embedding | on-machine (path) | Marginal: mostly fixed by flags, not runtime. |
| archives with readdir-ordered members | `tar`/`ar`/`zip` of a glob | fs ordering | **on-machine** | Good stress case: Hermit wrap alone likely does NOT fix (host getdents order) → motivates a Hermit getdents-canonicalization feature. |

## How to triage a new candidate

```sh
. /home/newton/.nix-profile/etc/profile.d/nix.sh
export http_proxy=http://fwdproxy:8080 https_proxy=http://fwdproxy:8080
cd experiments/nix-hermit-execbuilder-prototype_20260729
# 1. Is it even nondeterministic ON THIS MACHINE? (fair oracle, not --check)
bash harness/rebuild-canonical.sh <pkg>-native '(import <nixpkgs> {}).<pkg>'
#    reproducible      -> cross-machine only; NOT a single-host Hermit demo. Skip or mark negative control.
#    NONDETERMINISTIC  -> on-machine; proceed.
# 2. Does the Hermit wrap fix it?
bash harness/rebuild-canonical.sh <pkg>-hermit \
  '(import ./nix/hermit-wrap.nix {}).wrap ((import <nixpkgs> {}).<pkg>)'
#    reproducible -> WIN. NONDETERMINISTIC -> characterize the residual source (which class above).
```

## Recommended next targets for the epic

1. Re-pin nixpkgs to obtain `bcachefs-tools-1.34.0` and run the two-step triage
   (the current pin's 1.38.8 is a different derivation).
2. Systematically find **on-machine-nondeterministic** packages: run
   `rebuild-canonical.sh` across a sample of nixpkgs leaf packages and keep only
   those that differ on one host — that is the true Hermit-addressable set.
3. Pick 2–3 confirmed on-machine-nondeterministic-via-timestamps/urandom
   packages as the first real (non-synthetic) determinization wins.
4. File Hermit feature requests for the two characterized `--no-namespace` gaps
   (AT_RANDOM zeroing; procfs-uuid virtualization) and the getdents-ordering
   class, since those block whole categories above.
