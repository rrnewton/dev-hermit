# Validate admission controller: derived exclusivity, not advised

**Task:** `priority-based-ci-planner-owns-the-batch` (owner P0). **Author:** hermit-243. **Date:** 2026-08-04.

## Observable consequence first

Tonight six validate producers were dispatched onto a box where validates must run **solo**, while a
benchmark harness was also running. **Seven validates went RED.** One agent (hermit-sabre) *refused to
run* rather than mint a contaminated receipt, and that refusal is the only reason the contention
surfaced at all. The decision this creates: the "run validates solo" rule must become a **predicate the
machine enforces**, not a sentence in a dispatch that decays within one recycle. It decayed within an
hour tonight.

This controller answers three questions the priority function did not: **how many validates at once
(one), what may run alongside one (nothing box-heavy), and what happens to the second request (queued
with a position, or refused with a reason — never silently admitted).**

## The concurrency limit is DERIVED, not picked

The limit is not a taste call. `experiments/multisect_detcore_misc_20260803/` measured the residual
that concurrency amplifies:

- `test.detcore_misc`'s `vfork_parent_resumes_after_child_exec` is a **load-dependent probabilistic
  deadlock** (reverie `safeptrace` notifier `Died`-before-`commit` bug, amplified since the
  `a8195cfc` notifier regression). It is a **hang rate**, not a pass/fail — a single sample is
  meaningless.
- The rate is a **monotonically increasing function of concurrent box load**: **0–0.6 %** at the clean
  baseline pins, rising to **~16 % at current main's pin (`d973a85b`)** under matched-load stress
  (C=32 per label + ambient fleet load 286–590 on 316 cores). reverie PR #305 only *partially* lowered
  it (23 %→18 %); nothing after the flip restores CLEAN.

Two facts turn that curve into a hard cap of **N = 1**:

1. **No zero-crossing above 1.** The rate never returns to zero after the flip; it only *falls with
   load*. The single lever that drives manufactured-FAILED probability toward its floor is removing
   concurrent load — i.e. running one box-exclusive job at a time.
2. **A false FAILED is permanent.** Receipts are SHA-keyed and nobody re-runs a PR the ledger calls
   failing. So the cost of one contention-induced hang is not a retry — it is a PR silently removed
   from the landable set forever. Against a permanent, one-directional cost, the only defensible
   operating point is the one that minimizes the rate: **N = 1.**

**"Solo" must exclude load generators, not just peer validates.** The residual is driven by *ambient
load* (the experiment amplified it with fleet load, not only with peer copies of the test). A benchmark
harness raises ambient load exactly as a second validate does — which is why tonight's harness
contributed to the seven reds. Therefore the box-exclusive lock's scope is **validate ∪ benchmark**:
both kinds acquire the *same* lock and mutually exclude.

**hermit-250's forthcoming rate-vs-N curve is an input that can only *refine*, never loosen, this.** It
would quantify the slope; it cannot produce a safe N>1 plateau because the measured curve has none.
Accordingly the controller hard-**refuses** `--max >1` until such evidence exists (see below) — raising
the cap is a deliberate, evidence-gated act, not a default.

## Why a predicate, and why it must hold the lock for the child's lifetime

A per-request `GRANT/QUEUE/REFUSE` CLI called once from bash is **inert** — proven with the
safe_ci_dag admission CLI ([[admission-cli-inert-from-bash-needs-run-wrapper]]): the reservation dies
with the CLI process, so two back-to-back requests both GRANT. The only shape that *enforces* is a
wrapper that **holds the reservation for the whole child's lifetime** — exactly how `ci-hub land-lock
run -- CMD` works ([[landing-mutex-ci-hub-land-lock]]): reserve, exec the child under a heartbeat,
release on exit, evidence-reclaim a dead owner. The validate controller is that same proven engine
bound to a **separate** lockfile (`.validate-lock*`, orthogonal to `.landing-lock*` so a validate never
blocks a lander and vice-versa).

## Interface (enforce, not advise)

```
ci-hub validate-lock run --agent A --kind validate|bench --target <sha|bench:name> \
        [--no-wait] [--wait S] [--hold S] [--child-deadline S] -- CMD...
ci-hub validate-lock status        # holder + FIFO queue WITH positions
```

- **GRANT** — lock free ⇒ acquire, exec CMD under heartbeat + bounded child-deadline, release on exit.
- **QUEUE-WITH-POSITION** — default (blocking): print `queued position <k> behind <holder>` and admit
  in FIFO order when the lock frees.
- **REFUSE-WITH-REASON** — `--no-wait` and not immediately grantable ⇒ exit nonzero with
  `REFUSED: box-exclusive lock held by <agent> running <kind> <target>, ~<n>s left; <k> ahead`.
- **Cap** — `--max` defaults to 1 and **rejects >1** ("box-exclusive cap >1 is unproven; the
  detcore_misc residual rate is monotonic in load, experiments/multisect_detcore_misc_20260803 —
  raising N requires hermit-250 evidence").
- Unbounded child-deadline forbidden (inherits land-lock's head-of-line-block guard).

## Verification — BOTH directions (a controller that refuses everything must fail the positive test)

- **Negative / exclusivity fires:** with the box-exclusive lock held, a `--no-wait` request **REFUSES**
  (nonzero, names the holder) and a blocking request reports a **queue position** and is admitted only
  after release. A `bench`-kind holder blocks a `validate`-kind request and vice-versa (shared lock).
- **Positive / non-starvation, N STATED:** **N = 3** legitimate sequential validate runs each GRANT and
  release cleanly with the lock ending FREE — the controller does not wedge the common case. Generalized:
  every FIFO waiter is admitted in enqueue order, bounded by the child-deadline ceiling (no starvation).

## Relationship to the priority function

This is the exclusivity axis the priority note ([[priority-based-ci-allocation-function_20260804]])
lacked. The priority function answers **which** PR is admitted next; this controller enforces **how
many at once and what may run alongside**. Composed: the priority order chooses the next eligible PR,
and `validate-lock run` is the gate every validate producer passes through so the chosen one runs
*alone*. Tonight proved the ordering axis is necessary but not sufficient — allocating by importance
while ignoring exclusivity is what produced the seven reds.
