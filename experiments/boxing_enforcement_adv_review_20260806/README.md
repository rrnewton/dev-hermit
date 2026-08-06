# Adversarial review: do the boxing/enforcement artifacts actually fire?

**Task:** `adv-review-boxing-artifacts` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress.

The bar the task sets: **a breach must be killed, not limited on paper.** So every verdict below rests
on a planted breach run against the *shipped* runner, not on reading a config.

## Verdicts — denominator 6 artifacts

| # | artifact | verdict |
| --- | --- | --- |
| 1 | `oom.group` offender-kill | **REAL** |
| 2 | timeout audit at port time (wall→CPU) | **wall REAL · CPU INERT AT DEPLOYMENT (0/55)** |
| 3 | pmu-concurrency-cap | **ABSENT** — proposed, not in the canonical tree |
| 4 | systemd-run validate-producer path + bpfjailer resolution | **REAL**, both sides bracketed |
| 5 | validate.sh-duplicates-product-functionality | **OUT OF SCOPE** — not an enforcement mechanism |
| 6 | cmake content-hash validation | **REAL BUT NARROW** |

**3 REAL · 1 half-inert · 1 absent · 1 out of scope.**

## The core evidence: planted breaches against the shipped runner

`agent-utils/common/bin/safe-ci-dag-runner run --dag …` (engine resolves to **python**), three tiny
DAGs in this directory.

```
[safe-ci] outer cgroup audit: memory.max=562300178432 (bound), memory.swap.max=0 (disabled),
          memory.oom.group=1 (enabled), cpu.max=max 100000 (bound)
safe-ci-dag-runner: cgroup boxing ACTIVE (two-level cgroup-v2 scope; per-step memory/CPU caps
          + setsid-proof teardown).
```

| plant | outcome |
| --- | --- |
| **memory breach** — allocate 400 MiB under a 64 MiB inner cap | `✗ FAIL (0s, OOM-KILLED (hit inner MemoryMax; 3 oom_kill event(s)))`, cap≈64 MiB / peak≈64 MiB. **`MEM BREACH SURVIVED` never printed.** |
| **wall breach** — `sleep 120` with `timeout: 5` | `✗ FAIL (5s, TIMEOUT >5s)`, wall-clock elapsed **5s, not 120s**. **`WALL BREACH SURVIVED` never printed.** |
| **positive control** — two well-behaved steps under cap | `PASS - 2 passed, 0 failed, 0 aborted`, runner exit **0** |

So memory and wall enforcement **kill**, and the mechanism is **not over-broad** — the control passes
untouched.

**This also closes a gap I left open in the previous task.** There I could not confirm
`memory.oom.group=1` on a *live* boxed step because none was running. The runner's own audit line
above shows it **enabled on a real boxed run**, self-reported. That is the Proxy-Binding property done
right: the value travels with the run that produced it, so no consumer has to infer it from config.

### Methodological caveat, learned the hard way

My first attempt put all three planted steps in **one** DAG. The memory breach failed first, and
eager-exit **aborted the other two** — masking both the wall plant and the positive control:

```
[control.wellbehaved] ⊘ ABORT (1s — eager-exit after another step failed)
[plant.wall_breach]   ⊘ ABORT (1s — eager-exit after another step failed)
```

**One plant per DAG, or the verification is misleading.** (This is the same eager-exit that
hermit-liteinst identified as the real cause of the "truncated middle band" — worth knowing it also
sabotages naive enforcement testing.)

## Per-artifact detail

### 1. `oom.group` offender-kill — REAL

Deployed at the parent's agent-utils pin `570e7865` (both engines; rust writes **and reads back**).
Live audit shows `memory.oom.group=1 (enabled)`. My own bracket
(`experiments/oom_group_deployment_audit_20260806`): offender DEAD / neighbour ALIVE; the
`oom.group=0` control leaves a **half-dead** step, proving it is not inert; 10/10 legitimate steps
survive.

### 2. Timeout audit at port time (wall→CPU) — wall REAL, CPU INERT

The **wall** half is genuinely enforced (killed at 5s, measured above).

The **CPU** half has not been ported. Counting declarations across both lanes: **0 of 55 steps declare
`cpu_timeout`.** The single occurrence of the string in `portable.json` is inside a *description*
explaining why it was deliberately not set on `test.detcore_unit`/`test.detcore_misc` (detcore
livelocks under load; reverie#355 is the fix, and a budget derived from pre-355 hang behaviour would be
wrong — a defensible reason, and disclosed).

The sharp part is the **capability string**. Both engines advertise:

```
{"cpu_affinity":true,"cpu_timeout":true,"memory_max":true,"oom_detection":true,
 "pids_guard":false,"wall_timeout":true}
```

`"cpu_timeout":true` is true **of the engine** and 0/55 **of the deployment**. A consumer reading that
capability to mean "CPU budgets are enforced" would be wrong for every step in the fleet. Note the
same string honestly reports `"pids_guard":false` — so the format *can* express "not enforced"; the
gap is that engine-capability and deployment-adoption are being reported on one axis.

### 3. pmu-concurrency-cap — ABSENT

No PMU cap exists in the canonical tree: nothing in `ci-hub/runners/*.py` or `hermit/ci/`. Every hit is
in another agent's in-flight `.claude/worktrees/…` copy. The concurrency caps that *do* exist and *are*
enforced are `resource_caps {"hermit_guest": 1, "manifest_guest": 4}` (consumed in
`agent-utils/py/safe_ci_dag_runner/sizing.py:122`). So this artifact is **proposed, not deployed** —
distinct from inert, and it should not be counted as coverage.

### 4. systemd-run producer path + bpfjailer resolution — REAL, both sides

| direction | result |
| --- | --- |
| agent sandbox tries to create its own cgroup: `mkdir /sys/fs/cgroup/user.slice/user-<uid>.slice/probe` | **Permission denied** |
| admission path: `systemd-run --user --scope -p Delegate=yes` | **succeeds**, lands in its own `…/app.slice/<unit>.scope` |

The design holds: the only path that can box is the wrapper, and the direct path is denied rather than
silently unboxed. `ci-hub/validate/start_unit.py:127` defaults the profile to `full`
(`*(validate_args or ["full"])`), so the producer does not silently mint a narrow receipt.

### 5. validate.sh-duplicates-product-functionality — OUT OF SCOPE

I looked at it and it is **not a boxing/enforcement artifact** — it is an open P0 (owner hermit-coord,
27 notes) about bash reimplementing product behaviour. There is no breach to plant. I am recording it
as unreviewed-on-enforcement-grounds rather than assigning it a verdict it cannot have.

### 6. cmake content-hash validation — REAL BUT NARROW

`purge_zero_byte_objects` has **landed** on hermit `origin/main` (`validate.sh:872-878`, called at
`:1002`). I planted against it earlier today: **2 zero-byte `.o` purged, healthy `.o` and `.a`
untouched, missing root → 0.** It fires.

Two limits: it is `find … -name '*.o' -size 0`, i.e. **`.o`-only** — and the one live corrupt CMake
artifact on this box right now is a **`.so`** (`…/dynamorio-build/clients/lib64/release/libdrpoints.so`,
0 bytes) that it does not see. And `size == 0` is a proxy for "corrupt"; the 4-byte ELF-magic predicate
also catches truncated-to-N-bytes and costs **0.25 s** on the real 834-object / 407 MiB tree.

This matters more now that artifact 1 is live: `oom.group=1` is precisely what defeats make's
`.DELETE_ON_ERROR` (a whole-cgroup kill takes `make` with it), so the containment half is creating the
poisoning mode that this half is supposed to clean up.

## What I would change

1. **Split the capability string into engine-capability vs deployment-adoption**, or emit
   `cpu_timeout: 0/55 steps` alongside it. Today one field answers two questions.
2. **Widen `purge_zero_byte_objects` past `*.o`** and prefer ELF-magic over `size == 0`. Owed, not
   optional — the containment half is live.
3. **Do not count pmu-concurrency-cap as coverage** until it exists in the canonical tree.
4. **Keep the runner's audit line.** It is the best-behaved thing in this review: it states the
   enforcement it actually applied, on the run that applied it.
5. **Any future enforcement verification: one plant per DAG.** Eager-exit will otherwise abort your
   controls and you will report a pass you did not measure.

## Reproduction

```bash
cd experiments/boxing_enforcement_adv_review_20260806
R=agent-utils/common/bin/safe-ci-dag-runner
$R run --dag wall.json    # -> FAIL (5s, TIMEOUT >5s), exit 1
$R run --dag ctrl.json    # -> PASS 2/2, exit 0
$R run --dag dag.json     # -> mem_breach OOM-KILLED; NOTE the other two get eager-exit ABORTed
```
