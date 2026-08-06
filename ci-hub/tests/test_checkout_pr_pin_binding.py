#!/usr/bin/env python3
"""Regression checks for target-repository binding in PR pin preparation."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHECKOUT_HELPER = ROOT / "scripts/checkout-hermit-pr-latest-reverie.sh"
HERMIT = ROOT / "hermit"
CHECKER = HERMIT / "scripts/check-reverie-pin.rs"


def _print_pin_invocations(source: str) -> list[str]:
    return [line.strip() for line in source.splitlines() if "--print-pin" in line]


def _invocation_binds_target_repo(invocation: str) -> bool:
    return '--repo "$repo"' in invocation


def test_checkout_helper_binds_print_pin_to_target_repo() -> None:
    source = CHECKOUT_HELPER.read_text()
    invocations = _print_pin_invocations(source)

    assert len(invocations) == 1
    assert _invocation_binds_target_repo(invocations[0])
    assert not _invocation_binds_target_repo(
        invocations[0].replace('--repo "$repo"', "")
    )


def test_checker_repo_binding_works_from_parent_cwd() -> None:
    # The parent pins an older checker that supports --repo but not the newer
    # --print-pin convenience flag.  Derive the one observable pin from the
    # tracked Cargo.lock, then make the checker classify that exact value as
    # current without a network query.  This exercises repository binding with
    # the checker that actually exists at the proposed parent gitlink.
    lock = (HERMIT / "Cargo.lock").read_text()
    pins = set(
        re.findall(
            r"github\.com/rrnewton/reverie(?:\.git)?\?rev=([0-9a-f]{40})",
            lock,
        )
    )
    assert len(pins) == 1
    pin = pins.pop()
    bound = subprocess.run(
        [str(CHECKER), "--repo", str(HERMIT), "--reverie-main", pin],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    unbound = subprocess.run(
        [str(CHECKER), "--reverie-main", pin],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert bound.returncode == 0, bound.stderr
    assert f"Reverie pin is current: {pin}" in bound.stdout
    assert unbound.returncode != 0
