#!/usr/bin/env python3
"""Render README.md for the PRE-TIGHTENING compat-envelope baseline.

This is a RENDERING step, not a measurement step. It reads two frozen inputs
that sit beside it — `scorecard.csv` (the measured rows) and `metadata.json`
(the provenance recorded by the sweep) — and writes `README.md`. It calls no
clock and no network, so re-running it on unchanged inputs produces a
byte-identical file:

    compat-envelope/pre-tightening-baseline-20260806/generate.py --check

`--check` regenerates into memory and diffs against the committed README.md,
exiting 1 on any drift.

The scorecard tables themselves come from the shared renderer
(`compat-envelope/render-scorecard.rs`) so this artifact cannot disagree with
the rest of the directory about how a percentage is computed.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import subprocess
import sys
from collections import Counter, defaultdict

HERE = pathlib.Path(__file__).resolve().parent
CSV_PATH = HERE / "scorecard.csv"
META_PATH = HERE / "metadata.json"
NORESULT_PATH = HERE / "no-results.csv"
README_PATH = HERE / "README.md"
RENDERER = HERE.parent / "render-scorecard.rs"


def run_renderer(*args: str) -> str:
    """Call the shared renderer; its output is quoted verbatim into the README."""
    proc = subprocess.run(
        [str(RENDERER), "--csv", str(CSV_PATH), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        sys.exit(f"render-scorecard.rs failed ({proc.returncode}):\n{proc.stderr}")
    # The renderer echoes the absolute CSV path it was handed. Rewrite it to the
    # repo-relative form so this file regenerates identically from any checkout.
    rel = "compat-envelope/pre-tightening-baseline-20260806/scorecard.csv"
    return proc.stdout.rstrip("\n").replace(str(CSV_PATH), rel)


def load_rows() -> list[dict[str, str]]:
    with CSV_PATH.open(newline="") as fh:
        return list(csv.DictReader(fh))


def per_backend_counts(rows: list[dict[str, str]]) -> dict[str, Counter]:
    out: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        out[r["backend"]][r["outcome"]] += 1
    return out


def md_table(header: list[str], body: list[list[str]], align: list[str]) -> str:
    sep = {"l": "---", "r": "---:", "c": ":---:"}
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join(sep[a] for a in align) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)


def build_readme() -> str:
    meta = json.loads(META_PATH.read_text())
    rows = load_rows()
    counts = per_backend_counts(rows)
    backends = meta["backends_measured"]
    denom = meta["denominators"]["ptrace_pass_denominator"]

    # Render the ABSENT backend as a column too: the renderer prints `n/a`
    # for a backend with zero rows, which is the honest marker for "not
    # measurable here" and is strictly better than omitting the column.
    cols = ",".join(meta["render_backend_columns"])
    ascii_table = run_renderer("--all", "--backends", cols)
    tsv_table = run_renderer("--all", "--backends", cols, "--tsv")

    # Per-backend raw execution counts, straight off the CSV.
    outcome_keys = ["pass", "diverge", "timeout", "fail", "skip"]
    exec_body = []
    for b in backends:
        c = counts[b]
        det1 = sum(1 for r in rows if r["backend"] == b and r["deterministic"] == "1")
        par1 = sum(1 for r in rows if r["backend"] == b and r["stdout_parity"] == "1")
        parblank = sum(1 for r in rows if r["backend"] == b and r["stdout_parity"] == "")
        exec_body.append([
            f"`{b}`",
            str(sum(c.values())),
            *[str(c.get(k, 0)) for k in outcome_keys],
            str(det1),
            str(par1),
            str(parblank),
        ])
    exec_table = md_table(
        ["backend", "rows", *outcome_keys, "det=1", "stdout_parity=1", "stdout_parity=blank"],
        exec_body,
        ["l"] + ["r"] * (len(outcome_keys) + 4),
    )

    # No-result inventory.
    with NORESULT_PATH.open(newline="") as fh:
        nr = list(csv.DictReader(fh))
    nr_table = md_table(
        ["class", "scope", "cells", "why it produced no result", "evidence"],
        [[f"`{r['class']}`", r["scope"], r["cells"], r["reason"], r["evidence"]] for r in nr],
        ["l", "l", "r", "l", "l"],
    )

    # The exact cells whose ptrace reference was unusable, derived from the CSV
    # rather than restated by hand. Identical set across every measured backend
    # (asserted below), so one list describes all of them.
    blank_sets = {
        b: sorted(r["test_id"] for r in rows
                  if r["backend"] == b and r["stdout_parity"] == "")
        for b in backends if b != "ptrace"
    }
    distinct = {tuple(v) for v in blank_sets.values()}
    if len(distinct) != 1:
        sys.exit("unusable-reference cell set differs between backends; the "
                 "single-list summary in section 7 would be wrong")
    unusable = sorted(distinct.pop())
    unusable_list = "\n".join(f"- `{c}`" for c in unusable)

    # Buckets whose ptrace denominator is zero: 0%,0% there is no information.
    empty_buckets = sorted({
        r["bucket"] for r in rows if r["backend"] == "ptrace"
    } - {
        r["bucket"] for r in rows
        if r["backend"] == "ptrace" and r["outcome"] == "pass"
    })

    ratchet = meta["ratchet"]
    ratchet_table = md_table(
        ["backend", "det", "of measurable cells", "collector floor", "floor reachable?"],
        [[f"`{b}`", str(v["det"]), str(v["of"]), str(v["floor"]),
          "no — floor exceeds the cell count" if v["floor"] > v["of"]
          else ("reachable, met" if v["det"] >= v["floor"] else "reachable, not met — unranked")]
         for b, v in ratchet["observed"].items()],
        ["l", "r", "r", "r", "l"],
    )

    prov = meta["provenance"]
    prov_table = md_table(
        ["what", "exact value"],
        [
            ["Hermit source", f"`{prov['hermit_sha']}` (`{prov['hermit_ref']}`)"],
            ["Hermit binary `--version` stamp", f"`{prov['hermit_version_stamp']}`"],
            ["Reverie checkout", f"`{prov['reverie_sha']}` (`{prov['reverie_ref']}`)"],
            ["Reverie the binary actually links", f"`{prov['reverie_cargo_locked_rev']}` (from `hermit/Cargo.lock`)"],
            ["Parent producer commit", f"`{prov['parent_producer_commit']}`"],
            ["Producer script", f"`{prov['producer_script']}` sha256 `{prov['producer_script_sha256']}`"],
            ["Renderer", f"`{prov['renderer']}` sha256 `{prov['renderer_sha256']}`"],
            ["Corpus (C)", f"`{prov['corpus_c']}` sha256 `{prov['corpus_c_sha256']}`"],
            ["Corpus (non-C)", f"`{prov['corpus_nonc']}` sha256 `{prov['corpus_nonc_sha256']}`"],
            ["Host", f"`{prov['host']}`, {prov['cores']} cores, kernel `{prov['kernel']}`"],
            ["Toolchain", f"{prov['cc']}; {prov['rustc']}"],
            ["Sweep window (UTC)", f"{prov['run_utc_start']} → {prov['run_utc_end']}"],
            ["`run_utc` stamped on every row", f"`{prov['run_utc_field']}`"],
        ],
        ["l", "l"],
    )

    load = meta["host_load"]
    contract = meta["comparison_contract"]

    return TEMPLATE.format(
        title_date=meta["baseline_date"],
        cores=prov["cores"],
        backend_cols=cols,
        prov_table=prov_table,
        ascii_table=ascii_table,
        tsv_table=tsv_table,
        exec_table=exec_table,
        nr_table=nr_table,
        denom=denom,
        nominal=meta["denominators"]["corpus_nominal_cells"],
        measurable=meta["denominators"]["corpus_measurable_cells"],
        rows_per_backend=meta["denominators"]["rows_per_backend"],
        total_rows=len(rows),
        backends_measured=", ".join(f"`{b}`" for b in backends),
        backends_absent=", ".join(f"`{b}`" for b in meta["backends_absent"]),
        det_argv=contract["det_argv_template"],
        parity_argv=contract["parity_reference_argv_template"],
        comparator=contract["verify_compare"],
        tmo_run=contract["timeout_run_s"],
        tmo_verify=contract["timeout_verify_s"],
        par=contract["parallelism"],
        unusable_count=len(unusable),
        unusable_list=unusable_list,
        empty_buckets=", ".join(f"`{b}`" for b in empty_buckets),
        ratchet_table=ratchet_table,
        ratchet_verdict=ratchet["verdict"],
        ratchet_reason=ratchet["reason"],
        load_min=load["one_min_min"],
        load_median=load["one_min_median"],
        load_max=load["one_min_max"],
        load_samples=load["samples"],
        reproduce=meta["reproduce_command"],
    )


TEMPLATE = """# PRE-TIGHTENING compat-envelope baseline — {title_date} (HISTORICAL)

> # ⚠️ PRE-TIGHTENING / HISTORICAL — THIS IS A *BEFORE* SNAPSHOT
>
> This file exists **only** to be the matched before-state for an upcoming
> **strictness tightening** of the compat-envelope comparison contract. It is a
> point-in-time measurement of the corpus as it behaved **under the OLD,
> LOOSER contract**, taken so that the after-state can be diffed against
> something measured hours — not days — earlier, on the same host, with the
> same corpus and the same backend matrix.
>
> **Do not quote these numbers as current compatibility status.** They will be
> superseded by the after-state run, and they are not expected to survive the
> tightening unchanged — a drop after tightening is the *point* of tightening,
> not a regression.
>
> **No cell below is a bitwise certification.** Every `deterministic=1` here was
> produced by the **{comparator}** comparator. See §4.

---

## 1. What this is, in one paragraph

A single-sweep, single-host, single-SHA measurement of the full compat-envelope
corpus across every Detcore backend that would run on this box, produced by the
tracked collector `compat-envelope/collect-fullcorpus.sh` and rendered by the
tracked renderer `compat-envelope/render-scorecard.rs`. Unlike the standing
`compat-envelope/SCORECARD-CURRENT.md` — whose Table 1 is a 2026-08-01 sweep at
Hermit `82a8e853` — every row here was measured in one window at one Hermit SHA,
so it has no mixed-provenance problem.

## 2. Provenance — bind every number to this table

{prov_table}

Sweep parallelism `--par {par}`, per-cell `timeout {tmo_run}s` on the run leg and
`timeout {tmo_verify}s` on the verify leg. `--no-assert` was passed: this is a
measurement, not the green-stays-green gate, so a ratchet-floor drop records a
number instead of aborting the sweep.

**Host load is part of the measurement, not a footnote.** The box is shared with
~18 other agents and a second, independent full-corpus sweep was running
concurrently. Sampled 1-minute load average over the sweep window:
min {load_min}, median {load_median}, max {load_max} across {load_samples} samples on
{cores} cores (see `loadavg.tsv`). Read `duration_ms` in the CSV as
contended wall time, never as a benchmark. The pass / determinism / parity
fields are what this artifact reports.

## 3. The matrix — corpus × backend

- **Corpus:** {nominal} nominal cells (`corpus/corpus-c.tsv` + `corpus/corpus-nonc.tsv`),
  of which **{measurable} were measurable** on this Hermit SHA. Every backend was
  run over the same {rows_per_backend} cells, so the columns are directly comparable.
- **Backends measured:** {backends_measured}.
- **Backends absent (zero rows, `n/a`, NOT a zero score):** {backends_absent}.
- **Rows in `scorecard.csv`:** {total_rows}.

**Denominator: {denom}.** As in the standing scorecard, a non-ptrace backend's
percentage is a fraction of the ptrace cells that themselves passed the golden
`--strict --verify` leg. Cells where ptrace did not pass are excluded from every
backend percentage — they are a ptrace gap, not a backend gap.

## 4. The comparison contract (the thing about to be tightened)

Per cell, per backend, exactly two Hermit invocations:

```
det     {det_argv}
parity  {parity_argv}
```

- **`deterministic`** = the `--verify` leg exited 0. `--verify` with no
  `--verify-strict` selects the **{comparator}** comparator, which normalizes
  before comparing. Every row in `scorecard.csv` carries this explicitly in the
  `verify_compare` column, so the tier travels with the value rather than being
  inferred from prose.
- **`stdout_parity`** = SHA-256 of the backend's piped guest stdout equals
  SHA-256 of the ptrace reference's. The reference is captured with plain
  `--strict` (never `--strict --verify`, which double-runs internally and emits
  no guest stdout to the parent).

**What that contract does NOT establish.** `{comparator}` normalization has been
measured to miss DETLOG-only, address-value, and path-string divergence while
still printing `Determinism verified` — see Limitation L1 of
`compat-envelope/SCORECARD-CURRENT.md`. A green cell here rules out stdout and
exit-status divergence and nothing more. INFO logs, stack detlogs, heap detlogs,
and TTY behaviour are outside the observable entirely. **This looseness is the
motivation for the tightening; these numbers are what the loose contract said.**

## 5. Rendered scorecard

`render-scorecard.rs --csv scorecard.csv --all --backends {backend_cols}`

```
{ascii_table}
```

Each backend cell is `stdout-parity%, determinism%`. The two are independent
signals: a backend can reproduce its own wrong answer (100% determinism, 0%
parity) or match ptrace once without being reproducible.

**Do not read every `0%, 0%` as a red.** The buckets {empty_buckets} have a
**zero ptrace denominator** in this sweep — `0/0` formats as `0%` but carries no
information. A genuine red is `0%, 0%` over a non-zero denominator (`liteinst` on
`chaos-c`, `determinism-stress` and `determinism-stress-c` is a genuine red).
Read the `X/Y` counts in the TSV projection below before calling any cell a
failure.

### Exact fractions, with the measured/ran counts beside every percentage

```
{tsv_table}
```

## 6. Execution counts — what actually ran

Raw outcome tallies straight off `scorecard.csv`, before any percentage is taken.
These are the counts the denominators are built from; a percentage without them
is unqualified.

{exec_table}

`stdout_parity=blank` means *unmeasured*, not failed: the ptrace reference for
that cell was itself unusable, so no comparison was possible.

## 7. No-results — recorded, never silently dropped

{nr_table}

### The {unusable_count} cells whose ptrace stdout reference was unusable

Derived from `scorecard.csv`, not restated by hand; the set is byte-identical
across `dbi`, `sabre`, `e9patch` and `liteinst`, which is what you would expect
if the cause is the shared reference rather than any one backend.

{unusable_list}

## 8. The collector's ratchet floors do NOT apply to this run

{ratchet_verdict}

{ratchet_reason}

{ratchet_table}

So the four `REGRESSION:` lines the collector printed compare an absolute count
taken over 205 cells against a floor calibrated over 235, and **none of them is
evidence that a backend got worse**:

- For `ptrace` and `e9patch` the floor is provably unsatisfiable — it exceeds
  the number of cells that exist to run.
- For `dbi` and `sabre` the floor is numerically reachable, but the shortfall
  cannot be separated from the 30 missing cells without re-calibrating: whatever
  share of those 30 the backend used to pass was counted into the floor and is
  now unavailable. Treat these two as **unranked against the floor**, not as
  passes and not as regressions.
- `liteinst` clears its floor, which is a lower bar than it looks for the same
  reason.

`--no-assert` was passed precisely so this would be recorded rather than abort
the sweep. Re-calibrating the floors is out of scope here (this task makes no
product fixes); it needs either PR rrnewton/hermit#1727 landed so the 30
`performance/*` cells build again, or floors expressed as rates.

## 9. Producing the matched AFTER-state

Change **only** the comparison contract. Everything else in §2 and §3 must be
held fixed, or the diff measures the wrong variable:

```
{reproduce}
```

Then render with the same renderer, and diff §5 and §6 against this file. If the
corpus, the Hermit SHA, the host, or the backend set differs, say so and mark the
affected rows unranked rather than reporting a delta.

## 10. Regenerating this file

```
compat-envelope/pre-tightening-baseline-20260806/generate.py           # rewrite README.md
compat-envelope/pre-tightening-baseline-20260806/generate.py --check   # assert zero drift
```

`generate.py` reads only `scorecard.csv`, `metadata.json`, and `no-results.csv`,
and calls no clock — so an unchanged input set regenerates byte-identically.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="regenerate in memory and fail on any drift from README.md")
    args = ap.parse_args()

    text = build_readme()

    if args.check:
        current = README_PATH.read_text() if README_PATH.exists() else ""
        if current != text:
            sys.stderr.write("DRIFT: README.md does not match a fresh render of its inputs\n")
            return 1
        print("OK: README.md matches a fresh render of scorecard.csv + metadata.json")
        return 0

    README_PATH.write_text(text)
    print(f"wrote {README_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
