from __future__ import annotations

import hmac
from fastapi import HTTPException, Request, WebSocket, status
from fastapi import WebSocketDisconnect
from stash_ai_server.core.system_settings import get_value as sys_get

SETTING_KEY = 'UI_SHARED_API_KEY'
HEADER_NAME = 'x-ai-api-key'
QUERY_PARAM = 'api_key'
WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_FORBIDDEN = 4403


def _get_configured_key() -> str | None:
    value = sys_get(SETTING_KEY)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_candidate(header_value: str | None, query_value: str | None) -> str | None:
    candidate = header_value or query_value
    if candidate is None:
        return None
    candidate = candidate.strip()
    return candidate or None


def _matches(expected: str, provided: str) -> bool:
    try:
        return hmac.compare_digest(expected, provided)
    except Exception:
        return expected == provided


async def require_shared_api_key(request: Request) -> None:
    secret = _get_configured_key()
    if not secret:
        return
    provided = _extract_candidate(request.headers.get(HEADER_NAME), request.query_params.get(QUERY_PARAM))
    if not provided:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Shared API key required')
    if not _matches(secret, provided):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Invalid shared API key')


async def enforce_shared_key_websocket(ws: WebSocket) -> None:
    secret = _get_configured_key()
    if not secret:
        return
    provided = _extract_candidate(ws.headers.get(HEADER_NAME), ws.query_params.get(QUERY_PARAM))
    if not provided:
        await ws.close(code=WS_CLOSE_UNAUTHORIZED, reason='Shared API key required')
        raise WebSocketDisconnect(code=WS_CLOSE_UNAUTHORIZED)
    if not _matches(secret, provided):
        await ws.close(code=WS_CLOSE_FORBIDDEN, reason='Invalid shared API key')
        raise WebSocketDisconnect(code=WS_CLOSE_FORBIDDEN)
