"""Replay the full D_CPC scenario through the real HydroClient methods."""

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hydroqc.capture import run_contract_scenario
from hydroqc.contract import common as contract_common
from hydroqc.contract import contract_dpc
from hydroqc.hydro_api.client import HydroClient
from hydroqc.peak import basepeakhandler
from hydroqc import utils
from .fixture_replay import FixtureReplayTransport

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "common"
FIXTURE_D_CPC_ROOT = Path(__file__).parents[1] / "fixtures" / "D_CPC_SUMMER"


def _freeze_hydroqc_clock(
    monkeypatch: pytest.MonkeyPatch, reference_date: date
) -> None:
    """Freeze clock calls used by contracts and peak handlers to scenario date."""

    class FrozenDate(date):
        @classmethod
        def today(cls) -> "FrozenDate":
            return cls(reference_date.year, reference_date.month, reference_date.day)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "FrozenDateTime":
            value = cls.combine(FrozenDate.today(), time.min)
            if tz is None:
                return value
            if hasattr(tz, "localize"):
                return tz.localize(value)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["web", "mobile"])
async def test_full_d_cpc_scenario_replays(
    profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay contract selection and all captured D_CPC operations."""
    common_scenario: dict[str, Any] = json.loads(
        (FIXTURE_ROOT / "scenario.json").read_text(encoding="utf-8")
    )
    rate_scenario: dict[str, Any] = json.loads(
        (FIXTURE_D_CPC_ROOT / "scenario.json").read_text(encoding="utf-8")
    )
    reference_date = date.fromisoformat(rate_scenario["reference_date"])
    _freeze_hydroqc_clock(monkeypatch, reference_date)
    transport = FixtureReplayTransport(
        [FIXTURE_ROOT / profile, FIXTURE_D_CPC_ROOT / profile]
    )
    client = HydroClient(
        "fixture@example.invalid",
        "",
        auth_client=profile,
        enable_cache=False,
    )

    async def fixture_access_token(force_refresh: bool = True) -> str:
        del force_refresh
        client.access_token_expiry = datetime.now() + timedelta(days=1)
        return "fixture-access-token"

    client._get_access_token = fixture_access_token  # type: ignore[method-assign]
    try:
        client.http_request = transport.request  # type: ignore[method-assign]
        scenario = await run_contract_scenario(
            client, common_scenario["contract_id"], reference_date
        )
        transport.assert_complete()

        assert scenario.context == tuple(common_scenario["context"])
        assert scenario.contract.rate == rate_scenario["rate"]
        assert scenario.contract.rate_option == rate_scenario["option"]
        assert scenario.surface["methods"]
        assert scenario.surface["properties"]
        assert scenario.surface["subobjects"]
    finally:
        await client.close_session()
