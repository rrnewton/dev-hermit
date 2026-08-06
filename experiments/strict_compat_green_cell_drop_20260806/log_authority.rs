// Shared, fail-closed consumer of Hermit's canonical log parser.
//
// The exact bound Hermit binary parses a log by comparing it with itself.
// detcore's log-diff engine always emits its total, INFO, and deterministic
// (DETLOG + scheduler COMMIT) selection counts, even though the CLI's final
// comparison defaults to the deterministic selection.  This wrapper accepts
// exactly one equal-sided count for every category and otherwise refuses the
// evidence.  Raw log bytes remain a separate, unmodified comparison authority.

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::process::Command;

pub const PARSER_ID: &str = "hermit-log-diff-canonical-counts-v1";

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub struct CanonicalLogCounts {
    pub total_messages: u64,
    pub info_messages: u64,
    pub deterministic_messages: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CanonicalLogInspection {
    pub parser_id: String,
    pub command: Vec<String>,
    pub counts: CanonicalLogCounts,
    pub diagnostic_stderr: Vec<u8>,
}

pub fn inspect_file(binary: &Path, path: &Path) -> Result<CanonicalLogInspection> {
    let raw = std::fs::read(path).with_context(|| format!("reading raw log {}", path.display()))?;
    std::str::from_utf8(&raw).context("raw log is not valid UTF-8")?;
    let command = vec![
        binary.display().to_string(),
        "log-diff".to_owned(),
        path.display().to_string(),
        path.display().to_string(),
        "--no-color".to_owned(),
        "--limit=1".to_owned(),
    ];
    let output = Command::new(&command[0])
        .args(&command[1..])
        .output()
        .with_context(|| format!("running canonical Hermit log parser for {}", path.display()))?;
    if !output.status.success() {
        bail!(
            "canonical Hermit log parser refused {}: status={} stderr={}",
            path.display(),
            output.status,
            String::from_utf8_lossy(&output.stderr)
        );
    }
    if !output.stdout.is_empty() {
        bail!("canonical Hermit log parser unexpectedly wrote stdout");
    }
    let stderr = std::str::from_utf8(&output.stderr)
        .context("canonical Hermit log parser diagnostic is not UTF-8")?;
    let counts = CanonicalLogCounts {
        total_messages: parse_equal_count(stderr, "Logs contain ", " messages total")?,
        info_messages: parse_equal_count(stderr, "Logs contain ", " INFO messages")?,
        deterministic_messages: parse_equal_count(
            stderr,
            "Logs contain ",
            " DETLOG & scheduler COMMIT messages",
        )?,
    };
    Ok(CanonicalLogInspection {
        parser_id: PARSER_ID.to_owned(),
        command,
        counts,
        diagnostic_stderr: output.stderr,
    })
}

fn parse_equal_count(text: &str, prefix: &str, suffix: &str) -> Result<u64> {
    let matches: Vec<_> = text
        .lines()
        .filter_map(|line| {
            let body = line.strip_prefix(prefix)?.strip_suffix(suffix)?;
            let (left, right) = body.split_once(" | ")?;
            Some((left, right, line))
        })
        .collect();
    if matches.len() != 1 {
        bail!(
            "canonical parser diagnostic must contain exactly one {suffix:?} count; found {}",
            matches.len()
        );
    }
    let (left, right, line) = matches[0];
    let left: u64 = left
        .parse()
        .with_context(|| format!("invalid left count in {line:?}"))?;
    let right: u64 = right
        .parse()
        .with_context(|| format!("invalid right count in {line:?}"))?;
    if left != right {
        bail!("self-comparison parser counts disagree in {line:?}");
    }
    Ok(left)
}

pub fn self_test(binary: &Path, root: &Path) -> Result<()> {
    std::fs::create_dir_all(root)?;
    let valid = root.join("valid.log");
    std::fs::write(
        &valid,
        b"2026-08-06T10:00:00.000000Z  INFO detcore: DETLOG first\ncontinued\n\
Aug 06 10:00:01.000001  INFO detcore: scheduler COMMIT turn\n",
    )?;
    let counts = inspect_file(binary, &valid)?.counts;
    if counts.total_messages != 2 || counts.info_messages != 2 || counts.deterministic_messages != 2
    {
        bail!("canonical positive bracket returned {counts:?}");
    }

    let non_info = root.join("non-info.log");
    std::fs::write(
        &non_info,
        b"2026-08-06T10:00:00.000000Z  WARN detcore: DETLOG warning\n\
2026-08-06T10:00:01.000000Z  ERROR detcore: COMMIT error\n",
    )?;
    let counts = inspect_file(binary, &non_info)?.counts;
    if counts.total_messages != 2 || counts.info_messages != 0 {
        bail!("WARN/ERROR false-positive bracket returned {counts:?}");
    }

    for (name, invalid) in [
        (
            "timestamp-junk.log",
            b"2026-08-06T10:00:00.000000Z timestamp-shaped junk\n".as_slice(),
        ),
        ("invalid-utf8.log", &[0xff, 0xfe][..]),
    ] {
        let path = root.join(name);
        std::fs::write(&path, invalid)?;
        if inspect_file(binary, &path).is_ok() {
            bail!("malformed log bracket was accepted: {}", path.display());
        }
    }
    Ok(())
}
