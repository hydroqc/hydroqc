"""Fixture-backed HTTP transport for replaying captured HydroClient calls."""

import json
from collections.abc import Iterable
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yarl


class FixtureResponse:
    """Small aiohttp-response substitute backed by a fixture body."""

    def __init__(self, metadata: dict[str, Any], body: bytes, request_url: str) -> None:
        self.status: int = metadata["status"]
        self.headers: dict[str, str] = metadata.get("headers", {})
        self.url: yarl.URL = yarl.URL(request_url)
        self._body = body

    async def read(self) -> bytes:
        """Return the response bytes."""
        return self._body

    async def text(self, encoding: str | None = None, errors: str = "strict") -> str:
        """Decode the fixture body as text."""
        if encoding is None:
            content_type = Message()
            content_type["content-type"] = self.headers.get("Content-Type", "")
            encoding = content_type.get_content_charset() or "utf-8"
        return self._body.decode(encoding, errors=errors)

    async def json(self, **kwargs: Any) -> Any:
        """Decode the fixture body as JSON."""
        del kwargs
        return json.loads(self._body)


class FixtureReplayTransport:
    """Replay recorded responses in order and reject unexpected HTTP calls."""

    def __init__(self, fixture_folder: Path | Iterable[Path]) -> None:
        folders = (
            [fixture_folder]
            if isinstance(fixture_folder, Path)
            else list(fixture_folder)
        )
        self.events = [
            (folder, json.loads(path.read_text(encoding="utf-8")))
            for folder in folders
            for path in sorted(folder.glob("*-metadata.json"))
        ]
        self.calls: list[tuple[str, str]] = []

    async def request(self, url: str, method: str, **kwargs: Any) -> FixtureResponse:
        """Match the next method/path pair and return its fixture response."""
        del kwargs
        actual_path = urlsplit(str(url)).path
        call = (method.lower(), actual_path)
        self.calls.append(call)

        event_index = len(self.calls) - 1
        if event_index >= len(self.events):
            raise AssertionError(
                f"unexpected HTTP call: {method.upper()} {actual_path}"
            )

        event_folder, event = self.events[event_index]
        expected = (event["request_method"].lower(), event["request_path"])
        if call != expected:
            raise AssertionError(
                f"HTTP call {event_index + 1} did not match fixture: "
                f"expected {expected[0].upper()} {expected[1]}, "
                f"got {method.upper()} {actual_path}"
            )

        body_path = event_folder / event["body_file"]
        return FixtureResponse(
            event,
            body_path.read_bytes(),
            str(url),
        )

    def assert_complete(self) -> None:
        """Assert that the client consumed every recorded response."""
        if len(self.calls) != len(self.events):
            raise AssertionError(
                "fixture replay consumed "
                f"{len(self.calls)} of {len(self.events)} responses"
            )
