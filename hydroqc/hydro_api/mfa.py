"""Azure AD B2C login helpers.

Hydro-Québec protects its customer portal with an Azure AD B2C user flow. The
flow historically stopped after the password round trip, but the policy now
branches to a second step asking for a one-time code sent by e-mail.

Nothing here hardcodes the technical profiles used by Hydro-Québec: B2C pages
embed their own configuration (``SETTINGS`` and ``SA_FIELDS`` JSON blobs), so
the API segment, the transaction id and the real input field names are read
from the page the policy returns. This keeps the client able to recognise and
answer the MFA step whatever shape the portal settles on.

The verification step is a B2C *display control* (``VerificationControl``): the
page embeds the control's nested fields, the values B2C expects for each action
(``INPUT_FIELDS``) and the address to verify. Confirming the code is two calls
to ``{tenant}/{policy}/{api}/DisplayControlAction/vbeta/{control}/{action}``
followed by a regular ``SelfAsserted`` form submission, exactly what the
browser does.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from http.cookies import SimpleCookie
from typing import Any, cast
from urllib.parse import quote, urlencode

import aiohttp
import yarl

# Field id fragments used to recognise the one-time code and e-mail inputs of a
# B2C page. Matching is done on the ids embedded in ``SA_FIELDS``.
CODE_FIELD_HINTS = ("verificationcode", "verification_code", "otp", "passcode", "code")
EMAIL_FIELD_HINTS = ("email", "courriel", "strongauthenticationemailaddress", "mail")

# Name fragments of the B2C cookies that carry the single sign-on / trusted
# device state: they let a later login skip the e-mail code and must survive a
# fresh interactive login.
SSO_COOKIE_HINTS = ("sso", "persistent", "trusted", "device")

# Values an Azure AD B2C endpoint uses to report a failed answer.
FAILURE_RESULTS = frozenset(
    {"error", "failed", "failure", "invalid", "invalid_code", "expired", "timeout"}
)

# Markers found in the error message of an Azure AD B2C endpoint when the
# transaction (``tx``) is no longer usable. When this happens the whole login
# flow has to be restarted, the one-time code cannot be submitted anymore.
EXPIRED_MESSAGE_MARKERS = (
    "expir",
    "invalid transaction",
    "transaction invalide",
    "session has timed out",
    "timed out",
    "not found",
)

# Name of the B2C action that ends a form based verification page.
RESPONSE_REQUEST = "RESPONSE"


class MFAKind(StrEnum):
    """Kind of Azure AD B2C step detected after the password round trip."""

    DISPLAY_CONTROL = "display_control"
    SELF_ASSERTED = "self_asserted"
    UNKNOWN = "unknown"


def extract_json_assignment(html: str, name: str) -> dict[str, Any] | None:
    """Extract a ``NAME = {...}`` JavaScript object embedded in a B2C page."""
    if (match := re.search(rf"\b{re.escape(name)}\s*=\s*\{{", html)) is None:
        return None

    start = match.end() - 1
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(html)):
        char = html[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                try:
                    parsed = json.loads(html[start:end])
                except json.JSONDecodeError:
                    return None
                if isinstance(parsed, dict):
                    return cast("dict[str, Any]", parsed)
                return None
    return None


def extract_auth_code(location: str) -> str | None:
    """Return the ``code`` query parameter of a callback location."""
    match = re.search(r"[?&]code=([^&#]+)", location)
    return match.group(1) if match else None


def extract_redirect_error(location: str) -> str | None:
    """Return the ``error`` query parameter of a callback location."""
    match = re.search(r"[?&]error=([^&#]+)", location)
    return match.group(1) if match else None


@dataclass
class B2CControl:
    """A B2C display control (one-time code widget) and its nested fields."""

    control_id: str
    input_type: str = ""
    fields: list[str] = field(default_factory=list)
    prefill: dict[str, str] = field(default_factory=dict)
    input_fields: dict[str, list[str]] = field(default_factory=dict)

    def payload_fields(self, action: str) -> list[str]:
        """Return the claim ids B2C expects for ``action`` (``INPUT_FIELDS``)."""
        for key, values in self.input_fields.items():
            if key.lower() == action.lower():
                return [str(value) for value in values]
        return []


@dataclass
class B2CPage:
    """Configuration and input fields exposed by an Azure AD B2C page."""

    settings: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)
    prefill: dict[str, str] = field(default_factory=dict)
    controls: list[B2CControl] = field(default_factory=list)

    @classmethod
    def from_html(cls, html: str) -> "B2CPage":
        """Parse the ``SETTINGS`` and ``SA_FIELDS`` blobs of a B2C page."""
        settings = extract_json_assignment(html, "SETTINGS") or {}
        sa_fields = extract_json_assignment(html, "SA_FIELDS") or {}
        fields: dict[str, str] = {}
        prefill: dict[str, str] = {}
        controls: list[B2CControl] = []

        attribute_fields = sa_fields.get("AttributeFields")
        if isinstance(attribute_fields, list):
            for entry in attribute_fields:
                if not isinstance(entry, dict):
                    continue
                if (
                    control := cls._read_attribute_field(entry, fields, prefill)
                ) is not None:
                    controls.append(control)

        return cls(settings=settings, fields=fields, prefill=prefill, controls=controls)

    @staticmethod
    def _read_attribute_field(
        entry: dict[str, Any],
        fields: dict[str, str],
        prefill: dict[str, str],
    ) -> B2CControl | None:
        """Record one ``SA_FIELDS`` entry, and its control when it is one."""
        field_id = entry.get("ID")
        if not isinstance(field_id, str) or not field_id:
            return None

        input_type = str(entry.get("UX_INPUT_TYPE") or "")
        fields[field_id] = input_type
        value = entry.get("PRE")
        if isinstance(value, str) and value:
            prefill[field_id] = value

        is_control = (
            input_type == "VerificationControl"
            or entry.get("USER_INPUT_TYPE") == "DisplayControl"
        )
        if not is_control:
            return None

        control = B2CControl(control_id=field_id, input_type=input_type)
        display_fields = entry.get("DISPLAY_FIELDS")
        if isinstance(display_fields, list):
            for nested in display_fields:
                if not isinstance(nested, dict):
                    continue
                nested_id = nested.get("ID")
                if not isinstance(nested_id, str) or not nested_id:
                    continue
                fields[nested_id] = str(nested.get("UX_INPUT_TYPE") or "")
                control.fields.append(nested_id)
                nested_value = nested.get("PRE")
                if isinstance(nested_value, str) and nested_value:
                    prefill[nested_id] = nested_value
                    control.prefill[nested_id] = nested_value

        raw_inputs = entry.get("INPUT_FIELDS")
        if isinstance(raw_inputs, dict):
            for action, values in raw_inputs.items():
                if isinstance(values, list):
                    control.input_fields[str(action)] = [str(value) for value in values]

        return control

    @property
    def csrf(self) -> str | None:
        """Return the CSRF token of the page."""
        value = self.settings.get("csrf")
        return value if isinstance(value, str) and value else None

    @property
    def trans_id(self) -> str | None:
        """Return the transaction id of the page."""
        for key in ("transId", "transID", "transactionId", "tx"):
            value = self.settings.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @property
    def api(self) -> str | None:
        """Return the API segment used by the page (``SelfAsserted``, ...)."""
        value = self.settings.get("api")
        return value if isinstance(value, str) and value else None

    @property
    def policy(self) -> str | None:
        """Return the policy name declared in ``SETTINGS.hosts``."""
        hosts = self.settings.get("hosts")
        if isinstance(hosts, dict):
            value = hosts.get("policy")
            if isinstance(value, str) and value:
                return value
        return None

    @property
    def control(self) -> B2CControl | None:
        """Return the first display control of the page, if any."""
        return self.controls[0] if self.controls else None

    def field_id(self, hints: Iterable[str]) -> str | None:
        """Return the first field id matching one of ``hints``.

        Exact matches win over partial matches, so a page exposing both
        ``email`` and ``verificationCode`` reports the expected fields. Display
        control containers are skipped: their id (``readOnlyEmailVerification
        Control``) must not shadow the nested ``readOnlyEmail`` field.
        """
        lowered = {
            field_id.lower(): field_id
            for field_id, input_type in self.fields.items()
            if input_type not in ("VerificationControl", "DisplayControl")
        }
        for hint in hints:
            if hint in lowered:
                return lowered[hint]
        for hint in hints:
            for lowered_id, field_id in lowered.items():
                if hint in lowered_id:
                    return field_id
        return None


def _api_root(page_url: str) -> str:
    """Return the ``scheme://host/{tenant}/{policy}`` root of a B2C URL."""
    parsed = yarl.URL(page_url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) > 2:
        parts = parts[:2]
    return str(parsed.with_path("/" + "/".join(parts)).with_query(None))


def is_hydro_host(location: str) -> bool:
    """Tell whether a URL belongs to a Hydro-Québec host."""
    if not location.startswith("https://"):
        return False
    host = str(yarl.URL(location).host or "")
    return host == "hydroquebec.com" or host.endswith(".hydroquebec.com")


@dataclass
class MFAChallenge:
    """A pending one-time-code step returned by the B2C policy."""

    kind: MFAKind
    page_url: str
    api: str | None = None
    control_id: str | None = None
    send_fields: tuple[str, ...] = ()
    verify_fields: tuple[str, ...] = ()
    prefill: dict[str, str] = field(default_factory=dict)
    trans_id: str | None = None
    policy: str | None = None
    api_root: str | None = None
    csrf: str | None = None
    email: str | None = None
    email_field: str = "email"
    code_field: str = "verificationCode"

    @property
    def description(self) -> str:
        """Describe the pending step, for logs and error messages."""
        return f"{self.kind.value} verification step ({self.page_url})"

    @property
    def email_value(self) -> str:
        """Return the address to verify: the page's own value wins."""
        return self.prefill.get(self.email_field) or self.email or ""

    @classmethod
    def from_redirect(cls, location: str) -> "MFAChallenge | None":
        """Build a challenge from the ``Location`` of an unexpected redirect.

        Returns ``None`` when the redirect is a plain callback (carrying the
        authorization code or an error) or anything that is not a B2C step.
        """
        if extract_auth_code(location) or extract_redirect_error(location):
            return None
        if not is_hydro_host(location):
            return None

        parsed = yarl.URL(location)
        path = parsed.path.lower()
        if "displaycontrol" in path:
            kind = MFAKind.DISPLAY_CONTROL
        elif "selfasserted" in path:
            kind = MFAKind.SELF_ASSERTED
        elif "confirmed" in path:
            return None
        else:
            kind = MFAKind.UNKNOWN

        query = parsed.query
        return cls(
            kind=kind,
            page_url=location,
            api="SelfAsserted" if kind is MFAKind.SELF_ASSERTED else None,
            trans_id=query.get("tx"),
            policy=query.get("p"),
            api_root=_api_root(location),
        )

    @classmethod
    def from_page(
        cls, page_url: str, page: B2CPage, email: str | None = None
    ) -> "MFAChallenge | None":
        """Build a challenge from a B2C page that asks for a code.

        Returns ``None`` when the page does not expose a one-time code input,
        which means it is not an MFA step.
        """
        if (code_field := page.field_id(CODE_FIELD_HINTS)) is None:
            return None

        api = (page.api or "").lower()
        path = yarl.URL(page_url).path.lower() if page_url.startswith("http") else ""
        if "displaycontrol" in api or "displaycontrol" in path:
            kind = MFAKind.DISPLAY_CONTROL
        elif "selfasserted" in api or "selfasserted" in path:
            kind = MFAKind.SELF_ASSERTED
        else:
            kind = MFAKind.UNKNOWN

        control = page.control
        query = yarl.URL(page_url).query if page_url.startswith("http") else {}
        return cls(
            kind=kind,
            page_url=page_url,
            api=page.api,
            control_id=control.control_id if control is not None else None,
            send_fields=tuple(control.payload_fields("SendCode")) if control else (),
            verify_fields=(
                tuple(control.payload_fields("VerifyCode")) if control else ()
            ),
            prefill=dict(page.prefill),
            trans_id=page.trans_id or query.get("tx"),
            policy=page.policy or query.get("p"),
            api_root=_api_root(page_url),
            csrf=page.csrf,
            email=email,
            email_field=page.field_id(EMAIL_FIELD_HINTS) or "email",
            code_field=code_field,
        )

    def with_page(self, page: B2CPage, page_url: str | None = None) -> "MFAChallenge":
        """Return a copy of the challenge enriched with the page settings."""
        resolved_url = page_url or self.page_url
        api = (page.api or "").lower()
        if "displaycontrol" in api:
            kind = MFAKind.DISPLAY_CONTROL
        elif "selfasserted" in api:
            kind = MFAKind.SELF_ASSERTED
        else:
            kind = self.kind
        api_root = (
            _api_root(resolved_url)
            if resolved_url.startswith("http")
            else self.api_root
        )
        control = page.control
        return MFAChallenge(
            kind=kind,
            page_url=resolved_url,
            api=page.api or self.api,
            control_id=(control.control_id if control is not None else self.control_id),
            send_fields=(
                tuple(control.payload_fields("SendCode"))
                if control is not None
                else self.send_fields
            ),
            verify_fields=(
                tuple(control.payload_fields("VerifyCode"))
                if control is not None
                else self.verify_fields
            ),
            prefill={**self.prefill, **page.prefill},
            trans_id=page.trans_id or self.trans_id,
            policy=page.policy or self.policy,
            api_root=api_root,
            csrf=page.csrf or self.csrf,
            email=self.email,
            email_field=page.field_id(EMAIL_FIELD_HINTS) or self.email_field,
            code_field=page.field_id(CODE_FIELD_HINTS) or self.code_field,
        )

    def _query(self, **extra: str | None) -> str:
        """Build the query string of a B2C call."""
        parts = [f"{key}={value}" for key, value in extra.items() if value]
        if self.trans_id:
            parts.append(f"tx={self.trans_id}")
        if self.policy:
            parts.append(f"p={self.policy}")
        return "?" + "&".join(parts) if parts else ""

    def _endpoint(self, action: str) -> str:
        """Return the display control URL for ``action`` (SendCode, ...)."""
        root = self.api_root or _api_root(self.page_url)
        api = self.api or "SelfAsserted"
        if self.control_id is None:
            return f"{root}/api/DisplayControl/{action}{self._query()}"
        control = quote(self.control_id, safe="")
        return (
            f"{root}/{api}/DisplayControlAction/vbeta/{control}/{action}{self._query()}"
        )

    @property
    def submit_url(self) -> str:
        """Return the form URL that ends the verification page."""
        root = self.api_root or _api_root(self.page_url)
        return f"{root}/{self.api or 'SelfAsserted'}{self._query()}"

    @property
    def confirm_url(self) -> str:
        """Return the ``confirmed`` URL that asks B2C for the next step."""
        root = self.api_root or _api_root(self.page_url)
        query = self._query(csrf_token=self.csrf)
        return f"{root}/api/{self.api or 'SelfAsserted'}/confirmed{query}"


@dataclass
class MFARequest:
    """One HTTP call that answers a B2C verification step."""

    label: str
    url: str
    data: str = ""
    headers: dict[str, str] = field(default_factory=dict)


def _json_headers(csrf: str | None) -> dict[str, str]:
    """Return the headers of a B2C JSON call."""
    headers = {
        "content-type": "application/json; charset=UTF-8",
        "accept": "application/json, text/javascript, */*; q=0.01",
        "x-requested-with": "XMLHttpRequest",
    }
    if csrf:
        headers["x-csrf-token"] = csrf
    return headers


def _form_headers(csrf: str | None) -> dict[str, str]:
    """Return the headers of a B2C form call."""
    headers = {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "accept": "application/json, text/javascript, */*; q=0.01",
        "x-requested-with": "XMLHttpRequest",
    }
    if csrf:
        headers["x-csrf-token"] = csrf
    return headers


def _json_body(payload: dict[str, str]) -> str:
    """Serialize a B2C JSON body."""
    return json.dumps(payload)


def _form_body(payload: dict[str, str]) -> str:
    """Serialize a B2C form body."""
    return urlencode({key: value for key, value in payload.items() if value})


def _is_code_field(field_id: str) -> bool:
    """Tell whether ``field_id`` designates the one-time code input."""
    lowered = field_id.lower()
    return lowered not in EMAIL_FIELD_HINTS and any(
        hint in lowered for hint in CODE_FIELD_HINTS
    )


def _payload_for(
    challenge: MFAChallenge, action: str, code: str | None = None
) -> dict[str, str]:
    """Build the ``INPUT_FIELDS`` payload of a display control action."""
    if action == "SendCode":
        fields = challenge.send_fields
    else:
        fields = challenge.verify_fields
    if not fields:
        if action == "SendCode":
            fields = (challenge.email_field,)
        else:
            fields = (challenge.email_field, challenge.code_field)

    payload: dict[str, str] = {}
    for field_id in fields:
        if _is_code_field(field_id):
            if code is not None:
                payload[field_id] = code
            continue
        if field_id == challenge.email_field or any(
            hint in field_id.lower() for hint in EMAIL_FIELD_HINTS
        ):
            payload[field_id] = challenge.email_value
    if not payload:
        # Never send an empty body because the policy named its fields in an
        # unexpected way: fall back to the ids read from the page.
        payload[challenge.email_field] = challenge.email_value
        if code is not None:
            payload[challenge.code_field] = code
    return payload


def send_code_requests(challenge: MFAChallenge) -> list[MFARequest]:
    """Return the request that asks B2C to e-mail a one-time code.

    The browser sends the display control fields as a form POST to
    ``DisplayControlAction/vbeta/{control}/SendCode``. Policies that do not use
    a display control get the older JSON endpoint instead.
    """
    if not (payload := _payload_for(challenge, "SendCode")):
        return []
    label = f"display-control send code ({', '.join(payload)})"
    if challenge.control_id is not None:
        return [
            MFARequest(
                label=label,
                url=challenge._endpoint("SendCode"),
                data=_form_body(payload),
                headers=_form_headers(challenge.csrf),
            )
        ]
    return [
        MFARequest(
            label=label,
            url=challenge._endpoint("SendCode"),
            data=_json_body(payload),
            headers=_json_headers(challenge.csrf),
        )
    ]


def verify_code_requests(challenge: MFAChallenge, code: str) -> list[MFARequest]:
    """Return the request that submits ``code`` to B2C."""
    if not (payload := _payload_for(challenge, "VerifyCode", code)):
        return []
    label = f"display-control verify code ({', '.join(payload)})"
    if challenge.control_id is not None:
        return [
            MFARequest(
                label=label,
                url=challenge._endpoint("VerifyCode"),
                data=_form_body(payload),
                headers=_form_headers(challenge.csrf),
            )
        ]
    return [
        MFARequest(
            label=label,
            url=challenge._endpoint("VerifyCode"),
            data=_json_body(payload),
            headers=_json_headers(challenge.csrf),
        )
    ]


def completion_request(challenge: MFAChallenge, code: str) -> MFARequest:
    """Return the form POST that ends the verification page.

    Once the code is accepted, the browser submits the page like any other
    ``SelfAsserted`` step: every text field of the form plus
    ``request_type=RESPONSE``. B2C then answers with the ``confirmed`` URL.
    """
    payload = {
        challenge.email_field: challenge.email_value,
        challenge.code_field: code,
        "request_type": RESPONSE_REQUEST,
    }
    return MFARequest(
        label="self-asserted confirmation",
        url=challenge.submit_url,
        data=_form_body(payload),
        headers=_form_headers(challenge.csrf),
    )


def call_succeeded(payload: dict[str, Any]) -> bool:
    """Tell whether a B2C ``SelfAsserted``/``DisplayControl`` answer is a success."""
    if not payload:
        # An empty body means the endpoint was not understood, not a success.
        return False
    status = payload.get("status")
    if status is not None and str(status) not in ("200", "success"):
        return False
    result = payload.get("result")
    if isinstance(result, str) and result.lower() in FAILURE_RESULTS:
        return False
    return True


def payload_message(payload: dict[str, Any]) -> str:
    """Return the human readable message carried by a B2C answer."""
    for key in ("message", "result", "error_description", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def looks_expired(snapshot: str, payload: dict[str, Any]) -> bool:
    """Tell whether a failed answer means the B2C transaction is gone."""
    haystack = f"{snapshot} {payload_message(payload)}".lower()
    return any(marker in haystack for marker in EXPIRED_MESSAGE_MARKERS)


def clear_cookies_for_host(jar: aiohttp.CookieJar, host: str) -> None:
    """Remove every cookie stored for ``host``.

    ``CookieJar.clear`` accepts a predicate on aiohttp >= 3.13 only, older
    releases take ``domain``/``path`` arguments. Both are supported so the
    library keeps working with the whole supported aiohttp range.
    """
    clear_domain = getattr(jar, "clear_domain", None)
    if callable(clear_domain):
        clear_domain(host)
        return
    jar.clear(domain=host)  # type: ignore[call-arg]


def clear_transaction_cookies(jar: aiohttp.CookieJar, host: str) -> None:
    """Remove the B2C transaction cookies of ``host``, keeping its SSO state.

    A new login needs a clean ``csrf``/``trans``/``cache`` state, otherwise
    Azure AD B2C can reject the password round trip. The single sign-on cookie
    is the opposite: it is what lets a later login skip the e-mail code, so it
    must survive. Cookies are expired one by one through the public API, which
    works on every supported aiohttp release.
    """
    target = host.lstrip(".").lower()
    for morsel in list(jar):
        if (domain := (morsel["domain"] or "").lstrip(".").lower()) != target:
            continue
        if any(hint in morsel.key.lower() for hint in SSO_COOKIE_HINTS):
            continue
        expired = SimpleCookie()
        expired[morsel.key] = ""
        expired[morsel.key]["max-age"] = "0"
        expired[morsel.key]["path"] = morsel["path"] or "/"
        try:
            jar.update_cookies(expired, response_url=yarl.URL(f"https://{domain}/"))
        except (ValueError, KeyError):
            continue


def serialize_cookies(jar: aiohttp.CookieJar) -> list[dict[str, Any]]:
    """Serialize a cookie jar to JSON friendly dictionaries.

    Session cookies matter as much as the tokens: Azure AD B2C stores its
    single sign-on and "trusted device" cookies there, they are what allows a
    later login to skip the one-time code.
    """
    cookies: list[dict[str, Any]] = []
    for morsel in jar:
        if not (domain := morsel["domain"]):
            continue
        cookies.append(
            {
                "key": morsel.key,
                "value": morsel.value,
                "domain": domain,
                "path": morsel["path"] or "/",
                "expires": morsel["expires"] or None,
                "max_age": morsel["max-age"] or None,
                "secure": bool(morsel["secure"]),
            }
        )
    return cookies


def restore_cookies(jar: aiohttp.CookieJar, cookies: Sequence[dict[str, Any]]) -> None:
    """Load cookies produced by :func:`serialize_cookies` into ``jar``."""
    now = datetime.now(timezone.utc)
    for item in cookies:
        try:
            key = str(item["key"])
            value = str(item["value"])
            domain = str(item["domain"])
        except KeyError:
            continue

        simple = SimpleCookie()
        simple[key] = value
        morsel = simple[key]
        morsel["path"] = str(item.get("path") or "/")
        if item.get("secure"):
            morsel["secure"] = True

        max_age = item.get("max_age")
        expires = item.get("expires")
        if max_age:
            morsel["max-age"] = str(max_age)
        elif expires:
            try:
                moment = datetime.fromisoformat(str(expires))
            except ValueError:
                moment = None
            if moment is not None:
                if (seconds := int((moment - now).total_seconds())) > 0:
                    morsel["max-age"] = str(seconds)

        try:
            jar.update_cookies(
                simple, response_url=yarl.URL(f"https://{domain.lstrip('.')}/")
            )
        except (ValueError, KeyError):
            continue
