# Mechanisms that exist but nothing invokes — sweep, 2026-08-08

A mechanism nobody invokes is indistinguishable from one that does not exist,
**except that it inspires false confidence.** Every instance below was believed
to be providing protection.

This is the companion to *write the mechanism, not the warning*. That lesson is
about the producer. This one is about the other end: **verify the consumer.**

The shape surfaced five times on 2026-08-08 in five unrelated subsystems, which
is what prompted the sweep:

| already known | how it was inert |
|---|---|
| `--self-test` | reachable only via `make validate-self-test`, which nothing calls |
| `tier_evidence.py` | 18 passing tests, zero callers; read 0 of 6 on its first real run |
| `ready-to-merge` label | no workflow consumes it; no auto-merge machinery exists |
| producer predicate | declared but inert; 2 of 4 writers emitted nothing |
| two envelope rows | attributed to provenance work that had since landed |

---

## Method, and why the method is the point

**A grep hit is a candidate, not an instance.** Two agents were caught on
2026-08-08 by exactly that gap — a `grep -c` that counted comments as
deployment, and a static scan of 38 candidates whose first spot-check was
refuted. This sweep therefore separates generation from verification and
reports only what survived verification.

**Establish the consumer surface first, by reading it.** For this repository
the parent's real invocation surface is, from `.github/workflows/dev-hermit-ci.yml`:

- `python3 -m unittest discover -s scripts -p "test_*.py"`
- `ci-hub/tests/run_python_suites.py` over the eight directories in `DEFAULT_SUITES`
- `ci-hub/tests/documented_commands.py`
- tick-hub gate `cmd:` lines, `.githooks/`, Makefile prerequisites
- a handful of individually-named scripts

Anything unreachable from that set is not run, whatever else mentions it.
Corpus: 6,320 parent-owned tracked text files; submodules and `agent-utils`
excluded.

**Candidate generation** (raw counts, deliberately *not* reported as findings):

| class | rule | candidates |
|---|---|---|
| A | Makefile targets with no `make <t>` call and no prerequisite use | 3 of 41 |
| B | Python modules with tests and zero non-test consumers | 16 |
| C | executables referenced by no other file | 26 of 460 |

---

## Candidates that were NOT instances

Recorded because the refutation rate is the reason for the method.

- **`doctor-core`, `doctor-full`** — flagged by class A, **refuted**.
  `README.md:105` instructs a human: *"Run `make doctor` … or select
  `doctor-core`, `doctor-full`, or `doctor-qemu`."* A documented human runbook
  **is** a consumer; the generator only looked for machine invocation. 2 of 3
  class-A candidates fell here.
- **Three substring collisions**, all refuted on inspection: `parity_gate`
  matched a JSON *key* in an experiment's `metadata.json`; `retry_class` matched
  the unrelated identifier `validate_sh_retry_classifier`; `sabre_reach` matched
  the *directory* `experiments/sabre_reach_allowlist_20260806`.
- **`experiments/**` executables** — excluded by design, not orphaned. A durable
  experiment is a reproducibility artifact; nothing is supposed to invoke it on
  a schedule.

---

## Verified instances

### Group 1 — six shell brackets nothing runs *(highest confidence)*

Parent CI names shell tests **one by one**, and names exactly two. Of ten
bracket scripts under `scripts/`:

| script | runner |
|---|---|
| `release-worktree-target-scope-test.sh` | `dev-hermit-ci.yml` |
| `test-prepare-demo08-calibration.sh` | `nightly-demo-sweep.yml` |
| `allocate-worktree-lease-message-test.sh` | **none** |
| `allocate-worktree-released-rebind-test.sh` | **none** |
| `allocate-worktree-repair-test.sh` | **none** |
| `check-worktree-registry-test.sh` | **none** |
| `hermit-health-staleness-test.sh` | **none** |
| `release-worktree-orphan-test.sh` | **none** |
| `release-worktree-test.sh` | **none** |

Checked against workflows, `Makefile`, `ci-hub/tests`, `tick-hub.yaml` and
`.githooks`.

**And they work.** Three spot-run, all `rc=0` with counted assertions:
`RESULT: PASS`; `PASS (2/2 correct accepted; branch…)`; `PASS (exact-root ascent
refused; …)`. Functioning brackets over the worktree allocator and release
paths — written, committed, passing, never executed.

> **KEEP AND WIRE.** These cover the allocator and release-worktree paths, which
> are where slot corruption originates, and they already pass, so wiring is
> cheap and cannot fail the build on day one. Wire them as a **discovered set**
> (`scripts/*-test.sh`), not by adding seven more named lines — the named-one-by-one
> pattern is precisely what let seven accumulate unnoticed.

### Group 2 — eight `ci-hub` guards with no consumer

All eight have tests that **do** run (their suites are in `DEFAULT_SUITES`) and
**zero** production callers: no workflow, no tick-hub `cmd`, no Makefile, no
githook, no non-test importer, no non-collision mention anywhere in the corpus.

**Library, no importer — inert by construction:**

| module | lines | purpose |
|---|---|---|
| `ci-hub/validate/gate_completeness.py` | 111 | "did all declared gates genuinely run?" |
| `ci-hub/validate/retry_class.py` | 224 | typed, fail-closed classification of a validate outcome |
| `ci-hub/health/holder_liveness.py` | 151 | typed liveness for a held resource; fail-closed release verdict |

**CLI, no invoker — runnable on demand, inert as a standing check:**

| module | lines | purpose |
|---|---|---|
| `ci-hub/validate/parity_gate.py` | 220 | "**STANDING** cross-backend detlog parity gate" |
| `ci-hub/health/drain_reconcile.py` | 339 | open PRs no landing tracker accounts for |
| `ci-hub/health/landing_composition.py` | 279 | bind "awaiting landing" to repository state, not a tag |
| `ci-hub/validate/sabre_reach.py` | 168 | a SaBRe cell that patched nothing is a ptrace cell mislabelled |
| `ci-hub/landing/task_pr_join.py` | 475 | which task owns which open PR, and the gaps |

1,967 lines of guard logic with passing tests and nothing invoking any of it.

> **WIRE, in this order:** `gate_completeness.py` and `retry_class.py` first —
> they are libraries on the receipt path, the place where an unread verdict does
> the most damage, and a library with no importer cannot fire even by accident.
> `parity_gate.py` next: its own docstring calls it STANDING, so the gap between
> what it claims and what runs is already documented in its source.
>
> **DO NOT wire the rest reflexively.** `drain_reconcile.py`,
> `landing_composition.py` and `task_pr_join.py` are analysis tools whose output
> needs a reader; a tick-hub gate with no owner is how this fleet accumulated the
> alarms it is currently deleting. Leave them as on-demand CLIs and **document
> them** — that converts them from invisible to discoverable at near-zero cost.
>
> **`sabre_reach.py` — I checked, and did not get to delete it.** I expected this
> to be the one clear removal: an audit whose subject had moved on. Measured
> instead — `compat-envelope/scorecard.csv` still carries 7 SaBRe rows, so its
> premise is live and the mislabelling it detects is still possible. **KEEP**, and
> treat it as a wiring candidate behind the receipt-path pair. Recorded because a
> sweep that finds nothing to delete should say so rather than manufacture a
> deletion to look decisive.

### Group 3 — one orphan Makefile target

`restore-primary-freshness` → `scripts/primary_checkout.py freshness
--restore-safe`. Mentioned **nowhere** outside the `Makefile` — not even
`README.md`, unlike its `doctor-*` neighbours.

> **KEEP, DOCUMENT, DO NOT WIRE.** It is a *repair* action, and auto-repairing a
> primary checkout from CI is exactly the class of automated mutation this
> repository refuses elsewhere. The defect is not that CI does not run it; it is
> that nobody can invoke what nobody knows exists. One README line fixes it.

---

## Checklist for a future contributor

Before believing a mechanism protects you:

1. **Name its consumer, and open the file.** Not "there is a make target" —
   *which* workflow line runs it.
2. **A grep hit is a candidate.** Expect collisions: a JSON key, a directory
   name, a longer identifier. Three of three hits in this sweep were collisions.
3. **Distinguish a human consumer from a machine one.** A documented runbook
   step is a real consumer; an undocumented target is not, even though both are
   "not run by CI".
4. **A library with no importer cannot fire.** For a module, the consumer test
   is an import or an exec of its path — the existence of a `main()` is not one.
5. **Tests passing is not the mechanism running.** `tier_evidence.py` had 18
   green tests and zero callers.
6. **Named-one-by-one lists rot.** Seven brackets accumulated because the runner
   enumerates by hand. Prefer a discovered set with a counted floor.
7. **Ask whether it should exist at all.** Not every orphan should be wired. A
   checker nobody wanted costs less to delete.

## Reproducing this sweep

Generation scripts are throwaway; the durable parts are the corpus definition
and the consumer surface above. Re-derive with: candidate classes A/B/C over
`git ls-files` minus submodules, then verify each candidate against the consumer
surface **by opening the referencing file**, not by counting matches.

*Produced by `fleet-forensics` for task
`sweep-for-mechanisms-that-exist-but-have-no-consumers`. Every instance above was
verified individually; refuted candidates are listed with their refutation.*
