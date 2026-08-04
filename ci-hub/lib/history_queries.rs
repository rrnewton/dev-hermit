//! Shared local-validate history query engine.
//!
//! Both owner-facing directions use this index: `newest-green` scans a branch
//! newest-to-oldest for the latest passing evidence, while `first-bad` scans
//! recorded observations for the newest PASS -> FAIL transition. Keeping the
//! commit ordering and evidence rules here prevents the two answers drifting.

use crate::records::{GateHistoryRow, HistoryRow};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

pub const NEWEST_GREEN_CACHE_REL: &str = "ignored/ci-hub/newest-green-cache.json";
pub const CELL_EVIDENCE_CACHE_REL: &str = "ignored/ci-hub/local-cell-evidence-cache.json";

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum CoverageStrength {
    Full,
    SmartSelection,
    NarrowerProfile,
}

impl CoverageStrength {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Full => "full",
            Self::SmartSelection => "smart-selection",
            Self::NarrowerProfile => "narrower-profile",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct ValidationEvidence {
    pub sha: String,
    pub finished_at: Option<String>,
    pub profile: String,
    pub selection_mode: String,
    pub coverage: CoverageStrength,
    pub result: String,
    pub log_file: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct NewestGreenReport {
    pub schema_version: u32,
    pub branch: String,
    pub branch_ref: String,
    pub branch_tip: String,
    pub green: ValidationEvidence,
    pub commits_after_green: usize,
    pub commits_without_any_record: usize,
    pub commits_with_records: usize,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct FirstBadReport {
    pub schema_version: u32,
    pub branch: String,
    pub branch_ref: String,
    pub query: String,
    pub matched_name: String,
    pub first_bad: ValidationEvidence,
    pub last_good: ValidationEvidence,
    pub commits_between: usize,
    pub commits_without_cell_record: usize,
    pub first_bad_commit_has_mixed_outcomes: bool,
    pub files_touched: Vec<String>,
    pub plausibility: String,
    pub source_node: Option<String>,
    pub error_excerpt: Vec<String>,
    pub load_context: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct NewestGreenCache {
    pub schema_version: u32,
    pub branch: String,
    pub branch_ref: String,
    pub branch_tip: String,
    pub ledger_path: String,
    pub ledger_len: u64,
    pub ledger_modified_ns: u128,
    pub report: NewestGreenReport,
}

/// Durable derived detail for a row in the append-only validation ledger. This
/// is not a parallel verdict store: a cached record is used only when its
/// `(commit, finished_at)` ledger row still exists.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RetainedCellEvidence {
    pub commit: String,
    pub finished_at: Option<String>,
    pub gates: Vec<GateHistoryRow>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct CellEvidenceCache {
    pub schema_version: u32,
    pub records: Vec<RetainedCellEvidence>,
}

#[derive(Clone, Debug)]
pub enum NewestGreenOutcome {
    Found(Box<NewestGreenReport>),
    FailedOnly { branch_tip: String, recorded: usize },
    NoEvidence { branch_tip: String },
}

#[derive(Clone, Debug)]
pub enum FirstBadOutcome {
    Found(Box<FirstBadReport>),
    FailureWithoutKnownGood {
        query: String,
        matched_name: String,
        failure: ValidationEvidence,
    },
    NoEvidence {
        query: String,
        available_names: Vec<String>,
    },
    NoTransition {
        query: String,
        matched_name: String,
        observations: usize,
    },
}

pub struct HistoryQueryEngine {
    commits: Vec<String>,
    rows_by_commit: BTreeMap<String, Vec<HistoryRow>>,
}

impl HistoryQueryEngine {
    /// `commits` are first-parent branch commits in newest-to-oldest order.
    pub fn new(commits: Vec<String>, rows: Vec<HistoryRow>) -> Self {
        let main: BTreeSet<&str> = commits.iter().map(String::as_str).collect();
        let mut rows_by_commit: BTreeMap<String, Vec<HistoryRow>> = BTreeMap::new();
        for row in rows {
            let Some(commit) = row.commit.as_deref() else {
                continue;
            };
            if main.contains(commit) {
                rows_by_commit
                    .entry(commit.to_string())
                    .or_default()
                    .push(row);
            }
        }
        Self {
            commits,
            rows_by_commit,
        }
    }

    pub fn newest_green(&self, branch: &str, branch_ref: &str) -> NewestGreenOutcome {
        let branch_tip = self.commits.first().cloned().unwrap_or_default();
        let mut recorded = 0usize;
        for (index, sha) in self.commits.iter().enumerate() {
            let Some(rows) = self.rows_by_commit.get(sha) else {
                continue;
            };
            let Some(row) = latest_trustworthy_row(rows) else {
                continue;
            };
            recorded += 1;
            if row.result.as_deref() != Some("pass") {
                continue;
            }
            let newer = &self.commits[..index];
            let missing = newer
                .iter()
                .filter(|commit| !self.rows_by_commit.contains_key(*commit))
                .count();
            let report = NewestGreenReport {
                schema_version: 1,
                branch: branch.to_string(),
                branch_ref: branch_ref.to_string(),
                branch_tip,
                green: evidence(sha, row),
                commits_after_green: newer.len(),
                commits_without_any_record: missing,
                commits_with_records: newer.len() - missing,
            };
            return NewestGreenOutcome::Found(Box::new(report));
        }
        if recorded > 0 {
            NewestGreenOutcome::FailedOnly {
                branch_tip,
                recorded,
            }
        } else {
            NewestGreenOutcome::NoEvidence { branch_tip }
        }
    }

    pub fn first_bad(&self, query: &str, branch: &str, branch_ref: &str) -> FirstBadOutcome {
        let normalized = normalize(query);
        let mut available = BTreeSet::new();
        let mut observations: Vec<(usize, String, GateHistoryRow, HistoryRow)> = Vec::new();

        for (index, sha) in self.commits.iter().enumerate() {
            let Some(rows) = self.rows_by_commit.get(sha) else {
                continue;
            };
            for row in rows
                .iter()
                .filter(|row| row.commit_anchored == Some(true) && row.tree_dirty == Some(false))
            {
                for gate in &row.gates {
                    available.insert(gate.name.clone());
                }
                let Some(gate) = row
                    .gates
                    .iter()
                    .find(|gate| normalize(&gate.name) == normalized)
                else {
                    continue;
                };
                observations.push((index, sha.clone(), gate.clone(), row.clone()));
            }
        }

        if observations.is_empty() {
            let suggestions = available
                .into_iter()
                .filter(|name| {
                    let candidate = normalize(name);
                    candidate.contains(&normalized) || normalized.contains(&candidate)
                })
                .take(12)
                .collect();
            return FirstBadOutcome::NoEvidence {
                query: query.to_string(),
                available_names: suggestions,
            };
        }

        // Commit chronology first, then run chronology within one commit. A
        // later PASS must not erase an earlier FAIL at the same SHA: that is
        // positive flake evidence, not a reason to rewrite history as green.
        observations.sort_by(|a, b| {
            b.0.cmp(&a.0).then_with(|| {
                a.3.finished_at
                    .as_deref()
                    .unwrap_or("")
                    .cmp(b.3.finished_at.as_deref().unwrap_or(""))
            })
        });
        let matched_name = observations[0].2.name.clone();
        let mut latest_transition = None;
        let mut open_failure_epoch = None;
        let mut last_good = None;
        for observation in &observations {
            if gate_passed(&observation.2) {
                if let Some(epoch) = open_failure_epoch.take() {
                    latest_transition = Some(epoch);
                }
                last_good = Some(observation.clone());
            } else if gate_failed(&observation.2) && open_failure_epoch.is_none() {
                if let Some(good) = &last_good {
                    open_failure_epoch = Some((good.clone(), observation.clone()));
                }
            }
        }
        let transition = open_failure_epoch.or(latest_transition);
        if let Some((good, bad)) = transition {
            let lower = bad.0.min(good.0);
            let upper = bad.0.max(good.0);
            let between = upper.saturating_sub(lower + 1);
            let observed_indices: BTreeSet<usize> =
                observations.iter().map(|item| item.0).collect();
            let missing = (lower + 1..upper)
                .filter(|index| !observed_indices.contains(index))
                .count();
            let bad_results: BTreeSet<&str> = observations
                .iter()
                .filter(|item| item.0 == bad.0)
                .filter_map(|item| item.2.result.as_deref().or(item.2.kind.as_deref()))
                .collect();
            return FirstBadOutcome::Found(Box::new(FirstBadReport {
                schema_version: 1,
                branch: branch.to_string(),
                branch_ref: branch_ref.to_string(),
                query: query.to_string(),
                matched_name: bad.2.name.clone(),
                first_bad: gate_evidence(&bad.1, &bad.3, &bad.2),
                last_good: gate_evidence(&good.1, &good.3, &good.2),
                commits_between: between,
                commits_without_cell_record: missing,
                first_bad_commit_has_mixed_outcomes: bad_results.contains("pass")
                    && (bad_results.contains("fail") || bad_results.contains("timeout")),
                files_touched: Vec::new(),
                plausibility: "not-assessed".into(),
                source_node: bad.2.source_node.clone(),
                error_excerpt: error_excerpt(&bad.3, &bad.2.name),
                load_context: load_context(&bad.3),
            }));
        }

        if let Some((_, sha, gate, row)) =
            observations.iter().rev().find(|item| gate_failed(&item.2))
        {
            return FirstBadOutcome::FailureWithoutKnownGood {
                query: query.to_string(),
                matched_name,
                failure: gate_evidence(sha, row, gate),
            };
        }
        FirstBadOutcome::NoTransition {
            query: query.to_string(),
            matched_name,
            observations: observations.len(),
        }
    }
}

fn latest_trustworthy_row(rows: &[HistoryRow]) -> Option<&HistoryRow> {
    rows.iter()
        .filter(|row| row.commit_anchored == Some(true) && row.tree_dirty == Some(false))
        .max_by_key(|row| row.finished_at.as_deref().unwrap_or(""))
}

fn coverage(row: &HistoryRow) -> CoverageStrength {
    match (row.profile.as_deref(), row.selection_mode.as_deref()) {
        (Some("full"), Some("full")) => CoverageStrength::Full,
        (Some("full"), _) => CoverageStrength::SmartSelection,
        _ => CoverageStrength::NarrowerProfile,
    }
}

fn evidence(sha: &str, row: &HistoryRow) -> ValidationEvidence {
    ValidationEvidence {
        sha: sha.to_string(),
        finished_at: row.finished_at.clone(),
        profile: row.profile.clone().unwrap_or_else(|| "unknown".into()),
        selection_mode: row
            .selection_mode
            .clone()
            .unwrap_or_else(|| "unknown".into()),
        coverage: coverage(row),
        result: row.result.clone().unwrap_or_else(|| "unknown".into()),
        log_file: row.log_file.clone(),
    }
}

fn gate_evidence(sha: &str, row: &HistoryRow, gate: &GateHistoryRow) -> ValidationEvidence {
    let mut value = evidence(sha, row);
    value.result = gate
        .result
        .clone()
        .or_else(|| gate.kind.clone())
        .unwrap_or_else(|| "unknown".into());
    value
}

fn gate_passed(gate: &GateHistoryRow) -> bool {
    gate.result.as_deref() == Some("pass") || gate.kind.as_deref() == Some("pass")
}

fn gate_failed(gate: &GateHistoryRow) -> bool {
    matches!(gate.result.as_deref(), Some("fail") | Some("failed"))
        || matches!(gate.kind.as_deref(), Some("fail") | Some("timeout"))
}

fn normalize(value: &str) -> String {
    value
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect()
}

/// Add DAG-node and Rust-test observations from the retained log named by the
/// ledger. The ledger remains the index/source of truth; an absent log simply
/// means cell detail was not retained and is reported as such.
pub fn enrich_rows_from_logs(rows: &mut [HistoryRow]) {
    for row in rows {
        let Some(path) = row.log_file.as_deref() else {
            continue;
        };
        let Ok(raw) = std::fs::read_to_string(path) else {
            continue;
        };
        let mut seen: BTreeSet<(String, String)> = row
            .gates
            .iter()
            .map(|gate| {
                (
                    normalize(&gate.name),
                    gate.result.clone().unwrap_or_default(),
                )
            })
            .collect();
        for mut gate in parse_log_observations(&raw) {
            if gate_failed(&gate) {
                let excerpt = scoped_error_excerpt(&raw, &gate);
                if !excerpt.is_empty() {
                    gate.extra
                        .insert("error_excerpt".into(), serde_json::json!(excerpt));
                }
            }
            let key = (
                normalize(&gate.name),
                gate.result.clone().unwrap_or_default(),
            );
            if seen.insert(key) {
                row.gates.push(gate);
            } else if let Some(existing) = row.gates.iter_mut().find(|existing| {
                normalize(&existing.name) == normalize(&gate.name) && existing.result == gate.result
            }) {
                existing.extra.extend(gate.extra);
            }
        }
    }
}

pub fn merge_retained_cell_evidence(rows: &mut [HistoryRow], cache: &CellEvidenceCache) {
    let retained: BTreeMap<(&str, Option<&str>), &RetainedCellEvidence> = cache
        .records
        .iter()
        .map(|record| {
            (
                (record.commit.as_str(), record.finished_at.as_deref()),
                record,
            )
        })
        .collect();
    for row in rows {
        let Some(commit) = row.commit.as_deref() else {
            continue;
        };
        let Some(record) = retained.get(&(commit, row.finished_at.as_deref())) else {
            continue;
        };
        let mut names: BTreeSet<(String, String)> = row
            .gates
            .iter()
            .map(|gate| {
                (
                    normalize(&gate.name),
                    gate.result.clone().unwrap_or_default(),
                )
            })
            .collect();
        for gate in &record.gates {
            let key = (
                normalize(&gate.name),
                gate.result.clone().unwrap_or_default(),
            );
            if names.insert(key) {
                row.gates.push(gate.clone());
            }
        }
    }
}

pub fn retained_cell_evidence(rows: &[HistoryRow]) -> CellEvidenceCache {
    let records = rows
        .iter()
        .filter_map(|row| {
            let gates: Vec<GateHistoryRow> = row
                .gates
                .iter()
                .filter(|gate| gate.source_node.is_some())
                .cloned()
                .collect();
            Some(RetainedCellEvidence {
                commit: row.commit.clone()?,
                finished_at: row.finished_at.clone(),
                gates,
            })
        })
        .collect();
    CellEvidenceCache {
        schema_version: 2,
        records,
    }
}

pub fn parse_log_observations(raw: &str) -> Vec<GateHistoryRow> {
    let mut observations = Vec::new();
    for raw_line in raw.lines() {
        let line = strip_ansi(raw_line);
        let source_node = line
            .strip_prefix('[')
            .and_then(|rest| rest.split_once(']'))
            .map(|(node, _)| node.to_string());
        if let Some(node) = source_node.as_deref() {
            let result = if line.contains("\u{2713} PASS") {
                Some("pass")
            } else if line.contains("\u{2717} FAIL") {
                Some("fail")
            } else {
                None
            };
            if let Some(result) = result {
                observations.push(GateHistoryRow {
                    name: node.to_string(),
                    result: Some(result.into()),
                    kind: None,
                    exit_code: None,
                    real_seconds: None,
                    source_node: Some(node.to_string()),
                    extra: BTreeMap::new(),
                });
            }
        }
        let Some(test_at) = line.find("test ") else {
            continue;
        };
        let test = &line[test_at + 5..];
        let Some((name, suffix)) = test.split_once(" ... ") else {
            continue;
        };
        if name == "result:" || name.is_empty() {
            continue;
        }
        let result = if suffix.starts_with("ok") {
            "pass"
        } else if suffix.starts_with("FAILED") {
            "fail"
        } else {
            continue;
        };
        observations.push(GateHistoryRow {
            name: name.to_string(),
            result: Some(result.into()),
            kind: None,
            exit_code: None,
            real_seconds: None,
            source_node,
            extra: BTreeMap::new(),
        });
    }
    observations
}

fn strip_ansi(line: &str) -> String {
    let mut out = String::with_capacity(line.len());
    let mut chars = line.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch == '\u{1b}' && chars.peek() == Some(&'[') {
            chars.next();
            for code in chars.by_ref() {
                if ('@'..='~').contains(&code) {
                    break;
                }
            }
        } else {
            out.push(ch);
        }
    }
    out
}

fn error_excerpt(row: &HistoryRow, query: &str) -> Vec<String> {
    if let Some(lines) = row
        .gates
        .iter()
        .find(|gate| normalize(&gate.name) == normalize(query))
        .and_then(|gate| gate.extra.get("error_excerpt"))
        .and_then(|value| value.as_array())
    {
        return lines
            .iter()
            .filter_map(|line| line.as_str().map(str::to_string))
            .collect();
    }
    let Some(path) = row.log_file.as_deref() else {
        return Vec::new();
    };
    let Ok(raw) = std::fs::read_to_string(path) else {
        return Vec::new();
    };
    let gate = row
        .gates
        .iter()
        .find(|gate| normalize(&gate.name) == normalize(query))
        .cloned()
        .unwrap_or(GateHistoryRow {
            name: query.to_string(),
            result: Some("fail".into()),
            kind: None,
            exit_code: None,
            real_seconds: None,
            source_node: None,
            extra: BTreeMap::new(),
        });
    scoped_error_excerpt(&raw, &gate)
}

fn scoped_error_excerpt(raw: &str, gate: &GateHistoryRow) -> Vec<String> {
    let source_prefix = gate.source_node.as_deref().map(|node| format!("[{node}]"));
    raw.lines()
        .map(strip_ansi)
        .filter(|line| {
            let belongs = line.contains(&gate.name)
                || source_prefix
                    .as_deref()
                    .map(|prefix| line.starts_with(prefix))
                    .unwrap_or(false);
            let diagnostic = line.contains(&gate.name)
                || line.contains("panicked at")
                || line.contains("error:")
                || line.contains("\u{2717} FAIL");
            belongs && diagnostic
        })
        .map(|line| line.chars().take(300).collect())
        .take(20)
        .collect()
}

fn load_context(row: &HistoryRow) -> Option<String> {
    row.extra
        .get("load_context")
        .map(|value| value.to_string())
        .or_else(|| {
            let cpu = row.user_seconds? + row.sys_seconds?;
            let wall = row.real_seconds?;
            (wall > 0.0).then(|| {
                format!(
                    "run CPU/wall={:.1}; host-load-at-run-time not retained",
                    cpu / wall
                )
            })
        })
}

pub fn cache_matches(
    cache: &NewestGreenCache,
    branch: &str,
    branch_ref: &str,
    branch_tip: &str,
    ledger_path: &Path,
    ledger_len: u64,
    ledger_modified_ns: u128,
) -> bool {
    cache.schema_version == 2
        && cache.branch == branch
        && cache.branch_ref == branch_ref
        && cache.branch_tip == branch_tip
        && cache.ledger_path == ledger_path.display().to_string()
        && cache.ledger_len == ledger_len
        && cache.ledger_modified_ns == ledger_modified_ns
}

pub fn cache_path(root: &Path, override_path: &Option<PathBuf>) -> PathBuf {
    override_path
        .clone()
        .unwrap_or_else(|| root.join(NEWEST_GREEN_CACHE_REL))
}

pub fn cell_evidence_cache_path(root: &Path) -> PathBuf {
    root.join(CELL_EVIDENCE_CACHE_REL)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(sha: &str, at: &str, profile: &str, selection: &str, result: &str) -> HistoryRow {
        serde_json::from_value(serde_json::json!({
            "schema_version": 3,
            "finished_at": at,
            "profile": profile,
            "selection_mode": selection,
            "commit": sha,
            "commit_anchored": true,
            "tree_dirty": false,
            "result": result,
            "gates": []
        }))
        .unwrap()
    }

    fn gate(mut row: HistoryRow, name: &str, result: &str) -> HistoryRow {
        row.gates.push(GateHistoryRow {
            name: name.into(),
            result: Some(result.into()),
            kind: None,
            exit_code: None,
            real_seconds: None,
            source_node: None,
            extra: BTreeMap::new(),
        });
        row
    }

    #[test]
    fn newest_green_uses_latest_state_and_reports_gaps_and_profile() {
        let commits = vec!["tip".into(), "gap".into(), "green".into()];
        let rows = vec![
            row("tip", "2026-08-03T03:00:00Z", "full", "full", "fail"),
            row("green", "2026-08-03T01:00:00Z", "full", "selective", "pass"),
        ];
        let NewestGreenOutcome::Found(report) =
            HistoryQueryEngine::new(commits, rows).newest_green("main", "origin/main")
        else {
            panic!("expected green")
        };
        assert_eq!(report.green.sha, "green");
        assert_eq!(report.green.coverage, CoverageStrength::SmartSelection);
        assert_eq!(report.commits_after_green, 2);
        assert_eq!(report.commits_without_any_record, 1);
    }

    #[test]
    fn first_bad_finds_newest_transition_without_treating_gap_as_pass() {
        let commits = vec!["bad2".into(), "bad1".into(), "gap".into(), "good".into()];
        let rows = vec![
            gate(
                row("bad2", "2026-08-03T04:00:00Z", "full", "full", "fail"),
                "cell",
                "fail",
            ),
            gate(
                row("bad1", "2026-08-03T03:00:00Z", "full", "full", "fail"),
                "cell",
                "fail",
            ),
            gate(
                row("good", "2026-08-03T01:00:00Z", "full", "full", "pass"),
                "cell",
                "pass",
            ),
        ];
        let FirstBadOutcome::Found(report) =
            HistoryQueryEngine::new(commits, rows).first_bad("cell", "main", "origin/main")
        else {
            panic!("expected transition")
        };
        assert_eq!(report.first_bad.sha, "bad1");
        assert_eq!(report.last_good.sha, "good");
        assert_eq!(report.commits_without_cell_record, 1);
    }

    #[test]
    fn later_pass_on_same_sha_is_reported_as_mixed_not_erased() {
        let commits = vec!["tip".into(), "good".into()];
        let rows = vec![
            gate(
                row("good", "2026-08-03T01:00:00Z", "full", "full", "pass"),
                "cell",
                "pass",
            ),
            gate(
                row("tip", "2026-08-03T02:00:00Z", "full", "full", "fail"),
                "cell",
                "fail",
            ),
            gate(
                row("tip", "2026-08-03T03:00:00Z", "full", "full", "pass"),
                "cell",
                "pass",
            ),
        ];
        let FirstBadOutcome::Found(report) =
            HistoryQueryEngine::new(commits, rows).first_bad("cell", "main", "origin/main")
        else {
            panic!("expected retained historical transition")
        };
        assert_eq!(report.first_bad.sha, "tip");
        assert_eq!(report.last_good.sha, "good");
        assert!(report.first_bad_commit_has_mixed_outcomes);
    }

    #[test]
    fn parses_runner_nodes_and_rust_test_functions() {
        let rows = parse_log_observations(
            "[lint.clippy] \u{2717} FAIL Clippy\n[test.liteinst_strict] test liteinst_detcore_strict_verify_micro_suite ... FAILED\n",
        );
        assert!(rows
            .iter()
            .any(|row| row.name == "lint.clippy" && gate_failed(row)));
        let test = rows
            .iter()
            .find(|row| row.name == "liteinst_detcore_strict_verify_micro_suite")
            .unwrap();
        assert_eq!(test.source_node.as_deref(), Some("test.liteinst_strict"));
    }

    #[test]
    fn dirty_or_unanchored_pass_is_not_green() {
        let mut dirty = row("tip", "2026-08-03T01:00:00Z", "full", "full", "pass");
        dirty.tree_dirty = Some(true);
        assert!(matches!(
            HistoryQueryEngine::new(vec!["tip".into()], vec![dirty])
                .newest_green("main", "origin/main"),
            NewestGreenOutcome::NoEvidence { .. }
        ));
    }

    #[test]
    fn cache_invalidates_on_branch_tip_or_ledger_change() {
        let report = NewestGreenReport {
            schema_version: 1,
            branch: "main".into(),
            branch_ref: "origin/main".into(),
            branch_tip: "tip-a".into(),
            green: evidence(
                "tip-a",
                &row("tip-a", "2026-08-03T01:00:00Z", "full", "full", "pass"),
            ),
            commits_after_green: 0,
            commits_without_any_record: 0,
            commits_with_records: 0,
        };
        let cache = NewestGreenCache {
            schema_version: 2,
            branch: "main".into(),
            branch_ref: "origin/main".into(),
            branch_tip: "tip-a".into(),
            ledger_path: "/tmp/ledger".into(),
            ledger_len: 100,
            ledger_modified_ns: 200,
            report,
        };
        let path = Path::new("/tmp/ledger");
        assert!(cache_matches(
            &cache,
            "main",
            "origin/main",
            "tip-a",
            path,
            100,
            200
        ));
        assert!(!cache_matches(
            &cache,
            "main",
            "origin/main",
            "tip-b",
            path,
            100,
            200
        ));
        assert!(!cache_matches(
            &cache,
            "main",
            "origin/main",
            "tip-a",
            path,
            101,
            201
        ));
    }

    #[test]
    fn retained_cell_evidence_is_used_only_for_a_matching_ledger_row() {
        let original = gate(
            row("sha", "2026-08-03T01:00:00Z", "full", "full", "fail"),
            "outer",
            "fail",
        );
        let mut cached_gate = GateHistoryRow {
            name: "inner.cell".into(),
            result: Some("fail".into()),
            kind: None,
            exit_code: None,
            real_seconds: None,
            source_node: Some("test.inner".into()),
            extra: BTreeMap::new(),
        };
        cached_gate.extra.insert(
            "error_excerpt".into(),
            serde_json::json!(["panicked at source.rs:1"]),
        );
        let cache = CellEvidenceCache {
            schema_version: 2,
            records: vec![RetainedCellEvidence {
                commit: "sha".into(),
                finished_at: Some("2026-08-03T01:00:00Z".into()),
                gates: vec![cached_gate],
            }],
        };
        let mut rows = vec![original];
        merge_retained_cell_evidence(&mut rows, &cache);
        assert!(rows[0].gates.iter().any(|gate| gate.name == "inner.cell"));

        rows[0].finished_at = Some("different-run".into());
        rows[0].gates.retain(|gate| gate.name != "inner.cell");
        merge_retained_cell_evidence(&mut rows, &cache);
        assert!(!rows[0].gates.iter().any(|gate| gate.name == "inner.cell"));
    }
}
