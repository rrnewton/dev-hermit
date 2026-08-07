# Release 0.3 clean-checkout UX audit

**Verdict:** not release-ready at the measured revision. The default ptrace
path builds, installs, and passes a real L2 smoke test, but the public release
surface still identifies itself as 0.2.0, the documented release-package build
does not create the documented package, README overstates plain `--verify`, and
KVM is in a known total outage that can leave a CPU-running orphan after an
external timeout.

## Evidence boundary

- Repository: `rrnewton/hermit`
- Exact live `main`: `1fadc03779f2a246a9b5af5d4a93533511c837df`
- Git tree: `1fcb615c4c2d28448ac0c5de3605449c036b2ee4`
- Measurement began: `2026-08-07T02:27:17Z`
- Host: Linux `6.18.39-0_fbk0_hardened_0_ga43d5727b443`, x86_64;
  `perf_event_paranoid=1`; `/dev/kvm` absent
- Toolchain: `rustc 1.99.0-nightly (26ae60a9e 2026-07-28)`,
  `cargo 1.99.0-nightly (3efb1f477 2026-07-17)`
- Checkout: registered read-mostly slot `worktrees/w2/hermit`, clean before and
  after, with isolated Cargo home and target directories under `/tmp`
- Frontier recheck: live `main` was still `1fadc037...` after measurement.
- Exact-head CI was green: portable run `31139740417`, privileged run
  `31139740415`, docs run `31139740569`, and subsequent merge-gate runs.

No product files were edited. Generated binaries, Cargo state, verify JSON, and
logs remained outside the repository.

## New-user commands

| Surface | Exact result |
| --- | --- |
| `cargo build --workspace` with empty Cargo home/target | PASS, rc 0, 50.33 s, 1,334,272 KiB max RSS, no compiler warnings |
| `hermit --version` | rc 0: `hermit 0.2.0 (2026-08-07, g1fadc03779f2)` |
| `hermit run --strict -- /bin/echo hello` | PASS, rc 0, `hello`, 0.05 s; backend ptrace, default log level, no relaxations |
| `hermit run --strict --verify --verify-strict --verify-json ... -- /bin/echo hello` | PASS L2, rc 0; 303/303 INFO messages, JSON `verified=true`, `bitwise_parity=true`, `strictness=canonical` |
| README `cargo install --path hermit-cli` | PASS, rc 0, 66.20 s, 3,253,292 KiB max RSS; installed 0.2.0 and strict echo ran, but Cargo resolved 262 packages to latest compatible versions because the documented command is not locked |
| README `cargo build --release` | Build PASS, rc 0, 65.98 s, 3,303,092 KiB max RSS; **FAIL contract:** `target/install_pkg` was absent, so the next documented `./target/install_pkg/hermit --version` cannot run |

The install command emitted Cargo's toolchain-override and PATH warnings; the
workspace build itself emitted no compiler warnings.

## Release blockers

### P0: the release still identifies itself as 0.2.0

`hermit-cli/Cargo.toml:6`, the installed package, and both debug/release
binaries report `0.2.0`. A 0.3 release from this tree would carry the wrong
package and CLI identity. Live tracking exists in
[`rrnewton/hermit#1803`](https://github.com/rrnewton/hermit/issues/1803);
the broader initial crates.io release is
[`#1804`](https://github.com/rrnewton/hermit/issues/1804).

### P0: README's optimized-build recipe promises an artifact it cannot create

README says bare `cargo build --release` assembles every backend in
`target/install_pkg`. Root `Cargo.toml` explicitly excludes `hermit-install`,
`detcore-dbi`, and `detcore-sabre` from `default-members`. The measured command
therefore built successfully but produced no `install_pkg`; its Hermit binary
also rejected DBI as not compiled in. Replace the recipe with one that actually
builds/stages `hermit-install` and the matching backend-enabled Hermit binary,
then bracket the positive package contents and a negative default build.

Draft [`PR #1767`](https://github.com/rrnewton/hermit/pull/1767) is not a usable
fix: it is a stale, dirty, 47-file refile with red portable CI, not a focused
validated documentation correction.

### P0: README calls stripped verification L2/bitwise

README's LiteInst section says plain `--verify` produces bitwise-identical L2.
`WHATS_WORKING.md` makes the same broad claim throughout. A measured plain
`--strict --verify --verify-json` run reported:

```text
verified=true
bitwise_parity=false
comparison.strictness=stripped
strip_lines=true
exact_remainder=false
```

Only the otherwise-identical run with `--verify-strict` reported canonical
`bitwise_parity=true`. User-facing L2 claims and examples must include
`--verify-strict` and, where automated, key on `bitwise_parity` rather than the
success banner or `verified` field.

### P0: KVM hangs and escapes a normal timeout

`hermit --backend kvm run --strict -- /bin/echo hello` emitted nothing and did
not exit within 120 seconds. After GNU `timeout` terminated the direct Hermit
process, a child remained reparented to PID 1, running on CPU in the same owned
process group. It ignored SIGTERM and required an exact-process-group SIGKILL.
This host lacks `/dev/kvm`; the CLI should reject that prerequisite quickly,
not enter a silent unbounded run.

This is a real Detcore path, not a canned launcher:
`run.rs:1818-1885 -> lib.rs:1199 run_kvm -> lib.rs:1279 KvmBackend ->
lib.rs:1308 run_static_elf_with_tool::<Detcore>`.

Live [`issue #1830`](https://github.com/rrnewton/hermit/issues/1830) independently
confirms a 100% KVM outage even on a real-KVM host, bisected to `8b734510`, with
the fix in `rrnewton/reverie#396`. It also records the missing gating liveness
probe. Release 0.3 should require the pin bump plus an exact-head, bounded KVM
guest smoke that verifies child reaping on timeout/failure.

## Backend-selection UX

All measurements used the same default clean build.

| Selection | Result |
| --- | --- |
| ptrace | strict PASS; canonical L2 PASS |
| dbi | rc 1, clear `DBI support was not included in this build` |
| liteinst | rc 1, clear missing/staging diagnostic for `libreverie_liteinst.so` |
| sabre | rc 1, clear `SaBRe support was not included in this build` |
| e9patch | rc 1, clear `e9patch support was not included in this build` |
| kvm | silent hang; see blocker above |

The top-level help advertises all six variants as if compiled. Draft
[`PR #1722`](https://github.com/rrnewton/hermit/pull/1722) correctly annotates
feature-gated variants while preserving their specific runtime errors, but it
is still blocked and unlanded.

## Documentation and help findings

1. **Strict-mode contradiction (P1).** README says `--strict` is only a
   compatibility spelling and "does not enable a stronger mode". `run --help`
   says explicit strict additionally rejects unsupported syscalls immediately.
   The latter matches the fail-closed implementation policy.
2. **Wrong Reverie authority (P1).** README links
   `facebookexperimental/reverie`, while every current Cargo dependency pins
   `rrnewton/reverie@dd3c178e...`. The old URL returns HTTP 200 but points users
   to the wrong maintained source. Draft
   [`PR #1752`](https://github.com/rrnewton/hermit/pull/1752) fixes the link but
   is blocked and has no checks.
3. **Stale backend terminology (P1).** `docs/ARCHITECTURE.md` still groups SaBRe
   and DynamoRIO as one DBI prototype and says it is only a syscall interceptor,
   while current dispatch has dedicated Detcore-linked DBI and KVM paths.
   Reconcile the architecture status table with actual CLI/build availability
   and retain explicit assurance limitations.
4. **Stale root status document (P1).** `WHATS_WORKING.md` is anchored to
   `db09337`, includes session/load-specific historical prose, and repeatedly
   treats plain `--verify` as bitwise L2. Refresh it from a typed exact-head
   report or move it under a dated progress-report/archive name.
5. **Help density and polish (P2).** Top-level help is 60 lines, but `run -h`
   is still 332 lines and `run --help` is 478 lines, mixing public, deprecated,
   internal, and prototype flags. Literal defects include `content addressible`,
   `Analyze Pass and failing runs`, `determinstically`, `current current`, and
   `intercects`; the deterministic-I/O description says "by reassuring".
6. **Make help (P2).** `make help` is useful and lists focused backend targets,
   but always emits ANSI color escapes even when output is captured.

## Links and repository cleanliness

- Scanned 28 Markdown files, 86 inline links, and 27 local-path links under
  README/docs: zero missing local path targets.
- README's two external links both returned HTTP 200; the Reverie link is
  semantically stale rather than broken.
- Exact-head Docs CI passed (`31139740569`), but the workflow builds rustdoc and
  does not provide a comprehensive link/terminology freshness gate.
- Checkout remained clean at the identical HEAD/tree before and after all
  probes. All 22 tracked symlinks resolve.
- 1,103 files are tracked; none exceeds 2 MiB. One tracked binary archive,
  `detcore/tests/testdata/musl-1.2.1.tar.gz` (1,047,481 bytes), dates to the
  initial commit. It is deliberate test data, not new residue, but conflicts
  with the current no-archives policy and should either receive an explicit
  fixture exception or be replaced by a reproducible external/source input.

## Recommended release order

1. Fix KVM by landing the Reverie fix/pin bump and add bounded liveness + child
   reaping coverage.
2. Choose and apply the 0.3 version identity consistently.
3. Replace and test the release-package build/install recipe; lock the source
   install command.
4. Correct all L2/strict claims and land the backend-availability help marker.
5. Land the maintained-Reverie link and refresh/archive `WHATS_WORKING.md` plus
   the backend architecture table.
6. Clean help prose/density and decide the legacy archive-fixture exception.
