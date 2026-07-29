#!/usr/bin/env bash
set -Euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/adversarial-check.sh [quick|full|self-test]

Run bounded concurrency, crash-stop, parser, and dynamic-analysis gates.

  quick      Developer adversarial gate, bounded to 20 minutes by default.
  full       Extended state-space and fuzz gate, bounded to 90 minutes by default.
  self-test  Exercise the runner's fail-closed evidence parser only.

Environment:
  ADVERSARIAL_TOTAL_TIMEOUT    Whole-run deadline in seconds.
  ADVERSARIAL_COMMAND_TIMEOUT  Default per-command deadline in seconds.
  ADVERSARIAL_LONG_TIMEOUT     Model/fuzz/dynamic per-command deadline in seconds.
  ADVERSARIAL_NIGHTLY          Pinned nightly with Miri, rust-src, and sanitizers.
  ADVERSARIAL_FUZZ_SECONDS     Per-target libFuzzer time budget.
EOF
}

mode=${1:-quick}
case "$mode" in
  quick | full | self-test) ;;
  -h | --help)
    usage
    exit 0
    ;;
  *)
    echo "unknown adversarial-check mode: $mode" >&2
    usage >&2
    exit 2
    ;;
esac
if (($# > 1)); then
  usage >&2
  exit 2
fi

script_dir=${BASH_SOURCE[0]%/*}
root=$(cd "$script_dir/.." && pwd -P)
cd "$root"
manifest="$root/scripts/adversarial-gates.tsv"

readlink_bootstrap=$(type -P readlink) || {
  echo "UNAVAILABLE: required tool readlink is not installed" >&2
  exit 2
}
if [[ $readlink_bootstrap != /* || ! -f $readlink_bootstrap || ! -x $readlink_bootstrap ]]; then
  echo "UNAVAILABLE: readlink did not resolve to an absolute executable file" >&2
  exit 2
fi
READLINK_BIN=$("$readlink_bootstrap" -f -- "$readlink_bootstrap")

resolve_required_tool() {
  local variable=$1
  local name=$2
  local path
  path=$(type -P "$name") || {
    echo "UNAVAILABLE: required tool $name is not installed" >&2
    exit 2
  }
  if [[ $path != /* || ! -f $path || ! -x $path ]]; then
    echo "UNAVAILABLE: $name did not resolve to an absolute executable file: $path" >&2
    exit 2
  fi
  local canonical
  canonical=$("$READLINK_BIN" -f -- "$path")
  if [[ $canonical != /* || ! -f $canonical || ! -x $canonical ]]; then
    echo "UNAVAILABLE: canonical $name path is not an executable file: $canonical" >&2
    exit 2
  fi
  printf -v "$variable" '%s' "$path"
}

resolve_optional_tool() {
  local variable=$1
  local name=$2
  local path
  path=$(type -P "$name" 2>/dev/null || true)
  if [[ -n $path && $path == /* && -f $path && -x $path ]]; then
    printf -v "$variable" '%s' "$path"
  else
    printf -v "$variable" '%s' ''
  fi
}

RUSTC_BIN=
resolve_required_tool CARGO_BIN cargo
resolve_required_tool CAT_BIN cat
resolve_required_tool ENV_BIN env
resolve_required_tool MKTEMP_BIN mktemp
resolve_required_tool RM_BIN rm
resolve_required_tool READLINK_BIN readlink
resolve_required_tool RUSTC_BIN rustc
resolve_required_tool SHA256_BIN sha256sum
resolve_required_tool TIMEOUT_BIN timeout
resolve_required_tool UNAME_BIN uname
resolve_optional_tool CARGO_FUZZ_BIN cargo-fuzz
resolve_optional_tool RUSTUP_BIN rustup

if [[ ! -f $manifest ]]; then
  echo "FAIL: missing adversarial gate manifest: $manifest" >&2
  exit 1
fi

declare -A manifest_rows=()
validate_manifest() {
  local line_number=0
  local id kind evidence extra
  while IFS='|' read -r id kind evidence extra || [[ -n ${id:-} ]]; do
    line_number=$((line_number + 1))
    [[ -z $id || $id == \#* ]] && continue
    if [[ -n ${extra:-} || -z $kind || -z $evidence ]]; then
      echo "FAIL: malformed gate manifest row $line_number" >&2
      exit 1
    fi
    case "$kind" in
      test | command | artifact | fuzz | script) ;;
      *)
        echo "FAIL: unknown evidence kind '$kind' at manifest row $line_number" >&2
        exit 1
        ;;
    esac
    local key="$id|$kind|$evidence"
    if [[ -n ${manifest_rows[$key]+set} ]]; then
      echo "FAIL: duplicate gate manifest row: $key" >&2
      exit 1
    fi
    manifest_rows[$key]=1
  done <"$manifest"
}
validate_manifest

declare -A TOOL_PATHS=()
declare -A TOOL_DIGESTS=()
declare -A TOOL_INVOCATIONS=()
tool_digest() {
  local name=$1
  local invocation=$2
  local path
  path=$("$READLINK_BIN" -f -- "$invocation")
  local digest
  read -r digest _ < <("$SHA256_BIN" "$path")
  TOOL_INVOCATIONS[$name]=$invocation
  TOOL_PATHS[$name]=$path
  TOOL_DIGESTS[$name]=$digest
  printf '  %-12s %s -> %s sha256=%s\n' "$name" "$invocation" "$path" "$digest"
}

echo "validation tool attestation"
tool_digest sha256sum "$SHA256_BIN"
tool_digest cargo "$CARGO_BIN"
tool_digest cat "$CAT_BIN"
tool_digest mktemp "$MKTEMP_BIN"
tool_digest rm "$RM_BIN"
tool_digest rustc "$RUSTC_BIN"
tool_digest timeout "$TIMEOUT_BIN"
tool_digest env "$ENV_BIN"
tool_digest uname "$UNAME_BIN"
tool_digest readlink "$READLINK_BIN"
if [[ -n $RUSTUP_BIN ]]; then
  tool_digest rustup "$RUSTUP_BIN"
fi
if [[ -n $CARGO_FUZZ_BIN ]]; then
  tool_digest cargo-fuzz "$CARGO_FUZZ_BIN"
fi
manifest_digest=$({ "$SHA256_BIN" "$manifest"; } | { read -r digest _; printf '%s' "$digest"; })
echo "  gate-manifest $manifest sha256=$manifest_digest"

revalidate_attestation() {
  local name invocation path digest
  for name in "${!TOOL_PATHS[@]}"; do
    invocation=${TOOL_INVOCATIONS[$name]}
    path=${TOOL_PATHS[$name]}
    if [[ ! -f $invocation || ! -x $invocation \
      || $("$READLINK_BIN" -f -- "$invocation") != "$path" \
      || ! -f $path || ! -x $path ]]; then
      echo "FAIL: validation tool changed identity during run: $name ($invocation -> $path)" >&2
      return 1
    fi
    read -r digest _ < <("$SHA256_BIN" "$path")
    if [[ $digest != "${TOOL_DIGESTS[$name]}" ]]; then
      echo "FAIL: validation tool digest changed during run: $name ($path)" >&2
      return 1
    fi
  done
  read -r digest _ < <("$SHA256_BIN" "$manifest")
  if [[ $digest != "$manifest_digest" ]]; then
    echo "FAIL: adversarial gate manifest changed during run" >&2
    return 1
  fi
  echo "ATTESTED: validation tools and gate manifest unchanged at end of run"
}

declare -a GATE_EVIDENCE=()
GATE_KIND=
load_gate_evidence() {
  local wanted=$1
  GATE_KIND=
  GATE_EVIDENCE=()
  local id kind evidence extra
  while IFS='|' read -r id kind evidence extra || [[ -n ${id:-} ]]; do
    [[ -z $id || $id == \#* || $id != "$wanted" ]] && continue
    if [[ -n $GATE_KIND && $GATE_KIND != "$kind" ]]; then
      echo "FAIL: gate $wanted mixes evidence kinds" >&2
      exit 1
    fi
    GATE_KIND=$kind
    GATE_EVIDENCE+=("$evidence")
  done <"$manifest"
  if [[ -z $GATE_KIND || ${#GATE_EVIDENCE[@]} == 0 ]]; then
    echo "FAIL: gate $wanted has no manifest evidence" >&2
    exit 1
  fi
}

attest_test_log() {
  local gate_id=$1
  local log=$2
  load_gate_evidence "$gate_id"
  if [[ $GATE_KIND != test ]]; then
    echo "FAIL: gate $gate_id is not a test evidence gate" >&2
    return 1
  fi

  local -A expected=()
  local -A observed=()
  local name
  for name in "${GATE_EVIDENCE[@]}"; do
    expected[$name]=1
  done

  local test_re='^test[[:space:]]+([^[:space:]]+)[[:space:]]+\.\.\.[[:space:]]+(ok|FAILED|ignored)$'
  local summary_re='^test result: ok\. ([0-9]+) passed; 0 failed; 0 ignored; 0 measured; [0-9]+ filtered out;'
  local line status
  local observed_total=0
  local summary_count=0
  local summary_passed=-1
  while IFS= read -r line || [[ -n $line ]]; do
    if [[ $line =~ $test_re ]]; then
      name=${BASH_REMATCH[1]}
      status=${BASH_REMATCH[2]}
      if [[ -z ${expected[$name]+set} ]]; then
        echo "FAIL: gate $gate_id ran unexpected test $name" >&2
        return 1
      fi
      if [[ -n ${observed[$name]+set} ]]; then
        echo "FAIL: gate $gate_id ran duplicate test $name" >&2
        return 1
      fi
      if [[ $status != ok ]]; then
        echo "FAIL: gate $gate_id reported $status for $name" >&2
        return 1
      fi
      observed[$name]=1
      observed_total=$((observed_total + 1))
    elif [[ $line =~ $summary_re ]]; then
      summary_count=$((summary_count + 1))
      summary_passed=${BASH_REMATCH[1]}
    fi
  done <"$log"

  for name in "${GATE_EVIDENCE[@]}"; do
    if [[ -z ${observed[$name]+set} ]]; then
      echo "FAIL: gate $gate_id did not execute expected test $name" >&2
      return 1
    fi
  done
  if ((summary_count != 1 || summary_passed != ${#GATE_EVIDENCE[@]})); then
    echo "FAIL: gate $gate_id lacks one exact ${#GATE_EVIDENCE[@]}-test success summary" >&2
    return 1
  fi
  if ((observed_total != ${#GATE_EVIDENCE[@]})); then
    echo "FAIL: gate $gate_id observed $observed_total tests, expected ${#GATE_EVIDENCE[@]}" >&2
    return 1
  fi
  echo "ATTESTED: gate=$gate_id tests=$observed_total manifest=$manifest_digest"
}

tmpdir=$("$MKTEMP_BIN" -d)
trap '"$RM_BIN" -rf "$tmpdir"' EXIT

run_self_test() {
  local good="$tmpdir/good.log"
  local zero="$tmpdir/zero.log"
  local duplicate="$tmpdir/duplicate.log"
  local extra="$tmpdir/extra.log"
  printf '%s\n' \
    'running 1 test' \
    'test sentinel::executes ... ok' \
    'test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s' \
    >"$good"
  printf '%s\n' \
    'running 0 tests' \
    'test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 1 filtered out; finished in 0.00s' \
    >"$zero"
  printf '%s\n' \
    'test sentinel::executes ... ok' \
    'test sentinel::executes ... ok' \
    'test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s' \
    >"$duplicate"
  printf '%s\n' \
    'test sentinel::executes ... ok' \
    'test sentinel::unexpected ... ok' \
    'test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s' \
    >"$extra"

  attest_test_log self-attestation "$good" >/dev/null
  if attest_test_log self-attestation "$zero" >/dev/null 2>&1; then
    echo "FAIL: runner accepted zero tests" >&2
    exit 1
  fi
  if attest_test_log self-attestation "$duplicate" >/dev/null 2>&1; then
    echo "FAIL: runner accepted duplicate evidence" >&2
    exit 1
  fi
  if attest_test_log self-attestation "$extra" >/dev/null 2>&1; then
    echo "FAIL: runner accepted unexpected evidence" >&2
    exit 1
  fi

  local canary="$tmpdir/cargo-version.log"
  "$TIMEOUT_BIN" 15s "$CARGO_BIN" --version >"$canary" 2>&1
  local version
  IFS= read -r version <"$canary"
  if [[ $version != cargo\ * ]]; then
    echo "FAIL: absolute cargo canary returned unexpected output: $version" >&2
    exit 1
  fi
  revalidate_attestation >/dev/null
  echo "PASS validation-runner self-test: zero, duplicate, and unexpected tests rejected"
  echo "PASS validation-runner self-test: absolute cargo/timeout paths bypass shell functions"
}

if [[ $mode == self-test ]]; then
  run_self_test
  exit 0
fi

if [[ $mode == quick ]]; then
  default_total_timeout=1200
  default_command_timeout=120
  default_long_timeout=300
  loom_permutations=10000
  loom_preemptions=2
  default_fuzz_seconds=15
else
  default_total_timeout=5400
  default_command_timeout=600
  default_long_timeout=1200
  loom_permutations=100000
  loom_preemptions=3
  default_fuzz_seconds=120
fi

total_timeout=${ADVERSARIAL_TOTAL_TIMEOUT:-$default_total_timeout}
command_timeout=${ADVERSARIAL_COMMAND_TIMEOUT:-$default_command_timeout}
long_timeout=${ADVERSARIAL_LONG_TIMEOUT:-$default_long_timeout}
nightly=${ADVERSARIAL_NIGHTLY:-nightly-2026-06-01}
fuzz_seconds=${ADVERSARIAL_FUZZ_SECONDS:-$default_fuzz_seconds}

require_positive_integer() {
  local name=$1
  local value=$2
  if [[ ! $value =~ ^[1-9][0-9]*$ ]]; then
    echo "$name must be a positive integer, got: $value" >&2
    exit 2
  fi
}

require_positive_integer ADVERSARIAL_TOTAL_TIMEOUT "$total_timeout"
require_positive_integer ADVERSARIAL_COMMAND_TIMEOUT "$command_timeout"
require_positive_integer ADVERSARIAL_LONG_TIMEOUT "$long_timeout"
require_positive_integer ADVERSARIAL_FUZZ_SECONDS "$fuzz_seconds"

unset BASH_ENV ENV CDPATH
export CARGO_TERM_COLOR=never

started=$SECONDS
gate_number=0
pass_count=0
fail_count=0
unavailable_count=0
unsupported_count=0
timeout_count=0
corpus_root="$tmpdir/corpus"

print_command() {
  printf '  '
  printf '%q ' "$@"
  printf '\n'
}

limit_for() {
  local class=$1
  local configured=$command_timeout
  if [[ $class == long ]]; then
    configured=$long_timeout
  fi
  local elapsed=$((SECONDS - started))
  local remaining=$((total_timeout - elapsed))
  if ((remaining <= 0)); then
    echo "FAIL: whole-run deadline (${total_timeout}s) exhausted" >&2
    exit 124
  fi
  if ((configured < remaining)); then
    printf '%s\n' "$configured"
  else
    printf '%s\n' "$remaining"
  fi
}

RUN_LOG=
RUN_STATUS=0
run_logged_gate() {
  local gate_id=$1
  local expected_kind=$2
  local class=$3
  local label=$4
  shift 4
  load_gate_evidence "$gate_id"
  if [[ $GATE_KIND != "$expected_kind" ]]; then
    echo "FAIL: gate $gate_id expected $expected_kind evidence, manifest has $GATE_KIND" >&2
    exit 1
  fi
  gate_number=$((gate_number + 1))
  local limit
  limit=$(limit_for "$class")
  printf '\n[%02d] %s (id=%s timeout=%ss)\n' "$gate_number" "$label" "$gate_id" "$limit"
  print_command "$@"
  RUN_LOG="$tmpdir/gate-${gate_number}.log"
  set +e
  "$TIMEOUT_BIN" --signal=TERM --kill-after=10s "${limit}s" "$@" >"$RUN_LOG" 2>&1
  RUN_STATUS=$?
  set -e
  "$CAT_BIN" "$RUN_LOG"
}

command_succeeded() {
  local label=$1
  if ((RUN_STATUS == 0)); then
    return 0
  fi
  if ((RUN_STATUS == 124)); then
    echo "FAIL: $label (deadline expired)" >&2
    timeout_count=$((timeout_count + 1))
  elif ((RUN_STATUS == 137)); then
    echo "FAIL: $label (SIGKILL status 137; cause may be OOM, supervisor, or timeout escalation)" >&2
    fail_count=$((fail_count + 1))
  else
    echo "FAIL: $label (exit $RUN_STATUS)" >&2
    fail_count=$((fail_count + 1))
  fi
  return 1
}

mark_pass() {
  local label=$1
  echo "PASS: $label"
  pass_count=$((pass_count + 1))
}

run_command_gate() {
  local gate_id=$1
  local class=$2
  local label=$3
  shift 3
  run_logged_gate "$gate_id" command "$class" "$label" "$@"
  if command_succeeded "$label"; then
    echo "ATTESTED: gate=$gate_id command-binary-sha256 manifest=$manifest_digest"
    mark_pass "$label"
  fi
}

run_test_gate() {
  local gate_id=$1
  local class=$2
  local label=$3
  shift 3
  run_logged_gate "$gate_id" test "$class" "$label" "$@"
  if ! command_succeeded "$label"; then
    return
  fi
  if attest_test_log "$gate_id" "$RUN_LOG"; then
    mark_pass "$label"
  else
    echo "FAIL: $label (test evidence did not match manifest)" >&2
    fail_count=$((fail_count + 1))
  fi
}

run_artifact_gate() {
  local gate_id=$1
  local class=$2
  local label=$3
  shift 3
  run_logged_gate "$gate_id" artifact "$class" "$label" "$@"
  if ! command_succeeded "$label"; then
    return
  fi
  load_gate_evidence "$gate_id"
  local missing=0
  local artifact
  for artifact in "${GATE_EVIDENCE[@]}"; do
    if [[ ! -f "$corpus_root/$artifact" ]]; then
      echo "FAIL: gate $gate_id did not generate $artifact" >&2
      missing=1
    fi
  done
  shopt -s nullglob
  local actual=("$corpus_root"/*/*)
  shopt -u nullglob
  if ((${#actual[@]} != ${#GATE_EVIDENCE[@]})); then
    echo "FAIL: gate $gate_id generated ${#actual[@]} artifacts, expected ${#GATE_EVIDENCE[@]}" >&2
    missing=1
  fi
  if ((missing != 0)); then
    fail_count=$((fail_count + 1))
    return
  fi
  echo "ATTESTED: gate=$gate_id artifacts=${#GATE_EVIDENCE[@]} manifest=$manifest_digest"
  mark_pass "$label"
}

run_fuzz_gate() {
  local gate_id=$1
  local label=$2
  shift 2
  run_logged_gate "$gate_id" fuzz long "$label" "$@"
  if ! command_succeeded "$label"; then
    return
  fi
  local saw_done=0
  local line
  while IFS= read -r line || [[ -n $line ]]; do
    if [[ $line == *DONE* ]]; then
      saw_done=1
    fi
  done <"$RUN_LOG"
  if ((saw_done == 0)); then
    echo "FAIL: $label exited zero without libFuzzer DONE evidence" >&2
    fail_count=$((fail_count + 1))
    return
  fi
  echo "ATTESTED: gate=$gate_id libfuzzer-completion manifest=$manifest_digest"
  mark_pass "$label"
}

run_script_gate() {
  local gate_id=$1
  local class=$2
  local label=$3
  shift 3
  run_logged_gate "$gate_id" script "$class" "$label" "$@"
  if ! command_succeeded "$label"; then
    return
  fi
  load_gate_evidence "$gate_id"
  local -A observed=()
  local line marker
  while IFS= read -r line || [[ -n $line ]]; do
    for marker in "${GATE_EVIDENCE[@]}"; do
      if [[ $line == "$marker" ]]; then
        if [[ -n ${observed[$marker]+set} ]]; then
          echo "FAIL: gate $gate_id emitted duplicate marker: $marker" >&2
          fail_count=$((fail_count + 1))
          return
        fi
        observed[$marker]=1
      fi
    done
  done <"$RUN_LOG"
  for marker in "${GATE_EVIDENCE[@]}"; do
    if [[ -z ${observed[$marker]+set} ]]; then
      echo "FAIL: gate $gate_id omitted expected marker: $marker" >&2
      fail_count=$((fail_count + 1))
      return
    fi
  done
  echo "ATTESTED: gate=$gate_id markers=${#GATE_EVIDENCE[@]} manifest=$manifest_digest"
  mark_pass "$label"
}

record_unavailable() {
  local gate_id=$1
  local label=$2
  local reason=$3
  load_gate_evidence "$gate_id"
  gate_number=$((gate_number + 1))
  printf '\n[%02d] %s (id=%s)\n' "$gate_number" "$label" "$gate_id"
  echo "UNAVAILABLE: $label ($reason)"
  unavailable_count=$((unavailable_count + 1))
}

record_unsupported() {
  local gate_id=$1
  local label=$2
  local reason=$3
  load_gate_evidence "$gate_id"
  gate_number=$((gate_number + 1))
  printf '\n[%02d] %s (id=%s)\n' "$gate_number" "$label" "$gate_id"
  echo "UNSUPPORTED: $label ($reason)"
  unsupported_count=$((unsupported_count + 1))
}

echo "shmem-pod adversarial check"
echo "  mode: $mode"
echo "  root: $root"
echo "  whole-run timeout: ${total_timeout}s"
echo "  nightly: $nightly"
echo "  Loom: permutations=$loom_permutations preemptions=$loom_preemptions"
echo "  fuzz budget: ${fuzz_seconds}s per target"

atomic64=0
while IFS= read -r cfg; do
  if [[ $cfg == 'target_has_atomic="64"' ]]; then
    atomic64=1
  fi
done < <("$RUSTC_BIN" --print cfg)
linux=0
if [[ $("$UNAME_BIN" -s) == Linux ]]; then
  linux=1
fi
x86_64_linux=0
if [[ $linux == 1 && $("$UNAME_BIN" -m) == x86_64 ]]; then
  x86_64_linux=1
fi

if [[ $atomic64 == 1 ]]; then
  run_test_gate loom long "Loom production protocol models" \
    "$ENV_BIN" LOOM_MAX_PERMUTATIONS="$loom_permutations" \
    LOOM_MAX_PREEMPTIONS="$loom_preemptions" RUSTFLAGS="--cfg shmem_pod_loom" \
    "$CARGO_BIN" test --locked -p shmem-pod --lib model_checks \
    --no-default-features --features linux-futex -- --test-threads=1 --color=never
else
  record_unsupported loom "Loom production protocol models" "target lacks 64-bit atomics"
fi

if [[ $linux == 1 && $atomic64 == 1 ]]; then
  run_test_gate fault-cuts long "serial production fault cuts" \
    "$CARGO_BIN" test --locked -p shmem-pod --lib fault_checks \
    --no-default-features --features linux-futex -- --test-threads=1 --color=never
  run_test_gate mapping-crash normal "killed mapping initializer and participant" \
    "$CARGO_BIN" test --locked -p shmem-pod --test mapping_lifecycle \
    killed_initializer_and_admitted_process_fail_stuck_until_supervisor_poison \
    -- --exact --test-threads=1 --color=never
  run_test_gate allocator-crash normal "killed allocator transaction" \
    "$CARGO_BIN" test --locked -p shmem-pod --test reloc_allocator \
    killed_transaction_stays_bounded_until_supervisor_poison \
    -- --exact --test-threads=1 --color=never
  run_test_gate migration-copying normal "killed migration owner in Copying" \
    "$CARGO_BIN" test --locked -p shmem-pod --test migration \
    killed_migrator_leaves_copying_state_and_never_steals_transaction \
    -- --exact --test-threads=1 --color=never
  run_test_gate migration-target-ready normal "killed migration owner in TargetReady" \
    "$CARGO_BIN" test --locked -p shmem-pod --test migration \
    killed_target_ready_migrator_leaves_source_authoritative \
    -- --exact --test-threads=1 --color=never
  run_test_gate migration-committed normal "killed migration owner after commit" \
    "$CARGO_BIN" test --locked -p shmem-pod --test migration \
    killed_post_commit_migrator_leaves_recovery_backing_available \
    -- --exact --test-threads=1 --color=never
else
  record_unsupported fault-cuts "serial production fault cuts" "requires Linux and 64-bit atomics"
  record_unsupported mapping-crash "killed mapping initializer and participant" "requires Linux and 64-bit atomics"
  record_unsupported allocator-crash "killed allocator transaction" "requires Linux and 64-bit atomics"
  record_unsupported migration-copying "killed migration owner in Copying" "requires Linux and 64-bit atomics"
  record_unsupported migration-target-ready "killed migration owner in TargetReady" "requires Linux and 64-bit atomics"
  record_unsupported migration-committed "killed migration owner after commit" "requires Linux and 64-bit atomics"
fi

run_command_gate fuzz-build long "compile locked standalone fuzz workspace" \
  "$CARGO_BIN" check --locked --manifest-path fuzz/Cargo.toml --bins
run_artifact_gate corpus normal "generate deterministic fuzz corpus" \
  "$CARGO_BIN" run --locked --manifest-path fuzz/Cargo.toml \
  --bin generate-corpus -- "$corpus_root"

nightly_available=0
if [[ -n $RUSTUP_BIN ]] \
  && "$TIMEOUT_BIN" 30s "$RUSTUP_BIN" run "$nightly" rustc --version >/dev/null 2>&1; then
  nightly_available=1
fi
cargo_fuzz_available=0
if [[ -n $CARGO_FUZZ_BIN ]] \
  && "$TIMEOUT_BIN" 30s "$CARGO_BIN" fuzz --version >/dev/null 2>&1; then
  cargo_fuzz_available=1
fi

fuzz_targets=(
  image_header
  pod_artifact
  bootstrap_context
  layout_descriptor
  offset_resolution
)
for target in "${fuzz_targets[@]}"; do
  gate_id="fuzz-${target//_/-}"
  if [[ $nightly_available == 0 ]]; then
    record_unavailable "$gate_id" "fuzz target $target" "toolchain $nightly is not installed"
  elif [[ $cargo_fuzz_available == 0 ]]; then
    record_unavailable "$gate_id" "fuzz target $target" "cargo-fuzz is not installed"
  else
    run_fuzz_gate "$gate_id" "fuzz target $target" \
      "$ENV_BIN" CARGO_TARGET_DIR=target/adversarial/fuzz \
      "$CARGO_BIN" +"$nightly" fuzz run "$target" "$corpus_root/$target" -- \
      -max_total_time="$fuzz_seconds" -max_len=1048576 -timeout=5
  fi
done

miri_available=0
rust_src_available=0
if [[ $nightly_available == 1 ]]; then
  if "$TIMEOUT_BIN" 30s "$CARGO_BIN" +"$nightly" miri --version >/dev/null 2>&1; then
    miri_available=1
  fi
  while IFS= read -r component; do
    if [[ $component == rust-src || $component == rust-src-* ]]; then
      rust_src_available=1
    fi
  done < <("$RUSTUP_BIN" component list --toolchain "$nightly" --installed 2>/dev/null || true)
fi

if [[ $nightly_available == 0 ]]; then
  record_unavailable miri-pure "Miri pure parser and offset target" "toolchain $nightly is not installed"
elif [[ $miri_available == 0 ]]; then
  record_unavailable miri-pure "Miri pure parser and offset target" "Miri is not installed for $nightly"
else
  run_test_gate miri-pure long "Miri pure parser and offset target" \
    "$ENV_BIN" CARGO_TARGET_DIR=target/adversarial/miri \
    MIRIFLAGS="-Zmiri-strict-provenance -Zmiri-symbolic-alignment-check" \
    "$CARGO_BIN" +"$nightly" miri test --locked -p shmem-pod \
    --test miri_pure --no-default-features -- --test-threads=1 --color=never
fi

if [[ $x86_64_linux == 0 ]]; then
  record_unsupported asan "AddressSanitizer thread subset" "validated runner requires x86-64 Linux"
  record_unsupported tsan "ThreadSanitizer thread subset" "validated runner requires x86-64 Linux"
elif [[ $nightly_available == 0 ]]; then
  record_unavailable asan "AddressSanitizer thread subset" "toolchain $nightly is not installed"
  record_unavailable tsan "ThreadSanitizer thread subset" "toolchain $nightly is not installed"
elif [[ $rust_src_available == 0 ]]; then
  record_unavailable asan "AddressSanitizer thread subset" "rust-src is not installed for $nightly"
  record_unavailable tsan "ThreadSanitizer thread subset" "rust-src is not installed for $nightly"
else
  run_test_gate asan long "AddressSanitizer thread subset" \
    "$ENV_BIN" CARGO_TARGET_DIR=target/adversarial/asan \
    RUSTFLAGS="-Zsanitizer=address" RUSTDOCFLAGS="-Zsanitizer=address" \
    ASAN_OPTIONS="detect_leaks=0:halt_on_error=1" \
    "$CARGO_BIN" +"$nightly" test -Zbuild-std --locked \
    --target x86_64-unknown-linux-gnu -p shmem-pod \
    --test dynamic_analysis --no-default-features -- --test-threads=1 --color=never
  run_test_gate tsan long "ThreadSanitizer thread subset" \
    "$ENV_BIN" CARGO_TARGET_DIR=target/adversarial/tsan \
    RUSTFLAGS="-Zsanitizer=thread" RUSTDOCFLAGS="-Zsanitizer=thread" \
    TSAN_OPTIONS="halt_on_error=1:exitcode=66" \
    "$CARGO_BIN" +"$nightly" test -Zbuild-std --locked \
    --target x86_64-unknown-linux-gnu -p shmem-pod \
    --test dynamic_analysis --no-default-features -- --test-threads=1 --color=never
fi

if [[ $mode == full ]]; then
  if [[ $x86_64_linux == 1 ]]; then
    run_script_gate connector-recovery long "connector fail-closed recovery suite" \
      "$root/scripts/test-connector-failures.sh"
  else
    record_unsupported connector-recovery "connector fail-closed recovery suite" "requires x86-64 Linux"
  fi
fi

elapsed=$((SECONDS - started))
echo
if ! revalidate_attestation; then
  echo "FAIL: adversarial-check attestation changed during execution"
  exit 1
fi
echo "adversarial-check summary: mode=$mode gates=$gate_number pass=$pass_count fail=$fail_count unavailable=$unavailable_count unsupported=$unsupported_count timeouts=$timeout_count elapsed=${elapsed}s"
if ((timeout_count != 0)); then
  echo "FAIL: adversarial-check timed out"
  exit 124
fi
if ((fail_count != 0)); then
  echo "FAIL: adversarial-check found failing gates"
  exit 1
fi
if ((unavailable_count != 0 || unsupported_count != 0)); then
  if ((unavailable_count != 0)); then
    echo "UNAVAILABLE: adversarial-check has selected gates without tooling"
  else
    echo "UNSUPPORTED: adversarial-check has selected gates outside this platform"
  fi
  exit 2
fi
echo "PASS: adversarial-check mode=$mode gates=$gate_number manifest=$manifest_digest"
