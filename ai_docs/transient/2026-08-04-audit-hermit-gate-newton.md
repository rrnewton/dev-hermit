# Hermit Gate Runner Audit

Status: durable closure artifact for `audit_hermit_gate_newton`.

This document preserves the audit performed on 2026-08-03. It is an audit of
provenance, privilege, workload, and disposition. It did not modify runner or
repository state.

## Executive finding

`hermit-gate-newton` was created by an agent session on 2026-08-03 rather than
being pre-existing infrastructure. The session recorded a user/tool selection
of "Dedicated gate runner + fallback", but the available transcript did not
identify the human who supplied that response. Owner authorization and the
accountable human therefore remained **not determined**.

The runner executed as the unprivileged `newton` user with no effective Linux
capabilities, but it shared that user's home and credentials and had weak
systemd isolation. It was load-bearing for the Merge Gate despite the gate not
requiring special hardware. The recommended disposition was to migrate the
control-plane gate to GitHub-hosted capacity before removing the runner.

## Provenance

- Agent session: `51faa3b0-6472-4116-b69a-aa86d8de0ebc`, Claude Opus 4.8.
- 2026-08-03 02:59:55 UTC: the session recorded the selection "Dedicated gate
  runner + fallback".
- 03:05:41 UTC: the session requested a registration token from
  `repos/rrnewton/hermit/actions/runners/registration-token` and invoked
  `config.sh --name hermit-gate-newton --labels gate`.
- Local runner diagnostics began at 03:05:45 UTC and recorded configuration
  saved at 03:05:57 UTC. The `.runner` state assigned GitHub runner ID 24.
- 03:17:17-33 UTC: the session wrote and enabled the user service
  `hermit-gate-runner.service` and enabled user lingering.
- Hermit PR #1506 landed as `ebbe0b25` at 03:15:13 UTC and routed Merge Gate
  work to the `gate` label.

This proves session-level creation and the recorded selection. It does not
prove which human, if any, authorized the persistent registration.

## Runtime and isolation

At audit time, GitHub reported runner ID 24 online and idle with labels
`self-hosted`, `Linux`, `X64`, and `gate`. A completed job assigned to that ID
identified the machine as `devbig014`.

The local process ran as UID 212630 (`newton`) with `CapEff=0`, so it was not
host root. The user service was nevertheless weakly isolated:

- `NoNewPrivileges=no`
- no seccomp filter
- `ProtectHome=no`
- `ProtectSystem=no`
- `PrivateTmp=no`
- `PrivateUsers=no`
- `PrivateDevices=no`
- `systemd-analyze security` exposure score: 9.8, rated unsafe

Because workflow code shared the `newton` identity, it could read user-owned
credentials such as mode-0600 GitHub CLI configuration. "Not root" was not an
adequate trust boundary.

## Targeting and measured load

At the audited Hermit main revision, only `.github/workflows/merge-gate.yml`
targeted `[self-hosted, gate]`. Its work consisted of GitHub API calls,
shell/JQ processing, workflow dispatch, and checkout of trusted main. It did
not require PMU access, KVM, ptrace, mounts, containers, privileged execution,
or a Rust build.

For the fixed window 2026-08-02 18:03:31 UTC through 2026-08-03 18:03:31 UTC:

- runner ID 24 handled 1,006 Merge Gate jobs and 7,747 busy seconds;
- sibling ID 22 handled 238 jobs / 31,151 busy seconds, including 114 Merge
  Gate jobs / 751 seconds;
- sibling ID 23 handled 243 jobs / 28,758 busy seconds, including 120 Merge
  Gate jobs / 834 seconds;
- sibling ID 2 handled 179 jobs / 54,855 busy seconds, including 15 Merge Gate
  jobs / 115 seconds.

All four self-hosted runners handled 1,666 jobs and 122,511 service seconds
(34.03 runner-hours). Merge Gate accounted for 1,255 jobs and 9,447 seconds
(2.62 runner-hours). ID 24 existed for only about 15 hours of the window, so
these are observed counts, not a normalized daily rate.

A direct pre-switch example showed the required Merge Gate succeeding on a
GitHub-hosted runner in three seconds. Hosted pickup latency was variable: the
pre-switch sample had pickup p50/p90/p95/max of 17/426/667/1108 seconds. Thus
hosted execution was functionally sufficient, but not guaranteed to start
immediately.

## Sibling runner risk

Runner IDs 2, 22, and 23 predated ID 24 by at least four days and ran on a
different machine. Their registration actor and creation time were not
determined from the available GitHub objects or local transcripts.

At least one sibling Merge Gate job ran as UID 0 inside its execution
environment and copied `/root/.gitconfig`. Tracked runner tooling also enabled
`RUNNER_ALLOW_RUNASROOT=1` and documented a rootful privileged container
launcher. Whether container UID 0 mapped to host root could not be established
without access to that machine. The audit therefore established root inside
the execution environment, not host-root equivalence.

## Disposition

Recommended safe order:

1. Collapse Merge Gate fan-out to reduce runner acquisitions.
2. Move the Merge Gate workflow to GitHub-hosted execution and verify the
   required check on an exact PR head.
3. Remove the `gate` label from PMU runners.
4. Deregister `hermit-gate-newton`.

Removing every gate-labelled runner before the workflow migration would leave
required checks queued indefinitely and stop landing. Removing only ID 24
would retain function through the PMU runners but restore burst latency and
consume hardware capacity that Merge Gate did not need.

If ID 24 had to remain temporarily, the audit recommended a dedicated
unprivileged identity with no access to the developer home or credentials,
plus meaningful service isolation.

## Evidence and limitations

The original audit used GitHub runner/job API state, local runner diagnostics,
systemd/process/capability inspection, the exact agent transcript, workflow
source, and fixed-window job aggregation. Temporary raw API captures were not
retained as versioned artifacts. This document preserves the derived findings
and explicitly marks authorization, sibling provenance, and host-root mapping
as not determined.

