"""Shared helpers for replaying captured contract scenarios."""

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from hydroqc import utils
from hydroqc.contract import common as contract_common
from hydroqc.contract import contract_dpc
from hydroqc.peak import basepeakhandler


def freeze_hydroqc_clock(monkeypatch: pytest.MonkeyPatch, reference_date: date) -> None:
    """Freeze clock calls used by contracts and peak handlers to scenario date."""

    class FrozenDate(date):
        """Date subclass frozen at the scenario reference date."""

        @classmethod
        def today(cls) -> "FrozenDate":
            return cls(reference_date.year, reference_date.month, reference_date.day)

    class FrozenDateTime(datetime):
        """Datetime subclass frozen at the scenario reference date."""

        @classmethod
        def now(cls, tz: Any = None) -> "FrozenDateTime":
            value = cls.combine(FrozenDate.today(), time.min)
            if tz is None:
                return value
            if hasattr(tz, "localize"):
                return cast(FrozenDateTime, tz.localize(value))
            return value.replace(tzinfo=tz)

        @classmethod
        def today(cls) -> "FrozenDateTime":
            return cls.now()

    datetime_module = SimpleNamespace(
        date=FrozenDate,
        datetime=FrozenDateTime,
        timedelta=timedelta,
    )
    monkeypatch.setattr(contract_common, "datetime", FrozenDateTime)
    monkeypatch.setattr(contract_dpc, "datetime", FrozenDateTime)
    monkeypatch.setattr(basepeakhandler, "datetime", datetime_module)
    monkeypatch.setattr(utils, "datetime", datetime_module)
