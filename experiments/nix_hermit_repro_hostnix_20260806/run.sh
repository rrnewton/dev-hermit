#!/usr/bin/env bash
# run.sh — reproduce this experiment end to end.
#
# Prerequisite: `./bootstrap.sh check` must report a working host nix.
# `/nix` is EPHEMERAL on Meta devservers (chef reverts it); see bootstrap.sh.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
./bootstrap.sh check || true

N="${N:-10}"

echo "### 1. dose sweep on three derivations (the study)"
for probe in \
  "nondet-time|(import ./nix/nondet-time.nix) {}" \
  "nondet-demo|(import ./nix/nondet-demo.nix) {}" \
  "urandom|(import ./nix/real-candidates.nix {}).urandom-temp-names" ; do
  name="${probe%%|*}"; expr="${probe#*|}"
  bash harness/dose-sweep.sh "$expr" "$N" "$name"
done

echo "### 2. per-process cost of the seam"
bash harness/spawn-cost.sh 200 3

echo "### 3. real nixpkgs packages"
ALWAYS_HERMIT=1 bash harness/screen-batch.sh candidates-real.tsv 3 2
bash harness/screen-batch.sh candidates-haskell.tsv 3 3
ALWAYS_HERMIT=1 bash harness/screen-batch.sh candidates-buildability.tsv 2 4

echo "### 4. ergonomic opt-in overlay (evaluation-level gate)"
bash harness/ergonomics-check.sh

echo "### 5. content-addressed store assessment"
bash harness/ca-probe.sh

echo "### 6. collect"
python3 harness/collect-results.py
