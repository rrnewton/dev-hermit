#!/usr/bin/env rust-script
//! EXTERNAL liveness watchdog for the ORC process itself.
//!
//! ## Why this exists, and why it lives OUTSIDE ORC
//!
//! On 2026-08-06 the ORC process and its tmux server died together and took the
//! whole agent fleet with them. Nothing noticed. A human noticed, after a long
//! silent interval. Every existing monitor was ORC-hosted, so every existing
//! monitor died in the same event: a monitor that shares fate with the thing it
//! monitors fails silently together with it.
//!
//! This watchdog is therefore driven by `hermit-orc-liveness.timer`
//! (systemd --user), which re-arms off its OWN last activation. Killing ORC,
//! killing the tmux server, or killing this watchdog mid-run does not stop the
//! next check. The only shared substrate is the systemd --user manager.
//!
//! It is deliberately DISJOINT from the existing health path: the tick/staleness
//! pair watches whether the operational health TICK is firing, entirely by file
//! mtime, and would read a dead ORC as perfectly healthy. This watchdog watches
//! whether ORC ITSELF is alive, entirely by `~/.orc/index.db` plus `/proc`. It
//! shares no code, no state file, and no timer with them.
//!
//! ## Two proxies this deliberately refuses to trust
//!
//! 1. **`sessions.status = 'active'` is a LABEL ORC WROTE, not a fact about now.**
//!    ORC flips it to `closed` on orderly shutdown. A SIGKILL, an OOM kill, or a
//!    dead tmux server leaves it reading `active` forever. Treating that column
//!    as liveness is exactly the failure this tool exists to catch, so an
//!    `active` row is treated as nothing more than a CLAIM to be checked.
//!
//! 2. **"the PID exists" is a proxy for "ORC is running".** PIDs recycle. The
//!    observable binding between the recorded PID and the claimed process is
//!    `/proc/<pid>/cmdline`, so the check dereferences it and compares argv[0]'s
//!    basename against the expected ORC command. A live PID that is not ORC is
//!    reported as its own class (`DEAD_PID_RECYCLED`), never folded into "live"
//!    and never folded into "stale PID" — the diagnosis differs.
//!
//! Every record also carries `/proc/<pid>/stat` field 22 (process start time in
//! clock ticks). It is not used for classification today; it is written down so
//! that a future recycled-PID argument can be settled from the log instead of
//! re-derived from memory.
//!
//! ## Only LIVE is healthy. UNKNOWN is not healthy.
//!
//! If the database is missing or unreadable, this reports `UNKNOWN_*` and
//! alerts. Absence of evidence is not evidence of health; a watchdog that
//! degrades to silence when its input disappears is the original bug wearing a
//! different hat.
//!
//! ## Alerting is DURABLE-LOG-FIRST, and never lies about delivery
//!
//! The alarm case here is by definition "ORC is not running", so pushing the
//! alarm through `scripts/orc-hermit-msg.py` (which types into an ORC tmux pane)
//! would be self-defeating. This tool therefore does NOT claim to notify anyone
//! interactively. It appends a JSONL record on EVERY check and rewrites a
//! single-line status file for cheap human/`cat` inspection. `--alert-command`
//! exists so the owner can wire a genuinely out-of-band channel later; when it
//! is set, its exit status is recorded, so "pushed" and "tried and failed" never
//! render the same.
//!
//! ## Restart is opt-in, and defaults OFF on purpose
//!
//! Relaunching ORC is an owner-only decision on this fleet: the currently
//! running Orc injects `--codex-args=--dangerously-disable-linux-sandbox`, which
//! post-1.0 Codex rejects, so a naive auto-restart would faithfully reproduce a
//! coordinator that cannot spawn (see
//! `ai_docs/owner-decision-queue_20260806.md`, row 3.1). Auto-restarting into a
//! known-broken configuration is worse than alerting, so `--restart-command` is
//! unset by default and this tool never restarts anything unless told exactly
//! what to run.
//!
//! ```cargo
//! [dependencies]
//! serde = { version = "1", features = ["derive"] }
//! serde_json = "1"
//! ```

use serde::{Deserialize, Serialize};
use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{exit, Command};
use std::time::{SystemTime, UNIX_EPOCH};

const DEFAULT_EXPECT_COMMAND: &str = "orc";
const DEFAULT_COOLDOWN_SECS: u64 = 3600;

const EXIT_LIVE: i32 = 0;
const EXIT_DEAD: i32 = 1;
const EXIT_UNKNOWN: i32 = 2;
const EXIT_ERROR: i32 = 3;

const USAGE: &str = r#"Usage: orc-liveness-watchdog.rs [OPTIONS]

Check whether an ORC process is actually alive, by cross-checking the active
session rows in ORC's index database against /proc. Append a durable JSONL
health record on every check, and alert (subject to a cooldown) whenever ORC is
not observably live.

Options:
  --index-db PATH        ORC index database (default: $HOME/.orc/index.db).
  --log PATH             Durable JSONL health log
                         (default: $HOME/.local/state/hermit-orc-liveness.jsonl).
  --status-file PATH     One-line latest-verdict file for cheap inspection
                         (default: $HOME/.local/state/hermit-orc-liveness.status).
  --cooldown-stamp PATH  Alert cooldown stamp
                         (default: $HOME/.local/state/hermit-orc-liveness.last-alarm).
  --cooldown-secs N      Minimum seconds between alerts (default: 3600).
                         0 disables suppression entirely.
  --proc-root PATH       Root of the proc filesystem (default: /proc).
                         Overridable ONLY so the mutation tests can bracket both
                         sides against a fixture tree without touching the host.
  --expect-command NAME  Basename argv[0] must have for the PID to count as ORC
                         (default: orc).
  --alert-command CMD    Shell command run on an alert. Its exit status is
                         recorded in the log. Unset by default.
  --restart-command CMD  Shell command run when ORC is DEAD. Unset by default;
                         see the module header for why auto-restart is off.
  --dry-run              Classify and print, but write no log/status/stamp file
                         and run no alert or restart command.
  -h, --help             Print this pure help text.

Exit codes:
  0  LIVE      an ORC process was observed running.
  1  DEAD      ORC is not running (stale PID, recycled PID, null PID, or no
               active session row at all).
  2  UNKNOWN   liveness could not be determined (database missing/unreadable).
  3  ERROR     bad usage or an internal failure.
"#;

#[derive(Debug, Clone)]
struct Args {
    index_db: PathBuf,
    log: PathBuf,
    status_file: PathBuf,
    cooldown_stamp: PathBuf,
    cooldown_secs: u64,
    proc_root: PathBuf,
    expect_command: String,
    alert_command: Option<String>,
    restart_command: Option<String>,
    dry_run: bool,
}

/// One `status = 'active'` row, exactly as ORC last wrote it. Nothing here is
/// yet believed; `pid` in particular is a claim to be dereferenced.
#[derive(Debug, Clone, Deserialize, Serialize)]
struct SessionRow {
    id: String,
    #[serde(default)]
    name: String,
    #[serde(default)]
    pid: Option<i64>,
    #[serde(default)]
    cwd: Option<String>,
    #[serde(default)]
    updated_at: Option<String>,
}

/// What `/proc` actually says about a claimed PID.
#[derive(Debug, Clone, Serialize)]
struct ProcObservation {
    pid_alive: bool,
    /// argv[0] verbatim, so a mismatch is diagnosable and not merely asserted.
    cmdline_argv0: Option<String>,
    /// Basename of argv[0]; this is what the ORC comparison is made against.
    cmdline_basename: Option<String>,
    is_expected_command: bool,
    /// `/proc/<pid>/stat` field 22. Recorded, not used for classification.
    starttime_ticks: Option<u64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SessionVerdict {
    Live,
    StalePid,
    PidRecycled,
    NoPid,
}

impl SessionVerdict {
    fn as_str(self) -> &'static str {
        match self {
            SessionVerdict::Live => "live",
            SessionVerdict::StalePid => "stale_pid",
            SessionVerdict::PidRecycled => "pid_recycled",
            SessionVerdict::NoPid => "no_pid",
        }
    }
}

#[derive(Debug, Clone, Serialize)]
struct Observation {
    session_id: String,
    session_name: String,
    pid: Option<i64>,
    cwd: Option<String>,
    session_updated_at: Option<String>,
    #[serde(flatten)]
    proc: ProcObservation,
    verdict: &'static str,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum State {
    Live,
    DeadStalePid,
    DeadPidRecycled,
    DeadNoPid,
    DeadNoActiveSession,
    UnknownDbMissing,
    UnknownDbUnreadable,
}

impl State {
    fn as_str(self) -> &'static str {
        match self {
            State::Live => "LIVE",
            State::DeadStalePid => "DEAD_STALE_PID",
            State::DeadPidRecycled => "DEAD_PID_RECYCLED",
            State::DeadNoPid => "DEAD_NO_PID",
            State::DeadNoActiveSession => "DEAD_NO_ACTIVE_SESSION",
            State::UnknownDbMissing => "UNKNOWN_DB_MISSING",
            State::UnknownDbUnreadable => "UNKNOWN_DB_UNREADABLE",
        }
    }

    fn is_healthy(self) -> bool {
        matches!(self, State::Live)
    }

    fn exit_code(self) -> i32 {
        match self {
            State::Live => EXIT_LIVE,
            State::UnknownDbMissing | State::UnknownDbUnreadable => EXIT_UNKNOWN,
            _ => EXIT_DEAD,
        }
    }

    /// A one-line explanation that names the next diagnostic step, so the log is
    /// actionable by whoever reads it cold months from now.
    fn summary(self) -> &'static str {
        match self {
            State::Live => "ORC is running and observably bound to its recorded PID.",
            State::DeadStalePid => {
                "ALARM: ORC IS DEAD. An active session row survives, but its recorded PID has no \
                 /proc entry -- ORC exited without flipping the row to 'closed' (SIGKILL, OOM, or \
                 a tmux-server death). Diagnose: sqlite3 -readonly ~/.orc/index.db \
                 \"SELECT id,name,pid,status,updated_at FROM sessions WHERE status='active';\" \
                 Relaunch is OWNER-ONLY on this fleet -- see ai_docs/owner-decision-queue_20260806.md."
            }
            State::DeadPidRecycled => {
                "ALARM: ORC IS DEAD (PID RECYCLED). The recorded PID is alive but belongs to a \
                 DIFFERENT program -- the kernel reissued it after ORC died. A bare kill -0 check \
                 would have called this healthy. Diagnose: cat /proc/<pid>/cmdline | tr '\\0' ' '."
            }
            State::DeadNoPid => {
                "ALARM: ORC IS NOT VERIFIABLY RUNNING. An active session row exists with a NULL \
                 pid, so ORC never recorded a PID for it and liveness cannot be bound to any \
                 process. Treated as dead, not as unknown: a session ORC cannot name a process \
                 for is not serving the fleet."
            }
            State::DeadNoActiveSession => {
                "ALARM: NO ACTIVE ORC SESSION. The index database has zero rows with \
                 status='active'. Either ORC shut down cleanly and was never relaunched, or the \
                 database was reset. The fleet is not being coordinated either way."
            }
            State::UnknownDbMissing => {
                "ALARM (UNKNOWN, NOT HEALTHY): the ORC index database does not exist. Liveness \
                 could not be determined. Absence of evidence is not evidence of health."
            }
            State::UnknownDbUnreadable => {
                "ALARM (UNKNOWN, NOT HEALTHY): the ORC index database exists but could not be \
                 read. Liveness could not be determined. Check for a stale -wal/-shm pair or a \
                 permissions change."
            }
        }
    }
}

#[derive(Debug, Serialize)]
struct HealthRecord<'a> {
    schema_version: u8,
    checked_at: String,
    checked_at_epoch: u64,
    state: &'a str,
    healthy: bool,
    summary: &'a str,
    index_db: String,
    proc_root: String,
    expect_command: &'a str,
    active_session_count: usize,
    live_session_count: usize,
    observations: &'a [Observation],
    #[serde(skip_serializing_if = "Option::is_none")]
    db_error: Option<String>,
    alerted: bool,
    alert_suppressed_by_cooldown: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    alert_command_exit: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    restart_command_exit: Option<i32>,
}

fn main() {
    match run() {
        Ok(code) => exit(code),
        Err(message) => {
            eprintln!("orc-liveness-watchdog: ERROR: {message}");
            exit(EXIT_ERROR);
        }
    }
}

fn run() -> Result<i32, String> {
    let Some(args) = parse_args(env::args().skip(1))? else {
        print!("{USAGE}");
        return Ok(EXIT_LIVE);
    };

    let (rows, db_error) = load_active_sessions(&args.index_db);
    let observations = match &rows {
        Some(rows) => observe_sessions(rows, &args.proc_root, &args.expect_command),
        None => Vec::new(),
    };
    let state = classify(rows.as_deref(), db_error.as_deref(), &observations);

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("read system clock: {error}"))?
        .as_secs();

    let mut alerted = false;
    let mut suppressed = false;
    let mut alert_exit = None;
    let mut restart_exit = None;

    if !state.is_healthy() {
        if args.dry_run || cooldown_elapsed(&args.cooldown_stamp, now, args.cooldown_secs) {
            alerted = true;
            if !args.dry_run {
                write_atomic(&args.cooldown_stamp, &format!("{now}\n"))?;
                if let Some(command) = &args.alert_command {
                    alert_exit = Some(run_shell(command)?);
                }
                if let Some(command) = &args.restart_command {
                    if state.exit_code() == EXIT_DEAD {
                        restart_exit = Some(run_shell(command)?);
                    }
                }
            }
        } else {
            suppressed = true;
        }
    }

    let live_count = observations
        .iter()
        .filter(|observation| observation.verdict == SessionVerdict::Live.as_str())
        .count();
    let record = HealthRecord {
        schema_version: 1,
        checked_at: format_utc(now),
        checked_at_epoch: now,
        state: state.as_str(),
        healthy: state.is_healthy(),
        summary: state.summary(),
        index_db: args.index_db.display().to_string(),
        proc_root: args.proc_root.display().to_string(),
        expect_command: &args.expect_command,
        active_session_count: rows.as_ref().map_or(0, |rows| rows.len()),
        live_session_count: live_count,
        observations: &observations,
        db_error,
        alerted,
        alert_suppressed_by_cooldown: suppressed,
        alert_command_exit: alert_exit,
        restart_command_exit: restart_exit,
    };
    let line = serde_json::to_string(&record)
        .map_err(|error| format!("serialize health record: {error}"))?;

    if args.dry_run {
        println!("{line}");
    } else {
        append_line(&args.log, &line)?;
        write_atomic(
            &args.status_file,
            &format!(
                "{} {} active={} live={} alerted={} {}\n",
                record.checked_at,
                record.state,
                record.active_session_count,
                record.live_session_count,
                alerted,
                record.summary
            ),
        )?;
        println!(
            "state={} active={} live={} alerted={} log={}",
            record.state,
            record.active_session_count,
            record.live_session_count,
            alerted,
            args.log.display()
        );
    }

    Ok(state.exit_code())
}

fn parse_args(arguments: impl Iterator<Item = String>) -> Result<Option<Args>, String> {
    let home = env::var("HOME").map_err(|_| "HOME is not set".to_string())?;
    let home = PathBuf::from(home);
    let state_dir = home.join(".local/state");

    let mut args = Args {
        index_db: home.join(".orc/index.db"),
        log: state_dir.join("hermit-orc-liveness.jsonl"),
        status_file: state_dir.join("hermit-orc-liveness.status"),
        cooldown_stamp: state_dir.join("hermit-orc-liveness.last-alarm"),
        cooldown_secs: DEFAULT_COOLDOWN_SECS,
        proc_root: PathBuf::from("/proc"),
        expect_command: DEFAULT_EXPECT_COMMAND.to_string(),
        alert_command: None,
        restart_command: None,
        dry_run: false,
    };
    let mut arguments = arguments;

    while let Some(argument) = arguments.next() {
        let mut next = |flag: &str| -> Result<String, String> {
            arguments
                .next()
                .ok_or_else(|| format!("{flag} requires a value"))
        };
        match argument.as_str() {
            "-h" | "--help" => return Ok(None),
            "--index-db" => args.index_db = PathBuf::from(next("--index-db")?),
            "--log" => args.log = PathBuf::from(next("--log")?),
            "--status-file" => args.status_file = PathBuf::from(next("--status-file")?),
            "--cooldown-stamp" => args.cooldown_stamp = PathBuf::from(next("--cooldown-stamp")?),
            "--cooldown-secs" => {
                let value = next("--cooldown-secs")?;
                args.cooldown_secs = value
                    .parse::<u64>()
                    .map_err(|_| format!("invalid --cooldown-secs value {value:?}"))?;
            }
            "--proc-root" => args.proc_root = PathBuf::from(next("--proc-root")?),
            "--expect-command" => args.expect_command = next("--expect-command")?,
            "--alert-command" => args.alert_command = Some(next("--alert-command")?),
            "--restart-command" => args.restart_command = Some(next("--restart-command")?),
            "--dry-run" => args.dry_run = true,
            _ => return Err(format!("unknown argument {argument:?}\n{USAGE}")),
        }
    }

    Ok(Some(args))
}

/// Returns `(rows, db_error)`. `rows` is `None` exactly when the database could
/// not be consulted at all, which is the UNKNOWN case; an empty `Some(vec![])`
/// means the database answered and the answer was "no active session".
fn load_active_sessions(index_db: &Path) -> (Option<Vec<SessionRow>>, Option<String>) {
    if !index_db.exists() {
        return (
            None,
            Some(format!("index database not found: {}", index_db.display())),
        );
    }
    let output = Command::new("sqlite3")
        .arg("-readonly")
        .arg("-json")
        .arg(index_db)
        .arg("SELECT id, name, pid, cwd, updated_at FROM sessions WHERE status = 'active';")
        .output();
    let output = match output {
        Ok(output) => output,
        Err(error) => return (None, Some(format!("start sqlite3: {error}"))),
    };
    if !output.status.success() {
        return (
            None,
            Some(format!(
                "sqlite3 exited {}: {}",
                output.status.code().unwrap_or(-1),
                String::from_utf8_lossy(&output.stderr).trim()
            )),
        );
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let text = text.trim();
    // sqlite3 -json prints nothing at all for a zero-row result set.
    if text.is_empty() {
        return (Some(Vec::new()), None);
    }
    match serde_json::from_str::<Vec<SessionRow>>(text) {
        Ok(rows) => (Some(rows), None),
        Err(error) => (None, Some(format!("parse sqlite3 JSON output: {error}"))),
    }
}

fn observe_sessions(rows: &[SessionRow], proc_root: &Path, expect: &str) -> Vec<Observation> {
    rows.iter()
        .map(|row| {
            let proc = match row.pid {
                Some(pid) if pid > 0 => observe_pid(proc_root, pid, expect),
                _ => ProcObservation {
                    pid_alive: false,
                    cmdline_argv0: None,
                    cmdline_basename: None,
                    is_expected_command: false,
                    starttime_ticks: None,
                },
            };
            let verdict = verdict_for(row.pid, &proc);
            Observation {
                session_id: row.id.clone(),
                session_name: row.name.clone(),
                pid: row.pid,
                cwd: row.cwd.clone(),
                session_updated_at: row.updated_at.clone(),
                proc,
                verdict: verdict.as_str(),
            }
        })
        .collect()
}

fn verdict_for(pid: Option<i64>, proc: &ProcObservation) -> SessionVerdict {
    match pid {
        None => SessionVerdict::NoPid,
        Some(pid) if pid <= 0 => SessionVerdict::NoPid,
        Some(_) if !proc.pid_alive => SessionVerdict::StalePid,
        Some(_) if proc.is_expected_command => SessionVerdict::Live,
        Some(_) => SessionVerdict::PidRecycled,
    }
}

fn observe_pid(proc_root: &Path, pid: i64, expect: &str) -> ProcObservation {
    let directory = proc_root.join(pid.to_string());
    // /proc/<pid>/cmdline is read rather than kill(pid, 0) on purpose: one read
    // answers BOTH "does this PID exist" and "is it the program we recorded",
    // and the second question is the one that survives PID recycling.
    let Ok(bytes) = fs::read(directory.join("cmdline")) else {
        // A live kernel thread has an empty cmdline, and a process can exit
        // between the two reads. Fall back to directory existence so that a
        // read failure is not silently upgraded into "definitely dead".
        let alive = directory.is_dir();
        return ProcObservation {
            pid_alive: alive,
            cmdline_argv0: None,
            cmdline_basename: None,
            is_expected_command: false,
            starttime_ticks: read_starttime(&directory),
        };
    };
    let argv0 = bytes
        .split(|byte| *byte == 0)
        .next()
        .map(|slice| String::from_utf8_lossy(slice).into_owned())
        .filter(|value| !value.is_empty());
    let basename = argv0.as_deref().map(|value| {
        Path::new(value)
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_else(|| value.to_string())
    });
    ProcObservation {
        pid_alive: true,
        is_expected_command: basename.as_deref() == Some(expect),
        cmdline_argv0: argv0,
        cmdline_basename: basename,
        starttime_ticks: read_starttime(&directory),
    }
}

/// `/proc/<pid>/stat` field 22. The `comm` field is parenthesised and may itself
/// contain spaces and parentheses, so the split starts after the LAST `)`.
fn read_starttime(directory: &Path) -> Option<u64> {
    let text = fs::read_to_string(directory.join("stat")).ok()?;
    let tail = &text[text.rfind(')')? + 1..];
    tail.split_whitespace().nth(19)?.parse::<u64>().ok()
}

fn classify(
    rows: Option<&[SessionRow]>,
    db_error: Option<&str>,
    observations: &[Observation],
) -> State {
    let Some(rows) = rows else {
        return match db_error {
            Some(message) if message.starts_with("index database not found") => {
                State::UnknownDbMissing
            }
            _ => State::UnknownDbUnreadable,
        };
    };
    if rows.is_empty() {
        return State::DeadNoActiveSession;
    }
    if observations
        .iter()
        .any(|observation| observation.verdict == SessionVerdict::Live.as_str())
    {
        return State::Live;
    }
    // No live session. Report the MOST diagnostic of the failure classes present
    // rather than collapsing them: a recycled PID and a vanished PID need
    // different investigations, and folding them loses that.
    if observations
        .iter()
        .any(|observation| observation.verdict == SessionVerdict::PidRecycled.as_str())
    {
        return State::DeadPidRecycled;
    }
    if observations
        .iter()
        .any(|observation| observation.verdict == SessionVerdict::StalePid.as_str())
    {
        return State::DeadStalePid;
    }
    State::DeadNoPid
}

fn cooldown_elapsed(stamp: &Path, now: u64, cooldown_secs: u64) -> bool {
    if cooldown_secs == 0 {
        return true;
    }
    let last = fs::read_to_string(stamp)
        .ok()
        .and_then(|text| text.trim().parse::<u64>().ok())
        .unwrap_or(0);
    now.saturating_sub(last) >= cooldown_secs
}

fn run_shell(command: &str) -> Result<i32, String> {
    let status = Command::new("sh")
        .arg("-c")
        .arg(command)
        .status()
        .map_err(|error| format!("start command {command:?}: {error}"))?;
    Ok(status.code().unwrap_or(-1))
}

fn append_line(path: &Path, line: &str) -> Result<(), String> {
    ensure_parent(path)?;
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|error| format!("open log {}: {error}", path.display()))?;
    writeln!(file, "{line}").map_err(|error| format!("append log {}: {error}", path.display()))
}

fn write_atomic(path: &Path, contents: &str) -> Result<(), String> {
    ensure_parent(path)?;
    let temporary = path.with_extension(format!("tmp.{}", std::process::id()));
    fs::write(&temporary, contents)
        .map_err(|error| format!("write {}: {error}", temporary.display()))?;
    fs::rename(&temporary, path).map_err(|error| {
        format!(
            "atomically publish {} -> {}: {error}",
            temporary.display(),
            path.display()
        )
    })
}

fn ensure_parent(path: &Path) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("create directory {}: {error}", parent.display()))?;
        }
    }
    Ok(())
}

fn format_utc(epoch_secs: u64) -> String {
    // Civil-from-days (Howard Hinnant's algorithm). Implemented inline to keep
    // this watchdog dependency-light: it must build and run even when the rest
    // of the workspace's tooling does not.
    let days = (epoch_secs / 86_400) as i64;
    let seconds_of_day = epoch_secs % 86_400;
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let day_of_era = z.rem_euclid(146_097);
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let mp = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if month <= 2 { year + 1 } else { year };
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        year,
        month,
        day,
        seconds_of_day / 3_600,
        (seconds_of_day % 3_600) / 60,
        seconds_of_day % 60
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build a fixture proc tree. `cmdline` is written NUL-separated exactly as
    /// the kernel does, so the parser is exercised on the real byte shape.
    fn plant_process(root: &Path, pid: i64, argv: &[&str], starttime: u64) {
        let directory = root.join(pid.to_string());
        fs::create_dir_all(&directory).unwrap();
        let mut bytes = Vec::new();
        for argument in argv {
            bytes.extend_from_slice(argument.as_bytes());
            bytes.push(0);
        }
        fs::write(directory.join("cmdline"), bytes).unwrap();
        // 52 fields; comm deliberately contains a space and parentheses to prove
        // the field-22 parser does not naively split on whitespace.
        let mut stat = format!("{pid} (weird (comm) name) S 1 1 1 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 {starttime}");
        for _ in 0..30 {
            stat.push_str(" 0");
        }
        fs::write(directory.join("stat"), stat).unwrap();
    }

    fn temp_root(label: &str) -> PathBuf {
        let root = env::temp_dir().join(format!(
            "orc-liveness-watchdog-test-{}-{}",
            label,
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        root
    }

    fn session(pid: Option<i64>) -> SessionRow {
        SessionRow {
            id: "4fb50e87-5d91-4294-88b2-afeedf6cc917".to_string(),
            name: "hermit".to_string(),
            pid,
            cwd: Some("/home/example/work/dev-hermit".to_string()),
            updated_at: Some("2026-08-07T01:02:30Z".to_string()),
        }
    }

    fn classify_with(rows: Vec<SessionRow>, proc_root: &Path) -> (State, Vec<Observation>) {
        let observations = observe_sessions(&rows, proc_root, DEFAULT_EXPECT_COMMAND);
        (classify(Some(&rows), None, &observations), observations)
    }

    // ---- POSITIVE CONTROL: the qualifying case must FIRE, not sit inert. ----

    #[test]
    fn live_orc_process_is_not_flagged() {
        let root = temp_root("live");
        plant_process(&root, 2_592_813, &["/home/example/orc-bin/orc", "--db", "hermit"], 11_097_326);
        let (state, observations) = classify_with(vec![session(Some(2_592_813))], &root);
        assert_eq!(state, State::Live);
        assert!(state.is_healthy());
        assert_eq!(state.exit_code(), EXIT_LIVE);
        assert_eq!(observations[0].verdict, "live");
        assert!(observations[0].proc.pid_alive);
        assert_eq!(observations[0].proc.cmdline_basename.as_deref(), Some("orc"));
        // The record must carry the observed PID state, not merely a boolean.
        assert_eq!(observations[0].proc.starttime_ticks, Some(11_097_326));
        fs::remove_dir_all(&root).unwrap();
    }

    // ---- NEGATIVE CONTROLS: each violating case must be REFUSED. ----

    #[test]
    fn stale_pid_with_surviving_active_row_is_detected() {
        let root = temp_root("stale");
        // Plant nothing: the row claims a PID that /proc does not have.
        let (state, observations) = classify_with(vec![session(Some(2_592_813))], &root);
        assert_eq!(state, State::DeadStalePid);
        assert!(!state.is_healthy());
        assert_eq!(state.exit_code(), EXIT_DEAD);
        assert_eq!(observations[0].verdict, "stale_pid");
        assert!(!observations[0].proc.pid_alive);
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn recycled_pid_is_not_mistaken_for_a_live_orc() {
        let root = temp_root("recycled");
        // The exact defect a `kill -0` liveness check would call healthy.
        plant_process(&root, 2_592_813, &["/usr/bin/python3", "-m", "http.server"], 99_999);
        let (state, observations) = classify_with(vec![session(Some(2_592_813))], &root);
        assert_eq!(state, State::DeadPidRecycled);
        assert_eq!(state.exit_code(), EXIT_DEAD);
        assert_eq!(observations[0].verdict, "pid_recycled");
        assert!(observations[0].proc.pid_alive, "the PID really is alive");
        assert!(!observations[0].proc.is_expected_command);
        assert_eq!(
            observations[0].proc.cmdline_basename.as_deref(),
            Some("python3"),
            "the log must name what the PID actually is"
        );
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn active_row_with_null_pid_is_dead_not_live() {
        let root = temp_root("nullpid");
        let (state, observations) = classify_with(vec![session(None)], &root);
        assert_eq!(state, State::DeadNoPid);
        assert_eq!(state.exit_code(), EXIT_DEAD);
        assert_eq!(observations[0].verdict, "no_pid");
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn zero_active_rows_is_dead_not_live() {
        let root = temp_root("noactive");
        let (state, _) = classify_with(Vec::new(), &root);
        assert_eq!(state, State::DeadNoActiveSession);
        assert_eq!(state.exit_code(), EXIT_DEAD);
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn one_live_session_among_dead_ones_is_live() {
        let root = temp_root("mixed");
        plant_process(&root, 4_242, &["/home/example/orc-bin/orc"], 5);
        let mut dead = session(Some(999_999));
        dead.id = "dead-row".to_string();
        let mut live = session(Some(4_242));
        live.id = "live-row".to_string();
        let (state, _) = classify_with(vec![dead, live], &root);
        assert_eq!(state, State::Live);
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn recycled_pid_outranks_stale_pid_in_the_headline() {
        // Both classes present, none live: the headline must name the class that
        // changes the diagnosis, not the first row encountered.
        let root = temp_root("rank");
        plant_process(&root, 4_243, &["/usr/bin/sleep", "999"], 5);
        let mut stale = session(Some(999_999));
        stale.id = "stale-row".to_string();
        let mut recycled = session(Some(4_243));
        recycled.id = "recycled-row".to_string();
        let (state, _) = classify_with(vec![stale, recycled], &root);
        assert_eq!(state, State::DeadPidRecycled);
        fs::remove_dir_all(&root).unwrap();
    }

    // ---- UNKNOWN must not be laundered into healthy. ----

    #[test]
    fn missing_database_is_unknown_not_healthy() {
        let root = temp_root("nodb");
        let (rows, error) = load_active_sessions(&root.join("absent.db"));
        assert!(rows.is_none());
        let state = classify(rows.as_deref(), error.as_deref(), &[]);
        assert_eq!(state, State::UnknownDbMissing);
        assert!(!state.is_healthy(), "UNKNOWN must never read as healthy");
        assert_eq!(state.exit_code(), EXIT_UNKNOWN);
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn unreadable_database_is_unknown_not_healthy() {
        let root = temp_root("baddb");
        let path = root.join("garbage.db");
        fs::write(&path, b"this is not a sqlite database").unwrap();
        let (rows, error) = load_active_sessions(&path);
        assert!(rows.is_none(), "a corrupt database must not answer");
        let state = classify(rows.as_deref(), error.as_deref(), &[]);
        assert_eq!(state, State::UnknownDbUnreadable);
        assert!(!state.is_healthy());
        assert_eq!(state.exit_code(), EXIT_UNKNOWN);
        fs::remove_dir_all(&root).unwrap();
    }

    // ---- The sqlite read path itself, against a real fixture database. ----

    #[test]
    fn sqlite_query_selects_only_active_rows_and_preserves_null_pid() {
        let root = temp_root("sqlite");
        let path = root.join("index.db");
        let status = Command::new("sqlite3")
            .arg(&path)
            .arg(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT, status TEXT, pid INTEGER, \
                 cwd TEXT, updated_at TEXT); \
                 INSERT INTO sessions VALUES ('a','hermit','active',4242,'/tmp','t1'); \
                 INSERT INTO sessions VALUES ('b','old','closed',NULL,NULL,'t0'); \
                 INSERT INTO sessions VALUES ('c','nopid','active',NULL,'/tmp','t2');",
            )
            .status()
            .unwrap();
        assert!(status.success());
        let (rows, error) = load_active_sessions(&path);
        assert_eq!(error, None);
        let rows = rows.unwrap();
        assert_eq!(rows.len(), 2, "the closed row must not be selected");
        assert_eq!(rows[0].pid, Some(4242));
        assert_eq!(rows[1].pid, None, "NULL pid must survive as None, not 0");
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn empty_result_set_answers_rather_than_erroring() {
        let root = temp_root("emptyset");
        let path = root.join("index.db");
        Command::new("sqlite3")
            .arg(&path)
            .arg(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT, status TEXT, pid INTEGER, \
                 cwd TEXT, updated_at TEXT); \
                 INSERT INTO sessions VALUES ('b','old','closed',NULL,NULL,'t0');",
            )
            .status()
            .unwrap();
        let (rows, error) = load_active_sessions(&path);
        assert_eq!(error, None, "zero rows is an ANSWER, not a failure");
        assert_eq!(rows.unwrap().len(), 0);
        fs::remove_dir_all(&root).unwrap();
    }

    // ---- Cooldown must suppress repeats without suppressing the first alarm. ----

    #[test]
    fn cooldown_suppresses_repeats_but_never_the_first_alarm() {
        let root = temp_root("cooldown");
        let stamp = root.join("last-alarm");
        assert!(
            cooldown_elapsed(&stamp, 1_000_000, 3600),
            "a missing stamp must never suppress the first alarm"
        );
        fs::write(&stamp, "1000000\n").unwrap();
        assert!(!cooldown_elapsed(&stamp, 1_000_100, 3600));
        assert!(cooldown_elapsed(&stamp, 1_003_600, 3600));
        assert!(
            cooldown_elapsed(&stamp, 1_000_100, 0),
            "cooldown 0 disables suppression"
        );
        fs::write(&stamp, "corrupt\n").unwrap();
        assert!(
            cooldown_elapsed(&stamp, 1_000_100, 3600),
            "an unparseable stamp must fail OPEN (alert), not swallow the alarm"
        );
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn timestamps_render_as_utc_iso8601() {
        assert_eq!(format_utc(0), "1970-01-01T00:00:00Z");
        assert_eq!(format_utc(1_767_225_600), "2026-01-01T00:00:00Z");
        assert_eq!(format_utc(1_785_000_123), "2026-07-25T17:22:03Z");
        // Leap day, and the day after it, since the civil-from-days algorithm
        // is exactly where an off-by-one would hide.
        assert_eq!(format_utc(1_709_164_800), "2024-02-29T00:00:00Z");
        assert_eq!(format_utc(1_709_251_200), "2024-03-01T00:00:00Z");
    }

    #[test]
    fn expected_command_is_matched_on_basename_not_substring() {
        let root = temp_root("basename");
        // "orchestrator" contains "orc"; a substring check would call this live.
        plant_process(&root, 7_777, &["/opt/orchestration/bin/orchestrator"], 1);
        let (state, observations) = classify_with(vec![session(Some(7_777))], &root);
        assert_eq!(state, State::DeadPidRecycled);
        assert_eq!(
            observations[0].proc.cmdline_basename.as_deref(),
            Some("orchestrator")
        );
        fs::remove_dir_all(&root).unwrap();
    }

    // ---- PORTABILITY: this file must not re-acquire an owner-specific path ----

    /// Scan text the way `scripts/check-portable-paths.sh` does, narrowed to the
    /// three rules that actually bit this file. Returns offending `(line, text)`.
    ///
    /// Kept as a function so the test can bracket BOTH sides: a scanner that
    /// only ever returns empty would pass forever without checking anything.
    /// Substring match bounded by non-identifier characters on both sides.
    fn contains_word(haystack: &str, needle: &str) -> bool {
        let ident = |c: char| c.is_alphanumeric() || c == '_';
        let mut from = 0;
        while let Some(at) = haystack[from..].find(needle) {
            let start = from + at;
            let end = start + needle.len();
            let before_ok = start == 0 || !haystack[..start].chars().next_back().is_some_and(ident);
            let after_ok = end >= haystack.len() || !haystack[end..].chars().next().is_some_and(ident);
            if before_ok && after_ok {
                return true;
            }
            from = start + needle.len();
        }
        false
    }

    fn owner_specific_lines(text: &str) -> Vec<(usize, String)> {
        // Needles are assembled at runtime. Spelling them literally here would
        // plant the very strings this guards against, and the real gate --
        // which scans source text, not behaviour -- would flag this test.
        let owner = format!("{}{}", "new", "ton");
        let host = format!("{}{}", "dev", "big");
        let local_bin = concat!("/usr/", "local", "/bin/");
        // The real lint exempts these placeholder homes; mirror it or this
        // rejects the very fixtures we just made portable.
        let exempt = ["user", "test", "example"];

        let mut hits = Vec::new();
        for (index, line) in text.lines().enumerate() {
            let lower = line.to_lowercase();
            // The owner name must match on WORD BOUNDARIES, exactly as the real
            // lint does -- its rule brackets the name with non-identifier
            // characters on both sides. A naive substring check flags the
            // GitHub org in this file's own Documentation= URL, which embeds
            // the owner name inside a longer identifier and is a repository
            // name, not an owner path. (The comment explaining this cannot
            // spell the name either: the real gate scans comments too, and
            // exempts only author/copyright headers.)
            let mut bad = contains_word(&lower, &owner) || lower.contains(&host);
            if lower.contains(local_bin) {
                // A PATH element (":/usr/local/bin:") has no trailing slash and
                // is fine; a path INTO that directory is not.
                bad = true;
            }
            for prefix in ["/home/", "/users/"] {
                let mut rest = lower.as_str();
                while let Some(at) = rest.find(prefix) {
                    let tail = &rest[at + prefix.len()..];
                    let name: String =
                        tail.chars().take_while(|c| c.is_alphanumeric() || *c == '_' || *c == '-').collect();
                    if !name.is_empty() && !exempt.contains(&name.as_str()) {
                        bad = true;
                    }
                    rest = &tail[name.len().min(tail.len())..];
                }
            }
            if bad {
                hits.push((index + 1, line.to_string()));
            }
        }
        hits
    }

    /// POSITIVE CONTROL for the scanner itself: it must FIRE on planted cases.
    #[test]
    fn owner_path_scanner_is_not_inert() {
        let owner = format!("{}{}", "new", "ton");
        let planted = format!(
            "let a = \"/home/{owner}/work\";\nlet b = \"{}orchestrator\";\nlet c = \"host-{}014\";\n",
            concat!("/usr/", "local", "/bin/"),
            format!("{}{}", "dev", "big"),
        );
        let hits = owner_specific_lines(&planted);
        assert_eq!(hits.len(), 3, "scanner missed a planted violation: {hits:?}");

        // ...and must NOT fire on the placeholder homes the real lint exempts,
        // or it would reject this file's own portable fixtures.
        let clean = format!(
            "let a = \"/home/example/orc-bin/orc\";\n\
             let b = \"/opt/orchestration/bin/orchestrator\";\n\
             // https://github.com/rr{}/dev-hermit/blob/main/x.rs\n\
             let d = \"a:/usr/local/bin:b\";\n",
            owner
        );
        let clean = clean.as_str();
        assert!(
            owner_specific_lines(clean).is_empty(),
            "scanner rejected an exempt placeholder home: {:?}",
            owner_specific_lines(clean)
        );
    }

    /// The actual guard: this source and its unit template carry no owner path.
    ///
    /// `file!()` resolves to the real script path under `rust-script`, so this
    /// reads the same bytes the CI gate reads rather than a copy.
    #[test]
    fn no_owner_specific_paths_in_this_script_or_its_unit() {
        let source_path = Path::new(file!());
        let source = fs::read_to_string(source_path)
            .unwrap_or_else(|e| panic!("read own source {}: {e}", source_path.display()));
        let hits = owner_specific_lines(&source);
        assert!(hits.is_empty(), "owner-specific path(s) in the watchdog: {hits:?}");

        // The unit template is where the main-red actually came from, so guard
        // it here too instead of trusting that someone re-runs the shell gate.
        let unit = source_path
            .parent()
            .expect("script has a parent directory")
            .join("systemd/hermit-orc-liveness.service");
        let unit_text = fs::read_to_string(&unit)
            .unwrap_or_else(|e| panic!("read unit {}: {e}", unit.display()));
        let unit_hits = owner_specific_lines(&unit_text);
        assert!(unit_hits.is_empty(), "owner-specific path(s) in the unit: {unit_hits:?}");

        // And it must use the systemd specifier rather than a shell variable,
        // which systemd would take literally in ExecStart/WorkingDirectory.
        assert!(
            unit_text.contains("ExecStart=%h/"),
            "unit must derive ExecStart from %h"
        );
        // Scoped to DIRECTIVE lines: the unit's own comment explains why %h is
        // used instead of $HOME, and a blunt contains() would reject the
        // explanation along with the mistake it warns about.
        let shell_var_directives: Vec<&str> = unit_text
            .lines()
            .map(str::trim)
            .filter(|line| !line.starts_with('#') && !line.is_empty())
            .filter(|line| line.contains("$HOME"))
            .collect();
        assert!(
            shell_var_directives.is_empty(),
            "systemd does not expand shell variables in ExecStart/WorkingDirectory; \
             use %h instead: {shell_var_directives:?}"
        );
    }
}
