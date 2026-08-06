# The landing blocker was fictional: `--verify-strict` already works, and the offline build works too

**Date:** 2026-08-06 · **Task:** `crates-io-also-403-blocks-rebuild` · **Local only, no egress**
**Host:** devbig014 · **Status:** committed to the parent, **not pushed** (egress 403)

## Headline

Two independent blockers were claimed. **Neither exists.**

1. **`--verify-strict` is already present and working in the shipped binary.** No rebuild is
   needed. The "verified: unexpected argument" evidence was a misread clap error.
2. **Even if a rebuild were needed, it builds offline in 67 seconds with zero egress.**
   `cargo build --release --bin hermit --offline` → `EXIT=0`, 218 crates compiled.

The hardened re-baseline is unblocked **right now**, and the crates.io allowlist ask is not on
its critical path.

## Blocker 1 — a misread clap error

The task records `--verify-strict does not exist in the current binary (verified: 'unexpected
argument')`. What the current binary actually says:

```
$ hermit run --verify-strict -- /bin/true
error: the following required arguments were not provided:
  --verify
```

That is a **required-argument** error, not an **unknown-argument** error. The parser recognises
the flag perfectly well; it requires `--verify` alongside it. Read the message as
"unexpected argument" and you conclude the flag is missing and needs a rebuild — which is how a
non-blocker became a P0.

It is in `--help`, and in the source at `hermit-cli/src/bin/hermit/run.rs:333`
(`verify_strict: bool`):

> `--verify-strict` — Compare the internal logs under the CANONICAL parity policy: strip only the
> real wall-clock timestamp prefix (genuinely irreproducible), canonicalize host memory addresses
> to first-appearance ordinals (so an ASLR shift is tolerated but allocation-order and aliasing
> changes still diverge), and compare everything else — virtual-time timestamps, …

Supplied correctly it runs and returns a verdict.

## Blocker 2 — crates.io is 403, and it does not matter

The egress half of the premise is **true**: `crates.io` → **403**, `static.crates.io` → **403**,
same destination filter as github. But the conclusion drawn from it does not follow, because the
local cargo cache is fully populated: **`~/.cargo/registry` is 691 MB with 659 cached crate
files**, plus a git cache holding the pinned `reverie` and `liteinst2` revisions.

Measured, into a **fresh isolated `CARGO_TARGET_DIR`** (deliberately not the shared
`hermit/target`, so a failure could not damage the release binary other agents are using):

| | |
|---|---|
| command | `cargo build --release --bin hermit --offline` |
| result | **`EXIT=0`** |
| wall | **1 m 07 s** |
| crates compiled | **218** (of 308 in `Cargo.lock`) |
| `error:` lines | **0** |
| compiler cache | none — `RUSTC_WRAPPER` unset, no sccache |
| env | `PKG_CONFIG_PATH=$LU/lib64/pkgconfig`, `LD_LIBRARY_PATH=$LU/lib64` where `LU=/home/newton/.local/hermit-deps/lu/usr` |

218 crates from an empty target dir with no compiler cache is a real build, not a no-op; 67 s is
simply what a 316-core box does with that graph. The produced binary is
`hermit 0.2.0 (2026-08-06, gf89c69766371-dirty)` — **newer than the shipped one**
(`g0f891e432a75`) — and it accepts `--verify-strict`.

**So the allowlist ask can be narrowed.** `crates.io` + `static.crates.io` are *not* required to
rebuild while the cache is warm. They would matter for a genuinely cold machine or a new
dependency; github.com remains required for push/PR, which is a separate need.

## The first hardened measurement, which is now possible

With the flag exercisable, the canonical comparison was run on the simplest possible guest:

```
hermit run --verify --verify-strict -- /bin/true      # default ptrace backend
:: Log differences found between runs.
:: Failure: nondeterministic.        rc=1
```

Detached probe with a durable log (`ignored/verify-strict-probe/`):

- run1 and run2 logs are **both 515 lines** — same length, so no truncation or divergent control flow.
- **956 differing lines** (~478 of 515 differ on each side).
- Normalising hex values and long numbers makes the two sides **identical in shape and count**.
  The divergence is therefore entirely in **register values and numeric fields**, not in which
  events occurred.
- Most frequent differing shapes: `handle_signal: received signal SIGSEGV` (31 lines per side),
  `DETLOG (pre)/(post) registers`, and `[sched] advance global time for scheduler turn`.

Reproduced on the **freshly built** binary as well, so it is not an artifact of a stale binary.

**Deliberately not overclaiming.** This is consistent with the previously recorded host
condition that L2 is unattainable for any guest on this box and that ptrace reproduces it on
unmodified code. One host cannot distinguish "hardened standard exposes a real product gap" from
"this box cannot reach canonical determinism at all". What is solid and new is that **the
canonical comparison is exercisable**, so the hardened standard can now be *measured* rather than
being blocked — and its first measurement against the reference backend is a failure that needs
confirming elsewhere before attribution.

## Reproduction

```sh
LU=/home/newton/.local/hermit-deps/lu/usr
export LD_LIBRARY_PATH=$LU/lib64
hermit/target/release/hermit run --verify --verify-strict -- /bin/true   # works today, rc=1

# offline rebuild, isolated target dir so the shared binary is never at risk:
export PKG_CONFIG_PATH=$LU/lib64/pkgconfig
export CARGO_TARGET_DIR=$PWD/ignored/offline-build/target
cd hermit && cargo build --release --bin hermit --offline                 # EXIT=0, ~1m
```

## What this changes

- **The hardened re-baseline can start now.** No rebuild, no allowlist, no waiting.
- **Narrow the allowlist ask** to what is actually blocked: github.com + api.github.com for
  push/PR. crates.io is not blocking rebuilds while `~/.cargo` stays warm.
- **Do not let the cargo cache be cleaned.** It is currently the only thing standing between the
  fleet and a genuinely blocked rebuild. That is a real fragility created by the 403, even though
  it is not biting today.
