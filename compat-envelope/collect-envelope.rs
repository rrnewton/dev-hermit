#!/usr/bin/env rust-script
//! Collect the Hermit cross-backend **compatibility envelope** into the
//! scorecard CSV, as a side effect of running the e2e regression/expansion
//! suite. This lives in the OUTER dev-hermit repo on purpose: the CSV is
//! coordination/measurement state and must not add noise to hermit/reverie.
//!
//! It does NOT reimplement the e2e runner. It *drives* the canonical harness
//! (`hermit/ci/test_harness.sh`) and *consumes* the machine-readable JSONL it
//! already emits (one record per cell: outcome, backend, mode, duration_ms,
//! hermit_sha, ...). Two modes:
//!
//!   * `--mode regression` — run only the known-green (manifest `backends_enabled`,
//!     `ci=true`) cells and assert they stay green. This is what `validate`/CI
//!     calls; the CSV is the byproduct recording exactly what was green.
//!   * `--mode expansion` — run the FULL superset including currently-disabled /
//!     failing cells, to catch failing->passing flips (compat growth). Every
//!     such cell is boxed with a per-cell wall-time + memory budget (see
//!     `expansion-dag.rs`, which turns this same envelope into a fine-grained
//!     safe-ci-dag-runner DAG). Here `--mode expansion` runs them serially with
//!     `timeout(1)`; use `expansion-dag.rs` for the cgroup-boxed parallel run.
//!
//! Determinism vs parity:
//!   * determinism = a TWO-RUN comparison observed run1==run2. Only `verify`
//!     executes a second run, so only a passing `verify` cell earns a value;
//!     `strict`/`chaos`/`custom`/`replay` are single runs and are recorded BLANK
//!     (unmeasured), never 1. `verify_compare` records what the two runs were
//!     compared BY -- `stripped` normalises addresses/tmp paths and does not
//!     compare the detlog, so it is weaker than bitwise and must stay legible;
//!   * parity = the backend's guest output is bitwise-identical to the ptrace
//!     reference. We compute it by capturing guest stdout under ptrace and under
//!     the backend and comparing SHA-256 (only for reconstructable guest commands
//!     — `.sh`(--run) fixtures and `direct` commands; enable with `--with-parity`).
//!
//! Usage:
//!   compat-envelope/collect-envelope.rs --mode regression|expansion
//!       [--lane portable|privileged] [--buckets b1,b2,...] [--backends ...]
//!       [--with-parity] [--csv PATH] [--repo PATH] [--run-id ID] [--dry-run]
//!
//! ```cargo
//! [dependencies]
//! serde_json = "1"
//! sha2 = "0.10"
//! ```

use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{exit, Command};
use std::time::{SystemTime, UNIX_EPOCH};

const USAGE: &str = r#"Usage: compat-envelope/collect-envelope.rs --mode regression|expansion [OPTIONS]

Drive hermit/ci/test_harness.sh and record the cross-backend compat envelope to CSV.

Options:
  --mode M          regression (known-green, assert) or expansion (full superset).
  --lane L          portable | privileged (default: portable).
  --buckets LIST    Comma-separated manifest buckets (default: all in the lane).
  --backends LIST   Comma-separated backends to measure (default: all in plan).
  --with-parity     Capture guest stdout per backend and compare to ptrace ref.
  --csv PATH        Scorecard CSV to append (default: scorecard.csv beside script).
  --repo PATH       hermit checkout (default: ../hermit relative to this script,
                    else $DEV_HERMIT/hermit).
  --run-id ID       Override the run id (default: unix-seconds).
  --assert-green    Exit 1 if any enabled (known-green) cell no longer passes
                    (the `validate`/CI whole-envelope gate). Regression mode.
  --dry-run         Enumerate + print what would run; touch nothing.
  -h, --help        Show this help.
"#;

fn die(msg: &str) -> ! {
    eprintln!("collect-envelope: {msg}\n\n{USAGE}");
    exit(2);
}

const HEADER: &str = "run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,test_id,test_mode,backend,cell_state,outcome,deterministic,stdout_parity,parity_exercised,backend_engaged,native_output_hash,output_hash,ref_output_hash,duration_ms,max_rss_kb,reason,verify_compare,bitwise_parity,compared_log_messages,tier,run_flags";

/// Quote a CSV field if it contains a comma, quote, or newline.
fn csv_field(s: &str) -> String {
    if s.contains(',') || s.contains('"') || s.contains('\n') {
        format!("\"{}\"", s.replace('"', "\"\""))
    } else {
        s.to_string()
    }
}

struct Meta {
    run_id: String,
    run_utc: String,
    hermit_sha: String,
    reverie_sha: String,
    dirty: bool,
}

fn git(repo: &Path, args: &[&str]) -> Option<String> {
    let out = Command::new("git").arg("-C").arg(repo).args(args).output().ok()?;
    if out.status.success() {
        Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
    } else {
        None
    }
}

fn main() {
    let mut mode: Option<String> = None;
    let mut lane = "portable".to_string();
    let mut buckets_arg: Option<String> = None;
    let mut backends_arg: Option<String> = None;
    let mut with_parity = false;
    let mut csv: Option<PathBuf> = None;
    let mut repo: Option<PathBuf> = None;
    let mut run_id_arg: Option<String> = None;
    let mut dry_run = false;
    let mut assert_green = false;

    let mut it = env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "-h" | "--help" => {
                println!("{USAGE}");
                exit(0);
            }
            "--mode" => mode = Some(it.next().unwrap_or_else(|| die("--mode needs a value"))),
            "--lane" => lane = it.next().unwrap_or_else(|| die("--lane needs a value")),
            "--buckets" => buckets_arg = Some(it.next().unwrap_or_else(|| die("--buckets needs a list"))),
            "--backends" => backends_arg = Some(it.next().unwrap_or_else(|| die("--backends needs a list"))),
            "--self-check" => std::process::exit(self_check()),
            "--with-parity" => with_parity = true,
            "--csv" => csv = Some(PathBuf::from(it.next().unwrap_or_else(|| die("--csv needs a path")))),
            "--repo" => repo = Some(PathBuf::from(it.next().unwrap_or_else(|| die("--repo needs a path")))),
            "--run-id" => run_id_arg = Some(it.next().unwrap_or_else(|| die("--run-id needs a value"))),
            "--dry-run" => dry_run = true,
            "--assert-green" => assert_green = true,
            other => die(&format!("unknown argument {other}")),
        }
    }
    let mode = mode.unwrap_or_else(|| die("--mode is required"));
    if mode != "regression" && mode != "expansion" {
        die("--mode must be regression or expansion");
    }

    let here = script_dir();
    let csv_path = csv.unwrap_or_else(|| here.join("scorecard.csv"));
    let repo = repo
        .or_else(|| {
            let candidate = here.join("..").join("hermit");
            candidate.is_dir().then(|| candidate)
        })
        .or_else(|| env::var("DEV_HERMIT").ok().map(|d| PathBuf::from(d).join("hermit")))
        .unwrap_or_else(|| die("could not locate hermit checkout; pass --repo"));
    let repo = fs::canonicalize(&repo).unwrap_or(repo);
    let parent = repo.parent().map(Path::to_path_buf).unwrap_or_else(|| here.clone());
    let reverie = parent.join("reverie");

    let now = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    let meta = Meta {
        run_id: run_id_arg.unwrap_or_else(|| now.to_string()),
        run_utc: format!("@{now}"),
        hermit_sha: git(&repo, &["rev-parse", "HEAD"]).unwrap_or_else(|| "unknown".into()),
        reverie_sha: git(&reverie, &["rev-parse", "HEAD"]).unwrap_or_else(|| "unknown".into()),
        dirty: git(&repo, &["status", "--porcelain"]).map(|s| !s.is_empty()).unwrap_or(false),
    };

    let backends_filter: Option<Vec<String>> =
        backends_arg.map(|l| l.split(',').map(|s| s.trim().to_string()).collect());
    let buckets_filter: Option<Vec<String>> =
        buckets_arg.map(|l| l.split(',').map(|s| s.trim().to_string()).collect());

    // Enumerate the authoritative cell plan from the harness.
    let plan = enumerate_plan(&repo, &lane, &mode);
    let cells: Vec<PlanCell> = plan
        .into_iter()
        .filter(|c| buckets_filter.as_ref().map_or(true, |b| b.contains(&c.bucket)))
        .filter(|c| backends_filter.as_ref().map_or(true, |b| b.contains(&c.backend)))
        .collect();

    eprintln!(
        "collect-envelope: mode={mode} lane={lane} cells={} hermit={} dirty={}",
        cells.len(),
        &meta.hermit_sha[..meta.hermit_sha.len().min(12)],
        meta.dirty
    );

    if dry_run {
        let mut by_backend: BTreeMap<String, usize> = BTreeMap::new();
        for c in &cells {
            *by_backend.entry(c.backend.clone()).or_default() += 1;
        }
        for (b, n) in &by_backend {
            eprintln!("  {b}: {n} cells");
        }
        eprintln!("(dry-run: nothing executed, CSV untouched)");
        return;
    }

    // Ensure the CSV exists with a header.
    if !csv_path.exists() {
        fs::write(&csv_path, format!("{HEADER}\n")).unwrap_or_else(|e| die(&format!("cannot create CSV: {e}")));
    }
    // BIND TO THE FILE'S SCHEMA, not to ours.
    //
    // This producer's HEADER is wider than the canonical scorecard (it also records
    // stdout_parity/parity_exercised/backend_engaged/native_output_hash/
    // ref_output_hash/run_flags). It used to append its own row shape regardless of
    // what the target file's header actually was, so appending into the canonical
    // 23-column scorecard wrote 28-field rows: five values past the last column,
    // which a reader surfaces as csv.DictReader's None key and which shifts nothing
    // visibly until someone reads the tail. Read the header and project onto it.
    let target_header: Vec<String> = {
        let first = fs::read_to_string(&csv_path)
            .unwrap_or_else(|e| die(&format!("cannot read CSV header: {e}")));
        let line = first.lines().next().unwrap_or("").to_string();
        line.split(',').map(|c| c.trim().to_string()).collect()
    };
    // A column this producer MUST fill has to exist; extras the file lacks are
    // dropped, and columns the file has that we do not fill are written blank.
    for required in ["run_id", "test_id", "backend", "outcome", "deterministic"] {
        if !target_header.iter().any(|c| c == required) {
            die(&format!(
                "scorecard {} is missing the required column {required:?}; its header has {} \
                 column(s): {}",
                csv_path.display(), target_header.len(), target_header.join(",")
            ));
        }
    }
    let mut out = fs::OpenOptions::new()
        .append(true)
        .open(&csv_path)
        .unwrap_or_else(|e| die(&format!("cannot open CSV for append: {e}")));

    // Run each (bucket,backend) group through the harness and read its JSONL.
    let mut groups: BTreeMap<(String, String), Vec<PlanCell>> = BTreeMap::new();
    for c in cells {
        groups.entry((c.bucket.clone(), c.backend.clone())).or_default().push(c);
    }

    // Probe backend availability once so an absent backend binary (e.g. SaBRe
    // not built in this checkout) is recorded as UNAVAILABLE — an honest
    // "not measured", never a confirmed compat/determinism fail. Anti-fakery: a
    // missing backend must not masquerade as a red backend.
    let mut avail: BTreeMap<String, Option<String>> = BTreeMap::new();
    for (_, backend) in groups.keys() {
        avail
            .entry(backend.clone())
            .or_insert_with(|| backend_unavailable_reason(&repo, backend));
    }
    for (b, why) in &avail {
        if let Some(reason) = why {
            eprintln!(
                "collect-envelope: backend `{b}` UNAVAILABLE here — cells recorded as unavailable (not fail): {reason}"
            );
        }
    }

    let mut rows_written = 0usize;
    let mut regressions: Vec<String> = Vec::new();
    for ((bucket, backend), group) in &groups {
        let unavailable_reason = avail.get(backend).and_then(|r| r.clone());
        let available = unavailable_reason.is_none();
        let results = if available {
            run_group(&repo, &lane, bucket, backend, &mode)
        } else {
            Vec::new()
        };
        // Index harness outcomes by (test, mode).
        let mut outcomes: BTreeMap<(String, String), HarnessResult> = BTreeMap::new();
        for r in results {
            outcomes.insert((r.test.clone(), r.mode.clone()), r);
        }
        for c in group {
            let hr = outcomes.get(&(c.test.clone(), c.mode.clone()));
            let (outcome, duration_ms, reason) = if !available {
                (
                    "unavailable".to_string(),
                    0i64,
                    unavailable_reason
                        .clone()
                        .unwrap_or_else(|| "backend unavailable (reason not captured)".to_string()),
                )
            } else {
                match hr {
                    Some(r) => (r.outcome.clone(), r.duration_ms, r.reason.clone()),
                    None => ("skip".to_string(), 0i64, "no harness record".to_string()),
                }
            };
            let pass = outcome == "pass";
            // A known-green (enabled) cell that no longer passes is a regression —
            // but an UNAVAILABLE backend is environmental, never a regression.
            if !pass && available && c.cell_state == "enabled" {
                regressions.push(format!(
                    "{bucket}/{}::{} [{backend}] -> {outcome} ({reason})",
                    c.test, c.mode
                ));
            }
            // DETERMINISM MUST BE EARNED BY A TWO-RUN COMPARISON, never inferred
            // from a single passing run.
            //
            // The previous rule was `pass => deterministic=1` for EVERY mode. Only
            // `verify` actually executes a second run and compares it; `strict`,
            // `chaos`, `custom` and `replay` are single runs that cannot observe
            // run1==run2 at all. On the live scorecard that rule minted
            // deterministic=1 for 105 single-run cells (strict 102, chaos 1,
            // custom 1, replay 1) against 63 that genuinely compared — i.e. most
            // of the determinism evidence was an artifact of the rule, not a
            // measurement. A single run passing says the program worked once; it
            // is silent about reproducibility.
            //
            // BLANK means UNMEASURED and is the honest answer for a single run.
            // It is deliberately NOT `0`: `0` asserts observed nondeterminism,
            // which a single run cannot establish either.
            let ran_two_run_comparison = c.mode == "verify";
            let deterministic = if !available || outcome == "skip" {
                None
            } else if !pass {
                // A completed non-pass IS confirmed: the cell did not reproduce
                // its expected behaviour under its own backend.
                Some(false)
            } else if ran_two_run_comparison {
                Some(true)
            } else {
                None
            };
            // WHAT the two runs were compared BY. hermit's `--verify` defaults to
            // the STRIPPED comparison (this harness passes no compare-mode flag),
            // which normalises addresses and tmp paths and does NOT compare the
            // detlog. So `stripped` is a weaker claim than bitwise determinism and
            // must be legible as such in the row rather than hidden behind a `1`.
            let verify_compare = if deterministic == Some(true) && ran_two_run_comparison {
                "stripped"
            } else {
                ""
            };
            // The tier this row EARNED, carried beside the comparator so a bare
            // `deterministic=1` can never again stand in for "which comparison".
            // `stripped` is the ceiling this harness can reach: it passes no
            // compare-mode flag, so hermit runs the Stripped policy, whose own
            // --verify-json reports bitwise_parity:false. `bitwise` is therefore
            // NOT emittable here and is not merely unset -- it is unreachable.
            let tier = if verify_compare.is_empty() { "" } else { "stripped" };
            // Blank, not "0": this harness does not read --verify-json, so it has
            // no parity boolean and no message counts to report. Blank means "not
            // recorded"; a 0 would assert a measurement that was never taken.
            let bitwise_parity = "";
            let compared_log_messages = "";
            let (
                parity,
                parity_exercised,
                native_output_hash,
                output_hash,
                ref_output_hash,
                backend_engaged,
            ) = if with_parity && pass {
                capture_parity(&repo, &lane, c, backend)
            } else {
                (None, None, String::new(), String::new(), String::new(), None)
            };
            // NOT-EXERCISED is a THIRD bucket, not a downgrade of pass/fail. A cell whose guest
            // produces the same bytes with and without hermit did not exercise the property the
            // cell claims to measure, so reporting it as parity asserts coverage that does not
            // exist. The verdict is RECLASSIFIED, never dropped: `parity` keeps its measured
            // value so the row stays auditable, and `outcome` names the vacuity.
            // The SECOND, orthogonal not-exercised condition: DID THIS BACKEND ENGAGE.
            // The check above asks whether the GUEST exercised anything hermit
            // virtualizes. It cannot see a backend that did nothing, because the shared
            // runtime underneath still determinizes the guest: e9patch reporting
            // mapped_sites=0 still differs from native, so it passes the guest test and
            // then scores byte-identical parity with ptrace -- a manufactured perfect
            // score for a backend that never ran. Zero is a THIRD BUCKET here too:
            // never a pass, never a failure, excluded from the parity number with its
            // reason, because a libc-only guest legitimately gives a main-ELF patcher
            // nothing to do.
            let engagement_vacuous = backend_engaged == Some(0);
            let outcome = if parity_exercised == Some(false) || engagement_vacuous {
                "not-exercised".to_string()
            } else {
                outcome
            };
            let reason = if engagement_vacuous {
                format!(
                    "{backend} performed no work on this guest ({}=0); excluded from parity \
                     rather than scored, because a backend that does nothing agrees with the \
                     reference perfectly",
                    engagement_counter_name(backend)
                )
            } else {
                reason
            };
            // WHICH INVOCATION produced this row. Without it, "pass" is ambiguous across
            // strictness levels: the same cell id can be run under different harness flags and
            // the CSV would render both as an identical green. These are the flags
            // `run_group` actually passes for this (bucket, backend, run mode).
            let run_flags = format!(
                "--lane {lane} --category {bucket} --backend {backend} {} | test_mode={}",
                if mode == "regression" { "--ci-only" } else { "--include-manual" },
                c.mode
            );
            let row = [
                meta.run_id.clone(),
                meta.run_utc.clone(),
                meta.hermit_sha.clone(),
                meta.reverie_sha.clone(),
                meta.dirty.to_string(),
                mode.clone(),
                lane.clone(),
                bucket.clone(),
                c.test.clone(),
                c.mode.clone(),
                backend.clone(),
                c.cell_state.clone(),
                outcome,
                deterministic.map(|b| if b { "1" } else { "0" }).unwrap_or("").to_string(),
                parity.map(|b| if b { "1" } else { "0" }).unwrap_or("").to_string(),
                parity_exercised.map(|b| if b { "1" } else { "0" }).unwrap_or("").to_string(),
                // THE COUNT TRAVELS WITH THE CELL. Blank means no counter was found
                // for this backend, which is not the same as zero and must not be read
                // as engagement.
                backend_engaged.map(|n| n.to_string()).unwrap_or_default(),
                native_output_hash,
                output_hash,
                ref_output_hash,
                duration_ms.to_string(),
                String::new(), // max_rss_kb: filled by expansion-dag.rs cgroup path
                reason,
                verify_compare.to_string(),
                bitwise_parity.to_string(),
                compared_log_messages.to_string(),
                tier.to_string(),
                run_flags,
            ];
            // Project our named row onto the file's columns, in the file's order.
            // `parity` and `stdout_parity` are the same observable under two spellings
            // (the rename is in flight), so either target column accepts our value.
            let names: Vec<&str> = HEADER.split(',').collect();
            let line = target_header
                .iter()
                .map(|col| {
                    let want: &str = if col == "parity" { "stdout_parity" } else { col.as_str() };
                    match names.iter().position(|n| *n == want) {
                        Some(i) => csv_field(&row[i]),
                        None => String::new(),
                    }
                })
                .collect::<Vec<_>>()
                .join(",");
            writeln!(out, "{line}").unwrap_or_else(|e| die(&format!("CSV write failed: {e}")));
            rows_written += 1;
        }
    }
    eprintln!("collect-envelope: wrote {rows_written} rows to {}", csv_path.display());

    // Whole-envelope green gate (owner: `validate`/CI asserts every known-green
    // cell stayed green as a side effect of writing the CSV).
    if assert_green && !regressions.is_empty() {
        eprintln!(
            "collect-envelope: REGRESSION — {} known-green cell(s) no longer pass:",
            regressions.len()
        );
        for r in &regressions {
            eprintln!("  FAIL {r}");
        }
        exit(1);
    }
    if assert_green {
        eprintln!("collect-envelope: envelope GREEN — all enabled cells passed.");
    }
}

#[derive(Clone)]
struct PlanCell {
    bucket: String,
    test: String,
    mode: String,
    backend: String,
    cell_state: String, // enabled | disabled
}

struct HarnessResult {
    test: String,
    mode: String,
    outcome: String,
    duration_ms: i64,
    reason: String,
}

/// Enumerate the authoritative plan. Regression = enabled ci cells
/// (`test_harness.sh plan`); expansion = enabled + disabled (`plan` + `audit-gaps`).
fn enumerate_plan(repo: &Path, lane: &str, mode: &str) -> Vec<PlanCell> {
    let mut cells = Vec::new();
    let harness = repo.join("ci/test_harness.sh");
    let run = |args: &[&str]| -> Option<Value> {
        let out = Command::new("bash").arg(&harness).args(args).current_dir(repo).output().ok()?;
        if !out.status.success() {
            eprintln!(
                "warn: harness {:?} failed: {}",
                args,
                String::from_utf8_lossy(&out.stderr).trim()
            );
            return None;
        }
        serde_json::from_slice(&out.stdout).ok()
    };
    if let Some(Value::Array(rows)) = run(&["plan", "--lane", lane, "--format", "json"]) {
        for r in rows {
            cells.push(PlanCell {
                bucket: r.get("category").and_then(Value::as_str).unwrap_or("").to_string(),
                test: r.get("test").and_then(Value::as_str).unwrap_or("").to_string(),
                mode: r.get("mode").and_then(Value::as_str).unwrap_or("").to_string(),
                backend: r.get("backend").and_then(Value::as_str).unwrap_or("native").to_string(),
                cell_state: "enabled".to_string(),
            });
        }
    }
    if mode == "expansion" {
        if let Some(Value::Array(rows)) = run(&["audit-gaps", "--lane", lane, "--format", "json"]) {
            for r in rows {
                let backend = r.get("backend").and_then(Value::as_str).unwrap_or("").to_string();
                if backend.is_empty() || backend == "native" {
                    continue;
                }
                cells.push(PlanCell {
                    bucket: r.get("category").and_then(Value::as_str).unwrap_or("").to_string(),
                    test: r.get("test").and_then(Value::as_str).unwrap_or("").to_string(),
                    mode: r.get("mode").and_then(Value::as_str).unwrap_or("").to_string(),
                    backend,
                    cell_state: "disabled".to_string(),
                });
            }
        }
    }
    cells.retain(|c| !c.test.is_empty() && c.backend != "native");
    cells
}

/// Run one (bucket,backend) group and parse the results JSONL.
fn run_group(repo: &Path, lane: &str, bucket: &str, backend: &str, mode: &str) -> Vec<HarnessResult> {
    let harness = repo.join("ci/test_harness.sh");
    let results_dir = repo.join("ignored/e2e/compat-envelope").join(lane).join(bucket);
    let _ = fs::create_dir_all(&results_dir);
    let results = results_dir.join(format!("{backend}.jsonl"));
    let mut args: Vec<String> = vec![
        "run".into(),
        "--lane".into(),
        lane.into(),
        "--category".into(),
        bucket.into(),
        "--backend".into(),
        backend.into(),
        "--results".into(),
        results.to_string_lossy().into_owned(),
    ];
    if mode == "regression" {
        args.push("--ci-only".into());
    } else {
        args.push("--include-manual".into());
    }
    let status = Command::new("bash")
        .arg(&harness)
        .args(&args)
        .current_dir(repo)
        .status();
    if let Err(e) = status {
        eprintln!("warn: harness run failed for {bucket}/{backend}: {e}");
    }
    let mut out = Vec::new();
    if let Ok(text) = fs::read_to_string(&results) {
        for line in text.lines() {
            if line.trim().is_empty() {
                continue;
            }
            let Ok(v): Result<Value, _> = serde_json::from_str(line) else { continue };
            out.push(HarnessResult {
                test: v.get("test").and_then(Value::as_str).unwrap_or("").to_string(),
                mode: v.get("mode").and_then(Value::as_str).unwrap_or("").to_string(),
                outcome: v.get("outcome").and_then(Value::as_str).unwrap_or("").to_lowercase(),
                duration_ms: v.get("duration_ms").and_then(Value::as_i64).unwrap_or(0),
                reason: v.get("reason").and_then(Value::as_str).unwrap_or("").to_string(),
            });
        }
    }
    out
}

/// Capture guest stdout under ptrace and under `backend`; parity = hashes match.
/// Only reconstructable guest commands are attempted (`.sh` fixtures run with
/// `--run`, and `direct` commands); otherwise returns (None, "").
/// Returns `(parity, exercised, native_hash, output_hash, ref_output_hash)`.
///
/// `parity` alone is a POSITIVE-ONLY comparison: it asks "does the candidate match the ptrace
/// golden" and nothing else, so it passes whenever both sides agree -- INCLUDING when they agree
/// because the guest never exercised the determinization under test. A test whose output is the
/// same with and without hermit cannot distinguish a determinizing backend from an inert one, so
/// it scores parity on EVERY backend and the whole row reads green. That is the same shape as an
/// e9patch cell scoring byte-identical parity while reporting `candidate_sites=0`.
///
/// `exercised` supplies the missing negative side by running the guest with NO hermit at all and
/// comparing that host answer to the golden. If the bare host already produces the golden output,
/// hermit changed nothing observable and the cell is NOT-EXERCISED -- a distinct bucket from pass
/// and fail, because the cell is neither evidence of parity nor evidence of a defect.
///
/// The negative control is a BARE EXEC, not `--backend native`: hermit has no `native` backend
/// (`--backend` accepts only ptrace/dbi/liteinst/sabre/kvm/e9patch), so the do-nothing run has to
/// bypass hermit entirely.
///
/// The reference hash was always computed here and then DISCARDED, which left a `parity=1` row
/// unable to say what it matched. A comparison result that does not name both sides is not
/// reproducible evidence: a reader cannot tell a genuine match from a row where both sides were
/// empty, nor re-check the claim later. Both hashes are now recorded.
fn split_run(r: Option<(String, Option<i64>)>) -> (Option<String>, Option<i64>) {
    match r {
        Some((h, e)) => (Some(h), e),
        None => (None, None),
    }
}

fn capture_parity(
    repo: &Path,
    lane: &str,
    cell: &PlanCell,
    backend: &str,
) -> (Option<bool>, Option<bool>, String, String, String, Option<i64>) {
    let Some(guest) = guest_command(repo, &cell.test) else {
        return (None, None, String::new(), String::new(), String::new(), None);
    };
    let (ref_hash, _) = split_run(run_and_hash(repo, lane, "ptrace", &guest));
    let (this_hash, engaged) = split_run(run_and_hash(repo, lane, backend, &guest));
    let native_hash = run_native_and_hash(repo, &guest);
    // A cell is EXERCISED when the determinized answer differs from the bare host answer. If they
    // are equal the guest observed nothing hermit virtualizes, so no backend could ever fail it.
    let exercised = match (ref_hash.clone(), native_hash.clone()) {
        (Some(r), Some(n)) => Some(r != n),
        // The bare run failing is not evidence either way (the guest may need the container), so
        // exercise stays UNKNOWN rather than being silently asserted.
        _ => None,
    };
    let native = native_hash.unwrap_or_default();
    match (ref_hash.clone(), this_hash.clone()) {
        (Some(r), Some(t)) => (Some(r == t), exercised, native, t, r, engaged),
        _ => (
            None,
            exercised,
            native,
            this_hash.unwrap_or_default(),
            ref_hash.unwrap_or_default(),
            engaged,
        ),
    }
}

/// Run the guest with NO hermit -- the do-nothing control that gives parity its negative side.
/// Mirrors `run_and_hash`'s pinned guest environment so the only difference between the two runs
/// is hermit itself; otherwise an env delta, not determinization, would explain any divergence.
fn run_native_and_hash(repo: &Path, guest: &[String]) -> Option<String> {
    let (prog, args) = guest.split_first()?;
    let mut cmd = Command::new("timeout");
    cmd.arg("120s").arg(prog);
    for a in args {
        cmd.arg(a);
    }
    cmd.env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("LC_ALL", "C")
        .env("TZ", "UTC")
        .current_dir(repo);
    let out = cmd.output().ok()?;
    if !out.status.success() {
        return None;
    }
    let mut h = Sha256::new();
    h.update(&out.stdout);
    Some(format!("{:x}", h.finalize()))
}

/// Resolve a test-id to a runnable guest argv from the harness manifest docs.
fn guest_command(repo: &Path, test_id: &str) -> Option<Vec<String>> {
    let harness = repo.join("ci/test_harness.sh");
    let out = Command::new("cargo")
        .args(["run", "--quiet", "-p", "hermit-manifest-plan", "--", "--format", "harness-json"])
        .current_dir(repo)
        .output()
        .ok()?;
    if !out.status.success() {
        let _ = &harness;
        return None;
    }
    let docs: Value = serde_json::from_slice(&out.stdout).ok()?;
    let arr = docs.as_array()?;
    for doc in arr {
        let tests = doc.get("test").and_then(Value::as_array)?;
        for t in tests {
            if t.get("id").and_then(Value::as_str) != Some(test_id) {
                continue;
            }
            if let Some(prog) = t.get("program").and_then(Value::as_str) {
                if prog.ends_with(".sh") {
                    return Some(vec![repo.join(prog).to_string_lossy().into_owned(), "--run".into()]);
                }
                if prog.ends_with(".c") || prog.ends_with(".rs") {
                    // Compiled fixtures: replicate the harness build step
                    // (test_harness.sh cc/rustc invocation) into a stable temp
                    // binary so parity can run the SAME guest under ptrace and the
                    // backend. On any build failure return None so the cell records
                    // parity as UNMEASURED (honest `?`), never a false 0.
                    return build_compiled_fixture(repo, test_id, prog, t.get("build"));
                }
                return None; // unknown program kind
            }
            if let Some(direct) = t.get("direct").and_then(Value::as_str) {
                return Some(vec!["bash".into(), "-c".into(), direct.to_string()]);
            }
        }
    }
    None
}

/// Probe whether `backend` can actually launch a trivial guest in this checkout.
/// Returns false when the backend binary is missing (e.g. SaBRe not built) so
/// its cells are recorded UNAVAILABLE rather than as a compat/determinism fail.
/// ptrace/kvm are in-process; treat them as available (kvm's /dev/kvm gate is a
/// separate runtime concern surfaced by the actual cell run).
/// Returns `None` when the backend is usable, or `Some(reason)` naming why it is not.
///
/// This previously returned a bare bool and every unavailable cell got the same hardcoded string,
/// "backend binary not present in this checkout" -- a constant masquerading as a diagnosis. It was
/// also frequently WRONG: a backend that is built but refuses to load reported "not present". The
/// reason is now the observed cause, so an unavailable row says which check failed.
fn backend_unavailable_reason(repo: &Path, backend: &str) -> Option<String> {
    if backend == "ptrace" || backend == "kvm" || backend == "native" {
        return None;
    }
    let hermit = hermit_bin(repo);
    if !hermit.exists() {
        return Some(format!(
            "hermit binary not built at {} (backend `{backend}` never probed)",
            hermit.display()
        ));
    }
    let out = Command::new("timeout")
        .arg("60s")
        .arg(&hermit)
        .arg("run")
        .arg("--backend")
        .arg(backend)
        .arg("--")
        .arg("/bin/true")
        .current_dir(repo)
        .output();
    match out {
        Ok(o) => {
            let err = String::from_utf8_lossy(&o.stderr);
            // Explicit unavailability markers from the backend loader. Report the marker that
            // matched, so the row records the observed evidence rather than a generic phrase.
            for marker in [
                "is unavailable",
                "was not found",
                "HERMIT_SABRE_BINARY",
                "not found in the Hermit installation",
            ] {
                if err.contains(marker) {
                    let line = err
                        .lines()
                        .find(|l| l.contains(marker))
                        .unwrap_or(marker)
                        .trim();
                    return Some(format!(
                        "backend `{backend}` probe reported unavailable: {}",
                        line.chars().take(160).collect::<String>()
                    ));
                }
            }
            None
        }
        Err(e) => Some(format!(
            "backend `{backend}` probe could not be executed: {e}"
        )),
    }
}

/// Compile a `.c`/`.rs` fixture the way `ci/test_harness.sh` does, into a stable
/// per-test temp binary, and return its argv. `build` is the manifest's optional
/// `build` object (cflags/rustflags). Returns None on any compile failure so the
/// caller records parity as unmeasured rather than a false 0.
fn build_compiled_fixture(
    repo: &Path,
    test_id: &str,
    prog: &str,
    build: Option<&Value>,
) -> Option<Vec<String>> {
    let src = repo.join(prog);
    if !src.exists() {
        return None;
    }
    // Build under the repo output tree, NOT /tmp: hermit replaces guest /tmp with
    // an isolated dir and refuses to launch a guest program that lives there.
    let dir = repo
        .join("ignored/compat-envelope-parity")
        .join(test_id.replace('/', "_"));
    fs::create_dir_all(&dir).ok()?;
    let bin = dir.join("program");
    let extra = |key: &str| -> Vec<String> {
        build
            .and_then(|b| b.get(key))
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
            .unwrap_or_default()
    };
    let ok = if prog.ends_with(".c") {
        let mut c = Command::new("cc");
        c.args(["-std=c11", "-O2", "-g", "-Wall", "-Wextra", "-Werror"]);
        for a in extra("cflags") {
            c.arg(a);
        }
        c.arg(&src).arg("-o").arg(&bin);
        c.status().map(|s| s.success()).unwrap_or(false)
    } else {
        let mut c = Command::new("rustc");
        c.arg("-O");
        for a in extra("rustflags") {
            c.arg(a);
        }
        c.arg(&src).arg("-o").arg(&bin);
        c.status().map(|s| s.success()).unwrap_or(false)
    };
    if !ok {
        return None;
    }
    Some(vec![bin.to_string_lossy().into_owned()])
}

/// Run a guest under one backend with the portable determinism profile, capture
/// stdout, and return its SHA-256 (or None on failure/timeout).
/// How much work THIS backend uniquely performed, parsed from its own run output.
///
/// A backend that did nothing agrees with the reference perfectly. e9patch scored
/// byte-identical parity with ptrace while reporting `mapped_sites=0`: the AOT pass
/// rewrote nothing, so plain ptrace ran and the cell recorded a manufactured perfect
/// score. The count is therefore a CONDITION that must travel with the verdict --
/// a cell without it cannot be distinguished from an earned one.
///
/// Each pattern counts work only that backend does, so a nonzero value cannot be
/// produced by the shared runtime underneath:
///   e9patch  `mapped_sites`  -- main-ELF sites actually rewritten by the AOT pass
///   sabre    `patched_sites` -- call sites routed to the in-guest handler
///   dbi      `branches`      -- blocks translated by DynamoRIO
///   ptrace   `turns`         -- scheduler turns, i.e. guest stops the tracer took
///
/// `None` means NO COUNTER WAS FOUND, which is deliberately not the same as zero and
/// must never be read as engagement: liteinst and kvm expose no such counter yet, so
/// they return `None` here and their cells cannot claim to be earned until they do.
fn backend_engagement(backend: &str, text: &str) -> Option<i64> {
    let key = match backend {
        "e9patch" => "mapped_sites=",
        "sabre" => "patched_sites=",
        "dbi" => "branches=",
        "ptrace" => return parse_scheduler_turns(text),
        // liteinst and kvm publish no engagement counter today.
        _ => return None,
    };
    let start = text.find(key)? + key.len();
    let digits: String = text[start..].chars().take_while(|c| c.is_ascii_digit()).collect();
    digits.parse().ok()
}

/// PLANTED BOTH-WAYS CHECK for the engagement invariant, run with `--self-check`.
///
/// The fixtures are REAL banner text measured on this box, not invented strings, so
/// the parser is pinned against what the backends actually print rather than against
/// what this file wishes they printed. Every backend is asserted in BOTH directions:
/// an exercising run must read nonzero, a non-exercising run must read zero, and a
/// backend with no counter must read "unknown" -- which is deliberately distinct from
/// zero, because treating "I could not tell" as "it engaged" is the failure this
/// whole invariant exists to prevent.
fn self_check() -> i32 {
    // (backend, text, expected)
    let cases: &[(&str, &str, Option<i64>)] = &[
        // e9patch, the motivating case. Measured: an ordinary dynamically linked guest
        // gives the AOT pass nothing to rewrite, while a guest with syscalls in its main
        // ELF gives it two.
        ("e9patch", ":: Backend: e9patch preprocessing + ptrace runtime; candidate_sites=0; mapped_sites=0; b0_sites=0; instruction_map_cache=Hit;", Some(0)),
        ("e9patch", ":: Backend: e9patch preprocessing + ptrace runtime; candidate_sites=2; mapped_sites=2; b0_sites=0; instruction_map_cache=Miss;", Some(2)),
        // dbi
        ("dbi", "reverie-dbi: tool=Detcore branches=134328 syscalls=131 rewritten=130 stdin_reads=0 memory_hash=5262c47218cba9ee", Some(134328)),
        ("dbi", "reverie-dbi: tool=Detcore branches=0 syscalls=0 rewritten=0 stdin_reads=0 memory_hash=0", Some(0)),
        // sabre
        ("sabre", "sabre: patched_sites=7", Some(7)),
        ("sabre", "sabre: patched_sites=0", Some(0)),
        // ptrace
        ("ptrace", "Internally, the hermit scheduler ran 43 turns, recorded 0 events", Some(43)),
        ("ptrace", "Internally, the hermit scheduler ran 0 turns, recorded 0 events", Some(0)),
        // No counter published yet. MUST be None, never Some(0) and never a pass.
        ("liteinst", "hermit: [liteinst host hybrid] activation verified", None),
        ("kvm", "some kvm output with no engagement counter", None),
        // A backend that printed nothing at all cannot be scored either.
        ("e9patch", "", None),
    ];
    let mut failures = 0;
    for (backend, text, expected) in cases {
        let got = backend_engagement(backend, text);
        let verdict = match got {
            None => "unknown".to_string(),
            Some(0) => "NOT-EXERCISED".to_string(),
            Some(n) => format!("engaged({n})"),
        };
        if got != *expected {
            println!("SELF-CHECK FAIL {backend}: expected {expected:?} got {got:?}");
            failures += 1;
        } else {
            println!("self-check ok  {backend:<9} {verdict}");
        }
    }
    // The discrimination itself, stated as a property rather than left implicit.
    if backend_engagement("e9patch", "mapped_sites=0") == backend_engagement("e9patch", "mapped_sites=2") {
        println!("SELF-CHECK FAIL: parser does not discriminate zero from nonzero");
        failures += 1;
    }
    if failures == 0 {
        println!("self-check: {} cases, all discriminating", cases.len());
        0
    } else {
        println!("self-check: {failures} FAILED");
        3
    }
}

/// The name of the counter that backs each backend's engagement invariant, so the
/// reason string names the exact quantity that was zero rather than gesturing at it.
fn engagement_counter_name(backend: &str) -> &'static str {
    match backend {
        "e9patch" => "mapped_sites",
        "sabre" => "patched_sites",
        "dbi" => "branches",
        "ptrace" => "scheduler turns",
        _ => "engagement",
    }
}

/// ptrace reports its engagement as scheduler turns in the `--summary` report:
/// "Internally, the hermit scheduler ran 43 turns, ...".
fn parse_scheduler_turns(text: &str) -> Option<i64> {
    let marker = "hermit scheduler ran ";
    let start = text.find(marker)? + marker.len();
    let digits: String = text[start..].chars().take_while(|c| c.is_ascii_digit()).collect();
    digits.parse().ok()
}

fn run_and_hash(repo: &Path, lane: &str, backend: &str, guest: &[String]) -> Option<(String, Option<i64>)> {
    let hermit = hermit_bin(repo);
    let mut cmd = Command::new("timeout");
    cmd.arg("120s").arg(&hermit).arg("run").arg("--backend").arg(backend).arg("--strict");
    // Pin the GUEST environment. `--base-env` defaults to `host`, which passes every one of
    // hermit's own variables through to the guest. The kernel builds the initial stack from
    // argv/envp/auxv, so the size of that block decides where the stack sits: a collector run
    // from a shell with one extra (or longer) variable shifts every guest stack address.
    // Measured on ptrace/--strict with an otherwise identical guest, changing a single variable's
    // length moved the stack 16 bytes and changed 110 of 172 DETLOG lines — all 54 [stack] hashes
    // plus 56 INFO-log syscall arguments that carry stack addresses. Stdout parity does not see
    // this, but any stack-derived observable does, so pinning here (once, at the env) is what
    // keeps cells comparable across invocations instead of two downstream exclusions that drift.
    // `minimal` is hermit's own deterministic base: PATH, HOSTNAME, HOME and nothing else.
    // --summary makes ptrace state its own engagement count; the other backends
    // print theirs unconditionally.
    cmd.arg("--summary");
    cmd.arg("--base-env").arg("minimal");
    // Re-add the two variables this harness has always relied on for guest determinism.
    // `minimal` would otherwise drop them, and an UNSET TZ is worse than an inherited one:
    // glibc then falls back to /etc/localtime, i.e. host state. Pinned guest env is exactly:
    // PATH, HOSTNAME, HOME (hermit `minimal`) + LC_ALL=C + TZ=UTC. Nothing else reaches the guest.
    cmd.arg("-e").arg("LC_ALL=C").arg("-e").arg("TZ=UTC");
    if lane == "portable" {
        cmd.arg("--no-virtualize-cpuid").arg("--max-timeslice=disabled");
    }
    cmd.arg("--");
    for g in guest {
        cmd.arg(g);
    }
    cmd.env("LC_ALL", "C").env("TZ", "UTC").current_dir(repo);
    let out = cmd.output().ok()?;
    if !out.status.success() {
        return None;
    }
    let mut h = Sha256::new();
    h.update(&out.stdout);
    // The banner goes to stderr for some backends and stdout for others, so both are
    // searched; the count is parsed from the SAME run whose bytes are being hashed,
    // never from a separate invocation that might have engaged differently.
    let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    let engaged = backend_engagement(backend, &text);
    Some((format!("{:x}", h.finalize()), engaged))
}

/// Resolve the hermit binary the same way `ci/test_harness.sh` does:
/// `HERMIT_BIN` if set, else `<repo>/target/debug/hermit`. Keeping this
/// env-overridable lets CI point the gate at the exact binary it just built
/// (release, alternate target dir, etc.) instead of a hardcoded debug path.
fn hermit_bin(repo: &Path) -> PathBuf {
    if let Ok(p) = env::var("HERMIT_BIN") {
        if !p.is_empty() {
            return PathBuf::from(p);
        }
    }
    repo.join("target/debug/hermit")
}

fn script_dir() -> PathBuf {
    if let Ok(p) = env::var("RUST_SCRIPT_BASE_PATH") {
        return PathBuf::from(p);
    }
    env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| env::current_dir().expect("cwd"))
}
