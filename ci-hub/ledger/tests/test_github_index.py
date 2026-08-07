#!/usr/bin/env python3
"""Tests for the GitHub commit-comment validation index.

EVERY TEST RUNS OFFLINE. The transport is injected, so the suite never touches
github.com, never needs a token, and cannot leave residue on a real commit. That
is not only hygiene: the prototype's three probe STATUSES are still permanently
attached to an inert SHA and cannot be deleted, which is precisely the failure
mode a network-touching test suite reproduces at CI frequency.

The positive controls are load-bearing. The prototype's first mutation run had
all five negatives "refusing" -- including the case that was supposed to pass --
because one wrong schema string made everything die at the same check. Four green
negatives that proved nothing. Wherever a refusal is asserted below, a matching
acceptance is asserted too.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import github_index as gi  # noqa: E402

SHA = "a" * 40
OTHER_SHA = "b" * 40
REPO = "rrnewton/dev-hermit"

RECEIPT_BYTES = b'{"receipt":"canonical","executed":760}'
RECEIPT_SHA = hashlib.sha256(RECEIPT_BYTES).hexdigest()
RECEIPT = {"ref": "receipts/abc.json", "sha256": RECEIPT_SHA}


def opener(_ref: str) -> bytes:
    return RECEIPT_BYTES


def ev(event_id, *, commit=SHA, host="hosta", team="hermit", event_type="run.result",
       emitted_at="2026-08-07T02:00:00Z", run_id=None, **payload) -> dict:
    e = {
        "schema": "validate-ledger/v1",
        "event_id": event_id,
        "event_type": event_type,
        "emitted_at": emitted_at,
        "team": team,
        "host": host,
        "run_id": run_id or event_id,
        "producer": {"source": "observed", "tool": "ci-hub/validate-run", "tool_version": "1"},
        "commit": commit,
    }
    e.update(payload)
    return e


class FakeGitHub:
    """In-memory commit-comment store with scriptable failures.

    Records every call so cost can be COUNTED rather than estimated -- the
    comparison that chose this mechanism was nearly decided by an invocation
    counter that understated true cost 15-vs-26, because --paginate hides N HTTP
    requests inside one invocation.
    """

    def __init__(self, *, fail_first: int = 0, fail_status: int = 403):
        self.comments: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, str]] = []
        self.next_id = 1000
        self.fail_first = fail_first
        self.fail_status = fail_status
        self.posts_attempted = 0

    def __call__(self, method, path, fields=None):
        self.calls.append((method, path))
        if method == "GET":
            sha = path.rsplit("/commits/", 1)[1].split("/")[0]
            return 200, list(self.comments.get(sha, []))
        if method == "POST":
            self.posts_attempted += 1
            if self.posts_attempted <= self.fail_first:
                return self.fail_status, {"message": "rate limited"}
            sha = path.rsplit("/commits/", 1)[1].split("/")[0]
            comment = {"id": self.next_id, "body": fields["body"]}
            self.next_id += 1
            self.comments.setdefault(sha, []).append(comment)
            return 201, comment
        if method == "DELETE":
            cid = int(path.rsplit("/", 1)[1])
            for sha, items in self.comments.items():
                for item in list(items):
                    if item["id"] == cid:
                        items.remove(item)
                        return 204, ""
            return 404, {"message": "not found"}
        raise AssertionError(f"unexpected method {method}")

    def seed(self, sha: str, body: str) -> int:
        cid = self.next_id
        self.next_id += 1
        self.comments.setdefault(sha, []).append({"id": cid, "body": body})
        return cid


def no_sleep(_seconds: float) -> None:
    return None


# ==========================================================================
# POSITIVE CONTROL -- exact-SHA round trip
# ==========================================================================


class ExactShaRoundTrip(unittest.TestCase):
    def test_publish_then_read_back_at_the_exact_sha(self):
        api = FakeGitHub()
        event = ev("e1", outcome="pass", executed_tests=760)
        record = gi.publish([event], commit=SHA, repo=REPO, receipt=RECEIPT,
                            transport=api, enabled=True)
        self.assertEqual(record["counts"], {"offered": 1, "published": 1, "skipped": 0})

        read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
        self.assertEqual(read.counts, {"comments_seen": 1, "accepted": 1, "rejected": 0})
        self.assertEqual(read.accepted[0]["event_id"], "e1")
        self.assertEqual(read.accepted[0]["outcome"], "pass")

    def test_a_different_sha_does_not_see_it(self):
        """Binding is to the exact commit, not the repository."""
        api = FakeGitHub()
        gi.publish([ev("e1", outcome="pass")], commit=SHA, repo=REPO, receipt=RECEIPT,
                   transport=api, enabled=True)
        read = gi.fetch(OTHER_SHA, repo=REPO, transport=api, opener=opener)
        self.assertEqual(read.counts["accepted"], 0)

    def test_fresh_clone_query_needs_nothing_but_the_sha(self):
        """A machine that never ran validate can still answer the question.

        `fetch` is given no ledger, no shard, no local state -- only the SHA and
        the repo. That is the whole point of publishing an index.
        """
        api = FakeGitHub()
        gi.publish([ev("e1", outcome="pass", executed_tests=760)], commit=SHA, repo=REPO,
                   receipt=RECEIPT, transport=api, enabled=True)
        read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
        runs = read.runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(next(iter(runs.values()))["outcome"], "pass")


# ==========================================================================
# NEGATIVES -- each refuses for its OWN reason
# ==========================================================================


class TamperAndMisbinding(unittest.TestCase):
    def test_event_pasted_onto_the_wrong_commit_is_refused(self):
        api = FakeGitHub()
        # A perfectly valid entry for SHA, physically attached to OTHER_SHA.
        api.seed(OTHER_SHA, gi.comment_body(ev("e1", commit=SHA, outcome="pass"),
                                            receipt=RECEIPT))
        read = gi.fetch(OTHER_SHA, repo=REPO, transport=api, opener=opener)
        self.assertEqual(read.counts["accepted"], 0)
        self.assertIn("target-sha-mismatch", read.rejected[0][1])

    def test_tampered_receipt_bytes_are_refused(self):
        api = FakeGitHub()
        gi.publish([ev("e1", outcome="pass")], commit=SHA, repo=REPO, receipt=RECEIPT,
                   transport=api, enabled=True)

        def tampered(_ref: str) -> bytes:
            return RECEIPT_BYTES + b"tamper"

        read = gi.fetch(SHA, repo=REPO, transport=api, opener=tampered)
        self.assertEqual(read.counts["accepted"], 0)
        self.assertIn("receipt-hash-mismatch", read.rejected[0][1])

    def test_comment_without_a_receipt_is_refused(self):
        api = FakeGitHub()
        api.seed(SHA, gi.comment_body(ev("e1", outcome="pass")))  # no receipt
        read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
        self.assertEqual(read.rejected[0][1], "no-receipt-hash")

    def test_unverifiable_receipt_is_a_refusal_not_a_pass(self):
        """No dereference available is NO claim, not a weaker true claim."""
        api = FakeGitHub()
        gi.publish([ev("e1", outcome="pass")], commit=SHA, repo=REPO, receipt=RECEIPT,
                   transport=api, enabled=True)
        read = gi.fetch(SHA, repo=REPO, transport=api, opener=None)
        self.assertEqual(read.counts["accepted"], 0)
        self.assertEqual(read.rejected[0][1], "receipt-not-retrievable")

    def test_forged_body_with_wrong_schema_is_refused(self):
        api = FakeGitHub()
        forged = {"event": dict(ev("e1", outcome="pass"), schema="attacker/v9"),
                  "receipt": RECEIPT}
        api.seed(SHA, f"{gi.MARKER} x\n\n```json\n{json.dumps(forged)}\n```\n")
        read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
        self.assertIn("unknown-envelope-version", read.rejected[0][1])

    def test_a_bare_event_without_the_index_envelope_is_refused(self):
        """The wire format is {index_format, receipt, event}. A body carrying a
        naked ledger event is not one of ours and must not be read as one."""
        api = FakeGitHub()
        api.seed(SHA, f"{gi.MARKER} x\n\n```json\n{json.dumps(ev('e1', outcome='pass'))}\n```\n")
        read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
        self.assertEqual(read.rejected[0][1], "no-event-object")

    def test_ordinary_human_comment_is_ignored_not_an_error(self):
        api = FakeGitHub()
        api.seed(SHA, "looks good to me")
        gi.publish([ev("e1", outcome="pass")], commit=SHA, repo=REPO, receipt=RECEIPT,
                   transport=api, enabled=True)
        read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
        self.assertEqual(read.counts, {"comments_seen": 2, "accepted": 1, "rejected": 1})
        self.assertEqual(read.reasons(), {"not-an-index-comment": 1})

    def test_each_negative_refuses_for_a_DISTINCT_reason(self):
        """The guard against the prototype's own worst bug.

        Four negatives that all die at one shared check are not four tests. This
        asserts the reason codes are pairwise distinct AND that a positive still
        passes through the same code path.
        """
        api = FakeGitHub()
        reasons = set()
        api.seed(SHA, "plain human comment")
        api.seed(SHA, f"{gi.MARKER} x\n\nno fenced block here\n")
        api.seed(SHA, gi.comment_body(ev("w", commit=OTHER_SHA, outcome="pass"),
                                      receipt=RECEIPT))
        api.seed(SHA, gi.comment_body(ev("nr", outcome="pass")))
        read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
        for _, reason in read.rejected:
            reasons.add(reason.split(":")[0])
        self.assertEqual(
            reasons,
            {"not-an-index-comment", "no-json-block", "target-sha-mismatch",
             "no-receipt-hash"},
        )
        # POSITIVE CONTROL through the same path.
        gi.publish([ev("good", outcome="pass")], commit=SHA, repo=REPO, receipt=RECEIPT,
                   transport=api, enabled=True)
        read2 = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
        self.assertEqual(read2.counts["accepted"], 1)


# ==========================================================================
# PRIVACY -- enforced at the writer, fail-closed
# ==========================================================================


class Privacy(unittest.TestCase):
    def test_fqdn_host_is_refused_BEFORE_any_network_call(self):
        api = FakeGitHub()
        with self.assertRaises(gi.PrivacyRefusal) as caught:
            gi.publish([ev("e1", host="box.internal.example", outcome="pass")],
                       commit=SHA, repo=REPO, receipt=RECEIPT, transport=api, enabled=True)
        self.assertIn("fqdn-host", str(caught.exception))
        # The load-bearing assertion: nothing was sent. A read-side check cannot
        # unpublish, so "refused" must mean "never left the machine".
        self.assertEqual(api.calls, [])
        self.assertEqual(api.posts_attempted, 0)

    def test_owner_path_anywhere_in_the_event_is_refused_before_publishing(self):
        api = FakeGitHub()
        leaky = ev("e1", outcome="pass", workspace={"root": "/" + "home" + "/someone/x"})
        with self.assertRaises(gi.PrivacyRefusal) as caught:
            gi.publish([leaky], commit=SHA, repo=REPO, receipt=RECEIPT,
                       transport=api, enabled=True)
        self.assertIn("owner-path", str(caught.exception))
        self.assertEqual(api.calls, [])

    def test_short_host_publishes_fine(self):
        """Positive control: the privacy guard is not simply refusing everything."""
        api = FakeGitHub()
        record = gi.publish([ev("e1", host="hosta", outcome="pass")], commit=SHA,
                            repo=REPO, receipt=RECEIPT, transport=api, enabled=True)
        self.assertEqual(record["counts"]["published"], 1)

    def test_event_targeting_another_commit_is_refused_at_write_time_too(self):
        api = FakeGitHub()
        with self.assertRaises(gi.PrivacyRefusal):
            gi.publish([ev("e1", commit=OTHER_SHA, outcome="pass")], commit=SHA,
                       repo=REPO, receipt=RECEIPT, transport=api, enabled=True)
        self.assertEqual(api.calls, [])


# ==========================================================================
# OPT-IN / SAFETY
# ==========================================================================


class OptIn(unittest.TestCase):
    def test_publishing_is_off_by_default(self):
        api = FakeGitHub()
        with self.assertRaises(gi.PublishDisabled):
            gi.publish([ev("e1", outcome="pass")], commit=SHA, repo=REPO,
                       receipt=RECEIPT, transport=api)
        self.assertEqual(api.calls, [])

    def test_module_declares_it_is_not_landing_authority(self):
        """The index must never become a merge gate. Pinning the contract in a
        test means removing the disclaimer breaks something visible."""
        self.assertIn("not authority", gi.comment_body(ev("e1", outcome="pass")))
        source = Path(gi.__file__).read_text()
        self.assertIn("NOT A LANDING GATE", source)

    def test_no_branch_protection_surface_is_touched(self):
        """Nothing in this module may call a protection or required-checks
        endpoint. Asserted against the source so a future edit trips it."""
        source = Path(gi.__file__).read_text()
        for forbidden in ("/protection", "required_status_checks", "/branches/"):
            self.assertNotIn(forbidden, source)


# ==========================================================================
# MULTI-MACHINE / MULTI-RUN HISTORY -- the axis statuses lost on
# ==========================================================================


class History(unittest.TestCase):
    def test_two_machines_on_one_commit_BOTH_survive(self):
        """combined-status reported 1 row where 3 runs happened. The whole
        reason comments were chosen is that this returns both."""
        api = FakeGitHub()
        gi.publish(
            [ev("e1", host="hosta", run_id="r1", outcome="pass"),
             ev("e2", host="hostb", run_id="r2", outcome="fail")],
            commit=SHA, repo=REPO, receipt=RECEIPT, transport=api, enabled=True,
        )
        read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
        self.assertEqual(read.counts["accepted"], 2)
        self.assertEqual({e["host"] for e in read.accepted}, {"hosta", "hostb"})
        self.assertEqual(len(read.runs()), 2)

    def test_three_runs_on_one_host_all_visible(self):
        api = FakeGitHub()
        gi.publish(
            [ev(f"e{i}", run_id=f"r{i}", outcome="pass",
                emitted_at=f"2026-08-07T0{i}:00:00Z") for i in (1, 2, 3)],
            commit=SHA, repo=REPO, receipt=RECEIPT, transport=api, enabled=True,
        )
        read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
        self.assertEqual(read.counts["accepted"], 3)
        self.assertEqual(len(read.runs()), 3)

    def test_read_is_order_independent(self):
        api = FakeGitHub()
        gi.publish([ev("e2", run_id="r2", outcome="fail",
                       emitted_at="2026-08-07T03:00:00Z"),
                    ev("e1", run_id="r1", outcome="pass",
                       emitted_at="2026-08-07T01:00:00Z")],
                   commit=SHA, repo=REPO, receipt=RECEIPT, transport=api, enabled=True)
        forward = gi.fetch(SHA, repo=REPO, transport=api, opener=opener).runs()
        api.comments[SHA].reverse()
        backward = gi.fetch(SHA, repo=REPO, transport=api, opener=opener).runs()
        self.assertEqual(forward, backward)


# ==========================================================================
# APPEND-ONLY ENRICHMENT
# ==========================================================================


class Enrichment(unittest.TestCase):
    def test_later_enrichment_lands_on_the_run_without_editing_anything(self):
        api = FakeGitHub()
        base = ev("e1", run_id="r1", outcome="pass")
        gi.publish([base], commit=SHA, repo=REPO, receipt=RECEIPT,
                   transport=api, enabled=True)
        enrich = ev("e2", run_id="r1", event_type="run.enrich",
                    emitted_at="2026-08-07T03:00:00Z", enriches="e1",
                    executed_tests=760)
        gi.publish([enrich], commit=SHA, repo=REPO, receipt=RECEIPT,
                   transport=api, enabled=True)

        read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
        # Two comments exist: nothing was edited in place, so the stream is an
        # audit trail. The FOLDED view carries the enrichment.
        self.assertEqual(read.counts["comments_seen"], 2)
        run = read.runs()["r1"]
        self.assertEqual(run["outcome"], "pass")
        self.assertEqual(run["executed_tests"], 760)

    def test_duplicate_event_id_never_overwrites(self):
        api = FakeGitHub()
        good = ev("e1", outcome="pass")
        gi.publish([good], commit=SHA, repo=REPO, receipt=RECEIPT,
                   transport=api, enabled=True)
        # A forged second copy claiming a different outcome under the SAME id.
        api.seed(SHA, gi.comment_body(dict(good, outcome="fail"), receipt=RECEIPT))
        read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
        self.assertEqual(read.counts["accepted"], 1)
        self.assertEqual(read.accepted[0]["outcome"], "pass")
        self.assertEqual(read.reasons(), {"duplicate-event-id": 1})


# ==========================================================================
# IDEMPOTENCY / RETRY / RATE
# ==========================================================================


class PublishMechanics(unittest.TestCase):
    def test_republishing_the_same_event_is_a_no_op(self):
        api = FakeGitHub()
        event = ev("e1", outcome="pass")
        first = gi.publish([event], commit=SHA, repo=REPO, receipt=RECEIPT,
                           transport=api, enabled=True)
        second = gi.publish([event], commit=SHA, repo=REPO, receipt=RECEIPT,
                            transport=api, enabled=True)
        self.assertEqual(first["counts"]["published"], 1)
        self.assertEqual(second["counts"], {"offered": 1, "published": 0, "skipped": 1})
        self.assertEqual(len(api.comments[SHA]), 1)

    def test_a_rate_limited_post_is_retried_and_lands_exactly_once(self):
        api = FakeGitHub(fail_first=2, fail_status=403)
        record = gi.publish([ev("e1", outcome="pass")], commit=SHA, repo=REPO,
                            receipt=RECEIPT, transport=api, enabled=True,
                            sleep=no_sleep)
        self.assertEqual(api.posts_attempted, 3)          # the hook really fired
        self.assertEqual(record["counts"]["published"], 1)
        self.assertEqual(len(api.comments[SHA]), 1)        # exactly one copy

    def test_a_permanent_error_terminates_instead_of_retrying_forever(self):
        api = FakeGitHub(fail_first=99, fail_status=404)
        with self.assertRaises(gi.GitHubIndexError):
            gi.publish([ev("e1", outcome="pass")], commit=SHA, repo=REPO,
                       receipt=RECEIPT, transport=api, enabled=True, sleep=no_sleep)
        self.assertEqual(api.posts_attempted, 1)           # 404 is not retryable

    def test_retries_are_bounded_even_on_a_retryable_status(self):
        api = FakeGitHub(fail_first=99, fail_status=403)
        with self.assertRaises(gi.GitHubIndexError):
            gi.publish([ev("e1", outcome="pass")], commit=SHA, repo=REPO,
                       receipt=RECEIPT, transport=api, enabled=True,
                       max_attempts=3, sleep=no_sleep)
        self.assertEqual(api.posts_attempted, 3)

    def test_backoff_is_exponential_and_actually_waited(self):
        api = FakeGitHub(fail_first=2, fail_status=429)
        waited: list[float] = []
        gi.publish([ev("e1", outcome="pass")], commit=SHA, repo=REPO, receipt=RECEIPT,
                   transport=api, enabled=True, sleep=waited.append)
        self.assertEqual(waited, [1.0, 2.0])

    def test_a_partial_batch_failure_does_not_lose_the_events_already_published(self):
        """Publishing e1 then failing on e2 must leave e1 durable, and a rerun
        must publish only e2."""
        api = FakeGitHub(fail_first=0)
        gi.publish([ev("e1", outcome="pass")], commit=SHA, repo=REPO, receipt=RECEIPT,
                   transport=api, enabled=True)
        api.fail_first = api.posts_attempted + 99
        api.fail_status = 500
        with self.assertRaises(gi.GitHubIndexError):
            gi.publish([ev("e1", outcome="pass"), ev("e2", run_id="r2", outcome="fail")],
                       commit=SHA, repo=REPO, receipt=RECEIPT, transport=api,
                       enabled=True, max_attempts=2, sleep=no_sleep)
        read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
        self.assertEqual([e["event_id"] for e in read.accepted], ["e1"])

    def test_cost_is_counted_in_http_calls_not_invocations(self):
        """The comparison was nearly decided by a counter that understated cost
        15-vs-26 because --paginate hides requests inside one invocation. The
        happy path is 1 GET (idempotency probe) + 1 POST per new event."""
        api = FakeGitHub()
        gi.publish([ev("e1", outcome="pass"), ev("e2", run_id="r2", outcome="pass")],
                   commit=SHA, repo=REPO, receipt=RECEIPT, transport=api, enabled=True)
        self.assertEqual([m for m, _ in api.calls], ["GET", "POST", "POST"])

    def test_a_supplied_existing_read_avoids_the_idempotency_probe(self):
        """A caller publishing to many SHAs can amortise the probe."""
        api = FakeGitHub()
        empty = gi.IndexRead(SHA, [], [], comments_seen=0)
        gi.publish([ev("e1", outcome="pass")], commit=SHA, repo=REPO, receipt=RECEIPT,
                   transport=api, enabled=True, existing=empty)
        self.assertEqual([m for m, _ in api.calls], ["POST"])


# ==========================================================================
# ROLLBACK -- the axis statuses cannot do at all
# ==========================================================================


class Rollback(unittest.TestCase):
    def test_published_entries_can_be_withdrawn(self):
        api = FakeGitHub()
        gi.publish([ev("e1", outcome="pass")], commit=SHA, repo=REPO, receipt=RECEIPT,
                   transport=api, enabled=True)
        ids = [c["id"] for c in api.comments[SHA]]
        result = gi.unpublish(ids, repo=REPO, transport=api)
        self.assertEqual(result["deleted"], ids)
        self.assertEqual(result["failed"], [])
        self.assertEqual(gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
                         .counts["accepted"], 0)

    def test_deleting_a_missing_comment_is_reported_not_silently_ok(self):
        api = FakeGitHub()
        result = gi.unpublish([424242], repo=REPO, transport=api)
        self.assertEqual(result["deleted"], [])
        self.assertEqual(result["failed"], [(424242, 404)])


# ==========================================================================
# DENOMINATORS + INPUT VALIDATION
# ==========================================================================


class ShardAndUnionIntegration(unittest.TestCase):
    """The index must compose with the team/machine shards, not sit beside them."""

    def _shard(self, root: Path, host: str, events: list[dict]) -> Path:
        path = root / "ledger" / "hermit" / host / "2026-08.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in events))
        return path

    def test_events_for_commit_selects_from_real_shards(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._shard(root, "hosta", [
                ev("e1", commit=SHA, outcome="pass"),
                ev("e2", commit=OTHER_SHA, outcome="fail", run_id="r2"),
            ])
            picked = gi.events_for_commit([path], SHA)
            self.assertEqual([e["event_id"] for e in picked], ["e1"])

    def test_github_supplies_the_runs_a_local_shard_cannot_see(self):
        """hosta holds only its own shard; hostb's run reaches it via GitHub."""
        import tempfile
        api = FakeGitHub()
        gi.publish([ev("remote1", host="hostb", run_id="rb", outcome="fail")],
                   commit=SHA, repo=REPO, receipt=RECEIPT, transport=api, enabled=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = self._shard(Path(tmp), "hosta",
                               [ev("local1", host="hosta", run_id="ra", outcome="pass")])
            local = gi.events_for_commit([path], SHA)
            self.assertEqual(len(local), 1)          # local sees ONE run
            read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
            merged = gi.merge_with_local(read, local)
            self.assertEqual(len(merged), 2)         # merged sees BOTH
            self.assertEqual({r["host"] for r in merged.values()}, {"hosta", "hostb"})

    def test_an_event_present_on_both_sides_collapses_to_one(self):
        import tempfile
        api = FakeGitHub()
        shared = ev("e1", outcome="pass")
        gi.publish([shared], commit=SHA, repo=REPO, receipt=RECEIPT,
                   transport=api, enabled=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = self._shard(Path(tmp), "hosta", [shared])
            read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
            merged = gi.merge_with_local(read, gi.events_for_commit([path], SHA))
            self.assertEqual(len(merged), 1)

    def test_merge_is_order_independent(self):
        api = FakeGitHub()
        gi.publish([ev("r1", host="hostb", run_id="rb", outcome="fail")], commit=SHA,
                   repo=REPO, receipt=RECEIPT, transport=api, enabled=True)
        read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
        local = [ev("l1", host="hosta", run_id="ra", outcome="pass")]
        self.assertEqual(gi.merge_with_local(read, local),
                         gi.merge_with_local(gi.IndexRead(SHA, local, [], comments_seen=0),
                                             read.accepted))


class Denominators(unittest.TestCase):
    def test_counts_travel_with_the_result(self):
        """An accepted list alone cannot distinguish 'one validation' from 'one
        validation and nine entries I discarded'."""
        api = FakeGitHub()
        api.seed(SHA, "human note")
        api.seed(SHA, gi.comment_body(ev("bad", commit=OTHER_SHA, outcome="pass"),
                                      receipt=RECEIPT))
        gi.publish([ev("e1", outcome="pass")], commit=SHA, repo=REPO, receipt=RECEIPT,
                   transport=api, enabled=True)
        read = gi.fetch(SHA, repo=REPO, transport=api, opener=opener)
        self.assertEqual(read.counts,
                         {"comments_seen": 3, "accepted": 1, "rejected": 2})
        self.assertEqual(sum(read.reasons().values()), 2)

    def test_a_non_sha_is_refused_by_both_read_and_write(self):
        api = FakeGitHub()
        with self.assertRaises(gi.GitHubIndexError):
            gi.fetch("main", repo=REPO, transport=api)
        with self.assertRaises(gi.GitHubIndexError):
            gi.publish([ev("e1", outcome="pass")], commit="HEAD", repo=REPO,
                       receipt=RECEIPT, transport=api, enabled=True)
        self.assertEqual(api.calls, [])

    def test_an_oversized_body_fails_locally_not_as_an_opaque_422(self):
        big = ev("e1", outcome="pass", note="x" * (gi.MAX_BODY + 10))
        with self.assertRaises(gi.GitHubIndexError):
            gi.comment_body(big)


if __name__ == "__main__":
    unittest.main(verbosity=2)
