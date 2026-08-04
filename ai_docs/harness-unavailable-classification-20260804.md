# HARNESS-UNAVAILABLE: a sixth, structurally-distinct validate-red class

Task: `bpfjailer-blocks-cgroup-boxing-agent-validates-cannot-run` (P0).
Author: coordinator (opus-4.8), 2026-08-04. hermit-ptw owns the FIX; this doc owns
the CLASSIFICATION and the CONTAMINATION measurement.

## One-line

A validate run that **exits 3 at the safe-ci-dag-runner wrapper because cgroup
boxing could not be established** is a **NO-RESULT**, not a red PR. The code under
test never executes. It is empirically **rare and transient** (1 of 182 local
ledger rows), **not** a universal "agents cannot validate" outage, and it
**cannot** occur on GitHub CI. So it explains ~0 of the "0/35 green" figure.

## 1. Why it is its own bucket (vs the five environmental-DURING-run signatures)

The five prior environmental signatures ([[validate-env-sandbox-block-classification]],
`is_environmental_block()` in hermit PR #1521) are all failures **during** a run:
the DAG started, a node executed, and a build subprocess was blocked / OOM-killed /
coredumped:

1. BpfJailer FS `FILE_OPEN` banner -> `build.rs:339`
2. banner-less cc1 EPERM on a world-readable system header (DynamoRIO)
3. elfutils `--parallel 316` SIGABRT coredump
4. objcopy `Bad file descriptor` on the drconfig strip step
5. cold-build OOM cascade / memory cap (`j12 -> j8`)

This SIXTH one is **before any node runs**. The runner's default fail-closed
boxing (`reexec_in_scope` into a `systemd-run --user --scope`) cannot acquire the
scope, so `_cmd_run`'s boxing gate returns **exit 3** and the DAG is never entered.
`build.dbi_release` — indeed EVERY node — does not execute.

### The discriminator is the exit code, and it is mechanical

| | environmental-during-run | HARNESS-UNAVAILABLE (this) |
|---|---|---|
| gate `exit_code` | **1** (a node failed) | **3** (boxing gate) |
| wall vs CPU | CPU >> wall (real parallel work: 100s–12000s CPU) | wall ≈ CPU ≈ **9s**, ratio ~1.0x (nothing parallel ran) |
| log banner | build-tool error (`build.rs:339`, `CMake Error`, OOM-KILLED) | `safe-ci: ERROR: systemd --user scope is unavailable; refusing advisory-only containment` + `safe-ci-dag-runner: ERROR: cgroup boxing could not be established` |
| evidence about the PR | partial (other nodes ran; some passed) | **none** — zero nodes ran |
| right response | route to lane / retry on lighter host | **retry** (transient scope acquisition); never "PR is red" |

Source of the exit code: `agent-utils/py/safe_ci_dag_runner/cli.py:1181,1210`
(returns `3` unless `--allow-cgroup-failure`). Banner: `cli.py:1206`.

## 2. It FAILS CLOSED — it never silently degrades to unboxed (REQUIRED-Q3)

hermit's local path (`validate.sh -> run_ci_manifest_lane -> ci/run-dag.sh`) does
**not** pass `--allow-cgroup-failure`, so a local run either boxes or exits 3.
`hermit/ci/run-node.sh:120-123` adds `--allow-cgroup-failure` **only** when
`$GITHUB_ACTIONS`/`$CI` is set. Therefore:

- **Local / agent sandbox:** fail-closed. Exit 3 is a clean NO-RESULT; untrusted
  compute never runs unboxed. This is the safe direction — the owner's
  boxed-untrusted-compute invariant HOLDS.
- **GitHub CI:** deliberately skips the systemd scope AND passes
  `--allow-cgroup-failure` -> runs unboxed inside the ephemeral VM (the VM is the
  containment boundary). GitHub CI therefore **cannot emit exit 3**.

## 3. Who can produce a valid local validate record TODAY (REQUIRED-Q1 + user reframe)

**The "no producer" premise is REFUTED.** Agent sandboxes ARE the working
producer. In the parent ledger (`ignored/validate-run-ledger.jsonl`, 182 rows,
2026-08-03 02:15 -> 2026-08-04 04:46):

- Agent-named slots (227b, 247, lander, ci, standalone, slot0x, dbi, ghdag, …)
  produced **69 PASS + 79 non-exit-3 fail = 148 records that got PAST the
  fail-closed boxing gate**. Getting past the gate on a local run *is* proof that
  boxing was established (else exit 3).
- Exactly **1** of those 149 agent-sandbox attempts (0.67%) failed to establish
  boxing (the finding itself).
- The very slot that hit exit-3 — **227b @ 04:38:24** — established boxing
  successfully **13 min earlier** (04:25:33, wall135/cpu797), **7 min later**
  (04:45:09 + 04:46:29 pass), and ~20 other times that day.

**Conclusion:** this is a **transient failure to acquire the `systemd --user`
scope inside the 3pai sandbox** (~0.5–0.7% of runs), NOT a permanent capability
gap. The working path is: **agent sandboxes, the vast majority of the time.** The
GitHub-free landing rule DOES have a producer. The correct fix is
detect-and-retry the exit-3 boxing failure (exactly like #1521's env-block
retry), not "move validation to host-only."

This is therefore the OPPOSITE shape of the passed-review-codex gap (a required
artifact with NO entity able to generate it). Here the entity generates it ~99.5%
of the time.

## 4. Contamination of "0/35 green" (REQUIRED / CONTAMINATION)

- **Local validate ledgers:** 1 exit-3 row / 508 total rows across all three
  ledgers; 1 / 182 in the parent ledger; 1 / 327 retained `/tmp/*-validate.*.log`
  files carry the boxing banner. **Not a mass contaminant.**
- **GitHub CI (the source of the 0/35 figure):** exit-3 is structurally
  impossible (section 2). The 860 GitHub failure rows with `run_s <= 20s` in
  `gha-runs.csv` are fast product/lint/manifest reds, a DIFFERENT phenomenon.
  **Zero of the 35 GitHub reds are this signature.**
- **Indirect link:** exit-3 threatens the *local-validation recovery leg* that the
  16 NO-RECORD fail-closed PRs ([[pr-drain-reds-are-not-product-breaks]]) rely on.
  But because it is transient (~0.5%), a retry produces a clean record, so it is a
  minor self-healing contaminant, not the explanation for 0/35. The decomposition
  in [[pr-drain-reds-are-not-product-breaks]] stands (needs-rebase 25, NO-RECORD
  16, env OOM 5, ~0 product).

## 5. The #1532 / #1498 / #1470 / #1365 reframe — cause is NOT this

The user hypothesized these four `locally-validated` PRs are a SYMPTOM of the
harness failure. Checked via `ci-hub validate-status --pr`: all four head SHAs
have **zero** backing local validate records
(1532 `affa6e57`, 1498 `83dbe441`, 1470 `87359f7a`, 1365 `9f7f5171`).

But the causal hypothesis is REFUTED by section 3: sandboxes CAN and DO produce
records (148 of them). So a missing backing record at these heads is NOT "no
producer." Likelier causes, to verify independently (lander/reviewer task):
rebase-keying drift ([[validate-record-keying-breaks-under-rebase]]), a label
applied with no run, or a host-origin record not aggregated into this ledger. Do
**not** merge this open item into the harness-unavailable item — different root
cause.

## Recommended classification action (for the fix owner + ci-hub)

Add a distinct **HARNESS-UNAVAILABLE** verdict (glyph e.g. 🚫), separate from 🧱
environmental and ❌ test-fail:
- key on gate `exit_code == 3` AND the `cgroup boxing could not be established` /
  `systemd --user scope is unavailable` banner;
- count it as **NO-RESULT** — never green, never a PR red; surfaced and tallied
  separately so a systemic outage (rate climbing toward 100%) is loud;
- **retry** it (transient), like #1521's env-block retry;
- if the rate ever DID approach 100%, THEN and only then does the GitHub-free
  landing rule lose its producer — so the tally is the early-warning signal.

## 6. Fleet scope — is untrusted compute running UNBOXED? NO (systematic-violation alarm REFUTED)

Corrected question (2026-08-04): does the Python orchestrator's boxing path
succeed inside an agent sandbox, or fail silently? Settled by what runs DID, not
by flags:

- **Live capability test, inside a confirmed 3pai sandbox** (5 `META_3PAI` vars,
  cgroup `…/3pai_sandbox.slice/run-p3109228-….scope`):
  `systemd-run --user --scope -p Delegate=yes /bin/true` -> **exit 0**. Agent
  sandboxes CAN create delegated cgroup-v2 scopes; BpfJailer does not categorically
  deny it.
- **Fails CLOSED, never silent.** The default (no `--allow-cgroup-failure`) path
  returns exit 3 (`cli.py:1181,1210`). Silent-unboxed requires the explicit flag,
  which across the ENTIRE fleet is passed in only two CI-only spots
  (`run-node.sh:122` gated on `$GITHUB_ACTIONS`/`$CI`; `test_harness.sh:357`). No
  benchmark / corpus / experiment / stress harness passes it.
- **Log evidence:** 330 retained `/tmp/*-validate.*.log` -> **0** degraded-unboxed
  banners, **18** explicit `cgroup boxing ACTIVE`.
- **A real agent-benchmark box existed:** `dbibuild-cap8g-598976.scope` — a
  `systemd-run` cgroup created by the lander-slot DBI build measurement, reading
  `memory.peak`/`memory.max`/`memory.events` from its own cgroup. Direct proof an
  agent benchmark ran inside a real box.
- **`experiments/cpu-timeout-enforcement-verified_20260803/metadata.json`** records
  `boxing: fail-closed default (no --allow-cgroup-failure); systemd transient scope
  safe-ci-*.scope; two-level cgroup-v2` for an agent run.

**Verdict: possibility (2) of the corrected framing — Python boxes by default AND
succeeds in sandboxes.** The problem is scoped to the validate wrapper's TRANSIENT
scope-acquisition failure (~0.5-0.7%), not a fleet-wide violation. Tonight's
benchmarks were NOT measured unboxed via this mechanism; the silent-unboxed
worst case did not occur, so hermit-220's parallelism numbers do not need
re-examination *on this basis* (a raw `hermit run` that never routes through the
runner or systemd-run is a separate, pre-existing "not auto-boxed" question,
unrelated to the BpfJailer finding).

## Evidence

- Ledgers: `ignored/validate-run-ledger.jsonl` (182), `ignored/validate-run-global.jsonl`
  (326), `ignored/ci-hub/validate-runs.jsonl` (1).
- Finding row: slot 227b, commit `85626e18`, profile portable-only, wall 9s,
  user 5.038 + sys 4.233 = 9.27 CPU-s (ratio 1.03x), gate `portable CI DAG
  manifest` exit 3. Log `/tmp/hermit-validate.uSQjOV.log:59-60`.
- Mechanism: `agent-utils/py/safe_ci_dag_runner/cli.py:1156-1210`;
  `hermit/ci/run-node.sh:112-126`; GitHub `--allow-cgroup-failure` in
  `ci-privileged.yml:100`, `validation-levels.yml:139`.
