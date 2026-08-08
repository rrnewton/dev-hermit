# PR draft — `stack/ci-validate-tooling`

**Branch:** `stack/ci-validate-tooling` (pushed to `rrnewton/hermit`)
**Head:** `51fae5e1fabf9a5d637be498cdd40ff0e5230d0b`
**Base:** `4c70658e785834737cbe1524f77330c781a6f5ea` (the published FRESH-MAIN-TIP)
**Coalesces:** #1635 #1637 #1639 #1640 #1641 #1656 #1665 — 8 commits, cherry-picked (`-x`), not squashed.

> **Do not merge from this document.** Landing is serial and `hermit-det2` owns it.

---

## Summary

Seven conflict-free open PRs, all on one topic — CI and validation tooling — replayed onto the
current `origin/main` as a single reviewable stack so they consume **one** box-exclusive validate
slot instead of seven.

| PR | commit | what it does |
| --- | --- | --- |
| #1635 | `67b5080a3` | `validate.rs`: `full` meta-profile subsuming `run_full_suite` + self-teed durable receipt log |
| #1637 | `9a920b21a` | `ci(merge-gate)`: stop NO_RESULT retry amplification |
| #1639 | `47fa8a4e1` | `validate.sh`: emit per-node `coverage{}` in the ledger row (schema 5) |
| #1640 | `f05662236` | measurement-only `--strict-compat-jobs` inner-scaling benchmark |
| #1641 | `fc165a738` | backend-parity: one shared mutation harness for the identity-fixture family |
| #1656 | `fd1383c7b`, `ceb9b8e72` | remove `gh` bootstrap from the Reverie pin gate; split resolver bootstrap from workflow cutover |
| #1665 | `51fae5e1f` | `ci`: shard backend parity manifests |

Commits are kept **distinct and in PR order**, so if the receipt comes back red the failure
bisects to a single PR and the batch can be dropped back to serial without re-deriving anything.

## Determinism

**This stack changes no guest-visible behaviour, so it cannot change any program's determinism.**
That is an argument from the diff's reach, not from test results:

* Every file touched is CI plumbing, validation scripting, or test-harness/fixture material:
  `validate.sh`, `scripts/validate.rs`, `scripts/manifest-to-commands.rs`,
  `scripts/check-merge-gate-policy.sh`, `ci/test_harness.sh`,
  `ci/test-resolve-reverie-pin-targets.cjs`, `tests/backend-parity/**`,
  `tests/e2e/manifests/**`, `tests/manifest-cli.rs`, `docs/`. **No `detcore/`, no `detcore-model/`,
  no `hermit-cli/` runtime path, no syscall handler, no scheduler code.** Nothing in the diff is
  linked into the `hermit` binary that supervises a guest.
* The one change that *touches* determinism machinery does so only as an **observer**: #1639 adds a
  per-node `coverage{}` object to the ledger row. It records what a validate run covered; it does
  not gate, retry, or reorder anything, so it cannot alter what executes.
* #1640 is explicitly **measurement-only** — it adds a benchmark that reports inner-scaling numbers
  and changes no default.
* #1637 *removes* nondeterminism from the merge gate rather than adding any: NO_RESULT retry
  amplification made the gate's verdict depend on how many times a node happened to be retried.
  Collapsing that makes the gate a function of the run, not of retry history.
* #1641 and #1665 reorganise how parity fixtures are grouped and sharded. Sharding changes the
  *partition* of tests across jobs, not the content of any test, and each fixture still runs its
  own comparison; no fixture's expected output is edited by this stack.

**Where this stack could still bite, and why it does not here.** Reordering or resharding CI work
can hide a determinism failure by never running the test that would have caught it. Two checks
bound that risk: the repo's own exact ratchet (`enforce_exact_ratchet` against
`ci/matrix-symmetry-baseline.json`) passed on the coalesced tree, and the planner emitted a full
657 KB harness plan with `rc=0`, so no manifest silently lost coverage.

## Linux Semantics

Not applicable — no syscall, signal, process, or filesystem semantics are touched.

## Validation

* **Rebase:** all 8 commits cherry-picked onto `4c70658e7` with **zero conflicts**, individually
  and coalesced. No content was hand-merged and no drive-by refactor was taken.
* **Anchor preflight:** `ci-hub/validate/preflight_anchor.py --head 51fae5e1f` →
  *"contains all 2 producer anchors; validate can produce a qualifying receipt."*
* **Derived-inventory check (the trap this batch was most likely to hit):** the stack migrates
  `tests/e2e/manifests/inventory/test-files.json` (3095 lines, 166 C fixtures) to
  `explicit-test-files.json` (1175 lines, 8 C fixtures), and git cherry-picks a whole-file
  replacement without ever reporting a conflict. That is the exact shape of a silently-stale
  generated file. **Investigated and refuted:** schema 2's `explicit-` inventory is a curated
  exception list, not the full enumeration, and `ci/matrix-symmetry-baseline.json` at this head is
  the **tip's** copy, untouched by the stack. The planner reconciles the two through
  `enforce_exact_ratchet` and exits 0, so tip-baseline and stack-inventory are consistent.
* **Targeted tests at the head:** `cargo test -p hermit-manifest-plan` → 3 passed + 16 passed,
  0 failed.
* **Full validate receipt: NOT OBTAINED — see Blocker below.** No receipt is claimed and none
  should be inferred. This PR is **not** landable until one exists at exactly `51fae5e1f`.

### Blocker: validate admission cannot run in-jail

```
ci-hub validate-run --checkout worktrees/det1/hermit --agent hermit-det1 \
  --target 51fae5e1fabf9a5d637be498cdd40ff0e5230d0b -- full
→ REFUSED: cannot refresh origin/main in <checkout>:
  fatal: unable to access 'https://github.com/rrnewton/hermit.git/': CONNECT tunnel failed, response 403
```

`ci-hub/validate/preflight_validate.py:91-107` runs `with-proxy git -C <checkout> fetch origin
refs/heads/main:refs/remotes/origin/main` and fails closed if it errors. In-jail egress is 403, so
the gate refuses **before** the box-exclusive lock is taken.

This is the gate working as designed and **should not be bypassed** — it is the guard against
validating on a stale base, which is what caused the incident this whole landing effort is
recovering from. Note the gate is strictly stricter than its own stated predicate here: the
worktree's `refs/remotes/origin/main` **already equals** the authoritative published tip
`4c70658e7`, so the freshness condition is satisfied in fact; only the act of re-proving it over
the network fails.

Three ways to clear it, in order of preference:

1. **Run `ci-hub validate-run` outside the jail.** `herdr-run`'s allowlist is `cargo, gh, git`
   only, so it refuses `ci-hub`. Adding `ci-hub` to that allowlist is the smallest change that
   restores the whole fleet's ability to validate.
2. **Let the gate accept an already-current ref.** If `refs/remotes/origin/main` already resolves
   to a caller-supplied authoritative SHA, the fetch is redundant. This must be an explicit
   compared-against-a-published-value path, never a blanket `--no-fetch`.
3. Restore in-jail egress to `github.com`.

## Human Review Required

Not applicable. Checked against all four triggers: (1) no new syscall support; (2) no Reverie
API or core-abstraction change; (3) no new determinization strategy; (4) no DetCore scheduling
change. The `post-facto-human-review` label should **not** be applied.
