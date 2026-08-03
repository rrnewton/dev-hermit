# Tool cost awareness

Every user-facing tool owned by dev-hermit must make its time cost visible.
The caller should never discover after launch that a command needs hours.

## Required output

1. **Estimate before work.** Print expected wall and CPU time before the first
   expensive or mutating step. Derive it from known parameters and measured
   history: input count, repetitions, probe count, warm/cold cache state, and
   parallelism. Print that basis. A static broad guess is only a bootstrap until
   history exists.
2. **Actual on every completion path.** Print elapsed wall and total CPU
   (user + system) with the exit status on success, failure, signal, and bounded
   early exit. Preserve the command's exit status.

Canonical lines are stable and machine-readable enough for future ingestion:

```text
COST ESTIMATE tool=<name> wall=<seconds>s cpu=<seconds>s basis='<parameters/history>'
COST ACTUAL tool=<name> wall=<seconds>s cpu=<seconds>s cpu_user=<seconds>s cpu_system=<seconds>s exit=<code|signal:N>
```

Write estimates and actuals to stderr so structured stdout remains usable. A
tool may print richer detail, but it must retain these fields and meanings.

## Shared helper

Wrap commands with `ci-hub/bin/tool-cost`; it prints the estimate before launch,
uses `wait4` to measure the child process tree, forwards common termination
signals, reports actual wall/CPU, and returns the child's exit code.

```bash
ci-hub/bin/tool-cost \
  --tool multisect/search \
  --estimate-wall-seconds 7200 \
  --estimate-cpu-seconds 14400 \
  --basis '12 probes x 3 reps x 600s / parallelism 3' \
  --actual-json ignored/ci-hub/multisect-cost.json \
  -- ./multisect ...
```

`--actual-json` is optional. When supplied, the helper atomically writes schema
v1 containing the same estimate plus actual wall, total/user/system CPU, and
exit fields. Long-lived stores should ingest or reference that JSON instead of
parsing human-facing log lines. The speculative-land obligation store uses this
path for its detached local `validate.sh` run.

For a self-contained product tool that cannot depend on the parent checkout,
keep its calculation local but use the same output contract. Parent launchers
should still use the shared wrapper rather than adding another timer.

## Estimation rules

- **Parallel work:** wall estimate is critical-path work divided by effective
  parallelism plus setup/queue overhead; CPU estimate is summed work and is not
  divided by parallelism.
- **History-backed work:** state the sample/window and warm-vs-cold class. Fall
  back conservatively when history is absent and label the fallback.
- **Network work:** include queue/API retry allowance in wall; CPU should remain
  low. A high actual CPU/wall ratio then exposes unexpected local work/spin.
- **Early rejection:** estimate first when parameters are valid enough to size
  the work; an invalid invocation may report a near-zero estimate and actual.
- **Nested tools:** report the outer operation once. Set
  `CI_HUB_TOOL_COST_ACTIVE=1` when an already-timed parent invokes another
  wrapped ci-hub entrypoint.

The current compliance inventory and follow-ups are in
[`TOOL-COST-AUDIT.md`](TOOL-COST-AUDIT.md).
