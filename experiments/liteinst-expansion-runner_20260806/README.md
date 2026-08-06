# LiteInst expansion-runner: the 220 recorded cells do not reproduce, and the runner named in them never ran them

**Date:** 2026-08-06 · **Task:** `liteinst-expansion-runner-experiment` · **Agent:** hermit-oci2 (opus-5)
**Host:** devbig014.atn7.facebook.com · kernel 6.18.39-0_fbk0_hardened_0_ga43d5727b443 · **Local only, no egress**

## Question

`compat-envelope/scorecard.csv` carries **220 LiteInst rows — 136 recorded as `pass`** — all stamped
`run_mode=expansion`, `hermit_sha=464cbd9f9bb4`, `reverie_sha=aa6f1283aeee`, `dirty=false`.
LiteInst does not activate today: every bare `hermit run --backend liteinst` dies with
`tracee terminated before the required preload handshake completed (phase Waiting)`.

Agent `hermit-oci` established that this is **not a regression** — it reproduced the failure at the exact
recorded `(hermit, reverie)` pair — and left one lead untested before running out of context:

> all 220 rows carry `run_mode='expansion'`, i.e. they were produced by the **cgroup-boxed parallel runner**
> (`expansion-dag.rs`), NOT by a bare `hermit run` CLI. […] run liteinst THROUGH `expansion-dag.rs` on this
> host and see whether it activates. If it does, the 'blocker' is that a bare CLI invocation is not a
> supported way to run liteinst.

**This experiment runs that.** The standard it must hold: *a cell that cannot be reproduced from its recorded
provenance is not a verified cell.*

## Verdict

**The lead is refuted, and not narrowly.** The expansion runner is not merely *an* unsuccessful context —
it **could not have produced those rows at all**, and LiteInst fails in every context tried.

| # | arm | runner | cells | LiteInst result |
|---|---|---|---|---|
| A | bare CLI (predecessor's shape) | `hermit run --backend liteinst --strict --verify` | 1 | FAIL — phase `Waiting` |
| A′ | recorded producer's exact flag shape | `--strict --base-env=minimal --max-timeslice=disabled --tmp=/tmp` | 1 | FAIL — phase `Waiting` |
| A″ | **the recorded producer itself, verbatim** | `experiments/liteinst_backend_parity_20260801/run.py` | 20 attempted | **FAIL at its own smoke gate — 0 of 20 rows regenerate** |
| B | harness, single cell | `ci/test_harness.sh run … --backend liteinst` | 1 | REFUSED by manifest filter |
| C | **the literal expansion runner, at the scorecard SHA** | `expansion-dag.rs` → `safe-ci-dag-runner` (cgroup boxing ACTIVE) | 8 | **0 pass, 8 fail — hermit is never invoked** |
| D | the expansion runner, at a SHA where LiteInst *is* enabled | same, `hermit 52d56e5ceb38` | 28 | **0/28 LiteInst pass; 28/28 ptrace pass in the identical context** |

Arm D is the clean discriminator: **same runner, same cgroup box, same cell script, same host, same 28
programs — ptrace 28/28 PASS, LiteInst 0/28 PASS.** The failure is the LiteInst backend, not the invocation
context, not the box, not the corpus.

## Findings

### 1. `run_mode='expansion'` is a hardcoded string literal, not a producer binding

The 220 rows were written by two bespoke **parent-repo** runners, neither of which touches
`expansion-dag.rs`, `safe-ci-dag-runner`, or even `ci/test_harness.sh`:

| rows | run_id | producer | where `run_mode` comes from |
|---|---|---|---|
| 200 | `liteinst-fullcorpus-1785621912` | `experiments/liteinst_fullcorpus_scorecard_20260801/run.py` | `"run_mode": "expansion"` — literal, `run.py:320` |
| 20 | `liteinst-spst-1785620995` | `experiments/liteinst_backend_parity_20260801/run.py` | `"run_mode": "expansion"` — literal, `run.py:170` |

Both build an argv and `subprocess` hermit directly (`run.py:183`, and via
`tests/backend-parity/run_matrix.py::hermit_command` respectively). This is a **Proxy Binding** failure in
the exact sense the policy names: the field keys on a label with no observable link to the producer it
claims. Any consumer that reads `run_mode` to infer how a row was made is reading a constant.

### 2. The bucket `backend-parity-spst` has never existed in Hermit

`git log --all -S'backend-parity-spst'` over `rrnewton/hermit` returns **nothing**; there is no
`tests/e2e/manifests/backend-parity-spst.toml` at `464cbd9f`, at current main, or at any reachable commit.
It is a synthetic bucket name minted parent-side (`run.py:31 BUCKET = "backend-parity-spst"`) over
`tests/backend-parity/matrix.tsv`. So **20 of the 136 recorded passes name a bucket the recorded
`hermit_sha` cannot produce** — unreproducible by construction, before any run.

### 3. The expansion runner had no route to a LiteInst cell at `464cbd9f`

Machine-checked over `tests/e2e/manifests/*.toml` at that exact SHA:

| | count |
|---|---|
| `backends_enabled` blocks | 1010 |
| …of which contain `liteinst` | **0** |
| explicit `liteinst = "<reason>"` disabled lines | 808 |

`expansion-dag.rs`'s generated `run-expansion-cell.sh` execs `test_harness.sh run … --include-manual`, and
`--probe-disabled` **did not exist** at `464cbd9f` (`test_harness.sh: unknown option: --probe-disabled`).
There is therefore no path from the expansion runner to a disabled LiteInst cell at that SHA.

Arm C confirms this by execution rather than inference — 8 cells, cgroup boxing ACTIVE, **9.4 s, 0 passed,
8 failed**, every one `exit=2` / `test_harness.sh: filters selected no required test cells`. Hermit is never
launched. Evidence: `scratch/liteinst-expansion-464cbd9f/evidence/armC-464cbd9f/<cell>/{stdout,stderr,stats.json}`.

### 4. The recorded producer, re-run verbatim at its own recorded provenance, regenerates nothing

```
experiments/liteinst_backend_parity_20260801/run.py \
  --repo <clean clone @464cbd9f9bb4> \
  --hermit <release hermit that self-reports g464cbd9f9bb4> --output-dir …
=> LiteInst smoke failed: … tracee terminated before the required preload handshake completed (phase Waiting)
```

Same `hermit_sha`, same `reverie_sha` (`aa6f1283aeee`, re-derived independently from the pinned tree's
`Cargo.lock`), same host, same kernel, clean tree. It dies at its own smoke gate (`run.py:130`), before row 1.

**By the #268 standard, all 220 LiteInst rows in `compat-envelope/scorecard.csv` are UNVERIFIED cells.**
Not stale, not regressed — unreproducible from what was recorded.

**Credit and one correction, in the predecessor's favour.** They wrote that the scorecard records
`hermit_sha`/`reverie_sha` "but NOT the host". True of the CSV, but both producers' own
`results/metadata.json` **do** record `host=devbig014.atn7.facebook.com` and the kernel. The gap is a CSV
**schema** gap, not a missing record — and it matters, because that is **this host**. "Host-specific to some
other box" is therefore refuted, not merely untested. What the CSV additionally fails to record, and what
would have shortened this by a day: the **producer** (`run_mode` is a constant), and
`LD_LIBRARY_PATH=/home/newton/.local/hermit-deps/lu/usr/lib64`, without which **every hermit build on this
box fails to start** (`libunwind-x86_64.so.8 => not found`) — an unrecorded precondition of every row.

### 5. Enumeration hole: the expansion runner cannot see `backends_enabled` + `ci = false` cells

`enumerate_cells` (`compat-envelope/expansion-dag.rs:449`) builds its superset as **`plan` ∪ `audit-gaps`**.
At `52d56e5c`, `test_harness.sh plan --lane portable` returns 74 rows (**3** LiteInst) and `audit-gaps`
returns 5781 (1184 LiteInst, all *disabled* gaps). A cell that is **enabled for LiteInst but `ci = false`**
is in neither set. Measured: **28** cells enable LiteInst at `52d56e5c`; `expansion-dag.rs` emits **3**.
**25 of 28 enabled LiteInst cells are invisible to the expansion sweep** — silently, with no "skipped" count.
Arm D therefore synthesised the missing 25 into the DAG, reusing the runner's own generated helper script and
cmd template verbatim so the execution context stays the runner's.

### 6. Every expansion cell runs under an undeclared 10-second CPU budget

`expansion-dag.rs` computes a careful per-cell wall `timeout` (ptrace baseline × backend geomean × headroom;
20–1517 s across the 1087-step DAG) and a `hard_mem_max_bytes` — but **never emits `cpu_timeout`**
(`grep -c cpu_timeout compat-envelope/expansion-dag.rs` = **0**; **1087 of 1087** steps omit it).
`safe-ci-dag-runner` then applies `DEFAULT_SMALL_CPU_TIMEOUT = 10`
(`agent-utils/py/safe_ci_dag_runner/model.py:27`). Measured directly: with stock budgets **28/28** arm-D cells
were reaped `CPU-TIMEOUT >10s cpu`; setting `cpu_timeout=180` dropped that to **0/28**, same verdicts.
So the expansion sweep's computed wall budgets are inoperative above 10 s of CPU, and a reaped cell is
reported as a plain `✗ FAIL` — indistinguishable from a product failure.

### 7. The LiteInst activation failure is invariant across builds

| hermit build | tree | result |
|---|---|---|
| `g464cbd9f9bb4` | clean — the scorecard pin | phase `Waiting` |
| `g52d56e5ceb38` | clean, 2026-08-05 | phase `Waiting` |
| `g0f891e432a75-dirty` | the root-cause doc's build | phase `Waiting` |

Not a code regression, not staleness, not pin drift. Root-causing is **not** this experiment's job and was not
attempted — `ai_docs/liteinst-preload-handshake-root-cause-narrowed_20260806.md` already narrows it to five
clauses in `validate_liteinst_handshake` (`reverie-ptrace/src/task.rs:2106-2146`). The only thing this adds is
the cross-build invariance row above.

## Results

`evidence/` — the durable copies: `armC-dag.json` and `armD-dag-enabled28.json` (the exact DAGs run),
`run-expansion-cell.sh` (`expansion-dag.rs`'s own generated cell runner, byte-for-byte as executed),
`armD-safe-ci-dag-runner.txt` (the full arm-D runner log), `armC-per-cell.tsv` (arm-C exit codes + stderr).
The full per-cell trees (stdout/stderr/`results.jsonl`/`info.log`, plus the `ptrace-ref` subtrees) stay in
gitignored `scratch/…/evidence/` and are named per row in `results.csv`'s `evidence_path`.

`results.csv` — 68 rows, one per measured cell, each carrying its own provenance
(`hermit_sha`, `hermit_version_string`, `hermit_binary_sha256`, `liteinst_runtime_sha256`, `reverie_sha`,
`source_tree_dirty`, host, kernel, runner, outcome, reason, `evidence_path`).

| arm | backend | outcome | n |
|---|---|---|---|
| A-bare-cli | liteinst | FAIL | 1 |
| A-prime-recorded-flags | liteinst | FAIL | 1 |
| A-second-recorded-producer | liteinst | FAIL | 1 |
| B-harness-single-cell | liteinst | REFUSED | 1 |
| C-expansion-runner-at-scorecard-sha | liteinst | REFUSED (hermit never invoked) | 8 |
| D-expansion-runner-liteinst-enabled-cells | liteinst | FAIL | 28 |
| D-expansion-runner-liteinst-enabled-cells | ptrace | **PASS** | 28 |

Binary provenance is **self-attested**, not inferred from a file mtime: `hermit --version` prints its own git
SHA, and the `464cbd9f9bb4` binary's reverie pin was re-derived independently from the pinned tree's
`Cargo.lock` and matches the scorecard's `aa6f1283aeee`.

## Interpretation — what should change

1. **Do not treat any of the 220 LiteInst rows as evidence.** They are unverified cells. Any LiteInst
   percentage derived from `compat-envelope/scorecard.csv` (58.5 % determinism / 53.5 % parity full-corpus,
   91.4 % / 83.6 % SP/ST) currently rests on cells that do not reproduce.
2. **Bind `run_mode` to its producer** — record the producer's path plus its content SHA, not a literal.
   A field that a producer sets to a constant is a cache with no authority behind it.
3. **Add `host`, `kernel`, and the required runtime env to the scorecard CSV schema.** The producers already
   capture host/kernel in `metadata.json`; the CSV drops them, and nobody records the `LD_LIBRARY_PATH`
   precondition without which no row is reproducible.
4. **Reject synthetic buckets, or bind them.** `backend-parity-spst` must be traceable to something at the
   recorded `hermit_sha`, or be marked as parent-side and excluded from any "Hermit at SHA X" claim.
5. **Fix the enumeration hole (finding 5)** — `plan ∪ audit-gaps` misses *enabled but not CI-required*. That
   is 25 of 28 LiteInst-reachable cells today, dropped with no count.
6. **Emit `cpu_timeout` from `expansion-dag.rs` (finding 6)**, scaled like the wall budget, and make a reaped
   cell report as `CPU-TIMEOUT`, not as a bare `FAIL`.

Findings 5 and 6 are backend-independent: they affect **every** expansion sweep, not just LiteInst.

## Reproduction

```sh
cd ~/work/dev-hermit
export LD_LIBRARY_PATH=/home/newton/.local/hermit-deps/lu/usr/lib64   # else: libunwind-x86_64.so.8 not found

# --- pinned, read-only source trees (no worktree, no slot HEAD moved, no build) ---
git clone --shared --no-checkout worktrees/oci/hermit scratch/liteinst-expansion-464cbd9f/hermit
git -C scratch/liteinst-expansion-464cbd9f/hermit checkout 464cbd9f9bb43d5505c914783819e1d349630283
git clone --shared --no-checkout worktrees/dbi/hermit scratch/liteinst-expansion-52d56e5c/hermit
git -C scratch/liteinst-expansion-52d56e5c/hermit checkout 52d56e5ceb386d24ec809edbfdb6920e8484271e

H464=worktrees/oci/hermit/target/release/hermit   # must print: hermit 0.2.0 (2026-08-06, g464cbd9f9bb4)
H52=worktrees/dbi/hermit/target/release/hermit    # must print: hermit 0.2.0 (2026-08-05, g52d56e5ceb38)
$H464 --version; $H52 --version                   # provenance gate - stop if these differ

# --- A / A' : bare CLI, then the recorded producer's exact flag shape ---
$H464 run --backend liteinst --strict --verify -- /bin/true
$H464 run --backend liteinst --strict --base-env=minimal --max-timeslice=disabled --tmp=/tmp -- /bin/true

# --- A" : the recorded producer, verbatim ---
experiments/liteinst_backend_parity_20260801/run.py \
  --repo scratch/liteinst-expansion-464cbd9f/hermit --hermit $H464 --output-dir /tmp/spst-repro

# --- C : the literal expansion runner at the scorecard SHA ---
compat-envelope/expansion-dag.rs --repo scratch/liteinst-expansion-464cbd9f/hermit \
  --backends liteinst --buckets backend-parity-c \
  --evidence-root scratch/liteinst-expansion-464cbd9f/evidence --run-id armC-464cbd9f
HERMIT_BIN=$PWD/$H464 \
E2E_RESULT_ROOT=$PWD/scratch/liteinst-expansion-464cbd9f/e2e-armC \
E2E_BUILD_ROOT=$PWD/scratch/liteinst-expansion-464cbd9f/e2e-armC/build \
  agent-utils/common/bin/safe-ci-dag-runner run --keep-going --max-mem 16G \
  --dag scratch/liteinst-expansion-464cbd9f/evidence/armC-464cbd9f/dag.json

# --- D : expansion runner over the 28 LiteInst-enabled cells at 52d56e5c ---
# generate, then (a) patch in the 25 cells of finding 5 and (b) set cpu_timeout of finding 6:
compat-envelope/expansion-dag.rs --repo scratch/liteinst-expansion-52d56e5c/hermit --backends liteinst \
  --buckets applications,backend-parity-c,bin-c,c-programs,debugger-c,language-runtimes,system-utils \
  --evidence-root scratch/liteinst-expansion-52d56e5c/evidence --run-id armD-52d56e5c
experiments/liteinst-expansion-runner_20260806/build-enabled-dag.py \
  --repo scratch/liteinst-expansion-52d56e5c/hermit \
  --run-dir scratch/liteinst-expansion-52d56e5c/evidence/armD-52d56e5c
  # prints: 28 LiteInst-enabled cells (3 emitted by expansion-dag.rs, 25 invisible to it)
HERMIT_BIN=$PWD/$H52 \
E2E_RESULT_ROOT=$PWD/scratch/liteinst-expansion-52d56e5c/e2e \
E2E_BUILD_ROOT=$PWD/scratch/liteinst-expansion-52d56e5c/e2e/build E2E_RUN_ID=armD3-oci2 \
  agent-utils/common/bin/safe-ci-dag-runner run -k -j 28 \
  --dag scratch/liteinst-expansion-52d56e5c/evidence/armD-52d56e5c/dag-enabled28.json
```

`build-enabled-dag.py` (in this directory) enumerates the LiteInst-`backends_enabled` cells from the pinned
manifests and emits `dag-enabled28.json`, reusing `expansion-dag.rs`'s own generated `run-expansion-cell.sh`
and cmd template.

**Known gotcha:** `safe-ci-dag-runner run --max-mem 32G` on the 28-step DAG spun in state `R` for >4 minutes
without starting a step; `-j 28` starts immediately. Not investigated — recorded so the next person does not
read it as a hang in the cells.

## Limitations

One host (devbig014 — but see finding 4: it is the *recorded* host, which is what makes the non-reproduction
load-bearing). One iteration per cell — defensible only because the split is 0 %/100 % and deterministic, not
marginal; a future run reporting a *partial* LiteInst rate must repeat. Arm D covers the 28 LiteInst-enabled
cells, not the 1087-cell superset. Full list in `metadata.json → limitations`.
