#!/usr/bin/env rust-script
//! Append one structured coordinator status update to the durable JSONL log.
//!
//! ```cargo
//! [dependencies]
//! serde_json = "1"
//! ```

use serde_json::{json, Value};
use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{exit, Command};

const USAGE: &str = r#"Usage: scripts/status-log.rs [OPTIONS]

EVERY COUNT CARRIES ITS DENOMINATOR. Two adjacent entries once recorded
open_prs=105 and open_prs=10 nineteen minutes apart because one counted total
open across repositories and the other counted a ready/non-draft subset. The
same field name made a denominator change look like a 10x real drop. A count
whose counted set is not recorded beside it is not a measurement, so --repos is
required and `open_prs` has exactly one permitted meaning.

Required:
  --mapping-json JSON    Object mapping each workstream slug to
                         {"agent":"NAME","task":"TASK-ID"}.
  --repos LIST           Comma-separated owner/name repositories that every
                         count below was taken over, e.g.
                         rrnewton/hermit,rrnewton/reverie. This is the
                         denominator; it is recorded with the counts.
  --open-prs N           TOTAL open PRs INCLUDING DRAFTS across --repos.
                         This is the only permitted meaning of `open_prs`.
  --genuine-reds N       Current number of genuine-red PRs.
  --fleet-count N        Current active fleet size.

Status input:
  --status-file PATH     Read the full status text from PATH.
                         When omitted, read the full text from stdin.

Other:
  --ready-prs N          OPTIONAL, and a DISTINCT field. The ready/non-draft
                         subset. Recorded as `ready_prs`, never as `open_prs`.
  --open-prs-basis B     Assert the basis of --open-prs. The only accepted
                         value is total-open-including-drafts (the default).
                         A ready/non-draft basis is REFUSED here: pass it as
                         --ready-prs instead.
  --describe-log [PATH]  Read the log and print one line per entry describing
                         each entry's count denominator, then exit. Entries
                         written before schema 2 are labelled
                         denominator=unknown. Read-only: never rewrites.
  --log-file PATH        Override the repository-relative log path.
                         Default: ai_docs/status-log/status-log.jsonl
  -h, --help             Show this help.

Example:
  printf 'Fleet healthy; 3 PRs open.\n' | scripts/status-log.rs \
    --mapping-json '{"pr-landing":{"agent":"hermit-coord","task":"landing-watch"}}' \
    --repos rrnewton/hermit,rrnewton/reverie \
    --open-prs 3 --genuine-reds 0 --fleet-count 8
"#;

/// The ONE permitted meaning of `open_prs`.
const BASIS_TOTAL_OPEN: &str = "total-open-including-drafts";
/// The basis recorded for the distinct `ready_prs` field.
const BASIS_READY: &str = "ready-non-draft";
/// Entries at this schema carry `count_semantics`; anything lower does not and
/// is therefore of unknown denominator.
const SCHEMA_VERSION: u64 = 2;

#[derive(Default)]
struct Args {
    mapping_json: Option<String>,
    repos: Option<String>,
    open_prs: Option<u64>,
    open_prs_basis: Option<String>,
    ready_prs: Option<u64>,
    genuine_reds: Option<u64>,
    fleet_count: Option<u64>,
    status_file: Option<PathBuf>,
    log_file: Option<PathBuf>,
    describe_log: bool,
}

/// Parse and validate the repository scope. This is the denominator, so an
/// empty or malformed set is refused rather than recorded as a guess.
fn parse_repos(raw: &str) -> Result<Vec<String>, String> {
    let mut repos: Vec<String> = Vec::new();
    for part in raw.split(',') {
        let name = part.trim();
        if name.is_empty() {
            return Err("--repos contains an empty entry".to_string());
        }
        let mut halves = name.split('/');
        let owner = halves.next().unwrap_or_default();
        let repo = halves.next().unwrap_or_default();
        if owner.is_empty() || repo.is_empty() || halves.next().is_some() {
            return Err(format!(
                "--repos entry {name:?} must be exactly owner/name, e.g. rrnewton/hermit"
            ));
        }
        if !repos.iter().any(|existing| existing == name) {
            repos.push(name.to_string());
        }
    }
    if repos.is_empty() {
        return Err("--repos must name at least one owner/name repository".to_string());
    }
    Ok(repos)
}

/// Refuse a subset basis under `open_prs`. This is THE negative case: a
/// ready/non-draft count silently accepted here is exactly the defect that made
/// a denominator change look like a 10x drop.
fn validate_open_prs_basis(basis: &str) -> Result<(), String> {
    if basis == BASIS_TOTAL_OPEN {
        return Ok(());
    }
    let normalized = basis.trim().to_ascii_lowercase().replace('_', "-");
    let subset_like = [
        "ready",
        "ready-only",
        "ready-non-draft",
        "non-draft",
        "nondraft",
        "open-non-draft",
        "ready-prs",
        "excluding-drafts",
        "no-drafts",
    ];
    if subset_like.contains(&normalized.as_str()) {
        return Err(format!(
            "--open-prs-basis {basis:?} is a READY/NON-DRAFT SUBSET and is refused under \
             `open_prs`. `open_prs` means {BASIS_TOTAL_OPEN} and nothing else. Pass the subset \
             as --ready-prs, which is recorded in its own `ready_prs` field with basis \
             {BASIS_READY}."
        ));
    }
    Err(format!(
        "--open-prs-basis {basis:?} is not a recognised basis; the only accepted value is \
         {BASIS_TOTAL_OPEN}"
    ))
}

/// Describe one logged entry's count denominator. Entries written before
/// schema 2 carry no semantics, so they are labelled unknown rather than being
/// assumed to mean whatever the current field name means.
fn describe_entry(index: usize, entry: &Value) -> String {
    let timestamp = entry
        .get("timestamp")
        .and_then(Value::as_str)
        .unwrap_or("<no-timestamp>");
    let open_prs = entry
        .get("open_prs")
        .map(|v| v.to_string())
        .unwrap_or_else(|| "<absent>".to_string());
    let semantics = entry
        .get("count_semantics")
        .and_then(|s| s.get("open_prs"));
    match semantics {
        Some(open) => {
            let basis = open
                .get("basis")
                .and_then(Value::as_str)
                .unwrap_or("<no-basis>");
            let repos = open
                .get("repos")
                .and_then(Value::as_array)
                .map(|r| {
                    r.iter()
                        .filter_map(Value::as_str)
                        .collect::<Vec<_>>()
                        .join("+")
                })
                .unwrap_or_else(|| "<no-repos>".to_string());
            format!("{index} {timestamp} open_prs={open_prs} basis={basis} repos={repos}")
        }
        None => format!(
            "{index} {timestamp} open_prs={open_prs} basis=unknown repos=unknown \
             denominator=unknown pre-schema-{SCHEMA_VERSION}-entry"
        ),
    }
}

fn die(message: &str) -> ! {
    eprintln!("status-log: {message}");
    exit(2);
}

fn take_value<I>(flag: &str, args: &mut I) -> String
where
    I: Iterator<Item = String>,
{
    args.next()
        .unwrap_or_else(|| die(&format!("{flag} requires a value")))
}

fn parse_count(flag: &str, value: String) -> u64 {
    value
        .parse::<u64>()
        .unwrap_or_else(|_| die(&format!("{flag} must be a non-negative integer")))
}

fn parse_args() -> Args {
    let mut parsed = Args::default();
    let mut args = env::args().skip(1);

    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--mapping-json" => parsed.mapping_json = Some(take_value(&arg, &mut args)),
            "--repos" => parsed.repos = Some(take_value(&arg, &mut args)),
            "--open-prs" => parsed.open_prs = Some(parse_count(&arg, take_value(&arg, &mut args))),
            "--open-prs-basis" => parsed.open_prs_basis = Some(take_value(&arg, &mut args)),
            "--ready-prs" => {
                parsed.ready_prs = Some(parse_count(&arg, take_value(&arg, &mut args)))
            }
            "--describe-log" => parsed.describe_log = true,
            "--genuine-reds" => {
                parsed.genuine_reds = Some(parse_count(&arg, take_value(&arg, &mut args)))
            }
            "--fleet-count" => {
                parsed.fleet_count = Some(parse_count(&arg, take_value(&arg, &mut args)))
            }
            "--status-file" => parsed.status_file = Some(take_value(&arg, &mut args).into()),
            "--log-file" => parsed.log_file = Some(take_value(&arg, &mut args).into()),
            "-h" | "--help" => {
                print!("{USAGE}");
                exit(0);
            }
            _ => die(&format!("unknown argument: {arg}\n\n{USAGE}")),
        }
    }

    parsed
}

fn repository_root() -> PathBuf {
    let mut path = env::current_dir().unwrap_or_else(|e| die(&format!("current directory: {e}")));
    loop {
        if path.join(".gitmodules").is_file() && path.join("AGENTS.md").is_file() {
            return path;
        }
        if !path.pop() {
            die("could not locate the dev-hermit repository root");
        }
    }
}

fn timestamp_from_date() -> String {
    let output = Command::new("date")
        .args(["-u", "+%Y-%m-%dT%H:%M:%SZ"])
        .output()
        .unwrap_or_else(|e| die(&format!("run date: {e}")));
    if !output.status.success() {
        die("date failed while generating the timestamp");
    }
    String::from_utf8(output.stdout)
        .unwrap_or_else(|e| die(&format!("date returned non-UTF-8 output: {e}")))
        .trim_end()
        .to_owned()
}

fn validate_mapping(value: &Value) {
    let mapping = value
        .as_object()
        .unwrap_or_else(|| die("--mapping-json must be a JSON object"));
    for (slug, worker) in mapping {
        if slug.is_empty() {
            die("workstream slugs must not be empty");
        }
        let worker = worker
            .as_object()
            .unwrap_or_else(|| die(&format!("mapping value for {slug:?} must be an object")));
        for field in ["agent", "task"] {
            let field_value = worker
                .get(field)
                .and_then(Value::as_str)
                .unwrap_or_else(|| {
                    die(&format!(
                        "mapping value for {slug:?} must contain a string {field:?} field"
                    ))
                });
            if field_value.is_empty() {
                die(&format!("mapping {field:?} for {slug:?} must not be empty"));
            }
        }
    }
}

fn read_status_text(status_file: Option<&Path>) -> String {
    let mut text = String::new();
    match status_file {
        Some(path) => {
            text = fs::read_to_string(path)
                .unwrap_or_else(|e| die(&format!("read status file {}: {e}", path.display())))
        }
        None => {
            io::stdin()
                .read_to_string(&mut text)
                .unwrap_or_else(|e| die(&format!("read status text from stdin: {e}")));
        }
    }
    if text.is_empty() {
        die("status text must not be empty");
    }
    text
}

fn main() {
    let args = parse_args();

    let root = repository_root();
    let log_path = match &args.log_file {
        Some(path) if path.is_absolute() => path.clone(),
        Some(path) => root.join(path),
        None => root.join("ai_docs/status-log/status-log.jsonl"),
    };

    // Read-only. Never rewrites history; old rows are labelled, not migrated.
    if args.describe_log {
        let text = fs::read_to_string(&log_path)
            .unwrap_or_else(|e| die(&format!("read log {}: {e}", log_path.display())));
        for (index, line) in text.lines().filter(|l| !l.trim().is_empty()).enumerate() {
            match serde_json::from_str::<Value>(line) {
                Ok(entry) => println!("{}", describe_entry(index, &entry)),
                Err(e) => println!("{index} <unparseable entry> denominator=unknown error={e}"),
            }
        }
        exit(0);
    }

    let mapping_json = args
        .mapping_json
        .as_deref()
        .unwrap_or_else(|| die("missing --mapping-json"));
    let mapping: Value = serde_json::from_str(mapping_json)
        .unwrap_or_else(|e| die(&format!("parse --mapping-json: {e}")));
    validate_mapping(&mapping);

    let repos_raw = args
        .repos
        .as_deref()
        .unwrap_or_else(|| die("missing --repos: a count without its counted set is not a measurement"));
    let repos = parse_repos(repos_raw).unwrap_or_else(|e| die(&e));
    validate_open_prs_basis(args.open_prs_basis.as_deref().unwrap_or(BASIS_TOTAL_OPEN))
        .unwrap_or_else(|e| die(&e));

    let open_prs = args.open_prs.unwrap_or_else(|| die("missing --open-prs"));
    let genuine_reds = args
        .genuine_reds
        .unwrap_or_else(|| die("missing --genuine-reds"));
    let fleet_count = args
        .fleet_count
        .unwrap_or_else(|| die("missing --fleet-count"));
    let status_text = read_status_text(args.status_file.as_deref());

    if let Some(parent) = log_path.parent() {
        fs::create_dir_all(parent)
            .unwrap_or_else(|e| die(&format!("create log directory {}: {e}", parent.display())));
    }

    let mut count_semantics = json!({
        "open_prs": {
            "basis": BASIS_TOTAL_OPEN,
            "includes_drafts": true,
            "repos": repos,
        },
    });
    let mut entry = json!({
        "schema_version": SCHEMA_VERSION,
        "timestamp": timestamp_from_date(),
        "workstream_to_worker": mapping,
        "open_prs": open_prs,
        "genuine_reds": genuine_reds,
        "fleet_count": fleet_count,
        "status_text": status_text,
    });
    // The ready/non-draft subset is a DIFFERENT field with a DIFFERENT basis. It
    // is never allowed to occupy `open_prs`.
    if let Some(ready) = args.ready_prs {
        entry["ready_prs"] = json!(ready);
        count_semantics["ready_prs"] = json!({
            "basis": BASIS_READY,
            "includes_drafts": false,
            "repos": repos,
        });
    }
    entry["count_semantics"] = count_semantics;
    let line = serde_json::to_string(&entry)
        .unwrap_or_else(|e| die(&format!("serialize status entry: {e}")));

    let mut log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .unwrap_or_else(|e| die(&format!("open log {}: {e}", log_path.display())));
    log.write_all(line.as_bytes())
        .and_then(|_| log.write_all(b"\n"))
        .and_then(|_| log.flush())
        .unwrap_or_else(|e| die(&format!("append log {}: {e}", log_path.display())));

    println!("appended {}", log_path.display());
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---- POSITIVE: total-open with an explicit repo scope is accepted ----

    #[test]
    fn explicit_repo_scope_is_accepted_and_deduped() {
        assert_eq!(
            parse_repos("rrnewton/hermit,rrnewton/reverie").unwrap(),
            vec!["rrnewton/hermit", "rrnewton/reverie"]
        );
        // whitespace tolerated, duplicates collapsed -- the denominator is a SET
        assert_eq!(
            parse_repos(" rrnewton/hermit , rrnewton/hermit ").unwrap(),
            vec!["rrnewton/hermit"]
        );
    }

    #[test]
    fn default_basis_is_total_open_including_drafts() {
        assert!(validate_open_prs_basis(BASIS_TOTAL_OPEN).is_ok());
    }

    // ---- NEGATIVE: a ready-only count under `open_prs` is refused ----

    #[test]
    fn ready_only_basis_is_refused_under_open_prs_and_names_the_right_field() {
        for basis in [
            "ready", "ready-only", "ready_non_draft", "non-draft", "nondraft",
            "no-drafts", "excluding-drafts", "READY",
        ] {
            let err = validate_open_prs_basis(basis)
                .expect_err(&format!("{basis:?} is a subset and must be refused"));
            assert!(
                err.contains("--ready-prs"),
                "refusal must redirect to the distinct field, got: {err}"
            );
            assert!(err.contains("SUBSET"), "refusal must say why: {err}");
        }
    }

    #[test]
    fn an_unrecognised_basis_is_refused_rather_than_silently_recorded() {
        let err = validate_open_prs_basis("whatever-i-counted").unwrap_err();
        assert!(err.contains(BASIS_TOTAL_OPEN));
    }

    #[test]
    fn a_missing_or_malformed_repo_scope_is_refused() {
        for bad in ["", "   ", "hermit", "rrnewton/", "/hermit", "a/b/c", "rrnewton/hermit,"] {
            assert!(
                parse_repos(bad).is_err(),
                "{bad:?} is not a usable denominator and must be refused"
            );
        }
    }

    // ---- OLD ROWS: labelled unknown, never rewritten or assumed ----

    #[test]
    fn pre_schema2_rows_are_labelled_unknown_denominator() {
        // Exactly the shape of all 50 pre-existing rows: no schema_version,
        // no count_semantics.
        let old = serde_json::json!({
            "timestamp": "2026-08-06T15:05:12Z",
            "open_prs": 105,
            "genuine_reds": 0,
            "fleet_count": 15,
            "status_text": "…",
        });
        let line = describe_entry(7, &old);
        assert!(line.contains("denominator=unknown"), "{line}");
        assert!(line.contains("basis=unknown"), "{line}");
        assert!(line.contains("open_prs=105"), "{line}");
        assert!(line.contains("2026-08-06T15:05:12Z"), "{line}");
    }

    #[test]
    fn schema2_rows_report_their_actual_basis_and_scope() {
        let new = serde_json::json!({
            "schema_version": 2,
            "timestamp": "2026-08-07T02:00:00Z",
            "open_prs": 10,
            "count_semantics": {
                "open_prs": {
                    "basis": BASIS_TOTAL_OPEN,
                    "includes_drafts": true,
                    "repos": ["rrnewton/hermit", "rrnewton/reverie"],
                },
            },
        });
        let line = describe_entry(8, &new);
        assert!(line.contains(BASIS_TOTAL_OPEN), "{line}");
        assert!(line.contains("rrnewton/hermit+rrnewton/reverie"), "{line}");
        assert!(!line.contains("unknown"), "a scoped row must not read unknown: {line}");
    }

    /// THE REGRESSION THIS EXISTS FOR: 105 and 10 nineteen minutes apart looked
    /// like a 10x drop. Once each row carries its denominator, the two are
    /// visibly incomparable rather than falsely comparable.
    #[test]
    fn the_105_vs_10_pair_is_no_longer_silently_comparable() {
        let total = serde_json::json!({
            "schema_version": 2, "timestamp": "t1", "open_prs": 105,
            "count_semantics": {"open_prs": {"basis": BASIS_TOTAL_OPEN, "repos": ["rrnewton/hermit"]}},
        });
        // The ready subset can no longer be written as open_prs at all...
        assert!(validate_open_prs_basis("ready").is_err());
        // ...so it lands in its own field, and the describe view shows the bases differ.
        let ready_as_its_own_field = serde_json::json!({
            "schema_version": 2, "timestamp": "t2", "open_prs": 105, "ready_prs": 10,
            "count_semantics": {
                "open_prs": {"basis": BASIS_TOTAL_OPEN, "repos": ["rrnewton/hermit"]},
                "ready_prs": {"basis": BASIS_READY, "repos": ["rrnewton/hermit"]},
            },
        });
        let a = describe_entry(0, &total);
        let b = describe_entry(1, &ready_as_its_own_field);
        assert!(a.contains("open_prs=105") && b.contains("open_prs=105"),
            "the comparable quantity now agrees: {a} / {b}");
    }
}
