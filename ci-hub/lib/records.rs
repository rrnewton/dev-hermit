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
    pub commit: Option<String>,
    #[serde(default)]
    pub result: Option<String>,
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
