# Select the latest gate that actually ran for this PR head. The workflow_run
# controller runs on main and only dispatches the PR-head workflow, so it is
# deliberately excluded. PR statusCheckRollup is deliberately not the source:
# it can omit the workflow_dispatch success.
[
  .workflow_runs[]
  | select(.head_sha == $sha)
  | select(.event == "pull_request" or .event == "workflow_dispatch")
]
| sort_by(.created_at)
| last
| if . == null then
    ["MISSING", "PENDING", "-", "-", "-", "-"]
  else
    [
      (.status | ascii_upcase),
      ((.conclusion // "PENDING") | ascii_upcase),
      .event,
      (.id | tostring),
      .html_url,
      .created_at
    ]
  end
| @tsv
