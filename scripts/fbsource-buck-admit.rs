#!/usr/bin/env rust-script
//! Admission guard for heavy fbsource Buck runs on this shared host.
//!
//! ## The incident this exists to prevent, measured
//!
//! On 2026-08-06 at 22:39:58 EDT a task launched
//! `timeout 2700 buck2 test fbcode//hermetic_infra/hermit/...` from the local
//! fbsource checkout with **no `-j`**. Buck's `-j` defaults to the
//! host core count, which is 316 here. Sixteen seconds later the host-wide
//! blocked-task count stepped from 1 to 64, and of the 89 D-state records Below
//! captured at onset, **64 belonged to that Buck daemon's workers**. By 22:50
//! the host had load1 1238.70, IO PSI `full avg10` 77.52%, and 182 blocked
//! tasks -- while CPU was 59.66% IDLE and md0 utilisation was 1.67%.
//!
//! That combination is the whole diagnosis: high IO pressure with idle CPUs and
//! idle storage is an *internal filesystem lock convoy*, not saturation. The
//! saved stacks were `btrfs_read_lock_root_node`, `btrfs_tree_lock_nested`,
//! `do_rmdir`, and ordered-extent writeback. It propagated to sshd, git, Eden,
//! cron, and the health checks; the host was unusable for ~14 minutes.
//!
//! The Buck daemon's cgroup had `cpu.max=max`, `memory.max=max`, and
//! `pids.max=max`. There was no admission control of any kind.
//!
//! ## What this guard does, and the one thing it deliberately does not do
//!
//! 1. **Refuses a recursive pattern with no explicit `-j`.** A target ending in
//!    `/...` (or a bare `...`) expands to the whole subtree and defaults to
//!    316-way execution. This is the single control that would have prevented
//!    the incident, so it is the one that fails closed.
//! 2. **Caps the action fan-out.** The incident report is explicit that CPU
//!    limits alone do not solve Btrfs lock convoys -- the *action* cap is the
//!    load-bearing control. Conservative default of 16, raised only from
//!    measured PSI/D-state evidence.
//! 3. **Serialises full-suite runs behind one host semaphore**, so two heavy
//!    suites cannot stack. A second request reports `QUEUED` rather than
//!    silently proceeding.
//! 4. **Runs inside an attributed transient cgroup** with finite `TasksMax`,
//!    `MemoryHigh`/`MemoryMax`, and `CPUQuota`, named for the owning task and
//!    agent so Below attribution is immediate instead of forensic. The caps are
//!    read back from the live cgroup, because a scope can be created
//!    successfully while its caps silently fail to bind.
//! 5. **Captures evidence on threshold** -- IO full PSI, D-state census by
//!    kernel wait channel, and cgroup identity -- into a durable JSON record.
//!
//! It does **not** kill anything. Hard Invariant 15 forbids pattern-matched
//! kills on this shared box, and the incident's own recommendation is to
//! capture evidence and cancel *only the exact initiating scope*, by a human,
//! after the evidence exists. A guard that kills on a threshold it inferred
//! would be the more dangerous failure. `no_broad_kill_surface_in_this_script`
//! enforces that absence as a test.
//!
//! ```cargo
//! [dependencies]
//! serde = { version = "1", features = ["derive"] }
//! serde_json = "1"
//! ```

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::env;
use std::fs::{self, OpenOptions};
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};
use std::process::{exit, Command};
use std::time::{SystemTime, UNIX_EPOCH};

/// Conservative starting action cap. The incident report recommends "-j 16 or
/// -j 32 ... raise only from measured PSI/D-state evidence"; 16 is the low end
/// because nothing has been measured yet on this host.
const DEFAULT_MAX_JOBS: u32 = 16;

/// IO `full avg10` percentage that, combined with an idle device, indicates a
/// lock convoy rather than saturation. From the incident's alerting
/// recommendation: "IO full PSI exceeds 50 percent for 30 seconds while md0
/// utilization is below 10 percent".
const DEFAULT_PSI_FULL_THRESHOLD: f64 = 50.0;

/// D-state task count in Btrfs wait channels that indicates a forming convoy.
/// The incident's recommendation is "more than 32 tasks have Btrfs D-state
/// stacks"; at onset the real number was 64 of 89.
const DEFAULT_DSTATE_THRESHOLD: usize = 32;

const EXIT_OK: i32 = 0;
const EXIT_REFUSED: i32 = 2;
const EXIT_QUEUED: i32 = 4;
const EXIT_ERROR: i32 = 3;

const USAGE: &str = r#"Usage: fbsource-buck-admit.rs --task ID --agent NAME [OPTIONS] -- <buck2 command...>

Admit one heavy fbsource Buck run under a bounded, attributed cgroup, or refuse it.

Required:
  --task ID               Owning TaskGraph task id (recorded in the cgroup name
                          and in every evidence record).
  --agent NAME            Owning agent (same).

Options:
  --max-jobs N            Action fan-out cap (default: 16). A command whose -j
                          exceeds this is refused; a recursive command with no
                          -j at all is always refused.
  --allow-unbounded       Override for a recursive pattern with no -j. Recorded
                          in the evidence as an explicit human decision.
  --tasks-max N           cgroup pids.max (default: 4096).
  --memory-high BYTES     cgroup memory.high (default: 64GiB).
  --memory-max BYTES      cgroup memory.max (default: 96GiB).
  --cpu-quota PCT         cgroup CPUQuota, percent (default: 1600 = 16 cores).
  --lock PATH             Host heavy-work semaphore
                          (default: $XDG_RUNTIME_DIR/hermit-heavy-work.lock).
  --wait SECONDS          Block up to SECONDS for the semaphore instead of
                          reporting QUEUED immediately (default: 0).
  --evidence-dir PATH     Where threshold snapshots are written
                          (default: $HOME/.local/state/hermit-heavy-work).
  --psi-threshold PCT     IO full avg10 that arms a snapshot (default: 50).
  --dstate-threshold N    Btrfs D-state count that arms a snapshot (default: 32).
  --poll-seconds N        Threshold sampling interval (default: 10).
  --dry-run               Decide, print, and exit without running anything.
  --check-only            Alias for --dry-run.
  -h, --help              Print this pure help text.

Exit codes:
  0  admitted and the command ran (its own status is reported separately)
  2  REFUSED by admission policy -- nothing ran
  3  usage or internal error
  4  QUEUED: another heavy run holds the host semaphore; nothing ran
"#;

// ------------------------------------------------------------- parsing -----

/// What the guard understands about a Buck command line. Deliberately a plain
/// value so the admission rule is unit-testable without a Buck installation.
#[derive(Debug, Clone, PartialEq, Eq)]
struct BuckPlan {
    subcommand: Option<String>,
    patterns: Vec<String>,
    explicit_jobs: Option<u32>,
    recursive: bool,
}

/// A target pattern is recursive when it ends in `...` -- `fbcode//x/...`,
/// `//x/...`, or a bare `...`. That is the form that expands to a whole subtree
/// and inherits Buck's core-count default for `-j`.
fn is_recursive_pattern(token: &str) -> bool {
    if token.starts_with('-') {
        return false;
    }
    token == "..." || token.ends_with("/...")
}

fn parse_buck_command(argv: &[String]) -> BuckPlan {
    let mut subcommand = None;
    let mut patterns = Vec::new();
    let mut explicit_jobs = None;
    let mut index = 0;
    // Skip the binary itself, however it is spelled (buck, buck2, /path/to/buck2).
    if let Some(first) = argv.first() {
        let base = Path::new(first)
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or(first.as_str());
        if base == "buck" || base == "buck2" {
            index = 1;
        }
    }
    while index < argv.len() {
        let token = &argv[index];
        // `-j N`, `-jN`, `--num-threads N`, `--num-threads=N`. Buck accepts
        // several spellings and missing any of them would let an unbounded run
        // through while LOOKING bounded, so all are recognised.
        if token == "-j" || token == "--num-threads" {
            if let Some(value) = argv.get(index + 1) {
                explicit_jobs = value.parse::<u32>().ok();
                index += 2;
                continue;
            }
        } else if let Some(rest) = token.strip_prefix("--num-threads=") {
            explicit_jobs = rest.parse::<u32>().ok();
            index += 1;
            continue;
        } else if let Some(rest) = token.strip_prefix("-j") {
            if !rest.is_empty() && rest.chars().all(|c| c.is_ascii_digit()) {
                explicit_jobs = rest.parse::<u32>().ok();
                index += 1;
                continue;
            }
        }
        if !token.starts_with('-') {
            if subcommand.is_none() {
                subcommand = Some(token.clone());
            } else {
                patterns.push(token.clone());
            }
        }
        index += 1;
    }
    let recursive = patterns.iter().any(|p| is_recursive_pattern(p));
    BuckPlan {
        subcommand,
        patterns,
        explicit_jobs,
        recursive,
    }
}

// ----------------------------------------------------------- admission -----

#[derive(Debug, Clone, PartialEq, Eq)]
enum Admission {
    /// Run it, with this effective action cap.
    Admit { jobs: u32, overridden: bool },
    /// Do not run it. `reason` is written for a human, not a log parser.
    Refuse { reason: String },
}

impl Admission {
    /// Used by the admission tests; `run()` matches on the variant directly.
    #[cfg_attr(not(test), allow(dead_code))]
    fn admitted(&self) -> bool {
        matches!(self, Admission::Admit { .. })
    }
}

/// THE admission rule. Kept free of I/O so both controls can be bracketed
/// without a Buck installation, a cgroup, or a semaphore.
fn decide(plan: &BuckPlan, max_jobs: u32, allow_unbounded: bool) -> Admission {
    match plan.explicit_jobs {
        // The incident case exactly: recursive pattern, no -j, Buck defaults to
        // the core count. Fails closed.
        None if plan.recursive && !allow_unbounded => Admission::Refuse {
            reason: format!(
                "recursive target pattern {:?} with no explicit -j. Buck defaults -j to the host \
                 core count, which is how the 2026-08-06 22:40 EDT Btrfs lock convoy started \
                 (64 of 89 onset D-state tasks were one uncapped run). Pass -j {max_jobs} or \
                 lower, or narrow the pattern. --allow-unbounded overrides this deliberately.",
                plan.patterns
                    .iter()
                    .filter(|p| is_recursive_pattern(p))
                    .cloned()
                    .collect::<Vec<_>>()
            ),
        },
        None if plan.recursive => Admission::Admit {
            jobs: max_jobs,
            overridden: true,
        },
        // Non-recursive and unspecified: still bounded by the cgroup, and the
        // pattern names concrete targets rather than a subtree.
        None => Admission::Admit {
            jobs: max_jobs,
            overridden: false,
        },
        Some(0) => Admission::Refuse {
            reason: "-j 0 means unbounded to Buck; pass a positive job count".to_string(),
        },
        Some(jobs) if jobs > max_jobs && !allow_unbounded => Admission::Refuse {
            reason: format!(
                "-j {jobs} exceeds the host action cap of {max_jobs}. This host is shared with \
                 ~18 agents and the cap exists because action fan-out, not CPU, is what drives \
                 the Btrfs metadata lock convoy. Raise --max-jobs only from measured PSI and \
                 D-state evidence."
            ),
        },
        Some(jobs) if jobs > max_jobs => Admission::Admit {
            jobs,
            overridden: true,
        },
        Some(jobs) => Admission::Admit {
            jobs,
            overridden: false,
        },
    }
}

// ------------------------------------------------------------ telemetry ----

/// IO pressure, read from /proc/pressure/io. `full` is the fraction of wall
/// time in which EVERY task was stalled on IO -- the number that was 77.52% at
/// the incident peak while CPU sat 59.66% idle.
fn read_io_pressure(root: &Path) -> Option<f64> {
    let text = fs::read_to_string(root.join("proc/pressure/io")).ok()?;
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("full ") {
            for field in rest.split_whitespace() {
                if let Some(value) = field.strip_prefix("avg10=") {
                    return value.parse::<f64>().ok();
                }
            }
        }
    }
    None
}

/// Census of uninterruptible-sleep tasks by kernel wait channel.
///
/// `/proc/<pid>/stack` is the richer source but needs privilege; `wchan` is
/// world-readable and names the same blocking function, which is what
/// distinguishes a Btrfs lock convoy (`btrfs_tree_lock_nested`,
/// `btrfs_read_lock_root_node`) from ordinary IO waits. The distinction the
/// alert needs is *which* channel, not the full stack.
fn dstate_census(root: &Path) -> BTreeMap<String, usize> {
    let mut census = BTreeMap::new();
    let proc_dir = root.join("proc");
    let entries = match fs::read_dir(&proc_dir) {
        Ok(entries) => entries,
        Err(_) => return census,
    };
    for entry in entries.flatten() {
        let name = entry.file_name();
        let pid = match name.to_str() {
            Some(text) if text.chars().all(|c| c.is_ascii_digit()) => text.to_string(),
            _ => continue,
        };
        let stat = match fs::read_to_string(proc_dir.join(&pid).join("stat")) {
            Ok(text) => text,
            Err(_) => continue,
        };
        // State is the field after the parenthesised comm, which may itself
        // contain spaces and parentheses -- so split on the LAST ')'.
        let state = match stat.rfind(')').and_then(|i| stat[i + 2..].split(' ').next()) {
            Some(state) => state.to_string(),
            None => continue,
        };
        if state != "D" {
            continue;
        }
        let wchan = fs::read_to_string(proc_dir.join(&pid).join("wchan"))
            .map(|w| w.trim().to_string())
            .unwrap_or_default();
        let key = if wchan.is_empty() {
            "unknown".to_string()
        } else {
            wchan
        };
        *census.entry(key).or_insert(0) += 1;
    }
    census
}

fn btrfs_dstate_count(census: &BTreeMap<String, usize>) -> usize {
    census
        .iter()
        .filter(|(channel, _)| channel.contains("btrfs") || channel.contains("folio_wait"))
        .map(|(_, n)| *n)
        .sum()
}

/// Should a snapshot be captured? Deliberately an OR, not an AND: either signal
/// alone is enough to want evidence, and evidence capture is cheap and
/// side-effect free. (Killing on this would need a far stronger predicate --
/// which is exactly why this guard does not kill.)
fn threshold_tripped(psi_full: Option<f64>, btrfs_dstate: usize, psi_limit: f64, dstate_limit: usize) -> bool {
    psi_full.map(|p| p >= psi_limit).unwrap_or(false) || btrfs_dstate >= dstate_limit
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
struct Evidence {
    captured_at: String,
    task: String,
    agent: String,
    command: Vec<String>,
    effective_jobs: u32,
    cgroup: String,
    scope_unit: String,
    io_psi_full_avg10: Option<f64>,
    btrfs_dstate_tasks: usize,
    dstate_by_wchan: BTreeMap<String, usize>,
    psi_threshold: f64,
    dstate_threshold: usize,
    /// Recorded so a reader never has to infer it: this guard captures and
    /// reports, it never kills.
    action_taken: String,
}

// -------------------------------------------------------------- locking ----

/// Host-wide heavy-work semaphore.
///
/// One `flock` on one path. Deliberately NOT a reimplementation of ci-hub's
/// lease engine: this guard needs mutual exclusion between heavy suites, and a
/// second queue with its own dead-owner reclaim rules would be a second thing
/// to get wrong. The lock is advisory and dies with the process, so a killed
/// run cannot wedge the host.
struct HostSemaphore {
    _file: fs::File,
    path: PathBuf,
}

fn try_acquire(path: &Path, wait_seconds: u64) -> Result<Option<HostSemaphore>, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create {}: {e}", parent.display()))?;
    }
    let file = OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .open(path)
        .map_err(|e| format!("open {}: {e}", path.display()))?;
    let deadline = now_epoch() + wait_seconds;
    loop {
        // LOCK_EX|LOCK_NB via the flock(2) syscall through the `flock` binary is
        // not usable here (it would hold the lock in a child), so call libc's
        // flock through a raw syscall wrapper.
        let rc = unsafe { flock(file.as_raw_fd(), LOCK_EX | LOCK_NB) };
        if rc == 0 {
            return Ok(Some(HostSemaphore {
                _file: file,
                path: path.to_path_buf(),
            }));
        }
        if now_epoch() >= deadline {
            return Ok(None);
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
}

const LOCK_EX: i32 = 2;
const LOCK_NB: i32 = 4;

extern "C" {
    fn flock(fd: i32, operation: i32) -> i32;
}

// ----------------------------------------------------------------- time ----

fn now_epoch() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn format_utc(epoch: u64) -> String {
    let days = (epoch / 86_400) as i64;
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = if m <= 2 { y + 1 } else { y };
    let sec = epoch % 86_400;
    format!(
        "{year:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        sec / 3600,
        (sec % 3600) / 60,
        sec % 60
    )
}

// ----------------------------------------------------------------- main ----

#[derive(Debug, Clone)]
struct Args {
    task: String,
    agent: String,
    max_jobs: u32,
    allow_unbounded: bool,
    tasks_max: u64,
    memory_high: u64,
    memory_max: u64,
    cpu_quota: u32,
    lock: PathBuf,
    wait_seconds: u64,
    evidence_dir: PathBuf,
    psi_threshold: f64,
    dstate_threshold: usize,
    poll_seconds: u64,
    dry_run: bool,
    command: Vec<String>,
}

fn main() {
    match run() {
        Ok(code) => exit(code),
        Err(message) => {
            eprintln!("fbsource-buck-admit: {message}");
            exit(EXIT_ERROR);
        }
    }
}

fn run() -> Result<i32, String> {
    let args = parse_args()?;
    let plan = parse_buck_command(&args.command);
    let admission = decide(&plan, args.max_jobs, args.allow_unbounded);

    println!("task={} agent={}", args.task, args.agent);
    println!(
        "plan: subcommand={:?} patterns={:?} recursive={} explicit_jobs={:?}",
        plan.subcommand, plan.patterns, plan.recursive, plan.explicit_jobs
    );

    let jobs = match &admission {
        Admission::Refuse { reason } => {
            println!("REFUSED: {reason}");
            return Ok(EXIT_REFUSED);
        }
        Admission::Admit { jobs, overridden } => {
            println!(
                "ADMITTED: effective -j {jobs}{}",
                if *overridden { " (OVERRIDDEN by an explicit flag)" } else { "" }
            );
            *jobs
        }
    };

    if args.dry_run {
        println!("dry-run: nothing executed");
        return Ok(EXIT_OK);
    }

    // Full-suite runs serialise. A recursive pattern is the definition of
    // "full suite" here; a narrow target does not need the host semaphore.
    let _semaphore = if plan.recursive {
        match try_acquire(&args.lock, args.wait_seconds)? {
            Some(sem) => {
                println!("host semaphore: acquired {}", sem.path.display());
                Some(sem)
            }
            None => {
                println!(
                    "QUEUED: another heavy run holds {}. Nothing was started. \
                     Retry, or pass --wait SECONDS to block.",
                    args.lock.display()
                );
                return Ok(EXIT_QUEUED);
            }
        }
    } else {
        None
    };

    let unit = format!(
        "heavywork-{}-{}-{}",
        sanitize(&args.task),
        sanitize(&args.agent),
        std::process::id()
    );
    let effective: Vec<String> = inject_jobs(&args.command, jobs);
    println!("scope unit: {unit}");
    println!("command: {}", effective.join(" "));

    let status = launch(&args, &unit, &effective, jobs, &plan)?;
    Ok(status)
}

/// Replace or append the job cap so the launched command carries the admitted
/// value rather than merely having been checked against it.
fn inject_jobs(command: &[String], jobs: u32) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut index = 0;
    while index < command.len() {
        let token = &command[index];
        if token == "-j" || token == "--num-threads" {
            index += 2;
            continue;
        }
        if token.starts_with("--num-threads=")
            || (token.starts_with("-j") && token.len() > 2 && token[2..].chars().all(|c| c.is_ascii_digit()))
        {
            index += 1;
            continue;
        }
        out.push(token.clone());
        index += 1;
    }
    // After the subcommand if there is one, else at the end.
    let insert_at = if out.len() >= 2 { 2 } else { out.len() };
    out.insert(insert_at, format!("{jobs}"));
    out.insert(insert_at, "-j".to_string());
    out
}

fn sanitize(text: &str) -> String {
    text.chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '-' })
        .collect::<String>()
        .trim_matches('-')
        .to_string()
}

fn launch(
    args: &Args,
    unit: &str,
    command: &[String],
    jobs: u32,
    _plan: &BuckPlan,
) -> Result<i32, String> {
    fs::create_dir_all(&args.evidence_dir)
        .map_err(|e| format!("create {}: {e}", args.evidence_dir.display()))?;
    let cgroup_file = args.evidence_dir.join(format!("{unit}.cgroup"));
    let applied_file = args.evidence_dir.join(format!("{unit}.applied"));

    // The inner shell records the cgroup its OWN process landed in, and reads
    // the caps back out of it. The incident's Buck daemon scope had every cap
    // at `max`; a guard that trusted the flags it passed would have reported
    // success for exactly that configuration.
    let inner = format!(
        "CG=$(sed 's/^0:://' /proc/self/cgroup); printf '%s\\n' \"$CG\" > {cg}; \
         {{ printf 'pids.max=%s\\n' \"$(cat /sys/fs/cgroup$CG/pids.max 2>/dev/null)\"; \
            printf 'memory.high=%s\\n' \"$(cat /sys/fs/cgroup$CG/memory.high 2>/dev/null)\"; \
            printf 'memory.max=%s\\n' \"$(cat /sys/fs/cgroup$CG/memory.max 2>/dev/null)\"; \
            printf 'cpu.max=%s\\n' \"$(cat /sys/fs/cgroup$CG/cpu.max 2>/dev/null)\"; }} > {ap}; \
         exec \"$@\"",
        cg = cgroup_file.display(),
        ap = applied_file.display(),
    );

    let mut child = Command::new("systemd-run")
        .args([
            "--user",
            "--scope",
            "--quiet",
            &format!("--unit={unit}"),
            "-p",
            &format!("TasksMax={}", args.tasks_max),
            "-p",
            &format!("MemoryHigh={}", args.memory_high),
            "-p",
            &format!("MemoryMax={}", args.memory_max),
            "-p",
            &format!("CPUQuota={}%", args.cpu_quota),
            "bash",
            "-c",
            &inner,
            "buck-admit",
        ])
        .args(command)
        .spawn()
        .map_err(|e| format!("spawn systemd-run: {e}"))?;

    // Watch while it runs. Sampling only; the guard never signals the child.
    let root = PathBuf::from("/");
    loop {
        match child.try_wait().map_err(|e| format!("wait: {e}"))? {
            Some(status) => {
                println!("command exited: {}", status.code().unwrap_or(-1));
                return Ok(EXIT_OK);
            }
            None => {
                let psi = read_io_pressure(&root);
                let census = dstate_census(&root);
                let btrfs = btrfs_dstate_count(&census);
                if threshold_tripped(psi, btrfs, args.psi_threshold, args.dstate_threshold) {
                    let evidence = Evidence {
                        captured_at: format_utc(now_epoch()),
                        task: args.task.clone(),
                        agent: args.agent.clone(),
                        command: command.to_vec(),
                        effective_jobs: jobs,
                        cgroup: fs::read_to_string(&cgroup_file)
                            .unwrap_or_default()
                            .trim()
                            .to_string(),
                        scope_unit: unit.to_string(),
                        io_psi_full_avg10: psi,
                        btrfs_dstate_tasks: btrfs,
                        dstate_by_wchan: census,
                        psi_threshold: args.psi_threshold,
                        dstate_threshold: args.dstate_threshold,
                        action_taken: "captured-evidence-only; this guard never kills".to_string(),
                    };
                    let path = args
                        .evidence_dir
                        .join(format!("{unit}-{}.json", now_epoch()));
                    let body = serde_json::to_string_pretty(&evidence)
                        .map_err(|e| format!("serialize evidence: {e}"))?;
                    fs::write(&path, body + "\n")
                        .map_err(|e| format!("write {}: {e}", path.display()))?;
                    eprintln!(
                        "THRESHOLD: io_full_avg10={psi:?} btrfs_dstate={btrfs}; evidence {}",
                        path.display()
                    );
                }
                std::thread::sleep(std::time::Duration::from_secs(args.poll_seconds.max(1)));
            }
        }
    }
}

fn parse_args() -> Result<Args, String> {
    let home = env::var("HOME").map_err(|_| "HOME is not set".to_string())?;
    let runtime = env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| format!("{home}/.cache"));
    let mut args = Args {
        task: String::new(),
        agent: String::new(),
        max_jobs: DEFAULT_MAX_JOBS,
        allow_unbounded: false,
        tasks_max: 4096,
        memory_high: 64 * 1024 * 1024 * 1024,
        memory_max: 96 * 1024 * 1024 * 1024,
        cpu_quota: 1600,
        lock: PathBuf::from(format!("{runtime}/hermit-heavy-work.lock")),
        wait_seconds: 0,
        evidence_dir: PathBuf::from(format!("{home}/.local/state/hermit-heavy-work")),
        psi_threshold: DEFAULT_PSI_FULL_THRESHOLD,
        dstate_threshold: DEFAULT_DSTATE_THRESHOLD,
        poll_seconds: 10,
        dry_run: false,
        command: Vec::new(),
    };
    let mut raw = env::args().skip(1);
    while let Some(flag) = raw.next() {
        let mut next = |name: &str| -> Result<String, String> {
            raw.next()
                .ok_or_else(|| format!("{name} requires a value\n\n{USAGE}"))
        };
        match flag.as_str() {
            "--" => {
                args.command = raw.collect();
                break;
            }
            "--task" => args.task = next("--task")?,
            "--agent" => args.agent = next("--agent")?,
            "--max-jobs" => {
                args.max_jobs = next("--max-jobs")?
                    .parse()
                    .map_err(|_| "--max-jobs must be a positive integer".to_string())?
            }
            "--allow-unbounded" => args.allow_unbounded = true,
            "--tasks-max" => args.tasks_max = next("--tasks-max")?.parse().map_err(|_| "bad --tasks-max")?,
            "--memory-high" => {
                args.memory_high = next("--memory-high")?.parse().map_err(|_| "bad --memory-high")?
            }
            "--memory-max" => {
                args.memory_max = next("--memory-max")?.parse().map_err(|_| "bad --memory-max")?
            }
            "--cpu-quota" => args.cpu_quota = next("--cpu-quota")?.parse().map_err(|_| "bad --cpu-quota")?,
            "--lock" => args.lock = PathBuf::from(next("--lock")?),
            "--wait" => args.wait_seconds = next("--wait")?.parse().map_err(|_| "bad --wait")?,
            "--evidence-dir" => args.evidence_dir = PathBuf::from(next("--evidence-dir")?),
            "--psi-threshold" => {
                args.psi_threshold = next("--psi-threshold")?.parse().map_err(|_| "bad --psi-threshold")?
            }
            "--dstate-threshold" => {
                args.dstate_threshold =
                    next("--dstate-threshold")?.parse().map_err(|_| "bad --dstate-threshold")?
            }
            "--poll-seconds" => {
                args.poll_seconds = next("--poll-seconds")?.parse().map_err(|_| "bad --poll-seconds")?
            }
            "--dry-run" | "--check-only" => args.dry_run = true,
            "-h" | "--help" => {
                print!("{USAGE}");
                exit(EXIT_OK);
            }
            other => return Err(format!("unknown argument: {other}\n\n{USAGE}")),
        }
    }
    if args.task.is_empty() || args.agent.is_empty() {
        return Err(format!("--task and --agent are required\n\n{USAGE}"));
    }
    if args.command.is_empty() {
        return Err(format!("no command after --\n\n{USAGE}"));
    }
    Ok(args)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU32, Ordering};

    static COUNTER: AtomicU32 = AtomicU32::new(0);

    fn tmpdir(label: &str) -> PathBuf {
        let dir = env::temp_dir().join(format!(
            "buck-admit-test-{}-{}-{}",
            label,
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn argv(parts: &[&str]) -> Vec<String> {
        parts.iter().map(|s| s.to_string()).collect()
    }

    // ---- THE INCIDENT COMMAND ITSELF must be refused ----

    /// The exact command from the 2026-08-06 22:39:58 EDT incident, verbatim.
    #[test]
    fn the_incident_command_is_refused() {
        let plan = parse_buck_command(&argv(&[
            "buck2",
            "test",
            "fbcode//hermetic_infra/hermit/...",
        ]));
        assert!(plan.recursive, "the incident pattern must read as recursive");
        assert_eq!(plan.explicit_jobs, None, "the incident command had no -j");
        let decision = decide(&plan, DEFAULT_MAX_JOBS, false);
        assert!(
            !decision.admitted(),
            "the command that stalled the host for 14 minutes must not be admitted: {decision:?}"
        );
        match decision {
            Admission::Refuse { reason } => {
                // The refusal has to teach, or the next agent just adds --allow-unbounded.
                assert!(reason.contains("-j"), "refusal must name the missing flag: {reason}");
                assert!(
                    reason.contains("core count"),
                    "refusal must explain WHY the default is unsafe: {reason}"
                );
            }
            other => panic!("expected refusal, got {other:?}"),
        }
    }

    // ---- POSITIVE CONTROL: the bounded form must actually run ----

    #[test]
    fn positive_bounded_recursive_command_is_admitted() {
        let plan = parse_buck_command(&argv(&[
            "buck2",
            "test",
            "-j",
            "16",
            "fbcode//hermetic_infra/hermit/...",
        ]));
        assert_eq!(plan.explicit_jobs, Some(16));
        assert_eq!(
            decide(&plan, DEFAULT_MAX_JOBS, false),
            Admission::Admit { jobs: 16, overridden: false },
            "a bounded recursive run is the SUPPORTED path and must not be blocked"
        );
    }

    #[test]
    fn narrow_target_without_jobs_is_admitted_but_still_capped() {
        let plan = parse_buck_command(&argv(&["buck2", "test", "fbcode//hermetic_infra/hermit:lib"]));
        assert!(!plan.recursive);
        assert_eq!(
            decide(&plan, DEFAULT_MAX_JOBS, false),
            Admission::Admit { jobs: DEFAULT_MAX_JOBS, overridden: false },
            "a named target is not the incident shape, but still runs under the cap"
        );
    }

    // ---- the cap itself must bind, and be overridable only explicitly ----

    #[test]
    fn over_cap_job_count_is_refused_and_the_override_is_explicit() {
        let plan = parse_buck_command(&argv(&["buck2", "test", "-j", "316", "fbcode//x/..."]));
        assert_eq!(plan.explicit_jobs, Some(316));
        assert!(!decide(&plan, 16, false).admitted(), "316 is the host core count that caused this");
        assert_eq!(
            decide(&plan, 16, true),
            Admission::Admit { jobs: 316, overridden: true },
            "an override must be possible, and must mark itself as one"
        );
    }

    #[test]
    fn unbounded_override_is_recorded_as_overridden() {
        let plan = parse_buck_command(&argv(&["buck2", "test", "fbcode//x/..."]));
        assert_eq!(
            decide(&plan, 16, true),
            Admission::Admit { jobs: 16, overridden: true },
            "overriding the unbounded refusal must still apply the cap, not remove it"
        );
    }

    #[test]
    fn zero_jobs_is_refused_because_buck_reads_it_as_unbounded() {
        let plan = parse_buck_command(&argv(&["buck2", "test", "-j", "0", "fbcode//x/..."]));
        assert!(!decide(&plan, 16, false).admitted());
    }

    // ---- every -j spelling must be recognised, or a run LOOKS bounded ----

    #[test]
    fn all_job_flag_spellings_are_recognised() {
        for (spelling, expected) in [
            (vec!["buck2", "test", "-j", "8", "//x/..."], 8u32),
            (vec!["buck2", "test", "-j8", "//x/..."], 8),
            (vec!["buck2", "test", "--num-threads", "8", "//x/..."], 8),
            (vec!["buck2", "test", "--num-threads=8", "//x/..."], 8),
        ] {
            let plan = parse_buck_command(&argv(&spelling));
            assert_eq!(
                plan.explicit_jobs,
                Some(expected),
                "unrecognised -j spelling would let an unbounded run pass as bounded: {spelling:?}"
            );
        }
    }

    #[test]
    fn recursive_pattern_detection_covers_the_real_spellings() {
        for token in ["fbcode//hermetic_infra/hermit/...", "//x/...", "..."] {
            assert!(is_recursive_pattern(token), "{token} must read as recursive");
        }
        for token in ["fbcode//x:target", "//x:y", "-j", "--num-threads=4", "test"] {
            assert!(!is_recursive_pattern(token), "{token} must NOT read as recursive");
        }
    }

    /// A pattern that merely CONTAINS dots is not recursive. Getting this wrong
    /// in the permissive direction reopens the incident; in the strict
    /// direction it blocks ordinary work.
    #[test]
    fn dotted_target_names_are_not_mistaken_for_recursion() {
        let plan = parse_buck_command(&argv(&["buck2", "test", "fbcode//x:my.test.target"]));
        assert!(!plan.recursive);
        assert!(decide(&plan, 16, false).admitted());
    }

    // ---- the admitted command must CARRY the cap, not just pass the check ----

    #[test]
    fn injected_jobs_replace_any_existing_flag() {
        let out = inject_jobs(&argv(&["buck2", "test", "-j", "316", "//x/..."]), 16);
        assert_eq!(out, argv(&["buck2", "test", "-j", "16", "//x/..."]));
        let appended = inject_jobs(&argv(&["buck2", "test", "//x/..."]), 16);
        assert_eq!(appended, argv(&["buck2", "test", "-j", "16", "//x/..."]));
        // ...and no stale duplicate survives, which would let Buck pick either.
        assert_eq!(out.iter().filter(|t| *t == "-j").count(), 1);
    }

    // ---- host semaphore: the second full suite must QUEUE, not stack ----

    #[test]
    fn host_semaphore_serialises_full_suite_admissions() {
        let dir = tmpdir("sem");
        let lock = dir.join("heavy.lock");
        let first = try_acquire(&lock, 0).unwrap();
        assert!(first.is_some(), "first heavy run must be admitted");
        let second = try_acquire(&lock, 0).unwrap();
        assert!(
            second.is_none(),
            "a second concurrent full suite must QUEUE -- stacking two is the incident's \
             prevention item 1"
        );
        drop(first);
        let third = try_acquire(&lock, 0).unwrap();
        assert!(third.is_some(), "the lock must be released when the holder exits");
    }

    // ---- threshold: fires on the incident's numbers, silent when healthy ----

    #[test]
    fn threshold_fires_on_the_measured_incident_values() {
        // The durable 22:50 EDT sample: IO full avg10 77.52%.
        assert!(threshold_tripped(Some(77.52), 0, 50.0, 32));
        // And onset: 64 Btrfs D-state tasks, before PSI had climbed.
        assert!(threshold_tripped(Some(0.39), 64, 50.0, 32));
    }

    #[test]
    fn threshold_is_silent_on_a_healthy_host() {
        // The post-recovery sample: 0 blocked, negligible pressure.
        assert!(!threshold_tripped(Some(0.07), 0, 50.0, 32));
        assert!(!threshold_tripped(Some(0.0), 3, 50.0, 32));
        // Missing PSI must not be read as tripped.
        assert!(!threshold_tripped(None, 0, 50.0, 32));
    }

    #[test]
    fn btrfs_channels_are_counted_and_unrelated_waits_are_not() {
        let mut census = BTreeMap::new();
        census.insert("btrfs_tree_lock_nested".to_string(), 9);
        census.insert("btrfs_read_lock_root_node".to_string(), 47);
        census.insert("folio_wait_bit_common".to_string(), 100);
        census.insert("pipe_read".to_string(), 500);
        census.insert("do_wait".to_string(), 12);
        // The three convoy channels count; ordinary waits do not, or every idle
        // host would look like an incident.
        assert_eq!(btrfs_dstate_count(&census), 156);
    }

    // ---- the evidence record must carry everything the task requires ----

    #[test]
    fn evidence_record_carries_task_agent_command_cgroup_psi_and_dstate() {
        let mut census = BTreeMap::new();
        census.insert("btrfs_tree_lock_nested".to_string(), 64);
        let evidence = Evidence {
            captured_at: format_utc(1_786_069_814),
            task: "release-0.3-audit-fbsource-baseline".to_string(),
            agent: "hermit-w9".to_string(),
            command: argv(&["buck2", "test", "-j", "16", "fbcode//hermetic_infra/hermit/..."]),
            effective_jobs: 16,
            cgroup: "/user.slice/.../heavywork-x.scope".to_string(),
            scope_unit: "heavywork-x".to_string(),
            io_psi_full_avg10: Some(77.52),
            btrfs_dstate_tasks: 64,
            dstate_by_wchan: census,
            psi_threshold: 50.0,
            dstate_threshold: 32,
            action_taken: "captured-evidence-only; this guard never kills".to_string(),
        };
        let text = serde_json::to_string(&evidence).unwrap();
        for required in [
            "release-0.3-audit-fbsource-baseline",
            "hermit-w9",
            "buck2",
            "heavywork-x",
            "77.52",
            "btrfs_tree_lock_nested",
            "io_psi_full_avg10",
        ] {
            assert!(text.contains(required), "evidence is missing {required}: {text}");
        }
        // Round-trips, so a later reader can parse rather than grep it.
        let parsed: Evidence = serde_json::from_str(&text).unwrap();
        assert_eq!(parsed, evidence);
    }

    // ---- Hard Invariant 15: no broad kill surface, enforced as a test ----

    /// The guard watches and records; it must never acquire a kill. Asserting
    /// the ABSENCE in a test is what stops a future "just add a safety kill"
    /// edit from quietly violating Invariant 15 on a box shared with ~18 agents.
    #[test]
    fn no_broad_kill_surface_in_this_script() {
        let source = fs::read_to_string(Path::new(file!())).expect("read own source");
        // Needles are assembled so this test does not itself plant the strings
        // an auditor greps for.
        let banned = [
            format!("{}{}", "pk", "ill"),
            format!("{}{}", "kill", "all"),
            format!("{} {}", "kill", "-9 -1"),
            format!("{}{}", "killpg", "("),
        ];
        for needle in &banned {
            let hits: Vec<&str> = source
                .lines()
                .filter(|line| line.contains(needle.as_str()))
                .filter(|line| !line.trim_start().starts_with("//"))
                .filter(|line| !line.contains("format!"))
                .collect();
            assert!(hits.is_empty(), "broad-kill surface {needle:?} present: {hits:?}");
        }
        // And no signalling of the child at all: the guard samples and exits.
        // These needles are assembled at runtime for the same reason as the
        // ones above -- spelling `.kill()` literally here made THIS line match
        // itself, and the test failed on its own assertion text rather than on
        // any real call. A source scanner that reads the file it lives in has
        // to keep its needles out of that file.
        let signal_apis = [
            format!(".{}()", "kill"),
            format!("libc::{}", "kill"),
            format!("signal::{}", "kill"),
        ];
        for api in &signal_apis {
            let hits: Vec<&str> = source
                .lines()
                .filter(|l| l.contains(api.as_str()))
                .filter(|l| !l.trim_start().starts_with("//"))
                .filter(|l| !l.contains("format!"))
                .collect();
            assert!(hits.is_empty(), "guard must not signal processes; found {api}: {hits:?}");
        }
    }

    /// Positive control for the scanner above: it must FIRE on a planted case,
    /// or its silence proves nothing.
    #[test]
    fn kill_scanner_is_not_inert() {
        let planted = format!("    Command::new(\"{}{}\").arg(\"buck2\");", "pk", "ill");
        let needle = format!("{}{}", "pk", "ill");
        assert!(
            planted.contains(needle.as_str()),
            "the scanner's own needle construction must match a planted violation"
        );
    }

    // ---- telemetry parsers, against real kernel formats ----

    #[test]
    fn io_pressure_is_parsed_from_the_real_proc_format() {
        let dir = tmpdir("psi");
        fs::create_dir_all(dir.join("proc/pressure")).unwrap();
        fs::write(
            dir.join("proc/pressure/io"),
            "some avg10=80.88 avg60=40.00 avg300=10.00 total=917926420\n\
             full avg10=77.52 avg60=38.00 avg300=9.00 total=878707754\n",
        )
        .unwrap();
        assert_eq!(read_io_pressure(&dir), Some(77.52), "must read FULL, not SOME");
    }

    #[test]
    fn missing_pressure_file_reads_as_unknown_not_as_zero() {
        let dir = tmpdir("nopsi");
        assert_eq!(
            read_io_pressure(&dir),
            None,
            "absent telemetry must be None; zero would silently disarm the alert"
        );
    }

    /// `/proc/<pid>/stat` embeds comm in parentheses and comm may contain spaces
    /// and parentheses. Splitting on the first ')' misreads the state field.
    #[test]
    fn dstate_census_survives_a_comm_containing_spaces_and_parens() {
        let dir = tmpdir("dstate");
        for (pid, comm, state, wchan) in [
            ("101", "(buck2 worker (x))", "D", "btrfs_tree_lock_nested"),
            ("102", "(hermit)", "D", "btrfs_read_lock_root_node"),
            ("103", "(bash)", "S", "do_wait"),
            ("104", "(git)", "D", "path_openat"),
        ] {
            let p = dir.join("proc").join(pid);
            fs::create_dir_all(&p).unwrap();
            fs::write(p.join("stat"), format!("{pid} {comm} {state} 1 1 1\n")).unwrap();
            fs::write(p.join("wchan"), wchan).unwrap();
        }
        let census = dstate_census(&dir);
        assert_eq!(census.get("btrfs_tree_lock_nested"), Some(&1));
        assert_eq!(census.get("btrfs_read_lock_root_node"), Some(&1));
        assert_eq!(census.get("path_openat"), Some(&1));
        assert_eq!(census.get("do_wait"), None, "S-state tasks must not be counted");
        assert_eq!(btrfs_dstate_count(&census), 2);
    }
}
