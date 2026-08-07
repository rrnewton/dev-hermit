#!/usr/bin/env rust-script
//! Render the Hermit cross-backend **compatibility-envelope scorecard** from the
//! machine-readable CSV that `collect-envelope.rs` writes as a side effect of
//! every regression / expansion run.
//!
//! The scorecard is the owner's exact format:
//!
//!   * one row per e2e manifest **bucket**, plus a `TOTAL` row;
//!   * the leftmost data column is **ptrace** as an *integer count* — the number
//!     of tests in that bucket that pass the golden strict+replay determinism
//!     check (`verify` mode). This is the B4 denominator;
//!   * the canonical Hermit backend columns are
//!     `"stdout-parity%, determinism%"` where
//!       - **stdout-parity%** = fraction of the ptrace denominator whose piped
//!                              stdout SHA-256 matches the ptrace reference;
//!       - **determinism%**  = fraction of the ptrace denominator that is itself
//!                             deterministic under that backend (run1 == run2).
//!     stdout-parity% is an upper bound on full cross-backend parity: it does not
//!     compare INFO logs, stack detlogs, or heap detlogs. TTY behavior is also
//!     outside this scorecard.
//!     Determinism and stdout parity are independent signals; neither implies
//!     the other. A cell the backend never ran is NOT counted as 0: `ran_count`
//!     and `<parity>_measured_count` travel with every cell, an unmeasured
//!     observable renders `?` (or `~` for partial) and a backend that could not
//!     run renders `n/a` — none of which is a confirmed zero. A small envelope
//!     therefore reads as a small MEASURED population, not as a low percentage
//!     over an imagined one.
//!     Correspondingly, an EMPTY DENOMINATOR is refused rather than rendered:
//!     if no ptrace row passes the requested `--denominator` mode there are no
//!     cells at all, so the tool prints `NO DATA:` with the modes and backends
//!     the run does contain and exits 3 (distinct from 2 = usage, 0 = rendered).
//!     Without that, a dbi/strict-only run rendered a confident `TOTAL 0`.
//!     Reverie counter CSVs select `--observable tool-count` instead and are
//!     labeled `tool-count-parity%`; the two observables are never conflated.
//!
//! The machine-readable projection (`--json` / `--tsv`) is printed underneath /
//! instead of the human table, so downstream tooling never scrapes the ASCII.
//!
//! Usage:
//!   compat-envelope/render-scorecard.rs --csv PATH [--run-id ID|--latest]
//!                                       [--denominator MODE] [--json|--tsv]
//!                                       [--backends b1,b2,...]
//!                                       [--observable stdout|tool-count]
//!
//!   --csv PATH        Scorecard CSV. Required so the population is explicit;
//!                     use fullcorpus-scorecard.csv for the full corpus.
//!   --run-id ID       Render only rows from this run_id.
//!   --latest          Render only the most recent run_id (default when neither
//!                     --run-id nor --all is given).
//!   --all             Aggregate across every run in the CSV (last-writer-wins
//!                     per (run_mode,lane,bucket,test_id,test_mode,backend)).
//!   --denominator M   Which passing ptrace test_mode defines the denominator
//!                     (default: verify).
//!   --backends LIST   Comma-separated backend columns, in order
//!                     (default: dbi,kvm,sabre,liteinst — whichever appear).
//!   --observable O    Observable compared by the stdout-parity CSV column
//!                     (default: stdout; use tool-count for Reverie counters).
//!   --json | --tsv    Machine-readable output instead of the table.
//!
//! ```cargo
//! [dependencies]
//! serde_json = "1"
//! ```

use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs;
use std::path::PathBuf;
use std::process::exit;

const USAGE: &str = r#"Usage: compat-envelope/render-scorecard.rs --csv PATH [OPTIONS]

Render a cross-backend compatibility-envelope scorecard from an explicit CSV.

Options:
  --csv PATH        Scorecard CSV (required; population must be explicit).
  --run-id ID       Render only rows from this run_id.
  --latest          Render only the most recent run_id (default).
  --all             Aggregate across every run (last-writer-wins per cell).
  --denominator M   Passing ptrace test_mode defining the count (def: verify).
  --backends LIST   Comma-separated backend columns (def: dbi,kvm,sabre,liteinst).
  --observable O    stdout (default) or tool-count; labels parity honestly.
  --json | --tsv    Machine-readable output instead of the table.
  -h, --help        Show this help.
"#;

fn die(msg: &str) -> ! {
    eprintln!("render-scorecard: {msg}\n\n{USAGE}");
    exit(2);
}

/// One CSV row, only the fields the scorecard needs.
#[derive(Clone, Debug)]
struct Cell {
    /// File-append order (0-based). The collector only appends, so a higher seq
    /// is a strictly later write. "Newest" is defined by seq, NOT by run_id
    /// string order — run_ids may carry non-numeric prefixes (e.g.
    /// `canonical-<ts>`) that would sort above later numeric timestamps and
    /// silently mask a newer run.
    seq: usize,
    run_id: String,
    bucket: String,
    test_id: String,
    test_mode: String, // verify | replay | chaos | custom | naked
    backend: String,   // ptrace | dbi | kvm | sabre | liteinst | native | e9patch
    outcome: String,   // pass | fail | error | timeout | oom | skip | not-exercised | unavailable
    deterministic: Option<bool>,
    /// Stdout-only parity. Read from `stdout_parity`, or from the legacy `parity`
    /// column in scorecards written before the rename (see [`PARITY_COLUMNS`]).
    stdout_parity: Option<bool>,
    /// Whether the guest itself exercised anything hermit virtualizes (parity_exercised).
    parity_exercised: Option<bool>,
    /// How much work THIS backend uniquely performed (e9patch mapped_sites,
    /// sabre patched_sites, dbi branches, ptrace turns). None = unknown,
    /// distinct from 0 = vacuous. §319 counts must travel with cell.
    backend_engaged: Option<i64>,
}

/// Accepted names for the stdout-parity column, in preference order.
///
/// The column was originally called `parity`, which claimed more than it measured: it
/// only ever held piped-guest-stdout SHA-256 equality, never the four-signal standard.
/// The rendered label was corrected first; this is the raw column catching up. The old
/// name stays readable because published scorecards still carry it, and silently
/// rendering nothing for them would be a worse failure than the misnomer.
const PARITY_COLUMNS: &[&str] = &["stdout_parity", "parity"];

/// The canonical header this renderer and `collect-envelope.rs` agree on.
///
/// The stdout-parity column is deliberately absent: it is resolved through
/// [`PARITY_COLUMNS`] so either spelling is accepted.
///
/// Modern collectors append `stdout_parity, parity_exercised, backend_engaged,
/// native_output_hash, output_hash, ref_output_hash, verify_compare, run_flags`
/// alongside the core columns (§319: the count must travel with the cell).
/// Older CSVs lacking those columns remain readable: optional columns are
/// looked up fallibly.
const HEADER: &[&str] = &[
    "run_id",
    "run_utc",
    "hermit_sha",
    "reverie_sha",
    "dirty",
    "run_mode",
    "lane",
    "bucket",
    "test_id",
    "test_mode",
    "backend",
    "cell_state",
    "outcome",
    "deterministic",
    "output_hash",
    "duration_ms",
    "max_rss_kb",
    "reason",
];

fn parse_bool(s: &str) -> Option<bool> {
    match s.trim() {
        "1" | "true" | "TRUE" | "True" => Some(true),
        "0" | "false" | "FALSE" | "False" => Some(false),
        _ => None,
    }
}

/// Split the whole CSV text into RECORDS, not lines.
///
/// WHY RECORD-ORIENTED. RFC-4180 lets a quoted field contain a literal newline,
/// so ONE record can span several physical lines. The previous reader iterated
/// `text.lines()` and split each line independently, which cannot represent
/// that: it saw a multiline record as two short fragments, warned, skipped both,
/// and still exited 0 -- silently dropping a row and changing every derived
/// count. That is the shape that produced the DBI `file_metadata` incident (one
/// record split across physical lines 542-544).
///
/// Measured 2026-08-07 on the live file: 619 physical lines, 619 records, all 23
/// fields -- but 109 of 618 data rows already carry commas inside quoted fields,
/// so a naive splitter mis-reads one row in six today.
///
/// FAIL-CLOSED: an unterminated quote is an error, never a best-effort parse.
fn parse_csv_records(text: &str) -> Result<Vec<Vec<String>>, String> {
    let mut records = Vec::new();
    let mut field = String::new();
    let mut record: Vec<String> = Vec::new();
    let mut in_quotes = false;
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        if in_quotes {
            if c == '"' {
                if chars.peek() == Some(&'"') {
                    field.push('"');
                    chars.next();
                } else {
                    in_quotes = false;
                }
            } else {
                // Includes '\n': a newline inside quotes belongs to the field.
                field.push(c);
            }
        } else if c == '"' {
            in_quotes = true;
        } else if c == ',' {
            record.push(std::mem::take(&mut field));
        } else if c == '\n' {
            record.push(std::mem::take(&mut field));
            records.push(std::mem::take(&mut record));
        } else if c != '\r' {
            field.push(c);
        }
    }
    if in_quotes {
        return Err("unterminated quoted field (unbalanced quote) at end of input".into());
    }
    if !field.is_empty() || !record.is_empty() {
        record.push(field);
        records.push(record);
    }
    records.retain(|r| !(r.len() == 1 && r[0].trim().is_empty()));
    Ok(records)
}

/// Minimal RFC-4180-ish CSV split: handles double-quoted fields with commas and
/// escaped `""`. Good enough for our own writer's output.
#[allow(dead_code)]
fn split_csv_line(line: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut field = String::new();
    let mut in_quotes = false;
    let mut chars = line.chars().peekable();
    while let Some(c) = chars.next() {
        if in_quotes {
            if c == '"' {
                if chars.peek() == Some(&'"') {
                    field.push('"');
                    chars.next();
                } else {
                    in_quotes = false;
                }
            } else {
                field.push(c);
            }
        } else if c == '"' {
            in_quotes = true;
        } else if c == ',' {
            out.push(std::mem::take(&mut field));
        } else {
            field.push(c);
        }
    }
    out.push(field);
    out
}

fn main() {
    let mut csv: Option<PathBuf> = None;
    let mut run_id: Option<String> = None;
    let mut latest = false;
    let mut all = false;
    let mut denom_mode = "verify".to_string();
    let mut backends_arg: Option<String> = None;
    let mut observable = "stdout".to_string();
    let mut fmt = "table";

    let mut it = env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "-h" | "--help" => {
                println!("{USAGE}");
                exit(0);
            }
            "--csv" => csv = Some(PathBuf::from(it.next().unwrap_or_else(|| die("--csv needs a path")))),
            "--run-id" => run_id = Some(it.next().unwrap_or_else(|| die("--run-id needs a value"))),
            "--latest" => latest = true,
            "--all" => all = true,
            "--denominator" => denom_mode = it.next().unwrap_or_else(|| die("--denominator needs a mode")),
            "--backends" => backends_arg = Some(it.next().unwrap_or_else(|| die("--backends needs a list"))),
            "--observable" => observable = it.next().unwrap_or_else(|| die("--observable needs a value")),
            "--json" => fmt = "json",
            "--tsv" => fmt = "tsv",
            other => die(&format!("unknown argument {other}")),
        }
    }

    let csv_path = csv.unwrap_or_else(|| {
        die("--csv is required: choose `compat-envelope/fullcorpus-scorecard.csv` for the full corpus or `compat-envelope/scorecard.csv` for the CI/regression subset")
    });
    let (parity_label, parity_key, parity_meaning, full_parity_not_measured) =
        match observable.as_str() {
            "stdout" => (
                "stdout-parity",
                "stdout_parity",
                "piped guest stdout SHA-256 equality with ptrace; upper bound on four-signal cross-backend parity",
                vec!["INFO log", "stack detlog", "heap detlog"],
            ),
            "tool-count" => (
                "tool-count-parity",
                "tool_count_parity",
                "shared Tool callback-count equality with ptrace; not cross-backend execution parity",
                vec!["stdout", "INFO log", "stack detlog", "heap detlog"],
            ),
            _ => die("--observable must be `stdout` or `tool-count`"),
        };
    let denominator_meaning = if denom_mode == "verify" {
        "tests passing golden ptrace strict+replay (verify)".to_string()
    } else {
        format!("ptrace rows passing test_mode `{denom_mode}`")
    };
    let text = fs::read_to_string(&csv_path)
        .unwrap_or_else(|e| die(&format!("cannot read {}: {e}", csv_path.display())));

    let records = parse_csv_records(&text)
        .unwrap_or_else(|e| die(&format!("malformed CSV {}: {e}", csv_path.display())));
    let mut records = records.into_iter();
    let header = records.next().unwrap_or_else(|| die("empty CSV (no header)"));
    let idx = |name: &str| -> usize {
        header
            .iter()
            .position(|h| h == name)
            .unwrap_or_else(|| die(&format!("CSV missing required column `{name}`")))
    };
    // Validate the header carries every field we rely on.
    for h in HEADER {
        let _ = idx(h);
    }
    // Resolve stdout-parity under either spelling, preferring the honest one.
    let i_par = PARITY_COLUMNS
        .iter()
        .find_map(|name| header.iter().position(|h| h == name))
        .unwrap_or_else(|| {
            die(&format!(
                "CSV missing the stdout-parity column (looked for {})",
                PARITY_COLUMNS.join(" then ")
            ))
        });
    let (i_run, i_bucket, i_tid, i_tmode, i_backend, i_outcome, i_det) = (
        idx("run_id"),
        idx("bucket"),
        idx("test_id"),
        idx("test_mode"),
        idx("backend"),
        idx("outcome"),
        idx("deterministic"),
    );

    let mut cells: Vec<Cell> = Vec::new();
    for (n, f) in records.enumerate() {
        let get = |i: usize| f.get(i).cloned().unwrap_or_default();
        // FAIL CLOSED. This used to warn and `continue`, which changed every
        // derived count while still exiting 0 -- a producer defect could silently
        // move the numbers this renderer exists to report.
        if f.len() != header.len() {
            die(&format!(
                "row {} has {} fields, expected {}; refusing to render a partial scorecard",
                n + 2, f.len(), header.len()));
        }
        // Optional engagement columns (new collectors) — missing = unknown, not zero.
        let i_par_exercised = header.iter().position(|h| h == "parity_exercised");
        let i_engaged = header.iter().position(|h| h == "backend_engaged");
        let i_engaged_alt = header.iter().position(|h| h == "mapped_sites");
        let eng_raw = if let Some(ix) = i_engaged {
            f.get(ix).cloned().unwrap_or_default()
        } else if let Some(ix) = i_engaged_alt {
            f.get(ix).cloned().unwrap_or_default()
        } else {
            String::new()
        };
        let backend_engaged = eng_raw.parse::<i64>().ok();
        let parity_exercised = i_par_exercised.and_then(|ix| parse_bool(f.get(ix).cloned().unwrap_or_default().as_str()));

        cells.push(Cell {
            seq: n,
            run_id: get(i_run),
            bucket: get(i_bucket),
            test_id: get(i_tid),
            test_mode: get(i_tmode),
            backend: get(i_backend),
            outcome: get(i_outcome),
            deterministic: parse_bool(&get(i_det)),
            stdout_parity: parse_bool(&get(i_par)),
            parity_exercised,
            backend_engaged,
        });
    }
    if cells.is_empty() {
        die("CSV has a header but no data rows");
    }

    // Select the run scope.
    let scope_run: Option<String> = if let Some(r) = run_id {
        Some(r)
    } else if all {
        None
    } else {
        // default / --latest: the run_id of the LAST-APPENDED row (highest seq),
        // not the lexicographic max — run_ids can carry non-numeric prefixes
        // (e.g. `canonical-<ts>`) that would sort above a newer numeric run.
        let _ = latest; // --latest is the default; the flag is accepted for clarity
        cells.iter().max_by_key(|c| c.seq).map(|c| c.run_id.clone())
    };
    if let Some(r) = &scope_run {
        cells.retain(|c| &c.run_id == r);
    } else {
        // --all: last-writer-wins per logical cell key across runs.
        let mut newest: BTreeMap<(String, String, String, String), Cell> = BTreeMap::new();
        for c in cells.drain(..) {
            let key = (c.bucket.clone(), c.test_id.clone(), c.test_mode.clone(), c.backend.clone());
            newest
                .entry(key)
                .and_modify(|e| {
                    // last-writer-wins by file-append order, not run_id string.
                    if c.seq > e.seq {
                        *e = c.clone();
                    }
                })
                .or_insert(c);
        }
        cells = newest.into_values().collect();
    }

    // Restrict the determinism denominator to the requested mode.
    let denom_cells: Vec<&Cell> = cells.iter().filter(|c| c.test_mode == denom_mode).collect();

    // Discover buckets and backend columns actually present.
    let buckets: BTreeSet<String> = denom_cells.iter().map(|c| c.bucket.clone()).collect();
    let present_backends: BTreeSet<String> =
        denom_cells.iter().map(|c| c.backend.clone()).collect();

    // Per AGENTS.md e9patch is NOT a Detcore backend, but e9patch-scorecard.csv
    // DOES legitimately contain an `e9patch` column (its own preprocessing
    // measurement). Include it when present so the file remains self-rendering;
    // main scorecard.csv never has e9patch rows anyway.
    let default_order = ["dbi", "kvm", "sabre", "liteinst", "e9patch"];
    let backend_cols: Vec<String> = if let Some(list) = backends_arg {
        list.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect()
    } else {
        default_order
            .iter()
            .filter(|b| present_backends.contains(**b))
            .map(|s| s.to_string())
            .collect()
    };

    // ptrace denominator set per bucket = distinct test_ids where ptrace passed
    // the denominator mode.
    let mut ptrace_pass: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for c in &denom_cells {
        if c.backend == "ptrace" && c.outcome == "pass" {
            ptrace_pass.entry(c.bucket.clone()).or_default().insert(c.test_id.clone());
        }
    }

    // For each backend, index (bucket,test_id) -> (deterministic, parity, pass).
    // A backend "passes" a cell if outcome==pass. parity requires parity==true;
    // determinism requires deterministic==true OR (verify pass, since hermit's
    // internal double-run already proved run1==run2 for a passing verify cell).
    struct BCell {
        det: bool,
        par: bool,
        /// Whether parity was actually measured (CSV field non-blank). A blank
        /// parity is "unknown", NOT a confirmed 0 — kept distinct so the
        /// scorecard can report real comparison coverage (phase-2 needs every
        /// red to be a CONFIRMED red, never an unmeasured one).
        par_measured: bool,
        /// Whether the cell was actually RUN on this backend. An `unavailable`
        /// (backend binary absent) or `skip` cell was NOT run — its 0s are
        /// "not measured", never a confirmed compat/determinism fail.
        ran: bool,
    }
    let mut by_backend: BTreeMap<String, BTreeMap<(String, String), BCell>> = BTreeMap::new();
    for c in &denom_cells {
        if c.backend == "ptrace" || c.backend == "native" {
            continue;
        }
        // NOT-EXERCISED is a THIRD bucket, never a pass — vacuous, not green.
        // A backend that did nothing agrees with the reference perfectly, so
        // e9patch reporting mapped_sites=0 scores byte-identical parity while
        // actually running plain ptrace underneath. That manufactured perfect
        // score must not be credited. §319: the count must travel with the cell.
        let is_not_exercised = matches!(
            c.outcome.as_str(),
            "not-exercised" | "not_exercised" | "notexercised" | "not-exercised-no-main-elf-sites"
        ) || c.backend_engaged == Some(0) || c.parity_exercised == Some(false);

        // A known-green cell that is vacuous is NOT a failure but also NOT a
        // measurement; its parity/determinism must be withheld so a sweep over
        // dynamic guests does not manufacture 100% e9patch parity.
        let pass = c.outcome == "pass" && !is_not_exercised;
        let ran = !matches!(
            c.outcome.as_str(),
            "unavailable" | "skip" | "not-exercised" | "not_exercised" | "notexercised" | "not-exercised-no-main-elf-sites"
        ) && !is_not_exercised;
        // Determinism (run1 == run2) is independent of parity: a backend can be
        // self-deterministic yet diverge from ptrace. The CSV `deterministic`
        // field is authoritative; fall back to "verify pass => deterministic"
        // only when the collector left it blank. Do NOT gate on `pass`, which
        // for a non-ptrace backend already requires parity — except vacuous
        // cells which must be withheld entirely.
        let det = if is_not_exercised {
            false
        } else {
            c.deterministic.unwrap_or(pass && c.test_mode == "verify")
        };
        // Parity is true only when the collector recorded a bitwise match AND
        // the cell is not vacuous (§319).
        let par = if is_not_exercised {
            false
        } else {
            c.stdout_parity.unwrap_or(false)
        };
        let par_measured = if is_not_exercised {
            false
        } else {
            c.stdout_parity.is_some()
        };
        by_backend
            .entry(c.backend.clone())
            .or_default()
            .insert((c.bucket.clone(), c.test_id.clone()), BCell { det, par, par_measured, ran });
    }

    // Build per-bucket rows.
    #[derive(Default, Clone)]
    struct Row {
        ptrace: usize,
        // backend -> (parity_count, det_count, parity_measured_count, ran_count)
        back: BTreeMap<String, (usize, usize, usize, usize)>,
    }
    let mut rows: BTreeMap<String, Row> = BTreeMap::new();
    let mut total = Row::default();
    for bucket in &buckets {
        let denom = ptrace_pass.get(bucket).cloned().unwrap_or_default();
        let mut row = Row { ptrace: denom.len(), back: BTreeMap::new() };
        for b in &backend_cols {
            let mut par = 0usize;
            let mut det = 0usize;
            let mut par_meas = 0usize;
            let mut ran = 0usize;
            if let Some(map) = by_backend.get(b) {
                for tid in &denom {
                    if let Some(bc) = map.get(&(bucket.clone(), tid.clone())) {
                        if bc.par {
                            par += 1;
                        }
                        if bc.det {
                            det += 1;
                        }
                        if bc.par_measured {
                            par_meas += 1;
                        }
                        if bc.ran {
                            ran += 1;
                        }
                    }
                }
            }
            row.back.insert(b.clone(), (par, det, par_meas, ran));
        }
        total.ptrace += row.ptrace;
        for b in &backend_cols {
            let e = total.back.entry(b.clone()).or_insert((0, 0, 0, 0));
            let (p, d, m, r) = row.back[b];
            e.0 += p;
            e.1 += d;
            e.2 += m;
            e.3 += r;
        }
        rows.insert(bucket.clone(), row);
    }

    // A1 -- AN EMPTY DENOMINATOR IS NOT A ZERO RESULT.
    //
    // The per-cell vocabulary below (`?` / `~` / `n/a`, plus ran_count and
    // measured_count) is careful about unmeasured CELLS, but it cannot speak for
    // the denominator one level up: when no ptrace row passes the requested mode
    // there are no cells at all, and every format renders a confident `TOTAL 0`
    // (exit 0) that is indistinguishable from "we measured, and nothing passed".
    // That happens for real -- a run that is dbi/strict only has a legitimately
    // empty verify/ptrace denominator.
    //
    // So refuse to render, and say what IS present so the caller can pick a mode
    // or backend that exists. Distinct exit status (3) keeps "could not measure"
    // separable from usage errors (2) and from a rendered result (0).
    //
    // The remedy line reports the modes that would ACTUALLY yield a denominator --
    // i.e. modes with a *passing ptrace* row -- not the modes the run merely
    // contains. Those two differ exactly when the run has no ptrace rows at all
    // (a dbi-only run "contains" strict, yet `--denominator strict` refuses too),
    // and naming the wrong set would repeat this very defect one level up:
    // a remedy is only actionable if it travels with the population it is drawn from.
    let denom_total: usize = ptrace_pass.values().map(|s| s.len()).sum();
    if denom_total == 0 {
        let modes_present: BTreeSet<&str> = cells.iter().map(|c| c.test_mode.as_str()).collect();
        let backends_present: BTreeSet<&str> = cells.iter().map(|c| c.backend.as_str()).collect();
        let ptrace_rows = cells.iter().filter(|c| c.backend == "ptrace").count();
        // Exactly the `--denominator` values that would produce a non-empty denominator.
        let usable: BTreeSet<&str> = cells
            .iter()
            .filter(|c| c.backend == "ptrace" && c.outcome == "pass")
            .map(|c| c.test_mode.as_str())
            .collect();
        let run_label = scope_run.clone().unwrap_or_else(|| "ALL (last-writer-wins)".into());
        let remedy = if !usable.is_empty() {
            format!(
                "Retry with --denominator <{}> -- those are the modes this run has passing \
                 ptrace rows in.",
                usable.iter().copied().collect::<Vec<_>>().join("|")
            )
        } else if ptrace_rows == 0 {
            "This run has NO ptrace rows in any mode, so no denominator can be formed from it \
             at all; changing --denominator will not help. Use a run that includes the ptrace \
             reference backend."
                .to_string()
        } else {
            format!(
                "This run has {ptrace_rows} ptrace rows but none passing in any mode, so no \
                 --denominator choice yields a population; the reference backend itself failed \
                 here."
            )
        };
        eprintln!(
            "NO DATA: run {run} has 0 ptrace/{mode} passing cells, so the denominator is empty \
             and no percentage is defined (this is NOT a measured zero).\n\
             \x20 rows considered:  {n}\n\
             \x20 ptrace rows:      {ptrace_rows} (passing in modes: {usable})\n\
             \x20 modes present:    {modes}\n\
             \x20 backends present: {backends}\n\
             \x20 csv:              {csv}\n\
             {remedy}",
            run = run_label,
            mode = denom_mode,
            n = cells.len(),
            ptrace_rows = ptrace_rows,
            usable = if usable.is_empty() {
                "none".to_string()
            } else {
                usable.iter().copied().collect::<Vec<_>>().join(",")
            },
            modes = modes_present.iter().copied().collect::<Vec<_>>().join(","),
            backends = backends_present.iter().copied().collect::<Vec<_>>().join(","),
            csv = csv_path.display(),
            remedy = remedy,
        );
        exit(3);
    }

    let pct = |num: usize, den: usize| -> f64 {
        if den == 0 {
            0.0
        } else {
            100.0 * num as f64 / den as f64
        }
    };

    match fmt {
        "json" => {
            let mut out_rows = Vec::new();
            let mut emit = |name: &str, row: &Row| {
                let mut backs = serde_json::Map::new();
                for b in &backend_cols {
                    let (p, d, m, r) = row.back.get(b).copied().unwrap_or((0, 0, 0, 0));
                    let mut metrics = serde_json::Map::new();
                    metrics.insert(format!("{parity_key}_count"), json!(p));
                    metrics.insert(format!("{parity_key}_measured_count"), json!(m));
                    metrics.insert(
                        format!("{parity_key}_pct"),
                        json!((pct(p, row.ptrace) * 10.0).round() / 10.0),
                    );
                    metrics.insert("determinism_count".into(), json!(d));
                    metrics.insert("determinism_pct".into(), json!((pct(d, row.ptrace) * 10.0).round() / 10.0));
                    metrics.insert("ran_count".into(), json!(r));
                    backs.insert(b.clone(), Value::Object(metrics));
                }
                out_rows.push(json!({
                    "bucket": name,
                    "ptrace_count": row.ptrace,
                    "backends": backs,
                }));
            };
            for (name, row) in &rows {
                emit(name, row);
            }
            emit("TOTAL", &total);
            let doc = json!({
                "schema": 2,
                "kind": "compat-envelope-scorecard",
                "source_csv": csv_path.display().to_string(),
                "run_scope": scope_run.clone().unwrap_or_else(|| "all".into()),
                "denominator_mode": denom_mode,
                "denominator_meaning": denominator_meaning,
                "parity_metric": {
                    "label": parity_label,
                    "observable": observable,
                    "meaning": parity_meaning,
                    "is_full_parity": false,
                    "full_parity_not_measured": full_parity_not_measured,
                    "additional_unmeasured_context": ["TTY behavior"],
                },
                "backend_columns": backend_cols,
                "rows": out_rows,
            });
            println!("{}", serde_json::to_string_pretty(&doc).unwrap());
        }
        "tsv" => {
            let mut cols = vec!["bucket".to_string(), "ptrace".to_string()];
            for b in &backend_cols {
                cols.push(format!("{b}_{parity_key}_pct"));
                cols.push(format!("{b}_det_pct"));
                cols.push(format!("{b}_{parity_key}_measured"));
                cols.push(format!("{b}_ran"));
            }
            println!("{}", cols.join("\t"));
            let emit = |name: &str, row: &Row| {
                let mut f = vec![name.to_string(), row.ptrace.to_string()];
                for b in &backend_cols {
                    let (p, d, m, r) = row.back.get(b).copied().unwrap_or((0, 0, 0, 0));
                    f.push(format!("{:.1}", pct(p, row.ptrace)));
                    f.push(format!("{:.1}", pct(d, row.ptrace)));
                    f.push(format!("{m}/{}", row.ptrace));
                    f.push(format!("{r}/{}", row.ptrace));
                }
                println!("{}", f.join("\t"));
            };
            for (name, row) in &rows {
                emit(name, row);
            }
            emit("TOTAL", &total);
        }
        _ => {
            // Human table in the owner's exact shape.
            println!(
                "Compat-envelope scorecard  (run: {}, denominator: {} = {})",
                scope_run.clone().unwrap_or_else(|| "ALL (last-writer-wins)".into()),
                denom_mode,
                denominator_meaning,
            );
            println!("Input CSV: {}", csv_path.display());
            println!("Each backend cell is `{parity_label}%, determinism%` of the ptrace count. The two measurements are independent.");
            if observable == "stdout" {
                println!("CAVEAT: stdout-parity% compares piped guest stdout SHA-256 only. It is an upper bound on four-signal cross-backend parity; INFO logs, stack detlogs, and heap detlogs are not measured. TTY behavior is also outside this scorecard.");
            } else {
                println!("CAVEAT: tool-count-parity% compares only the shared Tool callback total. It does not measure stdout, INFO logs, stack detlogs, or heap detlogs, and is not full cross-backend parity. TTY behavior is also outside this scorecard.");
            }
            println!("{parity_label} suffix: `?` = the observable was never compared for that bucket (UNKNOWN, not confirmed 0); `~` = partial coverage (some cells unmeasured).");
            println!("`n/a` = backend not runnable here (binary absent / not enabled) — 0 cells run, NOT a confirmed fail.");
            println!();
            let mut header = format!("{:<22} {:>7}", "bucket", "ptrace");
            for b in &backend_cols {
                header.push_str(&format!("  {:>16}", b));
            }
            println!("{header}");
            println!("{}", "-".repeat(header.len()));
            let emit = |name: &str, row: &Row| {
                let mut line = format!("{:<22} {:>7}", name, row.ptrace);
                for b in &backend_cols {
                    let (p, d, m, r) = row.back.get(b).copied().unwrap_or((0, 0, 0, 0));
                    // A backend that ran ZERO denom cells here is not measurable
                    // (binary absent, not enabled, or vacuous e9patch mapped_sites=0
                    // which the engagement gate in collect-envelope.rs reclassifies
                    // as not-exercised and excluded from parity). Show n/a, never a
                    // 0% red — a vacuous cell manufactures a perfect parity score
                    // because the shared ptrace runtime ran underneath.
                    let cell = if row.ptrace > 0 && r == 0 {
                        "n/a".to_string()
                    } else {
                        // Distinguish "parity confirmed 0" from "parity never
                        // measured". m = denom cells whose parity was compared.
                        let mark = if row.ptrace == 0 {
                            ""
                        } else if m == 0 {
                            "?"
                        } else if m < row.ptrace {
                            "~"
                        } else {
                            ""
                        };
                        format!("{:.0}%{}, {:.0}%", pct(p, row.ptrace), mark, pct(d, row.ptrace))
                    };
                    line.push_str(&format!("  {:>16}", cell));
                }
                println!("{line}");
            };
            for (name, row) in &rows {
                emit(name, row);
            }
            println!("{}", "-".repeat(header.len()));
            emit("TOTAL", &total);
        }
    }
}
