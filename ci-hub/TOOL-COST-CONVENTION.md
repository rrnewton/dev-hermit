# Tool cost awareness

Every user-facing tool owned by dev-hermit must make its time cost visible.
The caller should never discover after launch that a command needs hours.

## Required output

1. **Estimate before work.** Print expected wall and CPU time before the first
   expensive or mutating step. Derive it from known parameters and measured
   history: input count, repetitions, probe count, warm/cold cache state, and
   parallelism. Print that basis. If the required measurements do not exist,
   print `unknown` and state what data is missing. A static broad guess is
   prohibited, including as a bootstrap.
2. **Actual on every completion path.** Print elapsed wall and total CPU
   (user + system) with the exit status on success, failure, signal, and bounded
   early exit. Preserve the command's exit status.
3. **Every displayed number is accountable.** A number shown to a human must be
   derived from real data with its basis stated (source, window, and sample size
   where applicable), or explicitly labeled `unknown` / `not measured`. Never
   print an invented constant that merely looks like a measurement.

Canonical lines are stable and machine-readable enough for future ingestion:

```text
COST ESTIMATE tool=<name> wall=<seconds>s cpu=<seconds>s basis='<parameters/history>'
COST ESTIMATE tool=<name> wall=unknown cpu=unknown basis='not measured: <missing data>'
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
  --estimate-cpu-seconds 21600 \
  --basis 'derived from 12 probes x 3 reps x measured p50 600s/probe (last 8 warm probes) / parallelism 3' \
  --actual-json ignored/ci-hub/multisect-cost.json \
  -- ./multisect ...
```

When no defensible estimate exists, use the explicit unknown path; do not put a
timeout, upper bound, or plausible constant into an estimate field:

```bash
ci-hub/bin/tool-cost \
  --tool ci-hub/example \
  --estimate-unknown \
  --basis 'not measured: no retained runs for this operation' \
  -- ./example ...
```

`--actual-json` is optional. When supplied, the helper atomically writes schema
v1 containing the same estimate plus actual wall, total/user/system CPU, and
exit fields. Long-lived stores should ingest or reference that JSON instead of
parsing human-facing log lines. The speculative-land obligation store uses this
path for its detached local `validate.sh` run. `estimate.kind` is `derived` or
`unknown`; unknown estimates store JSON `null` for wall and CPU seconds.

For a self-contained product tool that cannot depend on the parent checkout,
keep its calculation local but use the same output contract. Parent launchers
should still use the shared wrapper rather than adding another timer.

## Estimation rules

- **Parallel work:** wall estimate is critical-path work divided by effective
  parallelism plus setup/queue overhead; CPU estimate is summed work and is not
  divided by parallelism.
- **History-backed work:** state the sample/window and warm-vs-cold class. Fall
  back to `unknown`, not a conservative-looking number, when history is absent.
- **Network work:** derive queue/API retry allowance from measured history when
  available. A configured timeout or retry cap is a bound, not an estimate; label
  it as a bound and keep the estimate unknown when no runtime data exists.
- **Early rejection:** estimate first when parameters are valid enough to size
  the work; otherwise report an unknown estimate and the measured actual.
- **Nested tools:** report the outer operation once. Set
  `CI_HUB_TOOL_COST_ACTIVE=1` when an already-timed parent invokes another
  wrapped ci-hub entrypoint.

## Numeric claims and ratchets

- Counts, percentages, and progress indicators must be computed from the data
  actually inspected. State the denominator or sample size and the data window;
  progress must reflect completed work, not a theatrical counter.
- Configuration values, timeouts, resource limits, and decision thresholds are
  legitimate constants only when labeled as policy, limits, bounds, or
  thresholds. Do not describe them as observed values or estimates.
- Asserted ratchets are legitimate when their provenance is recorded beside the
  declaration and the code verifies the count. For example,
  `RR_COMPAT_EXPECTED=139` records the establishing PRs, measured exclusions,
  and the exact passing-label set in `hermit/validate.sh`. That is an intentional
  compatibility floor, not a fabricated measurement. A ratchet without recorded
  provenance is an audit failure even when its current value happens to be right.
- A summary must not imply that work was measured if a probe did not run. Emit
  `unknown`, `not measured`, or an explicit unavailable result instead.

The current compliance inventory and follow-ups are in
[`TOOL-COST-AUDIT.md`](TOOL-COST-AUDIT.md).
