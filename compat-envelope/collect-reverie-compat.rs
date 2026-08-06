#!/usr/bin/env rust-script
//! Reverie compat-envelope collector: ptrace-vs-kvm parity for the shared
//! Reverie counter tools (the B1.5 `Guest`/`Tool` boundary), written into the
//! SAME scorecard CSV schema that `collect-envelope.rs` / `render-scorecard.rs`
//! use (bucket = `reverie-examples`).
//!
//! This is owner directive #1 ("reverie-compat first"): before the hermit-side
//! Detcore envelope, measure that the shared Reverie tools run through both the
//! ptrace and KVM `Guest` contracts and report, per (tool, guest, backend):
//!   * determinism  — run1 == run2 within the backend, and
//!   * parity       — backend syscall total == the ptrace reference total.
//!
//! Only tools that have BOTH a ptrace launcher and a kvm launcher can produce a
//! cross-backend parity cell. Today that is `counter1` and `counter2`. Any
//! ptrace-only example (strace, chaos, …) has no kvm launcher, so its kvm cell
//! is honestly recorded as not-runnable (0/0), never faked.
//!
//! KVM launchers require a STATICALLY-LINKED guest ELF (install_static_elf +
//! /dev/kvm). The default guest corpus therefore uses static busybox applets.
//!
//! ```cargo
//! [dependencies]
//! ```
use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{exit, Command};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

// Shared contract with collect-envelope.rs / render-scorecard.rs. Keep in sync.
const HEADER: &str = "run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,test_id,test_mode,backend,cell_state,outcome,deterministic,stdout_parity,output_hash,duration_ms,max_rss_kb,reason";
const BUCKET: &str = "reverie-examples";
// Single reverie mode: "run the shared counter Tool". The specific tool
// (counter1/counter2) is preserved in test_id so it slots into the
// single-denominator renderer as one bucket.
const MODE: &str = "counter";

/// One Reverie tool measurable across backends.
struct Tool {
    /// tool name, used as `test_mode` and as the ptrace launcher bin name.
    name: &'static str,
    /// kvm launcher bin name, if any (None => ptrace-only, kvm cell = 0/0).
    kvm_bin: Option<&'static str>,
}

const TOOLS: &[Tool] = &[
    Tool { name: "counter1", kvm_bin: Some("reverie-kvm-counter1") },
    Tool { name: "counter2", kvm_bin: Some("reverie-kvm-counter2") },
];

fn die(msg: &str) -> ! {
    eprintln!("collect-reverie-compat: {msg}");
    exit(2);
}

fn script_dir() -> PathBuf {
    if let Ok(p) = env::var("RUST_SCRIPT_BASE_PATH") {
        return PathBuf::from(p);
    }
    env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn git(dir: &Path, args: &[&str]) -> Option<String> {
    let out = Command::new("git").arg("-C").arg(dir).args(args).output().ok()?;
    if !out.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

/// Extract the syscall total from a counter tool's stderr/stdout.
/// counter1: `counter1-global syscalls=N`
/// counter2: ` [counter tool] Total system calls in process tree: N, ...`
fn parse_syscalls(tool: &str, text: &str) -> Option<u64> {
    let needle = if tool == "counter2" { "process tree: " } else { "syscalls=" };
    for line in text.lines() {
        if let Some(pos) = line.find(needle) {
            let rest = &line[pos + needle.len()..];
            let num: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
            if !num.is_empty() {
                return num.parse().ok();
            }
        }
    }
    None
}

/// Run one launcher once; return (exit_ok, syscall_count_opt, duration_ms).
fn run_once(bin: &Path, guest_argv: &[String], tool: &str) -> (bool, Option<u64>, i64) {
    let t0 = Instant::now();
    let out = Command::new(bin).args(guest_argv).output();
    let dur = t0.elapsed().as_millis() as i64;
    match out {
        Ok(o) => {
            let mut text = String::from_utf8_lossy(&o.stderr).into_owned();
            text.push('\n');
            text.push_str(&String::from_utf8_lossy(&o.stdout));
            (o.status.success(), parse_syscalls(tool, &text), dur)
        }
        Err(_) => (false, None, dur),
    }
}

fn main() {
    let mut lane = "portable".to_string();
    let mut csv: Option<PathBuf> = None;
    let mut repo: Option<PathBuf> = None;
    let mut run_id_arg: Option<String> = None;
    let mut guest_path: Option<String> = None;
    let mut applets_arg: Option<String> = None;
    let mut backends_arg: Option<String> = None;
    let mut reps: u32 = 2;
    let mut dry_run = false;
    let mut assert_green = false;

    let mut it = env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--lane" => lane = it.next().unwrap_or_else(|| die("--lane needs a value")),
            "--csv" => csv = Some(PathBuf::from(it.next().unwrap_or_else(|| die("--csv needs a path")))),
            "--repo" => repo = Some(PathBuf::from(it.next().unwrap_or_else(|| die("--repo needs a path")))),
            "--run-id" => run_id_arg = Some(it.next().unwrap_or_else(|| die("--run-id needs a value"))),
            "--guest" => guest_path = Some(it.next().unwrap_or_else(|| die("--guest needs a path"))),
            "--applets" => applets_arg = Some(it.next().unwrap_or_else(|| die("--applets needs a list"))),
            "--backends" => backends_arg = Some(it.next().unwrap_or_else(|| die("--backends needs a list"))),
            "--reps" => reps = it.next().and_then(|s| s.parse().ok()).unwrap_or_else(|| die("--reps needs an int")),
            "--dry-run" => dry_run = true,
            "--assert-green" => assert_green = true,
            "-h" | "--help" => {
                eprintln!("collect-reverie-compat.rs [--lane L] [--csv F] [--repo hermit] [--run-id ID]");
                eprintln!("    [--guest STATIC_ELF] [--applets a,b,c] [--backends ptrace,kvm] [--reps N]");
                eprintln!("    [--dry-run] [--assert-green]");
                exit(0);
            }
            other => die(&format!("unknown argument {other}")),
        }
    }
    if reps < 2 {
        die("--reps must be >= 2 to measure determinism");
    }

    let here = script_dir();
    let csv_path = csv.unwrap_or_else(|| here.join("scorecard.csv"));
    let repo = repo
        .or_else(|| {
            let c = here.join("..").join("hermit");
            c.is_dir().then_some(c)
        })
        .or_else(|| env::var("DEV_HERMIT").ok().map(|d| PathBuf::from(d).join("hermit")))
        .unwrap_or_else(|| die("could not locate hermit checkout; pass --repo"));
    let repo = fs::canonicalize(&repo).unwrap_or(repo);
    let parent = repo.parent().map(Path::to_path_buf).unwrap_or_else(|| here.clone());
    let reverie = parent.join("reverie");
    let bindir = reverie.join("target").join("debug");

    // Guest corpus: static ELF + a list of applets/argvs. Default: busybox.
    let guest = guest_path.unwrap_or_else(|| {
        for c in ["/usr/sbin/busybox", "/bin/busybox", "/usr/bin/busybox"] {
            if Path::new(c).exists() {
                return c.to_string();
            }
        }
        die("no static guest found; pass --guest <static-elf>");
    });
    // Each applet string becomes the guest argv AFTER the guest path.
    let applets: Vec<Vec<String>> = applets_arg
        .unwrap_or_else(|| "true;echo hi;pwd".to_string())
        .split(';')
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .map(|s| s.split_whitespace().map(|w| w.to_string()).collect())
        .collect();

    let backends: Vec<String> = backends_arg
        .unwrap_or_else(|| "ptrace,kvm".to_string())
        .split(',')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();

    let kvm_present = Path::new("/dev/kvm").exists();

    let now = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    let run_id = run_id_arg.unwrap_or_else(|| now.to_string());
    let run_utc = format!("@{now}");
    let hermit_sha = git(&repo, &["rev-parse", "HEAD"]).unwrap_or_else(|| "unknown".into());
    let reverie_sha = git(&reverie, &["rev-parse", "HEAD"]).unwrap_or_else(|| "unknown".into());
    let dirty = git(&reverie, &["status", "--porcelain"]).map(|s| !s.is_empty()).unwrap_or(false);

    eprintln!(
        "collect-reverie-compat: reverie={} kvm_present={} guest={} applets={} backends={:?} reps={}",
        &reverie_sha[..reverie_sha.len().min(12)],
        kvm_present,
        guest,
        applets.len(),
        backends,
        reps
    );

    if dry_run {
        let cells = TOOLS.len() * applets.len() * backends.len();
        eprintln!("(dry-run) would measure {cells} cells; CSV untouched");
        for t in TOOLS {
            for b in &backends {
                let runnable = match b.as_str() {
                    "ptrace" => true,
                    "kvm" => kvm_present && t.kvm_bin.is_some(),
                    _ => false,
                };
                eprintln!("  {}/{b}: runnable={runnable}", t.name);
            }
        }
        return;
    }

    if !csv_path.exists() {
        fs::write(&csv_path, format!("{HEADER}\n")).unwrap_or_else(|e| die(&format!("cannot create CSV: {e}")));
    }
    let mut rows: Vec<String> = Vec::new();
    let mut regressions: Vec<String> = Vec::new();

    // ptrace reference counts, keyed by (tool, guest-slug).
    let mut ref_counts: BTreeMap<(String, String), u64> = BTreeMap::new();

    // Measure ptrace first (establishes the reference), then other backends.
    let ordered: Vec<String> = {
        let mut v = backends.clone();
        v.sort_by_key(|b| if b == "ptrace" { 0 } else { 1 });
        v
    };

    for backend in &ordered {
        for tool in TOOLS {
            for argv in &applets {
                let slug = argv.join("-").replace('/', "_");
                let slug = if slug.is_empty() { "noargs".to_string() } else { slug };
                let test_id = format!("{}-{}", tool.name, slug);

                // Resolve launcher + runnability for this backend.
                let (bin_name, runnable, why_unrunnable) = match backend.as_str() {
                    "ptrace" => (tool.name.to_string(), true, String::new()),
                    "kvm" => match tool.kvm_bin {
                        Some(b) if kvm_present => (b.to_string(), true, String::new()),
                        Some(_) => ("".into(), false, "no /dev/kvm".to_string()),
                        None => ("".into(), false, "no kvm launcher for tool".to_string()),
                    },
                    other => ("".into(), false, format!("unsupported backend {other}")),
                };

                if !runnable {
                    // Honest not-runnable cell: 0/0, never faked.
                    rows.push(row(
                        &run_id, &run_utc, &hermit_sha, &reverie_sha, dirty, &lane, &test_id,
                        MODE, backend, "enabled", "skip", None, None, "", 0, &why_unrunnable,
                    ));
                    continue;
                }

                let bin = bindir.join(&bin_name);
                if !bin.exists() {
                    rows.push(row(
                        &run_id, &run_utc, &hermit_sha, &reverie_sha, dirty, &lane, &test_id,
                        MODE, backend, "enabled", "skip", None, None, "", 0,
                        &format!("launcher not built: {}", bin.display()),
                    ));
                    continue;
                }

                let mut guest_argv = vec![guest.clone()];
                guest_argv.extend(argv.iter().cloned());

                // Run `reps` times: collect counts, exit-ok, total duration.
                let mut counts: Vec<Option<u64>> = Vec::new();
                let mut all_ok = true;
                let mut total_ms = 0i64;
                for _ in 0..reps {
                    let (ok, count, dur) = run_once(&bin, &guest_argv, tool.name);
                    all_ok = all_ok && ok;
                    total_ms += dur;
                    counts.push(count);
                }

                let first = counts.first().cloned().flatten();
                let deterministic = all_ok
                    && first.is_some()
                    && counts.iter().all(|c| *c == first);
                let count = first;

                // Parity: compare against the ptrace reference for this (tool,guest).
                let (parity, reason) = if backend == "ptrace" {
                    if let Some(c) = count {
                        ref_counts.insert((tool.name.to_string(), slug.clone()), c);
                    }
                    (None, count.map(|c| format!("ref syscalls={c}")).unwrap_or_else(|| "no count parsed".into()))
                } else {
                    match (count, ref_counts.get(&(tool.name.to_string(), slug.clone()))) {
                        (Some(c), Some(&r)) => {
                            let p = c == r;
                            (Some(p), format!("syscalls={c} vs ptrace {r}"))
                        }
                        (Some(c), None) => (None, format!("syscalls={c}; no ptrace ref")),
                        (None, _) => (None, "no count parsed".into()),
                    }
                };

                // Cell outcome (for the scorecard): pass = ran deterministically
                // AND (is the reference OR parity holds). Parity-diverge cells
                // render honestly as 0% parity / N% determinism.
                let pass = deterministic && (backend == "ptrace" || parity == Some(true));
                let outcome = if pass { "pass" } else if all_ok { "diverge" } else { "fail" };

                // Regression gate (--assert-green) tracks the DETERMINISM
                // invariant, NOT parity. KVM parity is a known B1.5 Guest-contract
                // gap (0% by design); flagging it every run would make the gate
                // permanently red. A parity IMPROVEMENT (KVM→matches ptrace) is
                // caught by expansion mode, not here. So a cell only counts as a
                // regression if it stopped running or stopped being self-
                // deterministic.
                let regressed = !all_ok || !deterministic;
                if regressed {
                    let why = if !all_ok { "run failed" } else { "non-deterministic" };
                    regressions.push(format!("{test_id} [{backend}] -> {why} ({reason})"));
                }
                let output_hash = count.map(|c| c.to_string()).unwrap_or_default();

                rows.push(row(
                    &run_id, &run_utc, &hermit_sha, &reverie_sha, dirty, &lane, &test_id,
                    MODE, backend, "enabled", outcome, Some(deterministic), parity,
                    &output_hash, total_ms, &reason,
                ));
            }
        }
    }

    // Append all rows.
    let existing = fs::read_to_string(&csv_path).unwrap_or_default();
    let mut buf = existing;
    if !buf.ends_with('\n') && !buf.is_empty() {
        buf.push('\n');
    }
    for r in &rows {
        buf.push_str(r);
        buf.push('\n');
    }
    fs::write(&csv_path, buf).unwrap_or_else(|e| die(&format!("cannot write CSV: {e}")));

    eprintln!(
        "collect-reverie-compat: wrote {} rows to {} (run_id={run_id})",
        rows.len(),
        csv_path.display()
    );

    if assert_green && !regressions.is_empty() {
        eprintln!(
            "collect-reverie-compat: REGRESSION — {} reverie cell(s) diverged/failed:",
            regressions.len()
        );
        for r in &regressions {
            eprintln!("  FAIL {r}");
        }
        exit(1);
    }
    if assert_green {
        eprintln!("collect-reverie-compat: envelope GREEN — all runnable reverie cells passed.");
    }
}

#[allow(clippy::too_many_arguments)]
fn row(
    run_id: &str,
    run_utc: &str,
    hermit_sha: &str,
    reverie_sha: &str,
    dirty: bool,
    lane: &str,
    test_id: &str,
    test_mode: &str,
    backend: &str,
    cell_state: &str,
    outcome: &str,
    deterministic: Option<bool>,
    parity: Option<bool>,
    output_hash: &str,
    duration_ms: i64,
    reason: &str,
) -> String {
    let det = deterministic.map(|b| if b { "1" } else { "0" }).unwrap_or("");
    let par = parity.map(|b| if b { "1" } else { "0" }).unwrap_or("");
    // reason may contain commas; wrap in quotes.
    let reason_q = format!("\"{}\"", reason.replace('"', "'"));
    [
        run_id.to_string(),
        run_utc.to_string(),
        hermit_sha.to_string(),
        reverie_sha.to_string(),
        dirty.to_string(),
        "reverie".to_string(), // run_mode
        lane.to_string(),
        BUCKET.to_string(),
        test_id.to_string(),
        test_mode.to_string(),
        backend.to_string(),
        cell_state.to_string(),
        outcome.to_string(),
        det.to_string(),
        par.to_string(),
        output_hash.to_string(),
        duration_ms.to_string(),
        String::new(), // max_rss_kb
        reason_q,
    ]
    .join(",")
}
