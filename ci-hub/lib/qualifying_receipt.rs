//! The SINGLE qualifying-receipt predicate, shared by every consumer.
//!
//! # Why this module exists
//!
//! "Does this ledger row / receipt qualify as a full green?" was answered by
//! FIVE independent inline certifiers across three languages
//! (`validate_status.rs`, `history_queries.rs`, `query.py`,
//! `publish_receipt.py`, `verify_receipt.sh`). Each was its own floor and each
//! drifted from the others: the count-schema constant was redefined three times,
//! one consumer gated `filtered_tests == 0` while the others did not, `result ==
//! pass` was belt-and-suspendered in two places and keyed alone in two others,
//! and the per-node coverage clause was present in three and absent in one. That
//! is the hardcoded-list-of-a-growing-set defect applied to a PREDICATE: a
//! duplicated rule drifts in N-1 places and each copy looks correct in isolation
//! (see task `one-shared-qualifying-receipt-predicate-five-consumers-bypass-the-registry`,
//! source sweep `ai_docs/2026-08-04-floor-consumer-sweep.md`).
//!
//! # The fix
//!
//! ONE data artifact — `ci-hub/validate/qualifying-receipt.json` — colocated
//! with `rebase-base-floors.json` / `gate_floors.py` so tightening the floor is
//! ONE edit. All consumers READ it rather than restating its values inline.
//! Because the consumers span Rust, Python, and jq, the shared thing cannot be a
//! single function; it is a single DATUM that each language loads. A mutation of
//! the datum (e.g. `counts_schema` 5 -> 6, or `executed_tests_min` 1 -> huge)
//! must move every consumer's answer; any consumer whose answer does not move is
//! still bypassing the registry.
//!
//! # Resolution order (single source at run time)
//!
//! 1. `$QUALIFYING_RECEIPT_PREDICATE` — an explicit override path (the mutation
//!    test points this at a tightened copy; NEVER at the live file).
//! 2. the on-disk `ci-hub/validate/qualifying-receipt.json` resolved against the
//!    repo root — this is PRIMARY in production so an edit to the JSON takes
//!    effect with no recompile (the "one edit" guarantee).
//! 3. the compile-time [`EMBEDDED`] snapshot of that same file — a last-resort
//!    fallback for unit tests that run the compiled binary from a temp dir where
//!    the on-disk file is not reachable. It is `include_str!`-ed from the exact
//!    file, so it cannot silently diverge from the source at compile time.
//!
//! A malformed override or on-disk file is a deploy defect and PANICS (loud),
//! never silently falls through to the embed — silent fallback would mask the
//! very drift this module exists to prevent.

use crate::records::{CoverageRow, HistoryRow};
use serde::Deserialize;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// Repo-relative path of the canonical predicate. The literal lives ONLY here.
pub const PREDICATE_REL: &str = "ci-hub/validate/qualifying-receipt.json";

/// Environment override consulted first, so the mutation test can point every
/// consumer at a tightened copy without touching the live file.
pub const PREDICATE_ENV: &str = "QUALIFYING_RECEIPT_PREDICATE";

/// Compile-time snapshot of the live file (fallback only; see module docs).
pub const EMBEDDED: &str = include_str!("../validate/qualifying-receipt.json");

/// The mandatory receipt/ledger-row fields and their required values. A `pass`
/// row must satisfy every one; sourcing them here means a consumer cannot omit
/// one and quietly become more lenient than its peers.
#[derive(Clone, Debug, Deserialize)]
pub struct RequireClause {
    pub commit_anchored: bool,
    pub tree_dirty: bool,
    pub profile: String,
    pub selection_mode: String,
    pub result: String,
    pub failures_max: u64,
    pub executed_tests_min: i64,
}

/// The per-node coverage obligation and the schema at/after which it is enforced.
#[derive(Clone, Debug, Deserialize)]
pub struct CoverageClause {
    pub applies_at_schema_min: u32,
    pub per_node: bool,
}

/// The whole predicate, deserialized from the JSON. Unknown keys (e.g.
/// `_comment`) are ignored on purpose so the file can carry documentation.
#[derive(Clone, Debug, Deserialize)]
pub struct QualifyingPredicate {
    /// The `schema_version` at/after which a receipt's writer GUARANTEES it emits
    /// both count fields and per-node coverage, so an absent count/coverage on
    /// such a receipt is a writer DEFECT (reject), not a pre-count receipt.
    pub counts_schema: u32,
    pub require: RequireClause,
    /// When true, additionally require `filtered_tests == 0`. Default false: a
    /// full run legitimately filters other-lane/other-shard tests, so a positive
    /// aggregate filtered count says nothing about coverage (`executed>0` plus
    /// the per-node clause already guard emptiness/narrowing).
    #[serde(default)]
    pub gate_filtered_tests: bool,
    /// Green classes admitted by the landing predicate. The class is derived
    /// from receipt provenance; a row's optional `green_class` field is only a
    /// cache and must agree with that derivation.
    #[serde(default = "default_accepts_green_class")]
    pub accepts_green_class: Vec<String>,
    pub coverage: CoverageClause,
}

fn default_accepts_green_class() -> Vec<String> {
    vec!["hard".to_string()]
}

impl QualifyingPredicate {
    /// Parse from JSON text, mapping errors to a message that names the source.
    pub fn parse(text: &str, origin: &str) -> Result<Self, String> {
        serde_json::from_str(text)
            .map_err(|e| format!("{origin}: malformed qualifying predicate: {e}"))
    }
}

/// A per-node coverage obligation is SATISFIED iff the run planned at least one
/// test-bearing DAG node AND no planned test node was inert or absent. Carrying
/// the node NAMES in the receipt lets this be re-derived without re-reading a log
/// (Proxy Binding: the condition travels with the value).
pub fn coverage_satisfied(cov: &CoverageRow) -> bool {
    cov.planned_test_nodes > 0 && cov.zero_executed_nodes.is_empty() && cov.absent_nodes.is_empty()
}

/// Derive the receipt's green class from the same provenance fields and rules
/// as `validate/green_class.py`. `None` is a refused, internally contradictory
/// provenance record; `not-green` remains a derived class so the policy datum,
/// rather than an inline Rust special case, decides whether it is accepted.
fn derived_green_class(row: &HistoryRow) -> Option<&'static str> {
    let commit = row.commit.as_deref()?;
    if commit == "unknown" {
        return None;
    }
    let validated = match row.extra.get("validated_head_sha") {
        None => commit,
        Some(value) => value.as_str()?,
    };
    let inherited = row.extra.get("inherited_from");
    let derived = if validated == commit {
        if inherited.is_some() {
            return None;
        }
        "hard"
    } else {
        let inherited = inherited?.as_object()?;
        let kind = inherited.get("delta_kind")?.as_str()?;
        if !matches!(
            kind,
            "rebase-only" | "rebase-plus-upstream" | "new-branch-commits"
        ) {
            return None;
        }
        let branch_commits = inherited.get("branch_commits")?.as_u64()?;
        let force_full = match inherited.get("force_full_paths") {
            None => &[][..],
            Some(value) => value.as_array()?.as_slice(),
        };
        if !force_full.iter().all(serde_json::Value::is_string) {
            return None;
        }
        let upstream = match inherited.get("upstream_commits") {
            None => 0,
            Some(value) => value.as_u64()?,
        };
        if kind == "new-branch-commits" || branch_commits > 0 {
            "not-green"
        } else {
            match kind {
                "rebase-only" => {
                    if upstream != 0 {
                        return None;
                    }
                    "soft-rebase-only"
                }
                "rebase-plus-upstream" => {
                    if upstream == 0 {
                        return None;
                    }
                    if force_full.is_empty() {
                        "soft-upstream-delta"
                    } else {
                        "soft-force-full-touched"
                    }
                }
                _ => unreachable!("delta kind was checked above"),
            }
        }
    };
    if let Some(label) = row.extra.get("green_class") {
        if label.as_str() != Some(derived) {
            return None;
        }
    }
    Some(derived)
}

/// THE qualifying-receipt predicate. Every Rust consumer routes here; the
/// control flow mirrors what the inline certifiers did, but every constant and
/// flag is sourced from `pred` so there is one place to tighten.
///
/// Over and above clean full coverage (the `require` string/bool fields) and
/// `result == require.result`:
///   * `failures` (defaulting to 0 when absent) must be `<= require.failures_max`
///     — belt-and-suspenders with `result == pass`; a `pass`/`failures>0` row is
///     malformed and must not qualify.
///   * a demonstrated zero-test run (`executed_tests == Some(0)`) is never a
///     green at any schema — never grandfathered.
///   * if `gate_filtered_tests`, additionally require `filtered_tests == 0`.
///   * COUNT-CAPABLE receipts (`schema_version >= counts_schema`) are held to the
///     FULL per-node contract: `executed_tests >= require.executed_tests_min`
///     AND a `coverage` object that satisfies its obligation (when
///     `coverage.per_node` and `schema_version >= coverage.applies_at_schema_min`).
///     Missing either on a count-capable receipt is a writer defect -> reject.
///   * receipts carrying counts under an OLDER schema prove nonzero execution but
///     predate per-node coverage: held only to `executed_tests >=
///     require.executed_tests_min`.
///   * a receipt carrying NEITHER count proves nothing and is rejected
///     (NotValidated / re-dispatch), never treated as green.
pub fn row_qualifies(row: &HistoryRow, sha: &str, pred: &QualifyingPredicate) -> bool {
    let req = &pred.require;
    if !(row.commit.as_deref() == Some(sha)
        && row.commit_anchored == Some(req.commit_anchored)
        && row.tree_dirty == Some(req.tree_dirty)
        && row.selection_mode.as_deref() == Some(req.selection_mode.as_str())
        && row.profile.as_deref() == Some(req.profile.as_str())
        && row.result.as_deref() == Some(req.result.as_str()))
    {
        return false;
    }
    // `pass` with a positive failure count is malformed; an absent count on a
    // pass row is treated as zero (old rows predate the field).
    if row.failures.unwrap_or(0) > req.failures_max {
        return false;
    }
    // A demonstrated zero-test run is never a full green, at any schema.
    if row.executed_tests == Some(0) {
        return false;
    }
    if pred.gate_filtered_tests && row.filtered_tests != Some(0) {
        return false;
    }
    let schema = row.schema_version.unwrap_or(0);
    let count_capable = schema >= pred.counts_schema;
    let counts_present = row.executed_tests.is_some() && row.filtered_tests.is_some();
    let executed_ok = matches!(row.executed_tests, Some(n) if n >= req.executed_tests_min);
    let value_qualifies = if count_capable {
        let coverage_ok = !pred.coverage.per_node
            || schema < pred.coverage.applies_at_schema_min
            || row.coverage.as_ref().is_some_and(coverage_satisfied);
        executed_ok && coverage_ok
    } else if counts_present {
        // Old-schema writer that carried counts but predates per-node coverage:
        // hold it to the strongest thing it can prove — nonzero execution.
        executed_ok
    } else {
        // Neither count present: an uncounted receipt is UNVERIFIED, not green.
        false
    };
    if !value_qualifies {
        return false;
    }
    let Some(green_class) = derived_green_class(row) else {
        return false;
    };
    pred.accepts_green_class
        .iter()
        .any(|accepted| accepted == green_class)
}

/// Resolve the on-disk predicate path against the repo root. The literal path
/// lives only in [`PREDICATE_REL`].
pub fn predicate_path(root: &Path) -> PathBuf {
    root.join(PREDICATE_REL)
}

/// Load the predicate, honoring the resolution order in the module docs.
/// `root` supplies the on-disk location; a malformed override/on-disk file is a
/// hard error (the caller decides whether to panic or fail closed).
pub fn load(root: &Path) -> Result<QualifyingPredicate, String> {
    if let Some(path) = std::env::var_os(PREDICATE_ENV).filter(|v| !v.is_empty()) {
        let path = PathBuf::from(path);
        let text = std::fs::read_to_string(&path)
            .map_err(|e| format!("{}: cannot read {PREDICATE_ENV}: {e}", path.display()))?;
        return QualifyingPredicate::parse(&text, &path.display().to_string());
    }
    let path = predicate_path(root);
    match std::fs::read_to_string(&path) {
        Ok(text) => QualifyingPredicate::parse(&text, &path.display().to_string()),
        // Not reachable on disk (e.g. a unit-test temp dir): fall back to the
        // compile-time snapshot of the very same file. A malformed on-disk file,
        // by contrast, propagates as an error above and is never masked.
        Err(_) => QualifyingPredicate::parse(EMBEDDED, "embedded qualifying-receipt.json"),
    }
}

/// The process-wide active predicate for callers without a `root` in hand
/// (`is_clean_full_pass`, `assess`). Resolved once and cached. Resolution
/// mirrors `ci-hub.rs::workspace_root` (script/exe dir -> git toplevel) so a run
/// from the parent finds the live file; on any failure it falls back to the
/// embedded snapshot rather than stranding every green.
pub fn active() -> &'static QualifyingPredicate {
    static ACTIVE: OnceLock<QualifyingPredicate> = OnceLock::new();
    ACTIVE.get_or_init(|| {
        let root = resolve_root();
        match load(&root) {
            Ok(pred) => pred,
            // load() only errors on a malformed override/on-disk file — a real
            // deploy defect. Fail LOUD: a landing gate must not run on a guessed
            // predicate.
            Err(message) => panic!("qualifying-receipt predicate unusable: {message}"),
        }
    })
}

/// Best-effort repo root for [`active`], mirroring `ci-hub.rs::workspace_root`.
/// Falls back to the current directory (the embed then covers a missing file).
fn resolve_root() -> PathBuf {
    let source = std::env::var_os("RUST_SCRIPT_PATH")
        .filter(|path| !path.is_empty())
        .map(PathBuf::from)
        .or_else(|| std::env::current_exe().ok());
    let start = source
        .as_deref()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    let output = std::process::Command::new("git")
        .arg("-C")
        .arg(&start)
        .args(["rev-parse", "--show-toplevel"])
        .output();
    match output {
        Ok(out) if out.status.success() => {
            PathBuf::from(String::from_utf8_lossy(&out.stdout).trim())
        }
        _ => start,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(dir: &Path, name: &str, body: &str) -> PathBuf {
        let path = dir.join(name);
        std::fs::write(&path, body).unwrap();
        path
    }

    /// The embedded snapshot must always parse — it is the fallback of last
    /// resort and a parse failure would strand every green in a temp-dir run.
    #[test]
    fn embedded_snapshot_parses() {
        let pred = QualifyingPredicate::parse(EMBEDDED, "embedded").unwrap();
        assert!(pred.counts_schema >= 1);
        assert!(pred.require.executed_tests_min >= 1);
    }

    /// A tightened override MOVES the answer for the same row — the core of the
    /// mutation proof, exercised here at the Rust predicate boundary.
    #[test]
    fn tightening_executed_min_moves_the_answer() {
        let dir = std::env::temp_dir().join(format!(
            "qrp-test-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let live = write(&dir, "live.json", EMBEDDED);
        let tightened = write(
            &dir,
            "tight.json",
            &EMBEDDED.replace(
                "\"executed_tests_min\": 1",
                "\"executed_tests_min\": 999999",
            ),
        );
        let sha = "a".repeat(40);
        // Build the row the way the rest of the crate's tests do (HistoryRow has
        // no Default): a genuine schema-5 full green carrying counts + coverage.
        let row: HistoryRow = serde_json::from_str(&format!(
            r#"{{"schema_version":5,"profile":"full","selection_mode":"full","commit":"{sha}",
                "commit_anchored":true,"tree_dirty":false,"result":"pass","failures":0,
                "executed_tests":740,"filtered_tests":3,
                "coverage":{{"planned_test_nodes":4,"executed_test_nodes":4,
                    "zero_executed_nodes":[],"absent_nodes":[]}}}}"#
        ))
        .unwrap();
        let live_pred =
            QualifyingPredicate::parse(&std::fs::read_to_string(&live).unwrap(), "live").unwrap();
        let tight_pred =
            QualifyingPredicate::parse(&std::fs::read_to_string(&tightened).unwrap(), "tight")
                .unwrap();
        assert!(
            row_qualifies(&row, &sha, &live_pred),
            "live must accept the genuine green"
        );
        assert!(
            !row_qualifies(&row, &sha, &tight_pred),
            "tightened must reject it"
        );
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn hard_positive_and_soft_rebase_only_negative_share_the_policy() {
        let pred = QualifyingPredicate::parse(EMBEDDED, "embedded").unwrap();
        let sha = "a".repeat(40);
        let validated = "c".repeat(40);
        let hard: HistoryRow = serde_json::from_value(serde_json::json!({
            "schema_version": 5,
            "profile": "full",
            "selection_mode": "full",
            "commit": sha,
            "commit_anchored": true,
            "tree_dirty": false,
            "result": "pass",
            "failures": 0,
            "executed_tests": 740,
            "filtered_tests": 3,
            "coverage": {
                "planned_test_nodes": 4,
                "executed_test_nodes": 4,
                "zero_executed_nodes": [],
                "absent_nodes": []
            }
        }))
        .unwrap();
        assert!(row_qualifies(&hard, &sha, &pred));

        let mut soft_value = serde_json::to_value(&hard).unwrap();
        soft_value["validated_head_sha"] = serde_json::json!(validated);
        soft_value["inherited_from"] = serde_json::json!({
            "delta_kind": "rebase-only",
            "upstream_commits": 0,
            "branch_commits": 0,
            "patch_identical": true,
            "force_full_paths": []
        });
        soft_value["green_class"] = serde_json::json!("soft-rebase-only");
        let soft: HistoryRow = serde_json::from_value(soft_value).unwrap();
        assert!(
            !row_qualifies(&soft, &sha, &pred),
            "hard-only policy must not promote inherited soft evidence"
        );
    }

    #[test]
    fn soft_provenance_refuses_bool_counts_and_non_string_paths() {
        let sha = "a".repeat(40);
        let validated = "c".repeat(40);
        let base = serde_json::json!({
            "schema_version": 5,
            "profile": "full",
            "selection_mode": "full",
            "commit": sha,
            "commit_anchored": true,
            "tree_dirty": false,
            "result": "pass",
            "failures": 0,
            "executed_tests": 740,
            "coverage": {
                "planned_test_nodes": 4,
                "executed_test_nodes": 4,
                "zero_executed_nodes": [],
                "absent_nodes": []
            },
            "validated_head_sha": validated,
            "inherited_from": {
                "delta_kind": "rebase-only",
                "upstream_commits": 0,
                "branch_commits": 0,
                "force_full_paths": []
            },
            "green_class": "soft-rebase-only"
        });
        let valid: HistoryRow = serde_json::from_value(base.clone()).unwrap();
        assert_eq!(derived_green_class(&valid), Some("soft-rebase-only"));

        let mut validated_number = base.clone();
        validated_number["validated_head_sha"] = serde_json::json!(7);
        let validated_number: HistoryRow = serde_json::from_value(validated_number).unwrap();
        assert_eq!(derived_green_class(&validated_number), None);

        let mut branch_bool = base.clone();
        branch_bool["inherited_from"]["branch_commits"] = serde_json::json!(false);
        let branch_bool: HistoryRow = serde_json::from_value(branch_bool).unwrap();
        assert_eq!(derived_green_class(&branch_bool), None);

        let mut upstream_bool = base.clone();
        upstream_bool["inherited_from"]["upstream_commits"] = serde_json::json!(false);
        let upstream_bool: HistoryRow = serde_json::from_value(upstream_bool).unwrap();
        assert_eq!(derived_green_class(&upstream_bool), None);

        let mut upstream_too_large = base.clone();
        upstream_too_large["inherited_from"]["delta_kind"] =
            serde_json::json!("rebase-plus-upstream");
        upstream_too_large["inherited_from"]["upstream_commits"] =
            serde_json::from_str("18446744073709551616").unwrap();
        upstream_too_large["green_class"] = serde_json::json!("soft-upstream-delta");
        let upstream_too_large: HistoryRow = serde_json::from_value(upstream_too_large).unwrap();
        assert_eq!(derived_green_class(&upstream_too_large), None);

        let mut null_path_list = base.clone();
        null_path_list["inherited_from"]["force_full_paths"] = serde_json::Value::Null;
        let null_path_list: HistoryRow = serde_json::from_value(null_path_list).unwrap();
        assert_eq!(derived_green_class(&null_path_list), None);

        let mut non_string_path = base;
        non_string_path["inherited_from"] = serde_json::json!({
            "delta_kind": "rebase-plus-upstream",
            "upstream_commits": 1,
            "branch_commits": 0,
            "force_full_paths": ["ci/run-node.sh", 7]
        });
        non_string_path["green_class"] = serde_json::json!("soft-force-full-touched");
        let non_string_path: HistoryRow = serde_json::from_value(non_string_path).unwrap();
        assert_eq!(derived_green_class(&non_string_path), None);
    }
}
