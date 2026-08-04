# cpu_timeout load-immunity A/B — the budget holds on a busy box

**Question.** A wall-clock timeout is load-dependent: it fires on a healthy-but-slow
run on a busy box, and lets a real hang run to full budget on a quiet one. A
CPU-time budget is supposed to be load-immune because it keys on the node's *own*
accrued CPU-seconds, not elapsed wall. Does that hold **on the running thing**?
Run the same boxed 3-node DAG twice — once idle, once with host load driven up
~10× — and check whether the kill-decision variable and every verdict are
invariant.

This is the controlled quiet-vs-busy A/B flagged as "needs a run" in the
`p0_implement_load_immune` handoff. It complements:
- `../cpu-timeout-enforcement-verified_20260803` (negative + wall-immunity controls)
- `../cpu-timeout-positive-control_20260804` (49 healthy nodes below budget, zero false-kills)

## Method

Same `probe.json` in both legs, boxed via the systemd `--user` producer path so
each step gets its own child cgroup with real `cpu.stat usage_usec` attribution
(runner `agent-utils@1c7c855`, python engine; enforcement = 1 Hz `usage_usec`
poll + whole-cgroup reap, `#7`/`0eb4203`). See `metadata.json` for exact SHAs,
host, and commands.

- **idle leg:** no injected load. Box executing ~5% (13/316 cores), `load1` 42.6.
- **busy leg:** 128 self-owned busy-loop workers in one process group, started
  before the run and killed by negative PGID after (confirmed empty; load back to
  4.2%). Box executing ~48% (152/316 cores), `load1` 62.5 — a real ~10× rise in
  running CPU. Held under the ci-hub 50% SUITABLE policy (≥160 cores left free) to
  stay a good co-tenant on a shared 18-agent box.

## Results (`results.csv`, cgroup-attributed)

| node | leg | rc | cpu_timed_out | ok | wall_s | **cpu_usage_s** | load1 |
|---|---|---|---|---|---|---|---|
| probe.cpu_breach | idle | -9 | True | False | 3.02 | **3.001** | 42.6 |
| probe.cpu_breach | busy | -9 | True | False | 3.03 | **2.957** | 62.5 |
| probe.compliant_idle | idle | 0 | False | True | 4.04 | 0.002 | 42.6 |
| probe.compliant_idle | busy | 0 | False | True | 4.08 | 0.003 | 62.5 |
| probe.compliant_cpu | idle | 0 | False | True | 0.41 | 0.165 | 42.6 |
| probe.compliant_cpu | busy | 0 | False | True | 0.15 | 0.068 | 62.5 |

## Conclusion

**The kill-decision variable (per-cgroup accrued CPU-seconds) did not move when
host `load1` jumped 42.6 → 62.5 (executing 5% → 48%).** The CPU burner was reaped
at ~3 CPU-s in *both* legs (`rc=-9`, `cpu_timed_out=True`), and both healthy nodes
passed in both legs — no false kill. This is the discriminator that makes CPU
budgets work: `wall == cpu` at the budget is a burned core (caught in seconds,
idle or busy); low CPU against high wall is a blocking wait (`compliant_idle`:
~4 s wall on 0.002 CPU-s, ignored under both loads). A wall budget wide enough to
survive the busy leg would have to be widened enough to re-admit the burner on a
quiet box; the CPU budget catches it at the same *work* regardless of load.

## Limitation (stated honestly)

~160 cores stayed free even in the busy leg, so the probe's own **wall** was not
core-starved (`cpu_breach` wall 3.02 → 3.03 s). This A/B therefore proves
**CPU-accounting sibling-independence** (host load +10×, per-node `usage_usec`
flat, verdict unchanged), *not* wall-inflation-under-scarcity. The all-wall /
no-cpu limiting case — a healthy blocking wait surviving a tight CPU budget — is
covered by `compliant_idle` here and in the enforcement-verified artifact. I
deliberately did not saturate all 316 cores on a shared box.

## Reproduction

`metadata.json` records the exact command, SHAs, and load-generator disposition.
Re-run: start N self-owned busy workers in one PGID, run the two legs via the
systemd `--user` scope producer path, join `perf_idle` vs `perf_busy`
step-profile CSVs on `cpu.usage_usec`, then kill the load PGID by its negative
value.
