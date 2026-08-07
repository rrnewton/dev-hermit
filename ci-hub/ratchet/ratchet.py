#!/usr/bin/env python3
"""The MONOTONIC RATCHET SET: metrics that should only go up, and the
re-baseline discipline that keeps a legitimate definition change from reading as
a regression.

WHY THIS EXISTS, AND WHAT IT IS DEFENDING AGAINST
-------------------------------------------------
Each metric in the set is monotonic **under a fixed definition**. When a
definition TIGHTENS the number legitimately DROPS: parity depth falls when it
deepens from stdout-only to detlog/stack/heap; the compat envelope falls when a
relaxation flag stops counting.

If that drop is read as a regression, the obvious way to "fix" it is to loosen
the definition back — which is precisely the fake-green move this codebase has
spent considerable effort removing. So the drop must be *classifiable*:

    a drop WITH a recorded definition change  -> REBASELINE (expected, fine)
    a drop WITHOUT one                        -> REGRESSION (real, act on it)

That single distinction is the whole value of keeping the set, and it is why
`definition_version` is mandatory on every metric rather than advisory.

THREE STATES THAT MUST NOT COLLAPSE INTO EACH OTHER
---------------------------------------------------
A metric can be *worse*, *unmeasured*, or *newly added*, and all three are easy
to render as "0":

* `value: null` means NOT MEASURED. It never compares as a drop. Treating an
  absent measurement as zero manufactures a regression out of silence, which
  then pressures someone to "fix" a number that was never taken.
* a metric absent from the baseline is NEW, not an improvement from zero.
* zero is a real measured value and does compare.

EVERY VALUE CARRIES ITS DENOMINATOR
-----------------------------------
`8` is not a measurement; `8 of 85 cells` is. A bare count invites comparison
against a differently-scoped count, which is the denominator confusion that put
`open_prs=105` next to `open_prs=10` in the status log. A denominator change is
therefore treated exactly like a definition change: it forces a re-baseline.

AND EVERY VALUE CARRIES HOW MUCH WAS MEASURED
---------------------------------------------
`denominator` says WHAT is counted; `coverage` says HOW MUCH was measured to
produce the figure. They are not interchangeable, and the difference is what
lets a stale number pass as current:

* a depth of 3 "detcore commits identical to the ptrace golden" reads the same
  whether ONE guest produced it or twenty did;
* a regenerated record that measured NOTHING keeps the old value and classifies
  FLAT/"unchanged" -- a run that measured nothing rendering exactly like one
  that measured everything;
* two readings taken on DIFFERENT guests look like a contradiction rather than
  two different samples, so the natural response is to argue about which is
  right instead of reporting both with their scopes.

So a published (non-null) value must carry `coverage: {measured, total, unit}`,
and `measured: 0` with a published value is REFUSED outright -- if nothing was
measured, the honest record is `value: null` plus an `unmeasured_reason`, which
already classifies as UNMEASURED and can never read as unchanged. `total: null`
is permitted, because an undefined population is a real state, but it must say
so in `total_unknown_reason` rather than being left blank.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

RECORD = Path(__file__).with_name("metrics.json")

#: Movement classes.
UP = "up"
FLAT = "flat"
REGRESSION = "regression"          # a drop with NO recorded definition change
REBASELINE = "rebaseline"          # a drop explained by a recorded change
NEW = "new"                        # not present in the baseline
UNMEASURED = "unmeasured"          # value is null on either side

#: The only classes that should ever page someone.
ACTIONABLE = frozenset({REGRESSION})


class RatchetError(Exception):
    """A malformed record. Raised rather than defaulted: a metric that silently
    loses its definition_version stops being classifiable at all."""


@dataclass(frozen=True)
class Movement:
    metric: str
    verdict: str
    before: Any
    after: Any
    denominator_before: str | None
    denominator_after: str | None
    definition_before: str | None
    definition_after: str | None
    coverage_before: Any
    coverage_after: Any
    detail: str

    def line(self) -> str:
        def show(v, d, cov):
            if v is None:
                return "unmeasured"
            base = f"{v} of {d}" if d else str(v)
            return base + format_coverage(cov)
        return (f"{self.metric:38} {self.verdict:11} "
                f"{show(self.before, self.denominator_before, self.coverage_before)} -> "
                f"{show(self.after, self.denominator_after, self.coverage_after)}"
                f"   {self.detail}")


REQUIRED_FIELDS = ("value", "denominator", "definition_version", "definition")

#: `denominator` says WHAT the number counts. `coverage` says HOW MUCH was
#: actually measured to produce it, and the two are not interchangeable: a depth
#: of 3 "detcore commits identical to the ptrace golden" is the same prose
#: whether one guest produced it or twenty did. Without a measured count a
#: regenerated record that measured NOTHING leaves the old value in place and
#: classifies FLAT/"unchanged" -- a run that measured nothing rendering exactly
#: like one that measured everything. Two readings taken on different guests
#: also look like a contradiction rather than two different samples.
COVERAGE_UNKNOWN_TOTAL_REASON = "total_unknown_reason"


def validate_coverage(name: str, metric: dict) -> None:
    """A published value must say how much was measured to produce it.

    Only enforced when `value` is non-null: an explicitly unmeasured metric
    already carries `unmeasured_reason` and has no sample to describe.
    """
    coverage = metric.get("coverage")
    if not isinstance(coverage, dict):
        raise RatchetError(
            f"{name}: value is published but no `coverage` object. State "
            f"{{measured, total, unit}} -- how many units were ACTUALLY "
            f"measured for this figure, out of how many exist. Without it a "
            f"regeneration that measured nothing reads as `unchanged`."
        )
    measured = coverage.get("measured")
    if type(measured) is not int or measured < 0:
        raise RatchetError(f"{name}: coverage.measured must be an integer >= 0")
    if not str(coverage.get("unit") or "").strip():
        raise RatchetError(
            f"{name}: coverage.unit must name what is being counted (for "
            f"example 'guest x backend pairs'); 3/5 of an unnamed thing is not "
            f"a denominator."
        )
    if measured == 0:
        raise RatchetError(
            f"{name}: coverage.measured is 0 but a value is published. A "
            f"figure produced by zero measurements is a STALE NUMBER, not a "
            f"measurement -- set value to null with an `unmeasured_reason` so "
            f"it classifies as unmeasured instead of rendering unchanged."
        )
    total = coverage.get("total")
    if total is None:
        if not str(coverage.get(COVERAGE_UNKNOWN_TOTAL_REASON) or "").strip():
            raise RatchetError(
                f"{name}: coverage.total is null but no "
                f"`{COVERAGE_UNKNOWN_TOTAL_REASON}`. An undefined population is "
                f"a real and reportable state, but it must be stated, not left "
                f"blank -- a reader cannot otherwise tell a partial sample from "
                f"a complete one."
            )
        return
    if type(total) is not int or total < measured:
        raise RatchetError(
            f"{name}: coverage.total must be an integer >= coverage.measured "
            f"({measured}); got {total!r}"
        )


def format_coverage(coverage: Any) -> str:
    """Render a coverage object for a report line, or '' when absent."""
    if not isinstance(coverage, dict):
        return ""
    measured = coverage.get("measured")
    total = coverage.get("total")
    unit = str(coverage.get("unit") or "units")
    shown_total = "?" if total is None else total
    return f" [{measured}/{shown_total} {unit}]"


def load(path: Any = RECORD) -> dict:
    data = json.loads(Path(path).read_text())
    validate_record(data)
    return data


def validate_record(data: dict) -> None:
    """Every metric must be classifiable. Refuse anything that is not."""
    metrics = data.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise RatchetError("record has no `metrics` object")
    for name, m in metrics.items():
        if not isinstance(m, dict):
            raise RatchetError(f"{name}: metric must be an object")
        for field in REQUIRED_FIELDS:
            if field not in m:
                raise RatchetError(
                    f"{name}: missing {field!r}. Every metric must carry its "
                    f"denominator and definition_version or a later drop cannot "
                    f"be told apart from a re-baseline."
                )
        if m["value"] is not None and not isinstance(m["value"], (int, float)):
            raise RatchetError(f"{name}: value must be a number or null (unmeasured)")
        if m["value"] is not None:
            validate_coverage(name, m)
        if m["value"] is None and not m.get("unmeasured_reason"):
            raise RatchetError(
                f"{name}: value is null but no `unmeasured_reason` given. An "
                f"unexplained blank is indistinguishable from a forgotten "
                f"measurement."
            )


def compare_metric(name: str, before: dict | None, after: dict) -> Movement:
    """Classify one metric's movement. See the module docstring for the rules."""
    def mk(verdict: str, detail: str) -> Movement:
        return Movement(
            metric=name, verdict=verdict,
            before=(before or {}).get("value"), after=after.get("value"),
            denominator_before=(before or {}).get("denominator"),
            denominator_after=after.get("denominator"),
            definition_before=(before or {}).get("definition_version"),
            definition_after=after.get("definition_version"),
            coverage_before=(before or {}).get("coverage"),
            coverage_after=after.get("coverage"),
            detail=detail,
        )

    if before is None:
        return mk(NEW, "not in the baseline; nothing to compare against")

    old, new = before.get("value"), after.get("value")
    if old is None or new is None:
        # Never a drop. An absent measurement is silence, not evidence.
        return mk(UNMEASURED, after.get("unmeasured_reason")
                  or before.get("unmeasured_reason") or "value not measured")

    def_changed = before.get("definition_version") != after.get("definition_version")
    den_changed = before.get("denominator") != after.get("denominator")

    if new > old:
        if def_changed or den_changed:
            # Going UP across a definition change is not comparable either: the
            # two numbers measure different things, so the rise is not evidence
            # of progress. Say so rather than banking it.
            return mk(REBASELINE,
                      "value rose BUT definition/denominator changed — not "
                      "comparable; re-baseline rather than claim progress")
        return mk(UP, "ratchet advanced under a fixed definition")

    if new == old and not (def_changed or den_changed):
        return mk(FLAT, "unchanged")

    if def_changed or den_changed:
        reason = after.get("rebaseline_reason")
        if not reason:
            raise RatchetError(
                f"{name}: definition/denominator changed but no "
                f"`rebaseline_reason` recorded. A tightening MUST be recorded "
                f"side by side with its reason, or the next reader cannot tell "
                f"it from a regression."
            )
        return mk(REBASELINE, f"definition tightened: {reason}")

    return mk(REGRESSION,
              "DROP with NO recorded definition change — treat as a real "
              "regression, do not loosen the definition to make it go away")


def compare(baseline: dict, current: dict) -> list[Movement]:
    out = []
    for name, after in current.get("metrics", {}).items():
        out.append(compare_metric(name, baseline.get("metrics", {}).get(name), after))
    return sorted(out, key=lambda m: m.metric)


def render(data: dict) -> str:
    """The one place: each metric, value, denominator, definition version, trend."""
    lines = [
        f"MONOTONIC RATCHET SET — record {data.get('record_version','?')} "
        f"as of {data.get('as_of','?')}",
        "",
        f"{'metric':38} {'value':>7}  {'denominator':34} {'defn':6} trend",
        "-" * 118,
    ]
    for name, m in sorted(data.get("metrics", {}).items()):
        value = "unmeasured" if m["value"] is None else str(m["value"])
        lines.append(
            f"{name:38} {value:>7}  {str(m['denominator'])[:34]:34} "
            f"{str(m['definition_version']):6} {m.get('trend','?')}"
        )
    lines.append("")
    lines.append("A DROP WITHOUT A RECORDED DEFINITION CHANGE IS A REAL REGRESSION.")
    lines.append("A drop WITH one is a re-baseline — record old -> new side by side.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--record", default=str(RECORD))
    ap.add_argument("--against", help="baseline record to compare the current one against")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    current = load(args.record)
    if not args.against:
        print(json.dumps(current, indent=2, sort_keys=True) if args.json else render(current))
        return 0

    moves = compare(load(args.against), current)
    if args.json:
        print(json.dumps([m.__dict__ for m in moves], indent=2, sort_keys=True))
    else:
        for m in moves:
            print(m.line())
    # Non-zero ONLY for a real regression, so this is safe to wire into a gate
    # without a re-baseline tripping it.
    return 1 if any(m.verdict in ACTIONABLE for m in moves) else 0


if __name__ == "__main__":
    raise SystemExit(main())
