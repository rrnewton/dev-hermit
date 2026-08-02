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
//!   * determinism = the cell passed under its own backend (for `verify`, hermit's
//!     internal --strict --verify double-run already proved run1==run2, so a
//!     passing verify cell is deterministic by construction);
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

const HEADER: &str = "run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,test_id,test_mode,backend,cell_state,outcome,deterministic,parity,output_hash,duration_ms,max_rss_kb,reason";

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
    let mut avail: BTreeMap<String, bool> = BTreeMap::new();
    for (_, backend) in groups.keys() {
        avail.entry(backend.clone()).or_insert_with(|| backend_available(&repo, backend));
    }
    for (b, ok) in &avail {
        if !ok {
            eprintln!("collect-envelope: backend `{b}` UNAVAILABLE here — cells recorded as unavailable (not fail)");
        }
    }

    let mut rows_written = 0usize;
    let mut regressions: Vec<String> = Vec::new();
    for ((bucket, backend), group) in &groups {
        let available = *avail.get(backend).unwrap_or(&true);
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
                ("unavailable".to_string(), 0i64, "backend binary not present in this checkout".to_string())
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
            // Determinism is BLANK (unmeasured) for an unavailable backend or a
            // skipped cell; a run that completed non-pass is confirmed false.
            let deterministic = if !available || outcome == "skip" {
                None
            } else if pass {
                Some(true)
            } else {
                Some(false)
            };
            let (parity, output_hash) = if with_parity && pass {
                capture_parity(&repo, &lane, c, backend)
            } else {
                (None, String::new())
            };
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
                output_hash,
                duration_ms.to_string(),
                String::new(), // max_rss_kb: filled by expansion-dag.rs cgroup path
                reason,
            ];
            let line = row.iter().map(|f| csv_field(f)).collect::<Vec<_>>().join(",");
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
fn capture_parity(repo: &Path, lane: &str, cell: &PlanCell, backend: &str) -> (Option<bool>, String) {
    let Some(guest) = guest_command(repo, &cell.test) else {
        return (None, String::new());
    };
    let ref_hash = run_and_hash(repo, lane, "ptrace", &guest);
    let this_hash = run_and_hash(repo, lane, backend, &guest);
    match (ref_hash, this_hash.clone()) {
        (Some(r), Some(t)) => (Some(r == t), t),
        _ => (None, this_hash.unwrap_or_default()),
    }
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
fn backend_available(repo: &Path, backend: &str) -> bool {
    if backend == "ptrace" || backend == "kvm" || backend == "native" {
        return true;
    }
    let hermit = hermit_bin(repo);
    if !hermit.exists() {
        return false;
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
            // Explicit unavailability markers from the backend loader.
            !(err.contains("is unavailable")
                || err.contains("was not found")
                || err.contains("HERMIT_SABRE_BINARY")
                || err.contains("not found in the Hermit installation"))
        }
        Err(_) => false,
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
fn run_and_hash(repo: &Path, lane: &str, backend: &str, guest: &[String]) -> Option<String> {
    let hermit = hermit_bin(repo);
    let mut cmd = Command::new("timeout");
    cmd.arg("120s").arg(&hermit).arg("run").arg("--backend").arg(backend).arg("--strict");
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
    Some(format!("{:x}", h.finalize()))
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
