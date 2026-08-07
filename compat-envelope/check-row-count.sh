#!/usr/bin/env bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
#
# Refuse a scorecard CSV whose declared row count disagrees with its contents.
#
# WHY. A truncated CSV is syntactically valid and semantically wrong. The short
# file parses cleanly and reads as a COMPLETE one, because nothing in it says how
# many rows there should have been: a reader cannot tell "42 rows" from "the
# first 42 of 619". Every derived count -- denominators, pass rates, the green
# scorecard itself -- is then computed over a silently smaller population and
# reported as a result. Declaring the count moves truncation from undetectable to
# refused.
#
# THE COUNT LIVES IN A TRAILER (`#rows=N`), not a header field, because a trailer
# is the FIRST thing truncation removes. Its absence is therefore itself a
# signal, whereas a header count survives truncation and only catches it by
# arithmetic.
#
# RECORDS, NOT LINES. RFC-4180 lets a quoted field contain a literal newline, so
# one record can span several physical lines. Writer and reader must count the
# same unit or this check compares two different quantities -- which would be the
# very boundary defect it exists to catch.
#
# ABSENT IS NOT PASS, AND NOT FAIL EITHER. At the time this was written, 0 of 264
# tracked CSVs carried a declared count, so refusing on absence would break every
# consumer on day one. Absence is reported loudly as UNVERIFIED so the gap is
# visible; only a MISMATCH is a refusal. When the writer emits trailers this
# becomes an enforcing check with no further edit.

set -uo pipefail

TRAILER_PREFIX='#rows='

usage() {
    echo "usage: check-row-count.sh <csv> [<csv>...]" >&2
    exit 2
}

[ "$#" -ge 1 ] || usage

# Count DATA RECORDS: quote-aware, excluding the header and any trailer.
count_records() {
    awk -v prefix="$TRAILER_PREFIX" '
        BEGIN { inq = 0; rec = 0; buf = "" }
        {
            line = $0
            n = split(line, ch, "")
            for (i = 1; i <= n; i++) if (ch[i] == "\"") inq = !inq
            buf = buf line
            if (!inq) {
                if (buf != "" && index(buf, prefix) != 1) rec++
                buf = ""
            } else {
                buf = buf "\n"
            }
        }
        END {
            if (buf != "" && index(buf, prefix) != 1) rec++
            print (rec > 0 ? rec - 1 : 0)   # exclude the header record
        }
    ' "$1"
}

declared_count() {
    # The trailer must be the LAST non-empty line; a count buried mid-file is
    # not an authority over what follows it.
    local last
    last=$(grep -v '^[[:space:]]*$' "$1" | tail -1)
    case "$last" in
        "${TRAILER_PREFIX}"*) printf '%s' "${last#"$TRAILER_PREFIX"}" ;;
        *) printf '' ;;
    esac
}

fail=0
unverified=0
verified=0

for csv in "$@"; do
    if [ ! -f "$csv" ]; then
        echo "check-row-count: MISSING $csv" >&2
        fail=1
        continue
    fi
    declared=$(declared_count "$csv")
    actual=$(count_records "$csv")
    if [ -z "$declared" ]; then
        echo "check-row-count: UNVERIFIED $csv -- no ${TRAILER_PREFIX}N trailer; truncation is UNDETECTABLE for this file ($actual data records found)"
        unverified=$((unverified + 1))
        continue
    fi
    if [ "$declared" != "$actual" ]; then
        echo "check-row-count: TRUNCATED OR CORRUPT $csv -- declares ${declared} data rows, found ${actual}." >&2
        echo "  Refusing: every derived count would be computed over a different population than the writer produced." >&2
        fail=1
        continue
    fi
    echo "check-row-count: OK $csv (${actual} data records, declared ${declared})"
    verified=$((verified + 1))
done

echo "check-row-count: ${verified} verified, ${unverified} unverified (no declared count), $(( $# )) inspected"
exit "$fail"
