#!/usr/bin/env rust-script
//! Allocate a worktree slot for a dev-hermit agent, enforcing worktree discipline.
//!
//! CANONICAL LAYOUT v3 (nested, one slot per agent):
//!
//!   worktrees/<slot>/hermit    Hermit worktree  (from the hermit/ primary)
//!   worktrees/<slot>/reverie   Reverie worktree (from the reverie/ primary)
//!   worktrees/<slot>/liteinst2 LiteInst2 worktree (from the liteinst2/ primary)
//!
//! `<slot>` is either a NAMED agent slot (e.g. worktrees/kvm, worktrees/dbi for
//! the purpose-fixed agents hermit-kvm, hermit-dbi, ...) or a generic slotNN
//! (worktrees/slot01, ...). Each agent owns exactly ONE slot (1-1 mapping). A
//! New slots contain all three product worktrees by default. A specialized
//! allocation may request an individual product or the legacy Hermit+Reverie
//! pair (`both`).
//!
//! This is the ONLY sanctioned way to create an agent worktree. It refuses to
//! let two mutating agents share a slot, records ownership in the machine-
//! readable `worktree-state.json`, and regenerates the machine-parseable table
//! block in `worktrees/ACTIVE.md`.
//!
//! ```cargo
//! [dependencies]
//! fs2 = "0.4"
//! serde_json = "1"
//! ```
use fs2::FileExt;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{exit, Command};

/// Workspace homeostasis caps. Disk is the primary advisory (expressed in GB);
/// the slot count is a secondary advisory against the active-worktree policy
/// limit. Override the disk cap with HERMIT_WORKTREE_GB_CAP.
///
/// UNIT: this cap is compared against `du -sb` (see `dir_size_bytes`), i.e. the
/// APPARENT / pre-compression / reflink-referenced logical size of worktrees/.
/// btrfs zstd is transparent to `du`, so `du -sb` overstates the real on-disk
/// footprint. Measured 2026-08-03 on this box (compsize over worktrees/):
///   du -sb (apparent, referenced) : actual disk = 639.7 GB : 163 GB = 3.92 : 1
/// so this apparent cap corresponds to ~1/3.9 as much real disk.
///
/// DERIVATION (do not "round" — recompute from measurements when the box or the
/// slot budget changes). Three recorded inputs, all in the apparent/`du -sb`
/// unit the check uses:
///   measured_per_tree = 95 GB  (mean of the 5 heaviest fully-built trees
///                               measured 2026-08-03: 54/66/87/110/152 GB ≈ 94,
///                               taken as 95 for margin; PRE-compression apparent)
///   target_count      = 12     (active-worktree policy limit, Hard Invariant 13)
///   headroom          = 1.25   (parked slots + build growth)
///   cap = 95 * 12 * 1.25 = 1425 GB apparent
/// Real-disk sanity: 1425 GB apparent / 3.92 ≈ 363 GB actual (≈475 GB even at a
/// conservative 3:1), vs 2158 GB free here — ~17-22% of free at the cap.
const DEFAULT_DISK_CAP_GB: u64 = 1425;
const SLOT_COUNT_ADVISORY: usize = 12;
const LANGUISH_HOURS: i64 = 24;

const USAGE: &str = r#"Usage: allocate-worktree.rs --agent NAME [OPTIONS]

Allocate a worktree slot (nested layout
worktrees/<slot>/{hermit,reverie,liteinst2}) and register its ownership. Each
agent owns exactly one slot.

Required:
  --agent NAME        Owning agent (e.g. hermit-kvm). Mutating owner of the slot.

Options:
  --slot SLOT         Slot name. Default: the agent name with a leading
                      'hermit-' stripped (hermit-kvm -> worktrees/kvm), or a
                      generic slotNN if the agent name is not a clean token.
                      Accepts a named token ([a-z0-9-]+) or slotNN.
  --task TASK-ID      Task this slot serves (recorded in state + ACTIVE.md).
  --product P         hermit | reverie | liteinst2 | both | all
                      (default: all; both means Hermit + Reverie).
  --hermit-branch B   Create this feature branch in the hermit worktree.
  --reverie-branch B  Create this feature branch in the reverie worktree.
  --liteinst2-branch B
                      Create this feature branch in the liteinst2 worktree.
  --start-point REF   Base for detached/new worktrees (default: primary HEAD).
  --purpose TEXT      One-line purpose recorded in state + ACTIVE.md.
  --i-promise-this-agent-is-read-mostly
                      Join an already-owned slot as an ADDITIONAL read-only
                      agent instead of failing the collision check.
  --check-only        Run registry branch/content consistency plus workspace
                      homeostasis, then exit WITHOUT allocating anything.
  -h, --help          Show this help.

Homeostasis (ADVISORY, never blocks — allocation always completes): every
allocation also prints an informational banner if worktrees/ apparent size
(du -sb) exceeds the GB soft cap (env HERMIT_WORKTREE_GB_CAP, default 1425 GB
apparent; note du -sb overstates real btrfs on-disk use ~3.9x here), if any
physical slot has had no file edits in >24h (registered or orphaned), or if slot
dirs exceed a soft count. These are housekeeping heads-ups, not stop signs; your
slot is allocated and you should proceed.

Policy:
  * One mutating owner per slot; a second mutating agent is refused.
  * One slot per agent; if the agent already owns a different slot, refused.
  * Read-mostly agents may share and are recorded as such.

Examples:
  ./scripts/allocate-worktree.rs --agent hermit-kvm --task impl-kvm-ratchet
      -> worktrees/kvm/{hermit,reverie,liteinst2}
  ./scripts/allocate-worktree.rs --agent hermit-201 --slot slot01 \
      --product hermit --hermit-branch debug-batch1 --task impl-debug
  ./scripts/allocate-worktree.rs --agent hermit-ci --slot kvm \
      --i-promise-this-agent-is-read-mostly --task research-ci
"#;

fn die(msg: &str) -> ! {
    eprintln!("allocate-worktree: {msg}");
    exit(1);
}

/// Walk up from CWD to the dev-hermit parent (all three primary submodules).
fn find_root() -> PathBuf {
    let mut dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    loop {
        if dir.join(".gitmodules").is_file()
            && dir.join("hermit").is_dir()
            && dir.join("reverie").is_dir()
            && dir.join("liteinst2").is_dir()
        {
            return dir;
        }
        if !dir.pop() {
            die("could not locate dev-hermit root (need .gitmodules + hermit/ + reverie/ + liteinst2/)");
        }
    }
}

/// Hold the single registry-writer authority across physical worktree and
/// state/ACTIVE mutations. The releaser takes this same lock.
fn lock_registry(root: &Path) -> fs::File {
    let path = root.join("worktree-state.lock");
    let file = fs::OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .open(&path)
        .unwrap_or_else(|error| die(&format!("open registry lock {}: {error}", path.display())));
    FileExt::lock_exclusive(&file)
        .unwrap_or_else(|error| die(&format!("lock registry {}: {error}", path.display())));
    file
}

fn now_iso() -> String {
    match Command::new("date")
        .args(["-u", "+%Y-%m-%dT%H:%M:%SZ"])
        .output()
    {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).trim().to_string(),
        _ => "unknown".to_string(),
    }
}

/// Run a git command in `dir`; return (success, stdout, stderr).
fn git(dir: &Path, args: &[&str]) -> (bool, String, String) {
    let out = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(args)
        .output()
        .unwrap_or_else(|e| die(&format!("failed to spawn git: {e}")));
    (
        out.status.success(),
        String::from_utf8_lossy(&out.stdout).trim().to_string(),
        String::from_utf8_lossy(&out.stderr).trim().to_string(),
    )
}

fn epoch_now() -> i64 {
    Command::new("date")
        .args(["+%s"])
        .output()
        .ok()
        .and_then(|o| String::from_utf8_lossy(&o.stdout).trim().parse().ok())
        .unwrap_or(0)
}

/// Convert an ISO-8601 timestamp to epoch seconds via `date -d`.
fn epoch_of(iso: &str) -> Option<i64> {
    let out = Command::new("date")
        .args(["-d", iso, "+%s"])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    String::from_utf8_lossy(&out.stdout).trim().parse().ok()
}

/// Total size of a directory in bytes via `du -sb`.
fn dir_size_bytes(path: &Path) -> u64 {
    if !path.exists() {
        return 0;
    }
    Command::new("du")
        .arg("-sb")
        .arg(path)
        .output()
        .ok()
        .and_then(|o| {
            String::from_utf8_lossy(&o.stdout)
                .split_whitespace()
                .next()
                .and_then(|s| s.parse().ok())
        })
        .unwrap_or(0)
}

/// Newest modification time (epoch seconds) of a *source* file anywhere under
/// `dir`, ignoring build/VCS churn (target/, .git/, node_modules/). This is the
/// real "when was this slot last edited" signal — unlike the state timestamp,
/// which only moves when allocate-worktree runs. Returns 0 when nothing is found.
fn newest_source_mtime(dir: &Path) -> i64 {
    if !dir.exists() {
        return 0;
    }
    let out = Command::new("find")
        .arg(dir)
        .args([
            "(",
            "-name",
            "target",
            "-o",
            "-name",
            ".git",
            "-o",
            "-name",
            "node_modules",
            ")",
            "-prune",
            "-o",
            "-type",
            "f",
            "-printf",
            "%T@\n",
        ])
        .output();
    match out {
        Ok(o) => String::from_utf8_lossy(&o.stdout)
            .lines()
            // find prints float epochs like "1690000000.1234567"; take the seconds.
            .filter_map(|l| l.split('.').next().and_then(|s| s.parse::<i64>().ok()))
            .max()
            .unwrap_or(0),
        Err(_) => 0,
    }
}

/// Count physical slot directories under worktrees/ (each child dir is a slot).
fn physical_slot_count(root: &Path) -> usize {
    let wt = root.join("worktrees");
    std::fs::read_dir(&wt)
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .filter(|e| e.path().is_dir())
                .count()
        })
        .unwrap_or(0)
}

/// Advisory workspace-health check: disk soft cap (GB, apparent/du -sb),
/// languishing slots (>24h), and a slot-count advisory. Never fails the run and
/// never blocks allocation — it prints an informational banner to stderr so
/// agents notice and can do housekeeping, NOT a stop sign. Wording is
/// deliberately advisory: a firing banner must not read as a reason to refuse
/// work.
fn homeostasis_check(root: &Path) {
    let cap_gb: u64 = std::env::var("HERMIT_WORKTREE_GB_CAP")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(DEFAULT_DISK_CAP_GB);

    let wt = root.join("worktrees");
    let bytes = dir_size_bytes(&wt);
    let gb = bytes as f64 / 1e9;

    let mut warnings: Vec<String> = Vec::new();

    if gb > cap_gb as f64 {
        warnings.push(format!(
            "disk heads-up: worktrees/ = {gb:.1} GB apparent (du -sb) > {cap_gb} GB soft cap.\n     \
             This is advisory only — btrfs compression means real on-disk use is far\n     \
             lower, and nothing is blocked. When convenient (or leave for the\n     \
             coordinator), reclaim build dirs / idle slots:\n     \
             find worktrees -name target -type d -maxdepth 3 -exec rm -rf {{}} +\n     \
             scripts/release-worktree.rs --slot <slot> --clean"
        ));
    } else {
        eprintln!("homeostasis: worktrees/ = {gb:.1} GB apparent / {cap_gb} GB soft cap (ok)");
    }

    let languish_hours: i64 = std::env::var("HERMIT_WORKTREE_LANGUISH_HOURS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(LANGUISH_HOURS);

    // Languishing slots: no *file edits* in >24h. We walk the PHYSICAL slot dirs
    // (not just registered ones) so abandoned/orphaned slots are caught, and use
    // the real newest source-file mtime — max'd with the registered state
    // timestamp so a freshly-allocated slot that hasn't been touched yet isn't
    // falsely flagged. Re-read state fresh so --check-only reflects current truth.
    let now = epoch_now();
    let state = load_state(root);
    let slots_obj = state["slots"].as_object();
    // (name, idle_hours, registered) sorted most-idle-first.
    let mut languishing: Vec<(String, i64, bool)> = Vec::new();
    if let Ok(rd) = std::fs::read_dir(root.join("worktrees")) {
        let mut names: Vec<String> = rd
            .filter_map(|e| e.ok())
            .filter(|e| e.path().is_dir())
            .filter_map(|e| e.file_name().into_string().ok())
            .collect();
        names.sort();
        for name in names {
            let dir = root.join("worktrees").join(&name);
            let registered = slots_obj.map(|s| s.contains_key(&name)).unwrap_or(false);
            let state_ts = slots_obj
                .and_then(|s| s.get(&name))
                .and_then(|s| s["updated"].as_str().or_else(|| s["allocated"].as_str()))
                .and_then(epoch_of)
                .unwrap_or(0);
            // Last activity = most recent of {real edit, registration bump}.
            let last = newest_source_mtime(&dir).max(state_ts);
            if last == 0 {
                continue; // can't determine an age; don't guess.
            }
            let hours = (now - last) / 3600;
            if hours >= languish_hours {
                languishing.push((name, hours, registered));
            }
        }
    }
    if !languishing.is_empty() {
        languishing.sort_by(|a, b| b.1.cmp(&a.1));
        let total = languishing.len();
        const SHOW: usize = 10;
        let shown: Vec<String> = languishing
            .iter()
            .take(SHOW)
            .map(|(n, h, reg)| {
                let tag = if *reg { "" } else { ", UNREGISTERED" };
                format!("{n} ({h}h no edits{tag})")
            })
            .collect();
        let more = if total > SHOW {
            format!(" (+{} more)", total - SHOW)
        } else {
            String::new()
        };
        warnings.push(format!(
            "LANGUISHING SLOTS: {total} slot(s) with no file edits in >{languish_hours}h: {}{more}.\n     \
             Land their work as a branch/draft PR, then release: scripts/release-worktree.rs --slot <slot> --clean",
            shown.join(", ")
        ));
    }

    // Slot-count advisory (disk is the real cap; this flags sprawl).
    let phys = physical_slot_count(root);
    if phys > SLOT_COUNT_ADVISORY {
        warnings.push(format!(
            "SLOT SPRAWL: {phys} physical slot dirs under worktrees/ (advisory soft \
             limit {SLOT_COUNT_ADVISORY}).\n     \
             The real cap is disk ({cap_gb} GB); consolidate/clean idle slots."
        ));
    }

    if !warnings.is_empty() {
        eprintln!();
        eprintln!("┌────────────────────────────────────────────────────────────────────┐");
        eprintln!("│  ℹ  WORKTREE HOMEOSTASIS — ADVISORY (nothing blocked; keep working) │");
        eprintln!("└────────────────────────────────────────────────────────────────────┘");
        eprintln!("  Your slot IS allocated. The item(s) below are housekeeping heads-ups,");
        eprintln!("  not errors and not a reason to stop or refuse work. Address them when");
        eprintln!("  convenient, or leave them for the coordinator.");
        for (n, w) in warnings.iter().enumerate() {
            eprintln!("  {}. {w}", n + 1);
        }
        eprintln!(
            "  See ai_docs/transient/2026-07-27-worktree-management-map.md §5 (disk hygiene)."
        );
        eprintln!("────────────────────────────────────────────────────────────────────────");
        eprintln!();
    }
}

fn state_path(root: &Path) -> PathBuf {
    root.join("worktree-state.json")
}

fn load_state(root: &Path) -> Value {
    let p = state_path(root);
    if !p.exists() {
        return json!({ "version": 3, "updated": Value::Null, "slots": {} });
    }
    let txt = std::fs::read_to_string(&p).unwrap_or_else(|e| die(&format!("read state: {e}")));
    let mut v: Value = serde_json::from_str(&txt)
        .unwrap_or_else(|e| die(&format!("parse worktree-state.json: {e}")));
    if !v.get("slots").map(|s| s.is_object()).unwrap_or(false) {
        v["slots"] = json!({});
    }
    v
}

fn save_state(root: &Path, state: &mut Value) {
    state["updated"] = json!(now_iso());
    state["version"] = json!(3);
    let txt = serde_json::to_string_pretty(state).unwrap();
    std::fs::write(state_path(root), txt + "\n")
        .unwrap_or_else(|e| die(&format!("write state: {e}")));
}

/// Rewrite the managed table block inside worktrees/ACTIVE.md from state.
/// Human-authored content outside the markers is preserved verbatim.
fn regen_active_md(root: &Path, state: &Value) {
    const BEGIN: &str = "<!-- BEGIN worktree-state (managed by scripts/allocate-worktree.rs; do not edit inside) -->";
    const END: &str = "<!-- END worktree-state -->";

    let mut rows = String::new();
    rows.push_str("| Slot | Agent | HermitBranch | ReverieBranch | LiteInst2Branch | Task | Status | ReadOnly |\n");
    rows.push_str("| --- | --- | --- | --- | --- | --- | --- | --- |\n");
    if let Some(slots) = state["slots"].as_object() {
        let mut names: Vec<&String> = slots.keys().collect();
        names.sort();
        for name in names {
            let s = &slots[name];
            let agents = s["agents"].as_array().cloned().unwrap_or_default();
            let owner = agents
                .iter()
                .find(|a| !a["read_only"].as_bool().unwrap_or(false))
                .and_then(|a| a["name"].as_str())
                .unwrap_or("-");
            let ro_agents: Vec<String> = agents
                .iter()
                .filter(|a| a["read_only"].as_bool().unwrap_or(false))
                .filter_map(|a| a["name"].as_str().map(|n| n.to_string()))
                .collect();
            let agent_cell = if ro_agents.is_empty() {
                owner.to_string()
            } else {
                format!("{owner} (+ro: {})", ro_agents.join(", "))
            };
            let hb = s["hermit_branch"].as_str().unwrap_or("-");
            let rb = s["reverie_branch"].as_str().unwrap_or("-");
            let lb = s["liteinst2_branch"].as_str().unwrap_or("-");
            let task = s["task"].as_str().unwrap_or("-");
            let status = s["status"].as_str().unwrap_or("active");
            let read_only = if ro_agents.is_empty() { "no" } else { "shared" };
            rows.push_str(&format!(
                "| {name} | {agent_cell} | {hb} | {rb} | {lb} | {task} | {status} | {read_only} |\n"
            ));
        }
    }
    let block = format!("{BEGIN}\n{rows}{END}\n");

    let path = root.join("worktrees").join("ACTIVE.md");
    let existing = std::fs::read_to_string(&path).unwrap_or_default();
    let new_content = if let (Some(b), Some(e)) = (existing.find(BEGIN), existing.find(END)) {
        let e_end = e + END.len();
        let after = existing[e_end..]
            .strip_prefix('\n')
            .unwrap_or(&existing[e_end..]);
        format!("{}{}{}", &existing[..b], block, after)
    } else {
        let sep = if existing.is_empty() || existing.ends_with('\n') {
            ""
        } else {
            "\n"
        };
        let lead = if existing.is_empty() {
            "# Active Hermit Worktrees\n\n## Machine-managed slot table\n\n".to_string()
        } else {
            format!("{sep}\n## Machine-managed slot table\n\n")
        };
        format!("{existing}{lead}{block}")
    };
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    std::fs::write(&path, new_content).unwrap_or_else(|e| die(&format!("write ACTIVE.md: {e}")));
}

/// Physical checkout of a product worktree, read from git porcelain. Mirrors the
/// canonical verifier scripts/check-worktree-registry.rs::actual_checkout so the
/// repair mode and the verifier agree on what "the actual branch" means.
fn actual_checkout(path: &Path) -> Option<String> {
    if !path.exists() {
        return Some("-".to_string()); // Absent -> recorded as "-"
    }
    let (inside, _, _) = git(path, &["rev-parse", "--is-inside-work-tree"]);
    if !inside {
        return None; // Unreadable: do NOT rewrite the recorded value; skip + warn.
    }
    let (on_branch, branch, _) = git(path, &["symbolic-ref", "--quiet", "--short", "HEAD"]);
    if on_branch && !branch.is_empty() {
        return Some(branch);
    }
    let (has_head, _, _) = git(path, &["rev-parse", "HEAD"]);
    if has_head {
        // Detached: verifier accepts the bare token "detached" for any detached SHA.
        Some("detached".to_string())
    } else {
        None
    }
}

/// SINGLE-WRITER REPAIR/SYNC: reconcile the recorded {product}_branch cells in
/// worktree-state.json (and, via regen_active_md, the managed ACTIVE.md block)
/// FROM the physical submodule porcelain. This is the reconciler that closes the
/// three-registries-no-reconciler gap (Proxy-Binding worked-example #9).
///
/// SAFETY CONTRACT:
///   * Only {product}_branch cells are touched. Ownership (agents/task/status/
///     purpose) is NEVER rewritten from physical state — repurposing intent lives
///     with a human, not on disk.
///   * NO git branch/checkout/delete is ever run. Unpushed local refs (e.g. a
///     slot's codex/* branch parked at a local-only SHA) survive inherently
///     because repair only rewrites recorded strings, never git objects.
///   * An Unreadable child leaves its recorded value untouched (skip + warn),
///     so a transient fault cannot erase a legitimate record.
/// Runs the verifier before and after so the drift delta is observable, not
/// inferred.
fn repair_registry(root: &Path, dry_run: bool) -> ! {
    let checker = root.join("scripts/check-worktree-registry.rs");
    let root_arg = root.to_string_lossy().into_owned();
    let run_checker = |label: &str| {
        eprintln!("── verifier {label} repair ──");
        let _ = Command::new(&checker)
            .args(["--root", root_arg.as_str()])
            .status();
    };
    run_checker("BEFORE");

    let mut state = load_state(root);
    let slot_names: Vec<String> = state["slots"]
        .as_object()
        .map(|s| s.keys().cloned().collect())
        .unwrap_or_default();

    let mut changed = 0usize;
    let mut skipped = 0usize;
    for slot in &slot_names {
        for product in ["hermit", "reverie", "liteinst2"] {
            let recorded = state["slots"][slot][format!("{product}_branch")]
                .as_str()
                .unwrap_or("-")
                .to_string();
            let rel = state["slots"][slot][format!("{product}_path")]
                .as_str()
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from(format!("worktrees/{slot}/{product}")));
            match actual_checkout(&root.join(&rel)) {
                None => {
                    skipped += 1;
                    eprintln!(
                        "  skip {slot}/{product}: worktree unreadable; recorded='{recorded}' left as-is"
                    );
                }
                Some(actual) => {
                    // "detached" recorded value already matches any detached SHA in
                    // the verifier; don't churn a detached:<sha> recording into bare
                    // "detached" if it already agrees.
                    let already_ok = recorded == actual
                        || (actual == "detached" && recorded.starts_with("detached"));
                    if !already_ok {
                        changed += 1;
                        println!("  reconcile {slot}/{product}: '{recorded}' -> '{actual}'");
                        if !dry_run {
                            state["slots"][slot][format!("{product}_branch")] = json!(actual);
                        }
                    }
                }
            }
        }
    }

    if dry_run {
        println!("repair --dry-run: {changed} branch cell(s) would change, {skipped} skipped (unreadable). No files written.");
        exit(if changed == 0 { 0 } else { 1 });
    }

    if changed > 0 {
        save_state(root, &mut state);
        regen_active_md(root, &state);
        println!("repair: reconciled {changed} branch cell(s), {skipped} skipped (unreadable).");
        println!("  state:  {}", state_path(root).display());
        println!("  active: {}", root.join("worktrees/ACTIVE.md").display());
    } else {
        println!("repair: 0 branch cells needed reconciliation ({skipped} skipped unreadable).");
    }

    run_checker("AFTER");
    exit(0);
}

/// Slot name is a named token [a-z0-9-]+ (e.g. kvm) or slotNN.
fn valid_slot(name: &str) -> bool {
    !name.is_empty()
        && name
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
        && name
            .chars()
            .next()
            .map(|c| c.is_ascii_alphanumeric())
            .unwrap_or(false)
}

fn next_free_slot(root: &Path, state: &Value) -> String {
    let slots = state["slots"].as_object();
    for n in 1..=999 {
        let name = format!("slot{n:02}");
        let in_state = slots.map(|s| s.contains_key(&name)).unwrap_or(false);
        if !in_state && !root.join("worktrees").join(&name).exists() {
            return name;
        }
    }
    die("no free slot found in slot01..slot999");
}

fn add_worktree(primary: &Path, dst: &Path, branch: Option<&str>, start: &str) {
    if dst.exists() {
        let (ok, _, _) = git(dst, &["rev-parse", "--is-inside-work-tree"]);
        if ok {
            println!("  adopt existing worktree {}", dst.display());
            return;
        }
        die(&format!(
            "path exists but is not a git worktree: {}",
            dst.display()
        ));
    }
    if let Some(parent) = dst.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    let dst_s = dst.to_string_lossy().to_string();
    let (ok, _, err) = match branch {
        Some(b) => git(primary, &["worktree", "add", "-b", b, &dst_s, start]),
        None => git(primary, &["worktree", "add", "--detach", &dst_s, start]),
    };
    if !ok {
        die(&format!(
            "git worktree add failed for {}: {err}",
            dst.display()
        ));
    }
    println!("  created {}", dst.display());
}

fn includes_product(selection: &str, product: &str) -> bool {
    selection == "all"
        || selection == product
        || (selection == "both" && matches!(product, "hermit" | "reverie"))
}

fn primary_start(root: &Path, product: &str, override_start: Option<&str>) -> String {
    if let Some(start) = override_start {
        return start.to_string();
    }
    let (ok, out, _) = git(&root.join(product), &["rev-parse", "--abbrev-ref", "HEAD"]);
    if ok && !out.is_empty() {
        out
    } else {
        "HEAD".to_string()
    }
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let mut agent = String::new();
    let mut slot = String::new();
    let mut task = String::new();
    let mut product = "all".to_string();
    let mut hermit_branch: Option<String> = None;
    let mut reverie_branch: Option<String> = None;
    let mut liteinst2_branch: Option<String> = None;
    let mut start_point: Option<String> = None;
    let mut purpose = String::new();
    let mut read_mostly = false;
    let mut check_only = false;
    let mut repair = false;
    let mut dry_run = false;

    let mut i = 0;
    let take = |i: &mut usize, argv: &[String], flag: &str| -> String {
        *i += 1;
        if *i >= argv.len() {
            die(&format!("{flag} requires a value"));
        }
        argv[*i].clone()
    };
    while i < argv.len() {
        match argv[i].as_str() {
            "--agent" => agent = take(&mut i, &argv, "--agent"),
            "--slot" => slot = take(&mut i, &argv, "--slot"),
            "--task" => task = take(&mut i, &argv, "--task"),
            "--product" => product = take(&mut i, &argv, "--product"),
            "--hermit-branch" => hermit_branch = Some(take(&mut i, &argv, "--hermit-branch")),
            "--reverie-branch" => reverie_branch = Some(take(&mut i, &argv, "--reverie-branch")),
            "--liteinst2-branch" => {
                liteinst2_branch = Some(take(&mut i, &argv, "--liteinst2-branch"))
            }
            "--start-point" => start_point = Some(take(&mut i, &argv, "--start-point")),
            "--purpose" => purpose = take(&mut i, &argv, "--purpose"),
            "--i-promise-this-agent-is-read-mostly" => read_mostly = true,
            "--check-only" => check_only = true,
            "--repair" | "--sync" => repair = true,
            "--dry-run" => dry_run = true,
            "-h" | "--help" => {
                print!("{USAGE}");
                return;
            }
            other => die(&format!("unknown argument: {other}\n\n{USAGE}")),
        }
        i += 1;
    }

    // Single-writer REPAIR/SYNC: reconcile recorded branch cells from physical
    // porcelain (never touches git objects or ownership). Runs before the normal
    // allocation path so it cannot be confused with a slot request.
    if repair {
        let root = find_root();
        let _registry_lock = lock_registry(&root);
        repair_registry(&root, dry_run);
    }

    // Health check only: report homeostasis and exit without allocating.
    if check_only {
        let root = find_root();
        let _registry_lock = lock_registry(&root);
        homeostasis_check(&root);
        let checker = root.join("scripts/check-worktree-registry.rs");
        let root_arg = root.to_string_lossy().into_owned();
        let status = Command::new(&checker)
            .args(["--root", root_arg.as_str()])
            .status()
            .unwrap_or_else(|error| die(&format!("run {}: {error}", checker.display())));
        if !status.success() {
            exit(status.code().unwrap_or(1));
        }
        return;
    }

    if agent.is_empty() {
        die(&format!("--agent is required\n\n{USAGE}"));
    }
    if !matches!(
        product.as_str(),
        "hermit" | "reverie" | "liteinst2" | "both" | "all"
    ) {
        die("--product must be hermit, reverie, liteinst2, both, or all");
    }

    let include_hermit = includes_product(&product, "hermit");
    let include_reverie = includes_product(&product, "reverie");
    let include_liteinst2 = includes_product(&product, "liteinst2");
    if hermit_branch.is_some() && !include_hermit {
        die("--hermit-branch requires --product hermit, both, or all");
    }
    if reverie_branch.is_some() && !include_reverie {
        die("--reverie-branch requires --product reverie, both, or all");
    }
    if liteinst2_branch.is_some() && !include_liteinst2 {
        die("--liteinst2-branch requires --product liteinst2 or all");
    }

    let root = find_root();
    let _registry_lock = lock_registry(&root);
    let mut state = load_state(&root);

    // Default slot name: agent name with a leading 'hermit-' stripped.
    if slot.is_empty() {
        let candidate = agent.strip_prefix("hermit-").unwrap_or(&agent).to_string();
        slot = if valid_slot(&candidate) {
            candidate
        } else {
            next_free_slot(&root, &state)
        };
        println!("selected slot: {slot}");
    }
    if !valid_slot(&slot) {
        die(&format!(
            "invalid slot name: '{slot}' (expected [a-z0-9-]+ or slotNN)"
        ));
    }

    // 1-1 mapping: refuse if this mutating agent already owns a different slot.
    if !read_mostly {
        if let Some(slots) = state["slots"].as_object() {
            for (other, s) in slots {
                if other == &slot {
                    continue;
                }
                let owns = s["agents"]
                    .as_array()
                    .map(|a| {
                        a.iter().any(|x| {
                            x["name"].as_str() == Some(&agent)
                                && !x["read_only"].as_bool().unwrap_or(false)
                        })
                    })
                    .unwrap_or(false);
                if owns {
                    die(&format!(
                        "agent '{agent}' already owns slot '{other}' (1 slot per agent).\n\
                         Release it first (scripts/release-worktree.rs --slot {other}) \
                         or reuse it."
                    ));
                }
            }
        }
    }

    // Collision check against existing owner of the target slot.
    if let Some(existing) = state["slots"].get(&slot) {
        let owner = existing["agents"]
            .as_array()
            .and_then(|a| {
                a.iter()
                    .find(|x| !x["read_only"].as_bool().unwrap_or(false))
            })
            .and_then(|x| x["name"].as_str())
            .map(|s| s.to_string());
        if let Some(owner) = owner {
            if owner != agent && !read_mostly {
                die(&format!(
                    "slot {slot} is owned by '{owner}'. Refusing collision.\n\
                     Use a different --slot, or pass --i-promise-this-agent-is-read-mostly \
                     to share read-only."
                ));
            }
        }
    }

    println!(
        "Allocating worktrees/{slot} for agent '{agent}' (product={product}, start={})",
        start_point.as_deref().unwrap_or("each primary HEAD")
    );

    let slot_dir = root.join("worktrees").join(&slot);
    if include_hermit {
        let start = primary_start(&root, "hermit", start_point.as_deref());
        add_worktree(
            &root.join("hermit"),
            &slot_dir.join("hermit"),
            hermit_branch.as_deref(),
            &start,
        );
    }
    if include_reverie {
        let rstart = primary_start(&root, "reverie", start_point.as_deref());
        add_worktree(
            &root.join("reverie"),
            &slot_dir.join("reverie"),
            reverie_branch.as_deref(),
            &rstart,
        );
    }
    if include_liteinst2 {
        let lstart = primary_start(&root, "liteinst2", start_point.as_deref());
        add_worktree(
            &root.join("liteinst2"),
            &slot_dir.join("liteinst2"),
            liteinst2_branch.as_deref(),
            &lstart,
        );
    }

    // Merge/append this agent into the slot record.
    let now = now_iso();
    let entry = state["slots"]
        .as_object_mut()
        .unwrap()
        .entry(slot.clone())
        .or_insert_with(|| {
            json!({
                "agents": [],
                "allocated": now.clone(),
                "hermit_path": format!("worktrees/{slot}/hermit"),
                "reverie_path": format!("worktrees/{slot}/reverie"),
                "liteinst2_path": format!("worktrees/{slot}/liteinst2"),
            })
        });
    entry["hermit_path"] = json!(format!("worktrees/{slot}/hermit"));
    entry["reverie_path"] = json!(format!("worktrees/{slot}/reverie"));
    entry["liteinst2_path"] = json!(format!("worktrees/{slot}/liteinst2"));
    entry["status"] = json!("active");
    entry["updated"] = json!(now);
    // Slot-level task/purpose/branches describe the mutating OWNER. A read-mostly
    // sharer records its own task per-agent (below) but must not clobber these.
    if !read_mostly {
        if !task.is_empty() {
            entry["task"] = json!(task);
        }
        if !purpose.is_empty() {
            entry["purpose"] = json!(purpose);
        }
        if let Some(b) = &hermit_branch {
            entry["hermit_branch"] = json!(b);
        }
        if let Some(b) = &reverie_branch {
            entry["reverie_branch"] = json!(b);
        }
        if let Some(b) = &liteinst2_branch {
            entry["liteinst2_branch"] = json!(b);
        }
    }
    if entry.get("hermit_branch").is_none() || (include_hermit && entry["hermit_branch"] == "-") {
        entry["hermit_branch"] = json!(if include_hermit { "detached" } else { "-" });
    }
    if entry.get("reverie_branch").is_none() || (include_reverie && entry["reverie_branch"] == "-")
    {
        entry["reverie_branch"] = json!(if include_reverie { "detached" } else { "-" });
    }
    if entry.get("liteinst2_branch").is_none()
        || (include_liteinst2 && entry["liteinst2_branch"] == "-")
    {
        entry["liteinst2_branch"] = json!(if include_liteinst2 { "detached" } else { "-" });
    }
    let agents = entry["agents"].as_array_mut().unwrap();
    if let Some(a) = agents
        .iter_mut()
        .find(|a| a["name"].as_str() == Some(&agent))
    {
        a["read_only"] = json!(read_mostly);
        if !task.is_empty() {
            a["task"] = json!(task.clone());
        }
    } else {
        agents.push(json!({ "name": agent, "read_only": read_mostly, "task": task }));
    }

    save_state(&root, &mut state);
    regen_active_md(&root, &state);

    println!("\n✓ allocated worktrees/{slot} to {agent}");
    println!("  state:   {}", state_path(&root).display());
    println!("  active:  {}", root.join("worktrees/ACTIVE.md").display());

    // Advisory workspace-health banner (never blocks allocation).
    homeostasis_check(&root);
    if hermit_branch.is_none() && include_hermit {
        println!(
            "  note: hermit worktree is DETACHED. Create a feature branch before editing:\n\
             \r        git -C worktrees/{slot}/hermit switch -c <branch> origin/main"
        );
    }
}
