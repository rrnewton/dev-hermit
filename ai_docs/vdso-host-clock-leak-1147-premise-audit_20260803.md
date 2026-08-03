# vDSO clock status: #1147 neutralized; retired LiteInst leaked

- Date: 2026-08-03
- Host: `devbig014` (Linux x86-64, 316 logical CPUs)
- Runtime under test: Hermit `bfb0a9ef1c303d1977f5f02903b70cc93e514cb5`,
  Reverie `d973a85b328610c14c41c39fa57495b9f77c3c90`
- PR under audit: [rrnewton/hermit#1147](https://github.com/rrnewton/hermit/pull/1147),
  head `683fb5ca25b6b4af2391c634a01f5245349a46ad`

## Corrected state

Hermit #1147's exact Reverie pin **already neutralizes vDSO clocks**. A separate,
retired pure in-guest LiteInst path **did leak host time under `--strict`**. The
same-host native cost of redirecting `clock_gettime` from vDSO to a real syscall
is **30.44 ns/call -> 113.36 ns/call: +82.92 ns, or 3.72x** (five repetitions of
10,000,000 calls per path). These are the durable findings.

The exact #1147 lockfile pins Reverie
`2afd1ecc576085332f71f02dfcea0de635d7026b`. That revision already contains
[Reverie `edebc286`](https://github.com/rrnewton/reverie/commit/edebc286342c6ff2a547dc3292cb9ea369497b27),
which rewrites vDSO clock entry points into real syscalls. Strict runtime tests
at the recorded snapshot confirm that the same mechanism routes libc clock reads
through Detcore and returns advancing deterministic time.

A **separate retired-path defect is real**: the former pure in-guest LiteInst
backend leaked host time through vDSO under `--strict`. Its raw
`clock_gettime` syscall was determinized, while libc's vDSO call was not. That
backend launched without the ptrace lifecycle that applies `vdso_patch`; it is
not the DBI code changed by #1147 and is not the tested ptrace-hosted LiteInst
hybrid.

No product patch is warranted for #1147 on this evidence.

## Provenance correction

The complete pushed #1147 review record contains no vDSO or host-clock finding.
Its blockers concern DBI exec/coordinator continuity, asynchronous run-queue
admission, failed-exec rollback, non-leader exec, and teardown. The task premise
was reconstructed from a truncated terminal-pane fragment, which was not enough
evidence to attribute a finding to the review. Runtime measurement and the exact
locked source establish the corrected state above.

## Why the distinction matters

The vDSO can answer `clock_gettime` by reading kernel-provided memory in user
space. No syscall instruction is executed, so syscall interception alone cannot
observe or determinize the call. A correct backend must neutralize the vDSO
entry point or provide a deterministic vDSO data source.

Reverie's existing neutralization replaces `__vdso_clock_gettime`,
`__vdso_clock_getres`, `__vdso_gettimeofday`, `__vdso_getcpu`, and
`__vdso_time` with `mov syscall_number,%eax; syscall; ret` stubs in
[`reverie-dbi/native/client.c`](https://github.com/rrnewton/reverie/blob/2afd1ecc576085332f71f02dfcea0de635d7026b/reverie-dbi/native/client.c#L1415-L1467).
The redirected call then reaches Detcore's ordinary deterministic clock path.
This substitutes deterministic time; it does not freeze the clock or manufacture
determinism by destroying clock functionality.

## Method

The probe reads `CLOCK_REALTIME` twice in the same process:

```c
clock_gettime(CLOCK_REALTIME, &vdso);              // libc/vDSO entry point
syscall(SYS_clock_gettime, CLOCK_REALTIME, &raw); // explicit syscall
```

The short probe records one pair. The sequence probe records eight alternating
pairs, exposing progression rather than merely matching first and final values.
Hermit runs used `--strict --verify`; a pass therefore means two runs produced no
substantive DETLOG differences. The tests ran at host load approximately
114-121, so the functional result is valid but wall-time measurements below are
reported with their high-load limitation.

Two revisions must not be conflated:

- **Exact #1147:** Hermit `683fb5ca`, Reverie `2afd1ecc`. Its source contains the
  neutralization. Runtime is **not determined** because rebuilding this old DBI
  revision was blocked by the host BpfJailer while DynamoRIO opened an elfutils
  build input.
- **Runtime test snapshot:** Hermit `bfb0a9ef`, Reverie `d973a85b`. This tests the
  still-present neutralization mechanism and the backend implementations at that
  snapshot, not the exact #1147 binary.

## Runtime evidence

| Backend / revision | Mode and sample | Observed result | Attribution |
| --- | --- | --- | --- |
| Native | two one-pair runs | `1785786972/1785786972`, then `1785786973/1785786973` | Host clock changes as expected. |
| Ptrace, tested snapshot | strict L2, eight pairs | first `1767225600.002519045/002529895`; last `002723745/002733765`; no DETLOG difference | Both paths are deterministic and advancing in roughly 10-50 us steps. |
| DBI, tested snapshot | strict L2, eight pairs | first `1767225600.155500000/160500000`; last `242000000/247000000`; identical memory hash `ff270cc6271a6d6c` | INFO logs show every libc and raw read entering `clock_gettime`; virtual time advances 5 ms per call. |
| LiteInst ptrace-host hybrid, tested snapshot | strict L2, one pair | `1767225600/1767225600`; no DETLOG difference | The tested hybrid's ptrace lifecycle neutralizes the vDSO. |
| e9patch preprocessing + ptrace runtime, tested snapshot | strict L2, one pair | `1767225600/1767225600`; no DETLOG difference | This result belongs to the ptrace runtime, not an independent e9patch execution backend. |
| SaBRe, tested snapshot | strict L2, one pair | `1767225600/1767225600`; no DETLOG difference | No leak observed in the strict probe. |
| KVM, tested snapshot | strict, one pair attempted | **Not determined:** the probe hung without output and was killed | Do not infer runtime behavior from source alone. |
| DBI, exact #1147 | build attempted | **Not determined:** BpfJailer blocked the old DynamoRIO dependency build | Source contains the fix; exact-head runtime was not established. |
| Pure in-guest LiteInst, Hermit `1470de8` / Reverie `456b628` | strict, one pair | `vdso=1785660392 raw=1767225600` | **Real strict leak:** vDSO returned host time while the intercepted raw syscall returned the deterministic epoch. |

The tested DBI INFO trace is especially decisive: calls made through libc appear
as inbound `clock_gettime` syscalls alongside the explicit raw calls. A vDSO fast
path that remained open would not generate those syscall records.

### Variance classification

The historical pure in-guest LiteInst result is a host-derived value, not a
wrong-but-stable deterministic constant. Its one retained strict sample matches
contemporaneous host wall time, and adjacent native controls show the host value
changing by one second. A repeated historical strict sample was not retained, so
the strict run-to-run rate is **not determined**. The causal split within one
process (`vdso=host`, `raw=epoch`) is sufficient to establish the escape path.

The tested ptrace and DBI sequence probes instead show an advancing virtual
clock and exact L2 agreement. Ptrace exposes microsecond-scale progression in
this probe. DBI's clock model at the tested snapshot advances in 5 ms increments
per intercepted call; that is existing Detcore behavior, not a mitigation
introduced by this audit. A fixed 5 ms increment is quantized, so this result
must not be described as proof of fine-grained DBI time. The narrower conclusion
is that vDSO neutralization preserves the same clock semantics as DBI's raw
syscall path; it does not add further rounding or freezing.

## Backend scope

The defect class applies to any backend that allows guest vDSO clock code to run
without replacing its data source or redirecting its entry points. It is not
universal merely because a backend intercepts clock syscalls correctly.

The historical pure in-guest LiteInst path demonstrated the vulnerable shape:
it launched with `std::process::Command::spawn()` and used in-process seccomp to
trap syscall instructions. The vDSO issued no syscall, and no ptrace lifecycle
ran `vdso_patch`. The tested LiteInst host hybrid uses
`TracerBuilder::<T>` and explicitly assigns lifecycle ownership to ptrace; the
runtime result above confirms the difference.

KVM scope remains unresolved by measurement. The failed KVM attempt cannot be
turned into a pass or a leak claim, and source inspection is only a hypothesis
generator.

## Cost of the existing remedy

The same-host native microbenchmark alternated five samples of 10,000,000
`CLOCK_REALTIME` reads per path:

| Native path | Median | Samples (ns/call) |
| --- | ---: | --- |
| libc vDSO | 30.44 ns/call | 30.39, 30.59, 30.40, 30.44, 31.37 |
| raw kernel syscall | 113.36 ns/call | 113.06, 113.00, 113.36, 113.63, 115.58 |

Redirecting a native vDSO call to a kernel syscall therefore has a measured
floor cost of about **83 ns/call**, or **3.72x** on this host. This is the cost
of crossing the native syscall boundary; it is not Hermit instrumentation cost.

For context, five high-load ptrace-strict samples of a 10,000-call loop had a
median 1,008 ms through the patched libc path and 1,316 ms through the explicit
raw path. A zero-call startup control had a 36.2 ms median, giving rough
startup-subtracted estimates of 97 us/call and 128 us/call. Host load was
above 114 and individual samples varied substantially, so these figures establish
only the order of magnitude. They do not establish that one intercepted path is
intrinsically faster than the other.

## Disposition

1. Do not patch #1147 for a vDSO leak that its review did not report and whose
   pinned Reverie revision already neutralizes.
2. Do not freeze, round, or replace virtual time with a constant. The observed
   tested paths return deterministic, advancing time.
3. Preserve the historical pure in-guest LiteInst result as a lifecycle-design
   requirement. If a supervisor-free in-guest backend is revived, it must gain a
   proved vDSO neutralization mechanism rather than relying on syscall trapping.
4. Keep KVM explicitly unresolved until a clean runtime probe completes.

## Reproduction and primary evidence

Historical source and retained results:
[`experiments/liteinst_inguest_vdso_clock_leak_20260802/`](../experiments/liteinst_inguest_vdso_clock_leak_20260802/README.md).
The new sequence and cost probes, metadata, and raw samples are retained in
[`experiments/vdso_host_clock_leak_1147_20260803/`](../experiments/vdso_host_clock_leak_1147_20260803/README.md).

```bash
cc -O2 -Wall -Wextra -Werror -o /tmp/vdso-clock-probe \
  experiments/liteinst_inguest_vdso_clock_leak_20260802/src/clock_probe.c

./target/debug/hermit --log=info run --tmp=/tmp \
  --backend ptrace --strict --verify /tmp/vdso-clock-probe
./target/debug/hermit --log=info run --tmp=/tmp \
  --backend dbi --strict --verify /tmp/vdso-clock-probe
```

Primary source links:

- [Hermit #1147 review and discussion](https://github.com/rrnewton/hermit/pull/1147)
- [Exact #1147 lockfile pin](https://github.com/rrnewton/hermit/blob/683fb5ca25b6b4af2391c634a01f5245349a46ad/Cargo.lock#L1723-L1726)
- [DBI vDSO neutralization at that pin](https://github.com/rrnewton/reverie/blob/2afd1ecc576085332f71f02dfcea0de635d7026b/reverie-dbi/native/client.c#L1415-L1467)
- [Tested LiteInst ptrace-host lifecycle](https://github.com/rrnewton/reverie/blob/d973a85b328610c14c41c39fa57495b9f77c3c90/reverie-liteinst/src/backend.rs#L197-L226)
