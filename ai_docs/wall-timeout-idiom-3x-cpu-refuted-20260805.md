# The wall-timeout idiom: why the 3×CPU default is refuted, and what to implement instead

**Task:** `no-hardcoded-wall-timeouts-idiom` (P1)
**Date:** 2026-08-05
**Scope:** local analysis + implementable spec. **No code written, no egress, no validate run.**
Section 6 explains why I did not implement, and exactly what would unblock it.

---

## 0. Why this document is not the change that was asked for

My dispatch asked me to *"design + implement"* a runtime default where an unset wall
timeout derives as **~3× the CPU limit**. I did not implement that, for three reasons that
all point the same way:

1. **A recorded owner decision on this very task already supersedes the 3× framing.**
   Task note, 2026-08-04: *"OWNER DECISION … adopt the DECOUPLED GENEROUS BACKSTOP, NOT
   literal 3x … WALL = a generous backstop DECOUPLED from cpu … CONSEQUENCE for PIECE 1:
   its resolver currently computes 3x*cpu fallback. Under this decision it must be REVISED
   … **Do NOT land the c7992c3 resolver as-is.**"* The 3× wording survives in the task
   *title*; the decision in the *notes* overrides it.
2. **The data refutes 3× independently** — §1 below, measured from the per-node history
   store, and it agrees with the earlier refutation without reusing its numbers.
3. **The task is PARKED** (note, 2026-08-05T02:05Z, owner stop-infra directive), the
   target repo `agent-utils` has a one-in-flight-PR cap, and its pin check cannot even run
   right now: `check-agent-utils-pin` fails with a proxy 403 because egress is down. There
   is no path from an edit to a landed change today.

Implementing the 3× rule would have produced a change that is refuted, explicitly
countermanded, and unlandable. What follows is the refutation with numbers, plus a spec
precise enough to be a mechanical edit when the park lifts.

---

## 1. Measured: what a 3×CPU wall default would actually do

Rule as dispatched: `wall := 3 × cpu_timeout`, where the shipped derivation is
`cpu_timeout = round(1.5 × max_cpu)` — so the derived wall is effectively `4.5 × max_cpu`.
Evaluated against each node's **own observed `max_wall`** over the 52 well-sampled nodes
in `ci-hub/history/query.py node-cpu-budgets`:

| outcome | nodes | share |
| --- | --- | --- |
| **derived wall BELOW the node's own observed max wall** → kills healthy runs | **18** | **35%** |
| derived wall >10× above anything ever observed → catches nothing | 12 | 23% |
| plausible (1–10×) | 22 | 42% |

**Worst misfires — the "backstop" fires below a known-healthy run:**

| node | derived wall | observed max wall | max CPU | ratio |
| --- | --- | --- | --- | --- |
| `build.flaky_harnesses` | **3.0 s** | 98.0 s | 0.86 s | 0.031× |
| `e2e.manifest_bin_c` | 54 s | 497.1 s | 12.24 s | 0.109× |
| `doc.doctests` | 57 s | 284.3 s | 12.70 s | 0.200× |
| `e2e.manifest_debugger_c` | 42 s | 203.6 s | 9.10 s | 0.206× |
| `e2e.manifest_determinism_stress` | 117 s | 506.5 s | 25.68 s | 0.231× |

**Worst no-ops — the "backstop" is far above anything reachable:**

| node | derived wall | observed max wall | ratio |
| --- | --- | --- | --- |
| `build.runtime_release` | 9999 s | 142.7 s | 70× |
| `build.privileged_tests` | 3642 s | 106.7 s | 34× |
| `test.cli` | 2781 s | 87.7 s | 32× |
| `build.workspace` | 5286 s | 257.5 s | 21× |

**Why it fails in both directions at once.** CPU and wall are not proportional per node —
in the previous pass I measured the cpu/wall ratio spanning **1774×** across nodes. A
wait-bound node (`build.flaky_harnesses`, 0.9% CPU-utilised) has tiny CPU and large wall,
so a CPU multiple is far *too small*. A fan-out node (`build.runtime_release`, 15.6×
parallel) has huge aggregate CPU and small wall, so a CPU multiple is far *too large*.
**No single multiplier can serve both classes** — the 42% that land in a plausible band do
so by coincidence of their parallelism, not by design.

The premise in the task description — *"3x CPU is the guard for a fully sequentialized
program, where wall ≈ CPU"* — is sound **for that class of node**. The error is
generalising it: most nodes are not fully sequentialized, and the ones furthest from it
are exactly where the rule breaks worst.

---

## 2. A design constraint the prior notes did not surface

The approved rule is *"wall = observed `max_wall` × ~1.5, or a large lane default,
decoupled from cpu"*. There is a problem with the first half:

> **`max_wall` is history, and the DAG runner has no history at runtime.**

`node-cpu-budgets` derives from the ci-hub history store; the runner receives only a
manifest and a step. So an observed-max-wall-based value cannot be computed *at runtime*
by a resolver. That leaves two implementable shapes, and only one satisfies the owner's
"don't hardcode in the DAG file" idiom:

| shape | where derived | satisfies "don't hardcode"? |
| --- | --- | --- |
| per-node wall generated into the manifest by a deriver | manifest-generation time | **No** — the DAG file still carries per-node numbers (derived, but hardcoded in the file) |
| **single generous lane default; per-node wall omitted** | runtime constant | **Yes** |

So the implementable form of the owner's idiom is: **omit per-node wall; let it fall back
to one generous lane constant.** The per-node `max_wall` figures then serve as the
*evidence for choosing that constant*, not as per-node values.

---

## 3. Choosing the constant — and a correction to my own first pass

My first cut was `max over nodes of (max_wall × 1.5)` = **799 s**. **That is wrong, and I
am recording the correction rather than the answer I first reached.**

`max_wall` was itself sampled under variable load. In the previous pass I measured
whole-run wall inflating **1.79×** from a quiet box to ≥6 concurrent validates. So:

| basis | value |
| --- | --- |
| largest observed `max_wall` (`e2e.manifest_language_runtimes`) | 532.8 s |
| × 1.5 safety only | 799 s |
| **× 1.79 measured load inflation** | **954 s** ← a *healthy* busy-box run can reach here |
| × 1.79 load × 1.5 safety | **1430 s** ← defensible backstop |

**799 s would kill healthy runs on a busy box.** A defensible lane constant is ~1430 s,
and the runner's existing `DEFAULT_STEP_TIMEOUT = 1800 s` sits just above it — **the
existing default is already about right.**

**Live finding, independent of this idiom:** `ci/dag/portable.json` sets
`default_step_timeout: 600`. That is **below** the 954 s a healthy worst-case node can
reach under measured load. Any node that today omits an explicit wall — or that omits one
after this idiom is applied — inherits a default that is too tight. **The 600 s manifest
default should rise to ~1800 s before any wall is stripped**, or the idiom will convert
"hardcoded but survivable" into "unset and killed under load".

For reference, applying `max_wall × 1.5` per node would **tighten every one of the 46
comparable portable nodes** (aggregate declared wall 31 980 s → 14 255 s). That is a
useful sanity check that today's hardcoded values are generous-but-arbitrary — but it is
*not* a recommendation, for the load-headroom reason above.

---

## 4. The spec to implement (when unparked)

**Resolver precedence** — three implementations must agree (Python enforcing, Rust parity,
plus `cross/differential.py` byte-for-byte serialization parity):

```
effective_wall(step, manifest):
    1. step.timeout > 0                  -> step.timeout        # explicit, verbatim
    2. otherwise                         -> manifest.default_step_timeout
                                                                 # generous lane backstop
    # NOTE: deliberately NOT a function of cpu_timeout. See §1.
```

`unset` is the `0` sentinel, mirroring the existing `cpu_timeout = 0` idiom: loader
omits → 0, serializer drops when 0, round-trip stable. That part of the staged branch
`codex/wall-timeout-derivation` @ `c7992c3` is reusable **as-is**; only its
`3 × cpu_timeout` clause must be deleted.

**Documentation the code must carry** (the framing is the deliverable as much as the
number): the lane wall is a **hang-catcher for a no-CPU wedge, not a performance budget**.
The tight, load-invariant bound is `cpu_timeout`. Tighten `cpu_timeout`; never tighten the
wall backstop to "make things fast" — that reintroduces the load-sensitivity this idiom
exists to remove.

**Scope, per the recorded decision:**
- Portable lane only.
- `privileged.json` **keeps** explicit walls — hard 270 s outer box in `ci-privileged.yml`
  plus a critical-path assertion in `test_harness.sh`.
- **Do not strip a node's wall unless that node has a working `cpu_timeout`.** Stripping
  wall from a node with no CPU guard replaces a real bound with a very loose one.

**The hosted-CI inversion still stands and gates the rollout.** `cpu_timeout` is inert on
GitHub lanes (boxing disabled via `--allow-cgroup-failure` under `GITHUB_ACTIONS`), so on
those lanes stripping wall removes the only live guard. Apply the idiom where boxing
actually engages — the local boxed `validate.sh` path — and keep explicit walls on hosted
lanes until boxing is on there. **This is also why `cpu_timeout` adoption must come first:
measured last pass, 0 of 55 manifest steps currently set one.**

**Order of operations:**
1. Raise `portable.json` `default_step_timeout` 600 → 1800.
2. Land per-node `cpu_timeout` values (the deriver output already exists; adoption is 0/55).
3. Land the resolver (both engines + differential).
4. `test_harness.sh dag_critical_path_seconds` must sum the **resolved** wall — today it
   reads raw `$step.timeout`, so an omitted wall is `null` and the jq underflows or crashes.
5. Only then strip wall, node by node, each one only where it has a `cpu_timeout`.

**Verify bar, both directions.** A wall change is a kill-criterion change: plant a genuine
no-CPU wedge and confirm the lane backstop reaps it; plant a merely-slow-but-healthy run
under artificial load and confirm it is **not** reaped. State both counts. Additionally
assert the round-trip: a manifest with wall omitted must serialize back with wall omitted.

---

## 5. What I would tell the owner in one line

The idiom is right — *stop hardcoding per-node wall, keep one generous runtime backstop,
put the real bound on CPU*. Only the **3× CPU derivation** is wrong, because CPU and wall
are not proportional per node (1774× spread), and the correct fallback is a single
generous lane constant of roughly 1800 s. The existing `DEFAULT_STEP_TIMEOUT` already is
that; the portable manifest's 600 s override is the thing that is out of line.

## 6. Why no code, and what unblocks it

- The change as dispatched (3×CPU) is **refuted** (§1) and **explicitly countermanded** by
  a recorded owner decision on this task.
- The change as *approved* targets `agent-utils`, which is **parked** (owner stop-infra,
  2026-08-05T02:05Z), **capped at one in-flight PR**, and whose pin state I cannot even
  verify: `make check-agent-utils-pin` exits 2 with a proxy 403 (egress down box-wide).
- Prior work already staged the reusable half on `codex/wall-timeout-derivation` @
  `c7992c3`; it needs a rebase and the deletion of its `3 × cpu_timeout` clause, not a
  rewrite from scratch.

**Unblock = owner lifts the park + an agent-utils PR slot frees + egress returns.** Then
§4 is a mechanical edit. If you want me to write it anyway as an unpushed local change,
say so and I will — I stopped short because the target repo is parked and in an
unverifiable pin state, not because the edit is hard.

## 7. Limitations

- Per-node figures come from the history store (52–54 well-sampled nodes of 130; 58% are
  thin). Nodes without samples cannot be assessed either way.
- `max_wall` may include killed runs; kill exclusion is applied to the CPU columns only.
  So the "observed max wall" figures are an upper envelope that may embed a timeout.
- The 1.79× load inflation is a whole-run median ratio applied to per-node maxima. Nodes
  do not inflate uniformly — a CPU-bound node inflates less than a wait-bound one — so the
  954 s and 1430 s figures are screening estimates, not per-node predictions.
- I did not run either engine, the differential cross-check, or any DAG.
- The hosted-CI boxing-inert claim is carried from prior task notes; I did not re-verify
  it at this HEAD.
