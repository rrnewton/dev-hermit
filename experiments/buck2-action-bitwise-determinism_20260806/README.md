# buck2 action bitwise determinism — reviving the 2022 reproducible-builds internship

**Status: bootstrap / demo, not a study.** N=1 target. Everything below is reproducible from this
directory. No fbsource content is reproduced here — internal artefacts are referenced by identifier only.

## Question

Can buck2 build actions be executed twice and compared for **bitwise** determinism, can irreproducible
actions be identified across fbsource, and does running them under **Hermit** determinize them?

## Prior art — it exists, and the code is still in the tree

The owner's ask named an intern project. It is real, and far better documented than expected.

- **Sarah Clark**, unixname **`saclark`**, Summer 2022 SWE intern on **Hermetic Infra**, mentored by
  Ryan Newton. Project plan states the milestone as *"demonstrate reproducible buck2 builds via
  determinization of RE actions."*
- **Her code is still present in fbsource today** at `fbcode/hermetic_infra/reproducible_builds/`:
  `assess_determinism.py` (~40 KB), `hermit_rebuilder.py`, `determinism_test.sh`,
  `launch_standalone_re.sh`, `BUCK`, `README.md`, `diffoscope/`, `integration/`.
- **13 landed diffs**, all titled `[reproducible builds] …`. Retrievable identifiers, in rough order:
  `D36720107`, `D36913483`, `D37125544`, `D37177189`, `D37196710`, `D37228406`, `D37257556`,
  `D37314860`, `D37433595`, `D37481466`, `D37540401`, `D37633313`, `D37690020`.
- Headline result from the project: **~146 irreproducible actions out of ~75,000 unique actions across
  19 fbcode targets.**

**The two traps she hit, both of which any new effort will hit:**

1. **Action *result* digests are nondeterministic by construction** — they embed execution
   metadata/metrics. Comparing them gave "893 of 1000 targets non-reproducible"; comparing **output
   file digests** instead gave 146/75,000. Compare outputs, never result digests.
2. **RE action digests expire in roughly 1.5 days**, so recorded digests cannot be replayed later; they
   must be re-derived from a fresh action listing at the same commit.

**Tooling that exists today and did not in 2022** (so a rewrite should consume it, not re-implement it):
`re_nondet` (re-executes an action N times on RE, reports `distinct_results`); `replay
--compare-action-results` (RE team, `fbcode/remote_execution/rust/replay_tool`); and
**`buck2 log diff action-divergence`**, which is built into the buck2 binary and needs nothing built.
There is also a **Hive table of nondeterministic action counts across the fleet**, which is a ready-made
candidate list — this is the single biggest shortcut available and it did not exist for her.

Full identifier list (wiki URLs, Workplace permalinks, doc IDs, Hive/Scuba tables) is deliberately kept
out of this public repo. It is recorded in the coordinator handoff for this task.

## Method

Two things were tested. Both are cheap and both ran tonight.

### 1. Is a buck2 target bitwise reproducible across two independent local executions?

`harness/double-build.sh <target>`. One target, two executions, then
`buck2 log diff action-divergence --trace-id1 … --trace-id2 …`.

**The method note is the main finding here.** The obvious approach — two `--isolation-dir`s, which buck2
documents as forcing cache misses — is **unsound**. buck-out paths contain the isolation-dir name, so any
action that writes a buck-out path into its own output diverges *by construction*. Measured: comparing
`det-probe-A` against `det-probe-B` reported

```
fbsource//third-party/rust/vendor/derive_more-impl:_1 (write .../__derive_more_impl-link_dwo_paths.txt)
first: 1866b1ed    second: 3d6c5ae3
```

as the first divergent action. Inspecting both files, they differed **only** in the substring
`det-probe-A` vs `det-probe-B`. A false positive, and the same shape of error as trap 1 above.

The sound method is **one isolation dir with `buck2 clean` between runs**: output paths are then
byte-identical across runs and cannot themselves be the difference.

**Cache-hit guard.** A second build after a clean could still be served from cache, in which case
"no divergence" would prove nothing. So the executed-action count is asserted, not assumed —
`buck2 log what-ran --format json`, counting `reproducer.executor`.

### 2. Does Hermit determinize a nondeterministic build action?

Sarah's own `determinism_test.sh`, unmodified, run both ways. It tars a file, touches it to change its
mtime, tars it again, and diffs the two archives. It is the `determinism_fail` / `determinism_pass`
`buck_sh_test` pair from her `BUCK` file.

## Results

See `results.csv`. Target: `fbcode//common/rust/shed/hostcaps:hostcaps`.

| what | result |
| --- | --- |
| two-isolation-dir method | **1 divergent action — false positive**, explained entirely by the isolation-dir substring |
| same-isolation-dir + clean | **0 divergent actions** over **337 executed actions per run** |
| executor mix, both runs | `Local=337, Cache=0, RE=0` — nothing was a cache hit |
| `determinism_test.sh --no-hermit` | `Determinism not achieved` (expected baseline) |
| `determinism_test.sh <hermit>` | **`Determinism achieved`** — bitwise-identical tars despite a changed mtime |
| cost | ~52–59 s wall, ~1.1 GiB buck-out, ~316 MB maxrss per build |

**Sarah Clark's 2022 test still passes in 2026 against the current OSS Hermit**, four years on.

## Interpretation

- **The mechanism works end to end and is cheap.** Two clean local rebuilds of a small Rust target plus a
  divergence comparison is a ~2.5 minute round trip, and the comparison tool ships inside buck2.
- **`hostcaps` is bitwise reproducible** across 337 locally executed actions. That is a real negative with
  a stated denominator, not an absence of evidence.
- **N=1.** One small, deliberately-chosen Rust library says nothing about fbsource's surface area. Do not
  read this as a reproducibility rate.
- **Execution was 100% local.** The owner's question is about **RE** actions. Nothing here exercises RE.
- **Hermit determinizes the canonical mtime-nondeterminism case**, which is the payoff direction — but on
  a hand-written tar workload, not on a buck2 action that buck2 itself found irreproducible. The join
  between the two halves is exactly what remains unbuilt.

## What it would take to finish (honest scoping)

- **Local batch study (~2 h).** The correction that matters: `buck2 clean` wipes everything, so paying it
  per target is quadratic. Do **one** clean, then **one batched build over all N targets**, then compare
  pairwise. 20–50 small fbcode targets, two rounds. Still a demo — small N, cherry-picked targets.
- **Fleet-informed study (~4–6 h, and the genuinely novel one).** Query the Hive table of
  nondeterministic action counts for the top-N fbcode targets, then locally A/B-rebuild exactly those.
  This **tests the fleet telemetry against a controlled experiment**, and the two can disagree. A result
  of the form *"of the 30 targets the fleet flagged most on <date>, K reproduced their nondeterminism in a
  controlled two-round local rebuild"* is defensible and has a denominator. Risk: flagged targets skew
  large (cuda, ocaml, thrift), so sample deliberately and state how.
- **What the owner literally asked for — RE actions twice, then Hermit — is 1–2 weeks, not a night.**
  Three unbuilt pieces stack: porting `assess_determinism.py` off its 2022 interfaces and rebuilding
  `recli` + `replay` from source; getting RE-side execution genuinely exercised, which is a
  permissions/capacity question rather than a coding one (Sarah needed a colleague's pointer in 2022 and
  it plausibly needs a use-case grant now); and the Hermit leg, whose only prior art is a short stub that
  never ran.

**Authorization notes.** fbsource was not mutated. Nothing was submitted to RE at scale — all execution
was local (`--local-only --no-remote-cache`). Running this against RE, or at fleet scale, needs explicit
approval and is flagged rather than attempted.

## Reproduction

```bash
# 1. buck2 double-build + divergence, sound method
experiments/buck2-action-bitwise-determinism_20260806/harness/double-build.sh \
    fbcode//common/rust/shed/hostcaps:hostcaps

# 2. assert the second build really executed (else you are comparing cache hits)
cd /home/newton/fbsource
buck2 log what-ran --trace-id <trace> --format json \
  | python3 -c "import sys,json,collections;c=collections.Counter(json.loads(l).get('reproducer',{}).get('executor','?') for l in sys.stdin if l.strip());print(dict(c))"

# 3. Hermit determinizes the mtime case (Sarah Clark's own test, unmodified)
cd /home/newton/fbsource/fbcode
./hermetic_infra/reproducible_builds/determinism_test.sh --no-hermit          # Determinism not achieved
./hermetic_infra/reproducible_builds/determinism_test.sh /path/to/hermit      # Determinism achieved
```

Exact SHAs, versions, host and commands: `metadata.json`.

**If you get to the Hermit leg, do not use `--no-namespace`.** Hermit's default full-namespace mode does
persist writes outside `/tmp`; what it discards is `/tmp`. Use `hermit run --tmp=/tmp`, which keeps all
namespaces, gives deterministic PIDs, and leaves writes visible.

---

# Track B — fleet telemetry vs a controlled local rebuild

**Status: the comparison is designed and half-executed. The control arm is measured; the flagged arm is
NOT measured (see the retraction below -- an earlier revision misattributed that to buildability). The
target sentence cannot yet be stated, and is not stated below.**

## The fleet telemetry exists and is queryable

`infrastructure.buck2_nondeterministic_actions_user` — Hive, daily, 365-day retention, oncall
`build_infra`, partition `ds`. Definition: an action is flagged when it has more than one distinct set of
output digests for the same `action_digest`. Schema (read from the checked-in UPM dataset definition, not
guessed): `inferred_repository`, `action_digest`, `distinct_output_digests`, `target`, `category`,
`identifier`, `action_id`, `output_digests_size`, `build_uuids`, `num_runs`.

Queried via the `presto` CLI at `ds=2026-08-05`:

| measurement | value |
| --- | --- |
| flagged action rows, all repositories | **85,674** |
| distinct targets carrying at least one flagged action | **41,701** |
| max flagged actions on a single fbcode target | **542** |

**Two properties of this table change how it must be read, and neither is obvious from its name:**

1. **It covers user builds only.** The pipeline filters out Sandcastle/CI, service accounts, cancelled
   builds, and non-`v2` isolation dirs. It is interactive-developer-build telemetry, not whole-fleet.
2. **RE's action-cache "paranoid" mode pins an action to its first result**, which suppresses the
   observable divergence for the great majority of actions that execute remotely. The residual signal
   therefore concentrates in **locally executed** actions. This cuts directly against the naive reading of
   the owner's ask: re-running *RE* actions is where the signal is *least* visible, and a local A/B is
   where it is most visible.

Also relevant to interpretation: fbcode build-stamping makes nearly every binary nondeterministic by
design, so a raw flag count includes a large intentional component. 41,701 flagged targets is not 41,701
bugs.

## What the flagged population actually looks like

Top-30 fbcode targets by flagged-action count are dominated by three families:
`cuda_cxx_compile` / `cudafepp` (GPU kernel compilation), `hip_compile`, and `genrule` (ASIC firmware
images). Exactly one of the top 30 is `rustc`. Target paths are deliberately **not** reproduced in this
public repo; the ranked list is in the coordinator handoff.

## Control arm — measured

Sampling frame, stated: **all 74 `rust_library` targets under one coherent fbcode package tree**
(`common/rust/shed/...`), enumerated with `buck2 uquery kind('rust_library', …)`. These are *not*
fleet-flagged; they are the control.

Method: one isolation dir, `buck2 clean` between rounds, **one batched build over all 74 per round**
(per-target cleans are quadratic), `--local-only --no-remote-cache`.

| | |
| --- | --- |
| executed actions per round | **5,548** |
| executor mix | `Local=5548, Cache=0, RE=0` |
| divergent actions | **0** |

A real negative with a denominator: 74 targets, 5,548 locally executed actions per round, twice, zero
divergences.

## Flagged arm — NOT measured. Retraction of an earlier claim in this file.

**An earlier revision of this README stated that the flagged arm was "blocked by buildability" and that
"the flagged population skews to MTIA/ASIC-firmware/GPU-adjacent targets that do not build on a generic
devserver". That claim was WRONG and is retracted. The evidence did not support it.**

What actually happened: three separate flagged-target batches reported `BUILD FAILED`, and I inferred
buildability. When I finally read the root cause instead of the exit status, it was:

```
Buck2 daemon was killed by an OOM killer due to high memory pressure. Common causes are
large or numerous build or test targets or too many Buck2 daemons running simultaneously.
  0: h2 protocol error: error reading a body from connection
  2: stream closed because of a broken pipe
```

**That is my own harness defect, not a property of the targets.** Each `--isolation-dir` starts and keeps
its own buck2 daemon. Over the session I created seven (`det-probe-A`, `det-probe-B`, `det-same`,
`det-batch`, `det-flagged`, `det-fl2`, `det-one`) and never killed any, on a box shared with three other
agents running concurrent Rust builds. The daemons were OOM-killed, and buck2 surfaced that as
`BUILD FAILED` with a gRPC broken-pipe cause that reads nothing like a memory problem.

`BUILD FAILED` was a **proxy** for "these targets cannot build here". The observable that actually binds
the claim is the root cause line, and it says something entirely different.

**Consequences for the results:**

* The **empty intersection stands** — it is a pure query result and never touched a daemon.
* The **control arm stands** — its 5,548 executed actions per round with zero divergences completed before
  the daemon pileup, and its executor mix was verified.
* The **flagged arm is simply NOT MEASURED.** Not blocked, not negative — unmeasured. Whether fleet-flagged
  targets reproduce their nondeterminism in a controlled local rebuild remains an open question, and this
  experiment says nothing about it either way.
* Any downstream reader who took "buildability blocks the comparison" from the earlier revision should
  discard it. It was one inference away from the evidence and it was the wrong one.

**Harness fix required before retrying:** reuse ONE isolation dir for the whole session, and
`buck2 --isolation-dir <dir> kill` between phases. Never `buck2 killall` on a shared box — it would kill
other agents' daemons.

## What would finish Track B

1. Add a **buildability pre-filter** pass: single-target `buck2 build --local-only` over the top ~200
   flagged compile-only fbcode targets, keep the survivors. Expect heavy attrition.
2. Batched two-round A/B over the survivors, same corrected method, with the executed-action guard.
3. Only then state the sentence *"of the K flagged targets that build locally, J reproduced their
   nondeterminism in a controlled two-round rebuild."*
4. Any target that does reproduce is the **Hermit-leg candidate** — run its failing action under
   `hermit run --tmp=/tmp` and re-compare. That join remains unbuilt.

A cheaper alternative that avoids the buildability problem entirely: pick flagged targets by joining
against the *control* frame — i.e. query the table for flagged actions whose `target` is already known to
build locally — rather than by rank. That trades "the most-flagged targets" for "flagged targets we can
actually test", and the report must say which was done.

---

# Track B, flagged arm — measured on retry

The retraction above stands and is now doubly confirmed: **these targets build locally without difficulty.**
Round 1 completed in 3.5 minutes. The earlier `BUILD FAILED`s were resource exhaustion, twice — first my
seven concurrent daemons, then host memory pressure from four build lanes sharing the box (round 2 showed
**146 kill signals** across 13 third-party rustc/link actions). Dropping to `-j 4` made round 2 succeed.
Neither failure was ever about the targets.

## Sampling rule

From `infrastructure.buck2_nondeterministic_actions_user` at `ds=2026-08-05`, `inferred_repository='fbcode'`,
restricted to targets whose flagged categories are exactly `rustc` and whose paths avoid GPU/ASIC/torch
domains, the largest coherent cluster was 30 unittest targets in a single Rust-port crate tree. **Took the
first 8 by sorted target name** — an arbitrary but stated and reproducible cut, chosen for session budget.

Method: one isolation dir, `buck2 clean` between rounds, batched build per round, `--local-only
--no-remote-cache -j 4`, daemon killed on exit.

## Result

| | |
| --- | --- |
| flagged targets sampled | **8** |
| flagged actions on them | **8** — exactly one each, all `category=rustc`, `identifier=link` |
| executed actions per round | **3,287** |
| executor mix | `Local=3287, Cache=0, RE=0` |
| divergent actions | **0** |

The binding was checked rather than assumed: the flagged action class for these targets is the final
`rustc link` step, and those actions demonstrably executed in both rounds (the builds produced the test
binaries). So this is not "the flagged action never ran".

> **Of 8 fbcode targets the fleet flagged on 2026-08-05, 0 reproduced their nondeterminism in a controlled
> two-round local rebuild over 3,287 executed actions per round.**

## Interpretation — this is a statement about the telemetry, not about buck2

**A same-host A/B cannot reproduce cross-environment nondeterminism, by construction.** The table flags an
action when it observes more than one distinct output-digest set for the same `action_digest` — across
different developers, machines, working-directory paths, times of day, and toolchain states. My two rounds
are the same machine, the same commit, the same configuration, minutes apart. Any nondeterminism sourced
from hostname, absolute path, wall-clock time, environment, or toolchain drift is *held constant* by my
design and cannot appear.

So 0/8 is the expected result if these flags encode **cross-environment** nondeterminism, and would have
been a surprise only if they encoded **intra-host** nondeterminism (uninitialized memory, hash-map iteration
order, parallelism races). The experiment therefore does not say "the fleet is wrong". It says the fleet's
flags on this sample are **not same-host reproducible**, which narrows what they can be.

That distinction matters for the Hermit question, and it cuts against the naive framing:

* Hermit determinizes *within* a run — time, PIDs, scheduling, randomness. It is the right tool for
  intra-host nondeterminism.
* It does **not** by itself normalize hostname or absolute build paths across machines. If these flags are
  path/host-sourced, Hermit is not the fix; a path-normalizing or hermetic-root change is.

**No Hermit leg was run, because nothing reproduced locally for Hermit to fix.** Reporting a Hermit result
here would have required first manufacturing a divergence, which would prove nothing about these targets.

## Limitations

* N=8 of 30 in the cluster, one day of telemetry, one repository area, `rustc`/`link` actions only.
* Configuration was not pinned to the flagged rows' configuration; the flagged rows I inspected during the
  failed round carried `cfg:dev-…asan-ubsan-dev`, and my `--local-only` build resolved its own default.
  A stricter retry should pin `--target-platforms`/config to match the flagged row.
* Two rounds only. A nondeterminism source with low per-run probability needs more rounds; the 2022 project
  used repeated execution for this reason.
* The control arm and this flagged arm used different frames, so they are not a matched comparison; both
  returned zero, which is consistent but not a contrast.

## The sharper next experiment

Vary **one environment axis at a time** across the two rounds instead of holding everything constant —
build the same target from two different absolute paths, or as two different users, or with a shifted
clock — and see which axis makes the flagged action diverge. That directly identifies the nondeterminism
source, and *then* Hermit has a defined job: determinize the axis that turns out to matter. That is a much
better use of the next session than more same-host rounds.

---

# The path-axis discriminator — reconciling two results that appeared to conflict

Two findings sat in direct tension:

* **This experiment concluded** Hermit determinizes *within* a run but does **not** normalize absolute
  build paths across machines, so path-sourced nondeterminism is out of its reach.
* **The Debian track finished 13 of 13** — every package diverges natively across two build roots and is
  byte-identical under `hermit run --strict --no-rcb-time`. That design varies **exactly one axis: the
  absolute build root path.**

If Hermit cannot normalize the path axis, how did varying only the path get fixed 13 out of 13 times?

## The experiment

Two build roots differing **only** in absolute path (`/tmp/pathaxis/rootA/deep` vs
`/tmp/pathaxis/rootB-longer-name/deep` — different length as well as content, so padding effects show).
One payload, four cases, run from each root, native and under
`hermit run --tmp=/tmp --no-rcb-time`. Output hashed and compared. `harness/path-axis-probe.sh`.

| case | what the output depends on | native | under Hermit |
| --- | --- | --- | --- |
| `path` | the cwd, printed literally into the output | DIFFERS | **DIFFERS** |
| `time` | `date +%s%N` | DIFFERS | **IDENTICAL** |
| `rand` | 8 bytes of `/dev/urandom` | DIFFERS | **IDENTICAL** |
| `tarmt` | tar of a file with a changing mtime | DIFFERS | **IDENTICAL** |

The `path` row is the sharp one: under Hermit it produced the **same two hashes as the native run**
(`5e6f6e41…` and `6a0bf70a…`). Hermit passed the real working directory through untouched — it did not
merely fail to help, it is transparent on this axis.

## Both results are true. They are different mechanisms.

Varying the build root path can make a build nondeterministic in two distinct ways, and only one of them
is Hermit's problem:

1. **Path-embedded.** The path itself ends up in the output bytes — a `__link_dwo_paths.txt`, a debug
   `DW_AT_comp_dir`, an `__FILE__` string, an rpath. **Hermit does not fix this**, because nothing is
   nondeterministic from the guest's point of view: the guest asked where it was and got a truthful,
   reproducible answer that simply differs between roots.
2. **Path-triggered.** Changing the root perturbs something *else* — allocation addresses, `readdir`
   order, timing, hash seeds — which then leaks into the output through time, entropy, or iteration
   order. **Hermit fixes this**, because those are exactly the sources it virtualizes.

So the Debian 13/13 is mechanism 2, and this track's conclusion was about mechanism 1. The earlier wording
here was too broad: "Hermit does not normalize absolute build paths" is correct, but it does **not** imply
"Hermit cannot fix nondeterminism revealed by varying the path", which is what it appeared to claim.
Corrected above.

**This also retro-classifies my own earlier false positive.** The two-isolation-dir divergence
(`__derive_more_impl-link_dwo_paths.txt`, outputs differing only by `det-probe-A` vs `det-probe-B`) is a
textbook mechanism-1 case. It was a false positive for measuring *intrinsic* determinism, but it is a
genuine example of path-embedded build nondeterminism — and one Hermit would not fix.

## A falsifiable prediction for the Debian track

If this classification is right, then **none of those 13 packages embeds its build root path in the
compared output** — otherwise Hermit could not have made them byte-identical. That is checkable: grep the
native (un-Hermit'd) artifacts from the two roots for the root path string. If any package embeds it and
is still 13/13 green under Hermit, this two-mechanism model is wrong and I would want to know.

## What this means for the buck2 ask

The owner's framing was "find nondeterministic buck2 builds and see if Hermit determinizes them". The
discriminator says that question has to be asked per mechanism:

* buck2 actions whose outputs embed buck-out paths → **not** a Hermit target; wants a hermetic-root or
  path-normalization change.
* buck2 actions nondeterministic through time, entropy, or iteration order → **Hermit's job**, and Sarah
  Clark's 2022 `determinism_test.sh` (still passing, see above) is the existence proof.

Triaging candidates by mechanism *before* reaching for Hermit is the cheap step that was missing.
