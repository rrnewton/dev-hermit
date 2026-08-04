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
//!     the other. A cell the backend never ran counts as 0 in both, so a small
//!     envelope reads as a low percentage — that is the honest, anti-fakery
//!     signal, not a bug.
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
//!   --observable O    Observable compared by the legacy CSV `parity` field
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
    backend: String,   // ptrace | dbi | kvm | sabre | liteinst | native
    outcome: String,   // pass | fail | error | timeout | oom | skip
    deterministic: Option<bool>,
    // Legacy CSV field name: this stores stdout-only parity.
    parity: Option<bool>,
}

/// The canonical header this renderer and `collect-envelope.rs` agree on.
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
    "parity",
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

/// Minimal RFC-4180-ish CSV split: handles double-quoted fields with commas and
/// escaped `""`. Good enough for our own writer's output.
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

    let mut lines = text.lines();
    let header_line = lines.next().unwrap_or_else(|| die("empty CSV (no header)"));
    let header = split_csv_line(header_line);
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
    let (i_run, i_bucket, i_tid, i_tmode, i_backend, i_outcome, i_det, i_par) = (
        idx("run_id"),
        idx("bucket"),
        idx("test_id"),
        idx("test_mode"),
        idx("backend"),
        idx("outcome"),
        idx("deterministic"),
        idx("parity"),
    );

    let mut cells: Vec<Cell> = Vec::new();
    for (n, line) in lines.enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let f = split_csv_line(line);
        let get = |i: usize| f.get(i).cloned().unwrap_or_default();
        if f.len() < header.len() {
            eprintln!("warn: row {} has {} fields (< {}); skipping", n + 2, f.len(), header.len());
            continue;
        }
        cells.push(Cell {
            seq: n,
            run_id: get(i_run),
            bucket: get(i_bucket),
            test_id: get(i_tid),
            test_mode: get(i_tmode),
            backend: get(i_backend),
            outcome: get(i_outcome),
            deterministic: parse_bool(&get(i_det)),
            parity: parse_bool(&get(i_par)),
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

    let default_order = ["dbi", "kvm", "sabre", "liteinst"];
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
        let pass = c.outcome == "pass";
        let ran = c.outcome != "unavailable" && c.outcome != "skip";
        // Determinism (run1 == run2) is independent of parity: a backend can be
        // self-deterministic yet diverge from ptrace. The CSV `deterministic`
        // field is authoritative; fall back to "verify pass => deterministic"
        // only when the collector left it blank. Do NOT gate on `pass`, which
        // for a non-ptrace backend already requires parity.
        let det = c.deterministic.unwrap_or(pass && c.test_mode == "verify");
        // Parity is true only when the collector recorded a bitwise match.
        let par = c.parity.unwrap_or(false);
        let par_measured = c.parity.is_some();
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
                    // (binary absent / not enabled) — show n/a, never a 0% red.
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
