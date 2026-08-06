# Boxing coverage gap: which layer leaks, and why a throttle is not a fix

**Task:** `close_boxing_coverage_gap` (P1)
**Date:** 2026-08-05 (runs stamped 2026-08-06T02:4xZ UTC)
**Host:** `devbig014`, kernel `6.18.39-0_fbk0_hardened_0_ga43d5727b443`, systemd 259, cgroup2fs
**Repos:** parent `1c27dfd4`, hermit `b64d893a`, agent-utils `570e7865`
**Mode:** local only (egress refused all session), investigation only — livelock-safe, no concurrent validate

The directive was: **confirm the layer first** — walk the live cgroup tree and see what is actually
holding a scope open before proposing a fix. That ordering turned out to matter, because the
obvious fix (put a CPUQuota on the agent slice) is measurably a **half-fix**.

---

## Headline

| Question | Answer |
|---|---|
| Is a leak live right now? | **No.** 7/7 agent scopes have live owners; 0 leaked guests |
| Which layer leaks? | **The guest subtree one layer BELOW hermit** — `bash`/`sh`, not the hermit binary |
| Does the agent slice enforce anything? | **No.** `cpu.max=max`, `memory.max=max`, `pids.max=max` |
| Does a scope survive its main process? | **Yes** — if any process remains. Confirmed empirically |
| Does `CPUQuota` fix it? | **Throttles, never reaps.** 100.3% → 20.0% of a core, process still alive, scope still held |
| What actually clears it? | **`cgroup.kill`** — scope gone, escapee dead |

**The fix needs BOTH a throttle and a reaper.** A quota alone downgrades "burns a whole core
forever" to "burns 20% of a core forever" — the scope is still pinned and the leak is still unbounded
in time. That is the single most important result here.

---

## 1. Current state: no live leak (measured, not assumed)

```
scope                              owner     status                  nproc
run-p181393-i4361539.scope         181393    OWNER-ALIVE (claude)        6
run-p213990-i213991.scope          213990    OWNER-ALIVE (metacode.real) 1
run-p236460-i4412086.scope         236460    OWNER-ALIVE (claude)        3
run-p3899038-i3899039.scope        3899038   OWNER-ALIVE (codex)         4
run-p4071341-i4071342.scope        4071341   OWNER-ALIVE (claude)        8
run-p4181961-i4181962.scope        4181961   OWNER-ALIVE (claude)        3
run-p68533-i4250863.scope          68533     OWNER-ALIVE (claude)        3
```

**Zero orphaned scopes.** The only two `ppid=1` residents box-wide are legitimate infrastructure
daemons in this agent's own scope (`guardrails_srv` 10 CPU-s, `privacy-aware daemon` 6 CPU-s) —
not leaked test guests. Box-wide CPU scan found no runaway: the only >50% processes are `orc`
(114%, the orchestrator) and `chef-client` (55%, 31 s old).

The acute burst documented on 2026-08-04 (17 procs / 2 dead scopes / ~752 CPU-hours) is **cleared**,
consistent with the owner-authorized PID reaps and the closure of `dbi_times_probe_loop` (the
recurring source). **The structural gap below persists regardless** — it is a property of the
container, not of whether anything is leaking this minute.

## 2. The agent sandbox enforces nothing

```
3pai_sandbox.slice   cpu.max      max 100000     <- "max" = no quota
                     memory.max   max
                     pids.max     max
                     cpu.weight   100            <- share, not a cap
```

And a delegation detail that constrains the fix:

```
user@.service       cgroup.subtree_control : cpuset cpu io memory pids
3pai_sandbox.slice  cgroup.subtree_control : io memory pids          <- no cpu
3pai_sandbox.slice  cgroup.controllers     : cpuset cpu io memory pids
```

Because `cpu` is **absent from `3pai_sandbox.slice`'s `subtree_control`**, the per-agent scopes
expose **no `cpu.max` file at all** — only `cpu.pressure` / `cpu.stat`. `memory.max` and `pids.max`
*are* present per-scope (those controllers are delegated). So today you cannot write a per-agent CPU
cap directly; you would have to enable `+cpu` first.

**Mitigating mechanism finding:** systemd does this for you. In the quota arm below, passing
`-p CPUQuota=20%` produced a live `cpu.max` readback of `20000 100000` on a scope that otherwise has
no `cpu.max` — i.e. requesting a quota through systemd causes it to enable the controller in the
parent's subtree. A CPU cap is therefore reachable through unit configuration without hand-editing
`subtree_control`.

## 3. The leak mechanism, reproduced (`leak_mechanism.json`)

A scope whose main process exits immediately while a `setsid` escapee (reparented to init) keeps
running — the exact shape of a hermit test whose supervisor dies and leaves its guest behind:

| arm | CPUQuota | `cpu.max` readback | scope survives main exit | procs | escapee CPU | alive after window | reaped |
|---|---|---|---|---:|---|---|---|
| no-quota | none | *(no cpu.max file)* | **yes** | 1 | **100.3 %** of a core | **yes** | **no** |
| with-quota | 20 % | `20000 100000` | **yes** | 1 | **20.0 %** of a core | **yes** | **no** |
| *either, after `cgroup.kill`* | — | — | **no (gone)** | 0 | — | **no (dead)** | **yes** |

Read the two middle rows together — that is the whole finding:

- **`CPUQuota` works as advertised as a throttle.** 100.3 % → 20.0 %, exactly the configured share.
- **`CPUQuota` never reaps.** The escapee is still running and the scope is still pinned. systemd
  removes a scope only when it is **empty**, and a throttled spinner is never empty.
- **`cgroup.kill` reaps atomically.** One write; scope gone, process gone.

So *"put a CPUQuota on the agent slice"* — fix option (A) as originally written — bounds the blast
radius but **does not close the leak**. It converts an unbounded core burn into a bounded one that
still runs forever and still holds its cgroup. Throttle and reap are independent properties and the
gap needs both.

## 4. Which layer leaks — confirmed at source

The directive's note is correct, and it is confirmed twice over.

**Structurally**, from the experiment: the survivor is whatever is left behind after the supervisor
exits. It is layer-agnostic; the reason it is observed as `bash` is that hermit's *guest* is one
layer below hermit, so when the hermit supervisor dies its guests reparent to init.

**At source**, hermit `b64d893a`, `hermit-cli/tests/cli.rs`:

- helpers spawn via `Command::new(env!("CARGO_BIN_EXE_hermit"))` + `.output()` (:36-38) / `.spawn()` (:43-48)
- **`grep -c kill_on_drop` → 0**; no `process_group`, no timeout, no subtree containment anywhere

Nothing in the harness reaps a guest subtree. So a PATH shim named `hermit` is **doubly wrong**: it
wraps the layer that already exits cleanly, and it misses explicit-path invocations
(`./target/{debug,release}/hermit`) entirely. **Wrap the tree root (cargo / bash), not the binary.**

---

## 5. The fix

Enforcement must be **defense in depth with two independent properties**, because the experiment
shows one without the other is incomplete:

### (a) Throttle — bound the blast radius *(necessary, insufficient)*

A systemd drop-in on `3pai_sandbox.slice`:

```ini
# ~/.config/systemd/user/3pai_sandbox.slice.d/50-cap.conf
[Slice]
CPUQuota=<N>%          # collective ceiling across all agents
MemoryMax=<M>G
TasksMax=<T>
```

Applies today (the slice is `LoadState=loaded`, currently all `infinity`), and requesting `CPUQuota`
makes systemd enable the `cpu` controller. This caps a runaway's cost — it will **not** remove it.

### (b) Reap — actually clear it *(the part a quota cannot do)*

A periodic sweeper over `3pai_sandbox.slice/*.scope` that, for each scope, writes `cgroup.kill` when:

1. the scope's owning agent PID (`run-p<PID>-…`) is **dead**, **and**
2. the scope is non-empty.

Both signals are cheap and already used above. `cgroup.kill` is the correct primitive: it is atomic
over the whole subtree, so it catches `setsid`/double-fork escapees that a `killpg` misses — proven
in both arms here and in the sibling pids experiment
(`experiments/pids_axis_cgroup_enforcement_20260805/`, kill-arm A: 14 members → 0, bracketed against
a no-kill arm where 14/14 survive).

**Owner constraint noted:** a scope-level reaper was previously forbidden as "a second reaper."
This evidence is the argument to revisit that specifically — not to overrule it. The two-signal
condition above (owner dead **and** non-empty) cannot touch a live agent's work, which was the
original objection. If it stays forbidden, the residue is unclosable by adoption alone and needs a
hermit-side self-watchdog or route-everything discipline.

### (c) Adoption — route tree roots through the existing box *(already-built, unchanged)*

The whole-tree reap already exists and is verified on agent-utils main: `teardown.py::reap` writes
step `cgroup.kill` first, `scheduler.py` reaps after every step. Prior verification
(`ai_docs/boxing-coverage-gap-whole-tree-reap-verification_20260804.md`) proved parts 1/2/3. Route
`cargo test`, `test_harness.sh`, and ad-hoc runs through it. This closes the routed paths and is
**complementary to (b)**, which is the backstop for everything not routed.

**Do not build a competing wrapper** — agent-utils had a 14-branch boxing pile-up and a
one-PR-in-flight serialization rule.

---

## Reproduction

```bash
cd experiments/boxing_coverage_gap_layer_and_reap_20260805
./leak_mechanism.sh      # both arms + cgroup.kill cleanup, ~15s
```

Assert the **relationship** (unthrottled ≈ 100% of a core vs throttled ≈ the configured share, both
alive and both holding the scope), not absolute jiffy counts.

## Safety

Every kill was `cgroup.kill` on a transient unit this experiment created seconds earlier, containing
only its own children — never a name/pattern/`-f` kill (Hard Invariant 15). No pre-existing process
was signalled; the two `ppid=1` infra daemons were observed only. Post-run audit: 0 leftover units,
0 leftover cgroups, 0 surviving spinners.
