# Proposal: enshrine "box all untrusted compute" as a structural invariant

**Task:** `enshrine-box-all-untrusted-compute` (owner P0, 2026-08-03).
**Deliverable:** analysis + proposed diff. The owner updates the canonical
AGENTS docs and skills himself — this document is the proposed text and the exact
insertion points, **not** an overwrite. Coordinate before applying.

**Source binding for the cited facts:** the usage audit
[`ai_docs/transient/2026-08-03-safe-ci-dag-runner-usage-audit.md`](transient/2026-08-03-safe-ci-dag-runner-usage-audit.md)
(dev-hermit `dea09de3`, Hermit `baf1a7b7`) and the runner's own contract in
[`agent-utils/common/docs/safe-ci-dag-runner/README.md`](../agent-utils/common/docs/safe-ci-dag-runner/README.md).

---

## 0. One staleness the brief must be corrected for (verify-before-citing)

The task brief and the audit both speak of "no path passes `--cgroups`". At the
current runner revision **that flag is a deprecated no-op** and **cgroup boxing
is ON by default** in the `run` CLI: it re-execs inside a transient
`systemd-run --user --scope`, caps each step, and tears the whole subtree down
with `cgroup.kill`; on a host without cgroup-v2 + a systemd `--user` scope it
**errors with exit 3** rather than running unprotected, and `--allow-cgroup-failure`
is the explicit opt-out (README "Status & limitations").

So the enshrined text must **not** tell agents to "pass `--cgroups`" (that would
be cargo-culting a dead flag). The real, current failure modes to name are:

1. **Raw-bash bypass.** The authoritative GitHub portable lane runs each shard
   through `ci/run-node.sh`, which extracts the command with `jq` and executes
   `bash` directly — the runner process never starts, so no node wall/CPU/memory
   policy, boxing, or profiling applies (audit §"GitHub portable/CI",
   gap rank 1).
2. **Library default is `NoopCgroups`.** The Python *library* entry point
   `run_dag(cgroups=None)` still defaults to no boxing; a programmatic caller
   must pass a real `Cgroups`/`reexec_in_scope`. The guarded stress harness does
   this correctly; a naive `run_dag(...)` call does not (README "Status &
   limitations"; audit §"Nightly/stress").
3. **`--allow-cgroup-failure` used routinely** downgrades to the safe no-op
   stand-in — legitimate on a laptop, but it means "not boxed", and must be
   reported as such, not treated as boxed.
4. **Declared-but-unenforced limits.** All 54 DAG nodes carry `hard_mem_max_bytes`
   hints and wall timeouts; **zero carry a `cpu_timeout`**; and under the
   raw-bash / NoopCgroups paths the memory hints are not hard-enforced at all
   (audit §"Resource-control conclusions" 3–4).

This correction is itself an instance of the reviewer posture we enforce
elsewhere ("verify a recalled flag still exists before recommending it").

---

## 1. The canonical invariant text (shared body, adapted per tree)

This block is the substance to enshrine. It is written once here; §2 and §3 say
exactly where each tree gets it and how the two forms cross-link (no symlink
sharing — the parent flat `.md` and hermit `SKILL.md` trees drift unless placed
deliberately in both).

> ## Boxing Untrusted Compute
>
> **All untrusted compute runs under `safe-ci-dag-runner`, always boxed, and
> killed cleanly when it exceeds its box.** "Untrusted compute" is every command
> whose resource use we do not fully trust in advance: tests, benchmarks, demos,
> compatibility sweeps, record/replay probes, guest binaries, foreign or
> third-party code, and any build or fetch that can run long or allocate without
> bound. For this compute the runner is the execution substrate, not an optional
> optimization.
>
> **Why this is an invariant, not a guideline.** Boxing converts resource
> discipline from per-caller diligence into a structural property of the
> workspace. When each caller is individually responsible for remembering a
> timeout and a memory cap, the failures are not hypothetical — they are the ones
> we keep hitting:
>
> - a reaper bug pinned a full CPU core, undetected, for hours, because nothing
>   owned the offending subtree's lifetime;
> - a 600-second wall budget sat on a node whose real work is ~8 seconds, so a
>   hang there wastes ten minutes before anything reacts;
> - no normal Hermit CI/validation path establishes real per-step containment —
>   the GitHub portable lane executes each shard as raw `bash` via
>   `ci/run-node.sh`, bypassing the runner entirely, and the local/privileged DAG
>   paths never establish it either;
> - all 54 current DAG nodes carry a wall timeout but **zero** carry a CPU-time
>   budget, and wall time is load-sensitive: on a busy host an 8-second node can
>   breach a generous wall budget through contention alone, so a wall backstop
>   cannot tell a hang from a crowded neighbour;
> - an unboxed memory hog's OOM blast radius lands on an innocent neighbour
>   instead of the offender, so the process that dies is not the one that
>   misbehaved.
>
> Under boxing each of these becomes a bounded, attributed event: the offending
> step — and only the offending step — is capped, killed, and named.
>
> **Two clauses that do the real work:**
>
> **(a) "Boxed" without enforcement is not boxed.** A declared limit that nothing
> enforces is a comment. A real box is a cgroup with enforced CPU, memory, and
> time limits plus a clean kill of the *entire* process subtree on breach
> (including `setsid`/double-forked escapees that a bare process-group kill
> misses). Enforcement is also what produces the attribution signal — "this node
> exceeded its declaration" — which is the only reliable way to set the
> declaration correctly next time. A wall timeout alone is not enough: budget
> CPU-time too, so a busy host does not turn contention into a false breach and a
> genuine spin is caught regardless of host load.
>
> **(b) "The runner doesn't support X" is never a reason to bypass it — it is a
> reason to extend the library.** Every current bypass was justified through some
> missing capability. That escape hatch is closed. If a real need is unmet — a
> per-step CPU-time budget, a subset/leaf-DAG entrypoint so a sharded GitHub job
> boxes each cell, a metrics field, a new named resource — extend
> `safe-ci-dag-runner` in `agent-utils` (used through `ci-hub/bin/agent-tool`;
> land the change and re-pin) and gain the capability for everyone. Do not fork a
> private timer, wrap the work in ad-hoc `timeout`, or run it raw.
>
> **Current runner contract (do not cite a dead flag).** Boxing is ON by default
> in `run`: it re-execs inside a transient `systemd-run --user --scope`, caps
> each step, and tears the subtree down with `cgroup.kill`. Without cgroup-v2 + a
> systemd `--user` scope it errors with exit 3 rather than running unprotected;
> `--allow-cgroup-failure` is the explicit, visible opt-out and means *not
> boxed* — report it as such. The old `--cgroups` flag is a deprecated no-op; do
> not add it as if it enabled anything. One gap to close deliberately, never
> silently: the Python *library* entry point `run_dag(cgroups=None)` defaults to
> `NoopCgroups`, so a programmatic caller must pass a real `Cgroups` /
> `reexec_in_scope` to actually box.

---

## 2. Proposed diffs — parent (coordinator) tree

### 2.1 `AGENTS.md` — add Hard Invariant 15

Under `## Hard Invariants`, after item 14, add:

```markdown
15. All untrusted compute — tests, benchmarks, demos, compatibility sweeps,
    record/replay probes, guest/foreign binaries, and any unbounded build or
    fetch — runs boxed under `safe-ci-dag-runner` with enforced CPU, memory, and
    time limits and a clean subtree kill on breach. A declared limit that nothing
    enforces does not count as boxed. When the runner lacks a capability the work
    needs, extend the library; never bypass it. See "Boxing Untrusted Compute".
```

### 2.2 `AGENTS.md` — new top-level section

Insert the full canonical block from §1 as a new `## Boxing Untrusted Compute`
section immediately **after** `## Validation And Evidence` (adjacent concern: how
validation compute is run safely) and **before** `## Product Vision`. Close it
with the cross-link:

```markdown
See the `box-all-untrusted-compute` skill (`.claude/skills/box-all-untrusted-compute/SKILL.md`)
for the operational checklist, and the hermit-side copy
(`hermit/.claude/skills/box-all-untrusted-compute/SKILL.md`) for product-repo
callers. The runner lives in `agent-utils` and is used through
`ci-hub/bin/agent-tool`.
```

### 2.3 New parent skill (repository-authoritative, optional memory mirror)

Parent skills are versioned packages and the repository is authoritative
(`.claude/skills/README.md`). Create the package and review it normally. The
optional local memory store may mirror that reviewed package via explicit
repository-to-memory export; local memory never generates or overwrites policy.

**Resulting optional memory mirror** `$MEMDIR/box-all-untrusted-compute.md`:

```markdown
---
name: box-all-untrusted-compute
description: "All untrusted compute (tests, benchmarks, demos, sweeps, guest/foreign binaries, unbounded builds/fetches) runs boxed under safe-ci-dag-runner with enforced CPU+memory+time limits and a clean subtree kill on breach; a declared-but-unenforced limit is not a box; extend the library rather than bypass it. Load when running or reviewing any resource-consuming compute, CI/validation wiring, or benchmarks."
metadata:
  node_type: memory
  type: project
  core_memory: true
  core_skill: .claude/skills/box-all-untrusted-compute/SKILL.md
---

> **CORE-MEMORY** — mirrored from `.claude/skills/box-all-untrusted-compute/SKILL.md`.

<full canonical block from §1, plus the operational checklist below>
```

**Operational checklist to append to the skill body (both trees):**

```markdown
## When it applies

Any command whose resource use is not trusted in advance: `cargo test` /
`nextest`, `hermit run`/`record`/`--verify`, demos, compat-envelope expansion,
rr probes, guest and third-party binaries, and long or unbounded builds/fetches.
Policy/analysis-only tools (merge-gate, log analyzers, `--help`/status reads) are
not compute and stay runner-free.

## How to box

- **A graph of steps:** author a DAG (JSON/YAML) and `safe-ci-dag-runner run`;
  boxing is default-on. Give each node real `hard_mem_max_bytes`, a wall timeout,
  **and** a CPU-time budget — not a wall backstop alone.
- **One step in isolation:** `run --only TAG` (still boxed).
- **A single leaf command inside a foreign scheduler** (e.g. a sharded GitHub
  job, an adaptive search like `debug/multisect`): use the runner as a leaf box —
  do not fall back to raw `bash`/`timeout`.
- **Programmatic (Python):** pass a real `Cgroups`/`reexec_in_scope`;
  `run_dag(cgroups=None)` defaults to `NoopCgroups` and does **not** box.

## Enforcement, not decoration (clause a)

A node is boxed only if its limits are actually enforced and breach kills the
whole subtree cleanly. If `--allow-cgroup-failure` was needed (laptop / no
systemd user scope), the run is **not** boxed — say so in the report. Prefer a
host with cgroup-v2 + a systemd `--user` scope for any result that claims
containment. Use the per-step profile/CSV output as the attribution signal to
tune each declaration.

## Extend, don't bypass (clause b)

Missing capability → land it in `agent-utils` `safe-ci-dag-runner` and re-pin;
never fork a private timer or run raw. Known worth-extending gaps: first-class
per-step CPU-time budget enforcement, and a subset/leaf-DAG entrypoint so the
GitHub portable shards box each cell instead of running `run-node.sh` raw
(audit gap rank 1).

## Related

- benchmarking (already mandates cgroup + K-core runs)
- validate-sh host/invariant skills (validation compute is untrusted compute)
- the test-architecture epic (the migration that makes every lane boxed)
- hermit-side copy: `hermit/.claude/skills/box-all-untrusted-compute/SKILL.md`
```

After the versioned package is reviewed, preview the optional export with
`scripts/sync-memory-skill.rs --adopt-skill .claude/skills/box-all-untrusted-compute/SKILL.md --check`;
then run it without `--check` only if the local mirror should be created. Finish
with `scripts/lint-memory-skill-sync.rs`.

### 2.4 Parent cross-references (small edits)

- `.claude/skills/benchmarking/SKILL.md` — under "Experimental validity" add a line:
  "Run every timed sample boxed under `safe-ci-dag-runner` (enforced cgroup CPU,
  memory, and time limits); an unboxed sample is not a publishable measurement.
  See [box-all-untrusted-compute](../box-all-untrusted-compute/SKILL.md)."
- `.claude/skills/validate-sh-cannot-be-green-on-devserver/SKILL.md` and
  `.claude/skills/hermit-ci/SKILL.md` — add "Validation compute is untrusted compute:
  run it boxed (see [box-all-untrusted-compute](../box-all-untrusted-compute/SKILL.md));
  do not paper over a load-sensitive wall breach by raising the timeout."
- **test-architecture epic task** — append a note that the epic is the vehicle
  that makes this invariant real on every lane (audit's corrected scope:
  subset-runner GitHub shards, mandatory profiling/limits, CPU budgets), so the
  invariant and the epic reference each other.

---

## 3. Proposed diffs — hermit (product) tree

### 3.1 `hermit/AGENTS.md` — new section

Insert the canonical block from §1 as `## Boxing Untrusted Compute`, placed after
`## Test` (where tests/benchmarks are invoked) and before `## Lint And Format`.
Adapt the closing cross-link to point back to the parent:

```markdown
See the `box-all-untrusted-compute` skill
(`.claude/skills/box-all-untrusted-compute/SKILL.md`) for the operational
checklist. The runner is a pinned `agent-utils` tool; the parent workspace states
the same invariant in its `AGENTS.md` "Boxing Untrusted Compute" section and
`.claude/skills/box-all-untrusted-compute/SKILL.md`.
```

Also add a one-line pointer in `## Test`: "Run the workspace suite and any
benchmark boxed under `safe-ci-dag-runner`, not bare — see Boxing Untrusted
Compute."

### 3.2 New hermit skill (hand-maintained dir form)

Create `hermit/.claude/skills/box-all-untrusted-compute/SKILL.md` with frontmatter:

```markdown
---
name: box-all-untrusted-compute
description: "Every resource-consuming command in Hermit (cargo test/nextest, hermit run/record/--verify, demos, benchmarks, guest/foreign binaries) runs boxed under safe-ci-dag-runner with enforced CPU+memory+time limits and a clean subtree kill; a declared-but-unenforced limit is not a box; extend the runner rather than bypass it. Load when running tests/benchmarks/demos or wiring CI/validation."
---
```

Body = the canonical block from §1 + the operational checklist from §2.3, with
the "Related" cross-link pointing at the parent:
`../../../AGENTS.md` "Boxing Untrusted Compute" and (for coordinator context)
the parent `.claude/skills/box-all-untrusted-compute/SKILL.md`. **No symlink** — this
is a deliberate second copy; when one changes, update both.

### 3.3 Hermit cross-references

- `hermit/.claude/skills/benchmark/SKILL.md` — its "Experimental Shape" already
  mandates a dedicated cgroup and K-core allocation; add one line tying that to
  the invariant: "This cgroup discipline is the boxing invariant applied to
  benchmarks — run through `safe-ci-dag-runner`; see box-all-untrusted-compute."
- `hermit/.claude/skills/ci-debugging/SKILL.md` and the parent
  `.claude/skills/hermit-ci/SKILL.md` — reference the new skill so CI wiring
  changes are held to boxed execution.

---

## 4. Placement summary (both trees, cross-linked, no symlink)

| Surface | Parent (`dev-hermit`) | Hermit |
| --- | --- | --- |
| AGENTS doc section | `AGENTS.md` "Boxing Untrusted Compute" + Hard Invariant 15 | `hermit/AGENTS.md` "Boxing Untrusted Compute" + `## Test` pointer |
| Skill | `.claude/skills/box-all-untrusted-compute/SKILL.md` (repository-authoritative; optional local mirror) | `.claude/skills/box-all-untrusted-compute/SKILL.md` (hand-maintained dir) |
| Cross-link direction | → hermit copy + agent-utils | → parent AGENTS/skill |
| Existing skills touched | benchmarking, validate-sh-*, hermit-ci | benchmark, ci-debugging, hermit-ci |
| Epic | note on the test-architecture epic (bidirectional) | — |

The two skill copies are **deliberate duplicates**, not a shared symlink (the
audit and the skill-scope map confirm the trees drift when shared). Each carries
an explicit cross-link to the other so a change to one flags the other.

## 5. What NOT to do (guardrails baked into the text)

- Do not add `--cgroups` anywhere — it is a dead no-op; boxing is default-on.
- Do not treat `--allow-cgroup-failure` runs as boxed.
- Do not raise a wall timeout to hide a load-sensitive breach — budget CPU-time.
- Do not fork a private timer or wrap in raw `timeout`/`bash` — extend the runner.
- Do not runner-wrap policy/analysis tools (merge-gate, log analyzers): not compute.
