"""Offline Azure AD B2C login flow, including the e-mail MFA branch."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from hydroqc.consts import LoginStatus
from hydroqc.error import (
    HydroQcHTTPError,
    HydroQcMFAExpiredError,
    HydroQcMFAInvalidCodeError,
    HydroQcMFARequiredError,
)
from hydroqc.hydro_api.mfa import MFAKind
from tests.unit.test_mfa import (
    CALLBACK_CODE,
    COMBINED_CONFIRMED_URL,
    MFA_PAGE_HTML,
    SELF_ASSERTED_CONFIRMED_URL,
    SEND_CODE_URL,
    VERIFY_CODE_URL,
    FakeResponse,
    b2c_url,
    build_client,
    build_mfa_login,
    build_plain_login,
)

MFA_PAGE = b2c_url(f"{COMBINED_CONFIRMED_URL}?tx=tx-1&p=B2C_1A_PRD_signup_signin")
SELF_ASSERTED_PAGE = b2c_url("SelfAsserted?tx=tx-1&p=B2C_1A_PRD_signup_signin")


@pytest.mark.asyncio(loop_scope="module")
async def test_login_without_mfa() -> None:
    """A single factor login is still SUCCESS and stores the tokens."""
    transport = build_plain_login()
    client = build_client(transport)

    assert await client.login() is LoginStatus.SUCCESS
    assert bool(await client.login()) is True
    assert client.access_token == "access-1"
    assert client.refresh_token == "refresh-1"
    assert client.mfa_required is False
    assert transport.called("oauth2/v2.0/token")

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_login_reports_mfa_for_a_code_step() -> None:
    """The verification page answered directly is recognised, not a failure."""
    transport = build_mfa_login()
    client = build_client(transport)

    assert await client.login() is LoginStatus.MFA_REQUIRED
    assert client.mfa_required is True
    challenge = client._mfa_challenge
    assert challenge is not None
    assert challenge.kind is MFAKind.SELF_ASSERTED
    assert challenge.control_id == "readOnlyEmailVerificationControl"
    assert challenge.email_field == "readOnlyEmail"
    assert challenge.code_field == "VerificationCode"
    # Logging in asks for the code on its own.
    assert transport.called(SEND_CODE_URL)
    assert transport.sent(SEND_CODE_URL) == {"readOnlyEmail": ["me@example.com"]}

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_login_reports_mfa_from_a_redirect() -> None:
    """A redirect to the verification page is also recognised."""
    transport = build_mfa_login(page_location=SELF_ASSERTED_PAGE)
    client = build_client(transport)

    assert await client.login() is LoginStatus.MFA_REQUIRED
    assert client._mfa_challenge is not None
    assert client._mfa_challenge.control_id == "readOnlyEmailVerificationControl"

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_login_unexpected_redirect_fails() -> None:
    """A redirect that carries neither a code nor a verification page fails."""
    transport = build_plain_login()
    transport.routes[2] = (
        "get",
        COMBINED_CONFIRMED_URL,
        FakeResponse(status=302, headers={"Location": b2c_url("NewStep?tx=tx-1")}),
    )
    transport.add("get", "NewStep", FakeResponse(text="<html>maintenance</html>"))
    client = build_client(transport)

    assert await client.login() is LoginStatus.FAILED
    assert client.mfa_required is False

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_login_page_with_control_reports_mfa() -> None:
    """A redirect whose page holds a verification control is an MFA step."""
    transport = build_plain_login()
    transport.routes[2] = (
        "get",
        COMBINED_CONFIRMED_URL,
        FakeResponse(status=302, headers={"Location": b2c_url("NewStep?tx=tx-1")}),
    )
    transport.add("get", "NewStep", FakeResponse(text=MFA_PAGE_HTML))
    transport.add("post", SEND_CODE_URL, FakeResponse(text='{"status":"200"}'))
    client = build_client(transport)

    assert await client.login() is LoginStatus.MFA_REQUIRED
    assert client.mfa_required is True

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_login_redirect_outside_hydro_fails() -> None:
    """A redirect to a foreign host is not a verification step."""
    transport = build_plain_login()
    transport.routes[2] = (
        "get",
        COMBINED_CONFIRMED_URL,
        FakeResponse(status=302, headers={"Location": "https://example.com/step"}),
    )
    client = build_client(transport)

    assert await client.login() is LoginStatus.FAILED
    assert client.mfa_required is False

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_login_unknown_page_fails_cleanly() -> None:
    """A 200 page that is neither a callback nor a code prompt fails."""
    transport = build_plain_login()
    transport.routes[2] = (
        "get",
        COMBINED_CONFIRMED_URL,
        FakeResponse(text="<html>maintenance</html>"),
    )
    client = build_client(transport)

    assert await client.login() is LoginStatus.FAILED

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_login_missing_transaction_fails() -> None:
    """A sign-in page without settings reports a failure."""
    transport = build_plain_login()
    transport.routes[0] = (
        "get",
        "oauth2/v2.0/authorize",
        FakeResponse(text="<html></html>"),
    )
    client = build_client(transport)

    assert await client.login() is LoginStatus.FAILED

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_submit_mfa_code_completes_the_login() -> None:
    """A submitted code is verified, the form is confirmed and tokens follow."""
    transport = build_mfa_login()
    client = build_client(transport)

    assert await client.login() is LoginStatus.MFA_REQUIRED
    assert await client.submit_mfa_code("123456") is LoginStatus.SUCCESS
    assert client.access_token == "access-1"
    assert client.mfa_required is False

    assert transport.sent(VERIFY_CODE_URL) == {
        "readOnlyEmail": ["me@example.com"],
        "VerificationCode": ["123456"],
    }
    assert transport.sent("/SelfAsserted?tx=") == {
        "readOnlyEmail": ["me@example.com"],
        "VerificationCode": ["123456"],
        "request_type": ["RESPONSE"],
    }
    assert transport.called(SELF_ASSERTED_CONFIRMED_URL)
    assert transport.called("oauth2/v2.0/token")

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_submit_mfa_code_after_a_refusal() -> None:
    """A refused code keeps the step alive so another code can be tried."""
    transport = build_mfa_login()
    client = build_client(transport)

    assert await client.login() is LoginStatus.MFA_REQUIRED
    transport.routes.insert(
        0,
        (
            "post",
            VERIFY_CODE_URL,
            FakeResponse(text='{"status":"400","message":"bad code"}'),
        ),
    )
    with pytest.raises(HydroQcMFAInvalidCodeError):
        await client.submit_mfa_code("000000")
    assert client.mfa_required is True

    transport.routes.pop(0)
    assert await client.submit_mfa_code("123456") is LoginStatus.SUCCESS

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_submit_mfa_code_reports_expiry() -> None:
    """An expired transaction asks for a fresh login."""
    transport = build_mfa_login()
    client = build_client(transport)

    assert await client.login() is LoginStatus.MFA_REQUIRED
    transport.routes.insert(
        0,
        (
            "post",
            VERIFY_CODE_URL,
            FakeResponse(
                text='{"status":"400","message":"The transaction has expired"}'
            ),
        ),
    )
    with pytest.raises(HydroQcMFAExpiredError):
        await client.submit_mfa_code("123456")
    assert client.mfa_required is False

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_submit_mfa_code_without_challenge() -> None:
    """Submitting a code without a pending step is an error, not a login."""
    client = build_client(build_plain_login())

    with pytest.raises(HydroQcMFAExpiredError):
        await client.submit_mfa_code("123456")

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_submit_empty_mfa_code_is_refused() -> None:
    """An empty code never reaches the portal."""
    transport = build_mfa_login()
    client = build_client(transport)

    assert await client.login() is LoginStatus.MFA_REQUIRED
    with pytest.raises(HydroQcMFAInvalidCodeError):
        await client.submit_mfa_code("   ")
    assert not transport.called(VERIFY_CODE_URL)

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_expired_session_reports_mfa_required() -> None:
    """A silent re-login that hits the code step cannot be done in background."""
    transport = build_mfa_login()
    client = build_client(transport)
    client.refresh_token_expiry = datetime.now() - timedelta(minutes=1)

    with pytest.raises(HydroQcMFARequiredError):
        await client._get_access_token()

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_expired_session_replays_the_sso() -> None:
    """An expired refresh token still reuses the single sign-on cookie."""
    transport = build_plain_login()
    transport.routes.insert(
        0,
        (
            "get",
            "oauth2/v2.0/authorize",
            FakeResponse(status=302, headers={"Location": CALLBACK_CODE}),
        ),
    )
    client = build_client(transport)
    client.refresh_token_expiry = datetime.now() - timedelta(minutes=1)

    assert await client._get_access_token() == "access-1"
    assert not transport.called("SelfAsserted")

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_silent_login_replays_the_session() -> None:
    """The stored cookies get an authorization code without interaction."""
    transport = build_plain_login()
    transport.routes.insert(
        0,
        (
            "get",
            "oauth2/v2.0/authorize",
            FakeResponse(status=302, headers={"Location": CALLBACK_CODE}),
        ),
    )
    client = build_client(transport)

    assert await client.try_silent_login() is LoginStatus.SUCCESS
    assert client.access_token == "access-1"

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_silent_login_falls_back_cleanly() -> None:
    """An unusable stored session reports FAILED so the caller can prompt."""
    transport = build_plain_login()
    transport.routes.insert(
        0,
        (
            "get",
            "oauth2/v2.0/authorize",
            FakeResponse(
                status=302,
                headers={
                    "Location": "https://session.hydroquebec.com/oauth2/callback"
                    "?error=login_required"
                },
            ),
        ),
    )
    client = build_client(transport)

    assert await client.try_silent_login() is LoginStatus.FAILED

    await client.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_silent_login_ignores_http_errors() -> None:
    """An HTTP failure during a silent attempt reports FAILED instead of raising."""

    def fail(_kwargs: dict[str, Any]) -> FakeResponse:
        raise HydroQcHTTPError("boom", 500)

    transport = build_plain_login()
    transport.routes.insert(0, ("get", "oauth2/v2.0/authorize", fail))
    client = build_client(transport)

    assert await client.try_silent_login() is LoginStatus.FAILED

    await client.close_session()


def test_token_payload_without_lifetime() -> None:
    """A token answer without a refresh lifetime does not break the client."""
    client = build_client(build_plain_login())
    payload: dict[str, Any] = {
        "access_token": "access-2",
        "expires_in": "3600",
        "refresh_token": "refresh-2",
        "id_token": "id-2",
    }
    client._store_tokens(payload)
    assert client.access_token == "access-2"
    assert client.refresh_token == "refresh-2"
    assert client.refresh_token_expiry == client.access_token_expiry
