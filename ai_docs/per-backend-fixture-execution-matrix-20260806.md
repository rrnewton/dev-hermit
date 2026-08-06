# Per-backend fixture execution matrix: which backend-parity fixtures actually run

**Task:** `per-backend-fixture-execution-matrix` · `egress-probe2` (opus-5), 2026-08-06
**Tree:** hermit `f706d3dc3` (= main `4c70658e7` + one unrelated fixture commit of mine)
**Data:** `experiments/per-backend-fixture-execution-matrix_20260806/matrix.csv` — 2550 rows,
one per (fixture, mode, backend).

The RUNNING column was a boolean. This turns it into a matrix, because a fixture does not run
"in general" — it runs under specific backends, and a backend-targeted fixture that executes only
under the ptrace reference proves nothing about its target.

No denominator for "fixtures needed" is invented here. The matrix is the finding.

---

## 1. The whole matrix, in one table

85 fixtures × 5 modes × 6 backends = **2550 cells**.

| cell class | count | what it means |
| --- | ---: | --- |
| `DECLINED-IN-MANIFEST` | 1592 | backend named, explicitly disabled, reason recorded |
| `NOT-WIRED` | 850 | backend never mentioned for that fixture/mode at all |
| `GATED-OFF (ci=false)` | 107 | backend **enabled**, but the mode never runs in CI |
| **`CI-SELECTED`** | **1** | enabled **and** `ci = true` |

**One cell out of 2550 is selected to run in CI:** `backend-parity-c/personality-domain`,
mode `verify`, backend `ptrace`.

### Per backend, `verify` mode

`verify` is the only mode with any backend enabled anywhere, so it is the only column that can
produce a result at all.

| backend | CI-SELECTED | GATED-OFF | DECLINED | NOT-WIRED | availability measured on this host/build |
| --- | ---: | ---: | ---: | ---: | --- |
| ptrace | **1** | 84 | 0 | 0 | available |
| kvm | 0 | 9 | 76 | 0 | **hung, no verdict in 60 s** |
| dbi | 0 | 11 | 74 | 0 | `DBI support was not included in this build` |
| liteinst | 0 | 1 | 84 | 0 | `libreverie_liteinst.so` missing |
| sabre | 0 | 2 | 83 | 0 | `SaBRe support was not included in this build` |
| **e9patch** | 0 | 0 | 0 | **85** | `e9patch support was not included in this build` |

### Per mode

| mode | CI-SELECTED | GATED-OFF | DECLINED | NOT-WIRED |
| --- | ---: | ---: | ---: | ---: |
| verify | 1 | 107 | 317 | 85 |
| naked | 0 | 0 | 0 | 510 |
| replay | 0 | 0 | 425 | 85 |
| chaos | 0 | 0 | 425 | 85 |
| custom | 0 | 0 | 425 | 85 |

**2040 of the 2550 cells belong to the four non-`verify` modes, and not one of them has a single
backend enabled.** Those modes exist in the manifest as fully-populated structure — every fixture
has a `naked`, `replay`, `chaos` and `custom` entry — while enabling nothing.

## 2. The empty cells — the deliverable

### 2a. The 23 cells we most believe in, and none has ever been observed

Thirteen fixtures name a non-ptrace backend as *enabled*. These are the strongest available
statement of intent to cover a backend. Every one of the 23 (fixture, backend) cells is
simultaneously `ci = false` **and** targets a backend that cannot execute here:

| fixture | backends enabled |
| --- | --- |
| cpu-virtualization, dup-shared-offset, fd-duplication, getcpu-identity, getpriority-identity, lseek-positioning, pidfd-open-self, rlimit-identity, sched-getaffinity-identity | kvm, dbi |
| numa-node-identity, prctl-identity | dbi |
| pid-probe | liteinst, sabre |
| pthread-lifecycle | sabre |

Two independent reasons, and **fixing either one alone changes nothing** — flipping `ci = true`
still cannot run a backend that is not in the build, and building the backend still leaves the
cell gated off. That is the practical content of "distinguish gated-off from not-wired": here they
are stacked.

### 2b. e9patch is not a column — it is an absence

e9patch appears in **zero** of the 2550 cells: not enabled, not disabled, not declined with a
reason. Every other backend at least appears as a recorded "no". `hermit run --backend=e9patch` is
a real CLI choice, so a reader comparing the CLI to this manifest would reasonably conclude e9patch
is covered somewhere. It is not covered and it is not refused; it is simply missing, which is the
one state that leaves no trace for anyone to audit.

(Consistent with hermit's own definition: e9patch is preprocessing plus the ptrace runtime, not a
backend. That is an argument for *declining* it with that reason, not for omitting it.)

### 2c. Three fixtures on disk are in no manifest

`sigaction_state.c`, `sigaltstack_state.c`, `sigprocmask_state.c` exist under
`tests/backend-parity/fixtures/` and are referenced by no manifest entry, so nothing can ever
select them — on any backend, in any mode. They are invisible rather than gated.

## 3. What actually ran, as opposed to what the manifest intends

The manifest says what should happen; only a run says what did. All 88 fixtures (82 under
`tests/backend-parity/fixtures/` + the 6 the manifest sources from `tests/c/`) were compiled and
executed under `hermit run --strict --verify --base-env=minimal`, ptrace:

* **86 ran to completion**, 84 of them with a self-reported check count (`ok=N`).
* **2 produced no result: `aio_refusal` and `append_pwrite` both hit the 90 s timeout (rc=124).**
  A timeout is not a failure and it is certainly not a pass — it is a no-result, and neither
  fixture is marked `occasional` or otherwise flagged as slow.
* **16 emit no `ok=` count.** Not automatically a defect: several deliberately emit *values*
  (`eventfd counter=36 sem=5`, `CPUID-SUCCESS vendor=GenuineIntel`), which is the stronger shape
  for a parity comparison. But it does mean an exit-code-only oracle is all that stands behind
  them, so "it passed" carries no count to check.

The `verify` mode is the one place the matrix has an enabled backend, and it is exactly the mode
where the ptrace column can be filled in — so the numbers above are the ceiling of what this
manifest can currently demonstrate, on the one backend that runs.

## 4. Two corrections I made to my own measurements

Recorded because both produced confident, wrong intermediate numbers, and both were caught only by
looking at the actual output rather than the summary:

* **"9 fixtures fail to compile" was my error, not theirs.** I compiled with plain `gcc -O1`;
  `run_matrix.py` uses `-D_GNU_SOURCE` (and `-pthread` for some). With the real flags,
  **0 of 88 fail to compile**. A tooling difference between the sweep and the harness reads
  exactly like a product defect.
* **"6 manifest programs are missing from disk" was also wrong.** They live in `tests/c/`, not
  `tests/backend-parity/fixtures/`. I had compared against one directory and concluded the
  programs did not exist. They do, and they are swept above.

The `liteinst` verdict also depends on getting the environment right: with the wrong
`LD_LIBRARY_PATH` the binary fails on `libunwind-ptrace.so.0`, which reads as a broken build. The
runtime path must be the fbcode libunwind; the recorded failure above
(`libreverie_liteinst.so` missing) is from a corrected run and is a genuine build-artifact gap.

## 5. How to read this against the compat scorecard

Any scorecard row asserting backend coverage from this manifest is qualified by exactly one
observation: **one cell runs in CI, on ptrace.** Every non-ptrace claim sourced here rests on cells
that are gated off, declined, or absent — and, on this host, on backends that are not even built.
The fixtures are real and (as a separate mutation audit showed) able to fail. The gate cannot see
them.

## 6. Reproduce

```bash
cd hermit                       # f706d3dc3
python3 - <<'EOF'               # intent layer
import tomllib; d=tomllib.load(open('tests/e2e/manifests/backend-parity-c.toml','rb'))
EOF
# run layer: compile with the harness's flags, run under hermit, record rc + ok= count
gcc -O1 -D_GNU_SOURCE -pthread -o /tmp/f tests/backend-parity/fixtures/<name>.c
hermit run --strict --verify --base-env=minimal -- /tmp/f
```

Backend availability is probed with `hermit run --backend=<b> --base-env=minimal -- <hello>`;
the unavailable ones report `<backend> support was not included in this build`, which is a
build-feature gap, **not** a statement that the product lacks the backend.
