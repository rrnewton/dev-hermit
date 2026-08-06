# Adversarial review, batch 2 — CI + MISC subset

- **Task:** `adversarial-review-tightening-batch-2`, scoped mid-task to the **CI+MISC subset only**
  (queue-depth-1 both repos, nightly-stress, dag-test-steps→hermit-binary, wall-timeout-idiom 3×CPU,
  ebadf-classifier, unify-backend-stats-transport). The determinism/parity, boxing, and
  landing/ledger subsets are owned elsewhere and were **not** touched.
- **Reviewer:** adversarial-reviewer, claude-opus-5. Local only, no egress, **no concurrent validate**
  (verified: zero `validate.sh` / `validate-run` processes for the duration).
- **SHAs:** hermit `b64d893ae9ea6404472eae9cb86102d91ec642ef`; dev-hermit parent working tree at
  `19feac38110af940c42c84216937e18296509254`.
- **Plants:** every planted artefact lived under `/tmp/ar/`, never inside a repo. Deletion verified
  (§6).

---

## 0. The finding that reframes the batch

The review protocol asks for plant-a-violation + positive-control and a REAL / INERT / OVER-BROAD
verdict per guard. **Four of the six in-scope items have no guard to plant against** — their own
`IMPLEMENTED` notes say so explicitly — and a fifth was never implemented at all:

| Item | Its own note says | Guard exists? |
|---|---|---|
| `dag-test-steps-…-not-cargo` | "No CI config was modified: the task says propose" | **no** — research + `experiments/` |
| `no-hardcoded-wall-timeouts-idiom` | "3xCPU REFUTED … **NO code written**" | **no** — and task is PARKED |
| `main-queue-depth-1-not-cancel-in-progress` | "**NO FILE EDITED**", enumeration only | **no** — diagnosis only |
| `sandbox-failure-classifier-misses-ebadf` | "the fix exists as PR #1566 and is **NOT ON MAIN** … the gap is LIVE TODAY" | **no** — unlanded |
| `per-backend-stats-framework` (unify-backend-stats-transport) | status **OPEN**, "umbrella … keep open as tracking umbrella" | **no** — never implemented |
| `nightly-stress-tests-not-actually-running` | ships `ci-hub/stress/check_freshness.py` + tests | **yes** |
| `queue-depth-1-on-dev-hermit-main-too` | "the guard already exists" (config, not new code) | **yes, as config** |

**REAL/INERT/OVER-BROAD is a category error for a research artifact** — there is nothing to make
fire. So this review does two different things: the full bracket for the two real guards (§1, §2),
and independent re-verification of the central factual claim for the other four (§3). The latter is
the useful adversarial act on a document: *is what it asserts actually true?*

This is worth surfacing on its own. The batch list was assembled as though 11 guards had been
tightened. In this subset, **1 of 6 items is executable guard code**, and it is unwired.

---

## 1. Guard — nightly-stress freshness alarm

`ci-hub/stress/check_freshness.py` (250 lines, **untracked/uncommitted**).

### Verdict: **REAL** as code — **OPERATIONALLY INERT**: nothing invokes it.

**Author's own tests:** 18/18 pass. Claim confirmed.

**Independent bracket (mine, 11 planted cases).** Every plant in `/tmp/ar/`:

| # | Planted condition | verdict | alarm | exit | expected | |
|---|---|---|---|---|---|---|
| P1 | fresh row, `bursts_ok=0`, `instances=0` — the exact 2026-08-04 `CALIB_UNDERPOWERED` shape | NEVER | true | 2 | ALARM | ✅ |
| P2 | fresh, `bursts_ok=3` but `instances=0` (partial) | NEVER | true | 2 | ALARM | ✅ |
| P3 | measuring run 49 h old, bound 48 h | STALE | true | 2 | ALARM | ✅ |
| P4 | measuring run 47 h old — just inside the bound | FRESH | false | 0 | quiet | ✅ |
| P5 | empty store | NEVER | true | 2 | ALARM | ✅ |
| P6 | malformed-JSON-only store | NEVER | true | 2 | ALARM | ✅ |
| P9 | `started_at` only, no `finished_at`, fresh | FRESH | false | 0 | quiet | ✅ |
| P10 | store file missing entirely | NO_STORE | true | 2 | ALARM | ✅ |
| **PC** | **positive control — 4 legitimately fresh measuring runs** | **FRESH** | **false** | **0** | **not flagged** | ✅ |

Both sides of the 48 h boundary are bracketed (P3/P4), which is what distinguishes a working
threshold from one that always fires.

**Exit-code binding — the load-bearing property.** A guard that prints `alarm: true` and exits 0 is
invisible to every CI consumer. Verified directly: `alarm=true → exit 2` (`EXIT_ALARM`),
`FRESH → exit 0`, on both the real store and the plants. **The alarm binds to the exit code.**

**Denominator (the real population).** `ignored/ci-hub/stress-runs.jsonl` holds **5 rows = 4
measuring + 1 no-result**. Run against it today the guard returns **STALE, alarm=true, exit 2**, and
separates *firing* (40.78 h since the newest run) from *measuring* (58.0 h since the newest
**measuring** run). That discrimination is the guard's entire reason to exist and it works on real
data.

### Two evasion holes (narrow; neither makes it inert)

- **H1 — future-dated row.** A row with `finished_at = now + 240 h` yields **FRESH / quiet**. No
  upper bound on row age is enforced. Any clock skew or corrupt timestamp suppresses the alarm
  indefinitely — and "the lane is silently broken" is precisely the scenario the guard exists to
  catch. *Fix: reject or alarm on `row_time > now + small_skew`.*
- **H2 — boolean counts.** `measured()` tests `isinstance(x, int) and x >= 1`, and in Python
  `isinstance(True, int)` is `True`. A producer emitting `"bursts_ok": true, "total_instances": true`
  is counted as **measuring**. The type check does not actually check the type. *Fix: exclude `bool`
  explicitly.*

### The operational verdict

Grepping the whole parent tree for call sites returns **zero real invocations** — the only hits are
an unrelated same-named function in an agent worktree and the artifact's own prose.
`hermit/.github/workflows/nightly-stress.yml` **does not exist** (the workflow ships uninstalled as
`ci-hub/stress/nightly-stress.workflow.yml`). `crontab -l` → *no crontab for newton*; user systemd
timers matching stress → **0**.

So the lane still has no schedule, **and the alarm that would report that fact is itself
unscheduled.** Credit where due: the artifact states this itself ("`check_freshness.py` is not yet
wired to anything"). The author did not overclaim — the gap is in wiring and landing, not honesty.

---

## 2. Guard — dev-hermit queue-depth-1 (configuration)

### Verdict: configuration **CORRECT** for the safety property; **INERT as a guard** — nothing enforces it.

Ground truth for all **5** dev-hermit workflows (the denominator):

| Workflow | `cancel-in-progress` | Group key on main push | Effect on main |
|---|---|---|---|
| `dev-hermit-ci.yml` | `${{ github.event_name == 'pull_request' }}` → **false** | `…-push-${{ github.ref }}` — **shared** | **queue-depth-1** ✅ |
| `compat-envelope.yml` | `true` | falls back to `github.run_id` — **unique per run** | preserve-all (block is a no-op on main) |
| `nightly-demo-sweep.yml` | `true` | falls back to `github.run_id` — **unique per run** | preserve-all (deliberate; comment says so) |
| `demo-review-gate.yml` | *no concurrency block* | — | no cancellation |
| `portability.yml` | *no concurrency block* | — | no cancellation |

**The claim under review — "no unconditional cancel in dev-hermit-ci", all five classified — is
CONFIRMED: 0 of 5 workflows have an unconditional main-push cancel.** The safety property holds.

But the stronger reading does not. **Queue-depth-1 on main is implemented by 1 of 5 workflows.** Two
achieve safety by a *different* mechanism — a `github.run_id` group key makes every main run its own
group, so `cancel-in-progress: true` can never reach another run. That is "preserve everything", not
"queue depth 1", and the concurrency block is inert on main by construction. For
`nightly-demo-sweep.yml` that is explicitly intended ("Preserve every main-push run for exact culprit
attribution"); for `compat-envelope.yml` no intent is recorded.

**Plant-a-violation.** I planted a workflow with the regressing shape — ref-keyed group +
`cancel-in-progress: true` on main push — into a scratch copy of `.github/workflows`, then looked for
anything that would catch it:

- no `actionlint` on this box;
- no `make` target inspecting workflows;
- the only `cancel-in-progress` matches in `scripts/` and `ci-hub/` are the
  **`mechanism:cancel-in-progress` PR *label*** in `ci-hub/health/tests/test_pr_status.py` and prose
  in `ci-hub/history/query.py` — neither reads workflow YAML.

**Nothing catches it.** The idiom is a hand-maintained YAML convention with zero enforcement, so the
exact regression that caused dev-hermit run `30864011845` (tooling shards cancelled mid-flight) can
be reintroduced by any edit and would be caught only by human review. That is the definition of an
inert guard: the current *state* is right, the *guard* does not exist.

---

## 3. The four research artifacts — are their claims true?

Each verified independently rather than taken on the note's word.

### 3.1 hermit queue-depth-1 enumeration — **CLAIM CONFIRMED**

Re-derived by YAML-parsing all 9 hermit workflows (not by grep, which is fooled by the many
`cancel-in-progress` mentions inside *comments*):

| Workflow | `cancel-in-progress` | triggers on main push | |
|---|---|---|---|
| `docs.yml` | **`true`** | **yes** | **← the sole unconditional main-push canceller** |
| `runner-health.yml` | `true` | no | leave |
| `validation-levels.yml` | `true` | no | leave |
| `ci-portable.yml` | `false` | yes | already correct |
| `ci-privileged.yml` | `false` | yes | already correct |
| `demo-hot-path.yml` | event-aware | yes | already correct |
| `merge-gate.yml` | event-aware | no | fine |
| `ci-dag.yml`, `ci-portable-autoretry.yml` | *no block* | no | fine |

**Denominator 9; unconditional main-push cancellers = 1 = `docs.yml`.** Exactly as claimed.

Two caveats on the artifact rather than the finding: (a) the note calls its table a "FULL TABLE" of
"9 workflows" but lists **7** — `demo-hot-path.yml` and `merge-gate.yml` are missing; both are
correct, so the conclusion is unaffected. (b) **The fix was never applied.** `docs.yml` still cancels
unconditionally on main push at `b64d893a`. Accurate diagnosis, zero remediation.

### 3.2 ebadf classifier — **CLAIM CONFIRMED, gap is live**

At hermit `b64d893a`: `grep -ci 'bad file descriptor' validate.sh` → **0**;
`scripts/validate-env-block-test.sh` → **does not exist**; `is_environmental_block` still present (4
references) with only `operation not permitted` / permission-denied anchors (3 matches). PR #1566 is
not on main. The classifier still misses EBADF today.

### 3.3 dag-test-steps → hermit binary — **CLAIM REPRODUCES EXACTLY**

Independently parsed `ci/dag/portable.json` + `ci/dag/privileged.json`:

```
47 portable + 8 privileged = 55 steps        (claim: 55 = 47 + 8)     ✅
24 steps invoke cargo = 44%                  (claim: 24/55 = 44%)     ✅
```

An exact reproduction, denominator included. This is the best-evidenced artifact in the subset.

### 3.4 wall-timeout 3×CPU — **CLAIM CONFIRMED (correctly *not* implemented)**

No 3×CPU resolver exists anywhere in `ci-hub/`, `scripts/`, or `agent-utils/`. The refutation
artifact `ai_docs/wall-timeout-idiom-3x-cpu-refuted-20260805.md` is present. The agent correctly
declined to implement the task's *title* because a recorded owner decision in the task *notes*
superseded literal-3×, and the task is parked. **Not-implementing was the right call**; the residual
risk is only that the title still says 3×, so a future agent may re-implement what was deliberately
rejected.

### 3.5 unify-backend-stats-transport — **NOT IMPLEMENTED**

`per-backend-stats-framework` is status **OPEN**, self-described as a tracking umbrella. No
implementation in `hermit/` or `reverie/`. It should not have been on a review list.

---

## 4. Scorecard

| Item | Guard? | Verdict | Denominator |
|---|---|---|---|
| nightly-stress freshness alarm | yes | **REAL** code (9/9 designed cases + positive control; exit-code bound) but **OPERATIONALLY INERT** — 0 call sites, 0 schedules. 2 evasion holes (future-dated row; boolean counts) | 11 plants; real store 5 rows = 4 measuring + 1 no-result |
| dev-hermit queue-depth-1 | config | Config **CORRECT** (0/5 unconditional main-push cancels) but **INERT as a guard** — no lint; planted regression caught by nothing | 5 workflows; 1/5 true queue-depth-1, 2/5 preserve-all, 2/5 no block |
| hermit queue-depth-1 enumeration | no | Claim **CONFIRMED**; fix **not applied** (`docs.yml` still cancels) | 9 workflows, 1 offender |
| ebadf classifier | no | Claim **CONFIRMED**; gap **live on main** | 0 matches for the anchor |
| dag-test-steps → binary | no | Claim **REPRODUCED EXACTLY** | 55 steps, 24 cargo (44%) |
| wall-timeout 3×CPU | no | Claim **CONFIRMED**; correctly not implemented | 0 resolvers found |
| unify-backend-stats-transport | no | **NOT IMPLEMENTED** (task OPEN) | — |

**No artifact in this subset was found to be dishonest.** Every note's factual claim that I could
test held up, including the two that say "I did not implement this." The systematic gap is different
and more useful to know: **of six reviewed items, one is executable guard code, and it is unwired.**
The batch's risk is not false claims — it is that diagnosis is being counted as remediation.

## 5. Recommendations

1. **Wire `check_freshness.py` or the nightly lane stays silently dead.** It is correct code with no
   caller. Install the workflow, or add it to an existing scheduled job, and fix H1/H2 first (both
   are a few lines).
2. **`docs.yml` is a one-line fix** that has been diagnosed twice and applied zero times. Set
   `cancel-in-progress: ${{ github.event_name == 'pull_request' }}`.
3. **Add a workflow-concurrency lint** to both repos. Without it the queue-depth-1 idiom is a
   convention, and the regression it was written to prevent has already happened once
   (dev-hermit run `30864011845`).
4. **Separate "guards" from "research artifacts" on future review lists.** Sending a reviewer to
   plant violations against a document wastes the reviewer and, worse, can produce a green review of
   a thing that does not exist.
5. **Retitle or close `no-hardcoded-wall-timeouts-idiom`.** The title still says 3×CPU; the notes
   rejected it. Titles outlive notes.

## 6. Plant hygiene

All plants were created under `/tmp/ar/` and never inside a repository. After deletion:
`/tmp/ar/wf` removed; `git status --short .github/` in dev-hermit → **empty**; `ci-hub/stress/`
shows only the three files the nightly-stress author left untracked, which pre-date this review.
No repository file was created, modified, or deleted by this review.

## 7. Limitations

- **No egress.** Nothing was verified against GitHub: no run histories, no PR #1566 contents, no live
  observation of concurrency behaviour. Claims about what GitHub *would* do with a given
  `concurrency:` block are read from the YAML semantics, not observed.
- **`compat-envelope.yml`'s `run_id` fallback is read, not executed.** I did not confirm empirically
  that GitHub assigns a distinct group per main run; that is standard documented behaviour, but it
  is inference here.
- Guards outside the CI+MISC subset (cmake-content-hash, oom.group, register-file-hashing, kvm-tty,
  fchown-DBI) were **not reviewed** — owned by other agents per the scoping instruction.
- The two evasion holes were found by targeted probing, not exhaustive fuzzing; absence of further
  holes is not established.
- `agent-utils` pin state could not be checked (`make check-agent-utils-pin` needs egress), so
  "commit `c7992c3` not landed" is unconfirmed rather than refuted.
