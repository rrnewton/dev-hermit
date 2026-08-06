#!/usr/bin/env python3
"""Coordinator-pane discovery tests for ``scripts/orc-hermit-msg.py``.

The bug these cover: a relay aimed at an *agent* pane (``--window
hermit-perf``) failed with "expected exactly one live Orc coordinator for
database 'hermit'; found 0 (coordinators: none)". That message reports a count
without the condition that produced it, so it reads as "the coordinator has
vanished" when it means "your window hint excluded the coordinator" — and it
was read that way, producing a bogus "pane-detect finds 0 coordinators"
outage report.

Every discovery outcome is bracketed on both sides: the qualifying case must
select the coordinator, and the disqualifying case must be refused with a
message that names *which* condition refused it.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "orc-hermit-msg.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("orc_hermit_msg", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves the defining module out of sys.modules, so the entry
    # must exist before exec_module runs.
    sys.modules["orc_hermit_msg"] = module
    spec.loader.exec_module(module)
    return module


ohm = _load_module()

ORC_START = (
    '"if [ -f \'/home/newton/.orc/tmux.conf\' ]; then tmux source-file '
    "'/home/newton/.orc/tmux.conf'; fi; exec '/home/newton/orc-bin/orc' "
    "'--db' 'hermit' '--resume' '--tui'\""
)
AGENT_START = (
    '"cd /home/newton/work/dev-hermit && exec env AGENT=orc claude '
    '--permission-mode acceptEdits"'
)


def pane_line(session, window, pane_id, command, start, dead="0"):
    return "\t".join([session, window, pane_id, dead, command, start])


# The live socket shape this fleet actually runs: one `orc` coordinator pane
# plus one window per agent, all inside a single session.
LIVE_SOCKET = "\n".join(
    [
        pane_line("orc-hermit", "orc", "%0", "orc", ORC_START),
        pane_line("orc-hermit", "hermit-coord", "%1", "codex", AGENT_START),
        pane_line("orc-hermit", "hermit-perf", "%2", "claude", AGENT_START),
    ]
)


class FakeTmux:
    """Stands in for run_tmux so discovery can be tested without a server."""

    def __init__(self, panes: str):
        self.panes = panes

    def __call__(self, socket, *args, input_text=None):
        if args and args[0] == "list-panes":
            return self.panes
        raise AssertionError(f"unexpected tmux call: {args}")


class DiscoveryTestCase(unittest.TestCase):
    def setUp(self):
        self._real_run_tmux = ohm.run_tmux
        self.addCleanup(setattr, ohm, "run_tmux", self._real_run_tmux)

    def find(self, panes=LIVE_SOCKET, sessions=("orc-hermit",), db="hermit", **hints):
        ohm.run_tmux = FakeTmux(panes)
        return ohm.find_coordinator_pane(
            Path("/nonexistent.sock"), list(sessions), db, **hints
        )

    def find_error(self, **kwargs):
        with self.assertRaises(ohm.OrcMessageError) as caught:
            self.find(**kwargs)
        return str(caught.exception)


class TestCoordinatorSelection(DiscoveryTestCase):
    """Positive bracket: the coordinator is found in the normal shapes."""

    def test_finds_the_single_coordinator_among_agent_panes(self):
        pane = self.find()
        self.assertEqual(
            (pane.session, pane.window, pane.pane_id), ("orc-hermit", "orc", "%0")
        )

    def test_coordinator_window_hint_still_selects_it(self):
        pane = self.find(window_hint="orc")
        self.assertEqual(pane.pane_id, "%0")

    def test_session_hint_matching_the_coordinator_selects_it(self):
        pane = self.find(session_hint="orc-hermit")
        self.assertEqual(pane.pane_id, "%0")

    def test_coordinator_without_a_db_flag_falls_back_to_session_name(self):
        panes = pane_line(
            "orc-hermit", "orc", "%0", "orc", '"exec /home/newton/orc-bin/orc --tui"'
        )
        self.assertEqual(self.find(panes=panes).pane_id, "%0")


class TestAgentWindowHintIsDiagnosed(DiscoveryTestCase):
    """The regression this file exists for.

    Aiming the coordinator relay at an agent window must fail, and must say so
    in terms of the hint — never as a bare "found 0 (coordinators: none)".
    """

    def test_agent_window_hint_names_the_window_and_its_command(self):
        message = self.find_error(window_hint="hermit-perf")
        self.assertIn("hermit-perf", message)
        self.assertIn("claude", message)
        self.assertIn("agent", message)

    def test_agent_window_hint_still_reports_the_reachable_coordinator(self):
        # The operator's next action is "drop the hint", so the message has to
        # show that a coordinator *was* available.
        message = self.find_error(window_hint="hermit-perf")
        self.assertIn("orc-hermit:orc", message)

    def test_agent_window_hint_does_not_claim_there_are_no_coordinators(self):
        message = self.find_error(window_hint="hermit-perf")
        self.assertNotIn("coordinators: none", message)
        self.assertNotIn("no live Orc coordinator pane", message)

    def test_unknown_window_hint_says_no_pane_has_that_window(self):
        message = self.find_error(window_hint="nonesuch")
        self.assertIn("no live pane has window 'nonesuch'", message)


class TestGenuinelyAbsentCoordinator(DiscoveryTestCase):
    """Negative bracket: a real zero must stay distinguishable from a hint miss."""

    def test_no_orc_pane_reports_the_socket_and_suggests_a_retry(self):
        panes = pane_line("orc-hermit", "hermit-perf", "%2", "claude", AGENT_START)
        message = self.find_error(panes=panes)
        self.assertIn("no live Orc coordinator pane", message)
        self.assertIn("retry", message)
        # It must still show what *was* live, so the reader can tell a
        # restarting coordinator from an empty socket.
        self.assertIn("hermit-perf", message)

    def test_a_dead_orc_pane_does_not_count_as_a_coordinator(self):
        panes = pane_line("orc-hermit", "orc", "%0", "orc", ORC_START, dead="1")
        self.assertIn("no live Orc coordinator pane", self.find_error(panes=panes))

    def test_a_pane_outside_the_active_sessions_is_ignored(self):
        message = self.find_error(sessions=("orc-other",))
        self.assertIn("no live Orc coordinator pane", message)


class TestDatabaseAndAmbiguity(DiscoveryTestCase):
    def test_wrong_database_is_reported_as_a_database_mismatch(self):
        message = self.find_error(db="other")
        self.assertIn("running database 'other'", message)
        self.assertIn("db=hermit", message)

    def test_two_coordinators_for_one_database_ask_for_disambiguation(self):
        panes = "\n".join(
            [
                pane_line("orc-hermit", "orc", "%0", "orc", ORC_START),
                pane_line("orc-hermit", "orc-two", "%9", "orc", ORC_START),
            ]
        )
        message = self.find_error(panes=panes)
        self.assertIn("found 2", message)
        self.assertIn("--session/--window", message)

    def test_session_hint_outside_active_sessions_is_refused_early(self):
        message = self.find_error(session_hint="orc-nope")
        self.assertIn("is not present in orc tmux ls", message)


class TestStartCommandDbParsing(unittest.TestCase):
    def test_parses_the_live_quoted_orc_start_command(self):
        self.assertEqual(ohm.orc_db_from_start_command(ORC_START), "hermit")

    def test_parses_the_equals_form(self):
        self.assertEqual(
            ohm.orc_db_from_start_command('"exec orc --db=hermit --tui"'), "hermit"
        )

    def test_agent_start_command_has_no_db(self):
        self.assertIsNone(ohm.orc_db_from_start_command(AGENT_START))

    def test_unparseable_start_command_is_not_an_exception(self):
        self.assertIsNone(ohm.orc_db_from_start_command('"exec orc --db \'unclosed'))


class TestLivePaneParsing(unittest.TestCase):
    def test_short_lines_are_skipped_rather_than_crashing(self):
        panes = "not\tenough\tfields\n" + LIVE_SOCKET
        parsed = ohm.parse_live_panes(panes, ["orc-hermit"])
        self.assertEqual([pane.pane_id for pane in parsed], ["%0", "%1", "%2"])

    def test_command_is_reduced_to_its_basename(self):
        panes = pane_line("orc-hermit", "orc", "%0", "/home/newton/orc-bin/orc", ORC_START)
        self.assertTrue(ohm.parse_live_panes(panes, ["orc-hermit"])[0].is_orc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
