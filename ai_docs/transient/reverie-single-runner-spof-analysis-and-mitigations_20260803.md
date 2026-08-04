# Reverie single self-hosted runner (SPOF) — analysis, owner action, mitigations

- **Date:** 2026-08-03
- **Task:** `reverie-single-runner-spof-add-second` (P1)
- **Agent:** hermit-250 (impl, opus-4.8)

## The mechanism (more specific than "CI is slow")

1. **reverie has exactly ONE self-hosted runner** — a SPOF. Confirmed via the
   GitHub Actions API (`with-proxy gh api repos/rrnewton/reverie/actions/runners`):
   `total_count=1`, `reverie-ci-newton` labels `[self-hosted,Linux,X64,reverie,pmu]`.
   For contrast, **hermit has 4**: `hermit-ci-newton`
   `[…,pmu,pmu-serial,gate]`, `hermit-ci-newton-2` `[…,pmu,gate]`,
   `hermit-ci-newton-3` `[…,pmu,gate]`, `hermit-gate-newton` `[gate]`. Hermit
   already has failover, a `pmu-serial` label, and a dedicated `gate` runner;
   reverie has one runner doing everything.
2. **Our agent fleet loads the host that runner runs on.**
3. **reverie's host-dependent tests are PMU-sensitive** (`pmu` label;
   perf-counter determinism). PMU tests degrade badly under host contention *by
   their nature* — they measure hardware counters whose behaviour changes when
   the machine is busy.

So fleet load lands disproportionately on exactly the tests least able to
tolerate it, on the one runner with no failover. This reads as "random CI
flakiness" but is a structural interaction.

**Evidence, two independent instances (2026-08-03):** the Host-dependent check
sat at **2h44m against a ~2min baseline (~80×), silent**; and hermit-kvm could
not measure KVM TTY parity because it **wedged at guest startup under ~470
concurrent hermit processes** (`kvm /bin/true` = 124 vs ptrace = 0). Two
subsystems, one cause.

## Where the job lives

`reverie/.github/workflows/ci.yml`, job `hardware`
("Host-dependent tests (self-hosted)"):
`runs-on: [self-hosted, Linux, X64, reverie]`, `--test-threads=1`,
`REVERIE_REQUIRE_KVM=1`, gated behind `vars.REVERIE_SELF_HOSTED == 'true'` and a
`push` / rrnewton `workflow_dispatch` / rrnewton PR event. The authoritative
gate for all reverie PRs is the *other* job, `regular`
("Regular tests (GitHub-hosted)"), on `ubuntu-latest` — unaffected by the SPOF.

## PRIMARY fix — a second runner (OWNER / INFRA action; I cannot do this)

A self-hosted runner is a machine plus a registration token; provisioning it is
owner action. **No workflow change is needed to consume a second runner** — the
`hardware` job already targets the label set `[self-hosted, Linux, X64,
reverie]`, so GitHub will schedule to *any* idle runner carrying those labels.
Registration recipe (owner, on the new host):

```bash
# owner, with admin on rrnewton/reverie:
with-proxy gh api -X POST repos/rrnewton/reverie/actions/runners/registration-token --jq .token
# then on the host:
./config.sh --url https://github.com/rrnewton/reverie \
  --token <TOKEN> --labels self-hosted,Linux,X64,reverie,pmu --name reverie-ci-newton-2
./run.sh    # or install as a service
```

**Lowest-cost option (no new hardware):** an existing *hermit* runner host can
co-host a second reverie runner instance (multiple runner services per machine,
registered to different repos, is supported). That removes the SPOF without new
hardware — but co-tenancy re-introduces contention, so it pairs best with
mitigation (2) below (a reserved cpuset for the PMU runner). Still owner action.

## Mitigation (1) — fail-fast load precondition + job timeout (DONE, unilateral)

Shipped in **reverie PR #356** (`ci/reverie-host-load-precondition-and-timeout`,
commit `db09e24`). Two changes to the `hardware` job only:

- `timeout-minutes: 45` — a wedge now fails loudly and fast instead of
  inheriting GitHub's 6h default and squatting the one runner. Generous vs the
  ~2min test baseline + cold DynamoRIO build, still catches an 80× wedge.
- A **"Host load precondition"** step run *first* (before build/test, so it sees
  ambient neighbour/fleet load, not the job's own build parallelism). Reads
  `/proc/loadavg`; if the 1-min load average exceeds
  `cores × REVERIE_MAX_LOAD_PER_CORE` (default `1.5`), it refuses with a clear
  `::error title=Host too loaded to measure (not a product failure)::…`
  annotation and exits 1. **A measurement taken under invalid conditions is
  discarded, not reported** — the nightly-stress validity-calibrator principle.
  Tunable / escapable via repo vars `REVERIE_MAX_LOAD_PER_CORE` and
  `REVERIE_SKIP_LOAD_PRECONDITION`.

Design note: this deliberately turns "silently green-after-3h / wedged" into
"honest red in seconds when the host is contended." Under our fleet it will
often refuse — that is correct and cheap; the reliable-green fix is the second
runner. Consumers must read the annotation: a load-precondition red is **not** a
Reverie regression and must not gate landing as one.

## Secondary mitigation (2) — pin PMU tests to a quiesced runner / cpuset (OWNER)

With only one runner there is nothing to pin *to* today. Once a second runner
exists, the clean split mirrors hermit's `pmu-serial`/`gate` labeling: keep PMU
determinism work on a **reserved, quiesced** runner (its own cpuset so neighbour
load can't perturb the counters) and let other work use the second. Two caveats:
(a) this is runner-host configuration (owner action), not a workflow edit; (b)
cpuset isolation needs a delegated `cpuset` cgroup controller — **this dev host
has cgroup v2 with no cpu/cpuset controller delegated** (same limitation that
keeps `ci-hub/history` node-cpu-budgets thin locally), so the runner host must be
configured to provide it. Recommend the owner reserve a cpuset for the PMU
runner when adding the second runner.

## Secondary mitigation (3) — fleet throttles hermit processes during a critical reverie check (COORDINATOR)

~470 concurrent hermit processes is enough to change the machine's character.
This is a **dev-hermit parent/coordinator** behaviour, not a reverie-repo change:
while a critical-path reverie check is in flight, the coordinator should throttle
concurrently-launched hermit processes (or steer them off the reverie runner's
host). This is the demand-side complement to mitigation (1)'s supply-side
refusal. Left as a coordinator recommendation; not implemented here because it
lives in fleet/coordinator policy, not `rrnewton/reverie`.

## Status / next

- Mitigation (1): reverie PR #356 open; authoritative `regular` gate runs on the
  PR. Task stays IMPLEMENTED (not closed) until #356 lands — coordinator closes
  after merge per the reverie/parent task-closure policy (agents do not
  self-close; an unmerged PR closed = phantom closure).
- Primary ask (2nd runner) + mitigations (2)/(3): owner/coordinator action,
  documented above. This is the durable record.
