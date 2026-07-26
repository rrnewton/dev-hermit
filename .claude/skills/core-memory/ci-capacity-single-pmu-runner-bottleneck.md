---
name: core-memory-ci-capacity-single-pmu-runner-bottleneck
description: "rrnewton/hermit Rust CI is chronically queued/never-green because ONE pmu self-hosted runner can't drain its heavy determinism suite x high PR volume; reverie has the same 1-runner setup but drains (light job). Not broken tests — a capacity mismatch. Status tool: ci-runner/ci-status.py. (CORE-MEMORY mirror of memory/ci-capacity-single-pmu-runner-bottleneck.md)"
---

# CORE-MEMORY: ci-capacity-single-pmu-runner-bottleneck

<!-- GENERATED MIRROR of core memory `ci-capacity-single-pmu-runner-bottleneck`. Source of truth is the memory
     file `ci-capacity-single-pmu-runner-bottleneck.md`. Regenerate: scripts/sync-memory-skill.rs. Verify in
     sync: scripts/lint-memory-skill-sync.rs. Do NOT hand-edit inside the
     markers — edit the memory and re-run sync. -->

<!-- BEGIN CORE-MEMORY-MIRROR (source: ci-capacity-single-pmu-runner-bottleneck.md) -->
Task impl-ci-deep-dive (2026-07-24). Deep dive on the Hermit CI alarm
("queue, cancelled runs, no green runs").

UPDATE (research-ci-runner-health, 2026-07-24 ~14:45 UTC): the runner FLEET GREW
1 → 3 (hermit-ci-newton, -2, -3, all online pmu; 1 busy/2 idle) — the throughput
fix landed, queue no longer backs up for hours. BUT the self-hosted 'Host-dependent
tests' job now fails 100% at the **Dependencies step** (exit 100): `apt-get install`
→ `E: Unable to locate package cmake / redis-server / redis-tools / sqlite3 /
zlib1g-dev`. Environmental/provisioning (partial/stale apt index, no `apt-get
update`, no full repo), NOT code/nondeterminism/PR — fails ~10s in before any test,
so identical on every run. GitHub-hosted 'Regular tests' stays green. Non-required
(main unprotected; gate = GH-hosted green + locally-validated), so it doesn't block
merges, but the self-hosted lane gives ZERO real coverage until provisioning is
fixed (preinstall the 5 pkgs on all 3 runners is the cleanest fix). This is a
DIFFERENT failure mode than the old queue/SIGSEGV — deps, not tests. FIX draft
PR #578 (impl-fix-ci-runner-deps): both self-hosted Dependencies steps now run
`apt-get update` first (non-fatal) + install hard link deps (g++/libunwind-dev/
liblzma-dev) strictly + the rest (cmake/zlib1g-dev/redis/sqlite3/golang-go)
best-effort per package, so a missing pkg degrades coverage instead of exit-100'ing
the whole job. Runner is apt-based, not Fedora. Unverified on the runner (draft).

CI WORKFLOW LAYOUT (.github/workflows/): ci.yml (name "Rust") has jobs `regular`
(GitHub-hosted ubuntu-latest, runs everywhere), `hardware` ("Host-dependent
tests", self-hosted PMU), `qemu-l2` (self-hosted, workflow_dispatch-only).
docs.yml (GitHub-hosted). merge-gate.yml keys off the overall ci.yml RUN
CONCLUSION or the `locally-validated` label (not individual jobs). The self-hosted
`if:` originally = push OR (PR by rrnewton) with NO repo guard → on
facebookexperimental (no self-hosted runner) it queued forever, blocking the gate.
FIX draft PR #573 (impl-conditional-ci-fbe): added `github.repository ==
'rrnewton/hermit'` conjunct to both self-hosted jobs → they SKIP on
facebookexperimental (skipped job doesn't fail the run), so GitHub-hosted tests
alone conclude the run there; rrnewton behavior unchanged (guard always true).
Can't test on facebookexperimental (no write access; bot policy forbids acting
there) — verified by YAML validity + logic only.

ROOT CAUSE = capacity, not broken tests. Each of `rrnewton/hermit` and
`rrnewton/reverie` has exactly ONE pmu self-hosted runner (`hermit-ci-newton`,
`reverie-ci-newton`; labels `self-hosted,Linux,X64,<repo>,pmu`). The `pmu` label
is required (determinism suite reads hardware RCB/retired-branch counters) so
these jobs CANNOT fall back to GitHub-hosted runners.
- **reverie**: Rust job ~2-3 min → runner stays IDLE, queue depth 0, goes green
  continuously. Proves the runner infra is fine.
- **hermit**: Rust ("Regular tests") job is much HEAVIER + PR/push volume high →
  the single runner can't keep up → 23-59 in-flight, constant supersession
  cancellations, and ZERO green Rust runs (observed 0/40). The GitHub-hosted
  **Docs** workflow stays green and is the practical hosted gate.
- **facebookexperimental/hermit** (fbcode-sync mirror): ~50% Rust failure rate
  from fbcode/folly-fmt sync breakage — separate issue; also no repo-runner read
  perm (gh api → 403).

Because self-hosted Rust CI can't reliably go green, landing uses the
post-facto-review discipline: run checks locally, apply **`locally-validated`**
label, merge on Docs-hosted green. Merged PRs #250-#261 correctly carry
`locally-validated`+`post-facto-review`; open PRs mostly unlabeled (awaiting).
This is the documented workaround, consistent with
[[self-hosted-ci-sigsegv-blocks-all-prs]] and
[[validate-sh-cannot-be-green-on-devserver]] (main is unprotected; gate =
GitHub-hosted green + locally-validated).

TOOL BUILT: `~/work/dev-hermit/ci-runner/ci-status.py` (+ README.md) — a
non-mutating status reporter (Python3 stdlib; shells out via `$GH` default
`with-proxy gh`). `./ci-status.py --all` reports runner health, queue depth,
last-green-per-workflow, and open-PR label compliance for all 3 repos. Modeled
on but much smaller than `~/work/dev-deepscry/ci-runner` (a full Hetzner
fleet/shepherd toolkit — NOT ported; hermit uses host-local PMU runners, not a
cloud fleet). ci-runner/ is UNTRACKED in the parent superproject (not committed;
no commit step was in the task).

Remediation for the human: (1) add N>1 pmu runners for hermit, (2) split the
Rust job so non-PMU parts (build/clippy/fmt/unit) run GitHub-hosted in parallel,
leaving only RCB tests on pmu, (3) throttle redundant PR triggers, (4) formally
accept locally-validated as the gate (current practice).
<!-- END CORE-MEMORY-MIRROR -->
