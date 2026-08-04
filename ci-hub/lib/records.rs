//! Typed schemas consumed by the ci-hub front door.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ObligationRecord {
    pub schema_version: u32,
    pub obligation_id: String,
    pub repo: String,
    pub landed_sha: String,
    pub opened_at: String,
    pub overall_state: String,
    pub local: VerificationState,
    pub github: VerificationState,
    #[serde(default)]
    pub failure_summary: Option<String>,
    #[serde(default)]
    pub recommendation: Option<Recommendation>,
    #[serde(default)]
    pub remediation: Option<RemediationState>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

impl ObligationRecord {
    pub fn is_closed(&self) -> bool {
        matches!(self.overall_state.as_str(), "satisfied" | "remediated")
    }

    pub fn remediation_required(&self) -> bool {
        self.overall_state == "remediation_required"
    }

    pub fn recommendation_action(&self) -> &str {
        self.recommendation
            .as_ref()
            .and_then(|recommendation| recommendation.action.as_deref())
            .unwrap_or("-")
    }

    pub fn dispatch_state(&self) -> &str {
        self.remediation
            .as_ref()
            .and_then(|remediation| remediation.dispatch.as_ref())
            .and_then(|dispatch| dispatch.state.as_deref())
            .unwrap_or("-")
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct VerificationState {
    pub state: String,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Recommendation {
    #[serde(default)]
    pub action: Option<String>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RemediationState {
    #[serde(default)]
    pub state: Option<String>,
    #[serde(default)]
    pub dispatch: Option<DispatchState>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct DispatchState {
    #[serde(default)]
    pub state: Option<String>,
    #[serde(default)]
    pub target: Option<String>,
    #[serde(default)]
    pub acknowledged_by: Option<String>,
    #[serde(default)]
    pub acknowledged_session: Option<String>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

/// The stable fields emitted by `validate/aggregate.py --json` and JSONL stores.
/// Optional fields reflect honest reconstructed rows where a measurement was not
/// available; unrecognized fields are retained for forward compatibility.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct HistoryRow {
    #[serde(default)]
    pub schema_version: Option<u32>,
    #[serde(default)]
    pub started_at: Option<String>,
    #[serde(default)]
    pub finished_at: Option<String>,
    #[serde(default)]
    pub host: Option<String>,
    #[serde(default)]
    pub slot: Option<String>,
    /// Product the run validated: "hermit" or "reverie". Absent on pre-`repo`
    /// hermit ledger rows (aggregate.py defaults those to "hermit").
    #[serde(default)]
    pub repo: Option<String>,
    #[serde(default)]
    pub cwd: Option<String>,
    #[serde(default)]
    pub profile: Option<String>,
    #[serde(default)]
    pub selection_mode: Option<String>,
    #[serde(default)]
    pub commit: Option<String>,
    #[serde(default)]
    pub commit_anchored: Option<bool>,
    #[serde(default)]
    pub tree_dirty: Option<bool>,
    #[serde(default)]
    pub result: Option<String>,
    /// Tests EXECUTED per the run's own test-runner banners. `None` = unknown (no
    /// banner in the log), distinct from `Some(0)` = a demonstrably inert green
    /// (a `--features`-gated build that compiled the tests out). A green carries
    /// this so a reader — and the landing predicate — can tell a real pass from a
    /// no-result wearing a success badge.
    #[serde(default)]
    pub executed_tests: Option<i64>,
    /// Tests FILTERED OUT by the run's selection. `Some(0)` alongside
    /// `executed_tests == Some(0)` is an empty target; `Some(n>0)` is a filtered
    /// subset (the `1 passed; 154 filtered out` narrowed-scope trap).
    #[serde(default)]
    pub filtered_tests: Option<i64>,
    /// Whether the run covered the FULL profile (`level == "full"`), not a
    /// partial `*-only` profile whose pass reads identically to a full green.
    #[serde(default)]
    pub full_coverage: Option<bool>,
    #[serde(default)]
    pub checks: Option<u64>,
    #[serde(default)]
    pub failures: Option<u64>,
    #[serde(default)]
    pub real_seconds: Option<f64>,
    #[serde(default)]
    pub user_seconds: Option<f64>,
    #[serde(default)]
    pub sys_seconds: Option<f64>,
    #[serde(default)]
    pub log_file: Option<String>,
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default)]
    pub gates: Vec<GateHistoryRow>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct GateHistoryRow {
    pub name: String,
    #[serde(default)]
    pub result: Option<String>,
    #[serde(default)]
    pub kind: Option<String>,
    #[serde(default)]
    pub exit_code: Option<i32>,
    #[serde(default)]
    pub real_seconds: Option<f64>,
    /// For a test function extracted from a retained DAG log, the manifest node
    /// which emitted it. Ledger-native outer gates leave this unset.
    #[serde(default)]
    pub source_node: Option<String>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn obligation_schema_retains_unknown_fields() {
        let record: ObligationRecord = serde_json::from_str(
            r#"{
                "schema_version":1,
                "obligation_id":"o1",
                "repo":"rrnewton/hermit",
                "landed_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "opened_at":"2026-08-03T00:00:00Z",
                "overall_state":"open",
                "local":{"state":"running","pid":42},
                "github":{"state":"pending","run_ids":[]},
                "remediation":{"state":"triggered","dispatch":{"state":"sent_unacknowledged","wake_id":"w1"}},
                "future_field":"preserved"
            }"#,
        )
        .unwrap();
        assert!(!record.is_closed());
        assert_eq!(record.local.extra["pid"], 42);
        assert_eq!(record.extra["future_field"], "preserved");
        assert_eq!(record.dispatch_state(), "sent_unacknowledged");
        assert_eq!(
            record.remediation.unwrap().dispatch.unwrap().extra["wake_id"],
            "w1"
        );
    }

    #[test]
    fn history_schema_accepts_unmeasured_cpu() {
        let row: HistoryRow = serde_json::from_str(
            r#"{
                "schema_version":1,
                "commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "result":"pass",
                "checks":36,
                "real_seconds":123,
                "user_seconds":null,
                "sys_seconds":null
            }"#,
        )
        .unwrap();
        assert_eq!(row.checks, Some(36));
        assert_eq!(row.real_seconds, Some(123.0));
        assert_eq!(row.user_seconds, None);
    }
}
