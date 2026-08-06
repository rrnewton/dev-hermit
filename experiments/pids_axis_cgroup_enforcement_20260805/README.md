# pids-axis cgroup enforcement: is a breach actually stopped?

**Task:** `pids_axis_real_cgroup` (P0) — close the MEDIUM caveat from agent-utils PR #8
adversarial review.
**Date:** 2026-08-05 (runs stamped 2026-08-06T02:4xZ UTC)
**Host:** `devbig014`, kernel `6.18.39-0_fbk0_hardened_0_ga43d5727b443`, systemd 259, cgroup2fs
**Runner:** solo — no concurrent validate (see *Concurrency* below)

## Question

The PR #8 review found that the pids fork-bomb axis's **kernel** enforcement was covered by
no committed test: `test_breach_pids_cap_named` hand-supplies `pids_events=12` (proving the
*classifier* only), the scheduler's `pids_events` branch is unexercised (fake cgmgr returns 0),
and `cgroup.py`'s `pids.max` write was never proven to cause kernel `EAGAIN`.

**Boxing without enforcement is not boxing.** So: does a real `pids.max` cap actually stop a
real fork storm, and can a runaway cgroup actually be killed?

## Method

Real transient systemd **user** scopes via `systemd-run --user` — the process *asks* systemd
for a scope rather than creating its own cgroup, which is why this works where the BpfJailer
agent sandbox does not (the sandbox denies self-created cgroups; `validate` exits 3 in ~9s
having run nothing).

The mechanism is verified **by the running thing, not by the flag we asked for**. The worker
discovers its own cgroup from `/proc/self/cgroup`, reads `pids.max` back from that directory,
and confirms its own PID appears in that cgroup's `cgroup.procs`. A `TasksMax=` we passed is
a request; `pids.max` readback plus `cgroup.procs` membership is the observation.

Three brackets, each with both sides:

| Arm | Setup | Expectation |
|---|---|---|
| **Breach** (positive) | `TasksMax=64`, attempt 200 children | fork past cap refused; `pids.events max` 0→1 |
| **Control** (negative) | `TasksMax=64`, attempt 8 children | all succeed; `pids.events max` stays 0 |
| **Kill A** (positive) | 14-member runaway, write `cgroup.kill` | every member dies |
| **Kill B** (negative) | identical runaway + identical wait, **no** kill | every member survives |

Kill-B is what gives Kill-A meaning: it proves the deaths in A were caused by the kill and not
by processes exiting on their own.

## Results

### Enforcement fact 1 — the kernel refuses the fork (`breach.json`)

```
pids.max readback          64        (read from the live cgroup dir)
self in cgroup.procs       true
baseline pids.current      3         (scope/runtime tasks count against the cap)
children forked            61
fork attempt 62            EAGAIN (errno 11)
pids.current at peak       64
pids.events max            0 -> 1
after reap                 3         (returned to baseline)
```

**Causal equation holds exactly: baseline 3 + 61 successful children = 64 = `pids.max`.**
The next fork — attempt **62** — is refused.

> Do not describe this as "the 65th fork failed." The kernel limit is on **cgroup tasks**, not
> on child-fork ordinal. With baseline 3, the 62nd *attempt* is the first refusal. Reporting a
> fork ordinal as if it were the task count would be false evidence.

### Enforcement fact 2 — the guard is not inert (`control.json`)

```
children forked            8/8, all live concurrently
pids.current at peak       11        (= baseline 3 + 8, equation holds)
pids.events max            0 -> 0    (unchanged)
after reap                 3
```

A within-cap run is **not** flagged. The signal fires on breach and only on breach.

### Enforcement fact 3 — a runaway cgroup can actually be killed (`kill_bracket.json`)

| Arm | kill fired | members before | alive after | `cgroup.procs` after | verdict |
|---|---|---:|---:|---|---|
| A | **yes** | 14 | **0** | dir absent | `all_died: true` |
| B | no | 14 | **14** | 14 | `all_survived: true` |

Identical setup, identical observation window; the only difference is the write to
`cgroup.kill`. Containment is real and atomic across the whole cgroup.

## Interpretation — and one precision that matters

**The `pids` controller does not kill. It denies `fork(2)` with `EAGAIN`.** That *is* its
enforcement. Killing is a separate mechanism (`cgroup.kill`) that a harness must choose to fire.

So the enforcement chain for the pids axis has three links, and this experiment establishes all
three independently:

1. **Kernel denies the fork** — `EAGAIN` at the exact predicted task count. *(fact 1)*
2. **A durable breach signal exists** — `pids.events max` transitions 0→1, and stays 0 on a
   clean run. This is precisely the field the PR #8 scheduler `pids_events` branch reads.
   *(facts 1 + 2)*
3. **A runaway can be contained** — `cgroup.kill` kills every member, bracketed against a
   no-kill control. *(fact 3)*

A report that says only "the breach was killed" would be wrong about the kernel's actual
semantics. The accurate statement: **the breach is refused at the syscall, recorded in
`pids.events`, and the containing cgroup is separately killable on demand.**

## What this does NOT close

The committed e2e test the task asks for **cannot be written yet**, and this is a hard blocker,
not a judgement call:

- agent-utils checkout `570e7865`: a sweep of the **entire repo** (excluding `.git`) for
  `pids.max` / `pids_events` / `PIDS-CAP` / `TasksMax` returns **zero hits**.
- The machinery the test must exercise (`cgroup.py`'s `pids.max` write, the scheduler
  `pids_events` branch, `parallel_experiment_runner`'s PIDS-CAP classification) is not on main.
- PR #8 (`codex/parallel-experiment-runner`) state is **not verifiable this session** — GitHub
  egress was refused all session (proxy 403, `agent_id: agent:claude_code`). Prior task notes
  last saw it OPEN DRAFT at `13b268f0`; treat that as unverified.

A committed test cannot be added against machinery that does not exist. What this experiment
does is **de-risk that test completely**: the mechanism is proven, the exact assertions are
known, and the numbers above are the expected values.

## Reproduction

```bash
cd experiments/pids_axis_cgroup_enforcement_20260805
systemd-run --user --scope --collect -p TasksMax=64 -- python3 pids_worker.py breach  200
systemd-run --user --scope --collect -p TasksMax=64 -- python3 pids_worker.py control 8
./kill_bracket.sh
```

Baseline `pids.current` (3 here) is environment-dependent — assert the **causal equation**
(`baseline + forked == pids.current == pids.max`) and the **events transition**, never a
hard-coded fork ordinal.

## Test placement, once PR #8 lands

New `py/tests/test_pids_cgroup_integration.py` (cleaner than extending the classifier-only
`py/tests/test_experiment_cli.py`). It should launch through `systemd-run --user --scope
--collect`, then assert:

1. `pids.max` readback == requested cap, and self ∈ `cgroup.procs` *(bind to the running thing)*
2. `baseline + children_forked == pids.current == pids.max` at peak
3. first refused fork carries `errno == EAGAIN(11)`
4. `pids.events max` 0→1 on breach, and **0→0 on the within-cap control**
5. `cgroup.kill` → all members dead, bracketed against a no-kill arm that survives
6. scope directory removal / unit `LoadState=not-found` after collection

Additive test only; the LINEAR agent-utils gate applies.

## Concurrency

Run solo, as instructed. `ci-hub validate-lock status` reported the lease **LAPSED and
reclaimable**, owner `hermit-coord` pid 1714110 **proven dead** (recorded `boot_id`
`b9da0208…` ≠ current `5367be51…`, i.e. the host rebooted under it). No live holder, no
concurrent validate, so the `detcore_misc` livelock risk did not apply. The lease was
deliberately **not** seized: these are second-scale cgroup probes, not a validate or bench,
and holding the box-exclusive lease for them would block real producers for no benefit.

## Safety

Every kill was `cgroup.kill` on a transient unit created moments earlier by this experiment,
containing only its own children — never a name/pattern/`-f`-substring kill (Hard Invariant 15).
Post-run audit: zero leftover units, zero leftover cgroup directories, zero stray processes.
