from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Mapping, TypeVar, cast

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError


from stash_ai_server.core.config import settings
from stash_ai_server.utils.url_helpers import dockerize_localhost

from .registry import ServiceBase

T = TypeVar("T")

_DEFAULT_TIMEOUT = httpx.Timeout(7200, connect=10.0)
_DEFAULT_HEADERS: Mapping[str, str] = {
    "User-Agent": "stash-ai-server-plugin/1.0",
}


def _coerce_timeout(value: httpx.Timeout | float | int | None) -> httpx.Timeout:
    if isinstance(value, httpx.Timeout):
        return value
    if isinstance(value, (int, float)):
        return httpx.Timeout(value)
    return _DEFAULT_TIMEOUT


def _trim(text: str | None, *, limit: int = 200) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


@dataclass(slots=True)
class ConnectivityProbe:
    ok: bool
    status: str
    status_code: int | None = None
    error: str | None = None
    latency_ms: float | None = None

    def describe(self) -> str:
        parts: list[str] = [self.status]
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.latency_ms is not None:
            parts.append(f"{self.latency_ms:.1f}ms")
        if self.error:
            parts.append(f"error={self.error}")
        return "; ".join(parts)


class HTTPClient:
    """Async HTTP helper with optional Pydantic model decoding."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: httpx.Timeout | float | int | None = None,
        headers: Mapping[str, str] | None = None,
        verify: bool | str | None = True,
        follow_redirects: bool = True,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        self._base_url = base_url.rstrip("/")
        self._timeout = _coerce_timeout(timeout)
        merged = dict(_DEFAULT_HEADERS)
        if headers:
            merged.update(headers)
        self._headers = merged
        self._verify = verify
        self._follow_redirects = follow_redirects
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return self._base_url

    @staticmethod
    def _normalize_path(path: str) -> str:
        if not path:
            return "/"
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if path.startswith("/"):
            return path
        return f"/{path}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        base_url=self._base_url,
                        timeout=self._timeout,
                        headers=self._headers,
                        verify=self._verify,
                        follow_redirects=self._follow_redirects,
                    )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        path: str = "",
        *,
        response_model: Any | None = None,
        expect_json: bool = False,
        raise_for_status: bool = True,
        **kwargs: Any,
    ) -> T | Any | httpx.Response:
        client = await self._get_client()
        url = self._normalize_path(path)
        response = await client.request(method, url, **kwargs)
        if raise_for_status:
            response.raise_for_status()

        wants_model = response_model is not None
        wants_json = expect_json or wants_model
        payload: Any | None = None

        if wants_json:
            if response.status_code == 204 or not response.content:
                payload = None
            else:
                try:
                    payload = response.json()
                except ValueError as exc:  # pragma: no cover - extremely rare
                    raise ValueError("Response body is not valid JSON") from exc

        if wants_model:
            if payload is None:
                raise ValueError("Expected JSON payload but response was empty")
            try:
                adapter = response_model if isinstance(response_model, TypeAdapter) else TypeAdapter(response_model)
                return cast(T, adapter.validate_python(payload))
            except ValidationError as exc:
                raise exc

        if wants_json:
            return payload

        return response

    async def get(
        self,
        path: str = "",
        *,
        params: Mapping[str, Any] | None = None,
        response_model: Any | None = None,
        expect_json: bool = False,
        **kwargs: Any,
    ) -> T | Any | httpx.Response:
        return await self.request(
            "GET",
            path,
            params=params,
            response_model=response_model,
            expect_json=expect_json,
            **kwargs,
        )

    async def post(
        self,
        path: str = "",
        *,
        json: Any = None,
        data: Any = None,
        response_model: Any | None = None,
        expect_json: bool = False,
        **kwargs: Any,
    ) -> T | Any | httpx.Response:
        return await self.request(
            "POST",
            path,
            json=json,
            data=data,
            response_model=response_model,
            expect_json=expect_json,
            **kwargs,
        )

    async def put(
        self,
        path: str = "",
        *,
        json: Any = None,
        data: Any = None,
        response_model: Any | None = None,
        expect_json: bool = False,
        **kwargs: Any,
    ) -> T | Any | httpx.Response:
        return await self.request(
            "PUT",
            path,
            json=json,
            data=data,
            response_model=response_model,
            expect_json=expect_json,
            **kwargs,
        )

    async def delete(
        self,
        path: str = "",
        *,
        response_model: Any | None = None,
        expect_json: bool = False,
        **kwargs: Any,
    ) -> T | Any | httpx.Response:
        return await self.request(
            "DELETE",
            path,
            response_model=response_model,
            expect_json=expect_json,
            **kwargs,
        )

    async def check_ready(self, path: str = "/ready") -> ConnectivityProbe:
        start = time.perf_counter()
        try:
            response = await self.request("GET", path, raise_for_status=False)
            latency = (time.perf_counter() - start) * 1000.0
            if isinstance(response, httpx.Response):
                status_code = response.status_code
                if status_code < 400:
                    return ConnectivityProbe(True, "ready", status_code, None, latency)
                return ConnectivityProbe(
                    False,
                    f"status-{status_code}",
                    status_code,
                    _trim(response.text),
                    latency,
                )
            return ConnectivityProbe(True, "ready", None, None, latency)
        except httpx.RequestError as exc:
            latency = (time.perf_counter() - start) * 1000.0
            return ConnectivityProbe(False, "network-error", None, str(exc), latency)


class RemoteServiceBase(ServiceBase):
    """Service base with shared HTTP client and readiness tracking."""

    server_url: str | None = None
    ready_endpoint: str = "/ready"
    readiness_cache_seconds: float = 10.0
    failure_backoff_seconds: float = 20.0
    request_timeout: httpx.Timeout | float | int | None = None
    verify_ssl: bool | str | None = True
    was_disconnected: bool = False

    def __init__(self) -> None:
        super().__init__()
        self._http_client: HTTPClient | None = None
        self._ready_lock = asyncio.Lock()
        self._last_ready_success: float | None = None
        self._last_ready_failure: float | None = None
        self._last_ready_attempt: float | None = None
        self._next_ready_attempt: float = 0.0
        self._last_ready_error: str | None = None
        self._connectivity_state: str = "unknown"
        self._connectivity_detail: str | None = None

    def request_headers(self) -> Mapping[str, str]:
        """Override to provide extra headers for the HTTP client."""
        return {}

    def build_http_client(self, base_url: str) -> HTTPClient:
        return HTTPClient(
            base_url,
            timeout=self.request_timeout,
            headers=self.request_headers(),
            verify=self.verify_ssl,
        )

    @property
    def http(self) -> HTTPClient:
        if not self.server_url:
            raise RuntimeError(f"Service '{self.name}' does not define a server_url")
        effective = dockerize_localhost(self.server_url, enabled=settings.docker_mode) or self.server_url
        normalized = effective.rstrip("/")
        client = self._http_client
        if client is None or client.base_url != normalized:
            if client is not None:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(client.close())
                else:
                    loop.create_task(client.close())
            self._http_client = self.build_http_client(normalized)
        return self._http_client

    async def close_http(self) -> None:
        if self._http_client is not None:
            await self._http_client.close()
            self._http_client = None

    def connectivity(self) -> str:
        detail = self._connectivity_detail
        if detail:
            return f"{self._connectivity_state}: {detail}"
        return self._connectivity_state

    def connectivity_details(self) -> dict[str, Any]:
        return {
            "state": self._connectivity_state,
            "detail": self._connectivity_detail,
            "last_ready_success": self._last_ready_success,
            "last_ready_attempt": self._last_ready_attempt,
            "last_ready_error": self._last_ready_error,
            "last_ready_failure": self._last_ready_failure,
        }

    async def ensure_remote_ready(self, *, force: bool = False) -> bool:
        if not self.server_url:
            self._connectivity_state = "local"
            self._connectivity_detail = "no server_url configured"
            return True

        now = time.monotonic()
        if (
            not force
            and self._last_ready_success is not None
            and (now - self._last_ready_success) < self.readiness_cache_seconds
        ):
            return True
        if not force and now < self._next_ready_attempt:
            self._connectivity_state = "waiting"
            return False

        async with self._ready_lock:
            now = time.monotonic()
            if (
                not force
                and self._last_ready_success is not None
                and (now - self._last_ready_success) < self.readiness_cache_seconds
            ):
                return True
            if (
                not force
                and self._last_ready_failure is not None
                and (now - self._last_ready_failure) < self.failure_backoff_seconds
            ):
                self._next_ready_attempt = self._last_ready_failure + self.failure_backoff_seconds
                self._connectivity_state = "unreachable"
                self._connectivity_detail = self._last_ready_error or "service unreachable"
                return False

            try:
                probe = await self.http.check_ready(self.ready_endpoint)
            except Exception as exc:  # pragma: no cover - defensive
                probe = ConnectivityProbe(False, "exception", None, str(exc), None)

            self._last_ready_attempt = now

            if probe.ok:
                self._last_ready_success = now
                self._last_ready_failure = None
                self._next_ready_attempt = 0.0
                self._last_ready_error = None
                self._connectivity_state = "ready"
                self._connectivity_detail = probe.describe()
                return True

            self._last_ready_error = probe.describe()
            self._last_ready_failure = now
            self._next_ready_attempt = now + self.failure_backoff_seconds
            detail = probe.describe()

            self._connectivity_state = "unreachable"

            self._connectivity_detail = detail
            return False

    async def close(self) -> None:
        await self.close_http()
