"""Tests for fixture-capture command-line options."""

import sys

from hydroqc.capture import parse_args


def test_winter_period_flag_defaults_to_false(monkeypatch) -> None:
    """Keep existing captures in the non-winter fixture directory by default."""
    monkeypatch.setattr(sys, "argv", ["hydroqc-capture", "--contract", "123"])

    assert parse_args().winter_period is False


def test_winter_period_flag_is_forwarded_by_argument_parser(monkeypatch) -> None:
    """Recognize the explicit winter-period option."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["hydroqc-capture", "--contract", "123", "--winter-period"],
    )

    assert parse_args().winter_period is True
