# compat-envelope: automated cross-backend compatibility measurement

This directory is the **canonical compat-envelope scorecard system** for the
dev-hermit workspace. It measures, in machine-readable form, how each Hermit /
Reverie execution backend compares to the golden **ptrace** reference across the
e2e test corpus, and renders the owner's scorecard table from that data.

It replaces the retired free-text
[`ai_docs/deprecated/progress_report_template.md`](../ai_docs/deprecated/progress_report_template.md)
(fake-cell risk): every number here is produced by actually running a cell, and
a cell exists **only** for a B1.5+ backend running the canonical shared tool
(anti-fakery gate #152). No fake cells for incomplete backends.

All CSVs live **in this outer dev-hermit repo**, never inside the inner
`hermit/` or `reverie/` checkouts (those stay low-noise).

## The scorecard, in one picture

Rows are e2e manifest buckets plus a `TOTAL`. The leftmost column is the golden
**ptrace** integer count — tests passing `--strict` + replay — which is the B4
denominator. Every other backend column is `parity%, determinism%` as a fraction
of that ptrace denominator:

- **parity%** — bitwise-identical guest-observable result vs the ptrace reference.
- **determinism%** — the backend is self-deterministic (run1 == run2), whether or
  not it matches ptrace. By construction `determinism% >= parity%`.

A cell a backend never ran counts as `0` in both (honest 0/0, never blank-as-green).

## Components

| File | Role |
| --- | --- |
| `collect-envelope.rs` | **Hermit** collector. Drives `hermit/ci/test_harness.sh`, consumes its JSONL, and appends rows to `scorecard.csv`. Two modes (below). `--assert-green` turns it into the regression gate. |
| `collect-reverie-compat.rs` | **Reverie** collector (owner directive #1, run first). Runs the shared Reverie counter Tool through the ptrace and KVM launchers and records ptrace-vs-kvm parity into `reverie-scorecard.csv`. |
| `collect-e9patch-compat.rs` | **e9patch** collector. Runs the freestanding raw-syscall corpus through the ptrace backend both un-rewritten (golden) and e9tool-rewritten, and records preprocessing-invariance (golden-vs-e9 parity) with honest L1/L2 levels into `e9patch-scorecard.csv`. e9patch is NOT a Detcore backend (see below), so it lives in its own CSV like reverie. |
| `expansion-dag.rs` | Generates the **expansion-mode** safe-ci-dag-runner DAG: one boxed step per cell with per-cell wall-time + memory budgets, plus a dated evidence run dir. |
| `render-scorecard.rs` | Reads a scorecard CSV and renders the owner's table (`--json` / `--tsv` also). Shared by all collectors. |
| `scorecard.csv` | Hermit Detcore-envelope results (schema below). |
| `reverie-scorecard.csv` | Reverie B1.5 Guest/Tool-boundary results (same schema). |
| `e9patch-scorecard.csv` | e9patch preprocessing-invariance results over the ptrace backend (same schema). |

All `.rs` files are [`rust-script`](https://rust-script.org) executables
(`chmod +x`, run directly). They resolve their own directory via
`RUST_SCRIPT_BASE_PATH`, so default CSV paths land next to the script.

## CSV schema (shared contract)

```
run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,
test_id,test_mode,backend,cell_state,outcome,deterministic,parity,
output_hash,duration_ms,max_rss_kb,reason
```

- `run_mode` — `regression` | `expansion` (hermit) or `reverie`.
- `bucket` — e2e manifest bucket, or `reverie-examples`.
- `test_mode` — hermit: `verify` | `replay` | `chaos` | `naked` | `custom`;
  reverie: `counter`.
- `backend` — `ptrace` | `dbi` | `kvm` | `sabre` | `liteinst`.
- `cell_state` — `enabled` (in the regression envelope) | `disabled` (expansion
  candidate).
- `outcome` — `pass` | `diverge` | `fail` | `skip`.
- `deterministic` / `parity` — `1` | `0` | blank (unknown). `deterministic`
  records run1==run2 **independent of parity**.
- `output_hash` — the comparable observable (hermit: guest-output hash; reverie:
  the syscall total).
- `max_rss_kb` — filled by the expansion cgroup path; blank in the fast lanes.

The renderer keys logical cells on `(bucket, test_id, test_mode, backend)` and,
with `--all`, keeps the newest `run_id` per cell (last-writer-wins).

## Two modes (hermit envelope)

### Regression — every `validate` + CI run

Runs only the **known-green** (`enabled`) cells, asserts they stay green, and
writes the green-cells CSV as a side effect. This is fast and safe.

```bash
./collect-envelope.rs --mode regression --lane portable \
    --repo ../hermit --with-parity --assert-green
```

`--assert-green` exits non-zero and lists every enabled cell that no longer
passes, so it is the drop-in check for the outer-repo `validate` entry point and
CI. It leaves the CSV updated regardless, so the scorecard always reflects the
last run.

### Expansion — periodic, full superset, hard-bounded

Runs the **full superset** including currently-failing / `disabled` cells to
catch failing→passing flips (compat growth). Because broken cells infinite-loop
or OOM, every cell is boxed by the `safe-ci-dag-runner` (229) with a per-cell
timeout + cgroup memory cap. `expansion-dag.rs` generates that DAG:

```bash
./expansion-dag.rs --csv scorecard.csv --repo ../hermit --lane portable \
    --backends dbi,kvm,sabre,liteinst --headroom 1.5 --dag-out /tmp/expand.dag.json
# validate then run with 229's runner:
python3 -m safe_ci_dag_runner.cli ascii --dag /tmp/expand.dag.json
```

**Per-cell budget:** for each backend, the geo-mean wall-time and max-mem ratio
vs ptrace across the green envelope is computed; a red frontier cell's budget is
`ptrace_baseline(test) × backend_geomean × headroom` (default 1.5×) for both
time and memory. Fallback ratios are used only when a backend has no green
overlap yet.

**Evidence retention:** each run writes a dated dir under
`ignored/compat-envelope/<run-id>/` (gitignored) with one subdir per cell
containing INFO logs (the ptrace reference and the backend run), machine-readable
exec stats, and captured stdout/stderr. The last `--keep` (default 5) runs are
retained; older ones rotate out. This feeds the `debug/` framework.

## Reverie compat (run this first)

`collect-reverie-compat.rs` measures the Reverie **B1.5 Guest/Tool boundary**:
the same shared counter Tool run through both the ptrace launcher
(`counter1`/`counter2`) and the KVM launcher
(`reverie-kvm-counter1`/`reverie-kvm-counter2`). The KVM launchers need a
statically-linked guest ELF + `/dev/kvm`, so the default corpus is static
busybox applets.

```bash
# build the launchers once (in the reverie checkout):
#   cargo build -p reverie-examples \
#     --bin counter1 --bin counter2 \
#     --bin reverie-kvm-counter1 --bin reverie-kvm-counter2
./collect-reverie-compat.rs --repo ../hermit --csv reverie-scorecard.csv
./render-scorecard.rs --csv reverie-scorecard.csv --denominator counter \
    --backends kvm --all
```

Only tools that have both launchers become parity cells; a ptrace-only example
records its KVM cell as not-runnable (0/0), never faked.

**Current finding** (reverie `a4f33d69`, hermit `2f3689bd`, this host with
`/dev/kvm`): KVM is `0% parity, 100% determinism` — fully self-deterministic but
surfaces a **constant 4 fewer syscalls** to the shared Tool callback than ptrace
(`true` 12→8, `echo hi` 15→11, `pwd` 16→12). That is a real Guest-contract
interception-surface gap, not a determinism defect, and is exactly the honest
B1.5 signal the scorecard is meant to expose.

## e9patch compat (preprocessing over ptrace, NOT a backend)

`collect-e9patch-compat.rs` measures **preprocessing-invariance**, not a
cross-backend parity. `hermit/AGENTS.md` is explicit that e9patch is not a
Detcore backend — it is binary-rewriting preprocessing used *with* the ptrace
backend. So this collector runs the freestanding raw-syscall corpus
(`hermit/tests/backend-parity/e9patch_corpus/*.c`) through the ptrace backend in
two arms and asks: is the e9tool-rewritten ELF's output bitwise-identical (L2) to
the same guest run **without** rewriting (the golden reference)?

- The `ptrace` column is the golden, un-rewritten reference arm (the denominator).
- The `e9patch` column is the e9tool-rewritten variant arm; its `parity` = e9
  output == golden output, both under ptrace.

It lives in its own `e9patch-scorecard.csv` (like reverie), never as a column in
the backend `scorecard.csv`, because a literal `e9patch` token in a backend field
would misread as a Detcore backend and violate the #152 anti-fakery gate.

**Honest L1 vs L2.** `deterministic=1` means the arm reached **L2** (`hermit run
--strict --verify` printed "Determinism verified" and exited cleanly). An arm
that ran under `--strict` (**L1**) but whose `--verify` leg did not confirm a
bitwise repeat is recorded with `deterministic=0` and a `reason` that says whether
L2 was missed because the verify leg **wedged** (PMU contention — an environment
limitation, `outcome=l1`, not a regression) or genuinely **diverged**
(`outcome=diverge` — a real finding). L1 is never reported as L2.

Both the strict and verify legs are retried up to `--verify-retries` (default 3)
whenever they wedge or skid-panic under fleet PMU load. If even the strict leg
never clears on any retry, the arm is unmeasurable environment noise, not a
confirmed red: it is recorded `outcome=skip` with **blank** determinism (never
`0`), so an env wedge is never rendered as a failing cell. A real non-124 strict
exit is the only `outcome=fail`.

```bash
export HERMIT_E9TOOL=../worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=../worktrees/e9patch/reverie/third-party/e9patch/e9patch
./collect-e9patch-compat.rs --csv e9patch-scorecard.csv --assert-green
./render-scorecard.rs --csv e9patch-scorecard.csv --backends e9patch --latest
```

`--assert-green` treats only real defects (parity divergence or run failure) as
regressions; an environment verify-wedge (L1-only) is reported, not failed. The
e9 arm requires a hermit built `--features e9patch` plus `e9tool`/`e9patch` on
disk (defaults resolve to the `worktrees/e9patch` checkout).

## Rendering

```bash
./render-scorecard.rs --all                       # hermit, default denominator=verify
./render-scorecard.rs --csv reverie-scorecard.csv --denominator counter --backends kvm --all
./render-scorecard.rs --csv e9patch-scorecard.csv --backends e9patch --latest
./render-scorecard.rs --all --json                # machine-readable
./render-scorecard.rs --latest                    # only the newest run_id
```

## Table markers (never let a `0` be ambiguous)

- `X%?` — parity **never measured** for that bucket → UNKNOWN, not a confirmed 0.
- `X%~` — **partial** parity coverage (some denom cells measured, some not).
- `n/a` — backend **ran zero** denom cells here (binary absent / not
  manifest-enabled) → not measurable, **not** a confirmed fail.

A real red (ran + failed) is `0%, 0%` with no marker; the markers keep it
visually distinct from "not measured" and "not runnable", which is what the
phase-2 bar (*every red a CONFIRMED red*) requires. Machine-readable
`--json`/`--tsv` carry `parity_measured_count`/`ran_count` per cell.

## Parity measurement

`collect-envelope.rs --with-parity` runs the same guest under ptrace and the
backend and compares stdout SHA-256. `.sh` fixtures run via `--run`, `direct`
commands via `bash -c`, and **compiled `.c`/`.rs` fixtures are built the way the
harness builds them** (`build_compiled_fixture()`) into
`repo/target/compat-envelope-parity/<id>/` — NOT `/tmp`, which hermit isolates
and refuses to launch a guest from. Backend availability is probed once up front
(`backend_available()`); an absent backend binary is recorded `unavailable`
(blank determinism), never a fabricated red.

## Anti-fakery invariants

1. A cell exists **only** for a B1.5+ backend running the canonical shared tool.
   Pre-B1.5 work is "progress toward", not a cell.
2. Not-run / not-runnable cells are `n/a` or `0/0`, never blank-rendered-as-green.
3. `determinism%` never implies `parity%`; a deterministic backend that diverges
   from ptrace reads `0% parity, N% determinism`.
4. Every number is produced by an actual run recorded in the CSV with its SHAs.
5. An **unmeasured** parity (`?`) or **unavailable** backend (`n/a`) is never
   presented as a confirmed 0 — measured-and-failed is the only real red.
