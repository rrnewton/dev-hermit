#!/usr/bin/env bash
set -euo pipefail

tier=${1:?tier}
backend=${2:?backend}
out=${3:?output directory}
root=/tmp/gvisor-tensorflow-repro-238b-root
reverie=/home/newton/work/dev-hermit/worktrees/238b/reverie

mkdir -p "$out"
printf 'tier\tbackend\tworkload\telapsed_seconds\trc\n' >"$out/summary.tsv"

workloads=(
  2_BasicModels/gradient_boosted_decision_tree.py
  2_BasicModels/kmeans.py
  2_BasicModels/logistic_regression.py
  2_BasicModels/nearest_neighbor.py
  2_BasicModels/random_forest.py
  3_NeuralNetworks/convolutional_network.py
  3_NeuralNetworks/multilayer_perceptron.py
  3_NeuralNetworks/neural_network.py
)

for workload in "${workloads[@]}"; do
  slug=${workload//\//-}
  run="$out/$slug"
  mkdir -p "$run/tmp"
  cp -a --reflink=auto "$root/tmp/." "$run/tmp/"

  guest=(
    bwrap --unshare-all --share-net --die-with-parent
    --ro-bind "$root" /
    --ro-bind /etc/resolv.conf /etc/resolv.conf
    --ro-bind /etc/hosts /etc/hosts
    --dev /dev --proc /proc
    --bind "$run/tmp" /tmp
    --setenv HOME /root
    --setenv PYTHONPATH /TensorFlow-Examples/examples
    --chdir /TensorFlow-Examples/examples
    /usr/bin/python "$workload"
  )

  case "$tier/$backend" in
    native/native)
      command=("${guest[@]}")
      ;;
    counter2/ptrace)
      command=("$reverie/target/release/counter2" -- "${guest[@]}")
      ;;
    *)
      printf 'unsupported tier/backend: %s/%s\n' "$tier" "$backend" >&2
      exit 2
      ;;
  esac

  set +e
  /usr/bin/time -f '%e' -o "$run/elapsed.txt" timeout 900 "${command[@]}" \
    >"$run/stdout.log" 2>"$run/stderr.log"
  rc=$?
  set -e
  elapsed=$(tail -n 1 "$run/elapsed.txt")
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$tier" "$backend" "$workload" "$elapsed" "$rc" | tee -a "$out/summary.tsv"
  [[ $rc -eq 0 ]] || exit "$rc"
done
