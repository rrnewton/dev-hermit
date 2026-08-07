# Prefix-parity depth round 4: pipeline, interpreter, and short application

Task: `prefix-parity-depth-round4-work-up-toward-demo5`

Measured 2026-08-07 UTC on Hermit
`590fcc9eeb0339c5cf23f72b84394a63333e88ff`, which remained freshly fetched
`rrnewton/hermit:main` after the runs.

## Tier and question

This is the **COMMIT-PREFIX-PARITY** tier only. It asks how many ordered
DetCore `COMMIT turn` records a backend keeps byte-identical to a ptrace
golden as the workload ladder moves beyond the coreutil and simple fork/exec
rungs.

It is not a strict/verify, L1, L2, full-INFO, heap, or stack assurance result.
No row in this experiment is an unqualified green.

For each guest:

- `Z` is the number of `COMMIT turn` records in the first ptrace golden.
- `Y` is the longest raw identical prefix against that golden.
- Only the logging prefix before `COMMIT turn` is removed. Paths, process IDs,
  values, addresses, and virtual time remain exact.
- Ptrace runs twice. A guest gets a denominator only if both commit streams,
  stdout, and stderr are identical.
- Candidate runs use the richer of the log-file and stderr COMMIT streams.
  This matters because DBI emits its records to stderr.

## Snapshot and commands

The exact clean detached Hermit source was built in release mode with:

```bash
CARGO_NET_OFFLINE=true cargo build --release --locked \
  -p hermit --features third-party-backends -p detcore-dbi -p detcore-sabre
CARGO_NET_OFFLINE=true HERMIT_INSTALL_FORCE_RESTAGE=prefix-round4-590fcc9e \
  cargo build --release --locked -p hermit-install
CARGO_NET_OFFLINE=true scripts/stage-liteinst-runtime.sh release \
  target/release/libreverie_liteinst.so \
  /tmp/prefix-round4-liteinst-runtime-build
```

Each runtime cell used its own `setsid` process group and a 240-second bound:

```text
setsid env TMPDIR=SHORT_DIR HERMIT_INSTALL_DIR=INSTALL_DIR \
  HERMIT_LITEINST_RUNTIME=LITEINST_DSO timeout 240 \
  hermit --log=info --log-file=LOG --backend BACKEND \
  run --base-env=minimal --tmp=/tmp -- GUEST ARGS...
```

No broad kill command was used. The three rungs were:

```text
/bin/sh -c "printf 'c\nb\na\n' | /usr/bin/sort | /usr/bin/sha256sum"
/usr/bin/python3 -c 'print(sum(range(100)))'
/usr/bin/tar -cf /dev/null /usr/include
```

## Results

The three golden checks were self-identical: 3/3 guests qualified, from 6/6
ptrace executions. Candidate coverage was 15/15 attempted executions. Three
e9patch rows were not engaged, one SaBRe row violated its execution-path
contract, one LiteInst row exited 1, and one KVM row timed out. Those typed
outcomes are not promoted to parity success.

| Guest | Backend | Tiered result | Emitted | rc | Execution/path observation |
| --- | --- | ---: | ---: | ---: | --- |
| shell pipeline | ptrace | 47/47 | 47 | 0 | golden; replicate identical; expected SHA stdout |
| shell pipeline | DBI | **0/47** | 305 | 0 | stdout matches; DBI active |
| shell pipeline | LiteInst | 2/47 | 37 | 1 | empty stdout; cleanup `ENOTSUPP` |
| shell pipeline | SaBRe | 2/47 | 42 | 0 | stdout matches; **path-invalid**, 66 trusted-shared-object sites |
| shell pipeline | KVM | 2/47 | 26 | 124 | timed out; empty stdout |
| shell pipeline | e9patch | NOT-ENGAGED (`Z=47`) | 47 | 0 | `candidate_sites=0`, `mapped_sites=0`; raw 47/47 withheld |
| Python sum | ptrace | 141/141 | 141 | 0 | golden; replicate identical; stdout `4950` |
| Python sum | DBI | **0/141** | 137 | 0 | stdout `4950`; DBI active |
| Python sum | LiteInst | 2/141 | 173 | 0 | stdout `4950`; activation verified |
| Python sum | SaBRe | 2/141 | 133 | 0 | stdout `4950`; path-valid |
| Python sum | KVM | 2/141 | 137 | 0 | stdout `4950` |
| Python sum | e9patch | NOT-ENGAGED (`Z=141`) | 141 | 0 | `candidate_sites=0`, `mapped_sites=0`; raw 38/141 withheld |
| tar `/usr/include` | ptrace | 174/174 | 174 | 0 | golden; replicate identical |
| tar `/usr/include` | DBI | **0/174** | 157 | 0 | DBI active |
| tar `/usr/include` | LiteInst | 2/174 | 211 | 0 | activation verified |
| tar `/usr/include` | SaBRe | 2/174 | 165 | 0 | path-valid |
| tar `/usr/include` | KVM | 2/174 | 173 | 0 | completed |
| tar `/usr/include` | e9patch | NOT-ENGAGED (`Z=174`) | 174 | 0 | `candidate_sites=0`, `mapped_sites=0`; raw 174/174 withheld |

The DBI zeroes are measured zeroes with known denominators, not missing runs.
Every DBI candidate emitted at least 137 comparable records and completed with
rc 0. Conversely, e9patch's apparently strong raw comparisons do not qualify:
its own banner proves that none of these dynamic guests exercised the rewrite
path.

## First diverging DetCore commit per qualifying pair

All DBI pairs first diverge at zero-based record index 0 (`Y=0`). The golden
uses deterministic process identity 3, while DBI renders a different raw host
identity in each run:

```text
golden:    COMMIT turn 0, dettid 3 using resources {ParentContinue { parent: DetPid(3), child: DetPid(3) }: W}, ...
pipeline:  COMMIT turn 0, dettid 2177057 using resources {ParentContinue { parent: DetPid(2177057), child: DetPid(2177057) }: W}, ...
Python:    COMMIT turn 0, dettid 2545486 using resources {ParentContinue { parent: DetPid(2545486), child: DetPid(2545486) }: W}, ...
tar:       COMMIT turn 0, dettid 2556876 using resources {ParentContinue { parent: DetPid(2556876), child: DetPid(2556876) }: W}, ...
```

All LiteInst, SaBRe, and KVM pairs first diverge at index 2 (`Y=2`). The exact
resource class at that commit identifies the next backend-specific blocker.

LiteInst commits its injected DSO path where ptrace commits the loader cache:

```text
golden:   COMMIT turn 2, dettid 3 using resources {Path("/etc/ld.so.cache"): R}, ...
LiteInst: COMMIT turn 2, dettid 3 using resources {Path("/home/newton/work/dev-hermit/scratch/p4/bin/libreverie_liteinst.so"): R}, ...
```

SaBRe reaches a different startup resource at the same commit:

```text
pipeline: Path("/dev/tty")
Python:   Path("/usr/lib/locale/locale-archive")
tar:      Path("/proc/filesystems")
```

The pipeline SaBRe number remains a raw depth measurement, but it is not an
eligible parity result: path evidence was `guest_rpc_observed=true`,
`ptrace_fallback_sites=0`, `trusted_shared_object_sites=66`. Python and tar
both had `true,0,0` and were path-valid.

KVM names the same loader-cache resource, but its virtual time differs:

```text
ptrace: COMMIT turn 2 ... Path("/etc/ld.so.cache") ... 1_767_225_600.001_332_xxxs
KVM:    COMMIT turn 2 ... Path("/etc/ld.so.cache") ... 1_767_225_600.001_298_250s
```

Ptrace self-pairs have no divergent commit. E9patch has no qualifying pair
because all three cells reported zero candidates and zero mapped sites. For
diagnosis only, its unbound Python stream first differs at index 38 on virtual
time for the same `site.cpython-39.pyc` path; the other two unbound streams are
raw full-prefix matches.

## Metric bracket

The Python/LiteInst cell proves that the metric awards full depth and detects
regression in both directions:

```text
ptrace self-control               141/141
measured LiteInst baseline          2/141
perturb candidate index 1           1/141
perturb candidate index 0           0/141
```

The last row is an explicit measured zero with denominator 141. Perturbations
append one token to the selected extracted record; they do not alter the
source run.

## Interpretation and limits

Increasing workload depth still does not increase any candidate numerator.
DBI remains blocked at the first commit by raw host process identity. LiteInst
remains blocked at commit 2 by its injected runtime path. KVM remains blocked
at commit 2 by virtual time. SaBRe reaches commit 2 but the resource depends on
workload startup, and the shell pipeline additionally exposes a path-contract
failure.

This is one host, one candidate run per guest/backend, and two ptrace runs per
guest. The tar denominator depends on the measured host's `/usr/include`
contents. Raw logs and binaries are intentionally not committed. Full details,
hashes, and every typed row are in `metadata.json` and `results.csv`.
