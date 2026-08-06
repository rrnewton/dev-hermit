# DBI red cells: root causes, not symptoms

**Task:** `dbi-close-remaining-cells` · **Date:** 2026-08-06 · Local, no egress, no validate run.

## The population

From the frozen anchor (`compat-envelope/fullcorpus-scorecard.csv`, hermit `82a8e853`),
restricted to the **179 ptrace-passing tests**: DBI has **43 red cells**. They bucket
cleanly by *recorded outcome*, and the buckets are not the same kind of problem:

| n | outcome / reason | what it means |
| --- | --- | --- |
| **21** | `pass` + stdout hash ≠ ptrace | **DBI ran fine and deterministically, but produced different output.** The genuine cross-backend divergence bucket. |
| 13 | `diverge` / `dbi-verify-fail-exit1` | DBI's own double-run verify failed — self-nondeterminism or a run failure |
| 7 | `timeout` / `dbi-verify-timeout-120s` | timed out |
| 1 | `diverge` / exit40 | — |
| 1 | `diverge` / exit101 | — |

The 21 are the interesting ones: no crash, no timeout, no self-nondeterminism — just a
different answer from ptrace. They cluster by *semantics*, which is what points at root
causes rather than 21 separate bugs:

- **pid/tid observers (5):** `dbi-pid-virtualization`, `proc-fd-link-aliases`, `vforkexec`,
  `wait-on-child`, `determinism-stress-c/pid-tid`
- **time observers (5):** `setitimer-determinism`, `sysinfo`, `sysinfo-uptime`,
  `clock-determinism`, `proc-uptime`
- **socket timestamps (3):** `socket-timestamp-{edge-cases,timespec,timeval}`
- **RNG/hash observers (4):** `random-sources`, `python-hash-determinism`, `python-random`,
  `openssl-passwd`
- **address observers (1):** `print-memaddrs`
- **other (3):** `uname`, `syscall-quick-wins`, `dbi-execveat-unsupported`

## Root cause A — tid allocation *stride* differs (not a host leak)

Ran `determinism-stress-c/pid-tid` live under both backends, `--strict`:

```
ptrace                          dbi
root   pid=3 ppid=1 tid=3       root   pid=3 ppid=1 tid=3      <- identical
thread[0] pid=3 tid=5           thread[0] pid=3 tid=4
thread[1] pid=3 tid=7           thread[1] pid=3 tid=5
thread[2] pid=3 tid=9           thread[2] pid=3 tid=6
thread[3] pid=3 tid=11          thread[3] pid=3 tid=7
child  pid=13 ppid=3 tid=13     child  pid=8 ppid=3 tid=8
```

**This corrects a hypothesis I was carrying.** I expected DBI to be leaking raw host TIDs
into guest-visible output. It is not — **both backends determinize**, and the root pid/ppid
are identical. What differs is the **allocation stride**: ptrace consumes **2** ids per
thread (5, 7, 9, 11), DBI consumes **1** (4, 5, 6, 7). The child then lands on 13 vs 8.

So this is a **determinization-policy divergence between two correct-looking
implementations**, not a determinization failure. Every test that prints a tid, or a pid
derived from the same counter, diverges — which covers the pid/tid cluster with one cause.

> Distinct from the earlier GAP-3 finding, and both are real: the *guest-visible* pid/tid
> **is** determinized under DBI (this measurement); the DETLOG **`dtid` log field** is the
> raw host TID (`experiments/dbi-strict-parity_20260806/`). Different surfaces, different
> defects — do not conflate them.

## Root cause B — DynamoRIO perturbs the guest's own address layout

`c-programs/print-memaddrs`, live, both backends:

| region | ptrace | dbi | delta |
| --- | --- | --- | --- |
| stack | `0x7fffffffb86c` | `0x7fffffffaf9c` | `-0x8d0` (sub-page) |
| static/heap ×4 | `0x4062b0`, `0x406320`, `0x406710`, `0x408e30` | `0x4072b0`, `0x407320`, `0x407710`, `0x409e30` | **`+0x1000` — exactly one page, uniformly** |
| mmap'd | `0x7ffff7ec2010` | `0x7ffdf75d1010` | **`-0x2008f1000`** |

The uniform one-page shift on static/heap and the large mmap displacement are DynamoRIO's
own footprint moving the guest's allocations. **This empirically confirms a prediction I
made and explicitly flagged as unmeasured** in the heap-domain analysis: *"separable ≠
bitwise-stable — DR's presence may perturb the application's own addresses, breaking 'same
address' even with a clean domain."* It is now measured.

**Consequence, and it is structural:** the owner's heap-parity requirement is *"same
address, same contents"*. Under DBI the addresses are **not** the same — off by exactly a
page for static/heap. So DBI heap parity is blocked by DR's memory footprint, independently
of every logging gap. This is the one DBI blocker that is **not** a plumbing fix.

## What this buys the burndown

**43 red cells → a small number of causes.** Cause A plausibly covers the 5 pid/tid cells
with a single change (align the tid stride). Cause B covers `print-memaddrs` and is the
structural blocker for heap parity. The time (5), socket-timestamp (3) and RNG/hash (4)
clusters were **not** tested here and are hypotheses, not findings — they are the obvious
next probes, and each is a ~5-minute run with the recipe below.

**Fix order:** align the tid allocation stride (5 cells, one cause, cheapest); then probe
the time and RNG clusters to see whether they too collapse to one cause each; treat cause B
as a scoping decision for DBI heap parity rather than a bug to fix.

## Reproduction

```bash
cd experiments/dbi-red-cells_20260806
B=../../worktrees/covnode/hermit/target/debug/hermit
for be in ptrace dbi; do
  LD_LIBRARY_PATH=../../ignored/haskell-drb/hostlibs \
    $B --backend $be run --strict -- ./pid_tid > pid_tid.$be.out
done
diff pid_tid.ptrace.out pid_tid.dbi.out
```

## Limitations

- **Nothing was fixed.** All causes are hermit/reverie product code and I have no worktree
  slot. Root-caused and filed, not closed.
- Two cells root-caused live (`pid-tid`, `print-memaddrs`); the other 19 divergent cells are
  **clustered by name and hypothesis**, not individually confirmed.
- The 43-cell population comes from the frozen 200-test anchor; the live corpus is 235
  tests, so the count is stale in denominator. The two live root-cause runs used
  `worktrees/covnode/hermit` @ `fc49593ac`.
- The 13 `exit1` and 7 `timeout` buckets were not investigated at all.
- Whether ptrace's stride-2 or DBI's stride-1 is "correct" is not established here — only
  that they differ. Aligning them requires deciding which is canonical.
