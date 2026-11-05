"""Offline tests for the e-mail verification (MFA) helpers."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, cast
from urllib.parse import parse_qs

import aiohttp
import pytest
import yarl

from hydroqc.consts import LoginStatus
from hydroqc.hydro_api.client import HydroClient
from hydroqc.hydro_api.mfa import (
    B2CPage,
    MFAChallenge,
    MFAKind,
    call_succeeded,
    clear_transaction_cookies,
    looks_expired,
    restore_cookies,
    send_code_requests,
    serialize_cookies,
    verify_code_requests,
)

SIGNIN_PAGE_HTML = """
<script>
SETTINGS = {"api":"CombinedSigninAndSignup","csrf":"csrf-token","transId":"tx-1",
"hosts":{"tenant":"/32bf9b91-0a36-4385-b231-d9a8fa3b05ab/B2C_1A_PRD_signup_signin",
"policy":"B2C_1A_PRD_signup_signin"},"config":{"enableRememberMe":"False"}};
SA_FIELDS = {"AttributeFields":[{"ID":"signInName","UX_INPUT_TYPE":"EmailBox"},
{"ID":"password","UX_INPUT_TYPE":"Password"}]};
</script>
"""

# Shape of the live verification page: one display control whose nested fields
# carry the read-only address and the code input.
MFA_PAGE_HTML = """
<script>
SETTINGS = {"api":"SelfAsserted","csrf":"mfa-csrf","transId":"tx-1",
"hosts":{"tenant":"/32bf9b91-0a36-4385-b231-d9a8fa3b05ab/B2C_1A_PRD_signup_signin",
"policy":"B2C_1A_PRD_signup_signin"},"config":{"timeout":"180000"}};
SA_FIELDS = {"AttributeFields":[{
"UX_INPUT_TYPE":"VerificationControl","USER_INPUT_TYPE":"DisplayControl",
"ID":"readOnlyEmailVerificationControl",
"INPUT_FIELDS":{"VerifyCode":["readOnlyEmail","VerificationCode"],
"SendCode":["readOnlyEmail"]},
"DISPLAY_FIELDS":[
{"UX_INPUT_TYPE":"Readonly","USER_INPUT_TYPE":"Readonly","CONTROL_CLAIM":"readOnlyEmail",
"ID":"readOnlyEmail","PRE":"me@example.com","IS_RDO":true},
{"UX_INPUT_TYPE":"TextBox","USER_INPUT_TYPE":"TextBox","CONTROL_CLAIM":"VerificationCode",
"ID":"VerificationCode","PRE":""}
]}]};
</script>
"""

CALLBACK_CODE = (
    "msauth.com.hydroquebec.infos-pannes://auth/"
    "?state=%257B%2522redirectUrl%2522%253A%2522CONSO%2522%257D&code=AUTH-CODE"
)

AUTHORIZE_URL = "oauth2/v2.0/authorize"
COMBINED_CONFIRMED_URL = "api/CombinedSigninAndSignup/confirmed"
SELF_ASSERTED_CONFIRMED_URL = "api/SelfAsserted/confirmed"
SELF_ASSERTED_URL = "/SelfAsserted?tx="
SEND_CODE_URL = (
    "SelfAsserted/DisplayControlAction/vbeta/readOnlyEmailVerificationControl/SendCode"
)
VERIFY_CODE_URL = (
    "SelfAsserted/DisplayControlAction/vbeta/"
    "readOnlyEmailVerificationControl/VerifyCode"
)
TOKEN_URL = "oauth2/v2.0/token"


class FakeResponse:
    """Minimal stand-in for :class:`aiohttp.ClientResponse`."""

    def __init__(
        self,
        status: int = 200,
        text: str = "",
        headers: dict[str, str] | None = None,
        url: str = "https://connexion.solutions.hydroquebec.com/",
    ) -> None:
        """Store the canned answer."""
        self.status = status
        self.headers = headers or {}
        self.url = yarl.URL(url)
        self._text = text

    async def text(self) -> str:
        """Return the body."""
        return self._text

    async def json(self, content_type: str | None = None) -> Any:
        """Return the decoded body."""
        del content_type
        return json.loads(self._text)


class ScriptedTransport:
    """Route HTTP calls to canned answers, and record what was sent."""

    def __init__(self) -> None:
        """Start with no route recorded."""
        self.routes: list[tuple[str, str, Any]] = []
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def add(self, method: str, url_part: str, reply: Any) -> None:
        """Register an answer for the first call matching ``method``/``url_part``."""
        self.routes.append((method, url_part, reply))

    async def request(self, url: str, method: str, **kwargs: Any) -> FakeResponse:
        """Fake ``HydroClient.http_request``."""
        self.calls.append((method, str(url), kwargs))
        for index, (route_method, url_part, reply) in enumerate(self.routes):
            if route_method == method and url_part in str(url):
                if callable(reply):
                    return reply(kwargs)  # type: ignore[no-any-return]
                if isinstance(reply, list):
                    # A list of answers consumed in order for repeated calls.
                    route_reply = reply.pop(0)
                    self.routes[index] = (route_method, url_part, reply)
                    response = cast(FakeResponse, route_reply)
                else:
                    response = cast(FakeResponse, reply)
                # Report the requested URL, like aiohttp does after redirects.
                response.url = yarl.URL(url)
                return response
        raise AssertionError(f"unexpected call: {method} {url}")

    def called(self, url_part: str) -> bool:
        """Tell whether any recorded call targeted ``url_part``."""
        return any(url_part in call[1] for call in self.calls)

    def sent(self, url_part: str) -> dict[str, list[str]]:
        """Return the decoded form body of the last call to ``url_part``."""
        for method, url, kwargs in reversed(self.calls):
            del method
            if url_part in url:
                return parse_qs(str(kwargs.get("data") or ""))
        raise AssertionError(f"no call matched {url_part}")


def _async_noop_session(client: Any) -> Callable[[], Any]:
    """Replace the aiohttp session bootstrap by an in-memory jar."""

    async def _ensure() -> None:
        """Create the in-memory jar and replay the pending cookies."""
        if client._cookie_jar is None:
            client._cookie_jar = aiohttp.CookieJar(quote_cookie=False, unsafe=True)
        if client._pending_cookies:
            restore_cookies(client._cookie_jar, client._pending_cookies)
            client._pending_cookies = []

    return _ensure


def build_client(transport: ScriptedTransport, **kwargs: Any) -> Any:
    """Create a :class:`HydroClient` wired to ``transport``."""
    client = HydroClient("me@example.com", "secret", verify_ssl=True, **kwargs)
    client.http_request = transport.request  # type: ignore[assignment]
    client._get_httpsession = _async_noop_session(client)  # type: ignore[method-assign]
    return client


def token_body(refresh_token: str = "refresh-1") -> str:
    """Return a token endpoint answer."""
    return json.dumps(
        {
            "id_token": "id-1",
            "access_token": "access-1",
            "expires_in": 3600,
            "refresh_token": refresh_token,
            "refresh_token_expires_in": 86400,
        }
    )


def build_plain_login() -> ScriptedTransport:
    """Single factor login: password then callback code."""
    transport = ScriptedTransport()
    transport.add("get", AUTHORIZE_URL, FakeResponse(text=SIGNIN_PAGE_HTML))
    transport.add("post", SELF_ASSERTED_URL, FakeResponse(text='{"status":"200"}'))
    transport.add(
        "get",
        COMBINED_CONFIRMED_URL,
        FakeResponse(status=302, headers={"Location": CALLBACK_CODE}),
    )
    transport.add("post", TOKEN_URL, FakeResponse(text=token_body()))
    return transport


def build_mfa_login(
    page_html: str = MFA_PAGE_HTML, page_location: str | None = None
) -> ScriptedTransport:
    """Login interrupted by the e-mail verification display control."""
    transport = ScriptedTransport()
    transport.add("get", AUTHORIZE_URL, FakeResponse(text=SIGNIN_PAGE_HTML))
    # Password round trip, then the verification page submission.
    transport.add(
        "post",
        SELF_ASSERTED_URL,
        [
            FakeResponse(text='{"status":"200"}'),
            FakeResponse(text='{"status":"200"}'),
        ],
    )
    if page_location is None:
        transport.add("get", COMBINED_CONFIRMED_URL, FakeResponse(text=page_html))
    else:
        transport.add(
            "get",
            COMBINED_CONFIRMED_URL,
            FakeResponse(status=302, headers={"Location": page_location}),
        )
        transport.add("get", "SelfAsserted?tx=", FakeResponse(text=page_html))
    transport.add("post", SEND_CODE_URL, FakeResponse(text='{"status":"200"}'))
    transport.add("post", VERIFY_CODE_URL, FakeResponse(text='{"status":"200"}'))
    transport.add(
        "get",
        SELF_ASSERTED_CONFIRMED_URL,
        FakeResponse(status=302, headers={"Location": CALLBACK_CODE}),
    )
    transport.add("post", TOKEN_URL, FakeResponse(text=token_body()))
    return transport


def b2c_url(path: str) -> str:
    """Return a full B2C page URL."""
    return (
        "https://connexion.solutions.hydroquebec.com/"
        f"32bf9b91-0a36-4385-b231-d9a8fa3b05ab/B2C_1A_PRD_signup_signin/{path}"
    )


# --------------------------------------------------------------------- mfa


def test_page_settings_are_parsed() -> None:
    """The embedded B2C settings drive the flow, not ad-hoc regexes."""
    page = B2CPage.from_html(SIGNIN_PAGE_HTML)
    assert page.csrf == "csrf-token"
    assert page.trans_id == "tx-1"
    assert page.api == "CombinedSigninAndSignup"
    assert page.policy == "B2C_1A_PRD_signup_signin"
    assert page.fields == {"signInName": "EmailBox", "password": "Password"}
    assert page.control is None


def test_verification_page_exposes_fields() -> None:
    """The whole point of the live flow: fields live inside the display control."""
    page = B2CPage.from_html(MFA_PAGE_HTML)
    assert page.fields == {
        "readOnlyEmailVerificationControl": "VerificationControl",
        "readOnlyEmail": "Readonly",
        "VerificationCode": "TextBox",
    }
    assert page.prefill["readOnlyEmail"] == "me@example.com"
    assert page.field_id(("email",)) == "readOnlyEmail"
    assert page.field_id(("verificationcode",)) == "VerificationCode"
    assert page.control is not None
    assert page.control.control_id == "readOnlyEmailVerificationControl"
    assert page.control.payload_fields("SendCode") == ["readOnlyEmail"]
    assert page.control.payload_fields("VerifyCode") == [
        "readOnlyEmail",
        "VerificationCode",
    ]


def test_verification_challenge_from_page() -> None:
    """A page holding a display control is reported as an MFA step."""
    page = B2CPage.from_html(MFA_PAGE_HTML)
    challenge = MFAChallenge.from_page(
        b2c_url(f"{COMBINED_CONFIRMED_URL}?tx=tx-1&p=B2C_1A_PRD_signup_signin"),
        page,
        email="me@example.com",
    )
    assert challenge is not None
    assert challenge.kind is MFAKind.SELF_ASSERTED
    assert challenge.api == "SelfAsserted"
    assert challenge.control_id == "readOnlyEmailVerificationControl"
    assert challenge.email_field == "readOnlyEmail"
    assert challenge.code_field == "VerificationCode"
    assert challenge.email_value == "me@example.com"
    assert challenge.csrf == "mfa-csrf"


def test_display_control_requests_match() -> None:
    """SendCode and VerifyCode POST the form B2C's display control expects."""
    page = B2CPage.from_html(MFA_PAGE_HTML)
    challenge = MFAChallenge.from_page(
        b2c_url(f"{COMBINED_CONFIRMED_URL}?tx=tx-1&p=B2C_1A_PRD_signup_signin"),
        page,
        email="me@example.com",
    )
    assert challenge is not None

    send = send_code_requests(challenge)
    assert len(send) == 1
    assert send[0].url == b2c_url(f"{SEND_CODE_URL}?tx=tx-1&p=B2C_1A_PRD_signup_signin")
    assert parse_qs(send[0].data) == {"readOnlyEmail": ["me@example.com"]}
    assert send[0].headers["x-csrf-token"] == "mfa-csrf"
    assert (
        send[0].headers["content-type"].startswith("application/x-www-form-urlencoded")
    )

    verify = verify_code_requests(challenge, "123456")
    assert len(verify) == 1
    assert verify[0].url == b2c_url(
        f"{VERIFY_CODE_URL}?tx=tx-1&p=B2C_1A_PRD_signup_signin"
    )
    assert parse_qs(verify[0].data) == {
        "readOnlyEmail": ["me@example.com"],
        "VerificationCode": ["123456"],
    }


def test_challenge_from_legacy_redirect() -> None:
    """Older policies redirect to a DisplayControl page without a control id."""
    challenge = MFAChallenge.from_redirect(
        b2c_url("DisplayControl?tx=tx-1&p=B2C_1A_PRD_signup_signin")
    )
    assert challenge is not None
    assert challenge.kind is MFAKind.DISPLAY_CONTROL
    assert challenge.control_id is None
    assert challenge.trans_id == "tx-1"
    request = verify_code_requests(challenge, "123456")[0]
    assert request.url == b2c_url(
        "api/DisplayControl/VerifyCode?tx=tx-1&p=B2C_1A_PRD_signup_signin"
    )
    assert json.loads(request.data)["verificationCode"] == "123456"


def test_callback_and_plain_pages_ignored() -> None:
    """Only an actual code prompt is reported as a verification step."""
    assert MFAChallenge.from_redirect(CALLBACK_CODE) is None
    assert (
        MFAChallenge.from_redirect(b2c_url("DisplayControl?error=access_denied"))
        is None
    )
    assert MFAChallenge.from_redirect("https://example.com/DisplayControl") is None
    assert (
        MFAChallenge.from_page(
            b2c_url("SelfAsserted?tx=1"), B2CPage.from_html(SIGNIN_PAGE_HTML)
        )
        is None
    )


def test_payload_helpers() -> None:
    """B2C answers are classified consistently."""
    assert call_succeeded({"status": "200"})
    assert call_succeeded({"result": "success"})
    assert not call_succeeded({})
    assert not call_succeeded({"status": "400", "message": "wrong code"})
    assert not call_succeeded({"result": "error"})
    assert looks_expired("", {"message": "The transaction has expired"})
    assert not looks_expired("", {"message": "Invalid verification code"})


def test_login_status_keeps_truthiness() -> None:
    """The historic ``if await login():`` idiom keeps working."""
    assert bool(LoginStatus.SUCCESS) is True
    assert bool(LoginStatus.MFA_REQUIRED) is False
    assert bool(LoginStatus.FAILED) is False
    assert str(LoginStatus.SUCCESS) == "success"


# ---------------------------------------------------------------- cookies


@pytest.mark.asyncio(loop_scope="module")
async def test_cookie_state_round_trip() -> None:
    """Cookies of the login host survive an export/import cycle."""
    jar = aiohttp.CookieJar(quote_cookie=False, unsafe=True)
    jar.update_cookies(
        {"x-ms-cpim-sso:B2C_1A_PRD_signup_signin": "sso"},
        response_url=yarl.URL("https://connexion.solutions.hydroquebec.com/"),
    )
    jar.update_cookies(
        {"portal": "session"},
        response_url=yarl.URL("https://session.hydroquebec.com/"),
    )

    state = serialize_cookies(jar)
    assert len(state) == 2

    restored = aiohttp.CookieJar(quote_cookie=False, unsafe=True)
    restore_cookies(restored, state)
    keys = {morsel.key for morsel in restored}
    assert keys == {"x-ms-cpim-sso:B2C_1A_PRD_signup_signin", "portal"}


@pytest.mark.asyncio(loop_scope="module")
async def test_new_login_keeps_the_sso_cookie() -> None:
    """A fresh login drops the stale transaction cookies but not the SSO one."""

    jar = aiohttp.CookieJar(quote_cookie=False, unsafe=True)
    url = yarl.URL("https://connexion.solutions.hydroquebec.com/")
    jar.update_cookies(
        {
            "x-ms-cpim-sso:connexionhq.onmicrosoft.com_0": "sso",
            "x-ms-cpim-csrf": "csrf",
            "x-ms-cpim-trans": "trans",
            "x-ms-cpim-cache|abc_0": "cache",
        },
        response_url=url,
    )

    clear_transaction_cookies(jar, "connexion.solutions.hydroquebec.com")

    assert {morsel.key for morsel in jar} == {
        "x-ms-cpim-sso:connexionhq.onmicrosoft.com_0"
    }


@pytest.mark.asyncio(loop_scope="module")
async def test_session_state_round_trip() -> None:
    """A session exported by one client restores the token and cookies."""

    source = build_client(build_plain_login())
    assert await source.login() is LoginStatus.SUCCESS
    assert source._cookie_jar is not None
    source._cookie_jar.update_cookies(
        {"x-ms-cpim-sso:policy": "sso"},
        response_url=yarl.URL("https://connexion.solutions.hydroquebec.com/"),
    )

    state = source.export_session_state()
    assert state["refresh_token"] == "refresh-1"
    assert state["cookies"]

    target = build_client(ScriptedTransport())
    target.import_session_state(state)
    await target._get_httpsession()
    assert target.refresh_token == "refresh-1"
    assert target.is_session_expired() is False
    assert target._cookie_jar is not None
    assert {morsel.key for morsel in target._cookie_jar} == {"x-ms-cpim-sso:policy"}

    assert isinstance(datetime.now() + timedelta(seconds=1), datetime)
    await source.close_session()
    await target.close_session()
