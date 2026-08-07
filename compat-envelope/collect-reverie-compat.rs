#!/usr/bin/env rust-script
//! Reverie compat-envelope collector: cross-backend parity for the shared
//! Reverie counter tools (the B1.5 `Guest`/`Tool` boundary), written into the
//! SAME scorecard CSV schema that `collect-envelope.rs` / `render-scorecard.rs`
//! use (bucket = `reverie-examples`).
//!
//! This is owner directive #1 ("reverie-compat first"): before the hermit-side
//! Detcore envelope, measure that the shared Reverie tools run through each
//! backend's `Guest` contract and report, per (tool, guest, backend):
//!   * determinism  — run1 == run2 within the backend, and
//!   * parity       — backend syscall total == the ptrace reference total.
//!
//! # This dataset is DISJOINT from the hermit Detcore envelope
//!
//! These rows are NOT a subset, slice, or re-render of the e2e manifest corpus
//! that `collect-envelope.rs` measures. Different boundary (Reverie
//! `Guest`/`Tool` callbacks, not Detcore program compat), different corpus
//! (synthetic busybox applets, not `hermit/tests/e2e/manifests/*.toml`),
//! different denominator. The intersection is empty on both the bucket axis and
//! the test_id axis. **Never sum the two totals or express one as a percentage
//! of the other.**
//!
//! # Every known backend gets a row — silence is not evidence
//!
//! Previously this collector hardcoded `ptrace,kvm` and emitted NOTHING for any
//! other backend. A reader could not distinguish "DBT was never asked to run"
//! from "DBT ran and failed", so the blank cells read as backend failures. They
//! were not.
//!
//! Now every (tool, guest, backend) cell in `KNOWN_BACKENDS` produces exactly one
//! row on every run, and a cell that did not produce a measurement carries a
//! typed `absence_reason`:
//!
//!   * `not_collected` — backend is known and this tool supports it, but it was
//!     not in the requested `--backends` set. NOT a failure; nobody asked.
//!   * `unsupported`   — this tool has no launcher for that backend (structural),
//!     or the backend name is unknown to the collector.
//!   * `unavailable`   — host/artifact gate unmet: no `/dev/kvm`, or the launcher
//!     binary is not built in this checkout.
//!   * `no_result`     — the launcher RAN but emitted no parseable syscall count.
//!
//! An EMPTY `absence_reason` means the cell was genuinely measured. There are no
//! blank-and-ambiguous cells. Nothing is ever faked.
//!
//! # Terminology
//!
//! The canonical backend name emitted is **`dbt`** (dynamic binary translation).
//! The legacy name `dbi` is accepted on input for compatibility (`--backends dbi`
//! normalizes to `dbt`) and is reported as an alias, never re-emitted.
//!
//! KVM launchers require a STATICALLY-LINKED guest ELF (install_static_elf +
//! /dev/kvm). The default guest corpus therefore uses static busybox applets.
//!
//! # Idempotent regeneration
//!
//! Re-running replaces this bucket's rows rather than appending to them, so the
//! CSV converges instead of growing duplicates. With `--run-id` and `--run-utc`
//! pinned, two runs over the same inputs produce a BYTE-IDENTICAL file.
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
// `absence_reason` is APPENDED at the end: readers resolve columns by header
// name, so trailing additions are backward compatible with the 19-column form.
const HEADER: &str = "run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,test_id,test_mode,backend,cell_state,outcome,deterministic,parity,output_hash,duration_ms,max_rss_kb,reason,verify_compare,bitwise_parity,compared_log_messages,tier,absence_reason";
const BUCKET: &str = "reverie-examples";
// Single reverie mode: "run the shared counter Tool". The specific tool
// (counter1/counter2) is preserved in test_id so it slots into the
// single-denominator renderer as one bucket.
const MODE: &str = "counter";

/// Canonical backend vocabulary for this collector, in reference-first order.
/// `ptrace` MUST stay first: it establishes the parity reference.
const KNOWN_BACKENDS: &[&str] = &["ptrace", "kvm", "dbt", "sabre", "liteinst"];

/// Typed reasons a cell produced no measurement. Empty string == measured.
const ABSENCE_NOT_COLLECTED: &str = "not_collected";
const ABSENCE_UNSUPPORTED: &str = "unsupported";
const ABSENCE_UNAVAILABLE: &str = "unavailable";
const ABSENCE_NO_RESULT: &str = "no_result";

/// Map a user-supplied backend name onto the canonical vocabulary.
/// Legacy `dbi` reads as `dbt`; everything else passes through unchanged so an
/// unknown name still reaches the `unsupported` path instead of being silently
/// dropped.
fn canonical_backend(name: &str) -> String {
    match name {
        "dbi" => "dbt".to_string(),
        other => other.to_string(),
    }
}

/// One Reverie tool measurable across backends.
struct Tool {
    /// tool name, used as `test_mode` and as the ptrace launcher bin name.
    name: &'static str,
    /// Per-backend launcher binaries: `(canonical_backend, bin_name)`.
    /// A backend absent from this list is structurally `unsupported` for this
    /// tool. This replaces the old single `kvm_bin` field, which made every
    /// non-KVM backend literally unnameable.
    launchers: &'static [(&'static str, &'static str)],
}

const TOOLS: &[Tool] = &[
    Tool {
        name: "counter1",
        launchers: &[
            ("ptrace", "counter1"),
            ("kvm", "reverie-kvm-counter1"),
            // DBT / SaBRe / LiteInst launchers are not built by the reverie
            // examples today. Listing them here (once they exist) is the ONLY
            // change needed to start measuring them; until then these cells
            // report `unsupported`, which is a launcher gap, NOT a backend fault.
        ],
    },
    Tool {
        name: "counter2",
        launchers: &[("ptrace", "counter2"), ("kvm", "reverie-kvm-counter2")],
    },
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

/// Why this (tool, backend) cell cannot be measured right now, if it cannot.
/// Returns `(launcher_bin, absence_reason, human_detail)`; an empty
/// `absence_reason` means the cell is runnable.
fn resolve_cell(
    tool: &Tool,
    backend: &str,
    requested: &[String],
    kvm_present: bool,
) -> (String, &'static str, String) {
    if !KNOWN_BACKENDS.contains(&backend) {
        return ("".into(), ABSENCE_UNSUPPORTED, format!("unknown backend {backend}"));
    }
    let bin = tool.launchers.iter().find(|(b, _)| *b == backend).map(|(_, n)| *n);
    let Some(bin) = bin else {
        return (
            "".into(),
            ABSENCE_UNSUPPORTED,
            format!("no {backend} launcher for tool {}", tool.name),
        );
    };
    if !requested.iter().any(|r| r == backend) {
        return (
            bin.to_string(),
            ABSENCE_NOT_COLLECTED,
            format!("{backend} not in requested --backends set"),
        );
    }
    if backend == "kvm" && !kvm_present {
        return (bin.to_string(), ABSENCE_UNAVAILABLE, "no /dev/kvm".to_string());
    }
    (bin.to_string(), "", String::new())
}

fn main() {
    let mut lane = "portable".to_string();
    let mut csv: Option<PathBuf> = None;
    let mut repo: Option<PathBuf> = None;
    let mut run_id_arg: Option<String> = None;
    let mut run_utc_arg: Option<String> = None;
    let mut guest_path: Option<String> = None;
    let mut applets_arg: Option<String> = None;
    let mut backends_arg: Option<String> = None;
    let mut reps: u32 = 2;
    let mut dry_run = false;
    let mut assert_green = false;
    let mut append = false;

    let mut it = env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--lane" => lane = it.next().unwrap_or_else(|| die("--lane needs a value")),
            "--csv" => csv = Some(PathBuf::from(it.next().unwrap_or_else(|| die("--csv needs a path")))),
            "--repo" => repo = Some(PathBuf::from(it.next().unwrap_or_else(|| die("--repo needs a path")))),
            "--run-id" => run_id_arg = Some(it.next().unwrap_or_else(|| die("--run-id needs a value"))),
            "--run-utc" => run_utc_arg = Some(it.next().unwrap_or_else(|| die("--run-utc needs a value"))),
            "--guest" => guest_path = Some(it.next().unwrap_or_else(|| die("--guest needs a path"))),
            "--applets" => applets_arg = Some(it.next().unwrap_or_else(|| die("--applets needs a list"))),
            "--backends" => backends_arg = Some(it.next().unwrap_or_else(|| die("--backends needs a list"))),
            "--reps" => reps = it.next().and_then(|s| s.parse().ok()).unwrap_or_else(|| die("--reps needs an int")),
            "--dry-run" => dry_run = true,
            "--assert-green" => assert_green = true,
            "--append" => append = true,
            "-h" | "--help" => {
                eprintln!("collect-reverie-compat.rs [--lane L] [--csv F] [--repo hermit] [--run-id ID]");
                eprintln!("    [--run-utc @EPOCH] [--guest STATIC_ELF] [--applets 'a;b;c']");
                eprintln!("    [--backends ptrace,kvm,dbt,sabre,liteinst] [--reps N]");
                eprintln!("    [--dry-run] [--assert-green] [--append]");
                eprintln!();
                eprintln!("Emits ONE row per (tool, guest, backend) for every backend in:");
                eprintln!("    {}", KNOWN_BACKENDS.join(", "));
                eprintln!("Cells that produced no measurement carry a typed absence_reason:");
                eprintln!("    not_collected | unsupported | unavailable | no_result");
                eprintln!("Legacy `dbi` is accepted on input and normalized to `dbt`.");
                eprintln!("Default REPLACES this bucket's rows (idempotent); --append restores");
                eprintln!("the old accumulating behaviour.");
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

    // Requested set. Default: every known backend — asking for everything is
    // what makes an absence meaningful. Legacy `dbi` normalizes to `dbt`.
    let requested_raw = backends_arg.unwrap_or_else(|| KNOWN_BACKENDS.join(","));
    let mut aliased: Vec<String> = Vec::new();
    let requested: Vec<String> = requested_raw
        .split(',')
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .map(|s| {
            let c = canonical_backend(s);
            if c != s {
                aliased.push(format!("{s}->{c}"));
            }
            c
        })
        .collect();
    for a in &aliased {
        eprintln!("collect-reverie-compat: legacy backend name {a} (canonical vocabulary is `dbt`)");
    }
    for r in &requested {
        if !KNOWN_BACKENDS.contains(&r.as_str()) {
            eprintln!(
                "collect-reverie-compat: requested backend `{r}` is not in the known vocabulary \
                 ({}); its cells will be recorded as {ABSENCE_UNSUPPORTED}",
                KNOWN_BACKENDS.join(",")
            );
        }
    }

    let kvm_present = Path::new("/dev/kvm").exists();

    let now = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    let run_id = run_id_arg.unwrap_or_else(|| now.to_string());
    let run_utc = run_utc_arg.unwrap_or_else(|| format!("@{now}"));
    let hermit_sha = git(&repo, &["rev-parse", "HEAD"]).unwrap_or_else(|| "unknown".into());
    let reverie_sha = git(&reverie, &["rev-parse", "HEAD"]).unwrap_or_else(|| "unknown".into());
    let dirty = git(&reverie, &["status", "--porcelain"]).map(|s| !s.is_empty()).unwrap_or(false);

    eprintln!(
        "collect-reverie-compat: reverie={} kvm_present={} guest={} applets={} known={:?} requested={:?} reps={}",
        &reverie_sha[..reverie_sha.len().min(12)],
        kvm_present,
        guest,
        applets.len(),
        KNOWN_BACKENDS,
        requested,
        reps
    );

    if dry_run {
        let cells = TOOLS.len() * applets.len() * KNOWN_BACKENDS.len();
        eprintln!("(dry-run) would emit {cells} rows (every tool x guest x KNOWN backend); CSV untouched");
        for t in TOOLS {
            for b in KNOWN_BACKENDS {
                let (_, absence, detail) = resolve_cell(t, b, &requested, kvm_present);
                if absence.is_empty() {
                    eprintln!("  {}/{b}: runnable", t.name);
                } else {
                    eprintln!("  {}/{b}: {absence} ({detail})", t.name);
                }
            }
        }
        return;
    }

    let mut rows: Vec<String> = Vec::new();
    let mut regressions: Vec<String> = Vec::new();

    // ptrace reference counts, keyed by (tool, guest-slug).
    let mut ref_counts: BTreeMap<(String, String), u64> = BTreeMap::new();

    // Iterate the KNOWN vocabulary, not the requested set: a backend nobody
    // asked for still gets a `not_collected` row, so its absence is visible.
    // ptrace is first in KNOWN_BACKENDS, which establishes the reference before
    // any comparison runs.
    for backend in KNOWN_BACKENDS {
        for tool in TOOLS {
            for argv in &applets {
                let slug = argv.join("-").replace('/', "_");
                let slug = if slug.is_empty() { "noargs".to_string() } else { slug };
                let test_id = format!("{}-{}", tool.name, slug);

                let (bin_name, absence, detail) = resolve_cell(tool, backend, &requested, kvm_present);

                if !absence.is_empty() {
                    // Typed non-measurement. Never faked, never blank.
                    rows.push(row(
                        &run_id, &run_utc, &hermit_sha, &reverie_sha, dirty, &lane, &test_id,
                        MODE, backend, "enabled", "skip", None, None, "", 0, &detail, absence, reps,
                    ));
                    continue;
                }

                let bin = bindir.join(&bin_name);
                if !bin.exists() {
                    rows.push(row(
                        &run_id, &run_utc, &hermit_sha, &reverie_sha, dirty, &lane, &test_id,
                        MODE, backend, "enabled", "skip", None, None, "", 0,
                        &format!("launcher not built: {}", bin.display()),
                        ABSENCE_UNAVAILABLE, reps,
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
                let deterministic = all_ok && first.is_some() && counts.iter().all(|c| *c == first);
                let count = first;

                // Parity: compare against the ptrace reference for this (tool,guest).
                let (parity, reason) = if *backend == "ptrace" {
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

                // A launcher that ran but yielded no parseable count is a
                // no_result, not a pass and not a failure of the backend.
                let absence_after_run = if count.is_none() { ABSENCE_NO_RESULT } else { "" };

                // Cell outcome (for the scorecard): pass = ran deterministically
                // AND (is the reference OR parity holds). Parity-diverge cells
                // render honestly as 0% parity / N% determinism.
                let pass = deterministic && (*backend == "ptrace" || parity == Some(true));
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
                    &output_hash, total_ms, &reason, absence_after_run, reps,
                ));
            }
        }
    }

    // Idempotent write: drop this bucket's previous rows, then write ours. A
    // re-run therefore CONVERGES instead of accumulating duplicates. `--append`
    // restores the old behaviour for callers that deliberately want history.
    let existing = fs::read_to_string(&csv_path).unwrap_or_default();
    let mut buf = String::new();
    let mut kept = 0usize;
    let mut dropped = 0usize;
    if existing.trim().is_empty() {
        buf.push_str(HEADER);
        buf.push('\n');
    } else {
        for (i, line) in existing.lines().enumerate() {
            if i == 0 {
                // Preserve a wider existing header; only widen a narrower one.
                let hdr = if line.split(',').count() >= HEADER.split(',').count() { line } else { HEADER };
                buf.push_str(hdr);
                buf.push('\n');
                continue;
            }
            if line.trim().is_empty() {
                continue;
            }
            let is_ours = line.split(',').nth(7).map(|b| b == BUCKET).unwrap_or(false);
            if is_ours && !append {
                dropped += 1;
                continue;
            }
            kept += 1;
            buf.push_str(line);
            buf.push('\n');
        }
    }
    for r in &rows {
        buf.push_str(r);
        buf.push('\n');
    }
    fs::write(&csv_path, buf).unwrap_or_else(|e| die(&format!("cannot write CSV: {e}")));

    let measured = rows.iter().filter(|r| r.rsplit(',').next() == Some("")).count();
    eprintln!(
        "collect-reverie-compat: wrote {} rows to {} (run_id={run_id}); {measured} measured, {} typed-absent; \
         kept {kept} foreign row(s), replaced {dropped} prior `{BUCKET}` row(s)",
        rows.len(),
        csv_path.display(),
        rows.len() - measured,
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
    absence_reason: &str,
    reps: u32,
) -> String {
    let det = deterministic.map(|b| if b { "1" } else { "0" }).unwrap_or("");
    // WHAT THIS COLLECTOR ACTUALLY COMPARED, carried with the verdict.
    //
    // `counter` mode runs each cell `reps` times and asserts the syscall counter is
    // identical across them. That IS a genuine multi-run comparison -- it is not a
    // single run claiming determinism -- but it is the WEAKEST rung: it compares one
    // integer, not stdout and not a log. Naming it keeps it from ever being read as
    // a DETLOG or even a guest-visible claim.
    let (verify_compare, tier, compared) = if deterministic.is_some() {
        ("syscall-count-across-reps", "counter", format!("{reps}|{reps}"))
    } else {
        ("", "", String::new())
    };
    let bitwise_parity = if deterministic.is_some() { "0" } else { "" };
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
        verify_compare.to_string(),
        bitwise_parity.to_string(),
        compared,
        tier.to_string(),
        absence_reason.to_string(),
    ]
    .join(",")
}
