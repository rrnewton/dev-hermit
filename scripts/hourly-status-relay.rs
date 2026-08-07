#!/usr/bin/env rust-script
//! EXTERNAL durable driver for the hourly owner-status report.
//!
//! ## The defect this replaces, measured
//!
//! The hourly status lived in an ORC session workflow:
//! `wf.loop(async () => { await wf.sleep(36e5); await orc.sendWakeup(...) })`.
//! On 2026-08-07 that workflow reported `state=0 alive=True` and its logs read
//! `[sleeping]` — and it had delivered nothing for 9h55m.
//! `ai_docs/status-log/status-log.jsonl` jumped straight from
//! `2026-08-06T15:05:12Z` to `2026-08-07T01:00:36Z`, so roughly nine hourly
//! reports never happened while every observable said "healthy".
//!
//! The mechanism is in ORC's own restore path. At the 00:35 cold boot it logged
//! `cleared persisted internal sleep steps before workflow restore`
//! (`workflow_id=hourly-status-report`, `removed=1`), then
//! `WARN restore: timed out waiting for restored effect registration
//! effect_name=sleep`, and the run came back at `attempt_no=1` with
//! `available_at = started_at + one full hour` exactly. **A restore discards
//! elapsed sleep time and re-arms the whole period.** Boots more frequent than
//! the period therefore starve the loop forever, and `alive=true` stays true the
//! entire time because the workflow really is alive — it is just permanently one
//! full hour away from firing.
//!
//! So the fix is not "restart the workflow". The fix is to move the CLOCK out of
//! the thing that keeps restarting. This driver is fired by
//! `hermit-hourly-status.timer` (systemd --user, `Persistent=true`, Linger), the
//! same external-driver pattern already used by `alignment-reminder-relay.sh`
//! and `orc-liveness-watchdog.rs`.
//!
//! ## What it does and, importantly, does not do
//!
//! It does NOT compose the status. Synthesis needs the coordinator's judgement,
//! so each firing WAKES the coordinator with `hourly_status_prompt.md` and asks
//! it to send one status and then call `scripts/status-log.rs` with the exact
//! delivered text. This driver owns the schedule and the deduplication; the
//! prompt owns the content.
//!
//! ## Deduplication is keyed on the SCHEDULED HOUR, not on "have I run"
//!
//! `Persistent=true` means a missed tick runs after downtime — which is the
//! whole point, and also the thing that can double-send. A run is therefore
//! identified by the UTC hour bucket it belongs to (`YYYY-MM-DDTHH`), and the
//! claim is a file created with `O_EXCL` under `hours/`. Second invocation in
//! the same hour, catch-up run, manual `systemctl start`, two racing runs — all
//! collapse onto one delivery.
//!
//! Bucketing by truncation is safe because systemd fires a calendar timer AT OR
//! AFTER its calendar point, never early; `AccuracySec` can only delay. A tick
//! for `01:00` can therefore land at `01:00:12` but not at `00:59:58`.
//!
//! ## Three outcomes that must never be rendered the same
//!
//! 1. **skipped** — a precondition was not met (no tmux socket, i.e. ORC is
//!    down; unreadable prompt). Nothing was delivered, so the hour is NOT
//!    claimed and a later invocation in the same hour may still deliver.
//! 2. **wake-failed** — the hour was claimed and the relay ran and failed. The
//!    claim is RELEASED so the hour can retry. This is safe only because
//!    `orc-hermit-msg.py` fails CLOSED: it refuses and reports nonzero rather
//!    than half-delivering (it declines while the coordinator pane is
//!    streaming). If that ever stops being true, `--no-release-on-failure`
//!    makes a failed hour terminal instead of retryable.
//! 3. **delivered** — the claim is finalized and that hour is closed forever.
//!
//! Collapsing 1 and 2 into "failed" would either burn hours that were never
//! attempted or retry hours that already went out. Both are silent.
//!
//! ## The heartbeat is written BEFORE anything that can fail
//!
//! Every tick appends exactly one `tick start` line to the invocation log before
//! any check runs, so "the timer fired" is observable independently of "a status
//! was delivered". Without that separation a dead timer and a quiet hour look
//! identical — which is precisely how the original outage hid for ten hours.
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
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::process::{exit, Command};
use std::time::{SystemTime, UNIX_EPOCH};

const GCHAT_SPACE: &str = "spaces/AAQAA6Irlwg";
const DEFAULT_STALE_PENDING_SECS: u64 = 900;

const EXIT_OK: i32 = 0;
const EXIT_ERROR: i32 = 3;

const USAGE: &str = r#"Usage: hourly-status-relay.rs [OPTIONS]

Fire one hourly owner-status wake, at most once per scheduled UTC hour.

Options:
  --root PATH             dev-hermit checkout. Defaults to walking up from the
                          working directory for .gitmodules + AGENTS.md.
  --state-dir PATH        Root of this driver's durable state
                          (default: $HOME/.local/state/hermit-hourly-status).
                          Holds invocations.log, latest.status, hours/, wakes/.
  --prompt-file PATH      Coordinator wake prompt
                          (default: <root>/hourly_status_prompt.md).
  --relay PATH            orc-hermit-msg.py used to type into the coordinator
                          pane (default: <root>/scripts/orc-hermit-msg.py).
  --socket PATH           tmux server socket whose ABSENCE means ORC is down and
                          the tick is skipped
                          (default: /run/user/<uid>/orc-tmux/tmux-<uid>/default).
  --relay-command CMD     Override the delivery command. Run as
                          `sh -c '<CMD> "$0"' <message-file>`, so CMD receives
                          the message file as its final argument. Exists so the
                          tests can bracket delivery both ways against a fixture
                          without typing into a live coordinator pane.
  --hour YYYY-MM-DDTHH    Force the scheduled hour instead of deriving it from
                          the clock. Testing and manual catch-up only.
  --now-epoch N           Force "now" (seconds since epoch). Testing only.
  --stale-pending-secs N  A `pending` claim older than this was left by a run
                          that died mid-flight and may be reclaimed
                          (default: 900). 0 means never reclaim.
  --no-release-on-failure Keep the claim on a failed wake, making that hour
                          terminal rather than retryable.
  --dry-run               Decide and print, but write no state and run no relay.
  -h, --help              Print this pure help text.

Exit codes:
  0  the tick ran and reached a recorded outcome (delivered, duplicate-hour,
     in-flight, skipped, wake-failed, or dry-run). All of these are successful
     runs OF THE DRIVER; the outcome is in the log, not in the exit status.
  3  usage or internal error -- the driver itself is misconfigured.
"#;

#[derive(Debug, Clone)]
struct Args {
    state_dir: PathBuf,
    prompt_file: PathBuf,
    relay: PathBuf,
    socket: PathBuf,
    relay_command: Option<String>,
    hour: Option<String>,
    now_epoch: Option<u64>,
    stale_pending_secs: u64,
    release_on_failure: bool,
    dry_run: bool,
}

/// One scheduled hour's record. `state` is the deduplication authority; the
/// remaining fields exist so a later reader can tell WHY an hour looks the way
/// it does without re-deriving it from the invocation log.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct Claim {
    hour: String,
    state: String,
    claimed_at: u64,
    claimed_by_pid: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    delivered_at: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    detail: Option<String>,
}

const STATE_PENDING: &str = "pending";
const STATE_DELIVERED: &str = "delivered";

/// What this invocation should do about its hour. Kept separate from the I/O so
/// the dedupe rule is unit-testable without a filesystem full of fixtures.
#[derive(Debug, Clone, PartialEq, Eq)]
enum Decision {
    /// No claim for this hour: deliver it.
    Deliver,
    /// A `pending` claim was left behind by a run that died; old enough to take.
    ReclaimStale { age_secs: u64 },
    /// This hour already went out. The negative case the whole design exists for.
    DuplicateHour,
    /// A `pending` claim is young enough that another run may still be working.
    InFlight { age_secs: u64 },
    /// The claim file exists but did not parse. A file we cannot read is a file
    /// whose `state` we cannot read, so it may be `delivered`; treating it as
    /// anything deliverable would risk the duplicate this design exists to
    /// exclude. Never deliverable, and never silently repaired.
    CorruptClaim { reason: String },
}

impl Decision {
    fn proceeds(&self) -> bool {
        matches!(self, Decision::Deliver | Decision::ReclaimStale { .. })
    }
}

/// Outcome of reading an hour's claim file. Distinguishing ABSENT from CORRUPT
/// is load-bearing: collapsing them (either way) is a double-send bug. Absent
/// means nobody has this hour; corrupt means somebody may have delivered it and
/// we can no longer prove otherwise.
#[derive(Debug, Clone, PartialEq, Eq)]
enum ClaimRead {
    Absent,
    Valid(Claim),
    Corrupt { reason: String },
}

/// THE dedupe rule. A delivered hour is closed forever; a pending hour is
/// someone else's until it is provably abandoned; an unreadable hour is nobody's
/// to take.
fn decide(existing: &ClaimRead, now: u64, stale_pending_secs: u64) -> Decision {
    let claim = match existing {
        ClaimRead::Absent => return Decision::Deliver,
        ClaimRead::Corrupt { reason } => {
            return Decision::CorruptClaim {
                reason: reason.clone(),
            };
        }
        ClaimRead::Valid(claim) => claim,
    };
    if claim.state == STATE_DELIVERED {
        return Decision::DuplicateHour;
    }
    // saturating_sub: a claim stamped in the future (clock step) reads as age 0,
    // i.e. in-flight, which is the conservative side -- it delays a status
    // rather than risking a second one.
    let age = now.saturating_sub(claim.claimed_at);
    if stale_pending_secs > 0 && age >= stale_pending_secs {
        Decision::ReclaimStale { age_secs: age }
    } else {
        Decision::InFlight { age_secs: age }
    }
}

/// Why a tick declined to even attempt delivery. Distinct from a failed
/// delivery: nothing was sent, so the hour stays open.
#[derive(Debug, Clone, PartialEq, Eq)]
enum Precondition {
    Ok,
    Skip { reason: String },
}

fn check_preconditions(args: &Args) -> Precondition {
    if !args.prompt_file.is_file() {
        return Precondition::Skip {
            reason: format!("prompt-unreadable path={}", args.prompt_file.display()),
        };
    }
    // A custom relay command is the test path; it does not need the real script
    // or a live tmux socket.
    if args.relay_command.is_some() {
        return Precondition::Ok;
    }
    if !args.relay.is_file() {
        return Precondition::Skip {
            reason: format!("relay-missing path={}", args.relay.display()),
        };
    }
    // A missing socket is the ROUTINE state between coordinator sessions, and
    // also exactly what a dead ORC looks like. Either way there is no pane to
    // type into, so this is a skip and never a failure: a failed unit is one
    // systemctl default away from not being retried.
    if !args.socket.exists() {
        return Precondition::Skip {
            reason: format!("no-tmux-socket socket={}", args.socket.display()),
        };
    }
    Precondition::Ok
}

// ---------------------------------------------------------------- time -----

/// UTC hour bucket, `YYYY-MM-DDTHH`. Truncating is correct here: see the module
/// header on why a calendar timer cannot fire early.
fn hour_bucket(epoch: u64) -> String {
    let (year, month, day) = civil_from_days((epoch / 86_400) as i64);
    let hour = (epoch % 86_400) / 3_600;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}")
}

fn format_utc(epoch: u64) -> String {
    let (year, month, day) = civil_from_days((epoch / 86_400) as i64);
    let seconds_of_day = epoch % 86_400;
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        seconds_of_day / 3_600,
        (seconds_of_day % 3_600) / 60,
        seconds_of_day % 60
    )
}

/// Howard Hinnant's civil-from-days. Avoids a chrono dependency for what is
/// ultimately one date format in a log line.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

fn now_epoch() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

// ------------------------------------------------------------ state I/O ----

fn claim_path(state_dir: &Path, hour: &str) -> PathBuf {
    state_dir.join("hours").join(format!("{hour}.json"))
}

fn wake_path(state_dir: &Path, hour: &str) -> PathBuf {
    state_dir.join("wakes").join(format!("{hour}.md"))
}

fn read_claim(state_dir: &Path, hour: &str) -> ClaimRead {
    let path = claim_path(state_dir, hour);
    let text = match fs::read_to_string(&path) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return ClaimRead::Absent,
        // An existing-but-unreadable file (permissions, I/O error) is NOT absent.
        // We cannot see its `state`, so it gets the same fail-closed treatment as
        // a corrupt one.
        Err(e) => {
            return ClaimRead::Corrupt {
                reason: format!("unreadable: {e}"),
            };
        }
    };
    // A corrupt claim is deliberately NOT treated as "no claim", and equally NOT
    // as a pending claim stamped at epoch 0. The epoch-0 fallback was the bug:
    // `serde_json::from_str` rejects TRAILING CONTENT, so appending one
    // human-readable line to an otherwise valid `delivered` claim turned it into
    // a synthetic pending@0, which then aged straight into `ReclaimStale` and
    // re-delivered an hour that had already gone out. Losing the `delivered`
    // state is precisely what must not happen, so corruption fails closed.
    match serde_json::from_str::<Claim>(&text) {
        Ok(claim) => ClaimRead::Valid(claim),
        Err(e) => ClaimRead::Corrupt {
            reason: format!("unparseable at line {} column {}: {e}", e.line(), e.column()),
        },
    }
}

fn write_atomic(path: &Path, contents: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create {}: {e}", parent.display()))?;
    }
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, contents).map_err(|e| format!("write {}: {e}", temporary.display()))?;
    fs::rename(&temporary, path).map_err(|e| format!("rename into {}: {e}", path.display()))
}

fn write_claim(state_dir: &Path, claim: &Claim) -> Result<(), String> {
    let body = serde_json::to_string(claim).map_err(|e| format!("serialize claim: {e}"))? + "\n";
    write_atomic(&claim_path(state_dir, &claim.hour), &body)
}

/// Create the claim with `O_EXCL`. `Ok(false)` means somebody else got there
/// first between our read and our write -- the race the dedupe must survive.
fn try_claim(state_dir: &Path, claim: &Claim, replace: bool) -> Result<bool, String> {
    let path = claim_path(state_dir, &claim.hour);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create {}: {e}", parent.display()))?;
    }
    if replace {
        let _ = fs::remove_file(&path);
    }
    let body = serde_json::to_string(claim).map_err(|e| format!("serialize claim: {e}"))? + "\n";
    match OpenOptions::new().write(true).create_new(true).open(&path) {
        Ok(mut file) => file
            .write_all(body.as_bytes())
            .map(|_| true)
            .map_err(|e| format!("write {}: {e}", path.display())),
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => Ok(false),
        Err(e) => Err(format!("create {}: {e}", path.display())),
    }
}

fn append_line(path: &Path, line: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create {}: {e}", parent.display()))?;
    }
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| format!("open {}: {e}", path.display()))?;
    writeln!(file, "{line}").map_err(|e| format!("append {}: {e}", path.display()))
}

// ------------------------------------------------------------- message -----

/// The header binds the wake to its scheduled hour and names the exact files
/// involved, so the coordinator can honour the one-status-per-hour rule and a
/// later investigator can reconstruct what was asked for.
fn build_message(hour: &str, now: u64, state_dir: &Path, prompt_body: &str) -> String {
    format!(
        "<!-- generated by scripts/hourly-status-relay.rs; do not edit -->\n\
         **SCHEDULED HOUR: `{hour}` (UTC).** Fired at {fired} by \
         `hermit-hourly-status.timer`, which is external to ORC and survives its restarts.\n\n\
         - Owner GChat space: `{space}`\n\
         - This hour's claim file: `{claim}`\n\
         - Structured log to append to: `ai_docs/status-log/status-log.jsonl` \
         via `scripts/status-log.rs`\n\
         - Driver invocation log: `{invlog}`\n\n\
         ---\n\n{prompt_body}",
        hour = hour,
        fired = format_utc(now),
        space = GCHAT_SPACE,
        claim = claim_path(state_dir, hour).display(),
        invlog = state_dir.join("invocations.log").display(),
        prompt_body = prompt_body,
    )
}

/// Run the delivery. Returns `(rc, first 160 chars of combined output)`.
fn run_relay(args: &Args, message_file: &Path) -> (i32, String) {
    let output = match &args.relay_command {
        Some(command) => Command::new("sh")
            .arg("-c")
            .arg(format!("{command} \"$0\""))
            .arg(message_file)
            .output(),
        None => Command::new("python3")
            .arg(&args.relay)
            .arg("--socket")
            .arg(&args.socket)
            .arg("--message-file")
            .arg(message_file)
            .output(),
    };
    match output {
        Ok(out) => {
            let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
            text.push_str(&String::from_utf8_lossy(&out.stderr));
            let summary: String = text
                .replace('\n', " ")
                .chars()
                .take(160)
                .collect::<String>()
                .trim()
                .to_string();
            (out.status.code().unwrap_or(-1), summary)
        }
        Err(e) => (-1, format!("spawn failed: {e}")),
    }
}

// ------------------------------------------------------------- the tick ----

#[derive(Debug, Clone, PartialEq, Eq)]
struct TickResult {
    hour: String,
    outcome: String,
    detail: String,
}

impl TickResult {
    fn line(&self, stamp: &str) -> String {
        format!(
            "{stamp} tick end hour={} outcome={} {}",
            self.hour, self.outcome, self.detail
        )
    }
}

/// The whole tick, as one in-process function so the tests exercise the real
/// control flow -- claim, deliver, finalize, release -- and not a reimplementation
/// of it.
fn execute(args: &Args) -> Result<TickResult, String> {
    let now = args.now_epoch.unwrap_or_else(now_epoch);
    let hour = args.hour.clone().unwrap_or_else(|| hour_bucket(now));
    let stamp = format_utc(now);
    let invocation_log = args.state_dir.join("invocations.log");

    // HEARTBEAT FIRST, before any check that can fail or return early. A flat
    // invocation log therefore means the TIMER is dead, and never merely that an
    // hour was quiet.
    if !args.dry_run {
        append_line(
            &invocation_log,
            &format!("{stamp} tick start hour={hour} pid={}", std::process::id()),
        )?;
    }

    let done = |outcome: &str, detail: String| -> TickResult {
        TickResult {
            hour: hour.clone(),
            outcome: outcome.to_string(),
            detail,
        }
    };

    if let Precondition::Skip { reason } = check_preconditions(args) {
        // Deliberately NOT claimed: nothing was delivered, so a later run in the
        // same hour must still be able to deliver.
        return Ok(done("skipped", format!("reason={reason} claimed=no")));
    }

    let existing = read_claim(&args.state_dir, &hour);
    let decision = decide(&existing, now, args.stale_pending_secs);
    match &decision {
        Decision::CorruptClaim { reason } => {
            // Actionable on purpose: name the file, because the ONLY safe repair
            // is a human deciding whether that hour actually went out. This tick
            // sends nothing and takes nothing.
            return Ok(done(
                "corrupt-claim",
                format!(
                    "reason={reason} path={} claimed=no-op delivered=no \
                     repair=inspect-file-and-restore-valid-json",
                    claim_path(&args.state_dir, &hour).display()
                ),
            ));
        }
        Decision::DuplicateHour => {
            let delivered = match &existing {
                ClaimRead::Valid(c) => c.delivered_at.map(format_utc),
                _ => None,
            }
            .unwrap_or_else(|| "unknown".to_string());
            return Ok(done(
                "duplicate-hour",
                format!("already-delivered-at={delivered} claimed=no-op"),
            ));
        }
        Decision::InFlight { age_secs } => {
            return Ok(done(
                "in-flight",
                format!("pending-age-secs={age_secs} claimed=no-op"),
            ));
        }
        other => debug_assert!(other.proceeds()),
    }

    let reclaimed = matches!(decision, Decision::ReclaimStale { .. });
    let prompt_body = fs::read_to_string(&args.prompt_file)
        .map_err(|e| format!("read {}: {e}", args.prompt_file.display()))?;
    let message = build_message(&hour, now, &args.state_dir, &prompt_body);

    if args.dry_run {
        return Ok(done(
            "dry-run",
            format!(
                "would-deliver reclaimed={reclaimed} message-bytes={}",
                message.len()
            ),
        ));
    }

    let claim = Claim {
        hour: hour.clone(),
        state: STATE_PENDING.to_string(),
        claimed_at: now,
        claimed_by_pid: std::process::id(),
        delivered_at: None,
        detail: if reclaimed {
            Some("reclaimed a stale pending claim".to_string())
        } else {
            None
        },
    };
    // The claim is taken BEFORE the relay runs. Claim-then-send can at worst lose
    // one hour; send-then-claim can double-send, and a duplicate owner status is
    // the failure this design is required to exclude.
    if !try_claim(&args.state_dir, &claim, reclaimed)? {
        return Ok(done(
            "in-flight",
            "reason=lost-claim-race claimed=no-op".to_string(),
        ));
    }

    let message_file = wake_path(&args.state_dir, &hour);
    write_atomic(&message_file, &message)?;

    let (rc, summary) = run_relay(args, &message_file);
    if rc == 0 {
        write_claim(
            &args.state_dir,
            &Claim {
                state: STATE_DELIVERED.to_string(),
                delivered_at: Some(now_epoch()),
                detail: None,
                ..claim
            },
        )?;
        return Ok(done(
            "delivered",
            format!("relay-rc=0 wake={}", message_file.display()),
        ));
    }

    // Fail-closed relay => nonzero means nothing was typed into the pane, so
    // releasing the hour cannot double-send. See the module header.
    let released = if args.release_on_failure {
        fs::remove_file(claim_path(&args.state_dir, &hour)).is_ok()
    } else {
        write_claim(
            &args.state_dir,
            &Claim {
                detail: Some(format!("wake failed rc={rc}: {summary}")),
                ..claim
            },
        )?;
        false
    };
    Ok(done(
        "wake-failed",
        format!("relay-rc={rc} released={released} detail={summary}"),
    ))
}

// ---------------------------------------------------------------- main -----

fn main() {
    match run() {
        Ok(code) => exit(code),
        Err(message) => {
            eprintln!("hourly-status-relay: {message}");
            exit(EXIT_ERROR);
        }
    }
}

fn run() -> Result<i32, String> {
    let args = parse_args()?;
    let stamp = format_utc(args.now_epoch.unwrap_or_else(now_epoch));
    let result = execute(&args)?;
    let line = result.line(&stamp);
    if args.dry_run {
        println!("{line}");
    } else {
        append_line(&args.state_dir.join("invocations.log"), &line)?;
        write_atomic(
            &args.state_dir.join("latest.status"),
            &format!("{line}\n"),
        )?;
    }
    println!(
        "hour={} outcome={} {}",
        result.hour, result.outcome, result.detail
    );
    Ok(EXIT_OK)
}

fn default_socket() -> PathBuf {
    // getuid without a libc dependency: /proc/self is owned by the caller.
    let uid = fs::metadata("/proc/self").map(|m| m.uid()).unwrap_or(0);
    PathBuf::from(format!("/run/user/{uid}/orc-tmux/tmux-{uid}/default"))
}

/// Locate the dev-hermit checkout by walking up from the working directory,
/// the same way `scripts/status-log.rs` does. Hard-coding an owner's path would
/// pin this tool to one machine and one account -- and the portability gate
/// rejects exactly that. The systemd unit sets `WorkingDirectory=` to the
/// checkout, so the walk starts inside the repo; `--root` overrides it for any
/// caller that cannot guarantee a cwd.
fn repository_root() -> Result<PathBuf, String> {
    let mut path = env::current_dir().map_err(|e| format!("current directory: {e}"))?;
    loop {
        if path.join(".gitmodules").is_file() && path.join("AGENTS.md").is_file() {
            return Ok(path);
        }
        if !path.pop() {
            return Err(
                "could not locate the dev-hermit repository root above the working \
                 directory; pass --root PATH"
                    .to_string(),
            );
        }
    }
}

fn parse_args() -> Result<Args, String> {
    let home = env::var("HOME").map_err(|_| "HOME is not set".to_string())?;
    // --root is parsed in the same pass below, so resolve lazily: a caller that
    // supplies --root must not be forced to stand in the repo to be understood.
    let explicit_root = env::args()
        .skip(1)
        .zip(env::args().skip(2))
        .find(|(flag, _)| flag == "--root")
        .map(|(_, value)| PathBuf::from(value));
    let root = match explicit_root {
        Some(path) => path,
        None => repository_root()?,
    };
    let mut args = Args {
        state_dir: PathBuf::from(&home).join(".local/state/hermit-hourly-status"),
        prompt_file: root.join("hourly_status_prompt.md"),
        relay: root.join("scripts/orc-hermit-msg.py"),
        socket: default_socket(),
        relay_command: None,
        hour: None,
        now_epoch: None,
        stale_pending_secs: DEFAULT_STALE_PENDING_SECS,
        release_on_failure: true,
        dry_run: false,
    };

    let mut raw = env::args().skip(1);
    while let Some(flag) = raw.next() {
        let mut next = |name: &str| -> Result<String, String> {
            raw.next()
                .ok_or_else(|| format!("{name} requires a value\n\n{USAGE}"))
        };
        match flag.as_str() {
            // Already resolved before this loop; consume its value so it is
            // not mistaken for a positional or an unknown flag.
            "--root" => {
                let _ = next("--root")?;
            }
            "--state-dir" => args.state_dir = PathBuf::from(next("--state-dir")?),
            "--prompt-file" => args.prompt_file = PathBuf::from(next("--prompt-file")?),
            "--relay" => args.relay = PathBuf::from(next("--relay")?),
            "--socket" => args.socket = PathBuf::from(next("--socket")?),
            "--relay-command" => args.relay_command = Some(next("--relay-command")?),
            "--hour" => args.hour = Some(next("--hour")?),
            "--now-epoch" => {
                let value = next("--now-epoch")?;
                args.now_epoch = Some(
                    value
                        .parse::<u64>()
                        .map_err(|_| "--now-epoch must be a non-negative integer".to_string())?,
                );
            }
            "--stale-pending-secs" => {
                let value = next("--stale-pending-secs")?;
                args.stale_pending_secs = value.parse::<u64>().map_err(|_| {
                    "--stale-pending-secs must be a non-negative integer".to_string()
                })?;
            }
            "--no-release-on-failure" => args.release_on_failure = false,
            "--dry-run" => args.dry_run = true,
            "-h" | "--help" => {
                print!("{USAGE}");
                exit(EXIT_OK);
            }
            other => return Err(format!("unknown argument: {other}\n\n{USAGE}")),
        }
    }
    Ok(args)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::atomic::{AtomicU32, Ordering};

    static COUNTER: AtomicU32 = AtomicU32::new(0);

    struct Fixture {
        root: PathBuf,
        state: PathBuf,
        prompt: PathBuf,
        witness: PathBuf,
    }

    impl Fixture {
        fn new(label: &str) -> Fixture {
            let root = env::temp_dir().join(format!(
                "hourly-status-relay-test-{}-{}-{}",
                label,
                std::process::id(),
                COUNTER.fetch_add(1, Ordering::SeqCst)
            ));
            let _ = fs::remove_dir_all(&root);
            fs::create_dir_all(&root).unwrap();
            let prompt = root.join("prompt.md");
            fs::write(&prompt, "SEND THE STATUS\n").unwrap();
            // A relay file that EXISTS but is never executed. The precondition
            // chain checks the relay before the socket, so a nonexistent relay
            // would make every socket test skip for the wrong reason and quietly
            // stop testing the socket at all.
            fs::write(root.join("relay-stub"), "#!/bin/sh\nexit 0\n").unwrap();
            Fixture {
                state: root.join("state"),
                witness: root.join("relay-calls.log"),
                prompt,
                root,
            }
        }

        /// A stand-in for `orc-hermit-msg.py` that records the message-file path
        /// it was handed and exits with a chosen code. Recording the ARGUMENT,
        /// not merely the call, is what lets a test assert that the coordinator
        /// would have received the right bytes.
        ///
        /// Written as a real script rather than an inline shell string because
        /// `run_relay` appends the message path as `"$0"`; a one-liner ending in
        /// `exit` would swallow it and the witness would record blank lines that
        /// still satisfy a naive call-count assertion.
        fn relay_command(&self, succeeds: bool) -> String {
            let name = if succeeds { "relay-ok.sh" } else { "relay-fail.sh" };
            let script = self.root.join(name);
            if !script.exists() {
                fs::write(
                    &script,
                    format!(
                        "#!/bin/sh\nprintf '%s\\n' \"$1\" >>{witness}\nexit {code}\n",
                        witness = self.witness.display(),
                        code = if succeeds { 0 } else { 1 }
                    ),
                )
                .unwrap();
                let mut permissions = fs::metadata(&script).unwrap().permissions();
                permissions.set_mode(0o755);
                fs::set_permissions(&script, permissions).unwrap();
            }
            script.display().to_string()
        }

        fn args(&self, hour: &str, now: u64, relay_succeeds: bool) -> Args {
            Args {
                state_dir: self.state.clone(),
                prompt_file: self.prompt.clone(),
                relay: self.root.join("relay-stub"),
                socket: self.root.join("fake.socket"),
                relay_command: Some(self.relay_command(relay_succeeds)),
                hour: Some(hour.to_string()),
                now_epoch: Some(now),
                stale_pending_secs: DEFAULT_STALE_PENDING_SECS,
                release_on_failure: true,
                dry_run: false,
            }
        }

        fn deliveries(&self) -> Vec<String> {
            fs::read_to_string(&self.witness)
                .unwrap_or_default()
                .lines()
                .map(str::to_string)
                .collect()
        }
    }

    fn expect_valid(state_dir: &Path, hour: &str) -> Claim {
        match read_claim(state_dir, hour) {
            ClaimRead::Valid(claim) => claim,
            other => panic!("expected a valid claim for {hour}, got {other:?}"),
        }
    }

    fn pending(hour: &str, claimed_at: u64) -> Claim {
        Claim {
            hour: hour.to_string(),
            state: STATE_PENDING.to_string(),
            claimed_at,
            claimed_by_pid: 4242,
            delivered_at: None,
            detail: None,
        }
    }

    fn delivered_claim(hour: &str, at: u64) -> Claim {
        Claim {
            hour: hour.to_string(),
            state: STATE_DELIVERED.to_string(),
            claimed_at: at,
            claimed_by_pid: 4242,
            delivered_at: Some(at),
            detail: None,
        }
    }

    // -- the dedupe KEY itself must be right, or everything above it is theatre --

    #[test]
    fn hour_bucket_is_utc_and_truncating() {
        // 1786014036 == 2026-08-07T01:00:36Z, the timestamp of the manually
        // logged entry that exposed the outage: a real observation, not a
        // made-up fixture.
        assert_eq!(hour_bucket(1_786_064_436), "2026-08-07T01");
        // One second BEFORE the hour belongs to the previous bucket. That is
        // safe only because a calendar timer never fires early; this assertion
        // is here so a future edit to the rounding has to argue with it.
        assert_eq!(hour_bucket(1_786_060_800 - 1), "2026-08-06T23");
        assert_eq!(hour_bucket(1_786_060_800), "2026-08-07T00");
        assert_eq!(hour_bucket(1_786_060_800 + 3_600), "2026-08-07T01");
        assert_eq!(hour_bucket(1_786_060_800 + 24 * 3_600), "2026-08-08T00");
    }

    #[test]
    fn format_utc_round_trips_a_known_instant() {
        assert_eq!(format_utc(1_786_064_436), "2026-08-07T01:00:36Z");
        assert_eq!(format_utc(1_786_060_800), "2026-08-07T00:00:00Z");
    }

    // ---- POSITIVE CONTROL: the qualifying case must FIRE, not sit inert ----

    #[test]
    fn positive_fresh_hour_delivers_exactly_once() {
        let fixture = Fixture::new("positive");
        let result = execute(&fixture.args("2026-08-07T01", 1_786_064_436, true)).unwrap();
        assert_eq!(result.outcome, "delivered", "detail: {}", result.detail);

        // Exactly one delivery, and it was handed the real message file.
        let deliveries = fixture.deliveries();
        assert_eq!(deliveries.len(), 1, "expected one relay call, got {deliveries:?}");
        let body = fs::read_to_string(&deliveries[0]).unwrap();
        assert!(
            body.contains("SCHEDULED HOUR: `2026-08-07T01`"),
            "the wake must bind itself to its hour, else the coordinator cannot dedupe: {body}"
        );
        assert!(body.contains("SEND THE STATUS"), "prompt body must be included");
        assert!(body.contains(GCHAT_SPACE), "the owner space must be named");
        assert!(
            body.contains("status-log.rs"),
            "the wake must ask for the structured append, which is the half that silently rots"
        );

        // Finalizing the claim is what actually closes the hour.
        let claim = expect_valid(&fixture.state, "2026-08-07T01");
        assert_eq!(claim.state, STATE_DELIVERED);
        assert!(claim.delivered_at.is_some());

        // Heartbeat and outcome are separately observable.
        let invocations = fs::read_to_string(fixture.state.join("invocations.log")).unwrap();
        assert!(invocations.contains("tick start hour=2026-08-07T01"));
    }

    // ---- NEGATIVE CONTROL: the same hour must NOT deliver twice ----

    #[test]
    fn dedupe_second_invocation_in_same_hour_does_not_deliver() {
        let fixture = Fixture::new("dedupe");
        let first = execute(&fixture.args("2026-08-07T01", 1_786_064_436, true)).unwrap();
        assert_eq!(first.outcome, "delivered");

        // A catch-up run, a manual `systemctl start`, a duplicate timer fire.
        let second = execute(&fixture.args("2026-08-07T01", 1_786_064_436 + 464, true)).unwrap();
        assert_eq!(second.outcome, "duplicate-hour", "detail: {}", second.detail);

        assert_eq!(
            fixture.deliveries().len(),
            1,
            "a second delivery escaped dedupe: {:?}",
            fixture.deliveries()
        );
    }

    #[test]
    fn dedupe_survives_many_repeats_including_a_much_later_one() {
        let fixture = Fixture::new("dedupe-many");
        assert_eq!(
            execute(&fixture.args("2026-08-07T01", 1_786_064_436, true))
                .unwrap()
                .outcome,
            "delivered"
        );
        for offset in [1, 60, 600, 3_599, 86_400, 7 * 86_400] {
            let result =
                execute(&fixture.args("2026-08-07T01", 1_786_064_436 + offset, true)).unwrap();
            assert_eq!(
                result.outcome, "duplicate-hour",
                "offset {offset}s re-delivered hour 01"
            );
        }
        assert_eq!(fixture.deliveries().len(), 1);
    }

    #[test]
    fn dedupe_is_keyed_on_the_hour_not_on_recency() {
        // Guards against a future "did I run recently" rewrite.
        let claim = delivered_claim("2026-08-07T01", 1_786_064_436);
        assert_eq!(
            decide(&ClaimRead::Valid(claim.clone()), 1_786_064_436 + 86_400, DEFAULT_STALE_PENDING_SECS),
            Decision::DuplicateHour
        );
    }

    // ---- RESTART: the next scheduled hour must still fire ----

    #[test]
    fn restart_next_scheduled_hour_still_delivers() {
        let fixture = Fixture::new("restart");
        assert_eq!(
            execute(&fixture.args("2026-08-07T01", 1_786_064_436, true))
                .unwrap()
                .outcome,
            "delivered"
        );
        // ORC, tmux, or the whole box goes away and comes back. This driver keeps
        // no in-memory clock and no in-memory sleep, so a "restart" is simply the
        // next process; the only thing carried across is the on-disk state dir.
        // This is the exact scenario the wf.sleep(36e5) loop failed.
        assert_eq!(
            execute(&fixture.args("2026-08-07T02", 1_786_068_036, true))
                .unwrap()
                .outcome,
            "delivered",
            "the hour AFTER a restart must fire -- this IS the defect being fixed"
        );
        assert_eq!(fixture.deliveries().len(), 2, "one delivery per hour");
    }

    #[test]
    fn restart_after_a_long_gap_delivers_the_current_hour_once_not_once_per_missed_hour() {
        // systemd Persistent=true runs ONE catch-up after downtime. Assert the
        // driver matches that: a 9h outage (the measured one) yields one status,
        // not nine.
        let fixture = Fixture::new("catchup");
        assert_eq!(
            execute(&fixture.args("2026-08-06T15", 1_786_028_712, true))
                .unwrap()
                .outcome,
            "delivered"
        );
        assert_eq!(
            execute(&fixture.args("2026-08-07T01", 1_786_064_436, true))
                .unwrap()
                .outcome,
            "delivered"
        );
        assert_eq!(
            fixture.deliveries().len(),
            2,
            "the 9-hour hole must not be back-filled with nine wakes"
        );
    }

    #[test]
    fn restart_mid_flight_pending_claim_ages_out_instead_of_wedging_forever() {
        // A run SIGKILLed between claiming and delivering leaves a pending claim.
        // If that were permanent, one crash would silence that hour and look
        // exactly like a delivered one.
        let hour = "2026-08-07T01";
        let claimed_at = 1_786_064_436;
        let claim = pending(hour, claimed_at);

        assert_eq!(
            decide(&ClaimRead::Valid(claim.clone()), claimed_at + 60, 900),
            Decision::InFlight { age_secs: 60 },
            "a young pending claim may still be a live run; do not steal it"
        );
        assert_eq!(
            decide(&ClaimRead::Valid(claim.clone()), claimed_at + 900, 900),
            Decision::ReclaimStale { age_secs: 900 },
            "past the window the owner is gone and the hour must be reclaimable"
        );
        assert_eq!(
            decide(&ClaimRead::Valid(claim.clone()), claimed_at + 86_400, 0),
            Decision::InFlight { age_secs: 86_400 },
            "--stale-pending-secs 0 must disable reclaim entirely"
        );
        // A clock step backwards must not manufacture a reclaim.
        assert_eq!(
            decide(&ClaimRead::Valid(claim.clone()), claimed_at - 5_000, 900),
            Decision::InFlight { age_secs: 0 }
        );
    }

    #[test]
    fn restart_reclaim_path_end_to_end_delivers_once() {
        let fixture = Fixture::new("reclaim");
        let hour = "2026-08-07T01";
        fs::create_dir_all(fixture.state.join("hours")).unwrap();
        fs::write(
            claim_path(&fixture.state, hour),
            serde_json::to_string(&pending(hour, 1_786_064_436)).unwrap(),
        )
        .unwrap();

        let result = execute(&fixture.args(hour, 1_786_064_436 + 1_200, true)).unwrap();
        assert_eq!(result.outcome, "delivered", "detail: {}", result.detail);
        assert_eq!(expect_valid(&fixture.state, hour).state, STATE_DELIVERED);
        assert_eq!(fixture.deliveries().len(), 1);
    }

    // ---- SKIP and FAILURE must not render the same ----

    #[test]
    fn missing_socket_skips_without_claiming_the_hour() {
        let fixture = Fixture::new("nosocket");
        // Drop the test relay override so the REAL precondition path runs, and
        // point the socket at a path that does not exist -- which is exactly
        // what a dead ORC looks like from outside.
        let mut args = fixture.args("2026-08-07T01", 1_786_064_436, true);
        args.relay_command = None;
        args.socket = fixture.root.join("definitely-not-a-socket");

        let result = execute(&args).unwrap();
        assert_eq!(result.outcome, "skipped", "detail: {}", result.detail);
        assert!(
            result.detail.contains("no-tmux-socket"),
            "the skip must name WHICH precondition failed: {}",
            result.detail
        );
        assert!(
            result.detail.contains("claimed=no"),
            "a skip must say it did not burn the hour: {}",
            result.detail
        );
        assert!(
            read_claim(&fixture.state, "2026-08-07T01") == ClaimRead::Absent,
            "a skipped hour must stay open for a later attempt"
        );
        assert!(fixture.deliveries().is_empty());
    }

    #[test]
    fn skip_then_recover_delivers_in_the_same_hour() {
        // ORC is down at :00 and back by :20. The hour must not be lost merely
        // because the first attempt found no socket.
        let fixture = Fixture::new("skip-recover");
        let mut down = fixture.args("2026-08-07T01", 1_786_064_436, true);
        down.relay_command = None;
        down.socket = fixture.root.join("gone");
        assert_eq!(execute(&down).unwrap().outcome, "skipped");

        let up = fixture.args("2026-08-07T01", 1_786_064_436 + 1_200, true);
        assert_eq!(execute(&up).unwrap().outcome, "delivered");
        assert_eq!(fixture.deliveries().len(), 1);
    }

    #[test]
    fn failed_wake_releases_the_hour_so_it_can_retry() {
        let fixture = Fixture::new("wakefail");
        let failed = execute(&fixture.args("2026-08-07T01", 1_786_064_436, false)).unwrap();
        assert_eq!(failed.outcome, "wake-failed", "detail: {}", failed.detail);
        assert!(failed.detail.contains("released=true"));
        assert!(
            read_claim(&fixture.state, "2026-08-07T01") == ClaimRead::Absent,
            "a fail-closed relay means nothing was delivered, so the hour must be released"
        );
        // The relay WAS invoked -- that is what distinguishes this from a skip.
        assert_eq!(fixture.deliveries().len(), 1);

        let retried = execute(&fixture.args("2026-08-07T01", 1_786_064_436 + 64, true)).unwrap();
        assert_eq!(retried.outcome, "delivered");
        assert_eq!(fixture.deliveries().len(), 2);
    }

    #[test]
    fn no_release_on_failure_keeps_the_failure_recorded_on_the_claim() {
        let fixture = Fixture::new("noremove");
        let mut args = fixture.args("2026-08-07T01", 1_786_064_436, false);
        args.release_on_failure = false;
        let result = execute(&args).unwrap();
        assert_eq!(result.outcome, "wake-failed");
        assert!(result.detail.contains("released=false"));

        let claim = expect_valid(&fixture.state, "2026-08-07T01");
        assert_eq!(claim.state, STATE_PENDING);
        assert!(
            claim.detail.as_deref().unwrap_or_default().contains("wake failed rc=1"),
            "the claim must carry WHY it failed, not just that it did: {claim:?}"
        );
        // Still pending, so an immediate retry is refused as in-flight...
        let immediate = execute(&fixture.args("2026-08-07T01", 1_786_064_436 + 64, true)).unwrap();
        assert_eq!(immediate.outcome, "in-flight");
        assert_eq!(fixture.deliveries().len(), 1);
    }

    // ---- a corrupt claim must never read as "no claim", and never as reclaimable ----

    #[test]
    fn unparseable_claim_is_typed_corruption_not_absent_and_not_reclaimable() {
        let fixture = Fixture::new("corrupt");
        let hour = "2026-08-07T01";
        fs::create_dir_all(fixture.state.join("hours")).unwrap();
        fs::write(claim_path(&fixture.state, hour), "{ this is not json").unwrap();

        assert!(
            matches!(read_claim(&fixture.state, hour), ClaimRead::Corrupt { .. }),
            "a corrupt file is neither absent nor a synthetic pending claim"
        );
        let decision = decide(
            &read_claim(&fixture.state, hour),
            1_786_064_436,
            DEFAULT_STALE_PENDING_SECS,
        );
        assert!(matches!(decision, Decision::CorruptClaim { .. }));
        assert!(
            !decision.proceeds(),
            "corruption must fail CLOSED: we cannot read `state`, so the hour may \
             already have been delivered"
        );
    }

    /// THE REGRESSION. A valid `delivered` claim plus one appended human-readable
    /// line used to parse-fail into a synthetic pending@epoch0, age straight into
    /// `ReclaimStale`, and re-send an hour that had already gone out. Measured
    /// with the real driver: pure JSON gave `duplicate-hour claimed=no-op`, the
    /// same file plus a trailing line gave `would-deliver reclaimed=true`.
    #[test]
    fn delivered_claim_plus_trailing_content_is_not_deliverable() {
        let fixture = Fixture::new("trailing");
        let hour = "2026-08-07T01";
        let at = 1_786_064_436;
        fs::create_dir_all(fixture.state.join("hours")).unwrap();
        let valid = serde_json::to_string(&delivered_claim(hour, at)).unwrap();

        // Control: the same bytes WITHOUT the trailing line still dedupe.
        fs::write(claim_path(&fixture.state, hour), format!("{valid}\n")).unwrap();
        assert_eq!(
            decide(
                &read_claim(&fixture.state, hour),
                at + 86_400,
                DEFAULT_STALE_PENDING_SECS
            ),
            Decision::DuplicateHour,
            "control: a clean delivered claim must dedupe"
        );

        // Now append exactly what the reporting task appended.
        fs::write(
            claim_path(&fixture.state, hour),
            format!("{valid}\nstatus delivered by hand at 01:04Z\n"),
        )
        .unwrap();
        let decision = decide(
            &read_claim(&fixture.state, hour),
            at + 86_400,
            DEFAULT_STALE_PENDING_SECS,
        );
        assert!(
            matches!(decision, Decision::CorruptClaim { .. }),
            "trailing content must be typed corruption, got {decision:?}"
        );
        assert!(
            !decision.proceeds(),
            "trailing content must NOT resurrect a delivered hour"
        );
        assert!(
            !matches!(decision, Decision::ReclaimStale { .. }),
            "the epoch-0 -> ReclaimStale fallback is the bug itself"
        );

        // End to end through the real driver: nothing is sent, nothing is claimed.
        let result = execute(&fixture.args(hour, at + 86_400, true)).unwrap();
        assert_eq!(result.outcome, "corrupt-claim");
        assert!(
            fixture.deliveries().is_empty(),
            "a corrupt claim must send nothing, got {:?}",
            fixture.deliveries()
        );
        // The operator needs the path to repair; assert the log is actionable.
        assert!(
            result.detail.contains("path=") && result.detail.contains("claimed=no-op"),
            "detail must name the file and say nothing was taken: {}",
            result.detail
        );
        // And the corrupt bytes are preserved, not silently repaired or deleted.
        let on_disk = fs::read_to_string(claim_path(&fixture.state, hour)).unwrap();
        assert!(on_disk.contains("status delivered by hand"));
    }

    /// The positive half of the bracket: fixing corruption must not have made the
    /// ordinary absent hour undeliverable.
    #[test]
    fn genuinely_absent_hour_still_delivers() {
        let fixture = Fixture::new("absent-still-delivers");
        let hour = "2026-08-07T02";
        assert_eq!(read_claim(&fixture.state, hour), ClaimRead::Absent);
        assert_eq!(
            decide(
                &read_claim(&fixture.state, hour),
                1_786_064_436,
                DEFAULT_STALE_PENDING_SECS
            ),
            Decision::Deliver
        );

        let result = execute(&fixture.args(hour, 1_786_064_436, true)).unwrap();
        assert_eq!(result.outcome, "delivered");
        assert_eq!(fixture.deliveries().len(), 1);
        assert_eq!(expect_valid(&fixture.state, hour).state, STATE_DELIVERED);
    }

    /// The documented safe workaround: write the outcome into `detail` by atomic
    /// rewrite instead of appending. It must stay parseable AND keep every
    /// pre-existing field, or the workaround silently becomes the bug.
    #[test]
    fn atomic_detail_update_preserves_all_pre_existing_fields() {
        let fixture = Fixture::new("detail-update");
        let hour = "2026-08-07T01";
        let at = 1_786_064_436;
        let original = delivered_claim(hour, at);
        write_claim(&fixture.state, &original).unwrap();

        let updated = Claim {
            detail: Some("status delivered by hand at 01:04Z".to_string()),
            ..expect_valid(&fixture.state, hour)
        };
        write_claim(&fixture.state, &updated).unwrap();

        let after = expect_valid(&fixture.state, hour);
        assert_eq!(after.detail.as_deref(), Some("status delivered by hand at 01:04Z"));
        assert_eq!(after.hour, original.hour);
        assert_eq!(after.state, original.state);
        assert_eq!(after.claimed_at, original.claimed_at);
        assert_eq!(after.claimed_by_pid, original.claimed_by_pid);
        assert_eq!(after.delivered_at, original.delivered_at);
        // Still dedupes afterwards -- the whole point of using `detail`.
        assert_eq!(
            decide(&read_claim(&fixture.state, hour), at + 86_400, DEFAULT_STALE_PENDING_SECS),
            Decision::DuplicateHour
        );
    }

    #[test]
    fn racing_claim_is_refused_by_o_excl() {
        let fixture = Fixture::new("race");
        let claim = pending("2026-08-07T01", 1_786_064_436);
        assert!(
            try_claim(&fixture.state, &claim, false).unwrap(),
            "first claim must win"
        );
        assert!(
            !try_claim(&fixture.state, &claim, false).unwrap(),
            "second claim must lose -- this is what makes concurrent runs safe"
        );
        assert!(
            try_claim(&fixture.state, &claim, true).unwrap(),
            "an explicit reclaim must be able to replace it"
        );
    }

    #[test]
    fn dry_run_writes_nothing_and_delivers_nothing() {
        let fixture = Fixture::new("dryrun");
        let mut args = fixture.args("2026-08-07T01", 1_786_064_436, true);
        args.dry_run = true;
        let result = execute(&args).unwrap();
        assert_eq!(result.outcome, "dry-run");
        assert_eq!(read_claim(&fixture.state, "2026-08-07T01"), ClaimRead::Absent);
        assert!(fixture.deliveries().is_empty());
        assert!(!fixture.state.join("invocations.log").exists());
    }

    #[test]
    fn missing_prompt_file_skips_rather_than_erroring_the_unit() {
        let fixture = Fixture::new("noprompt");
        let mut args = fixture.args("2026-08-07T01", 1_786_064_436, true);
        args.prompt_file = fixture.root.join("absent.md");
        let result = execute(&args).unwrap();
        assert_eq!(result.outcome, "skipped");
        assert!(result.detail.contains("prompt-unreadable"));
        assert_eq!(read_claim(&fixture.state, "2026-08-07T01"), ClaimRead::Absent);
    }
}
