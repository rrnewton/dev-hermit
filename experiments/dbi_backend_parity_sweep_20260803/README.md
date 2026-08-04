# DBI backend-parity sweep — backend-parity-c manifest

## Question
What is the DBI (DynamoRIO) backend's current parity/determinism against the
canonical cross-backend parity manifest (`backend-parity-c.toml`, 53 fixtures),
measured (not extrapolated) at the tip of `codex/dbi-getuid-identity-parity`?

## Method
For each fixture `program` in `tests/e2e/manifests/backend-parity-c.toml`:
1. compile: `cc -O2 -std=gnu11 -D_GNU_SOURCE -w <fixture>.c`
2. ptrace: `hermit run --strict --no-virtualize-cpuid --max-timeslice=disabled -- <bin>`
3. DBI:    `hermit run --backend dbi --strict --no-virtualize-cpuid --max-timeslice=disabled -- <bin>`
4. L1 parity = (ptrace exit == DBI exit) AND (ptrace stdout bytewise == DBI stdout)
5. L2 = `hermit run --backend dbi --verify ...` reports "Determinism verified"

`cpuid_probe` is measured WITHOUT `--no-virtualize-cpuid` because it is a
CPUID-*virtualization* fixture; the portable `--no-virtualize-cpuid` profile
makes ptrace trap-and-emulate report an unexpected identity (exit 1) while DBI
passes host CPUID through (exit 0). With virtualization on, both are identical.

## Results
- **L1 parity: 53/53** (bitwise-identical stdout + matching exit, DBI vs ptrace)
- **L2 determinism: 53/53** (DBI `--verify` deterministic)

See `results.csv`.

## Interpretation
The purpose-built backend-parity-c manifest is fully saturated at DBI L1+L2 as of
this SHA. Remaining DBI parity gaps (if any) live in the broader `c-programs.toml`
corpus (159 tests), which was NOT swept in this run.

## Harness caveats (measurement validity)
- Initial run used `-std=c11` (sets `__STRICT_ANSI__`, hiding GNU constants even
  with `_GNU_SOURCE`) → 18 spurious COMPILE_FAILs. Re-run with `-std=gnu11`: all PASS.
- `ioctl_fionread` first DBI run was blocked by a transient host BPFJailer
  FILE_OPEN policy (env, not product); passes cleanly on retry.
These artifacts are corrected in results.csv; do not read the raw first-pass logs
as parity failures.

## Reproduction
Re-run the sweep loop in the task handoff against the manifest program list.
