# safe-ci-dag-runner silent-failure audit — full inventory

**Upstream issue (canonical discussion + any later updates):**
[rrnewton/agent-utils#21](https://github.com/rrnewton/agent-utils/issues/21) —
*"ungrantable scarce-resource capacity is a permanent silent 0%-CPU sleep, and dropped
top-level DagConfig fields substitute defaults with no report"*.
The two mutation-verified findings blocks are
[S1–S9](https://github.com/rrnewton/agent-utils/issues/21) (sol-dag, recorded 2026-08-07) and
[N1–N8](https://github.com/rrnewton/agent-utils/issues/21#issuecomment-5223758974)
(sol-loc, 2026-08-08).

This file is the in-repo copy so a reader on a fresh checkout, or offline, sees the whole
inventory without network access. Issue 21 remains the place to *discuss*; this file is the
place to *find*. If they disagree, the issue is newer.

> **No staleness front-matter, deliberately.** `ai_docs/reference/README.md` defines the opt-in
> block with `tracks_repo: hermit|reverie`. This document tracks **agent-utils**, which is not a
> permitted value, so adding the block would feed `check-staleness.rs` a repo it cannot resolve.
> Docs without front-matter are left alone by design. Adding a malformed block to look
> well-governed would be precisely the class of defect this audit is about.

- **Audited revision:** `agent-utils` @ `32cbf16f983dc9b2d902a1b28b7f92dcc53759e5`
- **Component:** `rs/safe-ci-dag-runner`
- **Status:** inventory only — **0 of 17 findings fixed** as of 2026-08-08
- **Acceptance test for a fix:** the mutation harness reports **20/53 refused** today; a
  completed fix reaches 53/53 with no regression in the 20 that already pass

---

## Why this audit exists

The owner's standing requirement (2026-08-07): *"We do not want ANY silent failure. Everything
in the schema needs to be STRICT. Anything wrong is an IMMEDIATE CLEAR ERROR."*

The crate **already has** a "No Silent Failure" convention and cites it by name — the
degraded-cgroup warning in `run_dag_boxed_ordered` (`scheduler.rs:895-901`) and
`report_profile_written` (`cli.rs:1091-1106`). So this is **closing gaps in an existing
convention**, not imposing a new one. That framing matters when arguing each fix.

## Denominator

A partial audit reported as complete is itself a silent failure, so the coverage is stated
before the findings.

| | |
|---|---|
| Schema fields | **32** (11 root/`DagConfig` + 13 step + 8 hint) |
| Fields exercised by ≥1 planted mutation | **32 / 32** |
| Planted mutations | **53** across 7 shapes |
| Refused with a named error | **20 / 53 (38%)** |
| Silently accepted | 28 · starved 3 · aborted 1 · miscounted 1 |
| Findings | S1–S9 (9) + N1–N8 (8) = **17** |
| Findings fixed | **0** |

| mutation shape | refused with a named error |
|---|---|
| wrong type | **16 / 16 — already strict** |
| missing required | **4 / 4 — already strict** |
| unknown key | 0 / 7 |
| out of range | 0 / 16 |
| accepted-then-discarded | 0 / 3 |
| parses-and-no-ops | 0 / 2 |
| graph validity | 0 / 5 |

## What is already correct — do not rebuild it

**Type validation is genuinely strict**, with 16/16 mutation evidence. `opt_int` / `opt_float` /
`opt_bool` / `opt_str_list` / `opt_str_int_map` / `opt_str_str_map` (`io.rs:150-244`) each reject
the wrong JSON type with a named message; `Value::Bool` is explicitly refused where a number is
wanted; `StepClass::from_value` rejects an unknown classification. Observed messages:

```
field 'default_step_timeout' must be an integer
steps[0].hint.classification: unknown value 'gigantic'
steps[0].env.K: must be a string
steps[0].hint: expected an object, got list
```

**`cgroup.rs:853-864` is the shape every fix should copy.** It writes `cpu.max`, **reads it
back**, compares against the exact expected string, and on mismatch emits
`ERROR: step <tag> cpu.max mismatch: expected X, got Y` and exits 1. Write → verify → named
refusal. Note it uses `unwrap_or_default()` on the read-back and is *still* fail-closed, because
`"" != expected`. That is exactly what separates a safe defaulting read from an unsafe one — see
**N4**, where the default **is** the success value.

---

## Findings S1–S9 — parser, model, scheduler, CLI flags

*Recorded by sol-dag, 2026-08-07. Line numbers at `32cbf16`.*

**S1 — unknown keys are silently ignored at every level.** `grep deny_unknown_fields` across the
crate returns nothing and the parser is hand-rolled: `dag_from_value` (`io.rs:290-331`) only ever
asks for keys it knows. Any unrecognised key at root, in a step, or in `hint` is dropped without
a word, so `resource_cap` (singular), `default_step_timout`, or `resources` placed at step top
level instead of under `hint` all parse cleanly and yield the default. The doc comment at
`io.rs:272` claims *"strict field and type validation"* — the type half is true, the field half
is not; fix the overclaim with the code.

**S2 — three `DagConfig` fields are never parsed at all**, via `..DagConfig::default()` in the
loader itself (`io.rs:326-329`): `default_step_mem_cap_bytes`, `default_step_cpu_count`,
`default_step_cpu_timeout`. A DAG file that sets any of them is silently ignored. Whatever is
decided (parse them, or refuse them as unknown keys), *accept the key and discard the value* is
the one option that must not survive.

**S3 — `res_free` fuses absent with zero** (`scheduler.rs:102-107`):
`sh.resource_avail.get(r).copied().unwrap_or(0) >= *n`. A cap that was never declared reads
identically to a cap deliberately set to 0. See also **N3** — this defect is duplicated.

**S4 — a dangling dependency is a permanent silent starve**, and is probably the most likely
trigger in practice. `deps_known` (`scheduler.rs:98-100`) never validates that a dep string
resolves to a real step tag. A typo'd or renamed dep can never enter `sh.done`, so the step is
unlaunchable forever. Renaming a step and missing one referrer is a routine edit.

**S5 — duplicate step tags silently collapse** (`scheduler.rs:182`): last one wins and the other
vanishes. See **N2** for the measured observable, which is worse than "a step vanishes".

**S6 — no cycle detection anywhere.** `Runner::new` (`scheduler.rs:173-226`) does zero graph
validation. *Partly superseded by **N1**: a cycle does not hang, it aborts the process.*

> **The common shape of S3/S4/S5/S6:** four different defects terminate in **one** observable —
> the scheduler sleeping forever at 0% CPU, because its only exit is
> `running.is_empty() && (stop || done+skipped >= steps.len())` (`scheduler.rs:261-262`). An
> in-loop terminal-starve detector catches all four, including cases nobody has enumerated.

**S7 — out-of-range values are unvalidated and silently coerced.** Named instances: negative
`resource_caps`; `jobs: jobs.max(1)` (`scheduler.rs:204`) clamping 0 or negative rather than
refusing; `step_width` (`scheduler.rs:111-115`) coercing `preferred_inner_jobs <= 0` to 1;
`mem_cap_factor <= 0`, negative `mem_cap_floor_bytes`, and negative timeouts all accepted.
*Extended by **N7**: the property is total, not partial.*

**S8 — a flag that parses and no-ops: `--max-mem`.** `cpa_budgets` (`cli.rs:1083-1089`) opens
with `if planner != Planner::Cpa { return (None, None); }`, so `--max-mem 8G` under the **default**
greedy-lpt planner is accepted, validated, and discarded. On the same line,
`max_mem.filter(|s| !s.is_empty()).and_then(parse_size)` with `parse_size` (`sizing.rs:321-341`)
returning `None` on malformed input means `--max-mem 8GB` or `--max-mem banana` *also* silently
become "no budget" instead of a usage error. Two distinct silent acceptances on one line.
(`--planner` itself **is** properly validated at `cli.rs:899-905`.)

**S9 — teardown kill result discarded:** `let _ = Command::new("kill")` in `kill_group`
(`scheduler.rs:84`). A step whose process group could not be killed reports nothing, at exactly
the moment containment matters.

---

## Findings N1–N8 — cgroup.rs, estimates.rs, sizing.rs

*Recorded by sol-loc, 2026-08-08, mutation-verified against a binary built fresh from `32cbf16`.
Two of these correct S1–S9.*

**N1 — a dependency cycle is not a hang, it is a stack overflow and a core dump.**
**Corrects S6**, which predicted the same permanent 50 ms sleep. Planted `a↔b`:

```
thread 'main' has overflowed its stack
fatal runtime error: stack overflow, aborting
-> rc=134 (SIGABRT), core dumped
```

Root cause `sizing.rs:58-92`: `transitive_deps`'s inner recursive `visit()` writes its memo into
`result` only **after** the recursive call returns, so a cycle re-enters the same tag forever. It
fires on `run`, `ascii`, and `dot` — every path that sizes or renders. `json --dag` returns
**rc=0**, so the file *validates cleanly* and then aborts the process that uses it. Worse than a
hang: no diagnostic names the cycle, and a SIGABRT is easily misattributed to the workload. The
memo is also the fix site — an in-progress set turns this into a named error.

**N2 — duplicate tags do not just drop a step, they over-report the survivor.** Sharpens S5 with
the observable. Two steps both `g.dup`, each appending a distinct token to a file: the
side-effect ledger contains exactly **one** line (`SECOND`), and the summary prints
`PASS - 2 passed, 0 failed`. The runner claims 2 passed having executed 1 — a green asserting
coverage it did not deliver, produced by the runner itself.

**N3 — `res_free`'s absent-vs-zero fusion is duplicated in the CPA planner.**
`estimates.rs:1110` — `res_avail.get(r).copied().unwrap_or(0) >= *n` — is an independent second
copy of `scheduler.rs:106` (S3). Fixing only the scheduler leaves the planner's admission
*simulation* disagreeing with the scheduler's admission *decision*. Both, or one factored
predicate.

**N4 — `cgroup.rs:762` fuses "cannot read the proc list" with "migration complete."** In the
supervisor-cgroup migration loop:

```rust
let pids = read_trim(&scope, "cgroup.procs").unwrap_or_default();
let pids: Vec<&str> = pids.split_whitespace().collect();
if pids.is_empty() { break; }
for pid in pids { let _ = fs::write(sup.join("cgroup.procs"), pid); }
```

A **failed read** yields `""`, which is indistinguishable from an empty cgroup, so the loop
breaks and reports success. The per-pid move is discarded, and if the 5 iterations exhaust with
pids still present the loop simply ends with no check and no warning. Net: **processes can remain
outside the supervisor cgroup while per-step containment reports itself established.** Highest
stakes in the file, because containment is the crate's stated primary purpose.

**N5 — `cgroup.rs` is inconsistent with itself about `subtree_control`.** At `:770-775` the
delegation writes for `memory`/`cpu`/`pids` are each error-checked and warn on failure. At `:613`
the cpuset write is `let _ = fs::write(scope.join("cgroup.subtree_control"), "+cpuset")` —
discarded — and the function then returns `true` under a comment reading `Verified.`. The check
at `:609` verifies the **scope's own** `cpuset.cpus.effective`, not that children inherit it. So
`apply_specific_cores` reports a verified core box whose per-step children may be unconstrained,
while `--cores` advertises *"an exact verified cgroup cpuset; refuse when unavailable"*
(`ENFORCEMENT_CAPABILITIES`). Because the same file already does this correctly a few lines away,
this is a gap in an existing convention.

**N6 — an unparseable `oom_kill` count silently becomes "no OOM."** `cgroup.rs:927`
`rest.trim().parse().unwrap_or(0)`, and `:922` returns 0 when `memory.events` is unreadable.
`model.rs::step_failure_reason` keys the `OOM-KILLED` classification off this count, so a
malformed or unreadable `memory.events` reclassifies a genuine OOM kill as a plain `exit N` —
misattribution of the single most consequential failure class, invisibly.

**N7 — out-of-range validation is not partial, it is absent: 0/16.** S7 named four instances; the
sweep shows the property is total. Every one of these parses cleanly at `rc=0`:

`resource_caps -1` · `timeout -5` · `cpu_timeout -5` · `default_step_timeout 0` ·
`mem_cap_factor 0.0` · `mem_cap_factor -2.0` · `mem_cap_floor_bytes -1` ·
`outer_mem_safety_factor 0.0` · `preferred_inner_jobs 0` · `preferred_inner_jobs -3` ·
`est_duration_s -1.0` · `rss_baseline_bytes -1` · `hard_mem_max_bytes -1` ·
`hint.resources -2` · `measured_cpu_utilization 9.0` (900%) · `measured_effective_cores -4.0`

There is no range check anywhere in `io.rs` or `model.rs`. Two of these are not cosmetic: a
negative `resource_caps` and a negative `hint.resources` both route into the S3/N3 admission path.

**N8 — `estimates.rs` has zero production `let _ =`; the density estimate needs correcting.**
S1–S9 flagged `estimates.rs` (2134 lines, 22 `unwrap_or`) as one of the two highest-density
unswept surfaces. All 5 of its `let _ =` are inside `#[cfg(test)]` (module starts `:1679`) — test
cleanup, not findings. Of the 19 production `unwrap_or`, most are planner-scoring lookups
(`bottom.get` / `est.get` → `0.0`) where a missing sample legitimately means "no history": those
are **modelling defaults, not correctness gates**, and are not counted here. The two real ones are
N3 and `:402` — `cells.get(i).cloned().unwrap_or_default()`, where a truncated profile-CSV row
silently yields empty fields that then feed the planner. **`cgroup.rs` is the dense surface;
`estimates.rs` is not** — the audit should not spend equal effort on both.

---

## Suggested implementation order

1. **N1** — a crash, and the cheapest fix (a visiting set in `transitive_deps`).
2. **N4 + N5** — containment silently absent; highest consequence.
3. **S1** — unknown keys; composes with every other typo class, including S2.
4. **N7** — range checks; one predicate covers 16 cases.
5. **N3 + S3** — as **one** factored predicate, never two.
6. **N2 / S5** — duplicate-tag detection.
7. **N6** — OOM-count parse.
8. **S4 / S6** — dangling deps and cycles, ideally via the in-loop terminal-starve detector,
   which subsumes S3/S4/S5/S6.
9. **S8, S9** — flag no-ops and the discarded teardown kill.

`agent-utils` lands via **serialize + re-pin**, never straight to main.

## Coverage limits — this is not whole-crate coverage

**Swept:** `io.rs`, `model.rs`, `scheduler.rs`, `cli.rs` (flag surface) — S1–S9;
`cgroup.rs`, `estimates.rs`, `sizing.rs` — N1–N8.

**Still unswept**, highest `let _ =` density first — `reservation.rs` (4 `unwrap_or` / 11
`let _ =`) and `cpuset_allocator.rs` (6 / 7), both of which make allocation decisions, then
`perflog.rs`, `sync.rs`, `summary.rs`, `ambient.rs`, `viz.rs`, `profile_enrich.rs`.

**Also not covered:** the CLI flag surface beyond S8 (`--max-mem` stands unretested by the N-pass),
and the profile-CSV and summary-JSON input schemas, which are separate parsers with their own
strictness question.

## Reproducing the mutation evidence

The harness was machine-local scratch and is **not** durable; it is not in any repo. It is
reconstructible from this document: for each field, emit a DAG document containing the planted
value, run `safe-ci-dag-runner json --dag F` for parse-shape cases and
`safe-ci-dag-runner run --unsafe-no-cgroups -j2 --dag F` with `true` commands for graph cases, and
classify the outcome as REFUSED (non-zero exit **and** the message names the offending field),
ACCEPTED (exit 0), STARVED (no exit), or ABORTED. When the fix lands the harness belongs in the
crate as `tests/schema_strictness.rs`.

**Build a fresh binary before measuring.** The checked-in `rs/target` binaries were stale at
audit time — the release binary predated 10 source files — so auditing against them would have
measured old code. Build into a private `CARGO_TARGET_DIR` so the shared `agent-utils` target
tree is not disturbed.

One caution learned the hard way: an early classifier scored the duplicate-tag case as `WARNED`
because it matched the unrelated `--unsafe-no-cgroups` banner. Match on the specific field name,
not on the presence of the word "warning", and confirm execution counts with side effects rather
than with the runner's own summary — which is the very thing N2 shows to be unreliable.
