# Hermit examples cross-backend scorecard (2026-07-27)

## Result

No available non-ptrace backend matched the ptrace baseline across all requested
dimensions. Aggregate results were:

| Backend | Aggregate passes | Result |
| --- | ---: | --- |
| ptrace | 4/5 | `timed-progress-bar.py` timed out in ptrace heap-detlog mode |
| KVM | 0/5 | Two default runs completed with divergent output; the other three failed or timed out |
| DBI | 0/5 | Four script launchers timed out; `race.sh` completed with divergent output and logs |
| LiteInst | 0/5 | Required exec and fork injection paths are unsupported |
| SaBRe | 0 evaluated | Backend unavailable: no SaBRe executable |
| e9patch | 0 evaluated | Preprocessor unavailable: no `e9tool` |

The available alternate-backend parity score is therefore **0/15** across KVM,
DBI, and LiteInst. KVM's score alone is **0/5**.

## Snapshot and method

- Hermit: `c91c9c171e73b4dc5e1682b12bd7384bd19cef6d` (exact current
  `origin/main`, clean detached worktree)
- Host: Linux `6.17.13-0_fbk0_crackerjackhost_0_g2b4321c50d79`, x86-64,
  AMD EPYC 9D85 158-Core Processor
- KVM device: `/dev/kvm`, character device `10:232`, mode `0666`
- `kernel.perf_event_paranoid=1`
- Rust: `rustc 1.99.0-nightly (be8e82435 2026-07-11)`
- Cargo: `cargo 1.99.0-nightly (59800466c 2026-07-07)`
- Release build: `cargo build --release --bin hermit`
- Execution date: 2026-07-27, America/Los_Angeles
- Run level: L0 execution/output comparison. No `--strict` or `--verify` was
  requested or used. Log level was default or INFO as stated below. No semantic
  relaxations were enabled.

The five executable examples are `date.sh`, `devrand.sh`, `race.sh`, `rand.py`,
and `timed-progress-bar.py`; `examples/README.md` is not executable. There are no
Cargo example binaries under `target/release/examples/`.

The requested command shape is not runnable as written:

```text
cargo run --release -- run --backend ptrace -- ./target/release/examples/date
```

It exits 101 because the workspace has multiple binaries and no `default-run`.
The documented examples are scripts, not Cargo example targets. This audit
therefore used the exact release binary and ran each script directly:

```text
target/release/hermit run --backend BACKEND -- EXAMPLE
target/release/hermit --log INFO run --backend BACKEND -- EXAMPLE
target/release/hermit --log INFO run --backend BACKEND --detlog-stack -- EXAMPLE
target/release/hermit --log INFO run --backend BACKEND --detlog-heap -- EXAMPLE
```

Default ptrace/KVM runs used a 20-second bound except the first KVM `race.sh`
diagnostic, which used 120 seconds and still timed out. Repeated DBI mode probes
used a 10-second bound after the launcher hang was established. Exit 124 below
means the bound expired. A PASS requires exit 0 and byte-identical stdout,
stderr, INFO output, stack-detlog output, and heap-detlog output relative to
ptrace. An unavailable backend is UNSUPPORTED, not FAIL.

## Scorecard

| Example | ptrace | KVM | DBI | LiteInst | SaBRe | e9patch |
| --- | --- | --- | --- | --- | --- | --- |
| `date.sh` | PASS | FAIL | FAIL | FAIL | UNSUPPORTED | UNSUPPORTED |
| `devrand.sh` | PASS | FAIL | FAIL | FAIL | UNSUPPORTED | UNSUPPORTED |
| `race.sh` | PASS | FAIL | FAIL | FAIL | UNSUPPORTED | UNSUPPORTED |
| `rand.py` | PASS | FAIL | FAIL | FAIL | UNSUPPORTED | UNSUPPORTED |
| `timed-progress-bar.py` | FAIL (heap timeout) | FAIL | FAIL | FAIL | UNSUPPORTED | UNSUPPORTED |

## Ptrace/KVM comparison by mode

`out` and `err` report byte equality with the ptrace capture for the same mode.

| Example | Mode | Exit ptrace/KVM | out | err |
| --- | --- | ---: | :---: | :---: |
| `date.sh` | default | 0 / 0 | no | yes |
| | INFO | 0 / 0 | no | no |
| | detlog-stack | 0 / 127 | no | no |
| | detlog-heap | 0 / 127 | no | no |
| `devrand.sh` | default | 0 / 0 | no | yes |
| | INFO | 0 / 0 | no | no |
| | detlog-stack | 0 / 127 | no | no |
| | detlog-heap | 0 / 127 | no | no |
| `race.sh` | default | 0 / 124 | no | yes |
| | INFO | 0 / 124 | no | no |
| | detlog-stack | 0 / 127 | no | no |
| | detlog-heap | 0 / 127 | no | no |
| `rand.py` | default | 0 / 1 | no | no |
| | INFO | 0 / 1 | no | no |
| | detlog-stack | 0 / 127 | no | no |
| | detlog-heap | 0 / 127 | no | no |
| `timed-progress-bar.py` | default | 0 / 1 | no | no |
| | INFO | 0 / 1 | no | no |
| | detlog-stack | 0 / 127 | no | no |
| | detlog-heap | 124 / 127 | no | no |

INFO log sizes and `DETLOG` line counts also differ in every KVM comparison:

| Example | ptrace bytes / DETLOG lines | KVM bytes / DETLOG lines |
| --- | ---: | ---: |
| `date.sh` | 125,693 / 631 | 115,200 / 609 |
| `devrand.sh` | 158,708 / 789 | 145,919 / 765 |
| `race.sh` | 522,967 / 2,935 | 67,294 / 374 |
| `rand.py` | 6,741,249 / 37,665 | 233,928 / 293 |
| `timed-progress-bar.py` | 24,640,456 / 141,904 | 233,942 / 293 |

## Exact divergences

### `date.sh`

Both default runs exit 0 with an empty stderr and a 30-byte, newline-terminated
stdout. The virtual nanoseconds differ:

```diff
-2025-00-31_16:00:00_041738410
+2025-00-31_16:00:00_035785000
```

The full stdout SHA-256 values are:

- ptrace: `ed680c079f6139314da9b0a5865b39271f94e6fba02b307cbadb6077ab866729`
- KVM: `5176c513c14d0b753d511a2ee8df5bd355477bb2255c1c895f5dbbd6e2c47d5c`

This is not a newline or encoding issue.

### `devrand.sh`

Both default runs exit 0 with empty stderr and 200-byte stdout, but the
deterministic `/dev/urandom` streams differ. Full stdout SHA-256 values:

- ptrace: `f7157cb11357d100b6733157f12ee2395a1ed9a4c90ad636e06d248d37d602fc`
- KVM: `f5edcf77a8645391c7cdaa70592390c2365b1a0849dfc2729f9a6ca3ea874863`

### `race.sh`

Ptrace exits 0 and writes 403 bytes: 200 alternating `ba` pairs plus line
terminators. KVM does not finish even after 120 seconds, exits 124, and writes
201 bytes: 200 `a` bytes plus a newline. The background/fork branch never
contributes. Full stdout SHA-256 values:

- ptrace: `44f4a9c5837349cffaaf778ab4ddf4fc799d6bb3ef0d14b6fce4bc36f4000a93`
- KVM: `f2d620d16aed304f112c496df896f9c82e241159a52598fe2460064265404b1a`

The same KVM failure occurs under INFO logging (exit 124, 201 stdout bytes).

### Python examples

Ptrace completes both examples. KVM exits 1 before producing stdout for both
`rand.py` and `timed-progress-bar.py`:

```text
Error: KVM guest execution failed: guest exception vector 6 at 0x2e9b2cc (CR2=0x0)
```

The vector is x86 `#UD` (invalid opcode). Ptrace stdout is respectively:

```text
5 55 99 51 69 77 91 43 33 78 
[..................................................]
```

Ptrace heap-detlog execution of `timed-progress-bar.py` is itself incomplete:
it times out with exit 124 after 27 stdout bytes (`[..........................`).

### KVM detlog modes

Every KVM `--detlog-stack` and `--detlog-heap` run exits 127 before the example
starts, with 0 stdout bytes and this common fatal error:

```text
FATAL: cannot determine kernel version
```

The corresponding ptrace stack runs all exit 0. Ptrace heap runs exit 0 except
the bounded `timed-progress-bar.py` timeout described above.

## Other declared backends

### DBI

DBI passes a direct `/bin/true` preflight and reports an active Detcore tool.
However, `date.sh`, `devrand.sh`, `rand.py`, and `timed-progress-bar.py` time out
in every requested mode before the DynamoRIO client reports startup; stderr
contains only the launcher line. The timed-out launchers leave script
descendants behind, which required explicit cleanup.

`race.sh` exits 0 in every DBI mode with 403 stdout bytes, but its scheduling
order differs from ptrace and DBI writes backend diagnostics to stderr. Default
stdout SHA-256 is
`6bb5b2b81c19134c33c6e65c9b152b56c67d37999b2c29da5fb5ca93bb9808d7`,
versus ptrace
`44f4a9c5837349cffaaf778ab4ddf4fc799d6bb3ef0d14b6fce4bc36f4000a93`.

### LiteInst

LiteInst passes `/bin/true`, with the documented warning that PMU/RCB timer
delivery is not implemented. Every example fails immediately:

- `date.sh`: exit 126, `/usr/bin/date: Operation not supported`
- `devrand.sh`: exit 126, `/usr/bin/hexdump: Operation not supported`
- `race.sh`: exit 254, `reverie-liteinst: clone/fork injection is unsupported`
- both Python scripts: exit 126, `/usr/bin/env: 'python3': Operation not supported`

The same exit codes recur in INFO, stack-detlog, and heap-detlog probes.

### SaBRe and e9patch

SaBRe preflight exits 1 because the executable is absent; Hermit suggests
`HERMIT_INSTALL_DIR` or `HERMIT_SABRE_BINARY`. e9patch preprocessing exits 1
because `e9tool` is absent; Hermit suggests `HERMIT_E9TOOL`. e9patch is a
preprocessing path followed by ptrace execution, not an independent execution
backend. Neither unavailable path is counted as a tested failure.

## Conclusion

The strongest KVM results are `date.sh` and `devrand.sh`: both complete with
matching exit codes and empty stderr, but neither produces the ptrace byte
stream. KVM currently lacks parity for the fork workload and the host Python
binary, and both detlog memory modes fail before guest execution. The strict
all-dimensions criterion therefore has no KVM passes in this example suite.

Raw captures for this run are in `/tmp/hermit-examples-audit/` on the audit
host. They are transient; the tables, hashes, exits, sizes, and exact failure
signatures above are the durable evidence.
