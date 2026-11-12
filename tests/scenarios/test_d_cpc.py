"""Replay the full D_CPC scenario through the real HydroClient methods."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from hydroqc.capture import run_webuser_contract_scenario
from hydroqc.hydro_api.client import HydroClient

from .fixture_replay import FixtureReplayTransport
from .scenario_helpers import freeze_hydroqc_clock

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures"
D_CPC_FIXTURE_SETS = sorted(
    fixture_set
    for scenario_folder in FIXTURE_ROOT.glob("D_CPC_*")
    for fixture_set in scenario_folder.iterdir()
    if fixture_set.is_dir() and (fixture_set / "scenario.json").is_file()
)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["web", "mobile"])
@pytest.mark.parametrize(
    "fixture_root",
    D_CPC_FIXTURE_SETS,
    ids=lambda path: f"{path.parent.name}-{path.name}",
)
async def test_full_d_cpc_scenario_replays(
    profile: str, fixture_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay authenticated contract selection and all captured D_CPC operations."""
    common_profile = fixture_root / "common" / profile
    info_compte_captures = [
        metadata_path
        for metadata_path in common_profile.glob("*-metadata.json")
        if json.loads(metadata_path.read_text(encoding="utf-8"))
        .get("request_path", "")
        .endswith("/infoCompte")
    ]
    if len(info_compte_captures) < 2:
        pytest.skip(
            "Cette fixture doit être recapturée pour inclure les réponses infoCompte "
            "du parcours WebUser → Customer → Account."
        )

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
        scenario = await run_webuser_contract_scenario(
            client, rate_scenario["contract_id"], profile, reference_date
        )
        transport.assert_complete()

        assert scenario.context == tuple(rate_scenario["context"])
        assert scenario.contract.rate == rate_scenario["rate"]
        assert scenario.contract.rate_option == rate_scenario["option"]
        assert scenario.surface["methods"]
        assert scenario.surface["properties"]
        assert scenario.surface["subobjects"]
    finally:
        await client.close_session()
