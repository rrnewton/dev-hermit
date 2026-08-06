#!/usr/bin/env python3
"""Brackets for the standing cross-backend parity gate.

The gate's whole value is that it cannot silently pass. These tests pin that
property against the gate's own logic, so a refactor cannot quietly turn it into
a no-op — the same discipline the gate applies to the parity comparison itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import parity_gate as pg


def _msgs(*bodies: str) -> list[str]:
    return [f"INFO detcore: DETLOG {b}" for b in bodies]


MEM = "[memory][dtid 3] 0x1000-0x2000 MMPermissions(READ) 0 0:0 0 [heap]->abcdef0123456789"
MEM2 = "[memory][dtid 3] 0x3000-0x4000 MMPermissions(READ) 0 0:0 0 [stack]->0123456789abcdef"
SYS = "[syscall][detcore, dtid 3] finish syscall #1: brk(NULL) = Ok(4096)"


def test_identical_streams_match() -> None:
    a = _msgs(MEM, SYS)
    assert pg.compare(a, list(a))["verdict"] == "MATCH"


def test_empty_side_is_no_result_never_a_match() -> None:
    """A pass over an empty comparison is the failure mode this lane exists to stop."""
    assert pg.compare([], _msgs(MEM))["verdict"] == "NO-RESULT"
    assert pg.compare(_msgs(MEM), [])["verdict"] == "NO-RESULT"
    assert pg.compare([], [])["verdict"] == "NO-RESULT"


def test_content_hash_flip_is_caught() -> None:
    a = _msgs(MEM, SYS)
    b = _msgs(MEM.replace("abcdef0123456789", "bbcdef0123456789"), SYS)
    r = pg.compare(a, b)
    assert r["verdict"] == "DIVERGE"
    assert r["content_equal"] is False
    # The range did NOT change, and the gate must say so separately.
    assert r["ranges_equal"] is True


def test_range_and_content_are_reported_separately() -> None:
    """The reason this gate does NOT address-normalize.

    A page-granular relocation (DynamoRIO moves guest static/heap by +0x1000) must
    stay distinguishable from a content difference. Normalizing addresses would
    collapse both into one boolean and hide the relocation entirely.
    """
    a = _msgs(MEM)
    relocated = _msgs(MEM.replace("0x1000-0x2000", "0x2000-0x3000"))
    r = pg.compare(a, relocated)
    assert r["verdict"] == "DIVERGE"
    assert r["ranges_equal"] is False      # the relocation is visible...
    assert r["content_equal"] is True      # ...and NOT confused with a content change


def test_self_check_catches_a_planted_mutation() -> None:
    caught, how = pg.self_check(_msgs(MEM, MEM2, SYS))
    assert caught, how


def test_self_check_refuses_when_it_cannot_mutate() -> None:
    """No hash-bearing record => the gate must NOT claim a successful self-check."""
    caught, how = pg.self_check(_msgs(SYS, SYS))
    assert not caught
    assert "no hash-bearing record" in how
    caught, how = pg.self_check([])
    assert not caught


def test_known_red_set_does_not_grow_silently() -> None:
    """KNOWN_RED may SHRINK as backends are fixed; growing it needs a deliberate edit."""
    assert set(pg.KNOWN_RED) <= {
        ("ptrace", "dbi"), ("ptrace", "kvm"),
        ("ptrace", "sabre"), ("ptrace", "liteinst"),
    }
    for pair, reason in pg.KNOWN_RED.items():
        assert len(reason) > 10, f"{pair}: a known-red entry needs an actionable reason"


def test_enforced_pairs_are_disjoint_from_known_red() -> None:
    """A pair cannot be both enforced and excused."""
    assert not set(pg.ENFORCED_PAIRS) & set(pg.KNOWN_RED)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
