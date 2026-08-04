# Format the exact-head/latest run already selected by check_outcome.py. Keeping
# selection in the canonical authority prevents jq and Python consumers from
# drifting on head filtering, timestamps, or run-ID tie-breaking.
if . == null or . == {} then
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
