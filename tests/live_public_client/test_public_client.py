"""Opt-in live integration tests for Hydro-Québec public peak data."""

import os

import pytest

from hydroqc.public_client import PublicClient

pytestmark = pytest.mark.skipif(
    os.getenv("HYDROQC_RUN_PUBLIC_CLIENT_TESTS") != "1",
    reason=(
        "Set HYDROQC_RUN_PUBLIC_CLIENT_TESTS=1 to query Hydro-Québec live "
        "public endpoints."
    ),
)


async def _check_live_public_client(
    rate_code: str, rate_option_code: str | None = None
) -> None:
    """Check portal status and replay live peaks through the PublicClient API."""
    client = PublicClient(
        rate_code=rate_code,
        rate_option_code=rate_option_code,
    )
    try:
        assert await client.check_hq_portal_status()
        await client.fetch_peak_data()

        peaks = client.peaks
        assert isinstance(peaks, list)
        assert peaks == sorted(peaks, key=lambda peak: peak.start_date)
        assert all(peak.start_date <= peak.end_date for peak in peaks)
    finally:
        await client.close_session()


@pytest.mark.asyncio
async def test_public_client_fetches_live_dpc_peaks() -> None:
    """Fetch DPC open-data peaks from Hydro-Québec through PublicClient."""
    await _check_live_public_client("DPC")


@pytest.mark.asyncio
async def test_public_client_fetches_live_d_cpc_peaks() -> None:
    """Fetch D_CPC open-data peaks from Hydro-Québec through PublicClient."""
    await _check_live_public_client("D", "CPC")
