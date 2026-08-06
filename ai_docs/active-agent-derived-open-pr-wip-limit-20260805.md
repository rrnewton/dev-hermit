# Active-agent-derived open-PR WIP limit

Date: 2026-08-05

Task: `wip-limit-open-prs-tracked-against-active-agent-count`

## Decision

The fleet's open-PR limit is a derived control value, not a configured round
number:

```text
A_pr       = sum of PR ownership slots held by live publishing agents
mu_flow    = min(flow merges/hour over 6h, flow merges/hour over 24h)
S          = 24h (the existing fresh-flow service target)
C_land     = floor(mu_flow * S)
WIP_limit  = min(A_pr, C_land)
W_effective = O_open + R_reserved
```

`A_pr` prevents the fleet from manufacturing more independently owned changes
than it can actively shepherd. `C_land` prevents the same fleet from opening
more work than its measured landing lane can clear within the service target.
The smaller value is authoritative. There is deliberately no minimum of one:
zero measured flow-landing capacity grants zero new-PR admission tokens while
existing work and the broken landing path are drained.

The control objective is therefore **roughly one open PR per live publishing
agent, or fewer when measured landing capacity is the tighter constraint**.
An agent may own no PR while helping drain somebody else's; it must not open a
second PR merely because it is active.

## Units and population

The WIP unit is the unique tuple `(repository, PR number)`. TaskGraph records,
commits, branches, and validation records are not PR identities and must not be
added to this count. In particular, the reported `131 implemented-unlanded`
records are a task population, not evidence of 131 open PRs.

The repository set is an explicit, versioned allowlist of repositories sharing
this fleet's review and landing capacity. It includes at least
`rrnewton/hermit` and `rrnewton/reverie`; add `rrnewton/liteinst2` or
`rrnewton/dev-hermit` whenever their PRs enter the same fleet-managed landing
lane. The report gives per-repository subtotals and a deduplicated total.

Every open PR in scope consumes WIP, including drafts, ready PRs, holds,
external-contributor PRs, and temporarily blocked PRs. Those categories are
reported separately, but none is silently excluded to make the limit green.
An abandoned PR stops consuming WIP only when it is actually closed.

## Measuring live publishing capacity

`A_pr` is recomputed from a joined, time-stamped fleet snapshot. Count an agent
only when all of these are true:

1. Its orchestrator lease/heartbeat is fresh at the snapshot time.
2. It owns an `in_progress` code-producing or PR-shepherding TaskGraph task.
3. A mutating agent has the exclusive registered slot required by workspace
   policy.
4. Its current role is capable of publishing and shepherding a PR.

Exclude coordinators, lander-only agents, reviewer-only agents, monitoring
agents, research-only agents, agents with expired leases, and agents blocked
without code ownership. Count a normal publishing agent as `c_i = 1`. A
coordinated cross-repository task may have `c_i = 2` only when its registered
plan genuinely requires two simultaneously open, independently landed PRs.
Count each agent once even when it has multiple task records.

Neither `worktrees/ACTIVE.md` nor a TaskGraph owner field is sufficient alone:
the former is an ownership registry and can outlive a process, while the latter
does not prove a live worker or a valid checkout. The report must retain the
joined input rows and the reason each row was included or excluded.

## Measuring open PRs

After egress returns, freeze one raw snapshot from every in-scope repository at
one `as_of` time. A suitable collection shape is:

```bash
with-proxy gh pr list -R OWNER/REPO --state open --limit 1000 \
  --json number,isDraft,createdAt,headRefOid,url,author
```

Archive the raw JSON and its SHA-256 digest before classifying it. Compute
`O_open` by unique `(repository, number)`, and report ready/draft,
fleet-managed/external, fresh/stale, and blocked subtotals. The command is a
future live collection procedure, not evidence that a live query ran during
this egress-free analysis.

The existing two-pool reporting contract remains the source of the flow
classification: cleanup backlog and steady-state flow are measured separately.
Only **merged steady-state-flow PRs** contribute to `mu_flow`. Cleanup landings
may temporarily consume the same lane but do not overstate its sustainable
new-flow capacity. Non-merge closes reduce `O_open`; they do not prove landing
capacity.

Use the lower of the 6-hour and 24-hour qualifying merge rates so a short burst
does not inflate admission. If flow attribution is not available, total merge
rate may be shown only as an explicitly optimistic upper bound; it is not a
final `C_land` value.

## Atomic admission and steering

Counting only materialized PRs leaves a race: many agents can observe one free
slot and all begin new changes. The admission authority therefore maintains
short-lived, atomic reservations:

```text
R_reserved = tokens granted for accepted work that has not materialized as a PR
vacancies  = max(0, WIP_limit - O_open - R_reserved)
```

A one-repository task consumes one token; an approved cross-repository pair
consumes two. A reservation converts to `O_open` when the PR appears and is
released on cancellation or lease expiry before code starts. The decision log
binds each token to the agent, task, repository count, snapshot, and expiry.

The steer is mechanical:

- `W_effective > WIP_limit` — **DRAIN_ONLY**. Start no cold backlog and grant
  no new-PR tokens. Agents shepherd their one existing PR or adopt rebase,
  review, validation, conflict, closure, and abandoned-draft cleanup work from
  the open pool.
- `W_effective == WIP_limit` — **REPLACE_ONE**. A merge or close must free a
  token before another task can start. There is no speculative replacement.
- `W_effective < WIP_limit` — **ADMIT_COLD**. Atomically grant at most
  `WIP_limit - W_effective` tokens and pull that many highest-priority
  cold-area backlog units. Prefer a coherent PR over artificial slicing.

If an agent disappears, its PR remains in `O_open` and becomes drain work while
`A_pr` falls, immediately tightening the limit. If new agents become live,
their capacity raises the limit only when landing capacity permits it.

## Dated worked example

This example is a derivation check, not a current live report:

- The task's 2026-08-05 local notes record 77 open Hermit PRs and 13 open
  Reverie PRs, so dated `O_open = 90` (38 ready + 39 draft in Hermit; Reverie
  draft split was not recorded).
- The owner-supplied active fleet count is 12. With no proven two-PR exceptions,
  `A_pr = 12`.
- The previously frozen aggregate landing measurements were 18 merges/6h =
  3.00/h and 56 merges/24h = 2.33/h. Because those measurements are aggregate,
  not flow-attributed, they establish only an optimistic capacity upper bound:
  `floor(min(3.00, 2.33) * 24) = 55`.
- Even that optimistic capacity leaves the live-agent term tighter:
  `WIP_limit <= min(12, 55) = 12`.
- Against the dated PR snapshot, excess WIP is at least `90 - 12 = 78`, or
  7.5 open PRs per active agent. The only valid steer is **DRAIN_ONLY**.

The separate `131 implemented-unlanded` task-record figure is about 10.9 task
records per supplied agent and an excess of 119 relative to 12, but it is not
substituted for `O_open`. The current true values remain unknown until a fresh
egress-backed PR snapshot and a fresh joined roster snapshot are taken.

## Report record

Each control-cycle record contains, in one object:

```text
as_of
source URLs/digests and repository allowlist version
included/excluded live-agent rows with c_i and reason
A_pr
flow merge counts and elapsed windows (6h, 24h)
mu_flow, S, C_land, WIP_limit
open PR identities and per-category subtotals
R_reserved with task/agent/token/expiry identities
W_effective, excess_or_vacancies, mode
admission/drain decisions made from that record
```

This carries the conditions with the number: reporting a bare limit such as
`12`, a bare PR count, or an unqualified `agents=12` is invalid evidence.

## Acceptance brackets

The eventual controller is not complete until fixtures show both sides:

1. **Over-limit negative:** with `A_pr=6`, qualifying `C_land=20`, `O_open=7`,
   and `R_reserved=0`, cold-backlog admission is refused and drain mode fires.
2. **At-limit race negative:** with one apparent vacancy, two concurrent
   requests yield exactly one durable token.
3. **Under-limit positive:** with `A_pr=6`, qualifying `C_land=20`, `O_open=4`,
   and no reservations, exactly two tokens are available and cold-area work is
   selected.
4. **Landing-bound positive:** with `A_pr=12` and `C_land=5`, the limit is five,
   proving the formula does not blindly mirror headcount.
5. **Stale-agent negative:** an ACTIVE row with an expired heartbeat contributes
   zero to `A_pr`.
6. **Population-integrity negative:** 131 implemented task records plus 90 open
   PRs still produce `O_open=90`, not 221 or 131.

This document designs the local control contract only. It performs no network
query, PR mutation, validation run, or live admission action.
