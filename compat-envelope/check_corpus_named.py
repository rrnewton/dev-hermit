#!/usr/bin/env python3
"""REFUSE a per-backend ratio that does not name the corpus it was measured over.

This is the enforcement half of `corpus_registry.py`. Point it at emitted text
(a collector's summary, a rendered scorecard, a report) and it fails closed on
any line that scopes a ratio to a backend without naming a registered corpus.

WHAT COUNTS AS A VIOLATION
--------------------------
A line that contains BOTH

  * a backend name from `corpus_registry.BACKENDS`, and
  * a ratio -- `N/M`, or a percentage, or a `det N` / `parity N` count,

but NOT a registered corpus marker, and which is not inside a block whose
heading already named one (see SCOPE below).

`183/184`, `20/20`, and `4/137` are all "e9patch". Only the corpus separates
them, so the corpus is not decoration on the line -- it is half the claim.

SCOPE: A HEADING CAN QUALIFY THE LINES UNDER IT
-----------------------------------------------
Requiring the corpus on literally every physical line would make a table
unreadable and would push authors toward dropping the table instead. So a
markdown heading, an ASCII table header, or an explicit `corpus=` line sets the
corpus for the lines that follow, until the next heading. That is a real
qualification -- a reader scrolling to a row sees the section it lives in.

What it deliberately does NOT accept is the document TITLE alone rescuing a
number quoted 180 lines later in prose, which is how `REPORT.md` ended up
publishing "e9patch 183/184 (99.46%)" in a paragraph a reader can quote whole.
Use `--strict-lines` to require the marker on the line itself and see that gap.

EXIT
----
0  every backend ratio is qualified
1  at least one is not (each printed with file:line)
2  usage
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_registry import BACKENDS, all_markers  # noqa: E402

# `N/M` not preceded/followed by path or version characters, so `a/b.tsv`,
# `2026/08/07`, and `v1/2` do not false-match.
_RATIO = re.compile(r"(?<![\w./-])\d{1,5}\s*/\s*\d{1,5}(?![\w./-])")
_PERCENT = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d+)?\s*%")
# `det 179`, `parity 173`, `patched_sites=4` -- a bare count scoped to a backend
# is the same defect wearing different punctuation.
_SCOPED_COUNT = re.compile(
    r"\b(?:det|deterministic|parity|stdout[_-]?parity|reach|mapped_sites|"
    r"candidate_sites|patched_sites)\b\s*[=:]?\s*\d+", re.I)
# Alphanumeric boundaries, NOT `\b`. A column name is `e9patch_det_pct`, and
# `\be9patch\b` does not match it because `_` is a word character -- so the
# entire `--tsv` wide-table path looked backend-free and sailed through the gate.
# `_` and `-` separate a backend name from its metric; letters and digits do not.
_BACKEND = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(BACKENDS) + r")(?![A-Za-z0-9])", re.I)
# An explicit declaration an emitter can print to qualify what follows.
_DECLARES = re.compile(r"\bcorpus\s*[=:]\s*(\S+)", re.I)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
# A rule of dashes/equals is AMBIGUOUS: in markdown it underlines a setext
# heading, but in an ASCII table it is the separator printed directly beneath the
# column header. Treating it as a heading closed the table scope one line after
# it opened, so every row of `render-scorecard.rs`'s human table escaped the gate
# while the tool still looked checked. A rule is therefore NEUTRAL for table
# scope, and only promotes the PREVIOUS line to a heading (the setext case).
_RULE = re.compile(r"^\s*[-=]{3,}\s*$")


@dataclass
class Violation:
    path: str
    line_no: int
    text: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line_no}: {self.reason}\n    {self.text.strip()[:150]}"


def _markers_in(line: str) -> Optional[str]:
    low = line.lower()
    # Longest marker first so `full-corpus` wins over a bare `corpus`.
    for marker, name in sorted(all_markers().items(), key=lambda kv: -len(kv[0])):
        if marker in low:
            return name
    return None


def _is_table_header_naming_backends(line: str) -> bool:
    """A WIDE TABLE puts the backend in the COLUMN HEADER and the ratio in the row.

    `render-scorecard.rs --tsv` emits exactly that:

        bucket  ptrace  e9patch_stdout_parity_pct  e9patch_det_pct  ...
        applications  1  100.0  100.0  1/1  1/1

    Row 2 is unmistakably an e9patch ratio, yet the word "e9patch" is nowhere on
    it. A line-local check calls that clean and the whole machine-readable path
    escapes the gate -- which is how the TSV came to carry no population at all
    while the human table carried `Input CSV:`. So a header that names two or
    more backend columns opens a table scope, and the rows under it inherit
    backend-scoping until a blank line or a new heading closes it.
    """
    fields = re.split(r"\t|\s{2,}|\|", line)
    named = {m.group(1).lower() for f in fields for m in [_BACKEND.search(f)] if m}
    if len(named) < 2:
        return False
    # Header, not data: no field is a ratio/percentage value.
    return not any(_RATIO.search(f) or _PERCENT.search(f) for f in fields)


def scan(lines: Iterable[str], path: str = "<stdin>",
         strict_lines: bool = False) -> list[Violation]:
    """Return every backend ratio that cannot name its corpus."""
    out: list[Violation] = []
    section_corpus: Optional[str] = None
    table_backends = False
    prev = ""
    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")

        # A blank line closes a table scope (but not a section's corpus).
        if not line.strip():
            table_backends = False
            prev = line
            continue
        # A rule line is neutral for table scope; it only promotes the previous
        # line to a setext heading when that line could plausibly be one.
        if _RULE.match(line):
            if prev.strip() and not (_RATIO.search(prev) or _PERCENT.search(prev)):
                named = _markers_in(prev)
                if named is not None:
                    section_corpus = named
            prev = line
            continue
        # A `#` heading resets BOTH scopes; if it names a corpus, it sets one.
        if _HEADING.match(line):
            section_corpus = _markers_in(line)
            table_backends = False
            prev = line
            continue
        if _is_table_header_naming_backends(line):
            table_backends = True
            if _markers_in(line) is not None:
                section_corpus = _markers_in(line)
            prev = line
            continue
        # An explicit declaration also sets scope, and is the form an emitter
        # should print. `corpus=<name>` must name a REGISTERED corpus: a free
        # string would let an emitter satisfy the gate by inventing a word.
        decl = _DECLARES.search(line)
        if decl:
            named = _markers_in(line)
            if named is None:
                out.append(Violation(
                    path, i, line,
                    f"declares corpus={decl.group(1)!r} which is not in the "
                    f"registry -- an unregistered name qualifies nothing"))
                section_corpus = None
            else:
                section_corpus = named
            # A declaration line may itself also carry ratios; fall through.

        prev = line
        has_backend = bool(_BACKEND.search(line)) or table_backends
        has_ratio = bool(_RATIO.search(line) or _PERCENT.search(line)
                         or _SCOPED_COUNT.search(line))
        if not (has_backend and has_ratio):
            continue

        on_line = _markers_in(line)
        if on_line is not None:
            continue
        if not strict_lines and section_corpus is not None:
            continue
        scope = "on this line" if strict_lines else "on this line or in its section heading"
        out.append(Violation(
            path, i, line,
            f"backend ratio with no registered corpus {scope} -- "
            f"'20/20' and '4/137' are the same backend"))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="files to check; omit to read stdin")
    ap.add_argument("--strict-lines", action="store_true",
                    help="require the corpus on the ratio's own line, not just its section")
    ap.add_argument("--count-only", action="store_true")
    a = ap.parse_args(argv)

    violations: list[Violation] = []
    if not a.paths:
        violations += scan(sys.stdin.read().splitlines(), "<stdin>", a.strict_lines)
    else:
        for p in a.paths:
            try:
                text = Path(p).read_text(errors="replace")
            except OSError as e:
                print(f"check-corpus-named: cannot read {p}: {e}", file=sys.stderr)
                return 2
            violations += scan(text.splitlines(), p, a.strict_lines)

    if a.count_only:
        print(len(violations))
        return 1 if violations else 0
    if not violations:
        print("check-corpus-named: OK -- every backend ratio names a registered corpus")
        return 0
    print(f"check-corpus-named: REFUSED -- {len(violations)} backend ratio(s) "
          f"with no registered corpus:", file=sys.stderr)
    for v in violations:
        print(f"  {v}", file=sys.stderr)
    print("\nAdd the corpus to the line (or to the section heading), or register a "
          "new one in compat-envelope/corpus_registry.py.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
