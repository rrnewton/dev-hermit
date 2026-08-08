# The 0.3 fbsource rehearsal runbook cannot be written yet — four gates, and what each requires

**Task:** `release-0.3-fbsource-rehearsal-runbook` (P0, KEYSTONE) · hermit-w11
(`[impl agent, opus-5]`) · **2026-08-07** · read-only. **No source freeze, manifest
publication, import, version change, or fbsource mutation was performed**, per the
standing owner hold quoted below.

---

## 0. The premise

The task is to *"publish a repeatable runbook from the **successful** fbsource
import rehearsal."* **There is no successful rehearsal.** The rehearsal was
explicitly stopped by the owner before it produced a result, and its own closing
note states, verbatim:

> **No completed source pair or result is claimed.**

A runbook written now would not record a proven process. It would record an
attempt that was halted, and present it as repeatable.

---

## 1. Four gates, all currently unmet

| # | gate | live state | source |
| --- | --- | --- | --- |
| 1 | `release-0.3-fbsource-rehearsal-parity` — the declared blocker | **OPEN** (P0) | task graph |
| 2 | `certify-post-tightening-scorecard-postcard` — the hold's named resume condition | **IN_PROGRESS** (P0) | task graph |
| 3 | the frozen source pair | **INVALIDATED by its own terms** | §2 |
| 4 | `release-0.3-semver-cut` — must precede any 0.3 bump | **OPEN**; `hermit-cli` still `version = "0.2.0"` | `origin/main` |

The owner hold, quoted in full because it is the controlling instruction:

> OWNER HOLD 2026-08-07: newest standing release instruction supersedes this task
> description sentence allowing rehearsal before tightening certification. **Stop
> now**; preserve the read-only START note and **perform no source freeze,
> manifest publication, import, version, or fbsource mutation**. This task will be
> blocked behind `certify-post-tightening-scorecard-postcard` and **may resume
> only after certification**. No completed source pair or result is claimed.

---

## 2. The frozen pair is already invalid, by the freeze's own rule

The freeze states: *"Any main advance or pin mismatch invalidates pair."*

| | frozen at | now | |
| --- | --- | --- | --- |
| Hermit main | `0041130ccb0daa54ffe7dce2792c1f1495c57e58` | `294e89bfeeebeb0aa4f00bde4b0e1053350d6a5e` | **+11 commits** |
| Reverie main | `0ae0c01b5e4c9fbf85c97adc66c2740f280727df` | `038e993926e45514264d30367b70df9b6ac3b9b8` | **differs** |

Both sides moved. The pair is invalid on the freeze's own terms, independently of
the owner hold.

### This is structural, not a one-off

Hermit `main` commit rate, measured now:

| window | commits |
| --- | ---: |
| last 6 h | **11** |
| last 12 h | 16 |
| last 24 h | 20 |

`main` advanced **during this single session** (`723d19ad5` → `294e89bfe`). At
roughly two commits an hour, **any freeze is invalidated faster than a rehearsal
can be completed and documented.** A runbook whose first step is "freeze a pair"
will keep failing that step for reasons unrelated to the runbook's quality.

**This is the finding that matters for the keystone role**, because it is not
fixed by finishing the parity task or the certification. The RC procedure needs
one of:

- a **branch or tag** cut for the RC so the pair stops moving under it, rather
  than a freeze pinned to a moving `main`; or
- an explicit **re-validation-on-advance** step, with a stated cost per advance,
  so the invalidation is budgeted rather than treated as an error; or
- a **quiet window** on `main` agreed for the duration of the import.

Choosing among those is an owner decision and is recorded here as the question,
not answered.

---

## 3. What the halted attempt did establish — the runbook's raw material

Recorded so the next attempt does not rediscover it. **None of this is a result;
it is the state at the moment of the stop.**

- **Manifest v2** supersedes v1: sha256 `09aca0155e6408372aa781f46badf405e1d016a9775e10120c8d00998bbb7b77`,
  58 lines, observed 2026-08-07T03:37:21Z.
- **Pin verifier PASS** — 46 revision entries across 10 tracked Cargo metadata
  files; the old `dd3c178` pin absent.
- **Reverie** run `31142133929` green on both authoritative jobs.
- **Hermit** exact-head run `31144339446`: pin, shard coverage, selection,
  preflight, release backends, dbi-parity all PASS; **debug build FAILED** —
  `e2e.metadata` emitted inventory then exceeded 60 s; sabre/liteinst still
  running at snapshot. Therefore **`authoritative_green=false`** and **NOT RC**.
- **Import shape**: Reverie + common first, then Hermit, as two independently
  buildable diffs.
- **Transformation/exclusion decisions**: preserve the `f256a94da424` `mod.rs`
  mapping; exclude generated MSDK pointer updates.
- **Open discrepancy**: the **361-vs-362 Reverie file count** is unresolved.
- **Before RC**: rerun the Hermit heavy suite quiet; clear the exact-main
  metadata timeout to full green.
- **Prohibited throughout**: no version, tag, release, or tightening change.

---

## 4. The runbook's acceptance test, stated now so it is not negotiated later

The task requires *"another agent can follow the runbook in a clean workspace;
all commands verified."* That is unachievable today: the commands cannot be
verified because the import they describe is forbidden by the hold and was never
completed.

When the gates clear, the runbook is publishable only if it carries:

1. every command **executed**, with its observed exit status captured in its own
   statement — not transcribed from a plan;
2. the exact rehearsal diff IDs, SHAs, and build results **linked**, not summarised;
3. the **361-vs-362** count reconciled, with the correct number and why;
4. `authoritative_green=true` at the exact head, with the metadata timeout cleared;
5. the explicit list of **what changes for the final RC SHA** versus the rehearsal;
6. rollback/retry steps that were **exercised**, not merely written.

Item 6 deserves emphasis: an untested rollback is the same class of defect as an
untested guard — it reads correct and has never been shown to work.

---

## 5. Recommended disposition

Keep this task `in_progress` and **blocked**. It should not be closed — nothing
was delivered — and it should not be attempted again until gates 1, 2 and 4 clear
and gate 3 has an owner decision on how the pair stops moving.
