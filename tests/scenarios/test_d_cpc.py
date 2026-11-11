"""Replay the full D_CPC scenario through the real HydroClient methods."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from hydroqc.capture import run_contract_scenario
from hydroqc.hydro_api.client import HydroClient
from .fixture_replay import FixtureReplayTransport
from .scenario_helpers import freeze_hydroqc_clock

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "common"
FIXTURE_D_CPC_ROOT = Path(__file__).parents[1] / "fixtures" / "D_CPC_SUMMER"


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
    freeze_hydroqc_clock(monkeypatch, reference_date)
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
        client.http_request = transport.request  # type: ignore[assignment]
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
