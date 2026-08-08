#!/usr/bin/env python3
"""Regression gate for a ratchet series. A decrease must be justified or refused.

A ratchet is only a ratchet if something stops it turning backwards. Without a
gate it is a number someone reads occasionally, and the failure mode is precise:
a regression appears, and the cheapest way to make it go away is to loosen the
definition back to one the number already cleared.

So this gate enforces three things, in order of how easy they are to abuse:

1. **A decrease under the SAME definition is a REGRESSION and is REFUSED.**
   No exceptions, no tolerance band. If the number went down and nothing about
   the definition changed, something got worse.

2. **A decrease under a NEW definition is a RE-BASELINE — accepted, and logged
   as such.** A tightening legitimately drops the number. It opens a new block
   with its own floor and makes no claim against earlier blocks.

3. **Re-using a definition the series has already moved past is REFUSED.**
   This is the move the whole tightening exists to prevent: recover a number by
   going back to the weaker rule. It is not a re-baseline, it is a retreat, and
   naming it separately is the only way it stays visible.

A point with no definition SHA is refused outright. Comparability is not
something a caller may leave unstated.

Usage::

    ratchet_gate.py --series S.json --check-point new.json
    ratchet_gate.py --series S.json --report

Exit codes: 0 accepted, 1 REFUSED, 2 the series or point is unusable.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ACCEPT_INCREASE = "ACCEPT-INCREASE"
ACCEPT_LEVEL = "ACCEPT-LEVEL"
ACCEPT_REBASELINE = "ACCEPT-REBASELINE"
REFUSE_REGRESSION = "REFUSE-REGRESSION"
REFUSE_RETREAT = "REFUSE-RETREAT"
REFUSE_MALFORMED = "REFUSE-MALFORMED"

ACCEPTED = frozenset((ACCEPT_INCREASE, ACCEPT_LEVEL, ACCEPT_REBASELINE))


class SeriesError(ValueError):
    """The series or the proposed point cannot be trusted."""


@dataclass
class Decision:
    outcome: str
    previous: dict | None
    proposed: dict
    detail: str

    @property
    def accepted(self) -> bool:
        return self.outcome in ACCEPTED

    def render(self) -> str:
        p = self.previous
        lines = ["ratchet gate"]
        if p is None:
            lines.append("  previous : (none — this is the first point in the series)")
        else:
            lines.append(
                f"  previous : {p['id']} value={p['value']} "
                f"({p.get('measured','?')}/{p.get('total','?')}) "
                f"defn={p['definition_sha'][:12]}"
            )
        n = self.proposed
        lines.append(
            f"  proposed : {n.get('id','(unnamed)')} value={n['value']} "
            f"({n.get('measured','?')}/{n.get('total','?')}) "
            f"defn={str(n.get('definition_sha',''))[:12] or '(none)'}"
        )
        lines.append(f"  outcome  : {self.outcome}")
        lines.append(f"  detail   : {self.detail}")
        return "\n".join(lines)


def _require(point: dict, where: str) -> None:
    for field in ("value", "definition_sha"):
        if point.get(field) in (None, ""):
            raise SeriesError(f"{where}: missing required field {field!r}")
    if not isinstance(point["value"], int) or point["value"] < 0:
        raise SeriesError(f"{where}: value must be a non-negative integer")
    for field in ("measured", "total"):
        if point.get(field) is None:
            raise SeriesError(
                f"{where}: missing {field!r}; a bare count is not a ratchet point"
            )
    if point["total"] == 0:
        raise SeriesError(f"{where}: total of 0 — a point over an empty population is not a result")
    if point["measured"] > point["total"]:
        raise SeriesError(f"{where}: measured {point['measured']} exceeds total {point['total']}")


def load_series(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SeriesError(f"unreadable series {path}: {error}") from error
    if not isinstance(data.get("points"), list) or not data["points"]:
        raise SeriesError(f"{path}: series has no points; refusing to gate against nothing")
    for i, p in enumerate(data["points"]):
        _require(p, f"{path} point {i}")
    return data


def evaluate(series: dict, proposed: dict) -> Decision:
    _require(proposed, "proposed point")
    points = series["points"]
    previous = points[-1]
    defs = series.get("definitions", {})

    new_sha = proposed["definition_sha"]
    old_sha = previous["definition_sha"]

    if new_sha == old_sha:
        if proposed["value"] < previous["value"]:
            return Decision(
                REFUSE_REGRESSION, previous, proposed,
                f"value fell {previous['value']} -> {proposed['value']} under an UNCHANGED "
                f"definition ({new_sha[:12]}). That is a real regression. Loosening the "
                f"definition to recover it would be refused as a retreat.",
            )
        outcome = ACCEPT_INCREASE if proposed["value"] > previous["value"] else ACCEPT_LEVEL
        return Decision(
            outcome, previous, proposed,
            f"value {previous['value']} -> {proposed['value']} under the same definition.",
        )

    # A definition change. Is it forward, or a retreat to something already passed?
    seen = [p["definition_sha"] for p in points]
    if new_sha in seen:
        return Decision(
            REFUSE_RETREAT, previous, proposed,
            f"definition {new_sha[:12]} was already used earlier in this series and the "
            f"series has moved past it. Returning to a superseded definition is a RETREAT, "
            f"not a re-baseline — this is exactly the move the tightening exists to prevent.",
        )
    old_meta = defs.get(old_sha, {})
    new_meta = defs.get(new_sha, {})
    old_strict = old_meta.get("strictness")
    new_strict = new_meta.get("strictness")
    if old_strict is not None and new_strict is not None and new_strict < old_strict:
        return Decision(
            REFUSE_RETREAT, previous, proposed,
            f"definition {new_sha[:12]} (strictness {new_strict}) is WEAKER than the current "
            f"{old_sha[:12]} (strictness {old_strict}). A ratchet may not be re-based onto a "
            f"looser rule.",
        )
    if not proposed.get("rebaseline"):
        return Decision(
            REFUSE_MALFORMED, previous, proposed,
            f"definition changed {old_sha[:12]} -> {new_sha[:12]} but the point does not "
            f"declare rebaseline=true. A definition change must be stated, not inferred from "
            f"the SHA differing.",
        )
    if not str(proposed.get("rebaseline_reason", "")).strip():
        return Decision(
            REFUSE_MALFORMED, previous, proposed,
            "rebaseline=true requires rebaseline_reason naming the definition change.",
        )
    direction = "drops" if proposed["value"] < previous["value"] else "moves"
    return Decision(
        ACCEPT_REBASELINE, previous, proposed,
        f"RE-BASELINE: definition {old_sha[:12]} -> {new_sha[:12]}; value {direction} "
        f"{previous['value']} -> {proposed['value']}. New block starts its own floor; no "
        f"monotonicity is claimed across the change. Reason: {proposed['rebaseline_reason']}",
    )


def report(series: dict) -> str:
    lines = [f"ratchet series: {series.get('series','(unnamed)')}"]
    prev = None
    for p in series["points"]:
        marker = ""
        if prev is not None:
            if p["definition_sha"] != prev["definition_sha"]:
                marker = "  <== RE-BASELINE (new definition block)"
            elif p["value"] < prev["value"]:
                marker = "  <== REGRESSION IN RECORDED HISTORY"
        blk = series.get("definitions", {}).get(p["definition_sha"], {}).get("block", "?")
        lines.append(
            f"  {p['id']:8} {p['date']}  block={blk}  value={p['value']:>6}  "
            f"{p.get('measured')}/{p.get('total')}  defn={p['definition_sha'][:12]}{marker}"
        )
        prev = p
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--series", required=True, type=Path)
    ap.add_argument("--check-point", type=Path)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)

    try:
        series = load_series(args.series)
    except SeriesError as error:
        print(f"ratchet-gate: REFUSED: {error}", file=sys.stderr)
        return 2

    if args.report:
        print(report(series))

    if args.check_point:
        try:
            proposed = json.loads(args.check_point.read_text())
            decision = evaluate(series, proposed)
        except (OSError, json.JSONDecodeError) as error:
            print(f"ratchet-gate: REFUSED: unreadable point: {error}", file=sys.stderr)
            return 2
        except SeriesError as error:
            print(f"ratchet-gate: REFUSED: {error}", file=sys.stderr)
            return 1
        print(decision.render())
        return 0 if decision.accepted else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
