#!/bin/bash
# Writes one file under host /tmp (where Nix puts its build directory when
# `sandbox = false`) and one outside /tmp (standing in for $out in /nix/store).
# The caller checks from the HOST whether each survived the run.
mkdir -p "$RB_TMP_TARGET" "$RB_OUT_TARGET"
echo "tmp-write-ok" > "$RB_TMP_TARGET/witness"
echo "out-write-ok" > "$RB_OUT_TARGET/witness"
