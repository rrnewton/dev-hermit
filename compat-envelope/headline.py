#!/usr/bin/env python3
"""Every headline figure carries the count of cells actually executed.

The defect, in the auditing agent's words: *"a run that measured nothing is
currently indistinguishable from one that measured everything and passed."*
One required field defeats all three known producers of it -- the empty
selection, the static ratchet, and the unpopulated tier column -- because all
three are the same omission at the summary level rather than the row level.

Measured instance that motivated this, from `compat-envelope/scorecard.csv`
on 2026-08-07 (denominator D = 72 passing ptrace verify cells):

    backend    published   passed   measured   of-measured   unmeasured
    dbi              11%        8          8          100%    64 of 72
    kvm              21%       15         27           56%    45 of 72
    sabre             0%        0          0           n/a    72 of 72
    liteinst         38%       27         36           75%    36 of 72

`dbi` publishes **11%** while passing **8 of 8** cells that actually ran. The
existing renderer marks partial coverage with a `~` suffix but prints no
number, so 8-measured and 72-measured render the same in kind. The percentage
is not wrong -- it is unreadable without its executed count.

The rule this module enforces:

* A headline is ``value (executed=E/D)``. ``E`` is never optional.
* ``E == 0`` renders ``NO-RESULT`` and **cannot** be a pass, whatever the
  underlying ratio says. Zero executed is an absent measurement, not a score.
* ``E < D`` renders the shortfall explicitly, and the of-measured rate is shown
  beside the of-denominator rate, because those two numbers answer different
  questions and only quoting one is how 8/8 became "11%".

Usage::

    h = Headline(label="dbi", passed=8, executed=8, denominator=72)
    h.render()      # 'dbi: 11% of 72 denominator | 100% of 8 executed | 64 unmeasured'
    h.is_pass       # False -- 64 cells unmeasured
    Headline("sabre", 0, 0, 72).render()   # 'sabre: NO-RESULT (executed=0/72)'
"""

from __future__ import annotations

from dataclasses import dataclass

NO_RESULT = "NO-RESULT"


class HeadlineError(ValueError):
    """The headline is not renderable as stated."""


@dataclass(frozen=True)
class Headline:
    """A summary figure that cannot be rendered without its executed count."""

    label: str
    passed: int
    executed: int
    denominator: int

    def __post_init__(self) -> None:
        for name in ("passed", "executed", "denominator"):
            if getattr(self, name) < 0:
                raise HeadlineError(f"{self.label}: negative {name} is not a measurement")
        if self.passed > self.executed:
            raise HeadlineError(
                f"{self.label}: passed {self.passed} exceeds executed {self.executed} -- "
                "a cell cannot pass without running"
            )
        if self.executed > self.denominator:
            raise HeadlineError(
                f"{self.label}: executed {self.executed} exceeds denominator {self.denominator}"
            )

    @property
    def measured_nothing(self) -> bool:
        return self.executed == 0

    @property
    def complete(self) -> bool:
        return self.executed == self.denominator

    @property
    def unmeasured(self) -> int:
        return self.denominator - self.executed

    @property
    def is_pass(self) -> bool:
        """A pass requires full coverage AND zero failures.

        Zero executed is never a pass. Neither is a partial sweep in which
        everything that ran happened to succeed -- that is the 8/8-rendered-as-
        11% case, and calling it a pass would let an empty-ish selection wear a
        green.
        """
        return not self.measured_nothing and self.complete and self.passed == self.executed

    @property
    def rate_of_denominator(self) -> float | None:
        return None if self.denominator == 0 else self.passed / self.denominator

    @property
    def rate_of_executed(self) -> float | None:
        return None if self.executed == 0 else self.passed / self.executed

    def render(self) -> str:
        if self.measured_nothing:
            # The whole point: this can never read as a score.
            return f"{self.label}: {NO_RESULT} (executed=0/{self.denominator})"
        parts = [
            f"{self.label}: {self.rate_of_denominator:.0%} of {self.denominator} denominator",
            f"{self.rate_of_executed:.0%} of {self.executed} executed",
        ]
        if not self.complete:
            parts.append(f"{self.unmeasured} unmeasured")
        return " | ".join(parts)


def render_all(headlines) -> str:
    """Render a set of headlines plus a stated total. Refuses a silent zero."""
    headlines = list(headlines)
    if not headlines:
        raise HeadlineError("no headlines supplied; an empty summary is refused, not printed")
    lines = [h.render() for h in headlines]
    executed = sum(h.executed for h in headlines)
    denominator = sum(h.denominator for h in headlines)
    passed = sum(h.passed for h in headlines)
    lines.append(Headline("TOTAL", passed, executed, denominator).render())
    return "\n".join(lines)
