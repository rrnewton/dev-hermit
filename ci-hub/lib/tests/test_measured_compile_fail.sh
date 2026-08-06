#!/usr/bin/env bash
# Compile-fail proof for ci-hub/lib/measured.rs.
#
# measured.rs claims a bare count "does not compile". A #[test] cannot prove
# that -- a runtime test only ever exercises code that ALREADY compiled. This
# asserts the negative directly: plant each violation, run rustc, require it to
# be REFUSED. It also asserts the positive, because a type that rejects
# everything is as useless as one that rejects nothing.
set -uo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
lib="$root/ci-hub/lib/measured.rs"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cp "$lib" "$tmp/lib.rs"

fails=0

probe() { # probe <expect refused|accepted> <label> <body>
  local expect="$1" label="$2" body="$3"
  cat > "$tmp/p.rs" <<EOF
#[path = "lib.rs"] mod measured;
use measured::Measured;
fn main() { $body }
EOF
  if rustc --edition 2021 -o "$tmp/p" "$tmp/p.rs" >"$tmp/err" 2>&1; then got=accepted; else got=refused; fi
  if [ "$got" = "$expect" ]; then
    printf 'ok       %-46s (%s)\n' "$label" "$got"
  else
    printf 'FAIL     %-46s expected %s got %s\n' "$label" "$expect" "$got"; fails=$((fails+1))
  fi
}

# NEGATIVE: every way of getting a count without its denominator must be refused.
probe refused  "bare count via of()"        'let _m = Measured::of(5);'
probe refused  "integer coerced to Measured" 'let _m: Measured = 5;'
probe refused  "read value in isolation"     'let m = Measured::of(3,10,"c"); let _v = m.value;'
probe refused  "struct literal bypass"       'let _m = Measured { value: 5, denominator: 10, conditions: "c".into() };'
# POSITIVE: a properly-qualified value must still work, or the type is inert.
probe accepted "qualified construction"      'let m = Measured::of(3,10,"dag_jobs=16"); assert!(!format!("{m}").is_empty());'

echo "compile-fail proof: $fails failure(s)"
exit $((fails > 0))
