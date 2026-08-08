# Speculative-land workflow: typed bounded outcome + per-op timing

**Producer:** `fix-speculative-land-obligations-workflow-timeout` (hermit-w3), 2026-08-06.
**Consumer:** `rewrite-hermit-dev-plugin-post-1.0` (hermit-w2), who owns
`.orc/plugins/hermit-dev/index.ts`.

**Why this is a document and not a commit.** At the time of writing, w2 has 152 uncommitted
insertions in `index.ts` (last written 18:21:41, a startup surface self-test — a region disjoint
from this change). Editing a file carrying another agent's in-flight work would violate Invariant 2
and would land my lines inside their staged diff. So the code is here, context-anchored rather than
line-anchored, because w2's edits shift line numbers every few minutes.

## The measurement that governs this change

Full numbers and method on the producer task. The load-bearing results:

| measurement | value |
| --- | --- |
| `land_and_arm.py recover` | 2.18 s wall |
| `ci-hub watch-obligations --once --gate` | 4.21 s wall (rc=1 is gate semantics, not failure) |
| `ci-hub` full cold rebuild | 7.77 s wall / 23.93 s CPU |
| composed command, out-of-band, n=6 | 5.11 – 16.83 s |
| composed command, live ticks, n=8 | 5.136 – 19.319 s |
| configured bound | 240 s |
| **max observed / bound** | **19.319 / 240 = 8.0 %** |

Occurrences of the 240 s timeout in the entire orc log corpus: **1**. It is the only script-effect
timeout of any script on record.

### Therefore: do not change `timeoutSec: 240`

12.4x headroom is measured, not assumed. Lowering the bound converts ordinary `ci-hub` rebuild ticks
(the 11–19 s tail, caused by other agents editing `ci-hub/lib/*.rs` while ci-hub's shebang is
`rust-script --force`) into false failures. Raising it is unmotivated by any observation.

## (b) Typed bounded outcome instead of an untyped crash

Today `await orc.scripts.hermitSpeculativeLandObligations()` throws on effect timeout. The throw
escapes the `wf.loop` body and kills the workflow; it survives only because
`HEARTBEAT_RESTARTABLE` (w2's `c519e89`) catches the corpse and restarts it 5 s later. That is
survival by safety net, not reporting: nothing records *that* a tick timed out, or how long it ran.

The loop already has a `pollFailed` branch with signature-deduped alerting. Route the timeout into
it rather than inventing a second path.

**Anchor** — the current opening of the loop body:

```ts
    const result = await orc.scripts.hermitSpeculativeLandObligations() as {
      exitCode: number;
      stdout?: string;
      stderr?: string;
    };
```

**Replace with:**

```ts
    // A tick may exceed its effect bound. Measured 2026-08-06: live ticks run
    // 5.1-19.3s against a 240s bound (8% of budget), and exactly one timeout
    // exists in the whole log corpus -- so a timeout is a rare outlier, not the
    // steady state, and the bound is NOT the thing to tune. What was missing is
    // that the outlier CRASHED the loop: the throw escaped, and only the restart
    // policy kept the heartbeat alive. Surviving by safety net records nothing.
    // Convert it to a typed value the existing pollFailed branch already handles.
    const startedMs = Date.now();
    let result: { exitCode: number; stdout?: string; stderr?: string };
    let timedOut = false;
    try {
      result = await orc.scripts.hermitSpeculativeLandObligations() as {
        exitCode: number;
        stdout?: string;
        stderr?: string;
      };
    } catch (err) {
      // EXIT CODE 3 IS DELIBERATE: >1 and not 2, so it lands in `pollFailed`
      // and is alerted+deduped by machinery that already exists, instead of
      // adding a parallel path that would need its own dedupe and could rot.
      timedOut = true;
      result = {
        exitCode: 3,
        stdout: "",
        stderr: "speculative-land poll did not complete: " +
          String((err as any)?.message || err),
      };
    }
    const elapsedMs = Date.now() - startedMs;
```

Then extend the report line so the elapsed time travels with the evidence:

**Anchor:**

```ts
    const report = [stdout, stderr].filter(Boolean).join("\n");
```

**Replace with:**

```ts
    // The duration is part of the finding, not decoration: an alert that says
    // "the watcher failed" without saying whether it failed in 5s or 240s sends
    // the reader to the wrong hypothesis. Carry the condition with the value.
    const outcome = timedOut ? "timeout" : (exitCode > 1 ? "error" : "ok");
    const timing = "outcome=" + outcome + " elapsed_ms=" + elapsedMs +
      " bound_ms=" + (SPECULATIVE_LAND_TIMEOUT_SEC * 1000);
    const report = [timing, stdout, stderr].filter(Boolean).join("\n");
```

## (c) Make the bound a named constant, so the log can cite it

The `240` currently appears only as a literal in the `registerScript` call, so the loop cannot
report what bound it was measured against.

**Anchor** — near the other speculative-land constants:

```ts
const SPECULATIVE_LAND_INTERVAL_MS = 15 * 1000;
```

**Add beneath it:**

```ts
// Measured 2026-08-06: live ticks 5.1-19.3s (n=8), out-of-band 5.1-16.8s (n=6),
// worst case a cold ci-hub rebuild at ~14s. 240s is ~12x the observed maximum.
// The tail is ci-hub rebuild cost, not the poll: other agents edit ci-hub/lib/*.rs
// and ci-hub's shebang is `rust-script --force`, so every call enters Cargo.
// Do not lower this to "tighten" it -- that converts rebuild ticks into false
// failures. Do not raise it either; nothing observed needs more.
const SPECULATIVE_LAND_TIMEOUT_SEC = 240;
```

**Anchor** — the registration:

```ts
  orc.registerScript(SPECULATIVE_LAND_SCRIPT_NAME, {
```

...and inside it, replace `timeoutSec: 240,` with `timeoutSec: SPECULATIVE_LAND_TIMEOUT_SEC,`.

## What this does and does not establish

**Does:** a timed-out tick becomes an alerted, deduped, typed `outcome=timeout` with its measured
`elapsed_ms` and the bound it was measured against; the loop continues to its next `wf.sleep`
rather than dying and being resurrected; remediation immediacy is untouched (interval stays 15 s).

**Does not:** explain *why* the one 240 s block happened. That remains **UNKNOWN**. Ruled out by
measurement: steady-state work, gate result, and rebuild. Unverified hypothesis worth a probe rather
than a patch — Cargo build-lock contention on the single shared rust-script cache, since `--force`
enters Cargo on every invocation and ~14 agents share one cache, making a lock wait unbounded by
construction. No `Blocking waiting for file lock` line was captured, so this is a hypothesis.

**This change is the probe.** Once `elapsed_ms` is recorded per tick, the next occurrence arrives
with its own duration attached instead of costing another investigation.
