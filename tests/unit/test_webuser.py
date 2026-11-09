"""WebUser forwards the login options to its client."""

from __future__ import annotations

import pytest

from hydroqc.consts import LoginStatus
from hydroqc.webuser import WebUser
from tests.unit.test_mfa import (
    SEND_CODE_URL,
    ScriptedTransport,
    build_client,
    build_mfa_login,
)


def build_webuser() -> tuple[WebUser, ScriptedTransport]:
    """Create a WebUser whose client runs on a scripted MFA login."""
    transport = build_mfa_login()
    webuser = WebUser("me@example.com", "secret", verify_ssl=True)
    webuser._hydro_client = build_client(transport)  # pylint: disable=protected-access
    return webuser, transport


@pytest.mark.asyncio(loop_scope="module")
async def test_login_sends_the_code_by_default() -> None:
    """The historic call still asks the portal for the code."""
    webuser, transport = build_webuser()

    assert await webuser.login() is LoginStatus.MFA_REQUIRED
    assert transport.called(SEND_CODE_URL)

    await webuser.close_session()


@pytest.mark.asyncio(loop_scope="module")
async def test_login_can_skip_the_code() -> None:
    """A background caller stops at the code step without an e-mail."""
    webuser, transport = build_webuser()

    assert await webuser.login(send_mfa_code=False) is LoginStatus.MFA_REQUIRED
    assert webuser.mfa_required is True
    assert not transport.called(SEND_CODE_URL)

    # The interactive caller asks for the code when the user is ready for it.
    assert await webuser.request_mfa_code() is True
    assert transport.called(SEND_CODE_URL)

    await webuser.close_session()
