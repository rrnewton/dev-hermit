# Demo 8 crash-seed calibration: why the P0 gate went red fleet-wide

**Question.** The P0 demo gate failed on every content-bearing head from 2026-08-07T06:31Z,
including `main` itself, with `error: no ASAN UAF found in seeds 0-63 for the generated fixture`.
Is that statement true?

**Answer: no.** It conflated two different facts, and neither was the one reported.

## Method

Rebuilt the pinned Demo 8 fixture from source on devbig176 and ran the calibration search by hand,
recording per-seed exit status and wall time — the two things `calibrate_crash_seed()` discarded.

Reproducing needs these host packages on CentOS Stream 9 (absent by default, and each one aborts
the fixture build at a different stage): `e2fsprogs-devel libblkid-devel libuuid-devel lzo-devel
libasan`.

## Results

See `results.csv`. Seeds 0-15 plus the previously cached seed 47.

- **3 of 16 seeds reproduce the UAF** (9, 13, 14). The search works.
- **Per-seed runtime spans 6s to 103s**, median 11s — against a **30s** default timeout. The tail
  was being truncated into false negatives.
- **Cached seed 47 does not crash a rebuilt fixture.** It converts cleanly in 8s. The seed had been
  calibrated against a different binary and cached under a key
  (`hermit-demo-assets-v1-Linux-X64`) that records neither the compiler nor the fixture.
- **Seed 14 reports the UAF on a thread whose process still exits 0**, so exit status alone is not
  a valid detector; the ASAN report text is.

## Interpretation

The gate's green had been riding a cached value it never re-derived. `calibrate_crash_seed()`
returns at its fast path whenever `.crash-seed` exists — and that file lives *inside the cached
directory* — so the live calibration branch was unexercised for as long as the cache kept hitting.
When the GitHub Actions cache entry was evicted at ~06:30Z, every runner took the untested branch
at the same moment. That is why the outage was instantaneous and fleet-wide rather than spreading.

The reported error was itself a proxy. `rc` was captured and never read, and the sole verdict was a
grep for the ASAN banner, so **"no ASAN string" was reported as "this seed did not crash" when it
equally means "this seed never executed."** CI's own timing refutes the message it printed: 64
seeds inside 37.4 seconds, including a release Hermit build, against a 30s per-seed timeout. Those
runs did not happen. Five hours of fleet-wide blockage followed from a message that named a
property of the fixture while the observation was about the machine.

Two bindings were missing, and both are the same policy predicate — *carry the condition with the
value*:

1. A crash seed is meaningless without the fixture binary it was derived from. It is now stored as
   `<seed> <fixture-sha256>` and refused on mismatch.
2. A "did not crash" verdict is meaningless without evidence the guest ran. Execution is now
   counted separately (statuses 0/124/134 with output) and the two failures are reported
   differently.

## Fix

`scripts/prepare-demo08-assets.sh`, landed on dev-hermit `main` as
`6c7c0997042c4be0844d5e2c4688a8e9f1875cf6`. Timeout default 30s → 150s from the measured
distribution. Bracketed on both sides: three planted negatives (bare legacy seed; seed bound to a
different fixture; guest that cannot start) and two positives (correct-identity fast path;
end-to-end calibration from an empty cache, which finds seed 9 at guest exit 134 in under 200s).

## Reproduction

```
sudo dnf install -y e2fsprogs-devel libblkid-devel libuuid-devel lzo-devel libasan
DEMO08_DIR=$PWD/ignored/demo08-btrfs DEMO08_BUILD_ROOT=$PWD/ignored/demo08-build \
  HERMIT_RELEASE=<path to release hermit> scripts/prepare-demo08-assets.sh
```
