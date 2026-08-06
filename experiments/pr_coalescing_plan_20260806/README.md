# The coalescing plan — and a correction: none of the staged work can become a PR

**Task:** `coalesce-staged-work-into-topic-prs` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress.

*(The dispatch's crates.io/`--verify-strict` preamble does not apply — this is not a crates.io task, so
I did not run that build.)*

## Measured inventory — the premise is inverted

| repo | unpushed commits |
| --- | ---: |
| **parent `dev-hermit`** | **106** (527 files: experiments 279, ai_docs 120, ci-hub 92, scripts 19, compat-envelope 5) |
| hermit | **0** |
| reverie | **0** |
| liteinst2 | **0** |
| agent-utils | **0** (detached at the pin) |

**There are no unpushed product commits at all.** 100% of the staged work is in the *parent*, and per
`CLAUDE.md` the parent harness "works directly on shared `main`" — **it does not use PRs.**

So *"~193 artifacts must not become ~193 PRs"* is not the risk. They **cannot** become PRs. They need
**one push**, blocked only by egress. Hygiene checks out too: the only non-parent paths in the diff are
the `agent-utils` and `reverie` **gitlinks** — no product code was smuggled into the parent to dodge a
proper change.

The owner's directive — *fewer, stacked, coalesced per topic* — therefore applies to something else:
**the product fixes the diagnoses imply, none of which are written.** That is the plan below.

## Part A — the parent set (exists, needs a push, not a stack)

106 commits, dominated by `experiments:` (26), `ai_docs:` (14), `ai_docs+experiments:` (5), `ci-hub`
(6). One push to `dev-hermit` main when egress returns. No regrouping needed or possible — they are
already committed on shared main.

**One caveat worth flagging:** 106 commits landing at once is itself a reviewability problem. If the
owner wants a digest, the natural cut is by the same topics as Part B, produced as a *summary
document*, not as a re-commit.

## Part B — the product PR stack that needs writing (the actual deliverable)

Ordered so review order is sensible and dependencies come first. **None of these exist yet; each needs
a hermit/reverie slot.**

### Stack 1 — `detlog-correctness` (highest value; internally ordered)

| # | PR | why it is first / depends on |
| --- | --- | --- |
| 1.1 | **`DetInode` newtype** + single conversion at `add_inode`; fix `files.rs:969/1226/1256` | the proven host-inode leak; establishes the newtype pattern |
| 1.2 | **`DetTid`/`DetPid` split** + real `tid⇔dettid` mapping in `init_thread_state` (`lib.rs:1150`) | **same alias class as 1.1** — stack after so the pattern is already reviewed; fixes DBI's raw-host-TID in every DETLOG record |
| 1.3 | **DBI honours `--log-file`** (or the harness reads stderr, documented) | **blocks all DBI detlog comparison** — nothing downstream is measurable without it |
| 1.4 | **DBI `[heap]` emission** | depends on 1.3 to be verifiable at all |

### Stack 2 — `determinism-dimensions`

| # | PR | note |
| --- | --- | --- |
| 2.1 | rusage/`times`/`/proc/self/stat` CPU accounting derived from **virtual time**, continuous (#140) | `ru_maxrss` is the in-tree template |
| 2.2 | abnormal-termination fidelity: DBI signalled-death→`1`, DBI SIGILL/thread-exit hangs, SaBRe SIGFPE hang + wrong SIGILL | split per backend if review gets large |
| 2.3 | **ptrace `SIGTRAP` mistranslation** | small, but it is the *reference* backend — fix before ratcheting others against it |
| 2.4 | mmap layout policy unified across backends | largest; relative spacing and pointer-comparison order differ, so normalization cannot substitute |

### Stack 3 — `scorecard-integrity` (**parent-only — no slot, no PR needed**)

All in `compat-envelope/collect-envelope.rs`: `deterministic = None` unless `mode == verify`; record
`reverie_sha`; add `ref_output_hash`; add a flags/comparator column; fix the hardcoded `unavailable`
reason string. **These can land the moment the parent is pushed** — they are the cheapest real wins in
the whole set and are gated on nothing but egress.

### Stack 4 — `ci-tooling` (mixed ownership)

Widen `purge_zero_byte_objects` past `*.o` + ELF-magic (**hermit**); DAG steps invoke prebuilt test
binaries instead of `cargo` (**hermit**); scheduled-run liveness detector (**parent**); `cpu_timeout`
declarations + a pin-flip polarity guard (**hermit DAG + parent check**).

## Sequencing recommendation

1. **Push the parent** the moment egress returns — it unblocks Stack 3 entirely and publishes all the
   evidence the other stacks cite.
2. **Stack 1 first**, in its stated order — it is the only one where later items are *unmeasurable*
   until earlier ones land.
3. **2.3 before the rest of Stack 2** — a reference-backend bug distorts every comparison made against it.
4. Stacks 2 and 4 can proceed in parallel once slots exist.

## Limits

* This is a **plan**, as the task scopes it. **No PR was opened and no product code was written.**
* The "~193" figure in the task text does not match any measurement I can make; the real numbers are
  106 commits / 527 files, all parent. I did not try to reconcile where 193 came from.
* Stack membership is drawn from findings in `experiments/` committed this session; a fix I have not
  diagnosed will not appear here.
* Every Part-B item needs a slot. That, not analysis, is the binding constraint.
