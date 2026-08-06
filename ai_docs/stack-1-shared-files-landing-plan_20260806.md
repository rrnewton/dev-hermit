# Stack 1 — landing plan for the seven PRs over five shared source files

**Task:** `assemble-stack-1-seven-prs-five-shared-files`
**Authors:** `hermit-verify` (assembly, 2026-08-06 morning), `hermit-w4` (attribution, completion, this plan)
**Status:** stack assembled, completed, and pushed; **NOT landable.** Two independent blockers:

1. **§6A — a real, silent regression in PR #1710**: RDRAND determinization breaks the `liteinst` backend
   (`rc=1`, no output at all). Bisected to one commit, mechanism confirmed by toggling one flag. **This
   would have shipped.** It is the thing this stack was worth building for.
2. **§6 — a pre-existing red on `main`** that the stack neither causes nor can fix, and which also acts as
   a blindfold: it makes the portable lane exit after 15 of 47 steps, which is exactly why nobody saw §6A.

Read both before doing anything with this branch.

---

## 1. The question this stack answers

Seven open PRs touch a small set of shared files. A validate record is keyed to a SHA, so **every rebase
invalidates it**. Landing the seven serially forces ~7 rebases and destroys ~7 validate records — each
landing invalidates the evidence of everything still queued behind it. Assembled into one stack it is
**one rebase and one validate**.

This is the only place where serial draining is actively harmful. Stack 2 is a two-PR convenience and the
singletons need no stacking; **do not stack the rest** — elsewhere stacking buys nothing and costs review
friction.

## 2. The seven, and the files they share

All seven have merge-base **exactly** the stack base — none is stale-base.

| PR | branch | head | files |
|---|---|---|---|
| #1693 | `pin-check-via-herdr-run` | `0aface401e38fefc8bc784721d51f6b58b775e5d` | 1 |
| #1704 | `fixture-process-tree-ordering` | `c25549b5e034deb2e9cfb558ec99f8cd2215450c` | 3 |
| #1708 | `fixture-signal-inheritance` | `dd9d8569c5b6fdd74516868433776c3e4a64a751` | 3 |
| #1677 | `fail-closed-by-default` | `4a744f168e93110efa73824aa4df8c3b0c0147d4` | 3 |
| #1673 | `hermit-oci-podman-store-subcommand` | `a50f5eb1c917826dd1deab55e89775a8f7f33456` | 6 |
| #1710 | `fixture-randomness-source-identity` | `616f468391b101561e6fb619a1de8d17e4fc9924` | 20 |
| #1728 | `fixture/shm-coherency-identity` | `6c9495a880eecf0dc28cf7e3067913be0c34d360` | 4 |

### 2.1 Conflict map

Twenty-nine files are touched in total; **21 are singletons** (exactly one PR) and cannot conflict. The
eight shared files split into two layers, and the layers behave differently:

**Five shared SOURCE files** — the ones the task title names, and the reason the stack exists:

| shared source file | PRs | overlap kind |
|---|---|---|
| `scripts/check-reverie-pin.rs` | #1693, #1704, #1708 | **duplicate** — see §4.1 |
| `detcore/tests/misc/mod.rs` | #1704, #1708, #1710 | additive (`mod` registrations) |
| `hermit-cli/src/bin/hermit/run.rs` | #1673, #1677, #1710 | additive (separate CLI flags) |
| `detcore-model/src/config.rs` | #1677, #1710 | additive **+ semantic** — see §4.4 |
| `hermit-cli/src/metadata.rs` | #1677, #1710 | additive |

**Three shared MANIFEST/DERIVED files** — only #1710 and #1728, and these must settle once, last:

| shared derived file | PRs | overlap kind |
|---|---|---|
| `tests/e2e/manifests/system-utils.toml` | #1710, #1728 | additive, 5 interleaved hunks — see §4.3 |
| `tests/e2e/manifests/inventory/test-files.json` | #1710, #1728 | derived (regenerate, never hand-merge) |
| `ci/expected-e2e-plan.json` | #1710, #1728 | derived (regenerate, never hand-merge) |

## 3. The stack

```
base   4c70658e785834737cbe1524f77330c781a6f5ea   (rrnewton/hermit main, re-read at the remote)
branch stack/fixtures-shared-files
head   14a2ecee3c43c07450d3959da7920119e3123252   (20 commits)
```

**Order is by FILE DEPENDENCY, not PR number.** Rule: *leaves first, hub last, derived-only last.*

| rung | PR | commits | why here |
|---|---|---|---|
| 1 | #1693 | `39c83f840` | one file, `check-reverie-pin.rs` — the file #1704/#1708 also carry |
| 2 | #1704 | `cb4f35e94` | registers a module in `detcore/tests/misc/mod.rs` |
| 3 | #1708 | `6e84337ad` | registers another module in the same `mod.rs` |
| 4 | #1677 | `363ac9638` | `config.rs` + `run.rs` + `metadata.rs` |
| 5 | #1673 | `1c26f4696` | `run.rs` + the OCI leaves |
| 6 | #1710 | `7053fd350 ab4f09ef5 66181aece 82f01c281 05127b0ae 75703c1df 35738b069 97b4cbb4a` **+ `771056c87 14a2ecee3`** | **HUB** — touches 20 files including every shared source file and both derived inventories |
| 7 | #1728 | `f6d28e9fe` | derived-only + `system-utils.toml`, last so the inventories settle exactly once |
| — | integration | `e3ebc31fe 9a7abf385 031941dc3 c1f03f025` | see §4 — changes that exist only because the seven are together |

Rungs 1–3 all touch `check-reverie-pin.rs`; 4–6 all touch `run.rs`; 6–7 both touch the derived inventories.
Ordering leaves-before-hub means each shared file is written by an increasingly wide change, never the
reverse, so no rung has to undo a later rung's work.

**Containment is verified, not asserted.** Every path touched by all seven PRs is present in the stack
diff; 21 of them are byte-identical to their PR head and the rest are supersets produced by the merges in
§4:

```
#1693: 1 byte-identical, 0 merged   #1704: 1 identical, 2 merged   #1708: 1 identical, 2 merged
#1677: 0 identical, 3 merged        #1673: 5 identical, 1 merged   #1710: 12 identical, 8 merged
#1728: 1 identical, 3 merged        MISSING: none
```

## 4. What the assembly bought — six problems serial landing would have hit one at a time

Each of these would have surfaced *after* a rebase that had already destroyed the previous PR's record.
§4.1–4.4 and §4.6 were found by `hermit-verify`; §4.5 by `hermit-w4`.

### 4.1 Duplicate commit (#1704 vs #1693)
#1704 carries the **same commit** as #1693 — `0aface401`, the `check-reverie-pin` fix. **Three** PRs had
independently copied that fix. The cherry-pick went empty and was dropped with `--empty=drop`. Serially,
the second and third would each have produced a confusing empty-or-conflicting rebase.

### 4.2 Additive conflict in `detcore/tests/misc/mod.rs`
#1704 and #1708 each add a `mod` line. Resolved as a **sorted union**, stable order.

### 4.3 Additive conflict in `tests/e2e/manifests/system-utils.toml` (5 interleaved hunks)
Resolved **structurally, not textually**: `[[test]]` blocks unioned by `id`, then asserted the TOML parses,
22 tests, no duplicate ids, both new entries present. Hand-merging interleaved TOML is exactly how a
manifest silently loses a case.

### 4.4 Semantic break visible ONLY in the stack (#1677 × #1710)
#1677 adds `Config::no_panic_on_unsupported_syscalls`; #1710 adds explicit `Config` literals in
`detcore-testutils`. **Neither is broken alone.** Together: `E0063` missing field at 3 sites. Set `false`
(== `Default` == fail-closed): the flag is a *relaxation*, so even `TOP_CFG` should want `false` rather
than silently opting out of #1677's intent.

### 4.5 Two commits of #1710 were missing from the stack — recovered
The assembled stack carried #1710 only through `4196f5254`, while the live PR head is `616f46839`, **two
commits ahead**:

- `3125d697a` *Make flock(2) actually exclude, instead of a no-op success* — a real product change:
  `handle_flock` returned success unconditionally, so two guest processes could hold the same `LOCK_EX`
  at once. It also corrects the refuted justification comment in `detcore/src/syscall_classification.rs`.
- `616f46839` *Assert flock exclusion in the lock fixture now that flock is real* — flips
  `file-lock-ordering.c` from REPORTED to ASSERTED, which is only correct **with** `3125d697a`.

Detection: `detcore/src/syscall_classification.rs` appeared in #1710's diff but **not** in the stack's
diff. The two commits' committer timestamps (`09:44:16`, `09:44:25`) precede the stack's cherry-pick of
their parent (`09:45:46`) by ~90 seconds, so this reads as a same-minute race with the PR author rather
than a hunk dropped during conflict resolution. Either way the stack was two commits short of the live
PR and would have landed a *fixture asserting a contract whose implementation was absent* — or rather,
would have landed neither, silently dropping a product fix. Both were cherry-picked onto the stack head
with **zero conflicts** (`771056c87`, `14a2ecee3`).

They are **appended above** the integration commits rather than inserted into the #1710 rung on purpose:
appending is a fast-forward that disturbs none of the already-resolved merges in §4.1–4.4, and neither
commit touches a derived inventory, so the "derived-only last" rule is not violated.

### 4.6 Two defects in the PRs themselves, surfaced by the assembled build
- **#1708** casts a function item straight to `libc::sighandler_t` at 2 sites; the workspace build
  **denies** it (*direct cast of function item into an integer*). **This would fail that PR's own
  validate.** Fixed as the compiler suggests (`as *const () as ...`). Note: a plain
  `cargo clippy --workspace --all-targets` reported 0 errors — the DAG's workspace build enables features
  a plain check does not, so the cheap gate was **not** a proxy for the real one.
- **#1704**'s `process_tree_ordering.rs` arrives unformatted; `rustfmt` failed on it and on `run.rs` where
  several PRs' edits collide. Fixed; scope verified as exactly those two files.

## 5. Validation at the exact head

See §5.1 for the record. Rebuild and format gates at `14a2ecee3`:

```
cargo fmt --all -- --check            FMT_OK
cargo build --workspace --all-targets exit 0    (boxed: systemd-run --user w4-stackbuild)
```

### 5.1 Full validate record

Launched through `ci-hub validate-run` (the sole admission point; an agent sandbox cannot run `validate.sh`
directly).

```
target  14a2ecee3c43c07450d3959da7920119e3123252     <-- the record is keyed to THIS SHA
unit    validate-hermit-w4-14a2ecee3c43-1786049059.service
log     ignored/validate/validate-hermit-w4-14a2ecee3c43-1786049059.log
profile full, clean tree, commit-anchored, selection full
result  6 passed, 1 failed | wall 3m03s CPU 9m28s CPU/wall 3.1x across 316 cores
PASS    reverie pin, submodules, pin consistency, manifest+inventory (x2), PRIVILEGED CI DAG lane
FAIL    portable CI DAG lane -> e2e.manifest_determinism_stress   (see §6 — pre-existing)
```

CPU/wall 3.1x confirms a real run, not a 1.0x no-op. Adding the two recovered #1710 commits (§4.5) changed
the verdict shape not at all: same node, same reason string.

### 5.2 "6 passed" is a gate count, not coverage — the real number is 15 of 47

**The portable lane exits eagerly on first failure.** Counted from `ci/dag/portable.json` (47 steps)
against the run log:

| | count |
|---|---|
| portable DAG steps | 47 |
| started | 26 |
| **passed** | **15** |
| never started | 21 |
| started but killed mid-flight | 11 |

Among the never-started are **exactly the two nodes that exercise this stack's own new content** —
`e2e.manifest_system_utils` and `test.detcore_misc`. So the full-validate receipt, red on an unrelated
pre-existing node, says almost nothing about the seven. Reading "6 passed, 1 failed" as "nearly green" is
wrong twice over: one failing node is a failing validate, and the passing six did not cover the change
under review.

### 5.3 Direct coverage of the stack's own content

The two skipped nodes were therefore run directly, boxed, at `14a2ecee3`:

**`test.detcore_misc`** (exact DAG command) — **35 passed, 0 failed, 5 filtered out**, nonzero execution.
The suite lists 40 tests and contains all of the stack's new ones:

- #1704 → `process_tree_ordering::` × 5 (fork order/pid assignment, exec identity, pipeline reaping,
  reap-order stability, vfork budget)
- #1708 → `signal_inheritance::` × 6 (blocked mask, dispositions, sigaltstack, no pending signals, exec
  reset-but-keep-ignore, exec-preserved mask)
- #1710 → `randomness_sources_are_determinized`

(The 5 filtered are the DAG's own deliberate `--skip` set for host-sensitive cases, including
`rdrand_rdseed_is_masked` and `has_rdrand_without_detcore`.)

**`e2e.manifest_system_utils`** — every cell belonging to the stack passes:

| cell | backend | result |
|---|---|---|
| `system-utils/vdso-getrandom` (#1710) | ptrace, dbi | **PASS**, both |
| `system-utils/file-lock-ordering` (#1710, now ASSERTED per §4.5) | ptrace, dbi | **PASS**, both |
| `system-utils/shm-coherency-identity` (#1728) | ptrace | **PASS** |

`file-lock-ordering` passing under the *asserted* contract is the direct positive evidence that the
recovered `3125d697a` flock fix works — the assertion it enables would fail without it.

Three cells in that bucket did fail, **all on the `liteinst` backend and all on pre-existing tests the
stack does not touch** (`clock-determinism` custom + verify, `record-getpid` verify). See §6.3 for their
attribution.

## 6. THE BLOCKER — a pre-existing main red, measured, not assumed

The full validate at the stack head fails **one** node:

```
portable lane -> e2e.manifest_determinism_stress
  FAIL portable chaos ptrace determinism-stress/order-violation
       chaos distinct=1 passes=2 failures=0 repeat_mismatches=0
```

**It is pre-existing at the base. The stack did not introduce it.**

Method — the same bucket, boxed, at both ends:

| where | SHA | verdict |
|---|---|---|
| stack head | `c1f03f02572364e6ca7652c7c7f9b9eedb04fcf7` | `distinct=1 passes=2 failures=0 repeat_mismatches=0` |
| stack head, rerun solo | `c1f03f02572364e6ca7652c7c7f9b9eedb04fcf7` | identical |
| **base, zero stack commits** | `4c70658e785834737cbe1524f77330c781a6f5ea` | **identical** |

Three reproductions, byte-identical verdict, so it is **deterministic, not flaky**, and not attributable
to any of the seven. (The base was measured by detaching the slot at `4c70658e7`, rebuilding
`cargo build --workspace` — 26 s wall / 1 m34 s CPU, exit 0 — and rerunning the bucket: 16.7 s wall.)

### 6.1 What the oracle actually asserts

Read `ci/test_harness.sh:1720-1747` before calling this a "stress flake". `determinism-stress/order-violation`
declares `modes.chaos.seeds = [0, 9]` and `assert { min_distinct: 2, min_passes: 1, min_failures: 1 }`.
The harness runs each seed twice and requires:

1. each seed reproduces (`repeat_mismatches == 0`) — **it does**;
2. the two seeds produce **different** outcomes, at least one a failing guest run.

Observed `distinct=1 failures=0`: both seeds produced the same passing schedule. So chaos is perfectly
reproducible and the guest is perfectly deterministic; what is broken is **schedule diversity** — chaos
mode is no longer exposing the race. Two of the three assert legs are unmet; the printed reason names
only `distinct`.

### 6.2 Why this blocks the stack, and why re-running will not help

- `ci-hub validate-status 4c70658e7` → **NOT-VALIDATED**, 0 records.
- `ci-hub newest-green` → `d53550510d1e7d13e84cc8af9bb90269e90b3f07` (2026-08-05), **27 commits behind**
  the stack's base.
- Since the **base itself** fails this node, **no branch based on `4c70658e7` can produce a qualifying
  full-profile green on this host** — stack or singleton, and equally for the seven landed serially.

`ci-hub validate-status` classifies the stack-head red as **NEEDS-RERUN** ("known-flaky/contended, rerun
solo at -j 4"). **That classification is wrong for this evidence.** Three identical reproductions, one of
them at the base with none of the stack's commits, say the node is neither flaky nor contended. Re-running
validate at the stack head will reproduce the same red indefinitely.

The regression entered `main` somewhere in `d53550510..4c70658e7`. That is main-health work, owned outside
this task; bisecting it is the unblocking action for the whole drain, not just for these seven.

## 6A. A REAL REGRESSION IN #1710 — `liteinst` is broken by RDRAND determinization, and it fails SILENTLY

This is the most important finding on this page and it is **not** the §6 pre-existing red. It is a genuine
product regression carried by PR #1710, isolated to one commit, and **it would have shipped.**

### 6A.1 Symptom

Running `e2e.manifest_system_utils` directly (the node the eager exit skipped — §5.2) produced three
failures, all on the `liteinst` backend, all on tests the stack does not touch:

```
FAIL portable custom  liteinst system-utils/clock-determinism - custom runs=5 failed_runs=5 distinct=1
FAIL portable verify  liteinst system-utils/clock-determinism - verify exited with status 1
FAIL portable verify  liteinst system-utils/record-getpid     - verify exited with status 1
```

Those same three cells **PASS** in the last full green (2026-08-04) and in two other full runs from
2026-08-06 (11:00, 11:10), so they are not globally broken.

The first hypothesis — missing DAG prerequisites, since `build.runtime_release` was killed mid-flight and
`build.liteinst_runtime_release` never started — was **tested and REFUTED**: after building the release
runtime and staging `target/ci/hermit-strict`, the same three cells failed identically.

### 6A.2 Bisected to one commit

Reduced to a single command and bisected over the stack, rebuilding **only the binary** each step
(`cargo build -p hermit --bin hermit`; a `--workspace` build fails at intermediate commits on the §4.4
`E0063`, which will silently leave a stale binary and corrupt a naive bisect — this bit once):

| commit | what it is | `hermit run --backend=liteinst --strict` |
|---|---|---|
| `4c70658e7` | the base | **rc=0**, 340 bytes out |
| `1c26f4696` | after #1673, before any #1710 commit | **rc=0**, 340 bytes out |
| **`7053fd350`** | **#1710's first commit** (`bf8d8951e`, *Determinize RDRAND/RDSEED instead of only masking the CPUID bit*) | **rc=1, ZERO bytes out** |

### 6A.3 Mechanism — confirmed by bracketing both sides

At the stack head `14a2ecee3`, same guest, same flags, toggling only the new switch:

| backend | determinization ON | determinization OFF (`--no-determinize-rdrand`) |
|---|---|---|
| `ptrace` | rc=0, 339 B | rc=0, 339 B |
| **`liteinst`** | **rc=1, 0 B** | **rc=0, 340 B** |

The negative and the positive both fire, on the one flag, so the binding is observed rather than inferred.

RDRAND determinization **rewrites the guest's text in place**. #1710's own commit `e01ccfdda` already
diagnoses exactly this hazard and fences the feature off — **but only inside `run_dbi`**:

> *"A translating backend keeps its own copy of the instruction stream in a code cache, so patching the
> original text in place makes the patch and DR's translation two writers of the same instructions."*

LiteInst is also a patching backend. It needed the same fence and did not get one. `sabre` and `dbi`
cells pass under the harness at the stack head (`record-getpid` verify sabre PASS, `vdso-getrandom` and
`file-lock-ordering` verify dbi PASS), so `liteinst` is the lone casualty among the backends this bucket
exercises.

### 6A.4 The failure is silent, which is worse than the DBI case

`rc=1`, **zero stdout, zero stderr, nothing at `--log=warn`**. The DBI variant at least SIGSEGVs with a
named crash. This one produces no diagnostic at all — a guest simply does not run. #1710's own commit
message argues that a withdrawn determinism guarantee "must be visible or the fence is itself the
concealment pattern this work set out to remove"; by that standard this silent liteinst abort is the
same defect one backend over.

### 6A.5 Why nobody saw it

On a healthy `main`, #1710's own validate would have reached `e2e.manifest_system_utils` and caught it.
It is invisible **right now** only because the §6 pre-existing red makes the portable lane exit before
that node — the same eager exit that hides 32 of 47 steps. **The two findings compound: §6 is not just a
blocked landing, it is a blindfold over every PR validated against this main.**

### 6A.6 What must happen

Not fixed here. Choosing which backends may rewrite guest text is a product decision belonging to #1710,
not a mechanical integration fix like §4.6, and the correct scope of the fence (liteinst only? e9patch?
any in-guest-patching path?) is a real design question. **#1710 must not land until this is resolved.**
Note also that `test.liteinst_strict` is one of the 21 never-started nodes (§5.2), so the blast radius
beyond these three cells is **unmeasured**.

## 7. Landing plan

**Order of operations. No merge path outside `land-pr.sh`. No `--admin`, no force** — concurrent
`--rebase --admin` on stale bases previously rewound `main` and orphaned ~12 merged PRs.

0. **Resolve the `liteinst` regression in #1710 (§6A).** It is a product defect, it fails silently, and it
   is carried by a PR in this stack. Either fence RDRAND determinization off `liteinst` the way
   `e01ccfdda` fenced it off DBI, or establish why liteinst should be exempt. Nothing else in this plan
   matters until #1710 is safe to land.
1. **Unblock main next.** Bisect `determinism-stress/order-violation`'s chaos oracle over
   `d53550510..4c70658e7` and fix or quarantine it *on main*. Until this is done, step 3 cannot produce a
   qualifying receipt for anything, and no amount of re-validating the stack changes that. It is also the
   blindfold that hid §6A: clearing it restores coverage of 32 otherwise-unreached portable steps.
2. **Re-read the frontier.** `main` was `4c70658e7` at the time of writing. If it has moved, rebase
   `stack/fixtures-shared-files` onto the new tip **once** — that is the whole point of the stack — and
   re-check that all seven PR heads are still unmerged and unchanged (they were, at every check in §2).
3. **One validate at the final head**, through `ci-hub validate-run` (admission path; an agent sandbox
   cannot run `validate.sh` directly). Record the SHA the receipt is keyed to. **A record that predates
   the final rebase is not a record.**
4. **Land via `land-pr.sh`** against `rrnewton/hermit:main`.
5. **Close out the seven.** They are one squashed change on `main`; each of #1693 #1704 #1708 #1677 #1673
   #1710 #1728 is then closed against the landed commit, not against this branch.

### 7.1 Policy checks that will bite at landing

- **`post-facto-human-review`**: #1710 carries `3125d697a`, which changes how `flock(2)` is determinized
  (no-op success → forwarded to the kernel) and edits `syscall_classification.rs` with
  `AUTONOMOUS-BOT-IMPLEMENTED` / `TODO-HUMAN-REVIEW(#791)` tags already in place. Evaluate it against
  trigger **3, new determinization strategy** before landing; the label is informational and never a
  landing blocker, but the PR body must then name the numbered trigger.
- **#1677 changes a default** (fail-closed unsupported-syscall handling becomes the default). That is a
  user-visible behavior change riding inside a batch mostly made of fixtures; call it out in the landing
  PR body rather than letting it read as fixture work.
- **Mechanism tags**: #1728 already carries `mechanism:shared-memory-determinism`. No two of the seven
  share a mechanism tag today.

### 7.2 Do NOT

- **Do not stack the rest of the drain.** Stacking pays only where serial landing destroys evidence.
- **Do not collapse PRs already tracked in the serial landing queue.**
- **Do not resurrect the three closed-on-the-merits PRs** (vacuous `candidate_sites=0` fixture,
  timeout-only bump to 1200 s, fixture timing out under ptrace) — two are time-blunting violations.
- **Do not hand-merge the derived inventories** (`test-files.json`, `expected-e2e-plan.json`).
  Regenerate them; a hand-merge is how a manifest silently loses a case.

## 8. Side effect worth knowing, fleet-wide

`sudo dnf install -y lua ruby` (rung 1 of the dependency ladder) cleared the
`language-runtimes/lua-random.sh` / `ruby-random.sh` *prepare failed* red that had reddened **every** local
full validate since 2026-08-03. That was never this stack's fault and the fix helps everyone. Rungs 2
(home-dir install) and 3 (container) were not needed. Re-run confirmed 0 prepare failures.

## 9. Reproduction

```bash
SLOT=/home/newton/work/dev-hermit/worktrees/verify/hermit

# containment: every path of all seven present in the stack
git -C $SLOT diff --name-only 4c70658e7 14a2ecee3

# the failing bucket, boxed, at either end (detach at 4c70658e7 for the base measurement)
systemd-run --user --unit=repro --collect --wait --pipe \
  --working-directory=$SLOT \
  --setenv=PATH=/home/newton/.cargo/bin:/usr/local/bin:/usr/bin:/bin \
  --setenv=LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib \
  ./ci/test_harness.sh run --lane portable --category determinism-stress --ci-only --allow-empty \
    --results ignored/repro/results.jsonl --junit ignored/repro/junit.xml

# the authorities
./ci-hub/ci-hub validate-status 14a2ecee3c43c07450d3959da7920119e3123252
./ci-hub/ci-hub validate-status 4c70658e785834737cbe1524f77330c781a6f5ea
./ci-hub/ci-hub newest-green --json
```
