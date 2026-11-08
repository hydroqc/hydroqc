"""Check the planned unavailability feed used by the portal status."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from hydroqc.hydro_api import client as client_module
from hydroqc.hydro_api.consts import IS_HYDRO_PORTAL_UP_URL, PORTAL_UNAVAILABILITY_URL
from hydroqc.utils import EST_TIMEZONE
from tests.unit.test_mfa import FakeResponse, ScriptedTransport, build_client


def _feed(service: str = "appMobileAuthentifiee") -> str:
    """Return a feed with one window on 2026-11-07 18:00 to 2026-11-08 18:00."""
    return json.dumps(
        {
            service: [
                {
                    "from": "2026-11-07 18:00:00",
                    "to": "2026-11-08 18:00:00",
                    "messages_fr": "<h2>Travaux</h2>",
                    "messages_en": "<h2>Maintenance</h2>",
                }
            ]
        }
    )


def _freeze(monkeypatch: pytest.MonkeyPatch, when: str | datetime) -> None:
    """Make the client see ``when`` (Hydro-Quebec local time, or aware) as now."""
    frozen = (
        when
        if isinstance(when, datetime)
        else EST_TIMEZONE.localize(datetime.strptime(when, "%Y-%m-%d %H:%M"))
    )

    class FrozenDatetime(datetime):
        """Datetime whose now() is fixed."""

        @classmethod
        def now(cls, tz: Any = None) -> Any:
            """Return the frozen time."""
            return frozen.astimezone(tz) if tz else frozen

    monkeypatch.setattr(client_module, "datetime", FrozenDatetime)


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("service", ["libreService", "appMobileAuthentifiee"])
async def test_portal_down_during_window(
    monkeypatch: pytest.MonkeyPatch, service: str
) -> None:
    """A window covering now reports the portal down without probing it."""
    _freeze(monkeypatch, "2026-11-07 20:00")
    transport = ScriptedTransport()
    transport.add("get", PORTAL_UNAVAILABILITY_URL, FakeResponse(text=_feed(service)))
    client = build_client(transport)

    window = await client.get_portal_unavailability()
    assert window is not None
    assert window["service"] == service
    assert window["to"] == "2026-11-08 18:00:00"
    assert await client.check_portal_status() is False
    assert not transport.called(IS_HYDRO_PORTAL_UP_URL)
    # The feed is fetched with a cache buster, like the Espace client does.
    feed_call = next(c for c in transport.calls if PORTAL_UNAVAILABILITY_URL in c[1])
    assert feed_call[2]["params"]["date"].isdigit()


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("when", ["2026-11-07 17:59", "2026-11-08 18:00"])
async def test_portal_probed_outside_window(
    monkeypatch: pytest.MonkeyPatch, when: str
) -> None:
    """Outside the window the portal page is probed as before."""
    _freeze(monkeypatch, when)
    transport = ScriptedTransport()
    transport.add("get", PORTAL_UNAVAILABILITY_URL, FakeResponse(text=_feed()))
    transport.add("get", IS_HYDRO_PORTAL_UP_URL, FakeResponse())
    client = build_client(transport)

    assert await client.get_portal_unavailability() is None
    assert await client.check_portal_status() is True
    assert transport.called(IS_HYDRO_PORTAL_UP_URL)


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize(("utc_hour", "down"), [(21, False), (22, True)])
async def test_summer_window_uses_daylight_time(
    monkeypatch: pytest.MonkeyPatch, utc_hour: int, down: bool
) -> None:
    """A July 18:00 start is 22:00 UTC (EDT, -04:00), not 23:00 UTC (EST)."""
    _freeze(monkeypatch, datetime(2026, 7, 15, utc_hour, 0, tzinfo=timezone.utc))
    feed = (
        _feed().replace("2026-11-07", "2026-07-15").replace("2026-11-08", "2026-07-16")
    )
    transport = ScriptedTransport()
    transport.add("get", PORTAL_UNAVAILABILITY_URL, FakeResponse(text=feed))
    client = build_client(transport)

    assert (await client.get_portal_unavailability() is not None) is down


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize(
    "body", ["not json", "[]", '{"libreService": [{"from": "x"}]}']
)
async def test_unreadable_feed_falls_back(body: str) -> None:
    """A feed that cannot be used does not block the usual probe."""
    transport = ScriptedTransport()
    transport.add("get", PORTAL_UNAVAILABILITY_URL, FakeResponse(text=body))
    transport.add("get", IS_HYDRO_PORTAL_UP_URL, FakeResponse())
    client = build_client(transport)

    assert await client.get_portal_unavailability() is None
    assert await client.check_portal_status() is True


@pytest.mark.asyncio(loop_scope="module")
async def test_feed_http_error_falls_back() -> None:
    """A feed answering an error status falls back to the usual probe."""
    transport = ScriptedTransport()
    transport.add("get", PORTAL_UNAVAILABILITY_URL, FakeResponse(status=404))
    transport.add("get", IS_HYDRO_PORTAL_UP_URL, FakeResponse())
    client = build_client(transport)

    assert await client.get_portal_unavailability() is None
    assert await client.check_portal_status() is True
