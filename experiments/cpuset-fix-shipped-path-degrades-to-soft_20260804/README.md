# cpuset fix (PR #15) SHIPPED PATH degrades to soft in-sandbox — mutation-verified (2026-08-04)

## Question
Companion to `cpuset-pin-mechanism-mutation-verified_20260804/` (which tested the RAW
mechanisms in isolation). That experiment concluded "build on AllowedCPUs; systemd `--user`
scopes land under app.slice where cpuset IS delegated." This experiment asks the narrower,
decision-relevant question: **does the fix's ACTUAL shipped code path — `apply_core_box()`
called after `reexec_in_scope()` — deliver a HARD, inescapable core box in THIS 3pai
sandbox?** Proven by mutation (request K cores; confirm a running child cannot reach a
K+1th core), through the runner function, not a hand-rolled `-p AllowedCPUs` one-liner.

## Call path (traced, agent-utils branch `codex/runner-cpuset-core-box` @ 1c7c8556)
- `cli.main` → `cg.reexec_in_scope(argv, memory_max=None)` (cgroup.py:526-601). Builds
  `systemd-run --user --scope --collect -p Delegate=yes -p MemorySwapMax=0
  [-p CPUQuota=...%] [-p MemoryMax=...] --slice=<aggregate>`. **No `AllowedCPUs`, no cpuset
  property.**
- Opt-in `--cores K` → `cg.apply_core_box(K)` (cli.py:1391 → cgroup.py:985). PRIMARY:
  `_try_cgroup_cpuset(scope, cores)` writes `cpuset.cpus` on the scope IFF
  `"cpuset" in scope/cgroup.controllers` (cgroup.py:968). FALLBACK: `sched_setaffinity`.

## Method
Host devserver, `nproc=316`. Ran `apply_core_box(2)` from inside a transient
`systemd-run --user --scope` (exactly the scope shape `reexec_in_scope` creates), spawned a
child, read its inherited `Cpus_allowed_list`, then **attempted to escape** it to an excluded
core via `taskset -pc <excluded> <child>` and re-read. Hard bound masks the escape; soft lets
the child move.

## Results — the shipped path is SOFT in this sandbox
| observation | value |
|---|---|
| scope `cgroup.controllers` (plain `Delegate=yes` scope) | `io memory pids` — **NO cpuset** |
| `_try_cgroup_cpuset` outcome | returns False (no cpuset controller) → **PRIMARY skipped** |
| mechanism actually used | `sched_setaffinity` (FALLBACK) |
| requested K | 2; cores picked `[1, 2]` |
| child inherited | `Cpus_allowed_list: 1-2` |
| escape `taskset -pc 0 <child>` | **rc=0, SUCCEEDED → child moved to core 0** |
| verdict | **SOFT / escapable — NOT a real bound** |

**RECORD:** `{cores: [1, 2], count: 2, mechanism: "sched_setaffinity", excluded_core: 0,
child_allowed_after_escape: "0", verdict: "SOFT/escapable"}`

## Why — and where the predecessor note was imprecise
`app.slice` lists cpuset in `cgroup.controllers` (available) but **not** in
`cgroup.subtree_control` (`io memory pids` only). `Delegate=yes` delegates only the
controllers the parent has in `subtree_control`, so a plain `--user --scope` gets **no
cpuset**. cpuset appears on the scope **only** when systemd is asked for a cpuset property:

```
# plain scope (what reexec_in_scope creates):          CTRL: io memory pids
# scope WITH -p AllowedCPUs=1-2 (systemd self-enables): CTRL: cpuset io memory pids ; EFF: 1-2
```

So the raw-mechanism experiment worked **because it passed `-p AllowedCPUs`**, which forces
systemd to enable `+cpuset` delegation. Its note generalised that to "app.slice has cpuset
delegated" — true for available, false for a plain delegated scope. **PR #15 did NOT adopt
its own recommendation** (add `-p AllowedCPUs` to `reexec_in_scope`): it writes `cpuset.cpus`
to a plain scope (which lacks the controller) and falls back to soft `sched_setaffinity`. The
fallback's `sched_getaffinity` read-back "verification" (cgroup.py:1020) cannot detect the
escape — the exact "reading back the value proves nothing" trap.

## Interpretation / fix-forward for the allocator (hermit-220)
1. The claim ("runner reads `cpuset.cpus.effective` but never writes core affinity") is TRUE
   on `main` (7c532d4). PR #15 adds a write, but as shipped it degrades to a SOFT box here.
2. For a HARD box in this sandbox the mechanism must be `-p AllowedCPUs=<reserved-set>` on the
   scope at creation (in `reexec_in_scope`), NOT `_try_cgroup_cpuset` on a plain scope. That
   makes systemd enable cpuset delegation AND set `cpuset.cpus` atomically.
3. Alternatively, enable `+cpuset` in the parent slice `cgroup.subtree_control` BEFORE
   creating the scope; then a post-hoc `cpuset.cpus` write would take. `-p AllowedCPUs` is
   simpler and is the mutation-proven-hard mechanism.
4. Bake this escape-attempt in as the allocator self-test (negative: cannot reach K+1th core;
   positive: uses all K), and record `{cores:[...], count:K}` per the owner's rule.

## Reproduction
```
cd agent-utils   # branch codex/runner-cpuset-core-box
python3 - <<'PY'  # run under: systemd-run --user --scope --collect -p Delegate=yes <this>
import sys; sys.path.insert(0,"py")
from safe_ci_dag_runner import cgroup as cg
print(cg.apply_core_box(2))   # -> ('sched_setaffinity', [..]) here: cpuset not delegated
PY
# then: sleep 5 & ; taskset -pc <excluded> $! ; grep Cpus_allowed_list /proc/$!/status
#   -> child MOVES to <excluded> (soft). Contrast with -p AllowedCPUs: escape masked.
```
