"""Tests for fixture-capture command-line options."""

import sys

import pytest

from hydroqc.capture import parse_args


def test_winter_period_defaults_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep existing captures in the non-winter fixture directory by default."""
    monkeypatch.setattr(sys, "argv", ["hydroqc-capture", "--contract", "123"])

    assert parse_args().winter_period is False


def test_winter_period_flag_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recognize the explicit winter-period option."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["hydroqc-capture", "--contract", "123", "--winter-period"],
    )

    assert parse_args().winter_period is True
