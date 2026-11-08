"""HydroQc Client Module."""

import asyncio
import base64
import csv
import json
import logging
import os
import platform
import socket
import uuid
from collections.abc import Iterator, Mapping
from datetime import date, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from io import StringIO
from json import dumps as json_dumps
from typing import Any, cast

import aiohttp
import yarl
from pkce import generate_pkce_pair

from hydroqc.consts import LoginStatus
from hydroqc.error import (
    HydroQcError,
    HydroQcHTTPError,
    HydroQcMFAExpiredError,
    HydroQcMFAInvalidCodeError,
    HydroQcMFARequiredError,
)
from hydroqc.hydro_api.cache import CCached
from hydroqc.hydro_api.consts import (
    ANNUAL_DATA_URL,
    AUTH_CLIENT_WEB,
    AUTH_CLIENTS,
    AZB2C_CLIENT_ID_WEB,
    AZB2C_TIMEOUT_SKEW_SECS,
    CONSO_CSV_URL,
    CONSO_OVERVIEW_CSV_URL,
    CONTRACT_LIST_URL,
    CONTRACT_SUMMARY_URL,
    CUSTOMER_INFO_URL,
    DAILY_CONSUMPTION_API_URL,
    FLEXD_DATA_URL,
    FLEXD_PEAK_URL,
    GET_CPC_API_URL,
    HOST_LOGIN,
    HOST_SERVICES,
    HOST_SESSION,
    HOURLY_CONSUMPTION_API_URL,
    IS_HYDRO_PORTAL_UP_URL,
    MONTHLY_DATA_URL,
    OPEN_DATA_PEAK_URL,
    OUTAGES,
    PERIOD_DATA_URL,
    PORTRAIT_URL,
    RELATION_URL,
    REQUESTS_TIMEOUT,
    SESSION_URL,
)
from hydroqc.hydro_api.mfa import (
    B2CPage,
    MFAChallenge,
    MFAKind,
    call_succeeded,
    clear_cookies_for_host,
    clear_transaction_cookies,
    completion_request,
    extract_auth_code,
    extract_redirect_error,
    looks_expired,
    payload_message,
    restore_cookies,
    send_code_requests,
    serialize_cookies,
    verify_code_requests,
)
from hydroqc.logger import get_logger
from hydroqc.types import (
    AccountContractSummaryTyping,
    AccountTyping,
    ConsumpAnnualTyping,
    ConsumpDailyTyping,
    ConsumpHourlyTyping,
    ConsumpMonthlyTyping,
    ContractTyping,
    CPCDataTyping,
    DPCDataTyping,
    DPCPeakListDataTyping,
    IDTokenTyping,
    InfoAccountTyping,
    ListAccountsContractsTyping,
    ListContractsTyping,
    OpenDataPeakEvent,
    OpenDataPeakEventOffer,
    OutageListTyping,
    PeriodDataTyping,
)
from hydroqc.utils import EST_TIMEZONE

# Host serving the Azure AD B2C pages. Its cookies hold the login transaction
# and the single sign-on state: they are the ones worth persisting to skip a
# later e-mail verification code.
B2C_HOST: str = yarl.URL(HOST_LOGIN).host or ""


class HydroClient:
    """HydroQc HTTP Client."""

    _cookie_jar: aiohttp.CookieJar | None

    def __init__(
        self,
        username: str,
        password: str,
        *,
        timeout: int = REQUESTS_TIMEOUT,
        verify_ssl: bool = True,
        session: aiohttp.ClientSession | None = None,
        log_level: str | None = "INFO",
        diag_folder: str | None = None,
        enable_cache: bool = True,
        family: socket.AddressFamily = socket.AF_UNSPEC,  # pylint: disable=no-member
        cookie_jar: aiohttp.CookieJar | None = None,
        refresh_token: str = "",
        refresh_token_expiry: datetime | None = None,
        auth_client: str = AUTH_CLIENT_WEB,
    ):
        """Initialize the client object.

        Args:
            username: Hydro-Québec account username.
            password: Hydro-Québec account password.
            timeout: HTTP request timeout in seconds. Defaults to REQUESTS_TIMEOUT.
            verify_ssl: Whether to verify SSL certificates. Defaults to True.
            session: Optional aiohttp ClientSession to reuse. Defaults to None.
            log_level: Logging level. Defaults to "INFO".
            diag_folder: Optional folder path for diagnostic output. Defaults to None.
            enable_cache: Whether to enable response caching. Defaults to True.
            family: Socket address family for outbound connections. Defaults to
                socket.AF_UNSPEC, which keeps the default dual-stack (IPv6/IPv4)
                behaviour. Use socket.AF_INET to force IPv4 on networks where the
                IPv6 path is broken.
            cookie_jar: Cookie jar to reuse. Passing a jar restored with
                ``import_session_state`` lets the portal skip a new login (and a
                new e-mail verification code) when its single sign-on cookie is
                still valid. Defaults to None.
            refresh_token: Refresh token from a previous session. When it is
                still valid no login (hence no e-mail verification code) is
                needed. Defaults to "".
            refresh_token_expiry: Expiry of ``refresh_token``. Defaults to None,
                which marks the session as already expired.
            auth_client: B2C client to log in with, ``"web"`` (the Espace
                client) or ``"mobile"`` (the Android app, whose refresh token
                lasts 90 days). Defaults to ``"web"``.

        Raises:
            ValueError: ``auth_client`` is not a known client.

        """
        if auth_client not in AUTH_CLIENTS:
            raise ValueError(f"Unknown auth_client: {auth_client!r}")
        self._auth = AUTH_CLIENTS[auth_client]
        self.username: str = username
        self.password: str = password
        self._timeout: int = timeout
        self._session: aiohttp.ClientSession | None = session
        self._verify_ssl: bool = verify_ssl
        self.enable_cache: bool = enable_cache
        self._family: socket.AddressFamily = family  # pylint: disable=no-member
        self._cookie_jar = cookie_jar

        # Azure AD B2C login state
        self._code_verifier: str = ""
        self._mfa_challenge: MFAChallenge | None = None
        self._send_mfa_code: bool = True
        self._pending_cookies: list[dict[str, Any]] = []

        # OAuth handling variables
        self._id_token: str = ""
        self.access_token: str = ""
        self.access_token_expiry: datetime = datetime.now()
        self.refresh_token: str = refresh_token
        self.refresh_token_expiry: datetime = refresh_token_expiry or datetime.now()
        self.web_session_expiry: datetime = datetime.now()

        self._selected_customer: str
        self._selected_contract: str
        self._diag_folder: str | None = diag_folder
        self._diag_id: int = 0
        self.guid: str = str(uuid.uuid1())
        self._logger: logging.Logger = get_logger("httpclient", log_level)
        self._logger.debug(
            "HydroQc initialized (caching: %s)",
            "enabled" if enable_cache else "disabled",
        )
        # Cache for blocking operations (initialized lazily)
        self._cached_version: str | None = None
        self.reset()

    def reset(self) -> None:
        """Reset collected data and temporary variable."""
        self._id_token = ""
        self.access_token = ""
        self.access_token_expiry = datetime.now()
        self.refresh_token = ""
        self.refresh_token_expiry = datetime.now()
        self.web_session_expiry = datetime.now()
        self._mfa_challenge = None
        # self._cookie_jar = aiohttp.CookieJar()
        self._selected_customer = ""
        self._selected_contract = ""

    async def _get_user_agent(self) -> str:
        """Get http client user_agent."""
        if self._cached_version is None:
            # Run blocking version lookup in executor to avoid blocking event loop
            loop = asyncio.get_event_loop()
            try:
                self._cached_version = await loop.run_in_executor(
                    None, version, "Hydro_Quebec_API_Wrapper"
                )
            except PackageNotFoundError:
                # package is not installed
                self._logger.warning(
                    "Python package `Hydro_Quebec_API_Wrapper` is not installed. "
                    "Install it using pip"
                )
                self._cached_version = "unkwown"
        os_name = platform.system()
        return (
            f"Hydroqc/{self._cached_version} "
            f"(dev@hydroqc.ca; https://github.com/hydroqc) {os_name}"
        )

    async def http_request(
        self,
        url: str,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        data: str | dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        verify_ssl: bool | None = None,
        status: int | None = 200,
        url_encoded: bool = False,
        allow_redirects: bool | None = None,
    ) -> aiohttp.ClientResponse:
        """Make an HTTP request.

        ``status`` is the HTTP status expected for the call. ``None`` disables
        the check, which the login flow needs: Azure AD B2C answers the same
        endpoint with a redirect, a page or an error depending on the step.
        """
        if params is None:
            params = {}
        if data is None:
            data = {}
        if headers is None:
            headers = {}
        headers["User-Agent"] = await self._get_user_agent()
        if verify_ssl is None:
            verify_ssl = self._verify_ssl

        # Query Diagnostic
        if self._diag_folder is not None:
            cleaned_url = url.replace("/", "_").replace(":", "").split("#", 1)[0]
            diag_file_path = os.path.join(
                self._diag_folder,
                f"hq_{self._diag_id:02}_q_{method}_{cleaned_url}.json"[:128],
            )
            with open(diag_file_path, "w", encoding="utf-8") as fhj:
                json.dump(
                    {
                        "url": url,
                        "params": params,
                        "data": data,
                        # TODO Remove tokens
                        "headers": headers,
                    },
                    fhj,
                    indent=2,
                )

        self._logger.debug("HTTP query %s to %s", url, method)

        url_url = yarl.URL(url, encoded=url_encoded)
        # Since aiohttp 3.13 redirect handling for some endpoints (notably Azure B2C flows)
        # appears to differ (initial authorize endpoint may now return a 302 first),
        # we allow callers to opt-in to following redirects. Default keeps previous behavior.
        if allow_redirects is None:
            allow_redirects = False
        raw_res: aiohttp.ClientResponse = await getattr(self._session, method)(
            url_url,
            params=params,
            data=data,
            allow_redirects=allow_redirects,
            ssl=verify_ssl,
            headers=headers,
        )

        # Result Diagnostic
        if self._diag_folder is not None:
            cleaned_url = url.replace("/", "_").replace(":", "").split("#", 1)[0]
            diag_file_path = os.path.join(
                self._diag_folder,
                f"hq_{self._diag_id:02}_r_{method}_{cleaned_url}.json"[:128],
            )
            diag_result = await raw_res.text()
            try:
                diag_result = json.dumps(json.loads(diag_result), indent=2)

            except json.JSONDecodeError:
                pass

            with open(diag_file_path, "w", encoding="utf-8") as fhj:
                fhj.write(diag_result)
            # Increment diag_id
            self._diag_id += 1

        if status is not None and raw_res.status != status:
            self._logger.exception("Exception in http_request")
            data = await raw_res.text()
            self._logger.debug(data)
            raise HydroQcHTTPError(
                f"Error Fetching {url} - {raw_res.status}", raw_res.status
            )

        return raw_res

    def _load_json(self, data: str | bytes) -> Any:
        """Safely read json, raise a HydroQcHTTPError when parsing fails."""
        try:
            return json.loads(data)
        except json.decoder.JSONDecodeError as exp:
            # Log the actual response for debugging
            data_preview = (
                (
                    data[:500]
                    if isinstance(data, str)
                    else data[:500].decode("utf-8", errors="replace")
                )
                if data
                else "(empty response)"
            )
            self._logger.error(
                "Failed to parse JSON response. Position: line %d column %d. "
                "Response preview (first 500 chars): %s",
                exp.lineno,
                exp.colno,
                data_preview,
            )
            raise HydroQcHTTPError(
                f"Bad JSON format at line {exp.lineno} column {exp.colno}: {exp.msg}"
            ) from exp

    def get_token_data(self) -> IDTokenTyping | None:
        """Decode id token data."""
        if not self._id_token:
            return None
        raw_token_data = self._id_token.split(".")[1]
        # In some cases padding get lost, adding it to avoid issues with base64 decode
        raw_token_data += "=" * ((4 - len(raw_token_data) % 4) % 4)
        token_data: IDTokenTyping = self._load_json(base64.b64decode(raw_token_data))
        return token_data

    async def _get_httpsession(self) -> None:
        """Set http session."""
        if self._cookie_jar is None:
            # aiohttp 3.12+ changed cookie quoting behavior for shared/default-scope cookies.
            # Azure B2C endpoints used in the login flow can be sensitive to quoted cookies.
            # Disable quoting to preserve legacy behavior and improve compatibility.
            # Also allow 'unsafe' cookies as some endpoints may not strictly adhere to RFCs.
            self._cookie_jar = aiohttp.CookieJar(quote_cookie=False, unsafe=True)
        if self._pending_cookies:
            restore_cookies(self._cookie_jar, self._pending_cookies)
            self._pending_cookies = []
        if self._session is None:
            self._logger.debug("Creating new http session")
            self._session = aiohttp.ClientSession(
                requote_redirect_url=False,
                cookie_jar=self._cookie_jar,
                connector=aiohttp.TCPConnector(family=self._family),
            )

    async def _get_customer_http_headers(
        self, applicant_id: str, customer_id: str, force_refresh: bool = False
    ) -> dict[str, str]:
        """Prepare http headers for customer url queries."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + await self._get_access_token(force_refresh),
            "NO_PARTENAIRE_DEMANDEUR": applicant_id,
            "NO_PARTENAIRE_TITULAIRE": customer_id,
            "DATE_DERNIERE_VISITE": datetime.now().strftime(
                "%Y-%m-%dT%H:%M:%S.000+0000"
            ),
            "GUID_SESSION": self.guid,
        }
        return headers

    async def close_session(self) -> None:
        """Close current session."""
        if self._session is not None:
            self._logger.debug("Closing http session")
            await self._session.close()
            self._session = None

    async def check_portal_status(self) -> bool:
        """Check if Hydro Quebec portal is UP."""
        # Get http session
        await self._get_httpsession()
        self._logger.info("Checking if the Hydro Quebec portal is UP")
        api_call_response = await self.http_request(
            IS_HYDRO_PORTAL_UP_URL,
            "get",
        )
        if api_call_response.status != 200:
            # TODO Add reason ?
            return False
        return True

    async def login(self, *, send_mfa_code: bool = True) -> LoginStatus:
        """Log in HydroQuebec website.

        Hydroquebec is using B2C solution for authentication. The policy may now
        stop after the password round trip and ask for a one-time code sent by
        e-mail: :attr:`LoginStatus.MFA_REQUIRED` is then returned and the code
        has to be submitted with :meth:`submit_mfa_code`. Set ``send_mfa_code``
        to ``False`` to stop at that step without asking the portal to send a
        code: only an interactive caller should let the user request one.
        """
        self._logger.info("Login using %s", self.username)
        self._send_mfa_code = send_mfa_code

        # Reset cache
        self.reset()

        # Get code verifier and challenge
        self._code_verifier, code_challenge = generate_pkce_pair()

        # Get http session
        await self._get_httpsession()
        # Ensure a clean slate for the B2C transaction cookies (csrf, TransId,
        # ...). Some Azure B2C flows reject reused cookies leading to 400 on
        # SelfAsserted. The single sign-on cookie is kept: it lets this login
        # skip the e-mail verification code, and the cookies of the other
        # Hydro-Quebec hosts belong to the portal session.
        if self._cookie_jar is not None:
            clear_transaction_cookies(self._cookie_jar, B2C_HOST)

        page = await self._fetch_authorize_page(
            prompt="login", code_challenge=code_challenge
        )
        if page is None:
            return LoginStatus.FAILED

        # Extract the CSRF token
        if (csrf_token := page.csrf) is None:
            self._logger.error(
                "Login error finding csrf token in the sign-in page "
                "(the page layout may have changed)"
            )
            return LoginStatus.FAILED

        # Extract the transId
        if (transid := page.trans_id) is None:
            self._logger.error(
                "Login error finding trans Id in the sign-in page "
                "(the page layout may have changed)"
            )
            return LoginStatus.FAILED

        return await self._post_credentials(csrf_token, transid)

    async def _fetch_authorize_page(
        self, *, prompt: str, code_challenge: str
    ) -> B2CPage | None:
        """Fetch the B2C authorize page and parse its embedded configuration.

        Azure AD B2C pages embed their own settings: ``SETTINGS`` holds the csrf
        token, the transaction id and the API segment, ``SA_FIELDS`` holds the
        input field names. Reading them instead of scraping ad-hoc strings keeps
        the client working when the portal reshuffles its pages, and exposes the
        real field names of the verification step.
        """
        headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        params = {
            "redirect_uri": self._auth.redirect_uri,
            "client_id": self._auth.client_id,
            "response_type": "code",
            "scope": self._auth.scope,
            "prompt": prompt,
            "ui_locales": "fr",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "mobile": "false",
        }

        # Allow redirects for the initial authorize call
        # (Azure B2C sometimes issues a 302 before the final HTML)
        res = await self.http_request(
            self._auth.authorize_url,
            "get",
            headers=headers,
            params=params,
            allow_redirects=True,
        )
        if res.status != 200:
            self._logger.error(
                "Login error: the sign-in page answered HTTP %s", res.status
            )
            return None
        return B2CPage.from_html(await res.text())

    async def _post_credentials(self, csrf_token: str, transid: str) -> LoginStatus:
        """Submit the e-mail/password pair to Azure AD B2C.

        POST https://connexion.solutions.hydroquebec.com/<tenant>/
        B2C_1A_PRD_signup_signin/SelfAsserted?tx=<transId>&p=<policy>
        with the URL encoded form data ``request_type=RESPONSE``,
        ``signInName`` and ``password``, the ``x-csrf-token`` header set to the
        value extracted from the sign-in page and the session cookies.
        """
        url = self._auth.auth_url + "?tx=" + transid + "&p=" + self._auth.policy
        headers = {
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "accept": "application/json, text/javascript, */*; q=0.01",
            "x-csrf-token": csrf_token,
        }

        data = {
            "request_type": "RESPONSE",
            "signInName": self.username,
            "password": self.password,
        }
        res = await self.http_request(url, "post", headers=headers, data=data)
        res_json = await self._json_or_empty(res)

        if str(res_json.get("status")) != "200":
            self._logger.error("Login error - %s", res_json.get("message", "unknown"))
            return LoginStatus.FAILED

        return await self._advance_login(csrf_token, transid)

    async def _advance_login(
        self,
        csrf_token: str,
        transid: str,
        *,
        api: str = "CombinedSigninAndSignup",
        page: B2CPage | None = None,
    ) -> LoginStatus:
        """Ask Azure AD B2C for the next step of the login flow.

        GET https://connexion.solutions.hydroquebec.com/<tenant>/
        B2C_1A_PRD_signup_signin/api/<api>/confirmed
        ?rememberMe=false&csrf_token=<csrf>&tx=<transId>&p=<policy>

        A single factor login answers ``302`` with the ``code`` attribute of the
        callback in the ``Location`` header. A policy asking for a second factor
        answers ``302`` to its verification page (or ``200`` with that page),
        which is reported as :attr:`LoginStatus.MFA_REQUIRED`. ``api`` is
        ``SelfAsserted`` when the call follows the verification page.
        """
        url = (
            self._auth.auth_url.rsplit("/", 1)[0]
            + "/api/"
            + api
            + "/confirmed?csrf_token="
            + csrf_token
            + "&tx="
            + transid
            + "&p="
            + self._auth.policy
        )
        if api == "CombinedSigninAndSignup":
            url += "&rememberMe=false"
        headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

        # The status is not enforced here: depending on its configuration the
        # policy answers a redirect to the callback, a redirect to a
        # verification page, or the verification page itself.
        res = await self.http_request(url, "get", headers=headers, status=None)

        if res.status in {301, 302, 303, 307, 308}:
            location = res.headers.get("Location", "")
            if code := extract_auth_code(location):
                # Extract the code attribute from the location header, which
                # has the following format:
                # msauth.com.hydroquebec.infos-pannes://
                # auth/?state=%257B...&code=(code value)
                return await self._exchange_code_for_token(code)

            if (challenge := MFAChallenge.from_redirect(location)) is None:
                self._logger.error(
                    "Login error: unexpected redirect after the password step "
                    "(%s). The login flow of Hydro-Quebec may have changed.",
                    location,
                )
                return LoginStatus.FAILED
            return await self._open_mfa_challenge(challenge)

        page = page or B2CPage.from_html(await res.text())
        if (
            challenge := MFAChallenge.from_page(str(res.url), page, email=self.username)
        ) is None:
            self._logger.error(
                "Login error: unexpected answer after the password step (HTTP "
                "%s), neither an authorization code nor a verification form was "
                "found. The login flow of Hydro-Quebec may have changed.",
                res.status,
            )
            return LoginStatus.FAILED
        return await self._open_mfa_challenge(challenge, page)

    async def _open_mfa_challenge(
        self, challenge: MFAChallenge, page: B2CPage | None = None
    ) -> LoginStatus:
        """Record the verification step and ask for a one-time code."""
        challenge.email = challenge.email or self.username

        # A redirect only carries the URL of the verification page: load it to
        # get its csrf token, transaction id, display control and exact input
        # names. When the caller already holds the page (the policy answered it
        # directly), there is nothing to fetch.
        if page is not None:
            if page.settings or page.fields:
                challenge = challenge.with_page(page)
        elif challenge.page_url.startswith("http"):
            try:
                res = await self.http_request(
                    challenge.page_url,
                    "get",
                    headers={
                        "accept": (
                            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                        )
                    },
                    status=None,
                    allow_redirects=True,
                )
            except HydroQcHTTPError:
                self._logger.debug(
                    "Could not load the verification page %s",
                    challenge.page_url,
                    exc_info=True,
                )
            else:
                page = B2CPage.from_html(await res.text())
                if page.settings or page.fields:
                    challenge = challenge.with_page(page, str(res.url))

        if challenge.kind is MFAKind.UNKNOWN and (page is None or not page.fields):
            # An unexpected step that does not even look like a form: report a
            # failure instead of asking the user for a code that was never sent.
            self._logger.error(
                "Login error: unexpected step after the password step (%s). "
                "The login flow of Hydro-Quebec may have changed.",
                challenge.page_url,
            )
            return LoginStatus.FAILED

        self._mfa_challenge = challenge
        self._logger.warning(
            "Login requires an e-mail verification code for %s (%s). The login "
            "is not complete until the code is submitted.",
            self.username,
            challenge.description,
        )
        if self._send_mfa_code:
            await self.request_mfa_code()
        return LoginStatus.MFA_REQUIRED

    async def request_mfa_code(self) -> bool:
        """Ask Hydro-Quebec to e-mail a one-time code.

        Also used to resend a code that expired or never arrived. Returns
        ``False`` when no request was accepted, which does not mean no code was
        sent: some policies send it on their own.
        """
        if (challenge := self._mfa_challenge) is None:
            self._logger.error(
                "No verification step in progress, cannot request a code"
            )
            return False

        for request in send_code_requests(challenge):
            try:
                res = await self.http_request(
                    request.url,
                    "post",
                    headers=request.headers,
                    data=request.data,
                    status=None,
                )
            except HydroQcHTTPError:
                self._logger.debug(
                    "Verification code request failed (%s)",
                    request.label,
                    exc_info=True,
                )
                continue

            payload = await self._json_or_empty(res)
            if call_succeeded(payload):
                self._logger.info("Verification code requested (%s)", request.label)
                return True
            self._logger.debug(
                "Verification code request rejected (%s): %s",
                request.label,
                payload_message(payload) or res.status,
            )

        self._logger.warning(
            "Could not request an e-mail verification code automatically. "
            "The portal may already have sent one."
        )
        return False

    async def submit_mfa_code(self, code: str) -> LoginStatus:
        """Submit the one-time code received by e-mail and finish the login.

        Raises:
            HydroQcMFAExpiredError: no verification step is in progress, or it
                expired; the login has to be restarted.
            HydroQcMFAInvalidCodeError: Hydro-Quebec refused the code. The
                verification step is still valid, another code can be tried.

        """
        if (challenge := self._mfa_challenge) is None:
            raise HydroQcMFAExpiredError(
                "No e-mail verification step in progress, the login has to be restarted"
            )

        if not (code := code.strip()):
            raise HydroQcMFAInvalidCodeError("Empty verification code")

        last_message = ""
        expired = False
        for request in verify_code_requests(challenge, code):
            try:
                res = await self.http_request(
                    request.url,
                    "post",
                    headers=request.headers,
                    data=request.data,
                    status=None,
                )
            except HydroQcHTTPError as err:
                last_message = str(err)
                expired = expired or err.status_code in {400, 404, 410}
                continue

            payload = await self._json_or_empty(res)
            if call_succeeded(payload):
                self._logger.info("Verification code accepted (%s)", request.label)
                return await self._complete_login_after_mfa(code)

            last_message = payload_message(payload) or last_message
            expired = expired or looks_expired("", payload)
            # Stop at the first answer the policy actually understood: trying
            # every payload shape would burn the attempts allowed on the code.
            if {"status", "result", "message"} & set(payload):
                break

        if expired:
            self._logger.error("Verification step expired: %s", last_message)
            self._mfa_challenge = None
            raise HydroQcMFAExpiredError(
                "The e-mail verification step expired, the login has to be restarted"
                + (f" ({last_message})" if last_message else "")
            )

        self._logger.error("Verification code refused: %s", last_message)
        raise HydroQcMFAInvalidCodeError(last_message or "Verification code refused")

    async def _complete_login_after_mfa(self, code: str) -> LoginStatus:
        """Finish the login once the verification code was accepted.

        The browser submits the verification page as a regular ``SelfAsserted``
        form (``request_type=RESPONSE``) and then follows its ``confirmed`` URL.
        Doing the same gives B2C the chance to hand out the authorization code
        that the token endpoint expects.
        """
        if (challenge := self._mfa_challenge) is None:
            raise HydroQcMFAExpiredError(
                "No e-mail verification step in progress, the login has to be restarted"
            )
        if challenge.csrf is None or challenge.trans_id is None:
            self._logger.error(
                "Login error: the verification page did not expose a csrf token "
                "or a transaction id, cannot complete the login"
            )
            self._mfa_challenge = None
            return LoginStatus.FAILED

        request = completion_request(challenge, code)
        try:
            res = await self.http_request(
                request.url,
                "post",
                headers=request.headers,
                data=request.data,
                status=None,
            )
        except HydroQcHTTPError:
            # The verification itself already succeeded: keep going, the
            # confirmed URL below is what actually advances the transaction.
            self._logger.debug(
                "Could not submit the verification page (%s)",
                request.label,
                exc_info=True,
            )
        else:
            payload = await self._json_or_empty(res)
            if payload and not call_succeeded(payload):
                self._logger.debug(
                    "Verification page submission refused (%s): %s",
                    request.label,
                    payload_message(payload) or res.status,
                )

        status = await self._advance_login(
            challenge.csrf, challenge.trans_id, api=challenge.api or "SelfAsserted"
        )
        if status is LoginStatus.SUCCESS:
            self._mfa_challenge = None
        return status

    async def try_silent_login(self) -> LoginStatus:
        """Replay stored cookies without interacting with the user.

        Azure AD B2C keeps a single sign-on cookie after a successful login and
        verification. While that cookie is valid the portal issues a new
        authorization code without a password nor a new code. Always safe to
        call: any doubt reports :attr:`LoginStatus.FAILED` and the caller falls
        back to :meth:`login`.
        """
        try:
            return await self._silent_login()
        except HydroQcHTTPError as err:
            self._logger.debug("Silent login not possible: %s", err)
            return LoginStatus.FAILED

    async def _silent_login(self) -> LoginStatus:
        """Run the ``prompt=none`` authorization request with the stored cookies."""
        await self._get_httpsession()
        self._code_verifier, code_challenge = generate_pkce_pair()

        headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        params = {
            "redirect_uri": self._auth.redirect_uri,
            "client_id": self._auth.client_id,
            "response_type": "code",
            "scope": self._auth.scope,
            "prompt": "none",
            "ui_locales": "fr",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "mobile": "false",
        }
        res = await self.http_request(
            self._auth.authorize_url,
            "get",
            headers=headers,
            params=params,
            allow_redirects=False,
            status=None,
        )
        location = res.headers.get("Location", "")
        if code := extract_auth_code(location):
            return await self._exchange_code_for_token(code)
        self._logger.debug(
            "Silent login not possible (%s)",
            extract_redirect_error(location) or f"HTTP {res.status}",
        )
        return LoginStatus.FAILED

    async def _exchange_code_for_token(self, code: str) -> LoginStatus:
        """Exchange an authorization code for the OAuth tokens."""
        headers = {"content-type": "application/x-www-form-urlencoded", "accept": "*/*"}
        data = {
            "grant_type": "authorization_code",
            "client_id": self._auth.client_id,
            "redirect_uri": self._auth.redirect_uri,
            "code": code,
            "code_verifier": self._code_verifier,
        }

        res = await self.http_request(
            self._auth.token_url, "post", headers=headers, data=data
        )
        payload = await self._json_or_empty(res)
        if not payload.get("access_token"):
            self._logger.error(
                "Login error: the token endpoint did not return an access token (%s)",
                payload_message(payload) or f"HTTP {res.status}",
            )
            return LoginStatus.FAILED

        self._store_tokens(payload)
        self._mfa_challenge = None
        self._logger.info("Login completed using %s", self.username)
        return LoginStatus.SUCCESS

    @staticmethod
    async def _json_or_empty(res: aiohttp.ClientResponse) -> dict[str, Any]:
        """Decode a B2C JSON answer, tolerating HTML or empty bodies."""
        try:
            payload = await res.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _store_tokens(self, payload: Mapping[str, Any]) -> None:
        """Store the OAuth tokens returned by B2C."""
        self._id_token = str(payload.get("id_token") or "")
        self.access_token = str(payload.get("access_token") or "")
        expires_in = int(str(payload.get("expires_in") or 0))
        self.access_token_expiry = datetime.now() + timedelta(
            seconds=max(expires_in - AZB2C_TIMEOUT_SKEW_SECS, 0)
        )
        self.refresh_token = str(payload.get("refresh_token") or self.refresh_token)
        if refresh_expires_in := int(str(payload.get("refresh_token_expires_in") or 0)):
            self.refresh_token_expiry = datetime.now() + timedelta(
                seconds=max(refresh_expires_in - AZB2C_TIMEOUT_SKEW_SECS, 0)
            )
        else:
            # B2C may omit the refresh token lifetime: keep the access token
            # lifetime as a conservative lower bound.
            self.refresh_token_expiry = self.access_token_expiry

    def export_session_state(self) -> dict[str, Any]:
        """Return the reusable part of the session.

        The state holds the OAuth tokens and the cookies of the login host.
        Persisting it lets a later process skip the login, and therefore skip a
        new e-mail verification code, while the refresh token or the single
        sign-on cookie is still valid.
        """
        return {
            "client_id": self._auth.client_id,
            "refresh_token": self.refresh_token,
            "refresh_token_expiry": self.refresh_token_expiry.isoformat(),
            "access_token": self.access_token,
            "access_token_expiry": self.access_token_expiry.isoformat(),
            "cookies": (
                serialize_cookies(self._cookie_jar)
                if self._cookie_jar is not None
                else []
            ),
        }

    def import_session_state(self, state: Mapping[str, Any]) -> None:
        """Restore a session produced by :meth:`export_session_state`.

        The tokens are restored only when the state records the current client
        id. A state with no ``client_id`` predates this field and comes from
        the web client. Tokens of another client cannot be refreshed here, so
        they are dropped and the caller gets a plain interactive login. The
        login host cookies are restored either way, as they belong to the host
        and not to one client.
        """
        if state.get("client_id", AZB2C_CLIENT_ID_WEB) != self._auth.client_id:
            self._logger.info(
                "Ignoring the tokens of a stored session issued for another "
                "B2C client (an interactive login is required)"
            )
            self._restore_session_cookies(state)
            return

        refresh_token = state.get("refresh_token")
        if isinstance(refresh_token, str):
            self.refresh_token = refresh_token

        refresh_expiry = state.get("refresh_token_expiry")
        if isinstance(refresh_expiry, str):
            self.refresh_token_expiry = self._parse_datetime(refresh_expiry)

        access_token = state.get("access_token")
        if isinstance(access_token, str):
            self.access_token = access_token

        access_expiry = state.get("access_token_expiry")
        if isinstance(access_expiry, str):
            self.access_token_expiry = self._parse_datetime(access_expiry)

        self._restore_session_cookies(state)

    def _restore_session_cookies(self, state: Mapping[str, Any]) -> None:
        """Restore the login host cookies of a stored session."""
        cookies = state.get("cookies")
        if isinstance(cookies, list):
            if self._cookie_jar is None:
                # CookieJar captures the running event loop when created: keep
                # the cookies until the HTTP session is actually opened.
                self._pending_cookies = list(cookies)
            else:
                restore_cookies(self._cookie_jar, cookies)

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        """Parse an ISO datetime, falling back to the current time."""
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.now()

    @property
    def mfa_required(self) -> bool:
        """Return True while a verification code has to be submitted."""
        return self._mfa_challenge is not None

    async def _refresh_token(self) -> bool:
        """Refresh current session."""
        self._logger.debug("Refreshing access token")

        url = self._auth.token_url
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json, text/plain, */*",
        }
        data = {
            "grant_type": "refresh_token",
            "scope": self._auth.scope,
            "client_id": self._auth.client_id,
            "refresh_token": self.refresh_token,
        }

        res = await self.http_request(url, "post", headers=headers, data=data)
        res_json = await self._json_or_empty(res)

        if not res_json.get("access_token"):
            self._logger.error(
                "Token refresh failed (%s)",
                payload_message(res_json) or f"HTTP {res.status}",
            )
            return False

        self._store_tokens(res_json)

        return True

    def is_session_expired(self) -> bool:
        """Check if the session is expired."""
        return self.refresh_token_expiry < datetime.now()

    async def _get_access_token(self, force_refresh: bool = True) -> str:
        """Get an access token."""
        if self.is_session_expired():
            await self.close_session()
            # The single sign-on cookie may outlive the refresh token lifetime:
            # replay it before asking for the password again, it also skips a
            # new e-mail verification code.
            if await self.try_silent_login() is LoginStatus.SUCCESS:
                return self.access_token
            status = await self.login()
            if status is LoginStatus.MFA_REQUIRED:
                raise HydroQcMFARequiredError(
                    "Hydro-Quebec now asks for an e-mail verification code: an "
                    "interactive login is required, a background refresh cannot "
                    "provide it"
                )
            if status is not LoginStatus.SUCCESS:
                raise HydroQcError("Hydro-Quebec login failed")
        elif force_refresh or self.access_token_expiry < datetime.now():
            await self._refresh_token()

        return self.access_token

    @property
    def selected_customer(self) -> str:
        """Return the current selected customer."""
        return self._selected_customer

    @property
    def selected_contract(self) -> str:
        """Return the current selected contract."""
        return self._selected_contract

    async def _create_web_session(
        self, applicant_id: str, customer_id: str, contract_id: str
    ) -> None:
        """Create a web session from a OAuth access_token."""
        self._logger.debug("Creating new web session %s", contract_id)

        # Clearing the portal session cookies. The Azure AD B2C cookies of the
        # login host are kept: they carry the single sign-on / trusted device
        # state, which lets a later login skip the e-mail verification code.
        if self._cookie_jar is not None:
            for url in (HOST_SESSION, HOST_SERVICES):
                if host := yarl.URL(url).host:
                    clear_cookies_for_host(self._cookie_jar, host)

        # Forcing a refresh of the access token.
        # The new session that is created with SESSION_URL (/portal resources)
        # seemed to break (calls returning HTTP 400) in alignment with
        # the access_token expiry. With a new access_token, we can assume the web
        # session invalid at the same time as the access token that was used to create it.
        headers = await self._get_customer_http_headers(
            applicant_id, customer_id, force_refresh=True
        )

        params = {"mode": "web"}
        await self.http_request(SESSION_URL, "get", params=params, headers=headers)
        self.web_session_expiry = self.access_token_expiry

    async def _select_contract(
        self, applicant_id: str, customer_id: str, contract_id: str
    ) -> None:
        """Create a web session from a OAuth access_token and select a customer on the Home page.

        Equivalent to click on the customer box on the Home page.
        """
        if self.web_session_expiry > datetime.now():
            self._logger.debug("Not refreshing web session")
            return

        await self._create_web_session(applicant_id, customer_id, contract_id)

        self._logger.info("Selecting contract %s", contract_id)

        params = {"noContrat": contract_id}
        await self.http_request(PORTRAIT_URL, "get", params=params)

        self._selected_contract = contract_id
        self._selected_customer = customer_id
        self._logger.info("Contract %s selected", contract_id)

    @CCached(ttl=21600)
    async def get_user_info(self) -> list[AccountTyping]:
        """Fetch user ids and customer ids.

        .. todo::
            Handle json load error
        """
        self._logger.info("Fetching webuser info")
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + await self._get_access_token(),
        }
        res = await self.http_request(
            self._auth.api_url(RELATION_URL), "get", headers=headers
        )
        # TODO http errors
        data: list[AccountTyping] = await res.json()
        return data

    @CCached(ttl=21600)
    async def get_customer_info(
        self, applicant_id: str, customer_id: str
    ) -> InfoAccountTyping:
        """Fetch customer data."""
        self._logger.info("Fetching customer info: c-%s", customer_id)
        headers = await self._get_customer_http_headers(applicant_id, customer_id)
        params = {"withCredentials": "true"}
        api_call_response = await self.http_request(
            self._auth.api_url(CUSTOMER_INFO_URL),
            "get",
            headers=headers,
            params=params,
        )
        data: InfoAccountTyping = await api_call_response.json()
        return data

    @CCached(ttl=21600)
    async def get_periods_info(
        self, applicant_id: str, customer_id: str, contract_id: str
    ) -> list[PeriodDataTyping]:
        """Fetch all periods info."""
        await self._select_contract(applicant_id, customer_id, contract_id)
        headers = await self._get_customer_http_headers(applicant_id, customer_id)
        res = await self.http_request(PERIOD_DATA_URL, "get", headers=headers)
        data = json.loads(await res.text())

        periods: list[PeriodDataTyping] = [
            p for p in data["results"] if p["numeroContrat"] == contract_id
        ]
        if not periods:
            raise HydroQcError(f"No period found for contract {contract_id}")
        return periods

    @CCached(ttl=21600)
    async def get_account_info(
        self, applicant_id: str, customer_id: str, account_id: str
    ) -> ListAccountsContractsTyping:
        """Fetch account data."""
        self._logger.info("Fetching account info: c-%s - a-%s", customer_id, account_id)
        data = await self.get_customer_info(applicant_id, customer_id)
        accounts = {
            a["noCompteContrat"]: a
            for a in data["infoCockpitPourPartenaireModel"]["listeComptesContrats"]
        }
        if account_id not in accounts:
            raise HydroQcError("Account not found")
        return accounts[account_id]

    @CCached(ttl=21600)
    async def list_account_contract(
        self, applicant_id: str, customer_id: str
    ) -> AccountContractSummaryTyping:
        """Get all  account_contract linked to a customer."""
        headers = await self._get_customer_http_headers(applicant_id, customer_id)
        res = await self.http_request(
            self._auth.api_url(CONTRACT_SUMMARY_URL), "get", headers=headers
        )
        data: AccountContractSummaryTyping = await res.json()
        return data

    @CCached(ttl=21600)
    async def get_contract_info(
        self, applicant_id: str, customer_id: str, account_id: str, contract_id: str
    ) -> ContractTyping:
        """Fetch contract data."""
        self._logger.info(
            "Fetching contract info: c-%s - a-%s - c-%s",
            applicant_id,
            customer_id,
            contract_id,
        )
        headers = await self._get_customer_http_headers(applicant_id, customer_id)
        post_data = {
            "listeServices": ["PC"],
            "comptesContrats": [
                {
                    "listeNoContrat": [contract_id],
                    "noCompteContrat": account_id,
                    "titulaire": customer_id,
                }
            ],
        }
        post_data_str = json_dumps(post_data)
        res = await self.http_request(
            self._auth.api_url(CONTRACT_LIST_URL),
            "post",
            headers=headers,
            data=post_data_str,
        )
        data: ListContractsTyping = await res.json()
        # We ask only one contract, so we should have only one
        if not data["listeContrats"]:
            raise HydroQcError("Contract not found")
        return data["listeContrats"][0]

    @CCached(ttl=3600)
    async def get_cpc_credit(
        self, applicant_id: str, customer_id: str, contract_id: str
    ) -> CPCDataTyping:
        """Return information about CPC option (winter credit).

        :return: raw JSON from hydro QC API
        """
        self._logger.info("Fetching cpc: c-%s c-%s", customer_id, contract_id)
        headers = await self._get_customer_http_headers(applicant_id, customer_id)
        params = {"noContrat": contract_id}
        api_call_response = await self.http_request(
            self._auth.api_url(GET_CPC_API_URL),
            "get",
            headers=headers,
            params=params,
        )
        data: CPCDataTyping = await api_call_response.json()
        return data

    @CCached(ttl=900)
    async def get_outages(
        self, consumption_location_id: str
    ) -> OutageListTyping | None:
        """Return outages for a given consumption location id."""
        response = await self.http_request(OUTAGES + consumption_location_id, "get")
        res: list[OutageListTyping] = await response.json()
        return res[0] if res else None

    async def get_today_hourly_consumption(
        self, applicant_id: str, customer_id: str, contract_id: str
    ) -> ConsumpHourlyTyping:
        """Return latest consumption info (about 2h delay it seems).

        :return: raw JSON from hydro QC API for current day (not officially supported, data delayed)
        """
        today = datetime.today().astimezone(EST_TIMEZONE).date()
        day_m2 = today - timedelta(days=2)
        day_m1 = today - timedelta(days=1)
        # We need to call a valid date first as theoretically today is invalid
        # and the api will not respond if called directly
        # We also get 2 days ago to not crash right after midnight
        await self.get_hourly_consumption(
            applicant_id, customer_id, contract_id, day_m2
        )
        await self.get_hourly_consumption(
            applicant_id, customer_id, contract_id, day_m1
        )
        res: ConsumpHourlyTyping = await self.get_hourly_consumption(
            applicant_id, customer_id, contract_id, today
        )
        return res

    @CCached(ttl=3600)
    async def get_hourly_consumption(
        self, applicant_id: str, customer_id: str, contract_id: str, date_wanted: date
    ) -> ConsumpHourlyTyping:
        """Return hourly consumption for a specific day.

        .. todo::
            Use decorator for self._select_contract

        :param: date: YYYY-MM-DD string to pass to API

        :return: raw JSON from hydro QC API
        """
        self._logger.info(
            "Fetching hourly consumption: c-%s c-%s", customer_id, contract_id
        )
        # TODO use decorator
        await self._select_contract(applicant_id, customer_id, contract_id)
        params = {"date": date_wanted.isoformat()}
        api_call_response = await self.http_request(
            HOURLY_CONSUMPTION_API_URL, "get", params=params
        )
        # We can not use res.json() because the response header are not application/json
        response_text = await api_call_response.text()

        # Check if response is empty or whitespace only
        if not response_text or not response_text.strip():
            self._logger.warning(
                "Empty response received for hourly consumption on %s. "
                "Data may not be available yet from Hydro-Québec.",
                date_wanted.isoformat(),
            )
            raise HydroQcHTTPError(
                f"No data available for date {date_wanted.isoformat()}"
            )

        data: ConsumpHourlyTyping = self._load_json(response_text)
        return data

    @CCached(ttl=43200)
    async def get_daily_consumption(
        self,
        applicant_id: str,
        customer_id: str,
        *,
        contract_id: str,
        start_date: date,
        end_date: date,
    ) -> ConsumpDailyTyping:
        """Return daily consumption for a specific day.

        .. todo::
            Use decorator for self._select_contract

        :param: start_date: date to pass to API
        :param: end_date: date to pass to API

        :return: raw JSON from hydro QC API
        """
        self._logger.info(
            "Fetching daily consumption: c-%s c-%s", customer_id, contract_id
        )
        # TODO use decorator
        await self._select_contract(applicant_id, customer_id, contract_id)
        params = {"dateDebut": start_date.isoformat(), "dateFin": end_date.isoformat()}
        api_call_response = await self.http_request(
            DAILY_CONSUMPTION_API_URL,
            "get",
            params=params,
        )
        # We can not use res.json() because the response header are not application/json
        data: ConsumpDailyTyping = self._load_json(await api_call_response.text())
        return data

    @CCached(ttl=21600)
    async def get_monthly_consumption(
        self, applicant_id: str, customer_id: str, contract_id: str
    ) -> ConsumpMonthlyTyping:
        """Fetch data of the current year.

        .. todo::
            Use decorator for self._select_contract

        API URL: https://cl-ec-spring.hydroquebec.com/portail/fr/group/clientele/
        portrait-de-consommation/resourceObtenirDonneesConsommationMensuelles
        """
        self._logger.info(
            "Fetching monthly consumption: c-%s c-%s", customer_id, contract_id
        )
        # TODO use decorator
        await self._select_contract(applicant_id, customer_id, contract_id)
        headers = {"Content-Type": "application/json"}
        api_call_response = await self.http_request(
            MONTHLY_DATA_URL, "get", headers=headers
        )
        # We can not use res.json() because the response header are not application/json
        data: ConsumpMonthlyTyping = self._load_json(await api_call_response.text())
        return data

    @CCached(ttl=21600)
    async def get_annual_consumption(
        self, applicant_id: str, customer_id: str, contract_id: str
    ) -> ConsumpAnnualTyping:
        """Fetch data of the current year.

        API URL: https://cl-ec-spring.hydroquebec.com/portail/fr/group/clientele/
        portrait-de-consommation/resourceObtenirDonneesConsommationAnnuelles
        """
        self._logger.info(
            "Fetching annual consumption: c-%s c-%s", customer_id, contract_id
        )
        await self._select_contract(applicant_id, customer_id, contract_id)
        headers = {"Content-Type": "application/json"}
        api_call_response = await self.http_request(
            ANNUAL_DATA_URL, "get", headers=headers
        )
        # We can not use res.json() because the response header are not application/json
        data: ConsumpAnnualTyping = self._load_json(await api_call_response.text())
        return data

    @CCached(ttl=21600)
    async def get_dpc_data(
        self, applicant_id: str, customer_id: str, contract_id: str
    ) -> DPCDataTyping:
        """Fetch FlexD data of the current year.

        API URL: https://services-cl.solutions.hydroquebec.com/wsapi/web/prive/api/
        v3_0/tarificationDynamique/tarifPointeCritique

        """
        await self._select_contract(applicant_id, customer_id, contract_id)
        headers = await self._get_customer_http_headers(applicant_id, customer_id)
        params = {"noContrat": contract_id}
        api_call_response = await self.http_request(
            self._auth.api_url(FLEXD_DATA_URL), "get", params=params, headers=headers
        )
        # We can not use res.json() because the response header are not application/json
        data: DPCDataTyping = self._load_json(await api_call_response.text())
        return data

    @CCached(ttl=3600)
    async def get_dpc_peak_data(
        self, applicant_id: str, customer_id: str, contract_id: str
    ) -> DPCPeakListDataTyping:
        """Fetch FlexD data of the current year.

        API URL: https://cl-ec-spring.hydroquebec.com/portail/fr/group/clientele/
        portrait-de-consommation/resourceObtenirDonneesMoisHiverFlex

        """
        await self._select_contract(applicant_id, customer_id, contract_id)
        headers = await self._get_customer_http_headers(applicant_id, customer_id)
        params = {"noContrat": contract_id}
        api_call_response = await self.http_request(
            FLEXD_PEAK_URL, "get", params=params, headers=headers
        )
        # We can not use res.json() because the response header are not application/json
        data: DPCPeakListDataTyping = self._load_json(await api_call_response.text())
        return data

    async def get_consumption_csv(
        self,
        applicant_id: str,
        customer_id: str,
        *,
        contract_id: str,
        start_date: date,
        end_date: date,
        option: str,
        raw_output: bool = False,
    ) -> Iterator[list[str | int | float]] | StringIO:
        """Download one of the history CSV on the portrait-de-consommation page.

        `option` should be one of 'puissance-jour', 'energie-heure',
                                  'puissance-min', 'energie-jour',
        """
        await self._select_contract(applicant_id, customer_id, contract_id)
        data = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "option": option,
        }
        res = await self.http_request(
            CONSO_CSV_URL,
            "post",
            data=data,
        )
        # TODO: improve this, with something like asyncsv
        # instead of loading all the csv in memory
        content = StringIO(await res.text())
        if raw_output:
            return content
        data_csv: Iterator[Any] = csv.reader(content, delimiter=";")
        return data_csv

    async def get_consumption_overview_csv(
        self,
        applicant_id: str,
        customer_id: str,
        contract_id: str,
        raw_output: bool = False,
    ) -> Iterator[list[str | int | float]] | StringIO:
        """Download the overview by consumption period CSV on the portrait-de-consommation page."""
        await self._select_contract(applicant_id, customer_id, contract_id)
        res = await self.http_request(CONSO_OVERVIEW_CSV_URL, "get")
        # TODO: improve this, with something like asyncsv
        # instead of loading all the csv in memory
        content = StringIO(await res.text())
        if raw_output:
            return content
        data_csv: Iterator[Any] = csv.reader(content, delimiter=";")
        return data_csv

    @CCached(ttl=300)
    async def get_open_data_peaks(
        self,
        offer: OpenDataPeakEventOffer | None = None,
    ) -> list[OpenDataPeakEvent]:
        """Get the list of peak event from open data url."""
        await self._get_httpsession()
        res = await self.http_request(OPEN_DATA_PEAK_URL, "get")
        content = await res.text()
        try:
            raw_res_json = json.loads(content)
        except json.JSONDecodeError:
            # Try to clean up json removing comments
            cleaned_json = ""
            res_text = await res.text()
            for line in res_text.splitlines():
                if not line.startswith("/") and not line.startswith("#"):
                    cleaned_json += line
            raw_res_json = json.loads(cleaned_json)
        res_json = cast(list[OpenDataPeakEvent], raw_res_json.get("evenements", []))
        if not offer:
            return res_json
        return [e for e in res_json if e["offre"] == offer]
