#!/bin/bash -p
if [[ $- != *p* ]]; then
  printf '%s\n' 'adversarial-check.sh must be executed directly so /bin/bash -p can protect startup' >&2
  builtin exit 2
fi
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
  SHMEM_POD_RUSTUP_BIN         Trusted absolute rustup executable override.
  SHMEM_POD_CURRENT_TOOLCHAIN  Current rustup toolchain name (default: stable).
  SHMEM_POD_CARGO_FUZZ_BIN     Trusted absolute cargo-fuzz executable override.
  SHMEM_POD_CARGO_HOME         Source for registry/git caches, never active config.
  SHMEM_POD_RUSTUP_HOME        Absolute rustup toolchain store override.
EOF
}

mode=${1:-quick}
case "$mode" in
  quick | full | self-test | __tool-probe) ;;
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
readonly root
readonly manifest="$root/scripts/adversarial-gates.tsv"

# These are the bootstrap trust root. They are named literally instead of
# discovered through PATH, and must remain root-owned and non-writable by group
# or other users. Rust toolchains are separately resolved and attested below.
readonly CAT_BIN=/usr/bin/cat
readonly ENV_BIN=/usr/bin/env
readonly GETENT_BIN=/usr/bin/getent
readonly GIT_BIN=/usr/bin/git
readonly CHMOD_BIN=/usr/bin/chmod
readonly LN_BIN=/usr/bin/ln
readonly MKDIR_BIN=/usr/bin/mkdir
readonly MKTEMP_BIN=/usr/bin/mktemp
readonly READLINK_BIN=/usr/bin/readlink
readonly RM_BIN=/usr/bin/rm
readonly SHA256_BIN=/usr/bin/sha256sum
readonly STAT_BIN=/usr/bin/stat
readonly TIMEOUT_BIN=/usr/bin/timeout
readonly UNAME_BIN=/usr/bin/uname
readonly CONTROL_PATH=/usr/bin:/bin

verify_root_control_tool() {
  local path=$1
  local owner mode mode_value canonical
  canonical=$("$READLINK_BIN" -f -- "$path") || return 1
  [[ $canonical == /* && -f $canonical && -x $canonical ]] || return 1
  read -r owner mode < <("$STAT_BIN" -Lc '%u %a' -- "$canonical") || return 1
  mode_value=$((8#$mode))
  if [[ $owner != 0 ]] || ((mode_value & 8#22)); then
    return 1
  fi
}

for control_tool in \
  "$CAT_BIN" "$CHMOD_BIN" "$ENV_BIN" "$GETENT_BIN" "$GIT_BIN" \
  "$LN_BIN" "$MKDIR_BIN" "$MKTEMP_BIN" \
  "$READLINK_BIN" "$RM_BIN" "$SHA256_BIN" "$STAT_BIN" "$TIMEOUT_BIN" \
  "$UNAME_BIN"; do
  if ! verify_root_control_tool "$control_tool"; then
    echo "UNAVAILABLE: untrusted fixed control tool: $control_tool" >&2
    exit 2
  fi
done

passwd_record=$("$GETENT_BIN" passwd "$EUID") || {
  echo "UNAVAILABLE: cannot resolve the current account from the system user database" >&2
  exit 2
}
IFS=: read -r account_name _ _ _ _ account_home _ <<<"$passwd_record"
if [[ -z $account_name || $account_home != /* || ! -d $account_home ]]; then
  echo "UNAVAILABLE: current account has no trustworthy absolute home directory" >&2
  exit 2
fi
readonly account_name account_home
readonly cargo_home=${SHMEM_POD_CARGO_HOME:-"$account_home/.cargo"}
readonly rustup_home=${SHMEM_POD_RUSTUP_HOME:-"$account_home/.rustup"}
if [[ $cargo_home != /* || $rustup_home != /* ]]; then
  echo "UNAVAILABLE: SHMEM_POD_CARGO_HOME and SHMEM_POD_RUSTUP_HOME must be absolute" >&2
  exit 2
fi

canonical_trusted_executable() {
  local label=$1
  local requested=$2
  local canonical owner mode mode_value component
  [[ $requested == /* ]] || {
    echo "UNAVAILABLE: $label must be an absolute path: $requested" >&2
    return 2
  }
  canonical=$("$READLINK_BIN" -f -- "$requested") || {
    echo "UNAVAILABLE: cannot canonicalize $label: $requested" >&2
    return 2
  }
  [[ $canonical == /* && -f $canonical && -x $canonical ]] || {
    echo "UNAVAILABLE: $label is not an executable regular file: $canonical" >&2
    return 2
  }
  component=$canonical
  while :; do
    read -r owner mode < <("$STAT_BIN" -Lc '%u %a' -- "$component") || return 2
    mode_value=$((8#$mode))
    if [[ $owner != 0 && $owner != "$EUID" ]] || ((mode_value & 8#22)); then
      echo "UNAVAILABLE: $label has an untrusted path component: $component owner=$owner mode=$mode" >&2
      return 2
    fi
    [[ $component == / ]] && break
    component=${component%/*}
    [[ -n $component ]] || component=/
  done
  printf '%s\n' "$canonical"
}

rustup_request=${SHMEM_POD_RUSTUP_BIN:-"$account_home/.cargo/bin/rustup"}
if [[ -z ${SHMEM_POD_RUSTUP_BIN:-} && ! -x $rustup_request ]]; then
  for rustup_fallback in /usr/bin/rustup /usr/local/bin/rustup; do
    if [[ -x $rustup_fallback ]]; then
      rustup_request=$rustup_fallback
      break
    fi
  done
fi
RUSTUP_BIN=$(canonical_trusted_executable rustup "$rustup_request") || exit $?
readonly RUSTUP_BIN

rustup_exec() {
  "$ENV_BIN" -i HOME="$account_home" USER="$account_name" PATH="$CONTROL_PATH" \
    RUSTUP_HOME="$rustup_home" "$RUSTUP_BIN" "$@"
}

resolve_toolchain_binary() {
  local variable=$1
  local toolchain=$2
  local binary=$3
  local required=$4
  local requested canonical
  requested=$(rustup_exec which "$binary" --toolchain "$toolchain" 2>/dev/null) || {
    if [[ $required == required ]]; then
      echo "UNAVAILABLE: rustup toolchain $toolchain does not provide $binary" >&2
      exit 2
    fi
    printf -v "$variable" '%s' ''
    return
  }
  canonical=$(canonical_trusted_executable "$toolchain/$binary" "$requested") || {
    if [[ $required == required ]]; then
      exit 2
    fi
    printf -v "$variable" '%s' ''
    return
  }
  printf -v "$variable" '%s' "$canonical"
}

readonly current_toolchain=${SHMEM_POD_CURRENT_TOOLCHAIN:-stable}
nightly=${ADVERSARIAL_NIGHTLY:-nightly-2026-06-01}
if [[ ! $current_toolchain =~ ^[A-Za-z0-9._-]+$ || ! $nightly =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "UNAVAILABLE: Rust toolchain names contain invalid characters" >&2
  exit 2
fi
resolve_toolchain_binary CARGO_BIN "$current_toolchain" cargo required
resolve_toolchain_binary RUSTC_BIN "$current_toolchain" rustc required
resolve_toolchain_binary RUSTDOC_BIN "$current_toolchain" rustdoc required
resolve_toolchain_binary NIGHTLY_CARGO_BIN "$nightly" cargo optional
resolve_toolchain_binary NIGHTLY_RUSTC_BIN "$nightly" rustc optional
resolve_toolchain_binary NIGHTLY_RUSTDOC_BIN "$nightly" rustdoc optional
resolve_toolchain_binary CARGO_MIRI_BIN "$nightly" cargo-miri optional
resolve_toolchain_binary MIRI_BIN "$nightly" miri optional
readonly CARGO_BIN RUSTC_BIN RUSTDOC_BIN
readonly NIGHTLY_CARGO_BIN NIGHTLY_RUSTC_BIN NIGHTLY_RUSTDOC_BIN CARGO_MIRI_BIN MIRI_BIN

CARGO_FUZZ_BIN=
cargo_fuzz_request=${SHMEM_POD_CARGO_FUZZ_BIN:-"$account_home/.cargo/bin/cargo-fuzz"}
if [[ -x $cargo_fuzz_request ]]; then
  CARGO_FUZZ_BIN=$(canonical_trusted_executable cargo-fuzz "$cargo_fuzz_request") || exit $?
fi
readonly CARGO_FUZZ_BIN

tmpdir=$("$MKTEMP_BIN" -d)
trap '"$RM_BIN" -rf "$tmpdir"' EXIT
active_cargo_home="$tmpdir/cargo-home"
"$MKDIR_BIN" -m 700 "$active_cargo_home"
for cache_dir in registry git; do
  if [[ -d $cargo_home/$cache_dir ]]; then
    "$LN_BIN" -s -- "$cargo_home/$cache_dir" "$active_cargo_home/$cache_dir"
  fi
done
readonly active_cargo_home

invocation_root="$tmpdir/invocation-root"
"$MKDIR_BIN" -m 700 "$invocation_root"
shopt -s dotglob nullglob
for source_entry in "$root"/*; do
  entry_name=${source_entry##*/}
  [[ $entry_name == .cargo ]] && continue
  "$LN_BIN" -s -- "$source_entry" "$invocation_root/$entry_name"
done
shopt -u dotglob nullglob
readonly invocation_root

nightly_launcher="$tmpdir/nightly-launcher"
"$MKDIR_BIN" -m 700 "$nightly_launcher"
if [[ -n $NIGHTLY_CARGO_BIN ]]; then
  "$LN_BIN" -s -- "$NIGHTLY_CARGO_BIN" "$nightly_launcher/cargo"
fi
readonly nightly_launcher

config_dir=$invocation_root
while :; do
  for cargo_config in "$config_dir/.cargo/config" "$config_dir/.cargo/config.toml"; do
    if [[ -e $cargo_config || -L $cargo_config ]]; then
      echo "FAIL: Cargo ancestor configuration is not allowed during validation: $cargo_config" >&2
      exit 1
    fi
  done
  [[ $config_dir == / ]] && break
  config_dir=${config_dir%/*}
  [[ -n $config_dir ]] || config_dir=/
done

declare -ar CURRENT_ENV=(
  "$ENV_BIN" -i --chdir="$invocation_root"
  HOME="$account_home" USER="$account_name" PATH="$CONTROL_PATH"
  CARGO_HOME="$active_cargo_home" RUSTUP_HOME="$rustup_home" CARGO_TERM_COLOR=never
  CARGO_INCREMENTAL=0 CARGO_NET_OFFLINE=true
  CARGO="$CARGO_BIN" RUSTC="$RUSTC_BIN" RUSTDOC="$RUSTDOC_BIN"
)
declare -ar NIGHTLY_ENV=(
  "$ENV_BIN" -i --chdir="$invocation_root"
  HOME="$account_home" USER="$account_name" PATH="$nightly_launcher:$CONTROL_PATH"
  CARGO_HOME="$active_cargo_home" RUSTUP_HOME="$rustup_home" CARGO_TERM_COLOR=never
  CARGO_INCREMENTAL=0 CARGO_NET_OFFLINE=true RUSTUP_TOOLCHAIN="$nightly"
  CARGO="$NIGHTLY_CARGO_BIN" RUSTC="$NIGHTLY_RUSTC_BIN" RUSTDOC="$NIGHTLY_RUSTDOC_BIN"
  MIRI="$MIRI_BIN"
)

manifest_snapshot="$tmpdir/adversarial-gates.tsv"
if ! exec {manifest_fd}<"$manifest"; then
  echo "FAIL: cannot open adversarial gate manifest: $manifest" >&2
  exit 1
fi
manifest_fd_path="/proc/self/fd/$manifest_fd"
if [[ ! -e $manifest_fd_path ]]; then
  manifest_fd_path="/dev/fd/$manifest_fd"
fi
manifest_fd_identity=$("$STAT_BIN" -Lc '%d:%i' -- "$manifest_fd_path")
manifest_path_identity=$("$STAT_BIN" -Lc '%d:%i' -- "$manifest" 2>/dev/null || true)
if [[ ! -f $manifest || -L $manifest || $manifest_fd_identity != "$manifest_path_identity" ]]; then
  echo "FAIL: missing or symlinked adversarial gate manifest: $manifest" >&2
  exit 1
fi
"$CAT_BIN" <&"$manifest_fd" >"$manifest_snapshot"
exec {manifest_fd}<&-
read -r manifest_digest _ < <("$SHA256_BIN" "$manifest_snapshot")

declare -A GATE_KINDS=()
declare -A GATE_COUNTS=()
declare -A GATE_ITEMS=()
declare -A manifest_rows=()
line_number=0
while IFS='|' read -r id kind evidence extra || [[ -n ${id:-} ]]; do
  line_number=$((line_number + 1))
  [[ -z $id || $id == \#* ]] && continue
  if [[ -n ${extra:-} || -z $kind || -z $evidence || ! $id =~ ^[a-z0-9-]+$ ]]; then
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
  if [[ -n ${GATE_KINDS[$id]+set} && ${GATE_KINDS[$id]} != "$kind" ]]; then
    echo "FAIL: gate $id mixes evidence kinds" >&2
    exit 1
  fi
  key="$id|$kind|$evidence"
  if [[ -n ${manifest_rows[$key]+set} ]]; then
    echo "FAIL: duplicate gate manifest row: $key" >&2
    exit 1
  fi
  manifest_rows[$key]=1
  GATE_KINDS[$id]=$kind
  item_index=${GATE_COUNTS[$id]:-0}
  GATE_ITEMS["$id|$item_index"]=$evidence
  GATE_COUNTS[$id]=$((item_index + 1))
done <"$manifest_snapshot"
readonly -A GATE_KINDS GATE_COUNTS GATE_ITEMS manifest_rows

declare -A TOOL_PATHS=()
declare -A TOOL_DIGESTS=()
tool_digest() {
  local name=$1
  local path=$2
  local digest
  read -r digest _ < <("$SHA256_BIN" "$path")
  TOOL_PATHS[$name]=$path
  TOOL_DIGESTS[$name]=$digest
  printf '  %-20s %s sha256=%s\n' "$name" "$path" "$digest"
}

echo "validation tool attestation"
tool_digest sha256sum "$SHA256_BIN"
tool_digest cat "$CAT_BIN"
tool_digest getent "$GETENT_BIN"
tool_digest mktemp "$MKTEMP_BIN"
tool_digest readlink "$READLINK_BIN"
tool_digest rm "$RM_BIN"
tool_digest stat "$STAT_BIN"
tool_digest cargo-current "$CARGO_BIN"
tool_digest rustc-current "$RUSTC_BIN"
tool_digest rustdoc-current "$RUSTDOC_BIN"
tool_digest rustup "$RUSTUP_BIN"
tool_digest git "$GIT_BIN"
tool_digest chmod "$CHMOD_BIN"
tool_digest ln "$LN_BIN"
tool_digest mkdir "$MKDIR_BIN"
tool_digest timeout "$TIMEOUT_BIN"
tool_digest env "$ENV_BIN"
tool_digest uname "$UNAME_BIN"
if [[ -n $NIGHTLY_CARGO_BIN ]]; then tool_digest cargo-nightly "$NIGHTLY_CARGO_BIN"; fi
if [[ -n $NIGHTLY_RUSTC_BIN ]]; then tool_digest rustc-nightly "$NIGHTLY_RUSTC_BIN"; fi
if [[ -n $NIGHTLY_RUSTDOC_BIN ]]; then tool_digest rustdoc-nightly "$NIGHTLY_RUSTDOC_BIN"; fi
if [[ -n $CARGO_MIRI_BIN ]]; then tool_digest cargo-miri "$CARGO_MIRI_BIN"; fi
if [[ -n $MIRI_BIN ]]; then tool_digest miri "$MIRI_BIN"; fi
if [[ -n $CARGO_FUZZ_BIN ]]; then tool_digest cargo-fuzz "$CARGO_FUZZ_BIN"; fi
echo "  gate-manifest        $manifest sha256=$manifest_digest"
readonly -A TOOL_PATHS TOOL_DIGESTS

revalidate_attestation() {
  local name path digest
  for name in "${!TOOL_PATHS[@]}"; do
    path=${TOOL_PATHS[$name]}
    if [[ ! -f $path || ! -x $path ]]; then
      echo "FAIL: validation tool disappeared during run: $name ($path)" >&2
      return 1
    fi
    read -r digest _ < <("$SHA256_BIN" "$path")
    if [[ $digest != "${TOOL_DIGESTS[$name]}" ]]; then
      echo "FAIL: validation tool digest changed during run: $name ($path)" >&2
      return 1
    fi
  done
  if [[ ! -f $manifest || -L $manifest ]]; then
    echo "FAIL: adversarial gate manifest disappeared or became a symlink" >&2
    return 1
  fi
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
  local count index
  GATE_KIND=${GATE_KINDS[$wanted]:-}
  count=${GATE_COUNTS[$wanted]:-0}
  GATE_EVIDENCE=()
  if [[ -z $GATE_KIND || $count == 0 ]]; then
    echo "FAIL: gate $wanted has no manifest evidence" >&2
    exit 1
  fi
  for ((index = 0; index < count; index++)); do
    GATE_EVIDENCE+=("${GATE_ITEMS["$wanted|$index"]}")
  done
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

attest_command_descriptor() {
  local gate_id=$1
  local descriptor=$2
  load_gate_evidence "$gate_id"
  if [[ $GATE_KIND != command || ${#GATE_EVIDENCE[@]} != 1 \
    || ${GATE_EVIDENCE[0]} != "$descriptor" ]]; then
    echo "FAIL: gate $gate_id command declaration does not match the executed command" >&2
    return 1
  fi
}

validate_fuzz_declaration() {
  local gate_id=$1
  local target=$2
  load_gate_evidence "$gate_id"
  if [[ $GATE_KIND != fuzz || ${#GATE_EVIDENCE[@]} != 1 \
    || ${GATE_EVIDENCE[0]} != "$target" ]]; then
    echo "FAIL: gate $gate_id fuzz declaration does not match target $target" >&2
    return 1
  fi
}

attest_fuzz_log() {
  local gate_id=$1
  local target=$2
  local log=$3
  validate_fuzz_declaration "$gate_id" "$target" || return 1
  local line done_count=0
  while IFS= read -r line || [[ -n $line ]]; do
    if [[ $line =~ ^#[0-9]+[[:space:]]+DONE([[:space:]]|$) ]]; then
      done_count=$((done_count + 1))
    fi
  done <"$log"
  if ((done_count != 1)); then
    echo "FAIL: gate $gate_id emitted $done_count exact libFuzzer DONE markers" >&2
    return 1
  fi
}

attest_script_log() {
  local gate_id=$1
  local log=$2
  load_gate_evidence "$gate_id"
  if [[ $GATE_KIND != script ]]; then
    echo "FAIL: gate $gate_id is not a script evidence gate" >&2
    return 1
  fi
  local -A expected=()
  local -A observed=()
  local marker line
  for marker in "${GATE_EVIDENCE[@]}"; do expected[$marker]=1; done
  local marker_re='^[^[:space:]]+[[:space:]]+rejected[[:space:]].+[[:space:]]as[[:space:]]expected:[[:space:]].+$'
  while IFS= read -r line || [[ -n $line ]]; do
    if [[ $line =~ $marker_re ]]; then
      if [[ -z ${expected[$line]+set} ]]; then
        echo "FAIL: gate $gate_id emitted unexpected marker: $line" >&2
        return 1
      fi
      if [[ -n ${observed[$line]+set} ]]; then
        echo "FAIL: gate $gate_id emitted duplicate marker: $line" >&2
        return 1
      fi
      observed[$line]=1
    fi
  done <"$log"
  for marker in "${GATE_EVIDENCE[@]}"; do
    if [[ -z ${observed[$marker]+set} ]]; then
      echo "FAIL: gate $gate_id omitted expected marker: $marker" >&2
      return 1
    fi
  done
}

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
  "$TIMEOUT_BIN" 15s "${CURRENT_ENV[@]}" "$CARGO_BIN" --version >"$canary" 2>&1
  local version
  IFS= read -r version <"$canary"
  if [[ $version != cargo\ * ]]; then
    echo "FAIL: absolute cargo canary returned unexpected output: $version" >&2
    exit 1
  fi
  if ! attest_command_descriptor fuzz-build \
    'cargo check --locked --manifest-path fuzz/Cargo.toml --bins'; then
    echo "FAIL: runner rejected the declared command descriptor" >&2
    exit 1
  fi
  if attest_command_descriptor fuzz-build 'cargo check --forged' >/dev/null 2>&1; then
    echo "FAIL: runner accepted a changed command declaration" >&2
    exit 1
  fi

  local fuzz_good="$tmpdir/fuzz-good.log"
  printf '%s\n' '#42 DONE   cov: 12 ft: 12 corp: 3/12b lim: 4 exec/s: 42 rss: 42Mb' >"$fuzz_good"
  attest_fuzz_log fuzz-image-header image_header "$fuzz_good" >/dev/null
  if attest_fuzz_log fuzz-image-header pod_artifact "$fuzz_good" >/dev/null 2>&1; then
    echo "FAIL: runner accepted a fuzz target that disagrees with the manifest" >&2
    exit 1
  fi

  local script_good="$tmpdir/script-good.log"
  local script_extra="$tmpdir/script-extra.log"
  load_gate_evidence connector-recovery
  printf '%s\n' "${GATE_EVIDENCE[@]}" >"$script_good"
  "$CAT_BIN" "$script_good" >"$script_extra"
  printf '%s\n' \
    'ptrace-forged rejected forged-case as expected: forged status' >>"$script_extra"
  attest_script_log connector-recovery "$script_good" >/dev/null
  if attest_script_log connector-recovery "$script_extra" >/dev/null 2>&1; then
    echo "FAIL: runner accepted an undeclared connector marker" >&2
    exit 1
  fi

  local bash_env="$tmpdir/hostile-bash-env"
  printf '%s\n' 'exit 0' >"$bash_env"
  local entrypoint status attack_log="$tmpdir/startup-attack.log"
  for entrypoint in \
    "$root/scripts/adversarial-check.sh" "$root/scripts/release-check.sh"; do
    set +e
    "$ENV_BIN" -i HOME="$account_home" PATH="$CONTROL_PATH" BASH_ENV="$bash_env" \
      "$entrypoint" definitely-invalid >"$attack_log" 2>&1
    status=$?
    set -e
    if ((status != 2)); then
      echo "FAIL: BASH_ENV forged $entrypoint status $status" >&2
      exit 1
    fi
    set +e
    "$ENV_BIN" -i HOME="$account_home" PATH="$CONTROL_PATH" \
      'BASH_FUNC_exit%%=() { return 0; }' \
      "$entrypoint" definitely-invalid >"$attack_log" 2>&1
    status=$?
    set -e
    if ((status != 2)); then
      echo "FAIL: exported exit function forged $entrypoint status $status" >&2
      exit 1
    fi
  done

  local fakebin="$tmpdir/hostile-path"
  "$MKDIR_BIN" "$fakebin"
  local fake
  for fake in cargo rustc timeout rustup; do
    printf '%s\n' '#!/bin/bash' 'exit 99' >"$fakebin/$fake"
    "$CHMOD_BIN" 755 "$fakebin/$fake"
  done
  local hostile_cache="$tmpdir/hostile-cargo-cache"
  local wrapper="$hostile_cache/forged-rustc-wrapper"
  local wrapper_marker="$hostile_cache/wrapper-ran"
  "$MKDIR_BIN" -m 700 "$hostile_cache"
  for cache_dir in registry git; do
    if [[ -d $cargo_home/$cache_dir ]]; then
      "$LN_BIN" -s -- "$cargo_home/$cache_dir" "$hostile_cache/$cache_dir"
    fi
  done
  printf '%s\n' '[build]' "rustc-wrapper = \"$wrapper\"" >"$hostile_cache/config.toml"
  printf '%s\n' '#!/bin/bash' "printf attacked >\"$wrapper_marker\"" 'exec "$@"' >"$wrapper"
  "$CHMOD_BIN" 755 "$wrapper"
  set +e
  "$ENV_BIN" -i HOME="$account_home" USER="$account_name" PATH="$fakebin" \
    SHMEM_POD_CARGO_HOME="$hostile_cache" \
    "$root/scripts/adversarial-check.sh" __tool-probe >"$attack_log" 2>&1
  status=$?
  set -e
  if ((status != 0)); then
    "$CAT_BIN" "$attack_log" >&2
    echo "FAIL: hostile PATH affected canonical validation tools" >&2
    exit 1
  fi
  set +e
  "$ENV_BIN" -i HOME="$account_home" USER="$account_name" PATH="$fakebin" \
    SHMEM_POD_CARGO_HOME="$hostile_cache" RELEASE_CHECK_DRY_RUN=1 \
    "$root/scripts/release-check.sh" quick >"$attack_log" 2>&1
  status=$?
  set -e
  if ((status != 0)); then
    "$CAT_BIN" "$attack_log" >&2
    echo "FAIL: hostile PATH affected canonical release tools" >&2
    exit 1
  fi
  if [[ -e $wrapper_marker ]]; then
    echo "FAIL: Cargo loaded rustc-wrapper from the cache source configuration" >&2
    exit 1
  fi

  if ! revalidate_attestation >/dev/null; then
    echo "FAIL: validation-runner self-test end attestation failed" >&2
    exit 1
  fi
  echo "PASS validation-runner self-test: exact test, command, fuzz, and script evidence enforced"
  echo "PASS validation-runner self-test: privileged startup rejects BASH_ENV and exported functions"
  echo "PASS validation-runner self-test: hostile PATH cannot replace canonical Rust or control tools"
}

if [[ $mode == __tool-probe ]]; then
  "${CURRENT_ENV[@]}" "$CARGO_BIN" --version >/dev/null
  "${CURRENT_ENV[@]}" "$RUSTC_BIN" --version >/dev/null
  "${CURRENT_ENV[@]}" "$CARGO_BIN" check --locked -p shmem-pod --lib --quiet
  if ! revalidate_attestation >/dev/null; then
    exit 1
  fi
  exit 0
fi

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
  local descriptor=$4
  shift 4
  if ! attest_command_descriptor "$gate_id" "$descriptor"; then
    exit 1
  fi
  run_logged_gate "$gate_id" command "$class" "$label" "$@"
  if command_succeeded "$label"; then
    echo "ATTESTED: gate=$gate_id command='$descriptor' manifest=$manifest_digest"
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
  local target=$3
  shift 3
  run_logged_gate "$gate_id" fuzz long "$label" "$@"
  if ! command_succeeded "$label"; then
    return
  fi
  if ! attest_fuzz_log "$gate_id" "$target" "$RUN_LOG"; then
    echo "FAIL: $label did not match exact fuzz evidence" >&2
    fail_count=$((fail_count + 1))
    return
  fi
  echo "ATTESTED: gate=$gate_id target=$target libfuzzer-completion manifest=$manifest_digest"
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
  if ! attest_script_log "$gate_id" "$RUN_LOG"; then
    fail_count=$((fail_count + 1))
    return
  fi
  load_gate_evidence "$gate_id"
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
source_revision=$("$GIT_BIN" rev-parse HEAD 2>/dev/null || true)
if [[ -z $source_revision ]]; then
  source_revision=${SHMEM_POD_SOURCE_REVISION:-}
fi
if [[ ! $source_revision =~ ^[0-9a-f]{40}$ ]]; then
  echo "FAIL: source revision is unavailable or malformed" >&2
  exit 1
fi
echo "  source revision: $source_revision"
echo "  kernel: $("$UNAME_BIN" -sr)"
echo "  architecture: $("$UNAME_BIN" -m)"
echo "  current rustc: $("${CURRENT_ENV[@]}" "$RUSTC_BIN" --version)"
echo "  current cargo: $("${CURRENT_ENV[@]}" "$CARGO_BIN" --version)"
if [[ -n $NIGHTLY_RUSTC_BIN ]]; then
  echo "  nightly rustc: $("${NIGHTLY_ENV[@]}" "$NIGHTLY_RUSTC_BIN" --version)"
  echo "  nightly cargo: $("${NIGHTLY_ENV[@]}" "$NIGHTLY_CARGO_BIN" --version)"
else
  echo "  nightly toolchain: unavailable ($nightly)"
fi
echo "  whole-run timeout: ${total_timeout}s"
echo "  nightly: $nightly"
echo "  Loom: permutations=$loom_permutations preemptions=$loom_preemptions"
echo "  fuzz budget: ${fuzz_seconds}s per target"

atomic64=0
while IFS= read -r cfg; do
  if [[ $cfg == 'target_has_atomic="64"' ]]; then
    atomic64=1
  fi
done < <("${CURRENT_ENV[@]}" "$RUSTC_BIN" --print cfg)
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
    "${CURRENT_ENV[@]}" LOOM_MAX_PERMUTATIONS="$loom_permutations" \
    LOOM_MAX_PREEMPTIONS="$loom_preemptions" RUSTFLAGS="--cfg shmem_pod_loom" \
    "$CARGO_BIN" test --locked -p shmem-pod --lib model_checks \
    --no-default-features --features linux-futex -- --test-threads=1 --color=never
else
  record_unsupported loom "Loom production protocol models" "target lacks 64-bit atomics"
fi

if [[ $linux == 1 && $atomic64 == 1 ]]; then
  run_test_gate fault-cuts long "serial production fault cuts" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" test --locked -p shmem-pod --lib fault_checks \
    --no-default-features --features linux-futex -- --test-threads=1 --color=never
  run_test_gate mapping-crash normal "killed mapping initializer and participant" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" test --locked -p shmem-pod --test mapping_lifecycle \
    killed_initializer_and_admitted_process_fail_stuck_until_supervisor_poison \
    -- --exact --test-threads=1 --color=never
  run_test_gate allocator-crash normal "killed allocator transaction" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" test --locked -p shmem-pod --test reloc_allocator \
    killed_transaction_stays_bounded_until_supervisor_poison \
    -- --exact --test-threads=1 --color=never
  run_test_gate migration-copying normal "killed migration owner in Copying" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" test --locked -p shmem-pod --test migration \
    killed_migrator_leaves_copying_state_and_never_steals_transaction \
    -- --exact --test-threads=1 --color=never
  run_test_gate migration-target-ready normal "killed migration owner in TargetReady" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" test --locked -p shmem-pod --test migration \
    killed_target_ready_migrator_leaves_source_authoritative \
    -- --exact --test-threads=1 --color=never
  run_test_gate migration-committed normal "killed migration owner after commit" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" test --locked -p shmem-pod --test migration \
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
  'cargo check --locked --manifest-path fuzz/Cargo.toml --bins' \
  "${CURRENT_ENV[@]}" "$CARGO_BIN" check --locked --manifest-path fuzz/Cargo.toml --bins
run_artifact_gate corpus normal "generate deterministic fuzz corpus" \
  "${CURRENT_ENV[@]}" "$CARGO_BIN" run --locked --manifest-path fuzz/Cargo.toml \
  --bin generate-corpus -- "$corpus_root"

nightly_available=0
if [[ -n $NIGHTLY_CARGO_BIN && -n $NIGHTLY_RUSTC_BIN && -n $NIGHTLY_RUSTDOC_BIN ]] \
  && "$TIMEOUT_BIN" 30s "${NIGHTLY_ENV[@]}" "$NIGHTLY_RUSTC_BIN" --version >/dev/null 2>&1; then
  nightly_available=1
fi
cargo_fuzz_available=0
if [[ -n $CARGO_FUZZ_BIN ]] \
  && "$TIMEOUT_BIN" 30s "${NIGHTLY_ENV[@]}" "$CARGO_FUZZ_BIN" fuzz --version >/dev/null 2>&1; then
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
  if ! validate_fuzz_declaration "$gate_id" "$target"; then
    exit 1
  fi
  if [[ $nightly_available == 0 ]]; then
    record_unavailable "$gate_id" "fuzz target $target" "toolchain $nightly is not installed"
  elif [[ $cargo_fuzz_available == 0 ]]; then
    record_unavailable "$gate_id" "fuzz target $target" "cargo-fuzz is not installed"
  else
    run_fuzz_gate "$gate_id" "fuzz target $target" "$target" \
      "${NIGHTLY_ENV[@]}" CARGO_TARGET_DIR=target/adversarial/fuzz \
      "$CARGO_FUZZ_BIN" fuzz run "$target" "$corpus_root/$target" -- \
      -max_total_time="$fuzz_seconds" -max_len=1048576 -timeout=5
  fi
done

miri_available=0
rust_src_available=0
if [[ $nightly_available == 1 ]]; then
  if [[ -n $CARGO_MIRI_BIN && -n $MIRI_BIN ]] \
    && "$TIMEOUT_BIN" 30s "${NIGHTLY_ENV[@]}" "$CARGO_MIRI_BIN" miri --version >/dev/null 2>&1; then
    miri_available=1
  fi
  while IFS= read -r component; do
    if [[ $component == rust-src || $component == rust-src-* ]]; then
      rust_src_available=1
    fi
  done < <(rustup_exec component list --toolchain "$nightly" --installed 2>/dev/null || true)
fi

if [[ $nightly_available == 0 ]]; then
  record_unavailable miri-pure "Miri pure parser and offset target" "toolchain $nightly is not installed"
elif [[ $miri_available == 0 ]]; then
  record_unavailable miri-pure "Miri pure parser and offset target" "Miri is not installed for $nightly"
else
  run_test_gate miri-pure long "Miri pure parser and offset target" \
    "${NIGHTLY_ENV[@]}" CARGO_TARGET_DIR=target/adversarial/miri \
    MIRIFLAGS="-Zmiri-strict-provenance -Zmiri-symbolic-alignment-check" \
    "$CARGO_MIRI_BIN" miri test --locked -p shmem-pod \
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
    "${NIGHTLY_ENV[@]}" CARGO_TARGET_DIR=target/adversarial/asan \
    RUSTFLAGS="-Zsanitizer=address" RUSTDOCFLAGS="-Zsanitizer=address" \
    ASAN_OPTIONS="detect_leaks=0:halt_on_error=1" \
    "$NIGHTLY_CARGO_BIN" test -Zbuild-std --locked \
    --target x86_64-unknown-linux-gnu -p shmem-pod \
    --test dynamic_analysis --no-default-features -- --test-threads=1 --color=never
  run_test_gate tsan long "ThreadSanitizer thread subset" \
    "${NIGHTLY_ENV[@]}" CARGO_TARGET_DIR=target/adversarial/tsan \
    RUSTFLAGS="-Zsanitizer=thread" RUSTDOCFLAGS="-Zsanitizer=thread" \
    TSAN_OPTIONS="halt_on_error=1:exitcode=66" \
    "$NIGHTLY_CARGO_BIN" test -Zbuild-std --locked \
    --target x86_64-unknown-linux-gnu -p shmem-pod \
    --test dynamic_analysis --no-default-features -- --test-threads=1 --color=never
fi

if [[ $mode == full ]]; then
  if [[ $x86_64_linux == 1 ]]; then
    run_script_gate connector-recovery long "connector fail-closed recovery suite" \
      "$ENV_BIN" -i --chdir="$invocation_root" HOME="$account_home" USER="$account_name" \
      PATH="${CARGO_BIN%/*}:$CONTROL_PATH" CARGO_HOME="$active_cargo_home" \
      RUSTUP_HOME="$rustup_home" CARGO_NET_OFFLINE=true \
      "$invocation_root/scripts/test-connector-failures.sh"
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
