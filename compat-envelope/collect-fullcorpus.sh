#!/usr/bin/env bash
# collect-fullcorpus.sh — the LOCAL full-corpus compat-envelope gate.
#
# The portable/privileged split (collect-envelope.rs + validate-envelope.sh) is
# for GitHub CI, where a runner may lack /dev/kvm, the third-party-backend
# feature build, or the SaBRe loader. On a fully-provisioned local box (this
# machine has /dev/kvm) the definition-of-done gate should instead measure the
# UNION — the FULL 235-cell e2e verify corpus across EVERY backend the local
# binary can run — not the ~28-cell ci=true portable subset.
#
# This script enumerates the full 235-cell ptrace-verify corpus (the same
# denominator as compat-envelope/corpus-manifest.csv: 214 compiled C guests +
# 21 shell/interpreter cells, listed in corpus/corpus-c.tsv + corpus/corpus-nonc.tsv),
# and for every locally-available backend runs, per cell:
#   det    = <backend> --strict --verify exits 0 (L2 DETLOG-bitwise self-verify)
#   parity = <backend> --strict stdout == ptrace --strict --verify stdout
# It writes the merged fullcorpus-scorecard.csv, ASSERTS green-stays-green
# against a per-backend ratchet baseline (a real regression fails the gate), and
# renders the scorecard.
#
# Backends are AUTO-DETECTED from the binary + host so a partially-provisioned
# box degrades honestly (a missing backend is recorded n/a, never a false red):
#   ptrace, liteinst   — always (default binary)
#   dbi, sabre, e9patch — require a --features third-party-backends binary
#   kvm                — requires /dev/kvm
#
# Usage:
#   HERMIT_BIN=<third-party-backends release binary> collect-fullcorpus.sh
#     [--backends b1,b2,...]   restrict to these (default: auto-detect)
#     [--par N]                parallelism (default: 16)
#     [--no-assert]            measure only; do not fail on ratchet regression
#     [--out PATH]             output CSV (default: fullcorpus-scorecard.csv)
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Canonicalize repo roots: the e9patch AOT rewriter REJECTS any guest path
# containing parent ("..") components, so a relative "$here/../hermit" would make
# every e9patch cell fail "guest path cannot contain parent components" — a false
# 0/200 regression. realpath -m strips the "..".
HROOT="$(realpath -m "${HERMIT_REPO:-$here/../hermit}")"
RROOT="$(realpath -m "${REVERIE_REPO:-$here/../reverie}")"
BIN="${HERMIT_BIN:-$HROOT/target/release/hermit}"
BUILD="$(realpath -m "${FULLCORPUS_BUILD:-$HROOT/ignored/kvm-fullcorpus}")"   # gitignored guest build tree (shared)
CORPUS_C="${CORPUS_C:-$here/corpus/corpus-c.tsv}"
CORPUS_NONC="${CORPUS_NONC:-$here/corpus/corpus-nonc.tsv}"
OUT="$here/fullcorpus-scorecard.csv"
PAR="${PAR:-16}"
TMO_RUN="${TMO_RUN:-90}"
TMO_VERIFY="${TMO_VERIFY:-120}"
backends_arg=""
do_assert=1

while [ $# -gt 0 ]; do
  case "$1" in
    --backends) backends_arg="$2"; shift 2 ;;
    --par) PAR="$2"; shift 2 ;;
    --no-assert) do_assert=0; shift ;;
    --out) OUT="$2"; shift 2 ;;
    -h|--help) grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "collect-fullcorpus: unknown arg $1" >&2; exit 2 ;;
  esac
done

[ -x "$BIN" ] || { echo "collect-fullcorpus: HERMIT_BIN not executable: $BIN" >&2; exit 2; }
[ -f "$CORPUS_C" ] || { echo "collect-fullcorpus: missing $CORPUS_C" >&2; exit 2; }

HSHA="$(git -C "$HROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
RSHA="$(git -C "$RROOT" rev-parse HEAD 2>/dev/null || echo unknown)"

# --- backend auto-detection --------------------------------------------------
have_backend() { # $1 = backend name; probes the binary's --backend enum + host
  local b="$1"
  case "$b" in
    ptrace|liteinst) return 0 ;;
    kvm) [ -e /dev/kvm ] || return 1 ;;
  esac
  # feature backends (dbi/sabre/e9patch): require a FUNCTIONAL probe run on a
  # canonical (no-"..") guest path, not merely clap acceptance. clap exit 2 ==
  # "invalid value for --backend" (not compiled in); but a backend can be
  # compiled in yet non-runnable here — e9patch needs the e9tool AOT rewriter,
  # which fails at RUNTIME (exit 1) when absent. e9patch also REJECTS any guest
  # path containing parent ("..") components, so the probe path must be canonical
  # or e9patch would be spuriously dropped. Contract: a feature backend that
  # cannot run a plain guest here is n/a.
  timeout 30 "$BIN" --backend "$b" run -- /bin/true >/dev/null 2>&1
  local rc=$?
  [ "$rc" = 0 ]
}

ALL_BACKENDS="ptrace kvm dbi sabre e9patch liteinst"
[ -n "$backends_arg" ] && ALL_BACKENDS="${backends_arg//,/ }"
DETECTED=""
for b in $ALL_BACKENDS; do
  if have_backend "$b"; then DETECTED="$DETECTED $b"; else echo "  backend $b: NOT available here (recorded n/a)"; fi
done
DETECTED="${DETECTED# }"
echo "== full-corpus gate: hermit=$HSHA backends=[$DETECTED] par=$PAR =="
echo "   corpus = $(wc -l <"$CORPUS_C") C + $(grep -vc '^#' "$CORPUS_NONC") non-C cells"

# --- per-backend ratchet baselines (green-stays-green floor) ------------------
# Existing 205-cell floors were measured at hermit 82a8e853; the thirty performance
# cells were measured with the same binary and uniform lane flags.
# A backend that drops below its floor fails the gate. New backends default 0.
baseline() {
  # Combined floors over the full 235-cell corpus; det =
  # <backend> --strict --verify exits 0. A backend dropping
  # below its floor fails the gate.
  case "$1" in
    ptrace) echo 214 ;;
    kvm) echo 160 ;;
    liteinst) echo 118 ;;
    dbi) echo 190 ;;
    sabre) echo 199 ;;
    e9patch) echo 214 ;;
    *) echo 0 ;;
  esac
}

ROWS="$(mktemp -d)"
HDR="run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,test_id,test_mode,backend,cell_state,outcome,deterministic,stdout_parity,output_hash,duration_ms,max_rss_kb,reason"
RUN_UTC="@$(date +%s)"

# --- one (backend,cell) measurement ------------------------------------------
measure() { # $1=backend $2=cell-dir(holds ptv.out ref) $3=lane $4=id ; rest=guest argv
  local backend="$1" cell="$2" lane="$3" id="$4"; shift 4
  local -a gcmd=("$@")
  local bucket="${id%%/*}"
  local flags=""
  [ "$lane" = portable ] && flags="--no-virtualize-cpuid --max-timeslice=disabled"
  export LC_ALL=C TZ=UTC
  local re=0
  # ptrace: TWO runs.
  #  (1) plain --strict  -> ptv.out = the PARITY REFERENCE (real guest stdout).
  #      NB: --strict --verify does an internal DOUBLE-run and emits NO guest
  #      stdout to the parent, so a --verify capture would be 0 bytes and every
  #      backend's parity-vs-reference would be spuriously ~0. Reference MUST be
  #      plain --strict, matching how the backend .out files are produced.
  #  (2) --strict --verify -> det signal (L2 self-verify exit code).
  if [ "$backend" = ptrace ]; then
    local t0 t1 dur re ve ohash det outcome reason
    timeout "$TMO_RUN" "$BIN" run --strict $flags -- "${gcmd[@]}" >"$cell/ptv.out" 2>"$cell/ptv.err"; re=$?
    ohash=$(sha256sum "$cell/ptv.out" | cut -c1-64)
    t0=$(date +%s%3N)
    timeout "$TMO_VERIFY" "$BIN" run --strict --verify $flags -- "${gcmd[@]}" >/dev/null 2>"$cell/ptvv.err"; ve=$?
    t1=$(date +%s%3N); dur=$((t1-t0))
    if [ "$ve" = 0 ]; then det=1; outcome=pass; reason="";
    elif [ "$ve" = 124 ]; then det=0; outcome=timeout; reason="ptrace-verify-timeout-${TMO_VERIFY}s";
    else det=0; outcome=diverge; reason="ptrace-verify-fail-exit$ve"; fi
    # a failed plain --strict reference is unusable for parity: mark it so
    # downstream backends record parity="" (unmeasured), never a false match
    # (an empty-but-valid reference must still be comparable, hence a marker
    # file rather than relying on emptiness).
    if [ "$re" != 0 ]; then
      : >"$cell/ptv.out"; ohash=$(sha256sum "$cell/ptv.out" | cut -c1-64)
      : >"$cell/ptv.fail"; [ -z "$reason" ] && reason="ptrace-run-fail-exit$re"
    else
      rm -f "$cell/ptv.fail"
    fi
    echo "fullcorpus,$RUN_UTC,$HSHA,$RSHA,false,expansion,$lane,$bucket,$id,verify,ptrace,expansion,$outcome,$det,,$ohash,$dur,,$reason" > "$ROWS/ptrace_${id//\//_}.row"
    return
  fi
  # non-ptrace backend: strict (for parity) + strict --verify (for det)
  timeout "$TMO_RUN" "$BIN" --backend "$backend" run --strict $flags -- "${gcmd[@]}" >"$cell/$backend.out" 2>"$cell/$backend.err"; re=$?
  local t0 t1 dur ve bhash phash det outcome reason parity ohash
  t0=$(date +%s%3N)
  timeout "$TMO_VERIFY" "$BIN" --backend "$backend" run --strict --verify $flags -- "${gcmd[@]}" >"$cell/${backend}v.out" 2>"$cell/${backend}v.err"; ve=$?
  t1=$(date +%s%3N); dur=$((t1-t0))
  bhash=$(sha256sum "$cell/$backend.out" | cut -c1-64); ohash="$bhash"
  if [ "$ve" = 0 ]; then det=1; outcome=pass; reason="";
  elif [ "$ve" = 124 ]; then det=0; outcome=timeout; reason="$backend-verify-timeout-${TMO_VERIFY}s";
  else det=0; outcome=diverge; reason="$backend-verify-fail-exit$ve"; fi
  if [ ! -f "$cell/ptv.out" ] || [ -f "$cell/ptv.fail" ]; then parity="";  # ref missing/failed -> unmeasured
  elif [ "$re" != 0 ]; then parity=0; [ -z "$reason" ] && reason="$backend-run-fail-exit$re";
  else
    phash=$(sha256sum "$cell/ptv.out" | cut -c1-64)
    [ "$bhash" = "$phash" ] && parity=1 || parity=0
  fi
  echo "fullcorpus,$RUN_UTC,$HSHA,$RSHA,false,expansion,$lane,$bucket,$id,verify,$backend,expansion,$outcome,$det,$parity,$ohash,$dur,,$reason" > "$ROWS/${backend}_${id//\//_}.row"
}
export -f measure
export BIN ROWS RUN_UTC HSHA RSHA TMO_RUN TMO_VERIFY

# --- compile C guests once (shared build tree) -------------------------------
echo "== compiling/reusing C guests =="
mkdir -p "$BUILD"
while IFS='|' read -r id prog cflags extra lane cstate; do
  [ -n "$id" ] || continue
  key="${id//\//_}"; cell="$BUILD/$key"; mkdir -p "$cell"
  guest="$cell/guest"
  [ -x "$guest" ] && continue
  extra_abs=""
  for e in $extra; do extra_abs="$extra_abs $HROOT/$e"; done
  # shellcheck disable=SC2086
  cc -std=c11 -O2 -g -Wall -Wextra -Werror $cflags "$HROOT/$prog" $extra_abs -o "$guest" 2>"$cell/cc.err" \
    || echo "  build-fail: $id" >&2
done < "$CORPUS_C"

# --- run every detected backend, ptrace FIRST (writes the parity reference) ---
order="ptrace"
for b in $DETECTED; do [ "$b" = ptrace ] || order="$order $b"; done
# sweep_backend BACKEND PAR REPAIR
#   REPAIR=0 : measure every cell (parallel storm).
#   REPAIR=1 : re-measure ONLY cells whose current row is a --verify TIMEOUT.
#     A 120s timeout under a 24-way parallel storm is a load/scheduling artifact,
#     not a determinism verdict; a second, serial (PAR=1) pass gives each such
#     cell a fair shake. A genuine hang (e.g. a dbi preemption-ceiling cell) times
#     out again and correctly stays failed; only load flake recovers. This keeps
#     the ratchet trustworthy on a contended box without lowering any det floor.
sweep_backend() {
  local backend="$1" par="$2" repair="$3"
  xargs -a "$CORPUS_C" -d '\n' -P "$par" -I{} bash -c '
    IFS="|" read -r id prog cflags extra lane cstate <<<"$1"
    key="${id//\//_}"; cell="'"$BUILD"'/$key"
    [ -x "$cell/guest" ] || exit 0
    if [ "'"$repair"'" = 1 ]; then
      row="$ROWS/'"$backend"'_${id//\//_}.row"
      case "$(cut -d, -f13 "$row" 2>/dev/null)" in timeout) ;; *) exit 0;; esac
    fi
    measure "'"$backend"'" "$cell" "$lane" "$id" "$cell/guest"
  ' _ {}
  xargs -a "$CORPUS_NONC" -d '\n' -P "$par" -I{} bash -c '
    line="$1"; case "$line" in \#*) exit 0;; esac
    id="${line%%|*}"; rest="${line#*|}"; lane="${rest%%|*}"; cmd="${rest#*|}"
    cmd="${cmd//HERMITROOT/'"$HROOT"'}"
    key="${id//\//_}"; cell="'"$BUILD"'/nonc_$key"; mkdir -p "$cell"
    if [ "'"$repair"'" = 1 ]; then
      row="$ROWS/'"$backend"'_${id//\//_}.row"
      case "$(cut -d, -f13 "$row" 2>/dev/null)" in timeout) ;; *) exit 0;; esac
    fi
    # shellcheck disable=SC2086
    measure "'"$backend"'" "$cell" "$lane" "$id" $cmd
  ' _ {}
}
export ROWS
for backend in $order; do
  echo "== sweep backend=$backend =="
  sweep_backend "$backend" "$PAR" 0
  nto=$(ls "$ROWS/${backend}"_*.row 2>/dev/null | xargs -r grep -l ',timeout,' 2>/dev/null | wc -l)
  if [ "$nto" -gt 0 ]; then
    echo "   repair: re-running $nto timed-out $backend cell(s) serially"
    sweep_backend "$backend" 1 1
  fi
done

{ echo "$HDR"; cat "$ROWS"/*.row; } > "$OUT"
rm -rf "$ROWS"
echo "== full-corpus scorecard written: $OUT =="

# --- ratchet assert + render -------------------------------------------------
fail=0
echo "== green-stays-green ratchet (per-backend det floor over full corpus) =="
for backend in $order; do
  det=$(awk -F, -v b="$backend" 'NR>1 && $11==b && $14=="1"{n++} END{print n+0}' "$OUT")
  tot=$(awk -F, -v b="$backend" 'NR>1 && $11==b{n++} END{print n+0}' "$OUT")
  floor=$(baseline "$backend")
  if [ "$det" -lt "$floor" ]; then
    echo "  REGRESSION: $backend det $det/$tot < floor $floor" >&2; fail=1
  else
    echo "  OK: $backend det $det/$tot (floor $floor)"
  fi
done

echo "== rendered scorecard =="
"$here/render-scorecard.rs" --csv "$OUT" --all --backends kvm,dbi,sabre,liteinst,e9patch || true

if [ "$do_assert" -eq 1 ] && [ "$fail" -ne 0 ]; then
  echo "collect-fullcorpus: FAILED — backend regressed below ratchet floor" >&2
  exit 1
fi
echo "== full-corpus gate GREEN =="
