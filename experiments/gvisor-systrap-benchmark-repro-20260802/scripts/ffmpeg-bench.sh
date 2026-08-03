#!/usr/bin/env bash
set -euo pipefail

tier=${1:?tier}
backend=${2:?backend}
rep=${3:?repetition}
out=${4:?output directory}

root=/tmp/gvisor-ffmpeg-repro-238b-root
reverie=/home/newton/work/dev-hermit/worktrees/238b/reverie
hermit=/home/newton/work/dev-hermit/hermit/target/release/hermit

mkdir -p "$out"
slug=${tier}-${backend}-${rep}
output=$out/$slug.mp4
stdout=$out/$slug.stdout
stderr=$out/$slug.stderr
timing=$out/$slug.time
guest=(
  bwrap --unshare-all --share-net --die-with-parent
  --ro-bind "$root" / --dev /dev --proc /proc
  --bind "$out" /mnt --chdir /media
  /usr/bin/ffmpeg
  -nostdin -hide_banner -loglevel error -y
  -i video.mp4
  -c:v libx264 -preset veryslow "/mnt/$slug.mp4"
)

case "$tier/$backend" in
  native/native)
    command=("${guest[@]}")
    ;;
  counter2/ptrace)
    command=("$reverie/target/release/counter2" -- "${guest[@]}")
    ;;
  counter2/kvm)
    command=("$reverie/target/release/reverie-kvm-counter2" "${guest[@]}")
    ;;
  counter2/liteinst)
    command=("$reverie/target/release/reverie-liteinst-examples" --tool counter2 -- "${guest[@]}")
    ;;
  counter2/dbi)
    command=("$reverie/target/release/reverie-dbi-counter2-exact" -- "${guest[@]}")
    ;;
  counter2/sabre)
    command=(
      "$reverie/target/release/reverie-sabre-strace"
      --sabre "$reverie/target/sabre/sabre"
      --plugin "$reverie/target/release/libreverie_sabre_strace_plugin.so"
      --tool counter2-exact -- "${guest[@]}"
    )
    ;;
  counter2/e9patch)
    command=("$reverie/target/release/reverie-e9patch-counter2" -- "${guest[@]}")
    ;;
  relaxed/*)
    command=(
      "$hermit" --backend "$backend" run
      --no-sequentialize-threads --max-timeslice=disabled
      --tmp=/tmp -- "${guest[@]}"
    )
    ;;
  strict/*)
    command=(
      "$hermit" --backend "$backend" run
      --strict --max-timeslice=disabled
      --tmp=/tmp -- "${guest[@]}"
    )
    ;;
  *)
    printf 'unsupported tier/backend: %s/%s\n' "$tier" "$backend" >&2
    exit 2
    ;;
esac

set +e
/usr/bin/time -f 'elapsed_seconds=%e\nuser_seconds=%U\nsystem_seconds=%S\nmax_rss_kb=%M\nexit=%x' \
  -o "$timing" timeout 900 "${command[@]}" >"$stdout" 2>"$stderr"
rc=$?
set -e
elapsed=$(awk -F= '$1 == "elapsed_seconds" {print $2}' "$timing")
size=$(stat -c %s "$output" 2>/dev/null || printf 0)
printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$tier" "$backend" "$rep" "$elapsed" "$rc" "$size"
[[ $rc -eq 0 && $size -gt 1000000 ]]
