# Obligation revert-recommendation path: decision-point map + taxonomy audit

**Task:** `obligation-path-must-consume-no-result-taxonomy`
**Author:** drain agent (claude-opus-4-8), 2026-08-04
**Method:** static read of committed source + git history. NO live PR / real-SHA test (an
automated revert recommendation is an authorization; fixture/dry-run/read-only only).
**Code state read:** `ci-hub/remediation/protocol.py` is CLEAN at parent HEAD
`22a9db11603eb825045ddff05f289b990ecd391a`, and `origin/main == HEAD` — so this maps the
**landed** decision surface, not a working-tree draft. Only `nonzero_result.py` and
`validate/aggregate.py` were working-tree-modified.

## Headline

The three named failure modes are **all fixed and landed to parent `origin/main`.** The
actuator fix is not a working-tree draft and not a phantom `implemented` — it is a series of
direct-to-main commits (parent works directly on shared main; ci-hub is parent-owned tooling,
no PR):

| commit | landed (PT) | what it fixed |
|---|---|---|
| `44e82fa` | 08-03 18:09 | classify cancelled/absent GitHub runs as no_result, never red |
| `d568782` | 08-03 18:51 | obligation actuator consumes no_result taxonomy, never reverts on a hole |
| `7010e38` | 08-03 20:03 | DERIVE the cancel/environmental no_result discriminator (not enumerate) |
| `ada620f` | 08-03 20:50 | **never arm revert on a partial (pending) or environmental picture** |
| `77e4102` | 08-03 20:53 | inner-MemoryMax OOM → no_result, ahead of test verdict |
| `e460cc9` | 08-03 23:30 | RESULT-LEVEL detector: a green must carry a nonzero executed count |
| `4f01840` | 08-04 00:12 | landing verification tri-state |

All are ancestors of HEAD `22a9db11`. `land_and_arm.py` contains **no** conclusion
classification and **no** revert logic (grep for `conclusion|_github_state|_classify_local|
revert|remediation_recommendation|trigger_remediation` is empty) — `protocol.py` is the
**sole** revert recommender.

## The taxonomy producers (classification layer — branch on TAXONOMY, not "not success")

- **`_github_state`** `protocol.py:734-743`. `_GREEN={success,neutral}` (:730),
  `_RED={failure,timed_out,startup_failure}` (:731). `status!=completed → "running"` (:737);
  green→green, red→red, **everything else → `no_result`** (:743). This is TOTAL over the enum:
  `cancelled`, `skipped`, `stale`, `action_required`, empty, and any UNKNOWN future value all
  fall to `no_result`. **This is exactly the CANCELLED / TIMED-OUT(as cancel) / NEVER-STARTED /
  ADMISSION-DENIED bucket → no_result.** ✔ consumes taxonomy.
- **`_classify_local`** `protocol.py:230-273`. DERIVED, not enumerated: exit 0 + zero-test-green
  → no_result (:249); exit<0 or ∈{137,143} → no_result (:252); inner-MemoryMax OOM → no_result
  (:254); build-phase failure → no_result (:261); **only** `_has_test_failures` → `red` (:266);
  any other nonzero → no_result (:272). ✔ only a genuine test verdict is red.

## The revert decision gate — `_remediation_ready` `protocol.py:898-930` (THE decision point)

Every input state it acts on, and which taxonomy value each branch keys on:

| line | branch | input state | outcome | keys on |
|---|---|---|---|---|
| 921 | `if "running" in (local,github)` | either leg in flight | **return False** (no revert) | taxonomy `running` — **MODE 1 guard** |
| 923 | `if github == "red"` | hosted authoritative fail | **return True** (revert) | taxonomy `red` (only failure/timed_out/startup_failure) |
| 926 | `if local=="red" and github=="green"` | disagreement | **return False** | taxonomy `green` — **MODE 2 guard** |
| 928 | `if local=="red"` (github ∈ no_result) & `spent>=LIMIT` | local red, no hosted answer, budget spent | **return True** (revert alone) | `redispatch_count` — **RESIDUAL, see below** |
| 930 | default | e.g. local green+github no_result, or both no_result | **return False** | — |

Downstream of the gate:
- **`evaluate_obligation`** `:933-1037`: `if _remediation_ready` → recommend + `trigger_remediation`
  (:948-985); `(green,green)` → satisfied (:987); **`_legs_disagree` → `investigation_required`,
  never revert** (:1002-1028); else → progress no-op.
- **`_legs_disagree`** `:886-895`: `local=="red" and github=="green"` → surfaced for a human,
  never auto-revert. **MODE 2 second guard.**
- **`_failure_details`** `:841-861`: only legs whose state ∈ `REMEDIATION_STATES={red}` (:43, :845)
  are failures — a `no_result` leg is never a failure detail.
- **`_maybe_redispatch_local`** `:1452-1516`: `no_result` OR uncorroborated `red` → re-dispatch up
  to `DEFAULT_LOCAL_REDISPATCH_LIMIT=2` (:50), never revert.
- **`remediation_recommendation`** `:864-883`: only reached after the gate authorizes; picks
  revert (main tip == landed) vs fix-forward (main advanced).

## The three named failure modes — CONFIRMED FIXED in landed source

1. **Arms a revert while a leg is still PENDING** → FIXED. `protocol.py:921`
   `if "running" in (local, github): return False`. Design note :898-907 cites the exact incident
   (obligation `20260804-025419-0f891e43`, GitHub run 30873193855 still executing). (`ada620f`)
2. **Reverts on LOCAL-RED + GITHUB-GREEN disagreement** → FIXED. `protocol.py:925-927`
   (`if github=="green": return False`) AND `_legs_disagree` `:886-895` routing to
   `investigation_required` at `:1002-1028`. (`ada620f`)
3. **Misclassifies a BUILD PANIC as red** → FIXED. `_is_build_phase_failure` `:216-227`
   (`_BUILD_SCRIPT_PANIC_RE = panicked at ... build.rs`, :110; `failed to run custom build
   command`, :107) evaluated at `:261-265` **before** `_has_test_failures` `:266`, so the shared
   `N failed` / `panicked at` vocabulary cannot manufacture a red. (`ada620f`/`77e4102`)

## RESIDUAL exposure (the surface we did not know precisely) — `protocol.py:925-929`

**`local=="red"` + `github=="no_result"` + `redispatch_count >= 2` → REVERT alone.**

This branch *does* consume the taxonomy (it correctly distinguishes github `no_result` from
`red`/`green`). It is documented-intended behavior (design note :910-917: "reverts alone ONLY
when the hosted leg gave no answer AND it survived the whole re-dispatch budget"). The exposure
is a **policy × environment** interaction, not a classification bug:

- Under hosted CI **admission-limited at ~8 concurrent**, `github=="no_result"`
  (cancelled-below-cap / never-admitted / superseded) is no longer rare — it is the **common**
  case. That shifts the arbiter of a revert from "authoritative hosted leg" to `_classify_local`
  **alone**, with no hosted corroboration because hosted never ran.
- `_classify_local` is conservative but not infallible. A revert can still fire alone when a
  **genuinely environmental** failure renders as a test verdict AND reproduces across both cold
  re-dispatches:
  - a non-`build.rs` panic: `"panicked at .../<something>_test.rs"` matches `_TEST_FAILURE_MARKERS`
    (`"panicked at"`, `protocol.py:92`) but is **not** caught by `_BUILD_SCRIPT_PANIC_RE` (:110,
    requires `build.rs` on the line) → classified **red**. (cf. the lsmod strict-verify flaky =
    host `/proc/modules` refcount churn, and detcore_misc vfork ESRCH-spin under load — real
    "failures" that are environmental/load-dependent, not regressions.)
  - `\b[1-9]\d* failed\b` (`_TEST_FAILURE_COUNT_RE`, :94) from any source rendering that vocabulary.
- A deterministic-under-load flake reproduces across 2 re-dispatches on the same loaded/cold box,
  so `redispatch_count>=2` is reached, and — with hosted never admitted — `:928` returns True.

**Net:** the three code bugs are closed; the remaining hole is that a **local-only revert** is now
reachable in the common admission-limited regime, gated solely by `_classify_local`'s ability to
tell an environmental test-shaped failure from a real regression. Options to consider (not
implemented here — read-only deliverable):
1. Do not revert on `local red + github no_result` at all — downgrade to `investigation_required`
   (same treatment as `_legs_disagree`) until the hosted leg produces an actual `red`. Makes the
   hosted leg the *only* revert authority; a lone local red never reverts.
2. Require the hosted leg to have *reported* (green or red) before any revert — i.e. treat a
   perpetual `no_result` like `running` at `:921` (never-terminal → never-arm), relying on
   re-dispatch/backoff to eventually admit the hosted run.
3. Tighten `_classify_local`'s red set to exclude bare `panicked at` unless a libtest
   `test result: FAILED` summary is also present (a panic without a failing test summary is more
   often an environmental abort than a product verdict).

Verification owed for any fix (per CLAUDE.md bracket-both-sides): **negative** — plant
`local=red + github=no_result + spent>=limit` at a healthy fixture SHA, confirm NO revert /
NO obligation; **positive** — plant a genuine `github=red`, confirm it STILL reverts (guard not
inert). Fixture/dry-run only.
