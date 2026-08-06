# Reverie portable vs privileged: the denominator

- **Task:** `reverie-portable-vs-privileged-split-audit` (re-dispatch, 2026-08-06).
- **Author:** impl agent, claude-opus-5. Local analysis only; no egress (external 403).
- **Reverie SHA analysed:** `025d37800d347c32711038bd0a3889e8e4774c2b` (the primary checkout;
  note it was **`behind 1`** vs its own `origin/main`, which could not be fetched).
- **Relationship to prior work:** this **extends**, and does not replace,
  `ai_docs/transient/reverie-portable-vs-privileged-split-audit_20260803.md` (2026-08-03).
  That audit established the CI structure, the runtime share, the AppArmor/userns EPERM
  inversion, and the sysctl-flip probe. **It never established M.** This document supplies
  the denominator and corrects two counting errors that follow from not having one.

> **Artifact-location correction.** The 2026-08-03 task note cites
> `ai_docs/reverie-portable-vs-privileged-split-audit_20260803.md`. That path no longer
> exists: commit `714fc84` ("docs: file dated situational ai_docs into ai_docs/transient")
> moved it to `ai_docs/transient/…`. Anyone following the note's path finds nothing and may
> conclude the work was never done.

---

## The number

```
M  = 991   tests, enumerated by cargo itself across 68 test binaries
     19    excluded from the GitHub-hosted job by ci.yml's --skip filters
N  = 972   portable  =  98.1% of M
```

**972 of reverie's 991 cargo-visible tests already run, green, on a GitHub-hosted
`ubuntu-latest` runner on every push.** That is not an estimate or a classification — it is
what the `regular` job does today. The privileged bucket is **19 tests, 1.9%**.

The interesting number is smaller still. Of the 972 portable tests, **4 are inert** on a
GitHub-hosted runner — they run, pass, and assert nothing:

```
genuinely requires self-hosted hardware today  =  4 tests of 991  (0.4%)
```

Those 4 are the real-PMU tests in `reverie-ptrace/src/perf.rs` (§4).

---

## Method (why these are counts, not estimates)

Test names come from **cargo's own enumeration**, not a grep for `#[test]`:

```
cargo test --workspace --all-features -- --list      # 991 ": test" lines, 68 binaries
```

run against a **CoW copy** of the primary at `/tmp/revaudit/reverie`, so the primary
checkout was never built in or written to. The exact excluded set was then computed by
applying ci.yml's filters to that enumeration programmatically, rather than by counting
lines in the YAML — which is where the previous count went wrong (§3).

Two host gaps had to be worked around, both non-invasively and both reusable findings:

| Gap | Symptom | Local workaround (host NOT mutated) |
|---|---|---|
| **`cmake` is not installed** | `reverie-dbi` and `reverie-sabre` build scripts abort: `failed to configure DynamoRIO: No such file or directory (os error 2)` — the missing-cmake ENOENT, not a DynamoRIO defect | `dnf download --resolve cmake` (internal repos are reachable despite the external 403) + `rpm2cpio | cpio` into `/tmp/cm`; `PATH=/tmp/cm/usr/bin:$PATH` |
| **`libunwind` is not installed** | linking `reverie-e9patch` fails: `unable to find library -lunwind-ptrace / -lunwind-generic / -lunwind` | same extraction into `/tmp/lu`; `RUSTFLAGS="-L /tmp/lu/usr/lib64"`, `PKG_CONFIG_PATH=/tmp/lu/usr/lib64/pkgconfig` |

With both in place the full `--all-features` workspace — DynamoRIO built from vendored
source included — compiles and enumerates locally in **22 s** warm.

---

## §1 The CI split, restated against M

`reverie/.github/workflows/ci.yml`, workflow "Rust":

| Job | Runner | What it runs | Tests reached |
|---|---|---|---|
| `regular` — *Regular tests (GitHub-hosted)* | `ubuntu-latest` | `cargo test --workspace --all-features -- --test-threads=1` **+ 17 `--skip` filters** | **972 of 991** |
| `hardware` — *Host-dependent tests* | `[self-hosted, Linux, X64, reverie]`, `timeout-minutes: 10` | the identical command with **no `--skip`**, plus `REVERIE_REQUIRE_KVM=1` | 991 of 991 |

The self-hosted job's *unique* contribution is therefore exactly: the 19 excluded tests,
plus real-PMU assertions, plus hard-failing (rather than skipping) when `/dev/kvm` is
unusable.

---

## §2 The excluded 19 — every one, with the privilege it needs

All 19 live in **one crate, `reverie-process`**, and all but one need the *same* thing.

| # | Test | Privilege actually required |
|---|---|---|
| 1 | `tests::uid_namespace` | unprivileged userns + `uid_map` |
| 2 | `tests::pid_namespace` | userns + PID ns |
| 3 | `container::tests::pid_namespace` | userns + PID ns |
| 4 | `tests::mount_proc` | userns + PID + mount ns |
| 5 | `tests::hostname` | UTS ns |
| 6 | `tests::domainname` | UTS ns |
| 7 | `tests::mount_devpts_basic` | userns + mount ns |
| 8 | `tests::mount_devpts_isolated` | userns + mount ns |
| 9 | `tests::mount_tmpfs` | userns + mount ns |
| 10 | `tests::mount_and_move_tmpfs` | userns + mount ns (`MS_MOVE`) |
| 11 | `tests::mount_bind` | userns + mount ns |
| 12 | `tests::mount_bind_readonly_rejects_writes` | userns + mount ns |
| 13 | `tests::local_networking_ping` | network + mount ns |
| 14 | `tests::local_networking_loopback_flags` | network + mount ns |
| 15 | `tests::local_networking_there_can_be_only_one` | network + mount ns |
| 16 | `tests::port_isolation` | network ns (self-skips if `nc` absent) |
| 17 | `container::tests::bind_to_low_port` | userns + network ns |
| 18 | `container::tests::pin_affinity_to_all_cores` | `sched_setaffinity` + enough real cores |
| 19 | `tests::seccomp_notify` | seccomp user-notify |

**The mechanism is one thing, not nineteen.** `Container::map_root()`
(`reverie-process/src/container.rs:402-405`) writes `/proc/self/{uid,gid}_map` and OR-sets
`Namespace::USER`; `namespace.rs:19-33` maps `Namespace::{USER,MOUNT,PID,NETWORK,UTS}` to the
corresponding `CLONE_NEW*` flags. So tests 1–17 all reduce to **"an unprivileged user
namespace must be creatable."** That is not root, not hardware, and not self-hosted — it is
one sysctl, as the 2026-08-03 probe established: Ubuntu 24.04 sets
`kernel.apparmor_restrict_unprivileged_userns=1`, and flipping it to `0` on `ubuntu-latest`
(passwordless root on a dedicated VM) made `cargo test -p reverie-process` report **88
passed / 0 failed**, versus 16 `EPERM (MapUid)` without the flip.

Tests 18 and 19 do not depend on userns at all and were measured to **pass unmodified** on
`ubuntu-latest` in that probe.

---

## §3 Correction: "17 skipped tests" was a count of patterns, not tests

`ci.yml` lines 47–63 hold **17 `--skip` patterns**. `cargo test --skip` without `--exact` is
a **substring** filter, so the number of tests removed is not the number of lines. Computed
against the real enumeration:

| Pattern | Tests it removes |
|---|---|
| `tests::mount_bind` | **2** — also catches `tests::mount_bind_readonly_rejects_writes` |
| `tests::pid_namespace` | **2** — also catches `container::tests::pid_namespace` |
| the other 15 patterns | 1 each |

**17 patterns → 19 tests.** The prior audit reported "17 skipped tests" in its structural
sections and "19" in its probe results without reconciling them; the reconciliation is that
both numbers are right about different things.

Bounding the blast radius, since a substring filter is exactly the kind of thing that
silently over-matches: **all 19 hits are in `reverie-process`. Zero tests in any other crate
are caught.** So the over-match is real but contained, and no unrelated coverage is being
silently disabled. That is a measured negative result, not an assumption — it comes from
applying the patterns to all 991 names.

---

## §4 Portable ≠ covered: the 4 tests that genuinely earn the hardware

A test that runs on a portable runner and *self-skips* is counted in the 972, but provides
no coverage there. Two gate families exist:

**PMU — genuinely self-hosted.** `reverie_ptrace::is_perf_supported()`
(`reverie-ptrace/src/perf.rs:729`) does a live `perf_event_open`; the `ret_without_perf!()`
macro (`perf.rs:736`) turns an unsupported PMU into an early `return`, i.e. a trivial pass.
Inside M, **4 distinct test functions** are so gated:

| Test (`reverie-ptrace/src/perf.rs`) |
|---|
| `trace_self` |
| `trace_other_thread` |
| `deliver_signal` |
| `rdpmc_read_agrees_with_syscall_read` |

On `ubuntu-latest`, `perf_event_paranoid=4` blocks user hardware events, so these 4 pass
vacuously there. **These are the only tests in the 991 that today require the self-hosted
runner for their assertions to mean anything.**

**KVM — unproven, and probably not self-hosted.** 13 test functions self-skip on `/dev/kvm`
(`reverie-kvm/tests/counter.rs` 3, `reverie-kvm/tests/strace.rs` 3,
`reverie-examples/tests/kvm_cli.rs` 7). `REVERIE_REQUIRE_KVM=1` — read only in
`reverie-examples` (`kvm_test_support.rs:39`, `tests/kvm_cli.rs:26`) — converts the skip into
a hard failure, which is why the self-hosted job is the one that can fail on missing KVM.
But **`/dev/kvm` is PRESENT on `ubuntu-latest`** (measured in the 2026-08-03 probe and
independently on this box's own probing of GitHub-hosted capabilities). Whether it is
*usable* there has still never been tested. **This remains the single highest-value open
probe**, and it is blocked here by egress.

Note the asymmetry worth keeping in view: `reverie_kvm`'s unit-test binary contributes
**166** of the 991 tests, and almost all of them are ordinary pure logic that never opens
`/dev/kvm`. "KVM crate" and "needs KVM hardware" are very different populations.

---

## §5 A coverage hole the denominator exposes: 59 tests owned by no package

The repository root `Cargo.toml` is `[workspace]`-only — **there is no root `[package]`**.
The root `reverie/tests/` directory therefore belongs to no cargo package, and
`cargo test --workspace --all-features` does not build or run it. Confirmed: **zero**
`Running` lines for any of those files in the enumeration.

That directory contains **59 `#[test]` functions across 20 files**:

| File | tests | | File | tests |
|---|---:|---|---|---:|
| `tests/basics.rs` | 13 | | `tests/stack.rs` | 3 |
| `tests/delay_signal.rs` | 8 | | `tests/busywait.rs` | 2 |
| `tests/vfork.rs` | 6 | | `tests/convert.rs` | 2 |
| `tests/timer_semantics.rs` | 4 | | `tests/cpuid.rs` | 2 |
| `tests/vdso.rs` | 4 | | `tests/rdtsc.rs` | 2 |
| | | | *(+10 more files)* | 13 |

**These run in neither CI job.** They also hold **15 of the 20 `ret_without_perf!()` call
sites in the repo** (14 in `timer_semantics.rs`, 1 in `busywait.rs`) — i.e. the majority of
reverie's PMU-sensitive test code is not exercised by either GitHub CI job at all.

Two caveats, stated so this is not over-read:

1. Meta's internal Buck setup runs integration tests that were never ported to the public
   cargo build, so these files are plausibly covered *there*. "Not in cargo CI" is the
   claim; "untested anywhere" is not.
2. This is adjacent to, but distinct from, the `--ignored` gap the prior audit found
   (`reverie-e9patch/tests/backend.rs`, `reverie-examples/tests/e9patch_direct.rs`,
   `reverie-dbi/tests/stats_provider_live.rs` — neither job passes `--ignored`). Those files
   *are* built; these are not.

---

## §6 What this means for the SPOF question

The task's framing was: *if most tests do not need privilege, the self-hosted SPOF stops
mattering for them.* The answer is that **this is already true and has been all along** —
98.1% of reverie's tests run GitHub-hosted today. The audit's value is therefore not
discovering a large portable bucket; it is showing how small the privileged residue really
is, and that most of it is not privilege at all:

| Bucket | Tests | Share of M | Genuinely needs scarce hardware? |
|---|---:|---:|---|
| Runs GitHub-hosted, real coverage | 955 | 96.4% | no |
| Runs GitHub-hosted, inert (PMU self-skip) | 4 | 0.4% | **yes — real PMU** |
| Runs GitHub-hosted, inert-or-not (KVM self-skip) | 13 | 1.3% | **unproven** — `/dev/kvm` present on `ubuntu-latest` |
| Excluded: needs creatable userns | 17 | 1.7% | **no** — one sysctl |
| Excluded: needlessly (measured to pass) | 2 | 0.2% | no |
| **M** | **991** | **100%** | |
| *(separately: owned by no package, run nowhere in CI)* | *59* | *—* | *—* |

Against the owner's three-part admission rule — **short**, **genuinely requires the
hardware**, **tightly timed out** — the `hardware` job passes (1) and (3) comfortably
(mean 122 s against a 10-minute box, per the 2026-08-03 measurements) but satisfies (2) for
only **4 tests, and possibly 17**.

### Recommendations, in value order

1. **Probe KVM on `ubuntu-latest`.** `/dev/kvm` is present; usability is untested. If usable,
   13 of the 17 remaining self-hosted-justified tests move, and the residue is 4 PMU tests.
   This is the one measurement that materially changes the picture. *Blocked on egress.*
2. **Add `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` to the `regular`
   job and delete all 17 `--skip` patterns.** Already tested end to end (88 passed / 0
   failed). This moves 19 tests onto GitHub-hosted and — more valuable — gives contributor
   PRs their coverage for the first time, since the `hardware` job only runs on push and
   rrnewton-originated events.
3. **Decide what owns `reverie/tests/`.** 59 tests, including most PMU-sensitive code, are
   invisible to both jobs. Either add a root package / make them a member's `tests/`, or
   document explicitly that they are Buck-only.
4. **Replace the `--skip` pattern list with `--exact`** if it survives at all. 17 patterns
   silently meaning 19 tests is the same proxy-vs-identity error this audit was warned
   about; `--exact` makes the count auditable.
5. **Persist reverie `validate.sh` durations.** Unchanged from 2026-08-03: it prints
   per-check wall-seconds and writes a fresh `mktemp` log per run, with no aggregate and no
   `--write-global`, so "has local validate bloated?" is unanswerable from history. The
   absence is the finding; feed it into `ci-hub/history` as hermit's `aggregate.py` does.

---

## Limitations

- **`M = 991` is the cargo-visible population at one SHA on one host.** `--list` reflects
  the features and `cfg` that resolved here; a different feature set or arch would give a
  different M. The `--all-features` flag matches what CI runs, which is why it was used.
- **Nothing here was executed on a real GitHub-hosted runner during this task** — egress is
  403. The portability claims for the 972 rest on the `regular` job being green in
  production; the userns and capability claims rest on the 2026-08-03 probes
  (runs `30840658519`, `30842156411`), which I did not re-run.
- **The 59-test figure counts `#[test]` attributes textually**, unlike M. Those files are not
  compiled by cargo, so cargo cannot enumerate them; a macro-generated test would be missed.
- **KVM usability on GitHub-hosted remains unproven,** and the recommendation ordering above
  depends on it. Do not treat "13 tests may be portable" as though it were measured.
- The reverie primary was `behind 1` commit versus its own `origin/main`, which could not be
  fetched to check whether that commit touches tests.

## Reproduction

```bash
# non-invasive host deps
mkdir -p /tmp/lu /tmp/cm
(cd /tmp/lu && dnf download libunwind libunwind-devel && for f in *.rpm; do rpm2cpio $f | cpio -idm; done)
(cd /tmp/cm && dnf download --resolve cmake       && for f in *.rpm; do rpm2cpio $f | cpio -idm; done)
sed -i 's|^prefix=/usr$|prefix=/tmp/lu/usr|; s|^libdir=/usr/lib64$|libdir=/tmp/lu/usr/lib64|; \
        s|^includedir=/usr/include$|includedir=/tmp/lu/usr/include|' /tmp/lu/usr/lib64/pkgconfig/*.pc

# never build in the primary
cp -a --reflink=auto ~/work/dev-hermit/reverie /tmp/revaudit/reverie
cd /tmp/revaudit/reverie
export PATH=/tmp/cm/usr/bin:$PATH RUSTFLAGS="-L /tmp/lu/usr/lib64" \
       LD_LIBRARY_PATH=/tmp/lu/usr/lib64 PKG_CONFIG_PATH=/tmp/lu/usr/lib64/pkgconfig CARGO_NET_OFFLINE=true
cargo test --workspace --all-features -- --list > /tmp/revaudit/list-all.log

grep -c ': test$' /tmp/revaudit/list-all.log        # -> 991  = M
grep -c '^     Running' /tmp/revaudit/list-all.log  # -> 69 lines / 68 binaries

# excluded set: apply ci.yml's 17 patterns as substrings to the 991 names -> 19 tests
# cargo-invisible tree: no root [package] in Cargo.toml; zero Running lines for tests/*.rs
```
