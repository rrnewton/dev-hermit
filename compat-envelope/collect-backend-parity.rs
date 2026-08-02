#!/usr/bin/env rust-script
//! Additive scorecard producer for the Hermit backend-parity matrix.
//!
//! The compat-envelope scorecard (collect-envelope.rs) drives the e2e corpus
//! (hermit/ci/test_harness.sh) and knows nothing about the cross-backend parity
//! contracts in `hermit/tests/backend-parity/`. Those contracts are their OWN
//! CI-enforced ratchet (run_matrix.py, portable.json), so their green cells never
//! reach scorecard.csv on their own.
//!
//! This script converts the checked-in `matrix.tsv` (the authoritative 11-column
//! L1+L2 ratchet) into rows in the EXACT scorecard CSV schema, tagged with the
//! `backend-parity` bucket, so the scorecard owner (agent hermit-235) can ingest
//! them with a plain concat -- no change to their three collectors and no write
//! to scorecard.csv from here. It is intentionally read-only over the matrix and
//! append-only over its own output file.
//!
//! Each matrix row yields up to six scorecard rows: {ptrace,dbi,kvm} x {L1,L2}.
//! L1 -> test_mode "strict" (hermit run --strict, 3x byte-identical stdout).
//! L2 -> test_mode "verify" (hermit run --strict --verify). The L2 KVM cells are
//! guest-visible only (stdout+exit compared, internal trace NOT compared); that
//! weaker assurance is recorded verbatim in the reason column so the scorecard
//! never overstates KVM determinism (#152 anti-fakery).
//!
//! Usage:
//!   ./collect-backend-parity.rs [--matrix PATH] [--repo PATH] [--run-id ID]
//!                               [--csv PATH] [--stdout]
//! Defaults: --matrix ../hermit/tests/backend-parity/matrix.tsv,
//!           --repo   ../hermit,
//!           --run-id backend-parity-matrix,
//!           --csv    ignored/backend-parity-scorecard.csv
//!
//! To fold the result into the master scorecard (owner action, hermit-235):
//!   tail -n +2 ignored/backend-parity-scorecard.csv >> scorecard.csv

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

const HEADER: &str = "run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,test_id,test_mode,backend,cell_state,outcome,deterministic,parity,output_hash,duration_ms,max_rss_kb,reason";
const BUCKET: &str = "backend-parity";
const BACKENDS: [&str; 3] = ["ptrace", "dbi", "kvm"];

fn die(msg: &str) -> ! {
    eprintln!("collect-backend-parity: {msg}");
    std::process::exit(1);
}

fn csv_field(s: &str) -> String {
    if s.contains(',') || s.contains('"') || s.contains('\n') {
        format!("\"{}\"", s.replace('"', "\"\""))
    } else {
        s.to_string()
    }
}

fn git(repo: &Path, args: &[&str]) -> Option<String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

fn main() {
    let mut matrix = PathBuf::from("../hermit/tests/backend-parity/matrix.tsv");
    let mut repo = PathBuf::from("../hermit");
    let mut run_id = String::from("backend-parity-matrix");
    let mut csv = PathBuf::from("ignored/backend-parity-scorecard.csv");
    let mut to_stdout = false;

    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--matrix" => matrix = PathBuf::from(it.next().unwrap_or_else(|| die("--matrix needs a path"))),
            "--repo" => repo = PathBuf::from(it.next().unwrap_or_else(|| die("--repo needs a path"))),
            "--run-id" => run_id = it.next().unwrap_or_else(|| die("--run-id needs a value")),
            "--csv" => csv = PathBuf::from(it.next().unwrap_or_else(|| die("--csv needs a path"))),
            "--stdout" => to_stdout = true,
            "-h" | "--help" => {
                println!("usage: collect-backend-parity.rs [--matrix P] [--repo P] [--run-id ID] [--csv P] [--stdout]");
                return;
            }
            other => die(&format!("unknown argument: {other}")),
        }
    }

    let text = std::fs::read_to_string(&matrix)
        .unwrap_or_else(|e| die(&format!("cannot read matrix {}: {e}", matrix.display())));

    // Metadata, mirroring collect-envelope.rs conventions.
    let hermit_sha = git(&repo, &["rev-parse", "HEAD"]).unwrap_or_else(|| "unknown".to_string());
    let reverie_sha = "unknown".to_string();
    let dirty = git(&repo, &["status", "--porcelain"])
        .map(|s| !s.is_empty())
        .unwrap_or(false);
    let run_utc = format!(
        "@{}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0)
    );

    let mut lines = String::new();
    let mut wrote = 0usize;
    let mut header_cols: Vec<String> = Vec::new();

    for (lineno, raw) in text.lines().enumerate() {
        if raw.trim().is_empty() {
            continue;
        }
        let cols: Vec<&str> = raw.split('\t').collect();
        if lineno == 0 {
            header_cols = cols.iter().map(|s| s.to_string()).collect();
            // Guard against a future schema drift so we never emit garbage.
            let expect = [
                "test_name", "ptrace", "dbi", "kvm", "dbi_reason", "kvm_reason",
                "ptrace_l2", "dbi_l2", "kvm_l2", "dbi_l2_reason", "kvm_l2_reason",
            ];
            if header_cols != expect {
                die(&format!(
                    "matrix schema changed: expected {expect:?}, got {header_cols:?}. \
                     Update this converter before trusting its output."
                ));
            }
            continue;
        }
        if cols.len() != header_cols.len() {
            die(&format!(
                "row {lineno} has {} fields, expected {}",
                cols.len(),
                header_cols.len()
            ));
        }
        let test_name = cols[0];
        let test_id = format!("{BUCKET}/{test_name}");

        for backend in BACKENDS {
            // Column indices per the 11-column schema.
            let (l1_idx, l1_reason_idx, l2_idx, l2_reason_idx) = match backend {
                "ptrace" => (1usize, None, 6usize, None),
                "dbi" => (2, Some(4usize), 7, Some(9usize)),
                "kvm" => (3, Some(5usize), 8, Some(10usize)),
                _ => unreachable!(),
            };

            // kvm cells require /dev/kvm -> privileged lane; ptrace/dbi are portable.
            let lane = if backend == "kvm" { "privileged" } else { "portable" };

            // ---- L1 (strict) row ----
            let l1 = cols[l1_idx];
            let l1_pass = l1 == "pass";
            let l1_reason = l1_reason_idx
                .map(|i| cols[i])
                .filter(|r| *r != "-" && !r.is_empty())
                .unwrap_or("")
                .to_string();
            let l1_reason = if l1_pass { String::new() } else { l1_reason };
            push_row(
                &mut lines,
                &run_id, &run_utc, &hermit_sha, &reverie_sha, dirty,
                lane, &test_id, "strict", backend,
                if l1_pass { "pass" } else { "gap" },
                l1_pass, l1_pass, &l1_reason,
            );
            wrote += 1;

            // ---- L2 (verify) row ----
            let l2 = cols[l2_idx]; // detlog | guest | gap
            let l2_pass = l2 != "gap";
            let mut l2_reason = l2_reason_idx
                .map(|i| cols[i])
                .filter(|r| *r != "-" && !r.is_empty())
                .unwrap_or("")
                .to_string();
            // Preserve the L2 assurance KIND so KVM's weaker guest-visible L2 is
            // never presented as full DETLOG determinism (#152).
            if l2_pass && l2_reason.is_empty() {
                l2_reason = match l2 {
                    "detlog" => "L2 DETLOG-bitwise (--verify double-run matched)".to_string(),
                    "guest" => "L2 guest-visible only (stdout+exit compared, internal trace not compared)".to_string(),
                    _ => String::new(),
                };
            }
            // For KVM guest-visible L2, parity vs ptrace is guest-visible, not
            // DETLOG; mark deterministic=true (verified repeatable) but keep the
            // caveat in the reason. ptrace/dbi detlog rows are full parity.
            push_row(
                &mut lines,
                &run_id, &run_utc, &hermit_sha, &reverie_sha, dirty,
                lane, &test_id, "verify", backend,
                if l2_pass { "pass" } else { "gap" },
                l2_pass, l2_pass, &l2_reason,
            );
            wrote += 1;
        }
    }

    if to_stdout {
        println!("{HEADER}");
        print!("{lines}");
        eprintln!("collect-backend-parity: {wrote} rows to stdout");
        return;
    }

    // Append-only into a fresh file (write header once).
    if let Some(parent) = csv.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let body = format!("{HEADER}\n{lines}");
    std::fs::write(&csv, body)
        .unwrap_or_else(|e| die(&format!("cannot write {}: {e}", csv.display())));
    eprintln!(
        "collect-backend-parity: wrote {wrote} rows ({BUCKET} bucket) to {} \
         [hermit {hermit_sha} dirty={dirty}]",
        csv.display()
    );
    eprintln!(
        "To fold into the master scorecard (owner action): \
         tail -n +2 {} >> scorecard.csv",
        csv.display()
    );
}

#[allow(clippy::too_many_arguments)]
fn push_row(
    out: &mut String,
    run_id: &str,
    run_utc: &str,
    hermit_sha: &str,
    reverie_sha: &str,
    dirty: bool,
    lane: &str,
    test_id: &str,
    test_mode: &str,
    backend: &str,
    outcome: &str,
    deterministic: bool,
    parity: bool,
    reason: &str,
) {
    let row = [
        run_id.to_string(),
        run_utc.to_string(),
        hermit_sha.to_string(),
        reverie_sha.to_string(),
        dirty.to_string(),
        "regression".to_string(),
        lane.to_string(),
        BUCKET.to_string(),
        test_id.to_string(),
        test_mode.to_string(),
        backend.to_string(),
        "enabled".to_string(),
        outcome.to_string(),
        if deterministic { "1" } else { "" }.to_string(),
        if parity { "1" } else { "0" }.to_string(),
        String::new(),        // output_hash: not stored in matrix.tsv
        String::new(),        // duration_ms: static ratchet claim, unmeasured here
        String::new(),        // max_rss_kb
        reason.to_string(),
    ];
    let line = row.iter().map(|f| csv_field(f)).collect::<Vec<_>>().join(",");
    out.push_str(&line);
    out.push('\n');
}
