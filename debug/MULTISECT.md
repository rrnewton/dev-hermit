# Rate-aware commit multisect

`multisect` narrows a load-sensitive regression without treating one flaky run
as a bisect fact. For each round it selects `K` evenly spaced interior commits,
adds the current high/low endpoints, samples the commits concurrently, and runs
`N >= K` repetitions serially for each commit. It recurses into the largest
adjacent `GREENISH -> WEDGED` rate step until the two endpoints are adjacent.
The final adjacent pair is sampled once more under matched load before the tool
reports convergence.

Every repetition is executed through the native `safe-ci-dag-runner box`
subcommand from `rrnewton/agent-utils` (PR #3 introduced the one-off wrapper).
There is no unboxed fallback: `BOX-UNAVAILABLE`, a missing verdict, or malformed
runner output stops the search as an infrastructure failure. The driver also
never switches branches or creates worktrees. The test command receives the
selected SHA and owns any private clone, prebuilt binary, or other safe commit
materialization it needs.

Example using a pre-staged, commit-aware harness:

```bash
SAFE_CI_DAG_RUNNER_BIN=/path/to/safe-ci-dag-runner \
./debug/multisect \
  --repo hermit \
  --episode demo5-regression \
  --good adbfaca3 --bad 2f3689bd \
  -k 3 -n 6 -j 5 \
  --mem 6G --timeout 175 --cores 4 \
  -- /path/to/run-one.sh '{commit}' '{rep}' '{output_dir}'
```

The command argv supports `{commit}`, `{short_commit}`, `{rep}`, `{round}`,
`{output_dir}`, and `{repo}` placeholders. The same values are exported as
`MULTISECT_COMMIT`, `MULTISECT_SHORT_COMMIT`, `MULTISECT_REP`,
`MULTISECT_ROUND`, `MULTISECT_OUTPUT_DIR`, and `MULTISECT_REPO`. Use an explicit
`bash -lc '...'` command when shell parsing is required. Commands run with
`--repo` as their working directory, so use repository-relative or absolute
harness paths. `--dry-run` validates the Git range and prints the first-round
selection without requiring the boxed runner.

Raw output goes under
`debug/<episode>/ignored/multisect-<timestamp>-<good>-<bad>/`:

- `config.json`: resolved SHAs, thresholds, containment limits, and command;
- `repetitions.csv`: one row per boxed repetition;
- `logs/round-NN/<sha>/rep-NNN/`: stdout, stderr, perf data, and result JSON;
- `round-NN.json`: sampled rates and the chosen recursive interval; and
- `result.json`: converged adjacent pair or an honest ambiguous/infra verdict.

The default rate bands are `GREENISH >= 0.67`, `WEDGED <= 0.33`, with a
required adjacent drop of at least `0.50`. Tune them explicitly for a different
experiment. Test the driver without cgroups or hardware via:

```bash
python3 -m unittest -v debug/test_multisect.py
```
