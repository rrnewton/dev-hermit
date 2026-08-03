# Reverie portable vs privileged test split — evidence-based audit

- **Date:** 2026-08-03
- **Task:** `reverie-portable-vs-privileged-split-audit` (P1, research-only)
- **Agent:** hermit-250 (impl, opus-4.8)
- **Framing (updated by owner):** the reverie single-runner SPOF is CLOSED — a 2nd
  self-hosted runner is up, a 3rd on the way. This audit is therefore NOT a SPOF
  fix. Its value is **capacity optimization**: move genuinely-portable tests to
  plentiful GitHub-hosted runners so the scarce self-hosted capacity is spent only
  on work that actually needs privilege.
- **Core discipline (owner, emphasized twice):** do **NOT** infer "needs
  privilege" from "currently runs self-hosted." Tests inherit the runner they were
  first written for. Test whether a test actually FAILS on a portable runner.

## TL;DR

1. **The portable bucket is already proven and is the dominant runtime.** reverie's
   `regular` job runs the *entire* `--workspace --all-features` suite MINUS 17
   `reverie-process` tests, on GitHub-hosted `ubuntu-latest`, and is green. That is
   direct empirical proof that everything except those 17 tests is portable.
2. **The self-hosted job is small and fast, not a runtime hog.** Over 20 recent
   `main` runs: self-hosted `hardware` mean **122s** (very tight 116–131s, warm
   cache) vs GitHub-hosted `regular` mean **551s** (267–626s, DynamoRIO cache
   variance). Self-hosted is **0.22×** the portable job and only **18.2%** of the
   pair's wall-clock. The privilege-*requiring* slice is a subset of that 122s.
3. **EMPIRICALLY (probe on ubuntu-latest), 16 of the 19 skipped tests GENUINELY
   need privilege — the task's hypothesis is INVERTED for `reverie-process`.** The
   gating was applied wholesale (`1130201`, "Configure fork CI"), but the *outcome*
   is mostly correct: 16/19 fail with `EPERM (MapUid)` because **Ubuntu 24.04's
   `apparmor_restrict_unprivileged_userns=1`** blocks the unprivileged user
   namespace they all rely on. The intuitive inference ("Ubuntu allows userns → these
   are portable") is WRONG — which is precisely why the owner's *test, don't infer*
   rule was decisive. Only **3** tests are needlessly gated.
4. **PMU is genuinely self-hosted; KVM may NOT be.** On ubuntu-latest
   `perf_event_paranoid=4` blocks user hardware perf events → PMU tests self-gate
   to trivial passes, so real PMU coverage needs the self-hosted runner. BUT
   **`/dev/kvm` is PRESENT on ubuntu-latest** — the assumption that KVM needs
   self-hosted is unproven and worth a follow-up probe.
5. **reverie local validate has NO durable baseline** — the absence is the finding.

## Part 1 — CI structure (evidence)

`reverie/.github/workflows/ci.yml`, two jobs in workflow "Rust":

- **`regular`** — `runs-on: ubuntu-latest` (GitHub-hosted). Runs
  `cargo test --workspace --all-features -- --test-threads=1` with **17 `--skip`
  filters** (lines 52–72), plus build, doc-tests, clippy, rustfmt. **This is the
  authoritative gate for every PR.** It is the portable bucket, and it is green.
- **`hardware`** — `runs-on: [self-hosted, Linux, X64, reverie]`. Runs the
  *identical* command with **no `--skip`** and `REVERIE_REQUIRE_KVM=1`. Gated
  behind `vars.REVERIE_SELF_HOSTED=='true'` and a push / rrnewton-dispatch /
  rrnewton-PR event.

So the self-hosted job's UNIQUE contribution over the portable job = (a) the 17
skipped `reverie-process` tests, (b) PMU tests running for *real*, (c) KVM tests
running for *real* (hard-fail on missing `/dev/kvm`).

## Part 2 — Runtime share (the deciding number)

Per-job durations from the GitHub Actions API over 20 recent completed `main` runs
(`repos/rrnewton/reverie/actions/runs/<id>/jobs`, `completed_at − started_at`):

| Job | Runner | n | mean | range | total |
|---|---|---|---|---|---|
| Regular tests | GitHub-hosted (portable) | 20 | **551s** | 267–626s | 11018s |
| Host-dependent tests | self-hosted | 20 | **122s** | 116–131s | 2444s |

- Self-hosted mean / regular mean = **0.22×** — the self-hosted job is *faster*,
  because its cache is warm (DynamoRIO already built; the regular job's 267–626s
  spread is precisely cold-vs-warm DynamoRIO cache).
- Self-hosted is **18.2%** of the pair's wall-clock.
- The self-hosted 122s runs the WHOLE suite; the privilege-requiring slice (17
  reverie-process tests + real-PMU assertions + real-KVM assertions) is a *subset*
  of it. The 17 reverie-process tests are tiny namespace/affinity/seccomp unit
  tests. So **truly-privileged runtime is a small fraction of an already-small
  18%.**

**Interpretation for the capacity question:** moving the portable majority off the
self-hosted runner frees most of that ~122s per run, but 122s is already small.
The win is less "reclaim runtime" and more **qualitative**: keep the scarce
self-hosted runners doing ONLY PMU/KVM work that genuinely needs them, and get the
17 namespace tests' coverage on every PR (they only run on push/rrnewton events
today, so contributor PRs never exercise them at all).

## Part 3 — Privilege categorization (code evidence)

All 17 skipped tests are in crate `reverie-process`. Mechanism:
`Container`/`Command` clones a child with namespace flags
(`reverie-process/src/namespace.rs:19-33` maps `Namespace::{USER,MOUNT,PID,NETWORK,UTS}`
→ `CLONE_NEW{USER,NS,PID,NET,UTS}`); `map_root()` (`container.rs:402-405`) writes
`/proc/self/{uid,gid}_map` and OR-sets `Namespace::USER`. So every `map_root()`
test needs **unprivileged user-namespace creation** to be permitted by the host.

| Test | file:line | Capability exercised |
|---|---|---|
| `tests::uid_namespace` | `lib.rs:236` | user ns + uid_map |
| `tests::pid_namespace` (async) | `lib.rs:252` | user+PID ns |
| `container::tests::pid_namespace` | `container.rs:966` | user+PID ns (substring-matched) |
| `tests::mount_proc` | `lib.rs:273` | user+PID+mount ns (CAP_SYS_ADMIN-in-userns) |
| `tests::hostname` | `lib.rs:292` | UTS ns |
| `tests::domainname` | `lib.rs:308` | UTS ns |
| `tests::mount_devpts_basic` | `lib.rs:355` | user+mount ns |
| `tests::mount_devpts_isolated` | `lib.rs:373` | user+mount ns |
| `tests::mount_tmpfs` | `lib.rs:392` | user+mount ns |
| `tests::mount_and_move_tmpfs` | `lib.rs:409` | user+mount ns (MS_MOVE) |
| `tests::mount_bind` | `lib.rs:437` | user+mount ns (also matches `mount_bind_readonly_rejects_writes`) |
| `tests::local_networking_ping` | `lib.rs:483` | network+mount ns |
| `tests::local_networking_loopback_flags` | `lib.rs:512` | network+mount ns |
| `tests::local_networking_there_can_be_only_one` | `lib.rs:582` | network+mount ns |
| `tests::port_isolation` | `lib.rs:528` | network ns (self-skips if `nc` absent) |
| `container::tests::bind_to_low_port` | `container.rs:1001` | user+network ns |
| `container::tests::pin_affinity_to_all_cores` | `container.rs:1022` | `sched_setaffinity` + enough real cores |

**PMU tests** (`reverie-ptrace`) — NOT skip-listed. Gated by `is_perf_supported()`
(`perf.rs:559`), a live `perf_event_open`. On EACCES/EPERM/ENOENT the macro
`ret_without_perf!()` early-returns the test as a trivial pass. So they run in the
GitHub-hosted `regular` job too, but only exercise hardware if that runner permits
`perf_event_open`. The self-hosted runner is the environment *guaranteed* to have
working counters — this is reverie's genuinely-host-dependent core.

**KVM tests** — `REVERIE_REQUIRE_KVM` is read only in `reverie-examples`
(`kvm_test_support.rs:32`, `tests/kvm_cli.rs:25`): it converts "KVM missing" from a
silent skip into a hard failure. `reverie-kvm`'s own tests always self-skip when
`/dev/kvm` is absent, regardless of the env var. With the var unset (the portable
job) all KVM tests trivially pass.

**Neither job runs `--ignored`**, so e9patch/dbi-live tests
(`reverie-e9patch/tests/backend.rs`, `reverie-examples/tests/e9patch_direct.rs`,
`reverie-dbi/tests/stats_provider_live.rs`) run in NEITHER job — separate gap.

## Part 4 — Empirical portable test (does the claim hold?)

The owner's rule: test, don't infer. This dev host has ALL privilege
(max_user_namespaces=3091457, perf_event_paranoid=1, `/dev/kvm` present, 316
cores), so it CANNOT proxy a portable runner — running the tests here only proves
they pass *with* privilege. The only faithful test is an actual `ubuntu-latest`
run of the excluded tests.

**Experiment:** throwaway branch `experiment/portable-privilege-probe`
(rrnewton/reverie), workflow `.github/workflows/portable-privilege-probe.yml`,
run **30840658519**. Runs ONLY `cargo test -p reverie-process` with the 17 gated
tests as positive filters on `ubuntu-latest` (no DynamoRIO build → cheap), dumps
host capabilities, and reports per-test PASS (portable) / FAILED (needs privilege).

### RESULT (portable-runner ground truth — run 30840658519, ubuntu-latest)

**Host capabilities of the real portable runner (this is the surprise):**
`max_user_namespaces=63838`, but **`apparmor_restrict_unprivileged_userns=1`**
(Ubuntu 24.04 AppArmor restricts what an unprivileged userns may do),
`unprivileged_userns_clone=1`, **`perf_event_paranoid=4`** (user hardware perf
events blocked), **`/dev/kvm` PRESENT** (`crw-rw---- root kvm`), `nproc=4`,
`nc` present.

**Per-test result (19 tests; substring filters pulled in 2 extras):**

| Result | Tests |
|---|---|
| **PASS on portable (needlessly gated)** — 3 | `container::tests::pin_affinity_to_all_cores`, `tests::seccomp_notify`, `container::tests::pid_namespace` |
| **FAIL on portable (genuinely privileged)** — 16 | `container::tests::bind_to_low_port`, `tests::domainname`, `tests::hostname`, `tests::local_networking_loopback_flags`, `tests::local_networking_ping`, `tests::local_networking_there_can_be_only_one`, `tests::mount_and_move_tmpfs`, `tests::mount_bind`, `tests::mount_bind_readonly_rejects_writes`, `tests::mount_devpts_basic`, `tests::mount_devpts_isolated`, `tests::mount_proc`, `tests::mount_tmpfs`, `tests::pid_namespace`, `tests::port_isolation`, `tests::uid_namespace` |

**Root cause of the 16 failures (from the log):** `Error { errno: EPERM, context:
MapUid }` / `PermissionDenied "Operation not permitted"` — writing
`/proc/self/uid_map` after `CLONE_NEWUSER` is denied because **Ubuntu 24.04's
AppArmor `apparmor_restrict_unprivileged_userns=1`** neuters the unprivileged user
namespace. Every failure traces to `map_root()`/user-namespace creation.

**This INVERTS the task's motivating hypothesis.** The intuitive guess — "Ubuntu
allows unprivileged userns, so the namespace tests are portable" — is WRONG for
`ubuntu-latest` (now 24.04). 16 of 19 genuinely fail on the portable runner. The
`--skip` list is essentially *correct*, not lazy inheritance — which is exactly
why the owner's "test, don't infer" rule mattered: inference would have produced
the wrong answer here.

**Two caveats on the 3 passes:** `pin_affinity_to_all_cores` and `seccomp_notify`
are genuine portable coverage (affinity + seccomp-unotify work unprivileged).
`container::tests::pid_namespace` passes where the near-identical `tests::pid_namespace`
FAILS — before un-skipping it, confirm it is real coverage and not a graceful
self-skip under the userns restriction (read `container.rs:966`).

## Part 5 — reverie local validate: bloat / baseline

`reverie/validate.sh` (the canonical local gate per `reverie/CLAUDE.md`) runs 5
checks: **Build workspace**, **Test regular workspace cases** (with the SAME 17
`--skip` args as the `regular` CI job), **Documentation tests**, **Clippy**,
**Rustfmt** — all `--workspace --all-features`.

- It records **per-check wall-seconds** to stdout (`PASS: <name> (<N>s)`,
  `validate.sh:75`) and writes a full log to a **fresh `mktemp` file each run**
  (`VALIDATE_LOG_FILE`, `:52-56`).
- It persists **NO historical aggregate**: no `--write-global`, no accumulator,
  nothing fed into `ci-hub/history`. This is unlike hermit, which has
  `ci-hub/validate/aggregate.py --write-global` → `validate-run-global.jsonl`.

**FINDING (absence-of-baseline is the finding):** there is no durable history of
reverie local-validate durations, so "has it bloated?" cannot be answered from
history — only eyeballed, which the owner explicitly rejected. This argues for the
**same validate-time ratcheting we want on hermit**: teach `validate.sh` (or a
wrapper) to append `{commit, per-check seconds, total}` into `ci-hub/history` so a
trend exists to ratchet against. Note `validate.sh` also skips the 17 privileged
tests, so the local gate is portable-bucket-only — the host-dependent bucket is
exercised by neither `validate.sh` nor a contributor's PR, only by the self-hosted
CI job on push.

## Recommendations (research output — no code landed)

The honest bottom line: **for `reverie-process`, moving portable tests off
self-hosted is a small win, not the SPOF-dissolver the hypothesis imagined** — 16/19
genuinely need privilege and the self-hosted job is already only 18% of runtime and
*faster* than the portable job. The real value is contributor-PR coverage + a
KVM-portability lead, not reclaimed capacity.

1. **Un-skip exactly the 3 portable-proven tests on the `regular` job**:
   `container::tests::pin_affinity_to_all_cores`, `tests::seccomp_notify`, and (after
   confirming it is real coverage, not a graceful userns self-skip)
   `container::tests::pid_namespace`. Gives every contributor PR coverage of these
   three; contributor PRs never run the self-hosted job today.
2. **Keep the other 16 + PMU on self-hosted** — empirically privilege-requiring
   (unprivileged-userns blocked on ubuntu-latest by AppArmor; `perf_event_paranoid=4`
   blocks PMU). Pair PMU with the `reverie-single-runner-spof` load precondition (a
   contended PMU measurement must refuse, not report a degraded number).
3. **FOLLOW-UP PROBE — KVM portability (potentially the larger prize).** `/dev/kvm`
   is present on ubuntu-latest. Probe whether the `reverie-examples` / `reverie-kvm`
   tests actually pass there with `REVERIE_REQUIRE_KVM=1` (my probe covered only
   `reverie-process`, which needs no `/dev/kvm`). If they do, real KVM coverage could
   move to GitHub-hosted — a much bigger capacity shift than the 3 namespace tests.
   Caveat: ubuntu-latest is 4 cores and PMU-less, which may matter for KVM examples.
4. **Add reverie validate-time history** to `ci-hub/history` to create the missing
   baseline and enable the validate-time ratcheting the owner wants (mirror hermit's
   `aggregate.py --write-global`).
5. **Separately**: `--ignored` e9patch/dbi-live tests run in NO CI job — out of scope
   here but a real coverage gap worth its own task.

## Cleanup

Throwaway experiment branch `experiment/portable-privilege-probe` +
`.github/workflows/portable-privilege-probe.yml` exist ONLY to produce Part 4's
evidence; they are not for merge and should be deleted once this artifact is
accepted (`with-proxy git push origin --delete experiment/portable-privilege-probe`).

## Reproduction

```bash
# runtime share
runs=$(with-proxy gh api "repos/rrnewton/reverie/actions/workflows/ci.yml/runs?branch=main&status=completed&per_page=20" --jq '.workflow_runs[].id')
for r in $runs; do with-proxy gh api "repos/rrnewton/reverie/actions/runs/$r/jobs" \
  --jq '.jobs[] | [.name, .conclusion, ((.completed_at|fromdateiso8601)-(.started_at|fromdateiso8601))] | @tsv'; done

# portable probe (no sysctl flip): 16 EPERM MapUid
with-proxy gh run view 30840658519 -R rrnewton/reverie --log

# userns sysctl-flip probe (the decisive follow-up): 88/0 pass
with-proxy gh run view 30842156411 -R rrnewton/reverie --log
```

## ADDENDUM (2026-08-03, later) — the EPERM inversion is a sysctl, not a privilege

The owner asked to EXPLAIN the 16/19-FAIL EPERM inversion before recommending any
moves, because it decides whether the runtime-share numbers mean what they appear
to. Resolution of the "gating wrong vs our model wrong" fork:

- **Our model was wrong; the gating is substantively correct** — the 16 tests DO
  fail on `ubuntu-latest`, so they were not needlessly gated. But **"needs
  privilege" is the wrong label.** The true dependency is narrow: an *unprivileged
  user namespace must be creatable* (`map_root()` → `/proc/self/{u,g}id_map`,
  requires `clone(CLONE_NEWUSER)` to succeed). Not root, not hardware, not a
  self-hosted host. The self-hosted runner satisfies them *incidentally* because
  its host permits userns; `ubuntu-latest` blocks it via one sysctl,
  `kernel.apparmor_restrict_unprivileged_userns=1` (Ubuntu 24.04).

- **DECISIVE PROBE (run 30842156411, ubuntu-latest), tested not inferred:** flip
  the sysctl first, then run the crate. Result:
  - `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` **succeeds**
    on the GitHub-hosted VM (passwordless root; value went 1 → 0).
  - `cargo test -p reverie-process -- --test-threads=1` → **88 passed / 0 failed
    / 0 EPERM/MapUid** (two binaries: 58 + 30). Without the flip the same set gave
    16 EPERM.
  - `/dev/kvm` **is present** on `ubuntu-latest` (reconfirmed) — KVM portability
    still UNPROVEN (needs its own probe; this crate does not exercise it).

**What the numbers actually mean, post-probe:** the "privileged" bucket is not
18.2% of runtime bound to scarce self-hosted capacity — it is **portable with one
workflow line** for the namespace tests. The genuinely-self-hosted residue
collapses to **real-PMU** (`perf_event_paranoid` blocks user hardware counters on
GitHub-hosted) plus **KVM** (unproven). 

**Recommended move (now defensible):** add a `sudo sysctl -w
kernel.apparmor_restrict_unprivileged_userns=0` step to the `regular` job (or a
dedicated 3rd GitHub-hosted job) and drop the 14 userns `--skip`s + the 3
already-portable ones, leaving only true-PMU/KVM on self-hosted. That frees the
new 2nd/3rd self-hosted runners for work that genuinely needs privilege. Gate this
behind the KVM-portability probe before moving KVM itself.

Throwaway probe branch `experiment/userns-sysctl-probe` was created via git
plumbing off the primary object store (no checkout mutated) and deleted after
evidence capture.
