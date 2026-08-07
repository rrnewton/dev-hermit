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
  --ack-wait-secs N       How long a `wake_accepted` hour waits for a GChat
                          acknowledgement before the driver re-wakes it
                          (default: 600).
  --max-wake-attempts N   Bound on re-waking an unacknowledged hour (default: 3).
                          After this the hour is left OPEN and un-delivered.
  --dry-run               Decide and print, but write no state and run no relay.

Recording a GChat acknowledgement (promotes an hour to `gchat_delivered`):
  --ack-hour YYYY-MM-DDTHH   The hour being acknowledged.
  --ack-message-name NAME    spaces/<space>/messages/<id> returned by the send.
  --ack-space spaces/<id>    The space the message landed in.
  --ack-thread NAME          spaces/<space>/threads/<id>.
  --ack-text-file PATH       The EXACT bytes the API reports as sent. The digest
                             is computed from this file, never taken on trust.
                             At most one trailing line terminator is stripped --
                             the same canonical rule scripts/status-log.rs
                             applies -- so a file written by an editor or a
                             heredoc yields the digest of the SENT text, and the
                             claim digest and the status-log digest agree.
  --ack-text-sha256 HEX      Optional cross-check; must equal that digest.
  All five --ack-* identifiers are required together. `wake_accepted` alone is
  never `delivered`: relay rc=0 proves only that the coordinator PANE accepted
  text.
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
    ack_wait_secs: u64,
    max_wake_attempts: u32,
    release_on_failure: bool,
    dry_run: bool,
    /// Present iff this invocation is recording a GChat acknowledgement rather
    /// than running a tick. Kept on Args so there is one parse and one dispatch.
    ack: Option<AckArgs>,
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

    // ---- stage 1: the pane accepted a wake. Proves NOTHING about GChat. ----
    #[serde(default, skip_serializing_if = "Option::is_none")]
    wake_accepted_at: Option<u64>,
    /// How many wakes this hour has cost. The bound on re-waking an
    /// unacknowledged hour; without it a coordinator that never answers is
    /// re-woken every tick forever.
    #[serde(default, skip_serializing_if = "is_zero")]
    wake_attempts: u32,

    // ---- stage 2: the coordinator returned a real GChat API record. ----
    // All four are required together. A messageName without its text digest
    // cannot be audited, and a digest without its messageName cannot be
    // dereferenced, so neither alone may promote an hour to delivered.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    gchat_message_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    gchat_space: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    gchat_thread: Option<String>,
    /// sha256 of the EXACT bytes the API reports as sent. Deliberately not the
    /// status-log text: those differed by a trailing 0x0A on 2026-08-07T02, so
    /// hashing the log makes a correct delivery look tampered to any auditor
    /// who dereferences gchat_message_name and hashes what they get back.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    gchat_text_sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    gchat_text_bytes: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    gchat_acked_at: Option<u64>,
}

fn is_zero(n: &u32) -> bool {
    *n == 0
}

impl Claim {
    fn new(hour: &str, state: &str, now: u64) -> Claim {
        Claim {
            hour: hour.to_string(),
            state: state.to_string(),
            claimed_at: now,
            claimed_by_pid: std::process::id(),
            delivered_at: None,
            detail: None,
            wake_accepted_at: None,
            wake_attempts: 0,
            gchat_message_name: None,
            gchat_space: None,
            gchat_thread: None,
            gchat_text_sha256: None,
            gchat_text_bytes: None,
            gchat_acked_at: None,
        }
    }

    /// The ONLY predicate that may close an hour on delivery grounds. Every
    /// field must be present: this is what "delivered" is allowed to mean.
    fn has_gchat_ack(&self) -> bool {
        self.state == STATE_GCHAT_DELIVERED
            && non_empty(&self.gchat_message_name)
            && non_empty(&self.gchat_space)
            && non_empty(&self.gchat_thread)
            && non_empty(&self.gchat_text_sha256)
    }
}

fn non_empty(field: &Option<String>) -> bool {
    field
        .as_deref()
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false)
}

const STATE_PENDING: &str = "pending";
/// Stage 1. The relay typed the wake into the coordinator pane and got rc=0.
/// This is the state the old driver mislabelled `delivered`.
const STATE_WAKE_ACCEPTED: &str = "wake_accepted";
/// Stage 2. A dereferenceable GChat API record exists for this hour.
const STATE_GCHAT_DELIVERED: &str = "gchat_delivered";
/// LEGACY, pre-split. Written by the old driver on relay rc=0, so it means
/// "a wake was accepted" and NOT that the owner saw anything. Kept dedupe-final
/// on purpose: these hours are historical, and re-opening them would send a
/// burst of stale statuses. Migration is forward-only.
const STATE_DELIVERED: &str = "delivered";

/// How long an unacknowledged `wake_accepted` hour waits for the coordinator
/// before the driver re-wakes it. Long enough that a working coordinator
/// finishes first (the observed 2026-08-07T02 recovery took ~8 min from wake to
/// send), short enough that a dead one is retried inside the hour.
const DEFAULT_ACK_WAIT_SECS: u64 = 600;
/// Bound on re-waking. After this many wakes with no ack the driver stops
/// waking and leaves the hour openly unacknowledged rather than spamming a
/// coordinator that is not answering.
const DEFAULT_MAX_WAKE_ATTEMPTS: u32 = 3;

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
    /// The pane accepted a wake but no GChat acknowledgement has arrived yet,
    /// and the ack window has not expired. NOT delivered and NOT closed -- the
    /// hour stays retryable; this tick simply must not wake again yet.
    AwaitingAck { age_secs: u64, attempts: u32 },
    /// Woken, unacknowledged, and the ack window expired. Re-wake: the previous
    /// wake demonstrably produced no owner-visible message.
    RewakeNoAck { age_secs: u64, attempts: u32 },
    /// Woken and unacknowledged up to the attempt bound. The driver stops
    /// waking. The hour is deliberately left OPEN and un-delivered rather than
    /// closed, so it reads as an unmet obligation instead of a success.
    AckExhausted { age_secs: u64, attempts: u32 },
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
        matches!(
            self,
            Decision::Deliver | Decision::ReclaimStale { .. } | Decision::RewakeNoAck { .. }
        )
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
fn decide(
    existing: &ClaimRead,
    now: u64,
    stale_pending_secs: u64,
    ack_wait_secs: u64,
    max_wake_attempts: u32,
) -> Decision {
    let claim = match existing {
        ClaimRead::Absent => return Decision::Deliver,
        ClaimRead::Corrupt { reason } => {
            return Decision::CorruptClaim {
                reason: reason.clone(),
            };
        }
        ClaimRead::Valid(claim) => claim,
    };
    // A legacy `delivered` hour is historical and stays closed; see the constant.
    if claim.state == STATE_DELIVERED {
        return Decision::DuplicateHour;
    }
    if claim.state == STATE_GCHAT_DELIVERED {
        // Fail CLOSED on a half-written ack. `gchat_delivered` without its
        // record is not evidence of delivery, but it is evidence that something
        // wrote a terminal state, so re-waking could double-send. Refuse to act
        // and say so, exactly as for a corrupt claim.
        if !claim.has_gchat_ack() {
            return Decision::CorruptClaim {
                reason: format!(
                    "state={STATE_GCHAT_DELIVERED} but the acknowledgement is incomplete \
                     (message_name={} space={} thread={} text_sha256={})",
                    claim.gchat_message_name.as_deref().unwrap_or("<missing>"),
                    claim.gchat_space.as_deref().unwrap_or("<missing>"),
                    claim.gchat_thread.as_deref().unwrap_or("<missing>"),
                    claim.gchat_text_sha256.as_deref().unwrap_or("<missing>"),
                ),
            };
        }
        return Decision::DuplicateHour;
    }
    if claim.state == STATE_WAKE_ACCEPTED {
        // THE DEFECT THIS TASK EXISTS TO FIX. rc=0 from the relay proves the
        // pane accepted text; it does not prove the owner saw anything. So this
        // hour is NOT closed. It is waited on, re-woken, and finally left open
        // -- never silently converted into a success.
        let age = now.saturating_sub(claim.wake_accepted_at.unwrap_or(claim.claimed_at));
        let attempts = claim.wake_attempts;
        if attempts >= max_wake_attempts {
            return Decision::AckExhausted {
                age_secs: age,
                attempts,
            };
        }
        if age < ack_wait_secs {
            return Decision::AwaitingAck {
                age_secs: age,
                attempts,
            };
        }
        return Decision::RewakeNoAck {
            age_secs: age,
            attempts,
        };
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
            reason: format!(
                "unparseable at line {} column {}: {e}",
                e.line(),
                e.column()
            ),
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

// ------------------------------------------------------------- sha256 -----

// Implemented in-file rather than pulled from `sha2`. This script runs from a
// systemd timer on a box where a cold `rust-script` dependency fetch is a real
// failure mode, and the hourly driver must not be able to fail because crates.io
// was unreachable. FIPS 180-4; bracketed against the published vectors in
// `sha256_matches_published_vectors`.
fn sha256_hex(bytes: &[u8]) -> String {
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];
    let mut msg = bytes.to_vec();
    let bit_len = (bytes.len() as u64).wrapping_mul(8);
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bit_len.to_be_bytes());

    for chunk in msg.chunks(64) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([
                chunk[i * 4],
                chunk[i * 4 + 1],
                chunk[i * 4 + 2],
                chunk[i * 4 + 3],
            ]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let (mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut hh) =
            (h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7]);
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let t1 = hh
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        for (slot, v) in h.iter_mut().zip([a, b, c, d, e, f, g, hh]) {
            *slot = slot.wrapping_add(v);
        }
    }
    h.iter().map(|w| format!("{w:08x}")).collect()
}

// ---------------------------------------------------------------- ack -----

/// Strip at most one trailing line terminator. The counterpart of
/// `canonical_status_text` in scripts/status-log.rs; see the call site in
/// `apply_ack` for why the two must agree. At most one, never a full trim:
/// trailing blank lines inside a status are author content.
fn canonical_text_bytes(mut text: Vec<u8>) -> Vec<u8> {
    if text.last() == Some(&b'\n') {
        text.pop();
        if text.last() == Some(&b'\r') {
            text.pop();
        }
    }
    text
}

/// The coordinator's acknowledgement of one hour: the GChat API record, as
/// returned by the send. Every field is required -- see `Claim::has_gchat_ack`.
#[derive(Debug, Clone)]
struct AckArgs {
    hour: String,
    message_name: String,
    space: String,
    thread: String,
    text_file: PathBuf,
    /// Optional cross-check. When supplied it must equal the digest computed
    /// from `text_file`; a mismatch means the caller hashed something other
    /// than what it is handing us, which is the exact confusion this binds out.
    expect_sha256: Option<String>,
}

/// Validate that the three GChat identifiers are (a) well-shaped and (b) name
/// the SAME conversation. Three unrelated non-empty strings would satisfy a
/// naive presence check while proving nothing, so the space must be a prefix of
/// both the message and the thread.
fn validate_gchat_identity(message_name: &str, space: &str, thread: &str) -> Result<(), String> {
    for (label, value) in [
        ("--ack-message-name", message_name),
        ("--ack-space", space),
        ("--ack-thread", thread),
    ] {
        if value.trim().is_empty() {
            return Err(format!("{label} must be a non-empty GChat identifier"));
        }
    }
    if !space.starts_with("spaces/") || space.matches('/').count() != 1 {
        return Err(format!(
            "--ack-space must look like spaces/<id>, got {space:?}"
        ));
    }
    let want_msg = format!("{space}/messages/");
    if !message_name.starts_with(&want_msg) || message_name.len() == want_msg.len() {
        return Err(format!(
            "--ack-message-name must be {want_msg}<id> so it dereferences inside \
             the acknowledged space, got {message_name:?}"
        ));
    }
    let want_thread = format!("{space}/threads/");
    if !thread.starts_with(&want_thread) || thread.len() == want_thread.len() {
        return Err(format!(
            "--ack-thread must be {want_thread}<id>, got {thread:?}"
        ));
    }
    Ok(())
}

/// Record a GChat acknowledgement against an hour, promoting it to
/// `gchat_delivered`. Atomic typed rewrite -- never a plaintext append, which is
/// the corruption class `delivered_claim_plus_trailing_content_is_not_deliverable`
/// brackets.
fn apply_ack(state_dir: &Path, ack: &AckArgs, now: u64) -> Result<String, String> {
    validate_gchat_identity(&ack.message_name, &ack.space, &ack.thread)?;
    let raw = fs::read(&ack.text_file)
        .map_err(|e| format!("read --ack-text-file {}: {e}", ack.text_file.display()))?;
    // SAME CANONICAL RULE AS scripts/status-log.rs canonical_status_text: strip at
    // most one trailing line terminator. Every ordinary way of writing the API
    // text to a file (editor, heredoc, shell redirect) appends one that the sent
    // message never had. Measured 2026-08-07T02: the API reported 1843 bytes /
    // fd702682..., the file round trip produced 1844 / 1ccab376..., differing by
    // exactly one 0x0A. Applying the identical rule in both tools is what makes
    // the claim's gchat_text_sha256 and the log's status_text_sha256 comparable
    // BY CONSTRUCTION rather than by luck -- which is the whole point, since an
    // auditor's check is exactly that comparison.
    let text = canonical_text_bytes(raw);
    if text.is_empty() {
        return Err(format!(
            "--ack-text-file {} is empty; an empty delivered text is not an acknowledgement",
            ack.text_file.display()
        ));
    }
    // The digest is COMPUTED from the exact bytes handed over, never taken on
    // the caller's word. That is what makes it evidence rather than a label.
    let digest = sha256_hex(&text);
    if let Some(expected) = &ack.expect_sha256 {
        if !expected.eq_ignore_ascii_case(&digest) {
            return Err(format!(
                "--ack-text-sha256 {expected} does not match the digest of \
                 --ack-text-file ({digest}); refusing to record a digest that does \
                 not derive from the text being acknowledged"
            ));
        }
    }

    let claim = match read_claim(state_dir, &ack.hour) {
        ClaimRead::Absent => {
            return Err(format!(
                "no claim for hour {} at {}; an hour that was never woken cannot be \
                 acknowledged",
                ack.hour,
                claim_path(state_dir, &ack.hour).display()
            ));
        }
        ClaimRead::Corrupt { reason } => {
            return Err(format!(
                "claim for hour {} is unreadable ({reason}); refusing to overwrite a \
                 claim whose current state cannot be established",
                ack.hour
            ));
        }
        ClaimRead::Valid(claim) => claim,
    };

    // Idempotent for a repeat of the SAME message; refused for a different one,
    // because two message names for one hour is a double-delivery report and
    // silently keeping the last would erase the evidence.
    if let Some(existing) = &claim.gchat_message_name {
        if existing == &ack.message_name {
            return Ok(format!(
                "ack-noop hour={} message-name={} already-recorded",
                ack.hour, existing
            ));
        }
        return Err(format!(
            "hour {} is already acknowledged by {existing}; refusing to replace it \
             with {} -- two message names for one hour means the hour was delivered \
             twice, which is a finding, not an update",
            ack.hour, ack.message_name
        ));
    }

    let acked = Claim {
        state: STATE_GCHAT_DELIVERED.to_string(),
        gchat_message_name: Some(ack.message_name.clone()),
        gchat_space: Some(ack.space.clone()),
        gchat_thread: Some(ack.thread.clone()),
        gchat_text_sha256: Some(digest.clone()),
        gchat_text_bytes: Some(text.len() as u64),
        gchat_acked_at: Some(now),
        delivered_at: claim.delivered_at.or(Some(now)),
        detail: None,
        ..claim
    };
    debug_assert!(acked.has_gchat_ack());
    write_claim(state_dir, &acked)?;
    append_line(
        &state_dir.join("invocations.log"),
        &format!(
            "{} ack hour={} state={STATE_GCHAT_DELIVERED} message-name={} space={} \
             thread={} text-sha256={} text-bytes={}",
            format_utc(now),
            ack.hour,
            ack.message_name,
            ack.space,
            ack.thread,
            digest,
            text.len()
        ),
    )?;
    Ok(format!(
        "ack-recorded hour={} state={STATE_GCHAT_DELIVERED} message-name={} \
         text-sha256={} text-bytes={}",
        ack.hour,
        ack.message_name,
        digest,
        text.len()
    ))
}

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
    let decision = decide(
        &existing,
        now,
        args.stale_pending_secs,
        args.ack_wait_secs,
        args.max_wake_attempts,
    );
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
        Decision::AwaitingAck { age_secs, attempts } => {
            return Ok(done(
                "awaiting-gchat-ack",
                format!(
                    "wake-age-secs={age_secs} wake-attempts={attempts} \
                     ack-wait-secs={} delivered=no claimed=no-op \
                     note=wake-accepted-is-not-delivery",
                    args.ack_wait_secs
                ),
            ));
        }
        Decision::AckExhausted { age_secs, attempts } => {
            // Loud, and deliberately NOT an error exit: the driver did its job.
            // The hour is left open and un-delivered so it reads as an unmet
            // obligation instead of a success.
            return Ok(done(
                "gchat-ack-missing",
                format!(
                    "wake-age-secs={age_secs} wake-attempts={attempts} \
                     max-wake-attempts={} delivered=no claimed=no-op \
                     repair=coordinator-must-send-and-run---ack-hour",
                    args.max_wake_attempts
                ),
            ));
        }
        other => debug_assert!(other.proceeds()),
    }

    let reclaimed = matches!(decision, Decision::ReclaimStale { .. });
    let rewake = matches!(decision, Decision::RewakeNoAck { .. });
    let prior_attempts = match &existing {
        ClaimRead::Valid(c) => c.wake_attempts,
        _ => 0,
    };
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
        wake_attempts: prior_attempts,
        detail: if reclaimed {
            Some("reclaimed a stale pending claim".to_string())
        } else if rewake {
            Some(format!(
                "re-waking: {prior_attempts} previous wake(s) produced no GChat acknowledgement"
            ))
        } else {
            None
        },
        ..Claim::new(&hour, STATE_PENDING, now)
    };
    // The claim is taken BEFORE the relay runs. Claim-then-send can at worst lose
    // one hour; send-then-claim can double-send, and a duplicate owner status is
    // the failure this design is required to exclude.
    if !try_claim(&args.state_dir, &claim, reclaimed || rewake)? {
        return Ok(done(
            "in-flight",
            "reason=lost-claim-race claimed=no-op".to_string(),
        ));
    }

    let message_file = wake_path(&args.state_dir, &hour);
    write_atomic(&message_file, &message)?;

    let (rc, summary) = run_relay(args, &message_file);
    if rc == 0 {
        // STAGE 1 ONLY. rc=0 means orc-hermit-msg.py typed the wake into the
        // coordinator pane. It does NOT mean a status reached the owner: on
        // 2026-08-07T01 the pane accepted a wake at 01:19:49Z, the old driver
        // wrote state=delivered at 01:19:52Z, and zero hourly statuses were sent
        // that hour. So this records wake_accepted and leaves the hour OPEN.
        // Only `--ack-hour` with a dereferenceable GChat record closes it.
        let attempts = claim.wake_attempts.saturating_add(1);
        write_claim(
            &args.state_dir,
            &Claim {
                state: STATE_WAKE_ACCEPTED.to_string(),
                // The tick's own time base, NOT now_epoch(): --now-epoch must move
                // the ack window with it, or the window is untestable and a clock
                // step silently resets it.
                wake_accepted_at: Some(now),
                wake_attempts: attempts,
                delivered_at: None,
                detail: None,
                ..claim
            },
        )?;
        return Ok(done(
            "wake-accepted",
            format!(
                "relay-rc=0 wake={} wake-attempts={attempts} delivered=no \
                 awaiting=gchat-ack",
                message_file.display()
            ),
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
    let now = args.now_epoch.unwrap_or_else(now_epoch);
    if let Some(ack) = &args.ack {
        // Recording an acknowledgement is not a tick: it writes no heartbeat and
        // runs no relay. It only promotes an already-woken hour on evidence.
        let line = apply_ack(&args.state_dir, ack, now)?;
        println!("{line}");
        return Ok(EXIT_OK);
    }
    let stamp = format_utc(now);
    let result = execute(&args)?;
    let line = result.line(&stamp);
    if args.dry_run {
        println!("{line}");
    } else {
        append_line(&args.state_dir.join("invocations.log"), &line)?;
        write_atomic(&args.state_dir.join("latest.status"), &format!("{line}\n"))?;
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
        ack_wait_secs: DEFAULT_ACK_WAIT_SECS,
        max_wake_attempts: DEFAULT_MAX_WAKE_ATTEMPTS,
        release_on_failure: true,
        dry_run: false,
        ack: None,
    };

    let (mut ack_hour, mut ack_message_name, mut ack_space) = (None, None, None);
    let (mut ack_thread, mut ack_text_file, mut ack_expect_sha) = (None, None, None);
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
            "--ack-wait-secs" => {
                args.ack_wait_secs = next("--ack-wait-secs")?
                    .parse()
                    .map_err(|e| format!("--ack-wait-secs: {e}"))?
            }
            "--max-wake-attempts" => {
                args.max_wake_attempts = next("--max-wake-attempts")?
                    .parse()
                    .map_err(|e| format!("--max-wake-attempts: {e}"))?
            }
            "--ack-hour" => ack_hour = Some(next("--ack-hour")?),
            "--ack-message-name" => ack_message_name = Some(next("--ack-message-name")?),
            "--ack-space" => ack_space = Some(next("--ack-space")?),
            "--ack-thread" => ack_thread = Some(next("--ack-thread")?),
            "--ack-text-file" => ack_text_file = Some(PathBuf::from(next("--ack-text-file")?)),
            "--ack-text-sha256" => ack_expect_sha = Some(next("--ack-text-sha256")?),
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

    // Ack mode is all-or-nothing on purpose. A partial ack is exactly the shape
    // this task exists to forbid: a messageName with no digest, or a digest with
    // no messageName, cannot establish that the owner received anything.
    let ack_flags = [
        ("--ack-hour", ack_hour.is_some()),
        ("--ack-message-name", ack_message_name.is_some()),
        ("--ack-space", ack_space.is_some()),
        ("--ack-thread", ack_thread.is_some()),
        ("--ack-text-file", ack_text_file.is_some()),
    ];
    let supplied: Vec<&str> = ack_flags
        .iter()
        .filter(|(_, p)| *p)
        .map(|(n, _)| *n)
        .collect();
    if !supplied.is_empty() {
        let missing: Vec<&str> = ack_flags
            .iter()
            .filter(|(_, p)| !*p)
            .map(|(n, _)| *n)
            .collect();
        if !missing.is_empty() {
            return Err(format!(
                "incomplete acknowledgement: got {} but missing {}. All of \
                 --ack-hour/--ack-message-name/--ack-space/--ack-thread/--ack-text-file \
                 are required together -- a partial acknowledgement cannot prove delivery.\n\n{USAGE}",
                supplied.join(" "),
                missing.join(" ")
            ));
        }
        args.ack = Some(AckArgs {
            hour: ack_hour.unwrap(),
            message_name: ack_message_name.unwrap(),
            space: ack_space.unwrap(),
            thread: ack_thread.unwrap(),
            text_file: ack_text_file.unwrap(),
            expect_sha256: ack_expect_sha,
        });
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
            let name = if succeeds {
                "relay-ok.sh"
            } else {
                "relay-fail.sh"
            };
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
                ack_wait_secs: DEFAULT_ACK_WAIT_SECS,
                max_wake_attempts: DEFAULT_MAX_WAKE_ATTEMPTS,
                ack: None,
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
            claimed_by_pid: 4242,
            ..Claim::new(hour, STATE_PENDING, claimed_at)
        }
    }

    fn delivered_claim(hour: &str, at: u64) -> Claim {
        Claim {
            claimed_by_pid: 4242,
            delivered_at: Some(at),
            ..Claim::new(hour, STATE_DELIVERED, at)
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
        assert_eq!(result.outcome, "wake-accepted", "detail: {}", result.detail);

        // Exactly one delivery, and it was handed the real message file.
        let deliveries = fixture.deliveries();
        assert_eq!(
            deliveries.len(),
            1,
            "expected one relay call, got {deliveries:?}"
        );
        let body = fs::read_to_string(&deliveries[0]).unwrap();
        assert!(
            body.contains("SCHEDULED HOUR: `2026-08-07T01`"),
            "the wake must bind itself to its hour, else the coordinator cannot dedupe: {body}"
        );
        assert!(
            body.contains("SEND THE STATUS"),
            "prompt body must be included"
        );
        assert!(body.contains(GCHAT_SPACE), "the owner space must be named");
        assert!(
            body.contains("status-log.rs"),
            "the wake must ask for the structured append, which is the half that silently rots"
        );

        // Finalizing the claim is what actually closes the hour.
        let claim = expect_valid(&fixture.state, "2026-08-07T01");
        assert_eq!(claim.state, STATE_WAKE_ACCEPTED);
        // THE POINT OF THIS TASK: a pane that accepted a wake has delivered
        // NOTHING. No delivery timestamp, and no GChat record to dereference.
        assert!(
            claim.delivered_at.is_none(),
            "wake acceptance is not a delivery"
        );
        assert!(claim.wake_accepted_at.is_some());
        assert_eq!(claim.wake_attempts, 1);
        assert!(!claim.has_gchat_ack());

        // Heartbeat and outcome are separately observable.
        let invocations = fs::read_to_string(fixture.state.join("invocations.log")).unwrap();
        assert!(invocations.contains("tick start hour=2026-08-07T01"));
    }

    // ---- NEGATIVE CONTROL: the same hour must NOT deliver twice ----

    #[test]
    fn dedupe_second_invocation_in_same_hour_does_not_deliver() {
        let fixture = Fixture::new("dedupe");
        let first = execute(&fixture.args("2026-08-07T01", 1_786_064_436, true)).unwrap();
        assert_eq!(first.outcome, "wake-accepted");

        // A catch-up run, a manual `systemctl start`, a duplicate timer fire.
        let second = execute(&fixture.args("2026-08-07T01", 1_786_064_436 + 464, true)).unwrap();
        assert_eq!(
            second.outcome, "awaiting-gchat-ack",
            "detail: {}",
            second.detail
        );

        assert_eq!(
            fixture.deliveries().len(),
            1,
            "a second delivery escaped dedupe: {:?}",
            fixture.deliveries()
        );
    }

    #[test]
    fn dedupe_survives_many_repeats_including_a_much_later_one() {
        // Post-ack this hour is closed FOREVER. Pre-ack it is deliberately
        // retryable (see unacknowledged_hour_is_bounded_retryable_then_left_open),
        // so the forever-property is a property of the ACKNOWLEDGED hour.
        let fixture = Fixture::new("dedupe-many");
        let hour = "2026-08-07T01";
        let base = 1_786_064_436;
        assert_eq!(
            execute(&fixture.args(hour, base, true)).unwrap().outcome,
            "wake-accepted"
        );
        ack_ok(&fixture, hour, base + 5, "hello owner");
        for offset in [1, 60, 600, 3_599, 86_400, 7 * 86_400] {
            let result = execute(&fixture.args(hour, base + offset, true)).unwrap();
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
            decide(
                &ClaimRead::Valid(claim.clone()),
                1_786_064_436 + 86_400,
                DEFAULT_STALE_PENDING_SECS,
                DEFAULT_ACK_WAIT_SECS,
                DEFAULT_MAX_WAKE_ATTEMPTS
            ),
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
            "wake-accepted"
        );
        // ORC, tmux, or the whole box goes away and comes back. This driver keeps
        // no in-memory clock and no in-memory sleep, so a "restart" is simply the
        // next process; the only thing carried across is the on-disk state dir.
        // This is the exact scenario the wf.sleep(36e5) loop failed.
        assert_eq!(
            execute(&fixture.args("2026-08-07T02", 1_786_068_036, true))
                .unwrap()
                .outcome,
            "wake-accepted",
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
            "wake-accepted"
        );
        assert_eq!(
            execute(&fixture.args("2026-08-07T01", 1_786_064_436, true))
                .unwrap()
                .outcome,
            "wake-accepted"
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
            decide(
                &ClaimRead::Valid(claim.clone()),
                claimed_at + 60,
                900,
                DEFAULT_ACK_WAIT_SECS,
                DEFAULT_MAX_WAKE_ATTEMPTS
            ),
            Decision::InFlight { age_secs: 60 },
            "a young pending claim may still be a live run; do not steal it"
        );
        assert_eq!(
            decide(
                &ClaimRead::Valid(claim.clone()),
                claimed_at + 900,
                900,
                DEFAULT_ACK_WAIT_SECS,
                DEFAULT_MAX_WAKE_ATTEMPTS
            ),
            Decision::ReclaimStale { age_secs: 900 },
            "past the window the owner is gone and the hour must be reclaimable"
        );
        assert_eq!(
            decide(
                &ClaimRead::Valid(claim.clone()),
                claimed_at + 86_400,
                0,
                DEFAULT_ACK_WAIT_SECS,
                DEFAULT_MAX_WAKE_ATTEMPTS
            ),
            Decision::InFlight { age_secs: 86_400 },
            "--stale-pending-secs 0 must disable reclaim entirely"
        );
        // A clock step backwards must not manufacture a reclaim.
        assert_eq!(
            decide(
                &ClaimRead::Valid(claim.clone()),
                claimed_at - 5_000,
                900,
                DEFAULT_ACK_WAIT_SECS,
                DEFAULT_MAX_WAKE_ATTEMPTS
            ),
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
        assert_eq!(result.outcome, "wake-accepted", "detail: {}", result.detail);
        assert_eq!(
            expect_valid(&fixture.state, hour).state,
            STATE_WAKE_ACCEPTED
        );
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
        assert_eq!(execute(&up).unwrap().outcome, "wake-accepted");
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
        assert_eq!(retried.outcome, "wake-accepted");
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
            claim
                .detail
                .as_deref()
                .unwrap_or_default()
                .contains("wake failed rc=1"),
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
            DEFAULT_ACK_WAIT_SECS,
            DEFAULT_MAX_WAKE_ATTEMPTS,
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
                DEFAULT_STALE_PENDING_SECS,
                DEFAULT_ACK_WAIT_SECS,
                DEFAULT_MAX_WAKE_ATTEMPTS,
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
            DEFAULT_ACK_WAIT_SECS,
            DEFAULT_MAX_WAKE_ATTEMPTS,
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
                DEFAULT_STALE_PENDING_SECS,
                DEFAULT_ACK_WAIT_SECS,
                DEFAULT_MAX_WAKE_ATTEMPTS,
            ),
            Decision::Deliver
        );

        let result = execute(&fixture.args(hour, 1_786_064_436, true)).unwrap();
        assert_eq!(result.outcome, "wake-accepted");
        assert_eq!(fixture.deliveries().len(), 1);
        assert_eq!(
            expect_valid(&fixture.state, hour).state,
            STATE_WAKE_ACCEPTED
        );
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
        assert_eq!(
            after.detail.as_deref(),
            Some("status delivered by hand at 01:04Z")
        );
        assert_eq!(after.hour, original.hour);
        assert_eq!(after.state, original.state);
        assert_eq!(after.claimed_at, original.claimed_at);
        assert_eq!(after.claimed_by_pid, original.claimed_by_pid);
        assert_eq!(after.delivered_at, original.delivered_at);
        // Still dedupes afterwards -- the whole point of using `detail`.
        assert_eq!(
            decide(
                &read_claim(&fixture.state, hour),
                at + 86_400,
                DEFAULT_STALE_PENDING_SECS,
                DEFAULT_ACK_WAIT_SECS,
                DEFAULT_MAX_WAKE_ATTEMPTS
            ),
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
        assert_eq!(
            read_claim(&fixture.state, "2026-08-07T01"),
            ClaimRead::Absent
        );
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
        assert_eq!(
            read_claim(&fixture.state, "2026-08-07T01"),
            ClaimRead::Absent
        );
    }
    // ================= wake_accepted vs gchat_delivered =================
    // The whole point: relay rc=0 is stage ONE. Only a dereferenceable GChat
    // API record closes an hour. Every fixture below is INERT -- no network, no
    // relay beyond the shell stub, and no owner-visible message anywhere.

    const SPACE: &str = "spaces/AAQAA6Irlwg";

    fn ack_args(fixture: &Fixture, hour: &str, text: &str) -> AckArgs {
        let text_file = fixture.root.join(format!("acktext-{hour}.txt"));
        fs::write(&text_file, text).unwrap();
        AckArgs {
            hour: hour.to_string(),
            message_name: format!("{SPACE}/messages/QF7vBsq-DXc.QF7vBsq-DXc"),
            space: SPACE.to_string(),
            thread: format!("{SPACE}/threads/QF7vBsq-DXc"),
            text_file,
            expect_sha256: None,
        }
    }

    fn ack_ok(fixture: &Fixture, hour: &str, now: u64, text: &str) -> String {
        apply_ack(&fixture.state, &ack_args(fixture, hour, text), now).unwrap()
    }

    #[test]
    fn sha256_matches_published_vectors() {
        // The digest is the evidence; if it is wrong every ack below is theatre.
        // FIPS 180-4 / NIST published vectors.
        assert_eq!(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(
            sha256_hex(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
        );
        // Multi-block, exercises the length padding across a 64-byte boundary.
        assert_eq!(
            sha256_hex(&[b'a'; 1000]),
            "41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3"
        );
    }

    #[test]
    fn digest_binds_to_the_api_bytes_not_the_logged_text() {
        // Observed 2026-08-07T02: the status-log copy carried one extra 0x0A, so
        // hashing the LOG makes a correct delivery look tampered to an auditor
        // who dereferences the messageName. These must not collide.
        assert_ne!(sha256_hex(b"status text"), sha256_hex(b"status text\n"));
    }

    // ---- NEGATIVE: a wake that the pane accepted is NOT a delivery ----

    #[test]
    fn wake_rc_zero_without_an_ack_never_reads_as_delivered() {
        let fixture = Fixture::new("wake-not-delivered");
        let hour = "2026-08-07T01";
        let result = execute(&fixture.args(hour, 1_786_064_436, true)).unwrap();

        assert_eq!(result.outcome, "wake-accepted");
        assert!(
            result.detail.contains("delivered=no"),
            "the outcome line must say so out loud: {}",
            result.detail
        );
        let claim = expect_valid(&fixture.state, hour);
        assert_eq!(claim.state, STATE_WAKE_ACCEPTED);
        assert_ne!(claim.state, STATE_DELIVERED);
        assert_ne!(claim.state, STATE_GCHAT_DELIVERED);
        assert!(!claim.has_gchat_ack());
        assert!(claim.gchat_message_name.is_none());
        assert!(claim.gchat_text_sha256.is_none());
        assert!(claim.delivered_at.is_none());
        // And it is NOT closed: the hour remains reachable for a retry.
        assert!(matches!(
            decide(
                &read_claim(&fixture.state, hour),
                1_786_064_436 + 1,
                DEFAULT_STALE_PENDING_SECS,
                DEFAULT_ACK_WAIT_SECS,
                DEFAULT_MAX_WAKE_ATTEMPTS,
            ),
            Decision::AwaitingAck { .. }
        ));
    }

    #[test]
    fn unacknowledged_hour_is_bounded_retryable_then_left_open() {
        // Crash-between-wake-and-ack: the coordinator never answers. The hour
        // must be retried a BOUNDED number of times and then left visibly
        // un-delivered -- never closed, never retried forever.
        let fixture = Fixture::new("bounded-retry");
        let hour = "2026-08-07T01";
        let base = 1_786_064_436;

        assert_eq!(
            execute(&fixture.args(hour, base, true)).unwrap().outcome,
            "wake-accepted"
        );
        // Inside the ack window: no second wake.
        let quiet = execute(&fixture.args(hour, base + 60, true)).unwrap();
        assert_eq!(quiet.outcome, "awaiting-gchat-ack");
        assert_eq!(
            fixture.deliveries().len(),
            1,
            "must not re-wake inside the window"
        );

        // Past the window: re-wake, up to the bound.
        let second = execute(&fixture.args(hour, base + DEFAULT_ACK_WAIT_SECS + 1, true)).unwrap();
        assert_eq!(second.outcome, "wake-accepted");
        assert_eq!(expect_valid(&fixture.state, hour).wake_attempts, 2);
        let third =
            execute(&fixture.args(hour, base + 2 * DEFAULT_ACK_WAIT_SECS + 2, true)).unwrap();
        assert_eq!(third.outcome, "wake-accepted");
        assert_eq!(expect_valid(&fixture.state, hour).wake_attempts, 3);
        assert_eq!(fixture.deliveries().len(), 3);

        // Bound reached: stop waking, and say the obligation is unmet.
        let exhausted =
            execute(&fixture.args(hour, base + 9 * DEFAULT_ACK_WAIT_SECS, true)).unwrap();
        assert_eq!(exhausted.outcome, "gchat-ack-missing");
        assert!(exhausted.detail.contains("delivered=no"));
        assert_eq!(
            fixture.deliveries().len(),
            3,
            "the bound must actually stop the waking"
        );
        // Still not delivered, and still not closed as a success.
        let claim = expect_valid(&fixture.state, hour);
        assert_eq!(claim.state, STATE_WAKE_ACCEPTED);
        assert!(!claim.has_gchat_ack());
    }

    // ---- POSITIVE: a real API record closes the hour, exactly once ----

    #[test]
    fn ack_promotes_to_gchat_delivered_and_records_the_api_record() {
        let fixture = Fixture::new("ack-positive");
        let hour = "2026-08-07T02";
        let base = 1_786_068_000;
        execute(&fixture.args(hour, base, true)).unwrap();

        let text = "## Hourly status\nowner-visible body\n";
        let line = ack_ok(&fixture, hour, base + 470, text);
        assert!(line.contains("ack-recorded"), "{line}");

        let claim = expect_valid(&fixture.state, hour);
        assert_eq!(claim.state, STATE_GCHAT_DELIVERED);
        assert!(claim.has_gchat_ack());
        assert_eq!(
            claim.gchat_message_name.as_deref(),
            Some("spaces/AAQAA6Irlwg/messages/QF7vBsq-DXc.QF7vBsq-DXc")
        );
        assert_eq!(claim.gchat_space.as_deref(), Some(SPACE));
        assert_eq!(
            claim.gchat_thread.as_deref(),
            Some("spaces/AAQAA6Irlwg/threads/QF7vBsq-DXc")
        );
        // The digest is COMPUTED from the canonical bytes, not copied from a
        // caller. `text` ends with a newline because that is how a file is
        // written; the SENT message did not have it, so the recorded digest is
        // of the canonical form -- that identity is this change's whole point.
        let canonical = "## Hourly status\nowner-visible body";
        assert_eq!(
            claim.gchat_text_sha256.as_deref(),
            Some(sha256_hex(canonical.as_bytes()).as_str())
        );
        assert_eq!(claim.gchat_text_bytes, Some(canonical.len() as u64));
        assert_eq!(claim.gchat_text_bytes, Some(text.len() as u64 - 1));
        assert_eq!(claim.gchat_acked_at, Some(base + 470));
        // The two stages are separately visible in the durable log.
        let log = fs::read_to_string(fixture.state.join("invocations.log")).unwrap();
        assert!(
            log.contains("tick start hour=2026-08-07T02"),
            "stage 1 heartbeat: {log}"
        );
        assert!(
            log.contains("ack hour=2026-08-07T02"),
            "stage 2 must be visible: {log}"
        );
        assert!(log.contains(&format!("text-sha256={}", sha256_hex(canonical.as_bytes()))));
        assert!(log.contains("message-name=spaces/AAQAA6Irlwg/messages/"));
    }

    #[test]
    fn same_hour_retrigger_dedupes_after_the_ack() {
        let fixture = Fixture::new("ack-dedupe");
        let hour = "2026-08-07T02";
        let base = 1_786_068_000;
        execute(&fixture.args(hour, base, true)).unwrap();
        ack_ok(&fixture, hour, base + 10, "body");
        let again = execute(&fixture.args(hour, base + 20, true)).unwrap();
        assert_eq!(again.outcome, "duplicate-hour");
        assert_eq!(fixture.deliveries().len(), 1, "no second wake after an ack");
    }

    // ---- NEGATIVE: partial or unbound acknowledgements are refused ----

    #[test]
    fn ack_refuses_an_incomplete_or_unbound_identity() {
        let fixture = Fixture::new("ack-incomplete");
        let hour = "2026-08-07T02";
        let base = 1_786_068_000;
        execute(&fixture.args(hour, base, true)).unwrap();
        let good = ack_args(&fixture, hour, "body");

        let cases: Vec<(&str, AckArgs)> = vec![
            (
                "empty message name",
                AckArgs {
                    message_name: String::new(),
                    ..good.clone()
                },
            ),
            (
                "empty space",
                AckArgs {
                    space: String::new(),
                    ..good.clone()
                },
            ),
            (
                "empty thread",
                AckArgs {
                    thread: String::new(),
                    ..good.clone()
                },
            ),
            (
                "blank message name",
                AckArgs {
                    message_name: "   ".into(),
                    ..good.clone()
                },
            ),
            // Three well-shaped strings that do NOT name one conversation: the
            // exact case a naive non-empty check would wave through.
            (
                "message name in a different space",
                AckArgs {
                    message_name: "spaces/OTHER/messages/x".into(),
                    ..good.clone()
                },
            ),
            (
                "thread in a different space",
                AckArgs {
                    thread: "spaces/OTHER/threads/x".into(),
                    ..good.clone()
                },
            ),
            (
                "space is not spaces/<id>",
                AckArgs {
                    space: "AAQAA6Irlwg".into(),
                    ..good.clone()
                },
            ),
            (
                "message name has no id",
                AckArgs {
                    message_name: format!("{SPACE}/messages/"),
                    ..good.clone()
                },
            ),
        ];
        for (label, ack) in cases {
            let err = apply_ack(&fixture.state, &ack, base + 10)
                .expect_err(&format!("{label} must be refused"));
            assert!(!err.is_empty(), "{label}");
            let claim = expect_valid(&fixture.state, hour);
            assert_eq!(
                claim.state, STATE_WAKE_ACCEPTED,
                "{label} must not mutate the claim"
            );
            assert!(!claim.has_gchat_ack(), "{label}");
        }
    }

    #[test]
    fn ack_refuses_a_digest_that_does_not_derive_from_the_text() {
        let fixture = Fixture::new("ack-digest");
        let hour = "2026-08-07T02";
        let base = 1_786_068_000;
        execute(&fixture.args(hour, base, true)).unwrap();

        let mut ack = ack_args(&fixture, hour, "the real body");
        ack.expect_sha256 = Some(sha256_hex(b"a different body"));
        let err = apply_ack(&fixture.state, &ack, base + 10).unwrap_err();
        assert!(err.contains("does not match"), "{err}");
        assert_eq!(
            expect_valid(&fixture.state, hour).state,
            STATE_WAKE_ACCEPTED
        );

        // Positive control: the matching digest is accepted, so the check is not inert.
        ack.expect_sha256 = Some(sha256_hex(b"the real body"));
        apply_ack(&fixture.state, &ack, base + 11).unwrap();
        assert_eq!(
            expect_valid(&fixture.state, hour).state,
            STATE_GCHAT_DELIVERED
        );
    }

    #[test]
    fn ack_refuses_an_empty_text_and_an_hour_that_was_never_woken() {
        let fixture = Fixture::new("ack-preconditions");
        let hour = "2026-08-07T02";
        let base = 1_786_068_000;

        // Never woken: there is no claim to acknowledge.
        let err = apply_ack(&fixture.state, &ack_args(&fixture, hour, "body"), base).unwrap_err();
        assert!(
            err.contains("never woken") || err.contains("no claim"),
            "{err}"
        );

        execute(&fixture.args(hour, base, true)).unwrap();
        let err = apply_ack(&fixture.state, &ack_args(&fixture, hour, ""), base + 5).unwrap_err();
        assert!(err.contains("empty"), "{err}");
        assert_eq!(
            expect_valid(&fixture.state, hour).state,
            STATE_WAKE_ACCEPTED
        );
    }

    #[test]
    fn ack_is_idempotent_for_one_message_and_refuses_a_second_one() {
        let fixture = Fixture::new("ack-idempotent");
        let hour = "2026-08-07T02";
        let base = 1_786_068_000;
        execute(&fixture.args(hour, base, true)).unwrap();

        ack_ok(&fixture, hour, base + 10, "body");
        let before = fs::read_to_string(claim_path(&fixture.state, hour)).unwrap();
        // Same message replayed: a no-op, not an error and not a rewrite.
        let again = ack_ok(&fixture, hour, base + 20, "body");
        assert!(again.contains("ack-noop"), "{again}");
        assert_eq!(
            fs::read_to_string(claim_path(&fixture.state, hour)).unwrap(),
            before
        );

        // A DIFFERENT message name for the same hour is a double-delivery
        // report. Refuse it rather than silently keeping the newer one.
        let mut other = ack_args(&fixture, hour, "body");
        other.message_name = format!("{SPACE}/messages/SECOND-SEND");
        let err = apply_ack(&fixture.state, &other, base + 30).unwrap_err();
        assert!(err.contains("already acknowledged"), "{err}");
        assert_eq!(
            fs::read_to_string(claim_path(&fixture.state, hour)).unwrap(),
            before
        );
    }

    #[test]
    fn a_gchat_delivered_claim_without_its_record_fails_closed() {
        // Half-written ack: terminal state, no evidence. Never deliverable and
        // never silently repaired -- same treatment as a corrupt claim.
        let claim = Claim {
            state: STATE_GCHAT_DELIVERED.to_string(),
            gchat_message_name: Some(format!("{SPACE}/messages/x")),
            ..Claim::new("2026-08-07T02", STATE_GCHAT_DELIVERED, 1_786_068_000)
        };
        let decision = decide(
            &ClaimRead::Valid(claim),
            1_786_068_000 + 60,
            DEFAULT_STALE_PENDING_SECS,
            DEFAULT_ACK_WAIT_SECS,
            DEFAULT_MAX_WAKE_ATTEMPTS,
        );
        assert!(
            matches!(decision, Decision::CorruptClaim { .. }),
            "{decision:?}"
        );
        assert!(!decision.proceeds());
    }

    #[test]
    fn legacy_delivered_claims_stay_closed_and_are_not_resent() {
        // Pre-split hours are historical. Re-opening them would send a burst of
        // stale statuses; migration is forward-only.
        let decision = decide(
            &ClaimRead::Valid(delivered_claim("2026-08-07T01", 1_786_064_436)),
            1_786_064_436 + 7 * 86_400,
            DEFAULT_STALE_PENDING_SECS,
            DEFAULT_ACK_WAIT_SECS,
            DEFAULT_MAX_WAKE_ATTEMPTS,
        );
        assert_eq!(decision, Decision::DuplicateHour);
    }

    // ---- the file round trip must not invent a byte the API never saw ----

    #[test]
    fn ack_digest_is_of_the_sent_text_not_the_file_terminator() {
        // THE MEASURED DEFECT, 2026-08-07T02, message QF7vBsq-DXc.QF7vBsq-DXc:
        // the API reported 1843 bytes / fd702682...; the recorded digest was of
        // 1844 bytes / 1ccab376..., one appended 0x0A. A correct delivery then
        // hashes as tampered to anyone who dereferences the messageName.
        let fixture = Fixture::new("ack-newline");
        let base = 1_786_068_000;
        let sent = "## Hourly status\nbody with no terminator";

        // Same logical status, written the two ways a caller actually writes it.
        for (label, hour, on_disk) in [
            (
                "file with a trailing newline",
                "2026-08-07T02",
                format!("{sent}\n"),
            ),
            ("file without one", "2026-08-07T03", sent.to_string()),
        ] {
            execute(&fixture.args(hour, base, true)).unwrap();
            ack_ok(&fixture, hour, base + 10, &on_disk);
            let claim = expect_valid(&fixture.state, hour);
            assert_eq!(
                claim.gchat_text_sha256.as_deref(),
                Some(sha256_hex(sent.as_bytes()).as_str()),
                "{label}: digest must be of the SENT text"
            );
            assert_eq!(
                claim.gchat_text_bytes,
                Some(sent.len() as u64),
                "{label}: byte count must be the SENT count"
            );
        }

        // ... and the two spellings agree, which is what "one canonical byte
        // sequence" means. Before this change they differed by one byte.
        let a = expect_valid(&fixture.state, "2026-08-07T02");
        let b = expect_valid(&fixture.state, "2026-08-07T03");
        assert_eq!(a.gchat_text_sha256, b.gchat_text_sha256);
        assert_eq!(a.gchat_text_bytes, b.gchat_text_bytes);
    }

    #[test]
    fn canonicalization_strips_at_most_one_terminator() {
        // At most ONE. Trailing blank lines inside a status are author content;
        // a trim_end would silently rewrite the delivered message.
        assert_eq!(canonical_text_bytes(b"x\n".to_vec()), b"x".to_vec());
        assert_eq!(canonical_text_bytes(b"x\r\n".to_vec()), b"x".to_vec());
        assert_eq!(canonical_text_bytes(b"x".to_vec()), b"x".to_vec());
        assert_eq!(canonical_text_bytes(b"x\n\n".to_vec()), b"x\n".to_vec());
        assert_eq!(canonical_text_bytes(b"x\n\n\n".to_vec()), b"x\n\n".to_vec());
        assert_eq!(canonical_text_bytes(b"".to_vec()), b"".to_vec());
        assert_eq!(canonical_text_bytes(b"\n".to_vec()), b"".to_vec());
    }

    #[test]
    fn a_file_that_is_only_a_terminator_is_refused_as_empty() {
        // Canonicalizing "\n" yields "", which must hit the empty-text refusal
        // rather than recording a delivery of nothing.
        let fixture = Fixture::new("ack-only-newline");
        let hour = "2026-08-07T02";
        let base = 1_786_068_000;
        execute(&fixture.args(hour, base, true)).unwrap();
        let err = apply_ack(&fixture.state, &ack_args(&fixture, hour, "\n"), base + 5).unwrap_err();
        assert!(err.contains("empty"), "{err}");
        assert_eq!(
            expect_valid(&fixture.state, hour).state,
            STATE_WAKE_ACCEPTED
        );
    }

    #[test]
    fn expect_sha256_is_compared_against_the_canonical_text() {
        // A caller holding the API-reported digest must be able to pass it
        // straight through, even though its local file has a terminator.
        let fixture = Fixture::new("ack-expect-canonical");
        let hour = "2026-08-07T02";
        let base = 1_786_068_000;
        execute(&fixture.args(hour, base, true)).unwrap();
        let sent = "delivered body";
        let mut ack = ack_args(&fixture, hour, &format!("{sent}\n"));
        ack.expect_sha256 = Some(sha256_hex(sent.as_bytes()));
        apply_ack(&fixture.state, &ack, base + 10).unwrap();
        assert_eq!(
            expect_valid(&fixture.state, hour).state,
            STATE_GCHAT_DELIVERED
        );

        // NEGATIVE control: the pre-canonicalization digest -- the one the old
        // code would have produced -- is now correctly REFUSED.
        let hour2 = "2026-08-07T03";
        execute(&fixture.args(hour2, base, true)).unwrap();
        let mut stale = ack_args(&fixture, hour2, &format!("{sent}\n"));
        stale.expect_sha256 = Some(sha256_hex(format!("{sent}\n").as_bytes()));
        let err = apply_ack(&fixture.state, &stale, base + 10).unwrap_err();
        assert!(err.contains("does not match"), "{err}");
    }
}
