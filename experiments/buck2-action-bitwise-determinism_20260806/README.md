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
