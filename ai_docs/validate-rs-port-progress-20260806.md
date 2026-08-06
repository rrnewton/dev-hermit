# validate.sh → Rust port on safe-ci-dag-runner — progress and remaining gates

**Task:** `port_validate_sh_to` · **Date:** 2026-08-06 · **Author:** hermit-design
**Status:** two increments implemented and committed locally; **not pushed** (egress 403). Full-lane
validate deliberately **not run** (livelock risk at concurrency).
**Slot:** `worktrees/coord/hermit` · **Branch:** `coord/validate-rs-phase2-full-lane`
**Commit this session:** `1d92c5cddd6df25c56c59447d9a4c0315b59d686` (parent `2fe02e8f0`, PR #1635)
**Base:** hermit `origin/main` (branch is ahead 1, behind 18 at time of writing)

---

## 1. Where the port stands

`scripts/validate.rs` (1374 lines) drives validation through `safe-ci-dag-runner` **as a library** —
an in-process typed call, not a subprocess — which is the owner mandate: all validation runs through
the dag-runner, boxed and enforced. Boxing is fail-closed (re-exec into a transient `systemd --user`
scope; exit 3 if it cannot be established). Every verdict is derived from typed fields
(`RunResult.ok`, `StepOutcome.ok`/`returncode`/`reason`/`aborted`) — nothing is text-scraped.

Three **meta-profiles** now reproduce validate.sh's `run_ci_manifest_lane`-built levels gate for gate.
They share one implementation, `run_meta_profile`, parameterised only by their DAG lanes:

| Rust profile | validate.sh source | lanes | status |
| --- | --- | --- | --- |
| `full` | `run_full_suite` :4399 | portable + privileged | ported (PR #1635) |
| `portable-only` | `run_portable_only_suite` :4183 | portable | **ported this session** |
| `privileged-only` | `run_privileged_validation` :4381 | privileged | **ported this session** |

Each = the two always-on preflight gates with validate.sh's fail-fast (:4533, :4536, :4537), then the
centralized manifest gate (`./ci/test_harness.sh validate`, :4178), then its lanes (:4179).

> **A naming trap that had to be made explicit.** The bare `portable` / `privileged` profiles resolve
> `ci/dag/<name>.json` and run the runner once with **no preflight and no manifest gate**. They are
> strictly weaker than the same-named meta-profile. Collapsing the two behind one name would silently
> drop two gates — the exact fake-green shape this port exists to avoid — so they are named
> separately and the help text spells out the difference.

---

## 2. Gate × profile: ported vs remaining

Every profile below is additionally preceded by the two always-on preflight gates. "Gates" counts
`run_check`/`run_check_with_timeout` call sites in validate.sh.

| validate.sh profile | gates | ported? | what blocks the rest |
| --- | ---: | --- | --- |
| `full` | 6 (manifest ×2 deduped → 5 distinct) | **YES** | — |
| `--portable-only` | 4 | **YES** (this session) | — |
| `--privileged-only` | 4 | **YES** (this session) | — |
| `--only <lane>:<nodes>` | 3 | no | trivial: one `ci/run-node.sh` subprocess gate. **Next increment.** |
| `--qemu-l2-only` | 4 | no | 2 plain subprocess gates + fail-fast. Portable now; needs gate timeouts (§4). |
| `--liteinst-compat-only` | 5 | no | 3 plain subprocess gates + fail-fast. Portable now; needs gate timeouts (§4). |
| `--sabre-compat-only` | 5 | no | 3rd gate is the native-bash 212-program ratchet |
| `--e9patch-compat-only` | 5 | no | 3rd gate is the native-bash 155-program matrix |
| `--portable-strict-compat-only` | 3 | no | 191-row native-bash envelope (`run_strict_compatibility_envelope` :3875) |
| `--rr-compat-only` | 4 | no | 139-row record/replay envelope — **owned by the product-duplication task** |
| `--envelope-only` | 2 + `run_envelope` | no | native-bash envelope measurement |
| `quick` | 10 | no | 4 of 8 are bash product-duplication helpers (§3) |
| `super` | large | no | `run_super_suite` :4507; diagnostics + stress |
| `--selective` / `--since-green` | variable | no | couples to the green-inheritance anchor work |

**Nothing has been removed from validate.sh.** Every unported profile still dispatches through it
unchanged, so no gate is dropped — the port is additive until a profile is proven subsumed.

### The circular-embedding constraint on deletion

`ci/dag/portable.json`'s `test.strict_compat` node shells out to `./validate.sh
--portable-strict-compat-only`, and privileged's `rr.compat_baseline` to `./validate.sh
--rr-compat-only`. **Deleting validate.sh breaks those DAG nodes** until the compat corpora are
productized. Deletion is therefore gated on the corpus work, not on this port.

---

## 3. What must NOT be ported (coordination boundary)

Per the `validate-sh-duplicates-product-functionality` thread: do **not** port the Bash comparison
bodies of `hermit_determinism_check`, `hermit_record_replay_smoke`, or `rr_compatibility_probe`.
PR #1543 moves those to shipped product verification. The Rust port subsumes only the resulting
**product invocations and gate names**. In particular the R/R ratchet must invoke `--verify
--verify-strict --verify-json PATH` and accept only product JSON `bitwise_parity=true` — never infer
verification from a command's exit status. This is why `quick` is not simply "8 more subprocess
gates": half of it is scheduled to change shape underneath.

---

## 4. Semantic gaps in what is already ported — named, not papered over

**(a) No lane-level wall timeout.** validate.sh runs each lane as
`run_check_with_timeout "${CI_*_DAG_TIMEOUT_SECONDS:-7200}" ... ./ci/run-dag.sh <lane>`. `run_dag_lane`
calls `run_dag_boxed_ordered`, which enforces **per-step** timeouts from the DAG but has **no
lane-level deadline** — so a lane that wedges below the per-step granularity runs unbounded where
validate.sh would kill it at 2 h. Per the owner rule "if the runner lacks a feature, ADD it, never
bypass," the fix belongs in `safe-ci-dag-runner` (a run deadline parameter), not in a local watchdog.
Cross-repo, so it is called out rather than smuggled in.

**(b) `run_subprocess_gate` has no timeout.** It is the reason `--liteinst-compat-only` and
`--qemu-l2-only` are listed as "portable now, but". validate.sh has 25 `run_check_with_timeout` call
sites; porting any of them faithfully requires a timeout argument first. Adding one to
`run_subprocess_gate` is small and self-contained — it should land before those profiles, not with
them.

**(c) Gate granularity differs (finer, deliberately).** validate.sh records one gate per lane
("portable CI DAG lane"); validate.rs records one gate per DAG **node** (~47). That is strictly more
information and is what lets `gates[]` carry named-node + returncode for red attribution — but a
consumer comparing `gates_run` between the two producers is comparing different units. The
`producer` field already disambiguates the row.

---

## 5. The coverage object (implemented this session)

The receipt now carries a `coverage` object in the consumer's **exact** `CoverageRow` shape
(`ci-hub/lib/records.rs:204`): `planned_test_nodes`, `executed_test_nodes`, `zero_executed_nodes`,
`absent_nodes`.

**Why shape-exactness is load-bearing, not cosmetic.** `HistoryRow.coverage` is a typed
`#[serde(default)] Option<CoverageRow>`. `serde(default)` supplies `None` only for an **absent** key;
a key that is present with the **wrong shape** is a hard serde error on the whole row, and
`parse_ledger` then drops that entire row into a `skipped` counter nobody reads. A malformed coverage
object therefore makes a green receipt **vanish** — strictly worse than omitting coverage.

**What the values claim, and what they do not.** They are NODE-RAN granularity, derived from typed
gate outcomes: which planned `test.*` nodes ran. `zero_executed_nodes` is **always empty and means
"not determinable here", never "verified none"** — a node that ran while executing zero test cases is
visible only in the log banners, which is `finalize_receipt.py --scan`'s job. That is safe because
the coverage clause of the landing predicate fires only at `schema_version >= 5`
(`validate_status.rs:173`) and this producer stays at **3**, where a row carrying no `executed_tests`
can never qualify as a landing green at all (`qualifying_receipt.rs:136`). The authoritative
counted+coverage row is still minted by `finalize_receipt.py --scan` off the durable log.

**The fake-green line this port does not cross:** validate.rs never writes `executed_tests` and never
flips `full_coverage`. Emitting a ~47-**node** count under a libtest-**test** field name is precisely
how a DAG-lane run would masquerade as a 47-test full pass to a schema<5 consumer.

---

## 6. Verification performed (local only — no full validate, no network)

| Check | Result |
| --- | --- |
| `cargo check --offline` on the rust-script package | **exit 0**, no warnings |
| Coverage round-trips through the **real** consumer type | `ci-hub validate-status` → `disqualified_count: 1` (row **parsed**, then judged on the predicate) |
| **Negative bracket** — planted `absent_nodes: 1` (int, not `Vec<String>`) | `disqualified_count: 0` — row **silently dropped**, confirming the shape check is not vacuous *and* reproducing the hazard |
| 3-node fixture with a failing dependency | `coverage{planned:3, executed:2, absent:["test.absent_node"], zero_executed:[]}` — `absent` correctly tracks the skipped node |
| `portable-only` dispatch | takes the meta path: `preflight + manifest + lanes portable` |
| `privileged-only --dag-file F` | takes the single-DAG path — an explicit `--dag-file` still wins over a meta name |
| Receipt independent of launch path | standalone run (no systemd unit) wrote an **absolute** durable log and appended a ledger row |

The two brackets are the part worth keeping: a one-sided "it parsed" would not distinguish a working
shape from a reader that accepts anything.

**Deliberately not run:** the real `full` / `portable-only` / `privileged-only` lane executions. Those
need `ci-hub validate-run` on a quiet host; running them here risks the livelock the directive names,
and they cannot mint a landing receipt anyway without a clean tree at a landing commit.

---

## 7. Next increments, in order

1. **Timeout support in `run_subprocess_gate`** — unblocks every `run_check_with_timeout` port (25
   sites). Small, self-contained, no cross-repo dependency.
2. **`--only <lane>:<nodes>`** — one subprocess gate; the cheapest remaining profile.
3. **`--qemu-l2-only`, `--liteinst-compat-only`** — plain subprocess-gate sequences with fail-fast,
   portable exactly once (1) exists.
4. **Lane deadline in `safe-ci-dag-runner`** — closes gap §4(a) in the already-ported profiles.
   Cross-repo (agent-utils serialize + re-pin), so schedule it deliberately.
5. **Compat corpora** — remain in validate.sh until productized; deletion of validate.sh is gated on
   them, and on the two DAG nodes that shell back into it.

---

## 8. State and what is not established

* Commit `1d92c5cd` is **local to the slot**; egress is down so it is not pushed and PR #1635 does not
  yet contain it. The work is committed rather than left dirty so a slot recycle cannot lose it.
* No full validate was run this session. The claim "the ported profiles are gate-for-gate faithful" is
  supported by the validate.sh source mapping in §2 and by dispatch verification in §6 — **not** by an
  end-to-end run of either profile.
* Gap §4(a) (no lane-level timeout) exists in the **already-landed** `full` profile, not only in the
  new profiles.
* Rust-vs-Python runner parity was last measured at 269/270 (the one divergence being userguide help
  text, not execution semantics). Not re-measured this session — it needs the differential harness.
