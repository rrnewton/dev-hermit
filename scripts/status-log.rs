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

Required:
  --mapping-json JSON    Object mapping each workstream slug to
                         {"agent":"NAME","task":"TASK-ID"}.
  --open-prs N           Current total number of open PRs.
  --genuine-reds N       Current number of genuine-red PRs.
  --fleet-count N        Current active fleet size.

Status input:
  --status-file PATH     Read the full status text from PATH.
                         When omitted, read the full text from stdin.

Other:
  --log-file PATH        Override the repository-relative log path.
                         Default: ai_docs/status-log/status-log.jsonl
  -h, --help             Show this help.

Example:
  printf 'Fleet healthy; 3 PRs open.\n' | scripts/status-log.rs \
    --mapping-json '{"pr-landing":{"agent":"hermit-coord","task":"landing-watch"}}' \
    --open-prs 3 --genuine-reds 0 --fleet-count 8
"#;

#[derive(Default)]
struct Args {
    mapping_json: Option<String>,
    open_prs: Option<u64>,
    genuine_reds: Option<u64>,
    fleet_count: Option<u64>,
    status_file: Option<PathBuf>,
    log_file: Option<PathBuf>,
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
            "--open-prs" => parsed.open_prs = Some(parse_count(&arg, take_value(&arg, &mut args))),
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
    let mapping_json = args
        .mapping_json
        .as_deref()
        .unwrap_or_else(|| die("missing --mapping-json"));
    let mapping: Value = serde_json::from_str(mapping_json)
        .unwrap_or_else(|e| die(&format!("parse --mapping-json: {e}")));
    validate_mapping(&mapping);

    let open_prs = args.open_prs.unwrap_or_else(|| die("missing --open-prs"));
    let genuine_reds = args
        .genuine_reds
        .unwrap_or_else(|| die("missing --genuine-reds"));
    let fleet_count = args
        .fleet_count
        .unwrap_or_else(|| die("missing --fleet-count"));
    let status_text = read_status_text(args.status_file.as_deref());

    let root = repository_root();
    let log_path = match args.log_file {
        Some(path) if path.is_absolute() => path,
        Some(path) => root.join(path),
        None => root.join("ai_docs/status-log/status-log.jsonl"),
    };
    if let Some(parent) = log_path.parent() {
        fs::create_dir_all(parent)
            .unwrap_or_else(|e| die(&format!("create log directory {}: {e}", parent.display())));
    }

    let entry = json!({
        "timestamp": timestamp_from_date(),
        "workstream_to_worker": mapping,
        "open_prs": open_prs,
        "genuine_reds": genuine_reds,
        "fleet_count": fleet_count,
        "status_text": status_text,
    });
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
