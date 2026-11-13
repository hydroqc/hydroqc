"""Replay the full DT scenario through the real HydroClient methods."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from hydroqc.contract.contract_dt import ContractDT
from hydroqc.hydro_api.client import HydroClient
from hydroqc.tools.capture import run_contract_scenario

from .fixture_replay import FixtureReplayTransport
from .scenario_helpers import freeze_hydroqc_clock

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures"
DT_FIXTURE_SETS = sorted(
    scenario_file.parent
    for scenario_file in FIXTURE_ROOT.glob("DT_*/DT/**/scenario.json")
)
DT_PROPERTIES = {
    "amount_saved_vs_base_rate",
    "cp_higher_price_consumption",
    "cp_lower_price_consumption",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["web", "mobile"])
@pytest.mark.parametrize(
    "fixture_root",
    DT_FIXTURE_SETS,
    ids=lambda path: f"{path.parent.name}-{path.name}",
)
async def test_full_dt_scenario_replays(
    profile: str, fixture_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay contract selection and every captured DT operation."""
    rate_scenario: dict[str, Any] = json.loads(
        (fixture_root / "scenario.json").read_text(encoding="utf-8")
    )
    reference_date = date.fromisoformat(rate_scenario["reference_date"])
    freeze_hydroqc_clock(monkeypatch, reference_date)
    transport = FixtureReplayTransport(
        [
            fixture_root / "common" / profile,
            fixture_root / "contract" / profile,
        ]
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
            client, rate_scenario["contract_id"], reference_date
        )
        transport.assert_complete()

        assert scenario.context == tuple(rate_scenario["context"])
        assert isinstance(scenario.contract, ContractDT)
        assert scenario.contract.rate == rate_scenario["rate"]
        assert scenario.contract.rate_option == rate_scenario["option"]
        assert DT_PROPERTIES <= set(scenario.surface["properties"])

        expected_methods = {
            f"contract.{method}" for method in scenario.surface["methods"]
        }
        for name, subobject in scenario.surface["subobjects"].items():
            expected_methods.update(
                f"contract.{name}.{method}" for method in subobject["methods"]
            )
        assert expected_methods <= set(scenario.called_methods)

        def response_for_path(path_suffix: str) -> dict[str, Any]:
            for metadata_path in sorted(
                (fixture_root / "contract" / profile).glob("*-metadata.json")
            ):
                metadata: dict[str, Any] = json.loads(
                    metadata_path.read_text(encoding="utf-8")
                )
                if metadata["request_path"].endswith(path_suffix):
                    return cast(
                        dict[str, Any],
                        json.loads(
                            (metadata_path.parent / metadata["body_file"]).read_text(
                                encoding="utf-8"
                            )
                        ),
                    )
            raise AssertionError(f"No fixture response found for {path_suffix}")

        period_response = response_for_path(
            "/resourceObtenirDonneesPeriodesConsommation"
        )
        annual_response = response_for_path(
            "/resourceObtenirDonneesConsommationAnnuelles"
        )
        current_period = period_response["results"][0]
        current_year = annual_response["results"][0]["courant"]
        assert scenario.contract.cp_lower_price_consumption == float(
            current_period["consoRegPeriode"]
        )
        assert scenario.contract.cp_higher_price_consumption == float(
            current_period["consoHautPeriode"]
        )
        assert scenario.contract.amount_saved_vs_base_rate == float(
            current_year["montantGainPerteDTversusBaseAnnee"]
        )
        assert scenario.surface["methods"]
    finally:
        await client.close_session()
