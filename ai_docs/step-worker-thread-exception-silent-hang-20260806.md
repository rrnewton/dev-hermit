# A step supervisor that dies silently is worse than one that fails — closing the hang

**Task:** `step_worker_thread_exception` (P1) · **Date:** 2026-08-06 · **Author:** hermit-design
**Repo:** `rrnewton/agent-utils` · **Branch:** `worker-thread-exception-fails-loudly` · **SHA:** `0cb9576c7890f3eea90cb32eaf57f97a0bb83ab9`
**Base:** `570e78655e4cbfd398748b278252bfbaf4cc5930` · **Worktree:** `scratch/au-worker-exc`
**Local only.** No egress (GitHub 403), so nothing pushed and no PR opened.

---

## 1. The premise, reproduced before anything was changed

The task's repro is exact. At base `570e786`, adding one keyword argument to the
`StepOutcome.failed` call in `py/safe_ci_dag_runner/scheduler.py` that the signature in
`protocols.py` does not accept:

| | command | result |
| --- | --- | --- |
| **control** (unmodified) | `pytest tests/test_scheduler.py::test_dep_failure_skips_dependent ::test_eager_exit_aborts_inflight_step` | **2 passed in 0.37s** |
| **planted** | same command, 60s outer timeout | **exit 124, ZERO output** — no traceback, no progress, nothing |

The `TypeError` is raised on a daemon supervisor thread. Nothing joins that thread, nothing
inspects its exception, and the greedy ready-set loop's break condition
(`not running and (stop or len(done) + len(skipped) >= len(steps))`) can never be satisfied
once a step is in neither `done` nor `skipped`. The loop spins at 20 Hz forever.

**Why it matters past the typo.** This is the shape of any unexpected exception on the step
path, and an indefinite hang is the worst available failure mode for a CI supervisor: it burns
the lane's entire wall budget and emits nothing to triage from. A silent kill is
indistinguishable from a mystery failure.

---

## 2. Two structurally different leak windows, not one

The defect is usually described as "the tag stays in `running`". That is only half of it, and
the missing half is the half that bit me (§4).

| window | where | `running` | `done` | consequence |
| --- | --- | --- | --- | --- |
| **A — before bookkeeping** | anywhere from `prepare_command` through the child's exit | still holds the tag | absent | loop wedges **and** the step's named resources + CPA core budget are never returned |
| **B — inside the locked block** | after `_retire`, before `done[tag] = outcome` — **the discovering case** | already cleared | absent | loop wedges; the tag is in **neither** set |

Window B is why the natural fix is insufficient.

---

## 3. The fix: two independent layers, in both engines

### Layer 1 — per-supervisor guard

`_run_step` became a thin wrapper around `_run_step_body`, running it under
`except BaseException` (Python) / `catch_unwind` (Rust). An escaping exception becomes a failed
`StepOutcome` whose `reason` **names it**, with the full traceback on **both** stdout and
stderr — a caller at `verbosity=0` with stdout captured must still learn the runner broke.

`BaseException` rather than `Exception` is deliberate: a bare `SystemExit` raised on a worker
thread ends that thread exactly as silently as a `TypeError`.

### Layer 2 — dead-supervisor sweep

Each pass of the ready-set loop asks whether any launched supervisor has finished without
recording an outcome, and records a loud failure if so. It is a genuine backstop: it fires even
when layer 1 does not run at all.

### Supporting invariants

* **`retire()` is idempotent.** Both the normal path and the crash path call it. Without the
  guard, a crash *after* a normal release would release twice, drifting `resource_avail` above
  its declared cap and `cores_used` below zero — converting a loud bug into quiet
  over-subscription.
* **The crash handler's own reporting is guarded**, so a fault while reporting cannot re-lose
  the thread it was called to save (found by a test, §4).
* **Rust `lock_shared()` recovers from mutex poisoning.** A supervisor that panics under the
  guard poisons the mutex, and `lock().unwrap()` would then panic in *every* other thread —
  burying the first cause under a cascade. No Python counterpart; `threading.Lock` does not
  poison.
* **Rust spawn-failure path leaked `cores_used`** — it released the named resources but never
  decremented the core budget. Pre-existing, fixed in passing by routing through `retire()`.
* **The monitor thread now warns when it dies.** It is the only enforcer of the per-step
  CPU-time budget; its death does not hang anything, but it silently disabled `cpu_timeout`
  enforcement for the rest of the step. It warns and does **not** fail the step — the wall
  timeout remains a live backstop.

---

## 4. What I got wrong, and how it was caught

Both errors were caught by tests I had written to check something else. Neither was caught by
reasoning about the code.

**(a) Layer 2's first predicate was `tag in running` — and it missed the discovering case.**
Mutation M1 (layer 1 removed) still **hung for the full 30s deadline** on
`test_discovering_case_typeerror_from_stepoutcome_failed`. Window B (§2) leaves the tag in
neither `running` nor `done`, so a `running`-keyed sweep cannot see it. The predicate is now
`(launched) AND (finished) AND (no outcome)`, which covers both windows.
`test_dead_supervisor_sweep_catches_a_crash_after_the_tag_left_running` is bound to exactly
this, and mutation **M4 reproduces the bug I shipped first**.

**(b) The crash handler could itself raise and re-lose the thread.** The reporting-tail test's
anti-vacuity counter read `2 == 1`: its plant matched the substring `PASS`, which also appears
in the crash handler's own traceback dump (it quotes the emit call site), so the plant
re-entered the handler. The over-firing was a test bug — but it demonstrated that an exception
in `_record_lost_supervisor` propagates straight back out of the thread. The reporting is now
guarded, and the plant matches by line prefix.

---

## 5. Determinism

The DAG runner is a wall-clock CI scheduler, not detcore; the property at issue is that for a
given DAG and a given set of step exit codes, `RunResult.ok` / `outcomes` / `skipped` are
determined. The change preserves it:

1. **No new scheduling decision.** `retire()` performs exactly the state mutations of the code
   it replaced, guarded to happen once. Dependency gating, resource gating, the core-budget
   gate and LPT order are untouched.
2. **On every input where the old code terminated, behaviour is observationally identical.**
   The `except` branch never runs, `retire()`'s guard never trips twice, and the sweep always
   returns empty — so the reachable state sequence is unchanged. The crash path is reachable
   only on inputs for which the old code had *no* defined behaviour (it never terminated).
3. **The new liveness poll cannot fire spuriously.** A supervisor writes `done[tag]` inside the
   same critical section that clears `running`, and so does layer 1, so no intermediate state is
   observable under the lock. CPython sets `_is_stopped` only after the target returns, and
   Rust's `JoinHandle::is_finished()` becomes true only after the closure returns; therefore
   `not alive ⟹ an outcome exists` on the healthy path. `Thread.start()` blocks until the
   thread is running and registration into `step_threads` happens after it, so a just-launched
   step is never read as dead.
4. **Multi-loss reporting is ordered by tag** (`sorted(...)`), not by thread-completion order,
   so the recorded outcome sequence does not depend on scheduling.

---

## 6. Evidence

Every mechanism was mutation-checked. A guard with no failing mutant is indistinguishable from
an inert one.

| mutation | expected | observed |
| --- | --- | --- |
| M1 · layer-1 guard removed | crash tests lose the exception name | **6 failed** (2.08s — layer 2 carries it) |
| M2 · layer-2 sweep removed | layer-1-disabled test hangs | **3 failed, 60.96s** (deadline hit) |
| M3 · `retire` idempotence removed | resource double-release | **1 failed** |
| M4 · layer-2 predicate regressed to `running`-keyed | window B escapes | **1 failed, 31.01s** (deadline hit) |
| M5 · monitor guard removed | degradation goes silent | **1 failed** |
| RM1 · Rust `catch_unwind` removed | reason loses the fault name | **3 failed** (0.40s — layer 2 carries it) |
| RM1+RM2 · **both** Rust layers removed | **hang** | **4 failed at exactly 30.00s** — `scheduler HUNG` |
| RM3 · Rust `retire` idempotence removed | double-release | **1 failed** |
| RM4 · Rust poison recovery removed | cascade | **1 failed** |

Both directions are bracketed in the test suites themselves: a planted fault must produce a
`SUPERVISOR CRASH` outcome, **and** a clean run and an ordinary non-zero exit must not. Without
the negative half, "the marker appeared" would not prove the marker discriminates.

Runs are joined against a 30s deadline, so the regression surfaces as a failed assertion rather
than wedging the suite — which is the only way a test for "must not hang" is usable in CI.

**Full gate at `0cb9576`:**

```
make check   -> mypy: Success, no issues in 85 source files
                cargo clippy --release --workspace -- -D warnings: clean
make test    -> 335 passed (python)   [was 334; +16 new, -15 pre-existing recount]
                87 passed, 0 failed (rust, across 8 binaries)
```

Host: devbig014 (316 cores, shared, concurrent tenants). Timings are wall-clock on a loaded
box; the deadline assertions are ~100× the clean runtime, so load does not make them flaky.

---

## 7. Not established

* **Nothing pushed, no PR, no CI.** GitHub egress is 403, so this is a local branch only. The
  hosted gate has never seen it. Landing needs the agent-utils direct-to-main path
  (serialize → full intra-agent-utils validation → push → repin), plus the dual review this
  task specifies.
* **The Rust engine is not the live path.** The validate runner resolves to Python, and there
  is no engine-parity test, so the Rust mirror is verified only by its own suite — it has never
  run a real DAG in anger.
* **Rust layer 2 has no dedicated isolating test.** Layer 1 cannot be disabled from a Rust test
  the way `monkeypatch` disables it in Python. Its evidence is the RM1 vs RM1+RM2 pair
  (terminates in 0.40s with layer 2 alone; hangs to the deadline without it), which is mutation
  evidence rather than a standing regression test. A test-only feature gate would close this.
* **A supervisor that HANGS rather than dies is still not covered.** Both layers key on a thread
  that *ended*. A supervisor wedged inside `reap()` or blocked on a lock is invisible to them,
  and the ready-set loop would still spin. That is a different defect with a different fix (a
  per-step supervisor deadline); it is not addressed here.
* **`_pump`'s `except Exception: pass` is unchanged.** It swallows every reader-thread error,
  including genuine bugs. The swallow is deliberate and documented (a broken/orphan-held pipe
  must not crash the supervisor), and making it warn risks noise on every legitimately-broken
  pipe, so I left the semantics alone rather than change them under this task.
* **A crashed step contributes no `step_profile_rows` entry** when the crash precedes the row
  append, so it is absent from the profile CSV. The outcome is loud, but the measurement row is
  missing; the schema is caller-owned, so synthesising a partial row risked breaking writers.
* **The `retired` set grows monotonically** for the life of a run (one small string per step).
  Bounded by the step count, so not a leak in practice, but it is not pruned.
