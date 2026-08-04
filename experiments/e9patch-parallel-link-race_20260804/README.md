# e9patch vendored Makefile `-jN` link race (reverie #359)

## Question
reverie PR #359 ("Package third-party backend payloads from pinned source",
head `d3c60dc5`) makes `reverie-e9patch/build.rs` run
`make -C vendor/e9patch --jobs=N release` (N = `NUM_JOBS`, clamped 1..16) on a
**fresh copy** of the vendored tree each build. Is the reported link failure a
real build-graph race, and does an added prerequisite edge fix it while keeping
parallelism?

## Root cause
The vendored top-level `Makefile` declares, in the `dev` target:

```
dev: E9TOOL_LIBS += contrib/zydis/libZydis.a contrib/libdw/libdw.a
dev: contrib/zydis/libZydis.a contrib/libdw/libdw.a e9patch e9tool
```

The two contrib static archives and `e9tool` are **sibling** prerequisites of
`dev`. `e9tool` links both archives (`E9TOOL_LIBS`, Makefile line 56) but has
**no prerequisite edge** to them. Serial `make` builds the archives first by
list order; `make --jobs=N` builds all of `dev`'s prerequisites concurrently, so
the `e9tool` link can start before `ar` has created `libZydis.a`/`libdw.a`.
`release: dev` inherits the same graph.

## Fix (`reverie-e9patch/vendor/e9patch/Makefile`, +8 lines)
```
e9tool: | contrib/zydis/libZydis.a contrib/libdw/libdw.a
```
Order-only (`|`) prerequisites: the archives are always built before the
`e9tool` link, without forcing a spurious relink when an archive is newer, and
without serializing the rest of the build. The conventional `all` target links
the system `-ldw/-lZydis` and is on a separate path.

- Repo/branch: `rrnewton/reverie` `fix/e9patch-parallel-link-race`
- Fix SHA `31417312da49b92d452b507b35d15d3d15efd71b`, base `d3c60dc5` (#359 head)
- The vendored `Makefile` exists only on #359's branch, so this is folded into
  #359's integration (patching-coalesce), not a PR to reverie `main`.

## Method
Faithful reproduction of `build.rs::build_e9patch_tools` (`logs/run_build.sh`):
fresh `cp -a` of the pristine tree, then `make --jobs=N release`, then assert
both `e9tool` and `e9patch` exist. Batches via `logs/batch.sh`.

## Results (N stated)
| Condition | Build | -j | N | Result |
|---|---|---|---|---|
| Unpatched, realistic | clean | 16 | 24 | 24 PASS (no race) |
| Unpatched, realistic | clean | 48 | 24 | 24 PASS |
| Unpatched, realistic | clean | 96 | 20 | 19 PASS, 1 = BpfJailer `FILE_OPEN` block (not the race) |
| **Unpatched, forced window** (4s archive sleep) | clean | 16 | 1 | **FAIL** — `ld: cannot find contrib/zydis/libZydis.a` → `Makefile:58: e9tool Error 1` → `Waiting for unfinished jobs` |
| **Patched, forced window** (4s archive sleep) | clean | 16 | 1 | **PASS** in 4.68s (waited for archives; archives complete *before* the e9tool link line; 0 link errors) |
| Patched, realistic | clean | 16 | 40 | 40 PASS |
| Patched, realistic | clean | 48 | 20 | 19 PASS, 1 = BpfJailer block on `cc1 dwarf_linesrc.c` (not the race) |

## Interpretation
- The **"deterministic on a clean high-j build" premise is REFUTED as stated**:
  across 68 unpatched clean builds (`-j16/48/96`) the race never fired, because
  the heavy `e9tool` C++ objects (`e9frontend`, `e9metadata`, …) usually finish
  after the archives on this fast/warm 316-core box, keeping the link gated
  behind them. The missing edge is nonetheless a **real latent race** — it is
  data-dependent on relative compile times, disk/cache state, and core
  scheduling, i.e. exactly the conditions that differ in CI.
- The **mechanism is proved causally** by forcing the window (delay the archive
  rules): unpatched races ahead and fails with the exact CI signature; patched
  waits and succeeds with archives ordered before the link. Both sides bracketed.
- The fix is **parallelism-preserving**: order-only prereq; `e9patch` objects,
  `e9tool` objects, and both archives still compile concurrently — only the
  `e9tool` link waits (forced-window wall ≈ the injected archive delay).
- The scoped `.SECONDEXPANSION` + `e9tool: $$(E9TOOL_LIBS)` variant was tried and
  **refuted**: the target-specific `E9TOOL_LIBS` (set by `dev`) did not resolve
  during the prerequisite's second expansion in this GNU make, so it did **not**
  order the link. The unconditional order-only edge is the working fix.

## Reproduction
```
cd <reverie>; git fetch origin +refs/pull/359/head:pr-359
git archive d3c60dc5 reverie-e9patch/vendor/e9patch | tar -x -C /tmp/e9v
# negative (mechanism): inject `sleep 4` before each `$(MAKE) -C contrib/...`
#   rule, then: make -C /tmp/e9v/.../e9patch --jobs=16 release   -> Error 1
# positive: add `e9tool: | contrib/zydis/libZydis.a contrib/libdw/libdw.a`
#   (with or without the sleep) -> PASS, archives ordered before the link
```
See `logs/` for `run_build.sh`, `batch.sh`, and trimmed crux logs.
