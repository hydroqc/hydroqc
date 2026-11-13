"""Replay public-client operations using captured public peak-data fixtures."""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import pytest

from hydroqc.hydro_api.consts import OPEN_DATA_PEAK_URL
from hydroqc.public_client import PublicClient

from .fixture_replay import FixtureReplayTransport
from .scenario_helpers import freeze_hydroqc_clock

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures"
PUBLIC_FIXTURE_SETS = sorted(
    scenario_file.parent
    for scenario_file in FIXTURE_ROOT.glob("PUBLIC_PEAKS*/**/scenario.json")
)


def _load_peak_events(fixture_root: Path) -> list[dict[str, Any]]:
    """Load the event list from the captured open-data HTTP response."""
    for metadata_path in sorted((fixture_root / "public").glob("*-metadata.json")):
        metadata: dict[str, Any] = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata["request_path"] == urlsplit(OPEN_DATA_PEAK_URL).path:
            response = json.loads(
                (metadata_path.parent / metadata["body_file"]).read_text(
                    encoding="utf-8"
                )
            )
            return cast(list[dict[str, Any]], response["evenements"])
    raise AssertionError("The public fixture has no open peak-data response")


async def _replay_public_client(
    fixture_root: Path,
    rate_code: str,
    rate_option_code: str,
    offer: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay portal and open-data requests through the PublicClient API."""
    scenario: dict[str, Any] = json.loads(
        (fixture_root / "scenario.json").read_text(encoding="utf-8")
    )
    reference_date = date.fromisoformat(scenario["reference_date"])
    freeze_hydroqc_clock(monkeypatch, reference_date)
    events = _load_peak_events(fixture_root)
    transport = FixtureReplayTransport(fixture_root / "public")
    client = PublicClient(
        rate_code=rate_code,
        rate_option_code=rate_option_code,
    )
    # pylint: disable=protected-access
    client._hydro_client.http_request = transport.request  # type: ignore[assignment]
    try:
        assert await client.check_hq_portal_status()
        await client.fetch_peak_data()
        transport.assert_complete()

        peaks = client.peaks
        assert isinstance(peaks, list)
        assert peaks == sorted(peaks, key=lambda peak: peak.start_date)
        assert all(peak.start_date <= peak.end_date for peak in peaks)
        assert client.peak_handler is not None

        if offer == "TPC-DPC":
            expected_peaks = sorted(
                (
                    datetime.fromisoformat(event["dateDebut"]),
                    datetime.fromisoformat(event["dateFin"]),
                )
                for event in events
                if event["offre"] == offer
            )
            actual_peaks = [(peak.start_date, peak.end_date) for peak in peaks]
            assert actual_peaks == expected_peaks
        else:
            event_end_dates = {
                datetime.fromisoformat(event["dateFin"])
                for event in events
                if event["offre"] == offer
            }
            assert peaks
            assert {peak.end_date for peak in peaks if peak.is_critical} == {
                peak.end_date for peak in peaks if peak.end_date in event_end_dates
            }
    finally:
        await client.close_session()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture_root",
    PUBLIC_FIXTURE_SETS,
    ids=lambda path: f"{path.parent.name}-{path.name}",
)
async def test_public_client_replays_dpc_fixtures(
    fixture_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay DPC open-data requests without contacting Hydro-Québec."""
    await _replay_public_client(fixture_root, "DPC", "", "TPC-DPC", monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture_root",
    PUBLIC_FIXTURE_SETS,
    ids=lambda path: f"{path.parent.name}-{path.name}",
)
async def test_public_client_replays_d_cpc_fixtures(
    fixture_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay D_CPC open-data requests without contacting Hydro-Québec."""
    await _replay_public_client(fixture_root, "D", "CPC", "CPC-D", monkeypatch)
