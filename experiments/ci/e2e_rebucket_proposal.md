# E2E CI rebucketing proposal

This proposal covers only the 1,163 direct `hermit run` commands in
[`e2e_commands.sh`](./e2e_commands.sh). It deliberately excludes Cargo tests,
record/replay drivers, `validate.sh` entry points, and Python test drivers from
[`unit_tests.sh`](./unit_tests.sh).

The inventory was extracted at Hermit
`6cd2b1d4716d165fed5c46bbeadeceebde7c9754` and audited against current Hermit
main `bfea2d972dbd79d36d9d25e2212a93c3fbc27e85`. Counts below describe the
inventory snapshot, not an assertion that every command is currently enabled
in a blocking workflow.

## Recommendation at a glance

| Bucket | Commands | Warm-run estimate | PMU | KVM | CPUID faulting |
| --- | ---: | ---: | --- | --- | --- |
| `hosted-fast` | 258 | 5-10 min serial; 3-6 min with two shards | No hard requirement | No | No |
| `hosted-medium` | 896 | 7-15 min serial; 5-10 min in three sublanes | No hard requirement | No | No |
| `selfhosted-hw` | 8 | about 17 min from current DAG hints | Yes for 7; KVM for 1 | One command | No demonstrated requirement |
| `occasional` | 1 | less than 1 min warm, bounded at 20 s in its test | No | No | Explicitly disabled |
| **Total** | **1,163** |  |  |  |  |

Build and dependency installation are excluded from the warm-run estimates.
Cold Rust builds are already separate CI prerequisites. The estimates are
planning ranges, not measurements of every inventory line: current DAG hints
give 180 s for `command_strict_verify`, 180 s for DBI parity, 300 s for runtime
entropy, 240+300 s for LevelDB fixture/focused tests, and 180 s for KVM CLI;
`validate.sh` estimates 5-15 minutes for strict compatibility and 2-5 minutes
for the complete LiteInst compatibility baseline.

## Complete assignment rule

The following table is a complete, non-overlapping assignment. Apply rows from
top to bottom to every non-comment line in `e2e_commands.sh`. The row counts sum
to 1,163, so no E2E command is implicit or unassigned.

| Priority | Selector in `e2e_commands.sh` | Count | Bucket | Reason |
| ---: | --- | ---: | --- | --- |
| 1 | Contains `--backend liteinst` | 856 | `hosted-medium` | LiteInst is a preload/instrumentation backend, not KVM. Hermit forcibly disables PMU/RCB timer delivery for it. The full baseline is estimated at 2-5 minutes, which fits the medium lane. |
| 2 | Contains `--backend kvm` | 1 | `selfhosted-hw` | Hard dependency on readable/writable `/dev/kvm`. |
| 3 | Source annotation contains `language_runtime_determinism.rs` | 5 | `selfhosted-hw` | These use default strict scheduling and are currently the hardware DAG's `runtime/entropy` family. Keep their PMU-strength coverage rather than silently accepting hosted PMU fallback. |
| 4 | Source annotation contains `hermit-cli/tests/leveldb.rs` | 2 | `selfhosted-hw` | These are the current hardware DAG's strict LevelDB family and use default strict scheduling. The fixture is expensive and PMU-strength scheduling is intentional. |
| 5 | Source annotation contains `app_strict_verify.rs java_version` | 1 | `occasional` | It explicitly disables CPUID virtualization and PMU time slicing, but the hosted application gate currently skips Java and the weekly suite owns managed-JVM diagnostics. This is a stability/cost choice, not a hardware requirement. |
| 6 | Contains `--backend dbi` | 2 | `hosted-medium` | DynamoRIO startup and release-artifact preparation make this medium. DBI needs neither PMU nor KVM nor CPUID faulting. |
| 7 | Category is `Language runtimes` and source is `command_strict_verify.rs` | 5 | `hosted-fast` | Small Python/Perl command probes already covered by the hosted command gate. |
| 8 | Category is `System utilities` or `Compression/archive` | 253 | `hosted-fast` | Short command-line workloads. Hosted compatibility mode disables CPUID virtualization and PMU time slicing where necessary. |
| 9 | Remaining `Language runtimes` or `Applications` command | 38 | `hosted-medium` | Interpreter/application startup, fixtures, local services, or package availability dominate; none has a demonstrated hard KVM/CPUID-faulting requirement. |

### Assignment cross-check

| Bucket component | Count |
| --- | ---: |
| Portable ptrace system utilities | 235 |
| Portable ptrace compression/archive | 18 |
| Small hosted Python/Perl command probes | 5 |
| **`hosted-fast`** | **258** |
| LiteInst system utilities | 790 |
| LiteInst compression/archive | 12 |
| LiteInst language runtimes | 36 |
| LiteInst applications | 18 |
| DBI smoke/verification | 2 |
| Remaining ptrace language runtimes | 20 |
| Remaining ptrace applications | 18 |
| **`hosted-medium`** | **896** |
| KVM verification | 1 |
| PMU-strength runtime entropy | 5 |
| PMU-strength LevelDB | 2 |
| **`selfhosted-hw`** | **8** |
| Managed JVM version diagnostic | 1 |
| **`occasional`** | **1** |

## `hosted-fast`: 258 commands

Run this bucket on GitHub-hosted Linux for every PR. Split by the existing
inventory categories so a large compatibility corpus does not serialize all
fast feedback behind one process:

1. **System utilities, default ptrace: 235.** This includes the basic
   compatibility corpus (`echo`, `true`, `hostname`, `ls`, `date`, text tools,
   procfs readers, compiler/binutils version probes) and the portable working
   envelope variants.
2. **Compression/archive, default ptrace: 18.** This includes `tar`, `gzip`,
   `bzip2`, `xz`, `zip`, `unzip`, `zstd`, and related version/fixture probes.
3. **Small language command probes: 5.** These are the five
   `command_strict_verify.rs` Python/Perl rows (`python-pid-ns`,
   `perl-user-ns`, `perl-squares`, `python-prlimit`, and `python-getrandom`).

These do not have a hard PMU requirement. Generic `--strict --verify` commands
may request the default PMU-backed maximum time slice, but Hermit falls back to
`--max-timeslice=disabled` when user-space perf is unavailable. More
importantly, `validate.sh --hosted-strict-compat-only` already makes the hosted
contract explicit with both `--max-timeslice=disabled` and
`--no-virtualize-cpuid`. The rebucketed lane should keep that explicit portable
configuration rather than depend on host perf policy.

The 51 `command_strict_verify.rs` rows currently have a 180-second aggregate
DAG hint. The larger strict compatibility gate is estimated at 5-15 minutes;
moving its interpreter/application tail to `hosted-medium` should leave this
bucket near 5-10 minutes serial, or 3-6 minutes with system utilities and
compression/language probes in separate shards.

## `hosted-medium`: 896 commands

Run these on GitHub-hosted Linux for every PR, but after the fast lane and in
separate sublanes so backend setup does not hide fast compatibility failures.

### LiteInst compatibility: 856

This is the full `--backend liteinst --no-namespace --strict --verify` matrix:
790 system utilities, 12 compression/archive commands, 36 language runtime
commands, and 18 applications.

LiteInst does **not** require PMU, KVM, or CPUID faulting. The Hermit runtime
warns that LiteInst does not implement PMU/RCB timer delivery and forces
`max_timeslice=None`. It does require the `detcore-liteinst` runtime to be
built. `validate.sh --liteinst-compat-only` estimates the entire baseline at
2-5 minutes, so this belongs in hosted-medium rather than a scarce hardware
queue or a weekly-only lane.

The inventory has 856 LiteInst lines while current `validate.sh` describes an
855-program baseline. Treat that one-row discrepancy as an inventory-ratchet
check before wiring the lane; do not silently drop a line to make counts agree.

### DBI smoke and verification: 2

```sh
hermit run --backend dbi -- /bin/true
hermit run --backend dbi --verify -- /bin/echo "$SUPER_DBI_MARKER"
```

DBI is DynamoRIO-based and has no PMU/KVM/CPUID-faulting dependency. Its current
hosted parity node is estimated at 180 seconds. Put these two commands in a DBI
sub-lane with the release build/artifact preparation they need.

### Ptrace runtimes and applications: 38

This is every remaining default-ptrace command in the `Language runtimes` and
`Applications` categories after the five PMU runtime rows, two LevelDB rows,
one Java diagnostic, and five small Python/Perl probes are removed. It includes
portable language/version probes plus Redis, SQLite, local HTTP, OpenSSL, Git,
CMake, and the integration-matrix fixture.

These commands need fixtures, installed packages, interpreter startup, or
localhost/service orchestration, but no line demonstrates a hard PMU, KVM, or
CPUID-faulting dependency. Keep portable strict invocations on
`--max-timeslice=disabled --no-virtualize-cpuid`. The Redis and integration
matrix rows use shell variables, so their owning harness must populate those
variables; the inventory line is a runbook template, not a standalone fixture
creator.

Run the LiteInst, DBI, and ptrace application sublanes concurrently. Estimated
serial time is 7-15 minutes; expected bucket wall time is 5-10 minutes when the
three sublanes are independent and build artifacts are already present.

## `selfhosted-hw`: 8 commands

### KVM: 1

```sh
hermit run --backend kvm --verify -- /bin/echo "$SUPER_KVM_MARKER"
```

This is the sole direct E2E command with a hard KVM dependency. It must run on
a self-hosted runner with readable/writable `/dev/kvm`. It does not establish a
PMU or CPUID-faulting requirement by itself.

### Runtime entropy: 5

The Ruby, Node, JVM, CPython, and Ruby-thread commands annotated from
`language_runtime_determinism.rs` use default strict scheduling. They are
currently represented by hardware DAG node `runtime/entropy` (300-second hint).
Keep this PMU-strength signal on self-hosted hardware. A separate portable
smoke may be added later, but it must not replace these rows without recording
the weaker `--max-timeslice=disabled` assurance.

### LevelDB: 2

The `c_test` and focused `leveldb_tests` commands annotated from
`hermit-cli/tests/leveldb.rs` belong with the pinned LevelDB fixture on the
hardware runner. Current hints are 240 seconds to prepare the fixture plus 300
seconds for focused tests. They use default strict scheduling and retain the
PMU-strength lane. They require neither KVM nor demonstrated CPUID faulting.

The current hints total roughly 17 minutes for KVM CLI, runtime entropy, and
LevelDB preparation/focused execution when serialized. Sharing the build and
running independent KVM/runtime work beside fixture preparation should reduce
wall time, subject to the runner's PMU serialization policy.

## `occasional`: 1 command

```sh
hermit run --strict --verify --no-virtualize-cpuid --max-timeslice=disabled -- /usr/bin/java -Xint -XX:+UseSerialGC -XX:ActiveProcessorCount=1 -version
```

The hosted application gate currently skips Java, while the weekly super suite
owns managed-JVM strict-verify diagnostics with a 20-second per-run bound. Keep
this version probe in the scheduled/manual lane until JVM startup is a stable
blocking signal. It explicitly disables PMU-backed time slicing and CPUID
virtualization, so its placement is not caused by missing hosted hardware.

## Capability conclusions

- **PMU:** Seven commands deliberately retain PMU-strength coverage: five
  runtime-entropy rows and two LevelDB rows. Other hosted commands must use the
  existing portable policy or Hermit's documented no-perf fallback.
- **KVM:** Exactly one direct E2E command selects `--backend kvm`; it is the only
  hard `/dev/kvm` dependency in this inventory.
- **CPUID faulting:** No direct E2E command proves a hard CPUID-faulting
  dependency. The CPUID/RDRAND tests in the hardware DAG are Cargo tests and
  therefore correctly live in `unit_tests.sh`, outside this proposal.
- **LiteInst:** No PMU/KVM/CPUID-faulting requirement. It is medium because of
  backend build/setup and matrix size, not hardware.
- **DBI:** No PMU/KVM/CPUID-faulting requirement. It is medium because of
  DynamoRIO and release-artifact setup.

## Rollout

1. Add reporting-only E2E shards using the selectors above and assert the
   bucket counts `258/896/8/1` at inventory generation time.
2. Make `hosted-fast` blocking after one clean week; keep the current aggregate
   test targets during comparison.
3. Make the three `hosted-medium` sublanes independently visible. Promote them
   to blocking only after missing-package and fixture failures are separated
   from Hermit regressions.
4. Keep the eight hardware commands behind explicit `pmu` or KVM runner labels;
   do not send the other 1,155 commands through the self-hosted queue.
5. Keep the Java diagnostic scheduled/manual and publish its duration and
   failure class rather than treating a skip as green.

This proposal changes scheduling only. It does not remove coverage and it does
not recommend scheduling both every raw inventory line and its aggregate Cargo
test wrapper; that would duplicate the same test rather than add assurance.
