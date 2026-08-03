---
name: ci-capacity-single-pmu-runner-bottleneck
description: "rrnewton/hermit Rust CI can queue behind scarce PMU runners while Reverie's lighter lane drains. Load for historical runner-capacity context; live commands come from ci-hub quickstart."
---

> **CI-HUB** — Current CI code, live query entrypoints, history, runner operations, and health truth are centralized at `ci-hub/README.md`. This memory records role/policy or historical context; do not treat dated paths or state below as the live tool location.

Task impl-ci-deep-dive (2026-07-24). Deep dive on the Hermit CI alarm
("queue, cancelled runs, no green runs").

UPDATE (research-ci-runner-health, 2026-07-24 ~14:45 UTC): the runner FLEET GREW
1 → 3 (three online PMU runners; 1 busy/2 idle) — the throughput
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
`rrnewton/reverie` has exactly ONE pmu self-hosted runner (labels
`self-hosted,Linux,X64,<repo>,pmu`). The `pmu` label
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
label, merge on Docs-hosted green. Merged PRs #250-#261 now carry
`locally-validated`+`post-facto-human-review`; open PRs mostly await review.
This is the documented workaround, consistent with
[[self-hosted-ci-sigsegv-blocks-all-prs]] and
[[validate-sh-cannot-be-green-on-devserver]] (main is unprotected; gate =
GitHub-hosted green + locally-validated).

The historical runner reporter was consolidated behind the typed ci-hub front
door. Do not copy its old path or flags from this dated memory: run
`./ci-hub/ci-hub quickstart`, which owns the current runner-health workflow and
proxy behavior. Hermit still uses host-local PMU runners rather than a cloud
fleet; that architectural finding remains current.

Remediation for the human: (1) add N>1 pmu runners for hermit, (2) split the
Rust job so non-PMU parts (build/clippy/fmt/unit) run GitHub-hosted in parallel,
leaving only RCB tests on pmu, (3) throttle redundant PR triggers, (4) formally
accept locally-validated as the gate (current practice).
