# DBI/LiteInst out-of-process RPC serialization overhead

## Answer

For the actual 87-byte Detcore `GlobalTool` request/response pair measured here,
request encode+decode plus response encode+decode adds a median **226.008 ns
(0.226 us) per RPC**, with an interquartile range of 225.698-226.901 ns over
nine paired repetitions. This is codec work only. It excludes framing, Unix
socket syscalls, process scheduling, coordinator dispatch, Detcore scheduling,
and guest instrumentation.

That result does not justify performance work on the patching backends yet:

- Hermit's public DBI path uses `DbiRunner::{status,output}` and the in-process
  `GlobalState::receive_rpc`; it does not select `run_with_global`, so it does
  not currently pay per-RPC serde. DBI also rejects
  `--no-sequentialize-threads`, so there is no live non-sequentialized DBI
  control from which to infer scheduler serialization cost.
- Hermit's public LiteInst path uses `run_host_with_preload`; the ptracer owns
  the sole Tool/GlobalTool and the preload supplies installation and SIGTRAP
  hooks. It avoids the in-guest RPC codec, but a ptracer remains in the syscall
  path. The optional in-guest `BlockingRpcClient` path would pay codec plus UDS
  transport costs.

Optimization priority therefore remains the real instrumentation/ptrace and
scheduler path, not this approximately 0.226 us codec component.

## Provenance

- Run UTC: 2026-08-07 05:02:15Z; run ID
  `20260807T050215Z-devbig014-k1`.
- Parent base: `rrnewton/dev-hermit` `8c3c7df`; exact revisions and hashes are
  in [`metadata.json`](metadata.json).
- Hermit: `75506005`; its actual Reverie pin: `0ae0c01b`; live standalone
  Reverie main: `6144323c`; LiteInst2: `8bf704fe`.
- Short host: `devbig014`; Linux 6.18.39; AMD EPYC 9D85; 316 logical CPUs.
- Producer: [`harness/src/main.rs`](harness/src/main.rs), SHA-256
  `7770825211202792eb12954fc480c40cc9cd5e559fb61db99dcf06a2330938c9`.

## Methods

The fixture uses the real associated types
`<detcore::GlobalState as GlobalTool>::{Request,Response}`. The request is
`GlobalTimeLowerBound`; the response carries the matching logical-time result.
Before timing, the runner encodes and decodes both values and asserts exact
equality. The request encodes to 74 bytes and the response to 13 bytes using
bincode 2 `config::legacy()`, the codec used by both DBI `sync_rpc` and
`reverie-rpc-transport`.

The native/control operation clones the same typed request and response. The
changed variable is only four codec operations: request encode+decode and
response encode+decode. Each variant receives 100,000 untimed warmups. Nine
repetitions then execute 1,000,000 iterations per variant, alternating which
variant runs first: 9,000,000 timed direct iterations plus 9,000,000 timed codec
iterations. The statistic is the median of paired per-repetition deltas.

Exact measurement invocation:

```sh
systemd-run --user --scope --collect \
  -p MemoryMax=2G -p TasksMax=64 -p CPUQuota=100% \
  timeout 120s python3 \
  /tmp/dbi-liteinst-rpc-artifact-w21.9LewGF/repo/experiments/single-core-box-affinity-mechanism_20260804/run-on-k-free-cores.py \
  1 -- /tmp/dbi-liteinst-rpc-serialization-bench/target/release/rpc-serde-bench
```

The scope was `run-p1034762-i79900765.scope` (invocation
`dbe1be1157f143b4aaef7e7c56709a89`). The affinity helper selected CPU 1 and
all descendants inherited that one-CPU set. The systemd scope additionally
limited memory to 2 GiB, tasks to 64, aggregate CPU quota to 100%, and the
explicit timeout to 120 seconds. The adjacent load probe found 12.26% executing
CPU (38.75/316 cores), 87.68% idle, and 70.50% memory available; see
[`load.json`](load.json). No sample failed or timed out.

Raw runner output is [`raw.txt`](raw.txt); paired rows are
[`results.csv`](results.csv).

## Evaluation

`direct_clone` measures ownership/copy work on the typed operands without a
wire format. `bincode_roundtrip` measures the same work plus the four codec
operations that one request/response RPC requires. It is a shared codec
measurement for DBI and LiteInst, not two backend end-to-end benchmarks: their
codec and Detcore types are the same, while their live routing differs.

This microbenchmark intentionally does not execute guest code. It stresses
allocation and bincode serialization on one host CPU. It does not stress UDS
framing, kernel crossings, wakeups, ptrace/SIGTRAP instrumentation, backend
translation/patching, or Detcore's sequential scheduler.

## Results

| Variant | n | Median ns/RPC | p25-p75 | Min-max |
| --- | ---: | ---: | ---: | ---: |
| Direct typed clone | 9 | 13.415989 | 13.097039-13.435317 | 13.084280-13.852454 |
| Four bincode operations | 9 | 239.652356 | 239.132976-240.446979 | 237.108476-244.590152 |
| Paired added codec cost | 9 | **226.007874** | **225.697659-226.901215** | 224.024196-231.158109 |

The codec variant is 17.86x the direct-clone control, but the actionable number
is the paired addition: 0.226 us per request/response.

For orientation only, on the same short host but in different historical
collections, 0.226 us is 2.22% of the 10.176 us raw UDS p50 in
`experiments/hermit-experiments-migration_20260727/rpc-transport/`, and 0.339%
of the 66.60 us real LiteInst coordinator-hop slope in
`experiments/coordinator-rpc-guest-trim-realpath_20260804/`. These are not
causal A/B ratios: the dates, harnesses, and included mechanisms differ. They
only show that transport/scheduler/instrumentation dominates the end-to-end
path.

## Instrumentation versus sequentialization

The measured 226.008 ns is an IPC-codec component. It is neither patching
instrumentation cost nor Detcore sequentialization cost. The public LiteInst
real-path measurement cited above includes ptrace host, SIGTRAP, reactor, and
scheduler work together and cannot assign a separate sequentialization number.
For DBI, the CLI deliberately refuses the non-sequentialized configuration, so
an end-to-end sequentialization A/B is absent rather than silently inferred.
Sequentialization is also a definitional requirement for Hermit record/replay,
so disabling it would change semantics rather than isolate an optimization.

Consequently:

1. codec overhead is measured;
2. transport and real LiteInst hop numbers are context only;
3. sequentialization cost is **not measured** by this experiment;
4. no stdout-only or cross-workload number is presented as backend performance
   parity.

## Limitations

- One payload shape, one host, one selected CPU, and one collection.
- Allocation behavior is included; socket/framing and coordinator work are not.
- The control clones values rather than invoking an in-address-space
  `receive_rpc`, so it isolates codec overhead but not actual global-state
  method cost.
- Current public DBI and LiteInst routes do not pay the optional RPC codec, so
  this is a ceiling component for a future out-of-process/in-guest route, not a
  measured regression in today's CLI.

## Reproduction

From this experiment directory:

```sh
with-proxy cargo build --release --locked --manifest-path harness/Cargo.toml
systemd-run --user --scope --collect \
  -p MemoryMax=2G -p TasksMax=64 -p CPUQuota=100% \
  timeout 120s python3 \
  ../../experiments/single-core-box-affinity-mechanism_20260804/run-on-k-free-cores.py \
  1 -- harness/target/release/rpc-serde-bench
```

The committed lockfile and exact Git revisions reproduce the dependency graph.
Do not compare a rerun as a same-collection speedup unless the host, payload,
box, ordering, repetitions, and code revisions match.
