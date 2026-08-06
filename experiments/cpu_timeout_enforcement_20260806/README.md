# `cpu_timeout` is not inert — it works, it is off in hermit's lane, and turning it on wedges the lane

**Task:** `wire-cpu-timeout-enforcement-inert` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress.

## I did not make a code change, and here is why

The task asks me to *wire* CPU-time enforcement because it is inert. **It is not inert.** I planted a
CPU spinner against the shipped runner and it was killed. Changing agent-utils would be a fix for a
defect that is not there. What I found instead is a real and more dangerous thing: **the routine pin
advance that would "turn it on" kills every step in hermit's portable lane.**

## Two refutations

The task states *"cpu_timeout is DECLARED on nodes and NOT ENFORCED"*. Both halves are wrong, in
opposite directions — and one of them is a misreading of my own adv-review finding, which I should
state plainly since it is my finding being corrected:

| claim | reality |
| --- | --- |
| "DECLARED on nodes" | **0 of 55** steps declare a `cpu_timeout` across `ci/dag/{portable,privileged}.json` |
| "NOT ENFORCED (inert)" | **enforced and measured killing** — see below |

My adv-review said "the engine implements it, adoption is 0/55". That was right but **incomplete**: I
did not know hermit pins a version where the *default* is off too. The complete statement is that **no
CPU bound is in effect for any hermit step, for two independent reasons** — nothing declares one, and
the fallback is disabled in hermit's pin.

## Enforcement fires — planted, with a discriminating control

Runner: `agent-utils/common/bin/safe-ci-dag-runner` (engine = python), boxing ACTIVE.

| # | plant | result |
| --- | --- | --- |
| **E1** | CPU spinner (60 s of pure CPU) with `cpu_timeout: 3`, wall `timeout: 300` | **`FAIL (3s, CPU-TIMEOUT >3s cpu)`** — `CPU BREACH SURVIVED` never printed |
| **E2** | **`sleep 20`** with the *same* `cpu_timeout: 3` | **`PASS (20s)`** |

**E2 is the whole point.** The sleeper ran **20 seconds of wall clock against a 3-second budget** —
6.7× over in wall terms — and was **not** killed, because it burned ~0 CPU. A wall-timeout wearing a
CPU-timeout label would have killed it at 3 s. This is the load-immune bound the owner asked for
(#198), and it already exists.

The mechanism is `scheduler.py:442-455`: a monitor thread polls the step's cgroup `cpu.stat`
`usage_usec` every `_MONITOR_INTERVAL_S`, and on `cpu_used_s >= cpu_budget` reaps the whole group.
The code's own comment is honest about the limits: *"best-effort at the poll granularity, and inert
when cgroup boxing is off (`cpu_stats is None`)"*.

## The finding: the pin advance is a wedge

Same DAG (`undeclared.json`, a spinner declaring **no** `cpu_timeout`), two runner versions:

| runner | result |
| --- | --- |
| **hermit's pin `a6f4232`** | **`PASS (60s)`** — `UNDECLARED SPINNER SURVIVED 60s CPU` |
| **parent's pin `570e7865`** | **`FAIL (10s, CPU-TIMEOUT >10s cpu)`** |

The commit hermit pins is titled, literally:

> `safe-ci-dag-runner: make the SMALL default cap OPT-IN (default OFF) via --small-default-cap`

and its help text names the hazard:

> *"OFF by default: **an active cap on the shared canonical checkout would wedge every undeclared step
> in a concurrent validate run.**"*

In the parent's newer pin that decision is reversed — `--small-default-cap` is demoted to a *"no-op
compatibility flag"* and the caps (1 core / 1 GiB / **10 s CPU**) are **ON by default**.

**hermit's pin is behind the parent's.** So a routine `agent-utils` pin advance would impose a
**10 CPU-second budget on all 55 undeclared hermit steps.** `build.workspace` is a multi-minute cargo
build; it would be reaped at 10 CPU-seconds, as would essentially every other real step. `run-dag.sh`
passes no override (`ci/run-dag.sh:121: exec "$runner" "$verb" --dag "$dag" "$@"`), so nothing
intercepts it.

This is the same shape as the known `phase1-pin-advance-flips-boxing-privileged-risk` trap: **a pin
advance silently changes an enforcement default.**

## What the actual work is

1. **Nothing to wire in agent-utils.** The mechanism is real, correct, CPU-bound not wall-bound, and
   already deployed in both pins. (Also: egress is down, so the serialize → push → re-pin path the task
   names is unavailable regardless.)
2. **Declare per-step `cpu_timeout` budgets in hermit's DAG** — this is the adoption half, and it is a
   *hermit* change, not agent-utils. Budgets must be **derived from measured CPU-seconds per step**,
   not guessed; the DAG already carries a precedent for derived caps
   (`rss_baseline_bytes` with a `MEM-CAP DERIVATION` description naming its method and evidence).
   Note the existing description on `test.detcore_unit` deliberately declines to set one because
   detcore livelocks pre-`reverie#355` and a budget derived from hang behaviour would be wrong — that
   reasoning is sound and should be preserved per-step.
3. **Gate the pin advance.** Before advancing hermit's `agent-utils` pin past the OFF→ON flip, either
   declare budgets for all 55 steps or pass an explicit disable. A `check-agent-utils-pin`-style
   assertion that the SMALL-cap default has not changed polarity across the advance would catch this
   class automatically.
4. **Do not treat "0/55 declared" as the whole gap.** With hermit's current pin, declaring budgets is
   what turns enforcement on; with the parent's pin, *not* declaring them is what turns it on for
   everything at 10 s. The two pins invert which action is dangerous.

## Limits of this measurement

* All runs were local with `cgroup boxing ACTIVE`. The runner's own comment says the guard is **inert
  when boxing is off**, which is the known hosted-CI condition — so these results establish the
  mechanism, not its behaviour on hosted CI.
* I did not measure per-step CPU-seconds for the 55 hermit steps, so I am not proposing budget values.
  That is the next piece of work and it needs a quiet box.
* I made no code change and pushed nothing.

## Reproduction

```bash
cd experiments/cpu_timeout_enforcement_20260806
R=agent-utils/common/bin/safe-ci-dag-runner
$R run --dag spin3.json       # FAIL (3s, CPU-TIMEOUT >3s cpu)
$R run --dag sleep_ctrl.json  # PASS (20s)  <- proves CPU-time, not wall
$R run --dag undeclared.json  # FAIL (10s, CPU-TIMEOUT >10s cpu)   [parent pin]
# and against hermit's pin:
git -C agent-utils archive a6f4232 | tar -x -C /tmp/hp
/tmp/hp/common/bin/safe-ci-dag-runner run --dag undeclared.json   # PASS (60s)
```
