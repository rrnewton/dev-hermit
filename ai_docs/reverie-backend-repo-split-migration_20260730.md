# Reverie backend-repo-split — migration recipe

Task: `phase1-split-dep-heavy-reverie-repos` (part of
`vision-reverie-backend-repo-split`). Author: hermit-reveriesplit (impl agent,
opus-4.8), 2026-07-30.

Goal: move the dep-heavy experimental backends (`sabre`, `e9patch`,
`dynamorio`) out of the `reverie` repo into three owner-created standalone
repos, each depending on the core `reverie` crate and each vendoring the
reverie tests/examples it needs into **one** subdir (NOT a recursive `reverie`
submodule). Endstate: `reverie/` = pure first-party (traits/infra + the
in-tree `liteinst`/`kvm` backends), and `dev-hermit` no longer carries the
dep-heavy backends as recursive submodules.

## Current state (reverie @ origin/main 4deb923)

Core crate lives at `reverie/reverie/` (dir `reverie`, package **now**
`reverie-core` after PR #304). Backends and their dependency closures:

| Backend | reverie crate(s) | native third-party | reverie-crate deps (become git deps) |
| --- | --- | --- | --- |
| e9patch | `reverie-e9patch` | `third-party/e9patch` (submodule, `update=none`; only needed for `#[ignore]`'d integ tests — the rlib/cdylib builds with just `cc`) | `reverie-core`, `reverie-preload` (`default-features=false`, feature `coordinator-rpc`), `reverie-ptrace`, `reverie-rpc-transport` |
| dynamorio | `reverie-dbi` | `third-party/dynamorio` (submodule, checked out; **build.rs hard-requires it**) | `reverie-core`, `reverie-memory`, `reverie-rpc-transport` |
| sabre | `experimental/reverie-sabre` (+ siblings `reverie-rpc`, `reverie-rpc-macros`, `reverie-sabre-macros`, `reverie-sabre-strace`, `nostd-print`) | `third-party/sabre` (submodule, `update=none`) | `reverie-core`, `reverie-memory`, `reverie-rpc-transport`, `reverie-syscalls` |

Vendored test/example sources (the "one subdir" per repo):

- e9patch: `reverie-e9patch/tests/{backend.rs,fixtures/}` +
  `reverie-examples/{src/e9patch_smoke.rs, tests/e9patch_direct.rs,
  tests/fixtures/e9patch_direct_tool.c}`.
- dynamorio: `reverie-dbi/tests/**` + `reverie-dbi/scripts/**`.
- sabre: `experimental/reverie-sabre/tests/**` +
  `experimental/reverie-sabre-strace/**` (the strace demo tool).

## Ordering (dependency-first, minimizes breakage)

1. **[DONE] Rename core package `reverie` → `reverie-core`** with in-repo
   dependents aliased `reverie = { …, package = "reverie-core" }` (zero
   `use`-churn). PR https://github.com/rrnewton/reverie/pull/304. This is the
   unblocker: a standalone repo can now `reverie-core = { git = … }` without
   the dep key colliding with the repo name.
2. **Land #304 on `rrnewton/reverie:main`.** All later git deps should pin the
   **landed** `reverie-core`/friends SHA on `main`, not the feature branch.
   (During bring-up, git deps may pin `branch = "codex/reverie-core-rename"`;
   this is a temporary bring-up state, not a landable one.)
3. **Populate each standalone repo** (owner already created empty
   `rrnewton/{reverie-e9patch,reverie-dynamorio,reverie-sabre}`): move the
   backend crate source in, convert its in-workspace path deps on reverie
   crates to git deps, add its own `third-party/<tool>` submodule where the
   build needs it, vendor its tests into one subdir, add a CI workflow.
4. **Verify each repo builds+tests standalone** against `reverie-core` on
   `main` (rlib/cdylib build always; native/integration tests where the host
   provides the tool — e9tool/DynamoRIO/SaBRe).
5. **Remove the backend from `reverie/`** (crate dirs, workspace members,
   `third-party/<tool>` submodule) — a separate reverie PR, gated on the
   standalone repo being green. Keeps `reverie/` pure first-party.
6. **Parent + Hermit (via coord):** drop the recursive backend submodules from
   `dev-hermit`; add the standalone repos where consumed; bump the reverie
   pin; apply the 5-manifest `package = "reverie-core"` alias in Hermit
   (`detcore`, `detcore-dbi`, `detcore-sabre`, `detcore/tests/testutils`,
   `hermit-cli`) — no `use` edits (~512 refs unchanged).

## Standalone Cargo.toml pattern (git deps)

Each reverie-crate path dep becomes a git dep on the same repo. Cargo selects
the crate by name from the repo's workspace. Example (e9patch), landable form
pins the merged SHA on `main`:

```toml
[dependencies]
reverie            = { git = "https://github.com/rrnewton/reverie", rev = "<merged-core-sha>", package = "reverie-core" }
reverie-preload    = { git = "https://github.com/rrnewton/reverie", rev = "<merged-core-sha>", default-features = false, features = ["coordinator-rpc"] }
reverie-ptrace     = { git = "https://github.com/rrnewton/reverie", rev = "<merged-core-sha>" }
reverie-rpc-transport = { git = "https://github.com/rrnewton/reverie", rev = "<merged-core-sha>" }
```

All four `rev` values must be the **same** commit so Cargo unifies one copy of
each reverie crate. Keep the `package = "reverie-core"` alias on the `reverie`
key so the backend's `use reverie::` source is unchanged.

## Vendored tests, not a reverie submodule

Copy the specific test/example files listed above into one clearly named subdir
per repo, e.g. `vendored-reverie-tests/`, with a `PROVENANCE.md` giving the
source repo + path + SHA they were copied from. Do NOT add `reverie` as a
submodule of the backend repo (the whole point is to avoid 4+ recursive
reverie checkouts). When the vendored tests need reverie example *tools*, port
just those tool sources, not the whole `reverie-examples` crate.

## CI workflow template (per repo)

Mirror `reverie/.github/workflows/ci.yml` but scope to the single crate and
make native-tool tests opt-in:

```yaml
name: CI
on: [push, pull_request]
jobs:
  regular-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4               # NO submodules by default
      - uses: dtolnay/rust-toolchain@nightly    # match reverie rust-toolchain.toml
      - run: cargo build --all-features
      - run: cargo test --all-features           # native-tool integ tests are #[ignore]'d
      - run: cargo fmt --all -- --check
      - run: cargo clippy --all-targets --all-features
```

`third-party/<tool>` submodule + its integration tests run only on a runner
that provides the tool (e9tool / DynamoRIO / SaBRe), exactly as the tests are
`#[ignore]`'d in-tree today.

## Verification status (this pass)

- **Item 1 (rename): verified.** PR #304 @ af01aa83; `cargo metadata` full
  resolve shows `reverie-core` with no stale bare `reverie`; `cargo check`
  green on core + pure-rust dependents.
- **Item 2 e9patch proof-of-pattern: verified end-to-end.** A standalone
  `reverie-e9patch` (branch `seed/e9patch-standalone` @ 440193f on
  `rrnewton/reverie-e9patch`) with `reverie`/`reverie-preload`/`reverie-ptrace`/
  `reverie-rpc-transport` as **git deps** on `rrnewton/reverie@codex/reverie-core-rename`
  (no recursive reverie submodule) builds and tests standalone:
  - `with-proxy cargo check --all-features` → Finished (all 8 reverie crates
    fetched at af01aa83: reverie-core, reverie-process, reverie-syscalls,
    reverie-memory, safeptrace, reverie-rpc-transport, reverie-preload,
    reverie-ptrace).
  - `cargo test --all-features` → lib 60/60 pass on clean runs, 1 ignored;
    integration `tests/backend.rs` 13 ignored (native e9tool absent);
    `build.rs` assembled `syscall_trap.S` with only `cc`/`nm`.
  - `use reverie::…` source unchanged — the `package = "reverie-core"` alias
    resolves for an external git consumer, confirming the zero-churn strategy.
  - **Pre-existing flake (NOT a split artifact):** the crate's own unit suite
    intermittently fails one of `bootstrap_is_bounded_consumed_and_duplicate_safe`
    / `signal_fallback_sites_fail_closed_when_reported` /
    `partial_patch_coverage_fails_closed` (~0.01s, different test each run) under
    parallel execution. Reproduced identically in-tree
    (`cargo test -p reverie-e9patch --lib` in the reverie workspace). This is a
    parallel-execution/global-state race in the e9patch tests; file as a reverie
    test-hygiene follow-up.
- **Item 2 sabre / dynamorio: recipe only (scoped follow-ups).** Both are
  gated on #304 landing (git deps must repin to merged `main`). sabre also
  requires deciding the home of its experimental sibling crates (`reverie-rpc`,
  `reverie-rpc-macros`, `reverie-sabre-macros`, `reverie-sabre-strace`,
  `nostd-print` — sabre-only, so they should move WITH sabre) plus the
  `third-party/sabre` submodule. dynamorio's `build.rs` hard-requires the
  `third-party/dynamorio` submodule, so its standalone repo must carry that
  submodule (unlike e9patch). Follow the same git-dep + vendored-tests + CI
  pattern proven above.
- **Steps 5/6 (removal from reverie, parent/Hermit rewiring): coordinator-owned,
  gated on #304 landing** per the task ("Coordinate parent-repo changes via
  coord"): drop moved crates + `third-party/{e9patch,dynamorio,sabre}` from
  reverie; drop the recursive backend submodules from `dev-hermit`; bump the
  reverie pin; apply the 5-manifest Hermit `package = "reverie-core"` alias
  (no `use` edits).
