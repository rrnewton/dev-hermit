#!/usr/bin/env bash
set -uo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cases=${CASES:-"$script_dir/full-scorecard-cases.tsv"}
hermit_bin=${HERMIT_BIN:?set HERMIT_BIN to an immutable Hermit binary}
output_dir=${OUTPUT_DIR:?set OUTPUT_DIR outside the repository}
libunwind_dir=${LIBUNWIND_DIR:-/home/newton/.local/hermit-deps/lu/usr/lib64}
cpu=${CPU:-0}
timeout_seconds=${TIMEOUT_SECONDS:-60}
hermit_version=$(
  env LD_LIBRARY_PATH="$libunwind_dir" "$hermit_bin" --version
)
hermit_sha256=$(sha256sum "$hermit_bin" | cut -d' ' -f1)

mkdir -p "$output_dir/cells"
results="$output_dir/full-scorecard-results.csv"
printf '%s\n' 'cell_id,kind,path,host_readable,verify_rc,verified,bitwise_parity,verify_strictness,guest_exit_code,content_run1_rc,content_run2_rc,content_equal,stdout_sha256,stdout_bytes,host_sha256,host_bytes,guest_equals_host,status' >"$results"

while IFS=$'\t' read -r cell_id kind path; do
  [[ $cell_id == cell_id ]] && continue
  cell_dir="$output_dir/cells/$cell_id-$kind"
  mkdir -p "$cell_dir"
  if [[ -r $path ]]; then
    host_readable=true
    /bin/cat "$path" >"$cell_dir/host.stdout"
    host_sha=$(sha256sum "$cell_dir/host.stdout" | cut -d' ' -f1)
    host_bytes=$(wc -c <"$cell_dir/host.stdout")
  else
    host_readable=false
    : >"$cell_dir/host.stdout"
    host_sha=unavailable
    host_bytes=0
  fi

  guest=(/bin/cat "$path")
  if [[ $kind == Locks ]]; then
    guest=(/usr/bin/python3 -- -c 'import fcntl; f=open("/tmp/proc-sys-python-lock","w"); fcntl.lockf(f,fcntl.LOCK_EX); print(open("/proc/locks").read(),end="")')
  fi

  set +e
  env LD_LIBRARY_PATH="$libunwind_dir" timeout "${timeout_seconds}s" taskset -c "$cpu" \
    "$hermit_bin" --log info run --backend ptrace --strict --verify \
    --verify-json "$cell_dir/verify.json" --max-timeslice disabled \
    "${guest[@]}" </dev/null >"$cell_dir/verify.stdout" 2>"$cell_dir/verify.stderr"
  verify_rc=$?

  env LD_LIBRARY_PATH="$libunwind_dir" timeout "${timeout_seconds}s" taskset -c "$cpu" \
    "$hermit_bin" --log warn run --backend ptrace --strict --max-timeslice disabled \
    "${guest[@]}" </dev/null >"$cell_dir/run1.stdout" 2>"$cell_dir/run1.stderr"
  run1_rc=$?
  env LD_LIBRARY_PATH="$libunwind_dir" timeout "${timeout_seconds}s" taskset -c "$cpu" \
    "$hermit_bin" --log warn run --backend ptrace --strict --max-timeslice disabled \
    "${guest[@]}" </dev/null >"$cell_dir/run2.stdout" 2>"$cell_dir/run2.stderr"
  run2_rc=$?
  set -e

  if [[ -s $cell_dir/verify.json ]]; then
    verified=$(jq -r '.verified' "$cell_dir/verify.json")
    bitwise=$(jq -r '.bitwise_parity' "$cell_dir/verify.json")
    strictness=$(jq -r '.comparison.strictness' "$cell_dir/verify.json")
    guest_exit=$(jq -r '.guest_exit_code // "null"' "$cell_dir/verify.json")
  else
    verified=false
    bitwise=false
    strictness=no-result
    guest_exit=null
  fi

  if cmp -s "$cell_dir/run1.stdout" "$cell_dir/run2.stdout"; then
    content_equal=true
  else
    content_equal=false
  fi
  stdout_sha=$(sha256sum "$cell_dir/run1.stdout" | cut -d' ' -f1)
  stdout_bytes=$(wc -c <"$cell_dir/run1.stdout")
  if [[ $host_readable == true && $stdout_sha == "$host_sha" ]]; then
    guest_equals_host=true
  else
    guest_equals_host=false
  fi

  if [[ $host_readable != true ]]; then
    status=HOST_UNAVAILABLE
  elif [[ $verify_rc -eq 0 && $verified == true && $run1_rc -eq 0 && $run2_rc -eq 0 && $content_equal == true ]]; then
    status=PASS_STRIPPED_VERIFY_EXACT_STDOUT
  elif [[ $run1_rc -eq 124 || $run2_rc -eq 124 || $verify_rc -eq 124 ]]; then
    status=TIMEOUT
  elif [[ $content_equal != true ]]; then
    status=CONTENT_DIVERGED
  else
    status=VERIFY_FAILED
  fi

  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$cell_id" "$kind" "$path" "$host_readable" "$verify_rc" "$verified" \
    "$bitwise" "$strictness" "$guest_exit" "$run1_rc" "$run2_rc" \
    "$content_equal" "$stdout_sha" "$stdout_bytes" "$host_sha" "$host_bytes" \
    "$guest_equals_host" "$status" >>"$results"
  printf '%s %s %s\n' "$cell_id" "$kind" "$status"
done <"$cases"

ledger="$output_dir/full-scorecard-verification-ledger.jsonl"
: >"$ledger"
tail -n +2 "$results" | while IFS=, read -r \
  cell_id kind path host_readable verify_rc verified bitwise strictness guest_exit \
  run1_rc run2_rc content_equal stdout_sha stdout_bytes host_sha host_bytes \
  guest_equals_host status; do
  verify_record="$output_dir/cells/$cell_id-$kind/verify.json"
  jq -cn \
    --arg cell_id "$cell_id" --arg kind "$kind" --arg path "$path" \
    --arg hermit_version "$hermit_version" --arg hermit_sha256 "$hermit_sha256" \
    --arg cpu "$cpu" --arg timeout_seconds "$timeout_seconds" \
    --arg libunwind_dir "$libunwind_dir" --arg content_equal "$content_equal" \
    --arg stdout_sha "$stdout_sha" --arg stdout_bytes "$stdout_bytes" \
    --arg host_readable "$host_readable" --arg host_sha "$host_sha" \
    --arg host_bytes "$host_bytes" --arg guest_equals_host "$guest_equals_host" \
    --arg status "$status" --slurpfile verification "$verify_record" \
    '{cell_id:($cell_id|tonumber),kind:$kind,path:$path,status:$status,
      provenance:{hermit_version:$hermit_version,binary_sha256:$hermit_sha256,
        backend:"ptrace",flags:["--strict","--verify","--max-timeslice=disabled"],
        cpu:($cpu|tonumber),timeout_seconds:($timeout_seconds|tonumber),
        libunwind_dir:$libunwind_dir},verification:$verification[0],
      exact_stdout:{independent_runs:2,equal:($content_equal=="true"),
        sha256:$stdout_sha,bytes:($stdout_bytes|tonumber)},
      host_control:{readable:($host_readable=="true"),sha256:$host_sha,
        bytes:($host_bytes|tonumber),equals_guest:($guest_equals_host=="true")}}' \
    >>"$ledger"
done
