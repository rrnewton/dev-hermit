# safe-ci-dag-runner library mode: audit + design

**Task:** `safe-ci-dag-runner-library-mode` — "expose safe-ci-dag-runner as a
LIBRARY (not just a CLI) so validate calls it in-process with typed results, and
keep the Rust+Python implementations in sync past cross-check."

**Author:** hermit-226, 2026-08-04. Method: source trace + live execution, not
grep. **Bases:** agent-utils checkout `7d4ed14` (= current parent gitlink);
`origin/main` = `7c532d4` (checkout is **7 commits behind**, all
`pr-landing-planner` — the runner core is identical).

---

## TL;DR — the premise is ~70% already satisfied

Library mode as literally scoped **already exists in both engines.** This is an
"establish what you have before acting" outcome (the same lesson that governs the
sibling task): do not re-implement it.

| DELIVER item | Status | Evidence |
|---|---|---|
| Typed library API: run a DAG, run a node, query results | **EXISTS (both)** | Rust `lib.rs` `pub use scheduler::{run_dag, run_dag_boxed, run_dag_boxed_ordered}` + typed `RunResult`/`StepOutcome`/`DagConfig`/`Step`; Python `__init__.py` exports `run_dag`, `RunResult`, `StepOutcome`, `DagConfig`, `Step`, … |
| CLI reimplemented on top of the library (one code path) | **EXISTS (both)** | Rust `main.rs` is 8 lines → `cli::run(&args)`; `cli.rs` `run` (L1521) → `run_dag_boxed_ordered`; "run a node" = `run_single_step` (L983) builds a 1-step DAG + `run_dag_boxed`, or `--only` filter. Python `__main__.py` → `cli`. |
| Cross-check tests passing | **GREEN** | `python3 cross/differential.py --tool safe-ci-dag-runner --random 10`: **270 checks across 27 fixtures agree**; `capabilities` byte-identical live. |
| Demonstration of in-process consumption | **PARTIAL** | Python: live demo below works. Rust: only the CLI itself consumes the lib in-process; **no external in-process Rust consumer exists** (`boxing_smoke.rs` spawns the *binary* as a subprocess). |

Live Python in-process consumption (verified 2026-08-04):

```python
from safe_ci_dag_runner import dag_from_json, run_dag, RunResult, StepOutcome
cfg = dag_from_json(open('examples/01-linear-chain.json').read())
r = run_dag(cfg, jobs=2)          # -> typed RunResult
assert r.ok and all(isinstance(o, StepOutcome) for o in r.outcomes)
```

Also already landed (not to be redone): `0eb4203` (#7) added **cpu_timeout
enforcement in both engines** + the `capabilities` subcommand + the
`ENFORCEMENT_CAPABILITIES` byte-identical manifest — the recurrence guard for the
historical Rust-silently-skips-cpu_timeout gap.

---

## The real second-half finding: what the cross-check actually covers

**The differential is a CLI black-box subprocess differential. It never imports
either library API.** `cross/differential.py` invokes `python3 -m
safe_ci_dag_runner` (Python) and the compiled Rust *binary*
(`rs/target/release/...`, falling back to the stale committed `rs/bin/...`), and
diffs only *observable CLI surface*:

- `list`/`ascii`/`dot`/`json`/`yaml` stdout **byte-identical**;
- `run` at **-j>1**: exit code **only** (eager-exit races on timing);
- `run` at **-j1**: exit code **+ passed/failed/aborted/skipped counts** (parsed
  from stderr, not the typed objects);
- `--max-mem` chosen `-j` + modeled footprint; `--only`/sweep error exits;
- profile-store **CSV filenames + header row + line-ending** (data rows and the
  boxing-only `cpu.*` columns are explicitly *out of scope*);
- `--version`/`--help`/`capabilities` byte-identical.

**What this means for library mode — the trap the dispatch named
("a cross-check that compares less than it appears to"):**

1. The typed `RunResult`/`StepOutcome` **structure and field semantics are NOT
   cross-checked field-by-field.** Only what leaks to stdout/stderr (the -j1
   counts, the exit code) is compared. A new typed field added to Rust
   `RunResult` but forgotten in Python — or given divergent semantics that don't
   change CLI stdout — **passes the differential silently.**
2. Boxing/enforcement **behavior** is deliberately out of scope (env-dependent);
   only the *declared* `capabilities` manifest is byte-checked, plus per-language
   boxing smoke tests. The cross-check proves the two engines *claim* the same
   guards, **not** that a boxed run *produces* the same enforcement result.

So: designing library mode "to the existing cross-check" would be a mistake. The
differential is an adequate contract for the **CLI**, but it is **not** the sync
mechanism a growing **library** API needs. This is the concrete deliverable of
the task's second half.

---

## Design

### D1 — Close the library-API cross-check gap (the load-bearing one)

Add a **library-level differential** so the typed surface can't diverge silently.
Two viable shapes; recommend (a):

(a) **Structural projection into the CLI black-box.** Add a hidden/diagnostic CLI
    verb (e.g. `run --emit-result-json`) that serializes the *full typed
    RunResult* (ok, wall_s, every StepOutcome field, skipped, and the
    step_profile_row *schema*) as canonical JSON. Then the existing subprocess
    differential gains one comparison that covers every typed field, reusing the
    byte-identical machinery already trusted. Zero new harness; every library
    field becomes cross-checked the moment it is projected. **Contract to
    enforce: every typed RunResult/StepOutcome field must appear in the emitted
    JSON** — that projection-completeness is what keeps the two libraries in sync.

(b) A native in-process differential (pytest imports Python lib; a Rust test
    emits its RunResult; compare). Rejected as primary: two harnesses to keep
    aligned, and it re-introduces the "compares less than it appears" risk at a
    new seam.

### D2 — Typed run posture on RunResult (the one genuine API increment)

Today `RunResult` = `{ok, wall_s, outcomes, skipped, step_profile_rows}`. It does
**not** tell a caller, as a typed value, whether the run was actually
**boxed/enforced** or **downgraded to best-effort unboxed**. That posture lives
only in: the `--allow-cgroup-failure` *input* flag, a stderr *warning* string,
and the stringly-typed `enforcement_kind` key buried inside
`step_profile_rows: Vec<BTreeMap<String,String>>`. This is exactly the dispatch's
"a flag nobody reads."

**Add a typed posture** to `RunResult` in both engines, e.g.:

```
enum EnforcementPosture { Boxed, BestEffortUnboxed }   // + reason when downgraded
```

so `run_dag_boxed(...).enforcement` is a first-class value a rust-script validate
driver can assert on ("refuse to accept a portable result that ran unboxed unless
the caller opted in"). GitHub portable's `--allow-cgroup-failure` posture becomes
an inspectable field, not an opaque flag. **Both engines + D1 projection + green
270-check baseline required.**

### D3 — cpu_timeout authoring visibility (hermit-repo, not agent-utils)

The dispatch's "0 of 47 portable.json nodes author cpu_timeout" is a **graph
authoring** gap in `hermit/ci/dag/portable.json`, not a runner-library gap: the
library already types `Step.cpu_timeout: i64` (0 = unset) and *enforces* it where
boxed. Making the omission loud is a **lint/validate check over the graph**
(belongs with hermit-231b's timeout/boxing family), consuming the typed
`Step.cpu_timeout` the library already exposes. Out of scope for agent-utils.

### D4 — validate-in-process demonstration

Python in-process consumption works today (demo above). The Rust in-process path
that the *future rust-script validate* will use has **no example** — the runner's
own CLI is the only in-process Rust consumer. A small `examples/`/test that an
*external* caller links the crate and calls `dag_from_json` + `run_dag_boxed` +
inspects the typed `RunResult` (+ D2 posture) is the honest "demonstration" and
the template for the eventual `hermit` rust-script validate driver.

---

## Disposition / constraints for implementation

Not landing runner code this turn, deliberately:

- **Base is stale:** the canonical checkout is 7 behind `origin/main`; a real
  change must branch from fetched `origin/main`, not this pin.
- **Two-engine + cross-check discipline:** D1+D2 span Rust *and* Python and must
  keep the 270-check differential green *and extend it* (D1). That is a
  carefully-validated multi-step change, not a stale-base one-shot.
- **agent-utils serialize-one-change rule** (AGENTS.md): confirm no other
  agent-utils change is in flight before opening this queue.

Recommended order: **D1 (projection + coverage) → D2 (typed posture, now covered
by D1) → D4 (example) → D3 (hand to hermit graph-lint owner).** Each gated on the
extended cross-check green.
