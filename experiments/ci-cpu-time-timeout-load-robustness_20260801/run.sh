#!/usr/bin/env bash
# CPU-time vs wall-only timeout robustness under load (safe-ci-dag-runner).
#
# QUESTION (owner directive): is a GENEROUS wall timeout + AGGRESSIVE RLIMIT_CPU CPU-time budget
# MORE ROBUST against SPURIOUS timeouts under host CPU contention than today's tight wall-only
# timeout? Report a flake-rate comparison per load level.
#
# WHY THE LOAD IS *INSIDE* THE DAG (important, non-obvious):
# safe-ci-dag-runner boxes every step in its OWN cgroup-v2 scope
# (.../safe-ci.slice/safe-ci-<pid>.scope/step-<tag>.cpu). Under cgroup-v2 CPU distribution a step's
# scope gets its own weight-share of the CPU, so CPU burners launched in a SIBLING slice (e.g. a
# shell's own scope) barely slow the step down — boxing already isolates a step from *external* host
# load. The wall-timeout flakes that actually bite CI therefore come from CONTENTION AMONG THE
# RUNNER'S OWN CONCURRENT STEPS (sibling step-*.cpu cgroups splitting one core), not from a foreign
# process. So we model load as N extra CPU-bound STEPS in the same DAG, and pin the WHOLE runner (all
# step children inherit affinity) to a single core so the concurrent steps genuinely time-share it.
#
# On ONE core, K concurrent equal CPU-bound steps each get ~1/K of the core: wall inflates ~Kx while
# each step's CPU-seconds stay fixed (~2). The measured "victim" step does a fixed ~2 CPU-s of legit
# work, so ANY non-PASS is a SPURIOUS timeout == a flake. Two timeout policies are compared:
#   * wall-tight   : timeout=WALL_TIGHT_TMO (models expansion-dag --headroom 1.5 over ~2.25s idle
#                    boxed wall), cpu_timeout=0  (wall-only == today's behavior)
#   * cpu-generous : timeout=CPU_GEN_WALL (generous wall backstop), cpu_timeout=CPU_GEN_BUDGET
#                    (~3x the ~2 CPU-s budget)
# A CPU-second is physical: the same work costs ~2 CPU-s whether the core is idle or 6-way shared, so
# the CPU budget never trips on legit work, while contention inflates WALL time and trips the tight
# wall timeout. A final section confirms the CPU budget STILL catches a genuine runaway (robustness,
# not mere leniency).
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
RUNNER="${RUNNER:-/home/newton/work/dev-hermit/worktrees/ci-validate/hermit/agent-utils/rs/target/release/safe-ci-dag-runner}"
CORES="${CORES:-0}"                       # single core: exact, reproducible contention
REPS="${REPS:-8}"                         # reps per (policy x load) cell
WORK_N="${WORK_N:-64000000}"              # awk iterations ~= 2.0 CPU-seconds, ~2.25s idle boxed wall
WALL_TIGHT_TMO="${WALL_TIGHT_TMO:-4}"    # ~1.5x headroom over 2.25s idle == today's tight policy
CPU_GEN_WALL="${CPU_GEN_WALL:-120}"      # generous wall backstop
CPU_GEN_BUDGET="${CPU_GEN_BUDGET:-6}"    # ~3x the ~2 CPU-s of real work
RAW="$HERE/ignored"
OUT="$HERE/results/results.csv"
mkdir -p "$RAW" "$HERE/results"

if [[ ! -x "$RUNNER" ]]; then
  echo "FATAL: runner not found/executable at $RUNNER" >&2
  exit 2
fi

# awk summing WORK_N ints: pure-CPU, fixed iteration count -> load-invariant CPU cost.
WORK_CMD="awk 'BEGIN{s=0;for(i=0;i<${WORK_N};i++)s+=i}'"

# Emit a DAG: one victim step (policy-specific timeout policy) + N independent load steps.
# $1=victim_timeout $2=victim_cpu_timeout $3=N_load  -> stdout JSON
emit_dag() {
  local vt="$1" vc="$2" n="$3" i sep
  echo '{"steps":['
  echo "  {\"group\":\"victim\",\"job\":\"cpu\",\"desc\":\"legit ~2 CPU-s\",\"cmd\":\"${WORK_CMD}\",\"timeout\":${vt},\"cpu_timeout\":${vc}}$([[ $n -gt 0 ]] && echo , )"
  for ((i = 1; i <= n; i++)); do
    sep=','; [[ $i -eq $n ]] && sep=''
    echo "  {\"group\":\"load\",\"job\":\"n${i}\",\"desc\":\"load\",\"cmd\":\"${WORK_CMD}\",\"timeout\":${CPU_GEN_WALL}}${sep}"
  done
  echo ']}'
}

# One rep: run the pinned runner on a DAG, classify the VICTIM step from its per-step line.
# Prints: <status> <wall_s>   status in {PASS, TIMEOUT, CPU-TIMEOUT, OTHER}
one_rep() {
  local dag="$1" log="$2" jobs="$3" t0 t1 rc vline
  t0=$(date +%s.%N)
  taskset -c "$CORES" "$RUNNER" run --dag "$dag" --no-profile --jobs "$jobs" >"$log" 2>&1
  rc=$?
  t1=$(date +%s.%N)
  local wall
  wall=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.2f", b-a}')
  vline=$(grep -m1 '\[victim.cpu\] [✓✗]' "$log" || true)
  local status="OTHER"
  if [[ "$vline" == *"✓ PASS"* ]]; then
    status="PASS"
  elif [[ "$vline" == *"CPU-TIMEOUT"* ]]; then
    status="CPU-TIMEOUT"
  elif [[ "$vline" == *"TIMEOUT"* ]]; then
    status="TIMEOUT"
  fi
  echo "$status $wall"
}

echo "policy,load,rep,status,wall_s" > "$OUT"

# $1=policy $2=victim_timeout $3=victim_cpu_timeout $4=load_name $5=N_load
run_cell() {
  local policy="$1" vt="$2" vc="$3" load_name="$4" n="$5" rep line status wall dag jobs
  dag="$RAW/dag_${policy}_${load_name}.json"
  emit_dag "$vt" "$vc" "$n" > "$dag"
  jobs=$((n + 1))
  for ((rep = 1; rep <= REPS; rep++)); do
    line=$(one_rep "$dag" "$RAW/${policy}_${load_name}_${rep}.log" "$jobs")
    status="${line%% *}"; wall="${line##* }"
    echo "${policy},${load_name},${rep},${status},${wall}" >> "$OUT"
    echo "[$policy/$load_name] rep $rep -> $status (${wall}s)"
  done
}

echo "### CPU-time vs wall-only timeout robustness under load"
echo "runner=$RUNNER"
echo "cores=$CORES reps=$REPS work_n=$WORK_N wall_tight_tmo=${WALL_TIGHT_TMO}s cpu_gen_wall=${CPU_GEN_WALL}s cpu_gen_budget=${CPU_GEN_BUDGET}s"
echo

# Load levels (concurrent CPU-bound steps sharing ONE core):
#   idle=0 (~2.25s wall), moderate=2 (~6.75s == 3x), swamped=5 (~13.5s == 6x).
# The tight wall (4s) trips at moderate and above; the CPU budget (6 CPU-s) + generous wall (120s)
# never trip on the fixed ~2 CPU-s of legit work.
LEVELS="${LEVELS:-idle:0 moderate:2 swamped:5}"
for lvl in $LEVELS; do
  name="${lvl%%:*}"; n="${lvl##*:}"
  run_cell "wall-tight"   "$WALL_TIGHT_TMO" 0                "$name" "$n"
  run_cell "cpu-generous" "$CPU_GEN_WALL"   "$CPU_GEN_BUDGET" "$name" "$n"
done

echo
echo "### flake-rate summary (flake = any non-PASS on fixed ~2 CPU-s of LEGIT work)"
awk -F, 'NR>1{
  cell=$1"/"$2; tot[cell]++; if($4!="PASS") flake[cell]++;
  if($5>mx[cell])mx[cell]=$5
}
END{
  printf "%-26s %6s %8s %10s %12s\n","policy/load","reps","flakes","flake%","max_wall_s"
  n=split("wall-tight/idle wall-tight/moderate wall-tight/swamped cpu-generous/idle cpu-generous/moderate cpu-generous/swamped",order," ")
  for(i=1;i<=n;i++){c=order[i]; if(tot[c]==0)continue;
    printf "%-26s %6d %8d %9.0f%% %12.2f\n",c,tot[c],flake[c]+0,100*(flake[c]+0)/tot[c],mx[c]}
}' "$OUT" | tee "$HERE/results/summary.txt"

# --- Robustness check: does the CPU budget STILL catch a genuine runaway (not just tolerate load)? ---
echo
echo "### runaway control (genuine infinite CPU loop; must be KILLED, not tolerated)"
RUNAWAY_DAG="$RAW/dag_runaway.json"
cat > "$RUNAWAY_DAG" <<JSON
{"steps":[{"group":"victim","job":"cpu","desc":"genuine runaway","cmd":"while : ; do : ; done","timeout":${CPU_GEN_WALL},"cpu_timeout":${CPU_GEN_BUDGET}}]}
JSON
rt0=$(date +%s.%N)
taskset -c "$CORES" "$RUNNER" run --dag "$RUNAWAY_DAG" --no-profile --jobs 1 >"$RAW/runaway.log" 2>&1
rt1=$(date +%s.%N)
rwall=$(awk -v a="$rt0" -v b="$rt1" 'BEGIN{printf "%.2f", b-a}')
rline=$(grep -m1 '\[victim.cpu\] [✓✗]' "$RAW/runaway.log" || true)
echo "cpu-generous vs runaway: ${rline#*] }  (outer wall ${rwall}s; budget was ${CPU_GEN_BUDGET} CPU-s, wall backstop ${CPU_GEN_WALL}s)"
echo "  -> a wall-only policy generous enough (${CPU_GEN_WALL}s) to never flake would let this run ${CPU_GEN_WALL}s;"
echo "     the CPU budget kills it at ~${CPU_GEN_BUDGET} CPU-s while STILL never flaking legit work."
