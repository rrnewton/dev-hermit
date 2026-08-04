# dbi times-probe loop — boxed reap VERIFICATION (2026-08-04)

**Task:** `dbi_times_probe_loop` (the *source* of the recurring boxing-coverage-gap orphan
burst). **Author:** hermit-dbi (Opus 4.8). **Sibling:** the general whole-tree box is
verified separately in `ai_docs/boxing-coverage-gap-whole-tree-reap-verification_20260804.md`
(the `safe-ci-dag-runner` path); THIS note verifies the ad-hoc **bare-probe** path that
bypassed that box.

## The leak (measured, previously)

The times(2)/getrusage(2) parity investigation repeatedly ran, **bare**:

```
worktrees/dbi/hermit/target/debug/hermit run --backend=kvm --strict --verify \
    scratch/times-probe/probe
```

`--backend=kvm --strict --verify` hits the KVM verify-hang (memory
`hermit-run-verify-hang-holds-namespace`): the hermit process ignores SIGTERM and spins in
state R. Run bare inside the agent's `3pai_sandbox` scope, when the launching agent dies the
hang **reparents to ppid=1 and burns a core forever** — systemd removes a scope only when
EMPTY, so the ad-hoc scope never throttles or reaps it. Observed ≥4 successive instances in
one session (PIDs 570827, 748315, 1039384, … each reaped by explicit owner-authorized PID kill
and replaced within minutes). This is **adoption**, not a boxing-mechanism failure: the bare
probe simply never entered a box that owns its cgroup.

## Fix

Route every probe iteration through a **transient `systemd-run --user` unit** so *systemd*
(the user manager, PID 27446 here), not the launching shell, owns the cgroup. The wrapper is
`scratch/times-probe/boxed-probe.sh` (scratch is transient; the load-bearing body is embedded
below for reproducibility). Load-bearing properties:

- `RuntimeMaxSec=<cap>s` — hard wall cap; systemd stops the unit on breach regardless of
  launcher liveness.
- `TimeoutStopSec=5s` + default `KillMode=control-group` — SIGTERM then SIGKILL to the
  **whole cgroup**, reaping the SIGTERM-ignoring hung hermit AND any reparented orphan.
- `--collect` — the unit is removed after death, so **no empty scope lingers**.

The critical distinction from a naive `trap … EXIT; kill $pid` cleanup: **a cleanup path that
runs on normal exit does not cover a hung child.** Because the unit is owned by systemd, not
the launcher, reaping fires even when the launcher (or the whole agent) is killed mid-hang.

```bash
unit="times-probe-${backend}-$$-${idx}"
systemd-run --user --unit="$unit" \
  --working-directory="$(dirname "$HB")" \
  --setenv=PATH="$PATH" --setenv=HOME="$HOME" \
  --property=RuntimeMaxSec="${maxsec}s" \
  --property=TimeoutStopSec=5s \
  --collect \
  -- /bin/bash -c "exec '$HB' run --backend='$backend' --strict --verify '$PROBE' > '$log' 2>&1"
# classify on the log (Result= is gone after --collect):
#   "Determinism verified" present => completed (exit 0); absent => killed-on-breach (exit 124)
```

### Bounding the loop (point 2)

Boxing each *run* is necessary but not sufficient — the original leak was an **unbounded**
ad-hoc loop, so a durable driver must also make the *loop* itself unable to stall. The
bounded driver (`scratch/times-probe/bounded-loop.sh`, body embedded here since scratch is
transient) runs a **fixed iteration count** and delegates every iteration to `boxed-probe.sh`.
Because each boxed iteration is guaranteed to return within `maxsec + TimeoutStopSec` (systemd
enforces `RuntimeMaxSec` irrespective of launcher liveness), **a hung run cannot outlive its
iteration and the loop cannot run forever**:

```bash
for i in $(seq 1 "$iters"); do
  log="$logdir/loop_${backend}_${i}.log"
  if HB="$HB" PROBE="$PROBE" ./boxed-probe.sh "$backend" "$maxsec" "$log" "$i"; then
    completed=$(( completed + 1 )); else killed=$(( killed + 1 )); fi
done
echo "LOOP DONE backend=$backend iters=$iters completed=$completed killed=$killed"
```

## Verification (this session, HB = worktrees/dbi/hermit/target/debug/hermit @ 03:31 build)

Environment: `/dev/kvm` present; `systemd --user` running (degraded but functional),
`XDG_RUNTIME_DIR=/run/user/212630`. The probe reads times()+getrusage() 5× across 4×4096
`getpid` (see `scratch/times-probe/probe.c`).

Guarded behavior bracketed from **both** sides:

1. **Positive — normal iterations fire and are unharmed. N=3, N STATED.**
   `boxed-probe.sh ptrace 45 <log> {1,2,3}` → all three `OUTCOME=completed`, launcher exit 0,
   `"Determinism verified"`×1 each. The box does not break legitimate completing runs.

2. **Negative (a) — hung iteration is refused, launcher alive.**
   `boxed-probe.sh kvm 8 <log> 1` → KVM verify-hang (log stuck at `:: Run1...`, no
   "Determinism verified"); wrapper classified `OUTCOME=killed-on-breach(cap=8s)`, exit 124.
   After teardown: `systemctl --user status` → "could not be found" (**scope disappeared** via
   `--collect`); **zero ppid=1 hermit survivors**; zero cgroup members left running.

3. **Negative (b) — hung iteration is reaped EVEN WHEN THE LAUNCHER DIES (the real bug).**
   Launched `boxed-probe.sh kvm 30 <log> 9` in background; while the unit was `active`,
   confirmed the hung hermit's **PPID = 27446 = `/usr/lib/systemd/systemd --user`** (NOT the
   launcher). Then `kill <launcher_pid>` (single own-child PID). Unit stayed `active` (systemd
   owns it). RuntimeMaxSec then fired autonomously: unit → inactive/dead and removed; **both**
   hermit cgroup members (MainPID 3260185 + child 3260240) SIGKILLed; **zero ppid=1
   survivors**. This is the exact failure mode (agent dies → orphan burns a core forever) now
   covered.

**Global clean check after all tests:** no leftover `times-probe-*` `--user` units; zero
ppid=1 hermit survivors for this HB; zero processes still running this HB.

### Re-verification 2026-08-04 ~11:40 UTC (respawn, coordinator re-issue)

Re-run fresh through the **bounded loop driver** on the same HB (03:31 debug build), producing
current evidence rather than relying on the earlier logs:

- **Pre-scan:** zero ppid=1 `times-probe/probe` survivors; zero `times-probe-*` `--user` units
  (no acute leak outstanding to reap — the KVM-strict-verify core-burner class was already
  clear; other ppid=1 orphans present were the *separate* e2e/parity-fixture class in other
  worktrees, left untouched).
- **Positive:** `bounded-loop.sh ptrace 3 60` → `completed=3 killed=0`; all three
  `OUTCOME=completed` (waited 10–13 s each). **N=3, unharmed.**
- **Negative:** `bounded-loop.sh kvm 1 8` → `completed=0 killed=1`;
  `OUTCOME=killed-on-breach(cap=8s)`. Hang confirmed (log stuck at `:: Run1...`, no
  "Determinism verified"). Immediately after: **zero ppid=1 survivors** (the hung hermit was
  still *inside* the cgroup in `deactivating final-sigterm`, i.e. being SIGKILLed, not orphaned
  to init). Within ~3 s of teardown the **scope DISAPPEARED** (no `times-probe-*` unit remained
  — did not linger empty); zero processes running the probe. Confirms the coordinator's three
  criteria: zero ppid=1 survivors · N=3 normal iterations unharmed · scope disappears.

## Process-kill safety

Every kill in this verification targeted a **single PID that was my own child** (the launcher)
or was performed by **systemd's own cgroup teardown**. No `pkill`/`killall`/pattern/name/`-f`
kill was used (Hard Invariant 15). Pre-existing ppid=1 processes belonging to other agents
(dbt-compat, kvmpar, vforkverify, experiments) were observed and **left untouched**.

## Disposition / recurrence

The *immediate* source is also self-liquidating: the times(2) investigation that drove this
loop is **settled and routed to the owner-decision queue** (note
`owner-decision-times2-vs-getrusage-continuity`; Option C — zero tms_* — correctly rejected as
a frozen clock; Options A/B await owner), so no further times-probe loop should run. The
**general** lesson persists as memory: any bare `hermit run --backend=kvm --strict --verify`
(or any KVM verify-hang-prone invocation) run outside a box that owns its cgroup can orphan and
burn a core — route it through `systemd-run --user` (RuntimeMaxSec + TimeoutStopSec +
`--collect`) or `safe-ci-dag-runner run`. Reaping individual orphans is whack-a-mole; boxing
the invocation is the source fix.
