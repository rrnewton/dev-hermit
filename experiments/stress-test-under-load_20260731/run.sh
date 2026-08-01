#!/usr/bin/env bash
# Load-independence guardrail launcher.
#
# Stack: safe-ci-dag-runner (singleton DAG, outer cgroup MEM cap + perf profiling)
#          -> bounded worker pool of parallel `hermit run --strict --verify` reps
#             -> cross-rep output-hash / verify / schedule-fp divergence == P0.
#
# Profiles:
#   smoke     reps  mode, tiny pool + few reps  -> validate the engine end-to-end (fast).
#   validate  reps  mode, pool=Nprocs, reps=N   -> Phase 1: confirm engine + hash-diff at scale.
#   torture   timed mode, oversubscribed pool   -> Phase 2: the 1-hour fair hot loop (HELD).
#
# All output is redirected to results/<profile>_<ts>.log (issue #113: no stream flood).
# Env overrides: HERMIT_ROOT, HERMIT_BIN, MEM_CAP, POOL, REPS, MINUTES, PER_RUN_TIMEOUT.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMIT_ROOT="${HERMIT_ROOT:-$HOME/work/dev-hermit/hermit}"
AGENT_UTILS_PY="$HERMIT_ROOT/agent-utils/py"
RESULTS="$HERE/results"
mkdir -p "$RESULTS"

# --- hermit binary (release preferred) ---
if [[ -n "${HERMIT_BIN:-}" ]]; then
  :
elif [[ -x "$HERMIT_ROOT/target/release/hermit" ]]; then
  HERMIT_BIN="$HERMIT_ROOT/target/release/hermit"
elif [[ -x "$HERMIT_ROOT/target/debug/hermit" ]]; then
  HERMIT_BIN="$HERMIT_ROOT/target/debug/hermit"
else
  echo "ERROR: no hermit binary under $HERMIT_ROOT/target/{release,debug}" >&2
  exit 1
fi

NPROC="$(nproc)"
PROFILE="${1:-smoke}"

case "$PROFILE" in
  smoke)
    MODE=reps;  REPS="${REPS:-2}";  POOL="${POOL:-8}"
    MEM_CAP="${MEM_CAP:-8G}"; PER_RUN_TIMEOUT="${PER_RUN_TIMEOUT:-120}"
    STEP_TIMEOUT=1800 ;;
  validate)
    MODE=reps;  REPS="${REPS:-10}"; POOL="${POOL:-$NPROC}"
    MEM_CAP="${MEM_CAP:-64G}"; PER_RUN_TIMEOUT="${PER_RUN_TIMEOUT:-180}"
    STEP_TIMEOUT=7200 ;;
  torture)
    # Phase-2-tuned defaults (see README "Tuning for the real 1-hour torture"):
    # oversub 1.25 keeps the pool > Nprocs (machine stays swamped) while a 600s
    # per-run budget lets the heavy tests (rand, progressbar) still SCORE instead
    # of timing out to INCONCLUSIVE the way 1.5x + 180s did at load ~948.
    MODE=timed; MINUTES="${MINUTES:-60}"; POOL="${POOL:-0}"  # 0 = auto oversubscribe
    OVERSUB="${OVERSUB:-1.25}"
    MEM_CAP="${MEM_CAP:-64G}"; PER_RUN_TIMEOUT="${PER_RUN_TIMEOUT:-600}"
    # window + one full per-run drain of in-flight reps + margin.
    STEP_TIMEOUT=$(( $(printf '%.0f' "$MINUTES") * 60 + PER_RUN_TIMEOUT + 600 )) ;;
  *)
    echo "usage: $0 {smoke|validate|torture}" >&2; exit 2 ;;
esac

TS="$(date +%Y%m%d_%H%M%S)"
LOG="$RESULTS/${PROFILE}_${TS}.log"
PERF="$RESULTS/perf_${PROFILE}_${TS}"

HARNESS_ARGS=( --mode "$MODE" --pool "$POOL"
  --per-run-timeout "$PER_RUN_TIMEOUT"
  --hermit-bin "$HERMIT_BIN" --hermit-root "$HERMIT_ROOT" --outdir "$RESULTS" )
if [[ "$MODE" == reps ]]; then
  HARNESS_ARGS+=( --reps "$REPS" )
else
  HARNESS_ARGS+=( --minutes "$MINUTES" --oversub "${OVERSUB:-1.25}" )
fi

{
  echo "==== load-independence guardrail: profile=$PROFILE ===="
  echo "host: $(hostname)  nproc=$NPROC  $(date -Is)"
  echo "hermit_bin: $HERMIT_BIN"
  echo "mem_cap: $MEM_CAP  pool: $POOL  step_timeout: ${STEP_TIMEOUT}s"
  echo "cmd: guarded_run.py -- python3 harness.py ${HARNESS_ARGS[*]}"
  echo "======================================================="
} | tee "$LOG"

# safe-ci-dag-runner needs the package importable for the singleton-DAG run.
export PYTHONPATH="$AGENT_UTILS_PY${PYTHONPATH:+:$PYTHONPATH}"

set +e
python3 "$HERE/guarded_run.py" \
  --mem-cap "$MEM_CAP" --perf-dir "$PERF" --step-timeout "$STEP_TIMEOUT" \
  --agent-utils-py "$AGENT_UTILS_PY" \
  -- python3 "$HERE/harness.py" "${HARNESS_ARGS[@]}" >>"$LOG" 2>&1
RC=$?
set -e

echo "guardrail rc=$RC  (0=GREEN 2=P0 3=refused/preexisting-fail)  log=$LOG" | tee -a "$LOG"
exit $RC
