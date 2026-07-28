# Hermit examples cross-backend scorecard (2026-07-28)

## Result

All 20 requested `--strict --verify` cells completed within 60 seconds. The
backend-local determinism result is substantially better than the July 27
snapshot:

| Backend | Strict-verify passes | Result |
| --- | ---: | --- |
| ptrace | 5/5 | All examples verified |
| KVM | 5/5 | All examples verified in KVM concurrent-output mode |
| DBI | 4/5 | `race.sh` produced different run-1/run-2 schedules |
| SaBRe | 5/5 | All examples verified through Detcore/SaBRe |

Backend-local verification is not ptrace parity. Auxiliary strict single runs
were necessary because ptrace and SaBRe suppress guest stdout under `--verify`,
while KVM and DBI emit the first run's stdout. On the observable single-run
guest stream, byte-identical stdout plus matching exit status and guest stderr
was:

| Backend | Ptrace guest-output parity | Matching examples |
| --- | ---: | --- |
| KVM | 2/5 | `rand.py`, `timed-progress-bar.py` |
| DBI | 2/5 | `rand.py`, `timed-progress-bar.py` (DBI also emits backend diagnostics) |
| SaBRe | 1/5 | `timed-progress-bar.py` |
| **Aggregate** | **5/15** | Up from the July 27 available-backend parity score of 0/15 |

The result therefore has two defensible headlines: non-ptrace backends pass
**14/15 backend-local strict-verification cells**, but only **5/15 observable
guest-output parity cells** match ptrace.

## Snapshot and method

- Hermit: `adbfaca337c7b404c772573b327a4e739212f89d` (exact current
  `rrnewton/hermit:main`, clean detached product worktree)
- Reverie dependency: `f93bad17213609c85429613802ff367a2dd1f801`
- Host: Linux `6.17.13-0_fbk0_crackerjackhost_0_g2b4321c50d79`, x86-64,
  AMD EPYC 9D85 158-Core Processor, 316 logical CPUs
- KVM: `/dev/kvm`, character device `10:232`, mode `0666`
- `kernel.perf_event_paranoid=1`
- Rust: `rustc 1.99.0-nightly (be8e82435 2026-07-11)`
- Cargo: `cargo 1.99.0-nightly (59800466c 2026-07-07)`
- Release build: `cargo build --release --bin hermit` passed in 1m13s
- Execution date: 2026-07-28, America/Los_Angeles
- Level: L0 execution, backend-local verification, and output comparison
- Logging: default; no semantic relaxations
- Bound: 60 seconds per cell, with a separate process group and a five-second
  hard-kill grace period. No cell timed out and no audit process remained.

`examples/` contained one non-executable document (`README.md`) and these five
executable programs:

```text
date.sh
devrand.sh
race.sh
rand.py
timed-progress-bar.py
```

The requested commands were run through `with-proxy` exactly inside the timeout
wrapper:

```bash
cargo run --release --bin hermit -- run --strict --verify -- examples/EXAMPLE
cargo run --release --bin hermit -- run --backend kvm --strict --verify -- examples/EXAMPLE
cargo run --release --bin hermit -- run --backend dbi --strict --verify -- examples/EXAMPLE
cargo run --release --bin hermit -- run --backend sabre --strict --verify -- examples/EXAMPLE
```

For guest-output comparison, the same commands were also run without
`--verify`:

```bash
cargo run --release --bin hermit -- run --strict -- examples/EXAMPLE
cargo run --release --bin hermit -- run --backend BACKEND --strict -- examples/EXAMPLE
```

Stdout and stderr were captured separately. Cargo's `Finished`, `Running`, and
`Compiling` prologue lines were removed only from the stderr comparison. Raw
captures remain under `/tmp/hermit-examples-scorecard-0728/` and
`/tmp/hermit-examples-scorecard-0728-single/` on the measurement host.

## Strict-verify scorecard

PASS means the requested command exited zero and its backend-specific verifier
accepted both runs. It does not mean ptrace-equivalent output.

| Example | ptrace | KVM | DBI | SaBRe |
| --- | --- | --- | --- | --- |
| `date.sh` | PASS | PASS | PASS | PASS |
| `devrand.sh` | PASS | PASS | PASS | PASS |
| `race.sh` | PASS | PASS | **FAIL** | PASS |
| `rand.py` | PASS | PASS | PASS | PASS |
| `timed-progress-bar.py` | PASS | PASS | PASS | PASS |

Exact exits and elapsed wall time:

| Example | ptrace | KVM | DBI | SaBRe |
| --- | ---: | ---: | ---: | ---: |
| `date.sh` | 0 / 401 ms | 0 / 1,756 ms | 0 / 1,349 ms | 0 / 1,032 ms |
| `devrand.sh` | 0 / 433 ms | 0 / 2,886 ms | 0 / 1,084 ms | 0 / 1,195 ms |
| `race.sh` | 0 / 1,025 ms | 0 / 3,200 ms | **1 / 1,269 ms** | 0 / 1,362 ms |
| `rand.py` | 0 / 2,356 ms | 0 / 1,270 ms | 0 / 7,550 ms | 0 / 1,444 ms |
| `timed-progress-bar.py` | 0 / 27,272 ms | 0 / 21,978 ms | 0 / 9,874 ms | 0 / 13,120 ms |

### Requested-command stdout

Each entry is `bytes / SHA-256`. Empty stdout has the standard SHA-256 prefix
`e3b0c442...`.

| Example | ptrace | KVM | DBI | SaBRe |
| --- | --- | --- | --- | --- |
| `date.sh` | 0 / `e3b0c44298fc` | 30 / `0c20e68f4cb5` | 30 / `4bb7221dc1cc` | 0 / `e3b0c44298fc` |
| `devrand.sh` | 0 / `e3b0c44298fc` | 200 / `f5edcf77a864` | 200 / `01a591f3ba88` | 0 / `e3b0c44298fc` |
| `race.sh` | 0 / `e3b0c44298fc` | 403 / `671c5fe44211` | 0 / `e3b0c44298fc` | 0 / `e3b0c44298fc` |
| `rand.py` | 0 / `e3b0c44298fc` | 30 / `e1b8db378cfd` | 30 / `e1b8db378cfd` | 0 / `e3b0c44298fc` |
| `timed-progress-bar.py` | 0 / `e3b0c44298fc` | 53 / `d8778ce33675` | 53 / `d8778ce33675` | 0 / `e3b0c44298fc` |

This is a CLI presentation difference: ptrace and SaBRe's generic verifier
captures both runs and emits no guest stdout; KVM and DBI emit their accepted
first-run stdout. Exact combined-output parity is consequently 0/15 even when
the underlying single-run guest stdout matches.

### Requested-command stderr

Each entry is normalized `bytes / SHA-256` after removing only Cargo prologue
lines. These are exact captures, not claims of cross-backend equality.

| Example | ptrace | KVM | DBI | SaBRe |
| --- | --- | --- | --- | --- |
| `date.sh` | 485 / `ab344f85f9a1` | 238 / `d16bed087fff` | 1,803 / `77cf71a9f337` | 475 / `77332ea4d702` |
| `devrand.sh` | 485 / `f5bd210e80fb` | 238 / `d16bed087fff` | 1,806 / `daef672f1dc9` | 475 / `e596926286b6` |
| `race.sh` | 487 / `feb8997d7250` | 238 / `d16bed087fff` | 584 / `60464fa6a01b` | 473 / `f84cd97bf2af` |
| `rand.py` | 485 / `c0a4baa41c9f` | 238 / `d16bed087fff` | 1,168 / `926194201a6c` | 477 / `e7334e3e23d1` |
| `timed-progress-bar.py` | 499 / `57bcd0052ab4` | 238 / `d16bed087fff` | 1,182 / `68983d5838b3` | 477 / `df98539f0636` |

Ptrace and SaBRe report generic log comparison statistics. KVM explicitly
reports that concurrent mode compares guest output and exit status rather than
internal syscall trace order. DBI reports active Detcore/DynamoRIO client
initialization and compares observed guest-memory hashes.

## DBI failure

`race.sh` is the only strict-verification failure. Both DBI runs completed and
produced 403 bytes, but their schedules differed immediately:

```text
Error: DBI verification failed: guest stdout differed at byte 1
(run1_len=403, run2_len=403);
run1[0..121]="abaabababaabababaaabaaabaaaaaabaaabbabaaabaaaaabaaaaaabaaaaaaaaaabbbabbabaaaabaabaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
run2[0..121]="aaaabaaabaabaabaabaabaabaaaaaaabbababaabaaaaaaaaabaaabaaaabaabaababbbabbbabaaaaaaaaaaaaabaaaaaaaaaaabaaaaaaabaaaaaaaaaaaa"
```

This is a real backend-local determinism failure, not a timeout or packaging
failure. The single DBI run exits zero and confirms the active Detcore tool.

## Observable guest-output comparison

All 20 auxiliary strict single runs exited zero. The table compares stdout
byte-for-byte to ptrace; `stderr` is guest stderr after Cargo normalization.
DBI's backend diagnostics are reported separately and are not guest output.

| Example | KVM stdout | DBI stdout | SaBRe stdout | Guest stderr notes |
| --- | :---: | :---: | :---: | --- |
| `date.sh` | DIFF | DIFF | DIFF | Empty for ptrace/KVM/SaBRe; DBI diagnostics |
| `devrand.sh` | DIFF | DIFF | DIFF | Empty for ptrace/KVM/SaBRe; DBI diagnostics |
| `race.sh` | DIFF | DIFF | DIFF | KVM scheduler warning; DBI diagnostics |
| `rand.py` | **MATCH** | **MATCH** | DIFF | Empty for ptrace/KVM/SaBRe; DBI diagnostics |
| `timed-progress-bar.py` | **MATCH** | **MATCH** | **MATCH** | Empty for ptrace/KVM/SaBRe; DBI diagnostics |

Exact single-run stdout metadata:

| Example | ptrace | KVM | DBI | SaBRe |
| --- | --- | --- | --- | --- |
| `date.sh` | 30 / `305e2306a5ee` | 30 / `0c20e68f4cb5` | 30 / `4bb7221dc1cc` | 30 / `e6614629e3ba` |
| `devrand.sh` | 200 / `f7157cb11357` | 200 / `f5edcf77a864` | 200 / `01a591f3ba88` | 200 / `c9f80c9a1823` |
| `race.sh` | 403 / `44f4a9c58373` | 403 / `671c5fe44211` | 403 / `b85332438425` | 403 / `36be30d36af5` |
| `rand.py` | 30 / `e1b8db378cfd` | 30 / `e1b8db378cfd` | 30 / `e1b8db378cfd` | 30 / `91ee7cb99568` |
| `timed-progress-bar.py` | 53 / `d8778ce33675` | 53 / `d8778ce33675` | 53 / `d8778ce33675` | 53 / `d8778ce33675` |

### Exact short outputs

`date.sh` shows backend-specific logical-clock advancement:

```text
ptrace  2025-12-31_16:00:00_082507160
KVM     2025-12-31_16:00:00_059005000
DBI     2025-12-31_16:00:01_344000000
SaBRe   2025-12-31_16:00:00_002939250
```

`rand.py` matches ptrace under KVM and DBI, but not SaBRe:

```text
ptrace  93 78 74 85 81 69 1 82 64 20
KVM     93 78 74 85 81 69 1 82 64 20
DBI     93 78 74 85 81 69 1 82 64 20
SaBRe   94 29 27 3 66 78 66 55 45 26
```

Every backend produced this exact progress-bar stream:

```text
[..................................................]
```

### Longer-output characterization

- `devrand.sh`: every backend emitted the expected 200-byte hexdump format,
  but all four SHA-256 values differ. Random-stream determinization is not
  cross-backend compatible.
- `race.sh`: ptrace alternates `ba` 200 times. KVM emits 200 `a` bytes followed
  by 200 `b` bytes. DBI produces a mixed but different schedule and fails its
  own two-run verification. SaBRe emits 200 `b` bytes followed by 200 `a`
  bytes. All end with the expected line terminators and total 403 bytes.
- KVM's single `race.sh` stderr warning is:

```text
WARN detcore::scheduler: Nondeterministic external actions [DetPid(1)] jumped
in the middle of runnable work (1 tasks). Need to record this for reproducibility.
```

DBI emits its `Detcore Tool active` launcher line, initialization milestones,
and final `reverie-dbi: tool=Detcore ... memory_hash=...` summary on successful
runs. Ptrace and SaBRe single-run stderr is empty.

## Change from July 27

The comparison is not perfectly apples-to-apples: July 27 used default, INFO,
stack-detlog, and heap-detlog modes without strict verification, while this
task specifically requires `--strict --verify`. Still, the same five programs
now show clear progress:

- ptrace: 4/5 to 5/5; `timed-progress-bar.py` no longer times out.
- KVM: formerly no full parity cells, three failures/timeouts, and Python
  `#UD`; now 5/5 strict verification and 2/5 ptrace stdout parity.
- DBI: formerly four launcher timeouts and one divergent completion; now 4/5
  strict verification, 5/5 single-run completion, and 2/5 stdout parity.
- SaBRe: formerly unavailable; now 5/5 strict verification, 5/5 single-run
  completion, and 1/5 stdout parity.

The remaining cross-backend gaps are concrete: align logical-clock advancement,
random streams, and scheduling; make SaBRe's Python randomness match; fix DBI's
`race.sh` two-run nondeterminism; and unify `--verify` stdout presentation.
