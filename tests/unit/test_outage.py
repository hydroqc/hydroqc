"""Tests for interpreting outage API payloads."""

import datetime
import logging
from typing import Any, cast

import pytest

from hydroqc.contract.outage import Outage
from hydroqc.types import OutageCause, OutageCode, OutageStatus


def make_outage(**updates: Any) -> Outage:
    """Build an outage with a representative API payload."""
    payload: dict[str, Any] = {
        "idInterruption": {
            "site": "XXX",
            "typeObjet": "I",
            "noInterruption": 12345,
            "noSection": 1,
        },
        "dateDebut": "2026-11-13T10:00:00+00:00",
        "dateFin": "2026-11-13T11:00:00+00:00",
        "etat": "C",
        "dureePrevu": 60,
        "interruptionPlanifiee": False,
    }
    payload.update(updates)
    return Outage(payload, logging.getLogger("test.outage"))  # type: ignore[arg-type]


def test_outage_exposes_simple_properties() -> None:
    """Return identifiers, duration, status and optional outage metadata."""
    outage = make_outage(
        codeCause="51",
        codeIntervention="L",
        niveauUrgence="P",
        interruptionPlanifiee=True,
    )

    assert outage.id_ == 12345
    assert outage.planned_duration == datetime.timedelta(minutes=60)
    assert outage.cause == OutageCause.vegetation
    assert outage.code == OutageCode.travaux_en_cours
    assert outage.status == OutageStatus.courante_confirme
    assert outage.emergency_level == "P"
    assert outage.is_planned
    assert repr(outage).startswith("<Outage - 12345 - 2026-11-13T10:00:00")


def test_start_uses_reported_for_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the reported start date when the original outage has ended."""
    monkeypatch.setattr(
        "hydroqc.contract.outage.utils.now",
        lambda: datetime.datetime.fromisoformat("2026-11-13T12:00:00+00:00"),
    )
    outage = make_outage(dateDebutReport="2026-11-14T10:00:00+00:00")

    assert outage.start_date == datetime.datetime.fromisoformat(
        "2026-11-14T10:00:00+00:00"
    )


def test_start_date_falls_back_to_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the original start if it has not passed or has no reported date."""
    monkeypatch.setattr(
        "hydroqc.contract.outage.utils.now",
        lambda: datetime.datetime.fromisoformat("2026-11-13T10:30:00+00:00"),
    )
    assert make_outage().start_date == datetime.datetime.fromisoformat(
        "2026-11-13T10:00:00+00:00"
    )

    monkeypatch.setattr(
        "hydroqc.contract.outage.utils.now",
        lambda: datetime.datetime.fromisoformat("2026-11-13T12:00:00+00:00"),
    )
    assert make_outage().start_date == datetime.datetime.fromisoformat(
        "2026-11-13T10:00:00+00:00"
    )


def test_end_date_uses_estimated_maximum() -> None:
    """Prefer the maximum estimated end for an ongoing outage."""
    outage = make_outage(dateFinEstimeeMax="2026-11-13T13:00:00+00:00")

    assert outage.end_date == datetime.datetime.fromisoformat(
        "2026-11-13T13:00:00+00:00"
    )


def test_end_date_uses_reported_for_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the reported end date after the original end has passed."""
    monkeypatch.setattr(
        "hydroqc.contract.outage.utils.now",
        lambda: datetime.datetime.fromisoformat("2026-11-13T12:00:00+00:00"),
    )
    outage = make_outage(dateFinReport="2026-11-14T11:00:00+00:00")

    assert outage.end_date == datetime.datetime.fromisoformat(
        "2026-11-14T11:00:00+00:00"
    )


def test_end_date_uses_original_end_or_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fall back to the original end date or preserve a missing end date."""
    monkeypatch.setattr(
        "hydroqc.contract.outage.utils.now",
        lambda: datetime.datetime.fromisoformat("2026-11-13T10:30:00+00:00"),
    )
    assert make_outage().end_date == datetime.datetime.fromisoformat(
        "2026-11-13T11:00:00+00:00"
    )

    outage = make_outage(dateFin=None)
    raw_data = cast(dict[str, Any], outage._raw_data)
    del raw_data["dateFin"]
    assert outage.end_date is None


def test_cause_falls_back_for_bad_codes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Return the unknown cause and log when cause data is unusable."""
    with caplog.at_level(logging.WARNING, logger="test.outage"):
        assert make_outage().cause == OutageCause.inconnu
        assert make_outage(codeCause="not-a-code").cause == OutageCause.inconnu

    assert "not provided" in caplog.text
    assert "supported cause code" in caplog.text


def test_default_code_and_emergency_level() -> None:
    """Supply enum defaults and return None for absent emergency level."""
    outage = make_outage()

    assert outage.code == OutageCode.inconnu
    assert outage.emergency_level is None
