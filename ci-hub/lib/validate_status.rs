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
//! ## The predicate (VALIDATED) — version-aware over a schema transition
//!
//! The full rule is "a green must carry what it verified" (AGENTS.md Proxy
//! Binding, parent da98bdd): a clean, commit-anchored, FULL-profile,
//! FULL-selection PASS with a nonzero executed-test count AND zero filtered-out.
//! But the PRODUCER TRAVELS WITH THE BRANCH: a receipt's writer is whatever
//! `validate.sh` shipped on that PR's branch, and older writers do not emit the
//! count fields at all. Enforcing the count clauses on EVERY receipt therefore
//! strands every pre-count receipt (measured 2026-08-04: 35/35 clean full passes
//! in the ledger flip to NOT-VALIDATED), identically to the bfb0a9ef anchor
//! transition. So this consumer is VERSION-AWARE: it applies the rule that
//! matches each receipt's declared capability.
//!
//! A record satisfies the predicate for a commit iff it is clean,
//! commit-anchored, FULL-profile, FULL-selection, `result == "pass"`, AND:
//!   * it passes two UNIVERSAL guards that hold at every schema — `executed_tests
//!     != Some(0)` (a demonstrated zero-test run is a no-result, never green) and
//!     no positive `filtered_tests` (any name-filtered subset is a narrowed scope
//!     masquerading as the full profile); AND
//!   * IF the receipt is count-capable (`schema_version >= COUNTS_SCHEMA`, i.e.
//!     written by a count-emitting writer) OR actually carries both counts, it is
//!     held STRICT: `executed_tests == Some(n>0) && filtered_tests == Some(0)`.
//!     An absent count from a count-capable writer is a writer DEFECT, refused.
//!   * ELSE it is a genuinely pre-count receipt and is GRANDFATHERED: the two
//!     universal guards plus clean/full/full/pass suffice (the pre-tightening
//!     rule). This strands nobody and un-breaks the drain; new receipts pick up
//!     STRICT automatically as count-emitting writers roll out.
//!
//! The grandfather branch is schema/presence-keyed, NOT time-keyed, so it never
//! expires-and-strands: it self-liquidates as pre-count receipts age out. See
//! `ai_docs/transition-design-executed-filtered-count-schema-tightening_20260804.md`.
//! Keying strictness on field PRESENCE (not the `schema_version` integer alone) is
//! deliberate: the live writers disagree about the version — `aggregate.py` emits
//! counts under schema 1 while schema 3 and 4 exist WITHOUT counts — so the
//! condition ("this receipt measured coverage") must travel with the value as the
//! fields themselves; the schema escalator exists only to catch a count-capable
//! writer that emits nothing.
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

/// The `schema_version` at which a receipt's WRITER guarantees it emits both
/// count fields. A receipt stamped `>= COUNTS_SCHEMA` is held to the STRICT rule
/// even when a count is absent, because for such a writer an absent count is a
/// DEFECT (it was contractually required to emit), not a pre-count receipt.
///
/// This is the SINGLE source of truth for the version boundary: the count-emitting
/// `hermit/validate.sh` `append_validation_ledger` stamps exactly this
/// `schema_version`, and this consumer gates on `>=` it (see
/// `emit_executed_and_filtered`). Do not hard-code the integer in the writer with
/// a separate comment — writer and consumer are ONE judgement, or they diverge.
///
/// Why 5, not 4: schema 1/2/3 are already in use and schema 4 is ALREADY in the
/// ledger from a branch writer that emits NO counts, so 4 cannot mean "counts
/// present". 5 is the first clean anchor.
pub const COUNTS_SCHEMA: u32 = 5;

/// The landing / cache predicate — version-aware over the count-schema
/// transition (see the module docs and the transition design note). Over and
/// above clean full coverage (`is_clean_full_coverage`) and `result == "pass"`:
///
///   * TWO UNIVERSAL GUARDS, applied at EVERY schema (grandfather or strict):
///     `executed_tests != Some(0)` — a demonstrated zero-test run is a no-result
///     wearing a success badge (the `--features`-gated build that compiled the
///     tests out), never a green; and no positive `filtered_tests` — a
///     NAME-FILTERED subset (`1 passed; 154 filtered out`) covered a narrowed
///     scope while claiming the full profile, the same scope-masquerade as a
///     partial profile. Neither is ever grandfathered.
///   * THEN, if the receipt is COUNT-CAPABLE (`schema_version >= COUNTS_SCHEMA`)
///     OR actually CARRIES both counts, it is held STRICT: `executed_tests ==
///     Some(n>0) && filtered_tests == Some(0)`. This catches a count-capable
///     writer that emits nothing (a DEFECT) and enforces the full Proxy Binding
///     rule on every receipt able to prove its coverage — including
///     `aggregate.py`, which carries counts under an OLD schema.
///   * ELSE the receipt is a genuinely PRE-COUNT receipt (writer predates count
///     emission, carried neither count): it is GRANDFATHERED under the
///     pre-tightening rule (the two guards + clean/full/full/pass). This is the
///     transition allowance that un-breaks the drain without stranding the
///     backlog; it strands nobody and self-liquidates as pre-count receipts age.
///
/// Everything short of the applicable rule is NotValidated (exit 4 = re-dispatch),
/// never FailedOnRecord — an uncounted or under-covered run is UNVERIFIED, not
/// known-bad. This AGREES with the AGENTS.md Proxy Binding truth table (parent
/// da98bdd) for count-capable receipts; for grandfathered receipts it applies the
/// documented pre-transition contract those writers were built against.
pub fn is_clean_full_pass(row: &HistoryRow, sha: &str) -> bool {
    if !(is_clean_full_coverage(row, sha) && row.result.as_deref() == Some("pass")) {
        return false;
    }
    // Universal guards: a demonstrated zero-test run and any positive filtered
    // count are never a full green, at any schema — never grandfathered.
    if row.executed_tests == Some(0) {
        return false;
    }
    if matches!(row.filtered_tests, Some(f) if f > 0) {
        return false;
    }
    let count_capable = row.schema_version.is_some_and(|v| v >= COUNTS_SCHEMA);
    let counts_present = row.executed_tests.is_some() && row.filtered_tests.is_some();
    if count_capable || counts_present {
        // STRICT: the receipt can prove coverage (count-capable writer, or an
        // old-schema writer that carried both counts) — so require it to.
        matches!(row.executed_tests, Some(n) if n > 0) && row.filtered_tests == Some(0)
    } else {
        // GRANDFATHER: a genuinely pre-count receipt that cleared both universal
        // guards — accept under the pre-tightening clean/full/full/pass rule.
        true
    }
}

/// A clean full-coverage run for the commit that is a genuine FAILURE (a
/// known-bad commit). A `no_result` (a zero-test green already downgraded by the
/// aggregator) is deliberately EXCLUDED: it is not known-bad, it is unverified,
/// so it falls through to `NotValidated` (exit 4 = re-dispatch), never
/// `FailedOnRecord` (exit 3 = known failing).
fn is_clean_full_nonpass(row: &HistoryRow, sha: &str) -> bool {
    is_clean_full_coverage(row, sha)
        && row.result.as_deref() != Some("pass")
        && row.result.as_deref() != Some("no_result")
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

    /// The canonical LEGITIMATE full green: clean, commit-anchored, full/full,
    /// `result==pass`, and — as a green must — CARRYING its counts: a nonzero
    /// executed count and zero filtered-out. Negative tests override one field to
    /// prove that field alone is disqualifying.
    fn clean_full_pass(sha: &str) -> HistoryRow {
        row(&format!(
            r#"{{"schema_version":3,"finished_at":"2026-08-03T19:08:57Z","host":"devbig014",
                "profile":"full","selection_mode":"full","commit":"{sha}",
                "commit_anchored":true,"tree_dirty":false,"result":"pass",
                "executed_tests":36,"filtered_tests":0,
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
    fn zero_test_green_is_not_validated() {
        // NEGATIVE: a clean full-coverage "pass" whose own banners prove zero
        // tests ran is a no-result, not a landing-eligible green. It is neither a
        // pass nor a known failure, so it must fall through to NotValidated
        // (exit 4 = re-dispatch), never Validated and never FailedOnRecord.
        let mut r = clean_full_pass(PASS_SHA);
        r.executed_tests = Some(0);
        let a = assess(&[r], PASS_SHA);
        assert_eq!(a.verdict, Verdict::NotValidated);
        assert_eq!(a.verdict.exit_code(), 4);
        assert_eq!(a.qualifying.len(), 0);
    }

    #[test]
    fn counted_pass_validates() {
        // POSITIVE: the same clean full-coverage pass with a NONZERO executed
        // count is validated. Proves the guard is not inert — it does not reject
        // every green, only the demonstrably empty one.
        let mut r = clean_full_pass(PASS_SHA);
        r.executed_tests = Some(47);
        assert_eq!(assess(&[r], PASS_SHA).verdict, Verdict::Validated);
    }

    #[test]
    fn grandfathered_pre_count_pass_validates() {
        // TRANSITION POSITIVE (the 35/35 un-break case): a PRE-COUNT receipt —
        // old-schema (3 < COUNTS_SCHEMA) writer that carried NEITHER count — is a
        // clean full/full pass that clears both universal guards, so it is
        // grandfathered VALIDATED. This is exactly what un-breaks the live drain:
        // without it, every raw validate.sh receipt on main flips NotValidated.
        let mut r = clean_full_pass(PASS_SHA);
        r.schema_version = Some(3);
        r.executed_tests = None;
        r.filtered_tests = None;
        let a = assess(&[r], PASS_SHA);
        assert_eq!(a.verdict, Verdict::Validated);
        assert_eq!(a.verdict.exit_code(), 0);
    }

    #[test]
    fn grandfather_still_refuses_the_two_universal_guards() {
        // NEGATIVE: grandfathering is NOT a free pass. A pre-count receipt whose
        // OWN banners nonetheless prove a zero-test run, or a positive filtered
        // count, is refused at every schema — those are never a full green.
        let mut zero = clean_full_pass(PASS_SHA);
        zero.schema_version = Some(3);
        zero.filtered_tests = None;
        zero.executed_tests = Some(0);
        assert_eq!(assess(&[zero], PASS_SHA).verdict, Verdict::NotValidated);

        let mut filt = clean_full_pass(PASS_SHA);
        filt.schema_version = Some(3);
        filt.executed_tests = None;
        filt.filtered_tests = Some(154);
        assert_eq!(assess(&[filt], PASS_SHA).verdict, Verdict::NotValidated);
    }

    #[test]
    fn old_schema_carrying_counts_is_held_strict() {
        // aggregate.py stamps schema_version 1 but DOES emit both counts. Because
        // the counts are PRESENT the receipt can prove coverage, so it is held
        // STRICT even on the old schema: a positive count validates, a
        // demonstrated zero does not.
        let mut ok = clean_full_pass(PASS_SHA);
        ok.schema_version = Some(1);
        ok.executed_tests = Some(47);
        ok.filtered_tests = Some(0);
        assert_eq!(assess(&[ok], PASS_SHA).verdict, Verdict::Validated);

        let mut zero = clean_full_pass(PASS_SHA);
        zero.schema_version = Some(1);
        zero.executed_tests = Some(0);
        zero.filtered_tests = Some(0);
        assert_eq!(assess(&[zero], PASS_SHA).verdict, Verdict::NotValidated);
    }

    #[test]
    fn count_capable_absent_counts_is_a_defect() {
        // NEGATIVE (the new-writer bug catch): a COUNT-CAPABLE receipt
        // (schema_version >= COUNTS_SCHEMA) that emits NO counts is a writer
        // DEFECT, not a pre-count receipt — it was contractually required to emit.
        // Held strict; absent counts => NotValidated. This is the one case pure
        // presence-keying cannot catch and the schema escalator exists for.
        let mut r = clean_full_pass(PASS_SHA);
        r.schema_version = Some(COUNTS_SCHEMA);
        r.executed_tests = None;
        r.filtered_tests = None;
        let a = assess(&[r], PASS_SHA);
        assert_eq!(a.verdict, Verdict::NotValidated);
        assert_eq!(a.verdict.exit_code(), 4);
        assert_eq!(a.qualifying.len(), 0);
    }

    #[test]
    fn count_capable_full_green_validates() {
        // POSITIVE: a count-capable receipt carrying a nonzero executed count and
        // zero filtered is the fully-qualified green the transition converges on.
        let mut r = clean_full_pass(PASS_SHA);
        r.schema_version = Some(COUNTS_SCHEMA);
        r.executed_tests = Some(36);
        r.filtered_tests = Some(0);
        assert_eq!(assess(&[r], PASS_SHA).verdict, Verdict::Validated);
    }

    #[test]
    fn filtered_subset_is_not_validated() {
        // NEGATIVE: the `1 passed; 154 filtered out` narrowed-scope trap. A
        // full-coverage `pass` with a nonzero executed count but a POSITIVE
        // filtered count ran a name-filtered subset while claiming the full
        // profile — the same scope masquerade as a partial profile. Both
        // filtered=1 and filtered=154 are refused.
        for filtered in [1_i64, 154] {
            let mut r = clean_full_pass(PASS_SHA);
            r.executed_tests = Some(1);
            r.filtered_tests = Some(filtered);
            let a = assess(&[r], PASS_SHA);
            assert_eq!(
                a.verdict,
                Verdict::NotValidated,
                "filtered={filtered} must be NotValidated"
            );
            assert_eq!(a.verdict.exit_code(), 4);
        }
    }

    #[test]
    fn positive_control_legitimate_passes_validate_across_all_branches() {
        // POSITIVE CONTROL, N=6 across all three ACCEPT branches: the version-aware
        // consumer must not reject everything (a consumer that did would pass every
        // negative test and be disabled within a day). Each is a legitimate green:
        //   - GRANDFATHER: pre-count writer, no counts (schema 2 and 3).
        //   - OLD-WITH-COUNTS: aggregate.py carries counts under schema 1.
        //   - COUNT-CAPABLE: new writer, schema >= COUNTS_SCHEMA, counts present.
        // (schema_version, executed, filtered)
        let legit: [(u32, Option<i64>, Option<i64>); 6] = [
            (2, None, None),        // grandfather
            (3, None, None),        // grandfather (the 35/35 raw-validate.sh case)
            (1, Some(1), Some(0)),  // old schema carrying counts
            (1, Some(47), Some(0)), // old schema carrying counts
            (COUNTS_SCHEMA, Some(36), Some(0)), // count-capable full green
            (COUNTS_SCHEMA, Some(332), Some(0)), // count-capable full green
        ];
        for (sv, executed, filtered) in legit {
            let mut r = clean_full_pass(PASS_SHA);
            r.schema_version = Some(sv);
            r.executed_tests = executed;
            r.filtered_tests = filtered;
            let a = assess(&[r], PASS_SHA);
            assert_eq!(
                a.verdict,
                Verdict::Validated,
                "schema={sv} executed={executed:?} filtered={filtered:?} must be Validated"
            );
            assert_eq!(a.verdict.exit_code(), 0);
        }
    }

    #[test]
    fn no_result_verdict_is_not_failed_on_record() {
        // A `no_result` (the aggregator's downgrade of a zero-test green) is
        // unverified, not known-bad: NotValidated (re-dispatch), not
        // FailedOnRecord.
        let mut r = clean_full_pass(PASS_SHA);
        r.result = Some("no_result".into());
        let a = assess(&[r], PASS_SHA);
        assert_eq!(a.verdict, Verdict::NotValidated);
        assert_eq!(a.verdict.exit_code(), 4);
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
