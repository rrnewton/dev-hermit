# ci-hub-owned visible validate panes

Task: `ci-hub-owned-validate-panes-outside-the-agent-jail`  
Date: 2026-08-06 UTC  
Implementation checkpoint: parent commit `fbc9b7a21f5cedf8aa0287dcd2c474e5b26c9032`

## Result

`ci-hub validate-run` now owns both the detached validation service and an
owner-visible Herdr tab. The agent sandbox stays enabled and remains the
default-deny boundary. Legitimate validation crosses it only through ci-hub;
the actual workload immediately enters the existing systemd + validate-lock +
safe-ci-dag-runner containment chain.

```text
agent caller
  -> ci-hub validate-run (exact-head/preflight authority)
      -> short systemd broker -> Herdr observer tab (read-only visibility)
      -> validate-*.service -> validate-lock -> validate.sh -> safe-ci boxes
      -> durable log + exact-SHA ledger receipt
  <- handle printed, then caller waits on the independent service
replacement caller -> validate-run --attach HANDLE -> same service/result
```

The Herdr tab is intentionally **not** the producer. It only tails the durable
log, watches the exact service process tree, and records cgroup paths containing
`safe-ci-`. This prevents “visible pane” from becoming an unboxed second path.
If the tab cannot be created, launch refuses before the validation service
starts. If the caller is recycled after launch, only its wait disappears; the
service, pane, log, ledger writer, and durable JSON handle remain.

## Implemented mechanics

- One stable `validate-hermit` Herdr workspace; one titled tab per run:
  `PR #N | <head-prefix> | since <timestamp>` (unit replaces PR when absent).
- Every Herdr control call runs through a short-lived user service outside the
  agent jail. A persistent `ci-hub-herdr.service` owns the visibility server.
- `ignored/validate/runs/<unit>.json` binds unit, exact target, checkout, log,
  PR, pane IDs, lifecycle state, exit status, and observed safe-ci cgroups.
  Sidecar locking serializes caller/observer read-modify-write updates, so the
  terminal status cannot erase boxing evidence or vice versa.
- The normal call prints the handle and pane IDs before blocking. `--attach`
  waits on that exact handle without launching another run.
- The validation service still executes the one existing producer chain:
  `ci-hub validate-lock run -- ... with-proxy ./validate.sh ...`.
- `validate-lock` remains one-wide. Several queued service/pane handles may be
  visible, but this change does not silently implement the separately-derived
  `{jobs: 6, bytes: 12_884_901_888}` semaphore or weaken solo-negative
  authority.

## Live boxing/visibility bracket

No product validate and no concurrent validate were run. A harmless one-node
safe-ci DAG (`sleep 8`) exercised the same systemd-service → safe-ci handoff on
`devbig014`. The observer was created as Herdr workspace `wC`, tab `wC:t3`,
pane `wC:p3` and remained readable after completion.

The pane displayed the exact target, unit, checkout, durable log, node output,
and these process-identity-bound cgroups discovered below the service MainPID:

```text
/user.slice/user-212630.slice/user@212630.service/safe.slice/safe-ci.slice/
  safe-ci-2447362.scope/supervisor
/user.slice/user-212630.slice/user@212630.service/safe.slice/safe-ci.slice/
  safe-ci-2447362.scope/step-probe.boxed_sleep
```

The durable handle finished with `state=completed`, `result=success`,
`exit_code=0`, and both paths in `observed_safe_ci_cgroups`. The runner log
independently said `cgroup boxing ACTIVE`, audited bounded `memory.max`, disabled
swap, enabled `memory.oom.group`, and reported `1 passed, 0 failed`. This is the
positive bracket: visibility dereferenced the actual descendant cgroups rather
than inferring boxing from use of the CLI. Unit tests also plant an unrelated
`safe-ci-*` process and prove it is excluded when it is not a descendant.

## Local validation

```text
python3 -m py_compile ci-hub/validate/start_unit.py \
  ci-hub/validate/run_registry.py ci-hub/validate/pane_owner.py \
  ci-hub/validate/pane_watch.py ci-hub/validate/tests/test_start_unit.py \
  ci-hub/validate/tests/test_pane_watch.py

python3 -m unittest ci-hub/validate/tests/test_start_unit.py \
  ci-hub/validate/tests/test_pane_watch.py

Ran 9 tests in 0.014s -- OK
```

Covered refusal/positive cases: dirty checkout, stale admission, Herdr
unavailable before service launch, exact producer chain, observer-not-producer,
durable handle completion, replacement attachment without relaunch, and
descendant-only cgroup identity.

The broader `python3 -m unittest ci-hub/tests/test_documented_commands.py` is
not green: its pre-existing command classifier rejects the existing
`./ci-hub/bin/reconcile-receipts # human table` README example as unclassified.
This change does not own that command/classifier and did not relax the check.
`ruff` is not installed on the host; no package download was attempted.

## Remaining operational gate

The implementation is local. The required immediate push of checkpoint
`fbc9b7a21f5cedf8aa0287dcd2c474e5b26c9032` failed with the box-wide
`CONNECT tunnel failed, response 403`. No green product result is claimed; the
only live run was the bounded infrastructure probe above.
