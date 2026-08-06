# `memory.oom.group` is already deployed — and it is half of a two-part design

**Task:** `oom-kill-lands-on-innocent-neighbour` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress.

## Answer

**The change is already implemented, in both engines, and deployed at the pinned agent-utils commit.**
I re-ran the planting bracket myself and it passes both directions. The open item is not the setting —
it is the half of the design that pairs with it.

## 1. Deployment state (verified by ancestry, not by reading a note)

| thing | state |
| --- | --- |
| parent gitlink for `agent-utils` | `570e78655e4cbfd398748b278252bfbaf4cc5930` |
| canonical `agent-utils/` checkout | same SHA — **pin and checkout agree** |
| Python engine `py/safe_ci_dag_runner/cgroup.py:582` | `(child / "memory.oom.group").write_text("1")`, best-effort with a warn on failure |
| Rust engine `rs/safe-ci-dag-runner/src/cgroup.rs:402-425` | `enable_outer_oom_group()`, **write-then-read-back**, not trusted until the kernel file reads `1` |
| commits that added it | `e94924a` (per-step), `af76a8a` (Rust↔Python parity warn), `4affd42` (outer containment) |
| are they ancestors of the pin? | **yes — deployed** |

> **I got this wrong first and am recording the correction.** My initial grep suggested the Python
> engine never wrote `memory.oom.group` — which would have made the fix inert in the engine that
> actually runs (the runner resolves to Python). That was a `head`-truncation artifact of my own
> command: `cgroup.py` has 32 matches and does write it. There is even an explicit Rust↔Python parity
> commit. **No engine-parity gap here.**

**Live cgroup snapshot is inconclusive, and I am not going to dress it up:** all 41 non-empty user
cgroups on this box currently read `memory.oom.group=0`, but every one of them is an agent shell, a
tmux scope or a 3pai sandbox — **no boxed DAG step is running** (the validate ledger has had no writes
for ~26 h). The snapshot shows "nothing boxed is running now", not "the fix is inert".

## 2. Planting bracket — re-run independently

Using the committed harness `experiments/oom-group-blast-radius_20260804/inside.sh` under
`systemd-run --user --scope -p Delegate=yes -p MemorySwapMax=0`, delegated cgroup with `+memory`,
per-step children at `memory.max=64 MiB`, `swap.max=0`:

| case | result |
| --- | --- |
| **CASE1 — fix, `oom.group=1`, over-cap** | sentinel **DEAD** + allocator **DEAD**, `oom_group_kill=1` → the step dies as a **unit** |
| **CONTROL — `oom.group=0`, same over-cap** | allocator DEAD, **sentinel ALIVE**, `oom_group_kill=0` → half-dead step; **proves the fix is not inert** |
| **CASE2 — offender vs neighbour** | **OFFENDER DEAD** (`oom_group_kill=1`), **NEIGHBOUR ALIVE** (`oom_kill=0`) → blast radius contained |
| **CASE3 — positive control, N=10 legitimate steps at 32 MiB under a 64 MiB cap** | **alive 10/10**, `total_oom_kill=0` → not over-eager |
| **CASE4 — cleanup** | 14 child cgroups drained and removed, 0 residue |

Both directions the task demanded are satisfied: the hog dies and the neighbour survives, **and** a
normal set of jobs all complete untouched. Harness exit 0.

## 3. The part that is not done: this is half of a two-part design

Earlier today, in `experiments/cmake_mtime_vs_content_20260806`, I measured the following taxonomy of
what happens to a partially-written build artifact:

| how the compile dies | `.DELETE_ON_ERROR` | artifact |
| --- | --- | --- |
| recipe exits 1 (ordinary compile error) | fires | deleted ✅ |
| recipe's child SIGKILLed, `make` survives | fires | deleted ✅ |
| **whole cgroup SIGKILLed as a unit** | **never runs** | **survives, 0 bytes** ❌ |

**`memory.oom.group=1` is exactly the mechanism in that third row.** CASE1 above proves it kills the
whole step as a unit — which is the point — and that same property means `make` dies before it can run
its own cleanup. The 0-byte object then has a fresh mtime, so `gmake -q` reports **"up to date"
forever** and the next link fails with an `undefined reference` naming an unrelated source symbol.

This is **not an argument against the change.** Containment is correct: a wrong-victim kill is worse.
It is an argument that the two halves ship together, because the containment half alone trades
*"someone else's job dies"* for *"the build tree is permanently poisoned"*.

### Where the paired half actually stands

* **Artifact-integrity half: LANDED.** `purge_zero_byte_objects` is on hermit `origin/main`
  (`validate.sh:872-878`, called at `:1002`). Good — the pairing exists.
* **But it is `*.o`-only:** `find "$root" -type f -name '*.o' -size 0`.
* **And the one live corrupt CMake artifact on this box right now is a `.so`** —
  `worktrees/226/…/dynamorio-build/clients/lib64/release/libdrpoints.so`, 0 bytes, 2026-08-04 11:44:50.
  The landed purge **does not see it**.

So the residual is one line: widen the purge past `*.o` to `.a/.so/.rlib/.lo`, and prefer the 4-byte
ELF-magic predicate over `size == 0` (`size` is a proxy for "corrupt"; magic also catches
truncated-to-N-bytes). Cost measured on the real 834-object / 407 MiB DynamoRIO tree: **0.25 s**.

## 4. Honest scoping

The task's own note already says this and I am reinforcing it rather than quietly letting it drift:

* **The 21-row drain-unlock justification is REFUTED.** hermit-liteinst read all 21 band logs: zero
  hits for `memory.oom` / `oom-kill` / `memory.max` / `killed process` / `out of memory`. The band is
  the safe-ci-dag-runner **eager-exit stop point**, not OOM-on-neighbour. Exactly 1 of 21 is a real
  truncation, and its cause is an external SIGTERM.
* **So this is correct-but-not-a-drain-unlock, P2.** It recovers no PRs. Its value is that a future
  OOM lands on the offender instead of a bystander — real, but prospective.
* **No evidence of the misattribution ever having fired in the recorded corpus.** I did not find a
  single ledger row attributable to an innocent-neighbour OOM kill. The change is justified on
  principle (box untrusted compute; a kill must be attributable to its cause), not on an observed
  incident.

## Recommendations

1. **Nothing to implement for the setting itself** — it is deployed and passes both directions.
2. **Widen `purge_zero_byte_objects` past `*.o`** and switch `size == 0` to the ELF-magic predicate.
   This is now owed, not optional: the containment half is live, so the poisoning mode it creates is
   live too.
3. **Consider the atomic compiler launcher** (`CMAKE_<LANG>_COMPILER_LAUNCHER` → compile to a temp path,
   rename on success) as the durable prevention. Prototyped and bracketed in
   `experiments/cmake_mtime_vs_content_20260806`: under an identical group kill the naive recipe leaves
   a poisoned 0-byte target while the atomic one leaves no target at all, so make rebuilds normally.
4. **Confirm the setting on a live boxed step.** My snapshot could not, because nothing was boxed at
   the time. The Rust engine already write-then-reads-back; the check worth adding is that a *step*
   cgroup reads `1` during a real DAG run, recorded in the run's own log.

## Reproduction

```bash
cd experiments/oom-group-blast-radius_20260804
systemd-run --user --scope -q -p Delegate=yes -p MemorySwapMax=0 --unit=oomverify-$$ ./inside.sh
# CASE1 unit-kill · CONTROL half-dead · CASE2 offender-dead/neighbour-alive · CASE3 10/10 · CASE4 cleanup
```
