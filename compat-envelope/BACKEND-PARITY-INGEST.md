# Backend-parity runner -> scorecard ingest

Hermit's `tests/backend-parity/run_matrix.py` appends its live observations
directly to this outer workspace's `scorecard.csv`. Backend-parity measurement
state deliberately does not live in the inner Hermit repository.

The runner discovers dev-hermit by walking upward from the Hermit checkout, so
both `dev-hermit/hermit` and nested `dev-hermit/worktrees/<slot>/hermit`
layouts work. A standalone Hermit clone still runs the contracts but skips the
outer-write side effect.

```bash
cd ../hermit
python3 tests/backend-parity/run_matrix.py \
    --hermit target/release/hermit --backend dbi --strict --require-backend
```

Use `--parent-scorecard PATH` to select a scorecard explicitly or
`--no-parent-scorecard` for a deliberately side-effect-free run. Rows use the
shared 19-column scorecard schema with `bucket=backend-parity`; L1 is
`test_mode=strict`, L2 is `test_mode=verify`, and KVM L2 rows explicitly state
that their assurance is guest-visible rather than DETLOG-bitwise.

The runner serializes appends with a file lock so concurrent worktree runs
cannot interleave CSV records. Each invocation has a unique `run_id`, and the
renderer selects the newest observation for each logical cell.
