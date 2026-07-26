"""Unit tests for HTTP and WebSocket shared API key enforcement."""

import pytest
from fastapi import HTTPException, WebSocketDisconnect
from starlette.requests import Request

from stash_ai_server.core import api_key


def _request(*, header: str | None = None, query: str = "") -> Request:
    headers = []
    if header is not None:
        headers.append((api_key.HEADER_NAME.encode(), header.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "query_string": query.encode(),
        }
    )


class FakeWebSocket:
    def __init__(self, *, header: str | None = None, query: str | None = None):
        self.headers = {}
        if header is not None:
            self.headers[api_key.HEADER_NAME] = header
        self.query_params = {}
        if query is not None:
            self.query_params[api_key.QUERY_PARAM] = query
        self.closed = []

    async def close(self, *, code: int, reason: str):
        self.closed.append((code, reason))


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        (" secret ", "secret"),
        (123, "123"),
    ],
)
def test_get_configured_key_normalizes_values(monkeypatch, stored, expected):
    monkeypatch.setattr(api_key, "sys_get", lambda _key: stored)
    assert api_key._get_configured_key() == expected


def test_extract_candidate_prefers_header_and_strips_values():
    assert api_key._extract_candidate(" header ", "query") == "header"
    assert api_key._extract_candidate("", " query ") == "query"
    assert api_key._extract_candidate(None, "  ") is None
    assert api_key._extract_candidate(None, None) is None


def test_matches_falls_back_when_compare_digest_rejects_non_ascii_strings():
    assert api_key._matches("é", "é") is True
    assert api_key._matches("é", "è") is False


@pytest.mark.asyncio
async def test_http_authentication_is_disabled_without_a_configured_key(monkeypatch):
    monkeypatch.setattr(api_key, "sys_get", lambda _key: " ")
    await api_key.require_shared_api_key(_request())


@pytest.mark.asyncio
async def test_http_authentication_accepts_header_or_query_key(monkeypatch):
    monkeypatch.setattr(api_key, "sys_get", lambda _key: "secret")

    await api_key.require_shared_api_key(_request(header="secret"))
    await api_key.require_shared_api_key(_request(query="api_key=secret"))


@pytest.mark.asyncio
async def test_http_authentication_rejects_missing_and_invalid_keys(monkeypatch):
    monkeypatch.setattr(api_key, "sys_get", lambda _key: "secret")

    with pytest.raises(HTTPException) as missing:
        await api_key.require_shared_api_key(_request())
    assert missing.value.status_code == 401
    assert missing.value.detail == "Shared API key required"

    with pytest.raises(HTTPException) as invalid:
        await api_key.require_shared_api_key(_request(header="wrong"))
    assert invalid.value.status_code == 403
    assert invalid.value.detail == "Invalid shared API key"


@pytest.mark.asyncio
async def test_websocket_authentication_is_disabled_or_accepts_valid_key(monkeypatch):
    socket = FakeWebSocket()
    monkeypatch.setattr(api_key, "sys_get", lambda _key: None)
    await api_key.enforce_shared_key_websocket(socket)

    valid_socket = FakeWebSocket(query="secret")
    monkeypatch.setattr(api_key, "sys_get", lambda _key: "secret")
    await api_key.enforce_shared_key_websocket(valid_socket)

    assert socket.closed == []
    assert valid_socket.closed == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("socket_kwargs", "expected_code", "expected_reason"),
    [
        (
            {},
            api_key.WS_CLOSE_UNAUTHORIZED,
            "Shared API key required",
        ),
        (
            {"header": "wrong"},
            api_key.WS_CLOSE_FORBIDDEN,
            "Invalid shared API key",
        ),
    ],
)
async def test_websocket_authentication_closes_unauthorized_clients(
    monkeypatch,
    socket_kwargs,
    expected_code,
    expected_reason,
):
    monkeypatch.setattr(api_key, "sys_get", lambda _key: "secret")
    socket = FakeWebSocket(**socket_kwargs)

    with pytest.raises(WebSocketDisconnect) as raised:
        await api_key.enforce_shared_key_websocket(socket)

    assert raised.value.code == expected_code
    assert socket.closed == [(expected_code, expected_reason)]
