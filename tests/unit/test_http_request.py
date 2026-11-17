"""HTTP errors are logged with their status, not as an empty traceback."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from hydroqc.error import HydroQcHTTPError
from hydroqc.hydro_api.client import HydroClient

URL = "https://services-cl.solutions.hydroquebec.com/wsapi/web/prive/api/v1_0/relations"


class FakeResponse:
    """The parts of :class:`aiohttp.ClientResponse` http_request reads."""

    def __init__(self, status: int, text: str) -> None:
        """Store the canned answer."""
        self.status = status
        self._text = text

    async def text(self) -> str:
        """Return the canned body."""
        return self._text


class FakeSession:
    """Answer every GET with the same response."""

    def __init__(self, response: FakeResponse) -> None:
        """Store the response to return."""
        self.response = response

    async def get(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        """Return the canned response."""
        return self.response


def build_client(response: FakeResponse) -> HydroClient:
    """Create a client whose HTTP session answers ``response``."""
    client = HydroClient("me@example.com", "secret", verify_ssl=True)
    client._session = FakeSession(response)  # type: ignore[assignment]
    client._cached_version = "test"
    return client


@pytest.mark.asyncio(loop_scope="module")
async def test_http_error_logs_its_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The error line names the status and has no exception info."""
    client = build_client(FakeResponse(500, '{"message": "maintenance"}'))

    with caplog.at_level(logging.DEBUG, logger="hydroqc.httpclient"):
        with pytest.raises(HydroQcHTTPError) as err:
            await client.http_request(URL, "get")

    assert err.value.status_code == 500
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "500" in errors[0].getMessage()
    assert errors[0].exc_info is None or errors[0].exc_info == (None, None, None)
    assert "NoneType" not in caplog.text
    # The body stays available at debug level for diagnosis.
    assert '{"message": "maintenance"}' in caplog.text
