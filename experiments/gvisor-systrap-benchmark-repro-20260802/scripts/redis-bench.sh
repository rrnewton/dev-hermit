#!/usr/bin/env bash
set -euo pipefail

tier=${1:?tier}
backend=${2:?backend}
rep=${3:?repetition}
port=${4:?port}
out=${5:?output directory}

reverie=/home/newton/work/dev-hermit/worktrees/238b/reverie
hermit=/home/newton/work/dev-hermit/hermit/target/release/hermit
server=/usr/bin/redis-server
client=/usr/bin/redis-benchmark
cli=/usr/bin/redis-cli

mkdir -p "$out"
server_stdout="$out/${tier}-${backend}-${rep}.server.stdout"
server_stderr="$out/${tier}-${backend}-${rep}.server.stderr"
client_stdout="$out/${tier}-${backend}-${rep}.client.stdout"
client_stderr="$out/${tier}-${backend}-${rep}.client.stderr"
server_time="$out/${tier}-${backend}-${rep}.server.time"
client_time="$out/${tier}-${backend}-${rep}.client.time"

common=(
  "$server"
  --bind 127.0.0.1
  --port "$port"
  --save ''
  --appendonly no
  --protected-mode no
  --daemonize no
  --dir "$out"
  --dbfilename "${tier}-${backend}-${rep}.rdb"
)

case "$tier/$backend" in
  native/native)
    command=("${common[@]}")
    ;;
  counter2/ptrace)
    command=("$reverie/target/release/counter2" -- "${common[@]}")
    ;;
  counter2/liteinst)
    command=("$reverie/target/release/reverie-liteinst-examples" --tool counter2 -- "${common[@]}")
    ;;
  counter2/dbi)
    command=("$reverie/target/release/reverie-dbi-counter2-exact" -- "${common[@]}")
    ;;
  counter2/sabre)
    command=(
      "$reverie/target/release/reverie-sabre-strace"
      --sabre "$reverie/target/sabre/sabre"
      --plugin "$reverie/target/release/libreverie_sabre_strace_plugin.so"
      --tool counter2-exact -- "${common[@]}"
    )
    ;;
  counter2/e9patch)
    command=("$reverie/target/release/reverie-e9patch-counter2" -- "${common[@]}")
    ;;
  relaxed/*)
    command=(
      "$hermit" --backend "$backend" run
      --no-sequentialize-threads --max-timeslice=disabled
      --network=host --tmp=/tmp -- "${common[@]}"
    )
    ;;
  strict/*)
    command=(
      "$hermit" --backend "$backend" run
      --strict --max-timeslice=disabled
      --network=host --tmp=/tmp -- "${common[@]}"
    )
    ;;
  *)
    printf 'unsupported tier/backend: %s/%s\n' "$tier" "$backend" >&2
    exit 2
    ;;
esac

server_pid=
cleanup() {
  if [[ -n ${server_pid:-} ]] && kill -0 "$server_pid" 2>/dev/null; then
    "$cli" -h 127.0.0.1 -p "$port" shutdown nosave >/dev/null 2>&1 || true
    sleep 1
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT

server_start_ns=$(date +%s%N)
(
  exec /usr/bin/time -f 'elapsed_seconds=%e\nuser_seconds=%U\nsystem_seconds=%S\nmax_rss_kb=%M\nexit=%x' \
    -o "$server_time" timeout 900 "${command[@]}"
) >"$server_stdout" 2>"$server_stderr" &
server_pid=$!

ready=0
for _ in $(seq 1 1200); do
  if "$cli" -h 127.0.0.1 -p "$port" ping 2>/dev/null | grep -qx PONG; then
    ready=1
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    break
  fi
  sleep 0.1
done

if [[ $ready -ne 1 ]]; then
  printf 'server did not become ready: %s/%s\n' "$tier" "$backend" >&2
  tail -n 100 "$server_stdout" >&2 || true
  tail -n 100 "$server_stderr" >&2 || true
  exit 3
fi

ready_ns=$(date +%s%N)
/usr/bin/time -f 'elapsed_seconds=%e\nuser_seconds=%U\nsystem_seconds=%S\nmax_rss_kb=%M\nexit=%x' \
  -o "$client_time" timeout 900 \
  "$client" --csv -t set -h 127.0.0.1 -p "$port" -n 250000 -c 5 \
  >"$client_stdout" 2>"$client_stderr"
client_rc=$?
done_ns=$(date +%s%N)

"$cli" -h 127.0.0.1 -p "$port" shutdown nosave >/dev/null
wait "$server_pid"
server_rc=$?
server_pid=

qps=$(awk -F, '/SET/ {gsub(/"/, "", $2); print $2; exit}' "$client_stdout")
scaled_ms=$(awk -v qps="$qps" 'BEGIN { if (qps > 0) printf "%.3f", 250000000 / qps; else print "NA" }')
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$tier" "$backend" "$rep" "$port" \
  "$(((ready_ns - server_start_ns) / 1000000))" \
  "$(((done_ns - ready_ns) / 1000000))" \
  "$qps" "$scaled_ms" "$client_rc/$server_rc"
