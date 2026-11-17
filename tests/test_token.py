"""Id token decoding tests for hydroqc."""

import base64
import binascii
import json

import pytest

from hydroqc.hydro_api.client import HydroClient


def _b64url(data: bytes) -> str:
    """Encode bytes as base64url without padding, like a JWT segment."""
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _make_id_token(payload: dict[str, str]) -> str:
    """Build an unsigned JWT from a payload."""
    header = _b64url(b'{"alg":"none","typ":"JWT"}')
    body = _b64url(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return f"{header}.{body}.signature"


def _payload_with_urlsafe_chars() -> dict[str, str]:
    """Return a payload whose base64url encoding contains "-" or "_"."""
    for extra in range(64):
        payload = {
            "sub": "abc",
            "given_name": "Noémie",
            "family_name": "Tremblay",
            "pad": "x" * extra,
        }
        segment = _make_id_token(payload).split(".")[1]
        if "-" in segment or "_" in segment:
            return payload
    raise AssertionError("Could not build a payload with base64url characters")


def _client_with_token(token: str) -> HydroClient:
    """Create a client holding an id token, without logging in."""
    client = HydroClient("user@example.com", "password")
    client._id_token = token  # pylint: disable=protected-access
    return client


def test_token_data_with_base64url_chars() -> None:
    """Decode an id token whose payload uses base64url-only characters."""
    payload = _payload_with_urlsafe_chars()
    client = _client_with_token(_make_id_token(payload))

    token_data = client.get_token_data()

    assert token_data is not None
    assert dict(token_data) == payload


def test_standard_alphabet_fails_on_same_payload() -> None:
    """Document why the standard base64 alphabet cannot be used."""
    segment = _make_id_token(_payload_with_urlsafe_chars()).split(".")[1]
    segment += "=" * ((4 - len(segment) % 4) % 4)
    with pytest.raises(binascii.Error):
        base64.b64decode(segment)


def test_token_data_ascii_payload() -> None:
    """Decode a plain ASCII payload, whatever its padding length."""
    for extra in range(4):
        payload = {"sub": "abc", "given_name": "John", "pad": "x" * extra}
        client = _client_with_token(_make_id_token(payload))
        assert dict(client.get_token_data() or {}) == payload


def test_token_data_without_token() -> None:
    """Return None when no id token is available."""
    client = _client_with_token("")
    assert client.get_token_data() is None
