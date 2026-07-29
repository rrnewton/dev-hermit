# Detached command wrapper

`scripts/detached-verify.rs` prevents noisy builds, fuzzers, seed sweeps, and
fork-heavy Hermit guests from flooding an agent context. It launches the
command through `nohup setsid`, redirects combined stdout/stderr to the parent
harness's ignored `ignored/logs/` directory, waits without streaming, and
prints only bounded grep and tail sections after completion.

Run one command:

```bash
scripts/detached-verify.rs run --name cargo-build --tail 8 \
  --grep Finished --grep error -- \
  with-proxy cargo build --workspace
```

Run a complete Hermit strict-verification command twice and compare the two
combined logs:

```bash
scripts/detached-verify.rs verify-twice --name python-pool --tail 8 -- \
  ./target/release/hermit run --strict --verify -- python3 pool.py
```

The summary reports each exit status, elapsed time, byte count, log path,
selected marker lines, and the final lines. `verify-twice` additionally reports
`comparison: identical` or the first differing byte and line. For a Hermit
`--strict --verify` command it reports both the raw comparison and a normalized
verdict that ignores only Hermit's random `/tmp/run*_log_*` filenames. All
other differences still fail the comparison. The wrapper exits nonzero when a
child fails or the applicable comparison diverges. Use `--help` for all
options.

The command is passed as an argument vector after `--`; it is not interpreted
by a shell. To use shell syntax deliberately, pass an explicit shell, for
example `-- bash -lc 'make -j8 && ./sweep.sh'`.
