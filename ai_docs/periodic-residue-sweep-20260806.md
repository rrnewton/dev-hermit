# Periodic unowned-residue sweep

## Result

Correct refusals and agent recycling now emit a durable hourly coordinator
signal instead of silently leaving work behind. The implementation is
`ci-hub/health/residue_sweep.py`; `ci-hub/health/tick-hub.yaml` invokes its
fail-closed `--gate` every hour.

The sweep is report/router-only. It never closes or reassigns a task and never
cleans, releases, resets, or removes a worktree.

## Authorities and predicates

The report has three independently typed surfaces:

1. **Orphaned tasks:** reuse `scripts/orphaned-task-detector.sh --gate`, whose
   authority is exact set membership of an in-progress TaskGraph owner in the
   live ORC tmux windows. An unreadable or empty fleet is `unknown`, never
   “everyone is dead.”
2. **Declined-action residue:** query in-progress task notes for the established
   exact marker set (`did not commit`, `not committed`, `untracked`, `needs
   authorization`, `left for [the] coordinator`, and `no
   reset/clean/force-push done`). Each source note ID gets a durable
   `RESIDUE-DISPOSITION`: coordinator route, owner-decision route, or explicit
   no-action. A later sweep dereferences those source IDs and does not emit the
   same refusal again.
3. **Held slots with dead owners:** require both `registered owner not in the
   live fleet` **and** `no same-user live process has a cwd beneath the slot`.
   Owner absence alone is not enough because a delegate can remain alive
   without its own tmux window. The route goes to coordinator slot-lifecycle
   authority; it is not permission to clean the slot.

Every report item carries `{kind, key, evidence, disposition, route_task,
route_authority}`. A report with an empty route on an actionable item is a
schema error, not a successful sweep. `--gate` exits 1 for actionable residue,
2 when any authority is unreadable, and 0 only when no action is outstanding.
`--json` emits the complete typed record. `--route` appends grouped local
TaskGraph notes to the named authorities.

## Live run: 2026-08-06

The live identity set contained 10 ORC windows. The first run found:

- orphaned in-progress tasks: **0**;
- declined-action notes: **50 source notes**, grouped into **38 dispositions**
  across **35 tasks** — **30** coordinator routes, **5** owner-decision routes,
  and **3** explicit no-actions;
- active/held slots satisfying both dead-owner predicates: **52**;
- actionable total: **87** (35 declined-action routes + 52 slot routes).

The owner-decision routes were `adversarial-review-tightening-batch-2`,
`close_boxing_coverage_gap`, `no-hardcoded-wall-timeouts-idiom`, the
owner-authorized portion of `port_validate_sh_to`, and the owner-authorized
portion of `shared_inguest_toolhost_family`. The explicit no-actions were the
already-clean notes on `kvm-stdout-tty-winsize-divergence`, `port_validate_sh_to`,
and `reverie_host_dependent_dependencies`. All other declined-action groups
were routed to the coordinator task.

The dead-owner slot set was:

```
226 227b 231b 243 250 250-delegate canon citimeout cleanbuild
codex-reviewer coord coord-drain covnode dagmeasure dbi-fchown dbibuild
detwait4 docs e9bp e9patch envhash fork330 ghdag ghdagval inguest kvm
kvmcompat lander lander2 liteinst nlockgate opt orc-coord parity perf pinlint
pr1147 rb1595 revland sabre scwidth select slot01 slot70 slot73 staging-drain
val1147 vcache vforkverify vprod vselect wallmeasure
```

Every one is recorded on the sweep task with its registered owner, task, and
both negative liveness facts. No slot was modified. After routing, a second
run returned **0 declined-action hits** (the 50 note IDs were dereferenced as
disposed) while correctly retaining the **52 unresolved slot routes** as an
hourly warning until the coordinator explicitly reconciles them.

## Verification

```
python3 -m py_compile ci-hub/health/residue_sweep.py \
  ci-hub/health/tests/test_residue_sweep.py
python3 -m unittest ci-hub/health/tests/test_residue_sweep.py
# Ran 10 tests: OK

python3 ci-hub/health/residue_sweep.py --route
# actionable=87 orphaned_tasks=0 declined_actions=35 dead_owner_slots=52

python3 ci-hub/health/residue_sweep.py --json
# after routing: actionable=52, declined_actions=0, dead_owner_slots=52
```

The unit bracket covers coordinator vs owner routing, explicit no-action,
source-note deduplication, aggregation, live-owner protection, process-CWD
delegate protection, the positive dead-owner-slot case, released-slot
exclusion, and the invariant that every actionable item has a typed route.
