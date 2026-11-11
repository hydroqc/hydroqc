"""Replay the full DPC scenario through the real HydroClient methods."""

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from hydroqc.capture import run_contract_scenario
from hydroqc.contract.contract_dpc import ContractDPC, WinterState
from hydroqc.hydro_api.client import HydroClient
from .fixture_replay import FixtureReplayTransport
from .scenario_helpers import freeze_hydroqc_clock

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "common"
FIXTURE_DPC_ROOT = Path(__file__).parents[1] / "fixtures" / "DPC_SUMMER"
DPC_PROPERTIES = {
    "amount_saved_vs_base_rate",
    "critical_called_hours",
    "last_update_date",
    "max_critical_called_hours",
    "winter_state",
    "winter_total_days",
    "winter_total_days_last_update",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["web", "mobile"])
async def test_full_dpc_scenario_replays(
    profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay contract selection and all captured DPC operations."""
    common_scenario: dict[str, Any] = json.loads(
        (FIXTURE_ROOT / "scenario.json").read_text(encoding="utf-8")
    )
    rate_scenario: dict[str, Any] = json.loads(
        (FIXTURE_DPC_ROOT / "scenario.json").read_text(encoding="utf-8")
    )
    dpc_data_path = next(
        metadata_path
        for metadata_path in sorted(
            (FIXTURE_DPC_ROOT / profile).glob("*-metadata.json")
        )
        if json.loads(metadata_path.read_text(encoding="utf-8"))[
            "request_path"
        ].endswith("/tarifPointeCritique")
    )
    dpc_metadata: dict[str, Any] = json.loads(dpc_data_path.read_text(encoding="utf-8"))
    dpc_data: dict[str, Any] = json.loads(
        (dpc_data_path.parent / dpc_metadata["body_file"]).read_text(encoding="utf-8")
    )
    winter_period = dpc_data["periodesHiver"][0]
    reference_date = date.fromisoformat(rate_scenario["reference_date"])
    freeze_hydroqc_clock(monkeypatch, reference_date)
    transport = FixtureReplayTransport(
        [FIXTURE_ROOT / profile, FIXTURE_DPC_ROOT / profile]
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
        assert isinstance(scenario.contract, ContractDPC)
        assert scenario.contract.rate == rate_scenario["rate"]
        assert scenario.contract.rate_option == rate_scenario["option"]
        assert DPC_PROPERTIES <= set(scenario.surface["properties"])
        assert "contract.get_dpc_data" in scenario.called_methods
        assert scenario.contract.last_update_date == date.fromisoformat(
            winter_period["dateMaj"].split("T", maxsplit=1)[0]
        )
        assert scenario.contract.critical_called_hours == int(
            winter_period.get("hrsCritiquesAppelees", 0)
        )
        assert scenario.contract.max_critical_called_hours == int(
            winter_period["hrsCritiquesAppeleesMax"]
        )
        winter_start = datetime.fromisoformat(
            winter_period["dateDebut"].replace("Z", "+00:00")
        )
        winter_end = datetime.fromisoformat(
            winter_period["dateFin"].replace("Z", "+00:00")
        )
        winter_update = datetime.fromisoformat(
            winter_period["dateMaj"].replace("Z", "+00:00")
        )
        assert scenario.contract.winter_total_days == (winter_end - winter_start).days
        assert scenario.contract.winter_total_days_last_update == max(
            0, (winter_update - winter_start).days
        )
        reference_datetime = datetime.combine(
            reference_date, time.min, tzinfo=winter_start.tzinfo
        )
        expected_winter_state = (
            WinterState.CURRENT
            if winter_start <= reference_datetime <= winter_end
            else WinterState.FINISHED
        )
        assert scenario.contract.winter_state == expected_winter_state
        expected_savings = winter_period.get("montantEconPerteVSTarifBase")
        assert scenario.contract.amount_saved_vs_base_rate == (
            None if expected_savings is None else float(expected_savings)
        )
        assert scenario.surface["methods"]
        assert scenario.surface["subobjects"]
    finally:
        await client.close_session()
