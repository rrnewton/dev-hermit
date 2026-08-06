#!/usr/bin/env python3
"""Meta-bracket for the mutation suite: prove the SUITE can report a survivor.

A mutation suite that has only ever printed 100% is unproven as a detector --
the same "present but inert" failure it exists to find, one level up. If the
runner silently mis-scored a surviving mutant as killed, every future report
would read as reassurance.

So: inject a mutant that CANNOT be killed and assert the runner reports it as a
survivor, lowers the score, and names it individually. Also assert a failing
population control is not silently swallowed -- a guard that kills everything
must not be able to score 100%.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mutation_suite as MS  # noqa: E402


def test_the_runner_reports_a_survivor_and_lowers_the_score(monkeypatch) -> None:
    """THE META-BRACKET. Without this, a 100% score is an unverified claim."""
    killed = MS.Mutant("fake/killed", "fake-guard", "a defect that IS refused",
                       lambda: True)
    survivor = MS.Mutant("fake/survivor", "fake-guard",
                         "a defect the guard does NOT refuse", lambda: False)
    monkeypatch.setattr(MS, "_catalogue", lambda: ([killed, survivor], []))

    rep = MS.run()
    assert rep["mutation_score"] == {"killed": 1, "total_scored": 2,
                                     "skipped": 0, "percent": 50.0}
    assert [s["id"] for s in rep["survivors"]] == ["fake/survivor"]
    # The survivor must be named INDIVIDUALLY in the rendered output -- the
    # survivors ARE the finding, so a bare percentage would bury them.
    text = MS.render(rep)
    assert "fake/survivor" in text
    assert "SURVIVED" in text
    assert "a defect the guard does NOT refuse" in text


def test_a_surviving_mutant_fails_the_exit_code(monkeypatch, capsys) -> None:
    survivor = MS.Mutant("fake/survivor", "fake-guard", "not refused", lambda: False)
    monkeypatch.setattr(MS, "_catalogue", lambda: ([survivor], []))
    assert MS.main([]) == 1


def test_all_killed_exits_zero(monkeypatch, capsys) -> None:
    """POSITIVE CONTROL for the runner: it can actually succeed."""
    ok = MS.Mutant("fake/killed", "fake-guard", "refused", lambda: True)
    monkeypatch.setattr(MS, "_catalogue", lambda: ([ok], []))
    assert MS.main([]) == 0


def test_a_kill_everything_guard_fails_its_population_control(monkeypatch) -> None:
    """Part 2 of the three-part bracket. A guard that refuses the legitimate
    population passes every mutant perfectly and is still useless."""
    ok = MS.Mutant("fake/killed", "fake-guard", "refused", lambda: True)
    pop = MS.Population("fake-guard", "legitimate items", lambda: (7, 10))
    monkeypatch.setattr(MS, "_catalogue", lambda: ([ok], [pop]))

    rep = MS.run()
    assert rep["mutation_score"]["percent"] == 100.0, "mutants all killed ..."
    assert rep["population_controls"] == {"holding": 0, "total": 1}, "... but control fails"
    assert MS.main([]) == 1, "a 100% mutation score must NOT pass with a broken control"


def test_a_raising_probe_is_skipped_not_counted_as_killed(monkeypatch) -> None:
    """An erroring probe must never be scored as a kill -- that would inflate the
    score with checks that did not run, which is the zero-executed-tests defect
    in a new costume."""
    def boom():
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(MS, "_catalogue", lambda: (
        [MS.Mutant("fake/boom", "fake-guard", "d", boom)], []))
    rep = MS.run()
    assert rep["mutation_score"]["killed"] == 0
    assert rep["mutation_score"]["total_scored"] == 0
    assert rep["mutation_score"]["skipped"] == 1
    assert MS.main([]) == 1, "skipped mutants must block, not pass"


def test_an_authorisation_mutant_is_refused_at_construction() -> None:
    """THE HARD PRECONDITION, executable. Planting a `locally-validated` label on
    a cold PR can satisfy merge-gate and AUTO-MERGE; the suite must not be able
    to construct such a mutant at all."""
    try:
        MS.Mutant("unsafe/label", "merge-gate", "plant a locally-validated label",
                  lambda: True, hazard=MS.AUTHORISATION)
    except MS.UnsafeMutant as err:
        assert "authorisation" in str(err).lower()
    else:                                                     # pragma: no cover
        raise AssertionError("an authorisation-capable mutant was allowed")


def test_inert_mutants_are_permitted() -> None:
    """POSITIVE CONTROL for the precondition: it must not block ordinary mutants."""
    assert MS.Mutant("safe/x", "g", "d", lambda: True).hazard == MS.INERT


# --- the real catalogue ------------------------------------------------------


def test_the_real_catalogue_is_not_empty_and_every_mutant_is_inert() -> None:
    mutants, pops = MS._catalogue()
    assert len(mutants) >= 10, f"catalogue is suspiciously small: {len(mutants)}"
    assert all(m.hazard == MS.INERT for m in mutants)
    assert pops, "a catalogue with no population controls scores only half the bracket"


def test_every_mutant_id_is_unique() -> None:
    """Duplicate ids would silently overwrite each other in any downstream
    tracking of which mutants survived."""
    ids = [m.id for m in MS._catalogue()[0]]
    assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]
