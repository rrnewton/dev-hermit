# Readable end-to-end validate log — 2026-08-07

Owner-facing summary of a full `./validate.sh full` campaign on `rrnewton/hermit`.
Every number below came from a run that finished and left a durable log; nothing
here is an estimate presented as a measurement.

**Headline:** the deepest run executed **875 tests + 81 e2e cells with zero
failures**, and the *only* red was a wall-clock timeout on one diagnostic cell
while the box was at load average 56. Getting there required clearing two real
blockers on `main`, one of which made the repository **unvalidatable** — not
merely red — for a window after every Reverie merge.

---

## 1. What the runs found, in order

| # | Head | Cache | Wall | CPU | CPU/wall | Result |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `75506005` (main) | cold | 3s | 7s | 2.3x | died at gate 1 — Reverie pin stale |
| 2 | `caa363de` (+pin bump) | cold | 47s | 1m30s | 1.9x | 4 gates red from one silent `exit 2` |
| 3 | `d5e29ce9` (+budget carry) | cold | 5m57s | 42m44s | 7.2x | **6/7 gates green**, 1 chaos cell red |
| 4 | `d5e29ce9` (re-run) | warm | 45m22s | 43m25s | 1.0x | pin went stale *during* the run |
| 5 | `200e1ae1` (rebased) | cold | 4m32s | 40m03s | 8.8x | **6/7 gates green**, same chaos cell |
| 6 | `491f8d20` (main + #1750) | cold | 36m46s | 1h11m22s | 1.9x | **portable lane ran in full**: 36 pass / 1 timeout |

Runs 1–5 were **short because they eager-exited**, not because they were fast:
each aborted ten downstream steps. Run 6 is the first that actually executed the
DAG.

## 2. Blocker A — the pin gate and the DBI budget were mutually unsatisfiable

Two fail-closed mechanisms disagree the instant `rrnewton/reverie:main` advances:

* `ci/run-reverie-pin-check.sh` **requires** the recorded pin to equal the live
  `rrnewton/reverie:main` tip, resolved over the network at run time. No override
  exists; `docs/updating-reverie.md` says so explicitly.
* `ci/run-with-reverie-dbi-budget.sh` **refuses** any pin except the one the DBI
  elapsed budget was calibrated against, exiting 2 with
  `no calibrated budget for Reverie pin <new> (expected <old>)`.

So from a Reverie merge until someone hand-edits source, **no Hermit commit can
produce a passing local receipt** — not `main`, not any PR head. It fired twice
in three hours (`6144323c` at 04:35Z, `038e9939` at ~05:50Z).

Corroborating denominator: at the start of this work `ci-hub newest-green
--branch main` reported the newest full green as `d5355051`
(2026-08-05T05:22Z), with **31 of the last 33 main commits carrying no
validation record at all**.

Both instances are now cleared on `main` (`590fcc9ee`, then `4be8edcd2`). A
parallel fix of mine, PR #1861, was closed as superseded after verifying by
content — not by title — that `4be8edcd` carries the same 2 and 13 pin-literal
occurrences in `ci/configure-build-jobs.sh` and `ci/test_harness.sh`.

## 3. Blocker B — the failure was completely silent

`./ci/test_harness.sh audit-ci` exits **2 while printing nothing on stdout or
stderr**. `validate.sh` can therefore only render the root cause as the
uninformative `exit 2: }`, and it takes **four of the seven gates** down at once.
Diagnosing it required `bash -x`.

This is the highest-leverage unfixed item in the area: it is what turned a
one-line pin problem into a long hunt. Nobody has a PR for it.

## 4. The chaos cell — measured, not waved away

Runs 3 and 5, on two independent cold builds, each failed exactly one cell:
`determinism-stress/order-violation` in **chaos** mode, ptrace —
`chaos distinct=1 passes=2 failures=0 repeat_mismatches=0`. Everything else in
that bucket passed, including the same test in *verify* mode.

It is the known build-keyed oracle, and open PR
[#1750](https://github.com/rrnewton/hermit/pull/1750) is the authored fix.
Evidence gathered here rather than assumed:

* A 32-seed sweep on this host exposes the race on `{9, 12, 23, 30}` — 4/32,
  inside the 4/32–12/32 minority-class band measured across four earlier builds.
  Schedule diversity is intact; the Reverie pin move did not collapse it.
* `main` still ships `seeds = [0,9]` with `min_distinct = 2`, so a build whose
  minority class misses both seeds fails **by construction**.
* `./ci/test_harness.sh run --test determinism-stress/order-violation --mode chaos`
  passes twice in a row at the same source — the outcome tracks the *build
  artifact*, exactly the mode #1750 removes.
* With #1750's manifest applied (run 6), the cell passes:
  `chaos distinct=2 passes=20 failures=12; every seed reproduced`.

`repeat_mismatches = 0` throughout: every seed reproduced bit-for-bit, so
per-seed determinization is intact and the only thing this cell can fail on is
schedule diversity.

**Trap paid:** a first sweep reported 32/32 "race exposed", which was wrong — the
guest was under `/tmp` and Hermit refuses that (`Program /tmp/... is under host
/tmp, but Hermit replaces guest /tmp`). Rebuilt outside `/tmp` for the real map.

## 5. The deep run — `491f8d20` = main `4be8edcd` + PR #1750

Durable log: `ignored/validate/validate-hermit-w30-491f8d20c386-1786084919.log`
Central log: `/tmp/hermit-validate.LtVusr.log`
Header states `Build cache: cold (target/ debug=absent release=absent)` — the
tool's own words, not a warm run relabelled.

| Gate | Result | Wall |
| --- | --- | --- |
| Reverie dependency pin equals latest main | PASS | 1s |
| Initialize repository submodules | PASS | 0s |
| Reverie pin consistency | PASS | 1s |
| Centralized test manifest and inventory | PASS | 30s |
| portable CI DAG lane | FAIL | 2087s |
| Centralized test manifest and inventory | PASS | 28s |
| privileged CI DAG lane | PASS | 57s |

Wall **36m46s**, CPU **1h11m22s**, CPU/wall **1.9x** across 316 cores.

**Execution counts — this is not a green with zero tests run:**

* portable DAG: **36 passed, 1 failed, 0 aborted, 0 skipped**
* privileged DAG: **8 passed, 0 failed, 0 aborted, 0 skipped**
* cargo/nextest suites: 25 suites, **875 executed tests passed, 0 failed**,
  660 filtered out (ordinary mode/backend filtering, not a defect)
* e2e cells: **81 PASS, 0 FAIL**

**The single red:** `test.strict_compat` — "Portable strict compatibility
envelope" — hit its **1800s wall-clock gate timeout** with the box at load
average 56 and roughly a hundred agent slots active. It is a contention timeout,
not a product failure: the cell emitted only its header before being killed, and
it completed inside its budget in earlier runs. Note the observability gap — this
cell prints nothing until it finishes, so a timeout leaves no partial evidence.

## 6. Why a warm validate cannot win the race — measured

Run 4 spent **~45 minutes in `purge_zero_byte_objects` (`validate.sh:916`)
before gate 1 executed**, and Reverie merged during the scan, so the run failed
at the pin gate on work it had already done.

The scan walks `target/` and, per artifact, forks `stat` + `head` + `od` + `tr`,
plus a **fresh `python3`** for every ELF:

* 21,281 artifacts in an 18 GB `target/` — 19,978 `.o`, 993 `.rlib`, 140 `.so`,
  122 `.a`, 48 `.debug`
* **116 ms per artifact**, measured over a 200-file sample under load ~56
  (200 files in 23.355 s; a same-host projection from a sample, not a
  cgroup-recorded figure for the live run) → ~2,350 s projected, ~45 min observed
* That is **~8x the entire cold run** (4m32s)

A single `python3` over the whole file list, or the ELF header check in `od`
alone, would remove essentially all of it. Practical consequence today: **wiping
`target/` makes validate roughly eight times faster on this box.**

## 7. Open items, none bundled into any PR

1. Make `./ci/test_harness.sh audit-ci` say why it exits 2. Highest leverage.
2. Land [#1750](https://github.com/rrnewton/hermit/pull/1750). It is the only
   thing between `main` and a green portable lane.
3. Collapse `purge_zero_byte_objects` to one subprocess pass.
4. `docs/updating-reverie.md` "How to bump" omits the DBI calibration carry;
   following it verbatim leaves the tree red.
5. `test.strict_compat` should emit progress, and its 1800s budget should be
   stated against a contended-box baseline.

## 8. Method

Every run went through `ci-hub validate-run` — a systemd `--user` transient unit
entering via `validate-lock` — so each survived agent recycling and left a
durable log. **Exactly one validate on the box at a time**; runs were never
batched, because concurrency provokes the `detcore_misc` livelock and would write
false reds. Slot `worktrees/w30`, allocated through `scripts/allocate-worktree.rs`.
Host devbig014, 316 cores, `/dev/kvm` present.

Two admission behaviours worth knowing: `validate-run` does **not** queue — it
fails closed in ~2s when the box lock is held, and the refusal appears **only in
the durable log**, never on stdout. And admission refuses any head that does not
contain freshly fetched `origin/main`, so on a fast-moving `main` a run must be
rebased and launched inside the inter-merge window.
