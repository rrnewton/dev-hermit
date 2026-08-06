#!/bin/bash
# readdir/getdents enumeration-ordering determinism sweep.
#
#   usage: [HERMIT=<path>] run_sweep.sh <fixture-root> <outdir>
#
# For each (guest program, directory pair, buffer size) cell this records the
# exact enumeration order produced natively and under `hermit run --strict`
# (ptrace backend, virtualize_metadata on by default), then answers:
#
#   sorted_asc / sorted_desc
#       is the GLOBAL enumeration order name-sorted ("." ".." first)?
#   asc_eq_desc
#       do two directories with the SAME name set but opposite host
#       enumeration order produce the SAME guest-visible order?  This is the
#       host-to-host / filesystem-to-filesystem axis: it is what a guest sees
#       when the identical directory content is laid out differently on disk.
#   run1_eq_run2
#       is the order stable across two hermit runs on a fixed directory?
#       (the axis `--verify` double-run parity actually covers)
#
# Plus the hardened gate: --strict --verify --verify-strict --verify-json,
# reporting bitwise_parity, on both a single-batch and a multi-batch cell.
set -uo pipefail

root="${1:?usage: run_sweep.sh <fixture-root> <outdir>}"
out="${2:?usage: run_sweep.sh <fixture-root> <outdir>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMIT="${HERMIT:?set HERMIT to the hermit binary under test}"

mkdir -p "$out/raw"
csv="$out/results.csv"
echo "cell,guest,dir_pair,bufsize,mode,batches,entries,sorted_asc,sorted_desc,asc_eq_desc,run1_eq_run2,extra" >"$csv"

# is_sorted <file> -> yes|no ; "." and ".." are expected first, then strcmp order
is_sorted() {
  awk -F'\t' '
    /^--/ { next }
    { n++; name[n] = $2 }
    END {
      i = 1
      if (n >= 1 && name[1] == ".")  { i = 2 }
      if (n >= 2 && name[2] == "..") { i = 3 }
      for (; i < n; i++) { if (name[i] > name[i+1]) { print "no"; exit } }
      print "yes"
    }' "$1"
}
names_only() { awk -F'\t' '!/^--/ {print $2}' "$1"; }

run_cell() { # run_cell <tag> <native|hermit> <cmd...>
  local tag="$1" mode="$2"; shift 2
  if [[ $mode == native ]]; then
    "$@" >"$out/raw/$tag.out" 2>"$out/raw/$tag.err"
  else
    "$HERMIT" run --strict -- "$@" >"$out/raw/$tag.out" 2>"$out/raw/$tag.err"
  fi
}

emit() { # emit <cell> <guest> <pair> <bufsize> <mode> <asc-tag> <desc-tag> <extra>
  local cell="$1" guest="$2" pair="$3" bufsz="$4" mode="$5" a="$6" d="$7" extra="$8"
  local r1r2=na
  if [[ -f "$out/raw/${a}-r2.out" ]]; then
    r1r2=$(cmp -s "$out/raw/$a.out" "$out/raw/${a}-r2.out" && echo yes || echo no)
  fi
  local batches entries
  batches=$(grep -c '^-- batch' "$out/raw/$a.out"); [[ $batches == 0 ]] && batches=n/a
  entries=$(names_only "$out/raw/$a.out" | wc -l)
  local sa sd aeqd
  sa=$(is_sorted "$out/raw/$a.out"); sd=$(is_sorted "$out/raw/$d.out")
  aeqd=$(diff -q <(names_only "$out/raw/$a.out") <(names_only "$out/raw/$d.out") >/dev/null && echo yes || echo no)
  echo "$cell,$guest,$pair,$bufsz,$mode,$batches,$entries,$sa,$sd,$aeqd,$r1r2,$extra" >>"$csv"
}

# ---- raw getdents64 cells: (dir-size, explicit bufsize) grid ----------------
for spec in "10:4096" "200:65536" "200:1024" "200:512" "2000:65536" "2000:4096"; do
  n="${spec%%:*}"; bufsz="${spec##*:}"
  for mode in native hermit; do
    a="raw-${mode}-n${n}-b${bufsz}-asc"; d="raw-${mode}-n${n}-b${bufsz}-desc"
    run_cell "$a" "$mode" "$here/fixtures/dirls_raw" "$root/asc$n"  "$bufsz" --batches
    run_cell "$d" "$mode" "$here/fixtures/dirls_raw" "$root/desc$n" "$bufsz" --batches
    [[ $mode == hermit ]] && run_cell "${a}-r2" hermit "$here/fixtures/dirls_raw" "$root/asc$n" "$bufsz" --batches
    emit "raw-n${n}-b${bufsz}" dirls_raw "asc${n}/desc${n}" "$bufsz" "$mode" "$a" "$d" ""
  done
done

# ---- glibc opendir/readdir cells (buffer chosen by glibc) -------------------
for n in 10 200 2000; do
  for mode in native hermit; do
    a="libc-${mode}-n${n}-asc"; d="libc-${mode}-n${n}-desc"
    run_cell "$a" "$mode" "$here/fixtures/dirls_libc" "$root/asc$n"
    run_cell "$d" "$mode" "$here/fixtures/dirls_libc" "$root/desc$n"
    [[ $mode == hermit ]] && run_cell "${a}-r2" hermit "$here/fixtures/dirls_libc" "$root/asc$n"
    emit "libc-n${n}" dirls_libc "asc${n}/desc${n}" glibc "$mode" "$a" "$d" ""
  done
done

# ---- a real off-the-shelf program: `ls -f` (unsorted, readdir order) -------
for n in 200 2000; do
  for mode in native hermit; do
    a="ls-${mode}-n${n}-asc"; d="ls-${mode}-n${n}-desc"
    run_cell "$a" "$mode" /usr/bin/ls -f -a "$root/asc$n"
    run_cell "$d" "$mode" /usr/bin/ls -f -a "$root/desc$n"
    [[ $mode == hermit ]] && run_cell "${a}-r2" hermit /usr/bin/ls -f -a "$root/asc$n"
    # `ls -f` prints bare names, one per line; reuse the tab-field extractor by
    # prefixing a dummy field.
    for t in "$a" "$d" "${a}-r2"; do
      [[ -f "$out/raw/$t.out" ]] && awk '{print NR-1 "\t" $0}' "$out/raw/$t.out" >"$out/raw/$t.tsv" && mv "$out/raw/$t.tsv" "$out/raw/$t.out"
    done
    emit "ls-f-n${n}" "ls -f" "asc${n}/desc${n}" glibc "$mode" "$a" "$d" ""
  done
done

# ---- telldir/seekdir round-trip --------------------------------------------
# Run against BOTH orientations. On asc* the host order already equals sorted
# order, so the determinizer permutes nothing and the round-trip is trivially
# clean; desc* is where the d_off cookies actually move.
for n in 200 2000; do
  for which in asc desc; do
    for mode in native hermit; do
      t="seek-${mode}-n${n}-${which}"
      run_cell "$t" "$mode" "$here/fixtures/seekdir_rt" "$root/${which}$n"
      verdict=$(grep -o 'verdict=[A-Z]*' "$out/raw/$t.out" | tail -1)
      mism=$(grep -o 'mismatches=[0-9]*' "$out/raw/$t.out" | tail -1)
      echo "seekdir-n${n}-${which},seekdir_rt,${which}${n},glibc,${mode},n/a,${n},na,na,na,na,${verdict}/${mism}" >>"$csv"
    done
  done
done

# ---- hardened double-run parity gates --------------------------------------
# /bin/true is the CONTROL: --verify-strict must be interpreted against it,
# because on some hosts the canonical comparator reds on DEBUG-level vdso-patch
# ordering and the "Nondeterministic realtime elapsed" line for ANY guest.
verify_cell() { # verify_cell <tag> <cmd...>   (always --strict --verify --verify-strict)
  local tag="$1"; shift
  local vj="$out/raw/$tag.json"
  "$HERMIT" run --strict --verify --verify-strict --verify-json "$vj" \
    -- "$@" >"$out/raw/$tag.out" 2>"$out/raw/$tag.err"
  local rc=$?
  local bp
  bp=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('bitwise_parity'))" "$vj" 2>/dev/null || echo "n/a")
  echo "$tag,,,,hermit-verify,n/a,n/a,na,na,na,na,bitwise_parity=${bp}(rc=$rc)" >>"$csv"
}
verify_cell "verifystrict-CONTROL-bin-true" /bin/true
for spec in "200:65536" "200:1024" "2000:4096"; do
  n="${spec%%:*}"; bufsz="${spec##*:}"
  verify_cell "verifystrict-n${n}-b${bufsz}" "$here/fixtures/dirls_raw" "$root/asc$n" "$bufsz"
done
# Plain --verify (Stripped comparator) and L3 (+heap/stack digests) on the
# worst multi-batch cell: these are the gates that are supposed to catch
# nondeterminism, and they are the ones reported in the README.
"$HERMIT" run --strict --verify -- "$here/fixtures/dirls_raw" "$root/asc2000" 4096 \
  >"$out/raw/verify-plain-n2000-b4096.out" 2>"$out/raw/verify-plain-n2000-b4096.err"
echo "verify-plain-n2000-b4096,dirls_raw,asc2000,4096,hermit-verify,n/a,n/a,na,na,na,na,rc=$?" >>"$csv"
"$HERMIT" run --strict --verify --detlog-heap --detlog-stack \
  -- "$here/fixtures/dirls_raw" "$root/asc2000" 4096 \
  >"$out/raw/verify-l3-n2000-b4096.out" 2>"$out/raw/verify-l3-n2000-b4096.err"
echo "verify-l3-n2000-b4096,dirls_raw,asc2000,4096,hermit-verify-L3,n/a,n/a,na,na,na,na,rc=$?" >>"$csv"

# ---- cross-filesystem cell (optional 2nd fixture root) ----------------------
# Same directory CONTENT and same creation order, different filesystem. This is
# the closest local proxy for "same program, different host".
alt="${ALT_ROOT:-}"
if [[ -n $alt && -d $alt/desc200 ]]; then
  for fsroot in "$root" "$alt"; do
    tagfs=$(basename "$fsroot")
    run_cell "crossfs-native-${tagfs}" native "$here/fixtures/dirls_raw" "$fsroot/desc200" 1024 --batches
    run_cell "crossfs-hermit-${tagfs}"  hermit "$here/fixtures/dirls_raw" "$fsroot/desc200" 1024 --batches
  done
  a=$(basename "$root"); b=$(basename "$alt")
  nn=$(diff -q <(names_only "$out/raw/crossfs-native-$a.out") <(names_only "$out/raw/crossfs-native-$b.out") >/dev/null && echo yes || echo no)
  hh=$(diff -q <(names_only "$out/raw/crossfs-hermit-$a.out") <(names_only "$out/raw/crossfs-hermit-$b.out") >/dev/null && echo yes || echo no)
  echo "crossfs-desc200-b1024,dirls_raw,${a} vs ${b},1024,native,n/a,202,na,na,${nn},na," >>"$csv"
  echo "crossfs-desc200-b1024,dirls_raw,${a} vs ${b},1024,hermit,n/a,202,na,na,${hh},na," >>"$csv"
fi

echo "wrote $csv"
column -s, -t "$csv"
