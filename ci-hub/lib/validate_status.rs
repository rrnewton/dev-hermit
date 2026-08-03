//! The SHA-queryable landing / cache predicate over the validate-run ledger.
//!
//! This is the ONE shared artifact behind two owner P0s (2026-08-03):
//!   * `validate-result-cache-by-sha` CHECKS it (skip a validate whose clean
//!     HEAD already has a pass record), and
//!   * `lander-lands-on-local-validate-only` READS it (land iff a clean-validate
//!     record exists for the exact PR head, GitHub-free).
//!
//! One store, one predicate: hermit/reverie `validate.sh` WRITE the JSONL ledger
//! (`append_validation_ledger`), this module READS it. There is deliberately no
//! second table.
//!
//! ## The predicate (VALIDATED)
//!
//! A record satisfies the predicate for a commit iff it is a clean,
//! commit-anchored, FULL-profile, FULL-selection PASS:
//!   commit == <sha> && commit_anchored == true && tree_dirty == false &&
//!   selection_mode == "full" && profile == "full" && result == "pass".
//!
//! This is intentionally NEVER LOOSER than `hermit/validate.sh`'s own
//! `locally-validated` stamp guard, which fires only when
//! `VALIDATION_LEVEL == full && failures == 0` on a commit-anchored, non-dirty,
//! non-subset run (validate.sh:4161 with :283/:404). `profile` is `VALIDATION_
//! LEVEL` unless a subset mode overrode it, so requiring both `profile == "full"`
//! and `selection_mode == "full"` reproduces "level==full AND not a subset run".
//! A `quick`/`super`/`portable-only` level, or any `--only`/`--selective`/
//! `*-only` subset run, is fail-closed rejected: a subset must never masquerade
//! as full coverage. `commit_anchored`/`tree_dirty` guarantee the record
//! describes the actual commit, not a dirty tree (the tree is not the commit).

use crate::records::HistoryRow;
use std::collections::BTreeSet;

/// Canonical ledger path relative to the workspace root. This is the exact file
/// `validate.sh` appends to: it sets `VALIDATION_LEDGER_FILE` to
/// `$DEV_HERMIT_PARENT/ignored/validate-run-ledger.jsonl` (validate.sh line ~350)
/// and `append_validation_ledger()` writes one JSONL record per run there. The
/// landing gate MUST read this same file or it sees an empty history and reports
/// "no clean validate" for every PR. (`ci-hub/validate-runs.jsonl` is a stale
/// path; `ci-hub/validate/worktrees.py`'s `validate-runs.jsonl` is a different,
/// data-dir-scoped per-run registry, not this ledger.)
pub const LEDGER_REL: &str = "ignored/validate-run-ledger.jsonl";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verdict {
    /// At least one clean full-coverage PASS record for the commit.
    Validated,
    /// No PASS, but a clean full-coverage record that FAILED/killed/timed out —
    /// a known-bad commit, distinct and more informative than "no record".
    FailedOnRecord,
    /// No qualifying record: none at all, or only dirty/subset/unanchored runs.
    NotValidated,
}

impl Verdict {
    /// Exit codes are stable so shell callers (`land-pr.sh`, the cache) can gate
    /// on them: 0 = land/skip-validate, 3 = known-failing, 4 = must validate.
    pub fn exit_code(self) -> i32 {
        match self {
            Verdict::Validated => 0,
            Verdict::FailedOnRecord => 3,
            Verdict::NotValidated => 4,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Verdict::Validated => "VALIDATED",
            Verdict::FailedOnRecord => "FAILED",
            Verdict::NotValidated => "NOT-VALIDATED",
        }
    }
}

fn field_bool(row: &HistoryRow, key: &str) -> Option<bool> {
    match key {
        "commit_anchored" => row.commit_anchored,
        "tree_dirty" => row.tree_dirty,
        _ => row.extra.get(key).and_then(|value| value.as_bool()),
    }
}

fn field_str<'a>(row: &'a HistoryRow, key: &str) -> Option<&'a str> {
    match key {
        "selection_mode" => row.selection_mode.as_deref(),
        _ => row.extra.get(key).and_then(|value| value.as_str()),
    }
}

/// A record that is clean, commit-anchored, full-profile, full-selection — the
/// coverage prerequisite shared by the PASS and the known-FAIL cases.
fn is_clean_full_coverage(row: &HistoryRow, sha: &str) -> bool {
    row.commit.as_deref() == Some(sha)
        && field_bool(row, "commit_anchored") == Some(true)
        && field_bool(row, "tree_dirty") == Some(false)
        && field_str(row, "selection_mode") == Some("full")
        && row.profile.as_deref() == Some("full")
}

/// The landing / cache predicate: a clean full-coverage PASS for the commit.
pub fn is_clean_full_pass(row: &HistoryRow, sha: &str) -> bool {
    is_clean_full_coverage(row, sha) && row.result.as_deref() == Some("pass")
}

/// A clean full-coverage run for the commit that did NOT pass.
fn is_clean_full_nonpass(row: &HistoryRow, sha: &str) -> bool {
    is_clean_full_coverage(row, sha) && row.result.as_deref() != Some("pass")
}

/// The outcome of assessing a commit against the ledger.
pub struct Assessment {
    pub sha: String,
    pub verdict: Verdict,
    /// Records that satisfy the predicate (clean full-coverage PASS).
    pub qualifying: Vec<HistoryRow>,
    /// Every other record for the same commit (subset, dirty, failed, ...),
    /// retained so callers can explain WHY a commit is NOT validated.
    pub disqualified: Vec<HistoryRow>,
}

/// Assess a single commit against all ledger rows. `sha` must be a full 40-hex
/// commit (resolve prefixes with [`resolve_sha`] first).
pub fn assess(rows: &[HistoryRow], sha: &str) -> Assessment {
    let mut qualifying = Vec::new();
    let mut disqualified = Vec::new();
    let mut saw_clean_full_nonpass = false;
    for row in rows {
        if row.commit.as_deref() != Some(sha) {
            continue;
        }
        if is_clean_full_pass(row, sha) {
            qualifying.push(row.clone());
        } else {
            if is_clean_full_nonpass(row, sha) {
                saw_clean_full_nonpass = true;
            }
            disqualified.push(row.clone());
        }
    }
    let verdict = if !qualifying.is_empty() {
        Verdict::Validated
    } else if saw_clean_full_nonpass {
        Verdict::FailedOnRecord
    } else {
        Verdict::NotValidated
    };
    Assessment {
        sha: sha.to_string(),
        verdict,
        qualifying,
        disqualified,
    }
}

/// Pick the most recent qualifying record (by `finished_at` string order, which
/// is ISO-8601-Z and therefore lexicographically chronological).
pub fn newest(rows: &[HistoryRow]) -> Option<&HistoryRow> {
    rows.iter().max_by(|a, b| {
        a.finished_at
            .as_deref()
            .unwrap_or("")
            .cmp(b.finished_at.as_deref().unwrap_or(""))
    })
}

/// Expand a commit input to a full 40-hex commit using the ledger's own
/// commits. A full hex input is returned lowercased unchanged (no ledger lookup
/// needed). A shorter prefix must resolve to exactly one distinct commit.
pub fn resolve_sha(rows: &[HistoryRow], input: &str) -> Result<String, String> {
    let lc = input.to_ascii_lowercase();
    let is_hex = !lc.is_empty() && lc.chars().all(|c| c.is_ascii_hexdigit());
    if !is_hex {
        return Err(format!("'{input}' is not a hex commit or prefix"));
    }
    if lc.len() == 40 {
        return Ok(lc);
    }
    if lc.len() > 40 {
        return Err(format!("'{input}' is longer than a 40-hex commit"));
    }
    let mut distinct: BTreeSet<String> = BTreeSet::new();
    for row in rows {
        if let Some(commit) = row.commit.as_deref() {
            if commit.starts_with(&lc) {
                distinct.insert(commit.to_string());
            }
        }
    }
    match distinct.len() {
        1 => Ok(distinct.into_iter().next().expect("len==1")),
        0 => Err(format!("no ledger record has a commit matching '{input}'")),
        n => Err(format!(
            "commit prefix '{input}' is ambiguous across {n} distinct ledger commits"
        )),
    }
}

/// Parse an append-only validate-runs JSONL buffer into typed rows. Blank lines
/// are skipped. A line that fails to parse is skipped and COUNTED (returned as
/// the second element) rather than aborting: the ledger is appended by many
/// writers and one malformed line must never blind the query to every other
/// record. Fail-closed at the predicate, tolerant at the parser.
pub fn parse_ledger(buf: &str) -> (Vec<HistoryRow>, usize) {
    let mut rows = Vec::new();
    let mut skipped = 0usize;
    for line in buf.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        match serde_json::from_str::<HistoryRow>(line) {
            Ok(row) => rows.push(row),
            Err(_) => skipped += 1,
        }
    }
    (rows, skipped)
}

#[cfg(test)]
mod tests {
    use super::*;

    const PASS_SHA: &str = "cde3c1195eee4e2691bac64a4aec10a45aba853e";
    const OTHER_SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    fn row(json: &str) -> HistoryRow {
        serde_json::from_str(json).expect("valid HistoryRow json")
    }

    fn clean_full_pass(sha: &str) -> HistoryRow {
        row(&format!(
            r#"{{"schema_version":3,"finished_at":"2026-08-03T19:08:57Z","host":"devbig014",
                "profile":"full","selection_mode":"full","commit":"{sha}",
                "commit_anchored":true,"tree_dirty":false,"result":"pass",
                "checks":36,"failures":0,"real_seconds":528,"user_seconds":1300,"sys_seconds":90}}"#
        ))
    }

    #[test]
    fn clean_full_pass_validates() {
        let rows = vec![clean_full_pass(PASS_SHA)];
        let a = assess(&rows, PASS_SHA);
        assert_eq!(a.verdict, Verdict::Validated);
        assert_eq!(a.verdict.exit_code(), 0);
        assert_eq!(a.qualifying.len(), 1);
    }

    #[test]
    fn dirty_tree_is_never_a_hit() {
        let mut r = clean_full_pass(PASS_SHA);
        r.tree_dirty = Some(true);
        let a = assess(&[r], PASS_SHA);
        assert_eq!(a.verdict, Verdict::NotValidated);
        assert_eq!(a.verdict.exit_code(), 4);
    }

    #[test]
    fn unanchored_is_never_a_hit() {
        let mut r = clean_full_pass(PASS_SHA);
        r.commit_anchored = Some(false);
        assert_eq!(assess(&[r], PASS_SHA).verdict, Verdict::NotValidated);
    }

    #[test]
    fn subset_selection_is_never_a_hit() {
        let mut r = clean_full_pass(PASS_SHA);
        r.selection_mode = Some("selective".into());
        assert_eq!(assess(&[r], PASS_SHA).verdict, Verdict::NotValidated);
    }

    #[test]
    fn non_full_profile_is_never_a_hit() {
        // A `quick`-level run is real work but is NOT the full gate; the stamp
        // guard (VALIDATION_LEVEL==full) would not fire, so neither do we.
        let mut r = clean_full_pass(PASS_SHA);
        r.profile = Some("quick".into());
        assert_eq!(assess(&[r], PASS_SHA).verdict, Verdict::NotValidated);
    }

    #[test]
    fn clean_full_failure_is_failed_not_missing() {
        let mut r = clean_full_pass(PASS_SHA);
        r.result = Some("fail".into());
        let a = assess(&[r], PASS_SHA);
        assert_eq!(a.verdict, Verdict::FailedOnRecord);
        assert_eq!(a.verdict.exit_code(), 3);
    }

    #[test]
    fn killed_run_is_failed_on_record() {
        // The real observed record: a full/full run that was killed (Ctrl-C).
        let r = row(
            r#"{"schema_version":3,"host":"devbig014","profile":"full","selection_mode":"full",
                "commit":"cde3c1195eee4e2691bac64a4aec10a45aba853e","commit_anchored":true,
                "tree_dirty":false,"result":"killed","exit_code":130,"checks":0,"failures":0}"#,
        );
        assert_eq!(assess(&[r], PASS_SHA).verdict, Verdict::FailedOnRecord);
    }

    #[test]
    fn no_record_is_not_validated() {
        let rows = vec![clean_full_pass(OTHER_SHA)];
        assert_eq!(assess(&rows, PASS_SHA).verdict, Verdict::NotValidated);
    }

    #[test]
    fn a_pass_wins_over_a_sibling_failure() {
        // Two records for one commit: an earlier fail and a later clean pass.
        let mut fail = clean_full_pass(PASS_SHA);
        fail.result = Some("fail".into());
        fail.finished_at = Some("2026-08-03T10:00:00Z".into());
        let rows = vec![fail, clean_full_pass(PASS_SHA)];
        assert_eq!(assess(&rows, PASS_SHA).verdict, Verdict::Validated);
    }

    #[test]
    fn parse_ledger_skips_blank_and_bad_lines() {
        let buf = format!(
            "{}\n\n   \nnot json at all\n{}\n",
            serde_json::to_string(&clean_full_pass(PASS_SHA)).unwrap(),
            serde_json::to_string(&clean_full_pass(OTHER_SHA)).unwrap(),
        );
        let (rows, skipped) = parse_ledger(&buf);
        assert_eq!(rows.len(), 2);
        assert_eq!(skipped, 1);
    }

    #[test]
    fn resolve_full_sha_needs_no_ledger() {
        assert_eq!(resolve_sha(&[], PASS_SHA).unwrap(), PASS_SHA);
        assert_eq!(
            resolve_sha(&[], &PASS_SHA.to_ascii_uppercase()).unwrap(),
            PASS_SHA
        );
    }

    #[test]
    fn resolve_prefix_is_unambiguous_or_errors() {
        let rows = vec![clean_full_pass(PASS_SHA), clean_full_pass(OTHER_SHA)];
        assert_eq!(resolve_sha(&rows, "cde3c11").unwrap(), PASS_SHA);
        assert!(resolve_sha(&rows, "deadbeef").is_err()); // no match
        // "a" prefixes only OTHER_SHA here, so it is unambiguous; a shared
        // prefix across two distinct commits must error.
        let mut third = clean_full_pass("cdefffffffffffffffffffffffffffffffffffff");
        third.finished_at = Some("2026-08-03T20:00:00Z".into());
        let rows2 = vec![clean_full_pass(PASS_SHA), third];
        assert!(resolve_sha(&rows2, "cde").is_err()); // ambiguous
    }

    #[test]
    fn newest_picks_latest_finished_at() {
        let mut early = clean_full_pass(PASS_SHA);
        early.finished_at = Some("2026-08-03T01:00:00Z".into());
        let mut late = clean_full_pass(PASS_SHA);
        late.finished_at = Some("2026-08-03T23:00:00Z".into());
        let rows = vec![early, late.clone()];
        assert_eq!(
            newest(&rows).unwrap().finished_at.as_deref(),
            Some("2026-08-03T23:00:00Z")
        );
    }
}
