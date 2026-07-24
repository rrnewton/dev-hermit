# Hermit CI runner / status tooling

On-demand visibility into the Hermit repos' GitHub Actions state. Modeled on the
`dev-deepscry/ci-runner` pattern but sized to Hermit's reality: Hermit does **not**
run a container fleet — it uses a single, permanently-installed PMU self-hosted
runner per repo. This directory currently provides a **non-mutating status
reporter**, not fleet provisioning.

> Scope note: `dev-deepscry/ci-runner` additionally contains Hetzner fleet
> provisioning, a CI shepherd/reconciler, and a runner container image. Those are
> deliberately **not** ported here — Hermit's runners are host-local PMU machines
> (they need `perf` RCB counters), so cloud-fleet scale-out does not apply. Add
> them only if Hermit moves to on-demand runners.

## Quick start

```sh
cd ~/work/dev-hermit/ci-runner
./ci-status.py                 # rrnewton/hermit (default)
./ci-status.py --all           # all three Hermit repos
./ci-status.py --repo rrnewton/reverie --limit 60
```

`gh` is invoked through `$GH` (default `with-proxy gh`) so it works behind the
devserver proxy without changing the machine-global `gh` account. Override with
`GH='gh' ./ci-status.py` if you are already off-proxy/authed.

The report shows, per repo:

- **runner health** — count + per-runner status/busy/labels; flags when there is
  no idle runner.
- **queue depth** — status/conclusion histogram of recent runs and total
  in-flight (queued + running).
- **last green per workflow** — the newest successful run of each workflow, or
  "NO GREEN in last N".
- **open-PR label compliance** — how many open PRs carry a landing label
  (`locally-validated` / `post-facto-review` / `human-approved`).

## The Hermit CI situation (2026-07-24)

This is a **capacity** problem, not a broken-tests problem.

- Each of `rrnewton/hermit` and `rrnewton/reverie` has **one** PMU self-hosted
  runner (`hermit-ci-newton`, `reverie-ci-newton`; labels
  `self-hosted,Linux,X64,<repo>,pmu`). The `pmu` label is required because the
  determinism suite reads hardware retired-branch counters, so these jobs **cannot
  fall back to GitHub-hosted runners**.
- **reverie** drains fine: its Rust job runs ~2–3 min, so one runner stays idle
  and every push goes green quickly.
- **hermit** is jammed: the Rust ("Regular tests") job is much heavier and PR/push
  volume is high, so a single runner cannot keep up. Result: dozens of queued Rust
  runs (observed 23–59 in-flight), frequent supersession cancellations, and **zero
  green Rust runs**. The GitHub-hosted **Docs** workflow stays green and is the
  practical hosted gate.
- **facebookexperimental/hermit** (the fbcode-sync mirror) has a high Rust failure
  rate (~50% of recent runs) from fbcode/folly-fmt sync breakage — a separate
  issue from the rrnewton runner bottleneck.

### Landing discipline

Because self-hosted Rust CI cannot reliably go green here, changes land under the
**post-facto-review** discipline: run the affected checks locally, prove any
residual failure is baseline/environmental, apply the **`locally-validated`**
label, and merge on GitHub-hosted (Docs) green. Merged PRs should carry
`locally-validated` + `post-facto-review`. See
`hermit/.llms/skills/post-facto-review/SKILL.md`.

### Remediation options (for the human)

1. **Add PMU self-hosted runner(s)** for `rrnewton/hermit` — reverie proves one
   runner drains a light load; hermit's heavy load needs N>1.
2. **Split the Rust job** so non-PMU parts (build, clippy, fmt, unit tests) run on
   GitHub-hosted runners in parallel, leaving only the RCB/determinism tests on
   the PMU runner.
3. **Throttle redundant triggers** (cancel-in-progress per branch is already
   happening as supersession cancels; also consider path filters / fewer
   pull_request event types).
4. **Formally accept `locally-validated`** as the gate (current de-facto practice).

## Files

- `ci-status.py` — self-contained, non-mutating status reporter (Python 3, stdlib
  only; shells out to `gh`).
