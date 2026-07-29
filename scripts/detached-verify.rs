#!/usr/bin/env rust-script
//! Run noisy commands without streaming their output into an agent context.
//!
//! The child is launched through `nohup setsid --fork --wait`, with stdin
//! disconnected and stdout/stderr redirected to `ignored/logs/`. This process
//! waits quietly for completion, then prints only a bounded summary. The
//! twice mode reports the raw comparison and, for a Hermit `--strict
//! --verify` command, also ignores Hermit's random temporary log filenames.

use std::collections::VecDeque;
use std::env;
use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, BufReader, Read};
use std::os::unix::process::ExitStatusExt;
use std::path::{Path, PathBuf};
use std::process::{self, Command, ExitStatus, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const DEFAULT_TAIL_LINES: usize = 20;
const DEFAULT_GREP_LIMIT: usize = 20;
const MAX_DISPLAY_CHARS: usize = 500;
const COPY_BUFFER_BYTES: usize = 64 * 1024;

const USAGE: &str = r#"Usage:
  scripts/detached-verify.rs run [OPTIONS] -- COMMAND [ARG...]
  scripts/detached-verify.rs verify-twice [OPTIONS] -- COMMAND [ARG...]

Modes:
  run           Run one detached command and print a bounded summary.
  verify-twice  Run the complete command twice, compare the combined logs
                byte-for-byte, and report identical or divergent. Hermit
                --strict --verify commands also get a normalized verdict that
                ignores only their random /tmp run-log filenames.

Options:
  --name NAME          Log filename prefix (default: command basename).
  --tail N             Number of trailing log lines to print (default: 20).
  --grep TEXT          Case-insensitive marker to select; repeatable. Explicit
                       markers replace the defaults.
  --grep-limit N       Maximum selected lines to print per log (default: 20).
  --no-grep            Disable marker selection.
  --logs-dir PATH      Log directory (default: <workspace>/ignored/logs).
  -h, --help           Show this help.

Default grep markers:
  error, failed, failure, panic, success, determin, diverg

Examples:
  scripts/detached-verify.rs run --name cargo-build --tail 8 -- \
    with-proxy cargo build --workspace

  scripts/detached-verify.rs verify-twice --name python-pool --tail 8 -- \
    ./target/release/hermit run --strict --verify -- python3 pool.py
"#;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Mode {
    Run,
    VerifyTwice,
}

struct Options {
    mode: Mode,
    name: String,
    tail_lines: usize,
    grep_patterns: Vec<String>,
    grep_limit: usize,
    logs_dir: PathBuf,
    command: Vec<OsString>,
}

struct RunResult {
    status: ExitStatus,
    log_path: PathBuf,
    duration: Duration,
    bytes: u64,
}

struct GrepSummary {
    lines: Vec<(usize, String)>,
    total_matches: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Comparison {
    Identical,
    Divergent { byte_offset: u64, line: u64 },
}

fn main() {
    match real_main() {
        Ok(code) => process::exit(code),
        Err(message) => {
            eprintln!("detached-verify: {message}");
            process::exit(2);
        }
    }
}

fn real_main() -> Result<i32, String> {
    let options = parse_args(env::args_os().skip(1).collect())?;
    fs::create_dir_all(&options.logs_dir).map_err(|error| {
        format!(
            "cannot create log directory {}: {error}",
            options.logs_dir.display()
        )
    })?;

    let stamp = unique_stamp();
    match options.mode {
        Mode::Run => run_once_mode(&options, &stamp),
        Mode::VerifyTwice => run_twice_mode(&options, &stamp),
    }
}

fn parse_args(args: Vec<OsString>) -> Result<Options, String> {
    if args.is_empty() {
        return Err(format!("missing mode\n\n{USAGE}"));
    }
    if matches!(args[0].to_str(), Some("-h" | "--help")) {
        print!("{USAGE}");
        process::exit(0);
    }

    let mode = match args[0].to_str() {
        Some("run") => Mode::Run,
        Some("verify-twice") => Mode::VerifyTwice,
        Some(other) => return Err(format!("unknown mode {other:?}\n\n{USAGE}")),
        None => return Err("mode is not valid UTF-8".to_string()),
    };

    let mut name: Option<String> = None;
    let mut tail_lines = DEFAULT_TAIL_LINES;
    let mut grep_patterns = Vec::new();
    let mut grep_was_set = false;
    let mut grep_limit = DEFAULT_GREP_LIMIT;
    let mut logs_dir: Option<PathBuf> = None;
    let mut command = Vec::new();
    let mut index = 1;

    while index < args.len() {
        let arg = args[index].to_str();
        if arg == Some("--") {
            command.extend(args[index + 1..].iter().cloned());
            break;
        }
        match arg {
            Some("-h" | "--help") => {
                print!("{USAGE}");
                process::exit(0);
            }
            Some("--name") => {
                name = Some(take_utf8_value(&args, &mut index, "--name")?);
            }
            Some("--tail") => {
                tail_lines = parse_usize(&take_utf8_value(&args, &mut index, "--tail")?, "--tail")?;
            }
            Some("--grep") => {
                if !grep_was_set {
                    grep_patterns.clear();
                    grep_was_set = true;
                }
                grep_patterns.push(take_utf8_value(&args, &mut index, "--grep")?);
            }
            Some("--grep-limit") => {
                grep_limit = parse_usize(
                    &take_utf8_value(&args, &mut index, "--grep-limit")?,
                    "--grep-limit",
                )?;
            }
            Some("--logs-dir") => {
                logs_dir = Some(PathBuf::from(take_utf8_value(
                    &args,
                    &mut index,
                    "--logs-dir",
                )?));
            }
            Some("--no-grep") => {
                grep_patterns.clear();
                grep_was_set = true;
            }
            Some(value) => {
                return Err(format!(
                    "unknown option {value:?}; put the command after --\n\n{USAGE}"
                ));
            }
            None => return Err("option is not valid UTF-8".to_string()),
        }
        index += 1;
    }

    if command.is_empty() {
        return Err(format!("missing command after --\n\n{USAGE}"));
    }
    if !grep_was_set {
        grep_patterns = [
            "error", "failed", "failure", "panic", "success", "determin", "diverg",
        ]
        .iter()
        .map(|value| value.to_string())
        .collect();
    }

    let default_name = Path::new(&command[0])
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("command");
    let name = sanitize_name(name.as_deref().unwrap_or(default_name));
    let logs_dir = logs_dir.unwrap_or_else(default_logs_dir);

    Ok(Options {
        mode,
        name,
        tail_lines,
        grep_patterns,
        grep_limit,
        logs_dir,
        command,
    })
}

fn take_utf8_value(args: &[OsString], index: &mut usize, option: &str) -> Result<String, String> {
    *index += 1;
    args.get(*index)
        .ok_or_else(|| format!("{option} requires a value"))?
        .to_str()
        .map(|value| value.to_string())
        .ok_or_else(|| format!("{option} value is not valid UTF-8"))
}

fn parse_usize(value: &str, option: &str) -> Result<usize, String> {
    value
        .parse::<usize>()
        .map_err(|_| format!("{option} requires a non-negative integer, got {value:?}"))
}

fn sanitize_name(value: &str) -> String {
    let mut result = String::with_capacity(value.len().min(64));
    let mut previous_was_dash = false;
    for character in value.chars().take(64) {
        let mapped = if character.is_ascii_alphanumeric() || matches!(character, '-' | '_') {
            character
        } else {
            '-'
        };
        if mapped == '-' && previous_was_dash {
            continue;
        }
        previous_was_dash = mapped == '-';
        result.push(mapped);
    }
    let trimmed = result.trim_matches('-');
    if trimmed.is_empty() {
        "command".to_string()
    } else {
        trimmed.to_string()
    }
}

fn default_logs_dir() -> PathBuf {
    let mut directory = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    loop {
        if directory.join(".gitmodules").is_file()
            && directory.join("scripts").is_dir()
            && directory.join("hermit").is_dir()
            && directory.join("reverie").is_dir()
            && directory.join("liteinst2").is_dir()
        {
            return directory.join("ignored/logs");
        }
        if !directory.pop() {
            return env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .join("ignored/logs");
        }
    }
}

fn unique_stamp() -> String {
    let epoch = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    format!(
        "{}-{}-{}",
        epoch.as_secs(),
        epoch.subsec_millis(),
        process::id()
    )
}

fn log_path(options: &Options, stamp: &str, suffix: Option<&str>) -> PathBuf {
    let suffix = suffix.map(|value| format!("-{value}")).unwrap_or_default();
    options
        .logs_dir
        .join(format!("{}-{}{}.log", options.name, stamp, suffix))
}

fn run_once_mode(options: &Options, stamp: &str) -> Result<i32, String> {
    let result = run_detached(&options.command, log_path(options, stamp, None))?;
    println!("detached-verify: mode=run name={}", options.name);
    print_run_header("run", &result);
    print_log_excerpt(options, &result.log_path)?;
    Ok(exit_code(&result.status))
}

fn run_twice_mode(options: &Options, stamp: &str) -> Result<i32, String> {
    let first = run_detached(&options.command, log_path(options, stamp, Some("run1")))?;
    let second = run_detached(&options.command, log_path(options, stamp, Some("run2")))?;
    let raw_comparison = compare_files(&first.log_path, &second.log_path)
        .map_err(|error| format!("cannot compare logs: {error}"))?;
    let normalize_hermit = is_hermit_strict_verify(&options.command);
    let comparison = if normalize_hermit {
        compare_hermit_normalized(&first.log_path, &second.log_path)
            .map_err(|error| format!("cannot compare normalized Hermit logs: {error}"))?
    } else {
        raw_comparison
    };

    println!("detached-verify: mode=verify-twice name={}", options.name);
    print_run_header("run1", &first);
    print_run_header("run2", &second);
    if normalize_hermit {
        print_comparison("raw-comparison", raw_comparison);
        print_comparison("comparison", comparison);
        println!("normalization: hermit-temporary-run-log-paths-only");
    } else {
        print_comparison("comparison", comparison);
    }
    println!("excerpt-source: run1");
    print_log_excerpt(options, &first.log_path)?;
    if !matches!(comparison, Comparison::Identical) {
        println!("excerpt-source: run2");
        print_log_excerpt(options, &second.log_path)?;
    }

    let first_code = exit_code(&first.status);
    let second_code = exit_code(&second.status);
    if first_code != 0 {
        Ok(first_code)
    } else if second_code != 0 {
        Ok(second_code)
    } else if matches!(comparison, Comparison::Identical) {
        Ok(0)
    } else {
        Ok(1)
    }
}

fn print_comparison(label: &str, comparison: Comparison) {
    match comparison {
        Comparison::Identical => println!("{label}: identical"),
        Comparison::Divergent { byte_offset, line } => println!(
            "{label}: divergent first-byte={} first-line={}",
            byte_offset, line
        ),
    }
}

fn is_hermit_strict_verify(command: &[OsString]) -> bool {
    let has_hermit = command.iter().any(|argument| {
        Path::new(argument)
            .file_name()
            .and_then(|value| value.to_str())
            == Some("hermit")
    });
    let has_strict = command.iter().any(|argument| argument == "--strict");
    let has_verify = command.iter().any(|argument| argument == "--verify");
    has_hermit && has_strict && has_verify
}

fn run_detached(command: &[OsString], log_path: PathBuf) -> Result<RunResult, String> {
    let stdout = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&log_path)
        .map_err(|error| format!("cannot create {}: {error}", log_path.display()))?;
    let stderr = stdout
        .try_clone()
        .map_err(|error| format!("cannot clone log handle: {error}"))?;

    let start = Instant::now();
    let mut child = Command::new("nohup")
        .arg("setsid")
        .args(["--fork", "--wait", "--"])
        .args(command)
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr))
        .spawn()
        .map_err(|error| format!("cannot launch nohup/setsid: {error}"))?;
    let status = child
        .wait()
        .map_err(|error| format!("cannot wait for detached command: {error}"))?;
    let duration = start.elapsed();
    let bytes = fs::metadata(&log_path)
        .map(|metadata| metadata.len())
        .unwrap_or(0);

    Ok(RunResult {
        status,
        log_path,
        duration,
        bytes,
    })
}

fn exit_code(status: &ExitStatus) -> i32 {
    status
        .code()
        .or_else(|| status.signal().map(|signal| 128 + signal))
        .unwrap_or(1)
}

fn exit_description(status: &ExitStatus) -> String {
    if let Some(code) = status.code() {
        code.to_string()
    } else if let Some(signal) = status.signal() {
        format!("signal:{signal}")
    } else {
        "unknown".to_string()
    }
}

fn print_run_header(label: &str, result: &RunResult) {
    println!(
        "{label}: exit={} duration-ms={} bytes={} log={}",
        exit_description(&result.status),
        result.duration.as_millis(),
        result.bytes,
        result.log_path.display()
    );
}

fn print_log_excerpt(options: &Options, path: &Path) -> Result<(), String> {
    if !options.grep_patterns.is_empty() && options.grep_limit > 0 {
        let grep = grep_lines(path, &options.grep_patterns, options.grep_limit)
            .map_err(|error| format!("cannot grep {}: {error}", path.display()))?;
        println!(
            "grep: shown={}/{} patterns={}",
            grep.lines.len(),
            grep.total_matches,
            options.grep_patterns.join(",")
        );
        for (line_number, line) in grep.lines {
            println!("  L{line_number}: {line}");
        }
    }

    if options.tail_lines > 0 {
        let tail = tail_lines(path, options.tail_lines)
            .map_err(|error| format!("cannot tail {}: {error}", path.display()))?;
        println!("tail: shown={}", tail.len());
        for line in tail {
            println!("  {line}");
        }
    }
    Ok(())
}

fn display_line(bytes: &[u8]) -> String {
    let text = String::from_utf8_lossy(bytes);
    let trimmed = text.trim_end_matches(['\n', '\r']);
    let mut result = String::new();
    for character in trimmed.chars().take(MAX_DISPLAY_CHARS) {
        if character.is_control() && character != '\t' {
            result.push('�');
        } else {
            result.push(character);
        }
    }
    if trimmed.chars().count() > MAX_DISPLAY_CHARS {
        result.push_str("…");
    }
    result
}

fn grep_lines(path: &Path, patterns: &[String], limit: usize) -> io::Result<GrepSummary> {
    let lowered: Vec<String> = patterns.iter().map(|value| value.to_lowercase()).collect();
    let mut reader = BufReader::new(File::open(path)?);
    let mut buffer = Vec::new();
    let mut lines = Vec::new();
    let mut total_matches = 0;
    let mut line_number = 0;

    loop {
        buffer.clear();
        if reader.read_until(b'\n', &mut buffer)? == 0 {
            break;
        }
        line_number += 1;
        let text = String::from_utf8_lossy(&buffer).to_lowercase();
        if lowered.iter().any(|pattern| text.contains(pattern)) {
            total_matches += 1;
            if lines.len() < limit {
                lines.push((line_number, display_line(&buffer)));
            }
        }
    }

    Ok(GrepSummary {
        lines,
        total_matches,
    })
}

fn tail_lines(path: &Path, count: usize) -> io::Result<Vec<String>> {
    let mut reader = BufReader::new(File::open(path)?);
    let mut buffer = Vec::new();
    let mut lines = VecDeque::with_capacity(count);
    loop {
        buffer.clear();
        if reader.read_until(b'\n', &mut buffer)? == 0 {
            break;
        }
        if lines.len() == count {
            lines.pop_front();
        }
        lines.push_back(display_line(&buffer));
    }
    Ok(lines.into_iter().collect())
}

fn compare_files(first_path: &Path, second_path: &Path) -> io::Result<Comparison> {
    let mut first = File::open(first_path)?;
    let mut second = File::open(second_path)?;
    let first_len = first.metadata()?.len();
    let second_len = second.metadata()?.len();
    let common_len = first_len.min(second_len);
    let mut first_buffer = vec![0; COPY_BUFFER_BYTES];
    let mut second_buffer = vec![0; COPY_BUFFER_BYTES];
    let mut byte_offset = 0_u64;
    let mut line = 1_u64;

    while byte_offset < common_len {
        let chunk_len = (common_len - byte_offset).min(COPY_BUFFER_BYTES as u64) as usize;
        first.read_exact(&mut first_buffer[..chunk_len])?;
        second.read_exact(&mut second_buffer[..chunk_len])?;
        if first_buffer[..chunk_len] != second_buffer[..chunk_len] {
            let index = first_buffer[..chunk_len]
                .iter()
                .zip(&second_buffer[..chunk_len])
                .position(|(left, right)| left != right)
                .unwrap_or(0);
            line += first_buffer[..index]
                .iter()
                .filter(|byte| **byte == b'\n')
                .count() as u64;
            return Ok(Comparison::Divergent {
                byte_offset: byte_offset + index as u64,
                line,
            });
        }
        line += first_buffer[..chunk_len]
            .iter()
            .filter(|byte| **byte == b'\n')
            .count() as u64;
        byte_offset += chunk_len as u64;
    }
    if first_len != second_len {
        Ok(Comparison::Divergent { byte_offset, line })
    } else {
        Ok(Comparison::Identical)
    }
}

fn compare_hermit_normalized(first_path: &Path, second_path: &Path) -> io::Result<Comparison> {
    let mut first = BufReader::new(File::open(first_path)?);
    let mut second = BufReader::new(File::open(second_path)?);
    let mut first_line = Vec::new();
    let mut second_line = Vec::new();
    let mut byte_offset = 0_u64;
    let mut line = 1_u64;

    loop {
        first_line.clear();
        second_line.clear();
        let first_read = first.read_until(b'\n', &mut first_line)?;
        let second_read = second.read_until(b'\n', &mut second_line)?;
        if first_read == 0 || second_read == 0 {
            return if first_read == second_read {
                Ok(Comparison::Identical)
            } else {
                Ok(Comparison::Divergent { byte_offset, line })
            };
        }

        let first_normalized = normalize_hermit_line(&first_line);
        let second_normalized = normalize_hermit_line(&second_line);
        if first_normalized != second_normalized {
            let common = first_normalized.len().min(second_normalized.len());
            let index = first_normalized[..common]
                .iter()
                .zip(&second_normalized[..common])
                .position(|(left, right)| left != right)
                .unwrap_or(common);
            return Ok(Comparison::Divergent {
                byte_offset: byte_offset + index as u64,
                line,
            });
        }
        byte_offset += first_normalized.len() as u64;
        line += 1;
    }
}

fn normalize_hermit_line(line: &[u8]) -> Vec<u8> {
    if line.starts_with(b":: Comparing logs... /tmp/run1_log_") {
        b":: Comparing logs... <temporary run logs>\n".to_vec()
    } else {
        line.to_vec()
    }
}
