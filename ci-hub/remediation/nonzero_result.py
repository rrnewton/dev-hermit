#!/usr/bin/env python3
"""RESULT-LEVEL detector: a GREEN must carry a NONZERO EXECUTED-TEST COUNT.

A success badge is a PROXY for "the tests passed". The badge and the fact it
claims are two independent things: a target can compile, the harness can run, and
ZERO tests can execute, yet the job still exits 0 and reports success. That is a
no-result wearing a success badge — the classic shape being `--features <x>`
gating that excludes the tests from compilation, so the binary runs 0 tests and
passes (verified by hermit-codex-rev planting a feature-gated fixture: build
succeeds, target runs, zero tests execute, reported SUCCESS). The SAME defect is
a `locally-validated` label with no backing run: a pass asserted with nothing
executed behind it.

The BINDING this detector observes (Proxy Binding review axis): it does not
authenticate WHO emitted the green, nor AND two independent facts. It reads the
executed count the test runner ITSELF printed into the log — evidence that can
exist only when tests actually ran — and refuses a green whose observed executed
count is zero. The count is the first-cause result; the badge is the proxy.

WHY THIS IS NOT THE SOURCE LINT (`lint-rust-error-string-proxies.py`): feature
gating EXCLUDES the tests from compilation, so there is no proxy-matching source
for a source lint to find. It is a BUILD-CONFIGURATION property producing a
RUNTIME outcome, observable only in the run's own output. A source lint straining
after it becomes slow, wrong, and — worst — LOOKS like the class is covered. This
detector is deliberately RESULT-LEVEL: it reads the emitted log, not the source.

FAIL-SAFE DIRECTION. The detector fires ONLY on POSITIVE evidence of zero-ness:
at least one test-runner banner is present AND the total executed across every
banner is 0. Absence of any banner yields ``None`` (unknown, not zero) so a green
whose log we could not parse for banners is NEVER downgraded — the detector can
only refuse a demonstrably inert green, it can never manufacture a no-result from
a green it merely failed to read. In the speculative-land classifier a downgrade
is to ``no_result`` (a hole to RE-DISPATCH), never to ``red`` (a revert): even a
false positive here is recoverable and can never revert a healthy tip.

KNOWN LIMITATION — this detector catches "produced EVIDENCE OF NOTHING", NOT
"produced NO EVIDENCE". An absent or zero-length log is no-evidence just as a
zero-test banner is, but only the latter is caught: an empty log yields ``None``
(no banner) and stays green. That is the SAME defect one layer out, and the
empty-log case is the MORE likely failure in practice — a job that dies before
emitting, a retention expiry, a fetch that 404s. This gap is deliberately left
UNCLOSED here (documented, not silent: a detector with an undocumented hole
converts "we do not check this" into "we checked and it passed").

The predicate that would CLOSE it: ``log absent OR zero bytes after the
validate-start offset => NOT a pass => no_result``. It MUST key on an observable
first-cause — ``stat()`` (file exists AND ``st_size > offset``) — NOT on the
empty string this classifier receives, because ``_read_local_output`` in
``protocol.py`` returns ``""`` on an OSError too: a genuinely empty/absent log
(no evidence) and a READ FAILURE of a log that did contain a Validation summary
are both ``""`` here and must not be conflated. Treating a read failure as
``no_result`` is still SAFE (re-dispatch, never revert) but would spuriously
re-dispatch real greens whenever a log read hiccups, so the closing predicate
belongs at the read site (which can stat the file) rather than in this
string-only function. NOT a legitimate-empty concern at the ``_classify_local``
layer: a genuine exit-0 ``validate.sh`` always emits its ``Validation summary``
line, so empty output there is no-evidence, not a valid banner-less pass. (The
distinct, legitimate no-banner-but-NONEMPTY case — build-only / skipped-gate
summaries — is exactly why ``executed_test_count`` returns ``None`` for it,
UNKNOWN not zero; empty/absent is strictly stronger than no-banner.) At the
future log-FETCH consumer (GitHub job log / ``locally-validated`` label) the same
"absent/zero-length => no_result" predicate applies directly — that is where the
404 / retention-expiry cases live.
"""

from __future__ import annotations

import re

# libtest per-binary banner: `running 12 tests` (and `running 1 test`). This is
# the literal count of tests EXECUTED by that binary — the clean executed signal.
_RUNNING_RE = re.compile(r"\brunning (\d+) tests?\b")
# libtest summary line: `test result: ok. 12 passed; 0 failed; ...`. Only the
# `ok.` form carries a passing verdict; the passed count corroborates execution
# when a `running N` banner was truncated out of the captured window.
_RESULT_OK_RE = re.compile(r"\btest result: ok\. (\d+) passed\b")


def executed_test_count(output: str) -> int | None:
    """Total tests EXECUTED as reported by the run's own test-runner banners.

    Returns the summed executed count when at least one banner is present, or
    ``None`` when the output carries no recognizable test-runner banner at all.
    ``None`` means UNKNOWN (we cannot see how many ran), which is distinct from
    ``0`` (banners present, and every one executed zero tests). A validate run
    that built dozens of crates prints one banner per test binary — crates with
    no unit tests print `running 0 tests` — so the sum is zero only when EVERY
    banner ran zero tests, i.e. the whole run executed nothing.
    """
    if not output:
        return None
    running = [int(m) for m in _RUNNING_RE.findall(output)]
    if running:
        return sum(running)
    passed = [int(m) for m in _RESULT_OK_RE.findall(output)]
    if passed:
        return sum(passed)
    return None


def is_zero_test_green(output: str) -> bool:
    """Whether a passing run executed a DEMONSTRABLY zero test count.

    True only on positive evidence: banners are present and their total executed
    count is 0. Unknown (no banner) and any nonzero count both return False, so
    this only refuses a green that is provably inert — never one it could not
    read. Caller supplies the output of a run that already reported success; this
    answers whether that success is backed by any executed test.
    """
    return executed_test_count(output) == 0
