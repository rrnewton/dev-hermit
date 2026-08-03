#!/usr/bin/env rust-script
//! Verify that agent-utils is cleanly pegged to its fetched origin/main commit.
//!
//! The check compares the parent HEAD gitlink, the initialized checkout, and
//! `origin/main`, then finds commits unreachable from every fetched origin ref.
//! By default it fetches and prunes origin through `with-proxy` before deciding.

use std::env;
use std::ffi::{OsStr, OsString};
use std::fmt;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, ExitStatus};

#[derive(Debug)]
struct Options {
    root: Option<PathBuf>,
    fetch: bool,
}

#[derive(Debug)]
enum CheckError {
    Usage(String),
    Io { tool: String, source: io::Error },
    Command { command: String, detail: String },
    InvalidOutput(String),
}

impl fmt::Display for CheckError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Usage(message) | Self::InvalidOutput(message) => formatter.write_str(message),
            Self::Io { tool, source } => write!(formatter, "could not launch {tool}: {source}"),
            Self::Command { command, detail } => write!(formatter, "{command} failed: {detail}"),
        }
    }
}

fn main() -> ExitCode {
    let raw: Vec<OsString> = env::args_os().collect();
    let options = match parse_args(&raw[1..]) {
        Ok(Some(options)) => options,
        Ok(None) => {
            print_help();
            return ExitCode::SUCCESS;
        }
        Err(error) => {
            eprintln!("check-agent-utils-pin: {error}");
            eprintln!("Try --help for usage.");
            return ExitCode::from(2);
        }
    };
    let root = match options.root.clone().map(Ok).unwrap_or_else(workspace_root) {
        Ok(root) => root,
        Err(error) => {
            eprintln!("check-agent-utils-pin: {error}");
            return ExitCode::from(2);
        }
    };

    if options.fetch && env::var_os("CI_HUB_TOOL_COST_ACTIVE").is_none() {
        match run_costed(&root, &raw[1..]) {
            Ok(status) => return exit_code(status),
            Err(error) => {
                eprintln!("check-agent-utils-pin: {error}");
                return ExitCode::from(2);
            }
        }
    }

    match check(&root, options.fetch) {
        Ok(true) => ExitCode::SUCCESS,
        Ok(false) => ExitCode::from(1),
        Err(error) => {
            eprintln!("check-agent-utils-pin: {error}");
            ExitCode::from(2)
        }
    }
}

fn parse_args(args: &[OsString]) -> Result<Option<Options>, CheckError> {
    let mut root = None;
    let mut fetch = true;
    let mut index = 0;
    while index < args.len() {
        match args[index].to_str() {
            Some("-h" | "--help") => return Ok(None),
            Some("--no-fetch") => {
                fetch = false;
                index += 1;
            }
            Some("--root") => {
                let value = args.get(index + 1).ok_or_else(|| {
                    CheckError::Usage("--root requires a workspace path".to_string())
                })?;
                root = Some(PathBuf::from(value));
                index += 2;
            }
            Some(argument) => {
                return Err(CheckError::Usage(format!("unknown argument {argument:?}")));
            }
            None => {
                return Err(CheckError::Usage(
                    "arguments must be valid UTF-8".to_string(),
                ));
            }
        }
    }
    Ok(Some(Options { root, fetch }))
}

fn print_help() {
    println!(
        "Usage: scripts/check-agent-utils-pin.rs [--no-fetch] [--root PATH]\n\
         \n\
         Fetch agent-utils origin, then require:\n\
           * parent HEAD gitlink == checkout HEAD == origin/main\n\
           * checkout branch is main\n\
           * no local commit is unreachable from every origin/* ref\n\
         \n\
         --no-fetch  compare existing refs only (offline/debug use)\n\
         --root PATH override the dev-hermit parent path"
    );
}

fn workspace_root() -> Result<PathBuf, CheckError> {
    let mut command = Command::new("git");
    if let Some(script) = env::var_os("RUST_SCRIPT_PATH").filter(|path| !path.is_empty()) {
        let script = PathBuf::from(script);
        if let Some(parent) = script.parent() {
            command.arg("-C").arg(parent);
        }
    }
    let output = run(command.args(["rev-parse", "--show-toplevel"]))?;
    Ok(PathBuf::from(single_line("git workspace root", &output)?))
}

fn run_costed(root: &Path, original_args: &[OsString]) -> Result<ExitStatus, CheckError> {
    let executable = env::current_exe().map_err(|source| CheckError::Io {
        tool: "current executable".into(),
        source,
    })?;
    let mut command = Command::new(root.join("ci-hub/bin/tool-cost"));
    command
        .env("CI_HUB_TOOL_COST_ACTIVE", "1")
        .arg("--tool")
        .arg("check-agent-utils-pin")
        .arg("--estimate-unknown")
        .arg("--basis")
        .arg("not measured: one agent-utils fetch/prune plus local ancestry checks")
        .arg("--")
        .arg(executable)
        .arg("--root")
        .arg(root)
        .args(original_args);
    command.status().map_err(|source| CheckError::Io {
        tool: "ci-hub/bin/tool-cost".into(),
        source,
    })
}

fn check(root: &Path, fetch: bool) -> Result<bool, CheckError> {
    let checkout = root.join("agent-utils");
    if !checkout.join(".git").exists() {
        return Err(CheckError::InvalidOutput(format!(
            "{} is not initialized",
            checkout.display()
        )));
    }

    if fetch {
        let mut fetch_command = Command::new("with-proxy");
        fetch_command
            .args(["git", "-C"])
            .arg(&checkout)
            .args(["fetch", "--prune", "origin"]);
        run(&mut fetch_command)?;
    }

    let parent_pin = git(root, ["rev-parse", "HEAD:agent-utils"])?;
    let checkout_head = git(&checkout, ["rev-parse", "HEAD"])?;
    let origin_main = git(&checkout, ["rev-parse", "origin/main"])?;
    let branch = git(&checkout, ["branch", "--show-current"])?;
    let (checkout_ahead, checkout_behind) = divergence(&checkout, "HEAD", "origin/main")?;
    let (pin_ahead, pin_behind) = divergence(&checkout, &parent_pin, "origin/main")?;
    let unpushed = git_lines(
        &checkout,
        [
            "rev-list",
            "HEAD",
            "--branches",
            "--not",
            "--remotes=origin",
        ],
    )?;
    let dirty = !git(
        &checkout,
        ["status", "--porcelain=v1", "--untracked-files=all"],
    )?
    .is_empty();

    println!("agent-utils drift check");
    println!("  parent_gitlink={parent_pin}");
    println!("  checkout_head={checkout_head}");
    println!("  origin_main={origin_main}");
    println!("  checkout_branch={branch}");
    println!("  checkout_ahead={checkout_ahead}");
    println!("  checkout_behind={checkout_behind}");
    println!("  pin_ahead={pin_ahead}");
    println!("  pin_behind={pin_behind}");
    println!("  local_unpushed_commits={}", unpushed.len());

    let mut failures = Vec::new();
    if branch != "main" {
        failures.push(format!("checkout branch is {branch:?}, expected main"));
    }
    if checkout_head != origin_main || checkout_ahead != 0 || checkout_behind != 0 {
        failures.push(format!(
            "checkout is not exactly origin/main (ahead={checkout_ahead}, behind={checkout_behind})"
        ));
    }
    if parent_pin != checkout_head {
        failures.push(format!(
            "parent gitlink {parent_pin} does not match checkout {checkout_head}"
        ));
    }
    if parent_pin != origin_main || pin_ahead != 0 || pin_behind != 0 {
        failures.push(format!(
            "parent gitlink is not exactly origin/main (ahead={pin_ahead}, behind={pin_behind})"
        ));
    }
    if !unpushed.is_empty() {
        failures.push(format!(
            "{} local commit(s) are unreachable from every origin/* ref: {}",
            unpushed.len(),
            unpushed
                .iter()
                .take(20)
                .cloned()
                .collect::<Vec<_>>()
                .join(",")
        ));
    }

    if dirty {
        println!(
            "  warning=agent-utils worktree has uncommitted changes; active work must be committed and pushed through a feature branch"
        );
    }
    if failures.is_empty() {
        println!("state=ok");
        return Ok(true);
    }
    for failure in failures {
        eprintln!("ERROR: {failure}");
    }
    println!("state=drift");
    Ok(false)
}

fn divergence(repo: &Path, left: &str, right: &str) -> Result<(u64, u64), CheckError> {
    let range = format!("{left}...{right}");
    let output = git(repo, ["rev-list", "--left-right", "--count", &range])?;
    parse_divergence(&output)
}

fn parse_divergence(output: &str) -> Result<(u64, u64), CheckError> {
    let values: Vec<_> = output.split_whitespace().collect();
    if values.len() != 2 {
        return Err(CheckError::InvalidOutput(format!(
            "expected two divergence counts, got {output:?}"
        )));
    }
    let ahead = values[0].parse().map_err(|error| {
        CheckError::InvalidOutput(format!("invalid ahead count {:?}: {error}", values[0]))
    })?;
    let behind = values[1].parse().map_err(|error| {
        CheckError::InvalidOutput(format!("invalid behind count {:?}: {error}", values[1]))
    })?;
    Ok((ahead, behind))
}

fn git<I, S>(repo: &Path, args: I) -> Result<String, CheckError>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let mut command = Command::new("git");
    command.arg("-C").arg(repo).args(args);
    let output = run(&mut command)?;
    Ok(output.trim_end().to_string())
}

fn git_lines<I, S>(repo: &Path, args: I) -> Result<Vec<String>, CheckError>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    Ok(git(repo, args)?
        .lines()
        .filter(|line| !line.is_empty())
        .map(str::to_string)
        .collect())
}

fn run(command: &mut Command) -> Result<String, CheckError> {
    let display = format!("{command:?}");
    let output = command.output().map_err(|source| CheckError::Io {
        tool: display.clone(),
        source,
    })?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(CheckError::Command {
            command: display,
            detail: if detail.is_empty() {
                format!("exit {}", exit_number(output.status))
            } else {
                detail
            },
        });
    }
    String::from_utf8(output.stdout)
        .map_err(|error| CheckError::InvalidOutput(format!("non-UTF-8 output: {error}")))
}

fn single_line<'a>(name: &str, output: &'a str) -> Result<&'a str, CheckError> {
    let mut lines = output.lines();
    let value = lines
        .next()
        .filter(|line| !line.is_empty())
        .ok_or_else(|| CheckError::InvalidOutput(format!("{name} was empty")))?;
    if lines.next().is_some() {
        return Err(CheckError::InvalidOutput(format!(
            "{name} returned multiple lines"
        )));
    }
    Ok(value)
}

fn exit_number(status: ExitStatus) -> i32 {
    status.code().unwrap_or(1)
}

fn exit_code(status: ExitStatus) -> ExitCode {
    ExitCode::from(u8::try_from(exit_number(status).clamp(0, 255)).unwrap_or(1))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_git_divergence_counts() {
        assert_eq!(parse_divergence("3\t7\n").unwrap(), (3, 7));
        assert!(parse_divergence("3").is_err());
    }

    #[test]
    fn help_is_trivial_and_does_not_arm_work() {
        assert!(parse_args(&[OsString::from("--help")]).unwrap().is_none());
    }

    #[test]
    fn no_fetch_is_explicit() {
        let options = parse_args(&[OsString::from("--no-fetch")])
            .unwrap()
            .unwrap();
        assert!(!options.fetch);
    }
}
