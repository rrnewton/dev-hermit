# Vacuous-test audit of the Hermit staging candidates (reverie lens)

**Task:** `vacuous-test-audit-hermit-staging-candidates` · **Agent:** hermit-audit (`[impl agent, opus-5]`)
**Date:** 2026-08-06T02:56Z · **Constraint:** box-wide egress 403 — **local evidence only**, no GitHub
reconciliation. Candidate set is therefore the **locally materialized staging branches**, not a live
`gh pr list`.

## The question

Applied to every candidate, from the reverie result that motivated this task (5/5 reverie KVM staging
members had tests that passed without exercising their mechanism; 4/5 concealed a real bug):

> **Does the test FAIL if the mechanism does not run?**

Where feasible the verdict is **mutation-checked**: a divergence is planted and the test must go RED.
A test that stays green under a planted break is vacuous. Every mutation below was actually executed;
exit codes are quoted verbatim.

## Exact state

| Thing | SHA |
| --- | --- |
| hermit `origin/main` | `b64d893ae9ea6404472eae9cb86102d91ec642ef` |
| `staging/batch-1` | `82e44d4edb5c63715c71a74a90c63921a41bccdb` |
| `staging/batch-1b` | `e50b283ad915efae7a9b728c3a1b39ee6adb989e` |
| `staging/batch-2` | `99358391221f5624300ea0954a7638311e4981a0` |
| `staging/batch-3` | `9bee0e17432eb23db3b90b6b155773d42c5d6670` |
| `staging/drain-all` | `d1bf43bfe26b88228b8e8e9f57d2cee3e901838a` |
| parent `dev-hermit` HEAD | `a305cecf201ba05b353812ce64366ecdcb42c3c1` |

Mutation workspace: `staging/batch-2` exported with `git archive` to
`scratch/vacuous-audit-2026-08-05/b2` (git-init'd at `bf67a84` so tooling can resolve a repo root),
`hermit-manifest-plan` + `generate-test-footprints` rebuilt **in that tree** (`--offline`). Every
mutation was reverted; `git status --short` in the scratch tree is empty at the end of each block.
No primary checkout and no other agent's tree was modified.

> **Tooling landmine discovered while setting this up:** `ci/manifest-plan/src/main.rs:81` resolves the
> manifest directory from `env!("CARGO_MANIFEST_DIR")`, i.e. the path is **baked in at compile time**.
> A `hermit-manifest-plan` binary borrowed from another checkout silently validates *that* checkout's
> manifests, not the tree you are standing in. My first mutation round was invalidated by this and was
> re-run with a locally built binary. Any harness that reuses a prebuilt plan binary across worktrees
> is auditing the wrong tree.

## Denominator

51 distinct PR merges appear across the five local staging branches. The prior audits recorded in this
task's notes (2026-08-04, three areas) covered 33 of them. This audit covers:

* **28 previously un-audited candidates** (the gap), and
* **6 re-verdicts** of candidates the 2026-08-04 notes graded KEEP, overturned by the family finding below.

**Total judged here: 34.** One (`#1559`) is excluded — it is reverted inside `staging/batch-3`
(`9bee0e174 Revert "batch-3: merge PR #1559"`), so it is not a live batch member. Effective
denominator **33**.

---

## Finding 1 (headline, mutation-proven): the entire `backend-parity-c` contract family is
## **NOT BUILT AND NOT RUN IN CI**

Every "backend-parity contract" PR adds a `[[test]]` cell to `tests/e2e/manifests/backend-parity-c.toml`
in which **every mode declares `ci = false`**. The consequence chain, each link verified:

1. **Static.** 15 of the 16 parity PRs in the staging set add `ci = false` in all five modes and
   **zero** `ci = true`: `#1227 #1233 #1235 #1246 #1247 #1250 #1252 #1393 #1464 #1477(×3) #1365 #1380
   #1471 #1472 #1473`. Only `#1544` adds `ci = true` (4 occurrences).
2. **Static.** `ci/expected-e2e-plan.json` contains **0** `backend-parity-c` rows at both
   `staging/batch-2` and `origin/main`. The whole plan is **74 required cells**
   (`verify 70, custom 2, replay 1, chaos 1`) over `system-utils 22, c-programs 20,
   language-runtimes 17, determinism-stress 5, determinism-stress-c 5, data-handling 4,
   applications 1`. `backend-parity-c` contributes **0/74**.
3. **Mechanism.** The CI node is
   `./ci/test_harness.sh run --lane portable --category backend-parity-c --ci-only --allow-empty …`
   (`ci/dag/portable.json`). `--ci-only` drops every `ci = false` cell
   (`ci/test_harness.sh:1228-1229`); `--allow-empty` makes a zero-cell node green
   (`ci/test_harness.sh:1871`).
4. **MUTATION — sabotaged product binary.** Replaced `target/debug/hermit` with a stub that prints
   `SABOTAGED HERMIT INVOKED` and exits 1, then ran the exact node command.
   **`NODE EXIT=0`**, `junit tests="0" failures="0"`, `results.jsonl` 0 lines, **stub never invoked**.
5. **MUTATION — broken fixture semantics.** Rewrote `eventfd_probe.c` to print
   `counter=999 sem=999` and `return 3` (wrong values *and* nonzero exit).
   **parity node `EXIT=0`, `tests="0"`; `./ci/test_harness.sh validate` `EXIT=0`.**
6. **POSITIVE CONTROL (the gate is not inert in general).** Flipped
   `backend-parity-c/eventfd-semantics` `[test.modes.verify]` to `ci = true`:
   * the plan emitter now yields the cell (1 hit), and the node **`EXIT=1`** — the cell is really
     attempted (`ERROR portable verify ptrace backend-parity-c/eventfd-semantics`);
   * `./ci/test_harness.sh validate` **`EXIT=2`**: *"required E2E plan changed; update
     ci/expected-e2e-plan.json in the same review"*.
   So the plan ratchet is live, and the shipped `ci = false` is a deliberate, review-visible exclusion.
7. **Not built either.** With `tests/backend-parity/fixtures/` deleted,
   `./ci/test_harness.sh build --lane portable --ci-only --allow-empty` emitted 55 `BUILT` lines and
   **zero** `backend-parity` entries. (Deleting the directory *is* caught, but by manifest validation —
   `manifest-plan: … program path does not exist` — i.e. **file existence** is enforced, **behaviour**
   is not.)
8. **No second consumer.** The only backend-parity driver that runs in CI is
   `python3 tests/backend-parity/run_matrix.py --backend dbi --strict --require-backend`, whose
   `case_catalog` holds 48 cases with expected stdout. Grepped all 17 fixture names against it:
   **0 hits for every one.**

### Second-order vacuity: even if the cells were enabled, most would still not bind

`verify` mode runs `hermit run --backend B --strict --verify` — **two runs of the same backend compared
to each other**, `Stripped` comparator, no cross-backend comparison and no golden output
(`ci/test_harness.sh:1564-1567`, `run.rs:2695-2817`, `verify.rs:588-731`). The manifest schema has no
expected-output field at all (`ci/manifest-plan/src/main.rs:440-458, 575-600`). So the only assertion
available to a fixture is **its own exit status**.

Measured over the family:

| Fixture (PR) | self-assertion | binds if enabled? |
| --- | --- | --- |
| `cpu_virtualization` (#1464) | `return 1..5` on wrong `getcpu`/affinity values | **YES** |
| `eventfd_probe` (#1235) | 11 `fail()` paths, all syscall-error only; counter `36/5` never asserted | no |
| `pidfd_open_self` (#1393) | none — `ok=N` counter, unconditional `return 0` | no |
| `socketpair_flags` (#1380) | none — `ok=N`, `return 0` | no |
| `fchmodat2_flags` (#1365) | none — `ok=N`, `return 0` | no |
| `pipe_ipc` (#1227), `vectored_io` (#1233) | error-path exits only; result in stdout counters | no |
| `append_pwrite` (#1246), `ftruncate_sparse` (#1247), `vectored_file_io` (#1250), `openat_flags` (#1252), `fd_duplication` / `dup_shared_offset` / `lseek_positioning` (#1477) | `ok++` counters (6–11 each), unconditional `return 0` | no |
| `prctl/rlimit/getcpu/sched_getaffinity identity` (#1470–#1473) | in `tests/c/`, still `ci = false` in `backend-parity-c.toml`; 0 plan rows | not run |

A degraded-but-deterministic backend emits the same `ok=3` in both runs and passes. **This is exactly
the reverie `#338` shape** (per-instance collector where dropping everything still scores 100%),
one level up.

### Re-verdict of six earlier KEEPs

The 2026-08-04 13:26 note graded `#1470 #1471 #1472 #1473` KEEP on fixture-internal negative controls
(*"0x7f poison sentinel is a real negative control"*, *"best of set"*), and the 13:26 vs 13:30 notes
contradict each other on `#1365`/`#1380`. Both are resolved here: **a sentinel inside a guest that is
never compiled and never executed binds nothing.** The 13:30 note ("fixture always exits 0") is the
correct reading of `#1365`/`#1380`; the 13:26 note read the *intended* `ok=N` value as an assertion.

---

## Per-candidate verdicts

`REAL` = the test fails if the mechanism does not run. `VACUOUS` = it passes with the mechanism absent
or inert. `UNSURE` = not determinable from local evidence (needs a run this box cannot do offline).

### A. backend-parity contract family — 16 candidates, **16 VACUOUS**

| PR | cell | verdict | hidden bug? |
| --- | --- | --- | --- |
| #1227 | `pipe-ipc` | VACUOUS (not run; counter-only if run) | none found |
| #1233 | `vectored-io` | VACUOUS (idem) | none found |
| #1235 | `eventfd-semantics` | VACUOUS (idem) | none found |
| #1246 | `append-pwrite` | VACUOUS (idem) | none found |
| #1247 | `ftruncate-sparse` | VACUOUS (idem) | none found |
| #1250 | `vectored-file-io` | VACUOUS (idem) | none found |
| #1252 | `openat-flags` | VACUOUS (idem) | none found |
| #1393 | `pidfd-open-self` | VACUOUS (not run; **superseded** — #1544 ships a hard-asserting `tests/c/pidfd_open_self.c` with 7 `return 1` paths and enables it) | none |
| #1464 | `cpu-virtualization` | VACUOUS **(not run)** — but the *only* fixture that would bind if enabled | none |
| #1477 | `fd-duplication`, `dup-shared-offset`, `lseek-positioning` | VACUOUS (not run; counter-only) | none found |
| #1365 | `fchmodat2-flags` | VACUOUS (**re-verdict**) | none |
| #1380 | `socketpair-flags` | VACUOUS (**re-verdict**) | none |
| #1470 | `prctl-identity` | VACUOUS (**re-verdict**: 0 plan rows) | none |
| #1471 | `rlimit-identity` | VACUOUS (**re-verdict**) | none |
| #1472 | `getcpu-identity` | VACUOUS (**re-verdict**) | none |
| #1473 | `sched-getaffinity-identity` | VACUOUS (**re-verdict**) | none |

No concealed *product* bug was found behind this family — the defect is **claimed coverage that does
not exist**. Every one of these PRs advertises a "parity contract" and moves a ratchet in its
description while contributing 0 executed cells.

### B. Everything else — 17 candidates

| PR | what it changes | verdict | evidence |
| --- | --- | --- | --- |
| **#1598** | timerfd poll/epoll readiness under virtual time (the fix for the confirmed `#1213` gap) | **REAL — strongest in the batch** | real guests do cross-thread `poll`/`epoll_wait(timeout=0)`, run 5×, require exact stdout prefix `poll=1:1 time=` / `epoll=1:1:1 time=` and byte-stability. Without virtualization the timer is not ready at timeout 0 → `count=0` → prefix mismatch → RED |
| **#1631** | verify verdict staged beside its target; verdict stamped before every fallible path | **REAL** | 9 new tests that *plant* state (`plant_previous_green`, `assert_no_result`, `assert_top_level_exit_leaves_no_result`); asserts a stale green cannot survive an aborted run |
| **#1596** | record/replay xfail-strict ratchet | **REAL** | wired into `validate.sh` as a blocking gate (`cargo test -p hermit --test record_replay_xfail_strict`, 900 s); 6 classifier tests bracket both sides on synthetic evidence + 1 real-binary test that fails on an unexpected pass *or* an unrecognized failure shape |
| **#1608** | merge-gate NO_RESULT outcomes | **REAL** | the shipped `merge-gate.yml` embeds its own predicate self-test that runs in-gate: `N=2 PASSED, N=4 FAILED, N=12 NO_RESULT`. **RESIDUAL:** the python↔shell engine-parity script `scripts/test-check-status-outcome.sh` (plus `test-required-check-outcomes.sh`, `check-merge-gate-policy.sh`, `test_pr_status.py`) is invoked **only from `make lint`** (Makefile:89-91), and `make lint` appears in **no** `ci/dag/*.json` step and in no `validate.sh` gate — the parity check can rot undetected |
| **#1626** | procfs: hide numa_maps page counters + host `/run/user/<uid>` mounts | **REAL** | two fixture-based tests with real `mountinfo`/`numa_maps` input asserting the field is hidden; remove the sanitizer → RED |
| **#1607** | third-party backend build moved downstream in the portable DAG + regenerated footprints | **REAL (mutation-checked)** | `test_harness.sh validate` runs `audit_test_footprints`. **MUTATION:** dropped 1 of 19 entries from `ci/test-footprints.json` → **`EXIT=2`, "ci/test-footprints.json is stale"**. **RESIDUAL:** the die message claims staleness "relative to Cargo metadata, **the portable DAG**, or footprint policy", but two DAG mutations (appending to a step `cmd`; emptying a step's `deps`) both left it **`EXIT=0`** — the DAG half of that claim is weaker than advertised |
| **#1544** | promote high-value e9patch syscall contracts | **REAL** | the only family member that adds `ci = true` (4) and real rows to `ci/expected-e2e-plan.json` (`backend-parity-c/pid-probe` × ptrace/sabre/liteinst, `c-programs/pidfd-open-self`, `scheduler-policy-queries`, `syscall_file_metadata`). The promoted `tests/c/` fixtures hard-assert (`return 1` ×7 / ×12 / ×12), unlike the `ok=N` family |
| **#1622** | declare DBI `file_metadata` an `fchown` gap; ratchet 27/28 → 26/28 | **REAL** (honest ratchet lowering for the confirmed `#1549` fchown bug) | **RESIDUAL:** a declared gap is **skipped**, not xfail-strict — `run_matrix.py:790 if is_gap and not args.probe_gaps: continue`, and the CI node does not pass `--probe-gaps`. If DBI `fchown` is later fixed, nothing tells us (XPASS undetectable in CI) |
| **#1621** | pin core chosen from the tracer's allowed affinity mask | **REAL (weak)** | the unit test is the changed decision logic on a sparse mask `[7,31,211]` plus the empty-mask `None` control. **RESIDUAL:** it calls the helpers directly — `sched_getaffinity` and the `container.affinity()` call site are unbound; a revert of `apply_affinity` alone would only be caught indirectly (dead-code under `clippy -D warnings`) |
| **#1615** | inline `first_error_line` + `failed_substep_classes` into red ledger rows | **UNSURE** | no test in the PR. The consumer is real (`ci-hub/validate/attribute_reds.py:203` reads the row-carried fields and its docstring names this exact producer gap), but nothing asserts the producer emits non-null on a real red — same residual shape as `#1587` |
| **#1616** | purge zero-byte `*.o` before build | **VACUOUS (no test) — mechanism verified by this audit** | **MUTATION:** extracted `purge_zero_byte_objects` and planted `deps/truncated.o` (0 B), `sub/other.o` (0 B), `sub/healthy.o` (17 B), `keep.a` (1 B) → **purged 2, healthy `.o` and `.a` untouched**; missing root → `0`. **RESIDUAL:** only `*.o` is covered — a 0-byte `.a`/`.rlib`/`.so` from a killed `ar`/linker survives, and the PR's own rationale names SaBRe/e9patch static-archive links |
| **#1397** | LiteInst arch-prctl GS shadow | **VACUOUS — failure shape (1), arithmetic not behaviour** | the only test calls the pure helper `arch_prctl_gs_shadow(requested, observed)` with three integer pairs. Untested: the `ARCH_SET_GS` inject + `regs().gs_base` read, the shadow store, `ARCH_GET_GS` returning the shadow, the `EFAULT`-on-null-addr arm, and **clone inheritance** (`pts.1.arch_prctl_gs_shadow` at `detcore/src/lib.rs:1172`). **Suspected gap (UNVERIFIED, needs a run):** a child thread inherits the parent's GS shadow, so `ARCH_GET_GS` in a freshly cloned thread may report the *parent's* requested base; likewise the shadow is not obviously cleared across `execve`. Required control: a guest that sets GS, clones, and reads GS in the child — assert the child's value, at HEAD (fails) and after the fix (passes) |
| **#1630** | `validate.sh --shallow-select` / `--all` / `--full-run` | **VACUOUS + REAL RECEIPT-INTEGRITY RISK** | no test of any kind. `--shallow-select` pins the selective baseline to `HEAD~1` with **no greenness check at all** (`resolve_selective_baseline`), bypassing the function's own documented contract *"Never fail-open on a stale or missing baseline"* that the non-shallow path enforces via the ledger. It then prints `Selective validation: last-known-green baseline = <HEAD~1>` — **a false label**: nothing established `HEAD~1` as green. And the ledger row carries **no baseline field and no shallow flag**: `selection_mode` is `"selective"` for both paths, so a shallow receipt is byte-indistinguishable from a since-green one. Direct Proxy-Binding violation — the value does not carry its condition. `--all/--full-run` is a self-declared no-op that is likewise unrecorded |
| #1568 | `ci-portable-autoretry.yml` retry-vs-local-fallback | **UNSURE** | workflow-only, 0 tests, no local execution path; needs a live CI run to bracket |
| #1623 | stage `cargo-nextest` where cargo looks first | **UNSURE** | workflow-only, 0 tests |
| #1625 | advance Reverie pin to `55f6876a` | **UNSURE** | a pin advance's "test" is the full suite at the new pin; nothing in the diff binds it. Cross-check: memory records `reverie-pin-55f6876a-dbi-sigsegv-unsupported-syscalls` as CONFOUNDED — treat as unresolved until a full receipt exists at the pinned pair |
| #1628 | `.agents/skills` cross-client discovery | **N/A** | documentation only; no product mechanism to bind |

*(#1559 excluded — reverted inside `staging/batch-3`.)*

---

## Counts (denominator 33)

| Verdict | Count | Share |
| --- | --- | --- |
| **VACUOUS** | **19** | 58% |
| REAL | 9 | 27% |
| UNSURE | 4 | 12% |
| N/A (docs) | 1 | 3% |

VACUOUS breakdown: **16** are the never-executed parity family, **3** are individual
(`#1397` arithmetic-not-behaviour, `#1616` no test, `#1630` no test + receipt risk).

**Concealed product bugs behind the VACUOUS verdicts: 0 confirmed, 1 suspected-unverified**
(`#1397` clone-inherited GS shadow). This differs sharply from the reverie result (4/5 hid a real bug):
here the defect is overwhelmingly **claimed coverage that does not exist**, not a live wrong answer.
That is still a landing-blocking finding — a batch of "parity contracts" that moves ratchets and
advertises backend coverage while executing nothing is precisely the condition that lets the *next*
real defect land green.

## Comparison with the 2026-08-04 notes

| | 2026-08-04 rollup | This audit |
| --- | --- | --- |
| Denominator | 33 (live open PRs, GitHub-reconciled) | 33 (local staging branches, offline) |
| Method | diff + test reading | diff + test reading **+ 9 executed mutations** |
| Parity fixtures | 8 KEEP / 1 DROP | **16 VACUOUS** (family-level, mutation-proven) |
| Headline | "hermit staging is materially CLEANER than reverie's" | true for *hidden bugs*; **false for coverage** — the largest family contributes 0 executed cells |

The divergence is entirely explained by scope: the earlier audit asked whether each fixture's
*internal* controls were sound (many are), and never asked whether the cell **runs**.

## Recommended actions

1. **Do not count the parity family as coverage.** Any ratchet, scorecard, or PR description citing
   `backend-parity-c` cells must state `ci = false / 0 executed cells`. A "contract" that is not in
   `ci/expected-e2e-plan.json` is an inventory row.
2. **Adopt `#1544`'s shape as the family's fix template**: move the fixture to `tests/c/`, make it
   hard-assert (`return N` per failed property, never a stdout counter), set `ci = true`, and update
   `ci/expected-e2e-plan.json` in the same review — the plan ratchet already forces the last step
   (proven: `validate EXIT=2`).
3. **`#1464` is the cheapest promotion**: `cpu_virtualization.c` already hard-asserts (`return 1..5`);
   only the `ci` flag and a plan row are missing.
4. **`#1393` should be closed as superseded** by `#1544`'s hardened `tests/c/pidfd_open_self.c`.
5. **`#1630` must not land as-is.** Either record the baseline SHA and a `shallow` flag in the ledger
   row and stop printing `last-known-green` for an unvalidated parent, or drop `--shallow-select`.
6. **`#1397` needs the clone/exec GS control** before landing (see the row above).
7. **`#1616`**: extend the purge to `*.a`/`*.rlib`/`*.so`, and add the positive/negative plant this
   audit ran as a self-test.
8. **`#1608`**: wire `scripts/test-check-status-outcome.sh` into a DAG node or a `validate.sh` gate;
   `make lint` is not a gate.
9. **`#1622`**: make gaps xfail-strict (add `--probe-gaps` to the CI node, or fail on XPASS) so a fixed
   gap is noticed automatically.

## Reproduction

```bash
cd ~/work/dev-hermit
mkdir -p scratch/vac/b2 && git -C hermit archive staging/batch-2 | tar -x -C scratch/vac/b2
cd scratch/vac/b2 && git init -q && git add -A -f && git commit -qm base
cargo build --offline -q -p hermit-manifest-plan --target-dir ../mp   # tool must be built IN this tree
printf '#!/bin/bash\necho SABOTAGED >&2\nexit 1\n' > target/debug/hermit && chmod +x target/debug/hermit
./ci/test_harness.sh run --lane portable --category backend-parity-c \
    --ci-only --allow-empty --prebuilt --results /tmp/r.jsonl --junit /tmp/j.xml ; echo "EXIT=$?"
# => EXIT=0, junit tests="0"; the sabotaged binary is never invoked.
```
