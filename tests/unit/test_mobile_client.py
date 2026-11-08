"""Pin the Azure AD B2C parameters of the web and mobile clients.

The web client stays the default. The mobile client is opt-in: its client id,
redirect URI, policy, scope and ``offline_access`` refresh grant are what make
the 90-day sliding session possible.
"""

from __future__ import annotations

from typing import Any

import pytest

from hydroqc.consts import LoginStatus
from hydroqc.hydro_api.consts import (
    AUTH_CLIENT_MOBILE,
    AUTH_CLIENT_WEB,
    AUTH_CLIENTS,
    AZB2C_CLIENT_ID_MOBILE,
    AZB2C_CLIENT_ID_WEB,
    RELATION_URL,
)
from tests.unit.test_mfa import (
    FakeResponse,
    ScriptedTransport,
    build_client,
    build_plain_login,
    token_body,
)

TOKEN_URL = "oauth2/v2.0/token"
AUTHORIZE_URL = "oauth2/v2.0/authorize"
BOTH_CLIENTS = pytest.mark.parametrize("name", [AUTH_CLIENT_WEB, AUTH_CLIENT_MOBILE])


def _call(
    transport: ScriptedTransport, method: str, url_part: str
) -> tuple[str, dict[str, Any]]:
    """Return the URL and kwargs of the last recorded call to ``url_part``."""
    for call_method, url, kwargs in reversed(transport.calls):
        if call_method == method and url_part in url:
            return url, kwargs
    raise AssertionError(f"no {method} call matched {url_part}")


def test_mobile_client_values() -> None:
    """The mobile client keeps the app's audience, policy and offline_access."""
    mobile = AUTH_CLIENTS[AUTH_CLIENT_MOBILE]
    assert mobile.client_id == AZB2C_CLIENT_ID_MOBILE
    assert "offline_access" in mobile.scope
    assert "hq-clientele-mobile" in mobile.scope
    assert mobile.policy.lower().endswith("_mobile")
    assert mobile.redirect_uri.startswith("msauth://com.hydroquebec.mf_android/")
    assert mobile.api_url(RELATION_URL).count("/wsapi/mobile/") == 1


def test_web_is_the_default_client() -> None:
    """Without auth_client the web client and its /wsapi/web/ API are used."""
    client = build_client(ScriptedTransport())
    assert client._auth is AUTH_CLIENTS[AUTH_CLIENT_WEB]
    assert client._auth.client_id == AZB2C_CLIENT_ID_WEB
    assert client._auth.api_url(RELATION_URL) == RELATION_URL


def test_unknown_client_is_rejected() -> None:
    """A typo in auth_client fails at construction, not at login."""
    with pytest.raises(ValueError):
        build_client(ScriptedTransport(), auth_client="android")


@BOTH_CLIENTS
@pytest.mark.asyncio(loop_scope="module")
async def test_login_uses_the_selected_client(name: str) -> None:
    """Authorize and code grant carry the selected client's parameters."""
    auth = AUTH_CLIENTS[name]
    transport = build_plain_login()
    client = build_client(transport, auth_client=name)

    assert await client.login() is LoginStatus.SUCCESS

    url, kwargs = _call(transport, "get", AUTHORIZE_URL)
    assert auth.policy.lower() in url
    params = kwargs["params"]
    assert params["client_id"] == auth.client_id
    assert params["redirect_uri"] == auth.redirect_uri
    assert params["scope"] == auth.scope
    assert params["code_challenge_method"] == "S256"

    url, kwargs = _call(transport, "post", TOKEN_URL)
    assert auth.policy.lower() in url
    data = kwargs["data"]
    assert data["grant_type"] == "authorization_code"
    assert data["client_id"] == auth.client_id
    assert data["redirect_uri"] == auth.redirect_uri

    await client.close_session()


@BOTH_CLIENTS
@pytest.mark.asyncio(loop_scope="module")
async def test_refresh_uses_selected_client(name: str) -> None:
    """A refresh posts the selected client id and scope."""
    auth = AUTH_CLIENTS[name]
    transport = ScriptedTransport()
    transport.add("post", TOKEN_URL, FakeResponse(text=token_body()))
    client = build_client(transport, auth_client=name)
    client.refresh_token = "refresh-0"

    assert await client._refresh_token() is True

    url, kwargs = _call(transport, "post", TOKEN_URL)
    assert auth.policy.lower() in url
    data = kwargs["data"]
    assert data["grant_type"] == "refresh_token"
    assert data["client_id"] == auth.client_id
    assert data["scope"] == auth.scope
    assert client.refresh_token == "refresh-1"

    await client.close_session()


@BOTH_CLIENTS
@pytest.mark.asyncio(loop_scope="module")
async def test_private_api_uses_selected_client(name: str) -> None:
    """The private API is called on /wsapi/<channel>/ of the selected client."""
    auth = AUTH_CLIENTS[name]
    transport = ScriptedTransport()
    transport.add("get", "/relations", FakeResponse(text="[]"))
    client = build_client(transport, auth_client=name)

    async def _token(*_: Any, **__: Any) -> str:
        return "access-1"

    client._get_access_token = _token

    await client.get_user_info()

    url, _ = _call(transport, "get", "/relations")
    assert f"/wsapi/{auth.api_channel}/" in url


@BOTH_CLIENTS
def test_session_state_round_trip(name: str) -> None:
    """A session exported by a client is restored by the same client."""
    client = build_client(ScriptedTransport(), auth_client=name)
    client.refresh_token = "token-1"
    state = client.export_session_state()
    assert state["client_id"] == AUTH_CLIENTS[name].client_id

    restored = build_client(ScriptedTransport(), auth_client=name)
    restored.import_session_state(state)
    assert restored.refresh_token == "token-1"


@pytest.mark.parametrize(
    "state",
    [
        {"client_id": AZB2C_CLIENT_ID_WEB, "refresh_token": "web-token"},
        # A state predating the client id field comes from the web client.
        {"refresh_token": "web-token"},
    ],
)
def test_mobile_drops_web_tokens(
    state: dict[str, Any],
) -> None:
    """Web tokens are dropped, the login host cookies are kept."""
    cookies = [{"name": "x-ms-cpim-sso", "value": "sso", "domain": "example"}]
    client = build_client(ScriptedTransport(), auth_client=AUTH_CLIENT_MOBILE)
    client.import_session_state({**state, "cookies": cookies})

    assert client.refresh_token != "web-token"
    assert client.is_session_expired() is True
    assert client._pending_cookies == cookies


def test_web_keeps_legacy_state() -> None:
    """The default client still restores a session exported by 4.3.x."""
    client = build_client(ScriptedTransport())
    client.import_session_state({"refresh_token": "legacy-token"})
    assert client.refresh_token == "legacy-token"
