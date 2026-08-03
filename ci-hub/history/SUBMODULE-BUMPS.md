# Single-variable submodule bumps

Every parent gitlink advance is an A/B experiment:

1. **A is known green.** Record a durable evidence file containing the full
   parent A SHA. For determinism-related subjects, A needs a powered repeated
   probe; one green run is not evidence against a probabilistic hang.
2. **Change one variable.** Fetch the chosen submodule's `origin/main`, require
   a fast-forward, and create B whose tree changes exactly that one gitlink.
   Do not mix source, policy, generated files, or a second gitlink into B.
3. **Verify B.** Run the declared argv with stdout/stderr in a timestamped
   durable log. A failing B stays isolated for diagnosis; do not amend unrelated
   fixes into it and obscure attribution.
4. **Record the result.** Append the typed A/B record to
   `ignored/ci-hub/submodule-bumps.jsonl`. The operation's wall/CPU JSON and
   verification log live under `ignored/ci-hub/submodule-bumps/<run-id>/`.

Use a clean isolated parent bump branch/worktree. Inspect without mutation:

```bash
make single-submodule-bump ARGS='plan \
  --submodule agent-utils \
  --base <40-hex-known-green-parent-A> \
  --base-evidence <evidence-containing-A>'
```

Execute only after the plan passes and a verification argv is selected:

```bash
make single-submodule-bump ARGS='run \
  --submodule agent-utils \
  --base <40-hex-known-green-parent-A> \
  --base-evidence <evidence-containing-A> \
  -- <verification-program> <arg> ...'
```

For a determinism-related bump, add `--verification-kind determinism` and
`--matched-load-calibration <path>`. The reference powered probe is
`experiments/multisect_detcore_misc_20260803/matched.sh`: it co-schedules A, B,
and a known-bad calibrator; counts a wave only when the calibrator is FLAKY; and
classifies mixed results as red. Its established invocation shape is:

```bash
experiments/multisect_detcore_misc_20260803/matched.sh \
  32 20 10 a:<A-test-bin> b:<B-test-bin> z_HEAD:<known-bad-test-bin>
```

The verification argv may invoke that probe through an explicit `bash -lc`
wrapper when paths need expansion. The procedure does **not** trust
`matched.sh`'s process status (the probe prints classifications and normally
exits zero): it parses every wave, discards waves where `z_HEAD` is PASS,
requires A and B to be PASS in every powered wave, and records FLAKY/FAIL as
red. An underpowered run or a red A is not a B pass. A single passing sample
must never be recorded as a green determinism result.

## Result schema

`submodule-bumps.jsonl` is append-only and locked. Schema v1 records:

- identity: `run_id`, `recorded_at`, `submodule`;
- isolated transition: `parent_a`, `parent_b`, `submodule_a`, `submodule_b`,
  `submodule_remote_main`;
- evidence: `base_evidence`, `verification_kind`, `verification_command`,
  `matched_load_calibration`;
- outcome: `verification_command_exit`, semantic `verification_exit`,
  `verdict`, `matched_load_summary`, `log_path`, `cost_record_path`.

`parent_a`/`parent_b` are stable joins for the local/GitHub commit-history
store. Full-state results are never overwritten.

## Current gate and first queued instance (2026-08-03)

Do **not** run a real bump yet:

- Hermit main is not a valid green A. The calibrated matched-load experiment
  measures `test.detcore_misc` vfork/reap hangs at 16-23% under load. Establish
  A only after Reverie PR #355 lands, Hermit's Reverie pin includes it, and the
  same matched-load probe confirms the repaired baseline.
- Agent-utils has no A→B today: parent pin, checkout, and `origin/main` are all
  `1c0e9c3c4928dac192e0115291f114863bc03a0d` (zero commits behind).

The first queued execution is agent-utils PR #5, branch
`codex/cpu-time-timeout` at
`f43c3ea96ac1286cfebefae263834b20d30d53de`. It becomes eligible only after
that commit merges to agent-utils `main` and a real green parent A exists.
