#!/bin/bash
H="$1"; OUT="$2"; RUNS="${3:-30}"
mkdir -p "$OUT"; : > "$OUT/attempts.tsv"
printf 'cell\tbackend\trun\trc\tdetlog_records\tmapped_sites\trefusal\n' >> "$OUT/attempts.tsv"
G=/home/newton/work/dev-hermit/scratch/w7-liteinst-maps/e9g
for cmd in "$G/inline_syscall_sites" "$G/mixed_inline_and_libc_syscalls" "$G/static_nolibc_syscall_sites"; do
  tag=$(basename "$cmd")
  for be in ptrace kvm dbi sabre e9patch liteinst; do
    for r in $(seq 1 "$RUNS"); do
      timeout 120 "$H" --log=info --backend="$be" run --strict --base-env=minimal -- "$cmd" \
        >/dev/null 2>"$OUT/.err"
      rc=$?
      grep -o 'DETLOG .*' "$OUT/.err" > "$OUT/$tag.$be.$r.d"
      n=$(wc -l < "$OUT/$tag.$be.$r.d")
      ms=$(grep -o 'mapped_sites=[0-9]*' "$OUT/.err" | head -1)
      ref=""; [ "$n" -eq 0 ] && ref=$(grep -m1 -o 'Error:.*' "$OUT/.err" | cut -c1-140)
      [ "$rc" -eq 124 ] && ref="TIMEOUT 120s"
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$tag" "$be" "$r" "$rc" "$n" "${ms:-n/a}" "$ref" >> "$OUT/attempts.tsv"
    done
  done
done
rm -f "$OUT/.err"
